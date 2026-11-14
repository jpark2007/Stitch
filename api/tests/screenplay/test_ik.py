"""Two-bone IK and rig FK (api/screenplay/ik.py)."""

import math

import pytest

from screenplay.ik import (
    POSES,
    RIG,
    Body,
    OutOfReach,
    arm_bones,
    arm_reach,
    arm_socket,
    bone_frame,
    can_reach,
    handover_point,
    scale,
    shoulder_world,
    socket_world,
    solve_arm,
    solve_body_arm,
)

BUILDS = ["small", "average", "tall"]
FACINGS = [1, -1]
ARMS = ["arm_r", "arm_l"]

DAD = Body("dad", (0.0, 0.0), 1, "tall")
MOM = Body("mom", (0.2, 0.3), -1, "average")


def r3(v):
    return round(v, 3)


# ------------------------------------------------------------------------------ FK

def test_scale_follows_stage_height_and_build():
    assert scale("average") == pytest.approx(0.5)
    assert scale("tall") == pytest.approx(0.55)
    assert scale("small") == pytest.approx(0.4)
    with pytest.raises(ValueError, match="unknown build"):
        scale("giant")


def test_bone_frame_rest_pose():
    assert bone_frame("root", {}) == ((0.0, 0.0), 0.0)
    pos, ang = bone_frame("hand_r", {})
    assert pos == pytest.approx((0.09, 0.28 - 0.17 - 0.17))
    assert ang == 0.0


def test_bone_frame_accumulates_parent_rotation():
    # Torso leaned by pi/2 swings the shoulder pivot (0.09, 0.28) to (-0.28, 0.09).
    pos, ang = bone_frame("arm_r.upper", {"torso": math.pi / 2, "arm_r.upper": 0.3})
    assert pos == pytest.approx((-0.28, 0.09))
    assert ang == pytest.approx(math.pi / 2 + 0.3)


def test_socket_world_rest_and_mirror():
    s = scale("average")
    hand = socket_world("hand_r", {}, (0.0, 0.0), 1, "average")
    assert hand == pytest.approx((0.09 * s, (0.48 - 0.06) * s))
    mirrored = socket_world("hand_r", {}, (0.0, 0.0), -1, "average")
    assert mirrored == pytest.approx((-0.09 * s, (0.48 - 0.06) * s))
    head = socket_world("head", {}, (1.0, 0.3), 1, "tall")
    assert head == pytest.approx((1.0, 0.3 + (0.48 + 0.30 + 0.22) * scale("tall")))


def test_socket_world_rotation_is_ccw_and_forward_for_arms():
    # Positive upper-arm rotation swings the hand forward (+x when facing right).
    hand = socket_world("hand_r", {"arm_r.upper": 0.5}, (0.0, 0.0), 1, "average")
    rest = socket_world("hand_r", {}, (0.0, 0.0), 1, "average")
    assert hand[0] > rest[0]
    # Mirrored, the same rotation swings the hand toward -x.
    hand_m = socket_world("hand_r", {"arm_r.upper": 0.5}, (0.0, 0.0), -1, "average")
    assert hand_m[0] < -rest[0]


def test_unknown_names_raise():
    with pytest.raises(ValueError, match="unknown socket"):
        socket_world("tail", {}, (0, 0), 1, "average")
    with pytest.raises(ValueError, match="unknown bone"):
        bone_frame("wing", {})
    with pytest.raises(ValueError, match="unknown arm"):
        solve_arm("leg_r", (0, 0), (0, 0), 1, "average")


def test_shoulder_world_matches_fk_pivot():
    for build in BUILDS:
        for facing in FACINGS:
            for arm in ARMS:
                upper, _ = arm_bones(arm)
                pivot, _ = bone_frame(upper, {})
                s = scale(build)
                assert shoulder_world(arm, (0.3, 0.1), facing, build) == pytest.approx(
                    (0.3 + facing * pivot[0] * s, 0.1 + (pivot[1] + RIG["hipHeight"]) * s)
                )


def test_arm_reach_and_socket():
    assert arm_reach("average") == pytest.approx(2 * 0.17 * 0.5)
    assert arm_reach("tall", "arm_l") == pytest.approx(2 * 0.17 * 0.55)
    assert arm_socket("arm_r") == "hand_r"
    assert arm_socket("arm_l") == "hand_l"


# ------------------------------------------------------------------------------ IK

def _targets(ground, facing, build, arm):
    """A spread of reachable targets around the shoulder, including full stretch."""
    sh = shoulder_world(arm, ground, facing, build)
    reach = arm_reach(build, arm)
    out = []
    for frac in (0.05, 0.3, 0.6, 0.95, 1.0):
        for deg in range(0, 360, 30):
            a = math.radians(deg)
            out.append((sh[0] + frac * reach * math.cos(a), sh[1] + frac * reach * math.sin(a)))
    return out


@pytest.mark.parametrize("build", BUILDS)
@pytest.mark.parametrize("facing", FACINGS)
@pytest.mark.parametrize("arm", ARMS)
def test_solve_arm_lands_hand_on_target(build, facing, arm):
    ground = (0.37, 0.12)
    upper, lower = arm_bones(arm)
    for target in _targets(ground, facing, build, arm):
        up, lo = solve_arm(arm, target, ground, facing, build)
        got = socket_world(arm_socket(arm), {upper: up, lower: lo}, ground, facing, build)
        assert abs(got[0] - target[0]) < 1e-6 and abs(got[1] - target[1]) < 1e-6, (target, got)
        assert 0.0 <= lo <= math.pi + 1e-12  # elbow bends one way only


@pytest.mark.parametrize("build", BUILDS)
@pytest.mark.parametrize("facing", FACINGS)
def test_out_of_reach_raises_with_distances(build, facing):
    ground = (0.0, 0.0)
    sh = shoulder_world("arm_r", ground, facing, build)
    reach = arm_reach(build)
    target = (sh[0] + facing * reach * 1.2, sh[1])
    with pytest.raises(OutOfReach) as info:
        solve_arm("arm_r", target, ground, facing, build)
    err = info.value
    assert err.arm == "arm_r"
    assert err.distance > err.reach
    assert err.distance == pytest.approx(reach * 1.2)
    assert err.reach == pytest.approx(reach)
    assert "cannot reach" in str(err)
    assert not can_reach("arm_r", target, ground, facing, build)


def test_can_reach_agrees_with_solve_arm():
    ground, facing, build = (0.1, 0.2), -1, "average"
    sh = shoulder_world("arm_r", ground, facing, build)
    reach = arm_reach(build)
    for frac in (0.0, 0.5, 0.999, 1.0, 1.0 + 1e-6, 1.01, 2.0):
        for deg in (0, 45, 90, 200, 300):
            a = math.radians(deg)
            target = (sh[0] + frac * reach * math.cos(a), sh[1] + frac * reach * math.sin(a))
            try:
                solve_arm("arm_r", target, ground, facing, build)
                solved = True
            except OutOfReach:
                solved = False
            assert can_reach("arm_r", target, ground, facing, build) == solved, (frac, deg)
            assert solved == (frac <= 1.0), (frac, deg)


def test_target_on_shoulder_is_solvable():
    sh = shoulder_world("arm_r", (0.0, 0.0), 1, "average")
    up, lo = solve_arm("arm_r", sh, (0.0, 0.0), 1, "average")
    assert lo == pytest.approx(math.pi)
    got = socket_world("hand_r", {"arm_r.upper": up, "arm_r.lower": lo}, (0.0, 0.0), 1, "average")
    assert got == pytest.approx(sh)


def test_hold_pose_matches_sample_hand_position():
    # Mom on steps_top holding the books: the hand the sample drops them from.
    hold = POSES["poses"]["hold"]
    hand = socket_world("hand_r", {"arm_r.upper": hold["arm_r.upper"], "arm_r.lower": hold["arm_r.lower"]},
                        MOM.ground, MOM.facing, MOM.build)
    assert (r3(hand[0]), r3(hand[1] - 0.08)) == (0.045, 0.513)


# ------------------------------------------------------------------------ handover

def test_handover_point_reproduces_sample_rotations():
    meet = handover_point(DAD, MOM)
    dad = solve_arm("arm_r", meet, DAD.ground, DAD.facing, DAD.build)
    mom = solve_arm("arm_r", meet, MOM.ground, MOM.facing, MOM.build)
    assert (r3(dad[0]), r3(dad[1])) == (2.167, 1.183)
    assert (r3(mom[0]), r3(mom[1])) == (-0.342, 1.45)


def test_handover_point_formula():
    sg = shoulder_world("arm_r", DAD.ground, DAD.facing, DAD.build)
    st = shoulder_world("arm_r", MOM.ground, MOM.facing, MOM.build)
    gap = math.hypot(st[0] - sg[0], st[1] - sg[1])
    f = 0.83 * arm_reach("tall") / gap
    assert handover_point(DAD, MOM) == pytest.approx((sg[0] + (st[0] - sg[0]) * f, sg[1] + (st[1] - sg[1]) * f))
    # The giver's hand is 0.83 of its reach out from its shoulder.
    meet = handover_point(DAD, MOM)
    assert math.hypot(meet[0] - sg[0], meet[1] - sg[1]) == pytest.approx(0.83 * arm_reach("tall"))


def test_handover_point_with_left_arms():
    a = Body("a", (0.0, 0.0), 1, "average")
    b = Body("b", (0.2, 0.0), -1, "average")
    meet = handover_point(a, b, "arm_l", "arm_l")
    for body in (a, b):
        up, lo = solve_arm("arm_l", meet, body.ground, body.facing, body.build)
        got = socket_world("hand_l", {"arm_l.upper": up, "arm_l.lower": lo}, body.ground, body.facing, body.build)
        assert got == pytest.approx(meet)


def test_handover_out_of_reach_names_the_puppet():
    far = Body("mom", (0.9, 0.0), -1, "average")
    with pytest.raises(OutOfReach) as info:
        handover_point(DAD, far)
    assert info.value.who == "mom"
    assert info.value.distance > info.value.reach
    assert "mom's arm_r cannot reach" in str(info.value)


def test_handover_point_coincident_shoulders():
    a = Body("a", (0.0, 0.0), 1, "average")
    meet = handover_point(a, Body("b", (0.0, 0.0), 1, "average"))
    assert meet == shoulder_world("arm_r", a.ground, a.facing, a.build)


# --------------------------------------------- agreement with the reference generator

def _reference_helpers() -> dict:
    """The IK/FK helpers of api/tests/gen_hymn_books_sample.py, loaded without running the script.

    Importing the generator would rewrite web/public/samples/, so only its imports, constants
    and function definitions are executed.
    """
    import ast
    import json
    from pathlib import Path

    src_path = Path(__file__).resolve().parents[1] / "gen_hymn_books_sample.py"
    tree = ast.parse(src_path.read_text())
    keep = {"REPO", "RIG", "POSES", "VOC", "Z", "R"}
    body = []
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom, ast.FunctionDef)):
            body.append(node)
        elif isinstance(node, ast.Assign) and all(isinstance(t, ast.Name) and t.id in keep for t in node.targets):
            body.append(node)
    ns: dict = {"__file__": str(src_path), "json": json, "math": math}
    exec(compile(ast.Module(body=body, type_ignores=[]), str(src_path), "exec"), ns)
    return ns


REF = _reference_helpers()
SIZES_HAND = REF["VOC"]["sizes"]["hand"]
STEPS_FOOT = (2 * 0.5 - 1, 0.0)   # marks from the reference scene
STEPS_TOP = (2 * 0.6 - 1, 0.3)


def test_pick_up_reach_agrees_with_reference_generator():
    land = (STEPS_TOP[0] - 0.08, STEPS_TOP[1])
    grip = (land[0], land[1] + SIZES_HAND)
    want = REF["ik"]("arm_r", grip, STEPS_FOOT, 1, "tall")
    got = solve_arm("arm_r", grip, STEPS_FOOT, 1, "tall")
    assert abs(got[0] - want[0]) < 1e-6 and abs(got[1] - want[1]) < 1e-6, (got, want)


def test_handover_reaches_agree_with_reference_generator():
    dad = Body("dad", STEPS_FOOT, 1, "tall")
    mom = Body("mom", STEPS_TOP, -1, "average")
    sd = REF["shoulder_world"]("arm_r", dad.ground, 1, "tall")
    sm = REF["shoulder_world"]("arm_r", mom.ground, -1, "average")
    reach_d = 2 * REF["RIG"]["bones"]["arm_r.upper"]["length"] * REF["scale"]("tall")
    f = reach_d * 0.83 / math.hypot(sm[0] - sd[0], sm[1] - sd[1])
    ref_meet = (sd[0] + (sm[0] - sd[0]) * f, sd[1] + (sm[1] - sd[1]) * f)
    meet = handover_point(dad, mom)
    assert abs(meet[0] - ref_meet[0]) < 1e-6 and abs(meet[1] - ref_meet[1]) < 1e-6
    for body in (dad, mom):   # mom is the mirrored puppet (facing -1)
        want = REF["ik"]("arm_r", ref_meet, body.ground, body.facing, body.build)
        got = solve_arm("arm_r", meet, body.ground, body.facing, body.build)
        assert abs(got[0] - want[0]) < 1e-6 and abs(got[1] - want[1]) < 1e-6, (body.id, got, want)


@pytest.mark.parametrize("facing", FACINGS)
@pytest.mark.parametrize("build", BUILDS)
def test_reference_fk_agrees(build, facing):
    rots = {"arm_r.upper": 0.7, "arm_r.lower": 1.1, "torso": -0.2}
    for socket in ("hand_r", "hand_l", "head"):
        want = REF["socket_world"](socket, rots, (0.3, 0.1), facing, build)
        got = socket_world(socket, rots, (0.3, 0.1), facing, build)
        assert got == pytest.approx(want, abs=1e-12)


# ------------------------------------------------------------- clamping and mirroring

@pytest.mark.parametrize("facing", FACINGS)
@pytest.mark.parametrize("arm", ARMS)
def test_unreachable_target_clamps_to_full_extension(facing, arm):
    ground, build = (0.1, 0.0), "average"
    sh = shoulder_world(arm, ground, facing, build)
    for target in ((sh[0] + 5.0, sh[1] + 0.2), (sh[0] - 3.0, sh[1] - 1.0), (sh[0], sh[1] + 10.0), (sh[0] + 1e9, sh[1])):
        with pytest.raises(OutOfReach):
            solve_arm(arm, target, ground, facing, build)
        up, lo = solve_arm(arm, target, ground, facing, build, clamp=True)
        assert math.isfinite(up) and math.isfinite(lo)
        assert lo == 0.0
        upper, lower = arm_bones(arm)
        hand = socket_world(arm_socket(arm), {upper: up, lower: lo}, ground, facing, build)
        # Full stretch, pointing straight at the target.
        assert math.hypot(hand[0] - sh[0], hand[1] - sh[1]) == pytest.approx(arm_reach(build, arm))
        to_t = math.atan2(target[1] - sh[1], target[0] - sh[0])
        to_h = math.atan2(hand[1] - sh[1], hand[0] - sh[0])
        assert math.cos(to_t - to_h) == pytest.approx(1.0)


def test_clamp_does_not_change_reachable_solutions():
    target = (0.15, 0.3)
    assert solve_arm("arm_r", target, (0.0, 0.0), 1, "tall", clamp=True) == solve_arm("arm_r", target, (0.0, 0.0), 1, "tall")


def test_solve_body_arm_clamps():
    body = Body("dad", (0.0, 0.0), -1, "tall")
    with pytest.raises(OutOfReach):
        solve_body_arm(body, "arm_r", (-3.0, 0.4))
    up, lo = solve_body_arm(body, "arm_r", (-3.0, 0.4), clamp=True)
    assert math.isfinite(up) and lo == 0.0


def test_non_finite_or_bad_inputs_raise_instead_of_nan():
    with pytest.raises(ValueError):
        solve_arm("arm_r", (math.nan, 0.3), (0.0, 0.0), 1, "average", clamp=True)
    with pytest.raises(ValueError):
        solve_arm("arm_r", (math.inf, 0.3), (0.0, 0.0), 1, "average", clamp=True)
    with pytest.raises(ValueError):
        solve_arm("arm_r", (0.1, 0.3), (0.0, 0.0), 0, "average")


@pytest.mark.parametrize("arm", ARMS)
def test_mirrored_puppet_uses_the_same_local_rotations(arm):
    # A target mirrored about the puppet's vertical axis gives identical (unmirrored) rotations.
    # Offsets are from the shoulder, which itself mirrors with the puppet.
    ground, build = (0.25, 0.1), "tall"
    sr, sl = shoulder_world(arm, ground, 1, build), shoulder_world(arm, ground, -1, build)
    assert sl[0] - ground[0] == pytest.approx(-(sr[0] - ground[0]))
    for dx, dy in ((0.12, 0.05), (-0.05, -0.1), (0.0, 0.15), (0.15, -0.08)):
        right = solve_arm(arm, (sr[0] + dx, sr[1] + dy), ground, 1, build)
        left = solve_arm(arm, (sl[0] - dx, sl[1] + dy), ground, -1, build)
        assert left == pytest.approx(right, abs=1e-12)
        upper, lower = arm_bones(arm)
        hand = socket_world(arm_socket(arm), {upper: left[0], lower: left[1]}, ground, -1, build)
        assert hand == pytest.approx((sl[0] - dx, sl[1] + dy), abs=1e-9)


def test_mirrored_puppet_reaches_forward_with_positive_upper():
    # Facing left, a target in front (smaller x) at shoulder height swings the arm forward (+).
    ground = (0.5, 0.0)
    sh = shoulder_world("arm_r", ground, -1, "average")
    up, lo = solve_arm("arm_r", (sh[0] - 0.1, sh[1]), ground, -1, "average")
    assert up > 0 and 0 < lo < math.pi


def test_target_straight_above_the_shoulder_is_pi_for_both_facings():
    for facing in FACINGS:
        sh = shoulder_world("arm_r", (0.0, 0.0), facing, "average")
        up, lo = solve_arm("arm_r", (sh[0], sh[1] + 0.17), (0.0, 0.0), facing, "average")
        assert up + lo / 2 == pytest.approx(math.pi)
        up, lo = solve_arm("arm_r", (sh[0], sh[1] + 5.0), (0.0, 0.0), facing, "average", clamp=True)
        assert (up, lo) == (pytest.approx(math.pi), 0.0)
