"""db/search.py word folding and sentence splitting, and 'Ask the family' on the demo family.

Verb tenses, irregular pasts and kinship words fold to one token; a period after a title,
an initial or "a.m." inside a sentence does not end the sentence. The last part seeds the
real demo family (seed.demo, as STITCH_FAKE_DB does) into mongomock and asks as David.
Offline: no network, no keys, no MongoDB.
"""

from __future__ import annotations

import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import schemas
from db import client as db_client
from db import search
from db.client import get_db
from routes import ask as ask_routes
from routes import auth as auth_routes
from seed.demo import DEMO_FAMILY_ID, DEMO_PASSWORD, seed_demo

HYMN = "sample_hymn_books"
ROSA_WEDDING = "demo_rosa_joseph_wedding"


@pytest.fixture(autouse=True)
def no_embedder():
    search.set_embedder(None)  # keyword search only; never a network call
    yield
    search.set_embedder(None)


def token_set(word: str) -> set[str]:
    return set(search._tokens(word))


# ------------------------------------------------------------------ verb tenses

FOLD_TOGETHER = [
    ["rain", "rains", "rained", "raining"],
    ["dance", "danced", "dancing"],
    ["bake", "baked", "baking"],
    ["marry", "married", "marrying"],
    ["stop", "stopped", "stopping"],
    ["carry", "carried"],
    ["wedding", "weddings"],
    ["hand", "handed", "handing"],
]


@pytest.mark.parametrize("group", FOLD_TOGETHER, ids=lambda g: g[0])
def test_verb_forms_fold_to_one_token(group):
    sets = {word: token_set(word) for word in group}
    assert all(len(tokens) == 1 for tokens in sets.values()), sets
    assert len({frozenset(tokens) for tokens in sets.values()}) == 1, sets


NOT_CHANGED_INTO_ANOTHER = ["need", "red", "bed", "string", "spring", "thing", "king", "sing", "bring", "hymn"]


@pytest.mark.parametrize("word", NOT_CHANGED_INTO_ANOTHER)
def test_words_that_only_look_inflected_keep_their_own_stem(word):
    assert search._tokens(word) == [word]


def test_look_alikes_do_not_collide():
    stems = {word: search._stem(word) for word in NOT_CHANGED_INTO_ANOTHER}
    assert len(set(stems.values())) == len(stems), stems
    # The stripped remainder would be another word, or has no vowel: kept whole.
    assert search._stem("string") != search._stem("str")
    assert search._stem("spring") != search._stem("spr")
    assert search._stem("thing") != search._stem("th")
    assert search._stem("red") != search._stem("r")
    assert search._stem("bed") != search._stem("b")
    # '-eed' and '-ee' words are left alone.
    assert search._stem("need") == "need" and search._stem("agreed") == "agreed"
    assert search._stem("tree") == "tree" and search._stem("agree") == "agree"
    assert search._stem("needed") == search._stem("needing") == search._stem("needs") == "need"


def test_doubled_consonants():
    assert search._stem("sitting") == search._stem("sit") == "sit"
    assert search._stem("wedding") == search._stem("wed") == "wed"
    assert search._stem("stopped") == "stop"
    # l, s and z stay doubled: the base word has them too.
    assert search._stem("called") == search._stem("call") == "call"
    assert search._stem("kissed") == search._stem("kiss") == "kiss"
    assert search._stem("buzzing") == search._stem("buzz") == "buzz"


# ------------------------------------------------------------------ irregular pasts

IRREGULAR = {
    "met": "meet", "ran": "run", "gave": "give", "took": "take", "came": "come",
    "went": "go", "bought": "buy", "brought": "bring", "sang": "sing", "fell": "fall",
    "held": "hold", "made": "make", "said": "say", "wore": "wear", "drove": "drive",
    "rode": "ride", "ate": "eat", "sat": "sit", "got": "get", "lost": "lose",
    "found": "find", "taught": "teach", "wrote": "write", "built": "build", "flew": "fly",
    "threw": "throw", "caught": "catch", "grew": "grow", "knew": "know",
}


@pytest.mark.parametrize(("past", "present"), sorted(IRREGULAR.items()))
def test_irregular_past_folds_with_present(past, present):
    assert token_set(past) == token_set(present), (past, present)
    assert len(token_set(past)) == 1


def test_met_and_meet():
    assert search._tokens("met") == search._tokens("meet") == search._tokens("meeting") == ["meet"]
    assert search._tokens("How I met your mother") == search._tokens("How did you meet Mom?")


def test_saw_and_left_are_not_folded():
    # Both are also nouns or directions: never turned into see / leave.
    assert search._tokens("saw") == ["saw"] and search._tokens("left") == ["left"]
    assert token_set("saw") != token_set("see") and token_set("left") != token_set("leave")


def test_folding_words_are_not_stopwords():
    for word in ("met", "meet", *(k for k in search._KINSHIP)):
        assert word not in search._STOPWORDS, word


# ------------------------------------------------------------------ kinship

KINSHIP = {
    "mother": ["mom", "mother", "mama", "mum", "mommy", "momma", "moms", "mothers", "Mom's", "MOMS'"],
    "father": ["dad", "father", "papa", "daddy", "pop", "dads", "fathers", "Dad's", "Pops"],
    "grandma": ["grandma", "grandmother", "granny", "nana", "abuela", "grandmas", "grannies",
                "grandmothers", "Grandma's"],
    "grandpa": ["grandpa", "grandfather", "granddad", "grandad", "abuelo", "grandpas", "grandfathers"],
}


@pytest.mark.parametrize("group", sorted(KINSHIP), ids=str)
def test_kinship_synonyms_fold_to_one_token(group):
    sets = {word: search._tokens(word) for word in KINSHIP[group]}
    assert len({tuple(tokens) for tokens in sets.values()}) == 1, sets
    assert all(len(tokens) == 1 for tokens in sets.values()), sets


def test_kinship_tokens():
    assert search._tokens("Mom") == search._tokens("mother") == search._tokens("mama") == ["mother"]
    assert search._tokens("Dad") == search._tokens("father") == ["father"]
    assert token_set("mom") != token_set("dad")
    assert token_set("grandma") != token_set("mother") and token_set("grandpa") != token_set("father")
    assert search._tokens("Mom and Dad met at church") == ["mother", "father", "meet", "church"]


# ------------------------------------------------------------------ plurals unchanged

def test_existing_plural_behaviour_is_unchanged():
    assert search._stem("books") == search._stem("book") == "book"
    assert search._stem("churches") == search._stem("church") == "church"
    assert search._stem("stories") == search._stem("story") == "story"
    assert search._stem("cookies") == search._stem("cookie") == "cooky"
    for word in ("bus", "gas", "fly", "pie", "red", "bed", "ate", "cat", "class", "census",
                 "basis", "할머니", "josé"):
        assert search._stem(word) == (IRREGULAR.get(word) or word), word
    assert search._tokens("Grandma's cookies") == ["grandma", "cooky"]
    assert search._tokens("할머니의 김치, 정말!") == ["할머니의", "김치", "정말"]
    assert search._tokens("José_Ｔｒｕｍｐｅｔ ﬁsh") == ["josé", "trumpet", "fish"]


def test_matching_ignores_tense_in_a_sentence():
    text = "It rained on the morning we married. Dad handed Mama the umbrella."
    assert search.best_sentences(text, "rain") == ["It rained on the morning we married."]
    assert search.best_sentences(text, "Who hands mom things?") == ["Dad handed Mama the umbrella."]


def test_ties_go_to_the_query_word_as_written():
    # "went" folds to "go", so both sentences match; the one that says "went" wins.
    assert search.best_sentences('He said "go." Then we went.', "went") == ["Then we went."]
    assert search.best_sentences('He said "go." Then we went.', "go") == ['He said "go."']


# ------------------------------------------------------------------ sentences and abbreviations

@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "Mom and Dr. Kim walked me to St. Mary's on my first day of school. "
            "I dropped my lunchbox on the steps.",
            ["Mom and Dr. Kim walked me to St. Mary's on my first day of school.",
             "I dropped my lunchbox on the steps."],
        ),
        ("J. R. drove the green sedan. We sang.", ["J. R. drove the green sedan.", "We sang."]),
        ("We left at 6 a.m. and drove all day. It rained.",
         ["We left at 6 a.m. and drove all day.", "It rained."]),
        ("So did I. We went home.", ["So did I.", "We went home."]),
        # "a.m." before a capital letter does end the sentence.
        ("We stayed up until 6 a.m. Then we slept.", ["We stayed up until 6 a.m.", "Then we slept."]),
        ("Bring cake, plates, etc. and chairs. Done!", ["Bring cake, plates, etc. and chairs.", "Done!"]),
        # Titles match in any case, but only as whole words.
        ("We met MRS. Lee there. Then lunch.", ["We met MRS. Lee there.", "Then lunch."]),
        ("I sat first. Then Mrs. Lee sat.", ["I sat first.", "Then Mrs. Lee sat."]),
        ("Turn at the first. Second road.", ["Turn at the first.", "Second road."]),
        ("He lived on Oak Ave. near the mill. Mt. Hood was white.",
         ["He lived on Oak Ave. near the mill.", "Mt. Hood was white."]),
        # A line break always ends a sentence, abbreviation or not.
        ("Ask Mr.\nSmith came later.", ["Ask Mr.", "Smith came later."]),
        ("One.  \n\n  Two", ["One.", "Two"]),
        ("\n\n  \n", []),
        ("", []),
    ],
)
def test_sentences_keep_abbreviations_inside(text, expected):
    assert search._sentences(text) == expected


def test_sentences_with_many_abbreviations_stay_linear():
    text = "Dr. " * 40_000 + "Kim came. " + "J. " * 40_000 + "end."
    start = time.perf_counter()
    sentences = search._sentences(text)
    elapsed = time.perf_counter() - start
    assert elapsed < 1.0, f"splitting took {elapsed:.2f}s"
    assert len(sentences) == 2 and sentences[0].endswith("Kim came.") and sentences[1].endswith("end.")


def test_ask_quotes_the_whole_sentence_with_a_title(mdb):
    mdb.memories.insert_one({
        "_id": "m_school", "familyId": "fam", "title": "First day of school", "year": 1995,
        "tellings": [{
            "authorId": "ana",
            "text": "Mom and Dr. Kim walked me to St. Mary's on my first day of school. "
                    "I dropped my lunchbox on the steps.",
        }],
    })
    for query in ("Who walked you to school?", "Dr. Kim", "Mary"):
        quotes = search.ask_quotes(mdb, "fam", query)
        assert [q.text for q in quotes] == [
            "Mom and Dr. Kim walked me to St. Mary's on my first day of school."
        ], query
        assert all(q.text != "Mom and Dr." for q in quotes)
    lunch = search.ask_quotes(mdb, "fam", "lunchbox")
    assert [q.text for q in lunch] == ["I dropped my lunchbox on the steps."]


# ------------------------------------------------------------------ the demo family

@pytest.fixture
def demo_db(mdb):
    """The real demo family, seeded as STITCH_FAKE_DB seeds it."""
    db_client.ensure_indexes(mdb)
    seed_demo(mdb)
    return mdb


@pytest.fixture
def david(demo_db) -> TestClient:
    app = FastAPI()
    app.include_router(auth_routes.router)
    app.include_router(ask_routes.router)
    app.dependency_overrides[get_db] = lambda: demo_db
    tc = TestClient(app)
    res = tc.post("/auth/login", json={"email": "david@demo.stitch", "password": DEMO_PASSWORD})
    assert res.status_code == 200, res.text
    return tc


MEET_QUERIES = ["How did you meet Mom?", "How did you meet your wife?", "How did Mom and Dad meet?"]


@pytest.mark.parametrize("query", MEET_QUERIES)
def test_demo_meet_questions_find_the_hymn_books(demo_db, query):
    quotes = search.ask_quotes(demo_db, DEMO_FAMILY_ID, query)
    assert quotes and quotes[0].memory_id == HYMN, [(q.memory_id, q.text) for q in quotes]
    assert search.search_memories(demo_db, DEMO_FAMILY_ID, query)[0].memory_id == HYMN


@pytest.mark.parametrize("query", MEET_QUERIES[:2])
def test_demo_ask_route_as_david(david, query):
    """Asked to 'you', the kinship words are the listener's, so search answers as before."""
    res = david.post(f"/families/{DEMO_FAMILY_ID}/ask", json={"query": query})
    assert res.status_code == 200, res.text
    body = schemas.AskResponse.model_validate(res.json())
    assert body.quotes and body.quotes[0].memory_id == HYMN, body
    assert body.quotes[0].title == "How I met your mother"
    assert body.answer != search.NO_STORY


def test_demo_mom_and_dad_depend_on_who_asks(david, demo_db):
    """'Mom and Dad' are David's parents when David asks, and David and Ana when Sam asks."""
    query = "How did Mom and Dad meet?"
    body = david.post(f"/families/{DEMO_FAMILY_ID}/ask", json={"query": query}).json()
    assert body["best"]["memoryId"] == ROSA_WEDDING, body
    sam = TestClient(david.app)
    assert sam.post("/auth/login", json={"email": "sam@demo.stitch", "password": DEMO_PASSWORD}).status_code == 200
    body = sam.post(f"/families/{DEMO_FAMILY_ID}/ask", json={"query": query}).json()
    assert body["best"]["memoryId"] == HYMN, body
    assert body["quotes"][0]["memoryId"] == HYMN


def test_demo_rain_finds_rosa_and_josephs_wedding(david, demo_db):
    assert ROSA_WEDDING in {q.memory_id for q in search.ask_quotes(demo_db, DEMO_FAMILY_ID, "rain")}
    body = david.post(f"/families/{DEMO_FAMILY_ID}/ask", json={"query": "rain"}).json()
    rosa = [q for q in body["quotes"] if q["memoryId"] == ROSA_WEDDING]
    assert rosa and all("rained" in q["text"] for q in rosa), body


@pytest.mark.parametrize("query", ["spaceship volcano", "Did anyone ever ride a camel?"])
def test_demo_unrelated_question_still_has_no_story(david, demo_db, query):
    assert search.ask_quotes(demo_db, DEMO_FAMILY_ID, query) == []
    body = david.post(f"/families/{DEMO_FAMILY_ID}/ask", json={"query": query}).json()
    assert body["quotes"] == [] and body["answer"] == search.NO_STORY
