"""db/search.py: vector pipeline, keyword fallback, quotes and the composed answer."""

from __future__ import annotations

import json
import math
import os
import uuid
from pathlib import Path

import pytest

import schemas
from db import search
from db.search import (
    NO_STORY,
    VECTOR_INDEX,
    SearchHit,
    ask_quotes,
    best_sentences,
    compose_answer,
    embed,
    search_memories,
    set_embedder,
    vector_pipeline,
)

SAMPLE = Path(__file__).resolve().parents[3] / "web" / "public" / "samples" / "hymn_books.memory.json"
FAMILY_A = "family_a"
FAMILY_B = "family_b"
HYMN_QUERY = "hymn books church"


@pytest.fixture(autouse=True)
def no_embedder():
    set_embedder(None)
    yield
    set_embedder(None)


@pytest.fixture
def mdb():
    mongomock = pytest.importorskip("mongomock")
    return mongomock.MongoClient(tz_aware=True)["stitch_test"]


@pytest.fixture
def real_db():
    uri = os.environ.get("MONGODB_URI_TEST")
    if not uri:
        pytest.skip("MONGODB_URI_TEST is not set")
    from pymongo import MongoClient

    client = MongoClient(uri, serverSelectionTimeoutMS=3000, tz_aware=True)
    name = f"stitch_test_search_{uuid.uuid4().hex[:8]}"
    try:
        client.admin.command("ping")
    except Exception as exc:  # noqa: BLE001
        client.close()
        pytest.skip(f"MongoDB at MONGODB_URI_TEST is unreachable: {exc}")
    try:
        yield client[name]
    finally:
        client.drop_database(name)
        client.close()


def hymn_doc(family_id: str = FAMILY_A) -> dict:
    memory = json.loads(SAMPLE.read_text())["memory"]
    doc = {k: v for k, v in memory.items() if k != "id"}
    doc["_id"] = memory["id"]
    doc["familyId"] = family_id
    doc["embedding"] = [0.1, 0.2, 0.3]
    return doc


def other_docs(family_id: str = FAMILY_A) -> list[dict]:
    return [
        {
            "_id": "church_picnic",
            "familyId": family_id,
            "title": "The church picnic",
            "year": 1975,
            "participantIds": ["grandpa"],
            "tellings": [
                {"authorId": "grandpa", "question": "What do you remember about summers?",
                 "text": "Every July the church held a picnic by the lake. We ate cold chicken. "
                         "Nobody wanted to go home."},
            ],
        },
        {
            "_id": "green_sedan",
            "familyId": family_id,
            "title": "The green sedan",
            "year": 1985,
            "participantIds": ["dad"],
            "tellings": [
                {"authorId": "dad", "question": "What was your first car?",
                 "text": "It was a green sedan with a broken radio. I drove it to every gig."},
            ],
        },
        {
            "_id": "first_trumpet",
            "familyId": family_id,
            "title": "The old trumpet case",
            "year": 1990,
            "participantIds": ["mom"],
            "tellings": [
                {"authorId": "mom", "question": "When did you start playing music?",
                 "text": "My father gave me his trumpet case. Inside were old sheet music books."},
            ],
        },
    ]


@pytest.fixture
def seeded(mdb):
    mdb.memories.insert_many([hymn_doc(), *other_docs()])
    return mdb


# ------------------------------------------------------------------ embeddings

def test_embed_is_none_without_embedder():
    assert embed("hymn books") is None


def test_embed_uses_embedder():
    set_embedder(lambda text: [1, 2, 3])
    assert embed("hymn books") == [1.0, 2.0, 3.0]


def test_failing_embedder_gives_none(caplog):
    def boom(text: str) -> list[float]:
        raise RuntimeError("no network")

    set_embedder(boom)
    assert embed("hymn books") is None
    assert any("embedder failed" in r.message for r in caplog.records)


# ------------------------------------------------------------------ pipeline

def test_vector_pipeline_is_family_filtered():
    pipeline = vector_pipeline(FAMILY_A, [0.5, 0.25], 4)
    stage = pipeline[0]["$vectorSearch"]
    assert stage == {
        "index": VECTOR_INDEX,
        "path": "embedding",
        "queryVector": [0.5, 0.25],
        "numCandidates": 80,
        "limit": 4,
        "filter": {"familyId": {"$eq": FAMILY_A}},
    }
    assert pipeline[1] == {"$match": {"familyId": FAMILY_A}}
    project = pipeline[2]["$project"]
    assert project["score"] == {"$meta": "vectorSearchScore"}
    assert project["screenplay"] == 0 and project["timeline"] == 0 and project["embedding"] == 0


# ------------------------------------------------------------------ keyword search

def test_hymn_query_ranks_hymn_memory_first(seeded):
    hits = search_memories(seeded, FAMILY_A, HYMN_QUERY)
    assert hits, "expected keyword hits"
    assert all(isinstance(h, SearchHit) for h in hits)
    assert hits[0].memory_id == "sample_hymn_books"
    assert hits[0].title == "How I met your mother" and hits[0].year == 1988
    # The church picnic shares only "church"; the sedan shares nothing.
    ids = [h.memory_id for h in hits]
    assert "church_picnic" in ids and "green_sedan" not in ids
    assert hits[0].score > hits[1].score
    assert all(0 < h.score <= 1 for h in hits)


def test_hits_carry_quotes_from_each_telling(seeded):
    hit = search_memories(seeded, FAMILY_A, HYMN_QUERY)[0]
    assert [q.author_id for q in hit.quotes] == ["dad", "mom"]
    assert hit.quotes[0].text == "And then she dropped every single hymn book."
    assert hit.quotes[1].text == "I was carrying far too many hymn books."
    assert all(q.memory_id == "sample_hymn_books" and q.year == 1988 for q in hit.quotes)


def test_title_weighted_higher(mdb):
    mdb.memories.insert_many([
        {"_id": "told_in_text", "familyId": FAMILY_A, "title": "Sunday dinners", "year": 1950,
         "tellings": [{"authorId": "p", "text": "We always ate in the kitchen."}]},
        {"_id": "in_title", "familyId": FAMILY_A, "title": "Grandma's kitchen", "year": 1990,
         "tellings": [{"authorId": "p", "text": "It smelled of bread."}]},
    ])
    hits = search_memories(mdb, FAMILY_A, "kitchen")
    # The earlier year would win a tie, so the title weight is what puts in_title first.
    assert [h.memory_id for h in hits] == ["in_title", "told_in_text"]
    assert hits[0].score == pytest.approx(2 * hits[1].score)


def test_single_word_queries(seeded):
    assert [h.memory_id for h in search_memories(seeded, FAMILY_A, "trumpet")] == ["first_trumpet"]
    assert search_memories(seeded, FAMILY_A, "green sedan")[0].memory_id == "green_sedan"


def test_plural_stripping_and_stopwords(seeded):
    assert search_memories(seeded, FAMILY_A, "the hymns")[0].memory_id == "sample_hymn_books"
    assert search_memories(seeded, FAMILY_A, "picnics")[0].memory_id == "church_picnic"
    assert search_memories(seeded, FAMILY_A, "the and of was") == []
    assert search_memories(seeded, FAMILY_A, "") == []


def test_ties_go_to_earlier_year(mdb):
    for year, mid in [(2001, "late"), (1950, "early"), (1980, "middle")]:
        mdb.memories.insert_one({
            "_id": mid, "familyId": FAMILY_A, "title": "Snow day", "year": year,
            "tellings": [{"authorId": "p", "text": "It snowed."}],
        })
    assert [h.memory_id for h in search_memories(mdb, FAMILY_A, "snow")] == ["early", "middle", "late"]


def test_limit(seeded):
    assert len(search_memories(seeded, FAMILY_A, "church hymn books", limit=1)) == 1
    assert search_memories(seeded, FAMILY_A, HYMN_QUERY, limit=0) == []


def test_never_returns_another_familys_memories(mdb):
    mdb.memories.insert_many([hymn_doc(FAMILY_B), *other_docs(FAMILY_B)])
    mdb.memories.insert_one({
        "_id": "a_only", "familyId": FAMILY_A, "title": "Grandma's kitchen", "year": 1960,
        "tellings": [{"authorId": "grandma", "text": "The kitchen smelled of bread."}],
    })
    assert search_memories(mdb, FAMILY_A, HYMN_QUERY) == []
    assert ask_quotes(mdb, FAMILY_A, HYMN_QUERY) == []
    assert [h.memory_id for h in search_memories(mdb, FAMILY_A, "kitchen")] == ["a_only"]
    assert search_memories(mdb, FAMILY_B, HYMN_QUERY)[0].memory_id == "sample_hymn_books"
    # A vector does not open a door either.
    assert search_memories(mdb, FAMILY_A, HYMN_QUERY, vector=[0.1, 0.2, 0.3]) == []


def test_embedder_plus_mongomock_falls_back(seeded):
    calls = []

    def fake_embedder(text: str) -> list[float]:
        calls.append(text)
        return [0.1, 0.2, 0.3]

    set_embedder(fake_embedder)
    hits = search_memories(seeded, FAMILY_A, HYMN_QUERY)
    assert calls == [HYMN_QUERY]
    assert hits[0].memory_id == "sample_hymn_books"


def test_explicit_vector_on_mongomock_falls_back(seeded):
    hits = search_memories(seeded, FAMILY_A, HYMN_QUERY, vector=[0.3, 0.2, 0.1])
    assert hits[0].memory_id == "sample_hymn_books"


def test_failing_embedder_still_searches(seeded):
    set_embedder(lambda text: 1 / 0)
    assert search_memories(seeded, FAMILY_A, HYMN_QUERY)[0].memory_id == "sample_hymn_books"


class _FakeCollection:
    def __init__(self, docs, error=None):
        self.docs, self.error, self.pipelines = docs, error, []

    def aggregate(self, pipeline):
        self.pipelines.append(pipeline)
        if self.error:
            raise self.error
        return iter(self.docs)

    def find(self, *args, **kwargs):
        return iter([])


class _FakeDb(dict):
    def __getitem__(self, name):
        return dict.get(self, name)


def test_vector_path_used_when_available():
    docs = [
        {"_id": "m2", "familyId": FAMILY_A, "title": "Later", "year": 1999, "score": 0.8,
         "tellings": [{"authorId": "a", "text": "We sang hymns."}]},
        {"_id": "m1", "familyId": FAMILY_A, "title": "Earlier", "year": 1970, "score": 0.8,
         "tellings": [{"authorId": "b", "text": "Church bells. Hymn books everywhere."}]},
        {"_id": "m3", "familyId": FAMILY_A, "title": "Best", "year": 2005, "score": 0.95,
         "tellings": []},
        {"_id": "leak", "familyId": FAMILY_B, "title": "Leak", "year": 1900, "score": 0.99,
         "tellings": []},
    ]
    coll = _FakeCollection(docs)
    db = _FakeDb(memories=coll)
    hits = search_memories(db, FAMILY_A, HYMN_QUERY, vector=[0.1], limit=3)
    assert [h.memory_id for h in hits] == ["m3", "m1", "m2"]
    assert hits[0].score == pytest.approx(0.95)
    assert hits[1].quotes[0].text == "Hymn books everywhere."
    assert coll.pipelines[0][0]["$vectorSearch"]["filter"] == {"familyId": {"$eq": FAMILY_A}}


def test_operation_failure_falls_back_to_keywords():
    from pymongo.errors import OperationFailure

    coll = _FakeCollection([], error=OperationFailure("$vectorSearch is not allowed"))
    db = _FakeDb(memories=coll)
    assert search_memories(db, FAMILY_A, HYMN_QUERY, vector=[0.1]) == []
    assert len(coll.pipelines) == 1


def test_empty_vector_result_falls_back_to_keywords(seeded, monkeypatch):
    monkeypatch.setattr(search, "_vector_search", lambda *a, **k: [])
    assert search_memories(seeded, FAMILY_A, HYMN_QUERY, vector=[0.1])[0].memory_id == "sample_hymn_books"


# ------------------------------------------------------------------ keyword + vector merge

SKATEBOARD = "demo_skateboard"
HYMN_QUESTION = "who dropped the hymn books?"
UNRELATED = "zeppelin quasar xylophone"


def _skate_embedder(text: str) -> list[float]:
    """Anything about skating points one way, everything else the other (cosine 0)."""
    return [1.0, 0.0] if "skat" in text.lower() or "kickflip" in text.lower() else [0.0, 1.0]


def _atlas_score(a: list[float], b: list[float]) -> float:
    """Atlas cosine vectorSearchScore: (1 + cos) / 2."""
    dot = sum(x * y for x, y in zip(a, b))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return (1 + (dot / norm if norm else 0.0)) / 2


@pytest.fixture
def atlas(monkeypatch):
    """Stand-in for $vectorSearch: like Atlas it sees only the family's docs with an `embedding`.

    Returns the list of result lists it handed back, one per call.
    """
    returned: list[list[dict]] = []

    def fake(db, family_id, vector, limit):
        docs = db[search.MEMORIES].find({"familyId": family_id, "embedding": {"$exists": True}})
        out = [
            {**{k: v for k, v in d.items() if k not in ("embedding", "screenplay", "timeline")},
             "score": _atlas_score(vector, d["embedding"])}
            for d in docs
        ]
        out.sort(key=lambda d: -d["score"])
        returned.append(out[:limit])
        return out[:limit]

    monkeypatch.setattr(search, "_vector_search", fake)
    return returned


@pytest.fixture
def demo(mdb):
    """The seeded demo family (no embedder, so no vectors) plus one embedded skateboard story."""
    from db import memories as memories_db
    from seed.demo import DEMO_FAMILY_ID, seed_demo

    seed_demo(mdb)
    set_embedder(_skate_embedder)
    memories_db.create_memory(
        mdb, DEMO_FAMILY_ID, title="Sam's first skateboard", year=2015, participant_ids=["sam"],
        telling=schemas.Telling(author_id="sam", question="What did you do that summer?",
                                text="I learned a kickflip in the parking lot behind the school. "
                                     "It took all summer."),
        memory_id=SKATEBOARD,
    )
    embedded = [d["_id"] for d in mdb.memories.find({"embedding": {"$exists": True}})]
    assert embedded == [SKATEBOARD]
    return mdb, DEMO_FAMILY_ID


def test_unembedded_keyword_match_beats_vector_only_hit(demo, atlas):
    db, fid = demo
    hits = search_memories(db, fid, HYMN_QUESTION)
    # The stand-in did return the skateboard story, as Atlas would: its only embedded doc.
    assert [d["_id"] for d in atlas[0]] == [SKATEBOARD]
    assert atlas[0][0]["score"] < search.VECTOR_MIN_SCORE
    ids = [h.memory_id for h in hits]
    assert ids[0] == "sample_hymn_books"
    assert SKATEBOARD not in ids

    quotes = ask_quotes(db, fid, HYMN_QUESTION)
    assert quotes[0].memory_id == "sample_hymn_books"
    assert quotes[0].text == "And then she dropped every single hymn book."
    assert all(q.memory_id != SKATEBOARD for q in quotes)


def test_unrelated_question_gets_no_story(demo, atlas):
    db, fid = demo
    assert search._keyword_search(db, fid, UNRELATED) == []
    quotes = ask_quotes(db, fid, UNRELATED)
    assert atlas and atlas[-1], "the nearest neighbour still came back from vector search"
    assert all(d["score"] < search.VECTOR_MIN_SCORE for d in atlas[-1])
    assert quotes == []
    assert compose_answer(UNRELATED, quotes) == NO_STORY


def test_semantic_vector_hit_is_kept_after_keyword_matches(demo, atlas):
    db, fid = demo
    query = "who went skating near the church?"  # no word in common with the skateboard story
    keyword_ids = [str(d["_id"]) for d, _ in search._keyword_search(db, fid, query)]
    assert keyword_ids and SKATEBOARD not in keyword_ids
    hits = search_memories(db, fid, query, limit=len(keyword_ids) + 1)
    assert [h.memory_id for h in hits] == [*keyword_ids, SKATEBOARD]
    assert hits[-1].score == pytest.approx(1.0)
    # Truncation keeps the keyword matches first.
    assert [h.memory_id for h in search_memories(db, fid, query, limit=1)] == keyword_ids[:1]


def test_doc_in_both_paths_appears_once_with_keyword_score(seeded, monkeypatch):
    vector_docs = [
        {**d, "score": score}
        for d, score in [(hymn_doc(), 0.95), (other_docs()[1], 0.85), (other_docs()[0], 0.7)]
    ]
    monkeypatch.setattr(search, "_vector_search", lambda *a, **k: [dict(d) for d in vector_docs])
    keyword = {str(d["_id"]): s for d, s in search._keyword_search(seeded, FAMILY_A, HYMN_QUERY)}
    assert set(keyword) == {"sample_hymn_books", "church_picnic", "first_trumpet"}

    hits = search_memories(seeded, FAMILY_A, HYMN_QUERY, vector=[0.1], limit=10)
    ids = [h.memory_id for h in hits]
    # Keyword matches by keyword score, then the vector-only sedan (0.85 >= floor).
    assert ids == ["sample_hymn_books", "church_picnic", "first_trumpet", "green_sedan"]
    assert len(ids) == len(set(ids))
    assert hits[0].score == pytest.approx(keyword["sample_hymn_books"])
    assert hits[1].score == pytest.approx(keyword["church_picnic"])  # 0.7 vector, still kept
    assert hits[3].score == pytest.approx(0.85)


def test_keyword_ties_break_on_vector_rank(mdb, monkeypatch):
    for year, mid in [(2001, "late"), (1950, "early"), (1980, "middle")]:
        mdb.memories.insert_one({
            "_id": mid, "familyId": FAMILY_A, "title": "Snow day", "year": year,
            "tellings": [{"authorId": "p", "text": "It snowed."}],
        })
    late = {**mdb.memories.find_one({"_id": "late"}), "score": 0.5}
    monkeypatch.setattr(search, "_vector_search", lambda *a, **k: [late])
    # A vector hit wins a keyword tie even below the floor, which only gates vector-only hits.
    assert [h.memory_id for h in search_memories(mdb, FAMILY_A, "snow", vector=[0.1])] == [
        "late", "early", "middle",
    ]


def test_vector_floor_is_inclusive(mdb, monkeypatch):
    docs = [
        {"_id": "at", "familyId": FAMILY_A, "title": "A", "year": 2000, "score": 0.9, "tellings": []},
        {"_id": "below", "familyId": FAMILY_A, "title": "B", "year": 1900, "score": 0.89, "tellings": []},
        {"_id": "above", "familyId": FAMILY_A, "title": "C", "year": 2010, "score": 0.95, "tellings": []},
    ]
    monkeypatch.setattr(search, "VECTOR_MIN_SCORE", 0.9)
    monkeypatch.setattr(search, "_vector_search", lambda *a, **k: [dict(d) for d in docs])
    assert [h.memory_id for h in search_memories(mdb, FAMILY_A, UNRELATED, vector=[0.1])] == [
        "above", "at",
    ]


def test_vector_min_score_env(monkeypatch):
    name = "STITCH_VECTOR_MIN_SCORE"
    if name not in os.environ:
        assert search.VECTOR_MIN_SCORE == 0.8
    monkeypatch.delenv(name, raising=False)
    assert search._env_float(name, 0.8) == 0.8
    for raw, expected in [("0.65", 0.65), (" 0.9 ", 0.9), ("", 0.8), ("abc", 0.8), ("nan", 0.8),
                          ("inf", 0.8)]:
        monkeypatch.setenv(name, raw)
        assert search._env_float(name, 0.8) == expected, raw


# ------------------------------------------------------------------ quotes and answers

def test_best_sentences():
    text = "It was a Sunday in 1988. I saw her at the top of the church steps! Then rain? Yes."
    assert best_sentences(text, "church steps") == ["I saw her at the top of the church steps!"]
    assert best_sentences(text, "zebra") == ["It was a Sunday in 1988."]
    assert best_sentences(text, "sunday church", k=2) == [
        "It was a Sunday in 1988.",
        "I saw her at the top of the church steps!",
    ]
    assert best_sentences("", "anything") == []
    assert best_sentences("One sentence without a stop", "stop") == ["One sentence without a stop"]
    assert best_sentences('He said "go." Then we went.', "went") == ["Then we went."]
    assert best_sentences("Line one\nLine two about hymns", "hymn") == ["Line two about hymns"]


def test_ask_quotes(seeded):
    quotes = ask_quotes(seeded, FAMILY_A, HYMN_QUERY)
    assert all(isinstance(q, schemas.AskQuote) for q in quotes)
    assert quotes[0] == schemas.AskQuote(
        memory_id="sample_hymn_books", author_id="dad",
        text="And then she dropped every single hymn book.", title="How I met your mother", year=1988,
    )
    assert quotes[1].author_id == "mom"
    assert quotes[2].memory_id == "church_picnic"
    assert quotes[2].text == "Every July the church held a picnic by the lake."
    assert len(ask_quotes(seeded, FAMILY_A, HYMN_QUERY, limit=1)) == 1
    assert ask_quotes(seeded, FAMILY_A, "nothing matches xyzzy") == []


def test_compose_answer():
    quote = schemas.AskQuote(
        memory_id="m", author_id="dad", text="I saw her at the top of the church steps.",
        title="How I met your mother", year=1988,
    )
    assert compose_answer("how did they meet", [quote], {"dad": "David"}) == (
        "David remembers (1988): 'I saw her at the top of the church steps.'"
    )
    assert compose_answer("anything", []) == NO_STORY == "No one in the family has told that story yet."

    second = schemas.AskQuote(memory_id="m", author_id="mom", text="I couldn't look up.")
    answer = compose_answer("q", [quote, second], {"dad": "David"})
    assert answer == (
        "David remembers (1988): 'I saw her at the top of the church steps.'\n"
        "Someone in the family remembers: 'I couldn't look up.'"
    )
    # Deterministic.
    assert compose_answer("q", [quote, second], {"dad": "David"}) == answer


# ------------------------------------------------------------------ real MongoDB

def test_real_db_keyword_search(real_db):
    real_db.memories.insert_many([hymn_doc(), *other_docs(), *[
        {**d, "_id": f"b_{d['_id']}", "familyId": FAMILY_B} for d in [hymn_doc(), *other_docs()]
    ]])
    hits = search_memories(real_db, FAMILY_A, HYMN_QUERY)
    assert hits[0].memory_id == "sample_hymn_books"
    assert all(not h.memory_id.startswith("b_") for h in hits)
    # With a vector, plain MongoDB raises OperationFailure and Atlas without the index finds
    # nothing; both must land on the keyword path.
    hits = search_memories(real_db, FAMILY_A, HYMN_QUERY, vector=[0.1, 0.2, 0.3])
    assert hits[0].memory_id == "sample_hymn_books"
    assert all(not h.memory_id.startswith("b_") for h in hits)
