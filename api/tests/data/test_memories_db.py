"""db/memories.py: moments, tellings, scenes, book, threads, era, questions."""

from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import schemas

# mongomock becomes a dev dependency with db-foundation-auth; until then run with
# `uv run --with 'mongomock>=4.3.0' pytest tests/data/test_memories_db.py`.
mongomock = pytest.importorskip("mongomock")
from db import memories as m
from screenplay.schema import Screenplay, Timeline

REPO_DIR = Path(__file__).resolve().parents[3]
SAMPLE = json.loads((REPO_DIR / "web/public/samples/hymn_books.memory.json").read_text())

FA = "family_a"
FB = "family_b"
T0 = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)


@pytest.fixture
def mdb():
    return mongomock.MongoClient(tz_aware=True)["stitch_test"]


@pytest.fixture
def real_db():
    uri = os.environ.get("MONGODB_URI_TEST")
    if not uri:
        pytest.skip("MONGODB_URI_TEST not set")
    from pymongo import MongoClient

    client = MongoClient(uri, tz_aware=True, serverSelectionTimeoutMS=5000)
    name = f"stitch_test_{uuid.uuid4().hex[:8]}"
    try:
        yield client[name]
    finally:
        client.drop_database(name)
        client.close()


def add_people(db, family_id: str, *ids: str) -> None:
    for pid in ids:
        db["people"].replace_one(
            {"_id": pid},
            {"familyId": family_id, "userId": None, "name": pid.title(), "parentIds": [], "spouseIds": [],
             "character": {"color": "#3f6fb5", "build": "average"}},
            upsert=True,
        )


@pytest.fixture
def two_families(mdb):
    add_people(mdb, FA, "dad", "mom", "kid")
    add_people(mdb, FB, "b_grandpa", "b_grandma")
    return mdb


def telling(author: str, text: str = "It happened.", **kw) -> schemas.Telling:
    return schemas.Telling(author_id=author, text=text, **kw)


def strip_telling_times(dump: dict) -> dict:
    for t in dump["tellings"]:
        t.pop("createdAt", None)  # Model serializes by alias
    return dump


# ---------------------------------------------------------------------------------- era

@pytest.mark.parametrize(
    ("year", "era"),
    [(1939, "pre1940"), (1940, "1940s"), (1949, "1940s"), (1988, "1980s"), (2019, "2010s"), (2026, "2020s"), (-5, None)],
)
def test_era_for_year_boundaries(year, era):
    assert m.era_for_year(year) == era


# ------------------------------------------------------------------- hymn-book round trip

def roundtrip_hymn_books(db) -> schemas.Memory:
    sample = SAMPLE["memory"]
    add_people(db, FA, "dad", "mom")
    tellings = [schemas.Telling.model_validate(t) for t in sample["tellings"]]
    screenplay = Screenplay.model_validate(sample["screenplay"])
    timeline = Timeline.model_validate(sample["timeline"])

    created = m.create_memory(
        db, FA, title=sample["title"], year=sample["year"], participant_ids=sample["participantIds"],
        telling=tellings[0], now=T0,
    )
    assert created.version == 1
    assert created.era == "1980s"
    for t in tellings[1:]:
        assert m.add_telling(db, FA, created.id, t) is not None
    saved = m.save_scene(
        db, FA, created.id, screenplay=screenplay, timeline=timeline,
        prop_ids=sample["propIds"], set_ids=sample["setIds"],
    )
    assert saved is not None and saved.version == 1
    got = m.get_memory(db, FA, created.id)
    assert got is not None
    assert got == saved
    return got


def test_hymn_books_sample_round_trips(mdb):
    got = roundtrip_hymn_books(mdb)
    expected = schemas.Memory.model_validate(SAMPLE["memory"])

    assert got.family_id == FA
    assert all(t.created_at is not None for t in got.tellings)
    got_dump = strip_telling_times(got.model_dump(exclude={"id", "family_id"}))
    expected_dump = strip_telling_times(expected.model_dump(exclude={"id", "family_id"}))
    assert got_dump == expected_dump
    # The stored scene is camelCase JSON, exactly as the stage receives it.
    stored = mdb["memories"].find_one({"_id": got.id})
    assert stored["timeline"]["defaultNarrator"] == "dad"
    assert "era" not in stored


def test_scene_stored_without_nulls(mdb):
    got = roundtrip_hymn_books(mdb)
    stored = mdb["memories"].find_one({"_id": got.id})
    assert "audio" not in stored["timeline"]  # exclude_none
    assert isinstance(stored["createdAt"], datetime)


# -------------------------------------------------------------------------- create/read

def test_create_memory_validates_and_stores(two_families):
    db = two_families
    mem = m.create_memory(
        db, FA, title="Lake trip", year=1995, participant_ids=["dad", "kid", "dad"],
        telling=telling("dad"), prop_ids=["canoe", "canoe"], memory_id="lake", now=T0,
    )
    assert mem.id == "lake"
    assert mem.participant_ids == ["dad", "kid"]
    assert mem.prop_ids == ["canoe"]
    assert mem.era == "1990s"
    assert mem.tellings[0].created_at == T0
    stored = db["memories"].find_one({"_id": "lake"})
    assert stored["familyId"] == FA and stored["createdAt"] == T0 and stored["version"] == 1

    with pytest.raises(ValueError):
        m.create_memory(db, FA, title="Dup", year=1995, participant_ids=["dad"], memory_id="lake")
    with pytest.raises(ValueError):
        m.create_memory(db, FA, title="Ghost", year=1995, participant_ids=["nobody"])
    with pytest.raises(ValueError):
        m.create_memory(db, FA, title="", year=1995, participant_ids=["dad"])


def test_era_is_derived_not_trusted(two_families):
    db = two_families
    mem = m.create_memory(db, FA, title="Old", year=1952, participant_ids=["dad"])
    db["memories"].update_one({"_id": mem.id}, {"$set": {"era": "2020s"}})
    assert m.get_memory(db, FA, mem.id).era == "1950s"
    assert m.get_book(db, FA, "dad")[0].era == "1950s"


def test_get_memories_input_order(two_families):
    db = two_families
    a = m.create_memory(db, FA, title="A", year=2000, participant_ids=["dad"])
    b = m.create_memory(db, FA, title="B", year=1990, participant_ids=["mom"])
    got = m.get_memories(db, FA, [b.id, "missing", a.id, b.id])
    assert [x.id for x in got] == [b.id, a.id]
    assert m.get_memories(db, FA, []) == []


def test_get_book_order_and_shared_moment(two_families):
    db = two_families
    late = m.create_memory(db, FA, title="Late", year=2001, participant_ids=["dad"], now=T0)
    shared = m.create_memory(db, FA, title="Met", year=1988, participant_ids=["dad", "mom"], now=T0)
    same_year_later = m.create_memory(
        db, FA, title="Same year, added later", year=1988, participant_ids=["dad"], now=T0 + timedelta(hours=1)
    )
    early = m.create_memory(db, FA, title="Early", year=1970, participant_ids=["dad"], now=T0 + timedelta(days=1))
    m.create_memory(db, FA, title="Mom only", year=1980, participant_ids=["mom"])

    dad_book = m.get_book(db, FA, "dad")
    assert [x.id for x in dad_book] == [early.id, shared.id, same_year_later.id, late.id]
    mom_book = m.get_book(db, FA, "mom")
    assert shared.id in [x.id for x in mom_book]
    assert [x.year for x in mom_book] == [1980, 1988]
    assert m.get_book(db, FA, "kid") == []


# ------------------------------------------------------------------------------ tellings

def test_add_telling_replace_versus_append(two_families):
    db = two_families
    mem = m.create_memory(db, FA, title="Met", year=1988, participant_ids=["dad", "mom"],
                          telling=telling("dad", "first draft"), now=T0)

    appended = m.add_telling(db, FA, mem.id, telling("mom", "my side", question="What do you remember?"))
    assert [(t.author_id, t.text) for t in appended.tellings] == [("dad", "first draft"), ("mom", "my side")]
    assert appended.tellings[1].created_at is not None

    replaced = m.add_telling(db, FA, mem.id, telling("dad", "edited"))
    assert [(t.author_id, t.text) for t in replaced.tellings] == [("dad", "edited"), ("mom", "my side")]

    stamp = datetime(2020, 1, 1, tzinfo=UTC)
    kept = m.add_telling(db, FA, mem.id, telling("mom", "again", created_at=stamp))
    assert kept.tellings[1].created_at == stamp
    assert len(kept.tellings) == 2

    with pytest.raises(ValueError):
        m.add_telling(db, FA, mem.id, telling("stranger"))
    assert m.add_telling(db, FA, "missing", telling("dad")) is None


def test_link_participants_dedupes(two_families):
    db = two_families
    mem = m.create_memory(db, FA, title="Met", year=1988, participant_ids=["dad"])
    got = m.link_participants(db, FA, mem.id, ["mom", "dad", "mom"])
    assert got.participant_ids == ["dad", "mom"]
    got = m.link_participants(db, FA, mem.id, ["kid"])
    assert got.participant_ids == ["dad", "mom", "kid"]
    with pytest.raises(ValueError):
        m.link_participants(db, FA, mem.id, ["nobody"])
    assert m.link_participants(db, FA, "missing", ["dad"]) is None


# -------------------------------------------------------------------------------- scenes

def test_version_increments_on_later_saves(two_families):
    db = two_families
    sample = SAMPLE["memory"]
    sp = Screenplay.model_validate(sample["screenplay"])
    tl = Timeline.model_validate(sample["timeline"])
    mem = m.create_memory(db, FA, title="Met", year=1988, participant_ids=["dad", "mom"])

    first = m.save_scene(db, FA, mem.id, screenplay=sp, timeline=tl, prop_ids=["hymn_books"], set_ids=["steps"])
    assert first.version == 1
    second = m.save_scene(db, FA, mem.id, screenplay=sp, timeline=tl, prop_ids=["hymn_books"], set_ids=["steps"],
                          title="How I met your mother")
    assert second.version == 2
    assert second.title == "How I met your mother"
    third = m.save_scene(db, FA, mem.id, screenplay=sp, timeline=tl, prop_ids=[], set_ids=[])
    assert third.version == 3
    assert third.prop_ids == [] and third.set_ids == []
    assert m.get_memory(db, FA, mem.id).version == 3


def test_screenplay_only_save_keeps_version_one(two_families):
    db = two_families
    sp = Screenplay.model_validate(SAMPLE["memory"]["screenplay"])
    mem = m.create_memory(db, FA, title="Met", year=1988, participant_ids=["dad", "mom"])
    got = m.save_scene(db, FA, mem.id, screenplay=sp, timeline=None, prop_ids=[], set_ids=[])
    assert got.version == 1 and got.screenplay == sp and got.timeline is None
    assert m.save_scene(db, FA, "missing", screenplay=sp, timeline=None, prop_ids=[], set_ids=[]) is None


# ------------------------------------------------------------------------------- threads

def test_moment_summaries_order_and_fields(two_families):
    db = two_families
    later = m.create_memory(db, FA, title="Later", year=2010, participant_ids=["kid"], now=T0)
    met = m.create_memory(db, FA, title="Met", year=1988, participant_ids=["dad", "mom"],
                          telling=telling("dad"), now=T0)
    m.add_telling(db, FA, met.id, telling("mom"))
    m.add_telling(db, FA, met.id, telling("dad", "edited"))
    roundtrip = roundtrip_hymn_books(db)  # has a scene, also 1988 but added later
    m.set_embedding(db, FA, met.id, [0.1, 0.2])

    got = m.moment_summaries(db, FA)
    assert all(isinstance(s, schemas.MomentSummary) for s in got)
    assert [s.id for s in got] == [met.id, roundtrip.id, later.id]
    first = got[0]
    assert first.title == "Met" and first.year == 1988 and first.era == "1980s"
    assert first.participant_ids == ["dad", "mom"]
    assert first.telling_author_ids == ["dad", "mom"]
    assert got[2].telling_author_ids == [] and got[2].era == "2010s"
    dumped = json.dumps([s.model_dump(mode="json") for s in got])
    assert "screenplay" not in dumped and "timeline" not in dumped and "embedding" not in dumped
    assert m.moment_summaries(db, FB) == []


def test_embedding_never_returned(two_families):
    db = two_families
    mem = m.create_memory(db, FA, title="Met", year=1988, participant_ids=["dad", "mom"])
    assert m.set_embedding(db, FA, mem.id, [0.5, 0.25, 1]) is True
    assert db["memories"].find_one({"_id": mem.id})["embedding"] == [0.5, 0.25, 1.0]
    with pytest.raises(ValueError):
        m.set_embedding(db, FA, mem.id, [])

    results = [
        m.get_memory(db, FA, mem.id),
        *m.get_memories(db, FA, [mem.id]),
        *m.get_book(db, FA, "dad"),
        m.add_telling(db, FA, mem.id, telling("dad")),
        m.link_participants(db, FA, mem.id, ["kid"]),
        m.save_scene(db, FA, mem.id, screenplay=None, timeline=None, prop_ids=[], set_ids=[]),
    ]
    for r in results:
        assert isinstance(r, schemas.Memory)
        assert "embedding" not in r.model_dump_json(by_alias=True)


# ------------------------------------------------------------------- cross-family scoping

def test_cross_family_isolation(two_families):
    db = two_families
    sp = Screenplay.model_validate(SAMPLE["memory"]["screenplay"])
    b_mem = m.create_memory(db, FB, title="War years", year=1944, participant_ids=["b_grandpa"],
                            telling=telling("b_grandpa"))
    b_q = m.create_question(db, FB, person_id="b_grandma", text="What do you remember?", memory_id=b_mem.id)
    before = db["memories"].find_one({"_id": b_mem.id})

    # Reads with family A's id see nothing of family B.
    assert m.get_memory(db, FA, b_mem.id) is None
    assert m.get_memories(db, FA, [b_mem.id]) == []
    assert m.get_book(db, FA, "b_grandpa") == []
    assert m.moment_summaries(db, FA) == []
    assert m.open_questions(db, FA, "b_grandma") == []

    # Writes with family A's id do not touch family B.
    assert m.add_telling(db, FA, b_mem.id, telling("dad")) is None
    assert m.link_participants(db, FA, b_mem.id, ["dad"]) is None
    assert m.save_scene(db, FA, b_mem.id, screenplay=sp, timeline=None, prop_ids=[], set_ids=[]) is None
    assert m.set_embedding(db, FA, b_mem.id, [1.0]) is False
    assert m.set_question_status(db, FA, b_q.id, "dismissed") is None
    assert db["memories"].find_one({"_id": b_mem.id}) == before
    assert m.open_questions(db, FB, "b_grandma")[0].status == "open"

    # Family B people cannot be pulled into family A's records.
    a_mem = m.create_memory(db, FA, title="Met", year=1988, participant_ids=["dad"])
    with pytest.raises(ValueError):
        m.create_memory(db, FA, title="Mixed", year=1988, participant_ids=["dad", "b_grandpa"])
    with pytest.raises(ValueError):
        m.create_memory(db, FA, title="Mixed author", year=1988, participant_ids=["dad"],
                        telling=telling("b_grandpa"))
    with pytest.raises(ValueError):
        m.add_telling(db, FA, a_mem.id, telling("b_grandpa"))
    with pytest.raises(ValueError):
        m.link_participants(db, FA, a_mem.id, ["b_grandma"])
    with pytest.raises(ValueError):
        m.create_question(db, FA, person_id="b_grandma", text="Hi?")
    with pytest.raises(ValueError):
        m.create_question(db, FA, person_id="mom", text="Hi?", memory_id=b_mem.id)
    assert m.get_memory(db, FA, a_mem.id).participant_ids == ["dad"]
    assert m.get_memory(db, FA, a_mem.id).tellings == []


# ----------------------------------------------------------------------------- questions

def test_question_lifecycle(two_families):
    db = two_families
    mem = m.create_memory(db, FA, title="Met", year=1988, participant_ids=["dad", "mom"])
    q1 = m.create_question(db, FA, person_id="mom", text="Dad says you met on the steps. What do you remember?",
                           memory_id=mem.id)
    assert q1.status == "open" and q1.memory_id == mem.id and q1.family_id == FA
    q2 = m.create_question(db, FA, person_id="mom", text="Tell me about the wedding.")
    db["questions"].update_one({"_id": q2.id}, {"$set": {"createdAt": datetime.now(UTC) + timedelta(minutes=1)}})
    m.create_question(db, FA, person_id="dad", text="Your first car?")

    assert [q.id for q in m.open_questions(db, FA, "mom")] == [q2.id, q1.id]

    answered = m.set_question_status(db, FA, q1.id, "answered")
    assert answered.status == "answered" and answered.id == q1.id
    assert [q.id for q in m.open_questions(db, FA, "mom")] == [q2.id]
    assert m.set_question_status(db, FA, q2.id, "dismissed").status == "dismissed"
    assert m.open_questions(db, FA, "mom") == []
    assert m.set_question_status(db, FA, "missing", "answered") is None

    with pytest.raises(ValueError):
        m.set_question_status(db, FA, q1.id, "bogus")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        m.create_question(db, FA, person_id="mom", text="  ")
    with pytest.raises(ValueError):
        m.create_question(db, FA, person_id="nobody", text="Hi?")
    stored = db["questions"].find_one({"_id": q1.id})
    assert set(stored) == {"_id", "familyId", "personId", "text", "memoryId", "status", "createdAt"}


def _statuses(db) -> dict[str, str]:
    return {doc["_id"]: doc["status"] for doc in db["questions"].find()}


def test_add_telling_closes_the_authors_question_for_that_memory(two_families):
    db = two_families
    m1 = m.create_memory(db, FA, title="Met", year=1988, participant_ids=["dad", "mom"], memory_id="m1")
    b_mem = m.create_memory(db, FB, title="War years", year=1944, participant_ids=["b_grandma"])
    text = "Dad says you met on the steps. What do you remember?"
    mom_q = m.create_question(db, FA, person_id="mom", text=text, memory_id=m1.id)
    mom_other = m.create_question(db, FA, person_id="mom", text="Tell me about the wedding.")
    dad_q = m.create_question(db, FA, person_id="dad", text=text, memory_id=m1.id)
    b_q = m.create_question(db, FB, person_id="b_grandma", text=text, memory_id=b_mem.id)

    got = m.add_telling(db, FA, m1.id, telling("mom", "My side.", question=text))
    assert got is not None

    statuses = _statuses(db)
    assert statuses[mom_q.id] == "answered"
    assert [q.id for q in m.open_questions(db, FA, "mom")] == [mom_other.id]
    assert statuses[mom_other.id] == "open"
    assert statuses[dad_q.id] == "open"  # someone else's question for the same memory
    assert statuses[b_q.id] == "open"  # another family's question with the same text
    assert [q.id for q in m.open_questions(db, FB, "b_grandma")] == [b_q.id]

    # Editing your own telling again closes nothing more and does not reopen anything.
    assert m.add_telling(db, FA, m1.id, telling("mom", "Edited.")) is not None
    assert _statuses(db) == statuses


def test_create_memory_closes_the_question_it_answers_by_text(two_families):
    db = two_families
    q = m.create_question(db, FA, person_id="mom", text="Q")
    other = m.create_question(db, FA, person_id="mom", text="A different question?")
    dad_q = m.create_question(db, FA, person_id="dad", text="Q")

    m.create_memory(db, FA, title="Wedding", year=1990, participant_ids=["mom"],
                    telling=telling("mom", "We married in June.", question="  Q  "))

    statuses = _statuses(db)
    assert statuses[q.id] == "answered"
    assert statuses[other.id] == "open"
    assert statuses[dad_q.id] == "open"
    assert [x.id for x in m.open_questions(db, FA, "mom")] == [other.id]


def test_blank_question_does_not_match_by_text(two_families):
    db = two_families
    q = m.create_question(db, FA, person_id="mom", text="Q")
    m.create_memory(db, FA, title="One", year=1990, participant_ids=["mom"], telling=telling("mom", question="   "))
    m.create_memory(db, FA, title="Two", year=1991, participant_ids=["mom"], telling=telling("mom"))
    m.create_memory(db, FA, title="Three", year=1992, participant_ids=["mom"])
    assert _statuses(db)[q.id] == "open"


def test_close_answered_questions_returns_the_count(two_families):
    db = two_families
    m1 = m.create_memory(db, FA, title="Met", year=1988, participant_ids=["mom"], memory_id="m1")
    m.create_question(db, FA, person_id="mom", text="Q1", memory_id=m1.id)
    m.create_question(db, FA, person_id="mom", text="Q2")
    m.create_question(db, FA, person_id="mom", text="Q3")
    closed = m._close_answered_questions(db, FA, m1.id, telling("mom", question="Q2"))
    assert closed == 2
    assert [q.text for q in m.open_questions(db, FA, "mom")] == ["Q3"]
    assert m._close_answered_questions(db, FA, m1.id, telling("mom", question="Q2")) == 0


def test_identical_question_bound_to_another_memory_stays_open(two_families):
    """Two moments titled 'Christmas' get the same linker text; answering one closes only it."""
    db = two_families
    xmas1 = m.create_memory(db, FA, title="Christmas", year=1970, participant_ids=["dad", "mom"], memory_id="x1")
    xmas2 = m.create_memory(db, FA, title="Christmas", year=1995, participant_ids=["dad", "mom"], memory_id="x2")
    text = 'Dad told the story of "Christmas". What do you remember?'
    q1 = m.create_question(db, FA, person_id="mom", text=text, memory_id=xmas1.id)
    q2 = m.create_question(db, FA, person_id="mom", text=text, memory_id=xmas2.id)

    assert m.add_telling(db, FA, xmas1.id, telling("mom", "The tree fell over.", question=text)) is not None

    statuses = _statuses(db)
    assert statuses[q1.id] == "answered"
    assert statuses[q2.id] == "open"
    assert [q.id for q in m.open_questions(db, FA, "mom")] == [q2.id]

    # Answering the second Christmas closes the second question.
    assert m.add_telling(db, FA, xmas2.id, telling("mom", "Snow at last.", question=text)) is not None
    assert _statuses(db)[q2.id] == "answered"
    assert m.open_questions(db, FA, "mom") == []


def test_free_question_with_the_same_text_is_closed_by_the_telling(two_families):
    db = two_families
    mem = m.create_memory(db, FA, title="Christmas", year=1970, participant_ids=["mom"], memory_id="x1")
    other = m.create_memory(db, FA, title="Christmas", year=1995, participant_ids=["mom"], memory_id="x2")
    text = "What do you remember about Christmas?"
    free = m.create_question(db, FA, person_id="mom", text=text)
    bound_elsewhere = m.create_question(db, FA, person_id="mom", text=text, memory_id=other.id)
    # A question document with no memoryId field at all counts as free too.
    db["questions"].insert_one({"_id": "legacy", "familyId": FA, "personId": "mom", "text": text,
                                "status": "open", "createdAt": T0})
    assert free.memory_id is None

    assert m._close_answered_questions(db, FA, mem.id, telling("mom", question=f"  {text} ")) == 2
    statuses = _statuses(db)
    assert statuses[free.id] == "answered"
    assert statuses["legacy"] == "answered"
    assert statuses[bound_elsewhere.id] == "open"


def test_add_telling_on_unknown_memory_closes_nothing(two_families):
    db = two_families
    b_mem = m.create_memory(db, FB, title="War years", year=1944, participant_ids=["b_grandma"])
    q = m.create_question(db, FA, person_id="mom", text="Q")
    q_text_only = m.create_question(db, FA, person_id="mom", text="What about the war?")

    assert m.add_telling(db, FA, "missing", telling("mom", question="Q")) is None
    assert m.add_telling(db, FA, b_mem.id, telling("mom", question="What about the war?")) is None
    statuses = _statuses(db)
    assert statuses[q.id] == "open" and statuses[q_text_only.id] == "open"


# ---------------------------------------------------------------------------- embeddings

@pytest.fixture
def embedder():
    """Install an embedder for one test; always removed afterwards."""
    from db import search

    def install(fn):
        search.set_embedder(fn)

    try:
        yield install
    finally:
        search.set_embedder(None)


def test_create_memory_stores_embedding(two_families, embedder):
    db = two_families
    embedder(lambda text: [1.0, 2.0])
    mem = m.create_memory(db, FA, title="Met", year=1988, participant_ids=["dad"], telling=telling("dad"))
    stored = db["memories"].find_one({"_id": mem.id})
    assert stored["embedding"] == [1.0, 2.0]
    assert all(isinstance(x, float) for x in stored["embedding"])
    assert "embedding" not in mem.model_dump_json(by_alias=True)
    assert "embedding" not in m.get_memory(db, FA, mem.id).model_dump_json(by_alias=True)


def test_add_telling_reembeds_with_the_new_text(two_families, embedder):
    db = two_families
    seen: list[str] = []

    def record(text: str) -> list[int]:
        seen.append(text)
        return [len(seen), 0]

    embedder(record)
    mem = m.create_memory(db, FA, title="Lake trip", year=1995, participant_ids=["dad", "mom"],
                          telling=telling("dad", "We rowed out at dawn."))
    assert seen == ["Lake trip\n\nWe rowed out at dawn."]

    m.add_telling(db, FA, mem.id, telling("mom", "The canoe tipped over."))
    assert seen[-1] == "Lake trip\n\nWe rowed out at dawn.\n\nThe canoe tipped over."
    assert db["memories"].find_one({"_id": mem.id})["embedding"] == [2.0, 0.0]

    # Replacing your own telling re-embeds with the edited text, not the old one.
    m.add_telling(db, FA, mem.id, telling("dad", "We rowed out at noon."))
    assert seen[-1] == "Lake trip\n\nWe rowed out at noon.\n\nThe canoe tipped over."
    assert "dawn" not in seen[-1]

    # A failed write embeds nothing.
    calls = len(seen)
    assert m.add_telling(db, FA, "missing", telling("dad")) is None
    assert len(seen) == calls


def test_failing_embedder_never_breaks_writes(two_families, embedder):
    db = two_families

    def boom(text: str) -> list[float]:
        raise RuntimeError("embedding service down")

    embedder(boom)
    mem = m.create_memory(db, FA, title="Met", year=1988, participant_ids=["dad", "mom"], telling=telling("dad"))
    got = m.add_telling(db, FA, mem.id, telling("mom"))
    assert got is not None and len(got.tellings) == 2
    assert "embedding" not in db["memories"].find_one({"_id": mem.id})


def test_refresh_embedding_never_raises(two_families, embedder, monkeypatch):
    db = two_families
    embedder(lambda text: [1.0])
    mem = m.create_memory(db, FA, title="Met", year=1988, participant_ids=["dad"])

    def broken_embed(text: str):
        raise RuntimeError("unexpected")

    monkeypatch.setattr(m.search_db, "embed", broken_embed)
    m._refresh_embedding(db, FA, mem.id)  # does not raise
    assert m.add_telling(db, FA, mem.id, telling("dad")) is not None


def test_no_embedder_writes_no_embedding(two_families):
    from db import search

    search.set_embedder(None)
    db = two_families
    mem = m.create_memory(db, FA, title="Met", year=1988, participant_ids=["dad", "mom"], telling=telling("dad"))
    m.add_telling(db, FA, mem.id, telling("mom"))
    assert "embedding" not in db["memories"].find_one({"_id": mem.id})


def test_save_scene_does_not_reembed(two_families, embedder):
    db = two_families
    seen: list[str] = []
    embedder(lambda text: seen.append(text) or [1.0])
    mem = m.create_memory(db, FA, title="Met", year=1988, participant_ids=["dad"], telling=telling("dad"))
    calls = len(seen)
    sp = Screenplay.model_validate(SAMPLE["memory"]["screenplay"])
    m.save_scene(db, FA, mem.id, screenplay=sp, timeline=None, prop_ids=[], set_ids=[])
    assert len(seen) == calls


# ----------------------------------------------------------------------------- real Mongo

def test_real_mongodb_round_trip(real_db):
    got = roundtrip_hymn_books(real_db)
    assert m.get_book(real_db, FA, "mom")[0].id == got.id
    assert [s.id for s in m.moment_summaries(real_db, FA)] == [got.id]
    assert m.get_memory(real_db, FB, got.id) is None
    q = m.create_question(real_db, FA, person_id="mom", text="What do you remember?", memory_id=got.id)
    assert [x.id for x in m.open_questions(real_db, FA, "mom")] == [q.id]
    assert m.set_question_status(real_db, FA, q.id, "answered").status == "answered"
