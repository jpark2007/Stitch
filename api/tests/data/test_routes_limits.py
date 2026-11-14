"""Request size limits and length caps: main.BodySizeLimitMiddleware, families and signup.

- The body cap: a POST, PUT or PATCH body over main.MAX_JSON_BODY_BYTES (or
  main.MAX_MULTIPART_BODY_BYTES for multipart/form-data) is a 413 "That request is too
  large", by Content-Length and by counting a chunked body, with CORS headers for an
  allowed origin. Normal photo submits and the SSE run stream still work.
- Length caps at and over each limit, checked after trimming: family name, person name,
  character sheet free text, signup display name and email.
- Bugs found in the family and auth routes, one test each (see the comment above each).

Runs on mongomock (and the seeded STITCH_FAKE_DB app with scripted runs for the stream);
no MongoDB, no network, no keys.
"""

from __future__ import annotations

import asyncio
import io
import json
import time
from collections.abc import Iterator

import httpx
import pytest
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient
from PIL import Image

import agent.runs
import auth
import main
import routes.memories
import schemas
from db import client as db_client
from db import families as families_db
from db import media as media_db
from db import people as people_db
from db import users as users_db
from routes import auth as auth_routes
from routes import families as families_routes
from seed import demo

ORIGIN = "http://localhost:3000"
PASSWORD = "correct horse battery"
TOO_LARGE = {"detail": "That request is too large"}


def _png(color: tuple[int, int, int] = (200, 120, 40)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (4, 4), color).save(out, format="PNG")
    return out.getvalue()


# ------------------------------------------------------------------------------ fixtures


@pytest.fixture
def world(mdb, monkeypatch):
    """The real app (main.create_app, all middleware) over mongomock, without the lifespan.

    Ada is the admin of "The Maos" with her own person; Ben and Cy have accounts only.
    """
    monkeypatch.delenv("STITCH_CORS_ORIGINS", raising=False)
    db_client.ensure_indexes(mdb)
    db_client.set_db(mdb)
    made = {}
    for key, name in (("ada", "Ada"), ("ben", "Ben"), ("cy", "Cy")):
        made[key] = users_db.create_user(
            mdb, email=f"{key}@example.com", password_hash=auth.hash_password(PASSWORD), name=name
        )
    family = families_db.create_family(mdb, name="The Maos", creator_user_id=made["ada"].id)
    ada_p = people_db.create_person(mdb, family.id, schemas.NewPerson(name="Ada"), added_by=made["ada"].id,
                                    user_id=made["ada"].id)
    rosa = people_db.create_person(mdb, family.id, schemas.NewPerson(name="Rosa"), added_by=made["ada"].id)
    luis = people_db.create_person(mdb, family.id, schemas.NewPerson(name="Luis"), added_by=made["ada"].id)
    app = main.create_app()
    clients: list[TestClient] = []

    def client(user: schemas.User | None = None) -> TestClient:
        tc = TestClient(app)
        if user is not None:
            tc.cookies.set(auth.COOKIE_NAME, auth.create_session_token(user.id))
        clients.append(tc)
        return tc

    w = type("World", (), {})()
    w.db, w.app, w.client, w.users, w.fid = mdb, app, client, made, family.id
    w.ada_p, w.rosa, w.luis = ada_p, rosa, luis
    w.ada = client(made["ada"])
    yield w
    for tc in clients:
        tc.close()
    db_client.set_db(None)


def _small_limit_app(monkeypatch, *, json_limit: int, multipart_limit: int):
    """main.create_app() with tiny body caps (create_app reads the constants when called)."""
    monkeypatch.setattr(main, "MAX_JSON_BODY_BYTES", json_limit)
    monkeypatch.setattr(main, "MAX_MULTIPART_BODY_BYTES", multipart_limit)
    return main.create_app()


def _invite(tc: TestClient, fid: str) -> str:
    res = tc.post(f"/families/{fid}/invites")
    assert res.status_code == 201, res.text
    return res.json()["code"]


# ------------------------------------------------------------------------------ constants


def test_body_limits_are_derived_from_the_photo_limits():
    assert main.MAX_JSON_BODY_BYTES == 1_000_000
    assert main.MAX_MULTIPART_BODY_BYTES == routes.memories.MAX_PHOTOS * media_db.MAX_BYTES + 1_000_000
    assert main.BODY_TOO_LARGE == TOO_LARGE["detail"]


def test_cors_wraps_the_body_limit():
    """Starlette runs user_middleware[0] outermost: CORS first, then the body cap."""
    app = main.create_app()
    classes = [m.cls for m in app.user_middleware]
    assert classes == [CORSMiddleware, main.BodySizeLimitMiddleware]


# ------------------------------------------------------------------------- 413 by length


def _login_body(size: int) -> bytes:
    """A login JSON body of exactly `size` bytes (the password pads it)."""
    shell = json.dumps({"email": "nobody@example.com", "password": ""}).encode()
    body = json.dumps({"email": "nobody@example.com", "password": "p" * (size - len(shell))}).encode()
    assert len(body) == size
    return body


def test_json_body_over_the_limit_is_413(world):
    res = world.client().post(
        "/auth/login", content=_login_body(main.MAX_JSON_BODY_BYTES + 1),
        headers={"content-type": "application/json"},
    )
    assert res.status_code == 413
    assert res.json() == TOO_LARGE
    assert int(res.request.headers["content-length"]) == main.MAX_JSON_BODY_BYTES + 1


def test_json_body_at_the_limit_reaches_the_route(world):
    res = world.client().post(
        "/auth/login", content=_login_body(main.MAX_JSON_BODY_BYTES),
        headers={"content-type": "application/json"},
    )
    assert res.status_code == 401  # the route ran: unknown email
    assert res.json() == {"detail": auth_routes.INVALID_LOGIN}


def test_patch_and_put_are_capped_too(world):
    big = b'{"name": "' + b"x" * main.MAX_JSON_BODY_BYTES + b'"}'
    url = f"/families/{world.fid}/people/{world.rosa.id}"
    for method in ("PATCH", "PUT"):
        res = world.ada.request(method, url, content=big, headers={"content-type": "application/json"})
        assert res.status_code == 413, method
        assert res.json() == TOO_LARGE
    assert people_db.get_person(world.db, world.fid, world.rosa.id).name == "Rosa"


def test_a_body_with_no_content_type_gets_the_json_limit(world):
    res = world.client().post("/auth/login", content=b"x" * (main.MAX_JSON_BODY_BYTES + 1))
    assert res.status_code == 413


def test_multipart_over_its_limit_by_content_length_is_413(world):
    """The declared length alone decides; the route never runs."""
    res = world.ada.post(
        f"/families/{world.fid}/memories",
        data={"text": "We sang.", "year": "1988"},
        files=[("photos", ("a.png", _png(), "image/png"))],
        headers={"content-length": str(main.MAX_MULTIPART_BODY_BYTES + 1)},
    )
    assert res.status_code == 413
    assert res.json() == TOO_LARGE
    assert world.db[media_db.INLINE].count_documents({}) == 0


def test_multipart_gets_the_multipart_limit_not_the_json_one(monkeypatch, world):
    """A multipart body over the JSON cap but under the multipart cap is not a 413."""
    app = _small_limit_app(monkeypatch, json_limit=100, multipart_limit=100_000)
    tc = TestClient(app)
    tc.cookies.set(auth.COOKIE_NAME, auth.create_session_token(world.users["ada"].id))
    files = [("file", ("a.png", _png(), "image/png"))]
    res = tc.post(f"/families/{world.fid}/media", files=files)
    assert len(res.request.content) > 100
    assert res.status_code == 201, res.text
    over = [("file", ("a.png", _png() + b"\x00" * 200_000, "image/png"))]
    res = tc.post(f"/families/{world.fid}/media", files=over)
    assert res.status_code == 413
    assert res.json() == TOO_LARGE
    # Same small body as JSON: over the JSON cap.
    res = tc.post("/auth/login", content=b"{" + b" " * 200 + b"}", headers={"content-type": "application/json"})
    assert res.status_code == 413


# --------------------------------------------------------------------- 413 by streamed count


def _chunks(total: int, size: int = 64 * 1024) -> Iterator[bytes]:
    sent = 0
    while sent < total:
        n = min(size, total - sent)
        sent += n
        yield b" " * n


def test_chunked_json_body_over_the_limit_is_413(world):
    """A generator body has no Content-Length (Transfer-Encoding: chunked); it is counted."""
    res = world.client().post(
        "/auth/login",
        content=_chunks(main.MAX_JSON_BODY_BYTES + 1),
        headers={"content-type": "application/json"},
    )
    assert "content-length" not in res.request.headers
    assert res.request.headers.get("transfer-encoding") == "chunked"
    assert res.status_code == 413
    assert res.json() == TOO_LARGE


def test_chunked_json_body_under_the_limit_reaches_the_route(world):
    body = json.dumps({"email": "nobody@example.com", "password": PASSWORD}).encode()
    res = world.client().post(
        "/auth/login", content=iter([body[:10], body[10:]]), headers={"content-type": "application/json"}
    )
    assert "content-length" not in res.request.headers
    assert res.status_code == 401


def test_chunked_multipart_over_the_limit_is_413_and_saves_nothing(monkeypatch, world):
    app = _small_limit_app(monkeypatch, json_limit=1_000, multipart_limit=5_000)
    tc = TestClient(app)
    tc.cookies.set(auth.COOKIE_NAME, auth.create_session_token(world.users["ada"].id))
    request = tc.build_request(
        "POST", f"/families/{world.fid}/memories",
        data={"text": "We sang.", "year": "1988"},
        files=[("photos", ("a.png", _png() + b"\x00" * 20_000, "image/png"))],
    )
    body = request.read()
    content_type = request.headers["content-type"]
    res = tc.post(
        f"/families/{world.fid}/memories",
        content=iter([body[i:i + 1_000] for i in range(0, len(body), 1_000)]),
        headers={"content-type": content_type},
    )
    assert "content-length" not in res.request.headers
    assert res.status_code == 413
    assert res.json() == TOO_LARGE
    assert world.db[media_db.INLINE].count_documents({}) == 0


def _run_asgi(app, chunks: list[bytes], headers: list[tuple[bytes, bytes]] | None = None):
    """Drive an ASGI app with a body in chunks. Returns (sent messages, chunks pulled)."""
    pulled = 0
    sent: list[dict] = []

    async def receive():
        nonlocal pulled
        if pulled < len(chunks):
            pulled += 1
            return {"type": "http.request", "body": chunks[pulled - 1], "more_body": pulled < len(chunks)}
        return {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http", "method": "POST", "path": "/", "raw_path": b"/", "query_string": b"",
        "headers": headers or [(b"content-type", b"application/json")], "http_version": "1.1",
        "scheme": "http", "server": ("test", 80), "client": ("test", 1), "root_path": "",
    }
    asyncio.run(app(scope, receive, send))
    return sent, pulled


def test_streamed_count_stops_reading_at_the_limit_without_buffering():
    """The body is never read past the chunk that crosses the limit, and the app's own
    reaction to the cut-off (here a 400, as FastAPI sends) is replaced by the 413."""

    async def reads_everything(scope, receive, send):
        try:
            while True:
                message = await receive()
                if not message.get("more_body"):
                    break
        except Exception:  # noqa: BLE001 - what FastAPI does with a failed body read
            await send({"type": "http.response.start", "status": 400, "headers": []})
            await send({"type": "http.response.body", "body": b"parse error"})
            return
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    limited = main.BodySizeLimitMiddleware(reads_everything, json_limit=25, multipart_limit=1_000)
    sent, pulled = _run_asgi(limited, [b"x" * 10] * 100)
    assert pulled == 3, "stopped at the third 10-byte chunk (30 > 25), not the hundredth"
    assert [m["status"] for m in sent if m["type"] == "http.response.start"] == [413]
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    assert json.loads(body) == TOO_LARGE

    # Exactly at the limit is fine.
    sent, pulled = _run_asgi(limited, [b"x" * 5] * 5)
    assert pulled == 5
    assert [m["status"] for m in sent if m["type"] == "http.response.start"] == [200]


def test_streamed_count_also_catches_an_exception_that_escapes_the_app():
    async def lets_it_escape(scope, receive, send):
        while (await receive()).get("more_body"):
            pass

    limited = main.BodySizeLimitMiddleware(lets_it_escape, json_limit=25, multipart_limit=1_000)
    sent, _ = _run_asgi(limited, [b"x" * 10] * 10)
    assert [m["status"] for m in sent if m["type"] == "http.response.start"] == [413]


def test_get_and_non_http_scopes_pass_through_untouched():
    seen = []

    async def inner(scope, receive, send):
        seen.append(receive)

    async def receive():  # pragma: no cover - never called
        return {"type": "http.request", "body": b""}

    limited = main.BodySizeLimitMiddleware(inner, json_limit=1, multipart_limit=1)
    for scope in (
        {"type": "http", "method": "GET", "headers": [(b"content-length", b"999999999")]},
        {"type": "lifespan"},
    ):
        asyncio.run(limited(scope, receive, None))
    assert seen == [receive, receive], "the original receive, not a wrapper"


# -------------------------------------------------------------------------------- CORS


def test_413_carries_cors_headers_for_an_allowed_origin(world):
    tc = world.client()
    by_length = tc.post(
        "/auth/login", content=_login_body(main.MAX_JSON_BODY_BYTES + 1),
        headers={"content-type": "application/json", "origin": ORIGIN},
    )
    by_count = tc.post(
        "/auth/login", content=_chunks(main.MAX_JSON_BODY_BYTES + 1),
        headers={"content-type": "application/json", "origin": ORIGIN},
    )
    for res in (by_length, by_count):
        assert res.status_code == 413
        assert res.json() == TOO_LARGE
        assert res.headers["access-control-allow-origin"] == ORIGIN
        assert res.headers["access-control-allow-credentials"] == "true"


def test_413_has_no_cors_headers_for_another_origin(world):
    res = world.client().post(
        "/auth/login", content=_login_body(main.MAX_JSON_BODY_BYTES + 1),
        headers={"content-type": "application/json", "origin": "https://evil.example"},
    )
    assert res.status_code == 413
    assert "access-control-allow-origin" not in res.headers


# ------------------------------------------------- photo submit and SSE through the full app


@pytest.fixture
def demo_app(monkeypatch) -> Iterator[TestClient]:
    """main.create_app() with STITCH_FAKE_DB=1 and scripted runs, run through its lifespan."""
    monkeypatch.setenv("STITCH_FAKE_DB", "1")
    monkeypatch.setenv("STITCH_AGENT", "fake")
    monkeypatch.setenv("STITCH_FAKE_RUN_STEP", "0")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("STITCH_CORS_ORIGINS", raising=False)
    monkeypatch.setattr(routes.memories, "HEARTBEAT_SECONDS", 0.05)
    monkeypatch.setattr(auth_routes, "_dummy_hash", None)
    db_client.set_db(None)
    try:
        with TestClient(main.create_app()) as tc:
            yield tc
    finally:
        agent.runs.configure(None)
        db_client.set_db(None)


def _read_until(tc: TestClient, fid: str, run_id: str, until: str, deadline: float = 20.0) -> list[str]:
    names: list[str] = []
    started = time.monotonic()
    with tc.stream("GET", f"/families/{fid}/runs/{run_id}/stream") as res:
        assert res.status_code == 200
        assert res.headers["content-type"].startswith("text/event-stream")
        for line in res.iter_lines():
            assert time.monotonic() - started < deadline, f"stream stalled after {names}"
            if line.startswith("event:"):
                names.append(line.partition(":")[2].strip())
                if names[-1] == until:
                    return names
    pytest.fail(f"stream ended before {until!r}: {names}")


def test_max_photos_submit_and_its_event_stream_still_work(demo_app):
    tc = demo_app
    login = tc.post("/auth/login", json={"email": demo.DEMO_ACCOUNTS[0].email, "password": demo.DEMO_PASSWORD})
    assert login.status_code == 200, login.text
    files = [
        ("photos", (f"p{i}.png", _png((i * 20, 100, 200 - i * 10)), "image/png"))
        for i in range(routes.memories.MAX_PHOTOS)
    ]
    res = tc.post(
        f"/families/{demo.DEMO_FAMILY_ID}/memories",
        data={"text": "We flew kites on the beach until the string ran out.", "year": "1995"},
        files=files,
        headers={"origin": ORIGIN},
    )
    assert res.status_code == 202, res.text
    assert res.headers["access-control-allow-origin"] == ORIGIN
    run_id = res.json()["runId"]
    names = _read_until(tc, demo.DEMO_FAMILY_ID, run_id, "question")
    assert names[-1] == "question" and all(n in schemas.RUN_EVENTS for n in names)


def test_startup_precomputes_the_dummy_login_hash(demo_app):
    """Bug: the first unknown-email login in a process computed the dummy argon2 hash and
    then verified against it (two argon2 runs where a wrong password costs one), a timing
    tell that the account does not exist. The lifespan now computes it up front."""
    assert auth_routes._dummy_hash is not None and auth_routes._dummy_hash.startswith("$argon2id$")


# ---------------------------------------------------------------------------- family caps


def _create(tc: TestClient, name: str, person: dict | None = None) -> httpx.Response:
    return tc.post("/families", json={"name": name, "person": person or {"name": "Cy"}})


def test_family_name_cap(world):
    cy = world.client(world.users["cy"])
    limit = families_routes.MAX_FAMILY_NAME_CHARS
    assert limit == 100
    at = _create(cy, "  " + "F" * limit + "  ")
    assert at.status_code == 201, at.text
    assert at.json()["family"]["name"] == "F" * limit
    over = _create(cy, "F" * (limit + 1))
    assert over.status_code == 422
    assert over.json() == {"detail": "A family name must be at most 100 characters"}


def test_person_name_cap_on_create_family(world):
    cy = world.client(world.users["cy"])
    limit = families_routes.MAX_PERSON_NAME_CHARS
    assert limit == 100
    before = world.db.families.count_documents({})
    over = _create(cy, "Cy's family", {"name": "C" * (limit + 1)})
    assert over.status_code == 422
    assert over.json() == {"detail": "A name must be at most 100 characters"}
    assert world.db.families.count_documents({}) == before, "nothing written"
    at = _create(cy, "Cy's family", {"name": " " + "C" * limit + " "})
    assert at.status_code == 201
    assert at.json()["person"]["name"] == "C" * limit


def test_person_name_cap_on_add_person(world):
    limit = families_routes.MAX_PERSON_NAME_CHARS
    url = f"/families/{world.fid}/people"
    over = world.ada.post(url, json={"name": "N" * (limit + 1)})
    assert over.status_code == 422
    assert over.json() == {"detail": "A name must be at most 100 characters"}
    at = world.ada.post(url, json={"name": "N" * limit + "\n"})
    assert at.status_code == 201
    assert at.json()["name"] == "N" * limit


def test_person_name_cap_on_patch_person(world):
    limit = families_routes.MAX_PERSON_NAME_CHARS
    url = f"/families/{world.fid}/people/{world.rosa.id}"
    over = world.ada.patch(url, json={"name": "R" * (limit + 1)})
    assert over.status_code == 422
    assert over.json() == {"detail": "A name must be at most 100 characters"}
    assert people_db.get_person(world.db, world.fid, world.rosa.id).name == "Rosa"
    at = world.ada.patch(url, json={"name": "  " + "R" * limit})
    assert at.status_code == 200
    assert at.json()["name"] == "R" * limit


def test_person_name_cap_on_join_by_adding(world):
    limit = families_routes.MAX_PERSON_NAME_CHARS
    code = _invite(world.ada, world.fid)
    ben = world.client(world.users["ben"])
    over = ben.post(f"/invites/{code}/join", json={"person": {"name": "B" * (limit + 1)}})
    assert over.status_code == 422
    assert over.json() == {"detail": "A name must be at most 100 characters"}
    assert families_db.get_member_role(world.db, world.fid, world.users["ben"].id) is None
    at = ben.post(f"/invites/{code}/join", json={"person": {"name": "B" * limit + " "}})
    assert at.status_code == 200
    assert at.json()["person"]["name"] == "B" * limit


@pytest.mark.parametrize(("field", "what"), [("item", "A signature item"), ("notes", "Notes")])
def test_character_free_text_cap(world, field, what):
    limit = families_routes.MAX_PERSON_TEXT_CHARS
    assert limit == 2_000
    over_sheet = {"color": "#3f6fb5", field: "t" * (limit + 1)}
    at_sheet = {"color": "#3f6fb5", field: "  " + "t" * limit + "  "}
    detail = {"detail": f"{what} must be at most 2,000 characters"}

    add = f"/families/{world.fid}/people"
    res = world.ada.post(add, json={"name": "Tia", "character": over_sheet})
    assert res.status_code == 422 and res.json() == detail
    res = world.ada.post(add, json={"name": "Tia", "character": at_sheet})
    assert res.status_code == 201
    assert res.json()["character"][field] == "t" * limit

    patch = f"/families/{world.fid}/people/{world.rosa.id}"
    res = world.ada.patch(patch, json={"character": over_sheet})
    assert res.status_code == 422 and res.json() == detail
    res = world.ada.patch(patch, json={"character": at_sheet})
    assert res.status_code == 200
    assert res.json()["character"][field] == "t" * limit

    cy = world.client(world.users["cy"])
    res = _create(cy, "Cy's family", {"name": "Cy", "character": over_sheet})
    assert res.status_code == 422 and res.json() == detail

    code = _invite(world.ada, world.fid)
    ben = world.client(world.users["ben"])
    res = ben.post(f"/invites/{code}/join", json={"person": {"name": "Ben", "character": over_sheet}})
    assert res.status_code == 422 and res.json() == detail
    assert families_db.get_member_role(world.db, world.fid, world.users["ben"].id) is None


def test_blank_character_text_is_stored_as_none(world):
    res = world.ada.post(
        f"/families/{world.fid}/people",
        json={"name": "Tia", "character": {"color": "#3f6fb5", "item": "   ", "notes": " walks slowly "}},
    )
    assert res.status_code == 201
    assert res.json()["character"]["item"] is None
    assert res.json()["character"]["notes"] == "walks slowly"


# ----------------------------------------------------------------------------- signup caps


def _signup(tc: TestClient, email: str, name: str = "Ana") -> httpx.Response:
    return tc.post("/auth/signup", json={"email": email, "password": PASSWORD, "name": name})


def test_signup_display_name_cap(world):
    limit = auth_routes.MAX_DISPLAY_NAME_CHARS
    assert limit == 100
    over = _signup(world.client(), "ana@example.com", "A" * (limit + 1))
    assert over.status_code == 422
    assert over.json() == {"detail": "Your name must be at most 100 characters"}
    assert world.db.users.count_documents({"email": "ana@example.com"}) == 0
    at = _signup(world.client(), "ana@example.com", "\t" + "A" * limit + " ")
    assert at.status_code == 201
    assert at.json()["user"]["name"] == "A" * limit


def test_signup_email_cap(world):
    limit = auth_routes.MAX_EMAIL_CHARS
    assert limit == 254
    domain = "@example.com"
    at_email = "e" * (limit - len(domain)) + domain
    over = _signup(world.client(), "e" + at_email)
    assert over.status_code == 422
    assert over.json() == {"detail": "Email must be at most 254 characters"}
    assert world.db.users.count_documents({}) == 3
    at = _signup(world.client(), "  " + at_email.upper() + "  ")  # trimmed and lowercased first
    assert at.status_code == 201
    assert at.json()["user"]["email"] == at_email


# ------------------------------------------------------------------- bugs found, one each

# Bug: users_db.normalize_email only asks for an '@', so signup made accounts for "@",
# "ana@", "@example.com" and "a b@example.com".


@pytest.mark.parametrize("email", ["@", "ana@", "@example.com", "a b@example.com", "ana@exa\tmple.com"])
def test_signup_rejects_emails_with_an_empty_side_or_whitespace(world, email):
    res = _signup(world.client(), email)
    assert res.status_code == 422
    assert res.json() == {"detail": "Enter a valid email"}
    assert world.db.users.count_documents({}) == 3


# Bug: a character color was any string ("red; background:url(x)", 1 MB of text), though
# the contract says hex and the stage paints the puppet and its thread with it.


def test_character_color_must_be_hex(world):
    add = f"/families/{world.fid}/people"
    for color in ("red", "#12345", "#1234567", "3f6fb5", "url(x)", ""):
        res = world.ada.post(add, json={"name": "Tia", "character": {"color": color}})
        assert res.status_code == 422, color
        assert res.json() == {"detail": families_routes.BAD_COLOR}
    res = world.ada.patch(f"/families/{world.fid}/people/{world.rosa.id}", json={"character": {"color": "blue"}})
    assert res.status_code == 422
    for color in ("#abc", " #3F6FB5 "):
        res = world.ada.post(add, json={"name": "Tia", "character": {"color": color}})
        assert res.status_code == 201, color
        assert res.json()["character"]["color"] == color.strip()


# Bug: _join_by_claim sent the db layer's AlreadyClaimed text as the 409 detail
# ("this user already has a person in this family"), not a fixed sentence.


def test_claiming_a_second_person_is_a_fixed_sentence(world):
    code = _invite(world.ada, world.fid)
    ben = world.client(world.users["ben"])
    assert ben.post(f"/invites/{code}/join", json={"personId": world.rosa.id}).status_code == 200
    res = ben.post(f"/invites/{code}/join", json={"personId": world.luis.id})
    assert res.status_code == 409
    assert res.json() == {"detail": families_routes.ALREADY_HAVE_PERSON}
    assert people_db.get_person(world.db, world.fid, world.luis.id).user_id is None


def test_a_claim_lost_to_a_race_is_a_fixed_sentence(world, monkeypatch):
    code = _invite(world.ada, world.fid)

    def taken(*_args, **_kwargs):
        raise people_db.AlreadyClaimed("this person is already claimed")

    monkeypatch.setattr(people_db, "claim_person", taken)
    res = world.client(world.users["cy"]).post(f"/invites/{code}/join", json={"personId": world.rosa.id})
    assert res.status_code == 409
    assert res.json() == {"detail": families_routes.PERSON_TAKEN}
    assert families_db.get_member_role(world.db, world.fid, world.users["cy"].id) is None


# Checked, not a bug (kept as a regression): a revoked code stays dead for preview and join,
# even for someone already in the family, and a fresh invite works alongside it.


def test_revoked_invite_code_is_never_reusable(world):
    old = _invite(world.ada, world.fid)
    assert world.ada.delete(f"/families/{world.fid}/invites/{old}").status_code == 204
    ben = world.client(world.users["ben"])
    assert ben.get(f"/invites/{old}").status_code == 404
    assert ben.post(f"/invites/{old}/join", json={"personId": world.rosa.id}).status_code == 404
    assert world.ada.post(f"/invites/{old}/join", json={"personId": world.ada_p.id}).status_code == 404
    new = _invite(world.ada, world.fid)
    assert new != old
    assert ben.post(f"/invites/{new}/join", json={"personId": world.rosa.id}).status_code == 200
    assert ben.post(f"/invites/{old}/join", json={"personId": world.rosa.id}).status_code == 404
