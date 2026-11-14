"""seed/fake_runs.py: the unnamed stand-in is never someone dead or unborn in the memory's year.

When a run names nobody but the teller, the scripted director casts a stand-in for the
sample's second puppet (`_people_map`). That stand-in must have been alive in the memory's
year (`_present_in`), or living for an undated memory; otherwise the teller plays alone.
Named participants are untouched by this (the teller said they were there).

mongomock, no keys, no network: the run is driven through test_fake_runs.py's helpers.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import auth
import schemas
from db import families as families_db
from db import memories as memories_db
from db import people as people_db
from db import users as users_db
from db.client import get_db
from routes import tree as tree_routes
from seed.fake_runs import SAMPLE_OTHER, SAMPLE_TELLER, ScriptedRunManager, _present_in, _Run

from test_fake_runs import (  # noqa: F401 - `db` and `manager` are fixtures; pytest puts tests/data on sys.path
    assert_compiles,
    assert_well_formed,
    db,
    drive,
    fields,
    manager,
    scene_person_ids,
)

FAM = "fam_elder"
TELLER = "64e000000000000000000001"
ELDER = "64e000000000000000000002"
COUSIN = "64e000000000000000000003"
SPOUSE = "64e000000000000000000004"


# ------------------------------------------------------------------------------ _present_in


def _person(born: int | None = None, died: int | None = None) -> schemas.Person:
    return schemas.Person.model_construct(id="p", family_id="f", name="P", born=born, died=died)


@pytest.mark.parametrize(
    ("born", "died", "year", "present"),
    [
        (2021, None, 2020, False),  # born after the memory
        (1880, 1950, 2020, False),  # died before the memory
        (1930, 2020, 2020, True),  # died the same year: still there
        (2020, None, 2020, True),  # born the same year: there
        (None, None, 2020, True),  # nothing known: assumed present
        (1950, None, None, True),  # undated memory, living
        (None, None, None, True),
        (1880, 1950, None, False),  # undated memory never pulls in someone who has died
        (None, 2001, None, False),
    ],
)
def test_present_in(born, died, year, present):
    assert _present_in(_person(born, died), year) is present


# ------------------------------------------------------------------------------ runs


def _family(db, people: list[tuple[str, schemas.NewPerson]]) -> schemas.User:
    """A fresh family; the first person is the teller and has the account."""
    user = users_db.create_user(db, email="elder@x.com", password_hash=auth.hash_password("pw-12345678"), name="T")
    families_db.create_family(db, name="The Elders", creator_user_id=user.id, family_id=FAM)
    for i, (person_id, new) in enumerate(people):
        people_db.create_person(
            db, FAM, new, added_by=user.id, user_id=user.id if i == 0 else None, person_id=person_id
        )
    return user


def _run(db, mgr: ScriptedRunManager, user: schemas.User, run_fields: schemas.SubmitMemoryFields) -> schemas.Memory:
    run_id = mgr.start_run(FAM, user.id, TELLER, run_fields, [])
    events = drive(mgr, FAM, run_id, user.id)
    assert_well_formed(events)
    assert events[-1][0] == "done", events[-1]
    memory = memories_db.get_memory(db, FAM, events[-1][1].memory_id)
    assert memory is not None
    return memory


def _prose(memory: schemas.Memory) -> list[str]:
    sp, tl = memory.screenplay, memory.timeline
    assert sp is not None and tl is not None
    return [e.text for e in (sp.explanations or []) + (tl.explanations or [])]


def _assert_alone(db, memory: schemas.Memory) -> None:
    """The teller plays alone: one puppet, one participant, and the scene compiles."""
    assert memory.participant_ids == [TELLER]
    assert memory.screenplay is not None and memory.timeline is not None
    assert memory.screenplay.cast == [TELLER]
    assert scene_person_ids(memory) == {TELLER}
    puppets = {t.target for t in memory.timeline.tracks if t.target.startswith("puppet:")}
    assert puppets == {f"puppet:{TELLER}"}
    assert_compiles(db, FAM, memory)


def test_a_relative_who_died_long_before_is_never_the_stand_in(db, manager):
    user = _family(db, [
        (TELLER, schemas.NewPerson(name="Kid Elder", born=2010)),
        (ELDER, schemas.NewPerson(name="Aaron Elder", born=1880, died=1950)),
    ])
    memory = _run(db, manager, user, fields(year=2020, participant_ids=[]))

    _assert_alone(db, memory)
    assert ELDER not in memory.screenplay.cast
    assert not any("Aaron" in text for text in _prose(memory))
    assert memories_db.get_book(db, FAM, ELDER) == []

    # And over HTTP: Aaron's book is empty, the teller's has the memory.
    app = FastAPI()
    app.include_router(tree_routes.router)
    app.dependency_overrides[get_db] = lambda: db
    client = TestClient(app)
    client.cookies.set(auth.COOKIE_NAME, auth.create_session_token(user.id))
    elder_book = client.get(f"/families/{FAM}/people/{ELDER}/book")
    assert elder_book.status_code == 200, elder_book.text
    assert elder_book.json()["memories"] == []
    teller_book = client.get(f"/families/{FAM}/people/{TELLER}/book")
    assert teller_book.status_code == 200, teller_book.text
    assert [m["id"] for m in teller_book.json()["memories"]] == [memory.id]


def test_someone_born_after_the_memory_is_never_the_stand_in(db, manager):
    user = _family(db, [
        (TELLER, schemas.NewPerson(name="Kid Elder", born=1950)),
        (COUSIN, schemas.NewPerson(name="Baby Elder", born=2015)),
    ])
    memory = _run(db, manager, user, fields(year=1979, participant_ids=[]))
    _assert_alone(db, memory)


def test_the_living_spouse_is_still_preferred(db, manager):
    user = _family(db, [
        (TELLER, schemas.NewPerson(name="Kid Elder", born=1980)),
        (ELDER, schemas.NewPerson(name="Aaron Elder", born=1880, died=1950)),
        (COUSIN, schemas.NewPerson(name="Cousin Elder", born=1985)),
        (SPOUSE, schemas.NewPerson(name="Sam Elder", born=1981, spouse_ids=[TELLER])),
    ])
    assert SPOUSE in people_db.get_person(db, FAM, TELLER).spouse_ids
    memory = _run(db, manager, user, fields(year=2020, participant_ids=[]))

    assert set(memory.participant_ids) == {TELLER, SPOUSE}
    assert scene_person_ids(memory) == {TELLER, SPOUSE}
    assert not any("Aaron" in text for text in _prose(memory))
    assert_compiles(db, FAM, memory)


def test_a_spouse_who_had_died_gives_way_to_someone_alive(db, manager):
    user = _family(db, [
        (TELLER, schemas.NewPerson(name="Kid Elder", born=1930)),
        (SPOUSE, schemas.NewPerson(name="Sam Elder", born=1928, died=1990, spouse_ids=[TELLER])),
        (COUSIN, schemas.NewPerson(name="Cousin Elder", born=1960)),
    ])
    memory = _run(db, manager, user, fields(year=2005, participant_ids=[]))

    assert set(memory.participant_ids) == {TELLER, COUSIN}
    assert scene_person_ids(memory) == {TELLER, COUSIN}
    assert_compiles(db, FAM, memory)


def test_an_undated_memory_never_casts_someone_who_has_died(db, manager):
    """_people_map with no year: a dead relative is never the stand-in, a living one is.

    The submit form requires a year and db.memories refuses to save a memory without one,
    so an undated run cannot reach the save; the casting itself is checked directly.
    """
    user = _family(db, [
        (TELLER, schemas.NewPerson(name="Kid Elder", born=2010)),
        (ELDER, schemas.NewPerson(name="Aaron Elder", died=1950)),
    ])
    undated = fields(year=2020, participant_ids=[]).model_copy(update={"year": None})
    run = _Run(run_id="r", family_id=FAM, user_id=user.id, person_id=TELLER, fields=undated, photo_ids=[])
    assert manager._people_map(run, db, []) == {SAMPLE_TELLER: TELLER, SAMPLE_OTHER: None}

    people_db.create_person(db, FAM, schemas.NewPerson(name="Cousin Elder"), added_by=user.id, person_id=COUSIN)
    assert manager._people_map(run, db, []) == {SAMPLE_TELLER: TELLER, SAMPLE_OTHER: COUSIN}


def test_a_named_participant_is_cast_as_named(db, manager):
    """Only the unnamed stand-in is checked: the teller named this person, so they stay."""
    user = _family(db, [
        (TELLER, schemas.NewPerson(name="Kid Elder", born=2010)),
        (ELDER, schemas.NewPerson(name="Aaron Elder", born=1880, died=1950)),
    ])
    memory = _run(db, manager, user, fields(year=2020, participant_ids=[ELDER]))
    assert set(memory.participant_ids) == {TELLER, ELDER}
    assert ELDER in memory.screenplay.cast
    assert_compiles(db, FAM, memory)
