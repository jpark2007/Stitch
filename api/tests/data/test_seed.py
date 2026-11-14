"""seed/demo.py and seed/fake.py: the demo family, idempotence, reset, and the fake-db installer."""

from __future__ import annotations

import json
import shutil
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
from screenplay import compiler
from screenplay.schema import VOCABULARY, Screenplay
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
    assert len({p.character.build for p in people}) >= 3, "builds vary"


def test_signature_hats_are_props_of_the_family(seeded):
    mdb, _ = seeded
    worn = {p.id: p.character.item_prop_id for p in people_db.list_people(mdb, DEMO_FAMILY_ID)
            if p.character.item_prop_id}
    assert worn == {"grandpa_luis": "luis_straw_hat", "sam": "sam_cap"}
    for prop_id in worn.values():
        prop = props_db.get_prop(mdb, DEMO_FAMILY_ID, prop_id)
        assert prop is not None and prop.size in ("tiny", "hand", "carried")


# ------------------------------------------------------------------------------ memories

def test_memories_span_eras_and_generations(seeded):
    mdb, result = seeded
    summaries = memories_db.moment_summaries(mdb, DEMO_FAMILY_ID)
    assert len(summaries) >= 7
    assert {s.id for s in summaries} == set(result.memory_ids) == set(demo.DEMO_MEMORY_IDS)
    assert len({s.era for s in summaries}) >= 5
    participants = {pid for s in summaries for pid in s.participant_ids}
    assert participants == set(DEMO_PERSON_IDS)

    memories = memories_db.get_memories(mdb, DEMO_FAMILY_ID, result.memory_ids)
    assert len(memories) == len(result.memory_ids)
    for memory in memories:
        assert memory.screenplay is not None and memory.timeline is not None, memory.id
        assert 1 <= len(memory.tellings) <= 2
        assert len({t.author_id for t in memory.tellings}) == len(memory.tellings)
        for telling in memory.tellings:
            assert telling.text.strip() and telling.question
            assert people_db.get_person(mdb, DEMO_FAMILY_ID, telling.author_id) is not None
    by_id = {m.id: m for m in memories}
    assert [t.author_id for t in by_id["demo_sams_first_bike"].tellings] == ["dad", "sam"]
    assert sum(len(m.tellings) == 2 for m in memories) >= 2
    assert sum(2 <= len(m.participant_ids) <= 3 for m in memories) >= 3
    assert by_id["demo_sam_wedding_2019"].year == 2019
    assert set(by_id["demo_sam_wedding_2019"].participant_ids) >= {"lily", "sam", "mom", "dad"}


def test_existing_memory_ids_are_kept():
    """Other code and saved links use these ids."""
    assert {"demo_rosa_joseph_wedding", "demo_sunday_tamales", "demo_green_sedan_trip", "demo_sams_first_bike",
            "demo_lily_graduation", demo.HYMN_MEMORY_ID} <= set(demo.DEMO_MEMORY_IDS)
    assert demo.HYMN_MEMORY_ID == "sample_hymn_books"
    years = {m.id: m.year for m in demo.MOMENTS}
    assert (years["demo_rosa_joseph_wedding"], years["demo_sunday_tamales"], years["demo_green_sedan_trip"],
            years[demo.HYMN_MEMORY_ID], years["demo_sams_first_bike"], years["demo_lily_graduation"]) == (
        1958, 1970, 1972, 1988, 1997, 2015)


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
    """The memory is the sample's; its timeline is the real compiler's, not the sample's file."""
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
    # The seeded copy of the screenplay is the sample's, unchanged: it compiles as it is.
    assert stored.screenplay == demo.load_screenplay(demo.HYMN_MEMORY_ID) == want.screenplay
    fresh = compiler.compile(
        want.screenplay,
        sets={s.id: s for s in props_db.list_sets(mdb, DEMO_FAMILY_ID)},
        props={p.id: p for p in props_db.list_props(mdb, DEMO_FAMILY_ID)},
        people={p.id: p for p in people_db.list_people(mdb, DEMO_FAMILY_ID)},
    )
    assert fresh.ok and stored.timeline == fresh.timeline
    assert [s.id for s in stored.timeline.shots] == [s.id for s in want.timeline.shots]
    assert set(stored.timeline.narrations) == set(want.timeline.narrations) == {"dad", "mom"}
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


def test_every_prop_and_set_file_entry_is_in_the_demo_family(seeded):
    mdb, _ = seeded
    stored_props = {p.id: p for p in props_db.list_props(mdb, DEMO_FAMILY_ID)}
    stored_sets = {s.id: s for s in props_db.list_sets(mdb, DEMO_FAMILY_ID)}
    assert stored_props == {p.id: p for p in demo.load_props()}
    assert stored_sets == {s.id: s for s in demo.load_sets()}
    used_props = {pid for m in memories_db.get_memories(mdb, DEMO_FAMILY_ID, list(demo.DEMO_MEMORY_IDS))
                  for pid in m.prop_ids}
    used_sets = {sid for m in memories_db.get_memories(mdb, DEMO_FAMILY_ID, list(demo.DEMO_MEMORY_IDS))
                 for sid in m.set_ids}
    assert used_props == set(stored_props), "every seeded prop is in a scene"
    assert used_sets == set(stored_sets), "every seeded set is in a scene"


ANA_QUESTION = "What do you remember about the day Lily was born?"


def _all_open_questions(mdb) -> list[schemas.Question]:
    return [q for pid in DEMO_PERSON_IDS for q in memories_db.open_questions(mdb, DEMO_FAMILY_ID, pid)]


def test_seeded_questions_are_all_open(seeded):
    mdb, _ = seeded
    questions = _all_open_questions(mdb)
    assert sorted((q.person_id, q.text, q.memory_id) for q in questions) == sorted(demo.DEMO_QUESTIONS)
    assert all(q.status == "open" for q in questions)
    assert mdb.questions.count_documents({}) == len(demo.DEMO_QUESTIONS)
    ana = memories_db.open_questions(mdb, DEMO_FAMILY_ID, "mom")
    assert (ANA_QUESTION, None) in [(q.text, q.memory_id) for q in ana]


def test_sam_ana_and_david_each_have_an_open_question(seeded):
    mdb, _ = seeded
    for account in DEMO_ACCOUNTS:
        questions = memories_db.open_questions(mdb, DEMO_FAMILY_ID, account.person_id)
        assert questions, f"{account.email} has no open question"
        assert any(q.memory_id is not None for q in questions), account.email


def test_one_general_question_without_a_memory():
    general = [q for q in demo.DEMO_QUESTIONS if q[2] is None]
    assert len(general) == 1


def test_every_question_asks_a_participant_for_their_own_version(seeded):
    """The asked person was there, was born (and alive) by then, has not told it yet, and the
    moment has a compiled scene, so answering it adds a telling with either studio."""
    mdb, _ = seeded
    questions = [q for q in _all_open_questions(mdb) if q.memory_id is not None]
    assert len(questions) >= 4
    for question in questions:
        memory = memories_db.get_memory(mdb, DEMO_FAMILY_ID, question.memory_id)
        person = people_db.get_person(mdb, DEMO_FAMILY_ID, question.person_id)
        where = (question.person_id, question.memory_id)
        assert memory is not None and person is not None, where
        assert question.person_id in memory.participant_ids, where
        assert question.person_id not in {t.author_id for t in memory.tellings}, where
        assert person.born is not None and person.born <= memory.year, where
        assert person.died is None, where
        assert memory.screenplay is not None and memory.timeline is not None, where
        assert question.text.rstrip().endswith("?") and len(question.text.split()) >= 6, where


def test_every_question_with_a_memory_points_at_a_scene(seeded):
    """Regression: the real studio raises MemoryNotFound for a memory with no scene, so a
    seeded question aimed at one gave a 404 (now 409) when answered on stage."""
    mdb, _ = seeded
    targeted = [q for q in _all_open_questions(mdb) if q.memory_id is not None]
    assert targeted
    for question in targeted:
        memory = memories_db.get_memory(mdb, DEMO_FAMILY_ID, question.memory_id)
        assert memory is not None, question.memory_id
        assert memory.screenplay is not None and memory.timeline is not None, question.memory_id


# ------------------------------------------------------------------- broken authoring


def _copy_data(tmp_path: Path) -> Path:
    target = tmp_path / "demo_family"
    shutil.copytree(demo.DATA_DIR, target)
    return target


def test_a_screenplay_that_does_not_compile_stops_the_seed_with_the_exact_errors(mdb, tmp_path, monkeypatch):
    data = _copy_data(tmp_path)
    path = data / "demo_green_sedan_trip.screenplay.json"
    raw = json.loads(path.read_text())
    # Rosa hands David peaches she never picked up: a world-state error, found by compile().
    raw["shots"][1]["actions"] = [a for a in raw["shots"][1]["actions"] if a["do"] != "pick_up"]
    path.write_text(json.dumps(raw))
    broken = Screenplay.model_validate(raw)
    want = compiler.compile(
        demo.playable(broken)[0],
        sets={s.id: s for s in demo.load_sets()},
        props={p.id: p for p in demo.load_props()},
        people=demo.scene_people(),
    )
    assert not want.ok and want.errors
    monkeypatch.setattr(demo, "DATA_DIR", data)

    with pytest.raises(demo.DemoCompileError) as caught:
        seed_demo(mdb)

    message = str(caught.value)
    assert "demo_green_sedan_trip" in message
    for error in want.errors:
        assert error.message in message
    assert caught.value.memory_id == "demo_green_sedan_trip"
    assert caught.value.errors == want.errors
    assert counts(mdb) == {}, "nothing is written when a scene does not compile"


def test_a_static_compile_error_is_reported_too(mdb, tmp_path, monkeypatch):
    data = _copy_data(tmp_path)
    path = data / "demo_sunday_tamales.screenplay.json"
    raw = json.loads(path.read_text())
    raw["shots"][0]["actions"][1]["to"] = "the_moon"
    path.write_text(json.dumps(raw))
    monkeypatch.setattr(demo, "DATA_DIR", data)
    with pytest.raises(demo.DemoCompileError) as caught:
        seed_demo(mdb)
    assert "demo_sunday_tamales" in str(caught.value)
    assert '"the_moon" is not a mark in this shot' in str(caught.value)


def test_a_failed_reset_keeps_the_existing_demo(seeded, tmp_path, monkeypatch):
    mdb, _ = seeded
    before = counts(mdb)
    data = _copy_data(tmp_path)
    (data / "props.json").write_text("[{")
    monkeypatch.setattr(demo, "DATA_DIR", data)
    with pytest.raises(demo.DemoDataError, match=r"props\.json: invalid JSON"):
        seed_demo(mdb, reset=True)
    assert counts(mdb) == before


@pytest.mark.parametrize(
    ("file", "edit", "match"),
    [
        ("props.json", lambda raw: raw[0].update(size="enormous"), r"props\.json: item 0 \('hymn_books'\)"),
        ("sets.json", lambda raw: raw.append(dict(raw[0])), r"sets\.json: duplicate ids church_steps_1988"),
        ("demo_sam_wedding_2019.screenplay.json", lambda raw: raw.update(memoryId="other"),
         r"memoryId is 'other', expected 'demo_sam_wedding_2019'"),
        ("demo_sam_wedding_2019.screenplay.json", lambda raw: raw.update(cast=[*raw["cast"], "grandpa_joseph"]),
         r"cast grandpa_joseph not among the participants"),
        ("demo_sam_wedding_2019.screenplay.json", lambda raw: raw.pop("narrations"),
         r"narrated by \['sam'\], but the tellings are by \['dad', 'sam'\]"),
        ("demo_sam_wedding_2019.screenplay.json", lambda raw: raw.update(bogus=1),
         r"demo_sam_wedding_2019\.screenplay\.json: not a valid Screenplay"),
    ],
    ids=["bad-prop", "duplicate-set", "wrong-memory-id", "cast-not-there", "teller-missing", "unknown-field"],
)
def test_bad_demo_files_fail_loudly_with_the_file_name(mdb, tmp_path, monkeypatch, file, edit, match):
    data = _copy_data(tmp_path)
    path = data / file
    raw = json.loads(path.read_text())
    edit(raw)
    path.write_text(json.dumps(raw))
    monkeypatch.setattr(demo, "DATA_DIR", data)
    with pytest.raises(demo.DemoDataError, match=match):
        seed_demo(mdb)
    assert counts(mdb) == {}


def test_a_stray_screenplay_file_is_an_error(mdb, tmp_path, monkeypatch):
    data = _copy_data(tmp_path)
    (data / "demo_typo.screenplay.json").write_text("{}")
    monkeypatch.setattr(demo, "DATA_DIR", data)
    with pytest.raises(demo.DemoDataError, match="demo_typo.screenplay.json"):
        seed_demo(mdb)


def test_a_moment_before_a_participant_was_born_is_an_error(monkeypatch):
    moment = next(m for m in demo.MOMENTS if m.id == "demo_sunday_tamales")
    early = moment._replace(year=1960)  # Ana was born in 1964
    with pytest.raises(demo.DemoDataError, match="participant mom was born in 1964"):
        demo._check_moment(early, demo.load_screenplay(moment.id), demo.scene_people())
    late = moment._replace(year=2016)  # Luis died in 2015
    with pytest.raises(demo.DemoDataError, match="participant grandpa_luis died in 2015"):
        demo._check_moment(late, demo.load_screenplay(moment.id), demo.scene_people())


def test_only_effects_without_a_handler_are_left_out(monkeypatch):
    authored = demo.load_screenplay("demo_sam_wedding_2019")
    for verb in demo.EFFECT_VERBS:
        monkeypatch.delitem(compiler.HANDLERS, verb, raising=False)
    runnable, dropped = demo.playable(authored)
    assert dropped == ["shot s2, t=0: time_of_day", "shot s2, t=3: confetti"]
    assert {a.do for shot in runnable.shots for a in shot.actions} & demo.EFFECT_VERBS == set()

    monkeypatch.setitem(compiler.HANDLERS, "confetti", lambda *a: a[3])
    runnable, dropped = demo.playable(authored)
    assert dropped == ["shot s2, t=0: time_of_day"]
    assert "confetti" in {a.do for shot in runnable.shots for a in shot.actions}


def test_a_cue_on_a_dropped_effect_is_an_error(monkeypatch):
    for verb in demo.EFFECT_VERBS:
        monkeypatch.delitem(compiler.HANDLERS, verb, raising=False)
    raw = json.loads(demo.screenplay_path("demo_sam_wedding_2019").read_text())
    raw["shots"][1]["narration"][0]["cue"] = "confetti"
    with pytest.raises(demo.DemoDataError, match="cues narration on confetti"):
        demo.playable(Screenplay.model_validate(raw))


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
    assert mdb.questions.count_documents({"familyId": DEMO_FAMILY_ID}) == len(demo.DEMO_QUESTIONS)
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
