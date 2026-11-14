"""main.py: every router is wired, CORS with credentials, never-fatal startup, STITCH_FAKE_DB mode."""

from __future__ import annotations

import importlib
import logging
import os
import pkgutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from fastapi import routing as fastapi_routing
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
    monkeypatch.delenv("STITCH_FAKE_DB", raising=False)
    monkeypatch.delenv("STITCH_CORS_ORIGINS", raising=False)
    client.set_db(None)
    yield
    client.set_db(None)


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

    leaked = sorted(k for k in sys.modules if k == "agent" or k.startswith("agent."))
    if leaked:
        print("agent modules imported:", leaked)
        sys.exit(3)
    print("ok")
    """
)


def test_app_and_read_routes_never_import_agent():
    env = {k: v for k, v in os.environ.items() if not k.startswith("STITCH_")}
    env.update({"STITCH_FAKE_DB": "1", "JWT_SECRET": os.environ["JWT_SECRET"]})
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
