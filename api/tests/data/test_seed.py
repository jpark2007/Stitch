"""seed/demo.py and seed/fake.py: the demo family, idempotence, reset, and the fake-db installer."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import auth
import schemas
from db import client as db_client
from db import families as families_db
from db import media as media_db
from db import memories as memories_db
from db import people as people_db
from db import props as props_db
from db import users as users_db
from screenplay.schema import VOCABULARY
from seed import demo
from seed.demo import DEMO_ACCOUNTS, DEMO_FAMILY_ID, DEMO_PASSWORD, DEMO_PERSON_IDS, seed_demo
from seed.fake import install_fake_db

REPO_DIR = Path(__file__).resolve().parents[3]
SAMPLE = schemas.MemoryResponse.model_validate_json(
    (REPO_DIR / "web/public/samples/hymn_books.memory.json").read_text()
)
SAMPLE_RAW = json.loads((REPO_DIR / "web/public/samples/hymn_books.memory.json").read_text())


def counts(db) -> dict[str, int]:
    return {name: db[name].count_documents({}) for name in sorted(db.list_collection_names())}


@pytest.fixture
def seeded(mdb):
    db_client.ensure_indexes(mdb)
    result = seed_demo(mdb)
    return mdb, result


# ------------------------------------------------------------------- shape of the family

def test_seed_creates_the_family_and_accounts(seeded):
    mdb, result = seeded
    assert result.created is True
    assert result.family_id == DEMO_FAMILY_ID
    assert mdb.families.count_documents({}) == 1

    family = families_db.get_family(mdb, DEMO_FAMILY_ID)
    assert family is not None and family.name == demo.DEMO_FAMILY_NAME
    roles = {m.user_id: m.role for m in family.members}
    assert roles == {a.user_id: a.role for a in DEMO_ACCOUNTS}
    assert roles["demo_user_david"] == "admin"

    assert mdb.users.count_documents({}) == 3
    for account in DEMO_ACCOUNTS:
        person = people_db.get_person_for_user(mdb, DEMO_FAMILY_ID, account.user_id)
        assert person is not None and person.id == account.person_id
        summaries = families_db.list_family_summaries(mdb, account.user_id)
        assert [(s.id, s.role, s.person_id) for s in summaries] == [
            (DEMO_FAMILY_ID, account.role, account.person_id)
        ]
    assert [(e, p, pid) for e, p, pid in result.accounts] == [
        ("david@demo.stitch", DEMO_PASSWORD, "dad"),
        ("ana@demo.stitch", DEMO_PASSWORD, "mom"),
        ("sam@demo.stitch", DEMO_PASSWORD, "sam"),
    ]
    assert len(DEMO_PASSWORD) >= 8


def test_every_demo_account_logs_in(seeded):
    mdb, result = seeded
    for email, password, _person in result.accounts:
        login = users_db.get_login(mdb, email)
        assert login is not None
        _user, password_hash = login
        assert auth.verify_password(password_hash, password) is True
        assert auth.verify_password(password_hash, "wrong-password") is False


def test_tree_has_three_generations_in_order(seeded):
    mdb, result = seeded
    tree = people_db.get_tree(mdb, DEMO_FAMILY_ID)
    ids = [p.id for p in tree]
    assert len(ids) == 8 and set(ids) == set(result.person_ids)
    assert set(ids[:4]) == {"grandpa_joseph", "grandma_rosa", "grandpa_luis", "grandma_carmen"}
    assert set(ids[4:6]) == {"dad", "mom"}
    assert set(ids[6:]) == {"sam", "lily"}

    by_id = {p.id: p for p in tree}
    assert by_id["dad"].parent_ids == ["grandpa_joseph", "grandma_rosa"]
    assert by_id["mom"].parent_ids == ["grandpa_luis", "grandma_carmen"]
    assert by_id["sam"].parent_ids == ["dad", "mom"] and by_id["lily"].parent_ids == ["dad", "mom"]
    assert by_id["dad"].spouse_ids == ["mom"] and by_id["mom"].spouse_ids == ["dad"]
    assert by_id["grandpa_joseph"].spouse_ids == ["grandma_rosa"]
    assert by_id["grandpa_luis"].spouse_ids == ["grandma_carmen"]
    assert (by_id["grandpa_joseph"].born, by_id["grandpa_joseph"].died) == (1934, 2011)
    assert (by_id["grandpa_luis"].born, by_id["grandpa_luis"].died) == (1936, 2015)
    assert by_id["grandma_rosa"].born == 1938 and by_id["grandma_carmen"].born == 1940
    assert by_id["sam"].born == 1990 and by_id["lily"].born == 1993
    assert {p.user_id for p in tree if p.user_id} == {a.user_id for a in DEMO_ACCOUNTS}


def test_dad_and_mom_match_the_sample(seeded):
    mdb, _ = seeded
    for raw in SAMPLE_RAW["people"]:
        person = people_db.get_person(mdb, DEMO_FAMILY_ID, raw["id"])
        assert person is not None
        assert person.name == raw["name"] and person.born == raw["born"]
        assert person.character == schemas.CharacterSheet.model_validate(raw["character"])


def test_character_sheets_are_distinct_and_valid(seeded):
    mdb, _ = seeded
    people = people_db.list_people(mdb, DEMO_FAMILY_ID)
    colors = [p.character.color.lower() for p in people]
    assert len(set(colors)) == 8
    assert all(c.startswith("#") and len(c) == 7 for c in colors)
    assert all(p.character.build in VOCABULARY["builds"] for p in people)
    assert len({p.character.item for p in people}) == 8
    for gid in ("grandpa_joseph", "grandma_rosa", "grandpa_luis", "grandma_carmen"):
        notes = next(p for p in people if p.id == gid).character.notes
        assert notes and notes.strip()
    assert next(p for p in people if p.id == "grandpa_joseph").character.notes == "walks slowly with a cane"


# ------------------------------------------------------------------------------ memories

def test_memories_span_eras_and_generations(seeded):
    mdb, result = seeded
    summaries = memories_db.moment_summaries(mdb, DEMO_FAMILY_ID)
    assert len(summaries) >= 5
    assert {s.id for s in summaries} == set(result.memory_ids)
    assert len({s.era for s in summaries}) >= 4
    participants = {pid for s in summaries for pid in s.participant_ids}
    assert participants >= {"grandpa_joseph", "grandma_rosa", "dad", "mom", "sam", "lily"}

    extras = [m for m in memories_db.get_memories(mdb, DEMO_FAMILY_ID, result.memory_ids)
              if m.id != SAMPLE.memory.id]
    assert len(extras) >= 4
    for memory in extras:
        assert memory.screenplay is None and memory.timeline is None
        assert 1 <= len(memory.tellings) <= 2
        for telling in memory.tellings:
            assert telling.text.strip()
            assert people_db.get_person(mdb, DEMO_FAMILY_ID, telling.author_id) is not None
    bike = next(m for m in extras if m.id == "demo_sams_first_bike")
    assert [t.author_id for t in bike.tellings] == ["dad", "sam"]


def test_book_includes_the_hymn_book_scene(seeded):
    mdb, _ = seeded
    book = memories_db.get_book(mdb, DEMO_FAMILY_ID, "dad")
    years = [m.year for m in book]
    assert years == sorted(years)
    hymn = next(m for m in book if m.id == SAMPLE.memory.id)
    assert hymn.screenplay is not None and hymn.timeline is not None
    assert hymn.version == 1
    assert SAMPLE.memory.id in [m.id for m in memories_db.get_book(mdb, DEMO_FAMILY_ID, "mom")]
    for pid in ("sam", "lily", "grandpa_joseph", "grandma_carmen"):
        assert memories_db.get_book(mdb, DEMO_FAMILY_ID, pid), pid


def test_hymn_book_memory_round_trips_the_sample(seeded):
    mdb, _ = seeded
    stored = memories_db.get_memory(mdb, DEMO_FAMILY_ID, SAMPLE.memory.id)
    assert stored is not None
    want = SAMPLE.memory
    assert stored.family_id == DEMO_FAMILY_ID
    assert (stored.title, stored.year, stored.era) == (want.title, want.year, want.era)
    assert stored.participant_ids == want.participant_ids
    assert [(t.author_id, t.question, t.text, t.photo_ids) for t in stored.tellings] == [
        (t.author_id, t.question, t.text, t.photo_ids) for t in want.tellings
    ]
    assert stored.prop_ids == want.prop_ids and stored.set_ids == want.set_ids
    assert stored.screenplay == want.screenplay
    assert stored.timeline == want.timeline
    assert stored.version == want.version == 1


def test_sample_props_and_sets_are_in_the_demo_family(seeded):
    mdb, _ = seeded
    props = props_db.get_props(mdb, DEMO_FAMILY_ID, ["hymn_books"])
    sets = props_db.get_sets(mdb, DEMO_FAMILY_ID, ["church_steps_1988"])
    assert [p.id for p in props] == ["hymn_books"] and props[0].family_id == DEMO_FAMILY_ID
    assert [s.id for s in sets] == ["church_steps_1988"] and sets[0].family_id == DEMO_FAMILY_ID
    want_prop = SAMPLE.props[0].model_copy(update={"family_id": DEMO_FAMILY_ID})
    want_set = SAMPLE.sets[0].model_copy(update={"family_id": DEMO_FAMILY_ID})
    assert props[0] == want_prop and sets[0] == want_set
    assert [p.id for p in props_db.list_props(mdb, DEMO_FAMILY_ID)] == ["hymn_books"]


SAM_QUESTION = "Mom and Dad met on the church steps. What's the version of that day you grew up hearing?"
ANA_QUESTION = "What do you remember about the day Lily was born?"


def test_open_questions_for_mom_and_sam(seeded):
    mdb, _ = seeded
    sam = memories_db.open_questions(mdb, DEMO_FAMILY_ID, "sam")
    ana = memories_db.open_questions(mdb, DEMO_FAMILY_ID, "mom")
    assert [(q.text, q.memory_id, q.status) for q in sam] == [(SAM_QUESTION, demo.HYMN_MEMORY_ID, "open")]
    assert [(q.text, q.memory_id, q.status) for q in ana] == [(ANA_QUESTION, None, "open")]
    assert mdb.questions.count_documents({}) == 2


def test_every_question_with_a_memory_points_at_a_scene(seeded):
    """Regression: the real studio raises MemoryNotFound for a memory with no scene, so a
    seeded question aimed at one gave a 404 (now 409) when answered on stage."""
    mdb, _ = seeded
    questions = [q for pid in DEMO_PERSON_IDS for q in memories_db.open_questions(mdb, DEMO_FAMILY_ID, pid)]
    assert len(questions) == 2
    targeted = [q for q in questions if q.memory_id is not None]
    assert targeted  # the church-steps question adds to the hymn-book scene
    for question in targeted:
        memory = memories_db.get_memory(mdb, DEMO_FAMILY_ID, question.memory_id)
        assert memory is not None, question.memory_id
        assert memory.screenplay is not None and memory.timeline is not None, question.memory_id


def test_seed_source_has_no_raw_collection_access():
    """ARCHITECTURE section 7: only db/ talks to MongoDB. The seed calls db/ functions."""
    source = Path(demo.__file__).read_text()
    assert 'db["' not in source
    assert "db['" not in source
    assert ".delete_many(" not in source and ".find(" not in source


def test_demo_photos_are_family_scoped_pngs(seeded):
    mdb, _ = seeded
    trip = memories_db.get_memory(mdb, DEMO_FAMILY_ID, "demo_green_sedan_trip")
    assert trip is not None
    photo_ids = [pid for t in trip.tellings for pid in t.photo_ids]
    assert len(photo_ids) == 2
    for pid in photo_ids:
        media = media_db.get_media(mdb, DEMO_FAMILY_ID, pid)
        assert media is not None and media.content_type == "image/png" and media.kind == "photo"
        assert media.data.startswith(b"\x89PNG\r\n\x1a\n")
        assert media_db.get_media(mdb, "some_other_family", pid) is None


# --------------------------------------------------------------------- idempotence, reset

def _other_family(mdb) -> tuple[str, str]:
    user = users_db.create_user(mdb, email="olivia@example.com", password_hash=auth.hash_password("pw-123456"),
                                name="Olivia")
    family = families_db.create_family(mdb, name="The Others", creator_user_id=user.id)
    people_db.create_person(mdb, family.id, schemas.NewPerson(name="Olivia"), added_by=user.id, user_id=user.id)
    memory = memories_db.create_memory(mdb, family.id, title="Moving day", year=2001, participant_ids=[])
    memories_db.create_question(mdb, family.id, person_id=people_db.list_people(mdb, family.id)[0].id,
                                text="Where did you live first?")
    props_db.insert_prop(mdb, family.id, SAMPLE.props[0])
    props_db.insert_set(mdb, family.id, SAMPLE.sets[0])
    media_db.put_media(mdb, family.id, b"not really a png", content_type="image/png")
    assert memory.family_id == family.id
    return user.id, family.id


def test_seeding_twice_changes_nothing(seeded):
    mdb, _ = seeded
    before = counts(mdb)
    snapshot = {name: list(mdb[name].find({}, {"embedding": 0})) for name in mdb.list_collection_names()}

    again = seed_demo(mdb)

    assert again.created is False
    assert again.family_id == DEMO_FAMILY_ID and len(again.memory_ids) >= 5 and len(again.person_ids) == 8
    assert counts(mdb) == before
    assert {name: list(mdb[name].find({}, {"embedding": 0})) for name in mdb.list_collection_names()} == snapshot


def test_reset_rebuilds_without_touching_other_families(seeded):
    mdb, _ = seeded
    other_user, other_family = _other_family(mdb)
    before = counts(mdb)
    other_docs = {
        "users": mdb.users.find_one({"_id": other_user}),
        "families": mdb.families.find_one({"_id": other_family}),
        **{c: sorted(map(str, mdb[c].find({"familyId": other_family})))
           for c in ("people", "memories", "questions", "props", "sets")},
        "media": sorted(map(str, mdb[media_db.INLINE].find({"metadata.familyId": other_family}))),
    }

    # Simulate a demo user having edited things since the seed.
    memories_db.create_memory(mdb, DEMO_FAMILY_ID, title="Added on stage", year=2020, participant_ids=["sam"])
    people_db.create_person(mdb, DEMO_FAMILY_ID, schemas.NewPerson(name="Uncle Ray"), added_by="demo_user_david")

    result = seed_demo(mdb, reset=True)

    assert result.created is True
    assert counts(mdb) == before
    assert other_docs == {
        "users": mdb.users.find_one({"_id": other_user}),
        "families": mdb.families.find_one({"_id": other_family}),
        **{c: sorted(map(str, mdb[c].find({"familyId": other_family})))
           for c in ("people", "memories", "questions", "props", "sets")},
        "media": sorted(map(str, mdb[media_db.INLINE].find({"metadata.familyId": other_family}))),
    }
    assert people_db.get_person(mdb, DEMO_FAMILY_ID, "dad") is not None
    assert memories_db.get_memory(mdb, DEMO_FAMILY_ID, SAMPLE.memory.id).timeline is not None
    # Photos were re-created and the telling points at the new ones.
    trip = memories_db.get_memory(mdb, DEMO_FAMILY_ID, "demo_green_sedan_trip")
    for pid in (pid for t in trip.tellings for pid in t.photo_ids):
        assert media_db.media_exists(mdb, DEMO_FAMILY_ID, pid)
    assert mdb[media_db.INLINE].count_documents({"metadata.familyId": DEMO_FAMILY_ID}) == 2


def _non_demo_snapshot(mdb, other_family: str) -> dict[str, list]:
    """Every document outside the demo: other users, the other family and all its data."""
    by_id = {"sort": [("_id", 1)]}
    return {
        "users": list(mdb.users.find({"_id": {"$nin": list(demo.DEMO_USER_IDS)}}, **by_id)),
        "families": list(mdb.families.find({"_id": {"$ne": DEMO_FAMILY_ID}}, **by_id)),
        **{c: list(mdb[c].find({"familyId": other_family}, **by_id))
           for c in ("people", "memories", "questions", "props", "sets")},
        "media": list(mdb[media_db.INLINE].find({"metadata.familyId": {"$ne": DEMO_FAMILY_ID}}, **by_id)),
    }


def test_reset_twice_is_idempotent_and_leaves_other_data_alone(seeded):
    mdb, _ = seeded
    other_user, other_family = _other_family(mdb)
    users_db.create_user(mdb, email="loner@example.com", password_hash="x", name="Loner")  # in no family
    before = _non_demo_snapshot(mdb, other_family)
    assert len(before["users"]) == 2 and all(before[c] for c in before)

    def demo_ids() -> dict[str, list[str]]:  # questions get fresh ids on every build
        return {c: sorted(d["_id"] for d in mdb[c].find({"familyId": DEMO_FAMILY_ID}, {"_id": 1}))
                for c in ("people", "memories", "props", "sets")}

    first = seed_demo(mdb, reset=True)
    after_first, ids_first = counts(mdb), demo_ids()
    second = seed_demo(mdb, reset=True)

    assert first.created is True and second.created is True
    assert counts(mdb) == after_first
    assert demo_ids() == ids_first
    assert mdb.questions.count_documents({"familyId": DEMO_FAMILY_ID}) == 2
    assert mdb.users.count_documents({"_id": {"$in": list(demo.DEMO_USER_IDS)}}) == 3
    assert mdb[media_db.INLINE].count_documents({"metadata.familyId": DEMO_FAMILY_ID}) == 2
    assert _non_demo_snapshot(mdb, other_family) == before
    assert users_db.get_user(mdb, other_user) is not None


def test_interrupted_seed_is_cleaned_up_on_the_next_run(mdb):
    """The family doc is written last, so a half-finished seed is purged and rebuilt."""
    seed_demo(mdb)
    full = counts(mdb)
    mdb.families.delete_one({"_id": DEMO_FAMILY_ID})  # as if the run died before the last step

    result = seed_demo(mdb)

    assert result.created is True
    assert counts(mdb) == full


def test_seed_works_without_indexes_and_with_them(mdb):
    seed_demo(mdb)
    db_client.ensure_indexes(mdb)  # unique indexes accept the seeded data
    assert seed_demo(mdb, reset=True).created is True


# ----------------------------------------------------------------------------- fake db

@pytest.fixture
def restore_db():
    yield
    db_client.set_db(None)


def test_install_fake_db_returns_a_seeded_db(restore_db):
    db = install_fake_db()
    assert db_client.get_db() is db
    assert db.name == db_client.MONGODB_DB
    assert families_db.get_family(db, DEMO_FAMILY_ID) is not None
    assert len(people_db.get_tree(db, DEMO_FAMILY_ID)) == 8
    assert "users_email_unique" in db.users.index_information()
    login = users_db.get_login(db, "david@demo.stitch")
    assert login is not None and auth.verify_password(login[1], DEMO_PASSWORD)


def test_install_fake_db_without_seed_is_empty(restore_db):
    db = install_fake_db(seed=False)
    assert db_client.get_db() is db
    assert db.families.count_documents({}) == 0
    assert "users_email_unique" in db.users.index_information()


def test_install_fake_db_gives_a_fresh_db_each_time(restore_db):
    first = install_fake_db()
    first.users.delete_many({})
    second = install_fake_db()
    assert second is not first and db_client.get_db() is second
    assert second.users.count_documents({}) == 3


def test_no_password_hash_leaks_through_user_reads(seeded):
    mdb, _ = seeded
    for account in DEMO_ACCOUNTS:
        user = users_db.get_user(mdb, account.user_id)
        assert user is not None and "passwordHash" not in user.model_dump_json(by_alias=True)


def test_cli_seeds_and_prints_accounts(restore_db, capsys):
    import mongomock

    db = mongomock.MongoClient(tz_aware=True)["stitch_cli"]
    db_client.set_db(db)
    assert demo.main([]) == 0
    out = capsys.readouterr().out
    assert DEMO_FAMILY_ID in out and DEMO_PASSWORD in out
    for account in DEMO_ACCOUNTS:
        assert account.email in out
    assert "users_email_unique" in db.users.index_information()
    assert demo.main([]) == 0 and "already present" in capsys.readouterr().out
    assert demo.main(["--reset"]) == 0 and "seeded" in capsys.readouterr().out


# --------------------------------------------------------------------------- real MongoDB

def test_seed_and_reset_on_real_mongodb(real_db):
    """GridFS media and the real unique indexes; skips unless MONGODB_URI_TEST is set."""
    db_client.ensure_indexes(real_db)
    other = media_db.put_media(real_db, "other_family", b"keep me", content_type="text/plain", kind="other")
    assert seed_demo(real_db).created is True
    full = counts(real_db)
    assert seed_demo(real_db).created is False and counts(real_db) == full
    assert seed_demo(real_db, reset=True).created is True and counts(real_db) == full
    assert real_db[media_db.FILES].count_documents({"metadata.familyId": DEMO_FAMILY_ID}) == 2
    assert media_db.get_media(real_db, "other_family", other).data == b"keep me"
    trip = memories_db.get_memory(real_db, DEMO_FAMILY_ID, "demo_green_sedan_trip")
    for pid in (pid for t in trip.tellings for pid in t.photo_ids):
        assert media_db.get_media(real_db, DEMO_FAMILY_ID, pid) is not None
