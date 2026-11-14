"""seed/fake_runs.py: the scripted stand-in for agent.runs.RunManager (mongomock, no keys, no network)."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import types
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

import schemas
from db import client as db_client
from db import families as families_db
from db import media as media_db
from db import memories as memories_db
from db import people as people_db
from db import props as props_db
from screenplay.schema import Screenplay, Timeline
from seed import fake_runs
from seed.demo import DEMO_FAMILY_ID, DEMO_PERSON_IDS, SAMPLE_PATH, seed_demo
from seed.fake_runs import ScriptedRunManager, install_fake_runs, remap_ids, short_title, strip_person

API_DIR = Path(__file__).resolve().parents[2]
TIMEOUT = 10.0

FAM_B = "fam_b"
B_TELLER = "64b000000000000000000001"
B_OTHER = "64b000000000000000000002"
B_THIRD = "64b000000000000000000003"
B_USER = "user_b"

SOLO = "fam_solo"
SOLO_PERSON = "64c000000000000000000001"
SOLO_USER = "user_solo"

ANA = "demo_user_ana"


# ------------------------------------------------------------------------------ fixtures


def _family(db, family_id: str, user_id: str, people: list[tuple[str, str]]) -> None:
    families_db.create_family(db, name=f"Family {family_id}", creator_user_id=user_id, family_id=family_id)
    for i, (person_id, name) in enumerate(people):
        people_db.create_person(
            db,
            family_id,
            schemas.NewPerson(name=name, born=1950 + i),
            added_by=user_id,
            user_id=user_id if i == 0 else None,
            person_id=person_id,
        )


@pytest.fixture
def db(mdb):
    db_client.ensure_indexes(mdb)
    seed_demo(mdb)
    _family(mdb, FAM_B, B_USER, [(B_TELLER, "Bea"), (B_OTHER, "Ben"), (B_THIRD, "Bo")])
    _family(mdb, SOLO, SOLO_USER, [(SOLO_PERSON, "Solo")])
    return mdb


@pytest.fixture
def manager(db):
    mgr = ScriptedRunManager(db_getter=lambda: db, step_seconds=0)
    yield mgr
    mgr.shutdown()


def fields(**kw: Any) -> schemas.SubmitMemoryFields:
    base: dict[str, Any] = {
        "text": "We waited at the bus stop in the snow. Nobody had a hat.",
        "year": 1979,
        "question": "What do you remember about winter?",
    }
    base.update(kw)
    return schemas.SubmitMemoryFields(**base)


def answer(text: str = "Grandpa Luis and the neighbors.") -> schemas.AnswerRequest:
    return schemas.AnswerRequest(kind="answer", text=text)


def keep(prop_id: str) -> schemas.AnswerRequest:
    return schemas.AnswerRequest(kind="props", props=[schemas.PropEdit(id=prop_id, action="keep")])


def drive(
    mgr: ScriptedRunManager,
    family_id: str,
    run_id: str,
    user_id: str,
    *,
    reply: schemas.AnswerRequest | None = None,
    review: Any = None,
) -> list[tuple[str, Any]]:
    """Stream a run to its end, answering the question and the prop review as they come."""

    async def go() -> list[tuple[str, Any]]:
        events: list[tuple[str, Any]] = []
        async for name, payload in mgr.stream_run(family_id, run_id):
            events.append((name, payload))
            if name == "question":
                assert mgr.status(family_id, run_id) == "waiting"
                mgr.answer_run(family_id, run_id, user_id, reply or answer())
            elif name == "props_found":
                assert mgr.status(family_id, run_id) == "waiting"
                chip_id = payload.props[0].id
                body = review(chip_id) if callable(review) else (review or keep(chip_id))
                mgr.answer_run(family_id, run_id, user_id, body)
        return events

    return asyncio.run(asyncio.wait_for(go(), TIMEOUT))


def collect(mgr: ScriptedRunManager, family_id: str, run_id: str) -> list[tuple[str, Any]]:
    async def go() -> list[tuple[str, Any]]:
        return [e async for e in mgr.stream_run(family_id, run_id)]

    return asyncio.run(asyncio.wait_for(go(), TIMEOUT))


def assert_well_formed(events: list[tuple[str, Any]]) -> None:
    for name, payload in events:
        assert name in schemas.RUN_EVENTS
        assert isinstance(payload, schemas.RUN_EVENTS[name]), (name, type(payload))
    terminal = [name for name, _ in events if name in ("done", "error")]
    assert len(terminal) == 1
    assert events[-1][0] == terminal[0]


def names(events: list[tuple[str, Any]]) -> list[str]:
    return [name for name, _ in events]


_PROSE = {"text", "word", "label", "pageText", "title", "description", "notes"}
_TOKEN_SPLIT = str.maketrans({c: " " for c in ":./-"})


def id_tokens(value: Any, key: str | None = None) -> set[str]:
    """Every identifier token in a scene JSON value, prose fields excluded (dict keys included)."""
    out: set[str] = set()
    if isinstance(value, dict):
        for k, v in value.items():
            out |= set(k.translate(_TOKEN_SPLIT).split())
            out |= id_tokens(v, k)
    elif isinstance(value, list):
        for v in value:
            out |= id_tokens(v, key)
    elif isinstance(value, str) and key not in _PROSE and " " not in value:
        out |= set(value.translate(_TOKEN_SPLIT).split())
    return out


def scene_person_ids(memory: schemas.Memory) -> set[str]:
    """Every person id the screenplay and timeline refer to."""
    sp, tl = memory.screenplay, memory.timeline
    assert sp is not None and tl is not None
    ids = set(sp.cast)
    for shot in sp.shots:
        ids |= set(shot.start)
        for action in shot.actions:
            if action.who:
                ids.add(action.who)
            if action.do == "hand_to" and action.to:
                ids.add(action.to)
        for line in shot.narration:
            if line.source and line.source.startswith("telling:"):
                ids.add(line.source.split(":")[1])
    ids |= set(sp.narrations or {})
    for track in tl.tracks:
        if track.target.startswith("puppet:"):
            ids.add(track.target.split(":", 1)[1])
        for _, value in track.keys:
            if isinstance(value, str) and value.startswith("puppet:"):
                ids.add(value.split(":", 1)[1].split(".")[0])
    for author, track in (tl.narrations or {}).items():
        ids |= {author, track.author_id}
    if tl.default_narrator:
        ids.add(tl.default_narrator)
    for item in (sp.explanations or []) + (tl.explanations or []):
        if item.target.startswith("puppet:"):
            ids.add(item.target.split(":", 1)[1])
    for item in (sp.interactions or []) + (tl.interactions or []):
        for target in item.drives.targets or []:
            if target.startswith("puppet:"):
                ids.add(target.split(":", 1)[1])
    return ids


def family_person_ids(db, family_id: str) -> set[str]:
    return {p.id for p in people_db.list_people(db, family_id)}


# ------------------------------------------------------------------------------ full run


def test_full_run_in_the_demo_family(db, manager):
    photo = media_db.put_media(db, DEMO_FAMILY_ID, b"\x89PNG fake", content_type="image/png", kind="photo")
    run_id = manager.start_run(
        DEMO_FAMILY_ID, ANA, "mom", fields(participant_ids=["lily"]), [photo]
    )

    async def go() -> list[tuple[str, Any]]:
        events: list[tuple[str, Any]] = []
        stream = manager.stream_run(DEMO_FAMILY_ID, run_id)
        async for name, payload in stream:
            events.append((name, payload))
            if name == "question":
                # plan, subagent_start, subagent_end, question; then the run waits.
                assert names(events) == ["plan", "subagent_start", "subagent_end", "question"]
                assert manager.status(DEMO_FAMILY_ID, run_id) == "waiting"
                assert manager.wait(DEMO_FAMILY_ID, run_id, timeout=0) == "waiting"
                assert payload.text == fake_runs.FOLLOWUP_QUESTION
                manager.answer_run(DEMO_FAMILY_ID, run_id, ANA, answer("Lily was there, in her yellow coat."))
            elif name == "props_found":
                assert manager.status(DEMO_FAMILY_ID, run_id) == "waiting"
                [chip] = payload.props
                assert chip.id == "hymn_books" and chip.reused is True and chip.size == "hand"
                manager.answer_run(DEMO_FAMILY_ID, run_id, ANA, keep(chip.id))
        return events

    events = asyncio.run(asyncio.wait_for(go(), TIMEOUT))
    assert_well_formed(events)
    seq = names(events)
    assert seq[0] == "plan"
    assert seq.index("question") < seq.index("props_found") < seq.index("scene_ready") < seq.index("done")
    assert seq[-2:] == ["scene_ready", "done"]
    assert {"interviewer", "prop_master", "director"} == {
        p.name for n, p in events if n == "subagent_start"
    }
    starts = [p.name for n, p in events if n == "subagent_start"]
    ends = [p.name for n, p in events if n == "subagent_end"]
    assert starts == ends
    plans = [p for n, p in events if n == "plan"]
    assert plans[0].todos[0].status == "in_progress"
    assert all(item.status == "completed" for item in plans[-1].todos)
    assert manager.status(DEMO_FAMILY_ID, run_id) == "done"

    memory_id = events[-1][1].memory_id
    ready = events[-2][1]
    assert ready.memory_id == memory_id and ready.version == 1
    memory = memories_db.get_memory(db, DEMO_FAMILY_ID, memory_id)
    assert memory is not None
    [telling] = memory.tellings
    assert telling.author_id == "mom"
    assert telling.photo_ids == [photo]
    assert telling.question == "What do you remember about winter?"
    assert telling.text.startswith("We waited at the bus stop")
    assert telling.text.endswith("Lily was there, in her yellow coat.")  # the follow-up reply is kept
    assert memory.year == 1979
    assert memory.title == "We waited at the bus stop…"
    assert {"mom", "lily"} <= set(memory.participant_ids)
    assert memory.screenplay is not None and memory.timeline is not None
    assert memory.screenplay.memory_id == memory_id
    assert memory.prop_ids == ["hymn_books"] and memory.set_ids == ["church_steps_1988"]

    # In the demo family the sample ids are real: mom stays mom, dad is the first participant.
    people = scene_person_ids(memory)
    assert people <= family_person_ids(db, DEMO_FAMILY_ID)
    assert people == {"mom", "lily"}
    assert "dad" not in id_tokens(memory.screenplay.model_dump(mode="json", by_alias=True))

    # The demo already had the prop and set; nothing was duplicated.
    assert [p.id for p in props_db.list_props(db, DEMO_FAMILY_ID)] == ["hymn_books"]
    assert [s.id for s in props_db.list_sets(db, DEMO_FAMILY_ID)] == ["church_steps_1988"]
    assert memory in memories_db.get_book(db, DEMO_FAMILY_ID, "lily")


def test_demo_teller_without_participants_casts_their_spouse(db, manager):
    run_id = manager.start_run(DEMO_FAMILY_ID, ANA, "mom", fields(), [])
    events = drive(manager, DEMO_FAMILY_ID, run_id, ANA)
    assert_well_formed(events)
    memory = memories_db.get_memory(db, DEMO_FAMILY_ID, events[-1][1].memory_id)
    assert memory is not None
    assert scene_person_ids(memory) == {"mom", "dad"}  # the sample ids, which really are this family's
    assert set(memory.participant_ids) == {"mom", "dad"}


def test_run_in_another_family_recasts_people_and_copies_the_prop_and_set(db, manager):
    demo_props_before = props_db.list_props(db, DEMO_FAMILY_ID)
    demo_sets_before = props_db.list_sets(db, DEMO_FAMILY_ID)

    run_id = manager.start_run(FAM_B, B_USER, B_TELLER, fields(participant_ids=[B_TELLER, B_THIRD]), [])
    events = drive(manager, FAM_B, run_id, B_USER)
    assert_well_formed(events)
    chip = next(p for n, p in events if n == "props_found").props[0]
    assert chip.id == f"hymn_books_{FAM_B}" and chip.reused is False  # the family had no copy yet

    memory = memories_db.get_memory(db, FAM_B, events[-1][1].memory_id)
    assert memory is not None
    people = scene_person_ids(memory)
    assert people == {B_TELLER, B_THIRD}  # mom -> teller, dad -> first participant who is not the teller
    assert people <= family_person_ids(db, FAM_B)
    tokens = id_tokens(memory.screenplay.model_dump(mode="json", by_alias=True)) | id_tokens(
        memory.timeline.model_dump(mode="json", by_alias=True)
    )
    assert not tokens & {"mom", "dad", "hymn_books", "church_steps_1988", "sample_hymn_books"}
    assert set(memory.participant_ids) == {B_TELLER, B_THIRD}

    # The family got its own copies of the prop and set, and the scene points at them.
    [prop] = props_db.list_props(db, FAM_B)
    [set_def] = props_db.list_sets(db, FAM_B)
    assert prop.id == f"hymn_books_{FAM_B}" and prop.family_id == FAM_B and prop.name == "hymn books"
    assert set_def.id == f"church_steps_1988_{FAM_B}" and set_def.family_id == FAM_B
    assert memory.prop_ids == [prop.id] and memory.set_ids == [set_def.id]
    assert memory.screenplay.props == [prop.id]
    assert {shot.set for shot in memory.screenplay.shots} == {set_def.id}
    assert f"prop:{prop.id}" in {t.target for t in memory.timeline.tracks}

    # The demo family was not touched.
    assert props_db.list_props(db, DEMO_FAMILY_ID) == demo_props_before
    assert props_db.list_sets(db, DEMO_FAMILY_ID) == demo_sets_before

    # A second run in the same family reuses the copies.
    run_2 = manager.start_run(FAM_B, B_USER, B_OTHER, fields(), [])
    events_2 = drive(manager, FAM_B, run_2, B_USER)
    assert_well_formed(events_2)
    chip_2 = next(p for n, p in events_2 if n == "props_found").props[0]
    assert chip_2.id == chip.id and chip_2.reused is True
    assert len(props_db.list_props(db, FAM_B)) == 1 and len(props_db.list_sets(db, FAM_B)) == 1
    memory_2 = memories_db.get_memory(db, FAM_B, events_2[-1][1].memory_id)
    assert memory_2 is not None
    # No participants: dad becomes another person of the family.
    assert scene_person_ids(memory_2) <= family_person_ids(db, FAM_B)
    assert B_OTHER in scene_person_ids(memory_2) and len(scene_person_ids(memory_2)) == 2


def test_family_of_one_casts_the_teller_alone(db, manager):
    run_id = manager.start_run(SOLO, SOLO_USER, SOLO_PERSON, fields(), [])
    events = drive(manager, SOLO, run_id, SOLO_USER)
    assert_well_formed(events)
    assert events[-1][0] == "done"
    memory = memories_db.get_memory(db, SOLO, events[-1][1].memory_id)
    assert memory is not None
    assert scene_person_ids(memory) == {SOLO_PERSON}
    assert memory.screenplay.cast == [SOLO_PERSON]


def _strings(value: Any) -> list[str]:
    """Every string in a JSON value, dict keys included."""
    if isinstance(value, dict):
        return [s for k, v in value.items() for s in [k, *_strings(v)]]
    if isinstance(value, list):
        return [s for v in value for s in _strings(v)]
    return [value] if isinstance(value, str) else []


def assert_clean_scene(memory: schemas.Memory) -> None:
    """One track per target + channel, nobody hands to themselves, and the scene validates."""
    sp, tl = memory.screenplay, memory.timeline
    assert sp is not None and tl is not None
    counts = Counter((t.target, t.channel) for t in tl.tracks)
    assert not [k for k, n in counts.items() if n > 1], counts
    for shot in sp.shots:
        for action in shot.actions:
            assert action.who is None or action.who not in (action.to, action.target), action
    Screenplay.model_validate(sp.model_dump(mode="json", by_alias=True))
    Timeline.model_validate(tl.model_dump(mode="json", by_alias=True))


def test_family_made_by_post_families_gets_a_one_puppet_scene(mdb):
    """Regression: a family of one used to merge the sample's two puppets into the teller."""
    family = families_db.create_family(mdb, name="Just me", creator_user_id=SOLO_USER)
    me = people_db.create_person(
        mdb, family.id, schemas.NewPerson(name="Rosa", born=1961), added_by=SOLO_USER, user_id=SOLO_USER
    )
    mgr = ScriptedRunManager(step_seconds=0, db_getter=lambda: mdb)
    try:
        run_id = mgr.start_run(family.id, SOLO_USER, me.id, fields(), [])
        assert mgr.wait(family.id, run_id, timeout=TIMEOUT) == "waiting"
        mgr.answer_run(family.id, run_id, SOLO_USER, schemas.AnswerRequest(kind="answer", text="Only me."))
        assert mgr.wait(family.id, run_id, timeout=TIMEOUT) == "waiting"
        mgr.answer_run(family.id, run_id, SOLO_USER, schemas.AnswerRequest(kind="props", props=[]))
        assert mgr.wait(family.id, run_id, timeout=TIMEOUT) == "done"
        events = mgr.events(family.id, run_id)
    finally:
        mgr.shutdown()
    assert_well_formed(events)
    memory = memories_db.get_memory(mdb, family.id, events[-1][1].memory_id)
    assert memory is not None and memory.screenplay is not None and memory.timeline is not None
    assert_clean_scene(memory)
    dumped = _strings(memory.screenplay.model_dump(by_alias=True)) + _strings(
        memory.timeline.model_dump(by_alias=True)
    )
    assert not [s for s in dumped if "puppet:dad" in s or "telling:dad" in s]
    assert memory.screenplay.cast == [me.id]
    assert memory.participant_ids == [me.id]
    assert memory.timeline.default_narrator == me.id
    assert scene_person_ids(memory) == {me.id}
    # The teller's own narration plays, and the books stay where they fell.
    assert [line.source for line in memory.screenplay.shots[0].narration] == [
        f"telling:{me.id}:s1", f"telling:{me.id}:s2", f"telling:{me.id}:s3"
    ]
    assert memory.timeline.captions == memory.timeline.narrations[me.id].captions
    parent = next(t for t in memory.timeline.tracks if t.target.startswith("prop:") and t.channel == "parent")
    assert parent.keys[-1][1] == "ground"
    assert memories_db.open_questions(mdb, family.id, me.id) == []


def test_two_person_family_keeps_both_puppets(db, manager):
    run_id = manager.start_run(FAM_B, B_USER, B_TELLER, fields(participant_ids=[B_OTHER]), [])
    events = drive(manager, FAM_B, run_id, B_USER)
    assert_well_formed(events)
    memory = memories_db.get_memory(db, FAM_B, events[-1][1].memory_id)
    assert memory is not None
    assert_clean_scene(memory)
    assert set(memory.screenplay.cast) == {B_TELLER, B_OTHER}
    targets = {t.target for t in memory.timeline.tracks}
    assert {f"puppet:{B_TELLER}", f"puppet:{B_OTHER}"} <= targets
    assert memory.timeline.default_narrator == B_OTHER  # the sample's default telling, recast
    hand = [a for a in memory.screenplay.shots[0].actions if a.do == "hand_to"]
    assert [(a.who, a.to) for a in hand] == [(B_OTHER, B_TELLER)]


# ------------------------------------------------------------------------------ strip_person


def _raw_sample() -> tuple[dict, dict]:
    raw = json.loads(SAMPLE_PATH.read_text())
    return raw["memory"]["screenplay"], raw["memory"]["timeline"]


def test_strip_person_on_the_raw_sample():
    screenplay, timeline = _raw_sample()
    before = json.dumps([screenplay, timeline], sort_keys=True)
    sp, tl = strip_person(screenplay, timeline, "dad", keep_narrator="mom")
    assert json.dumps([screenplay, timeline], sort_keys=True) == before  # pure: inputs untouched

    assert not [t for t in tl["tracks"] if t["target"] == "puppet:dad"]
    parent = next(t for t in tl["tracks"] if t["target"] == "prop:hymn_books" and t["channel"] == "parent")
    assert not [v for _, v in parent["keys"] if isinstance(v, str) and v.startswith("puppet:dad")]
    assert parent["keys"][-1][1] == "ground"
    # Every channel of the prop is cut at the same time, so the books stay on the ground.
    cut = next(t for t, v in _raw_parent_keys() if isinstance(v, str) and v.startswith("puppet:dad"))
    for track in tl["tracks"]:
        if track["target"] == "prop:hymn_books":
            assert track["keys"] and all(t < cut for t, _ in track["keys"])
    assert "dad" not in sp["cast"] and sp["cast"] == ["mom"]
    shot = sp["shots"][0]
    assert "dad" not in shot["start"]
    assert not [a for a in shot["actions"] if a.get("who") == "dad"]
    assert not [a for a in shot["actions"] if "dad" in (a.get("to"), a.get("target"), a.get("at"), a.get("on"))]
    assert "dad" not in (sp.get("narrations") or {})
    assert "dad" not in tl["narrations"]

    # The default telling is now mom's, with cues only on actions that still exist.
    mom_lines = screenplay["narrations"]["mom"]["s1"]
    assert [line["text"] for line in shot["narration"]] == [line["text"] for line in mom_lines]
    action_cues = {a.get("id") for a in shot["actions"]} | {a["do"] for a in shot["actions"]}
    assert all(line.get("cue") is None or line["cue"] in action_cues for line in shot["narration"])
    assert {line.get("cue") for line in shot["narration"]} == {None, "drop"}
    assert all(line.get("cue") in (None, "drop") for line in tl["narrations"]["mom"]["lines"])
    assert tl["defaultNarrator"] == "mom"
    assert tl["captions"] == timeline["narrations"]["mom"]["captions"]

    assert not [e for e in sp["explanations"] + tl["explanations"] if e["target"] == "puppet:dad"]
    for item in sp["interactions"] + tl["interactions"]:
        assert "puppet:dad" not in item["drives"].get("targets", [])
    assert not [s for s in _strings([sp, tl]) if "puppet:dad" in s or "telling:dad" in s]
    Screenplay.model_validate(sp)
    Timeline.model_validate(tl)


def _raw_parent_keys() -> list:
    _, timeline = _raw_sample()
    return next(
        t["keys"] for t in timeline["tracks"] if t["target"] == "prop:hymn_books" and t["channel"] == "parent"
    )


def test_strip_person_drops_interaction_targets_and_empty_interactions():
    sp = {
        "cast": ["a", "b"],
        "shots": [],
        "interactions": [
            {"id": "both", "drives": {"type": "scrub", "targets": ["puppet:a", "puppet:b"]}},
            {"id": "only_b", "drives": {"type": "scrub", "targets": ["puppet:b.hand_r"]}},
            {"id": "all", "drives": {"type": "scrub"}},
        ],
        "explanations": [{"target": "puppet:a", "text": "A"}, {"target": "puppet:b", "text": "B"}],
    }
    tl = {"tracks": [], "interactions": sp["interactions"], "explanations": sp["explanations"]}
    sp2, tl2 = strip_person(sp, tl, "b", keep_narrator="a")
    for out in (sp2, tl2):
        assert [(i["id"], i["drives"].get("targets")) for i in out["interactions"]] == [
            ("both", ["puppet:a"]), ("all", None)
        ]
        assert out["explanations"] == [{"target": "puppet:a", "text": "A"}]
    assert sp2["cast"] == ["a"]


def test_strip_person_keeps_a_default_narrator_who_stays():
    sp, tl = _raw_sample()
    tl = {**tl, "defaultNarrator": "mom", "captions": [{"t": 0, "text": "mine"}]}
    _, out = strip_person(sp, tl, "dad", keep_narrator="mom")
    assert out["defaultNarrator"] == "mom" and out["captions"] == [{"t": 0, "text": "mine"}]


# ------------------------------------------------------------------------------ prop chips


def test_reused_is_true_only_when_the_family_already_had_the_prop(db, manager):
    run_1 = manager.start_run(SOLO, SOLO_USER, SOLO_PERSON, fields(), [])
    events_1 = drive(manager, SOLO, run_1, SOLO_USER)
    chip_1 = next(p for n, p in events_1 if n == "props_found").props[0]
    assert chip_1.reused is False
    end_1 = next(p for n, p in events_1 if n == "subagent_end" and p.name == "prop_master")
    assert end_1.summary == f"Saved the {chip_1.name}."

    run_2 = manager.start_run(SOLO, SOLO_USER, SOLO_PERSON, fields(), [])
    events_2 = drive(manager, SOLO, run_2, SOLO_USER)
    chip_2 = next(p for n, p in events_2 if n == "props_found").props[0]
    assert chip_2.id == chip_1.id and chip_2.reused is True
    end_2 = next(p for n, p in events_2 if n == "subagent_end" and p.name == "prop_master")
    assert end_2.summary == f"Reused the {chip_2.name}."


# ------------------------------------------------------------------------------ linker


def _questions(db, family_id: str, person_id: str) -> list[schemas.Question]:
    return memories_db.open_questions(db, family_id, person_id)


def test_linker_asks_each_named_participant_once(db, manager):
    run_id = manager.start_run(FAM_B, B_USER, B_TELLER, fields(participant_ids=[B_OTHER]), [])
    events = drive(manager, FAM_B, run_id, B_USER)
    assert_well_formed(events)
    memory = memories_db.get_memory(db, FAM_B, events[-1][1].memory_id)
    assert memory is not None

    [question] = _questions(db, FAM_B, B_OTHER)
    assert question.memory_id == memory.id and question.status == "open"
    assert "Bea" in question.text and memory.title in question.text
    assert question.text == f'Bea told the story of "{memory.title}". What do you remember?'
    assert _questions(db, FAM_B, B_TELLER) == []  # the teller is never asked
    assert _questions(db, FAM_B, B_THIRD) == []

    # The plan shows the step, in order, and ends all completed.
    plans = [p for n, p in events if n == "plan"]
    assert plans[0].todos[-1].text == fake_runs.ASK_OTHERS
    asking = [p for p in plans if any(t.text == fake_runs.ASK_OTHERS and t.status == "in_progress" for t in p.todos)]
    assert len(asking) == 1
    assert all(t.status == "completed" for t in asking[0].todos[:-1])
    assert all(t.status == "completed" for t in plans[-1].todos)
    seq = names(events)
    assert seq.index("scene_ready") > max(i for i, (n, p) in enumerate(events) if p in asking)

    # Running the linker again does not duplicate the open question.
    assert fake_runs.ask_participants(db, FAM_B, memory, B_TELLER, [B_OTHER, B_TELLER]) == []
    assert len(_questions(db, FAM_B, B_OTHER)) == 1


def test_linker_creates_nothing_without_participants(db, manager):
    run_id = manager.start_run(FAM_B, B_USER, B_TELLER, fields(participant_ids=[B_TELLER]), [])
    events = drive(manager, FAM_B, run_id, B_USER)
    assert_well_formed(events)
    memory = memories_db.get_memory(db, FAM_B, events[-1][1].memory_id)
    assert memory is not None
    # B_OTHER was recast as the sample's second puppet, but was not named, so is not asked.
    assert B_OTHER in memory.participant_ids
    assert db["questions"].count_documents({"familyId": FAM_B}) == 0
    assert fake_runs.ASK_OTHERS not in {t.text for n, p in events if n == "plan" for t in p.todos}


def test_linker_failure_never_fails_the_run(db, manager, monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("questions collection is down")

    monkeypatch.setattr(memories_db, "create_question", boom)
    run_id = manager.start_run(FAM_B, B_USER, B_TELLER, fields(participant_ids=[B_OTHER, B_THIRD]), [])
    events = drive(manager, FAM_B, run_id, B_USER)
    assert_well_formed(events)
    assert events[-1][0] == "done" and "scene_ready" in names(events)
    assert db["questions"].count_documents({"familyId": FAM_B}) == 0


def test_linker_asks_several_participants(db, manager):
    run_id = manager.start_run(
        FAM_B, B_USER, B_TELLER, fields(participant_ids=[B_THIRD, B_TELLER, B_OTHER, B_THIRD]), []
    )
    events = drive(manager, FAM_B, run_id, B_USER)
    memory_id = events[-1][1].memory_id
    for pid in (B_OTHER, B_THIRD):
        [question] = _questions(db, FAM_B, pid)
        assert question.memory_id == memory_id
    assert _questions(db, FAM_B, B_TELLER) == []


# ------------------------------------------------------------------------------ prop review


def test_rename_edit_renames_the_saved_prop(db, manager):
    run_id = manager.start_run(FAM_B, B_USER, B_TELLER, fields(), [])
    events = drive(
        manager, FAM_B, run_id, B_USER,
        review=lambda pid: schemas.AnswerRequest(
            kind="props", props=[schemas.PropEdit(id=pid, action="rename", name="Mom's hymnals")]
        ),
    )
    assert_well_formed(events)
    [prop] = props_db.list_props(db, FAM_B)
    assert prop.name == "Mom's hymnals"
    assert props_db.get_prop(db, DEMO_FAMILY_ID, "hymn_books").name == "hymn books"


def _replace_photo(photo_id: str):
    return lambda pid: schemas.AnswerRequest(
        kind="props", props=[schemas.PropEdit(id=pid, action="replace_photo", photo_id=photo_id)]
    )


@pytest.mark.parametrize("as_ref", [False, True], ids=["bare-id", "media-ref"])
def test_replace_photo_edit_uses_a_photo_of_this_family(db, manager, as_ref):
    photo = media_db.put_media(db, FAM_B, b"\x89PNG real", content_type="image/png", kind="photo")
    assert media_db.parse_media_ref(photo) == photo  # put_media hands back the bare id
    run_id = manager.start_run(FAM_B, B_USER, B_TELLER, fields(), [])
    sent = media_db.media_ref(photo) if as_ref else photo
    events = drive(manager, FAM_B, run_id, B_USER, review=_replace_photo(sent))
    assert_well_formed(events)
    [prop] = props_db.list_props(db, FAM_B)
    # The same shape as PATCH /families/{fid}/props/{id} and the schemas.Prop.art example.
    assert props_db.get_prop(db, FAM_B, prop.id).art == {"body": f"media/{photo}"}
    assert prop.source == "photo"


@pytest.mark.parametrize(
    "photo_id",
    ["64f000000000000000000099", "media/64f000000000000000000099", "not-an-id", "media/"],
)
def test_replace_photo_with_an_unknown_photo_leaves_the_art(db, manager, photo_id):
    run_id = manager.start_run(FAM_B, B_USER, B_TELLER, fields(), [])
    events = drive(manager, FAM_B, run_id, B_USER, review=_replace_photo(photo_id))
    assert_well_formed(events)
    assert events[-1][0] == "done"
    [prop] = props_db.list_props(db, FAM_B)
    sample_prop = props_db.get_prop(db, DEMO_FAMILY_ID, "hymn_books")
    assert prop.art == sample_prop.art and prop.source == sample_prop.source


def test_replace_photo_with_another_familys_photo_leaves_the_art(db, manager):
    theirs = media_db.put_media(db, DEMO_FAMILY_ID, b"\x89PNG demo", content_type="image/png", kind="photo")
    run_id = manager.start_run(FAM_B, B_USER, B_TELLER, fields(), [])
    drive(manager, FAM_B, run_id, B_USER, review=_replace_photo(theirs))
    [prop] = props_db.list_props(db, FAM_B)
    assert prop.art == props_db.get_prop(db, DEMO_FAMILY_ID, "hymn_books").art


def test_remove_edit_leaves_the_prop_out(db, manager):
    run_id = manager.start_run(FAM_B, B_USER, B_TELLER, fields(), [])
    events = drive(
        manager, FAM_B, run_id, B_USER,
        review=lambda pid: schemas.AnswerRequest(kind="props", props=[schemas.PropEdit(id=pid, action="remove")]),
    )
    assert_well_formed(events)
    assert events[-1][0] == "done"
    memory = memories_db.get_memory(db, FAM_B, events[-1][1].memory_id)
    assert memory is not None and memory.screenplay is not None and memory.timeline is not None
    assert memory.prop_ids == []
    assert memory.screenplay.props == []
    tokens = id_tokens(memory.screenplay.model_dump(mode="json", by_alias=True)) | id_tokens(
        memory.timeline.model_dump(mode="json", by_alias=True)
    )
    assert not any(t.startswith("hymn_books") for t in tokens)
    shot = memory.screenplay.shots[0]
    assert all(a.what is None for a in shot.actions)
    assert all(line.cue is None for line in shot.narration)  # cues on removed actions are dropped
    # Never created for this family, since it was left out; the set still was.
    assert props_db.list_props(db, FAM_B) == []
    assert len(props_db.list_sets(db, FAM_B)) == 1
    summary = next(p for n, p in events if n == "subagent_end" and p.name == "prop_master").summary
    assert summary.startswith("Left out")


def test_remove_in_the_demo_family_keeps_the_saved_prop(db, manager):
    run_id = manager.start_run(DEMO_FAMILY_ID, ANA, "mom", fields(), [])
    events = drive(
        manager, DEMO_FAMILY_ID, run_id, ANA,
        review=lambda pid: schemas.AnswerRequest(kind="props", props=[schemas.PropEdit(id=pid, action="remove")]),
    )
    assert_well_formed(events)
    memory = memories_db.get_memory(db, DEMO_FAMILY_ID, events[-1][1].memory_id)
    assert memory is not None and memory.prop_ids == []
    assert props_db.get_prop(db, DEMO_FAMILY_ID, "hymn_books") is not None
    sample = memories_db.get_memory(db, DEMO_FAMILY_ID, "sample_hymn_books")
    assert sample is not None and sample.prop_ids == ["hymn_books"]


def test_no_followup_skips_the_question(db):
    mgr = ScriptedRunManager(db_getter=lambda: db, step_seconds=0, ask_followup=False)
    try:
        run_id = mgr.start_run(FAM_B, B_USER, B_TELLER, fields(text="Short and sweet."), [])
        events = drive(mgr, FAM_B, run_id, B_USER)
    finally:
        mgr.shutdown()
    assert_well_formed(events)
    assert "question" not in names(events)
    assert "props_found" in names(events) and events[-1][0] == "done"
    plan = events[0][1]
    assert "Ask one follow-up" not in [t.text for t in plan.todos]
    memory = memories_db.get_memory(db, FAM_B, events[-1][1].memory_id)
    assert memory is not None and memory.tellings[0].text == "Short and sweet."
    assert memory.title == "Short and sweet"


# ------------------------------------------------------------------------------ second telling


def test_second_telling_adds_to_the_moment_without_asking(db, manager):
    before = memories_db.get_memory(db, DEMO_FAMILY_ID, "demo_sams_first_bike")
    assert before is not None
    photo = media_db.put_media(db, DEMO_FAMILY_ID, b"\x89PNG bike", content_type="image/png", kind="photo")
    run_id = manager.start_run(
        DEMO_FAMILY_ID, "demo_user_lily", "lily",
        fields(text="I watched from the porch.", year=1997, memory_id="demo_sams_first_bike",
               question="What do you remember?"),
        [photo],
    )
    events = collect(manager, DEMO_FAMILY_ID, run_id)
    assert_well_formed(events)
    assert names(events) == ["plan", "subagent_start", "subagent_end", "plan", "done"]
    assert events[-1][1].memory_id == "demo_sams_first_bike"
    assert manager.status(DEMO_FAMILY_ID, run_id) == "done"

    after = memories_db.get_memory(db, DEMO_FAMILY_ID, "demo_sams_first_bike")
    assert after is not None
    assert len(after.tellings) == len(before.tellings) + 1
    new = after.tellings[-1]
    assert (new.author_id, new.text, new.photo_ids, new.question) == (
        "lily", "I watched from the porch.", [photo], "What do you remember?"
    )
    assert "lily" in after.participant_ids
    assert after.screenplay is None and after.timeline is None  # no scene save
    assert after.title == before.title and after.year == before.year


# ------------------------------------------------------------------------------ subscribers


def test_late_subscriber_gets_the_full_replay(db, manager):
    run_id = manager.start_run(FAM_B, B_USER, B_TELLER, fields(), [])
    first = drive(manager, FAM_B, run_id, B_USER)
    assert manager.wait(FAM_B, run_id, timeout=TIMEOUT) == "done"
    late = collect(manager, FAM_B, run_id)
    assert late == first
    assert late == manager.events(FAM_B, run_id)


def test_two_concurrent_subscribers_both_get_the_full_sequence(db):
    mgr = ScriptedRunManager(db_getter=lambda: db, step_seconds=0.01)
    try:
        run_id = mgr.start_run(FAM_B, B_USER, B_TELLER, fields(), [])

        async def watch() -> list[tuple[str, Any]]:
            return [e async for e in mgr.stream_run(FAM_B, run_id)]

        async def go() -> tuple[list, list, list]:
            a = asyncio.ensure_future(watch())
            b = asyncio.ensure_future(watch())
            seen: list[tuple[str, Any]] = []
            async for name, payload in mgr.stream_run(FAM_B, run_id):  # the one who answers
                seen.append((name, payload))
                if name == "question":
                    mgr.answer_run(FAM_B, run_id, B_USER, answer())
                elif name == "props_found":
                    mgr.answer_run(FAM_B, run_id, B_USER, keep(payload.props[0].id))
            return await a, await b, seen

        a, b, seen = asyncio.run(asyncio.wait_for(go(), TIMEOUT))
    finally:
        mgr.shutdown()
    assert_well_formed(seen)
    assert a == seen and b == seen


def test_streaming_does_not_block_the_event_loop(db):
    mgr = ScriptedRunManager(db_getter=lambda: db, step_seconds=0.05, ask_followup=False)
    try:
        run_id = mgr.start_run(FAM_B, B_USER, B_TELLER, fields(), [])

        async def go() -> int:
            ticks = 0
            stop = asyncio.Event()

            async def ticker() -> None:
                nonlocal ticks
                while not stop.is_set():
                    ticks += 1
                    await asyncio.sleep(0.01)

            task = asyncio.ensure_future(ticker())
            async for name, payload in mgr.stream_run(FAM_B, run_id):
                if name == "props_found":
                    mgr.answer_run(FAM_B, run_id, B_USER, keep(payload.props[0].id))
            stop.set()
            await task
            return ticks

        ticks = asyncio.run(asyncio.wait_for(go(), TIMEOUT))
    finally:
        mgr.shutdown()
    assert ticks >= 5


def test_start_run_from_inside_the_event_loop(db, manager):
    async def go() -> list[tuple[str, Any]]:
        run_id = manager.start_run(FAM_B, B_USER, B_TELLER, fields(), [])
        out = []
        async for name, payload in manager.stream_run(FAM_B, run_id):
            out.append((name, payload))
            if name == "question":
                manager.answer_run(FAM_B, run_id, B_USER, answer())
            elif name == "props_found":
                manager.answer_run(FAM_B, run_id, B_USER, keep(payload.props[0].id))
        return out

    events = asyncio.run(asyncio.wait_for(go(), TIMEOUT))
    assert_well_formed(events)
    assert events[-1][0] == "done"


# ------------------------------------------------------------------------------ errors


def test_answer_run_errors(db, manager):
    run_id = manager.start_run(FAM_B, B_USER, B_TELLER, fields(), [])
    assert manager.wait(FAM_B, run_id, timeout=TIMEOUT) == "waiting"  # on the question

    with pytest.raises(LookupError):
        manager.answer_run(DEMO_FAMILY_ID, run_id, B_USER, answer())  # another family's fid
    with pytest.raises(LookupError):
        manager.answer_run(FAM_B, "no-such-run", B_USER, answer())
    with pytest.raises(PermissionError):
        manager.answer_run(FAM_B, run_id, "someone_else", answer())
    with pytest.raises(ValueError):
        manager.answer_run(FAM_B, run_id, B_USER, keep("hymn_books"))  # waiting on a question
    with pytest.raises(ValueError):
        manager.answer_run(FAM_B, run_id, B_USER, schemas.AnswerRequest(kind="merge", approve=True))
    with pytest.raises(ValueError):
        manager.answer_run(FAM_B, run_id, B_USER, answer("   "))
    assert manager.status(FAM_B, run_id) == "waiting"  # bad answers leave it paused

    manager.answer_run(FAM_B, run_id, B_USER, answer())
    assert manager.wait(FAM_B, run_id, timeout=TIMEOUT) == "waiting"  # on the prop review
    events = manager.events(FAM_B, run_id)
    assert events[-1][0] == "props_found"
    with pytest.raises(ValueError):
        manager.answer_run(FAM_B, run_id, B_USER, answer())  # waiting on props
    with pytest.raises(ValueError):
        manager.answer_run(FAM_B, run_id, B_USER, schemas.AnswerRequest(kind="props"))  # no list
    manager.answer_run(FAM_B, run_id, B_USER, keep(events[-1][1].props[0].id))
    assert manager.wait(FAM_B, run_id, timeout=TIMEOUT) == "done"
    with pytest.raises(ValueError):
        manager.answer_run(FAM_B, run_id, B_USER, answer())  # not waiting any more


def test_answer_while_running_is_rejected(db):
    mgr = ScriptedRunManager(db_getter=lambda: db, step_seconds=5)
    try:
        run_id = mgr.start_run(FAM_B, B_USER, B_TELLER, fields(), [])
        assert mgr.status(FAM_B, run_id) == "running"
        with pytest.raises(ValueError):
            mgr.answer_run(FAM_B, run_id, B_USER, answer())
    finally:
        mgr.shutdown()


def test_start_run_errors(db, manager):
    with pytest.raises(ValueError):
        manager.start_run(FAM_B, B_USER, "mom", fields(), [])  # a person of another family
    with pytest.raises(ValueError):
        manager.start_run(FAM_B, B_USER, "nobody", fields(), [])
    with pytest.raises(ValueError):
        manager.start_run(FAM_B, B_USER, B_TELLER, fields(participant_ids=["dad"]), [])
    with pytest.raises(LookupError):
        manager.start_run(FAM_B, B_USER, B_TELLER, fields(memory_id="nope"), [])
    with pytest.raises(LookupError):  # another family's memory
        manager.start_run(FAM_B, B_USER, B_TELLER, fields(memory_id="demo_sams_first_bike"), [])


def test_other_family_cannot_see_a_run(db, manager):
    run_id = manager.start_run(FAM_B, B_USER, B_TELLER, fields(), [])
    for call in (
        lambda: manager.stream_run(DEMO_FAMILY_ID, run_id),
        lambda: manager.stream_run(FAM_B, "missing"),
        lambda: manager.status(DEMO_FAMILY_ID, run_id),
        lambda: manager.wait(SOLO, run_id, timeout=0),
        lambda: manager.events(DEMO_FAMILY_ID, run_id),
    ):
        with pytest.raises(LookupError):
            call()  # raised by the call itself, before any iteration


def test_failure_sends_one_plain_error_event(db, manager, monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("database exploded with secret details")

    monkeypatch.setattr(memories_db, "create_memory", boom)
    run_id = manager.start_run(FAM_B, B_USER, B_TELLER, fields(), [])
    events = drive(manager, FAM_B, run_id, B_USER)
    assert_well_formed(events)
    assert events[-1][0] == "error"
    assert events[-1][1].message == fake_runs.ERROR_MESSAGE
    assert "secret" not in events[-1][1].message
    assert "done" not in names(events) and "scene_ready" not in names(events)
    assert manager.status(FAM_B, run_id) == "error"
    assert memories_db.moment_summaries(db, FAM_B) == []


def test_shutdown_ends_a_waiting_run_with_an_error(db):
    mgr = ScriptedRunManager(db_getter=lambda: db, step_seconds=0)
    run_id = mgr.start_run(FAM_B, B_USER, B_TELLER, fields(), [])
    assert mgr.wait(FAM_B, run_id, timeout=TIMEOUT) == "waiting"
    mgr.shutdown()
    assert mgr.wait(FAM_B, run_id, timeout=TIMEOUT) == "error"
    events = collect(mgr, FAM_B, run_id)
    assert_well_formed(events)
    assert events[-1][1].message == fake_runs.STOPPED_MESSAGE
    with pytest.raises(RuntimeError):
        mgr.start_run(FAM_B, B_USER, B_TELLER, fields(), [])


def test_uses_the_db_getter_once_per_run(db):
    calls = []

    def getter():
        calls.append(1)
        return db

    mgr = ScriptedRunManager(db_getter=getter, step_seconds=0, ask_followup=False)
    try:
        run_id = mgr.start_run(FAM_B, B_USER, B_TELLER, fields(), [])
        drive(mgr, FAM_B, run_id, B_USER)
    finally:
        mgr.shutdown()
    assert len(calls) == 1


def test_default_db_getter_reads_the_db_client(db):
    db_client.set_db(db)
    try:
        mgr = ScriptedRunManager(step_seconds=0)
        run_id = mgr.start_run(FAM_B, B_USER, B_TELLER, fields(), [])
        events = drive(mgr, FAM_B, run_id, B_USER)
        mgr.shutdown()
    finally:
        db_client.set_db(None)
    assert events[-1][0] == "done"


# ------------------------------------------------------------------------------ helpers


def test_remap_ids_renames_whole_tokens_and_leaves_prose():
    ids = {"mom": "a1", "dad": "b2", "hymn_books": "hb_x"}
    value = {
        "cast": ["mom", "dad"],
        "start": {"mom": {"holding": "hymn_books", "at": "steps_top"}, "dad": {"at": "offstage_left"}},
        "target": "puppet:mom",
        "parent": "puppet:dad.hand_r",
        "source": "telling:dad:s2",
        "in": "hymn_books.top",
        "text": "mom",
        "word": "dad",
        "label": "mom and dad",
        "other": "mommy",
        "tracks": [[0, "puppet:mom.hand_r"], [1, "ground"]],
    }
    out = remap_ids(value, ids)
    assert out["cast"] == ["a1", "b2"]
    assert out["start"] == {"a1": {"holding": "hb_x", "at": "steps_top"}, "b2": {"at": "offstage_left"}}
    assert out["target"] == "puppet:a1" and out["parent"] == "puppet:b2.hand_r"
    assert out["source"] == "telling:b2:s2" and out["in"] == "hb_x.top"
    assert (out["text"], out["word"], out["label"], out["other"]) == ("mom", "dad", "mom and dad", "mommy")
    assert out["tracks"] == [[0, "puppet:a1.hand_r"], [1, "ground"]]
    # A swap is applied in one pass.
    assert remap_ids(["mom", "dad"], {"mom": "dad", "dad": "mom"}) == ["dad", "mom"]


def test_short_title():
    assert short_title("It was a Sunday in 1988. I saw her.") == "It was a Sunday in 1988"
    assert short_title("One two three four five six seven eight") == "One two three four five six…"
    assert short_title("   ") == "A memory"


# ------------------------------------------------------------------------------ install


def test_install_returns_false_when_agent_runs_cannot_be_imported(monkeypatch):
    monkeypatch.setitem(sys.modules, "agent.runs", None)
    assert install_fake_runs() is False


def test_install_returns_false_without_configure(monkeypatch):
    monkeypatch.setitem(sys.modules, "agent.runs", types.ModuleType("agent.runs"))
    assert install_fake_runs() is False


def test_install_configures_a_scripted_manager(monkeypatch):
    stub = types.ModuleType("agent.runs")
    seen: list[Any] = []
    stub.configure = seen.append  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "agent.runs", stub)
    assert install_fake_runs(step_seconds=0.25) is True
    [manager] = seen
    assert isinstance(manager, ScriptedRunManager)
    assert manager._step_seconds == 0.25
    manager.shutdown()


def test_importing_fake_runs_does_not_import_agent():
    code = (
        "import sys\n"
        "import seed.fake_runs\n"
        "loaded = sorted(m for m in sys.modules if m == 'agent' or m.startswith('agent.'))\n"
        "print(loaded)\n"
        "sys.exit(1 if loaded else 0)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=API_DIR, capture_output=True, text=True, timeout=60, check=False
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout.strip().replace("'", '"')) == []


def test_demo_person_ids_are_what_the_sample_uses():
    # The demo-family assertions above rely on the sample's cast being real demo people.
    assert {"mom", "dad"} <= set(DEMO_PERSON_IDS)
