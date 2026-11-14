"""The demo family's pre-compiled scenes (seed/demo.py, seed/demo_family/).

Every seeded moment carries a hand-authored screenplay that the seed compiled with the real
compiler. Here each one is compiled again from what is stored, against the family's own
props, sets and people read back from the database, and must give zero errors and exactly
the stored timeline. The storybook extras the stage demo relies on are checked too:
pageText on every shot, a pull tab per moment, one narration per teller with cues,
explanations for every puppet and prop, and a good spread of the vocabulary across the family.
"""

from __future__ import annotations

import json
from collections.abc import Iterator

import mongomock
import pytest

import schemas
from agent.tools.scene_review import review_scene, review_timeline
from db import memories as memories_db
from db import people as people_db
from db import props as props_db
from screenplay import compiler, effects
from screenplay.narration import default_author
from screenplay.schema import VOCABULARY, Screenplay, Timeline
from screenplay.validate import validate
from seed import demo

FID = demo.DEMO_FAMILY_ID
MEMORY_IDS = list(demo.DEMO_MEMORY_IDS)
REQUIRED_VERBS = {"walk_to", "hand_to", "hug", "sit_on", "get_in", "drive_to", "wave", "kneel"}
EFFECTS = set(VOCABULARY["effects"])


@pytest.fixture(scope="module")
def db() -> Iterator:
    client = mongomock.MongoClient(tz_aware=True)
    database = client["stitch_seed_scenes"]
    demo.seed_demo(database)
    yield database
    client.close()


@pytest.fixture(scope="module")
def scene(db):
    """The family's props, sets and people as the compiler takes them, read from the db."""
    return {
        "sets": {s.id: s for s in props_db.list_sets(db, FID)},
        "props": {p.id: p for p in props_db.list_props(db, FID)},
        "people": {p.id: p for p in people_db.list_people(db, FID)},
    }


@pytest.fixture(scope="module")
def memories(db) -> dict[str, schemas.Memory]:
    found = memories_db.get_memories(db, FID, MEMORY_IDS)
    return {m.id: m for m in found}


def _actions(screenplay: Screenplay) -> list:
    return [a for shot in screenplay.shots for a in shot.actions]


def _authored() -> dict[str, Screenplay]:
    return {mid: demo.load_screenplay(mid) for mid in MEMORY_IDS}


# ------------------------------------------------------------------------- the files


def test_one_screenplay_file_per_moment_plus_props_and_sets():
    names = sorted(p.name for p in demo.DATA_DIR.glob("*.json"))
    want = sorted([f"{mid}{demo.SCREENPLAY_SUFFIX}" for mid in MEMORY_IDS] + ["props.json", "sets.json"])
    assert names == want
    for path in demo.DATA_DIR.glob("*.json"):
        json.loads(path.read_text())  # every file is plain JSON an editor can open


def test_props_and_sets_use_placeholder_art_with_sensible_rigs():
    props = demo.load_props()
    sets = demo.load_sets()
    for prop in props:
        assert prop.art and all(v is None for v in prop.art.values()), prop.id
        assert prop.color and prop.color.startswith("#"), prop.id
        assert prop.size in VOCABULARY["sizes"] and prop.aspect > 0, prop.id
        assert all(0 <= v <= 1 for v in prop.anchor), prop.id
        for point in [*([prop.grip] if prop.grip else []), *prop.sockets.values(), *(p.at for p in prop.parts.values())]:
            assert len(point) == 2 and all(0 <= v <= 1 for v in point), prop.id
        for part in prop.parts.values():
            assert part.art in prop.art, prop.id
        if prop.size in ("furniture", "vehicle", "building"):
            assert prop.grip is None, f"{prop.id} is too big to carry and should have no grip"
        else:
            assert prop.grip is not None, f"{prop.id} can be carried and needs a grip"
    for set_def in sets:
        assert set_def.layers and set_def.marks, set_def.id
        assert all(layer.art is None and layer.color for layer in set_def.layers), set_def.id
        for name, mark in set_def.marks.items():
            assert len(mark) in (2, 3) and mark[1] >= 0, (set_def.id, name)


def test_the_family_car_is_a_rigged_vehicle():
    car = next(p for p in demo.load_props() if p.id == "green_sedan")
    assert car.size == "vehicle"
    assert {"driver", "passenger", "back_seat"} <= set(car.sockets)
    assert [name for name, part in car.parts.items() if part.spin] == ["wheel_back", "wheel_front"]


# ------------------------------------------------------------------- every moment


@pytest.mark.parametrize("memory_id", MEMORY_IDS)
def test_stored_scene_recompiles_to_the_same_timeline(memory_id, memories, scene):
    memory = memories[memory_id]
    assert memory.screenplay is not None and memory.timeline is not None
    fresh = compiler.compile(memory.screenplay, **scene)
    assert fresh.errors == [], [e.message for e in fresh.errors]
    assert fresh.ok and fresh.timeline is not None
    assert memory.timeline == fresh.timeline
    # The stored JSON round-trips through the contract model.
    Timeline.model_validate_json(memory.timeline.model_dump_json(by_alias=True))
    Screenplay.model_validate_json(memory.screenplay.model_dump_json(by_alias=True))
    assert memory.version == 1


@pytest.mark.parametrize("memory_id", MEMORY_IDS)
def test_stored_screenplay_is_the_authored_file(memory_id, memories):
    stored = memories[memory_id].screenplay
    playable, dropped = demo.playable(demo.load_screenplay(memory_id))
    assert stored == playable
    assert all(d.split(": ", 1)[1] in demo.unsupported_effects() for d in dropped)


@pytest.mark.parametrize("memory_id", MEMORY_IDS)
def test_storybook_extras(memory_id, memories, scene):
    memory = memories[memory_id]
    sp, tl = memory.screenplay, memory.timeline
    assert sp is not None and tl is not None

    # pageText on every shot, in the screenplay and the timeline
    assert all(shot.page_text and shot.page_text.strip() for shot in sp.shots)
    assert [s.page_text for s in tl.shots] == [s.page_text for s in sp.shots]

    # at least one pull tab
    assert any(it.kind == "pull_tab" for it in sp.interactions or [])
    assert tl.interactions and any(it.kind == "pull_tab" for it in tl.interactions)

    # one narration per teller: the keys are exactly the tellings' authors
    authors = {t.author_id for t in memory.tellings}
    assert set(tl.narrations or {}) == authors
    assert demo.tellers(sp) == authors
    assert tl.default_narrator == default_author(sp) == memory.tellings[0].author_id
    for author in authors:
        track = tl.narrations[author]
        assert track.lines and track.captions, author
        lines = [line for shot in sp.shots for line in shot.narration] if author == tl.default_narrator else [
            line for lines in (sp.narrations or {})[author].values() for line in lines
        ]
        assert any(line.cue for line in lines), f"{author}'s narration has no cue"
        assert all((line.source or "").startswith(f"telling:{author}:") for line in lines)

    # explanations for every cast member and prop, and nothing the scene review would flag
    targets = {e.target for e in sp.explanations or []}
    assert targets == {f"puppet:{c}" for c in sp.cast} | {f"prop:{p}" for p in sp.props}
    assert review_scene(sp, props=scene["props"]) == []
    assert review_timeline(sp, tl, props=scene["props"]) == []

    # the memory's props and sets are the scene's, and the family has them
    assert memory.prop_ids == list(sp.props)
    assert memory.set_ids == list(dict.fromkeys(shot.set for shot in sp.shots))
    assert set(memory.prop_ids) <= set(scene["props"]) and set(memory.set_ids) <= set(scene["sets"])


@pytest.mark.parametrize("memory_id", MEMORY_IDS)
def test_everyone_on_stage_was_there_and_alive(memory_id, memories, scene):
    memory = memories[memory_id]
    people = scene["people"]
    assert set(memory.screenplay.cast) <= set(memory.participant_ids)
    for pid in memory.participant_ids:
        person = people[pid]
        assert person.born is not None and person.born <= memory.year, (pid, memory.year)
        assert person.died is None or person.died >= memory.year, (pid, memory.year)


# ----------------------------------------------------------------------- the family


def test_vocabulary_coverage_across_the_family(memories):
    stored = [m.screenplay for m in memories.values()]
    verbs = {a.do for sp in stored for a in _actions(sp)}
    assert REQUIRED_VERBS <= verbs, REQUIRED_VERBS - verbs

    moves = {cue.move for sp in stored for shot in sp.shots for cue in shot.camera}
    assert {"wide", "close_up", "dolly_to", "pan_to", "pull_back"} <= moves

    # Effects are authored in the files. The ones the compiler can run are stored too.
    authored = {a.do for sp in _authored().values() for a in _actions(sp)}
    assert {"confetti", "steam"} <= authored and len(authored & EFFECTS) >= 3
    assert verbs & EFFECTS == (authored & EFFECTS) - demo.unsupported_effects()

    # The vehicle verbs use the family car.
    vehicle = [a for sp in stored for a in _actions(sp) if a.do in ("get_in", "drive_to")]
    assert {a.what for a in vehicle} == {"green_sedan"}


def test_multi_shot_moments_fold_between_sets(memories):
    folding = []
    for memory in memories.values():
        sp, tl = memory.screenplay, memory.timeline
        if len(sp.shots) < 2:
            continue
        for shot, nxt in zip(sp.shots, sp.shots[1:]):
            if shot.transition == "fold" and shot.set != nxt.set:
                fold = next(t for t in tl.tracks if t.target == f"set:{shot.set}.layer0" and t.channel == "fold")
                assert fold.keys[-1][1] == 0.0, f"{memory.id}: {shot.set} never folds away"
                folding.append(memory.id)
                break
    assert len(set(folding)) >= 2, folding


def test_moments_span_decades_and_threads_cross(memories):
    assert len(memories) >= 7
    years = sorted(m.year for m in memories.values())
    assert years[0] <= 1960 and years[-1] >= 2019
    assert len({m.era for m in memories.values()}) >= 5
    assert {"sample_hymn_books", "demo_sam_wedding_2019", "demo_green_sedan_trip"} <= set(memories)
    assert memories["sample_hymn_books"].year == 1988 and memories["demo_sam_wedding_2019"].year == 2019
    two_tellers = [m.id for m in memories.values() if len({t.author_id for t in m.tellings}) >= 2]
    assert len(two_tellers) >= 2
    small_groups = [m.id for m in memories.values() if 2 <= len(m.participant_ids) <= 3]
    assert len(small_groups) >= 3
    # threads cross: every person of the second and third generation shares a moment with
    # someone from another generation
    for pid in ("dad", "mom", "sam", "lily"):
        assert any(pid in m.participant_ids and len(m.participant_ids) > 1 for m in memories.values()), pid


def test_signature_hats_are_worn_on_stage(memories):
    worn = {}
    for memory in memories.values():
        for track in memory.timeline.tracks:
            if track.channel == "parent" and track.target in ("prop:luis_straw_hat", "prop:sam_cap"):
                worn[track.target] = track.keys[0][1]
    assert worn == {"prop:luis_straw_hat": "puppet:grandpa_luis.head", "prop:sam_cap": "puppet:sam.head"}


def test_the_car_carries_its_riders(memories):
    tl = memories["demo_green_sedan_trip"].timeline
    car = next(t for t in tl.tracks if t.target == "prop:green_sedan" and t.channel == "position")
    xs = [k[1][0] for k in car.keys]
    assert max(xs) - min(xs) > 1.0, "the sedan should drive across the stage"
    assert any(t.target == "prop:green_sedan" and t.channel.startswith("part.wheel") for t in tl.tracks)


# ------------------------------------------------------------------------- effects


def _effect_handler(world, tracks, action, t, action_start, locate):
    """Stand-in effect handler: runs effects.effect_for (every check it makes) and occupies nobody."""
    effects.effect_for(action, t, locate, shot_end=t + 60.0)
    return t


@pytest.mark.parametrize("memory_id", MEMORY_IDS)
def test_authored_effects_are_valid_and_compile_once_supported(memory_id, scene, monkeypatch):
    """The effects left out today validate statically, and compile once the compiler runs effects."""
    authored = demo.load_screenplay(memory_id)
    assert validate(authored, scene["sets"], scene["props"], scene["people"]) == []
    for verb in EFFECTS:
        monkeypatch.setitem(compiler.HANDLERS, verb, compiler.HANDLERS.get(verb, _effect_handler))
    assert demo.unsupported_effects() == frozenset()
    runnable, dropped = demo.playable(authored)
    assert dropped == [] and runnable == authored
    result = compiler.compile(authored, **scene)
    assert result.ok, [e.message for e in result.errors]
