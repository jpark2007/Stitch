"""routes/ask.py: 'Ask the family' and the caller's open questions. Runs on mongomock."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import auth
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
from routes import props as props_routes

API_DIR = Path(__file__).resolve().parents[2]
FAM_A = "fam_a"
FAM_B = "fam_b"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32

HYMN_TELLING = (
    "It rained all morning. We carried the blue hymn books up the church steps, "
    "two stacks each. Father Doyle laughed when David dropped his."
)
SEDAN_TELLING = "Dad drove the green sedan to the lake every July. The windows never closed."
B_TELLING = "Our hymn books were red, and we sang the loudest in the church choir."


def _signed_in(tc: TestClient, user_id: str) -> TestClient:
    tc.cookies.set(auth.COOKIE_NAME, auth.create_session_token(user_id))
    return tc


@pytest.fixture
def env(mdb):
    """Family A: Alice (has a person), Carol (member, no person), Grandma Rose and Dad (no accounts).
    Family B: Bob, with his own memory about hymn books."""
    db_client.ensure_indexes(mdb)
    search_db.set_embedder(None)  # keyword search; never a network call

    def user(email: str, name: str) -> schemas.User:
        return users_db.create_user(mdb, email=email, password_hash=auth.hash_password(f"pw-{name}"), name=name)

    alice, carol, bob = user("alice@x.com", "Alice"), user("carol@x.com", "Carol"), user("bob@x.com", "Bob")
    families_db.create_family(mdb, name="Family A", creator_user_id=alice.id, family_id=FAM_A)
    families_db.add_member(mdb, FAM_A, carol.id, "member")
    families_db.create_family(mdb, name="Family B", creator_user_id=bob.id, family_id=FAM_B)

    def person(fid: str, name: str, *, by: str, user_id: str | None = None, pid: str | None = None,
               born: int | None = None) -> schemas.Person:
        return people_db.create_person(mdb, fid, schemas.NewPerson(name=name, born=born), added_by=by,
                                       user_id=user_id, person_id=pid)

    rose = person(FAM_A, "Grandma Rose", by=alice.id, pid="rose", born=1931)
    dad = person(FAM_A, "Dad", by=alice.id, pid="dad", born=1958)
    alice_p = person(FAM_A, "Alice", by=alice.id, user_id=alice.id, pid="alice_p", born=1990)
    bob_p = person(FAM_B, "Bob", by=bob.id, user_id=bob.id, pid="bob_p")

    hymn = memories_db.create_memory(
        mdb, FAM_A, title="Hymn books at St. Mary's", year=1988, participant_ids=[rose.id, dad.id],
        telling=schemas.Telling(author_id=rose.id, question="What happened at church?", text=HYMN_TELLING),
        memory_id="m_hymn",
    )
    sedan = memories_db.create_memory(
        mdb, FAM_A, title="The green sedan", year=1972, participant_ids=[dad.id],
        telling=schemas.Telling(author_id=dad.id, text=SEDAN_TELLING), memory_id="m_sedan",
    )
    b_memory = memories_db.create_memory(
        mdb, FAM_B, title="Choir practice", year=1990, participant_ids=[bob_p.id],
        telling=schemas.Telling(author_id=bob_p.id, text=B_TELLING), memory_id="m_b_choir",
    )

    q_open = memories_db.create_question(mdb, FAM_A, person_id=alice_p.id, text="What do you remember of the lake?",
                                         memory_id=sedan.id)
    q_answered = memories_db.create_question(mdb, FAM_A, person_id=alice_p.id, text="Were you at church?")
    memories_db.set_question_status(mdb, FAM_A, q_answered.id, "answered")
    q_dismissed = memories_db.create_question(mdb, FAM_A, person_id=alice_p.id, text="Old question")
    memories_db.set_question_status(mdb, FAM_A, q_dismissed.id, "dismissed")
    q_rose = memories_db.create_question(mdb, FAM_A, person_id=rose.id, text="Tell us about the steps")
    q_bob = memories_db.create_question(mdb, FAM_B, person_id=bob_p.id, text="Which hymn?")

    app = FastAPI()
    app.include_router(ask_routes.router)
    app.include_router(props_routes.router)
    app.dependency_overrides[get_db] = lambda: mdb
    tc = _signed_in(TestClient(app), alice.id)
    yield {
        "tc": tc, "app": app, "db": mdb, "alice": alice, "carol": carol, "bob": bob,
        "rose": rose, "dad": dad, "alice_p": alice_p, "bob_p": bob_p,
        "hymn": hymn, "sedan": sedan, "b_memory": b_memory,
        "q_open": q_open, "q_rose": q_rose, "q_bob": q_bob,
    }
    search_db.set_embedder(None)


# --------------------------------------------------------------------- ask


def test_ask_quotes_the_matching_telling_with_the_authors_name(env):
    resp = env["tc"].post(f"/families/{FAM_A}/ask", json={"query": "Who carried the hymn books?"})
    assert resp.status_code == 200
    body = resp.json()
    schemas.AskResponse.model_validate(body)
    assert body["quotes"], body
    first = body["quotes"][0]
    assert first["memoryId"] == "m_hymn"
    assert first["authorId"] == env["rose"].id
    assert first["title"] == "Hymn books at St. Mary's"
    assert first["year"] == 1988
    assert "hymn books" in first["text"] and first["text"] in HYMN_TELLING
    assert "Grandma Rose" in body["answer"]
    assert first["text"] in body["answer"]
    assert body["answer"] == search_db.compose_answer(
        "Who carried the hymn books?",
        [schemas.AskQuote.model_validate(q) for q in body["quotes"]],
        {"rose": "Grandma Rose", "dad": "Dad", "alice_p": "Alice"},
    )


def test_ask_finds_the_other_memory(env):
    body = env["tc"].post(f"/families/{FAM_A}/ask", json={"query": "  the green sedan  "}).json()
    assert [q["memoryId"] for q in body["quotes"]] == ["m_sedan"]
    assert body["quotes"][0]["authorId"] == env["dad"].id
    assert body["answer"].startswith("Dad remembers (1972):")


def test_ask_with_no_match_returns_no_story(env):
    resp = env["tc"].post(f"/families/{FAM_A}/ask", json={"query": "xylophone submarine"})
    assert resp.status_code == 200
    assert resp.json() == {"answer": search_db.NO_STORY, "quotes": [], "best": None, "mode": "search"}


@pytest.mark.parametrize("query", ["", "   ", "\n\t "])
def test_blank_query_is_422(env, query):
    assert env["tc"].post(f"/families/{FAM_A}/ask", json={"query": query}).status_code == 422


@pytest.mark.parametrize("payload", [{}, {"query": None}, {"q": "hymn"}])
def test_malformed_ask_body_is_422(env, payload):
    assert env["tc"].post(f"/families/{FAM_A}/ask", json=payload).status_code == 422


def test_family_b_memories_never_appear_in_a_results(env):
    tc = env["tc"]
    for query in ("hymn books", "church choir", "red hymn books loudest", "Choir practice"):
        body = tc.post(f"/families/{FAM_A}/ask", json={"query": query}).json()
        assert all(q["memoryId"] != "m_b_choir" for q in body["quotes"]), (query, body)
        assert all(q["authorId"] != "bob_p" for q in body["quotes"])
        assert "loudest" not in body["answer"] and "Bob" not in body["answer"]
    # And the same query from Bob's side sees only family B.
    _signed_in(tc, env["bob"].id)
    body = tc.post(f"/families/{FAM_B}/ask", json={"query": "hymn books"}).json()
    assert [q["memoryId"] for q in body["quotes"]] == ["m_b_choir"]
    assert "Bob remembers" in body["answer"]


def test_ask_uses_an_installed_embedder_without_network(env):
    """With an embedder set, mongomock cannot run $vectorSearch, so keyword search still answers."""
    calls: list[str] = []
    search_db.set_embedder(lambda text: calls.append(text) or [0.1, 0.2, 0.3])
    body = env["tc"].post(f"/families/{FAM_A}/ask", json={"query": "hymn books"}).json()
    assert calls == ["hymn books"]
    assert body["quotes"][0]["memoryId"] == "m_hymn"


# --------------------------------------------------------------------- questions


def test_questions_are_the_callers_open_questions_only(env):
    resp = env["tc"].get(f"/families/{FAM_A}/questions")
    assert resp.status_code == 200
    body = resp.json()
    schemas.QuestionsResponse.model_validate(body)
    assert [q["id"] for q in body["questions"]] == [env["q_open"].id]
    q = body["questions"][0]
    assert q == {
        "id": env["q_open"].id, "familyId": FAM_A, "personId": "alice_p",
        "text": "What do you remember of the lake?", "memoryId": "m_sedan", "status": "open",
    }


def test_questions_newest_first(env):
    newer = memories_db.create_question(env["db"], FAM_A, person_id="alice_p", text="And the steps?")
    ids = [q["id"] for q in env["tc"].get(f"/families/{FAM_A}/questions").json()["questions"]]
    assert set(ids) == {newer.id, env["q_open"].id}
    assert ids[0] == newer.id


def test_questions_empty_when_the_caller_has_no_person(env):
    tc = _signed_in(env["tc"], env["carol"].id)
    resp = tc.get(f"/families/{FAM_A}/questions")
    assert resp.status_code == 200
    assert resp.json() == {"questions": []}


def test_signed_out_is_401(env):
    tc = env["tc"]
    tc.cookies.clear()
    assert tc.post(f"/families/{FAM_A}/ask", json={"query": "hymn"}).status_code == 401
    assert tc.get(f"/families/{FAM_A}/questions").status_code == 401


# --------------------------------------------------------------------- cross-family


def test_member_of_a_gets_404_for_family_b_on_all_six_routes(env):
    """props, sets, patch prop, upload, ask, questions: family B's fid is a 404 for Alice."""
    db = env["db"]
    props_db.insert_prop(db, FAM_B, schemas.Prop(id="b_lamp", name="brass lamp", size="hand",
                                                 art={"body": None}), prop_id="b_lamp")
    b_photo = media_db.put_media(db, FAM_B, PNG, content_type="image/png")
    tc = env["tc"]
    calls = {
        "GET props": lambda fid: tc.get(f"/families/{fid}/props"),
        "GET sets": lambda fid: tc.get(f"/families/{fid}/sets"),
        "PATCH prop": lambda fid: tc.patch(f"/families/{fid}/props/b_lamp",
                                           json={"name": "stolen", "photoId": b_photo}),
        "POST media": lambda fid: tc.post(f"/families/{fid}/media", files={"file": ("p.png", PNG, "image/png")}),
        "POST ask": lambda fid: tc.post(f"/families/{fid}/ask", json={"query": "hymn books"}),
        "GET questions": lambda fid: tc.get(f"/families/{fid}/questions"),
    }
    # Every family route the two routers expose is covered here (paths via OpenAPI, which
    # sees through included routers).
    family_routes = {
        (method.upper(), path)
        for path, ops in env["app"].openapi()["paths"].items()
        if path.startswith("/families/{fid}")
        for method in ops
    }
    assert family_routes == {
        ("GET", "/families/{fid}/props"),
        ("GET", "/families/{fid}/sets"),
        ("PATCH", "/families/{fid}/props/{prop_id}"),
        ("POST", "/families/{fid}/media"),
        ("POST", "/families/{fid}/ask"),
        ("GET", "/families/{fid}/questions"),
    }
    assert len(calls) == len(family_routes)

    for name, call in calls.items():
        resp = call(FAM_B)
        assert resp.status_code == 404, (name, resp.status_code, resp.text)
        assert resp.json() == {"detail": "Not found"}
        assert "hymn" not in resp.text and "Which hymn" not in resp.text
        # The same call on a family that does not exist is indistinguishable.
        missing = call("fam_missing")
        assert (missing.status_code, missing.json()) == (404, {"detail": "Not found"}), name

    assert props_db.get_prop(db, FAM_B, "b_lamp").name == "brass lamp"
    assert db[media_db.INLINE].count_documents({"metadata.familyId": FAM_B}) == 1
    # Bob, the B member, still gets 200s on his own family.
    _signed_in(tc, env["bob"].id)
    assert tc.get(f"/families/{FAM_B}/questions").json()["questions"][0]["id"] == env["q_bob"].id


def test_responses_never_contain_password_hash(env):
    tc = env["tc"]
    for resp in (
        tc.post(f"/families/{FAM_A}/ask", json={"query": "hymn books"}),
        tc.get(f"/families/{FAM_A}/questions"),
    ):
        assert resp.status_code == 200
        assert "passwordHash" not in resp.text and "argon2" not in resp.text


def test_routers_do_not_import_agent():
    """Read routes never import agent/ (ARCHITECTURE.md section 7). Checked in a fresh interpreter."""
    code = (
        "import sys, routes.ask, routes.props; "
        "print(sorted(m for m in sys.modules if m == 'agent' or m.startswith('agent.')))"
    )
    out = subprocess.run([sys.executable, "-c", code], cwd=API_DIR, capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "[]", out.stdout + out.stderr
