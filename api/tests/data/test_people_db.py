"""db/people.py: CRUD, claiming, character sheets, and the $graphLookup tree."""

from __future__ import annotations

import os
import uuid

import pytest
from pymongo.errors import OperationFailure

# mongomock becomes a dev dependency with db-foundation-auth; until then run with
# `uv run --with 'mongomock>=4.3.0' pytest tests/data/test_people_db.py`.
mongomock = pytest.importorskip("mongomock")

import schemas
from db import people as P

FAM = "fam_a"
OTHER = "fam_b"


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
    name = f"stitch_test_people_{uuid.uuid4().hex[:8]}"
    try:
        yield client[name]
    finally:
        client.drop_database(name)
        client.close()


def new(name, born=None, parents=(), spouses=(), character=None, died=None):
    return schemas.NewPerson(
        name=name,
        born=born,
        died=died,
        parent_ids=list(parents),
        spouse_ids=list(spouses),
        character=character,
    )


def add(db, name, born=None, parents=(), spouses=(), *, pid=None, family=FAM, **kw):
    return P.create_person(
        db, family, new(name, born, parents, spouses, **kw), added_by="u_admin", person_id=pid
    )


def build_family(db, family=FAM):
    """Three generations, 8 people, married-in spouses on both sides (mom and Uncle Joe)."""
    add(db, "Grandpa Luis", 1935, pid="grandpa", family=family)
    add(db, "Grandma Rosa", 1938, spouses=["grandpa"], pid="grandma", family=family)
    add(db, "David", 1962, parents=["grandpa", "grandma"], pid="dad", family=family)
    add(db, "Ana", 1964, spouses=["dad"], pid="mom", family=family)
    add(db, "Aunt Maria", 1965, parents=["grandpa", "grandma"], pid="aunt", family=family)
    add(db, "Uncle Joe", 1960, spouses=["aunt"], pid="joe", family=family)
    add(db, "Sam", 1990, parents=["dad", "mom"], pid="kid", family=family)
    add(db, "Lucia", None, parents=["aunt", "joe"], pid="cousin", family=family)


EXPECTED_ORDER = ["grandpa", "grandma", "joe", "dad", "mom", "aunt", "kid", "cousin"]
EXPECTED_GEN = {
    "grandpa": 0, "grandma": 0,
    "joe": 1, "dad": 1, "mom": 1, "aunt": 1,
    "kid": 2, "cousin": 2,
}


# ------------------------------------------------------------------ pick_character


def test_palette_and_items_shape():
    assert len(P.PALETTE) >= 12 and len({c.lower() for c in P.PALETTE}) == len(P.PALETTE)
    assert len(P.ITEMS) >= 16 and len({i.lower() for i in P.ITEMS}) == len(P.ITEMS)
    assert "#3f6fb5" in P.PALETTE and "#c0504d" in P.PALETTE
    assert all(c.startswith("#") and len(c) == 7 for c in P.PALETTE)


def test_pick_character_first_unused_in_order():
    sheet = P.pick_character(set(), set())
    assert sheet.color == P.PALETTE[0]
    assert sheet.item == P.ITEMS[0]
    assert sheet.build == "average"

    sheet = P.pick_character({P.PALETTE[0], P.PALETTE[2]}, {P.ITEMS[0]})
    assert sheet.color == P.PALETTE[1]
    assert sheet.item == P.ITEMS[1]


def test_pick_character_is_case_insensitive():
    sheet = P.pick_character({P.PALETTE[0].upper()}, {P.ITEMS[0].upper(), P.ITEMS[1].title()})
    assert sheet.color == P.PALETTE[1]
    assert sheet.item == P.ITEMS[2]


def test_pick_character_ignores_foreign_values():
    sheet = P.pick_character({"#000000"}, {"jetpack"})
    assert sheet.color == P.PALETTE[0]
    assert sheet.item == P.ITEMS[0]


def test_pick_character_exhaustion_cycles_by_len_used():
    colors = set(P.PALETTE)
    items = set(P.ITEMS)
    sheet = P.pick_character(colors, items)
    assert sheet.color == P.PALETTE[len(colors) % len(P.PALETTE)]
    assert sheet.item == P.ITEMS[len(items) % len(P.ITEMS)]

    more_colors = colors | {"#010101", "#020202", "#030303"}
    more_items = items | {"jetpack"}
    sheet = P.pick_character(more_colors, more_items)
    assert sheet.color == P.PALETTE[len(more_colors) % len(P.PALETTE)]
    assert sheet.item == P.ITEMS[len(more_items) % len(P.ITEMS)]


def test_pick_character_is_deterministic():
    used = ({P.PALETTE[3]}, {P.ITEMS[5]})
    assert P.pick_character(*used) == P.pick_character(*used)


# ---------------------------------------------------------------------- create


def test_create_auto_picks_distinct_colors_across_8_people(mdb):
    build_family(mdb)
    tree = P.get_tree(mdb, FAM)
    colors = [p.character.color.lower() for p in tree]
    items = [p.character.item for p in tree]
    assert len(tree) == 8
    assert len(set(colors)) == 8
    assert len(set(items)) == 8
    # Creation order follows the palette, so the first two match the sample's David and Ana.
    assert P.get_person(mdb, FAM, "grandpa").character.color == "#3f6fb5"
    assert P.get_person(mdb, FAM, "grandma").character.color == "#c0504d"


def test_auto_pick_is_per_family(mdb):
    add(mdb, "A", pid="a1", family=FAM)
    b = add(mdb, "B", pid="b1", family=OTHER)
    assert b.character.color == P.PALETTE[0]


def test_create_keeps_explicit_character(mdb):
    sheet = schemas.CharacterSheet(color="#123456", item="trumpet", build="tall", notes="hums")
    p = add(mdb, "Dad", 1962, character=sheet)
    assert p.character == sheet
    assert P.get_person(mdb, FAM, p.id).character == sheet
    # A non-palette explicit color uses up no palette slot.
    nxt = add(mdb, "Mom")
    assert nxt.character.color == P.PALETTE[0]
    assert nxt.character.item == P.ITEMS[0]


def test_create_returns_api_model_with_generated_id(mdb):
    p = add(mdb, "Rosa", 1938)
    assert isinstance(p, schemas.Person)
    assert p.family_id == FAM and p.user_id is None and p.added_by == "u_admin"
    stored = mdb.people.find_one({"_id": p.id})
    assert isinstance(stored["_id"], str) and len(stored["_id"]) == 24
    assert stored["familyId"] == FAM and "parentIds" in stored and "spouseIds" in stored
    dumped = p.model_dump(by_alias=True)
    assert dumped["id"] == p.id and "_id" not in dumped and "familyId" in dumped


def test_create_drops_storage_only_fields(mdb):
    p = add(mdb, "Rosa")
    mdb.people.update_one({"_id": p.id}, {"$set": {"embedding": [0.1], "createdAt": "x"}})
    assert P.get_person(mdb, FAM, p.id) == p


def test_create_duplicate_person_id_raises_value_error(mdb):
    add(mdb, "Dad", pid="dad")
    with pytest.raises(ValueError):
        add(mdb, "Other Dad", pid="dad")
    with pytest.raises(ValueError):
        add(mdb, "Other family dad", pid="dad", family=OTHER)


def test_create_with_user_id_and_already_claimed(mdb):
    p = P.create_person(mdb, FAM, new("Dad"), added_by="u1", user_id="u1")
    assert p.user_id == "u1"
    with pytest.raises(P.AlreadyClaimed):
        P.create_person(mdb, FAM, new("Dad again"), added_by="u1", user_id="u1")
    # Same user in another family is fine.
    q = P.create_person(mdb, OTHER, new("Dad"), added_by="u1", user_id="u1")
    assert q.family_id == OTHER


# ------------------------------------------------------------------- relations


def test_relations_must_exist(mdb):
    add(mdb, "Grandpa", pid="grandpa")
    with pytest.raises(P.InvalidRelation):
        add(mdb, "Dad", parents=["ghost"])
    with pytest.raises(P.InvalidRelation):
        add(mdb, "Mom", spouses=["ghost"])
    assert mdb.people.count_documents({}) == 1


def test_cross_family_parent_and_spouse_rejected(mdb):
    add(mdb, "B grandpa", pid="b_grandpa", family=OTHER)
    with pytest.raises(P.InvalidRelation):
        add(mdb, "Dad", parents=["b_grandpa"])
    with pytest.raises(P.InvalidRelation):
        add(mdb, "Mom", spouses=["b_grandpa"])
    a = add(mdb, "A person", pid="a1")
    with pytest.raises(P.InvalidRelation):
        P.update_person(mdb, FAM, a.id, schemas.PersonPatch(parent_ids=["b_grandpa"]))
    with pytest.raises(P.InvalidRelation):
        P.update_person(mdb, FAM, a.id, schemas.PersonPatch(spouse_ids=["b_grandpa"]))
    assert P.get_person(mdb, OTHER, "b_grandpa").spouse_ids == []


def test_at_most_two_parents_without_duplicates(mdb):
    for pid in ("p1", "p2", "p3"):
        add(mdb, pid, pid=pid)
    with pytest.raises(P.InvalidRelation):
        add(mdb, "Kid", parents=["p1", "p2", "p3"])
    with pytest.raises(P.InvalidRelation):
        add(mdb, "Kid", parents=["p1", "p1"])
    kid = add(mdb, "Kid", parents=["p1", "p2"])
    assert kid.parent_ids == ["p1", "p2"]


def test_cannot_be_parent_and_spouse_or_self(mdb):
    add(mdb, "P", pid="p")
    with pytest.raises(P.InvalidRelation):
        add(mdb, "Q", parents=["p"], spouses=["p"])
    q = add(mdb, "Q", pid="q")
    with pytest.raises(P.InvalidRelation):
        P.update_person(mdb, FAM, "q", schemas.PersonPatch(parent_ids=["q"]))
    with pytest.raises(P.InvalidRelation):
        P.update_person(mdb, FAM, "q", schemas.PersonPatch(spouse_ids=["q"]))
    P.update_person(mdb, FAM, "q", schemas.PersonPatch(spouse_ids=["p"]))
    with pytest.raises(P.InvalidRelation):
        P.update_person(mdb, FAM, "q", schemas.PersonPatch(parent_ids=["p"]))
    assert P.get_person(mdb, FAM, q.id).parent_ids == []


def test_spouse_symmetry_on_create(mdb):
    add(mdb, "Dad", pid="dad")
    add(mdb, "Mom", spouses=["dad", "dad"], pid="mom")
    assert P.get_person(mdb, FAM, "dad").spouse_ids == ["mom"]
    assert P.get_person(mdb, FAM, "mom").spouse_ids == ["dad"]


def test_spouse_symmetry_on_patch_add_and_remove(mdb):
    add(mdb, "A", pid="a")
    add(mdb, "B", pid="b")
    add(mdb, "C", pid="c")
    P.update_person(mdb, FAM, "a", schemas.PersonPatch(spouse_ids=["b"]))
    assert P.get_person(mdb, FAM, "b").spouse_ids == ["a"]

    P.update_person(mdb, FAM, "a", schemas.PersonPatch(spouse_ids=["c"]))
    assert P.get_person(mdb, FAM, "a").spouse_ids == ["c"]
    assert P.get_person(mdb, FAM, "b").spouse_ids == []
    assert P.get_person(mdb, FAM, "c").spouse_ids == ["a"]

    P.update_person(mdb, FAM, "a", schemas.PersonPatch(spouse_ids=[]))
    assert P.get_person(mdb, FAM, "c").spouse_ids == []


def test_patch_cycle_rejected(mdb):
    build_family(mdb)
    # Grandpa cannot become his grandchild's child.
    with pytest.raises(P.InvalidRelation):
        P.update_person(mdb, FAM, "grandpa", schemas.PersonPatch(parent_ids=["kid"]))
    with pytest.raises(P.InvalidRelation):
        P.update_person(mdb, FAM, "dad", schemas.PersonPatch(parent_ids=["kid"]))
    assert P.get_person(mdb, FAM, "grandpa").parent_ids == []
    # A non-descendant is fine: Uncle Joe's parent can be someone outside his line.
    add(mdb, "Joe's dad", 1930, pid="joes_dad")
    joe = P.update_person(mdb, FAM, "joe", schemas.PersonPatch(parent_ids=["joes_dad"]))
    assert joe.parent_ids == ["joes_dad"]


def test_partial_patch_leaves_other_fields(mdb):
    build_family(mdb)
    before = P.get_person(mdb, FAM, "dad")
    after = P.update_person(mdb, FAM, "dad", schemas.PersonPatch(died=2020))
    assert after.died == 2020
    assert after.model_copy(update={"died": None}) == before

    renamed = P.update_person(mdb, FAM, "dad", schemas.PersonPatch(name="Dave"))
    assert renamed.name == "Dave" and renamed.died == 2020
    assert renamed.parent_ids == before.parent_ids and renamed.spouse_ids == before.spouse_ids
    assert renamed.character == before.character

    cleared = P.update_person(mdb, FAM, "dad", schemas.PersonPatch(born=None))
    assert cleared.born is None and cleared.name == "Dave"


def test_patch_character(mdb):
    p = add(mdb, "Dad")
    sheet = schemas.CharacterSheet(color="#111111", item="cane", build="tall")
    assert P.update_person(mdb, FAM, p.id, schemas.PersonPatch(character=sheet)).character == sheet


def test_patch_missing_or_other_family_returns_none(mdb):
    add(mdb, "B", pid="b1", family=OTHER)
    assert P.update_person(mdb, FAM, "b1", schemas.PersonPatch(name="X")) is None
    assert P.update_person(mdb, FAM, "nope", schemas.PersonPatch(name="X")) is None
    assert P.get_person(mdb, OTHER, "b1").name == "B"


# ------------------------------------------------------------------------ reads


def test_reads_are_family_scoped(mdb):
    build_family(mdb)
    add(mdb, "B person", pid="b1", family=OTHER)
    assert P.get_person(mdb, FAM, "b1") is None
    assert P.get_person(mdb, OTHER, "dad") is None
    assert P.get_people(mdb, FAM, ["kid", "b1", "missing", "dad", "kid"]) == [
        P.get_person(mdb, FAM, "kid"),
        P.get_person(mdb, FAM, "dad"),
    ]
    assert P.get_people(mdb, FAM, []) == []
    assert [p.id for p in P.list_people(mdb, OTHER)] == ["b1"]
    assert len(P.list_people(mdb, FAM)) == 8
    assert P.list_people(mdb, "nobody") == []


def test_unclaimed_and_summaries(mdb):
    build_family(mdb)
    P.claim_person(mdb, FAM, "dad", "u_david")
    unclaimed = P.unclaimed_people(mdb, FAM)
    assert "dad" not in {s.id for s in unclaimed}
    assert len(unclaimed) == 7
    assert all(isinstance(s, schemas.PersonSummary) for s in unclaimed)

    summaries = P.person_summaries(mdb, FAM)
    assert [s.id for s in summaries] == EXPECTED_ORDER
    by_id = {s.id: s for s in summaries}
    assert by_id["grandpa"].color == P.get_person(mdb, FAM, "grandpa").character.color
    assert by_id["cousin"].born is None
    assert P.get_person_for_user(mdb, FAM, "u_david").id == "dad"
    assert P.get_person_for_user(mdb, OTHER, "u_david") is None


# ------------------------------------------------------------------------ claims


def test_claim_and_reclaim_is_noop(mdb):
    add(mdb, "Dad", pid="dad")
    p = P.claim_person(mdb, FAM, "dad", "u1")
    assert p.user_id == "u1"
    assert P.claim_person(mdb, FAM, "dad", "u1") == p


def test_claim_missing_or_other_family_returns_none(mdb):
    add(mdb, "B", pid="b1", family=OTHER)
    assert P.claim_person(mdb, FAM, "b1", "u1") is None
    assert P.claim_person(mdb, FAM, "ghost", "u1") is None
    assert P.get_person(mdb, OTHER, "b1").user_id is None


def test_second_claimer_gets_already_claimed(mdb):
    add(mdb, "Dad", pid="dad")
    P.claim_person(mdb, FAM, "dad", "u1")
    with pytest.raises(P.AlreadyClaimed):
        P.claim_person(mdb, FAM, "dad", "u2")
    assert P.get_person(mdb, FAM, "dad").user_id == "u1"


def test_user_cannot_hold_two_people_in_one_family(mdb):
    add(mdb, "Dad", pid="dad")
    add(mdb, "Uncle", pid="uncle")
    P.claim_person(mdb, FAM, "dad", "u1")
    with pytest.raises(P.AlreadyClaimed):
        P.claim_person(mdb, FAM, "uncle", "u1")
    assert P.get_person(mdb, FAM, "uncle").user_id is None
    # But one person per family: the same user can claim in another family.
    add(mdb, "Dad", pid="dad_b", family=OTHER)
    assert P.claim_person(mdb, OTHER, "dad_b", "u1").user_id == "u1"


def test_claim_race_loser_sees_already_claimed(mdb, monkeypatch):
    """Both claimers pass the pre-check; the atomic filter lets only one through."""
    add(mdb, "Dad", pid="dad")
    real_fau = mongomock.Collection.find_one_and_update

    def racing_fau(self, filter, update, *a, **kw):
        # Another user's claim lands between our pre-check and our update.
        self.update_one({"_id": "dad"}, {"$set": {"userId": "u_fast"}})
        return real_fau(self, filter, update, *a, **kw)

    monkeypatch.setattr(mongomock.Collection, "find_one_and_update", racing_fau)
    with pytest.raises(P.AlreadyClaimed):
        P.claim_person(mdb, FAM, "dad", "u_slow")
    assert P.get_person(mdb, FAM, "dad").user_id == "u_fast"


def test_claim_race_same_user_two_people_rolls_back(mdb, monkeypatch):
    """A concurrent claim of another person by the same user is detected and undone."""
    add(mdb, "Dad", pid="dad")
    add(mdb, "Uncle", pid="uncle")
    real_fau = mongomock.Collection.find_one_and_update

    def racing_fau(self, filter, update, *a, **kw):
        self.update_one({"_id": "uncle"}, {"$set": {"userId": "u1"}})
        return real_fau(self, filter, update, *a, **kw)

    monkeypatch.setattr(mongomock.Collection, "find_one_and_update", racing_fau)
    with pytest.raises(P.AlreadyClaimed):
        P.claim_person(mdb, FAM, "dad", "u1")
    assert P.get_person(mdb, FAM, "dad").user_id is None
    assert P.get_person_for_user(mdb, FAM, "u1").id == "uncle"


# -------------------------------------------------------------------------- tree


def test_tree_generation_order(mdb):
    build_family(mdb)
    tree = P.get_tree(mdb, FAM)
    assert [p.id for p in tree] == EXPECTED_ORDER
    assert all(isinstance(p, schemas.Person) for p in tree)
    gens = {d["_id"]: d["generation"] for d in P._tree_from_docs(list(mdb.people.find({"familyId": FAM})))}
    assert gens == EXPECTED_GEN


def test_tree_family_isolation(mdb):
    build_family(mdb)
    build_family_b = [
        {"_id": "b_root", "familyId": OTHER, "name": "B root", "parentIds": [], "spouseIds": []},
        # Bad cross-family data: a B person naming A's grandpa as a parent.
        {"_id": "b_leak", "familyId": OTHER, "name": "B leak", "parentIds": ["grandpa"], "spouseIds": []},
    ]
    for doc in build_family_b:
        mdb.people.insert_one({**doc, "character": {"color": "#000000", "build": "average"}})
    ids = [p.id for p in P.get_tree(mdb, FAM)]
    assert ids == EXPECTED_ORDER
    assert {p.id for p in P.get_tree(mdb, OTHER)} == {"b_root", "b_leak"}
    assert P.get_tree(mdb, "nobody") == []


def test_tree_pipeline_shape():
    pipeline = P._tree_pipeline("fam_x")
    assert pipeline[0] == {"$match": {"familyId": "fam_x", "parentIds": []}}
    lookup = pipeline[1]["$graphLookup"]
    assert lookup == {
        "from": "people",
        "startWith": "$_id",
        "connectFromField": "_id",
        "connectToField": "parentIds",
        "as": "descendants",
        "depthField": "depth",
        "restrictSearchWithMatch": {"familyId": "fam_x"},
    }


def _add_bad_data(db):
    """Orphans and a parent cycle that the lookup never reaches from a root."""
    cs = {"color": "#000000", "build": "average"}
    db.people.insert_many([
        {"_id": "orphan", "familyId": FAM, "name": "Orphan", "born": 1999,
         "parentIds": ["deleted"], "spouseIds": [], "character": cs},
        {"_id": "orphan_kid", "familyId": FAM, "name": "Orphan kid", "born": 2020,
         "parentIds": ["orphan"], "spouseIds": [], "character": cs},
        {"_id": "cyc1", "familyId": FAM, "name": "Cyc 1", "parentIds": ["cyc2"],
         "spouseIds": [], "character": cs},
        {"_id": "cyc2", "familyId": FAM, "name": "Cyc 2", "parentIds": ["cyc1"],
         "spouseIds": [], "character": cs},
    ])


def test_tree_includes_unreached_people_at_generation_zero(mdb):
    build_family(mdb)
    _add_bad_data(mdb)
    ids = [p.id for p in P.get_tree(mdb, FAM)]
    assert ids == [
        "grandpa", "grandma", "orphan", "orphan_kid", "cyc1", "cyc2",
        "joe", "dad", "mom", "aunt", "kid", "cousin",
    ]


def test_tree_cycle_reachable_from_root_terminates():
    cs = {"color": "#000000", "build": "average"}
    docs = [
        {"_id": "root", "familyId": FAM, "name": "Root", "parentIds": [], "spouseIds": [], "character": cs},
        {"_id": "x", "familyId": FAM, "name": "X", "parentIds": ["root", "y"], "spouseIds": [], "character": cs},
        {"_id": "y", "familyId": FAM, "name": "Y", "parentIds": ["x"], "spouseIds": [], "character": cs},
    ]
    out = P._tree_from_docs(docs)
    assert out[0]["_id"] == "root"
    assert {d["_id"] for d in out} == {"root", "x", "y"}
    assert all(d["generation"] <= len(docs) for d in out)


def test_tree_from_docs_is_pure():
    docs = [{"_id": "a", "familyId": FAM, "name": "A", "parentIds": [], "spouseIds": [],
             "character": {"color": "#000000"}}]
    snapshot = [dict(d) for d in docs]
    P._tree_from_docs(docs)
    assert docs == snapshot


def test_spouse_with_no_parents_sits_beside_partner_in_any_generation(mdb):
    build_family(mdb)
    add(mdb, "Sam's wife", 1991, spouses=["kid"], pid="kid_wife")
    add(mdb, "Great-grandkid", 2020, parents=["kid", "kid_wife"], pid="ggk")
    gens = {d["_id"]: d["generation"] for d in P._tree_from_docs(list(mdb.people.find({"familyId": FAM})))}
    assert gens["kid_wife"] == 2 and gens["ggk"] == 3
    assert [p.id for p in P.get_tree(mdb, FAM)][-1] == "ggk"


def test_aggregation_path_matches_fallback(mdb, monkeypatch):
    build_family(mdb)
    _add_bad_data(mdb)
    add(mdb, "Sam's wife", 1991, spouses=["kid"], pid="kid_wife")
    add(mdb, "Great-grandkid", 2020, parents=["kid", "kid_wife"], pid="ggk")
    docs = list(mdb.people.find({"familyId": FAM}))

    # The aggregation really runs on mongomock (no silent fallback).
    results = list(mdb.people.aggregate(P._tree_pipeline(FAM)))
    assert {r["_id"] for r in results} == {"grandpa", "grandma", "mom", "joe", "kid_wife"}
    agg = P._order_tree(docs, P._reached_from_aggregation(results))
    fallback = P._tree_from_docs(docs)
    assert [(d["_id"], d["generation"]) for d in agg] == [
        (d["_id"], d["generation"]) for d in fallback
    ]

    via_aggregate = [p.id for p in P.get_tree(mdb, FAM)]

    calls = []

    def broken(self, *a, **kw):
        calls.append(1)
        raise OperationFailure("$graphLookup not supported")

    monkeypatch.setattr(mongomock.Collection, "aggregate", broken)
    via_fallback = [p.id for p in P.get_tree(mdb, FAM)]
    assert calls, "fallback path was not exercised"
    assert via_fallback == via_aggregate == [d["_id"] for d in fallback]

    def unimplemented(self, *a, **kw):
        raise NotImplementedError

    monkeypatch.setattr(mongomock.Collection, "aggregate", unimplemented)
    assert [p.id for p in P.get_tree(mdb, FAM)] == via_aggregate


# ----------------------------------------------------------------------- real db


def test_real_db_get_tree(real_db):
    build_family(real_db)
    add(real_db, "B person", pid="b_only", family=OTHER)
    tree = P.get_tree(real_db, FAM)
    assert [p.id for p in tree] == EXPECTED_ORDER
    docs = list(real_db.people.find({"familyId": FAM}))
    assert [d["_id"] for d in P._tree_from_docs(docs)] == EXPECTED_ORDER
    assert P.get_tree(real_db, OTHER)[0].id == "b_only"
    assert P.claim_person(real_db, FAM, "dad", "u1").user_id == "u1"
    with pytest.raises(P.AlreadyClaimed):
        P.claim_person(real_db, FAM, "dad", "u2")
