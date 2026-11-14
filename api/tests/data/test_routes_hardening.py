"""Route hardening regressions: no internal error text in responses, size limits, and edge cases.

Each test here was written against a real bug on origin/main (see the comment above each
group). Runs on mongomock with a fake agent seam; no network, no keys.
"""

from __future__ import annotations

import logging
import sys
import types

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import auth
import main
import schemas
from db import client as db_client
from db import families as families_db
from db import media as media_db
from db import memories as memories_db
from db import people as people_db
from db import props as props_db
from db import search as search_db
from db import users as users_db
from db.client import get_db
from routes import ask as ask_routes
from routes import auth as auth_routes
from routes import memories as memories_routes
from routes import props as props_routes
from routes import tree as tree_routes

FAM_A = "fam_a"
FAM_B = "fam_b"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32 + b"hardening"
PASSWORD = "correct horse battery"


# ------------------------------------------------------------------------------ fake seam


class RunNotWaiting(RuntimeError, ValueError):
    """Shaped like agent.runs.RunNotWaiting."""


class Seam:
    """agent.runs stand-in whose start_run / answer_run raise what a test sets."""

    def __init__(self) -> None:
        self.start_error: BaseException | None = None
        self.answer_error: BaseException | None = None
        self.started: list[tuple] = []
        self.answered: list[tuple] = []

    def start_run(self, family_id, user_id, person_id, fields, photo_ids):
        self.started.append((family_id, user_id, person_id, fields, photo_ids))
        if self.start_error is not None:
            raise self.start_error
        return "run-1"

    def answer_run(self, family_id, run_id, user_id, answer):
        self.answered.append((family_id, run_id, user_id, answer))
        if self.answer_error is not None:
            raise self.answer_error

    def stream_run(self, family_id, run_id):  # pragma: no cover - not driven here
        raise LookupError(run_id)

    def module(self) -> types.ModuleType:
        mod = types.ModuleType("agent.runs")
        mod.start_run = self.start_run
        mod.answer_run = self.answer_run
        mod.stream_run = self.stream_run
        mod.RunNotWaiting = RunNotWaiting
        return mod


@pytest.fixture
def seam(monkeypatch) -> Seam:
    fake = Seam()
    monkeypatch.setitem(sys.modules, "agent.runs", fake.module())
    return fake


# ------------------------------------------------------------------------------ world


class World(types.SimpleNamespace):
    pass


@pytest.fixture
def world(mdb, seam) -> World:
    """Family A (Alice, with her person, and Grandma) and family B (Bob). Every router mounted."""
    db_client.ensure_indexes(mdb)
    search_db.set_embedder(None)
    alice = users_db.create_user(mdb, email="alice@x.com", password_hash=auth.hash_password(PASSWORD), name="Alice")
    bob = users_db.create_user(mdb, email="bob@x.com", password_hash=auth.hash_password(PASSWORD), name="Bob")
    families_db.create_family(mdb, name="Family A", creator_user_id=alice.id, family_id=FAM_A)
    families_db.create_family(mdb, name="Family B", creator_user_id=bob.id, family_id=FAM_B)
    alice_p = people_db.create_person(mdb, FAM_A, schemas.NewPerson(name="Alice"), added_by=alice.id,
                                      user_id=alice.id, person_id="alice_p")
    grandma = people_db.create_person(mdb, FAM_A, schemas.NewPerson(name="Grandma"), added_by=alice.id,
                                      person_id="grandma")
    people_db.create_person(mdb, FAM_B, schemas.NewPerson(name="Bob"), added_by=bob.id, user_id=bob.id,
                            person_id="bob_p")
    memories_db.create_memory(
        mdb, FAM_A, title="Hymn books", year=1988, participant_ids=[grandma.id],
        telling=schemas.Telling(author_id=grandma.id, text="We carried the hymn books up the church steps."),
        memory_id="m_hymn",
    )
    props_db.insert_prop(
        mdb, FAM_A,
        schemas.Prop(id="trumpet_case", name="trumpet case", size="hand", art={"body": None}, color="#2f4a7a"),
        prop_id="trumpet_case",
    )
    photo = media_db.put_media(mdb, FAM_A, PNG, content_type="image/png", filename="a.png")
    audio = media_db.put_media(mdb, FAM_A, b"ID3 narration", content_type="audio/mpeg", kind="audio")

    app = FastAPI()
    app.add_exception_handler(main.RequestValidationError, main.validation_error_handler)
    for router in main.ROUTERS:
        app.include_router(router)
    app.dependency_overrides[get_db] = lambda: mdb
    db_client.set_db(mdb)  # routes/auth.py tests below sign in through the real dependency too
    tc = TestClient(app)
    tc.cookies.set(auth.COOKIE_NAME, auth.create_session_token(alice.id))
    yield World(db=mdb, app=app, tc=tc, seam=seam, alice=alice, bob=bob, alice_p=alice_p, grandma=grandma,
                photo=photo, audio=audio)
    db_client.set_db(None)
    search_db.set_embedder(None)


def _media_count(db) -> int:
    return db[media_db.INLINE].count_documents({})


def _submit(tc: TestClient, data: dict | None = None, files: list | None = None):
    form = {"text": "We sang from the green hymn books.", "year": "1988"}
    form.update(data or {})
    return tc.post(f"/families/{FAM_A}/memories", data=form, files=files or [])


# ------------------------------------------------------------------- answer: no exception text
# Bug on origin/main: routes/memories._answer_error sent str(exc) as the 409 detail, so an
# agent ValueError leaked run ids, tool names and parser internals to the client.

ANSWER = {"kind": "answer", "text": "Green"}


@pytest.mark.parametrize(
    ("message", "detail"),
    [
        ("run 8f3a2c91 is waiting for kind='props', got 'answer'", memories_routes.ANSWER_WRONG_KIND),
        ("answer kind 'answer' does not match the pending 'confirm_props'", memories_routes.ANSWER_WRONG_KIND),
        ("run 8f3a2c91 is done, not waiting for an answer", memories_routes.NOT_WAITING),
        ("ask_teller args need a non-empty string 'question'", memories_routes.ANSWER_CONFLICT),
        ("not a human-in-the-loop interrupt: dict", memories_routes.ANSWER_CONFLICT),
        ("confirm_props has a malformed prop: 1 validation error for PropChip", memories_routes.ANSWER_CONFLICT),
        ("memory 'm9' belongs to family 'fam_b', not 'fam_a'", memories_routes.ANSWER_CONFLICT),
        ("key sk-ant-api03-SECRET", memories_routes.ANSWER_CONFLICT),
    ],
)
def test_answer_value_error_never_echoes_its_text(world, caplog, message, detail):
    world.seam.answer_error = ValueError(message)
    with caplog.at_level(logging.INFO, logger=memories_routes.log.name):
        res = world.tc.post(f"/families/{FAM_A}/runs/run-1/answer", json=ANSWER)
    assert res.status_code == 409
    assert res.json() == {"detail": detail}
    for fragment in ("8f3a2c91", "ask_teller", "confirm_props", "fam_b", "sk-ant", "interrupt", "PropChip"):
        assert fragment not in res.text
    assert message in caplog.text, "the original goes to the log"


def test_run_not_waiting_class_is_still_the_not_waiting_sentence(world):
    world.seam.answer_error = RunNotWaiting("run 8f3a2c91 is done, not waiting for an answer")
    res = world.tc.post(f"/families/{FAM_A}/runs/run-1/answer", json=ANSWER)
    assert res.status_code == 409
    assert res.json() == {"detail": memories_routes.NOT_WAITING}


# ------------------------------------------------------------------- submit: no exception text
# Bug on origin/main: a ValueError from start_run was sent back as the 422 detail.


def test_submit_value_error_never_echoes_its_text(world, caplog):
    world.seam.start_error = ValueError("memory 'm9' belongs to family 'fam_b', not 'fam_a'")
    with caplog.at_level(logging.INFO, logger=memories_routes.log.name):
        res = _submit(world.tc, files=[("photos", ("a.png", PNG, "image/png"))])
    assert res.status_code == 422
    assert res.json() == {"detail": memories_routes.SUBMIT_REFUSED}
    assert "fam_b" not in res.text and "m9" not in res.text
    assert "belongs to family 'fam_b'" in caplog.text
    assert _media_count(world.db) == 2, "the submitted photo was deleted again (2 = the fixture's own media)"


# ------------------------------------------------------------------- submit: size limits
# Bug on origin/main: no upper bound on text, question, title or participantIds.


@pytest.mark.parametrize(
    ("field", "limit", "message"),
    [
        ("text", memories_routes.MAX_TEXT_CHARS, "The memory must be at most 20,000 characters"),
        ("question", memories_routes.MAX_QUESTION_CHARS, "The question must be at most 1,000 characters"),
        ("title", memories_routes.MAX_TITLE_CHARS, "The title must be at most 200 characters"),
    ],
)
def test_submit_text_fields_are_bounded_at_the_limit(world, field, limit, message):
    at_limit = _submit(world.tc, {field: "a" * limit})
    assert at_limit.status_code == 202, at_limit.text
    assert len(world.seam.started) == 1

    before = _media_count(world.db)
    over = _submit(world.tc, {field: "a" * (limit + 1)}, files=[("photos", ("a.png", PNG, "image/png"))])
    assert over.status_code == 422
    assert over.json() == {"detail": message}
    assert len(world.seam.started) == 1, "the agent is never reached"
    assert _media_count(world.db) == before, "no photo is stored"


def test_submit_limits_are_generous_for_real_memories():
    assert memories_routes.MAX_TEXT_CHARS == 20_000
    assert memories_routes.MAX_QUESTION_CHARS >= 500
    assert memories_routes.MAX_TITLE_CHARS >= 120
    assert memories_routes.MAX_PARTICIPANTS >= 50


def test_submit_participant_count_is_bounded(world):
    limit = memories_routes.MAX_PARTICIPANTS
    at_limit = _submit(world.tc, {"participantIds": [f"p{i}" for i in range(limit)]})
    # Past the size check: the ids are then looked up (and these do not exist).
    assert at_limit.status_code == 400
    assert at_limit.json() == {"detail": "Every participant must be a person in this family"}

    over = _submit(world.tc, {"participantIds": [f"p{i}" for i in range(limit + 1)]})
    assert over.status_code == 422
    assert over.json() == {"detail": f"A memory can have at most {limit} people in it"}
    assert world.seam.started == []


def test_submit_other_familys_memory_is_404_before_any_photo_is_stored(world, mdb):
    memories_db.create_memory(mdb, FAM_B, title="B", year=1990, participant_ids=["bob_p"], memory_id="m_b")
    before = _media_count(world.db)
    res = _submit(world.tc, {"memoryId": "m_b"}, files=[("photos", ("a.png", PNG, "image/png"))])
    assert res.status_code == 404
    assert res.json() == {"detail": "Memory not found"}
    assert _media_count(world.db) == before
    assert world.seam.started == []


# ------------------------------------------------------------------- answer: size limits
# Bug on origin/main: a JSON answer of any size went straight to the agent.


def test_answer_text_is_bounded_at_the_limit(world):
    limit = memories_routes.MAX_ANSWER_CHARS
    ok = world.tc.post(f"/families/{FAM_A}/runs/run-1/answer", json={"kind": "answer", "text": "a" * limit})
    assert ok.status_code == 204
    over = world.tc.post(f"/families/{FAM_A}/runs/run-1/answer", json={"kind": "answer", "text": "a" * (limit + 1)})
    assert over.status_code == 422
    assert over.json() == {"detail": "The answer must be at most 20,000 characters"}
    assert len(world.seam.answered) == 1


def test_answer_props_review_is_bounded(world):
    limit = memories_routes.MAX_PROP_EDITS
    keep = [{"id": f"p{i}", "action": "keep"} for i in range(limit)]
    assert world.tc.post(f"/families/{FAM_A}/runs/run-1/answer", json={"kind": "props", "props": keep}).status_code == 204
    too_many = keep + [{"id": "extra", "action": "keep"}]
    res = world.tc.post(f"/families/{FAM_A}/runs/run-1/answer", json={"kind": "props", "props": too_many})
    assert res.status_code == 422
    assert res.json() == {"detail": f"A props review can list at most {limit} props"}

    long_name = [{"id": "p0", "action": "rename", "name": "n" * (memories_routes.MAX_TITLE_CHARS + 1)}]
    res = world.tc.post(f"/families/{FAM_A}/runs/run-1/answer", json={"kind": "props", "props": long_name})
    assert res.status_code == 422
    assert res.json() == {"detail": "A prop name must be at most 200 characters"}
    assert len(world.seam.answered) == 1


# ------------------------------------------------------------------- ask: query length
# Bug on origin/main: no bound on the query, which is tokenised against every memory and
# sent to the embedder.


def test_ask_query_is_bounded_at_the_limit_after_trimming(world, monkeypatch):
    limit = ask_routes.MAX_QUERY_CHARS
    at_limit = "hymn " + "b" * (limit - 5)
    assert len(at_limit) == limit
    res = world.tc.post(f"/families/{FAM_A}/ask", json={"query": f"   {at_limit}   "})
    assert res.status_code == 200, res.text

    def never(*args, **kwargs):
        raise AssertionError("an over-long query must not be searched")

    monkeypatch.setattr(search_db, "ask_quotes", never)
    over = world.tc.post(f"/families/{FAM_A}/ask", json={"query": "a" * (limit + 1)})
    assert over.status_code == 422
    assert over.json() == {"detail": "Keep the question to 1,000 characters or fewer"}


def test_ask_blank_query_is_still_422(world):
    res = world.tc.post(f"/families/{FAM_A}/ask", json={"query": " \n\t "})
    assert res.status_code == 422
    assert res.json() == {"detail": "Ask a question"}


# ------------------------------------------------------------------- media: Range digits
# Bug on origin/main: int() refuses strings over 4300 digits, so one Range header with a
# very long number raised ValueError inside the route: a 500.


def _media(world: World, range_header: str):
    return world.tc.get(f"/families/{FAM_A}/media/{world.photo}", headers={"Range": range_header})


def test_media_range_with_a_huge_start_is_416(world):
    res = _media(world, "bytes=" + "9" * 5000 + "-")
    assert res.status_code == 416
    assert res.headers["content-range"] == f"bytes */{len(PNG)}"


def test_media_range_with_a_huge_end_is_clamped(world):
    res = _media(world, "bytes=2-" + "9" * 5000)
    assert res.status_code == 206
    assert res.content == PNG[2:]
    assert res.headers["content-range"] == f"bytes 2-{len(PNG) - 1}/{len(PNG)}"


def test_media_range_with_a_huge_suffix_is_the_whole_file(world):
    res = _media(world, "bytes=-" + "9" * 5000)
    assert res.status_code == 206
    assert res.content == PNG


def test_media_range_with_leading_zeros_is_read_as_a_number(world):
    res = _media(world, "bytes=" + "0" * 5000 + "1-0003")
    assert res.status_code == 206
    assert res.content == PNG[1:4]


# ------------------------------------------------------------------- props: photo must be an image
# Bug on origin/main: any media id of the family was accepted as a prop's new art, including
# narration audio, which the stage cannot draw.


def test_prop_photo_that_is_narration_audio_is_400(world):
    for photo_id in (world.audio, f"media/{world.audio}"):
        res = world.tc.patch(f"/families/{FAM_A}/props/trumpet_case", json={"photoId": photo_id})
        assert res.status_code == 400
        assert res.json() == {"detail": props_routes.NOT_A_PHOTO}
    prop = props_db.get_prop(world.db, FAM_A, "trumpet_case")
    assert prop.art == {"body": None}


def test_prop_photo_that_is_an_image_still_works(world):
    res = world.tc.patch(f"/families/{FAM_A}/props/trumpet_case", json={"photoId": world.photo})
    assert res.status_code == 200, res.text
    assert res.json()["art"] == {"body": f"media/{world.photo}"}


def test_prop_rename_is_bounded_at_the_limit(world):
    limit = props_routes.MAX_PROP_NAME_CHARS
    ok = world.tc.patch(f"/families/{FAM_A}/props/trumpet_case", json={"name": "n" * limit})
    assert ok.status_code == 200
    over = world.tc.patch(f"/families/{FAM_A}/props/trumpet_case", json={"name": "n" * (limit + 1)})
    assert over.status_code == 422
    assert over.json() == {"detail": f"Name must be at most {limit} characters"}
    assert props_db.get_prop(world.db, FAM_A, "trumpet_case").name == "n" * limit


# ------------------------------------------------------------------- auth: Secure cookie
# Bug on origin/main: Secure was set only with COOKIE_SECURE=1 (documented nowhere), so a
# deployment behind https sent the session cookie without Secure.


def _cookie(res) -> str:
    headers = [v for k, v in res.headers.multi_items() if k.lower() == "set-cookie"]
    assert len(headers) == 1, headers
    return headers[0].lower()


def _https(world: World) -> TestClient:
    return TestClient(world.app, base_url="https://stitch.example")


def test_login_over_https_sets_a_secure_cookie(world):
    res = _https(world).post("/auth/login", json={"email": "alice@x.com", "password": PASSWORD})
    assert res.status_code == 200
    cookie = _cookie(res)
    assert "; secure" in cookie and "httponly" in cookie and "samesite=lax" in cookie


def test_signup_and_logout_over_https_are_secure_too(world):
    tc = _https(world)
    res = tc.post("/auth/signup", json={"email": "new@x.com", "password": PASSWORD, "name": "New"})
    assert res.status_code == 201
    assert "; secure" in _cookie(res)
    out = tc.post("/auth/logout")
    assert out.status_code == 204
    assert "; secure" in _cookie(out)


def test_login_over_plain_http_is_not_secure(world):
    res = TestClient(world.app).post("/auth/login", json={"email": "alice@x.com", "password": PASSWORD})
    assert res.status_code == 200
    assert "secure" not in _cookie(res)


@pytest.mark.parametrize(("raw", "base_url", "secure"), [
    ("1", "http://localhost", True),
    ("true", "http://localhost", True),
    ("YES", "http://localhost", True),
    ("0", "https://stitch.example", False),
    ("false", "https://stitch.example", False),
    ("", "https://stitch.example", True),
    ("maybe", "http://localhost", False),
])
def test_cookie_secure_env_overrides_the_scheme(world, monkeypatch, raw, base_url, secure):
    monkeypatch.setenv("COOKIE_SECURE", raw)
    res = TestClient(world.app, base_url=base_url).post(
        "/auth/login", json={"email": "alice@x.com", "password": PASSWORD}
    )
    assert res.status_code == 200
    assert ("; secure" in _cookie(res)) is secure


def test_set_session_cookie_without_a_request_keeps_the_old_behaviour(monkeypatch):
    from fastapi import Response

    response = Response()
    auth.set_session_cookie(response, "u1")
    assert "secure" not in response.headers["set-cookie"].lower()
    monkeypatch.setenv("COOKIE_SECURE", "1")
    response = Response()
    auth.set_session_cookie(response, "u1")
    assert "; secure" in response.headers["set-cookie"].lower()


def test_auth_router_still_has_its_five_routes():
    assert {(m, r.path) for r in auth_routes.router.routes for m in r.methods} == {
        ("POST", "/auth/signup"), ("POST", "/auth/login"), ("POST", "/auth/logout"), ("GET", "/me"),
        ("GET", "/session"),
    }


# ------------------------------------------------------------------- main: CORS origins
# Bug on origin/main: an origin configured with a trailing slash never matched, because a
# browser's Origin header has none and CORS compares exactly.


def test_cors_origin_with_a_trailing_slash_matches_the_browser_origin(monkeypatch):
    monkeypatch.setenv("STITCH_CORS_ORIGINS", "https://stitch.example/, http://localhost:3000")
    assert main.cors_origins() == ["https://stitch.example", "http://localhost:3000"]
    app = main.create_app()
    res = TestClient(app).options(
        "/health",
        headers={"Origin": "https://stitch.example", "Access-Control-Request-Method": "GET"},
    )
    assert res.status_code == 200
    assert res.headers["access-control-allow-origin"] == "https://stitch.example"
    assert res.headers["access-control-allow-credentials"] == "true"


@pytest.mark.parametrize("raw", ["*/", "/", " , /"])
def test_cors_origins_never_wildcard_after_normalising(monkeypatch, raw):
    monkeypatch.setenv("STITCH_CORS_ORIGINS", raw)
    assert main.cors_origins() == list(main.DEFAULT_CORS_ORIGINS)


# ------------------------------------------------------------------- the offline demo path
# The scripted runs (seed/fake_runs.py) raise plain ValueErrors naming the run id; through
# the real app on the fake db, a wrong-kind answer must be a fixed sentence.


def test_fake_db_wrong_kind_answer_does_not_leak_the_run_id(monkeypatch):
    import agent.runs
    from seed.demo import DEMO_FAMILY_ID, DEMO_PASSWORD

    for name in ("STITCH_CORS_ORIGINS", "STITCH_AGENT", "ANTHROPIC_API_KEY", "VOYAGE_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("STITCH_FAKE_DB", "1")
    monkeypatch.setenv("STITCH_FAKE_RUN_STEP", "0")
    try:
        with TestClient(main.create_app()) as c:
            login = c.post("/auth/login", json={"email": "sam@demo.stitch", "password": DEMO_PASSWORD})
            assert login.status_code == 200, login.text
            res = c.post(f"/families/{DEMO_FAMILY_ID}/memories",
                         data={"text": "We sang from the green hymn books on the church steps.", "year": "1998"})
            assert res.status_code == 202, res.text
            run_id = res.json()["runId"]
            manager = agent.runs.get_manager()
            assert manager.wait(DEMO_FAMILY_ID, run_id, 10) == "waiting"
            pending = [name for name, _ in manager.events(DEMO_FAMILY_ID, run_id)][-1]
            wrong = {"kind": "merge", "approve": True} if pending != "merge" else ANSWER
            answer = c.post(f"/families/{DEMO_FAMILY_ID}/runs/{run_id}/answer", json=wrong)
            assert answer.status_code == 409
            assert answer.json() == {"detail": memories_routes.ANSWER_WRONG_KIND}
            assert run_id not in answer.text
    finally:
        db_client.set_db(None)
        agent.runs.configure(None)


# ------------------------------------------------------------------- tree: unresolved people


def test_book_and_memory_render_when_a_participant_or_teller_no_longer_resolves(world, mdb):
    """Not a bug on origin/main (kept as a guard): unknown person ids are skipped, never a 500."""
    memories_db.create_memory(
        mdb, FAM_A, title="Gone", year=1950, participant_ids=["grandma"],
        telling=schemas.Telling(author_id="grandma", text="Someone told this once."), memory_id="m_gone",
    )
    # db.memories refuses unknown people on write, so stand in for a person removed later.
    mdb["memories"].update_one(
        {"_id": "m_gone"},
        {"$set": {"participantIds": ["grandma", "ghost"], "tellings.0.authorId": "ghost_teller"}},
    )
    memory = world.tc.get(f"/families/{FAM_A}/memories/m_gone")
    assert memory.status_code == 200, memory.text
    assert [p["id"] for p in memory.json()["people"]] == ["grandma"]
    book = world.tc.get(f"/families/{FAM_A}/people/grandma/book")
    assert book.status_code == 200
    assert "m_gone" in {m["id"] for m in book.json()["memories"]}
    assert tree_routes.NOT_FOUND not in book.text
