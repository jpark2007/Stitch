"""Story scenarios: realistic multi-shot family screenplays compile cleanly and sanely.

Each story in story_scenes.STORIES is what a director agent would write for a whole memory
(wedding, first car, birthday, graduation, kitchen, church, moving house, rainy walk, Christmas,
and two that leave props behind on one set and move on to another: moving day, rainy Sunday).
Every one must compile with zero errors, pass timeline_sanity.sanity_problems, compile to
byte-identical JSON twice, and round-trip through Timeline.model_validate. Together they use
every action verb, effect and camera move in shared/vocabulary.json.

The second half proves each sanity check catches what it is for, on small hand-built timelines.

Compiler bugs the stories ran into are minimal reproductions at the bottom, each
xfail(strict=True) with the exact error; the stories themselves use a compiling variant (named
in a comment where the story does it).
"""

from __future__ import annotations

import copy
import json
import math
from typing import Any

import pytest

from screenplay import ik
from screenplay.compiler import compile as compile_screenplay
from screenplay.schema import VOCABULARY, CompileResult, Screenplay, Timeline
from story_scenes import STORIES, story_scene
from timeline_sanity import CHECKS, SIT, sanity_problems, value_at

NAMES = sorted(STORIES)


def compile_story(sp: dict[str, Any]) -> CompileResult:
    sets, props, people = story_scene()
    return compile_screenplay(Screenplay.model_validate(copy.deepcopy(sp)), sets=sets, props=props, people=people)


def messages(result: CompileResult) -> list[str]:
    return [f"{e.code}: {e.message}" for e in result.errors]


_COMPILED: dict[str, CompileResult] = {}


def compiled(name: str) -> CompileResult:
    if name not in _COMPILED:
        _COMPILED[name] = compile_story(STORIES[name])
    return _COMPILED[name]


def keys(tl: Timeline, target: str, channel: str) -> list[tuple[float, Any]]:
    for tr in tl.tracks:
        if (tr.target, tr.channel) == (target, channel):
            return [(float(k[0]), k[1]) for k in tr.keys]
    return []


# ------------------------------------------------------------------- the catalogue itself

def test_there_are_at_least_nine_stories_of_two_to_four_shots_with_a_set_change():
    assert len(STORIES) >= 9
    for name, sp in STORIES.items():
        shots = sp["shots"]
        assert 2 <= len(shots) <= 4, name
        sets = [s["set"] for s in shots]
        assert any(a != b for a, b in zip(sets, sets[1:])), f"{name} never changes set"
        assert all(s["camera"] for s in shots), f"{name} has a shot without camera moves"
        cues = [line for s in shots for line in s["narration"] if line.get("cue")]
        assert cues, f"{name} has no cued narration"


def test_the_stories_use_every_verb_effect_and_camera_move():
    verbs = {a["do"] for sp in STORIES.values() for s in sp["shots"] for a in s["actions"]}
    moves = {c["move"] for sp in STORIES.values() for s in sp["shots"] for c in s["camera"]}
    assert set(VOCABULARY["actions"]) - verbs == set()
    assert set(VOCABULARY["effects"]) - verbs == set()
    assert set(VOCABULARY["camera"]) - moves == set()


def test_the_scene_has_four_to_six_people_of_different_builds_and_speeds():
    sets, props, people = story_scene()
    assert 4 <= len(people) <= 6
    assert len({p.character.build for p in people.values()}) >= 3
    assert len({p.character.notes for p in people.values()}) >= 3
    used_sets = {s["set"] for sp in STORIES.values() for s in sp["shots"]}
    used_props = {p for sp in STORIES.values() for p in sp["props"]}
    assert used_sets <= set(sets) and used_props <= set(props)
    # Fresh objects every call: a test that mutates its copy cannot leak into another.
    assert story_scene()[1]["car"] is not props["car"]


# ---------------------------------------------------------------------------- each story

@pytest.mark.parametrize("name", NAMES)
def test_story_compiles_with_zero_errors(name):
    result = compiled(name)
    assert result.ok, "\n".join(messages(result))
    assert result.errors == []
    assert result.timeline is not None


@pytest.mark.parametrize("name", NAMES)
def test_story_timeline_is_sane(name):
    result = compiled(name)
    assert result.ok, "\n".join(messages(result))
    assert sanity_problems(result.timeline) == []


@pytest.mark.parametrize("name", NAMES)
def test_story_compiles_deterministically(name):
    sp = STORIES[name]
    before = json.dumps(sp, sort_keys=True)
    first, second = compile_story(sp), compile_story(sp)
    assert first.model_dump_json() == second.model_dump_json()
    assert json.dumps(sp, sort_keys=True) == before  # the catalogue is not mutated


@pytest.mark.parametrize("name", NAMES)
def test_story_timeline_round_trips(name):
    tl = compiled(name).timeline
    assert tl is not None
    again = Timeline.model_validate(json.loads(tl.model_dump_json()))
    assert again == tl
    assert again.model_dump_json() == tl.model_dump_json()


@pytest.mark.parametrize("name", NAMES)
def test_story_shots_match_the_screenplay(name):
    tl = compiled(name).timeline
    assert [s.id for s in tl.shots] == [s["id"] for s in STORIES[name]["shots"]]
    assert [s.set for s in tl.shots] == [s["set"] for s in STORIES[name]["shots"]]


# ----------------------------------------------------------------- story-specific checks

def test_moving_house_picks_the_box_up_off_the_floor_at_dads_own_feet():
    """Regression for #160 (pick_up reaches a low grip): the box stands on Dad's own mark."""
    shot = STORIES["moving_house"]["shots"][0]
    assert shot["propStart"]["box_a"]["at"] == shot["start"]["dad"]["at"]
    result = compiled("moving_house")
    assert result.ok, "\n".join(messages(result))
    tl = result.timeline
    parent = keys(tl, "prop:box_a", "parent")
    assert parent[0][1] == "ground"
    t_grab, hand = next((t, v) for t, v in parent if v != "ground")
    assert hand in ("puppet:dad.hand_r", "puppet:dad.hand_l")

    # Where the box stood: the rug mark, on the floor, right under Dad at the start.
    rug_x = 2 * 0.3 - 1
    assert value_at(keys(tl, "prop:box_a", "position"), 0.0)[:2] == pytest.approx([rug_x, 0.0], abs=1e-6)
    assert value_at(keys(tl, "puppet:dad", "root.position"), 0.0)[0] == pytest.approx(rug_x, abs=1e-6)
    # Its grip is halfway up the 0.2-tall box, 0.1 above the floor, far below a standing hand.
    _, props, _ = story_scene()
    box = props["box_a"]
    height = VOCABULARY["sizes"][box.size]
    grip = (rug_x, (box.anchor[1] - box.grip[1]) * height)
    assert grip[1] == pytest.approx(0.1)

    # The hand is on the grip at the moment the parent swaps (the compiler bent or knelt for it).
    rots = {b: value_at(keys(tl, "puppet:dad", f"{b}.rot"), t_grab) or 0.0 for b in ik.RIG["bones"]}
    x, y, _ = value_at(keys(tl, "puppet:dad", "root.position"), t_grab)
    facing = int(value_at(keys(tl, "puppet:dad", "root.facing"), t_grab))
    socket = hand.rsplit(".", 1)[1]
    assert ik.socket_world(socket, rots, (x, y), facing, "tall") == pytest.approx(grip, abs=2e-3)

    # Then he carries it out of the shot and sets it on box_b in the next set.
    later = keys(tl, "prop:box_a", "parent")[-1][1]
    assert later == "prop:box_b.top"


def test_first_car_riders_move_with_the_car():
    tl = compiled("first_car").timeline
    car = keys(tl, "prop:car", "position")
    assert len({tuple(v) for _, v in car}) > 2  # it drives in both shots
    assert keys(tl, "prop:car", "part.wheel_front.rot")  # and its wheels spin


def test_rain_with_no_dur_lasts_its_shot():
    tl = compiled("rainy_walk").timeline
    rains = [e for e in tl.effects if e.kind == "rain"]
    assert len(rains) == 2
    for rain, shot in zip(rains, tl.shots):
        assert rain.dur is not None
        assert rain.t + rain.dur == pytest.approx(shot.end, abs=1e-6)


def test_birthday_candles_stay_lit_into_the_backyard():
    tl = compiled("birthday").timeline
    state = keys(tl, "prop:cake", "state")
    assert state[0][1] == "unlit"
    assert state[-1][1] == "lit"
    assert keys(tl, "prop:cake", "parent")[-1][1] == "prop:picnic_table.top"


def _ground_x(tl: Timeline, prop: str, t: float) -> float | None:
    """The prop's x at ``t`` when it is on the ground, else None."""
    if value_at(keys(tl, f"prop:{prop}", "parent"), t) != "ground":
        return None
    return value_at(keys(tl, f"prop:{prop}", "position"), t)[0]


def _mid(tl: Timeline, shot_id: str) -> float:
    shot = next(s for s in tl.shots if s.id == shot_id)
    return (shot.start + shot.end) / 2


def test_moving_day_leaves_the_boxes_in_the_old_house():
    """box_b and the armchair stay in the old living room: offstage on the new porch, and when the
    living room comes back only its own propStart (the armchair) is on stage again."""
    tl = compiled("moving_day").timeline
    assert tl is not None
    assert [s.set for s in tl.shots] == ["living_room", "new_house_porch", "living_room"]
    hall, armchair_spot = 2 * 0.92 - 1, 2 * 0.5 - 1
    assert _ground_x(tl, "box_b", _mid(tl, "old_living_room")) == pytest.approx(hall)
    for prop in ("box_b", "armchair"):
        assert _ground_x(tl, prop, _mid(tl, "new_porch")) <= -1.0, prop
    assert _ground_x(tl, "box_b", _mid(tl, "one_last_look")) <= -1.0
    assert _ground_x(tl, "armchair", _mid(tl, "one_last_look")) == pytest.approx(armchair_spot)
    # box_a went along in Dad's hands and was stacked on box_c, which only the porch has; both
    # stay on the porch when the film cuts back to the old house.
    porch_end = next(s for s in tl.shots if s.id == "new_porch").end - 0.01
    assert value_at(keys(tl, "prop:box_a", "parent"), porch_end) == "prop:box_c.top"
    assert _ground_x(tl, "box_a", _mid(tl, "one_last_look")) <= -1.0
    assert _ground_x(tl, "box_c", _mid(tl, "old_living_room")) <= -1.0
    assert _ground_x(tl, "box_c", _mid(tl, "one_last_look")) <= -1.0


def test_rainy_sunday_leaves_the_box_at_home_and_the_umbrella_on_the_steps():
    tl = compiled("rainy_sunday").timeline
    assert tl is not None
    assert [s.set for s in tl.shots] == ["new_house_porch", "church_steps", "chapel"]
    assert _ground_x(tl, "box_c", _mid(tl, "the_porch")) == pytest.approx(2 * 0.9 - 1)
    for shot in ("the_steps", "the_pew_inside"):
        assert _ground_x(tl, "box_c", _mid(tl, shot)) <= -1.0, shot
    # The umbrella is dropped on the church steps and stays there, so the chapel has none.
    steps_x = _ground_x(tl, "umbrella", next(s for s in tl.shots if s.id == "the_steps").end - 0.01)
    assert steps_x is not None and abs(steps_x) <= 1.0
    assert _ground_x(tl, "umbrella", _mid(tl, "the_pew_inside")) <= -1.0
    assert value_at(keys(tl, "prop:hymn_books", "parent"), _mid(tl, "the_pew_inside")).startswith("puppet:grandpa.")


def test_wedding_has_both_tellings():
    tl = compiled("wedding").timeline
    assert tl.default_narrator == "dad"
    assert set(tl.narrations or {}) == {"dad", "mom"}


# ------------------------------------------------------------ the sanity checks catch things

def _timeline(tracks: list[dict], **extra: Any) -> Timeline:
    """A tiny one-shot timeline: dad standing at x = 0 and a cup on the ground, plus ``tracks``."""
    base = [
        {"target": "puppet:dad", "channel": "root.position", "keys": [[0, [0.0, 0.0, 0.5]]]},
        {"target": "puppet:dad", "channel": "root.facing", "keys": [[0, 1]]},
        {"target": "prop:cup", "channel": "parent", "keys": [[0, "ground"]]},
        {"target": "prop:cup", "channel": "position", "keys": [[0, [0.3, 0.0, 0.5]]]},
    ]
    replaced = {(t["target"], t["channel"]) for t in tracks}
    data = {
        "duration": 5.0,
        "tracks": [t for t in base if (t["target"], t["channel"]) not in replaced] + tracks,
        "shots": [{"id": "s1", "set": "kitchen", "start": 0.0, "actionStart": 1.2, "end": 5.0}],
        **extra,
    }
    return Timeline.model_validate(data)


def only(problems: list[str], check: str) -> list[str]:
    assert problems, "expected a problem"
    assert all(p.startswith(f"{check}: ") for p in problems), problems
    return problems


def test_a_clean_hand_built_timeline_has_no_problems():
    tl = _timeline([
        {"target": "prop:cup", "channel": "parent", "keys": [[0, "ground"], [2, "puppet:dad.hand_r"], [4, "ground"]]},
        {"target": "prop:cup", "channel": "position",
         "keys": [[0, [0.3, 0.0, 0.5]], [2, [0, 0, 0]], [4, [0.1, 0.0, 0.5]]]},
    ])
    assert sanity_problems(tl) == []
    assert [c.__name__ for c in CHECKS] == [
        "check_hands", "check_parent_position", "check_times", "check_seated", "check_finite", "check_props_on_set",
    ]


# (a) no prop in two hands
def test_hands_catches_two_props_in_one_hand():
    tl = _timeline([
        {"target": "prop:cup", "channel": "parent", "keys": [[0, "ground"], [2, "puppet:dad.hand_r"]]},
        {"target": "prop:cup", "channel": "position", "keys": [[0, [0.3, 0.0, 0.5]], [2, [0, 0, 0]]]},
        {"target": "prop:keys", "channel": "parent", "keys": [[0, "ground"], [3, "puppet:dad.hand_r"]]},
        {"target": "prop:keys", "channel": "position", "keys": [[0, [0.4, 0.0, 0.5]], [3, [0, 0, 0]]]},
    ])
    problems = only(sanity_problems(tl), "hands")
    assert problems == ["hands: t=3: hand puppet:dad.hand_r holds 2 props: cup, keys"]


def test_hands_catches_a_prop_with_two_parents_at_one_time():
    tl = _timeline([
        {"target": "prop:cup", "channel": "parent",
         "keys": [[0, "ground"], [2, "puppet:dad.hand_r"], [2, "puppet:dad.hand_l"]]},
        {"target": "prop:cup", "channel": "position", "keys": [[0, [0.3, 0.0, 0.5]], [2, [0, 0, 0]]]},
    ])
    problems = only(sanity_problems(tl), "hands")
    assert problems == [
        "hands: t=2: prop:cup has two parents at once: 'puppet:dad.hand_r' and 'puppet:dad.hand_l'"
    ]


def test_hands_catches_a_prop_resting_on_itself_through_another():
    tl = _timeline([
        {"target": "prop:box_a", "channel": "parent", "keys": [[0, "ground"], [1, "prop:box_b.top"]]},
        {"target": "prop:box_a", "channel": "position", "keys": [[0, [0.0, 0.0, 0.5]], [1, [0, 0, 0]]]},
        {"target": "prop:box_b", "channel": "parent", "keys": [[0, "ground"], [1, "prop:box_a.top"]]},
        {"target": "prop:box_b", "channel": "position", "keys": [[0, [0.2, 0.0, 0.5]], [1, [0, 0, 0]]]},
    ])
    problems = only(sanity_problems(tl), "hands")
    assert any("prop:box_a is its own ancestor: box_a -> box_b -> box_a" in p for p in problems)


# (b) parent keys paired with position keys
def test_parent_position_catches_a_parent_key_without_a_position_key():
    tl = _timeline([
        {"target": "prop:cup", "channel": "parent", "keys": [[0, "ground"], [2, "puppet:dad.hand_r"]]},
        {"target": "prop:cup", "channel": "position", "keys": [[0, [0.3, 0.0, 0.5]], [2.5, [0, 0, 0]]]},
    ])
    assert only(sanity_problems(tl), "parent_position") == [
        "parent_position: t=2: prop:cup parent key 'puppet:dad.hand_r' has no position key at the same time"
    ]


# (c) all times within duration
def test_times_catches_a_key_after_the_end():
    tl = _timeline([{"target": "puppet:dad", "channel": "head.rot", "keys": [[1, 0.0], [6, -0.4]]}])
    assert only(sanity_problems(tl), "times") == ["times: puppet:dad head.rot key at t=6 is outside [0, 5]"]


def test_times_catches_effects_captions_and_words_outside_the_timeline():
    tl = _timeline([], effects=[{"t": 4.0, "kind": "confetti", "dur": 2.0}], captions=[{"t": -1.0, "text": "early"}],
                   narrations={"dad": {"authorId": "dad", "lines": [
                       {"t": 4.5, "text": "too late", "words": [{"t": 4.5, "word": "too"}, {"t": 5.5, "word": "late"}]},
                   ]}})
    assert only(sanity_problems(tl), "times") == [
        "times: confetti effect end at t=6 is outside [0, 5]",
        "times: caption 'early' at t=-1 is outside [0, 5]",
        "times: dad's word 'late' at t=5.5 is outside [0, 5]",
    ]


def test_times_catches_unsorted_keys_and_broken_shots():
    tl = Timeline.model_validate({
        "duration": 10.0,
        "tracks": [{"target": "puppet:dad", "channel": "head.rot", "keys": [[3, 0.0], [2, -0.4]]}],
        "shots": [
            {"id": "s1", "set": "kitchen", "start": 0.0, "actionStart": 1.2, "end": 4.0},
            {"id": "s2", "set": "park", "start": 4.5, "actionStart": 4.0, "end": 9.0},
        ],
    })
    assert only(sanity_problems(tl), "times") == [
        "times: puppet:dad head.rot: key at t=2 comes after one at t=3",
        "times: shot s2: start 4.5, actionStart 4, end 9 out of order",
        "times: shots not contiguous: s1 ends at 4, s2 starts at 4.5",
        "times: last shot s2 ends at 9, duration is 10",
    ]


# (d) nobody walking while seated
def _seated(root_keys: list, extra: list[dict] | None = None) -> Timeline:
    legs = [{"target": "puppet:dad", "channel": f"{b}.rot", "keys": [[0, v], [4, v]]} for b, v in SIT.items()]
    return _timeline([{"target": "puppet:dad", "channel": "root.position", "keys": root_keys}, *legs, *(extra or [])])


def test_seated_catches_a_seated_puppet_sliding_with_no_vehicle():
    tl = _seated([[0, [0.0, 0.08, 0.5]], [3, [0.4, 0.08, 0.5]]])
    assert only(sanity_problems(tl), "seated") == [
        "seated: t=0 to t=3: puppet:dad is seated but moves (+0.400, +0.000) and no prop moves with them"
    ]


def test_seated_lets_a_rider_move_with_the_vehicle():
    car = [
        {"target": "prop:car", "channel": "parent", "keys": [[0, "ground"]]},
        {"target": "prop:car", "channel": "position", "keys": [[0, [-0.5, 0.0, 0.5]], [3, [-0.1, 0.0, 0.5]]]},
    ]
    assert sanity_problems(_seated([[0, [0.0, 0.08, 0.5]], [3, [0.4, 0.08, 0.5]]], car)) == []
    # A car that moves a different distance does not carry them.
    car[1]["keys"][1] = [3, [0.0, 0.0, 0.5]]
    assert only(sanity_problems(_seated([[0, [0.0, 0.08, 0.5]], [3, [0.4, 0.08, 0.5]]], car)), "seated")


def test_seated_catches_walk_cycle_legs_while_seated():
    walk = [{"target": "puppet:dad", "channel": "leg_l.upper.rot", "keys": [[0, 1.5], [1, 0.45], [2, 0.1], [4, 1.5]]}]
    tl = _seated([[0, [0.0, 0.08, 0.5]], [4, [0.0, 0.08, 0.5]]], walk)
    assert only(sanity_problems(tl), "seated") == [
        "seated: t=1: puppet:dad is seated but leg_l.upper takes walk/run cycle value 0.45",
        "seated: t=2: puppet:dad is seated but leg_l.upper takes walk/run cycle value 0.1",
    ]


# (e) no NaN or inf, facing 1 or -1
def test_finite_catches_nan_and_inf():
    tl = _timeline([
        {"target": "puppet:dad", "channel": "root.position", "keys": [[0, [0.0, 0.0, 0.5]], [1, [math.nan, 0.0, 0.5]]]},
        {"target": "puppet:dad", "channel": "arm_r.upper.rot", "keys": [[0, math.inf]]},
    ])
    problems = only(sanity_problems(tl), "finite")
    assert any("root.position" not in p and "is inf" in p for p in problems), problems
    assert any(p.endswith("is nan") for p in problems), problems


def test_finite_catches_a_bad_facing_and_a_short_position():
    tl = _timeline([
        {"target": "puppet:dad", "channel": "root.facing", "keys": [[0, 1], [2, 0]]},
        {"target": "prop:cup", "channel": "position", "keys": [[0, [0.3, 0.0]]]},
    ])
    assert only(sanity_problems(tl), "finite") == [
        "finite: t=2: puppet:dad root.facing is 0.0, not 1 or -1",
        "finite: t=0: prop:cup position is [0.3, 0.0], not [x, y, z]",
    ]


def test_value_at_steps_strings_and_interpolates_numbers():
    k = [(0.0, 0.0), (2.0, 1.0), (2.0, 3.0), (4.0, 5.0)]
    assert value_at(k, -1) == 0.0
    assert value_at(k, 1) == pytest.approx(0.5)
    assert value_at(k, 2, before=True) == pytest.approx(1.0)
    assert value_at(k, 2) == 3.0
    assert value_at(k, 3) == pytest.approx(4.0)
    assert value_at(k, 9) == 5.0
    assert value_at([(0.0, "ground"), (1.0, "puppet:dad.hand_r")], 0.5) == "ground"
    assert value_at([(0.0, [0.0, 0.0, 0.0]), (2.0, [2.0, 0.0, 1.0])], 1.0) == [1.0, 0.0, 0.5]
    assert value_at([], 1.0) is None


# ---------------------------------------------------------------- compiler bugs found here

def _one_shot(cast: list[str], props: list[str], set_id: str, shot: dict) -> dict:
    return {"memoryId": "repro", "cast": cast, "props": props,
            "shots": [{"id": "s1", "set": set_id, "camera": [], "narration": [], **shot}]}


def test_the_same_floor_gift_can_be_picked_up_standing():
    """Control for the xfail below: pick_up bends to this exact prop (#160)."""
    result = compile_story(_one_shot(["kid"], ["gift"], "living_room", {
        "start": {"kid": {"at": "rug", "facing": "right"}}, "marks": {"gift_spot": [0.34, 0.0]},
        "propStart": {"gift": {"at": "gift_spot", "state": "closed"}},
        "actions": [{"t": 0.5, "do": "pick_up", "who": "kid", "what": "gift"}],
    }))
    assert result.ok, messages(result)


@pytest.mark.xfail(strict=True, reason=(
    'open/close do not bend or kneel to a prop on the floor (pick_up and place_on do): '
    '"shot s1, t=0.5: kid cannot open "gift"; kid\'s arm_r cannot reach: target is 0.17 from the shoulder, '
    'reach is 0.14" for a gift 0.08 in front of the kid\'s feet, which pick_up reaches. A director writes '
    '"drop the gift, then open it" (the christmas story kneels first instead). The fix is parked on '
    'origin/jonah/compiler-open-close-floor-reach pending the agent stream\'s test and doc changes '
    '(api/tests/agent/test_agent_skills.py::test_open_does_not_reach_a_carried_prop_on_the_floor_or_a_table '
    'pins the current behaviour).'
))
def test_open_a_gift_on_the_floor_standing():
    result = compile_story(_one_shot(["kid"], ["gift"], "living_room", {
        "start": {"kid": {"at": "rug", "facing": "right"}}, "marks": {"gift_spot": [0.34, 0.0]},
        "propStart": {"gift": {"at": "gift_spot", "state": "closed"}},
        "actions": [{"t": 0.5, "do": "open", "who": "kid", "what": "gift"}],
    }))
    assert result.ok, messages(result)


@pytest.mark.xfail(strict=True, reason=(
    'open does not turn the opener toward the prop (pick_up and hand_to do): kneeling mom facing left, with '
    'a box on the floor 0.1 to her right, gets "shot s1, t=2: mom cannot open "box_c"; mom\'s arm_r cannot '
    'reach: target is 0.22 from the shoulder, reach is 0.17"; facing right, the same open compiles '
    '(the moving_house story starts her facing the box instead). The fix is parked on '
    'origin/jonah/compiler-open-close-floor-reach pending the agent stream\'s test and doc changes '
    '(api/tests/agent/test_agent_skills.py::test_open_does_not_reach_a_carried_prop_on_the_floor_or_a_table '
    'pins the current behaviour).'
))
def test_open_a_box_on_the_floor_behind_you():
    result = compile_story(_one_shot(["mom"], ["box_c"], "new_house_porch", {
        "start": {"mom": {"at": "porch_door", "facing": "left"}}, "marks": {"bc": [0.8, 0.0]},
        "propStart": {"box_c": {"at": "bc"}},
        "actions": [{"t": 0.2, "do": "kneel", "who": "mom"}, {"t": 2, "do": "open", "who": "mom", "what": "box_c"}],
    }))
    assert result.ok, messages(result)


def test_the_box_behind_mom_opens_when_she_faces_it():
    """Control for the xfail above."""
    result = compile_story(_one_shot(["mom"], ["box_c"], "new_house_porch", {
        "start": {"mom": {"at": "porch_door", "facing": "right"}}, "marks": {"bc": [0.8, 0.0]},
        "propStart": {"box_c": {"at": "bc"}},
        "actions": [{"t": 0.2, "do": "kneel", "who": "mom"}, {"t": 2, "do": "open", "who": "mom", "what": "box_c"}],
    }))
    assert result.ok, messages(result)


# ------------------------------------------------------------- props across sets (wave 3)

def _wave_shot(sid: str, set_id: str, start: dict, prop_start: dict) -> dict:
    who = next(iter(start))
    return {"id": sid, "set": set_id, "start": start, "propStart": prop_start, "camera": [], "narration": [],
            "actions": [{"t": 0.5, "do": "wave", "who": who}]}


def test_a_prop_put_back_at_another_x_on_a_new_set_is_keyed_there():
    """Control for the xfail below: back_door is x = -0.84 in the kitchen, rug x = -0.4 in the
    living room, so the cup steps there at the cut."""
    result = compile_story({"memoryId": "repro", "cast": ["kid"], "props": ["cup"], "shots": [
        _wave_shot("s1", "kitchen", {"kid": {"at": "by_table"}}, {"cup": {"at": "back_door"}}),
        _wave_shot("s2", "living_room", {"kid": {"at": "front_door"}}, {"cup": {"at": "rug"}}),
    ]})
    assert result.ok, messages(result)
    assert sanity_problems(result.timeline) == []
    assert keys(result.timeline, "prop:cup", "position")[-1] == (4.0, [-0.4, 0.0, 0.5])


@pytest.mark.xfail(strict=True, reason=(
    "compiler.py _start_keys (the propStart loop, via _hold_then, which skips an unchanged value): a "
    "propStart on a new set that puts a prop at the same place it stood on the old set writes no key, so "
    "the timeline cannot tell it from a prop left over from the old set: the kitchen's back_door and the "
    "living room's front_door are both x = -0.84, and sanity_problems gives 'props_on_set: shot s2 "
    "(living_room) at t=4: prop:cup is on the ground at x=-0.84 but has no parent or position key in this "
    "shot, nor in an earlier shot of this set'. It renders correctly; on a set change _start_keys should "
    "key every propStart prop (a parent and a position key at the shot's start) even when unchanged. "
    "test_compile_fuzz's StoryGen steers around it (StoryGen.begin, was_at)."
))
def test_a_prop_put_back_at_the_same_x_on_a_new_set_is_keyed_there():
    result = compile_story({"memoryId": "repro", "cast": ["kid"], "props": ["cup"], "shots": [
        _wave_shot("s1", "kitchen", {"kid": {"at": "by_table"}}, {"cup": {"at": "back_door"}}),
        _wave_shot("s2", "living_room", {"kid": {"at": "rug"}}, {"cup": {"at": "front_door"}}),
    ]})
    assert result.ok, messages(result)
    assert sanity_problems(result.timeline) == []


@pytest.mark.xfail(strict=True, reason=(
    "compiler.py _start_keys (the start `holding` keys, which are appended as they are instead of going "
    "through _hold_then): a prop left on the kitchen floor is parked offstage at the cut to the living "
    "room (position [-1.3, 0, 0.5] at t=4), then held from the start of the backyard shot (position "
    "[0, 0, 0] at t=7.9) with no hold key before it, so through the whole living-room shot it glides along "
    "the ground from offstage to centre stage (x = -0.65 halfway). With a shot between the two that has no "
    "key for it, sanity_problems flags it too ('props_on_set: ... prop:cup is on the ground at x=...'), "
    "which the fuzz finds outside the default corpus (story seeds 559 and 672 of a 1000-story sweep). "
    "The start holding keys should hold the old value up to the shot's start and step there."
))
def test_a_prop_held_in_a_later_shot_does_not_glide_in_from_offstage():
    result = compile_story({"memoryId": "repro", "cast": ["kid"], "props": ["cup"], "shots": [
        _wave_shot("s1", "kitchen", {"kid": {"at": "by_table"}}, {"cup": {"at": "window"}}),
        _wave_shot("s2", "living_room", {"kid": {"at": "rug"}}, {}),
        _wave_shot("s3", "backyard", {"kid": {"at": "lawn_left", "holding": "cup"}}, {}),
    ]})
    assert result.ok, messages(result)
    tl = result.timeline
    s2 = tl.shots[1]
    for t in (s2.start + 0.1, (s2.start + s2.end) / 2, s2.end - 0.1):
        assert value_at(keys(tl, "prop:cup", "position"), t)[0] <= -1.0, t  # offstage through s2
