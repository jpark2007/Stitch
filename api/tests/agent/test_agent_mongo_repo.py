"""AgentRepo conformance: the same behaviour from InMemoryRepo and MongoRepo.

Every test taking the `repo` fixture runs against InMemoryRepo, MongoRepo on mongomock,
and MongoRepo on a real MongoDB (skipped unless MONGODB_URI_TEST is set). The fixtures
live here because tests/data/conftest.py does not apply under tests/agent.

Ids are global in MongoDB, so the two families below never share a person, memory,
prop or set id.
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Iterator

import mongomock
import pytest
from pymongo import MongoClient

import db.props as db_props
from agent.mongo_repo import MongoRepo
from agent.repo import SAMPLE_MEMORY_PATH, AgentRepo, InMemoryRepo, SeedableRepo, load_sample_family
from db import media as db_media
from schemas import CharacterSheet, Memory, MemoryResponse, Person, Prop, SetDef, SetLayer, Telling

FAM_A = "fam_a"
FAM_B = "fam_b"


# ------------------------------------------------------------------------- builders


def person(pid: str, family_id: str = FAM_A, **extra) -> Person:
    return Person(id=pid, family_id=family_id, name=pid.title(), character=CharacterSheet(color="#123456"), **extra)


def memory(mid: str, *, year: int, title: str = "A day", text: str = "We went out.", author: str = "dad",
           family_id: str = FAM_A, participants: list[str] | None = None) -> Memory:
    return Memory(
        id=mid,
        family_id=family_id,
        title=title,
        year=year,
        participant_ids=participants or [author],
        tellings=[Telling(author_id=author, text=text)],
    )


def prop(pid: str, family_id: str | None = FAM_A, *, name: str | None = None, description: str | None = None) -> Prop:
    return Prop(id=pid, family_id=family_id, name=name or pid, description=description, size="carried",
                art={"body": None}, color="#aa0000")


def set_def(sid: str, family_id: str | None = FAM_A, *, name: str | None = None,
            description: str | None = None) -> SetDef:
    return SetDef(id=sid, family_id=family_id, name=name or sid, description=description,
                  layers=[SetLayer(art=None, depth=0, height=1.0, color="#cccccc")], marks={"left": [0.2, 0.0]})


def sample() -> MemoryResponse:
    return MemoryResponse.model_validate_json(SAMPLE_MEMORY_PATH.read_text())


def sample_memory(family_id: str = FAM_A) -> Memory:
    return sample().memory.model_copy(update={"family_id": family_id}, deep=True)


def first_telling_only(full: Memory) -> Memory:
    """The sample as it was before mom's telling: dad's telling and narration only."""
    base = full.model_copy(deep=True)
    base.tellings = base.tellings[:1]
    base.participant_ids = ["dad", "mom"]
    assert base.screenplay is not None and base.timeline is not None
    base.screenplay.narrations = {k: v for k, v in (base.screenplay.narrations or {}).items() if k != "mom"}
    base.timeline.narrations = {k: v for k, v in (base.timeline.narrations or {}).items() if k != "mom"}
    base.version = 1
    return base


def without_created_at(m: Memory) -> Memory:
    """The db stamps created_at on tellings; compare everything else."""
    return m.model_copy(update={"tellings": [t.model_copy(update={"created_at": None}) for t in m.tellings]})


# ------------------------------------------------------------------------- fixtures


@pytest.fixture
def empty_library(tmp_path, monkeypatch: pytest.MonkeyPatch):
    """Point the built-in prop library at an empty dir, so only saved props can match."""
    lib = tmp_path / "library"
    lib.mkdir()
    monkeypatch.setattr(db_props, "LIBRARY_DIR", lib)
    return lib


@pytest.fixture
def mock_db() -> Iterator:
    client = mongomock.MongoClient(tz_aware=True)
    yield client["t"]
    client.close()


@pytest.fixture
def real_db() -> Iterator:
    """A throwaway database on a real MongoDB, dropped afterwards. Skips without MONGODB_URI_TEST."""
    uri = os.environ.get("MONGODB_URI_TEST")
    if not uri:
        pytest.skip("MONGODB_URI_TEST is not set")
    client = MongoClient(uri, tz_aware=True, serverSelectionTimeoutMS=3000)
    name = f"stitch_agent_test_{uuid.uuid4().hex[:8]}"
    try:
        yield client[name]
    finally:
        client.drop_database(name)
        client.close()


@pytest.fixture(params=["memory", "mongomock", "real_mongo"])
def repo(request: pytest.FixtureRequest, empty_library) -> SeedableRepo:
    if request.param == "memory":
        return InMemoryRepo()
    if request.param == "mongomock":
        return MongoRepo(request.getfixturevalue("mock_db"))
    return MongoRepo(request.getfixturevalue("real_db"))


@pytest.fixture
def seeded(repo: SeedableRepo) -> SeedableRepo:
    """Family A: dad, mom, kid, two memories, a prop and a set. Family B: one person."""
    repo.add_person(FAM_A, person("dad"))
    repo.add_person(FAM_A, person("mom"))
    repo.add_person(FAM_A, person("kid"))
    repo.add_person(FAM_B, person("bob", FAM_B))
    repo.save_memory(FAM_A, memory("m1", year=1990, title="Lake trip", text="The canoe tipped over at the lake."))
    repo.save_memory(FAM_A, memory("m0", year=1975, title="First car", text="A green sedan.", author="mom"))
    repo.save_prop(FAM_A, prop("canoe", description="a red wooden canoe"))
    repo.save_set(FAM_A, set_def("lake", description="a still lake with pines"))
    return repo


# ---------------------------------------------------------------------- conformance


def test_satisfies_protocol(repo: SeedableRepo) -> None:
    assert isinstance(repo, AgentRepo)
    assert isinstance(repo, SeedableRepo)


def test_mongo_repo_satisfies_protocol(mock_db) -> None:
    assert isinstance(MongoRepo(mock_db), AgentRepo)


def test_people(seeded: SeedableRepo) -> None:
    assert seeded.get_person(FAM_A, "dad") == person("dad")
    assert {p.id for p in seeded.list_people(FAM_A)} == {"dad", "mom", "kid"}
    assert seeded.get_person(FAM_A, "nobody") is None


def test_new_id_is_prefixed_and_unique(repo: SeedableRepo) -> None:
    ids = [repo.new_id(FAM_A, "memory") for _ in range(3)] + [repo.new_id(FAM_B, "memory")]
    assert all(i.startswith("memory_") for i in ids)
    assert len(set(ids)) == len(ids)
    assert repo.new_id(FAM_A, "question").startswith("question_")


def test_hymn_books_memory_round_trips(repo: SeedableRepo) -> None:
    load_sample_family(repo, FAM_A)
    full = sample_memory()
    assert full.screenplay is not None and full.timeline is not None

    saved = repo.save_memory(FAM_A, full)
    got = repo.get_memory(FAM_A, full.id)
    assert got is not None
    assert got.screenplay == full.screenplay
    assert got.timeline == full.timeline
    assert without_created_at(got) == without_created_at(full)
    assert without_created_at(saved) == without_created_at(full)
    assert (got.version, got.era, got.prop_ids, got.set_ids) == (1, "1980s", ["hymn_books"], ["church_steps_1988"])
    # It validates again after reading, as the API would send it.
    Memory.model_validate(got.model_dump(mode="json", by_alias=True))


def test_second_telling_bumps_version_by_one(repo: SeedableRepo) -> None:
    load_sample_family(repo, FAM_A)
    full = sample_memory()
    base = repo.save_memory(FAM_A, first_telling_only(full))
    assert base.version == 1 and len(base.tellings) == 1
    assert "mom" not in (base.timeline.narrations or {})  # type: ignore[union-attr]

    # What tools/family.py does for a second telling: append it, add the narrations, version + 1.
    stored = repo.get_memory(FAM_A, full.id)
    assert stored is not None
    second = stored.model_copy(
        update={
            "tellings": [*stored.tellings, full.tellings[1]],
            "screenplay": full.screenplay,
            "timeline": full.timeline,
            "version": stored.version + 1,
        },
        deep=True,
    )
    saved = repo.save_memory(FAM_A, second)
    got = repo.get_memory(FAM_A, full.id)
    assert got is not None
    assert saved.version == got.version == 2
    assert [t.author_id for t in got.tellings] == ["dad", "mom"]
    assert got.tellings[1].text == full.tellings[1].text
    assert got.timeline == full.timeline and got.screenplay == full.screenplay
    assert set((got.timeline.narrations or {})) == {"dad", "mom"}  # type: ignore[union-attr]


def test_linking_participants_keeps_the_version(repo: SeedableRepo) -> None:
    load_sample_family(repo, FAM_A)
    repo.add_person(FAM_A, person("kid"))
    repo.save_memory(FAM_A, sample_memory())

    stored = repo.get_memory(FAM_A, "sample_hymn_books")
    assert stored is not None
    # What the link_participants tool does: participants change, nothing else.
    linked = stored.model_copy(update={"participant_ids": [*stored.participant_ids, "kid"]})
    saved = repo.save_memory(FAM_A, linked)
    got = repo.get_memory(FAM_A, "sample_hymn_books")
    assert got is not None
    assert saved.version == got.version == 1
    assert got.participant_ids == ["dad", "mom", "kid"]
    assert got.timeline == stored.timeline

    # Saving it again unchanged changes nothing either.
    assert repo.save_memory(FAM_A, got).version == 1


def test_find_memories(seeded: SeedableRepo) -> None:
    assert [m.id for m in seeded.find_memories(FAM_A)] == ["m0", "m1"]  # oldest first
    assert [m.id for m in seeded.find_memories(FAM_A, query="CANOE")] == ["m1"]  # telling, any case
    assert [m.id for m in seeded.find_memories(FAM_A, query="first")] == ["m0"]  # title
    assert [m.id for m in seeded.find_memories(FAM_A, person_id="mom")] == ["m0"]
    assert [m.id for m in seeded.find_memories(FAM_A, person_id="mom", query="canoe")] == []
    assert seeded.find_memories(FAM_A, query="nothing like this") == []
    assert [m.id for m in seeded.find_memories(FAM_A, limit=1)] == ["m0"]
    assert seeded.find_memories(FAM_A, limit=0) == []


def test_questions(seeded: SeedableRepo) -> None:
    q = seeded.create_question(FAM_A, person_id="mom", text="What do you remember?", memory_id="m1")
    assert (q.family_id, q.person_id, q.text, q.memory_id, q.status) == (
        FAM_A, "mom", "What do you remember?", "m1", "open",
    )
    assert q.id
    assert seeded.list_questions(FAM_A) == [q]  # type: ignore[attr-defined]
    assert seeded.list_questions(FAM_B) == []  # type: ignore[attr-defined]


def open_ids(repo: SeedableRepo, family_id: str = FAM_A) -> list[str]:
    return [q.id for q in repo.list_questions(family_id)]  # type: ignore[attr-defined]


def test_new_telling_closes_the_questions_it_answers(seeded: SeedableRepo) -> None:
    asked = "What do you remember?"
    on_m1 = seeded.create_question(FAM_A, person_id="mom", text="The lake?", memory_id="m1")
    other_memory = seeded.create_question(FAM_A, person_id="mom", text=asked, memory_id="m0")
    free = seeded.create_question(FAM_A, person_id="mom", text=asked, memory_id=None)
    dads = seeded.create_question(FAM_A, person_id="dad", text=asked, memory_id="m1")
    assert open_ids(seeded) == [on_m1.id, other_memory.id, free.id, dads.id]  # oldest first

    stored = seeded.get_memory(FAM_A, "m1")
    assert stored is not None
    stored.tellings.append(Telling(author_id="mom", text="I watched from the shore.", question=f"  {asked} "))
    stored.participant_ids.append("mom")
    seeded.save_memory(FAM_A, stored)

    # Bound to m1, or the free question she answered: closed. Same text but bound to
    # another memory, or someone else's question: still open.
    assert open_ids(seeded) == [other_memory.id, dads.id]
    assert all(q.status == "open" for q in seeded.list_questions(FAM_A))  # type: ignore[attr-defined]

    # Saving again with no new telling closes nothing, even a fresh question on m1.
    later = seeded.create_question(FAM_A, person_id="mom", text="Anything else?", memory_id="m1")
    again = seeded.get_memory(FAM_A, "m1")
    assert again is not None
    seeded.save_memory(FAM_A, again)
    assert open_ids(seeded) == [other_memory.id, dads.id, later.id]


def test_new_memory_with_a_telling_closes_the_authors_questions(seeded: SeedableRepo) -> None:
    asked = "Tell us about the fair."
    answered = seeded.create_question(FAM_A, person_id="kid", text=asked, memory_id=None)
    unrelated = seeded.create_question(FAM_A, person_id="kid", text="What was your first pet?", memory_id=None)
    on_m1 = seeded.create_question(FAM_A, person_id="kid", text=asked, memory_id="m1")
    moms = seeded.create_question(FAM_A, person_id="mom", text=asked, memory_id=None)

    fair = memory("m5", year=2001, title="The fair", text="A goldfish in a bag.", author="kid")
    fair.tellings[0].question = asked
    seeded.save_memory(FAM_A, fair)
    assert open_ids(seeded) == [unrelated.id, on_m1.id, moms.id]
    assert answered.id not in open_ids(seeded)


def test_props_save_get_list_and_replace(seeded: SeedableRepo) -> None:
    assert seeded.get_prop(FAM_A, "canoe") == prop("canoe", description="a red wooden canoe")
    assert [p.id for p in seeded.list_props(FAM_A)] == ["canoe"]

    renamed = prop("canoe", name="Grandpa's canoe", description="a red wooden canoe")
    assert seeded.save_prop(FAM_A, renamed) == renamed
    assert seeded.get_prop(FAM_A, "canoe") == renamed
    assert [p.id for p in seeded.list_props(FAM_A)] == ["canoe"]  # replaced, not duplicated

    seeded.save_prop(FAM_A, prop("hymn_books", name="hymn books", description="a stack of worn blue hymn books"))
    assert {p.id for p in seeded.list_props(FAM_A)} == {"canoe", "hymn_books"}


def test_sets_save_get_list_and_replace(seeded: SeedableRepo) -> None:
    assert seeded.get_set(FAM_A, "lake") == set_def("lake", description="a still lake with pines")
    assert [s.id for s in seeded.list_sets(FAM_A)] == ["lake"]
    moved = set_def("lake", name="Lake Tahoe", description="a still lake with pines")
    assert seeded.save_set(FAM_A, moved) == moved
    assert seeded.get_set(FAM_A, "lake") == moved
    assert [s.id for s in seeded.list_sets(FAM_A)] == ["lake"]


def test_find_props(seeded: SeedableRepo) -> None:
    seeded.save_prop(FAM_A, prop("hymn_books", name="hymn books", description="a stack of worn blue hymn books"))
    seeded.save_prop(FAM_A, prop("trumpet", name="trumpet", description="an old brass trumpet in a case"))
    seeded.save_prop(FAM_A, prop("case", name="trumpet case", description="a battered black case"))

    assert [p.id for p in seeded.find_props(FAM_A, "Hymn Book")] == ["hymn_books"]  # plural stem, any case
    assert seeded.find_props(FAM_A, "hymn book")[0] == prop(
        "hymn_books", name="hymn books", description="a stack of worn blue hymn books"
    )
    assert seeded.find_props(FAM_A, "trumpet case")[0].id == "case"  # exact name first
    assert {p.id for p in seeded.find_props(FAM_A, "trumpet case")} == {"case", "trumpet"}
    assert len(seeded.find_props(FAM_A, "trumpet case", limit=1)) == 1
    assert seeded.find_props(FAM_A, "trumpet", limit=0) == []
    assert seeded.find_props(FAM_A, "spaceship") == []


def test_find_sets(seeded: SeedableRepo) -> None:
    seeded.save_set(FAM_A, set_def("church", name="church steps", description="brick church front with stone steps"))
    seeded.save_set(FAM_A, set_def("kitchen", name="kitchen", description="a yellow kitchen"))
    assert [s.id for s in seeded.find_sets(FAM_A, "the church step")] == ["church"]
    assert [s.id for s in seeded.find_sets(FAM_A, "Kitchens")] == ["kitchen"]
    assert seeded.find_sets(FAM_A, "lakes")[0].id == "lake"
    assert len(seeded.find_sets(FAM_A, "church kitchen lake", limit=2)) == 2
    assert seeded.find_sets(FAM_A, "church", limit=0) == []
    assert seeded.find_sets(FAM_A, "spaceship") == []


def test_media_round_trip(seeded: SeedableRepo) -> None:
    ref = seeded.put_media(FAM_A, b"\x89PNG-bytes", "image/png", kind="cutout")
    assert ref.startswith("media/")
    assert seeded.get_media(FAM_A, ref) == (b"\x89PNG-bytes", "image/png")
    bare = ref.removeprefix("media/")
    assert seeded.get_media(FAM_A, bare) == (b"\x89PNG-bytes", "image/png")

    # A kind the db does not list (narration) still stores and reads back.
    audio = seeded.put_media(FAM_A, b"RIFF", "audio/wav", kind="narration")
    assert seeded.get_media(FAM_A, audio) == (b"RIFF", "audio/wav")
    assert audio != ref

    for garbage in ["", "media/", "media/nope", "nope", "media/../etc", "0" * 24, "media/" + "f" * 24]:
        assert seeded.get_media(FAM_A, garbage) is None


def test_other_family_sees_nothing(seeded: SeedableRepo) -> None:
    ref = seeded.put_media(FAM_A, b"secret", "image/png", kind="photo")
    seeded.create_question(FAM_A, person_id="mom", text="What do you remember?", memory_id="m1")

    assert seeded.get_person(FAM_B, "dad") is None
    assert [p.id for p in seeded.list_people(FAM_B)] == ["bob"]
    assert seeded.get_memory(FAM_B, "m1") is None
    assert seeded.find_memories(FAM_B) == []
    assert seeded.find_memories(FAM_B, query="canoe") == []
    assert seeded.find_memories(FAM_B, person_id="dad") == []
    assert seeded.get_prop(FAM_B, "canoe") is None
    assert seeded.list_props(FAM_B) == []
    assert seeded.find_props(FAM_B, "canoe") == []
    assert seeded.get_set(FAM_B, "lake") is None
    assert seeded.list_sets(FAM_B) == []
    assert seeded.find_sets(FAM_B, "lake") == []
    assert seeded.get_media(FAM_B, ref) is None
    assert seeded.get_media(FAM_B, ref.removeprefix("media/")) is None
    assert seeded.list_questions(FAM_B) == []  # type: ignore[attr-defined]

    # A family that has never been written to.
    for fam in ["fam_nobody", ""]:
        assert seeded.get_person(fam, "dad") is None
        assert seeded.list_people(fam) == []
        assert seeded.get_memory(fam, "m1") is None
        assert seeded.find_memories(fam, query="canoe") == []
        assert seeded.list_props(fam) == [] and seeded.find_props(fam, "canoe") == []
        assert seeded.list_sets(fam) == [] and seeded.find_sets(fam, "lake") == []
        assert seeded.get_media(fam, ref) is None


def test_writes_into_wrong_family_are_rejected(seeded: SeedableRepo) -> None:
    with pytest.raises(ValueError):
        seeded.save_memory(FAM_B, memory("m9", year=2000))
    with pytest.raises(ValueError):
        seeded.save_prop(FAM_B, prop("canoe2", FAM_A))
    with pytest.raises(ValueError):
        seeded.save_set(FAM_B, set_def("lake2", FAM_A))
    with pytest.raises(ValueError):
        seeded.add_person(FAM_B, person("ann", FAM_A))
    assert seeded.get_memory(FAM_B, "m9") is None
    assert seeded.get_memory(FAM_A, "m9") is None
    assert seeded.get_prop(FAM_B, "canoe2") is None


def test_returned_models_are_copies(seeded: SeedableRepo) -> None:
    got = seeded.get_memory(FAM_A, "m1")
    assert got is not None
    got.title = "Changed"
    got.tellings[0].text = "Changed"
    assert seeded.get_memory(FAM_A, "m1").title == "Lake trip"  # type: ignore[union-attr]
    found = seeded.find_props(FAM_A, "canoe")[0]
    found.art["body"] = "media/evil"
    assert seeded.get_prop(FAM_A, "canoe").art["body"] is None  # type: ignore[union-attr]


# -------------------------------------------------------------------- MongoRepo only


@pytest.fixture
def mongo(mock_db, empty_library) -> MongoRepo:
    return MongoRepo(mock_db)


def test_mongo_new_id_is_object_id_based(mongo: MongoRepo) -> None:
    from bson import ObjectId

    new = mongo.new_id(FAM_A, "prop")
    assert new.startswith("prop_") and ObjectId.is_valid(new.removeprefix("prop_"))


def test_mongo_scene_change_bumps_version_and_title_counts(mongo: MongoRepo) -> None:
    load_sample_family(mongo, FAM_A)
    mongo.save_memory(FAM_A, sample_memory())
    stored = mongo.get_memory(FAM_A, "sample_hymn_books")
    assert stored is not None
    # The version on the model is not what decides; the db does.
    assert mongo.save_memory(FAM_A, stored.model_copy(update={"version": 7})).version == 1
    assert mongo.save_memory(FAM_A, stored.model_copy(update={"title": "Church steps"})).version == 2
    assert mongo.get_memory(FAM_A, "sample_hymn_books").title == "Church steps"  # type: ignore[union-attr]


def test_mongo_new_memory_without_scene_stays_sceneless(mongo: MongoRepo) -> None:
    mongo.add_person(FAM_A, person("dad"))
    mongo.add_person(FAM_A, person("mom"))
    two = memory("m2", year=2001, participants=["dad", "mom"])
    two.tellings.append(Telling(author_id="mom", text="I remember it differently."))
    saved = mongo.save_memory(FAM_A, two)
    assert saved.version == 1 and saved.screenplay is None and saved.timeline is None
    assert [t.author_id for t in saved.tellings] == ["dad", "mom"]
    assert all(t.created_at is not None for t in saved.tellings)


def test_mongo_editing_own_telling_replaces_it(mongo: MongoRepo) -> None:
    mongo.add_person(FAM_A, person("dad"))
    mongo.save_memory(FAM_A, memory("m3", year=1999, text="First draft."))
    stored = mongo.get_memory(FAM_A, "m3")
    assert stored is not None
    edited = stored.model_copy(update={"tellings": [Telling(author_id="dad", text="Second draft.")]})
    got = mongo.save_memory(FAM_A, edited)
    assert [t.text for t in got.tellings] == ["Second draft."]


def test_mongo_unknown_participant_is_rejected(mongo: MongoRepo) -> None:
    mongo.add_person(FAM_A, person("dad"))
    with pytest.raises(ValueError):
        mongo.save_memory(FAM_A, memory("m4", year=2000, participants=["dad", "ghost"]))


def test_mongo_seeds_spouses_both_ways(mongo: MongoRepo) -> None:
    load_sample_family(mongo, FAM_A)
    assert mongo.get_person(FAM_A, "dad").spouse_ids == ["mom"]  # type: ignore[union-attr]
    assert mongo.get_person(FAM_A, "mom").spouse_ids == ["dad"]  # type: ignore[union-attr]
    assert mongo.get_prop(FAM_A, "hymn_books").family_id == FAM_A  # type: ignore[union-attr]
    assert mongo.get_set(FAM_A, "church_steps_1988") is not None


def test_mongo_unknown_media_kind_is_stored_as_other(mongo: MongoRepo) -> None:
    ref = mongo.put_media(FAM_A, b"RIFF", "audio/wav", kind="narration")
    stored = db_media.get_media(mongo.db, FAM_A, ref)
    assert stored is not None and stored.kind == "other"
    cut = mongo.put_media(FAM_A, b"PNG", "image/png", kind="cutout")
    assert db_media.get_media(mongo.db, FAM_A, cut).kind == "cutout"  # type: ignore[union-attr]


def test_mongo_library_props(mongo: MongoRepo, empty_library) -> None:
    (empty_library / "accordion.json").write_text(json.dumps({
        "name": "accordion", "description": "a red piano accordion", "size": "carried",
        "art": {"body": None}, "color": "#aa2222",
    }))
    db_props.library_props.cache_clear()  # type: ignore[attr-defined]

    library = mongo.get_prop(FAM_A, "accordion")
    assert library is not None and library.family_id is None
    assert mongo.get_prop(FAM_B, "accordion") is not None  # the library is shared by every family
    assert [p.id for p in mongo.find_props(FAM_A, "accordions")] == ["accordion"]
    assert mongo.list_props(FAM_A) == []  # saved props only

    # Saving a library prop makes it the family's own copy.
    copy = mongo.save_prop(FAM_A, library.model_copy(update={"name": "Grandma's accordion"}))
    assert copy.family_id == FAM_A and copy.name == "Grandma's accordion"
    assert [p.id for p in mongo.list_props(FAM_A)] == ["accordion"]
    assert mongo.get_prop(FAM_A, "accordion").name == "Grandma's accordion"  # type: ignore[union-attr]
    assert mongo.get_prop(FAM_B, "accordion").name == "accordion"  # type: ignore[union-attr]
    db_props.library_props.cache_clear()  # type: ignore[attr-defined]


# ------------------------------------------------------------- books: end-to-end parity
#
# The weekend demo checked books only through InMemoryRepo.find_memories. Here the same
# runs go through the whole studio (agent.wiring over the fake studio, the real compiler)
# on InMemoryRepo and on MongoRepo, and every participant's book must come out the same.

PARITY_FAMILY = "fam_parity"
PARITY_STORY_TEXT = (
    "We carried the picnic basket down to the lake dock. "
    "I nearly dropped it at the end of the boards. "
    "He took it from me and laughed."
)
PARITY_SECOND_TEXT = "She was sure the basket would go in the lake. I took it before it did, and we both laughed."
PARITY_SOLO_TEXT = "I rowed out alone at dawn with the picnic basket and ate breakfast on the water."
PARITY_WAIT = 25.0


def _drive_run(manager, user_id: str, author_id: str, fields) -> str:
    """Start a run, keep every prop at each review pause, and return the saved memory id."""
    import asyncio

    import schemas

    run_id = manager.start_run(PARITY_FAMILY, user_id, author_id, fields, [])
    for _ in range(5):
        status = manager.wait(PARITY_FAMILY, run_id, PARITY_WAIT)
        assert status != "running", [n for n, _ in manager.events(PARITY_FAMILY, run_id)]
        if status != "waiting":
            break
        events = manager.events(PARITY_FAMILY, run_id)
        found = [p for n, p in events if n == "props_found"]
        assert found, [n for n, _ in events]
        edits = [schemas.PropEdit(id=c.id, action="keep") for c in found[-1].props]
        manager.answer_run(PARITY_FAMILY, run_id, user_id, schemas.AnswerRequest(kind="props", props=edits))

    async def replay() -> list:
        return [e async for e in manager.stream_run(PARITY_FAMILY, run_id)]

    events = asyncio.run(asyncio.wait_for(replay(), PARITY_WAIT))
    names = [n for n, _ in events]
    assert names[-1] == "done" and "error" not in names, [p for n, p in events if n == "error"]
    return events[-1][1].memory_id


def _tell_the_parity_story(repo: SeedableRepo) -> list[str]:
    """Mom tells a memory with dad in it; dad adds his telling to it; dad tells one alone, earlier.

    Returns the memory ids in the order the runs saved them.
    """
    import schemas
    from agent import wiring
    from agent.fake_studio import FakeStory, fake_studio_model

    load_sample_family(repo, PARITY_FAMILY)
    story = FakeStory(place="lake dock", prop="picnic basket", prop_size="carried", compile_failures=0)
    manager = wiring.make_manager(wiring.fake_services(model=fake_studio_model(story), repo=repo))
    try:
        both = _drive_run(manager, "user_mom", "mom",
                          schemas.SubmitMemoryFields(text=PARITY_STORY_TEXT, year=1994, participant_ids=["dad"]))
        second = _drive_run(manager, "user_dad", "dad",
                            schemas.SubmitMemoryFields(text=PARITY_SECOND_TEXT, year=1994, memory_id=both))
        assert second == both
        solo = _drive_run(manager, "user_dad", "dad", schemas.SubmitMemoryFields(text=PARITY_SOLO_TEXT, year=1990))
    finally:
        manager.shutdown(wait=True)
    return [both, solo]


def _canonical(memories: list[Memory], ids: list[str]) -> list[dict]:
    """The memories as the API sends them, with what each store names its own way made equal:
    memory ids (by the order the runs saved them), media ids, and the db's created_at stamps."""
    import re

    rename = {mid: f"memory#{i}" for i, mid in enumerate(ids)}
    out = []
    for m in memories:
        text = m.model_dump_json(by_alias=True)
        for old, new in rename.items():
            text = text.replace(f'"{old}"', f'"{new}"')
        text = re.sub(r'"media/[^"]+"', '"media/*"', text)
        data = json.loads(text)
        for telling in data.get("tellings", []):
            telling.pop("createdAt", None)
        out.append(data)
    return out


@pytest.mark.parametrize("mongo_db", ["mock_db", "real_db"])
def test_books_are_the_same_on_both_repos_after_a_memory_and_a_second_telling(
    request: pytest.FixtureRequest, mongo_db: str, empty_library
) -> None:
    """On mongomock always; on a real MongoDB too when MONGODB_URI_TEST is set (else skipped)."""
    from db import memories as db_memories

    database = request.getfixturevalue(mongo_db)
    memory_repo = InMemoryRepo()
    mongo_repo = MongoRepo(database)
    ids = {"memory": _tell_the_parity_story(memory_repo), "mongo": _tell_the_parity_story(mongo_repo)}
    repos: dict[str, SeedableRepo] = {"memory": memory_repo, "mongo": mongo_repo}

    def book(name: str, person_id: str) -> list[dict]:
        found = repos[name].find_memories(PARITY_FAMILY, person_id=person_id, limit=100)
        return _canonical(found, ids[name])

    for person_id in ("mom", "dad"):
        expected = book("memory", person_id)
        assert book("mongo", person_id) == expected, person_id
        # The API's book route reads db.memories.get_book directly: the same again.
        assert _canonical(db_memories.get_book(database, PARITY_FAMILY, person_id), ids["mongo"]) == expected

    # The books themselves: mom is in one memory, dad in both, oldest first.
    assert [m["id"] for m in book("memory", "mom")] == ["memory#0"]
    assert [m["id"] for m in book("memory", "dad")] == ["memory#1", "memory#0"]
    told = book("memory", "dad")[1]
    assert [t["authorId"] for t in told["tellings"]] == ["mom", "dad"]
    assert told["version"] == 2 and set(told["timeline"]["narrations"]) == {"mom", "dad"}
    assert told["participantIds"] == ["mom", "dad"]

    # The whole family, and a search, agree too.
    everything = {name: _canonical(r.find_memories(PARITY_FAMILY, limit=100), ids[name]) for name, r in repos.items()}
    assert everything["mongo"] == everything["memory"]
    searched = {name: [m.id for m in r.find_memories(PARITY_FAMILY, query="picnic basket")] for name, r in repos.items()}
    assert [ids["mongo"].index(i) for i in searched["mongo"]] == [ids["memory"].index(i) for i in searched["memory"]]

    # Whatever questions the runs left open, both stores left the same ones.
    open_questions = {
        name: [(q.person_id, q.memory_id and ids[name].index(q.memory_id), q.text) for q in r.list_questions(PARITY_FAMILY)]  # type: ignore[attr-defined]
        for name, r in repos.items()
    }
    assert open_questions["mongo"] == open_questions["memory"]


def test_mongo_find_props_uses_the_embedder_when_set(mongo: MongoRepo, monkeypatch: pytest.MonkeyPatch) -> None:
    import db.search as db_search

    seen: list[tuple[str, list[float] | None]] = []

    def fake_find(db, family_id, text, *, vector=None, limit=5):
        seen.append((text, vector))
        return []

    monkeypatch.setattr(db_props, "find_props", fake_find)
    monkeypatch.setattr(db_props, "find_sets", fake_find)
    mongo.find_props(FAM_A, "canoe", limit=3)
    db_search.set_embedder(lambda text: [1.0, 0.0])
    try:
        mongo.find_sets(FAM_A, "lake")
    finally:
        db_search.set_embedder(None)
    assert seen == [("canoe", None), ("lake", [1.0, 0.0])]
