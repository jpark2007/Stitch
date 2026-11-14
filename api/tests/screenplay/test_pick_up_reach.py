"""pick_up steps to a spot from which the arm actually used can take a low grip (finding F3).

On base_scene the cap sits at the door mark with its grip about 0.03 above the ground. Before
the fix, a puppet standing over it (or arriving there by walk_to) failed with out_of_reach for
every build: the plain walk-up only runs when the grip is far from the root, and a forward bend
swings the shoulder away from a grip under the hips. With hand_r full, a walk-up from the curb
failed on arm_l, whose shoulder pivot sits behind the hip. verbs_core.apply_pick_up now steps
back (or stops where that arm reaches), only to spots on stage (by an edge it walks past the
cap instead), and still fails, with the closest miss, when nothing reaches. A pick_up that
compiled before compiles to exactly the same keys.
"""

from __future__ import annotations

import pytest

from screenplay import body as B
from screenplay import ik, verbs_core
from screenplay.compiler import compile as compile_screenplay
from screenplay.errors import hint_for
from screenplay.schema import VOCABULARY, Screenplay
from screenplay.tracks import evaluate
from verb_cases import base_scene

DOOR_X = -0.8  # YARD_MARKS["door"] = [0.1, 0.0]: x = 2u - 1
BUILDS = list(VOCABULARY["builds"])  # small, short, average, tall
CAST_BUILDS = {"dad": "tall", "mom": "average", "kid": "small"}


def compile_one(actions, start, prop_start=None, marks=None, build=None, plant_feet=True):
    """One shot on the yard. ``build`` re-casts dad in that build."""
    sp = Screenplay.model_validate({
        "memoryId": "m", "cast": ["dad", "mom", "kid"], "props": ["hymn_books", "cap", "chair", "table"],
        "shots": [{"id": "s1", "set": "yard", "start": start, "propStart": prop_start or {"cap": {"at": "door"}},
                   "marks": marks or {}, "actions": actions}],
    })
    sc = base_scene()
    if build is not None:
        dad = sc.people["dad"]
        sc.people["dad"] = dad.model_copy(update={"character": dad.character.model_copy(update={"build": build})})
    return compile_screenplay(sp, sets=sc.sets, props=sc.props, people=sc.people, plant_feet=plant_feet)


def track(res, target, channel):
    for tr in res.timeline.tracks:
        if (tr.target, tr.channel) == (target, channel):
            return [list(k) for k in tr.keys]
    return []


def at(res, target, channel, t):
    return evaluate(res.timeline.tracks, target, channel, t)


def grab_time(res, prop="cap"):
    """When the prop changes parent from the ground to a hand."""
    (_, ground), (t, hand) = track(res, f"prop:{prop}", "parent")
    assert ground == "ground"
    return t, hand


def cap_grip(anchor=(DOOR_X, 0.0)):
    return B.grip_from_anchor(B.PropGeom.from_prop(base_scene().props["cap"]), (*anchor, 0.5))


def hand_at(res, who, socket, t, build):
    """The hand's world position from the compiled tracks alone (every bone, the torso too)."""
    rots = {b: at(res, f"puppet:{who}", f"{b}.rot", t) or 0.0 for b in ik.RIG["bones"]}
    x, y, _ = at(res, f"puppet:{who}", "root.position", t)
    facing = int(at(res, f"puppet:{who}", "root.facing", t))
    return ik.socket_world(socket, rots, (x, y), facing, build)


def assert_hand_on_the_grip(res, who, socket, build, grip=None):
    t, hand = grab_time(res)
    assert hand == f"puppet:{who}.{socket}"
    # Two-bone IK is exact; the tolerance covers the tracks' 3 dp rounding of root and angles.
    assert hand_at(res, who, socket, t, build) == pytest.approx(grip or cap_grip(), abs=2e-3)
    return t


def errors(res):
    return [(e.code, e.message) for e in res.errors]


# ------------------------------------------------------- (a) standing over the cap

@pytest.mark.parametrize("who", ["dad", "mom", "kid"])
def test_standing_over_the_cap_steps_back_and_picks_it_up(who):
    res = compile_one([{"t": 1, "who": who, "do": "pick_up", "what": "cap"}], {who: {"at": "door"}})
    assert res.ok, errors(res)
    t = assert_hand_on_the_grip(res, who, "hand_r", CAST_BUILDS[who])
    x0 = at(res, f"puppet:{who}", "root.position", 0.0)[0]
    x = at(res, f"puppet:{who}", "root.position", t)[0]
    assert x0 == DOOR_X
    # It faces right (the default), so it steps back to the left, away from the grip, and
    # turns to face it again before the reach.
    assert x <= DOOR_X - 0.01
    assert at(res, f"puppet:{who}", "root.facing", t) == 1
    # Then it is back up, standing on the mark's ground, holding the cap.
    end = res.timeline.duration
    assert at(res, f"puppet:{who}", "root.position", end) == [x, 0.0, 0.5]
    assert at(res, f"puppet:{who}", "torso.rot", end) == 0.0


@pytest.mark.parametrize("build", BUILDS)
def test_walking_onto_the_cap_then_picking_it_up_compiles(build):
    res = compile_one(
        [{"t": 1, "who": "dad", "do": "walk_to", "to": "door"}, {"t": 9, "who": "dad", "do": "pick_up", "what": "cap"}],
        {"dad": {"at": "curb"}}, build=build,
    )
    assert res.ok, errors(res)
    t = assert_hand_on_the_grip(res, "dad", "hand_r", build)
    # It arrived on the door mark walking left, so the step back is to the right.
    x = at(res, "puppet:dad", "root.position", t)[0]
    assert x >= DOOR_X + 0.01
    assert at(res, "puppet:dad", "root.facing", t) == -1


@pytest.mark.parametrize("build", BUILDS)
def test_standing_over_the_cap_compiles_for_every_build(build):
    res = compile_one([{"t": 1, "who": "dad", "do": "pick_up", "what": "cap"}], {"dad": {"at": "door"}}, build=build)
    assert res.ok, errors(res)
    assert_hand_on_the_grip(res, "dad", "hand_r", build)


# ----------------------------------------------------- (b) the left arm, hand_r full

@pytest.mark.parametrize("build", BUILDS)
def test_with_the_right_hand_full_the_walk_up_stops_where_the_left_arm_reaches(build):
    res = compile_one([{"t": 1, "who": "dad", "do": "pick_up", "what": "cap"}],
                      {"dad": {"at": "curb", "holding": "hymn_books"}}, build=build)
    assert res.ok, errors(res)
    t = assert_hand_on_the_grip(res, "dad", "hand_l", build)
    # The books never leave the right hand.
    assert track(res, "prop:hymn_books", "parent") == [[0.0, "puppet:dad.hand_r"]]
    # It walked up from the curb (x = 0.8) toward the door and faces the cap.
    x = at(res, "puppet:dad", "root.position", t)[0]
    assert DOOR_X < x < 0.8
    assert at(res, "puppet:dad", "root.facing", t) == -1


@pytest.mark.parametrize("who", ["dad", "mom", "kid"])
def test_with_the_right_hand_full_over_the_cap_it_steps_back_and_uses_the_left_arm(who):
    res = compile_one([{"t": 1, "who": who, "do": "pick_up", "what": "cap"}],
                      {who: {"at": "door", "holding": "hymn_books"}})
    assert res.ok, errors(res)
    t = assert_hand_on_the_grip(res, who, "hand_l", CAST_BUILDS[who])
    assert at(res, f"puppet:{who}", "root.position", t)[0] <= DOOR_X - 0.01


# ------------------------------------------------------------------ stage edges

EDGE_CASES = [(0.0, None), (0.01, None), (0.03, None), (0.05, None), (0.97, "left"), (1.0, "left")]


@pytest.mark.parametrize("u,facing", EDGE_CASES)
@pytest.mark.parametrize("who", ["dad", "mom", "kid"])
@pytest.mark.parametrize("holding", [None, "hymn_books"])
def test_by_the_stage_edge_it_only_steps_to_spots_on_stage(u, facing, who, holding):
    """Over a cap by the edge the step back may have no room: it stops on the edge or walks past
    the cap to the far side. The shot goes on: a later action is not refused as offstage."""
    start = {who: {"at": "e", **({"facing": facing} if facing else {}), **({"holding": holding} if holding else {})}}
    res = compile_one([{"t": 1, "who": who, "do": "pick_up", "what": "cap"}, {"t": 6, "who": who, "do": "wave"}],
                      start, {"cap": {"at": "e"}}, marks={"e": [u, 0.0]})
    assert res.ok, errors(res)
    for _, pos in track(res, f"puppet:{who}", "root.position"):
        assert abs(pos[0]) <= 1.0
    grip = cap_grip((2 * u - 1, 0.0))
    assert_hand_on_the_grip(res, who, "hand_l" if holding else "hand_r", CAST_BUILDS[who], grip)


@pytest.mark.parametrize("u,facing,side", [(0.0, None, 1), (1.0, "left", -1)])
def test_over_a_cap_on_the_edge_it_walks_past_it_and_turns_back(u, facing, side):
    start = {"dad": {"at": "e", **({"facing": facing} if facing else {})}}
    res = compile_one([{"t": 1, "who": "dad", "do": "pick_up", "what": "cap"}], start, {"cap": {"at": "e"}},
                      marks={"e": [u, 0.0]})
    assert res.ok, errors(res)
    t = assert_hand_on_the_grip(res, "dad", "hand_r", "tall", cap_grip((2 * u - 1, 0.0)))
    x = at(res, "puppet:dad", "root.position", t)[0]
    assert (x - (2 * u - 1)) * side >= 0.01  # the stage's side of the cap
    assert at(res, "puppet:dad", "root.facing", t) == -side  # facing back toward the edge


# ------------------------------------------------------------------ spot choice

def test_just_off_the_cap_it_takes_a_short_step_not_a_long_one():
    """Dad at x = -0.81, 0.01 past the cap: the search weighs the walk against the crouch
    (SPOT_WALK_COST), so it steps 0.04 to x = -0.85 and kneels with a moderate bend rather than
    walking to x = -0.9 (0.09) for the shallowest bend."""
    res = compile_one([{"t": 1, "who": "dad", "do": "pick_up", "what": "cap"}], {"dad": {"at": "e"}},
                      marks={"e": [0.095, 0.0]})
    assert res.ok, errors(res)
    t = assert_hand_on_the_grip(res, "dad", "hand_r", "tall")
    assert at(res, "puppet:dad", "root.position", t)[0] == -0.85
    assert at(res, "puppet:dad", "torso.rot", t) == -1.0


@pytest.mark.parametrize("u", [0.0, 0.05, 0.08, 0.095, 0.1, 0.105, 0.12, 0.2, 0.5])
@pytest.mark.parametrize("who", ["dad", "mom", "kid"])
def test_the_right_arm_never_folds_past_the_plain_crouch(u, who):
    """CROUCH_DEEP_BEND is a last resort (arm_l, whose shoulder sits behind the hip): a free
    right hand always finds a spot within CROUCH_MAX_BEND."""
    res = compile_one([{"t": 1, "who": who, "do": "pick_up", "what": "cap"}], {who: {"at": "e"}},
                      marks={"e": [u, 0.0]})
    assert res.ok, errors(res)
    for _, bend in track(res, f"puppet:{who}", "torso.rot"):
        assert bend >= -verbs_core.CROUCH_MAX_BEND - 1e-9


# ------------------------------------------------------------- regression guard

MAIN_SNAPSHOT = {
    "dad": {
        "duration": 8.151,
        "prop:cap parent": [[0.0, "ground"], [7.251, "puppet:dad.hand_r"]],
        "prop:cap position": [[0.0, [-0.8, 0.0, 0.5]], [7.251, [0.0, 0.0, 0.0]]],
        "puppet:dad arm_r.lower.rot": [[2.2, 0.0], [2.504, 0.15], [2.807, 0.2], [3.111, 0.15], [3.415, 0.3], [3.718, 0.15], [4.022, 0.2], [4.325, 0.15], [4.629, 0.3], [4.933, 0.15], [5.236, 0.2], [5.54, 0.15], [5.844, 0.3], [6.147, 0.15], [6.451, 0.0], [6.851, 0.0], [7.251, 0.724], [7.651, 1.2]],
        "puppet:dad arm_r.upper.rot": [[2.2, 0.0], [2.504, 0.0], [2.807, -0.35], [3.111, 0.0], [3.415, 0.35], [3.718, 0.0], [4.022, -0.35], [4.325, 0.0], [4.629, 0.35], [4.933, 0.0], [5.236, -0.35], [5.54, 0.0], [5.844, 0.35], [6.147, 0.0], [6.451, 0.0], [6.851, 0.0], [7.251, 0.25], [7.651, 0.3]],
        "puppet:dad leg_r.lower.rot": [[2.2, 0.0], [2.504, -0.9], [2.807, -0.1], [3.111, -0.05], [3.415, -0.25], [3.718, -0.9], [4.022, -0.1], [4.325, -0.05], [4.629, -0.25], [4.933, -0.9], [5.236, -0.1], [5.54, -0.05], [5.844, -0.25], [6.147, -0.9], [6.451, 0.0], [6.851, -1.5], [7.251, -1.5], [7.651, 0.0]],
        "puppet:dad root.facing": [[0.0, 1.0], [2.2, -1.0]],
        "puppet:dad root.position": [[0.0, [0.8, 0.0, 0.5]], [2.2, [0.8, 0.0, 0.5]], [6.451, [-0.688, 0.0, 0.5]], [6.851, [-0.688, -0.132, 0.5]], [7.251, [-0.688, -0.132, 0.5]], [7.651, [-0.688, 0.0, 0.5]]],
        "puppet:dad torso.rot": [[6.451, 0.0], [6.851, -0.8], [7.251, -0.8], [7.651, 0.0]],
    },
    "mom": {
        "duration": 8.18,
        "prop:cap parent": [[0.0, "ground"], [7.28, "puppet:mom.hand_r"]],
        "prop:cap position": [[0.0, [-0.8, 0.0, 0.5]], [7.28, [0.0, 0.0, 0.0]]],
        "puppet:mom arm_r.lower.rot": [[2.2, 0.0], [2.485, 0.15], [2.771, 0.2], [3.056, 0.15], [3.341, 0.3], [3.627, 0.15], [3.912, 0.2], [4.197, 0.15], [4.483, 0.3], [4.768, 0.15], [5.053, 0.2], [5.339, 0.15], [5.624, 0.3], [5.909, 0.15], [6.195, 0.2], [6.48, 0.0], [6.88, 0.0], [7.28, 0.814], [7.68, 1.2]],
        "puppet:mom arm_r.upper.rot": [[2.2, 0.0], [2.485, 0.0], [2.771, -0.35], [3.056, 0.0], [3.341, 0.35], [3.627, 0.0], [3.912, -0.35], [4.197, 0.0], [4.483, 0.35], [4.768, 0.0], [5.053, -0.35], [5.339, 0.0], [5.624, 0.35], [5.909, 0.0], [6.195, -0.35], [6.48, 0.0], [6.88, 0.0], [7.28, 0.201], [7.68, 0.3]],
        "puppet:mom leg_r.lower.rot": [[2.2, 0.0], [2.485, -0.9], [2.771, -0.1], [3.056, -0.05], [3.341, -0.25], [3.627, -0.9], [3.912, -0.1], [4.197, -0.05], [4.483, -0.25], [4.768, -0.9], [5.053, -0.1], [5.339, -0.05], [5.624, -0.25], [5.909, -0.9], [6.195, -0.1], [6.48, 0.0], [6.88, -1.5], [7.28, -1.5], [7.68, 0.0]],
        "puppet:mom root.facing": [[0.0, 1.0], [2.2, -1.0]],
        "puppet:mom root.position": [[0.0, [0.8, 0.0, 0.5]], [2.2, [0.8, 0.0, 0.5]], [6.48, [-0.698, 0.0, 0.5]], [6.88, [-0.698, -0.12, 0.5]], [7.28, [-0.698, -0.12, 0.5]], [7.68, [-0.698, 0.0, 0.5]]],
        "puppet:mom torso.rot": [[6.48, 0.0], [6.88, -0.8], [7.28, -0.8], [7.68, 0.0]],
    },
    "kid": {
        "duration": 8.238,
        "prop:cap parent": [[0.0, "ground"], [7.338, "puppet:kid.hand_r"]],
        "prop:cap position": [[0.0, [-0.8, 0.0, 0.5]], [7.338, [0.0, 0.0, 0.0]]],
        "puppet:kid arm_r.lower.rot": [[2.2, 0.0], [2.428, 0.15], [2.657, 0.2], [2.885, 0.15], [3.113, 0.3], [3.342, 0.15], [3.57, 0.2], [3.798, 0.15], [4.027, 0.3], [4.255, 0.15], [4.483, 0.2], [4.712, 0.15], [4.94, 0.3], [5.168, 0.15], [5.397, 0.2], [5.625, 0.15], [5.853, 0.3], [6.082, 0.15], [6.31, 0.2], [6.538, 0.0], [6.938, 0.0], [7.338, 0.7], [7.738, 1.2]],
        "puppet:kid arm_r.upper.rot": [[2.2, 0.0], [2.428, 0.0], [2.657, -0.35], [2.885, 0.0], [3.113, 0.35], [3.342, 0.0], [3.57, -0.35], [3.798, 0.0], [4.027, 0.35], [4.255, 0.0], [4.483, -0.35], [4.712, 0.0], [4.94, 0.35], [5.168, 0.0], [5.397, -0.35], [5.625, 0.0], [5.853, 0.35], [6.082, 0.0], [6.31, -0.35], [6.538, 0.0], [6.938, 0.0], [7.338, 0.208], [7.738, 0.3]],
        "puppet:kid leg_r.lower.rot": [[2.2, 0.0], [2.428, -0.9], [2.657, -0.1], [2.885, -0.05], [3.113, -0.25], [3.342, -0.9], [3.57, -0.1], [3.798, -0.05], [4.027, -0.25], [4.255, -0.9], [4.483, -0.1], [4.712, -0.05], [4.94, -0.25], [5.168, -0.9], [5.397, -0.1], [5.625, -0.05], [5.853, -0.25], [6.082, -0.9], [6.31, -0.1], [6.538, 0.0], [6.938, -1.5], [7.338, -1.5], [7.738, 0.0]],
        "puppet:kid root.facing": [[0.0, 1.0], [2.2, -1.0]],
        "puppet:kid root.position": [[0.0, [0.8, 0.0, 0.5]], [2.2, [0.8, 0.0, 0.5]], [6.538, [-0.718, 0.0, 0.5]], [6.938, [-0.718, -0.096, 0.5]], [7.338, [-0.718, -0.096, 0.5]], [7.738, [-0.718, 0.0, 0.5]]],
        "puppet:kid torso.rot": [[6.538, 0.0], [6.938, -0.7], [7.338, -0.7], [7.738, 0.0]],
    },
}


@pytest.mark.parametrize("who", ["dad", "mom", "kid"])
def test_the_walk_up_from_the_curb_with_free_hands_is_unchanged(who):
    """It compiled before the fix: the same keys, the same duration (MAIN_SNAPSHOT is those
    tracks as origin/main 7baa415 compiled them, before foot planting: compiled unplanted)."""
    res = compile_one([{"t": 1, "who": who, "do": "pick_up", "what": "cap"}], {who: {"at": "curb"}},
                      plant_feet=False)
    assert res.ok, errors(res)
    expected = MAIN_SNAPSHOT[who]
    assert res.timeline.duration == expected["duration"]
    for name, keys in expected.items():
        if name == "duration":
            continue
        target, channel = name.split(" ")
        assert track(res, target, channel) == keys, name


@pytest.mark.parametrize("who", ["dad", "mom", "kid"])
def test_the_walk_up_from_the_curb_stops_short_of_the_grip_by_the_plain_rule(who):
    """The same pin, by hand: the plain walk-up stops AUTO_WALK_REACH (0.6) of the arm's reach
    short of the grip, measured from the root, and the arm reaches from there."""
    build = CAST_BUILDS[who]
    # unplanted, so pos[2] is the arrival key (planting adds keys at the walk's cycle keys)
    res = compile_one([{"t": 1, "who": who, "do": "pick_up", "what": "cap"}], {who: {"at": "curb"}},
                      plant_feet=False)
    stop = round(cap_grip()[0] + 0.6 * ik.arm_reach(build), 3)
    pos = track(res, f"puppet:{who}", "root.position")
    assert pos[2][1] == [stop, 0.0, 0.5]
    arrive = 2.2 + (0.8 - (cap_grip()[0] + 0.6 * ik.arm_reach(build))) / 0.35  # the walk speed
    assert pos[2][0] == pytest.approx(arrive, abs=1e-3)
    assert track(res, f"puppet:{who}", "root.facing") == [[0.0, 1], [2.2, -1]]  # no turn back
    assert_hand_on_the_grip(res, who, "hand_r", build)


# ------------------------------------------------------------------ still errors

SEATED = {"chair": {"at": "bench_spot"}, "cap": {"at": "bench_spot"}}


@pytest.mark.parametrize("holding,message", [
    (None, 'shot s1, t=1: mom cannot pick_up "cap"; mom\'s arm_r cannot reach: target is 0.23 from the shoulder, '
           "reach is 0.17; mom is seated in chair.seat; use stand_up first"),
    ("hymn_books", 'shot s1, t=1: mom cannot pick_up "cap"; mom\'s arm_l cannot reach: target is 0.28 from the '
                   "shoulder, reach is 0.17; mom is seated in chair.seat; use stand_up first"),
])
def test_a_seated_puppet_that_cannot_bend_down_to_the_cap_is_out_of_reach(holding, message):
    start = {"mom": {"at": "bench_spot", "in": "chair.seat", **({"holding": holding} if holding else {})}}
    res = compile_one([{"t": 1, "who": "mom", "do": "pick_up", "what": "cap"}], start, SEATED)
    assert errors(res) == [("out_of_reach", message)]
    assert hint_for(res.errors[0]) == "add a stand_up for mom before this pick_up"


@pytest.mark.parametrize("holding", [None, "hymn_books"])
def test_the_seated_hint_applied_picks_the_cap_up_off_the_floor(holding):
    """errors.HINT_VARIANTS' fix for the seated miss works: stand_up, then the same pick_up once
    the stand_up has ended (POSE_SECONDS later) reaches the cap at the chair's foot."""
    start = {"mom": {"at": "bench_spot", "in": "chair.seat", **({"holding": holding} if holding else {})}}
    res = compile_one([{"t": 1, "who": "mom", "do": "stand_up"},
                       {"t": 1 + B.POSE_SECONDS, "who": "mom", "do": "pick_up", "what": "cap"}], start, SEATED)
    assert res.ok, errors(res)


def test_the_same_reach_standing_beside_the_chair_steps_instead():
    """What the seated puppet is denied: standing on the same mark, it steps back and reaches."""
    res = compile_one([{"t": 1, "who": "mom", "do": "pick_up", "what": "cap"}], {"mom": {"at": "bench_spot"}}, SEATED)
    assert res.ok, errors(res)
    grip = cap_grip((-0.4, 0.0))
    t = assert_hand_on_the_grip(res, "mom", "hand_r", "average", grip)
    assert at(res, "puppet:mom", "root.position", t)[0] < -0.4


def test_a_seated_puppet_never_walks_to_the_cap():
    """A cap further off than the plain walk-up distance is not stepped to from a seat either."""
    res = compile_one([{"t": 1, "who": "mom", "do": "pick_up", "what": "cap"}],
                      {"mom": {"at": "bench_spot", "in": "chair.seat"}}, {"chair": {"at": "bench_spot"},
                                                                         "cap": {"at": "door"}})
    assert errors(res) == [("seated", 'shot s1, t=1: mom cannot pick_up "cap"; mom is seated in chair.seat; use stand_up first')]


HIGH = {"high": [0.5, 1.0]}  # x = 0, y = 1.0: far above every standing shoulder


@pytest.mark.parametrize("who,miss", [("dad", "0.61"), ("mom", "0.65"), ("kid", "0.73")])
def test_a_grip_above_standing_reach_is_out_of_reach_from_every_spot(who, miss):
    res = compile_one([{"t": 1, "who": who, "do": "pick_up", "what": "cap"}], {who: {"at": "door"}},
                      {"cap": {"at": "high"}}, marks=HIGH)
    build = CAST_BUILDS[who]
    reach = ik.arm_reach(build)
    message = (f'shot s1, t=1: {who} cannot pick_up "cap"; {who}\'s arm_r cannot reach: target is {miss} from the '
               f"shoulder, reach is {reach:.2f}")
    assert errors(res) == [("out_of_reach", message)]
    # The closest miss is from the spot with the shoulder right under the grip: straight up.
    grip = cap_grip((0.0, 1.0))
    shoulder_y = ik.shoulder_world("arm_r", (0.0, 0.0), 1, build)[1]
    assert f"{grip[1] - shoulder_y:.2f}" == miss
