"""db/props.py: saved props and sets, library props, vector match with plain-text fallback."""

from __future__ import annotations

import json
import math
import os
import uuid
from pathlib import Path

import pytest
from pymongo.errors import OperationFailure

# mongomock becomes a dev dependency with db-foundation-auth; until then run with
# `uv run --with 'mongomock>=4.3.0' pytest tests/data/test_props_db.py`.
mongomock = pytest.importorskip("mongomock")

import schemas
from db import props as P

REPO = Path(__file__).resolve().parents[3]
SAMPLE = json.loads((REPO / "web" / "public" / "samples" / "hymn_books.memory.json").read_text())

FAM_A = "fam_a"
FAM_B = "fam_b"


# --------------------------------------------------------------------- fixtures


@pytest.fixture
def mdb():
    return mongomock.MongoClient(tz_aware=True)["stitch_test"]


@pytest.fixture
def real_db():
    uri = os.environ.get("MONGODB_URI_TEST")
    if not uri:
        pytest.skip("MONGODB_URI_TEST not set")
    from pymongo import MongoClient

    client = MongoClient(uri, serverSelectionTimeoutMS=3000, tz_aware=True)
    name = f"stitch_test_props_{uuid.uuid4().hex[:8]}"
    try:
        yield client[name]
    finally:
        client.drop_database(name)
        client.close()


@pytest.fixture
def library_dir(tmp_path, monkeypatch):
    """An empty library dir that the module uses by default; tests drop JSON files in it."""
    lib = tmp_path / "props"
    lib.mkdir()
    monkeypatch.setattr(P, "LIBRARY_DIR", lib)
    P.library_props.cache_clear()
    yield lib
    P.library_props.cache_clear()


@pytest.fixture(autouse=True)
def _isolated_library(library_dir):
    """Every test starts from an empty library unless it writes files into it."""
    return library_dir


def sample_prop() -> schemas.Prop:
    return schemas.Prop.model_validate(SAMPLE["props"][0])


def sample_set() -> schemas.SetDef:
    return schemas.SetDef.model_validate(SAMPLE["sets"][0])


def make_prop(name: str, description: str | None = None, **kw) -> schemas.Prop:
    kw.setdefault("size", "hand")
    kw.setdefault("art", {"body": None})
    return schemas.Prop(id="ignored", name=name, description=description, **kw)


def make_set(name: str, description: str | None = None) -> schemas.SetDef:
    return schemas.SetDef(id="ignored", name=name, description=description,
                          layers=[schemas.SetLayer(art=None, depth=0, height=1.0)])


def write_library_prop(lib: Path, pid: str, name: str, description: str | None = None,
                       **extra) -> None:
    data = {"id": pid, "name": name, "description": description, "source": "library",
            "size": "furniture", "art": {"body": None}, **extra}
    (lib / f"{pid}.json").write_text(json.dumps(data))
    P.library_props.cache_clear()


# ------------------------------------------------------------- round trip, ids


def test_round_trip_hymn_sample_prop_and_set(mdb):
    prop = sample_prop()
    saved = P.insert_prop(mdb, FAM_A, prop, prop_id="hymn_books", embedding=[0.1, 0.2, 0.3])
    assert saved.id == "hymn_books"
    assert saved.family_id == FAM_A
    # Everything but the family id round-trips unchanged.
    expected = prop.model_copy(update={"family_id": FAM_A})
    assert saved == expected
    assert P.get_prop(mdb, FAM_A, "hymn_books") == expected
    assert P.list_props(mdb, FAM_A) == [expected]
    assert P.get_props(mdb, FAM_A, ["hymn_books"]) == [expected]

    stored = mdb.props.find_one({"_id": "hymn_books"})
    assert stored["familyId"] == FAM_A
    assert "id" not in stored and "family_id" not in stored
    assert stored["embedding"] == [0.1, 0.2, 0.3]

    set_def = sample_set()
    saved_set = P.insert_set(mdb, FAM_A, set_def, set_id="church_steps_1988")
    expected_set = set_def.model_copy(update={"family_id": FAM_A})
    assert saved_set == expected_set
    assert P.get_set(mdb, FAM_A, "church_steps_1988") == expected_set
    assert P.list_sets(mdb, FAM_A) == [expected_set]
    assert P.get_sets(mdb, FAM_A, ["church_steps_1988"]) == [expected_set]
    assert mdb.sets.find_one({"_id": "church_steps_1988"})["familyId"] == FAM_A


def test_insert_ignores_incoming_id_and_family_and_generates_objectid(mdb):
    prop = make_prop("Trumpet", family_id="someone_else")
    saved = P.insert_prop(mdb, FAM_A, prop)
    assert saved.id != "ignored" and len(saved.id) == 24
    assert saved.family_id == FAM_A
    assert mdb.props.find_one({"_id": saved.id})["familyId"] == FAM_A
    s = P.insert_set(mdb, FAM_A, make_set("Kitchen"))
    assert s.id != "ignored" and len(s.id) == 24


def test_duplicate_id_raises_value_error(mdb):
    P.insert_prop(mdb, FAM_A, sample_prop(), prop_id="hymn_books")
    with pytest.raises(ValueError):
        P.insert_prop(mdb, FAM_A, sample_prop(), prop_id="hymn_books")
    P.insert_set(mdb, FAM_A, sample_set(), set_id="steps")
    with pytest.raises(ValueError):
        P.insert_set(mdb, FAM_A, sample_set(), set_id="steps")


def test_embedding_is_never_returned(mdb):
    vec = [1.0, 0.0]
    p = P.insert_prop(mdb, FAM_A, make_prop("Trumpet", "a brass trumpet"), embedding=vec)
    s = P.insert_set(mdb, FAM_A, make_set("Kitchen", "grandma's kitchen"), embedding=vec)
    results = [
        p,
        P.get_prop(mdb, FAM_A, p.id),
        *P.get_props(mdb, FAM_A, [p.id]),
        *P.list_props(mdb, FAM_A),
        *P.find_props(mdb, FAM_A, "trumpet"),
        *P.find_props(mdb, FAM_A, "trumpet", vector=vec),
        P.update_prop(mdb, FAM_A, p.id, schemas.PropPatch(name="Horn")),
        P.replace_prop(mdb, FAM_A, p, embedding=vec),
        s,
        P.get_set(mdb, FAM_A, s.id),
        *P.get_sets(mdb, FAM_A, [s.id]),
        *P.list_sets(mdb, FAM_A),
        *P.find_sets(mdb, FAM_A, "kitchen"),
        P.replace_set(mdb, FAM_A, s, embedding=vec),
    ]
    for r in results:
        assert r is not None
        dumped = r.model_dump(by_alias=True)
        assert "embedding" not in dumped and "_id" not in dumped
        assert dumped["id"]


def test_list_ordered_by_name_and_get_many_in_input_order(mdb):
    ids = {n: P.insert_prop(mdb, FAM_A, make_prop(n)).id for n in ["trumpet", "Accordion", "banjo"]}
    assert [p.name for p in P.list_props(mdb, FAM_A)] == ["Accordion", "banjo", "trumpet"]
    got = P.get_props(mdb, FAM_A, [ids["trumpet"], "missing", ids["Accordion"], ids["trumpet"]])
    assert [p.name for p in got] == ["trumpet", "Accordion", "trumpet"]
    assert P.get_props(mdb, FAM_A, []) == []

    sids = {n: P.insert_set(mdb, FAM_A, make_set(n)).id for n in ["porch", "Attic"]}
    assert [s.name for s in P.list_sets(mdb, FAM_A)] == ["Attic", "porch"]
    assert [s.name for s in P.get_sets(mdb, FAM_A, [sids["porch"], "nope", sids["Attic"]])] == [
        "porch", "Attic"]


# ---------------------------------------------------------------------- replace


def test_replace_prop_and_set(mdb):
    p = P.insert_prop(mdb, FAM_A, make_prop("Trumpet", "a brass trumpet"), embedding=[1.0, 0.0])
    new = p.model_copy(update={"color": "#ffcc00", "grip": [0.2, 0.5]})
    out = P.replace_prop(mdb, FAM_A, new)
    assert out.color == "#ffcc00" and out.grip == [0.2, 0.5] and out.family_id == FAM_A
    assert P.get_prop(mdb, FAM_A, p.id) == out
    # Same description: the stored vector survives a replace without one.
    assert mdb.props.find_one({"_id": p.id})["embedding"] == [1.0, 0.0]
    # A rename alone keeps it too (the vector is computed from the description), as
    # update_prop does.
    new = P.replace_prop(mdb, FAM_A, new.model_copy(update={"name": "Horn"}))
    assert new.name == "Horn"
    assert mdb.props.find_one({"_id": p.id})["embedding"] == [1.0, 0.0]
    # New description: the old vector is stale and dropped.
    P.replace_prop(mdb, FAM_A, new.model_copy(update={"description": "a silver cornet"}))
    assert "embedding" not in mdb.props.find_one({"_id": p.id})
    # An explicit embedding is stored.
    P.replace_prop(mdb, FAM_A, new, embedding=[0.0, 1.0])
    assert mdb.props.find_one({"_id": p.id})["embedding"] == [0.0, 1.0]

    assert P.replace_prop(mdb, FAM_A, new.model_copy(update={"id": "missing"})) is None

    s = P.insert_set(mdb, FAM_A, make_set("Kitchen"))
    s2 = P.replace_set(mdb, FAM_A, s.model_copy(update={"marks": {"sink": [0.3, 0.0]}}))
    assert s2.marks == {"sink": [0.3, 0.0]}
    assert P.get_set(mdb, FAM_A, s.id) == s2


# ------------------------------------------------------------------- update_prop


def test_update_prop_rename(mdb):
    p = P.insert_prop(mdb, FAM_A, sample_prop(), prop_id="hymn_books", embedding=[1.0])
    out = P.update_prop(mdb, FAM_A, "hymn_books", schemas.PropPatch(name="Hymnals"))
    assert out.name == "Hymnals"
    assert out.model_copy(update={"name": p.name}) == p
    assert P.get_prop(mdb, FAM_A, "hymn_books").name == "Hymnals"
    assert mdb.props.find_one({"_id": "hymn_books"})["embedding"] == [1.0]


def test_update_prop_photo_replaces_art_and_keeps_rig(mdb):
    prop = make_prop("Accordion", size="carried", anchor=[0.4, 0.9], grip=[0.3, 0.2],
                     sockets={"strap": [0.5, 0.1]},
                     art={"body": "media/a1", "bellows": "media/a2"},
                     parts={"bellows": schemas.PropPart(art="bellows", at=[0.5, 0.5])},
                     states={"open": {"body": "media/a3"}})
    p = P.insert_prop(mdb, FAM_A, prop)
    out = P.update_prop(mdb, FAM_A, p.id, schemas.PropPatch(photo_id="media/photo9"))
    assert out.art == {"body": "media/photo9"}
    assert out.source == "photo"
    assert out.states == {}
    assert out.parts == {}
    assert (out.size, out.anchor, out.grip) == ("carried", [0.4, 0.9], [0.3, 0.2])
    assert out.sockets == {"strap": [0.5, 0.1]}
    assert out.name == "Accordion"
    assert P.get_prop(mdb, FAM_A, p.id) == out


def test_update_prop_both_and_empty_patch(mdb):
    p = P.insert_prop(mdb, FAM_A, make_prop("Trumpet"))
    out = P.update_prop(mdb, FAM_A, p.id, schemas.PropPatch(name="Horn", photo_id="media/x"))
    assert (out.name, out.art, out.source) == ("Horn", {"body": "media/x"}, "photo")
    assert P.update_prop(mdb, FAM_A, p.id, schemas.PropPatch()) == out
    assert P.update_prop(mdb, FAM_A, "missing", schemas.PropPatch()) is None


def test_library_props_cannot_be_patched(mdb, library_dir):
    write_library_prop(library_dir, "chair", "Chair", "a wooden kitchen chair")
    assert P.get_prop(mdb, FAM_A, "chair") is not None
    assert P.update_prop(mdb, FAM_A, "chair", schemas.PropPatch(name="Stool")) is None
    assert P.update_prop(mdb, FAM_A, "chair", schemas.PropPatch(photo_id="media/x")) is None
    assert P.get_prop(mdb, FAM_A, "chair").name == "Chair"
    assert mdb.props.count_documents({}) == 0


# ----------------------------------------------------------------------- library


def test_library_empty_and_missing_dir(tmp_path, library_dir):
    assert P.library_props() == {}
    assert P.library_props(tmp_path / "does_not_exist") == {}
    (library_dir / ".gitkeep").write_text("")
    P.library_props.cache_clear()
    assert P.library_props() == {}


def test_real_library_dir_loads():
    # The checked-in shared/props/ (currently only .gitkeep) must always load cleanly.
    real = P.library_props(Path(__file__).resolve().parents[3] / "shared" / "props")
    for pid, prop in real.items():
        assert prop.id == pid and prop.family_id is None


def test_library_loading_from_tmp_dir(tmp_path, mdb):
    lib = tmp_path / "lib"
    lib.mkdir()
    (lib / "car.json").write_text(json.dumps({
        "id": "car", "familyId": "should_be_cleared", "name": "Car", "source": "library",
        "size": "vehicle", "art": {"body": None, "door": None},
        "parts": {"door": {"art": "door", "at": [0.6, 0.5]}},
        "states": {"door_open": {"door": "media/door_open"}},
    }))
    (lib / "suitcase.json").write_text(json.dumps({
        "name": "Suitcase", "size": "carried", "art": {"body": None}}))  # id from filename
    (lib / "notes.txt").write_text("not a prop")
    loaded = P.library_props(lib)
    assert set(loaded) == {"car", "suitcase"}
    assert all(p.family_id is None for p in loaded.values())
    assert loaded["car"].parts["door"].at == [0.6, 0.5]
    # Copies: mutating a result does not poison the cache.
    loaded["car"].name = "Mutated"
    assert P.library_props(lib)["car"].name == "Car"


@pytest.mark.parametrize("content", [
    json.dumps({"id": "broken", "name": "Broken"}),  # fails Prop validation
    "[1]",  # top level is not an object
    '"chair"',
    "null",
    "{not json",
])
def test_invalid_library_file_raises_with_filename(tmp_path, content):
    lib = tmp_path / "bad"
    lib.mkdir()
    (lib / "broken.json").write_text(content)
    with pytest.raises(ValueError, match="broken.json"):
        P.library_props(lib)


def test_library_merged_into_get_prop_and_get_props(mdb, library_dir):
    write_library_prop(library_dir, "chair", "Chair")
    write_library_prop(library_dir, "table", "Table")
    mine = P.insert_prop(mdb, FAM_A, make_prop("Our chair"), prop_id="chair")  # shadows library
    assert P.get_prop(mdb, FAM_A, "chair") == mine
    assert P.get_prop(mdb, FAM_B, "chair").family_id is None  # B sees the library one
    assert P.get_prop(mdb, FAM_A, "table").name == "Table"
    got = P.get_props(mdb, FAM_A, ["table", "nope", "chair"])
    assert [(p.id, p.name) for p in got] == [("table", "Table"), ("chair", "Our chair")]
    # list_props is the family's saved props only.
    assert [p.id for p in P.list_props(mdb, FAM_A)] == ["chair"]
    assert P.list_props(mdb, FAM_B) == []


# -------------------------------------------------------------- family isolation


def test_cross_family_isolation_for_every_read_and_update(mdb):
    b_prop = P.insert_prop(mdb, FAM_B, make_prop("Trumpet case", "the old trumpet case"),
                           embedding=[1.0, 0.0])
    b_set = P.insert_set(mdb, FAM_B, make_set("Kitchen", "grandma's kitchen"), embedding=[1.0, 0.0])
    a_prop = P.insert_prop(mdb, FAM_A, make_prop("Banjo"))

    assert P.get_prop(mdb, FAM_A, b_prop.id) is None
    assert P.get_props(mdb, FAM_A, [b_prop.id, a_prop.id]) == [a_prop]
    assert P.list_props(mdb, FAM_A) == [a_prop]
    assert P.find_props(mdb, FAM_A, "trumpet case") == []
    assert P.find_props(mdb, FAM_A, "trumpet case", vector=[1.0, 0.0]) == []
    assert P.update_prop(mdb, FAM_A, b_prop.id, schemas.PropPatch(name="Mine now")) is None
    assert P.update_prop(mdb, FAM_A, b_prop.id, schemas.PropPatch(photo_id="media/x")) is None
    assert P.replace_prop(mdb, FAM_A, b_prop.model_copy(update={"name": "Mine now"})) is None
    assert P.get_set(mdb, FAM_A, b_set.id) is None
    assert P.get_sets(mdb, FAM_A, [b_set.id]) == []
    assert P.list_sets(mdb, FAM_A) == []
    assert P.find_sets(mdb, FAM_A, "kitchen") == []
    assert P.find_sets(mdb, FAM_A, "kitchen", vector=[1.0, 0.0]) == []
    assert P.replace_set(mdb, FAM_A, b_set.model_copy(update={"name": "Mine now"})) is None

    # Family B's data is untouched.
    assert P.get_prop(mdb, FAM_B, b_prop.id) == b_prop
    assert P.get_set(mdb, FAM_B, b_set.id) == b_set
    # A same-id insert in another family is a duplicate (ids are global), not a takeover.
    with pytest.raises(ValueError):
        P.insert_prop(mdb, FAM_A, make_prop("x"), prop_id=b_prop.id)
    assert P.get_prop(mdb, FAM_B, b_prop.id) == b_prop


# --------------------------------------------------------------- keyword scoring


def test_keyword_score_prefers_closer_name():
    q = "the old trumpet case"
    assert P._keyword_score(q, "Trumpet case", None) > P._keyword_score(q, "Trumpet", None) > 0


def test_keyword_score_exact_name_scores_highest():
    exact = P._keyword_score("hymn book", "hymn books", None)  # plural stripped
    partial = P._keyword_score("hymn book", "hymn book stand", "hymn book stand for hymn books")
    desc_only = P._keyword_score("hymn book", "Stack", "a stack of worn blue hymn books")
    assert exact >= 1.0 > partial > desc_only > 0


def test_keyword_score_tokens_stopwords_plurals():
    assert P._keyword_score("", "Trumpet", None) == 0
    assert P._keyword_score("the of a", "the of a", None) == 0
    assert P._keyword_score("piano", "Trumpet", "brass") == 0
    assert P._keyword_score("TRUMPETS", "trumpet", None) == 1.0  # exact, no description
    assert P._keyword_score("glasses", "glass", None) == 1.0
    assert P._keyword_score("boxes", "box", None) == 1.0
    assert P._keyword_score("cherries", "cherry", None) == 1.0
    assert P._keyword_score("grandma's kitchen", "Grandmas kitchen", None) == 1.0
    assert P._keyword_score("trumpet", "Trumpet", "a trumpet") > 1.0
    # -ies plurals of -ie words (cookie, movie) as well as -y words (cherry, lady).
    for plural, singular in [("cookies", "cookie"), ("movies", "movie"), ("brownies", "brownie"),
                             ("ladies", "lady"), ("pies", "pie"), ("keys", "key"),
                             ("toys", "toy")]:
        assert P._keyword_score(plural, singular, None) == 1.0, plural
    assert P._keyword_score("cookies", "Cookie tin", None) > 0
    assert P._keyword_score("cookie", "Cooking pot", None) == 0
    # Description-only matches count, but less than name matches.
    assert 0 < P._keyword_score("brass", "Trumpet", "a brass trumpet") < P._keyword_score(
        "trumpet", "Trumpet case", None)


def test_keyword_score_non_latin_scripts():
    assert P._keyword_score("할머니", "할머니 부엌", None) > 0
    assert P._keyword_score("할머니 부엌", "할머니 부엌", None) == 1.0
    assert P._keyword_score("부엌", "Kitchen", "할머니의 부엌") > 0  # description match
    assert P._keyword_score("café", "Café table", None) > 0
    assert P._keyword_score("ＴＲＵＭＰＥＴ", "trumpet", None) == 1.0  # NFKC full-width
    assert P._keyword_score("snake_case", "snake case", None) == 1.0


# -------------------------------------------------------------- find (fallback)


def test_find_props_fallback_ranking_with_library(mdb, library_dir):
    write_library_prop(library_dir, "suitcase", "Suitcase", "a leather suitcase")  # no match
    write_library_prop(library_dir, "lib_trumpet", "Trumpet", "a brass trumpet")
    case = P.insert_prop(mdb, FAM_A, make_prop("Trumpet case", "black case with velvet lining"))
    P.insert_prop(mdb, FAM_A, make_prop("Banjo", "a five string banjo"))
    hymn = P.insert_prop(mdb, FAM_A, sample_prop(), prop_id="hymn_books")

    found = P.find_props(mdb, FAM_A, "the old trumpet case")
    assert [p.name for p in found] == ["Trumpet case", "Trumpet"]
    assert found[0] == case
    assert found[1].family_id is None

    assert P.find_props(mdb, FAM_A, "hymn book")[0] == hymn
    assert [p.name for p in P.find_props(mdb, FAM_A, "worn blue books")] == ["hymn books"]
    assert P.find_props(mdb, FAM_A, "piano") == []
    assert len(P.find_props(mdb, FAM_A, "the old trumpet case", limit=1)) == 1
    assert P.find_props(mdb, FAM_A, "trumpet", limit=0) == []


def test_find_props_family_prop_shadows_library(mdb, library_dir):
    write_library_prop(library_dir, "chair", "Chair")
    P.insert_prop(mdb, FAM_A, make_prop("Chair", "grandpa's rocking chair"), prop_id="chair")
    found = P.find_props(mdb, FAM_A, "chair")
    assert [(p.id, p.family_id) for p in found] == [("chair", FAM_A)]
    assert [(p.id, p.family_id) for p in P.find_props(mdb, FAM_B, "chair")] == [("chair", None)]


def test_find_props_family_isolation(mdb):
    P.insert_prop(mdb, FAM_B, make_prop("Trumpet case", "the old trumpet case"))
    a = P.insert_prop(mdb, FAM_A, make_prop("Trumpet", "a dented trumpet"))
    assert P.find_props(mdb, FAM_A, "the old trumpet case") == [a]


def test_find_sets_fallback(mdb):
    steps = P.insert_set(mdb, FAM_A, sample_set(), set_id="church_steps_1988")
    P.insert_set(mdb, FAM_A, make_set("Grandma's kitchen", "yellow kitchen with a big table"))
    P.insert_set(mdb, FAM_B, make_set("Church steps", "church steps"))
    assert P.find_sets(mdb, FAM_A, "church steps") == [steps]
    assert [s.name for s in P.find_sets(mdb, FAM_A, "the kitchen")] == ["Grandma's kitchen"]
    assert P.find_sets(mdb, FAM_A, "beach") == []


def test_vector_path_on_mongomock_falls_back_without_raising(mdb):
    with pytest.raises(NotImplementedError):  # what the fallback is guarding against
        list(mdb.props.aggregate(P._vector_pipeline("props_vector", [1.0], FAM_A, 5)))
    case = P.insert_prop(mdb, FAM_A, make_prop("Trumpet case"), embedding=[1.0, 0.0])
    assert P.find_props(mdb, FAM_A, "trumpet case", vector=[1.0, 0.0]) == [case]
    s = P.insert_set(mdb, FAM_A, make_set("Kitchen"), embedding=[1.0, 0.0])
    assert P.find_sets(mdb, FAM_A, "kitchen", vector=[1.0, 0.0]) == [s]


# ------------------------------------------------------------ find (vector path)


class _FakeVectorCollection:
    """Wraps a mongomock collection and simulates $vectorSearch (cosine, pre-filter)."""

    def __init__(self, coll, mode: str, calls: list):
        self._coll, self._mode, self._calls = coll, mode, calls

    def __getattr__(self, name):
        return getattr(self._coll, name)

    def aggregate(self, pipeline):
        if "$vectorSearch" not in pipeline[0]:
            return self._coll.aggregate(pipeline)
        self._calls.append(pipeline)
        if self._mode == "fail":
            raise OperationFailure("$vectorSearch stage is only allowed on MongoDB Atlas")
        if self._mode == "empty":
            return iter([])
        vs = pipeline[0]["$vectorSearch"]
        docs = list(self._coll.find(vs["filter"]))  # {"familyId": {"$eq": fid}} is valid MQL
        q = vs["queryVector"]

        def cos(doc):
            v = doc.get(vs["path"])
            if not v:
                return -2.0
            return sum(a * b for a, b in zip(q, v)) / (math.hypot(*q) * math.hypot(*v))

        docs = [d for d in docs if d.get(vs["path"])]
        docs.sort(key=cos, reverse=True)
        docs = docs[: vs["limit"]]
        ids = [d["_id"] for d in docs]
        rest = pipeline[1:]
        out = list(self._coll.aggregate([{"$match": {"_id": {"$in": ids}}}, *rest]))
        out.sort(key=lambda d: ids.index(d["_id"]))
        return iter(out)


class _FakeVectorDB:
    def __init__(self, db, mode: str = "ok"):
        self._db, self._mode, self.calls = db, mode, []

    def __getitem__(self, name):
        return _FakeVectorCollection(self._db[name], self._mode, self.calls)

    def __getattr__(self, name):
        return self[name]


def test_vector_pipeline_has_family_prefilter():
    pipe = P._vector_pipeline("props_vector", [0.5, 1], FAM_A, 3)
    vs = pipe[0]["$vectorSearch"]
    assert vs == {
        "index": "props_vector", "path": "embedding", "queryVector": [0.5, 1.0],
        "numCandidates": 60, "limit": 3, "filter": {"familyId": {"$eq": FAM_A}},
    }
    assert {"$match": {"familyId": FAM_A}} in pipe
    assert {"$project": {"embedding": 0}} in pipe


def test_find_props_vector_path_merges_with_keyword_and_library(mdb, library_dir):
    write_library_prop(library_dir, "lib_trumpet", "Trumpet", "a brass trumpet")
    write_library_prop(library_dir, "shadowed", "Trumpet stand")
    case = P.insert_prop(mdb, FAM_A, make_prop("Trumpet case", "velvet lined case"),
                         embedding=[1.0, 0.0])
    banjo = P.insert_prop(mdb, FAM_A, make_prop("Banjo"), embedding=[0.0, 1.0])
    stand = P.insert_prop(mdb, FAM_A, make_prop("Our trumpet stand"), prop_id="shadowed")  # no vec
    P.insert_prop(mdb, FAM_B, make_prop("Trumpet case B"), embedding=[1.0, 0.0])
    fake = _FakeVectorDB(mdb)

    found = P.find_props(fake, FAM_A, "old trumpet case", vector=[0.9, 0.1], limit=5)
    assert len(fake.calls) == 1
    vs = fake.calls[0][0]["$vectorSearch"]
    assert vs["index"] == "props_vector" and vs["filter"] == {"familyId": {"$eq": FAM_A}}
    assert vs["limit"] == 5 and vs["numCandidates"] == 100
    # The related vector hit, then keyword matches best first (the library trumpet also
    # matches in its description, then the family prop with no embedding), then the
    # unrelated nearest neighbour. The library prop shadowed by the family's prop with
    # the same id is never added, and family B never appears.
    assert [p.id for p in found] == [case.id, "lib_trumpet", stand.id, banjo.id]
    assert found[2] == stand and found[2].family_id == FAM_A
    assert all(p.family_id in (FAM_A, None) for p in found)

    # An exact name match (here the library "Trumpet") outranks the nearest vector hit.
    assert [p.id for p in P.find_props(fake, FAM_A, "trumpet", vector=[0.9, 0.1], limit=1)] == [
        "lib_trumpet"]
    assert [p.id for p in P.find_props(fake, FAM_A, "old trumpet case", vector=[0.9, 0.1],
                                       limit=2)] == [case.id, "lib_trumpet"]


def test_find_props_vector_path_never_hides_props_without_embedding(mdb):
    # Seeded offline: no embedding. Another prop has one and is the nearest neighbour.
    hymn = P.insert_prop(mdb, FAM_A, sample_prop(), prop_id="hymn_books")
    banjo = P.insert_prop(mdb, FAM_A, make_prop("Banjo"), embedding=[0.0, 1.0])
    fake = _FakeVectorDB(mdb)
    found = P.find_props(fake, FAM_A, "hymn books", vector=[1.0, 0.0])
    assert [p.id for p in found] == ["hymn_books", banjo.id]
    assert found[0] == hymn

    # A replace with a new description and no vector drops the stale embedding; the prop
    # still matches by name, ahead of the unrelated neighbour.
    case = P.insert_prop(mdb, FAM_A, make_prop("Trumpet case", "black case"), embedding=[1.0, 0.0])
    P.replace_prop(mdb, FAM_A, case.model_copy(update={"description": "velvet lined case"}))
    assert "embedding" not in mdb.props.find_one({"_id": case.id})
    found = P.find_props(fake, FAM_A, "trumpet case", vector=[1.0, 0.0])
    assert [p.id for p in found][:2] == [case.id, banjo.id]


def test_find_props_exact_name_beats_nearer_vector_hit(mdb):
    near = P.insert_prop(mdb, FAM_A, make_prop("Cornet", "a small brass horn"), embedding=[1.0, 0.0])
    exact = P.insert_prop(mdb, FAM_A, make_prop("Trumpet"), embedding=[0.0, 1.0])
    fake = _FakeVectorDB(mdb)
    assert [p.id for p in P.find_props(fake, FAM_A, "trumpet", vector=[1.0, 0.0])] == [
        exact.id, near.id]
    # With no word in common, the vector order alone decides (semantic-only matches).
    assert [p.id for p in P.find_props(fake, FAM_A, "bugle", vector=[1.0, 0.0])] == [
        near.id, exact.id]


def test_find_sets_vector_path(mdb):
    near = P.insert_set(mdb, FAM_A, make_set("Kitchen"), embedding=[0.0, 1.0])
    far = P.insert_set(mdb, FAM_A, make_set("Porch"), embedding=[1.0, 0.0])
    P.insert_set(mdb, FAM_B, make_set("B kitchen"), embedding=[0.0, 1.0])
    fake = _FakeVectorDB(mdb)
    assert P.find_sets(fake, FAM_A, "anything", vector=[0.1, 0.9]) == [near, far]
    assert fake.calls[0][0]["$vectorSearch"]["index"] == "sets_vector"
    # A saved set with no embedding still matches by name on the vector path.
    steps = P.insert_set(mdb, FAM_A, sample_set(), set_id="church_steps_1988")
    assert P.find_sets(fake, FAM_A, "church steps", vector=[0.1, 0.9])[0] == steps


@pytest.mark.parametrize("mode", ["fail", "empty"])
def test_vector_failure_or_no_hits_falls_back_to_keywords(mdb, mode):
    case = P.insert_prop(mdb, FAM_A, make_prop("Trumpet case"), embedding=[1.0, 0.0])
    s = P.insert_set(mdb, FAM_A, make_set("Kitchen"))
    fake = _FakeVectorDB(mdb, mode)
    assert P.find_props(fake, FAM_A, "trumpet case", vector=[1.0, 0.0]) == [case]
    assert P.find_sets(fake, FAM_A, "kitchen", vector=[1.0, 0.0]) == [s]
    assert len(fake.calls) == 2


# ---------------------------------------------------------------------- real db


def test_real_db_round_trip_and_fallback(real_db):
    hymn = P.insert_prop(real_db, FAM_A, sample_prop(), prop_id="hymn_books", embedding=[0.1] * 4)
    steps = P.insert_set(real_db, FAM_A, sample_set(), set_id="church_steps_1988")
    P.insert_prop(real_db, FAM_B, make_prop("hymn books", "family B's"))
    assert P.get_prop(real_db, FAM_A, "hymn_books") == hymn
    assert P.list_props(real_db, FAM_A) == [hymn]
    assert P.get_sets(real_db, FAM_A, ["church_steps_1988"]) == [steps]
    with pytest.raises(ValueError):
        P.insert_prop(real_db, FAM_A, sample_prop(), prop_id="hymn_books")
    renamed = P.update_prop(real_db, FAM_A, "hymn_books", schemas.PropPatch(name="hymnals"))
    assert renamed.name == "hymnals"
    # Plain MongoDB raises OperationFailure on $vectorSearch; Atlas without the index
    # returns nothing. Either way the keyword fallback answers, scoped to family A.
    found = P.find_props(real_db, FAM_A, "hymnals", vector=[0.1] * 4)
    assert [p.id for p in found] == ["hymn_books"]
    assert P.find_sets(real_db, FAM_A, "church steps", vector=[0.1] * 4) == [steps]
