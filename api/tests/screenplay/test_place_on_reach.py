"""place_on reaches like pick_up: it turns to face its destination, steps back from a mark under
its own feet, and lets arm_l fold as deep as pick_up does (verbs_objects.apply_place_on).

The first three tests are the compile fuzz's reproductions (test_compile_fuzz.py on the
fuzz-sanity-bughunt branch, bugs B2, B3 and B10), verbatim, now passing; each is then checked
for a sane timeline and, where the puppet steps back, for planted feet.
"""

from __future__ import annotations

from typing import Any

import pytest

from screenplay import ik
from screenplay.body import REACH_SECONDS
from screenplay.compiler import compile as compile_screenplay
from screenplay.schema import CompileResult, Screenplay, Timeline
from screenplay.verbs_objects import PLACE_FACE_DEAD_ZONE, PLACE_STEP_BACK_MAX
from test_foot_planting import planting_problems
from timeline_sanity import sanity_problems, value_at
from verb_cases import CAST, PROPS, YARD_MARKS, Scene, base_scene


def _compile(sp: Screenplay, scene: Scene, **kw: Any) -> CompileResult:
    return compile_screenplay(sp, sets=scene.sets, props=scene.props, people=scene.people, **kw)


def _repro(*shots: dict, scene: Scene | None = None, plant_feet: bool = True) -> CompileResult:
    scene = scene or base_scene()
    sp = Screenplay.model_validate({"memoryId": "repro", "cast": list(CAST), "props": list(PROPS), "shots": list(shots)})
    return _compile(sp, scene, plant_feet=plant_feet)


def _shot(sid: str, set_id: str, start: dict, actions: list[dict], prop_start: dict | None = None,
          marks: dict | None = None) -> dict:
    shot = {"id": sid, "set": set_id, "start": start, "propStart": prop_start or {}, "actions": actions}
    if marks:
        shot["marks"] = marks
    return shot


def _keys(tl: Timeline, target: str, channel: str) -> list[tuple[float, Any]]:
    return next(([(float(k[0]), k[1]) for k in tr.keys] for tr in tl.tracks
                 if (tr.target, tr.channel) == (target, channel)), [])


def _errors(result: CompileResult) -> list[str]:
    return [f"{e.code}: {e.message}" for e in result.errors]


# ------------------------------------------------------------------ the fuzz repros

def test_place_on_the_mark_you_stand_on():
    result = _repro(_shot("s1", "yard", {"dad": {"at": "door", "holding": "cap"}},
                          [{"t": 1, "do": "place_on", "who": "dad", "what": "cap", "on": "door"}]))
    assert result.ok, _errors(result)


def test_place_on_a_mark_behind_you():
    result = _repro(_shot("s1", "yard", {"dad": {"at": "by_table", "holding": "cap", "facing": "left"}},
                          [{"t": 1, "do": "place_on", "who": "dad", "what": "cap", "on": "table_spot"}]))
    assert result.ok, _errors(result)


def test_put_a_prop_back_down_with_the_left_hand():
    result = _repro(_shot("s1", "yard", {"dad": {"at": "door", "facing": "right", "holding": "hymn_books"}},
                          [{"t": 1, "do": "pick_up", "who": "dad", "what": "cap"},
                           {"t": 7, "do": "place_on", "who": "dad", "what": "cap", "on": "spot"}],
                          prop_start={"cap": {"at": "spot"}}, marks={"spot": [0.16, 0.0]}))
    assert result.ok, _errors(result)


# The repros again, each with its timeline checked: sane, and planted feet (the planted compile is
# the unplanted one with root y lowered at walk cycle keys; test_foot_planting.planting_problems).

AT_THE_FEET = _shot("s1", "yard", {"dad": {"at": "door", "holding": "cap"}},
                    [{"t": 1, "do": "place_on", "who": "dad", "what": "cap", "on": "door"}])
BEHIND = _shot("s1", "yard", {"dad": {"at": "by_table", "holding": "cap", "facing": "left"}},
               [{"t": 1, "do": "place_on", "who": "dad", "what": "cap", "on": "table_spot"}])
LEFT_HAND = _shot("s1", "yard", {"dad": {"at": "door", "facing": "right", "holding": "hymn_books"}},
                  [{"t": 1, "do": "pick_up", "who": "dad", "what": "cap"},
                   {"t": 7, "do": "place_on", "who": "dad", "what": "cap", "on": "spot"}],
                  prop_start={"cap": {"at": "spot"}}, marks={"spot": [0.16, 0.0]})
REPROS = {"at_the_feet": AT_THE_FEET, "behind": BEHIND, "left_hand": LEFT_HAND}
BUILDS = {pid: p.character.build for pid, p in base_scene().people.items()}


def _mark_x(u: float) -> float:
    return round(2 * u - 1, 6)  # a mark's stage x (CONTRACTS.md: marks are [u, y], x = 2u - 1)


@pytest.mark.parametrize("name", sorted(REPROS))
def test_repro_timeline_is_sane_and_planted(name):
    planted, unplanted = _repro(REPROS[name]), _repro(REPROS[name], plant_feet=False)
    assert planted.ok and unplanted.ok, _errors(planted) + _errors(unplanted)
    assert planted.timeline is not None and unplanted.timeline is not None
    assert sanity_problems(planted.timeline) == []
    assert planting_problems(planted.timeline, unplanted.timeline, BUILDS) == []


def test_at_the_feet_steps_back_turns_back_and_lays_the_cap_on_the_mark():
    tl = _repro(AT_THE_FEET).timeline
    assert tl is not None
    door = _mark_x(YARD_MARKS["door"][0])
    root = _keys(tl, "puppet:dad", "root.position")
    t0 = root[1][0]  # the action's start: the root is held there, then walks
    stepped = root[2][1]
    # a short step back (dad faces right, so back is left), behind the grip, on the same ground
    assert door - PLACE_STEP_BACK_MAX <= stepped[0] < door
    assert stepped[1] == 0.0 and root[-1][1] == stepped  # and back up on the same spot
    facing = _keys(tl, "puppet:dad", "root.facing")
    assert facing[0] == (0.0, 1) and facing[-1][1] == 1  # walked off facing left, turned back right
    assert [f for _, f in facing].count(-1) == 1 and facing[1][0] == t0
    t_let_go = next(t for t, v in _keys(tl, "prop:cap", "parent") if v == "ground")
    assert value_at(_keys(tl, "prop:cap", "position"), t_let_go) == [door, 0.0, 0.5]


def test_behind_turns_once_at_t_and_does_not_move():
    tl = _repro(BEHIND).timeline
    assert tl is not None
    facing = _keys(tl, "puppet:dad", "root.facing")
    assert facing == [(0.0, -1), (round(tl.shots[0].action_start + 1, 3), 1)]
    assert {round(v[0], 6) for _, v in _keys(tl, "puppet:dad", "root.position")} == {_mark_x(YARD_MARKS["by_table"][0])}


def test_left_hand_bends_deeper_than_the_plain_crouch():
    from screenplay.verb_helpers import CROUCH_MAX_BEND
    from screenplay.verbs_core import CROUCH_DEEP_BEND

    tl = _repro(LEFT_HAND).timeline
    assert tl is not None
    deepest = min(v for _, v in _keys(tl, "puppet:dad", "torso.rot"))
    assert -CROUCH_DEEP_BEND <= deepest < -CROUCH_MAX_BEND
    assert value_at(_keys(tl, "prop:hymn_books", "parent"), tl.duration) == "puppet:dad.hand_r"
    assert value_at(_keys(tl, "prop:cap", "parent"), tl.duration) == "ground"



# ------------------------------------------------------------------ turning to face

@pytest.mark.parametrize(("behind", "turns"), [(0.0, False), (0.015, False), (0.019, False), (0.021, True),
                                                (0.05, True), (0.1, True)])
def test_a_grip_behind_past_the_dead_zone_turns_once_at_t_and_takes_no_time(behind, turns):
    """The table top's grip is ``behind`` stage units behind dad's root (he faces right). Past
    PLACE_FACE_DEAD_ZONE he turns: one root.facing key at t, and the place_on lasts as long."""
    assert PLACE_FACE_DEAD_ZONE == 0.02
    u = (_mark_x(YARD_MARKS["table_spot"][0]) + behind + 1) / 2
    result = _repro(_shot("s1", "yard", {"dad": {"at": "here", "holding": "hymn_books", "facing": "right"}},
                          [{"t": 1, "do": "place_on", "who": "dad", "what": "hymn_books", "on": "table"}],
                          prop_start={"table": {"at": "table_spot"}}, marks={"here": [u, 0.0]}))
    assert result.ok, _errors(result)
    tl = result.timeline
    assert tl is not None and sanity_problems(tl) == []
    t = round(tl.shots[0].action_start + 1, 3)
    assert _keys(tl, "puppet:dad", "root.facing") == [(0.0, 1)] + ([(t, -1)] if turns else [])
    assert _keys(tl, "puppet:dad", "arm_r.upper.rot")[-1][0] == round(t + 2 * REACH_SECONDS, 3)  # no time added
    assert not _keys(tl, "puppet:dad", "torso.rot")  # a plain reach: no crouch


def test_the_durations_with_and_without_a_turn_are_the_same():
    def duration(behind: float) -> float:
        u = (_mark_x(YARD_MARKS["table_spot"][0]) + behind + 1) / 2
        tl = _repro(_shot("s1", "yard", {"dad": {"at": "here", "holding": "hymn_books", "facing": "right"}},
                          [{"t": 1, "do": "place_on", "who": "dad", "what": "hymn_books", "on": "table"}],
                          prop_start={"table": {"at": "table_spot"}}, marks={"here": [u, 0.0]})).timeline
        assert tl is not None
        return tl.duration
    assert duration(0.0) == duration(0.05) == duration(-0.05)


# --------------------------------------------- handler level: who steps, and atomicity

def _handler():
    """test_verbs_objects' fixtures: dad (tall) at "home" (x = 0), marks near (0.15 in front),
    behind (-0.2), far (0.8); run() calls the handler at T, out_of_reach() asserts the world and
    tracks are untouched."""
    import test_verbs_objects as V
    return V


def test_a_kneeling_puppet_turns_to_a_grip_behind_it_and_only_bends():
    V = _handler()
    tracks = V.TrackSet()
    # kneeling facing left: the root is down, offset to the left (body.kneel_keys)
    low = [round(-V.KNEEL_ROOT[0] * ik.scale("tall"), 3), round(V.KNEEL_ROOT[1] * ik.scale("tall"), 3), 0.5]
    tracks.extend([("puppet:dad", "root.position", 0.0, low)])
    w = V.make_world({"dad": {"at": "home", "holding": "hat", "pose": "kneel", "facing": "left"}})
    V.run(V.apply_place_on, w, tracks, do="place_on", who="dad", what="hat", on="near")
    assert V.keys_of(tracks, "dad", "root.facing") == [(round(V.T, 3), 1)]
    assert V.keys_of(tracks, "dad", "root.position") == [(0.0, low)]  # never steps (nor kneels again)
    assert not any(tr.channel.startswith("leg_") for tr in tracks.tracks())
    assert w.people["dad"].pos == V.MARKS["home"] and w.people["dad"].facing == 1
    assert w.people["dad"].pose == "kneel" and w.props["hat"].world_pos == V.MARKS["near"]


@pytest.mark.parametrize("start", [
    {"dad": {"at": "home", "holding": "hat", "pose": "kneel"}},
    {"dad": {"at": "home", "holding": "hat", "in": "cushion.seat"}},
], ids=["kneeling", "seated"])
def test_a_kneeling_or_seated_puppet_never_steps_back_from_its_own_feet(start):
    """Standing, the same place_on steps back (see the at-the-feet repro); down, it keeps
    today's behaviour: the crouch misses, and nothing changes."""
    V = _handler()
    tracks = V.TrackSet()
    w = V.make_world(start, {"cushion": {"at": "home"}})
    err = V.out_of_reach(V.apply_place_on, w, tracks, do="place_on", who="dad", what="hat", on="home")
    assert (err.who, err.arm) == ("dad", "arm_r") and err.distance > err.reach
    assert w.people["dad"].pos == V.MARKS["home"] and w.holder("hat") == ("dad", "hand_r")


def test_standing_at_its_own_feet_it_steps_back_behind_the_grip():
    V = _handler()
    tracks = V.TrackSet()
    w = V.make_world({"dad": {"at": "home", "holding": "hat", "facing": "right"}})
    V.run(V.apply_place_on, w, tracks, do="place_on", who="dad", what="hat", on="home")
    x, y, z = w.people["dad"].pos
    assert -PLACE_STEP_BACK_MAX <= x < V.MARKS["home"][0] and (y, z) == V.MARKS["home"][1:]
    assert w.people["dad"].facing == 1 and w.people["dad"].pose == "stand"
    assert w.props["hat"].world_pos == V.MARKS["home"] and w.holder("hat") is None
    facing = V.keys_of(tracks, "dad", "root.facing")
    assert facing[0] == (round(V.T, 3), -1) and facing[-1][1] == 1 and len(facing) == 2


@pytest.mark.parametrize(("facing", "behind"), [("right", False), ("left", True)])
def test_a_grip_too_far_is_out_of_reach_with_no_keys_and_no_turn(facing, behind):
    """No walk toward it, no step back from it, and (behind) no turn kept: atomic."""
    V = _handler()
    tracks = V.TrackSet()
    w = V.make_world({"dad": {"at": "home", "holding": "hat", "facing": facing}})
    err = V.out_of_reach(V.apply_place_on, w, tracks, do="place_on", who="dad", what="hat", on="far")
    assert (err.who, err.arm) == ("dad", "arm_r") and err.distance > err.reach
    assert w.people["dad"].facing == (-1 if behind else 1) and w.people["dad"].pos == V.MARKS["home"]
    assert V.dump(tracks) == []


def test_a_grip_too_far_is_still_the_pinned_out_of_reach_through_the_compiler():
    from verb_cases import CASES

    case = next(c for c in CASES if c.name == "place_on:out_of_reach")
    result = _compile(case.screenplay, case.scene)
    assert [(e.code, e.message) for e in result.errors] == [(case.code, case.message)]
    assert result.timeline is None or not any(
        tr.channel == "root.facing" and len(tr.keys) > 1 for tr in result.timeline.tracks)


def test_a_step_back_from_a_raised_mark_stays_at_its_height():
    """Dad on a mark 0.3 up sets the cap down at his own feet: he steps back along the same
    height, behind the grip, and comes back up there."""
    result = _repro(_shot("s1", "yard", {"dad": {"at": "stage", "holding": "cap", "facing": "right"}},
                          [{"t": 1, "do": "place_on", "who": "dad", "what": "cap", "on": "stage"}],
                          marks={"stage": [0.4, 0.3]}))
    assert result.ok, _errors(result)
    tl = result.timeline
    assert tl is not None and sanity_problems(tl) == []
    root = _keys(tl, "puppet:dad", "root.position")
    stage_x = _mark_x(0.4)
    assert stage_x - PLACE_STEP_BACK_MAX <= root[-1][1][0] < stage_x and root[-1][1][1] == 0.3
    assert value_at(_keys(tl, "prop:cap", "position"), tl.duration) == [stage_x, 0.3, 0.5]
    assert planting_problems(tl, _repro(_shot("s1", "yard", {"dad": {"at": "stage", "holding": "cap", "facing": "right"}},
                                              [{"t": 1, "do": "place_on", "who": "dad", "what": "cap", "on": "stage"}],
                                              marks={"stage": [0.4, 0.3]}), plant_feet=False).timeline, BUILDS) == []
