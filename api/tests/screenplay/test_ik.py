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
