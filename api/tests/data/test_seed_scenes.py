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
from screenplay import compiler
from screenplay.narration import default_author
from screenplay.schema import VOCABULARY, Screenplay, Timeline
from screenplay.validate import validate
from seed import demo, fake_runs

FID = demo.DEMO_FAMILY_ID
MEMORY_IDS = list(demo.DEMO_MEMORY_IDS)
REQUIRED_VERBS = {
    "walk_to", "run_to", "enter", "follow", "turn_to", "hand_to", "pick_up", "drop", "place_on", "put_on",
    "hold", "set_state", "hug", "sit_on", "stand_up", "get_in", "get_out", "drive_to", "wave", "point_at",
    "kneel", "lean_toward", "pose",
}
EFFECTS = set(VOCABULARY["effects"])
MAX_PAGE_TEXT = 160


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
    """Nothing authored is left out: the stored screenplay is the file, effects and all."""
    assert memories[memory_id].screenplay == demo.load_screenplay(memory_id)


@pytest.mark.parametrize("memory_id", MEMORY_IDS)
def test_every_authored_effect_is_in_the_stored_timeline(memory_id, memories):
    """Each effect action in the file lands in Timeline.effects, in order, one entry per action."""
    memory = memories[memory_id]
    authored = [a.do for a in _actions(demo.load_screenplay(memory_id)) if a.do in EFFECTS]
    stored = [a.do for a in _actions(memory.screenplay) if a.do in EFFECTS]
    assert stored == authored
    timeline = memory.timeline
    assert sorted(e.kind for e in timeline.effects) == sorted(authored)
    assert all(0 <= e.t <= timeline.duration for e in timeline.effects)


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

    # Every effect is in the stored screenplays AND in the compiled timelines the stage plays.
    assert EFFECTS <= verbs, EFFECTS - verbs
    played = {e.kind for m in memories.values() for e in m.timeline.effects}
    assert played == EFFECTS, EFFECTS - played

    # The vehicle verbs use the family car and Sam's bike.
    vehicle = [a for sp in stored for a in _actions(sp) if a.do in ("get_in", "drive_to")]
    assert {a.what for a in vehicle} == {"green_sedan", "red_bike"}

    # Every library pose but the plain ones shows up somewhere.
    poses = {a.pose for sp in stored for a in _actions(sp) if a.do == "pose"}
    assert {"reach", "arms_raised", "bow_head", "slumped"} <= poses, poses


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


@pytest.mark.parametrize("memory_id", MEMORY_IDS)
def test_authored_file_validates_and_compiles_as_is(memory_id, scene):
    """The file on disk, untouched, validates statically and compiles through the real
    compiler's effect path (no stand-in handlers), giving the effects it authors."""
    authored = demo.load_screenplay(memory_id)
    assert validate(authored, scene["sets"], scene["props"], scene["people"]) == []
    result = compiler.compile(authored, **scene)
    assert result.ok, [e.message for e in result.errors]
    assert result.timeline is not None
    want = sorted(a.do for a in _actions(authored) if a.do in EFFECTS)
    assert sorted(e.kind for e in result.timeline.effects) == want


def test_the_stage_demo_has_its_confetti_and_steam(memories):
    """The moments the stage demo leans on show their effects."""
    kinds = {mid: {e.kind for e in m.timeline.effects} for mid, m in memories.items()}
    assert "confetti" in kinds["demo_sam_wedding_2019"]
    assert "confetti" in kinds["demo_lily_graduation"]
    assert "steam" in kinds["demo_sunday_tamales"]
    assert "rain" in kinds["demo_rosa_joseph_wedding"]


# ------------------------------------------------------------------ timeline helpers


def _track(tl: Timeline, target: str, channel: str):
    return next((t for t in tl.tracks if t.target == target and t.channel == channel), None)


def _at(track, t: float, default=None):
    """A track's value at timeline time t, as the stage plays it (shared/CONTRACTS.md):
    hold the first value before the first key and the last after the last; step for strings
    and parents; linear for numbers and vectors. At a repeated key time the last key wins."""
    if track is None:
        return default
    keys = [(float(k[0]), k[1]) for k in track.keys]
    before = [k for k in keys if k[0] <= t + 1e-9]
    if not before:
        return keys[0][1]
    t0, v0 = before[-1]
    after = [k for k in keys if k[0] > t + 1e-9]
    if not after or isinstance(v0, str) or v0 is None or track.channel in ("parent", "state", "root.facing"):
        return v0
    t1, v1 = after[0]
    s = (t - t0) / (t1 - t0)
    if isinstance(v0, list):
        return [a + (b - a) * s for a, b in zip(v0, v1)]
    return v0 + (v1 - v0) * s


def _shot(tl: Timeline, shot_id: str):
    return next(s for s in tl.shots if s.id == shot_id)


def _seated(tl: Timeline, pid: str, t: float) -> bool:
    """Both thighs forward as in the sit pose (shared/rig/poses.json: leg upper 1.5)."""
    return all(
        abs(_at(_track(tl, f"puppet:{pid}", f"leg_{side}.upper.rot"), t, 0.0) - 1.5) < 0.05 for side in "lr"
    )


def _on_stage(tl: Timeline, prop_id: str, start: float, end: float) -> bool:
    """The prop is on stage at some point of [start, end]: held or resting on something, or
    standing on the ground within the stage's width."""
    parent = _track(tl, f"prop:{prop_id}", "parent")
    position = _track(tl, f"prop:{prop_id}", "position")
    if parent is None and position is None:
        return False
    times = {start, end} | {float(k[0]) for tr in (parent, position) if tr for k in tr.keys if start <= k[0] <= end}
    for t in sorted(times):
        where = _at(parent, t, "ground")
        if where != "ground":
            return True
        pos = _at(position, t, [0.0, 0.0, 0.0])
        if abs(pos[0]) <= 1.0:
            return True
    return False


# ------------------------------------------------------------ the director's pass


DEMO_MOMENTS = [mid for mid in MEMORY_IDS if mid != demo.HYMN_MEMORY_ID]


@pytest.mark.parametrize("memory_id", MEMORY_IDS)
def test_every_puppet_and_prop_is_explained(memory_id, memories):
    """One explanation per cast member and per prop, each saying something real about them."""
    sp = memories[memory_id].screenplay
    by_target: dict[str, list[str]] = {}
    for e in sp.explanations or []:
        by_target.setdefault(e.target, []).append(e.text)
    want = {f"puppet:{c}" for c in sp.cast} | {f"prop:{p}" for p in sp.props}
    assert set(by_target) == want, set(by_target) ^ want
    for target, texts in by_target.items():
        assert len(texts) == 1, target
        assert len(texts[0].split()) >= 8, f"{memory_id}: the explanation of {target} is too thin"


@pytest.mark.parametrize("memory_id", MEMORY_IDS)
def test_page_text_is_short_storybook_text(memory_id, memories):
    for shot in memories[memory_id].screenplay.shots:
        text = (shot.page_text or "").strip()
        assert text, f"{memory_id} {shot.id}: no pageText"
        assert len(text) <= MAX_PAGE_TEXT, f"{memory_id} {shot.id}: pageText is {len(text)} characters"
        assert text[-1] in ".!?", f"{memory_id} {shot.id}: pageText should read as finished sentences"


@pytest.mark.parametrize("memory_id", DEMO_MOMENTS)
def test_every_demo_moment_has_several_shots_and_camera_moves(memory_id, memories):
    sp = memories[memory_id].screenplay
    assert len(sp.shots) >= 2, f"{memory_id} should have a second beat"
    moves = {cue.move for shot in sp.shots for cue in shot.camera}
    assert len(moves) >= 4, f"{memory_id} uses only {sorted(moves)}"
    for shot in sp.shots:
        assert len(shot.camera) >= 2, f"{memory_id} {shot.id}: the camera never moves"


@pytest.mark.parametrize("memory_id", MEMORY_IDS)
def test_pull_tabs_drive_something_on_stage_in_their_shot(memory_id, memories, scene):
    memory = memories[memory_id]
    sp, tl = memory.screenplay, memory.timeline
    props = scene["props"]
    tabs = [it for it in tl.interactions or [] if it.kind == "pull_tab"]
    assert tabs
    for it in tabs:
        where = f"{memory_id} {it.id}"
        assert it.shot is not None, f"{where}: a pull tab belongs to one shot"
        span = _shot(tl, it.shot)
        d = it.drives
        if d.type == "scrub":
            assert d.from_ is not None and d.to is not None and d.from_ < d.to, where
            assert span.start <= d.from_ < d.to <= span.end, (where, d.from_, d.to, span)
            assert d.targets, f"{where}: name what the tab scrubs"
            moving = []
            for target in d.targets:
                tracks = [t for t in tl.tracks if t.target == target]
                assert tracks, f"{where}: {target} has no track"
                if any(d.from_ < float(k[0]) <= d.to + 1e-6 for t in tracks for k in t.keys):
                    moving.append(target)
            assert moving, f"{where}: nothing it scrubs changes between {d.from_} and {d.to}"
        else:
            assert d.prop in sp.props, where
            assert _on_stage(tl, d.prop, span.start, span.end), f"{where}: {d.prop} is not on stage in {it.shot}"
            if d.type == "prop_state":
                assert d.states and len(d.states) >= 2 and set(d.states) <= set(props[d.prop].states), where
                # a state the tab shows must look different from another one
                assert any(props[d.prop].states[s] for s in d.states), f"{where}: every state looks the same"
            else:
                assert d.part in props[d.prop].parts and d.channel in ("rot", "offset"), where
                assert d.range and len(d.range) == 2 and d.range[0] < d.range[1], where


def test_sam_rides_the_bike_while_dad_runs_behind_then_lets_go(memories):
    sp, tl = memories["demo_sams_first_bike"].screenplay, memories["demo_sams_first_bike"].timeline
    rides = [a for a in _actions(sp) if a.do == "drive_to"]
    assert rides and all(a.who == "sam" and a.what == "red_bike" for a in rides)
    assert any(a.do == "follow" and a.who == "dad" and a.target == "sam" for a in _actions(sp))

    bike = _track(tl, "prop:red_bike", "position")
    sam = _track(tl, "puppet:sam", "root.position")
    dad = _track(tl, "puppet:dad", "root.position")
    keys = [(float(k[0]), k[1]) for k in bike.keys]
    legs = [(a, b) for a, b in zip(keys, keys[1:]) if b[0] > a[0] and abs(b[1][0] - a[1][0]) > 0.1]
    assert len(legs) >= 3, legs  # off down the street, wobbling in, then on to the corner
    for shot_id in ("s1", "s2"):
        span = _shot(tl, shot_id)
        xs = [_at(bike, t)[0] for t in (span.action_start, span.end - 0.01)]
        assert xs[1] - xs[0] > 0.5, f"{shot_id}: the bike should travel, {xs}"
    for (t0, p0), (t1, _p1) in legs:
        offset = _at(sam, t0)[0] - p0[0]
        for s in (0.25, 0.5, 0.75):
            t = t0 + (t1 - t0) * s
            assert abs(_at(sam, t)[0] - _at(bike, t)[0] - offset) < 2e-3, "Sam rides on the seat"
            assert _seated(tl, "sam", t), "Sam sits on the bike while it moves"
            assert _at(bike, t)[0] > _at(dad, t)[0], "Dad is behind the bike"

    # s1: Dad runs behind; s2: he lets go and stands still while Sam rings his way to the corner.
    (s1_t0, _), (s1_t1, _) = legs[0]
    assert _at(dad, s1_t1)[0] - _at(dad, s1_t0)[0] > 0.5, "Dad runs after the bike"
    (t0, _), (t1, _) = legs[-1]
    assert abs(_at(dad, t1)[0] - _at(dad, t0)[0]) < 1e-3, "Dad has let go and stopped"
    assert _at(_track(tl, "prop:red_bike", "state"), (t0 + t1) / 2) == "ringing"
    s2 = _shot(tl, "s2")
    assert _at(dad, s2.end - 0.01)[0] - _at(dad, t1)[0] > 0.3, "Dad catches up afterwards"


def test_rosa_cheers_at_the_graduation_from_her_front_row_seat(memories):
    sp, tl = memories["demo_lily_graduation"].screenplay, memories["demo_lily_graduation"].timeline
    cheer = [a for a in _actions(sp) if a.who == "grandma_rosa" and a.do == "pose" and a.pose == "arms_raised"]
    assert cheer, "Rosa clapped louder than anyone: she needs a visible cheer"
    line = next(line for line in sp.shots[0].narration if "clapped" in line.text)
    assert line.cue == cheer[0].id
    s1 = _shot(tl, "s1")
    arms = _track(tl, "puppet:grandma_rosa", "arm_l.upper.rot")
    assert arms and max(float(k[1]) for k in arms.keys if s1.start <= k[0] <= s1.end) > 2.5
    for grandma in ("grandma_rosa", "grandma_carmen"):
        assert _seated(tl, grandma, s1.action_start), f"{grandma} starts in her folding chair"
        assert _seated(tl, grandma, s1.end - 0.01), f"{grandma} ends the shot in her folding chair"
        hat = "rosa_hat" if grandma == "grandma_rosa" else "carmen_hat"
        assert _at(_track(tl, f"prop:{hat}", "parent"), s1.action_start + 3.0) == f"puppet:{grandma}.head"


def test_rosa_and_joseph_come_out_into_the_sun(memories):
    sp, tl = memories["demo_rosa_joseph_wedding"].screenplay, memories["demo_rosa_joseph_wedding"].timeline
    assert [s.set for s in sp.shots] == ["church_steps_1988", "st_marys_churchyard_1958"]
    assert sp.shots[0].set == memories["sample_hymn_books"].screenplay.shots[0].set
    assert "same church steps" in (sp.shots[0].page_text or "")
    rosa = next(e.text for e in sp.explanations if e.target == "puppet:grandma_rosa")
    assert "same church steps" in rosa, "say why the 1988 set is reused"
    umbrella = _track(tl, "prop:black_umbrella", "parent")
    assert umbrella.keys[0][1] == "puppet:grandpa_joseph.hand_r"
    assert _at(umbrella, _shot(tl, "s1").end - 0.01) == "puppet:grandma_rosa.hand_r"
    kinds = [(e.kind, e.value) for e in tl.effects]
    assert ("time_of_day", "day") in kinds and ("rain", None) in kinds and ("confetti", None) in kinds
    assert {a.do for a in sp.shots[1].actions} >= {"hug", "kneel", "point_at"}


def test_sunday_tamales_ends_at_the_table(memories):
    sp, tl = memories["demo_sunday_tamales"].screenplay, memories["demo_sunday_tamales"].timeline
    s2 = _shot(tl, "s2")
    hat = _track(tl, "prop:luis_straw_hat", "parent")
    assert [k[1] for k in hat.keys] == ["puppet:grandpa_luis.head"], "Luis wears his hat the whole time"
    assert _seated(tl, "grandpa_luis", s2.action_start)
    assert _seated(tl, "mom", s2.end - 0.01), "Ana sits at the table on her stool"
    tamale = _track(tl, "prop:lumpy_tamale", "parent")
    assert tamale.keys[0][1] == "puppet:mom.hand_r" and _at(tamale, s2.end - 0.01) == "puppet:grandpa_luis.hand_r"
    assert _at(_track(tl, "prop:tamale_pot", "parent"), s2.action_start) == "prop:kitchen_table.top"
    assert any(a.who == "grandpa_luis" and a.pose == "arms_raised" for a in _actions(sp))


def test_sams_wedding_bench_and_boutonniere(memories):
    sp, tl = memories["demo_sam_wedding_2019"].screenplay, memories["demo_sam_wedding_2019"].timeline
    s1, s2 = _shot(tl, "s1"), _shot(tl, "s2")
    for parent in ("mom", "dad"):
        for t in (s1.action_start, (s1.action_start + s1.end) / 2, s1.end - 0.01):
            assert _seated(tl, parent, t), f"{parent} sits on the front bench"
    flower = _track(tl, "prop:boutonniere", "parent")
    hug = next(a for a in sp.shots[1].actions if a.do == "hug" and a.who == "mom")
    fell = [float(k[0]) for k in flower.keys if k[1] == "ground" and s2.start <= k[0] <= s2.end]
    assert fell and fell[0] > s2.action_start + hug.t, "the boutonniere falls off after Mom's hug"
    assert _at(flower, s2.end - 0.01) == "puppet:sam.hand_r", "Lily gives it back"


# ------------------------------------------------------- the questions' second tellings

# What each person asked in DEMO_QUESTIONS might answer: several sentences, so the scripted
# run spreads them over every shot.
SECOND_TELLINGS: dict[tuple[str, str], str] = {
    ("sam", "demo_lily_graduation"): (
        "I was in the third row with Dad's camera bag on my lap. I saw her foot catch before anyone "
        "else did. She bowed like she had planned it all along, and I laughed so hard Grandma "
        "Carmen shushed me."
    ),
    ("dad", "demo_lily_graduation"): (
        "I took ninety photos that day and three are in focus. When Lily tripped I was changing the "
        "film. Outside I just stood there and waved at her like a fool."
    ),
    ("mom", "demo_sam_wedding_2019"): (
        "I cried before the music even started. When Lily handed him the rings I squeezed David's "
        "hand so hard he winced. At the party I hugged Sam and heard something small hit the floor."
    ),
    ("lily", "demo_sam_wedding_2019"): (
        "I checked that ring box every five minutes all morning. Walking up the aisle I was more "
        "nervous than Sam was. Then his flower fell off at the party, and I pinned it right back on."
    ),
    ("grandma_carmen", "demo_sunday_tamales"): (
        "Sundays were loud and warm and full of steam. Ana spread the masa like she was painting a "
        "wall. Luis always found her tamale first, and he never once said it was lumpy."
    ),
    ("grandma_rosa", "demo_lily_graduation"): (
        "I wore my blue hat and there was Carmen in the very same one. When Lily bowed I stood "
        "right up and cheered. I did not care one bit who was looking."
    ),
}
LINKED_QUESTIONS = [(pid, mid) for pid, _text, mid in demo.DEMO_QUESTIONS if mid is not None]


def test_every_linked_question_has_a_sample_answer():
    assert set(LINKED_QUESTIONS) == set(SECOND_TELLINGS)


@pytest.mark.parametrize("cued", [True, False], ids=["cued", "uncued"])
@pytest.mark.parametrize(("person_id", "memory_id"), LINKED_QUESTIONS)
def test_a_question_s_second_telling_compiles_over_the_scene(person_id, memory_id, cued, db, memories):
    """Answering a seeded question in the offline demo adds that person's narration over the
    stored action: fake_runs.telling_narration + compile_scene, as the scripted run does."""
    memory = memories[memory_id]
    assert person_id in memory.participant_ids
    assert person_id not in demo.tellers(memory.screenplay)
    text = SECOND_TELLINGS[(person_id, memory_id)]
    drafted = fake_runs.telling_narration(memory.screenplay, person_id, text, cued=cued)
    result = fake_runs.compile_scene(db, FID, drafted)
    assert result.ok, [e.message for e in result.errors]
    tl = result.timeline
    assert tl is not None and tl.narrations is not None
    track = tl.narrations[person_id]
    assert track.lines and track.captions
    assert all((line.source or "").startswith(f"telling:{person_id}:s") for line in track.lines)
    assert set(tl.narrations) == {t.author_id for t in memory.tellings} | {person_id}
    # The new teller narrates over the same action: no track moves.
    assert tl.tracks == memory.timeline.tracks
    assert tl.default_narrator == memory.timeline.default_narrator
