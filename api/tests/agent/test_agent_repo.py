"""InMemoryRepo: round trips, family isolation, copy safety, sample seeding."""

from __future__ import annotations

import pytest

from agent.repo import AgentRepo, InMemoryRepo, load_sample_family
from schemas import CharacterSheet, Memory, Person, Prop, SetDef, SetLayer, Telling

FAM_A = "fam_a"
FAM_B = "fam_b"


def person(pid: str, family_id: str = FAM_A) -> Person:
    return Person(id=pid, family_id=family_id, name=pid.title(), character=CharacterSheet(color="#123456"))


def memory(mid: str, *, year: int, title: str = "A day", text: str = "We went out.", family_id: str = FAM_A,
           participants: list[str] | None = None) -> Memory:
    return Memory(
        id=mid,
        family_id=family_id,
        title=title,
        year=year,
        participant_ids=participants or ["dad"],
        tellings=[Telling(author_id="dad", text=text)],
    )


def prop(pid: str, family_id: str | None = FAM_A) -> Prop:
    return Prop(id=pid, family_id=family_id, name=pid, size="carried", art={"body": None}, color="#aa0000")


def set_def(sid: str, family_id: str | None = FAM_A) -> SetDef:
    return SetDef(id=sid, family_id=family_id, name=sid, layers=[SetLayer(art=None, depth=0, height=1.0, color="#ccc")],
                  marks={"left": [0.2, 0.0]})


@pytest.fixture
def repo() -> InMemoryRepo:
    r = InMemoryRepo()
    r.add_person(FAM_A, person("dad"))
    r.add_person(FAM_A, person("mom"))
    r.save_memory(FAM_A, memory("m1", year=1990, title="Lake trip", text="The canoe tipped over at the lake."))
    r.save_memory(FAM_A, memory("m0", year=1975, title="First car", text="A green sedan.", participants=["mom"]))
    r.save_prop(FAM_A, prop("canoe"))
    r.save_set(FAM_A, set_def("lake"))
    return r


def test_in_memory_repo_satisfies_protocol(repo: InMemoryRepo) -> None:
    assert isinstance(repo, AgentRepo)


def test_round_trips_every_entity(repo: InMemoryRepo) -> None:
    assert repo.get_person(FAM_A, "dad") == person("dad")
    assert {p.id for p in repo.list_people(FAM_A)} == {"dad", "mom"}
    assert repo.get_memory(FAM_A, "m1") == memory("m1", year=1990, title="Lake trip",
                                                  text="The canoe tipped over at the lake.")
    assert repo.get_prop(FAM_A, "canoe") == prop("canoe")
    assert [p.id for p in repo.list_props(FAM_A)] == ["canoe"]
    assert repo.get_set(FAM_A, "lake") == set_def("lake")
    assert [s.id for s in repo.list_sets(FAM_A)] == ["lake"]

    media_id = repo.put_media(FAM_A, b"\x89PNG", "image/png", kind="cutout")
    assert media_id.startswith("media/")
    assert repo.get_media(FAM_A, media_id) == (b"\x89PNG", "image/png")

    q = repo.create_question(FAM_A, person_id="mom", text="What do you remember?", memory_id="m1")
    assert (q.family_id, q.person_id, q.memory_id, q.status) == (FAM_A, "mom", "m1", "open")
    assert repo.list_questions(FAM_A) == [q]

    # Upsert by id.
    updated = repo.get_memory(FAM_A, "m1")
    assert updated is not None
    updated.version = 2
    repo.save_memory(FAM_A, updated)
    assert repo.get_memory(FAM_A, "m1").version == 2  # type: ignore[union-attr]


def test_new_id_is_deterministic_and_unique() -> None:
    r = InMemoryRepo()
    assert [r.new_id(FAM_A, "memory"), r.new_id(FAM_B, "memory"), r.new_id(FAM_A, "question")] == [
        "memory_1", "memory_2", "question_1",
    ]
    assert r.put_media(FAM_A, b"a", "audio/mpeg", kind="narration") == "media/1"
    assert r.put_media(FAM_B, b"b", "audio/mpeg", kind="narration") == "media/2"


def test_find_memories_filters_and_sorts_by_year(repo: InMemoryRepo) -> None:
    assert [m.id for m in repo.find_memories(FAM_A)] == ["m0", "m1"]
    assert [m.id for m in repo.find_memories(FAM_A, query="CANOE")] == ["m1"]  # telling text, any case
    assert [m.id for m in repo.find_memories(FAM_A, query="first")] == ["m0"]  # title
    assert [m.id for m in repo.find_memories(FAM_A, person_id="mom")] == ["m0"]
    assert repo.find_memories(FAM_A, query="nothing like this") == []
    assert [m.id for m in repo.find_memories(FAM_A, limit=1)] == ["m0"]


def test_other_family_sees_nothing(repo: InMemoryRepo) -> None:
    media_id = repo.put_media(FAM_A, b"secret", "image/png", kind="photo")
    repo.add_person(FAM_B, person("kid", FAM_B))  # family B exists but is empty of A's data

    assert repo.get_person(FAM_B, "dad") is None
    assert [p.id for p in repo.list_people(FAM_B)] == ["kid"]
    assert repo.get_memory(FAM_B, "m1") is None
    assert repo.find_memories(FAM_B) == []
    assert repo.find_memories(FAM_B, query="canoe") == []
    assert repo.get_prop(FAM_B, "canoe") is None
    assert repo.list_props(FAM_B) == []
    assert repo.get_set(FAM_B, "lake") is None
    assert repo.list_sets(FAM_B) == []
    assert repo.get_media(FAM_B, media_id) is None
    # A family that has never been written to.
    assert repo.get_person("fam_nobody", "dad") is None
    assert repo.list_people("fam_nobody") == []


def test_writes_into_wrong_family_are_rejected(repo: InMemoryRepo) -> None:
    with pytest.raises(ValueError):
        repo.save_memory(FAM_B, memory("m9", year=2000))
    with pytest.raises(ValueError):
        repo.save_prop(FAM_B, prop("canoe2", FAM_A))
    with pytest.raises(ValueError):
        repo.save_set(FAM_B, set_def("lake2", FAM_A))
    with pytest.raises(ValueError):
        repo.add_person(FAM_B, person("dad", FAM_A))
    assert repo.get_memory(FAM_B, "m9") is None


def test_stored_models_cannot_be_mutated_through_returned_or_passed_objects(repo: InMemoryRepo) -> None:
    got = repo.get_memory(FAM_A, "m1")
    assert got is not None
    got.title = "Changed"
    got.tellings[0].text = "Changed"
    got.participant_ids.append("intruder")
    assert repo.get_memory(FAM_A, "m1").title == "Lake trip"  # type: ignore[union-attr]
    assert repo.get_memory(FAM_A, "m1").tellings[0].text.startswith("The canoe")  # type: ignore[union-attr]
    assert "intruder" not in repo.get_memory(FAM_A, "m1").participant_ids  # type: ignore[union-attr]

    listed = repo.find_memories(FAM_A)
    listed[0].tellings.clear()
    assert repo.get_memory(FAM_A, listed[0].id).tellings  # type: ignore[union-attr]

    p = repo.get_person(FAM_A, "dad")
    p.character.color = "#000000"  # type: ignore[union-attr]
    assert repo.get_person(FAM_A, "dad").character.color == "#123456"  # type: ignore[union-attr]

    pr = repo.list_props(FAM_A)[0]
    pr.art["body"] = "media/evil"
    assert repo.get_prop(FAM_A, "canoe").art["body"] is None  # type: ignore[union-attr]

    s = repo.get_set(FAM_A, "lake")
    s.marks["left"][0] = 0.9  # type: ignore[union-attr]
    assert repo.get_set(FAM_A, "lake").marks["left"] == [0.2, 0.0]  # type: ignore[union-attr]

    # The object passed into a save is copied too.
    mine = memory("m2", year=2001)
    saved = repo.save_memory(FAM_A, mine)
    mine.title = "Mutated after save"
    saved.title = "Mutated return"
    assert repo.get_memory(FAM_A, "m2").title == "A day"  # type: ignore[union-attr]


def test_load_sample_family_seeds_people_props_and_sets() -> None:
    r = InMemoryRepo()
    load_sample_family(r, "fam_sample")

    assert {p.id for p in r.list_people("fam_sample")} == {"dad", "mom"}
    dad = r.get_person("fam_sample", "dad")
    assert dad is not None and dad.family_id == "fam_sample"

    books = r.get_prop("fam_sample", "hymn_books")
    assert books is not None and books.family_id == "fam_sample"
    church = r.get_set("fam_sample", "church_steps_1988")
    assert church is not None and church.family_id == "fam_sample"
    assert church.marks  # marks come through

    # The memory itself is not seeded, and nothing leaks into another family.
    assert r.find_memories("fam_sample") == []
    assert r.get_memory("fam_sample", "sample_hymn_books") is None
    assert r.get_person("fam_other", "dad") is None


def test_load_sample_family_keeps_library_props_family_less(tmp_path) -> None:
    from schemas import MemoryResponse
    from agent.repo import SAMPLE_MEMORY_PATH

    sample = MemoryResponse.model_validate_json(SAMPLE_MEMORY_PATH.read_text())
    sample.props[0].family_id = None
    path = tmp_path / "library.memory.json"
    path.write_text(sample.model_dump_json(by_alias=True))

    r = InMemoryRepo()
    load_sample_family(r, "fam_lib", path=path)
    books = r.get_prop("fam_lib", "hymn_books")
    assert books is not None and books.family_id is None
    assert [p.id for p in r.list_props("fam_lib")] == ["hymn_books"]
    assert r.get_prop("fam_other", "hymn_books") is None


# ------------------------------------------------------------------ find_props / find_sets


def described_prop(pid: str, name: str, description: str | None = None, family_id: str | None = FAM_A) -> Prop:
    return prop(pid, family_id).model_copy(update={"name": name, "description": description})


def described_set(sid: str, name: str, description: str | None = None, family_id: str | None = FAM_A) -> SetDef:
    return set_def(sid, family_id).model_copy(update={"name": name, "description": description})


@pytest.fixture
def finder() -> InMemoryRepo:
    r = InMemoryRepo()
    r.save_prop(FAM_A, described_prop("books", "hymn books", "a stack of worn blue hymn books"))
    r.save_prop(FAM_A, described_prop("trumpet", "trumpet", "an old brass trumpet in its case"))
    r.save_prop(FAM_A, described_prop("case", "trumpet case", "a battered black case"))
    r.save_prop(FAM_A, described_prop("box", "Shoe box", "cardboard box of glasses"))
    r.save_prop(FAM_A, described_prop("bus", "bus"))
    r.save_set(FAM_A, described_set("church", "church steps", "brick church front with wide stone steps"))
    r.save_set(FAM_A, described_set("kitchen", "Kitchen", "a yellow kitchen with a church calendar"))
    r.save_prop(FAM_B, described_prop("b_trumpet", "trumpet", "family B's trumpet", FAM_B))
    r.save_set(FAM_B, described_set("b_church", "church steps", None, FAM_B))
    return r


def test_find_props_stems_plurals_any_case(finder: InMemoryRepo) -> None:
    assert [p.id for p in finder.find_props(FAM_A, "Hymn Book")] == ["books"]
    assert [p.id for p in finder.find_props(FAM_A, "HYMN-BOOKS")] == ["books"]
    assert [p.id for p in finder.find_props(FAM_A, "boxes")] == ["box"]  # -es after x
    assert [p.id for p in finder.find_props(FAM_A, "shoes")] == ["box"]  # plain -s
    assert [p.id for p in finder.find_props(FAM_A, "glass")] == ["box"]  # glasses -> glass
    assert [p.id for p in finder.find_props(FAM_A, "bus")] == ["bus"]  # short words keep their s
    assert finder.find_props(FAM_A, "the of a") == []  # stopwords alone match nothing
    assert finder.find_props(FAM_A, "") == []
    assert finder.find_props(FAM_A, "spaceship") == []


def test_find_props_ranks_exact_name_then_shared_tokens_then_name(finder: InMemoryRepo) -> None:
    # 'trumpet case' and 'trumpet' (description has 'case') both share two tokens;
    # the exact name match wins.
    assert [p.id for p in finder.find_props(FAM_A, "the trumpet case")] == ["case", "trumpet"]
    # An exact name match beats a higher token overlap elsewhere.
    assert [p.id for p in finder.find_props(FAM_A, "trumpet")] == ["trumpet", "case"]
    # More shared tokens rank higher.
    finder.save_prop(FAM_A, described_prop("a_case", "a case", "brown"))
    assert [p.id for p in finder.find_props(FAM_A, "case brown")][:1] == ["a_case"]
    # Equal scores fall back to name order (case-insensitive).
    finder.save_prop(FAM_A, described_prop("z1", "Zither", "family music"))
    finder.save_prop(FAM_A, described_prop("a1", "accordion", "music"))
    assert [p.id for p in finder.find_props(FAM_A, "music")] == ["a1", "z1"]


def test_find_props_limit(finder: InMemoryRepo) -> None:
    assert [p.id for p in finder.find_props(FAM_A, "trumpet case", limit=1)] == ["case"]
    assert finder.find_props(FAM_A, "trumpet", limit=0) == []
    assert finder.find_props(FAM_A, "trumpet", limit=-3) == []
    assert len(finder.find_props(FAM_A, "trumpet case box bus books")) == 5  # default limit 5
    assert len(finder.find_props(FAM_A, "trumpet case box bus books", limit=50)) == 5


def test_find_sets(finder: InMemoryRepo) -> None:
    assert [s.id for s in finder.find_sets(FAM_A, "Church step")] == ["church", "kitchen"]
    assert [s.id for s in finder.find_sets(FAM_A, "kitchens")] == ["kitchen"]
    assert [s.id for s in finder.find_sets(FAM_A, "church", limit=1)] == ["church"]
    assert finder.find_sets(FAM_A, "church", limit=0) == []
    assert finder.find_sets(FAM_A, "spaceship") == []


def test_finders_never_cross_families(finder: InMemoryRepo) -> None:
    assert [p.id for p in finder.find_props(FAM_A, "trumpet")] == ["trumpet", "case"]
    assert [p.id for p in finder.find_props(FAM_B, "trumpet")] == ["b_trumpet"]
    assert finder.find_props(FAM_B, "hymn books") == []
    assert [s.id for s in finder.find_sets(FAM_B, "church")] == ["b_church"]
    assert finder.find_sets(FAM_B, "kitchen") == []
    assert finder.find_props("fam_nobody", "trumpet") == []
    assert finder.find_sets("fam_nobody", "church") == []


def test_finders_return_copies(finder: InMemoryRepo) -> None:
    found = finder.find_props(FAM_A, "hymn books")[0]
    found.name = "changed"
    found.art["body"] = "media/evil"
    again = finder.get_prop(FAM_A, "books")
    assert again is not None and again.name == "hymn books" and again.art["body"] is None
    s = finder.find_sets(FAM_A, "church")[0]
    s.marks["left"][0] = 0.9
    assert finder.get_set(FAM_A, "church").marks["left"] == [0.2, 0.0]  # type: ignore[union-attr]


def test_get_media_accepts_a_bare_id() -> None:
    r = InMemoryRepo()
    ref = r.put_media(FAM_A, b"x", "image/png", kind="photo")
    assert r.get_media(FAM_A, ref.removeprefix("media/")) == (b"x", "image/png")
    assert r.get_media(FAM_B, ref.removeprefix("media/")) is None
    assert r.get_media(FAM_A, "media/999") is None
