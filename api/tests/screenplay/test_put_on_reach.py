"""put_on with realistic grips (finding F5): the hand reaches as far as it can and the prop
settles onto the head.

A grip above the anchor (a cap held by its top edge, a side grip, the default centre on a small
build) puts the exact grip point above the wearer's head, out of the arm's reach. body.put_on_keys
then stops the hand at PUT_ON_REACH_FRACTION of full reach, parents the prop to the head with the
offset where the hand let go, and settles it to [0, 0, 0] over PUT_ON_SETTLE_SECONDS. A grip the
hand can carry all the way (the catalogue cap's [0.5, 0.6]) emits exactly the keys it always did.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

import schemas
from screenplay.body import (
    PUT_ON_REACH_FRACTION,
    PUT_ON_SETTLE_MAX,
    PUT_ON_SETTLE_SECONDS,
    REACH_SECONDS,
    REST_ARM,
    PropGeom,
    anchor_from_grip,
    grip_from_anchor,
    put_on_keys,
)
from screenplay.compiler import compile as compile_screenplay
from screenplay.ik import (
    Body,
    OutOfReach,
    arm_bones,
    arm_reach,
    body_socket,
    shoulder_world,
    solve_body_arm,
)
from screenplay.schema import VOCABULARY, CompileResult, Screenplay
from screenplay.tracks import evaluate
from verb_cases import Scene, _a, _at, _one, base_scene

R = REACH_SECONDS
S = PUT_ON_SETTLE_SECONDS
BUILDS = {"dad": "tall", "mom": "average", "kid": "small"}
CLAMPED_GRIPS = [[0.5, 0.0], [0.0, 0.5], [1.0, 0.5], None]
TOL = 2e-3  # compiled keys are rounded to 3 dp


# ---------------------------------------------------------------------------- helpers

def cap_scene(grip) -> Scene:
    """base_scene with the cap's grip replaced (None = the default centre)."""
    scene = base_scene()
    scene.props["cap"] = scene.props["cap"].model_copy(update={"grip": grip})
    return scene


def compile_case(sp: Screenplay, scene: Scene) -> CompileResult:
    return compile_screenplay(sp, sets=scene.sets, props=scene.props, people=scene.people)


def put_on_own_head(who: str, facing: str = "right") -> Screenplay:
    return _one(_a(0, "put_on", who=who, what="cap"),
                start={who: _at("bench_spot", holding="cap", facing=facing)})


def track(res: CompileResult, target: str, channel: str) -> list:
    for tr in res.timeline.tracks:
        if (tr.target, tr.channel) == (target, channel):
            return [[k[0], k[1]] for k in tr.keys]
    return []


def value(res: CompileResult, target: str, channel: str, t: float):
    return evaluate(res.timeline.tracks, target, channel, t)


def swap_time(res: CompileResult, prop: str, parent: str) -> float:
    (t,) = [t for t, v in track(res, f"prop:{prop}", "parent") if v == parent]
    return t


def body_at(res: CompileResult, who: str, t: float) -> Body:
    root = value(res, f"puppet:{who}", "root.position", t)
    facing = int(value(res, f"puppet:{who}", "root.facing", t) or 1)
    return Body(who, (root[0], root[1]), facing, BUILDS[who])


def hand_at(res: CompileResult, body: Body, arm: str, t: float):
    upper, lower = arm_bones(arm)
    rots = {b: value(res, f"puppet:{body.id}", f"{b}.rot", t) or 0.0 for b in (upper, lower)}
    return body_socket(body, "hand_r" if arm == "arm_r" else "hand_l", rots)


def unclamped_keys(body: Body, t: float, current: tuple[float, float], prop: PropGeom, wearer: Body, arm: str) -> list:
    """put_on_keys as it was before the clamp (origin/main 7baa415), written out by hand."""
    head = body_socket(wearer, "head", {})
    u, lo = solve_body_arm(body, arm, grip_from_anchor(prop, head))
    upper, lower = arm_bones(arm)
    who, socket = f"puppet:{body.id}", f"puppet:{wearer.id}.head"
    return [
        (who, f"{upper}.rot", t, float(current[0])), (who, f"{lower}.rot", t, float(current[1])),
        (who, f"{upper}.rot", t + R, float(u)), (who, f"{lower}.rot", t + R, float(lo)),
        (who, f"{upper}.rot", t + 2 * R, 0.0), (who, f"{lower}.rot", t + 2 * R, 0.0),
        (f"prop:{prop.id}", "parent", t + R, socket),
        (f"prop:{prop.id}", "position", t + R, [0.0, 0.0, 0.0]),
    ]


# ------------------------------------------------------------- clamped grips compile

@pytest.mark.parametrize("facing", ["right", "left"])
@pytest.mark.parametrize("who", list(BUILDS))
@pytest.mark.parametrize("grip", CLAMPED_GRIPS, ids=str)
def test_put_on_own_head_compiles_for_every_grip_and_build(grip, who, facing):
    res = compile_case(put_on_own_head(who, facing), cap_scene(grip))
    assert res.ok, [e.message for e in res.errors]
    parents = track(res, "prop:cap", "parent")
    assert parents[-1][1] == f"puppet:{who}.head"
    positions = track(res, "prop:cap", "position")
    assert positions[-1][1] == [0.0, 0.0, 0.0]


def positions_from(res: CompileResult, prop: str, t0: float) -> dict[float, list]:
    """The prop's position keys at or after t0 (the start-held hand attach is at t=0)."""
    return {round(t, 3): v for t, v in track(res, f"prop:{prop}", "position") if t >= t0 - 1e-9}


# (grip, facing) pairs whose exact grip point is out of hand_r's reach for every build: a grip
# on the far side of the cap from the right arm (a side grip, and the top edge either way).
CLAMPED = [([0.5, 0.0], "right"), ([0.5, 0.0], "left"), ([0.0, 0.5], "right"), ([1.0, 0.5], "left")]


@pytest.mark.parametrize("who", list(BUILDS))
@pytest.mark.parametrize(("grip", "facing"), CLAMPED, ids=str)
def test_a_clamped_put_on_lets_go_within_reach_and_settles(grip, facing, who):
    """The hand stops short, the cap is parented with an offset and settles onto the head."""
    scene = cap_scene(grip)
    res = compile_case(put_on_own_head(who, facing), scene)
    assert res.ok, [e.message for e in res.errors]
    t_on = swap_time(res, "cap", f"puppet:{who}.head")
    me = body_at(res, who, t_on)
    arm = "arm_r"  # the cap starts in hand_r
    geom = PropGeom.from_prop(scene.props["cap"])
    head = body_socket(me, "head", {})
    with pytest.raises(OutOfReach):  # the precondition: the exact grip point is out of reach
        solve_body_arm(me, arm, grip_from_anchor(geom, head))

    # a position key at the parent swap: the offset from the head socket, short and non-zero
    positions = positions_from(res, "cap", t_on)
    offset = positions[round(t_on, 3)]
    assert offset[2] == 0.0
    assert 0.0 < math.hypot(offset[0], offset[1]) <= PUT_ON_SETTLE_MAX
    # ... then [0, 0, 0] PUT_ON_SETTLE_SECONDS later, and nothing after it
    t_settled = round(t_on + S, 3)
    assert positions[t_settled] == [0.0, 0.0, 0.0]
    assert max(positions) == t_settled

    # the reach key is a valid solve: the hand is inside the arm's reach, at the clamp fraction
    hand = hand_at(res, me, arm, t_on)
    sh = shoulder_world(arm, me.ground, me.facing, me.build)
    reach = math.hypot(hand[0] - sh[0], hand[1] - sh[1])
    assert reach <= arm_reach(me.build, arm)
    assert reach == pytest.approx(PUT_ON_REACH_FRACTION * arm_reach(me.build, arm), abs=TOL)

    # the stage puts the anchor on head + offset (upright, unmirrored): where the hand left it
    left_at = anchor_from_grip(geom, hand)
    assert (head[0] + offset[0], head[1] + offset[1]) == pytest.approx(left_at, abs=TOL)

    # the arm is back at rest by t + 2R, after the cap has settled
    upper, lower = arm_bones(arm)
    t_rest = t_on + R
    assert t_settled < t_rest
    assert [value(res, f"puppet:{who}", f"{b}.rot", t_rest) for b in (upper, lower)] == list(REST_ARM)


def test_kid_with_the_default_centre_grip_is_clamped_but_the_grown_ups_are_not():
    """The F5 case for grip None: only the small build's arm falls short."""
    for who, clamped in (("dad", False), ("mom", False), ("kid", True)):
        res = compile_case(put_on_own_head(who), cap_scene(None))
        assert res.ok
        t_on = swap_time(res, "cap", f"puppet:{who}.head")
        n = len(positions_from(res, "cap", t_on))
        assert n == (2 if clamped else 1), who


# ------------------------------------------------ the reachable grip does not change

@pytest.mark.parametrize("arm", ["arm_r", "arm_l"])
@pytest.mark.parametrize("facing", [1, -1])
@pytest.mark.parametrize("who", list(BUILDS))
def test_reachable_grip_keys_are_exactly_the_unclamped_ones(who, facing, arm):
    geom = PropGeom.from_prop(base_scene().props["cap"])
    assert geom.grip == (0.5, 0.6)
    me = Body(who, (-0.4, 0.0), facing, BUILDS[who])
    current = (0.3, 1.2)
    keys = put_on_keys(me, 1.7, current, geom, me, arm)
    assert keys == unclamped_keys(me, 1.7, current, geom, me, arm)


@pytest.mark.parametrize("who", list(BUILDS))
def test_reachable_grip_compiles_to_one_attach_at_the_head(who):
    res = compile_case(put_on_own_head(who), base_scene())
    assert res.ok, [e.message for e in res.errors]
    t_on = swap_time(res, "cap", f"puppet:{who}.head")
    # held in the hand from the start, then one attach at the head: no settle key
    assert track(res, "prop:cap", "position") == [[0.0, [0.0, 0.0, 0.0]], [t_on, [0.0, 0.0, 0.0]]]
    assert track(res, "prop:cap", "parent") == [[0.0, f"puppet:{who}.hand_r"], [t_on, f"puppet:{who}.head"]]
    # the hand carries the grip exactly to where the anchor rests on the head
    me = body_at(res, who, t_on)
    geom = PropGeom.from_prop(base_scene().props["cap"])
    hand = hand_at(res, me, "arm_r", t_on)
    assert anchor_from_grip(geom, hand) == pytest.approx(body_socket(me, "head", {}), abs=TOL)


# ------------------------------------------- test_compiler's per-verb case for put_on

SAMPLES = Path(__file__).resolve().parents[3] / "web" / "public" / "samples"


def hymn_scene() -> Scene:
    """The scene test_compiler.py compiles its per-verb cases against, rebuilt here."""
    memory = json.loads((SAMPLES / "hymn_books.memory.json").read_text())
    sets = {s["id"]: schemas.SetDef.model_validate(s) for s in memory["sets"]}
    props = {p["id"]: schemas.Prop.model_validate(p) for p in memory["props"]}
    for extra in (
        {"id": "chair", "name": "chair", "size": "furniture", "art": {"body": None}, "sockets": {"seat": [0.5, 0.5]}},
        {"id": "sedan", "name": "sedan", "size": "vehicle", "aspect": 2.0, "art": {"body": None},
         "sockets": {"driver": [0.35, 0.55], "passenger": [0.6, 0.55]},
         "states": {"parked": {}, "open": {}, "closed": {}}},
    ):
        props[extra["id"]] = schemas.Prop.model_validate(extra)
    people = {p["id"]: schemas.Person.model_validate(p) for p in memory["people"]}
    people["kid"] = schemas.Person(id="kid", family_id="sample_family", name="Kid",
                                   character={"color": "#0a0", "build": "small"})
    return Scene(sets=sets, props=props, people=people)


def test_compiler_per_verb_case_mom_puts_on_the_hymn_books():
    scene = hymn_scene()
    assert scene.props["hymn_books"].grip == [0.5, 0.0]
    sp = Screenplay.model_validate({
        "memoryId": "m", "cast": ["dad", "mom", "kid"], "props": ["hymn_books", "chair", "sedan"],
        "shots": [{
            "id": "s1", "set": "church_steps_1988",
            "start": {
                "dad": {"at": "steps_foot", "facing": "right"},
                "mom": {"at": "steps_top", "holding": "hymn_books", "facing": "left"},
                "kid": {"at": "offstage_left"},
            },
            "propStart": {"chair": {"at": "steps_left"}, "sedan": {"at": "curb"}},
            "actions": [{"t": 0, "who": "mom", "do": "put_on", "what": "hymn_books"}],
        }],
    })
    res = compile_case(sp, scene)
    assert res.ok, [e.message for e in res.errors]
    t_on = swap_time(res, "hymn_books", "puppet:mom.head")
    positions = track(res, "prop:hymn_books", "position")
    assert positions[-2][0] == t_on and 0 < math.hypot(*positions[-2][1][:2]) <= PUT_ON_SETTLE_MAX
    assert positions[-1] == [round(t_on + S, 3), [0.0, 0.0, 0.0]]


# ---------------------------------------------------------- out of reach still fails

FAR_MESSAGE = ('shot s1, t=0: mom cannot put_on "cap"; '
               "mom's arm_r cannot reach: target is 0.76 from the shoulder, reach is 0.17")


def far_scene(grip) -> Scene:
    scene = cap_scene(grip)
    scene.sets["yard"].marks["far_spot"] = [0.7, 0.0]  # x = 0.4: 0.8 from bench_spot (x = -0.4)
    return scene


@pytest.mark.parametrize("grip", [[0.5, 0.6], *CLAMPED_GRIPS], ids=str)
def test_putting_a_cap_on_someone_0_8_away_is_out_of_reach(grip):
    scene = far_scene(grip)
    sp = _one(_a(0, "put_on", who="mom", what="cap", target="kid"),
              start={"mom": _at("bench_spot", holding="cap"), "kid": _at("far_spot", facing="left")})
    res = compile_case(sp, scene)
    assert not res.ok
    (err,) = res.errors
    assert err.code == "out_of_reach"
    # the reason is the exact grip point's, as before the clamp existed
    mom = Body("mom", (-0.4, 0.0), 1, "average")
    kid = Body("kid", (0.4, 0.0), -1, "small")
    target = grip_from_anchor(PropGeom.from_prop(scene.props["cap"]), body_socket(kid, "head", {}))
    with pytest.raises(OutOfReach) as exact:
        solve_body_arm(mom, "arm_r", target)
    assert f"{exact.value.distance:.2f}" in err.message
    if grip in ([0.5, 0.6], [0.5, 0.0], None):  # grips centred across the cap: the same distance
        assert err.message == FAR_MESSAGE


def test_a_carried_prop_held_by_its_top_is_too_far_to_settle():
    """A grip so high the prop would drop more than PUT_ON_SETTLE_MAX stays an error."""
    me = Body("dad", (0.0, 0.0), 1, "tall")
    case = PropGeom("case", VOCABULARY["sizes"]["carried"], VOCABULARY["sizes"]["carried"], (0.5, 1.0), (0.5, 0.0))
    with pytest.raises(OutOfReach) as ei:
        put_on_keys(me, 0.0, (0.3, 1.2), case, me)
    assert ei.value.who == "dad" and ei.value.arm == "arm_r"
    assert ei.value.distance > ei.value.reach


def test_settle_constants():
    assert 0 < PUT_ON_SETTLE_SECONDS < REACH_SECONDS
    assert 0 < PUT_ON_REACH_FRACTION < 1
    # every grip of a hand-size prop settles for the smallest build
    assert PUT_ON_SETTLE_MAX >= VOCABULARY["sizes"]["hand"]
