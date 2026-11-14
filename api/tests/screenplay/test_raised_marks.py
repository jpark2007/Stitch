"""Raised marks (shared/CONTRACTS.md decision 3: a mark is [x, y], y the standing surface's height).

pick_up of a prop on another surface than the puppet's (fuzz bug B5): verbs_core's walk-up and
every candidate spot of _reach_spot used to keep the puppet's own height, so from the church
steps or a stage a prop on the floor below was never reached, and where it was reached the puppet
stood off its mark in mid-air. Now the spots may be on the surface the prop rests on
(verbs_core.resting_surface_y: its anchor y on the ground, the ground y of the prop at the bottom
of its stack on a socket), reached in one walk from the puppet's own height
(verbs_core._approach_ys): a prop below is approached on its own surface (the puppet steps down
and stands on the floor); a prop above is reached from the puppet's own surface when it can be,
else from the prop's surface. Either only within CLIMB_LIMIT of the puppet's own height up or
down: a shelf further up is still out of reach. A prop on the puppet's own surface compiles to
exactly the keys it did before.

Then the bug hunt over verbs_core and verbs_motion on raised marks: enter, exit, follow, drop,
walk_to holding a prop, and turn_to toward a target straight above or below.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest
from story_scenes import STORIES, story_scene
from test_foot_planting import planting_problems
from timeline_sanity import sanity_problems
from verb_cases import CAST, PROPS, Scene, base_scene, cases_for

from screenplay import body as B
from screenplay import ik, verbs_core
from screenplay.compiler import compile as compile_screenplay
from screenplay.schema import CompileResult, Screenplay, Timeline
from screenplay.tracks import evaluate

BUILDS = {"dad": "tall", "mom": "average", "kid": "small"}
STAGE = {"stage": [0.4, 0.3]}  # x = -0.2, 0.3 up: the B5 reproduction's raised mark
POS_TOL = 2e-3  # the tracks round to 3 dp


# ------------------------------------------------------------------------- helpers

def _shot(sid: str, set_id: str, start: dict, actions: list[dict], prop_start: dict | None = None,
          marks: dict | None = None) -> dict:
    shot = {"id": sid, "set": set_id, "start": start, "propStart": prop_start or {}, "actions": actions}
    if marks:
        shot["marks"] = marks
    return shot


def _sp(*shots: dict) -> Screenplay:
    return Screenplay.model_validate({"memoryId": "repro", "cast": list(CAST), "props": list(PROPS),
                                      "shots": list(shots)})


def _compile(sp: Screenplay, scene: Scene | None = None, plant_feet: bool = True) -> CompileResult:
    scene = scene or base_scene()
    return compile_screenplay(sp, sets=scene.sets, props=scene.props, people=scene.people, plant_feet=plant_feet)


def _repro(*shots: dict, plant_feet: bool = True) -> CompileResult:
    """test_compile_fuzz's _repro: the shots on verb_cases.base_scene with its CAST and PROPS."""
    return _compile(_sp(*shots), plant_feet=plant_feet)


def _errors(result: CompileResult) -> list[str]:
    return [f"{e.code}: {e.message}" for e in result.errors]


def _keys(tl: Timeline, target: str, channel: str) -> list[tuple[float, Any]]:
    return next(([(float(k[0]), k[1]) for k in tr.keys] for tr in tl.tracks
                 if (tr.target, tr.channel) == (target, channel)), [])


def _at(tl: Timeline, target: str, channel: str, t: float) -> Any:
    return evaluate(tl.tracks, target, channel, t)


def _grab(tl: Timeline, prop: str, who: str | None = None) -> tuple[float, str]:
    """When and into which socket the prop goes into a hand (of ``who``, when given)."""
    for t, parent in _keys(tl, f"prop:{prop}", "parent"):
        if parent.startswith(f"puppet:{who}." if who else "puppet:"):
            return t, parent
    raise AssertionError(f"{prop} is never taken")


def _hand(tl: Timeline, who: str, socket: str, t: float, build: str) -> tuple[float, float]:
    """A hand's world position from the compiled tracks alone (every bone, the torso too)."""
    rots = {b: _at(tl, f"puppet:{who}", f"{b}.rot", t) or 0.0 for b in ik.RIG["bones"]}
    x, y, _ = _at(tl, f"puppet:{who}", "root.position", t)
    facing = int(_at(tl, f"puppet:{who}", "root.facing", t))
    return ik.socket_world(socket, rots, (x, y), facing, build)


def _clean(sp: Screenplay, scene: Scene | None = None) -> Timeline:
    """Compile planted and unplanted; both ok, sane, and planting holds. Returns the unplanted
    timeline, whose root y is the standing surface (planting only lowers it mid-stride)."""
    scene = scene or base_scene()
    planted, unplanted = _compile(sp, scene), _compile(sp, scene, plant_feet=False)
    assert planted.ok and unplanted.ok, _errors(planted) + _errors(unplanted)
    assert planted.timeline is not None and unplanted.timeline is not None
    assert sanity_problems(planted.timeline) == []
    builds = {pid: p.character.build for pid, p in scene.people.items()}
    assert planting_problems(planted.timeline, unplanted.timeline, builds) == []
    return unplanted.timeline


def _ends_on(tl: Timeline, who: str, y: float) -> list[float]:
    """Root at the end of the timeline: standing on the surface at height y."""
    root = _at(tl, f"puppet:{who}", "root.position", tl.duration)
    assert root[1] == pytest.approx(y, abs=1e-9)
    return root


def _grip(prop: str, anchor: tuple[float, float], scene: Scene | None = None) -> tuple[float, float]:
    geom = B.PropGeom.from_prop((scene or base_scene()).props[prop])
    return B.grip_from_anchor(geom, (*anchor, 0.5))


def _takes_it(tl: Timeline, who: str, prop: str, anchor: tuple[float, float], surface: float,
              scene: Scene | None = None) -> float:
    """``who`` takes ``prop`` with its hand on the grip, standing (or crouched) over the surface
    at height ``surface`` (a crouch lowers the root below it, never raises it), and ends up
    back on that surface holding it. Returns the root x at the grab."""
    scene = scene or base_scene()
    build = scene.people[who].character.build
    t, parent = _grab(tl, prop, who)
    socket = parent.split(".", 1)[1]
    assert _hand(tl, who, socket, t, build) == pytest.approx(_grip(prop, anchor, scene), abs=POS_TOL)
    x, y, _ = _at(tl, f"puppet:{who}", "root.position", t)
    assert surface - 0.2 <= y <= surface + 1e-9
    _ends_on(tl, who, surface)
    assert _keys(tl, f"prop:{prop}", "parent")[-1][1] == parent
    return x


# ------------------------------------------------------ the fuzz reproduction (B5)

def test_pick_up_from_a_raised_mark_reaches_the_floor_below():
    """test_compile_fuzz's B5 reproduction, verbatim: mom on a mark 0.3 up, the cap at the curb."""
    result = _repro(_shot("s1", "yard", {"mom": {"at": "stage"}},
                          [{"t": 1, "do": "pick_up", "who": "mom", "what": "cap"}],
                          prop_start={"cap": {"at": "curb"}}, marks={"stage": [0.4, 0.3]}))
    assert result.ok, _errors(result)


def test_the_b5_pick_up_ends_on_the_floor_sane_and_planted():
    sp = _sp(_shot("s1", "yard", {"mom": {"at": "stage"}}, [{"t": 1, "do": "pick_up", "who": "mom", "what": "cap"}],
                   prop_start={"cap": {"at": "curb"}}, marks=STAGE))
    tl = _clean(sp)
    # It walks off the stage down to the floor in one walk, and crouches on the floor.
    start = _at(tl, "puppet:mom", "root.position", 0.0)
    assert start == [-0.2, 0.3, 0.5]
    x = _takes_it(tl, "mom", "cap", (0.8, 0.0), 0.0)
    assert 0.6 < x < 0.8  # short of the cap, on its own side
    # root y equals the floor at the pick-up spot: where the walk ends, before the crouch
    t_grab, _ = _grab(tl, "cap")
    arrival = [p for t, p in _keys(tl, "puppet:mom", "root.position") if t < t_grab and p[0] == x]
    assert arrival[0][1] == 0.0


# ----------------------------------------------------- a prop on the floor below

@pytest.mark.parametrize("who", ["dad", "mom", "kid"])
@pytest.mark.parametrize("cap_at", ["low", "curb", "door"])
def test_a_prop_on_the_floor_below_a_raised_mark(who, cap_at):
    marks = {**STAGE, "low": [0.45, 0.0]}  # low: x = -0.1, just off the stage mark
    sp = _sp(_shot("s1", "yard", {who: {"at": "stage"}}, [{"t": 1, "do": "pick_up", "who": who, "what": "cap"}],
                   prop_start={"cap": {"at": cap_at}}, marks=marks))
    tl = _clean(sp)
    x = {"low": -0.1, "curb": 0.8, "door": -0.8}[cap_at]
    _takes_it(tl, who, "cap", (x, 0.0), 0.0)


@pytest.mark.parametrize("who", ["dad", "mom", "kid"])
def test_with_the_right_hand_full_the_left_arm_reaches_the_floor_below(who):
    sp = _sp(_shot("s1", "yard", {who: {"at": "stage", "holding": "hymn_books"}},
                   [{"t": 1, "do": "pick_up", "who": who, "what": "cap"}],
                   prop_start={"cap": {"at": "low"}}, marks={**STAGE, "low": [0.45, 0.0]}))
    tl = _clean(sp)
    _takes_it(tl, who, "cap", (-0.1, 0.0), 0.0)
    assert _grab(tl, "cap")[1] == f"puppet:{who}.hand_l"
    assert _keys(tl, "prop:hymn_books", "parent") == [(0.0, f"puppet:{who}.hand_r")]


def test_kneeling_on_a_raised_mark_it_gets_up_then_steps_down():
    sp = _sp(_shot("s1", "yard", {"mom": {"at": "stage"}},
                   [{"t": 0, "do": "kneel", "who": "mom"}, {"t": 2, "do": "pick_up", "who": "mom", "what": "cap"}],
                   prop_start={"cap": {"at": "low"}}, marks={**STAGE, "low": [0.45, 0.0]}))
    tl = _clean(sp)
    _takes_it(tl, "mom", "cap", (-0.1, 0.0), 0.0)
    ys = [p[1] for _, p in _keys(tl, "puppet:mom", "root.position")]
    assert 0.3 in ys[ys.index(min(y for y in ys[:4])):]  # stood back up on the stage before the walk down


def test_on_the_church_steps_a_hymn_book_dropped_at_the_foot():
    """The story corpus's church: mom on the top step (0.3 up) picks up the books at the foot."""
    sets, props, people = story_scene()
    scene = Scene(sets, props, people)
    sp = Screenplay.model_validate({"memoryId": "m", "cast": ["mom"], "props": ["hymn_books"], "shots": [
        _shot("s1", "church_steps", {"mom": {"at": "steps_top"}},
              [{"t": 1, "do": "pick_up", "who": "mom", "what": "hymn_books"},
               {"t": 8, "do": "walk_to", "who": "mom", "to": "church_door"}],
              prop_start={"hymn_books": {"at": "steps_foot"}})]})
    tl = _clean(sp, scene)
    t, _ = _grab(tl, "hymn_books")
    build = people["mom"].character.build
    assert _hand(tl, "mom", "hand_r", t, build) == pytest.approx(_grip("hymn_books", (0.0, 0.0), scene), abs=POS_TOL)
    assert _at(tl, "puppet:mom", "root.position", t)[1] <= 0.0
    _ends_on(tl, "mom", 0.3)  # and back up to the church door


# ------------------------------------------------ a prop on a raised mark, from the floor

def test_a_prop_on_a_raised_mark_picked_up_from_the_floor_is_reached_from_the_floor():
    """Too far to reach from the curb: the walk-up stays on the floor (its own surface) and the
    arm reaches up to the stage, as it always did: nobody climbs for a prop it can reach."""
    sp = _sp(_shot("s1", "yard", {"mom": {"at": "curb"}}, [{"t": 1, "do": "pick_up", "who": "mom", "what": "cap"}],
                   prop_start={"cap": {"at": "stage"}}, marks=STAGE))
    tl = _clean(sp)
    _takes_it(tl, "mom", "cap", (-0.2, 0.3), 0.0)
    assert all(p[1] == 0.0 for _, p in _keys(tl, "puppet:mom", "root.position"))


def test_a_prop_on_a_raised_mark_out_of_reach_from_the_floor_is_climbed_to():
    """The small kid cannot reach the books on a mark 0.38 up from the floor (its shoulder is
    0.30 up): it walks up onto the mark's surface in one walk and bends down to them there."""
    scene = base_scene()
    sp = _sp(_shot("s1", "yard", {"kid": {"at": "curb"}},
                   [{"t": 1, "do": "pick_up", "who": "kid", "what": "hymn_books"}],
                   prop_start={"hymn_books": {"at": "s"}}, marks={"s": [0.4, 0.38]}))
    tl = _clean(sp, scene)
    t, _ = _grab(tl, "hymn_books", "kid")
    assert _hand(tl, "kid", "hand_r", t, "small") == pytest.approx(_grip("hymn_books", (-0.2, 0.38)), abs=POS_TOL)
    walk = [p for _, p in _keys(tl, "puppet:kid", "root.position")]
    assert walk[0] == [0.8, 0.0, 0.5] and walk[2] == [-0.118, 0.38, 0.5]  # one walk, floor to mark
    _ends_on(tl, "kid", 0.38)


def test_a_prop_on_a_raised_mark_within_reach_from_the_floor_is_taken_without_moving():
    sp = _sp(_shot("s1", "yard", {"mom": {"at": "under"}}, [{"t": 1, "do": "pick_up", "who": "mom", "what": "cap"}],
                   prop_start={"cap": {"at": "stage"}}, marks={**STAGE, "under": [0.43, 0.0]}))
    tl = _clean(sp)
    t, _ = _grab(tl, "cap")
    assert _keys(tl, "puppet:mom", "root.position") == [(0.0, [-0.14, 0.0, 0.5])]
    assert _hand(tl, "mom", "hand_r", t, "average") == pytest.approx(_grip("cap", (-0.2, 0.3)), abs=POS_TOL)


@pytest.mark.parametrize("who,height,outcome", [
    ("kid", 0.38, "climbs"), ("kid", 0.4, "climbs"), ("kid", 0.45, "out_of_reach"),
    ("mom", 0.45, "floor"), ("mom", 0.5, "climbs"), ("mom", 0.55, "out_of_reach"),
    ("dad", 0.5, "floor"), ("dad", 0.55, "climbs"),
])
def test_the_climb_is_at_most_the_puppets_own_height(who, height, outcome):
    """The books on a mark ``height`` up, the puppet at the curb. Reached from the floor when the
    arm gets there; else climbed to when the mark is at most the puppet's own height up
    (ik.scale: kid 0.4, mom 0.5, dad 0.55); else out of reach, with the miss from the floor."""
    sp = _sp(_shot("s1", "yard", {who: {"at": "curb"}}, [{"t": 1, "do": "pick_up", "who": who, "what": "hymn_books"}],
                   prop_start={"hymn_books": {"at": "s"}}, marks={"s": [0.4, height]}))
    if outcome == "out_of_reach":
        result = _compile(sp)
        assert [e.code for e in result.errors] == ["out_of_reach"]
        assert height > ik.scale(BUILDS[who])
        return
    tl = _clean(sp)
    t, _ = _grab(tl, "hymn_books", who)
    assert _hand(tl, who, "hand_r", t, BUILDS[who]) == pytest.approx(_grip("hymn_books", (-0.2, height)), abs=POS_TOL)
    _ends_on(tl, who, height if outcome == "climbs" else 0.0)


def test_a_shelf_above_the_climb_limit_is_still_out_of_reach():
    """test_agent_real_compiler's pick_up_above_the_shoulder shape: books on a shelf 0.9 up."""
    shelf = _sp(_shot("s1", "yard", {"dad": {"at": "door"}},
                      [{"t": 1, "do": "pick_up", "who": "dad", "what": "hymn_books"}],
                      prop_start={"hymn_books": {"at": "shelf"}}, marks={"shelf": [0.5, 0.9]}))
    result = _compile(shelf)
    assert [e.code for e in result.errors] == ["out_of_reach"]


# ------------------------------------------------------ a prop on a socket below

@pytest.mark.parametrize("who", ["dad", "mom", "kid"])
def test_a_prop_on_a_table_socket_below_a_stage(who):
    """The books on the table top; the table stands on the floor below the stage the puppet is
    on. The spot is on the floor (the table's ground), not at the stage's height beside it."""
    sp = _sp(_shot("s1", "yard", {who: {"at": "stage"}}, [{"t": 1, "do": "pick_up", "who": who, "what": "hymn_books"}],
                   prop_start={"table": {"at": "table_spot"}, "hymn_books": {"on": "table.top"}}, marks=STAGE))
    tl = _clean(sp)
    t, parent = _grab(tl, "hymn_books")
    assert parent == f"puppet:{who}.hand_r"
    world = _world()
    world.props["table"].pos = world.props["table"].world_pos = (0.2, 0.0, 0.5)
    world._to_ground("table", (0.2, 0.0, 0.5))
    world._attach_prop("hymn_books", "table", "top")
    grip = B.grip_from_anchor(B.PropGeom.from_prop(world.catalog["hymn_books"]), world.props["hymn_books"].world_pos)
    assert _hand(tl, who, "hand_r", t, BUILDS[who]) == pytest.approx(grip, abs=POS_TOL)
    assert _at(tl, f"puppet:{who}", "root.position", t)[1] <= 1e-9  # on the floor by the table
    _ends_on(tl, who, 0.0)


def _world():
    from screenplay.state import World

    scene = base_scene()
    return World(scene.people.values(), scene.props.values())


def test_resting_surface_y_follows_the_stack_to_the_ground():
    world = _world()
    world._to_ground("table", (0.2, 0.15, 0.5))  # the table on a riser 0.15 up
    world._attach_prop("hymn_books", "table", "top")
    world._to_ground("cap", (-0.5, 0.3, 0.5))
    assert verbs_core.resting_surface_y(world, "table") == 0.15
    assert verbs_core.resting_surface_y(world, "hymn_books") == 0.15  # not the table top's height
    assert verbs_core.resting_surface_y(world, "cap") == 0.3
    assert verbs_core.resting_surface_y(world, "lamp") is None  # not on stage


# --------------------------------------------- same surface: exactly the keys of before

def _today(world, who, surface_y):  # noqa: ARG001: the rule before the fix, for the comparison
    return [world.people[who].pos[1]]


def _same_surface_cases() -> list[tuple[str, Screenplay, Scene]]:
    cases = [(f"{c.verb}:{c.name}", c.screenplay, c.scene)
             for verb in ("pick_up", "hand_to", "drop") for c in cases_for(verb) if c.ok]
    sets, props, people = story_scene()
    cases += [(f"story:{name}", Screenplay.model_validate(copy.deepcopy(sp)), Scene(sets, props, people))
              for name, sp in sorted(STORIES.items())]
    # both on the same raised mark's surface
    cases.append(("raised:same_surface", _sp(_shot(
        "s1", "yard", {"mom": {"at": "stage"}}, [{"t": 1, "do": "pick_up", "who": "mom", "what": "cap"}],
        prop_start={"cap": {"at": "stage_right"}}, marks={**STAGE, "stage_right": [0.6, 0.3]})), base_scene()))
    return cases


SAME_SURFACE = _same_surface_cases()


def test_the_same_surface_cases_cover_every_ok_pick_up_hand_to_and_drop_case_and_every_story():
    names = [n for n, _, _ in SAME_SURFACE]
    assert sum(n.startswith("story:") for n in names) == len(STORIES)
    for verb in ("pick_up", "hand_to", "drop"):
        assert any(n.startswith(f"{verb}:") for n in names)


@pytest.mark.parametrize("name,sp,scene", SAME_SURFACE, ids=[n for n, _, _ in SAME_SURFACE])
def test_a_prop_on_the_puppets_own_surface_compiles_byte_identical_to_before(name, sp, scene, monkeypatch):
    """With the spot height forced back to the puppet's own (the rule before the fix), every
    verb case and story compiles to the same bytes: nothing on one surface goes another way."""
    now = _compile(sp, scene).model_dump_json()
    monkeypatch.setattr(verbs_core, "_approach_ys", _today)
    before = _compile(sp, scene).model_dump_json()
    assert now == before, name


# ------------------------------------------------------- hand_to across heights

def test_hand_to_between_puppets_on_different_heights_meets_between_them():
    """mom 0.15 up on a stage edge hands the cap down to the kid on the floor: both hands meet at
    the handover point (it lies on the line between their shoulders, whatever their heights)."""
    sp = _sp(_shot("s1", "yard", {"mom": {"at": "stage", "holding": "cap"}, "kid": {"at": "low"}},
                   [{"t": 1, "do": "hand_to", "who": "mom", "what": "cap", "to": "kid"}],
                   marks={"stage": [0.4, 0.15], "low": [0.45, 0.0]}))
    tl = _clean(sp)
    t, parent = _grab_after_first(tl)
    assert parent == "puppet:kid.hand_r"
    giver = _hand(tl, "mom", "hand_r", t, "average")
    taker = _hand(tl, "kid", "hand_r", t, "small")
    assert giver == pytest.approx(taker, abs=POS_TOL)
    assert taker[1] > 0.15  # up between them, not at the floor
    _ends_on(tl, "mom", 0.15)
    _ends_on(tl, "kid", 0.0)


def _grab_after_first(tl: Timeline) -> tuple[float, str]:
    (_, first), *rest = _keys(tl, "prop:cap", "parent")
    assert first == "puppet:mom.hand_r"
    return rest[0]


def test_hand_to_down_a_step_too_tall_for_the_taker_is_out_of_reach_with_the_miss():
    """hand_to has no walk-up: 0.3 down to the small kid the hands cannot meet."""
    result = _repro(_shot("s1", "yard", {"mom": {"at": "stage", "holding": "cap"}, "kid": {"at": "low"}},
                          [{"t": 1, "do": "hand_to", "who": "mom", "what": "cap", "to": "kid"}],
                          marks={**STAGE, "low": [0.45, 0.0]}))
    assert _errors(result) == [
        'out_of_reach: shot s1, t=1: mom cannot hand_to "cap"; kid\'s arm_r cannot reach: target is 0.24 from the '
        "shoulder, reach is 0.14"
    ]


# ------------------------------------------------------------------ the bug hunt

def test_drop_from_a_raised_mark_lands_on_the_mark():
    """CONTRACTS.md decision 6: the dropped prop lands 0.08 in front of the feet on the dropper's
    standing surface, which is the raised mark, not the floor."""
    for start in ({"mom": {"at": "stage", "holding": "cap"}},):
        for actions in ([{"t": 1, "do": "drop", "who": "mom", "what": "cap"}],
                        [{"t": 0, "do": "kneel", "who": "mom"}, {"t": 2, "do": "drop", "who": "mom", "what": "cap"}]):
            tl = _clean(_sp(_shot("s1", "yard", start, actions, marks=STAGE)))
            assert _keys(tl, "prop:cap", "position")[-1][1] == [-0.12, 0.3, 0.5]


def test_drop_seated_on_a_chair_on_a_raised_mark_lands_on_the_mark():
    tl = _clean(_sp(_shot("s1", "yard", {"mom": {"at": "stage", "in": "chair.seat", "holding": "cap"}},
                          [{"t": 1, "do": "drop", "who": "mom", "what": "cap"}],
                          prop_start={"chair": {"at": "stage"}}, marks=STAGE)))
    assert _keys(tl, "prop:cap", "position")[-1][1][1] == 0.3


def test_a_cap_dropped_on_a_raised_mark_is_picked_up_there_from_the_floor():
    """The drop and the pick_up agree on where the cap is: on the stage, 0.3 up, where the kid
    on the floor reaches up to it."""
    tl = _clean(_sp(_shot("s1", "yard", {"mom": {"at": "stage", "holding": "cap"}, "kid": {"at": "curb"}},
                          [{"t": 1, "do": "drop", "who": "mom", "what": "cap"},
                           {"t": 3, "do": "walk_to", "who": "mom", "to": "door"},
                           {"t": 9, "do": "pick_up", "who": "kid", "what": "cap"}], marks=STAGE)))
    _takes_it(tl, "kid", "cap", (-0.12, 0.3), 0.0)


def test_walk_to_holding_a_prop_across_a_height_change_keeps_it_in_hand():
    tl = _clean(_sp(_shot("s1", "yard", {"mom": {"at": "stage", "holding": "cap"}},
                          [{"t": 1, "do": "walk_to", "who": "mom", "to": "curb"},
                           {"t": 6, "do": "walk_to", "who": "mom", "to": "stage"}], marks=STAGE)))
    assert _keys(tl, "prop:cap", "parent") == [(0.0, "puppet:mom.hand_r")]
    hold = B.hold_pose("arm_r")
    for t in (2.0, 3.5, 7.0, 9.0):  # mid-walk, both ways
        assert _at(tl, "puppet:mom", "arm_r.upper.rot", t) == pytest.approx(hold[0])
    _ends_on(tl, "mom", 0.3)


def test_enter_onto_and_exit_from_a_raised_mark():
    tl = _clean(_sp(_shot("s1", "yard", {}, [{"t": 1, "do": "enter", "who": "mom", "to": "stage"},
                                             {"t": 6, "do": "exit", "who": "mom", "to": "offstage_right"}],
                          marks=STAGE)))
    path = [p for _, p in _keys(tl, "puppet:mom", "root.position")]
    assert [-0.2, 0.3, 0.5] in path
    assert path[0][1] == 0.0 and path[-1][1] == 0.0  # offstage is on the ground


def test_follow_onto_a_raised_mark_stops_on_the_leaders_surface():
    """follow keeps the leader's standing surface: the follower stops spacing behind where the
    leader stopped, at the leader's height there (the same rule as pick_up's spots: a spot beside
    something is on its surface)."""
    tl = _clean(_sp(_shot("s1", "yard", {"mom": {"at": "door"}, "dad": {"at": "bench_spot"}},
                          [{"t": 0, "do": "walk_to", "who": "mom", "to": "top"},
                           {"t": 0.5, "do": "follow", "who": "dad", "target": "mom"}], marks={"top": [0.6, 0.3]})))
    assert _ends_on(tl, "dad", 0.3)[0] == pytest.approx(0.0)


def test_follow_over_a_raised_mark_and_back_down_ends_on_the_floor():
    tl = _clean(_sp(_shot("s1", "yard", {"mom": {"at": "door"}, "dad": {"at": "low"}},
                          [{"t": 0, "do": "walk_to", "who": "mom", "to": "stage"},
                           {"t": 4, "do": "walk_to", "who": "mom", "to": "far"},
                           {"t": 0, "do": "follow", "who": "dad", "target": "mom"}],
                          marks={**STAGE, "low": [0.6, 0.0], "far": [0.9, 0.0]})))
    assert _ends_on(tl, "dad", 0.0)[0] == pytest.approx(0.6)


def test_follow_a_leader_kneeling_on_a_raised_mark_walks_at_the_marks_height():
    tl = _clean(_sp(_shot("s1", "yard", {"mom": {"at": "stage"}, "dad": {"at": "far"}},
                          [{"t": 0, "do": "kneel", "who": "mom"}, {"t": 2, "do": "walk_to", "who": "mom", "to": "door"},
                           {"t": 0, "do": "follow", "who": "dad", "target": "mom"}],
                          marks={**STAGE, "far": [0.9, 0.0]})))
    for _, p in _keys(tl, "puppet:dad", "root.position"):
        assert p[1] >= 0.0  # never dips to the kneeling root height
    _ends_on(tl, "dad", 0.0)


@pytest.mark.parametrize("target_x", [0.6002, 0.60008, 0.5999])
def test_turn_to_a_target_straight_above_keeps_the_facing(target_x):
    """Bug: mom stood at x = 0.20024 (her root track rounds it to 0.2) and turned to face a mark
    at x = 0.2002 on a riser above her: the 3 dp rounding made it 'to her right', so she turned
    around. A target within verbs_motion.TURN_DEAD_ZONE of her x is above or below her: no turn."""
    for facing in ("left", "right"):
        tl = _clean(_sp(_shot("s1", "yard", {"mom": {"at": "a", "facing": facing}},
                              [{"t": 1, "do": "turn_to", "who": "mom", "target": "b"}],
                              marks={"a": [0.60012, 0.0], "b": [target_x, 0.3]})))
        assert _keys(tl, "puppet:mom", "root.facing") == [(0.0, -1.0 if facing == "left" else 1.0)]


def test_turn_to_a_target_just_past_the_dead_zone_turns():
    tl = _clean(_sp(_shot("s1", "yard", {"mom": {"at": "a", "facing": "left"}},
                          [{"t": 1, "do": "turn_to", "who": "mom", "target": "b"}],
                          marks={"a": [0.6, 0.0], "b": [0.601, 0.3]})))
    assert _keys(tl, "puppet:mom", "root.facing")[-1][1] == 1.0
