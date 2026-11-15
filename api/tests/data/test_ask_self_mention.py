"""/ask: the asker's own presence ("I", "me", "my <thing>") is not evidence on its own.

The asker is in many of the family's memories, so a self mention still ranks their pages
first but does not make a match confident, nor keep it over a model's null pick, without
real keyword support or another person named in the question. Runs on mongomock with the
seeded demo family; the model step is always a fake.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import asking
from db import client as db_client
from db import people as people_db
from db import search
from db.client import get_db
from routes import ask as ask_routes
from routes import auth as auth_routes
from seed.demo import DEMO_FAMILY_ID, DEMO_PASSWORD, seed_demo

HYMN = "sample_hymn_books"
ROSA_WEDDING = "demo_rosa_joseph_wedding"
BIKE = "demo_sams_first_bike"
SAM_WEDDING = "demo_sam_wedding_2019"
GRADUATION = "demo_lily_graduation"
ROAD_TRIP = "demo_green_sedan_trip"
DEMO_ASK = f"/families/{DEMO_FAMILY_ID}/ask"
NULL_REPLY = {"memory_id": None, "confident": False, "answer": "Nothing relates."}


@pytest.fixture(autouse=True)
def _no_model_or_embedder():
    asking.set_answerer(None)
    search.set_embedder(None)
    yield
    asking.set_answerer(None)
    search.set_embedder(None)


@pytest.fixture
def demo_db(mdb):
    db_client.ensure_indexes(mdb)
    seed_demo(mdb)
    return mdb


def _rank(db, asker: str | None, query: str) -> search.Ranking:
    return search.rank_memories(db, DEMO_FAMILY_ID, query, people=people_db.list_people(db, DEMO_FAMILY_ID),
                                asker_id=asker)


@pytest.mark.parametrize(("asker", "query", "best", "confident", "keep"), [
    # Self only, nothing in the question matches: Sam is in many pages, so no story.
    ("sam", "what did I eat at christmas", BIKE, False, False),
    ("sam", "my favorite toy", BIKE, False, False),
    # Self only, "first" matches a title in full but "job" matches nothing: not confident.
    # Kept over a null pick only by the full keyword match (keyword >= 1.0), not by David.
    ("dad", "what was my first job", BIKE, False, True),
    # Self only with real keyword support: confident as before.
    ("sam", "my wedding", SAM_WEDDING, True, True),
    ("lily", "my graduation", GRADUATION, True, True),
    ("sam", "my first bike", BIKE, True, True),
    # A relative named next to the asker: confident as before.
    ("dad", "how did I meet my wife", HYMN, True, True),
    # Named relatives keep their page over a null pick.
    ("mom", "where did grandpa and grandma meet", ROSA_WEDDING, False, True),
    ("sam", "where did grandpa and grandma meet", ROSA_WEDDING, False, True),
    ("dad", "where did grandpa and grandma meet", ROSA_WEDDING, False, True),
    # "Sam" written in the question is a real mention, even when Sam asks.
    ("dad", "who taught Sam to ride a bike", BIKE, False, True),
    ("sam", "who taught Sam to ride a bike", BIKE, False, True),
    # Loose and nobody named.
    ("mom", "what do we eat at christmas", ROAD_TRIP, False, False),
    ("sam", "what do we eat at christmas", ROAD_TRIP, False, False),
])
def test_the_askers_own_presence_is_not_evidence(demo_db, asker, query, best, confident, keep):
    ranking = _rank(demo_db, asker, query)
    assert ranking.candidates[0].memory_id == best
    assert ranking.confident() is confident
    assert ranking.outweighs_a_null_pick() is keep


def test_person_other_leaves_out_only_the_self_mention(demo_db):
    first = _rank(demo_db, "sam", "what did I eat at christmas").candidates[0]
    assert first.person > 0 and first.person_other == 0 and first.named_person == 0  # still ranks Sam's pages
    first = _rank(demo_db, "dad", "how did I meet my wife").candidates[0]
    assert 0 < first.person_other < first.person
    first = _rank(demo_db, "sam", "who taught Sam to ride a bike").candidates[0]
    assert first.person_other == first.person > 0  # "Sam" by name, not as "I"
    first = _rank(demo_db, "mom", "what do we eat at christmas").candidates[0]
    assert first.person == first.person_other == 0


def _candidate(person: float, coverage: float, person_other: float | None = None) -> search.Candidate:
    return search.Candidate(memory_id="m0", title="T", year=2000, participant_ids=["p"], teller_ids=[],
                            score=1.0, keyword=0.5, person=person, coverage=coverage, person_other=person_other)


def test_named_person_defaults_to_person_for_candidates_built_elsewhere():
    assert _candidate(0.5, 0.4).named_person == 0.5
    assert search.Ranking([_candidate(0.5, 0.4)], []).confident()  # as before: people named, no coverage veto
    self_only = search.Ranking([_candidate(0.5, 0.4, person_other=0.0)], [])
    assert not self_only.confident() and not self_only.outweighs_a_null_pick()
    assert search.Ranking([_candidate(0.5, 0.5, person_other=0.0)], []).confident()  # half the query covered
    assert search.Ranking([_candidate(0.5, 0.4, person_other=0.2)], []).confident()


def _client_as(db, email: str) -> TestClient:
    app = FastAPI()
    app.include_router(ask_routes.router)
    app.include_router(auth_routes.router)
    app.dependency_overrides[get_db] = lambda: db
    tc = TestClient(app)
    assert tc.post("/auth/login", json={"email": email, "password": DEMO_PASSWORD}).status_code == 200
    return tc


@pytest.mark.parametrize("query", ["what did I eat at christmas", "my favorite toy"])
def test_route_null_pick_over_only_the_asker_is_no_story(demo_db, query):
    tc = _client_as(demo_db, "sam@demo.stitch")
    asking.set_answerer(lambda ctx: NULL_REPLY)
    body = tc.post(DEMO_ASK, json={"query": query}).json()
    assert body == {"answer": search.NO_STORY, "quotes": [], "best": None, "mode": "model"}


def test_route_self_only_partial_match_is_not_confident(demo_db):
    tc = _client_as(demo_db, "david@demo.stitch")
    body = tc.post(DEMO_ASK, json={"query": "what was my first job"}).json()
    assert body["mode"] == "search" and body["best"]["memoryId"] == BIKE
    assert body["best"]["confident"] is False
    assert body["answer"].startswith("The closest page is “Sam's first bike”")


@pytest.mark.parametrize(("email", "query"), [
    ("sam@demo.stitch", "who taught Sam to ride a bike"),
    ("david@demo.stitch", "who taught Sam to ride a bike"),
    ("sam@demo.stitch", "where did grandpa and grandma meet"),
    ("david@demo.stitch", "where did grandpa and grandma meet"),
])
def test_route_null_pick_keeps_a_named_match(demo_db, email, query):
    tc = _client_as(demo_db, email)
    deterministic = tc.post(DEMO_ASK, json={"query": query}).json()
    assert deterministic["best"] is not None
    asking.set_answerer(lambda ctx: NULL_REPLY)
    assert tc.post(DEMO_ASK, json={"query": query}).json() == deterministic


def test_route_null_pick_on_we_eat_at_christmas_is_still_no_story(demo_db):
    tc = _client_as(demo_db, "sam@demo.stitch")
    asking.set_answerer(lambda ctx: NULL_REPLY)
    body = tc.post(DEMO_ASK, json={"query": "what do we eat at christmas"}).json()
    assert body == {"answer": search.NO_STORY, "quotes": [], "best": None, "mode": "model"}
