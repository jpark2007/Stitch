"""main.py: every router is wired, CORS with credentials, never-fatal startup, STITCH_FAKE_DB mode,
STITCH_AGENT run wiring and 422 bodies that never echo the request input."""

from __future__ import annotations

import importlib
import json
import logging
import os
import pkgutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from fastapi import exception_handlers as fastapi_exception_handlers
from fastapi import routing as fastapi_routing
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient
from pymongo.errors import ServerSelectionTimeoutError

import main
import routes
from db import client
from seed.demo import DEMO_FAMILY_ID, DEMO_PASSWORD, HYMN_MEMORY_ID

API_DIR = Path(__file__).resolve().parents[2]
ADMIN_EMAIL = "david@demo.stitch"


@pytest.fixture(autouse=True)
def _clean_env_and_db(monkeypatch: pytest.MonkeyPatch):
    """No STITCH_* env leaks in, and the db override is always restored afterwards."""
    for name in ("STITCH_FAKE_DB", "STITCH_CORS_ORIGINS", "STITCH_AGENT", "STITCH_FAKE_RUN_STEP", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    client.set_db(None)
    yield
    client.set_db(None)
    runs = sys.modules.get("agent.runs")
    if runs is not None:
        runs.configure(None)


def _route_set(routes_list) -> set[tuple[str, str]]:
    """(path, method) of every HTTP route, looking through included routers.

    FastAPI 0.16x includes routers lazily (app.routes holds _IncludedRouter wrappers), so the
    flattened view comes from fastapi.routing.iter_route_contexts.
    """
    found: set[tuple[str, str]] = set()
    for route in fastapi_routing.iter_route_contexts(list(routes_list)):
        if route.path and route.methods:
            found |= {(route.path, method) for method in route.methods}
    return found


# ------------------------------------------------------------------------------- routers


def test_every_router_in_the_routes_package_is_wired():
    expected: set[tuple[str, str]] = set()
    modules_with_router = []
    for info in pkgutil.iter_modules(routes.__path__):
        module = importlib.import_module(f"routes.{info.name}")
        router = getattr(module, "router", None)
        if router is None:
            continue
        modules_with_router.append(info.name)
        expected |= _route_set(router.routes)

        assert any(r is router for r in main.ROUTERS), f"routes.{info.name}.router is not in main.ROUTERS"

    assert {"auth", "families", "tree", "props", "ask", "memories"} <= set(modules_with_router)
    # Guard against a vacuous pass: the section 6 routes are really in the expected set.
    assert {("/auth/login", "POST"), ("/me", "GET"), ("/families/{fid}/tree", "GET"),
            ("/families/{fid}/memories", "POST"), ("/families/{fid}/ask", "POST")} <= expected

    for app in (main.app, main.create_app()):
        wired = _route_set(app.routes)
        missing = expected - wired
        assert not missing, f"routes not wired into the app: {sorted(missing)}"
        assert ("/health", "GET") in wired


def test_module_level_app_serves_health(mdb):
    """`uvicorn main:app` gets the same wiring as create_app()."""
    client.set_db(mdb)
    with TestClient(main.app) as c:
        res = c.get("/health")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}
    assert main.app.title == "Stitch API"


# ------------------------------------------------------------------------------- CORS


def _preflight(c: TestClient, origin: str, path: str = "/me"):
    return c.options(path, headers={"Origin": origin, "Access-Control-Request-Method": "GET"})


def test_cors_preflight_allows_the_frontend_with_credentials():
    c = TestClient(main.create_app())
    res = _preflight(c, "http://localhost:3000")
    assert res.status_code == 200
    assert res.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert res.headers["access-control-allow-credentials"] == "true"

    res = _preflight(c, "http://127.0.0.1:3000")
    assert res.headers["access-control-allow-origin"] == "http://127.0.0.1:3000"


def test_cors_preflight_rejects_other_origins():
    c = TestClient(main.create_app())
    res = _preflight(c, "http://evil.example")
    assert "access-control-allow-origin" not in res.headers


def test_cors_simple_request_echoes_the_allowed_origin_only():
    c = TestClient(main.create_app())
    ok = c.get("/health", headers={"Origin": "http://localhost:3000"})
    assert ok.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert ok.headers["access-control-allow-credentials"] == "true"
    evil = c.get("/health", headers={"Origin": "http://evil.example"})
    assert "access-control-allow-origin" not in evil.headers


def test_cors_origins_env_overrides_the_defaults(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("STITCH_CORS_ORIGINS", " https://stitch.example , ,http://localhost:4000,")
    assert main.cors_origins() == ["https://stitch.example", "http://localhost:4000"]

    c = TestClient(main.create_app())
    res = _preflight(c, "https://stitch.example")
    assert res.status_code == 200
    assert res.headers["access-control-allow-origin"] == "https://stitch.example"
    assert res.headers["access-control-allow-credentials"] == "true"
    assert "access-control-allow-origin" not in _preflight(c, "http://localhost:3000").headers


@pytest.mark.parametrize("raw", ["", " , ", "*", " * , "])
def test_cors_origins_falls_back_to_the_defaults_and_never_wildcards(monkeypatch, raw):
    monkeypatch.setenv("STITCH_CORS_ORIGINS", raw)
    assert main.cors_origins() == list(main.DEFAULT_CORS_ORIGINS)


def test_cors_origins_drops_a_wildcard_among_real_origins(monkeypatch):
    monkeypatch.setenv("STITCH_CORS_ORIGINS", "*,https://stitch.example")
    assert main.cors_origins() == ["https://stitch.example"]


def test_cors_origins_default():
    assert main.cors_origins() == ["http://localhost:3000", "http://127.0.0.1:3000"]


# ------------------------------------------------------------------------------- env flags


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "Yes", " yes "])
def test_fake_db_enabled_truthy(monkeypatch, value):
    monkeypatch.setenv("STITCH_FAKE_DB", value)
    assert main.fake_db_enabled() is True


@pytest.mark.parametrize("value", [None, "", "0", "false", "no", "on"])
def test_fake_db_enabled_falsy(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("STITCH_FAKE_DB", raising=False)
    else:
        monkeypatch.setenv("STITCH_FAKE_DB", value)
    assert main.fake_db_enabled() is False


# ------------------------------------------------------------------------------- fake mode


def _login(c: TestClient, email: str = ADMIN_EMAIL) -> None:
    res = c.post("/auth/login", json={"email": email, "password": DEMO_PASSWORD})
    assert res.status_code == 200, res.text


def test_fake_db_mode_serves_the_seeded_demo_family(monkeypatch, caplog):
    monkeypatch.setenv("STITCH_FAKE_DB", "1")
    caplog.set_level(logging.INFO)
    with TestClient(main.create_app()) as c:
        _login(c)
        me = c.get("/me")
        assert me.status_code == 200
        assert [f["id"] for f in me.json()["families"]] == [DEMO_FAMILY_ID]
        assert "passwordHash" not in me.text

        tree = c.get(f"/families/{DEMO_FAMILY_ID}/tree")
        assert tree.status_code == 200
        assert len(tree.json()["people"]) == 8

        book = c.get(f"/families/{DEMO_FAMILY_ID}/people/dad/book")
        assert book.status_code == 200
        hymn = [m for m in book.json()["memories"] if m["id"] == HYMN_MEMORY_ID]
        assert len(hymn) == 1
        assert hymn[0]["timeline"] is not None

        props = c.get(f"/families/{DEMO_FAMILY_ID}/props")
        assert props.status_code == 200
        assert "hymn_books" in {p["id"] for p in props.json()["props"]}

        assert client._db_override is not None
    assert client._db_override is None, "fake db override must be cleared on shutdown"

    lines = [r.getMessage() for r in caplog.records if r.levelno == logging.INFO]
    assert any("STITCH_FAKE_DB" in line and DEMO_PASSWORD in line for line in lines), lines


def test_fake_db_mode_starts_fresh_each_time(monkeypatch):
    monkeypatch.setenv("STITCH_FAKE_DB", "1")
    with TestClient(main.create_app()):
        first = client.get_db()
    with TestClient(main.create_app()):
        second = client.get_db()
    assert first is not second


def test_fake_db_mode_rejects_other_families(monkeypatch):
    monkeypatch.setenv("STITCH_FAKE_DB", "1")
    with TestClient(main.create_app()) as c:
        _login(c)
        assert c.get("/families/someone_else/tree").status_code == 404


def test_fake_db_mode_does_not_touch_real_indexes(monkeypatch):
    """Fake mode never reaches for the real database's indexes."""
    monkeypatch.setenv("STITCH_FAKE_DB", "1")

    def boom(_db):
        raise AssertionError("search indexes must not be created in fake mode")

    monkeypatch.setattr(client, "ensure_search_indexes", boom)
    with TestClient(main.create_app()) as c:
        assert c.get("/health").status_code == 200


# ------------------------------------------------------------------------------- real mode


def test_startup_creates_indexes_on_the_installed_db(mdb):
    client.set_db(mdb)
    with TestClient(main.create_app()) as c:
        assert c.get("/health").json() == {"status": "ok"}
        users_ix = mdb.users.index_information()
        assert users_ix["users_email_unique"]["unique"] is True
        for collection, specs in client.INDEXES.items():
            existing = mdb[collection].index_information()
            for name, _keys, _opts in specs:
                assert name in existing, (collection, name)
    # Not fake mode: the override this test installed is left alone.
    assert client.get_db() is mdb


def test_startup_survives_failing_search_indexes(mdb, monkeypatch, caplog):
    client.set_db(mdb)
    calls = []

    def failing(db):
        calls.append(db)
        raise RuntimeError("search indexes need Atlas")

    monkeypatch.setattr(client, "ensure_search_indexes", failing)
    caplog.set_level(logging.WARNING)
    with TestClient(main.create_app()) as c:
        assert c.get("/health").status_code == 200
    assert calls == [mdb]
    assert "users_email_unique" in mdb.users.index_information()
    assert any("search indexes" in r.getMessage() for r in caplog.records)


def test_startup_on_mongomock_search_indexes_is_harmless(mdb):
    """The real ensure_search_indexes on mongomock logs and returns; startup goes on."""
    client.set_db(mdb)
    with TestClient(main.create_app()) as c:
        assert c.get("/health").status_code == 200


def test_startup_survives_unreachable_mongodb(mdb, monkeypatch, caplog):
    client.set_db(mdb)

    def unreachable(_db):
        raise ServerSelectionTimeoutError("localhost:27017: [Errno 61] Connection refused")

    search_calls = []
    monkeypatch.setattr(client, "ensure_indexes", unreachable)
    monkeypatch.setattr(client, "ensure_search_indexes", lambda db: search_calls.append(db) or [])
    caplog.set_level(logging.WARNING)
    with TestClient(main.create_app()) as c:
        res = c.get("/health")
        assert res.status_code == 200
        assert res.json() == {"status": "ok"}
    assert any("unreachable" in r.getMessage() for r in caplog.records)
    assert search_calls == [], "no point waiting on search indexes when the server is unreachable"


def test_startup_survives_any_index_error(mdb, monkeypatch):
    client.set_db(mdb)

    def broken(_db):
        raise ValueError("index spec conflict")

    monkeypatch.setattr(client, "ensure_indexes", broken)
    with TestClient(main.create_app()) as c:
        assert c.get("/health").status_code == 200


# ------------------------------------------------------------------------------- no agent import

_NO_AGENT_SCRIPT = textwrap.dedent(
    """
    import os
    import sys

    from fastapi.testclient import TestClient

    import main

    FID = "demo_family"
    with TestClient(main.create_app()) as c:
        def ok(res):
            assert res.status_code == 200, (res.request.method, res.request.url, res.status_code, res.text)
            assert "passwordHash" not in res.text
            return res

        ok(c.post("/auth/login", json={"email": "david@demo.stitch", "password": "stitch-demo"}))
        ok(c.get("/me"))
        ok(c.get(f"/families/{FID}/tree"))
        book = ok(c.get(f"/families/{FID}/people/dad/book")).json()
        ok(c.get(f"/families/{FID}/memories/sample_hymn_books"))
        ok(c.get(f"/families/{FID}/threads"))
        ok(c.get(f"/families/{FID}/props"))
        ok(c.get(f"/families/{FID}/sets"))
        ok(c.get(f"/families/{FID}/questions"))
        ok(c.post(f"/families/{FID}/ask", json={"query": "hymn books"}))
        photo_ids = [p for m in book["memories"] for t in m["tellings"] for p in t["photoIds"]]
        assert photo_ids, "the demo seed has photos"
        photo = ok(c.get(f"/families/{FID}/media/{photo_ids[0]}"))
        assert photo.headers["content-type"].startswith("image/"), photo.headers

        # STITCH_AGENT=off, even with ANTHROPIC_API_KEY set: the run routes refuse without
        # importing the agent, and no photo is stored.
        assert os.environ["ANTHROPIC_API_KEY"], "the parent sets a key"
        assert c.app.state.agent_mode == "off"
        png = b"\\x89PNG\\r\\n\\x1a\\n" + b"\\x00" * 32
        submit = c.post(
            f"/families/{FID}/memories",
            data={"text": "We sang on the church steps.", "year": "1998"},
            files=[("photos", ("steps.png", png, "image/png"))],
        )
        assert submit.status_code == 503, (submit.status_code, submit.text)
        assert submit.json() == {"detail": "Could not start the studio"}
        stream = c.get(f"/families/{FID}/runs/some-run/stream")
        assert stream.status_code == 503, (stream.status_code, stream.text)
        answer = c.post(f"/families/{FID}/runs/some-run/answer", json={"kind": "answer", "text": "hi"})
        assert answer.status_code == 503, (answer.status_code, answer.text)

    leaked = sorted(k for k in sys.modules if k == "agent" or k.startswith("agent."))
    if leaked:
        print("agent modules imported:", leaked)
        sys.exit(3)
    print("ok")
    """
)


def test_app_and_read_routes_never_import_agent():
    """(f) Read routes, and the run routes under STITCH_AGENT=off with a key set, never import agent/."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("STITCH_")}
    env.update({
        "STITCH_FAKE_DB": "1",
        "STITCH_AGENT": "off",
        "ANTHROPIC_API_KEY": "sk-ant-test-not-a-key",
        "JWT_SECRET": os.environ["JWT_SECRET"],
    })
    proc = subprocess.run(
        [sys.executable, "-c", _NO_AGENT_SCRIPT],
        cwd=API_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    assert proc.stdout.strip().endswith("ok")


# ------------------------------------------------------------------------------- agent mode

SAM_EMAIL = "sam@demo.stitch"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32 + b"main"
WAIT = 10.0


def _warnings(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]


@pytest.mark.parametrize(("raw", "expected"), [
    ("off", "off"), ("fake", "fake"), ("real", "real"), (" REAL ", "real"), ("Off", "off"),
])
@pytest.mark.parametrize("fake_db", [False, True])
@pytest.mark.parametrize("key", [None, "sk-test-not-a-real-key"])
def test_agent_mode_explicit_values_win(monkeypatch, caplog, raw, expected, fake_db, key):
    monkeypatch.setenv("STITCH_AGENT", raw)
    if fake_db:
        monkeypatch.setenv("STITCH_FAKE_DB", "1")
    if key:
        monkeypatch.setenv("ANTHROPIC_API_KEY", key)
    caplog.set_level(logging.WARNING)
    assert main.agent_mode() == expected
    assert _warnings(caplog) == []


def test_agent_mode_default_is_fake_with_the_fake_db_even_with_a_key(monkeypatch):
    monkeypatch.setenv("STITCH_FAKE_DB", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-a-real-key")
    assert main.agent_mode() == "fake"


@pytest.mark.parametrize("key", [None, "", "   "])
def test_agent_mode_default_is_fake_without_an_anthropic_key(monkeypatch, key):
    if key is not None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", key)
    assert main.agent_mode() == "fake"


@pytest.mark.parametrize("raw", [None, "", "  "])
def test_agent_mode_default_is_real_with_a_key_and_no_fake_db(monkeypatch, caplog, raw):
    if raw is not None:
        monkeypatch.setenv("STITCH_AGENT", raw)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-a-real-key")
    caplog.set_level(logging.WARNING)
    assert main.agent_mode() == "real"
    assert _warnings(caplog) == [], "unset or empty is not worth a warning"


@pytest.mark.parametrize(("key", "expected"), [(None, "fake"), ("sk-test-not-a-real-key", "real")])
def test_agent_mode_unknown_value_warns_and_uses_the_default(monkeypatch, caplog, key, expected):
    monkeypatch.setenv("STITCH_AGENT", "langgraph")
    if key:
        monkeypatch.setenv("ANTHROPIC_API_KEY", key)
    caplog.set_level(logging.WARNING)
    assert main.agent_mode() == expected
    warnings = _warnings(caplog)
    assert len(warnings) == 1
    assert "STITCH_AGENT" in warnings[0] and "langgraph" in warnings[0]
    assert key is None or key not in warnings[0]


def test_agent_modes_constant():
    assert main.AGENT_MODES == ("off", "fake", "real")


@pytest.mark.parametrize(("raw", "expected"), [
    (None, 0.6), ("", 0.6), ("0", 0.0), ("1.5", 1.5), (" 0.05 ", 0.05),
])
def test_fake_run_step_seconds(monkeypatch, caplog, raw, expected):
    if raw is not None:
        monkeypatch.setenv("STITCH_FAKE_RUN_STEP", raw)
    caplog.set_level(logging.WARNING)
    assert main.fake_run_step_seconds() == expected
    assert _warnings(caplog) == []


@pytest.mark.parametrize("raw", ["fast", "-1", "nan", "inf", "1,5"])
def test_fake_run_step_seconds_bad_value_warns_and_defaults(monkeypatch, caplog, raw):
    monkeypatch.setenv("STITCH_FAKE_RUN_STEP", raw)
    caplog.set_level(logging.WARNING)
    assert main.fake_run_step_seconds() == 0.6
    assert any("STITCH_FAKE_RUN_STEP" in w for w in _warnings(caplog))


# ------------------------------------------------------------------------------- agent runs


def _submit(c: TestClient, **extra: str):
    form = {"text": "We sang from the green hymn books on the church steps.", "year": "1998", **extra}
    return c.post(
        f"/families/{DEMO_FAMILY_ID}/memories",
        data=form,
        files=[("photos", ("steps.png", PNG, "image/png"))],
    )


def _sse_events(text: str) -> list[tuple[str, dict]]:
    events = []
    for block in text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        events.append((lines["event"], json.loads(lines["data"])))
    return events


def test_fake_db_default_agent_runs_the_scripted_add_a_memory_flow(monkeypatch, caplog):
    """The README's no-keys flow: submit with a photo, follow-up, props review, scene in the books."""
    import agent.runs

    monkeypatch.setenv("STITCH_FAKE_DB", "1")
    monkeypatch.setenv("STITCH_FAKE_RUN_STEP", "0")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-a-real-key")  # fake db still means fake runs
    caplog.set_level(logging.INFO)
    with TestClient(main.create_app()) as c:
        _login(c, SAM_EMAIL)
        res = _submit(c, participantIds="lily")
        assert res.status_code == 202, res.text
        run_id = res.json()["runId"]
        assert isinstance(run_id, str) and run_id
        assert res.json()["memoryId"] is None

        manager = agent.runs.get_manager()
        assert type(manager).__name__ == "ScriptedRunManager"
        assert manager.wait(DEMO_FAMILY_ID, run_id, WAIT) == "waiting"
        answer = c.post(
            f"/families/{DEMO_FAMILY_ID}/runs/{run_id}/answer",
            json={"kind": "answer", "text": "Lily held the umbrella."},
        )
        assert answer.status_code == 204, answer.text

        assert manager.wait(DEMO_FAMILY_ID, run_id, WAIT) == "waiting"
        chips = [p for name, p in manager.events(DEMO_FAMILY_ID, run_id) if name == "props_found"][0].props
        review = c.post(
            f"/families/{DEMO_FAMILY_ID}/runs/{run_id}/answer",
            json={"kind": "props", "props": [{"id": chip.id, "action": "keep"} for chip in chips]},
        )
        assert review.status_code == 204, review.text
        assert manager.wait(DEMO_FAMILY_ID, run_id, WAIT) == "done"

        # A late subscriber replays the whole run as SSE.
        stream = c.get(f"/families/{DEMO_FAMILY_ID}/runs/{run_id}/stream")
        assert stream.status_code == 200
        assert stream.headers["content-type"].startswith("text/event-stream")
        events = _sse_events(stream.text)
        names = [name for name, _ in events]
        assert names[0] == "plan"
        assert {"question", "props_found", "scene_ready"} <= set(names)
        assert names[-1] == "done"
        memory_id = events[-1][1]["memoryId"]

        # The scene is in the teller's book and the participant's book.
        for person_id in ("sam", "lily"):
            book = c.get(f"/families/{DEMO_FAMILY_ID}/people/{person_id}/book")
            assert book.status_code == 200
            found = [m for m in book.json()["memories"] if m["id"] == memory_id]
            assert len(found) == 1, person_id
            assert found[0]["timeline"] is not None
            assert "Lily held the umbrella." in json.dumps(found[0]["tellings"])

    # Cleared on shutdown. (Not get_manager(): with the key set it would build a studio.)
    assert agent.runs._manager is None
    assert client._db_override is None

    lines = [r.getMessage() for r in caplog.records if r.levelno == logging.INFO]
    assert any(line.startswith("STITCH_AGENT=fake") for line in lines), lines


def test_fake_agent_submit_returns_202_and_shutdown_clears_the_manager(monkeypatch):
    import agent.runs

    monkeypatch.setenv("STITCH_FAKE_DB", "1")
    monkeypatch.setenv("STITCH_FAKE_RUN_STEP", "0")
    with TestClient(main.create_app()) as c:
        _login(c, SAM_EMAIL)
        res = _submit(c)
        assert res.status_code == 202, res.text
        assert res.json()["runId"]
        assert client._db_override is not None
    with pytest.raises(RuntimeError):
        agent.runs.get_manager()
    assert client._db_override is None


def test_fake_agent_uses_the_configured_step(monkeypatch):
    import agent.runs

    monkeypatch.setenv("STITCH_FAKE_DB", "1")
    monkeypatch.setenv("STITCH_FAKE_RUN_STEP", "0.25")
    with TestClient(main.create_app()):
        assert agent.runs.get_manager()._step_seconds == 0.25


def test_fake_agent_without_fake_db_scripts_runs_on_the_installed_db(mdb, monkeypatch):
    """No ANTHROPIC_API_KEY and a real (here: mongomock) db: fake runs, the db override is kept."""
    import agent.runs

    client.set_db(mdb)
    with TestClient(main.create_app()) as c:
        assert c.get("/health").status_code == 200
        assert type(agent.runs.get_manager()).__name__ == "ScriptedRunManager"
    with pytest.raises(RuntimeError):
        agent.runs.get_manager()
    assert client.get_db() is mdb


def test_agent_off_leaves_runs_unconfigured_and_submit_is_503(monkeypatch, caplog):
    import agent.runs

    monkeypatch.setenv("STITCH_FAKE_DB", "1")
    monkeypatch.setenv("STITCH_AGENT", "off")
    calls = []
    monkeypatch.setattr(agent.runs, "configure", lambda manager: calls.append(manager))
    caplog.set_level(logging.INFO)
    with TestClient(main.create_app()) as c:
        _login(c, SAM_EMAIL)
        with pytest.raises(RuntimeError):
            agent.runs.get_manager()
        res = _submit(c)
        assert res.status_code == 503
        assert res.json() == {"detail": "Could not start the studio"}
    assert calls == [], "off never configures nor clears agent runs"
    assert any("STITCH_AGENT=off" in r.getMessage() for r in caplog.records)


def test_agent_off_does_not_clear_a_manager_it_did_not_install(mdb, monkeypatch):
    import agent.runs

    sentinel = object()
    agent.runs.configure(sentinel)
    monkeypatch.setenv("STITCH_AGENT", "off")
    client.set_db(mdb)
    with TestClient(main.create_app()):
        pass
    assert agent.runs.get_manager() is sentinel


MODEL_STUDIO_LINE = "STITCH_AGENT=real: agent runs use the model studio"


class _StubManager:
    """Stands in for agent.production.production_manager()'s RunManager."""

    def __init__(self) -> None:
        self.shutdowns = 0

    def shutdown(self) -> None:
        self.shutdowns += 1


def _patch_production(monkeypatch, result) -> list[dict]:
    """Replace agent.production.production_manager; returns the list of calls (their kwargs).

    `result` is returned, or raised when it is an exception.
    """
    import agent.production

    calls: list[dict] = []

    def production_manager(**kwargs):
        calls.append(kwargs)
        if isinstance(result, BaseException):
            raise result
        return result

    monkeypatch.setattr(agent.production, "production_manager", production_manager)
    return calls


def _infos(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno == logging.INFO]


def test_real_mode_installs_the_production_manager_and_stops_it(mdb, monkeypatch, caplog):
    """(a) No manager configured: startup builds agent.production.production_manager() via get_manager()."""
    import agent.runs

    stub = _StubManager()
    calls = _patch_production(monkeypatch, stub)
    client.set_db(mdb)
    monkeypatch.setenv("STITCH_AGENT", "real")
    caplog.set_level(logging.INFO)
    app = main.create_app()
    with TestClient(app) as c:
        assert c.get("/health").status_code == 200
        assert agent.runs._manager is stub
        assert agent.runs.get_manager() is stub
        assert app.state.agent_mode == "real"
    assert calls == [{}], "built exactly once"
    assert stub.shutdowns == 1
    assert agent.runs._manager is None, "shutdown clears the manager it installed"
    assert MODEL_STUDIO_LINE in _infos(caplog)
    assert not any("STITCH_AGENT" in w for w in _warnings(caplog)), "no fallback when the studio is built"


def test_real_mode_default_with_a_key_builds_the_production_manager(mdb, monkeypatch, caplog):
    import agent.runs

    stub = _StubManager()
    calls = _patch_production(monkeypatch, stub)
    client.set_db(mdb)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-a-real-key")
    caplog.set_level(logging.INFO)
    with TestClient(main.create_app()):
        assert agent.runs.get_manager() is stub
    assert calls == [{}]
    assert stub.shutdowns == 1
    assert MODEL_STUDIO_LINE in _infos(caplog)


def test_real_mode_build_failure_falls_back_to_scripted_runs(monkeypatch, caplog):
    """(b) production_manager raises: a warning with the reason, then scripted runs that work."""
    import agent.runs

    calls = _patch_production(monkeypatch, RuntimeError("no key"))
    monkeypatch.setenv("STITCH_FAKE_DB", "1")
    monkeypatch.setenv("STITCH_FAKE_RUN_STEP", "0")
    monkeypatch.setenv("STITCH_AGENT", "real")
    caplog.set_level(logging.INFO)
    app = main.create_app()
    with TestClient(app) as c:
        assert c.get("/health").status_code == 200
        assert type(agent.runs.get_manager()).__name__ == "ScriptedRunManager"
        assert app.state.agent_mode == "real", "the resolved mode, not the fallback"
        _login(c, SAM_EMAIL)
        assert _submit(c).status_code == 202
    assert calls == [{}]
    assert agent.runs._manager is None
    warnings = [w for w in _warnings(caplog) if w.startswith("STITCH_AGENT=real")]
    assert len(warnings) == 1, _warnings(caplog)
    assert "no key" in warnings[0] and "falling back to scripted runs" in warnings[0]
    assert MODEL_STUDIO_LINE not in _infos(caplog)
    assert any(line.startswith("STITCH_AGENT=fake: scripted agent runs") for line in _infos(caplog))


def test_real_mode_without_a_key_falls_back_with_the_reason(monkeypatch, caplog):
    """The real production build (unpatched) refuses without ANTHROPIC_API_KEY; the warning says so."""
    import agent.runs

    monkeypatch.setenv("STITCH_FAKE_DB", "1")
    monkeypatch.setenv("STITCH_AGENT", "real")
    caplog.set_level(logging.WARNING)
    with TestClient(main.create_app()) as c:
        assert c.get("/health").status_code == 200
        assert type(agent.runs.get_manager()).__name__ == "ScriptedRunManager"
    warnings = [w for w in _warnings(caplog) if w.startswith("STITCH_AGENT=real")]
    assert len(warnings) == 1, _warnings(caplog)
    assert "ANTHROPIC_API_KEY is not set" in warnings[0] and "falling back to scripted runs" in warnings[0]


def test_real_mode_keeps_a_manager_configured_before_startup(mdb, monkeypatch, caplog):
    """(c) An already configured manager is kept, production_manager is never called, and it is not stopped."""
    import agent.runs

    calls = _patch_production(monkeypatch, AssertionError("production_manager must not be called"))
    kept = _StubManager()
    agent.runs.configure(kept)
    client.set_db(mdb)
    monkeypatch.setenv("STITCH_AGENT", "real")
    caplog.set_level(logging.INFO)
    with TestClient(main.create_app()) as c:
        assert c.get("/health").status_code == 200
        assert agent.runs.get_manager() is kept
    assert calls == []
    assert kept.shutdowns == 0, "whoever configured it owns it"
    assert agent.runs._manager is kept
    assert any("keeping the agent run manager" in line for line in _infos(caplog))
    assert not any("STITCH_AGENT" in w for w in _warnings(caplog))


def test_real_mode_import_failure_falls_back(monkeypatch, caplog):
    monkeypatch.setitem(sys.modules, "agent.runs", None)  # importing it raises ImportError
    monkeypatch.setenv("STITCH_FAKE_DB", "1")
    monkeypatch.setenv("STITCH_AGENT", "real")
    caplog.set_level(logging.WARNING)
    with TestClient(main.create_app()) as c:
        assert c.get("/health").status_code == 200
    warnings = _warnings(caplog)
    assert any("cannot be imported" in w and "falling back to scripted runs" in w for w in warnings), warnings


def test_real_mode_unpatched_build_with_a_dummy_key_installs_the_run_manager(monkeypatch, caplog):
    """(d) The real agent.production build, with a dummy key and no other service env, makes no network call."""
    import socket

    import agent.runs

    def no_network(*args, **kwargs):
        raise AssertionError(f"network access during the studio build: {args!r}")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)
    monkeypatch.setenv("STITCH_FAKE_DB", "1")
    monkeypatch.setenv("STITCH_AGENT", "real")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-not-a-key")
    for name in ("IMAGE_API_KEY", "ELEVENLABS_API_KEY", "MONGODB_URI", "STITCH_WEB_URL",
                 "STITCH_MODEL", "STITCH_SUBAGENT_MODEL"):
        monkeypatch.delenv(name, raising=False)
    caplog.set_level(logging.INFO)
    with TestClient(main.create_app()) as c:
        assert c.get("/health").status_code == 200
        manager = agent.runs.get_manager()
        assert type(manager) is agent.runs.RunManager, type(manager)
    assert agent.runs._manager is None, "shutdown stops and clears the studio it built"
    assert MODEL_STUDIO_LINE in _infos(caplog)
    assert any("model studio" in line for line in _infos(caplog))
    assert not any(w.startswith("STITCH_AGENT") for w in _warnings(caplog)), _warnings(caplog)


def test_agent_off_with_a_key_is_truly_off(monkeypatch, caplog):
    """(e) STITCH_AGENT=off beats ANTHROPIC_API_KEY: every run route is 503 and nothing is built."""
    import agent.runs

    calls = _patch_production(monkeypatch, _StubManager())
    monkeypatch.setenv("STITCH_FAKE_DB", "1")
    monkeypatch.setenv("STITCH_AGENT", "off")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-not-a-key")
    caplog.set_level(logging.INFO)
    app = main.create_app()
    with TestClient(app) as c:
        assert app.state.agent_mode == "off"
        _login(c, SAM_EMAIL)
        res = _submit(c)
        assert res.status_code == 503
        assert res.json() == {"detail": "Could not start the studio"}
        stream = c.get(f"/families/{DEMO_FAMILY_ID}/runs/some-run/stream")
        assert stream.status_code == 503
        assert stream.json() == {"detail": "The studio is unavailable"}
        answer = c.post(
            f"/families/{DEMO_FAMILY_ID}/runs/some-run/answer", json={"kind": "answer", "text": "hello"}
        )
        assert answer.status_code == 503
        assert answer.json() == {"detail": "The studio is unavailable"}
        # Membership still comes first.
        assert _submit_to(c, "someone_else").status_code == 404
        assert c.get("/families/someone_else/runs/some-run/stream").status_code == 404
    assert calls == [], "off never builds the studio"
    assert agent.runs._manager is None
    assert any(line.startswith("STITCH_AGENT=off") for line in _infos(caplog))


def _submit_to(c: TestClient, fid: str):
    return c.post(
        f"/families/{fid}/memories",
        data={"text": "x", "year": "1998"},
        files=[("photos", ("steps.png", PNG, "image/png"))],
    )


@pytest.mark.parametrize("mode", ["off", "fake", "real"])
def test_lifespan_records_the_resolved_agent_mode(mdb, monkeypatch, mode):
    _patch_production(monkeypatch, _StubManager())
    client.set_db(mdb)
    monkeypatch.setenv("STITCH_AGENT", mode)
    app = main.create_app()
    assert not hasattr(app.state, "agent_mode"), "only set when the app starts"
    with TestClient(app):
        assert app.state.agent_mode == mode


def test_agent_shutdown_errors_never_escape(mdb, monkeypatch, caplog):
    import agent.runs

    class Stubborn:
        def shutdown(self) -> None:
            raise RuntimeError("worker stuck")

    _patch_production(monkeypatch, Stubborn())
    client.set_db(mdb)
    monkeypatch.setenv("STITCH_AGENT", "real")
    caplog.set_level(logging.WARNING)
    with TestClient(main.create_app()) as c:
        assert c.get("/health").status_code == 200
    assert agent.runs._manager is None
    assert any("worker stuck" in w for w in _warnings(caplog))


def test_an_agent_step_that_raises_never_stops_startup(monkeypatch, caplog):
    import agent.runs

    def explode() -> bool:
        raise ImportError("seed.fake_runs is broken")

    monkeypatch.setenv("STITCH_FAKE_DB", "1")
    monkeypatch.setattr(main, "_install_agent_fake", explode)
    caplog.set_level(logging.WARNING)
    with TestClient(main.create_app()) as c:
        assert c.get("/health").status_code == 200
        _login(c, SAM_EMAIL)
        assert _submit(c).status_code == 503
        with pytest.raises(RuntimeError):
            agent.runs.get_manager()
    assert any("seed.fake_runs is broken" in w for w in _warnings(caplog))


def test_install_fake_runs_returning_false_logs_a_warning(monkeypatch, caplog):
    import seed.fake_runs

    monkeypatch.setenv("STITCH_FAKE_DB", "1")
    monkeypatch.setattr(seed.fake_runs, "install_fake_runs", lambda step_seconds: False)
    caplog.set_level(logging.WARNING)
    with TestClient(main.create_app()) as c:
        assert c.get("/health").status_code == 200
    assert any("STITCH_AGENT=fake" in w and "not installed" in w for w in _warnings(caplog))


# ------------------------------------------------------------------------------- 422 bodies

SECRET_PASSWORD = "hunter2"  # shorter than 8: signup rejects it


def _assert_redacted_422(res, secret: str) -> list[dict]:
    assert res.status_code == 422, res.text
    assert secret not in res.text
    body = res.json()
    assert set(body) == {"detail"}
    assert body["detail"], "at least one error"
    for error in body["detail"]:
        assert set(error) == {"type", "loc", "msg"}, error
        assert isinstance(error["loc"], list) and isinstance(error["msg"], str) and isinstance(error["type"], str)
    assert '"input"' not in res.text
    return body["detail"]


def test_signup_with_a_short_password_does_not_echo_it():
    c = TestClient(main.create_app())
    res = c.post("/auth/signup", json={"email": "new@example.com", "password": SECRET_PASSWORD, "name": "New"})
    detail = _assert_redacted_422(res, SECRET_PASSWORD)
    assert detail == [{
        "type": "string_too_short",
        "loc": ["body", "password"],
        "msg": "String should have at least 8 characters",
    }]


def test_login_missing_email_does_not_echo_the_password():
    secret = "correct horse battery staple"
    c = TestClient(main.create_app())
    res = c.post("/auth/login", json={"password": secret})
    detail = _assert_redacted_422(res, secret)
    assert detail == [{"type": "missing", "loc": ["body", "email"], "msg": "Field required"}]


def test_malformed_json_body_is_redacted_too():
    secret = "my-secret-password"
    c = TestClient(main.create_app())
    res = c.post(
        "/auth/login",
        content=f'{{"email": "a@b.c", "password": "{secret}"',
        headers={"Content-Type": "application/json"},
    )
    detail = _assert_redacted_422(res, secret)
    assert detail[0]["type"] == "json_invalid"


def test_validation_error_handler_is_registered():
    for app in (main.app, main.create_app()):
        handler = app.exception_handlers[RequestValidationError]
        assert handler is main.validation_error_handler
        assert handler is not fastapi_exception_handlers.request_validation_exception_handler
