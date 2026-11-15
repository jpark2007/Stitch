"""agent/preflight.py: the env report, key-format checks, limits and --live checks, all offline.

Every live call goes through LiveSeams fakes (httpx.MockTransport, a fake Anthropic client,
a fake MongoClient, a fake Playwright page factory); sockets are blocked where services are
built, and no test launches a browser.
"""

from __future__ import annotations

import contextlib
import importlib
import io
import json
import logging
import os
import re
import socket
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import quote, quote_plus

import anthropic
import httpx
import mongomock
import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore
from pymongo.errors import OperationFailure, ServerSelectionTimeoutError

from agent import guards, preflight, production, runs, studio
from agent.adapters import images as images_adapter
from agent.adapters import tts as tts_adapter
from agent.guards import RunLimits
from agent.tools import frames
from db import client as db_client

ANTHROPIC = "sk-ant-api03-PREFLIGHTsecretANTHROPICvalue0001"
IMAGE = "sk-proj-PREFLIGHTsecretIMAGEvalue0002"
ELEVEN = "sk_PREFLIGHTsecretELEVENLABSvalue0003"
MONGO_PASSWORD = "PREFLIGHTmongoPASSWORD0004"
MONGO_USER = "stitchuser"
MONGO_URI = f"mongodb+srv://{MONGO_USER}:{MONGO_PASSWORD}@cluster0.example.mongodb.net/stitch?retryWrites=true"
VOICE_A = "VoiceAid000000000001"
VOICE_B = "VoiceBid000000000002"
SECRETS = (ANTHROPIC, IMAGE, ELEVEN, MONGO_PASSWORD, MONGO_URI)
WEB_URL = "http://localhost:3000"

AUTH_ERROR_BODY = "Incorrect API key provided: {key}. You can find your API key in your dashboard."

EXTRA_ENV = ("STITCH_NO_DOTENV", "STITCH_FAKE_RUN_STEP", "JWT_SECRET", "VOYAGE_API_KEY", "MONGODB_URI_TEST")


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> Any:
    for name in (*preflight.ENV_NAMES, *EXTRA_ENV):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(guards, "_warned", set())
    monkeypatch.setattr(production, "_memory_warned", True)  # no persistence warning noise
    db = mongomock.MongoClient(tz_aware=True)[db_client.MONGODB_DB]
    db_client.set_db(db)
    runs.configure(None)
    yield db
    runs.configure(None)
    db_client.set_db(None)


@pytest.fixture
def no_network(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    attempts: list[Any] = []

    def refuse(*args: Any, **_kwargs: Any) -> Any:
        attempts.append(args)
        raise AssertionError(f"network call: {args!r}")

    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)
    return attempts


def set_all_keys(monkeypatch: pytest.MonkeyPatch, *, mongo: bool = True, web: bool = True) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", ANTHROPIC)
    monkeypatch.setenv("STITCH_SUBAGENT_MODEL", "claude-sonnet-5-5")
    monkeypatch.setenv("STITCH_VISION_MODEL", "claude-haiku-5-5")
    monkeypatch.setenv(images_adapter.API_KEY_ENV, IMAGE)
    monkeypatch.setenv(tts_adapter.API_KEY_ENV, ELEVEN)
    monkeypatch.setenv(tts_adapter.VOICES_ENV, f"voice_a={VOICE_A},voice_b={VOICE_B}")
    if mongo:
        monkeypatch.setenv("MONGODB_URI", MONGO_URI)
    if web:
        monkeypatch.setenv("STITCH_WEB_URL", WEB_URL)


# ------------------------------------------------------------------------------ fakes


class FakeModels:
    def __init__(self, owner: FakeAnthropic) -> None:
        self.owner = owner

    def retrieve(self, model_id: str) -> Any:
        self.owner.calls.append(("models.retrieve", model_id))
        if self.owner.error is not None:
            raise self.owner.error
        return {"id": model_id}


class FakeMessages:
    def __init__(self, owner: FakeAnthropic) -> None:
        self.owner = owner

    def create(self, **kwargs: Any) -> Any:
        self.owner.calls.append(("messages.create", kwargs))
        if self.owner.error is not None:
            raise self.owner.error

        class Response:
            stop_reason = "max_tokens"

        return Response()


class FakeAnthropic:
    def __init__(self, error: Exception | None = None, factory_error: Exception | None = None) -> None:
        self.error = error
        self.factory_error = factory_error
        self.calls: list[tuple[str, Any]] = []
        self.keys: list[str] = []
        self.models = FakeModels(self)
        self.messages = FakeMessages(self)

    def factory(self, api_key: str, timeout: float) -> FakeAnthropic:
        self.keys.append(api_key)
        if self.factory_error is not None:
            raise self.factory_error
        return self


class FakeMongo:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.opened: list[tuple[str, int]] = []
        self.closed = 0
        self.commands: list[str] = []

    def factory(self, uri: str, timeout_ms: int) -> FakeMongo:
        self.opened.append((uri, timeout_ms))
        return self

    @property
    def admin(self) -> FakeMongo:
        return self

    def command(self, name: str) -> dict[str, int]:
        self.commands.append(name)
        if self.error is not None:
            raise self.error
        return {"ok": 1}

    def close(self) -> None:
        self.closed += 1


class FakeRenderPage:
    """A Playwright page that behaves like one kind of render page.

    contract: sets __STITCH_READY__ (web/app/render on main today); legacy: only data-stage-ready
    (a web checkout from before the contract);
    down: navigation fails, its error echoing `echo`.
    """

    def __init__(self, kind: str, echo: str = "") -> None:
        self.kind = kind
        self.echo = echo
        self.urls: list[str] = []

    def add_init_script(self, script: str) -> None:
        assert script.startswith("window.__STITCH_RENDER__ = ")

    def goto(self, url: str, timeout: int) -> None:
        self.urls.append(url)
        if self.kind == "down":
            raise RuntimeError(f"net::ERR_CONNECTION_REFUSED at {url} {self.echo}")

    def wait_for_function(self, expression: str, timeout: int) -> None:
        if expression == frames.CONTRACT_EXPR and self.kind == "legacy":
            raise TimeoutError(f"Timeout {timeout}ms exceeded")

    def screenshot(self, type: str, full_page: bool) -> bytes:  # noqa: A002 - Playwright's name
        raise AssertionError("a probe takes no screenshot")


class FakeBrowser:
    """A frames.PageFactory: 'no_playwright' raises PlaywrightUnavailable, as a missing Chromium does."""

    def __init__(self, kind: str = "contract", echo: str = "") -> None:
        self.kind = kind
        self.page = FakeRenderPage(kind, echo)
        self.opened = 0

    @contextlib.contextmanager
    def __call__(self, viewport: tuple[int, int]) -> Iterator[FakeRenderPage]:
        self.opened += 1
        if self.kind == "no_playwright":
            raise frames.PlaywrightUnavailable(f"chromium would not start {self.page.echo}")
        yield self.page


def ok_handler(seen: list[httpx.Request]) -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.startswith("/v1/voices/"):
            return httpx.Response(200, json={"voice_id": request.url.path.rsplit("/", 1)[1], "name": "Rachel"})
        return httpx.Response(200, json={"id": "gpt-image-1"})

    return handler


def seams(
    *,
    handler: Any = None,
    claude: FakeAnthropic | None = None,
    mongo: FakeMongo | None = None,
    browser: FakeBrowser | None = None,
) -> preflight.LiveSeams:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"unexpected HTTP call {request.url}")

    def no_browser(_viewport: Any) -> Any:
        raise AssertionError("unexpected render probe")

    return preflight.LiveSeams(
        http_transport=httpx.MockTransport(handler or refuse),
        anthropic_factory=(claude or FakeAnthropic()).factory,
        mongo_factory=(mongo or FakeMongo()).factory,
        page_factory=browser or no_browser,
    )


def run(argv: list[str], s: preflight.LiveSeams | None = None) -> tuple[int, str]:
    out = io.StringIO()
    code = preflight.main(argv, out=out, seams=s or seams())
    return code, out.getvalue()


def by_service(output: str) -> dict[str, dict[str, Any]]:
    return {entry["service"]: entry for entry in json.loads(output)}


def var(report: dict[str, Any], name: str) -> dict[str, Any]:
    return next(v for v in report["vars"] if v["name"] == name)


SERVICES = ["agent", "model", "vision", "persistence", "art", "tts", "renderer", "compiler", "limits"]


# --------------------------------------------------------------------- the plain report


def test_with_no_keys_it_exits_1_and_lists_every_service_var_fallback_and_limit() -> None:
    code, text = run([])
    assert code == 1
    for service in SERVICES:
        assert f"[{service}]  mode:" in text
    for name in preflight.ENV_NAMES:
        assert name in text
    assert "ANTHROPIC_API_KEY" in text and "missing (required, no fallback)" in text
    assert "[art]  mode: placeholder" in text
    assert "[tts]  mode: fake" in text
    assert "[renderer]  mode: diagram" in text
    assert "[persistence]  mode: memory" in text
    assert f"[limits]  mode: {RunLimits().label()}" in text
    # each fallback says what the family will see
    assert "placeholder color" in text
    assert "silent narration" in text and "estimated timings" in text
    assert "diagrams" in text
    assert "lost when the API restarts" in text
    assert "result: 1 error(s)" in text


def test_json_report_shape() -> None:
    code, out = run(["--json"])
    assert code == 1
    report = json.loads(out)
    assert isinstance(report, list)
    assert [r["service"] for r in report] == SERVICES
    for entry in report:
        assert {"service", "mode", "vars", "live", "fallback"} <= set(entry)
        assert {"status", "detail"} <= set(entry["live"])
        for v in entry["vars"]:
            assert {"name", "set", "value", "problem", "severity"} <= set(v)
    model = by_service(out)["model"]
    assert var(model, "ANTHROPIC_API_KEY")["set"] is False
    assert var(model, "ANTHROPIC_API_KEY")["problem"]
    assert model["live"]["status"] == "not_run"


def test_bad_arguments_exit_2(capsys: pytest.CaptureFixture[str]) -> None:
    assert preflight.main(["--nope"], out=io.StringIO(), seams=seams()) == 2
    assert preflight.main(["--help"], out=io.StringIO(), seams=seams()) == 0
    capsys.readouterr()


def test_with_a_good_anthropic_key_it_exits_0(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", ANTHROPIC)
    monkeypatch.setenv("STITCH_FAKE_DB", "1")
    code, text = run([])
    assert code == 0, text
    assert "set (" in text and "format ok" in text
    assert "[agent]  mode: fake" in text  # STITCH_FAKE_DB makes main.py's default fake
    assert "studio services: model=real subagent_model=studio repo=mongo persistence=memory" in text
    assert f"limits={RunLimits().label()}" in text
    assert "next: uv run python -m agent.preflight --live" in text


@pytest.mark.parametrize("as_json", [False, True])
def test_secret_values_never_appear(monkeypatch: pytest.MonkeyPatch, as_json: bool) -> None:
    set_all_keys(monkeypatch)
    code, out = run(["--json"] if as_json else [])
    for secret in SECRETS:
        assert secret not in out
    assert MONGO_USER not in out
    if not as_json:
        assert f"set ({len(ANTHROPIC)} chars), format ok" in out
        assert "mongodb+srv://<credentials redacted>@cluster0.example.mongodb.net" in out
    assert code == 0, out


@pytest.mark.parametrize(
    ("value", "needle"),
    [
        (f'"{ANTHROPIC}"', "quotes"),
        (f"'{ANTHROPIC}'", "quotes"),
        (f"{ANTHROPIC} ", "whitespace"),
        (f" {ANTHROPIC}", "whitespace"),
        (f"{ANTHROPIC}\n", "newline"),
        (f"{ANTHROPIC[:20]}\n{ANTHROPIC[20:]}", "newline"),
    ],
)
def test_shape_problems_are_errors(monkeypatch: pytest.MonkeyPatch, value: str, needle: str) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", value)
    monkeypatch.setenv("STITCH_FAKE_DB", "1")
    code, out = run(["--json"])
    key = var(by_service(out)["model"], "ANTHROPIC_API_KEY")
    assert key["severity"] == "error" and needle in key["problem"]
    assert code == 1
    assert ANTHROPIC not in out and ANTHROPIC[:20] not in out
    code, text = run([])
    assert ANTHROPIC not in text and ANTHROPIC[:20] not in text


def test_prefix_checks_are_warnings_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "not-an-anthropic-key-xyz")
    monkeypatch.setenv(images_adapter.API_KEY_ENV, "img-not-openai-xyz")
    monkeypatch.setenv(tts_adapter.API_KEY_ENV, "eleven-not-sk-xyz")
    monkeypatch.setenv("STITCH_FAKE_DB", "1")
    code, out = run(["--json"])
    report = by_service(out)
    assert "sk-ant-" in var(report["model"], "ANTHROPIC_API_KEY")["problem"]
    assert var(report["model"], "ANTHROPIC_API_KEY")["severity"] == "warning"
    assert var(report["art"], images_adapter.API_KEY_ENV)["severity"] == "warning"
    assert var(report["tts"], tts_adapter.API_KEY_ENV)["severity"] == "warning"
    assert code == 0
    # A non-OpenAI image URL drops the sk- expectation.
    monkeypatch.setenv(images_adapter.URL_ENV, "https://images.example.com/v1")
    _, out = run(["--json"])
    assert var(by_service(out)["art"], images_adapter.API_KEY_ENV)["problem"] is None


def test_bad_mongodb_uri_scheme_is_flagged_and_redacted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", ANTHROPIC)
    monkeypatch.setenv("MONGODB_URI", f"postgres://{MONGO_USER}:{MONGO_PASSWORD}@db.example.com/stitch")
    code, out = run(["--json"])
    uri = var(by_service(out)["persistence"], "MONGODB_URI")
    assert uri["severity"] == "error" and "mongodb://" in uri["problem"]
    assert code == 1
    assert MONGO_PASSWORD not in out and MONGO_USER not in out
    _, text = run([])
    assert MONGO_PASSWORD not in text and MONGO_USER not in text


def test_redact_mongo_uri() -> None:
    assert preflight.redact_mongo_uri(MONGO_URI) == "mongodb+srv://<credentials redacted>@cluster0.example.mongodb.net"
    assert preflight.redact_mongo_uri("mongodb://a:b@h1:27017,h2:27017/db?replicaSet=x") == (
        "mongodb://<credentials redacted>@h1:27017,h2:27017"
    )
    assert preflight.redact_mongo_uri("mongodb://localhost:27017") == "mongodb://localhost:27017"
    assert preflight.redact_mongo_uri("p@ss") == "<not a URI>"
    # Unescaped '/' or '?' in the password: the credentials are still never shown.
    assert preflight.redact_mongo_uri("mongodb://user:pa/ssword@host/db") == "mongodb://<credentials redacted>@host"
    assert (
        preflight.redact_mongo_uri("mongodb+srv://user:p?w@c.x.net") == "mongodb+srv://<credentials redacted>@c.x.net"
    )
    # An '@' inside an option value does not move the host.
    assert preflight.redact_mongo_uri("mongodb://u:p@h/db?authSource=a@b") == "mongodb://<credentials redacted>@h"
    # An unescaped '@' and then '/' or '?' in the password: no piece after the first '@' is shown as a host.
    redacted = preflight.redact_mongo_uri("mongodb://user:p@ssXYZ/wordSECRET@host/db")
    assert redacted == "mongodb://<credentials redacted>@host"
    assert preflight.redact_mongo_uri("mongodb://user:p@ss?wordSECRET@host") == "mongodb://<credentials redacted>@host"


@pytest.mark.parametrize(
    "password",
    [
        f"pa/ss{MONGO_PASSWORD}",
        f"p?w{MONGO_PASSWORD}",
        f"pa:ss{MONGO_PASSWORD}",
        f"p%zz{MONGO_PASSWORD}",
        f"p@ssXYZ/word{MONGO_PASSWORD}",
        f"p@ssXYZ?word{MONGO_PASSWORD}",
    ],
)
@pytest.mark.parametrize("scheme", ["mongodb", "mongodb+srv"])
def test_unescaped_reserved_characters_in_mongodb_credentials(
    monkeypatch: pytest.MonkeyPatch, password: str, scheme: str
) -> None:
    """pymongo rejects these URIs; preflight must flag them and never print any part of the credentials."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", ANTHROPIC)
    monkeypatch.setenv("MONGODB_URI", f"{scheme}://{MONGO_USER}:{password}@cluster0.example.net/stitch")
    code, out = run(["--json"])
    uri = var(by_service(out)["persistence"], "MONGODB_URI")
    assert uri["severity"] == "error" and "percent-encode" in uri["problem"]
    assert code == 1
    _, text = run([])
    for shown in (out, text):
        assert MONGO_PASSWORD not in shown and MONGO_USER not in shown and "ssXYZ" not in shown
    assert f"{scheme}://<credentials redacted>@cluster0.example.net" in text


@pytest.mark.parametrize(
    "password",
    [
        f"pa/ss{MONGO_PASSWORD}",
        f"p@ssXYZ/word{MONGO_PASSWORD}",
        f"p@ssXYZ?word{MONGO_PASSWORD}",
        f"pa:ss{MONGO_PASSWORD}",
    ],
)
def test_live_never_pings_a_malformed_mongodb_uri(monkeypatch: pytest.MonkeyPatch, password: str) -> None:
    """pymongo's InvalidURI quotes part of the password ('Bad database name ...'): with a format
    error the ping is skipped, so neither the URI nor any fragment of it reaches pymongo or the output."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", ANTHROPIC)
    monkeypatch.setenv("MONGODB_URI", f"mongodb://{MONGO_USER}:{password}@host/db")
    mongo = FakeMongo()
    for argv in (["--live", "--json"], ["--live"]):
        code, out = run(argv, seams(claude=FakeAnthropic(), mongo=mongo))
        assert code == 1
        assert MONGO_PASSWORD not in out and MONGO_USER not in out and "ssXYZ" not in out
    assert mongo.opened == []
    _, out = run(["--live", "--json"], seams(claude=FakeAnthropic(), mongo=mongo))
    live = by_service(out)["persistence"]["live"]
    assert live["status"] == "skipped" and "format error" in live["detail"]


def test_password_fragments_split_at_reserved_characters_are_scrubbed() -> None:
    """A parser error that quotes a piece of the password split at a reserved character
    (pymongo's 'Bad database name "sswordSECRET@host/db"') never prints that piece."""
    for uri, fragment in (
        ("mongodb://user:pa/sswordSECRET@host/db", "sswordSECRET"),
        ("mongodb://user:p@ssXYZ/wordSECRET@host/db", "wordSECRET"),
        ("mongodb://user:p@ssXYZ?wordSECRET@host", "ssXYZ"),
    ):
        secrets = preflight.secret_values({"MONGODB_URI": uri})
        text = preflight.scrub(f'InvalidURI: Bad database name "{fragment}@host/db"', secrets)
        assert fragment not in text, (uri, text)
        assert "host/db" in text  # the host is not a secret
    # A well-formed URI scrubs its credentials and its query string, not a bare option value.
    secrets = preflight.secret_values({"MONGODB_URI": "mongodb://user:pass@h/db?authSource=admin@x"})
    assert "admin" not in secrets and "pass" in secrets and "authSource=admin@x" in secrets


def test_mongo_uri_problem_matches_pymongo() -> None:
    """mongodb:// URIs (no DNS): preflight flags a credentials problem exactly when pymongo refuses the URI."""
    from pymongo import uri_parser

    for uri in (
        "mongodb://u:p@h/db",
        "mongodb://u:p%2Fw@h/db",
        "mongodb://u:p#w@h/db",
        "mongodb://u:p%2Fw@h/db?authSource=a@b",
        "mongodb://u:p/w@h/db",
        "mongodb://u:p?w@h",
        "mongodb://u:p:w@h",
        "mongodb://u:p%w@h",
        "mongodb://u@v:p@h",
        "mongodb://localhost:27017",
        "mongodb://user:p@ssXYZ/wordSECRET@host/db",
        "mongodb://user:p@ss?wordSECRET@host",
    ):
        try:
            uri_parser.parse_uri(uri)
            pymongo_ok = True
        except Exception:  # noqa: BLE001 - InvalidURI, or a bad database name for '/'
            pymongo_ok = False
        assert (preflight.mongo_uri_problem(uri) is None) == pymongo_ok, uri
    # pymongo parses this one, but reads 'ssXYZ' (part of the password) as the host and
    # 'wordSECRET@host' as the database: preflight flags it rather than connect to the wrong host.
    assert preflight.mongo_uri_problem("mongodb://user:p@ssXYZ/wordSECRET@host") is not None


def test_image_url_query_string_is_never_shown(monkeypatch: pytest.MonkeyPatch) -> None:
    token = "imgSECRETqueryTOKEN0005"
    monkeypatch.setenv("ANTHROPIC_API_KEY", ANTHROPIC)
    monkeypatch.setenv("STITCH_FAKE_DB", "1")
    monkeypatch.setenv(images_adapter.API_KEY_ENV, IMAGE)
    monkeypatch.setenv(images_adapter.URL_ENV, f"https://x.example.com/v1?api-version=1&key={token}")
    for args in ([], ["--json"]):
        code, out = run(args)
        assert token not in out and IMAGE not in out
        assert "https://x.example.com/v1?<query redacted>" in out
        assert code == 0, out
    assert token in preflight.secret_values(dict(os.environ))
    assert (
        preflight.redact_url("https://u:pw@h.example.com/v1#frag")
        == "https://<credentials redacted>@h.example.com/v1?<query redacted>"
    )
    assert preflight.redact_url(WEB_URL) == WEB_URL


def test_secret_values_cover_encoded_forms_and_secret_named_variables() -> None:
    password = "p@ss/w0rd:SECRET!"
    env = {
        "MONGODB_URI": f"mongodb://{MONGO_USER}:{quote_plus(password)}@h/db",
        "JWT_SECRET": "jwtSECRETvalue000000000000",
        "SOME_OTHER_TOKEN": "tokenVALUE12345678",
        "HOME": "/Users/someone",
    }
    secrets = preflight.secret_values(env)
    for form in (password, quote_plus(password), quote(password, safe=""), "jwtSECRETvalue000000000000"):
        assert form in secrets, form
    assert "tokenVALUE12345678" in secrets
    assert "/Users/someone" not in secrets
    assert preflight.scrub(f"a {password.upper()} b", secrets) == "a [redacted] b"  # any letter case


@pytest.mark.parametrize(
    "voices",
    ["voice_a", "voice_a=", "voice_a=has space", "voice_a=bad/id", "=abc", '"voice_a=abc"'],
)
def test_malformed_elevenlabs_voices_are_flagged(monkeypatch: pytest.MonkeyPatch, voices: str) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", ANTHROPIC)
    monkeypatch.setenv("STITCH_FAKE_DB", "1")
    monkeypatch.setenv(tts_adapter.VOICES_ENV, voices)
    # Without the key the voices are unused: a warning.
    code, out = run(["--json"])
    assert var(by_service(out)["tts"], tts_adapter.VOICES_ENV)["severity"] == "warning"
    assert code == 0
    # With the key ElevenLabsTTS() would refuse them: an error.
    monkeypatch.setenv(tts_adapter.API_KEY_ENV, ELEVEN)
    code, out = run(["--json"])
    voices_var = var(by_service(out)["tts"], tts_adapter.VOICES_ENV)
    assert voices_var["severity"] == "error" and voices_var["problem"]
    assert code == 1
    assert ELEVEN not in out


def test_preflight_rejects_exactly_what_the_tts_adapter_rejects(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(tts_adapter.API_KEY_ENV, ELEVEN)
    for voices in ("voice_a=ok_id-1,voice_b=fine", "voice_a=bad/id", "voice_a"):
        monkeypatch.setenv(tts_adapter.VOICES_ENV, voices)
        try:
            tts_adapter.ElevenLabsTTS().close()
            adapter_ok = True
        except ValueError:
            adapter_ok = False
        report = {r.service: r for r in preflight.build_report(seams=seams())}
        flagged = any(v.severity == "error" for v in report["tts"].vars if v.name == tts_adapter.VOICES_ENV)
        assert flagged == (not adapter_ok), voices


def test_unknown_voice_names_and_a_bad_default_voice(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(tts_adapter.API_KEY_ENV, ELEVEN)
    monkeypatch.setenv(tts_adapter.VOICES_ENV, "mom=abc123")
    monkeypatch.setenv(tts_adapter.DEFAULT_VOICE_ENV, "not a voice")
    _, out = run(["--json"])
    tts = by_service(out)["tts"]
    assert var(tts, tts_adapter.VOICES_ENV)["severity"] == "warning"
    assert "voice_a" in var(tts, tts_adapter.VOICES_ENV)["problem"]
    assert var(tts, tts_adapter.DEFAULT_VOICE_ENV)["severity"] == "error"


@pytest.mark.parametrize(("value", "needle"), [("abc\n", "newline"), ('"abc"', "quotes"), ("abc ", "whitespace")])
def test_default_voice_gets_the_shape_check(monkeypatch: pytest.MonkeyPatch, value: str, needle: str) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", ANTHROPIC)
    monkeypatch.setenv("STITCH_FAKE_DB", "1")
    monkeypatch.setenv(tts_adapter.API_KEY_ENV, ELEVEN)
    monkeypatch.setenv(tts_adapter.DEFAULT_VOICE_ENV, value)
    code, out = run(["--json"])
    default = var(by_service(out)["tts"], tts_adapter.DEFAULT_VOICE_ENV)
    assert default["severity"] == "error" and needle in default["problem"]
    assert code == 1


@pytest.mark.parametrize("url", ["localhost:3000", "ftp://example.com", "http://", '"http://localhost:3000"'])
def test_web_url_must_be_http(monkeypatch: pytest.MonkeyPatch, url: str) -> None:
    monkeypatch.setenv("STITCH_WEB_URL", url)
    code, out = run(["--json"])
    assert var(by_service(out)["renderer"], "STITCH_WEB_URL")["severity"] == "error"
    assert code == 1


def test_playwright_mode_names_its_diagram_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", ANTHROPIC)
    monkeypatch.setenv("STITCH_FAKE_DB", "1")
    monkeypatch.setenv("STITCH_WEB_URL", WEB_URL)
    code, out = run(["--json"])
    renderer = by_service(out)["renderer"]
    assert renderer["mode"] == "playwright"
    assert "per render" in renderer["fallback"] and "__STITCH_READY__" in renderer["fallback"]
    assert code == 0


def test_unknown_stitch_agent_is_an_error_and_real_without_key_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STITCH_AGENT", "sometimes")
    _, out = run(["--json"])
    assert var(by_service(out)["agent"], "STITCH_AGENT")["severity"] == "error"
    monkeypatch.setenv("STITCH_AGENT", "real")
    _, out = run(["--json"])
    agent = by_service(out)["agent"]
    assert agent["mode"] == "fake" and agent["fallback"]


def test_real_tts_names_its_mid_run_silent_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """production: synthesize_narration retries a failed TTS call once, then narrates silently."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", ANTHROPIC)
    monkeypatch.setenv("STITCH_FAKE_DB", "1")
    monkeypatch.setenv(tts_adapter.API_KEY_ENV, ELEVEN)
    code, out = run(["--json"])
    tts = by_service(out)["tts"]
    assert tts["mode"] == "real"
    assert "silent narration" in tts["fallback"] and "one retry fails too" in tts["fallback"]
    assert "the run goes on" in tts["fallback"]
    assert code == 0


def test_art_fallback_names_the_image_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", ANTHROPIC)
    monkeypatch.setenv("STITCH_FAKE_DB", "1")
    monkeypatch.setenv(images_adapter.API_KEY_ENV, IMAGE)
    monkeypatch.setenv(guards.ENV_MAX_IMAGES_PER_RUN, "0")
    code, out = run(["--json"])
    art = by_service(out)["art"]
    assert art["mode"] == "openai"
    assert "STITCH_MAX_IMAGES_PER_RUN=0" in art["fallback"]
    assert any("every run gets placeholder art" in p["text"] for p in art["problems"])
    assert code == 0


# --------------------------------------------------------------------------- limits


def test_limits_section_shows_each_variable_and_the_value_in_force(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(guards.ENV_MAX_IMAGES_PER_RUN, "6")
    monkeypatch.setenv(guards.ENV_RUN_TIMEOUT_S, "900")
    _, out = run(["--json"])
    limits = by_service(out)["limits"]
    assert [v["name"] for v in limits["vars"]] == [name for name, _ in preflight.LIMIT_ENVS]
    assert var(limits, guards.ENV_MAX_IMAGES_PER_RUN) == {
        "name": guards.ENV_MAX_IMAGES_PER_RUN,
        "set": True,
        "value": "6",
        "problem": None,
        "severity": None,
    }
    assert var(limits, guards.ENV_RUN_TIMEOUT_S)["value"] == "900"
    assert var(limits, guards.ENV_MAX_GRAPH_STEPS) == {
        "name": guards.ENV_MAX_GRAPH_STEPS,
        "set": False,
        "value": "unset (default: 1000)",
        "problem": None,
        "severity": None,
    }
    expected = RunLimits(max_images_per_run=6, run_timeout_s=900.0).label()
    assert limits["mode"] == RunLimits.from_env(os.environ).label() == expected
    assert "run_timeout=900s" in expected
    assert "900 s of running time per run" in limits["summary"]


def test_limits_list_the_model_call_cap_with_the_other_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    assert preflight.LIMIT_ENVS[0] == (guards.ENV_MAX_MODEL_CALLS, "max_model_calls")
    assert guards.ENV_MAX_MODEL_CALLS in preflight.ENV_NAMES
    _, out = run(["--json"])
    limits = by_service(out)["limits"]
    assert var(limits, guards.ENV_MAX_MODEL_CALLS) == {
        "name": guards.ENV_MAX_MODEL_CALLS,
        "set": False,
        "value": "unset (default: 600)",
        "problem": None,
        "severity": None,
    }
    assert limits["summary"].startswith("at most 600 model calls and 24 image(s) a run")
    assert any("counts the studio's and every sub-agent's model calls together" in n for n in limits["notes"])
    assert any("bounds steps per studio graph stream only" in n and "fresh step budget" in n
               and "the per-run caps are STITCH_MAX_MODEL_CALLS and STITCH_RUN_TIMEOUT_S" in n
               for n in limits["notes"])

    monkeypatch.setenv(guards.ENV_MAX_MODEL_CALLS, "250")
    _, out = run(["--json"])
    limits = by_service(out)["limits"]
    assert var(limits, guards.ENV_MAX_MODEL_CALLS)["value"] == "250"
    assert limits["summary"].startswith("at most 250 model calls")
    _, text = run([])
    assert re.search(r"STITCH_MAX_MODEL_CALLS\s+250\n", text)


@pytest.mark.parametrize(("raw", "default"), [("99", "600"), ("lots", "600"), ("", "600"), ("150.5", "600")])
def test_an_invalid_model_call_cap_is_a_warning_and_the_default_stays(
    monkeypatch: pytest.MonkeyPatch, raw: str, default: str
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", ANTHROPIC)
    monkeypatch.setenv("STITCH_FAKE_DB", "1")
    monkeypatch.setenv(guards.ENV_MAX_MODEL_CALLS, raw)
    code, out = run(["--json"])
    limit = var(by_service(out)["limits"], guards.ENV_MAX_MODEL_CALLS)
    assert limit["set"] is True and limit["severity"] == "warning"
    assert f"default {default}" in limit["problem"]
    assert code == 0


# ------------------------------------------------------------------ model ids: blank and padded


@pytest.mark.parametrize("blank", ["", " ", "  \t "])
def test_a_blank_studio_model_is_reported_as_the_default_production_uses(
    monkeypatch: pytest.MonkeyPatch, blank: str
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", ANTHROPIC)
    monkeypatch.setenv("STITCH_FAKE_DB", "1")
    monkeypatch.setenv("STITCH_MODEL", blank)
    monkeypatch.setenv("STITCH_VISION_MODEL", blank)
    code, text = run([])
    assert code == 0
    assert f"studio model {studio.DEFAULT_MODEL};" in text
    assert "studio model ;" not in text and "studio model  ;" not in text
    assert f"Claude vision ({studio.DEFAULT_MODEL})" in text
    # The report names exactly what production builds.
    assert studio.make_model().model == studio.DEFAULT_MODEL
    assert preflight._studio_model(os.environ) == studio.DEFAULT_MODEL
    _, out = run(["--json"])
    model_var = var(by_service(out)["model"], "STITCH_MODEL")
    assert model_var["set"] is False and f"default: {studio.DEFAULT_MODEL}" in model_var["value"]


def test_a_padded_model_id_is_reported_stripped_as_production_uses_it(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", ANTHROPIC)
    monkeypatch.setenv("STITCH_FAKE_DB", "1")
    monkeypatch.setenv("STITCH_MODEL", "  claude-haiku-5-5 ")
    monkeypatch.setenv("STITCH_VISION_MODEL", " claude-sonnet-5-5")
    monkeypatch.setenv(images_adapter.API_KEY_ENV, IMAGE)
    monkeypatch.setenv(images_adapter.MODEL_ENV, " gpt-image-2 ")
    monkeypatch.setenv(tts_adapter.MODEL_ENV, "eleven_turbo_v2 ")
    code, out = run(["--json"])
    report = by_service(out)
    assert code == 0  # spaces around a model id are a warning: the studio strips them
    assert report["model"]["summary"].startswith("studio model claude-haiku-5-5;")
    assert var(report["model"], "STITCH_MODEL")["value"] == "claude-haiku-5-5"
    assert var(report["model"], "STITCH_MODEL")["severity"] == "warning"
    assert "Claude vision (claude-sonnet-5-5)" in report["vision"]["summary"]
    assert "(gpt-image-2 at" in report["art"]["summary"]
    assert var(report["tts"], tts_adapter.MODEL_ENV)["value"] == "eleven_turbo_v2"
    # Production uses the same stripped ids.
    assert studio.make_model().model == "claude-haiku-5-5"
    assert images_adapter.OpenAIImageModel(api_key=IMAGE).model == "gpt-image-2"
    assert tts_adapter.ElevenLabsTTS(api_key=ELEVEN).model == "eleven_turbo_v2"
    from agent.adapters.vision import AnthropicVision

    assert AnthropicVision(client=object()).model == "claude-sonnet-5-5"


def test_a_model_id_with_inner_spaces_or_quotes_is_still_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", ANTHROPIC)
    monkeypatch.setenv("STITCH_FAKE_DB", "1")
    monkeypatch.setenv("STITCH_MODEL", '"claude-haiku-5-5"')
    code, out = run(["--json"])
    assert var(by_service(out)["model"], "STITCH_MODEL")["severity"] == "error"
    assert code == 1


def test_the_run_timeout_is_a_rule_not_a_caveat(monkeypatch: pytest.MonkeyPatch) -> None:
    """runs.py enforces STITCH_RUN_TIMEOUT_S: preflight states the rule and never warns it is unenforced."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", ANTHROPIC)
    monkeypatch.setenv("STITCH_FAKE_DB", "1")
    assert hasattr(runs, "RUN_TIMEOUT_ERROR")
    assert not hasattr(preflight, "run_timeout_enforced")
    code, out = run(["--json"])
    limits = by_service(out)["limits"]
    assert limits["problems"] == []
    assert any("never while it waits on the teller" in n for n in limits["notes"])
    assert "not enforced" not in out
    assert code == 0
    _, text = run([])
    assert "not enforced" not in text and "never while it waits on the teller" in text


@pytest.mark.parametrize(
    ("name", "raw", "default"),
    [
        (guards.ENV_MAX_GRAPH_STEPS, "10", "1000"),  # below the minimum of 50
        (guards.ENV_MAX_IMAGES_PER_RUN, "-1", "24"),
        (guards.ENV_MAX_IMAGES_PER_RUN, "2.5", "24"),
        (guards.ENV_MODEL_TIMEOUT_S, "soon", "120"),
        (guards.ENV_MODEL_TIMEOUT_S, "nan", "120"),
        (guards.ENV_RUN_TIMEOUT_S, "0", "1200"),
        (guards.ENV_MODEL_MAX_RETRIES, "", "2"),
    ],
)
def test_an_invalid_limit_is_a_warning_and_the_default_stays(
    monkeypatch: pytest.MonkeyPatch, name: str, raw: str, default: str
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", ANTHROPIC)
    monkeypatch.setenv("STITCH_FAKE_DB", "1")
    monkeypatch.setenv(name, raw)
    code, out = run(["--json"])
    limit = var(by_service(out)["limits"], name)
    assert limit["set"] is True and limit["severity"] == "warning"
    assert f"default {default}" in limit["problem"] and f"default {default}" in limit["value"]
    assert code == 0  # a warning, not a failure: RunLimits keeps the default
    _, text = run([])
    assert "warning(s); exit 0" in text and f"RunLimits keeps the default {default}" in text


# ------------------------------------------------------------------------ drift tests


def test_env_files_match_main() -> None:
    import main

    assert preflight.ENV_FILES == main.ENV_FILES
    assert preflight.dotenv_enabled() is False  # under pytest, like main.py


def test_load_env_files_never_overrides(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    first, second, missing = tmp_path / "a.env", tmp_path / "b.env", tmp_path / "none.env"
    first.write_text("STITCH_PREFLIGHT_A=from_first\nSTITCH_PREFLIGHT_B=from_first\n")
    second.write_text("STITCH_PREFLIGHT_B=from_second\nSTITCH_PREFLIGHT_C=from_second\nSTITCH_PREFLIGHT_SHELL=file\n")
    monkeypatch.setenv("STITCH_PREFLIGHT_SHELL", "shell")
    for name in ("STITCH_PREFLIGHT_A", "STITCH_PREFLIGHT_B", "STITCH_PREFLIGHT_C"):
        monkeypatch.delenv(name, raising=False)
    try:
        assert preflight.load_env_files([first, missing, second]) == [first, second]
        assert os.environ["STITCH_PREFLIGHT_A"] == "from_first"
        assert os.environ["STITCH_PREFLIGHT_B"] == "from_first"
        assert os.environ["STITCH_PREFLIGHT_C"] == "from_second"
        assert os.environ["STITCH_PREFLIGHT_SHELL"] == "shell"
    finally:
        for name in ("STITCH_PREFLIGHT_A", "STITCH_PREFLIGHT_B", "STITCH_PREFLIGHT_C"):
            monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize(
    "env",
    [
        {"STITCH_AGENT": None, "ANTHROPIC_API_KEY": None},
        {"STITCH_AGENT": None, "ANTHROPIC_API_KEY": ANTHROPIC},
        {"STITCH_AGENT": None, "ANTHROPIC_API_KEY": "  "},
        {"STITCH_AGENT": None, "ANTHROPIC_API_KEY": ANTHROPIC, "STITCH_FAKE_DB": "yes"},
        {"STITCH_AGENT": "REAL", "ANTHROPIC_API_KEY": None},
        {"STITCH_AGENT": " off ", "ANTHROPIC_API_KEY": ANTHROPIC},
        {"STITCH_AGENT": "fake", "ANTHROPIC_API_KEY": ANTHROPIC},
        {"STITCH_AGENT": "bogus", "ANTHROPIC_API_KEY": ANTHROPIC},
    ],
)
def test_agent_mode_matches_main(monkeypatch: pytest.MonkeyPatch, env: dict[str, str | None]) -> None:
    import main

    for name, value in env.items():
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    assert preflight.agent_mode(os.environ)[0] == main.agent_mode()


COMBOS = [
    {},
    {"IMAGE_API_KEY": IMAGE},
    {"ELEVENLABS_API_KEY": ELEVEN},
    {"IMAGE_API_KEY": "   ", "ELEVENLABS_API_KEY": "  "},
    {"STITCH_WEB_URL": WEB_URL},
    {"STITCH_WEB_URL": "  "},
    {"STITCH_SUBAGENT_MODEL": "claude-sonnet-5-5"},
    {"STITCH_SUBAGENT_MODEL": "  "},
    {"STITCH_FAKE_DB": "1"},
    {"STITCH_FAKE_DB": "1", "MONGODB_URI": MONGO_URI},
    {"MONGODB_URI": MONGO_URI},
    {"MONGODB_URI": "  "},
    {"STITCH_MAX_IMAGES_PER_RUN": "3", "STITCH_MODEL_TIMEOUT_S": "45.5", "STITCH_MODEL_MAX_RETRIES": "0"},
    {"STITCH_MAX_GRAPH_STEPS": "10", "STITCH_RUN_TIMEOUT_S": "never"},
    {
        "IMAGE_API_KEY": IMAGE,
        "ELEVENLABS_API_KEY": ELEVEN,
        "STITCH_WEB_URL": WEB_URL,
        "STITCH_SUBAGENT_MODEL": "claude-sonnet-5-5",
        "MONGODB_URI": MONGO_URI,
        "STITCH_MAX_IMAGES_PER_RUN": "0",
    },
]


@pytest.mark.parametrize("combo", COMBOS)
def test_modes_match_production_services(
    monkeypatch: pytest.MonkeyPatch, no_network: list[Any], combo: dict[str, str]
) -> None:
    """production_services() with its one connecting constructor (Mongo persistence) faked."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", ANTHROPIC)
    for name, value in combo.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(studio, "mongo_persistence", lambda *a, **k: (InMemorySaver(), InMemoryStore()))
    labels = production.production_services().labels
    predicted = preflight.modes(os.environ)
    assert predicted.keys() == labels.keys()
    assert predicted == labels
    assert predicted["limits"] == RunLimits.from_env(os.environ).label()
    reports = {r.service: r for r in preflight.build_report(os.environ, seams=seams())}
    for service in ("art", "tts", "renderer", "persistence", "model", "vision", "compiler", "limits"):
        assert reports[service].mode == labels[service], service
    assert no_network == []


def test_expected_log_line_matches_production_describe(monkeypatch: pytest.MonkeyPatch, no_network: list[Any]) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", ANTHROPIC)
    monkeypatch.setenv("IMAGE_API_KEY", IMAGE)
    monkeypatch.setenv("STITCH_FAKE_DB", "1")
    monkeypatch.setenv("STITCH_MAX_IMAGES_PER_RUN", "5")
    services = production.production_services()
    reports = preflight.build_report(os.environ, seams=seams())
    assert preflight.expected_log_line(reports, os.environ) == production.describe(services)


def test_modes_without_the_key_say_missing() -> None:
    labels = preflight.modes({})
    assert labels["model"] == labels["vision"] == labels["critic"] == "missing"
    assert labels["limits"] == RunLimits().label()


# ------------------------------------------------------------------------------- live


def test_live_all_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    set_all_keys(monkeypatch)
    seen: list[httpx.Request] = []
    claude, mongo, browser = FakeAnthropic(), FakeMongo(), FakeBrowser("contract")
    code, out = run(["--live", "--json"], seams(handler=ok_handler(seen), claude=claude, mongo=mongo, browser=browser))
    report = by_service(out)
    assert code == 0, out
    for service in ("model", "vision", "art", "tts", "persistence", "renderer"):
        assert report[service]["live"]["status"] == "pass", (service, report[service]["live"])
    for service in ("agent", "compiler", "limits"):
        assert report[service]["live"]["status"] == "skipped"

    retrieved = [arg for name, arg in claude.calls if name == "models.retrieve"]
    assert retrieved == ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"]
    creates = [arg for name, arg in claude.calls if name == "messages.create"]
    assert len(creates) == 1
    assert creates[0]["max_tokens"] == 1 and creates[0]["model"] == "claude-opus-5-5"
    assert claude.keys and all(k == ANTHROPIC for k in claude.keys)

    urls = [str(r.url) for r in seen]
    assert f"{images_adapter.DEFAULT_URL}/models/{images_adapter.DEFAULT_MODEL}" in urls
    assert f"{tts_adapter.BASE_URL}/v1/voices/{tts_adapter.DEFAULT_VOICE_ID}" in urls
    assert f"{tts_adapter.BASE_URL}/v1/voices/{VOICE_A}" in urls
    assert f"{tts_adapter.BASE_URL}/v1/voices/{VOICE_B}" in urls
    image_req = next(r for r in seen if "/models/" in r.url.path)
    assert image_req.method == "GET" and image_req.headers["authorization"] == f"Bearer {IMAGE}"
    voice_req = next(r for r in seen if "/voices/" in r.url.path)
    assert voice_req.headers["xi-api-key"] == ELEVEN

    assert mongo.opened == [(MONGO_URI, 5000)] and mongo.commands == ["ping"] and mongo.closed == 1
    assert browser.opened == 1 and browser.page.urls == [f"{WEB_URL}/render/{frames.PROBE_MEMORY_ID}?t=0.000"]
    for secret in SECRETS:
        assert secret not in out


@pytest.mark.parametrize(
    ("kind", "state", "status"),
    [
        ("contract", "contract", "pass"),
        ("legacy", "legacy", "warn"),
        ("down", "unreachable", "fail"),
        ("no_playwright", "no_playwright", "warn"),
    ],
)
def test_render_probe_states_map_to_pass_warn_fail(
    monkeypatch: pytest.MonkeyPatch, kind: str, state: str, status: str
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", ANTHROPIC)
    monkeypatch.setenv("STITCH_FAKE_DB", "1")
    monkeypatch.setenv("STITCH_WEB_URL", WEB_URL + "/")
    browser = FakeBrowser(kind)
    code, out = run(["--live", "--json"], seams(browser=browser))
    live = by_service(out)["renderer"]["live"]
    assert live["status"] == status, live
    assert f": {state}: " in live["detail"]
    assert preflight.RENDER_STATES[state][1] in live["detail"]
    assert code == (1 if status == "fail" else 0)
    _, text = run(["--live"], seams(browser=FakeBrowser(kind)))
    assert f"live: {status}: render page {WEB_URL}: {state}" in text
    if status == "warn":
        assert "warning(s); exit 0" in text
    if kind == "legacy":
        assert "__STITCH_RENDER__/__STITCH_READY__" in text and "diagrams" in text


def test_render_probe_answers_through_a_monkeypatched_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    """The four answers, straight from probe_render_page, and the arguments it is called with."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", ANTHROPIC)
    monkeypatch.setenv("STITCH_FAKE_DB", "1")
    monkeypatch.setenv("STITCH_WEB_URL", WEB_URL)
    calls: list[tuple[str, int, Any]] = []
    factory = FakeBrowser()
    for state, (status, _) in preflight.RENDER_STATES.items():

        def probe(base_url: str, *, timeout_ms: int, page_factory: Any = None, state: str = state) -> str:
            calls.append((base_url, timeout_ms, page_factory))
            return state

        monkeypatch.setattr(frames, "probe_render_page", probe)
        _, out = run(["--live", "--json"], seams(browser=factory))
        assert by_service(out)["renderer"]["live"]["status"] == status, state
    assert calls == [(WEB_URL, preflight.RENDER_PROBE_TIMEOUT_MS, factory)] * 4


def test_render_probe_is_skipped_without_stitch_web_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", ANTHROPIC)
    monkeypatch.setenv("STITCH_FAKE_DB", "1")
    code, out = run(["--live", "--json"])  # the default seams fail the test on any probe
    live = by_service(out)["renderer"]["live"]
    assert live["status"] == "skipped" and "diagram" in live["detail"]
    assert code == 0


def test_live_401_shows_the_provider_error_and_scrubs_the_key(monkeypatch: pytest.MonkeyPatch) -> None:
    set_all_keys(monkeypatch, mongo=False, web=False)
    monkeypatch.setenv("STITCH_FAKE_DB", "1")

    def handler(request: httpx.Request) -> httpx.Response:
        key = request.headers.get("authorization", "").removeprefix("Bearer ") or request.headers.get("xi-api-key")
        return httpx.Response(401, json={"error": {"message": AUTH_ERROR_BODY.format(key=key)}})

    request = httpx.Request("GET", "https://api.anthropic.com/v1/models/claude-opus-5-5")
    auth = anthropic.AuthenticationError(
        f"Error code: 401 - invalid x-api-key {ANTHROPIC}",
        response=httpx.Response(401, request=request),
        body={"type": "error", "error": {"type": "authentication_error", "message": "invalid x-api-key"}},
    )
    claude = FakeAnthropic(error=auth)
    for argv in (["--live"], ["--live", "--json"]):
        code, out = run(argv, seams(handler=handler, claude=claude))
        assert code == 1
        for secret in SECRETS:
            assert secret not in out
        assert "Incorrect API key provided" in out
        assert "HTTP 401" in out
        assert "AuthenticationError" in out and "invalid x-api-key" in out
    report = by_service(out)
    for service in ("model", "vision", "art", "tts"):
        assert report[service]["live"]["status"] == "fail", service
        assert len(report[service]["live"]["detail"]) < 2 * preflight.DETAIL_CHARS
    # A bad key fails every call the same way: the first failure stops the Anthropic checks.
    assert [name for name, _ in claude.calls].count("messages.create") == 0


def test_live_errors_are_scrubbed_before_they_are_cut(monkeypatch: pytest.MonkeyPatch) -> None:
    """A key that straddles DETAIL_CHARS is scrubbed whole: no prefix of it survives the cut."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", ANTHROPIC)
    monkeypatch.setenv("STITCH_FAKE_DB", "1")
    monkeypatch.setenv(images_adapter.API_KEY_ENV, IMAGE)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="x" * (preflight.DETAIL_CHARS - 30) + IMAGE + "y" * 5000)

    _, out = run(["--live", "--json"], seams(handler=handler))
    detail = by_service(out)["art"]["live"]["detail"]
    assert "HTTP 500" in detail
    assert len(detail) <= preflight.DETAIL_CHARS + len(f"GET /models/{images_adapter.DEFAULT_MODEL}: ")
    assert IMAGE[:12] not in out and IMAGE[:8] not in out


def test_live_timeouts_are_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    set_all_keys(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    mongo = FakeMongo(error=ServerSelectionTimeoutError("cluster0.example.mongodb.net:27017: timed out"))
    claude = FakeAnthropic(error=anthropic.APITimeoutError(request=httpx.Request("GET", "https://api.anthropic.com")))
    code, out = run(
        ["--live", "--json"], seams(handler=handler, claude=claude, mongo=mongo, browser=FakeBrowser("down"))
    )
    assert code == 1
    report = by_service(out)
    for service in ("model", "vision", "art", "tts", "persistence"):
        assert report[service]["live"]["status"] == "fail", service
        assert "timed out" in report[service]["live"]["detail"], service
    assert report["renderer"]["live"]["status"] == "fail"
    for secret in SECRETS:
        assert secret not in out


def test_live_skips_unconfigured_services(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", ANTHROPIC)
    monkeypatch.setenv("STITCH_FAKE_DB", "1")
    mongo = FakeMongo()
    # The default handler and page factory fail the test on any HTTP call or probe.
    code, out = run(["--live", "--json"], seams(mongo=mongo))
    report = by_service(out)
    assert code == 0, out
    assert report["model"]["live"]["status"] == "pass"
    for service in ("art", "tts", "persistence", "renderer", "compiler", "limits"):
        assert report[service]["live"]["status"] == "skipped", service
    assert mongo.opened == []


def test_live_without_the_anthropic_key_skips_claude_and_still_exits_1(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STITCH_FAKE_DB", "1")
    claude = FakeAnthropic()
    code, out = run(["--live", "--json"], seams(claude=claude))
    assert code == 1
    assert by_service(out)["model"]["live"]["status"] == "skipped"
    assert claude.calls == []


def test_live_pings_the_default_database_when_mongodb_uri_is_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", ANTHROPIC)
    mongo = FakeMongo(error=ServerSelectionTimeoutError("localhost:27017: [Errno 61] Connection refused"))
    code, out = run(["--live", "--json"], seams(mongo=mongo))
    assert mongo.opened == [(preflight.DEFAULT_MONGODB_URI, 5000)]
    live = by_service(out)["persistence"]["live"]
    assert live["status"] == "fail" and "db.client's default" in live["detail"]
    assert code == 1


# ------------------------------------------------------------------- secrecy, end to end


ENCODED_PASSWORD = "p@ss/w0rd:SECRET+mongo!0007"
QUERY_TOKEN = "QUERYsecretTOKENvalue0008"
WEB_TOKEN = "WEBsecretTOKENvalue0009"
ENCODED_URI = (
    f"mongodb+srv://{MONGO_USER}:{quote_plus(ENCODED_PASSWORD)}@cluster0.example.mongodb.net/stitch"
    f"?retryWrites=true&appToken={QUERY_TOKEN}"
)


def all_forms(secret: str) -> set[str]:
    return {secret, quote(secret), quote(secret, safe=""), quote_plus(secret)}


def echoing_seams() -> preflight.LiveSeams:
    """Every seam fails, and every failure echoes every secret it might know, raw and URL-encoded."""
    echo = " ".join(
        form
        for secret in (ANTHROPIC, IMAGE, ELEVEN, ENCODED_PASSWORD, ENCODED_URI, QUERY_TOKEN, WEB_TOKEN)
        for form in sorted(all_forms(secret))
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text=f"bad key for {request.url}: {echo}")

    claude = FakeAnthropic(
        error=anthropic.APIConnectionError(
            message=f"cannot connect: {echo}", request=httpx.Request("GET", "https://api.anthropic.com")
        )
    )
    mongo = FakeMongo(error=OperationFailure(f"bad auth : authentication failed for {MONGO_USER}: {echo}"))
    return seams(handler=handler, claude=claude, mongo=mongo, browser=FakeBrowser("down", echo=echo))


def assert_no_secret(text: str) -> None:
    for secret in (ANTHROPIC, IMAGE, ELEVEN, ENCODED_PASSWORD, ENCODED_URI, QUERY_TOKEN, WEB_TOKEN, MONGO_USER):
        for form in all_forms(secret):
            assert form not in text, form


@pytest.mark.parametrize("argv", [["--live"], ["--live", "--json"]])
def test_no_secret_reaches_stdout_json_or_logs_when_every_provider_echoes_them(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, argv: list[str]
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", ANTHROPIC)
    monkeypatch.setenv(images_adapter.API_KEY_ENV, IMAGE)
    monkeypatch.setenv(tts_adapter.API_KEY_ENV, ELEVEN)
    monkeypatch.setenv("MONGODB_URI", ENCODED_URI)
    monkeypatch.setenv("STITCH_WEB_URL", f"https://web.example.com/?token={WEB_TOKEN}")
    caplog.set_level(logging.DEBUG)
    code, out = run(argv, echoing_seams())
    assert code == 1
    assert_no_secret(out)
    if "--json" in argv:
        report = by_service(out)  # still parses
        for service in ("model", "vision", "art", "tts", "persistence", "renderer"):
            assert report[service]["live"]["status"] == "fail", service
        assert "[redacted]" in report["persistence"]["live"]["detail"]
    # The render probe logs its failure with the URL and traceback: scrubbed too.
    assert any("render page probe" in r.getMessage() for r in caplog.records)
    assert_no_secret(caplog.text)
    assert_no_secret("\n".join(r.getMessage() for r in caplog.records))


def test_the_anthropic_factory_failing_with_the_key_is_scrubbed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", ANTHROPIC)
    monkeypatch.setenv("STITCH_FAKE_DB", "1")
    claude = FakeAnthropic(factory_error=ValueError(f"bad api_key {quote_plus(ANTHROPIC)} / {ANTHROPIC}"))
    code, out = run(["--live", "--json"], seams(claude=claude))
    assert code == 1
    assert_no_secret(out)
    assert by_service(out)["model"]["live"]["status"] == "fail"


def test_a_crash_inside_preflight_prints_a_scrubbed_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", ANTHROPIC)

    def boom(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError(f"unexpected {ANTHROPIC}")

    monkeypatch.setattr(preflight, "build_report", boom)
    code, out = run([])
    assert code == 1
    assert out.startswith("preflight failed: RuntimeError: unexpected [redacted]")
    assert ANTHROPIC not in out


@pytest.mark.parametrize("password", ["pässwört123", 'q"uote\\back12', "naïve/pass:wörd@99"])
@pytest.mark.parametrize("argv", [["--live"], ["--live", "--json"]])
def test_a_json_escaped_echo_of_a_password_is_scrubbed(
    monkeypatch: pytest.MonkeyPatch, password: str, argv: list[str]
) -> None:
    """A JSON error body echoes non-ASCII as \\uXXXX, and '"' and '\\' escaped."""
    monkeypatch.setenv("MONGODB_URI", f"mongodb://{MONGO_USER}:{quote_plus(password)}@db.example.com/stitch")
    escaped = {json.dumps(password)[1:-1], json.dumps(password, ensure_ascii=False)[1:-1]}
    assert escaped != {password}  # each case really is changed by JSON escaping
    echo = " ".join(sorted(escaped | all_forms(password)))
    mongo = FakeMongo(error=OperationFailure(f'{{"errmsg": "auth failed for {echo}"}}'))
    code, out = run(argv, seams(mongo=mongo))
    assert code == 1
    for form in escaped | all_forms(password):
        assert form not in out, form
    if "--json" in argv:
        assert "[redacted]" in by_service(out)["persistence"]["live"]["detail"]


def test_a_value_that_cannot_be_utf8_encoded_does_not_crash_preflight() -> None:
    """A bad byte in the environment arrives as a lone surrogate, which quote() cannot encode."""
    key = "sk-ant-abc\udcffdefghijkl"
    out = io.StringIO()
    code = preflight.main(
        ["--json"], environ={"ANTHROPIC_API_KEY": key, "STITCH_NO_DOTENV": "1"}, out=out, seams=seams()
    )
    assert code == 0, out.getvalue()
    assert key not in out.getvalue() and "sk-ant-abc" not in out.getvalue()
    assert by_service(out.getvalue())["model"]["mode"] == "real"
    assert key in preflight.secret_values({"ANTHROPIC_API_KEY": key})


def test_scrubbed_logs_rewrites_records_and_restores_handlers(caplog: pytest.LogCaptureFixture) -> None:
    log = logging.getLogger("agent.preflight.test")
    caplog.set_level(logging.INFO)
    before = [list(h.filters) for h in logging.getLogger().handlers]
    with preflight.scrubbed_logs([ANTHROPIC]):
        try:
            raise ValueError(f"key {ANTHROPIC}")
        except ValueError:
            log.info("calling with %s", ANTHROPIC, exc_info=True)
    assert ANTHROPIC not in caplog.text
    assert "calling with [redacted]" in caplog.text and "ValueError: key [redacted]" in caplog.text
    assert [list(h.filters) for h in logging.getLogger().handlers] == before


# ----------------------------------------------------------------------------- README


def test_readme_documents_every_var_the_limits_the_fallbacks_and_the_commands() -> None:
    readme = (Path(preflight.__file__).parent / "README.md").read_text()
    for name in preflight.ENV_NAMES:
        assert f"`{name}`" in readme, name
    for needle in (
        "uv run python -m agent.preflight",
        "uv run python -m agent.preflight --live",
        "agent.demo --auto",
        "uv run playwright install chromium",
        "python -m seed.serve",
        "STITCH_AGENT=fake",
        "studio services:",
        "Tell it again",
        "legacy",
        "State today: `contract`",
        "__STITCH_READY__",
        "STITCH_WEB_URL=http://localhost:3000",
        "its one retry failing too",
        "never while it waits on the teller",
        "| Service |",  # the fallback table
        "agent.demo --fake --auto --weekend",
    ):
        assert needle in readme, needle


README = Path(preflight.__file__).parent / "README.md"
_TABLE_VAR = re.compile(r"^\|\s*`([A-Z][A-Z0-9_]+)`\s*\|", re.MULTILINE)
"""The first cell of a README table row, when it is one env var in backticks."""


def readme_table_vars(text: str) -> set[str]:
    return set(_TABLE_VAR.findall(text))


def test_readme_tables_name_exactly_the_variables_preflight_reads() -> None:
    """Doc drift: every env var a README table lists is one preflight reports, and the other way round."""
    documented = readme_table_vars(README.read_text())
    reported = set(preflight.ENV_NAMES) | {name for name, _ in preflight.LIMIT_ENVS}
    assert documented, "no variable tables found in the README"
    assert documented - reported == set(), "in the README but not in preflight.ENV_NAMES/LIMIT_ENVS"
    assert reported - documented == set(), "in preflight.ENV_NAMES/LIMIT_ENVS but not in a README table"


def test_readme_table_parser_sees_rows_and_skips_prose() -> None:
    sample = "| `FOO_BAR` | x |\n| `STITCH_X_1` | `1` |\n| model | `real` |\nsee `NOT_A_ROW` here\n"
    assert readme_table_vars(sample) == {"FOO_BAR", "STITCH_X_1"}


_MODULE_COMMAND = re.compile(r"python -m (agent\.[a-z_]+)")


def test_every_agent_module_the_readme_runs_is_importable_with_a_main() -> None:
    modules = set(_MODULE_COMMAND.findall(README.read_text()))
    assert {"agent.preflight", "agent.demo"} <= modules
    for name in sorted(modules):
        module = importlib.import_module(name)
        assert callable(getattr(module, "main", None)), f"{name} has no main()"


def test_the_runbook_has_no_transitional_caveats() -> None:
    """runs.py enforces the run timeout and TTS degrades mid-run: no 'until then' notes remain."""
    text = README.read_text()
    for stale in (
        "not enforced",
        "once the run-timeout change",
        "Until then",
        "Depends on your checkout",
        "ends the run",
        "fails the run",
    ):
        assert stale not in text, stale
    assert "never while it waits on the teller" in text


def test_a_set_mongodb_uri_names_its_in_memory_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", ANTHROPIC)
    monkeypatch.setenv("MONGODB_URI", MONGO_URI)
    code, out = run(["--json"])
    persistence = by_service(out)["persistence"]
    assert persistence["mode"] == "mongo"
    assert "unreachable" in persistence["fallback"] and "persistence=memory" in persistence["fallback"]
    assert "lost when the API restarts" in persistence["fallback"]
    assert code == 0


# --------------------------------------------------------------- secrecy, every secret variable


SENTINELS: dict[str, str] = {
    "ANTHROPIC_API_KEY": "sk-ant-api03-SENTINELanthropic0101",
    images_adapter.API_KEY_ENV: "sk-proj-SENTINELimage0102",
    "HIGGSFIELD_API_KEY": "SENTINELhiggsfieldID0111",
    "HIGGSFIELD_API_SECRET": "SENTINELhiggsfieldSECRET0112",
    "CLOUDFLARE_ACCOUNT_ID": "SENTINELcloudflareACCOUNT0113",
    "CLOUDFLARE_API_TOKEN": "SENTINELcloudflareTOKEN0114",
    tts_adapter.API_KEY_ENV: "sk_SENTINELelevenlabs0103",
    "JWT_SECRET": "SENTINELjwtSECRET0104",
    "VOYAGE_API_KEY": "pa-SENTINELvoyage0105",
}
SENTINEL_MONGO_USER = "SENTINELmongoUSER0106"
SENTINEL_MONGO_PASSWORD = "SENTINELmongoPASS0107"
SENTINEL_TEST_USER = "SENTINELtestUSER0108"
SENTINEL_TEST_PASSWORD = "SENTINELtestPASS0109"
SENTINEL_WEB_TOKEN = "SENTINELwebTOKEN0110"
SENTINEL_URI = (
    f"mongodb+srv://{SENTINEL_MONGO_USER}:{SENTINEL_MONGO_PASSWORD}@cluster0.example.mongodb.net/stitch"
    "?retryWrites=true"
)
SENTINEL_TEST_URI = f"mongodb://{SENTINEL_TEST_USER}:{SENTINEL_TEST_PASSWORD}@test.example.com/stitch_test"


def every_sentinel() -> list[str]:
    return [
        *SENTINELS.values(),
        SENTINEL_MONGO_USER,
        SENTINEL_MONGO_PASSWORD,
        SENTINEL_TEST_USER,
        SENTINEL_TEST_PASSWORD,
        SENTINEL_WEB_TOKEN,
        SENTINEL_URI,
        SENTINEL_TEST_URI,
    ]


def set_sentinels(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in SENTINELS.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("MONGODB_URI", SENTINEL_URI)
    monkeypatch.setenv("MONGODB_URI_TEST", SENTINEL_TEST_URI)
    monkeypatch.setenv("STITCH_WEB_URL", f"https://web.example.com/?token={SENTINEL_WEB_TOKEN}")


def sentinel_echoing_seams() -> preflight.LiveSeams:
    """Every live seam fails with an error that echoes every key, both URIs and the web token back."""
    echo = " ".join(form for s in every_sentinel() for form in sorted(all_forms(s)))

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text=f"{request.url} {request.headers} rejected: {echo}")

    claude = FakeAnthropic(
        error=anthropic.AuthenticationError(
            message=f"invalid x-api-key {echo}",
            response=httpx.Response(401, request=httpx.Request("GET", "https://api.anthropic.com/v1/models")),
            body={"error": {"message": echo}},
        )
    )
    mongo = FakeMongo(error=OperationFailure(f"bad auth for {SENTINEL_URI}: {echo}"))
    return seams(handler=handler, claude=claude, mongo=mongo, browser=FakeBrowser("down", echo=echo))


def test_the_secret_list_covers_every_secret_variable_preflight_reads() -> None:
    """A new key-shaped variable in ENV_NAMES must be in SECRET_ENVS, or this sweep would miss it."""
    keyish = {n for n in preflight.ENV_NAMES if re.search(r"KEY|SECRET|TOKEN|PASSWORD|URI", n)}
    assert keyish <= set(preflight.SECRET_ENVS)
    assert set(SENTINELS) | {"MONGODB_URI", "MONGODB_URI_TEST"} == set(preflight.SECRET_ENVS)


@pytest.mark.parametrize("argv", [[], ["--json"], ["--live"], ["--live", "--json"]])
def test_no_sentinel_reaches_stdout_or_logs(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
    argv: list[str],
) -> None:
    set_sentinels(monkeypatch)
    caplog.set_level(logging.DEBUG)
    out = io.StringIO()
    s = sentinel_echoing_seams()
    code = preflight.main(argv, out=out, seams=s)
    text = out.getvalue()
    captured = capsys.readouterr()
    assert text.strip(), "preflight wrote nothing"
    if "--json" in argv:
        by_service(text)  # still valid JSON
    if "--live" in argv:
        assert code == 1  # every provider failed
        assert "[redacted]" in text
        assert any("render page probe" in r.getMessage() for r in caplog.records)
    else:
        assert code == 0, text
    for sentinel in every_sentinel():
        for form in all_forms(sentinel):
            for where, blob in (
                ("stdout", text),
                ("process stdout", captured.out),
                ("process stderr", captured.err),
                ("log text", caplog.text),
                ("log messages", "\n".join(r.getMessage() for r in caplog.records)),
            ):
                assert form not in blob, f"{sentinel!r} in {where}"
    # Every key shows only its length and format.
    if "--json" not in argv:
        for name in SENTINELS:
            if name in preflight.ENV_NAMES:
                assert re.search(rf"{name}\s+set \(\d+ chars\)", text), name
