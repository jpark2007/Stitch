"""main.py's Stitch Channels hooks (api/channels/README.md, "Integration hooks"): mounting
behind STITCH_CHANNELS exactly "1", startup channel indexes, the media body cap, the smaller
ElevenLabs post-call body cap (STITCH_CHANNELS_ELEVENLABS also exactly "1"), and the family
purge of the Channels collections.

Runs on mongomock with TestClient; no MongoDB, no provider, no network, no keys.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import subprocess
import sys
import textwrap
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

import main
from db import client
from db import families as families_db

API_DIR = Path(__file__).resolve().parents[2]
SECRET = "channels-hooks-test-secret-0123456789abcdef"
TOO_LARGE = {"detail": "That request is too large"}
CAP = main.MAX_CHANNELS_MEDIA_BODY_BYTES
NORMAL = main.MAX_JSON_BODY_BYTES
PHOTO = "/internal/channels/inbound-photo"
VOICE = "/internal/channels/inbound-voice"
ELEVEN = main.ELEVENLABS_POST_CALL_PATH
ELEVEN_CAP = main.MAX_ELEVENLABS_POST_CALL_BODY_BYTES
INTERNAL_PATHS = (
    "/internal/channels/inbound", "/internal/channels/authorize", PHOTO, VOICE,
    "/internal/channels/tick", "/internal/channels/enroll",
)
SETTINGS_PATHS = (
    ("GET", "/families/fam_a/channels/contacts"),
    ("GET", "/families/fam_a/channels/people/p1/contact"),
    ("POST", "/families/fam_a/channels/people/p1/enrollment"),
    ("PATCH", "/families/fam_a/channels/people/p1/contact"),
    ("POST", "/families/fam_a/channels/people/p1/contact/stop"),
)


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch, mdb):
    for name in ("STITCH_CHANNELS", "STITCH_FAKE_DB", "STITCH_CORS_ORIGINS", "STITCH_AGENT", "ANTHROPIC_API_KEY",
                 "VOYAGE_API_KEY", "STITCH_CHANNELS_WORKER", "STITCH_CHANNELS_SCHEDULER",
                 "STITCH_CHANNELS_ELEVENLABS"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("STITCH_AGENT", "off")
    monkeypatch.setenv("CHANNELS_SHARED_SECRET", SECRET)
    client.set_db(mdb)
    yield
    client.set_db(None)


@pytest.fixture
def index_calls(monkeypatch: pytest.MonkeyPatch) -> list:
    """Records every channels.ensure_channel_indexes call (and still creates the indexes)."""
    import channels

    calls: list = []
    real = channels.ensure_channel_indexes

    def spy(db):
        calls.append(db)
        real(db)

    monkeypatch.setattr(channels, "ensure_channel_indexes", spy)
    return calls


def _signed(path: str, body: bytes) -> dict[str, str]:
    timestamp, nonce = str(int(time.time())), str(uuid4())
    signed = f"POST\n{path}\n{timestamp}\n{nonce}\n".encode() + body
    return {"content-type": "application/json", "X-Channels-Timestamp": timestamp, "X-Channels-Nonce": nonce,
            "X-Channels-Signature": hmac.new(SECRET.encode(), signed, hashlib.sha256).hexdigest()}


def _unknown(c: TestClient, method: str) -> tuple[int, object, dict]:
    res = c.request(method, "/definitely/not/a/route", content=b"{}", headers={"content-type": "application/json"})
    return res.status_code, res.json(), {k: v for k, v in res.headers.items() if k != "content-length"}


# ------------------------------------------------------------------------------- off


@pytest.mark.parametrize("raw", [None, "", "0", "true", "TRUE", "yes", " 1", "1 ", "01", "on"])
def test_channels_enabled_only_for_exactly_one(monkeypatch, raw):
    if raw is None:
        monkeypatch.delenv("STITCH_CHANNELS", raising=False)
    else:
        monkeypatch.setenv("STITCH_CHANNELS", raw)
    assert main.channels_enabled() is False


def test_channels_enabled_for_exactly_one(monkeypatch):
    monkeypatch.setenv("STITCH_CHANNELS", "1")
    assert main.channels_enabled() is True


@pytest.mark.parametrize("raw", [None, "true", "yes", " 1"])
def test_off_every_channels_path_is_an_unknown_404(monkeypatch, index_calls, raw):
    if raw is not None:
        monkeypatch.setenv("STITCH_CHANNELS", raw)
    app = main.create_app()
    with TestClient(app) as c:
        for path in INTERNAL_PATHS:
            res = c.post(path, content=b"{}", headers={"content-type": "application/json"})
            assert (res.status_code, res.json()) == _unknown(c, "POST")[:2], path
        for method, path in SETTINGS_PATHS:
            res = c.request(method, path, content=b"{}", headers={"content-type": "application/json"})
            assert (res.status_code, res.json()) == _unknown(c, method)[:2], (method, path)
        paths = c.get("/openapi.json").json()["paths"]
    assert not [p for p in paths if "channels" in p]
    assert index_calls == []
    assert app.state.channels_enabled is False


@pytest.mark.parametrize("path", [PHOTO, VOICE])
def test_off_the_media_paths_keep_the_normal_body_limit(path):
    with TestClient(main.create_app()) as c:
        over = c.post(path, content=b"x" * (NORMAL + 1), headers={"content-type": "application/json"})
        assert (over.status_code, over.json()) == (413, TOO_LARGE)
        at = c.post(path, content=b"x" * NORMAL, headers={"content-type": "application/json"})
        assert at.status_code == 404


_NO_CHANNELS_SCRIPT = textwrap.dedent(
    """
    import sys
    from fastapi.testclient import TestClient
    import main

    with TestClient(main.create_app()) as c:
        assert c.get("/health").status_code == 200
        assert c.post("/internal/channels/inbound", json={}).status_code == 404
    leaked = sorted(k for k in sys.modules if k == "channels" or k.startswith("channels."))
    if leaked:
        print("channels modules imported:", leaked)
        sys.exit(3)
    print("ok")
    """
)


@pytest.mark.parametrize("raw", [None, "true"])
def test_off_the_channels_package_is_never_imported(raw):
    env = {k: v for k, v in os.environ.items() if not k.startswith("STITCH_") and k != "CHANNELS_SHARED_SECRET"}
    env.update({"STITCH_FAKE_DB": "1", "STITCH_AGENT": "off", "STITCH_NO_DOTENV": "1",
                "JWT_SECRET": os.environ["JWT_SECRET"]})
    if raw is not None:
        env["STITCH_CHANNELS"] = raw
    proc = subprocess.run([sys.executable, "-c", _NO_CHANNELS_SCRIPT], cwd=API_DIR, env=env,
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    assert proc.stdout.strip().endswith("ok")


# ------------------------------------------------------------------------------- on


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setenv("STITCH_CHANNELS", "1")


def test_on_routes_are_mounted_and_internal_ones_stay_out_of_openapi(on, index_calls, mdb):
    app = main.create_app()
    with TestClient(app) as c:
        # Signed and mounted through main's whole middleware stack: the worker is off.
        body = b"{}"
        tick = c.post("/internal/channels/tick", content=body, headers=_signed("/internal/channels/tick", body))
        assert tick.status_code == 200, tick.text
        assert tick.json() == {"processed": 0, "scheduler": "inactive"}
        # Unsigned internal requests reach the handlers (401), not an unknown path.
        for path in INTERNAL_PATHS:
            res = c.post(path, content=b"{}", headers={"content-type": "application/json"})
            assert res.status_code == 401, (path, res.status_code, res.text)
        # Cookie settings routes are there and want a session.
        for method, path in SETTINGS_PATHS:
            res = c.request(method, path, json={})
            assert res.status_code == 401, (method, path, res.status_code, res.text)
        paths = c.get("/openapi.json").json()["paths"]
    assert not [p for p in paths if p.startswith("/internal")]
    assert "/families/{fid}/channels/contacts" in paths
    assert "/families/{fid}/channels/people/{person_id}/enrollment" in paths
    assert app.state.channels_enabled is True
    assert index_calls == [mdb]  # once, on the installed database


def test_on_startup_creates_the_channel_indexes_on_the_real_path(on, mdb):
    with TestClient(main.create_app()):
        pass
    assert any(ix.get("unique") for name, ix in mdb.contacts.index_information().items() if name != "_id_")
    assert any("expireAfterSeconds" in ix for ix in mdb.channelMessages.index_information().values())
    assert any(ix.get("unique") for name, ix in mdb.questionBank.index_information().items() if name != "_id_")
    assert any(ix.get("unique") for name, ix in mdb.notifications.index_information().items() if name != "_id_")


def test_on_startup_creates_the_channel_indexes_on_the_fake_db_too(on, monkeypatch, index_calls):
    monkeypatch.setenv("STITCH_FAKE_DB", "1")
    client.set_db(None)
    with TestClient(main.create_app()):
        fake = client.get_db()
        assert index_calls == [fake]
        assert any("expireAfterSeconds" in ix for ix in fake.channelMessages.index_information().values())
    assert client._db_override is None


@pytest.mark.parametrize("fake_db", [False, True])
def test_on_a_channel_index_failure_stops_startup(on, monkeypatch, caplog, fake_db):
    import channels

    if fake_db:
        monkeypatch.setenv("STITCH_FAKE_DB", "1")
        client.set_db(None)

    def broken(_db):
        raise RuntimeError("unique index conflict")

    monkeypatch.setattr(channels, "ensure_channel_indexes", broken)
    with pytest.raises(RuntimeError, match="unique index conflict"):
        with TestClient(main.create_app()):
            pass  # pragma: no cover - startup must not get here
    assert any("refusing to start" in r.getMessage() for r in caplog.records)
    if fake_db:
        assert client._db_override is None


@pytest.mark.parametrize("path", [PHOTO, VOICE])
def test_on_the_media_paths_take_a_body_up_to_the_cap(on, path):
    assert CAP == 27_978_412

    def unsigned() -> dict[str, str]:
        # Well-formed but wrongly signed: the handler gets past its header checks and reads
        # the body, so the size cap (not a missing header) is what decides.
        return {
            "content-type": "application/json",
            "x-channels-timestamp": str(int(time.time())),
            "x-channels-nonce": str(uuid4()),
            "x-channels-signature": "0" * 64,
        }

    def chunks(size: int):  # no Content-Length: counted as it streams
        for _ in range(size // 1_000_000):
            yield b"x" * 1_000_000
        yield b"x" * (size % 1_000_000)

    with TestClient(main.create_app()) as c:
        at = c.post(path, content=b"x" * CAP, headers=unsigned())
        # Not refused by the size middleware; the handler then refuses the signature.
        assert (at.status_code, at.json()) == (401, {"detail": "Invalid channel authentication"}), at.text
        over = c.post(path, content=b"x" * (CAP + 1), headers=unsigned())
        assert (over.status_code, over.json()) == (413, TOO_LARGE)

        streamed_at = c.post(path, content=chunks(CAP), headers=unsigned())
        assert (streamed_at.status_code, streamed_at.json()) == (
            401, {"detail": "Invalid channel authentication"}), streamed_at.text
        streamed = c.post(path, content=chunks(CAP + 1), headers=unsigned())
        assert (streamed.status_code, streamed.json()) == (413, TOO_LARGE)

        # Without the X-Channels headers the request is refused, before or after the body
        # is read (401 from the header check, or 413 from the size cap), never accepted.
        headerless = c.post(path, content=chunks(CAP + 1), headers={"content-type": "application/json"})
        assert headerless.status_code in (401, 413), headerless.text


@pytest.mark.parametrize("path", [
    "/internal/channels/inbound", "/internal/channels/authorize", "/internal/channels/tick",
    "/internal/channels/enroll", PHOTO + "/", VOICE + "/", PHOTO.upper(), "/internal/channels/inbound-photos",
    "/internal/channels/inbound-photo/x", "/internal/channels", "/families/fam_a/memories",
    "//internal/channels/inbound-photo",
])
def test_on_every_other_path_keeps_the_normal_limit(on, path):
    with TestClient(main.create_app()) as c:
        res = c.post(path, content=b"x" * (NORMAL + 1), headers={"content-type": "application/json"})
    assert (res.status_code, res.json()) == (413, TOO_LARGE), path


def test_on_a_query_string_does_not_change_the_media_limit(on):
    with TestClient(main.create_app()) as c:
        over = c.post(PHOTO + "?big=1", content=b"x" * (CAP + 1), headers={"content-type": "application/json"})
        assert (over.status_code, over.json()) == (413, TOO_LARGE)
        other = c.post("/internal/channels/inbound?/internal/channels/inbound-photo", content=b"x" * (NORMAL + 1),
                       headers={"content-type": "application/json"})
        assert (other.status_code, other.json()) == (413, TOO_LARGE)


def test_on_multipart_web_uploads_keep_their_own_limit(on):
    mw = next(m for m in main.create_app().user_middleware if m.cls is main.BodySizeLimitMiddleware)
    assert mw.kwargs["path_limits"] == {PHOTO: CAP, VOICE: CAP}
    assert mw.kwargs["multipart_limit"] == main.MAX_MULTIPART_BODY_BYTES
    assert mw.kwargs["json_limit"] == NORMAL


def test_off_the_middleware_has_no_path_limits():
    mw = next(m for m in main.create_app().user_middleware if m.cls is main.BodySizeLimitMiddleware)
    assert not mw.kwargs["path_limits"]


# ------------------------------------------------------------------------------- elevenlabs


def _body_limit_kwargs() -> dict:
    """The BodySizeLimitMiddleware kwargs create_app() uses under the current environment."""
    mw = next(m for m in main.create_app().user_middleware if m.cls is main.BodySizeLimitMiddleware)
    return dict(mw.kwargs)


def _stub_app() -> tuple[FastAPI, list[int]]:
    """A bare app with a stub route at the ElevenLabs path behind create_app()'s real body caps.

    The stub reads the whole body and records its length, so a request that reaches it got
    through the middleware. Not create_app() itself: the real webhook route may be mounted there.
    """
    seen: list[int] = []
    app = FastAPI()

    @app.post(ELEVEN)
    async def post_call(request: Request) -> dict[str, int]:
        body = await request.body()
        seen.append(len(body))
        return {"bytes": len(body)}

    app.add_middleware(main.BodySizeLimitMiddleware, **_body_limit_kwargs())
    return app, seen


def _chunks(size: int, step: int = 65_536):  # no Content-Length: counted as it streams
    for _ in range(size // step):
        yield b"x" * step
    yield b"x" * (size % step)


@pytest.fixture
def eleven(on, monkeypatch):
    monkeypatch.setenv("STITCH_CHANNELS_ELEVENLABS", "1")


def test_eleven_constants():
    assert ELEVEN_CAP == 262_144 < NORMAL
    assert ELEVEN == "/internal/channels/elevenlabs/post-call"


@pytest.mark.parametrize("channels", [None, "", "0", "1", "true", " 1", "01", "1 "])
@pytest.mark.parametrize("elevenlabs", [None, "", "0", "1", "true", " 1", "01", "1 "])
def test_eleven_enabled_only_when_both_flags_are_exactly_one(monkeypatch, channels, elevenlabs):
    for name, raw in (("STITCH_CHANNELS", channels), ("STITCH_CHANNELS_ELEVENLABS", elevenlabs)):
        if raw is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, raw)
    both = channels == "1" and elevenlabs == "1"
    assert main.channels_elevenlabs_enabled() is both
    limits = main.body_path_limits()
    assert limits.get(ELEVEN) == (ELEVEN_CAP if both else None)
    # The media caps follow STITCH_CHANNELS alone, unchanged by the ElevenLabs flag.
    assert {k: v for k, v in limits.items() if k != ELEVEN} == ({PHOTO: CAP, VOICE: CAP} if channels == "1" else {})
    assert _body_limit_kwargs()["path_limits"] == (limits or None)


@pytest.mark.parametrize("channels, elevenlabs", [
    (None, None), (None, "1"), ("1", None), ("1", "true"), ("1", " 1"), ("1", "01"), ("1", "1 "), ("true", "1"),
    (" 1", "1"), ("01", "1"), ("1 ", "1"), ("1", "1"),
])
def test_eleven_path_limit_through_the_whole_app(monkeypatch, channels, elevenlabs):
    for name, raw in (("STITCH_CHANNELS", channels), ("STITCH_CHANNELS_ELEVENLABS", elevenlabs)):
        if raw is not None:
            monkeypatch.setenv(name, raw)
    both = channels == "1" and elevenlabs == "1"
    headers = {"content-type": "application/json"}
    with TestClient(main.create_app()) as c:
        over_cap = c.post(ELEVEN, content=b"x" * (ELEVEN_CAP + 1), headers=headers)
        if both:
            assert (over_cap.status_code, over_cap.json()) == (413, TOO_LARGE)
        else:  # the ordinary limit: whatever answers, it is not the size middleware
            assert over_cap.status_code != 413, over_cap.text
            at_normal = c.post(ELEVEN, content=b"x" * NORMAL, headers=headers)
            assert at_normal.status_code != 413, at_normal.text
        over_normal = c.post(ELEVEN, content=b"x" * (NORMAL + 1), headers=headers)
        assert (over_normal.status_code, over_normal.json()) == (413, TOO_LARGE)
        at_cap = c.post(ELEVEN, content=b"x" * ELEVEN_CAP, headers=headers)
        assert at_cap.status_code != 413, at_cap.text


def test_eleven_cap_is_exact_for_content_length_and_chunked_bodies(eleven):
    app, seen = _stub_app()
    headers = {"content-type": "application/json"}
    with TestClient(app) as c:
        at = c.post(ELEVEN, content=b"x" * ELEVEN_CAP, headers=headers)
        assert (at.status_code, at.json()) == (200, {"bytes": ELEVEN_CAP})
        over = c.post(ELEVEN, content=b"x" * (ELEVEN_CAP + 1), headers=headers)
        assert (over.status_code, over.json()) == (413, TOO_LARGE)
        streamed_at = c.post(ELEVEN, content=_chunks(ELEVEN_CAP), headers=headers)
        assert (streamed_at.status_code, streamed_at.json()) == (200, {"bytes": ELEVEN_CAP})
        streamed = c.post(ELEVEN, content=_chunks(ELEVEN_CAP + 1), headers=headers)
        assert (streamed.status_code, streamed.json()) == (413, TOO_LARGE)
        # A lying Content-Length under the cap does not get a bigger body through.
        lying = c.post(ELEVEN, content=_chunks(ELEVEN_CAP + 1), headers={**headers, "content-length": "10"})
        assert lying.status_code in (400, 413), lying.text
    assert seen == [ELEVEN_CAP, ELEVEN_CAP]


def test_eleven_cap_holds_whatever_the_content_type(eleven):
    app, seen = _stub_app()
    with TestClient(app) as c:
        for content_type in ("multipart/form-data; boundary=x", "text/plain", ""):
            res = c.post(ELEVEN, content=b"x" * (ELEVEN_CAP + 1), headers={"content-type": content_type})
            assert (res.status_code, res.json()) == (413, TOO_LARGE), content_type
    assert seen == []


@pytest.mark.parametrize("path", [
    ELEVEN + "?big=1",
    "/internal/channels/elevenlabs%2Fpost-call",
    "/internal/channels/eleven%6Cabs/post-call",
    "/internal/channels/elevenlabs/post%2Dcall",
])
def test_eleven_variants_that_reach_the_route_get_the_cap(eleven, path):
    app, seen = _stub_app()
    with TestClient(app) as c:
        over = c.post(path, content=b"x" * (ELEVEN_CAP + 1), headers={"content-type": "application/json"})
        assert (over.status_code, over.json()) == (413, TOO_LARGE), path
        at = c.post(path, content=b"x" * ELEVEN_CAP, headers={"content-type": "application/json"})
        assert (at.status_code, at.json()) == (200, {"bytes": ELEVEN_CAP}), path
    assert seen == [ELEVEN_CAP]


@pytest.mark.parametrize("path", [
    ELEVEN.upper(), "/internal/channels/ElevenLabs/post-call", "/internal//channels/elevenlabs/post-call",
    "/internal/channels//elevenlabs/post-call", "/internal/channels/elevenlabs/post-call/x",
    "/internal/channels/elevenlabs/post-calls",
])
def test_eleven_variants_with_the_default_limit_never_reach_the_route(eleven, path):
    app, seen = _stub_app()
    with TestClient(app) as c:
        res = c.post(path, content=b"x" * (ELEVEN_CAP + 1), headers={"content-type": "application/json"})
        assert res.status_code == 404, (path, res.status_code, res.text)
        over = c.post(path, content=b"x" * (NORMAL + 1), headers={"content-type": "application/json"})
        assert (over.status_code, over.json()) == (413, TOO_LARGE), path
    assert seen == []


async def _raw_post(app, path: str, body: bytes, root_path: str = "") -> int:
    """POST straight through ASGI with this exact scope["path"] and root_path (an HTTP client
    would read a leading "//" as a host name). Returns the response status."""
    sent: list[dict] = []
    messages = [{"type": "http.request", "body": body, "more_body": False}]

    async def receive():
        return messages.pop(0) if messages else {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST",
             "scheme": "http", "path": path, "raw_path": path.encode(), "root_path": root_path, "query_string": b"",
             "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
             "client": ("testclient", 50000), "server": ("testserver", 80)}
    await app(scope, receive, send)
    return next(m["status"] for m in sent if m["type"] == "http.response.start")


@pytest.mark.anyio
@pytest.mark.parametrize("anyio_backend", ["asyncio"])
async def test_eleven_leading_double_slash_never_reaches_the_route(eleven, anyio_backend):
    app, seen = _stub_app()
    path = "/" + ELEVEN
    assert await _raw_post(app, path, b"x" * (ELEVEN_CAP + 1)) == 404
    assert await _raw_post(app, path, b"x" * (NORMAL + 1)) == 413
    assert await _raw_post(app, ELEVEN, b"x" * (ELEVEN_CAP + 1)) == 413  # the same helper does hit the cap
    assert await _raw_post(app, ELEVEN, b"x" * ELEVEN_CAP) == 200
    assert seen == [ELEVEN_CAP]


@pytest.mark.anyio
@pytest.mark.parametrize("anyio_backend", ["asyncio"])
async def test_eleven_cap_holds_when_the_scope_path_carries_a_root_path(eleven, anyio_backend):
    # uvicorn --root-path /api: scope["path"] keeps the prefix, the router strips it.
    app, seen = _stub_app()
    path = "/api" + ELEVEN
    assert await _raw_post(app, path, b"x" * (ELEVEN_CAP + 1), root_path="/api") == 413
    assert await _raw_post(app, path, b"x" * 400_000, root_path="/api") == 413
    assert await _raw_post(app, path, b"x" * ELEVEN_CAP, root_path="/api") == 200
    assert seen == [ELEVEN_CAP]  # the same handler, so it really is the same route


@pytest.mark.parametrize("path", [ELEVEN, "/api" + ELEVEN])
def test_eleven_cap_holds_under_a_test_client_root_path(eleven, path):
    app, seen = _stub_app()
    headers = {"content-type": "application/json"}
    with TestClient(app, root_path="/api") as c:
        at = c.post(path, content=b"x" * ELEVEN_CAP, headers=headers)
        assert (at.status_code, at.json()) == (200, {"bytes": ELEVEN_CAP})
        over = c.post(path, content=b"x" * (ELEVEN_CAP + 1), headers=headers)
        assert (over.status_code, over.json()) == (413, TOO_LARGE)
        streamed_at = c.post(path, content=_chunks(ELEVEN_CAP), headers=headers)
        assert (streamed_at.status_code, streamed_at.json()) == (200, {"bytes": ELEVEN_CAP})
        streamed = c.post(path, content=_chunks(ELEVEN_CAP + 1), headers=headers)
        assert (streamed.status_code, streamed.json()) == (413, TOO_LARGE)
    assert seen == [ELEVEN_CAP, ELEVEN_CAP]


@pytest.mark.parametrize("root_path, prefix", [("", ""), ("/api", ""), ("/api", "/api")])
def test_root_path_leaves_the_media_and_ordinary_limits_as_before(eleven, root_path, prefix):
    headers = {"content-type": "application/json"}
    with TestClient(main.create_app(), root_path=root_path) as c:
        eleven_over = c.post(prefix + ELEVEN, content=b"x" * (ELEVEN_CAP + 1), headers=headers)
        assert (eleven_over.status_code, eleven_over.json()) == (413, TOO_LARGE)
        for media in (PHOTO, VOICE):
            big = c.post(prefix + media, content=b"x" * (NORMAL + 1), headers=headers)
            assert big.status_code == 401, (media, big.text)  # over the default, under the media cap
            over = c.post(prefix + media, content=b"x" * (CAP + 1), headers=headers)
            assert (over.status_code, over.json()) == (413, TOO_LARGE), media
        ordinary = prefix + "/families/fam_a/memories"
        under = c.post(ordinary, content=b"x" * (ELEVEN_CAP + 1), headers=headers)
        assert under.status_code != 413, under.text
        over = c.post(ordinary, content=b"x" * (NORMAL + 1), headers=headers)
        assert (over.status_code, over.json()) == (413, TOO_LARGE)


@pytest.mark.parametrize("scope, expected", [
    ({"path": ELEVEN}, ELEVEN),
    ({"path": ELEVEN, "root_path": ""}, ELEVEN),
    ({"path": "/api" + ELEVEN, "root_path": "/api"}, ELEVEN),
    ({"path": ELEVEN, "root_path": "/api"}, ELEVEN),
    ({"path": "/apix" + ELEVEN, "root_path": "/api"}, "/apix" + ELEVEN),
    ({"path": "/api", "root_path": "/api"}, ""),
])
def test_route_path_strips_the_root_path_like_the_router(scope, expected):
    assert main.BodySizeLimitMiddleware.route_path(scope) == expected


def test_limit_for_takes_the_smaller_of_the_matching_path_limits():
    from starlette.datastructures import Headers

    mw = main.BodySizeLimitMiddleware(FastAPI(), json_limit=NORMAL, multipart_limit=main.MAX_MULTIPART_BODY_BYTES,
                                      path_limits={"/api" + ELEVEN: CAP, ELEVEN: ELEVEN_CAP, PHOTO: CAP})
    h = Headers({"content-type": "application/json"})
    assert mw.limit_for(h, "/api" + ELEVEN, ELEVEN) == ELEVEN_CAP
    assert mw.limit_for(h, ELEVEN, ELEVEN) == ELEVEN_CAP
    assert mw.limit_for(h, "/api" + PHOTO, PHOTO) == CAP
    assert mw.limit_for(h, "/api/other", "/other") == NORMAL
    assert mw.limit_for(h, PHOTO) == CAP  # route_path omitted: scope["path"] alone, as before


def test_eleven_trailing_slash_redirects_without_reading_and_the_retry_is_capped(eleven):
    app, seen = _stub_app()
    with TestClient(app) as c:
        moved = c.post(ELEVEN + "/", content=b"x" * (ELEVEN_CAP + 1), headers={"content-type": "application/json"},
                       follow_redirects=False)
        assert moved.status_code == 307
        assert moved.headers["location"].endswith(ELEVEN)
        assert seen == []  # the handler never ran on the slash path
        followed = c.post(ELEVEN + "/", content=b"x" * (ELEVEN_CAP + 1), headers={"content-type": "application/json"})
        assert (followed.status_code, followed.json()) == (413, TOO_LARGE)
    assert seen == []


@pytest.mark.parametrize("path", [
    "/internal/channels/inbound", "/internal/channels/tick", "/families/fam_a/memories",
    "/internal/channels/elevenlabs", "/internal/channels/elevenlabs/pre-call",
])
def test_eleven_on_every_other_path_keeps_the_normal_limit(eleven, path):
    with TestClient(main.create_app()) as c:
        at = c.post(path, content=b"x" * (ELEVEN_CAP + 1), headers={"content-type": "application/json"})
        assert at.status_code != 413, (path, at.text)
        res = c.post(path, content=b"x" * (NORMAL + 1), headers={"content-type": "application/json"})
    assert (res.status_code, res.json()) == (413, TOO_LARGE), path


def test_eleven_leaves_the_media_and_multipart_caps_unchanged(eleven):
    kwargs = _body_limit_kwargs()
    assert kwargs["path_limits"] == {PHOTO: CAP, VOICE: CAP, ELEVEN: ELEVEN_CAP}
    assert kwargs["json_limit"] == NORMAL
    assert kwargs["multipart_limit"] == main.MAX_MULTIPART_BODY_BYTES
    with TestClient(main.create_app()) as c:
        over = c.post(PHOTO, content=b"x" * (CAP + 1), headers={"content-type": "application/json"})
        assert (over.status_code, over.json()) == (413, TOO_LARGE)
        big = c.post(VOICE, content=b"x" * (NORMAL + 1), headers={"content-type": "application/json"})
        assert big.status_code == 401, big.text  # over the default, under the media cap: reaches the handler


# ------------------------------------------------------------------------------- purge


def _channel_rows(db, fid: str) -> None:
    """Channels documents of one family, shaped as db/contacts.py and db/conversations.py write them."""
    now = datetime.now(UTC)
    db.contacts.insert_one({"_id": f"contact-{fid}", "familyId": fid, "personId": "p1", "channel": "imessage",
                            "handle": f"+1555000{len(fid):04d}", "status": "active", "verifiedAt": now})
    db.channelMessages.insert_one({"_id": f"msg-{fid}", "familyId": fid, "contactId": f"contact-{fid}",
                                   "kind": "inbound", "status": "received", "createdAt": now})
    db.conversations.insert_one({"_id": f"conv-{fid}", "familyId": fid, "contactId": f"contact-{fid}",
                                 "status": "draft"})
    db.notifications.insert_one({"_id": f"note-{fid}", "familyId": fid, "contactId": f"contact-{fid}",
                                 "questionKey": "q1", "active": True})


def test_purge_removes_the_channels_collections_of_that_family_only(mdb):
    for fid in ("fam_a", "fam_b"):
        families_db.create_family(mdb, name=fid, creator_user_id=f"u_{fid}", family_id=fid)
        _channel_rows(mdb, fid)
    # No family content: the replay nonces, scan markers, rate rows and the neutral question bank.
    mdb.channelMessages.insert_many([
        {"_id": "auth:" + str(uuid4()), "kind": "auth", "expireAt": datetime.now(UTC)},
        {"_id": "worker-scan", "inbox": "msg-fam_a"},
        {"_id": "prompt-scan", "cursor": "contact-fam_a"},
    ])
    mdb.channelEnrollmentLimits.insert_one({"_id": "rate:abc", "count": 1, "expiresAt": datetime.now(UTC)})
    mdb.questionBank.insert_one({"_id": "qb1", "questionKey": "q1", "text": "What did you cook?"})
    keep = {name: list(mdb[name].find({"familyId": {"$exists": False}}).sort("_id", 1))
            for name in ("channelMessages", "channelEnrollmentLimits", "questionBank")}
    b_before = {name: list(mdb[name].find({"familyId": "fam_b"}))
                for name in ("contacts", "channelMessages", "conversations", "notifications")}

    families_db.purge_family(mdb, "fam_a")

    for name in ("contacts", "channelMessages", "conversations", "notifications"):
        assert mdb[name].count_documents({"familyId": "fam_a"}) == 0, name
        assert list(mdb[name].find({"familyId": "fam_b"})) == b_before[name], name
    for name, rows in keep.items():
        assert list(mdb[name].find({"familyId": {"$exists": False}}).sort("_id", 1)) == rows, name
    assert {"contacts", "channelMessages", "conversations", "notifications"} <= set(families_db.FAMILY_SCOPED)
    assert not {"channelEnrollmentLimits", "questionBank"} & set(families_db.FAMILY_SCOPED)


def test_signed_tick_body_shape_is_exact(on):
    """A body that is not exactly {} is refused by the mounted handler (it is Nate's rule, reached through main)."""
    with TestClient(main.create_app()) as c:
        body = json.dumps({"x": 1}).encode()
        res = c.post("/internal/channels/tick", content=body, headers=_signed("/internal/channels/tick", body))
    assert res.status_code == 422
