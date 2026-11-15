"""routes/questions.py: POST /families/{fid}/questions (ask a person a question). Runs on mongomock."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import auth
import schemas
from db import client as db_client
from db import families as families_db
from db import memories as memories_db
from db import people as people_db
from db import users as users_db
from db.client import get_db
from main import ROUTERS
from routes import ask as ask_routes
from routes import questions as questions_routes

FAM_A = "fam_a"
FAM_B = "fam_b"


@pytest.fixture
def env(mdb):
    """Family A: Alice (admin, with her person), Dan (member, with his person) and Grandma
    (no account). Family B: Bob and Bo. Carol has an account and no family."""
    db_client.ensure_indexes(mdb)
    alice = users_db.create_user(mdb, email="alice@x.com", password_hash="unused", name="Alice")
    dan = users_db.create_user(mdb, email="dan@x.com", password_hash="unused", name="Dan")
    bob = users_db.create_user(mdb, email="bob@x.com", password_hash="unused", name="Bob")
    carol = users_db.create_user(mdb, email="carol@x.com", password_hash="unused", name="Carol")
    families_db.create_family(mdb, name="Family A", creator_user_id=alice.id, family_id=FAM_A)
    families_db.add_member(mdb, FAM_A, dan.id, "member")
    families_db.create_family(mdb, name="Family B", creator_user_id=bob.id, family_id=FAM_B)
    me = people_db.create_person(mdb, FAM_A, schemas.NewPerson(name="Alice"), added_by=alice.id, user_id=alice.id)
    dan_p = people_db.create_person(mdb, FAM_A, schemas.NewPerson(name="Dan"), added_by=alice.id, user_id=dan.id)
    grandma = people_db.create_person(mdb, FAM_A, schemas.NewPerson(name="Grandma"), added_by=alice.id)
    bo = people_db.create_person(mdb, FAM_B, schemas.NewPerson(name="Bo"), added_by=bob.id, user_id=bob.id)

    app = FastAPI()
    app.include_router(questions_routes.router)
    app.include_router(ask_routes.router)  # GET /families/{fid}/questions
    app.dependency_overrides[get_db] = lambda: mdb

    def client_for(user: schemas.User | None) -> TestClient:
        tc = TestClient(app)
        if user is not None:
            tc.cookies.set(auth.COOKIE_NAME, auth.create_session_token(user.id))
        return tc

    return {"db": mdb, "client_for": client_for, "alice": alice, "dan": dan, "bob": bob, "carol": carol,
            "me": me, "dan_p": dan_p, "grandma": grandma, "bo": bo}


def _url(fid: str = FAM_A) -> str:
    return f"/families/{fid}/questions"


def _count(db) -> int:
    return db["questions"].count_documents({})


def test_router_is_mounted():
    assert questions_routes.router in ROUTERS


def test_ask_a_relative_then_it_is_their_open_question(env):
    alice = env["client_for"](env["alice"])
    res = alice.post(_url(), json={"personId": env["dan_p"].id, "text": "  How did you and Grace meet?  "})
    assert res.status_code == 201, res.text
    q = res.json()
    assert q["familyId"] == FAM_A
    assert q["personId"] == env["dan_p"].id
    assert q["text"] == "How did you and Grace meet?"  # trimmed
    assert q["status"] == "open"
    assert q["memoryId"] is None
    assert q["id"]

    # It is Dan's open question: his GET lists it, the store's open_questions has it.
    dan = env["client_for"](env["dan"])
    assert [x["id"] for x in dan.get(_url()).json()["questions"]] == [q["id"]]
    assert [x.id for x in memories_db.open_questions(env["db"], FAM_A, env["dan_p"].id)] == [q["id"]]
    # Not Alice's.
    assert alice.get(_url()).json() == {"questions": []}


def test_a_member_may_ask_anyone_including_themselves_and_relatives_without_accounts(env):
    dan = env["client_for"](env["dan"])
    for person in (env["grandma"], env["me"], env["dan_p"]):
        res = dan.post(_url(), json={"personId": person.id, "text": "What was your first job?"})
        assert res.status_code == 201, res.text
        assert res.json()["personId"] == person.id
    assert [q.text for q in memories_db.open_questions(env["db"], FAM_A, env["grandma"].id)] == [
        "What was your first job?"
    ]


def test_newest_question_is_listed_first(env):
    alice = env["client_for"](env["alice"])
    first = alice.post(_url(), json={"personId": env["me"].id, "text": "First?"}).json()["id"]
    second = alice.post(_url(), json={"personId": env["me"].id, "text": "Second?"}).json()["id"]
    assert [x["id"] for x in alice.get(_url()).json()["questions"]] == [second, first]


@pytest.mark.parametrize(
    "text",
    ["", "   ", "\n\t", "x" * (schemas.MAX_QUESTION_CHARS + 1), " " + "y" * (schemas.MAX_QUESTION_CHARS + 1)],
    ids=["empty", "spaces", "whitespace", "too-long", "too-long-after-trim"],
)
def test_blank_or_too_long_text_is_422(env, text):
    alice = env["client_for"](env["alice"])
    res = alice.post(_url(), json={"personId": env["dan_p"].id, "text": text})
    assert res.status_code == 422, res.text
    assert _count(env["db"]) == 0


def test_text_at_the_limit_is_fine(env):
    alice = env["client_for"](env["alice"])
    text = "z" * schemas.MAX_QUESTION_CHARS
    res = alice.post(_url(), json={"personId": env["dan_p"].id, "text": f"  {text}  "})
    assert res.status_code == 201, res.text
    assert res.json()["text"] == text


@pytest.mark.parametrize("body", [{"text": "Hi?"}, {"personId": "p"}, {}], ids=["no-person", "no-text", "empty"])
def test_missing_fields_are_422(env, body):
    res = env["client_for"](env["alice"]).post(_url(), json=body)
    assert res.status_code == 422, res.text
    assert _count(env["db"]) == 0


def test_unknown_person_is_404(env):
    res = env["client_for"](env["alice"]).post(_url(), json={"personId": "nobody", "text": "Hello?"})
    assert res.status_code == 404, res.text
    assert res.json()["detail"] == questions_routes.PERSON_NOT_FOUND
    assert _count(env["db"]) == 0


def test_person_from_another_family_is_404(env):
    res = env["client_for"](env["alice"]).post(_url(), json={"personId": env["bo"].id, "text": "Hello?"})
    assert res.status_code == 404, res.text
    assert res.json()["detail"] == questions_routes.PERSON_NOT_FOUND
    assert _count(env["db"]) == 0


@pytest.mark.parametrize("who", ["carol", "bob"], ids=["no-family", "other-family"])
def test_non_member_is_404(env, who):
    res = env["client_for"](env[who]).post(_url(), json={"personId": env["grandma"].id, "text": "Hello?"})
    assert res.status_code == 404, res.text
    assert _count(env["db"]) == 0


def test_unknown_family_is_404(env):
    res = env["client_for"](env["alice"]).post(_url("no_such_family"), json={"personId": "x", "text": "Hello?"})
    assert res.status_code == 404, res.text


@pytest.mark.parametrize("token", [None, "not-a-jwt"], ids=["no-cookie", "garbage"])
def test_signed_out_is_401(env, token):
    tc = env["client_for"](None)
    if token is not None:
        tc.cookies.set(auth.COOKIE_NAME, token)
    res = tc.post(_url(), json={"personId": env["grandma"].id, "text": "Hello?"})
    assert res.status_code == 401, res.text
    assert _count(env["db"]) == 0
