"""Body and prop-gesture key generators (api/screenplay/body.py).

The hymn-book cases reproduce keys from web/public/samples/hymn_books.timeline.json.
"""

import json
import math
from pathlib import Path

import pytest

import schemas
from screenplay.body import (
    DROP_OFFSET,
    FALL_SECONDS,
    PASS_SECONDS,
    POSE_SECONDS,
    REACH_SECONDS,
    RETURN_SECONDS,
    PropGeom,
    anchor_from_grip,
    drop_keys,
    grip_from_anchor,
    hand_to_keys,
    hold_keys,
    hold_pose,
    hug_keys,
    kneel_keys,
    last_values,
    lean_keys,
    pick_up_keys,
    place_on_keys,
    point_keys,
    pose_keys,
    put_on_keys,
    sit_keys,
    stand_keys,
    state_keys,
    wave_keys,
)
from screenplay.ik import POSES, RIG, Body, OutOfReach, body_socket, scale, socket_world

SAMPLES = Path(__file__).resolve().parents[3] / "web" / "public" / "samples"
MEMORY = json.loads((SAMPLES / "hymn_books.memory.json").read_text())
TIMELINE = json.loads((SAMPLES / "hymn_books.timeline.json").read_text())

BOOKS = PropGeom.from_prop(schemas.Prop.model_validate(MEMORY["props"][0]))
MOM = Body("mom", (0.2, 0.3), -1, "average")      # steps_top, facing left
DAD = Body("dad", (0.0, 0.0), 1, "tall")          # steps_foot, facing right
HOLD = (0.3, 1.2)
LANDING = (0.12, 0.3, 0.5)


def r3(v):
    if isinstance(v, (list, tuple)):
        return [round(x, 3) for x in v]
    return round(v, 3) if isinstance(v, float) else v


def track(keys, target, channel):
    """[[t, value], ...] for one track, sorted, rounded to 3 dp like the sample."""
    return [[r3(t), r3(v)] for (tg, ch, t, v) in sorted(keys, key=lambda k: k[2]) if tg == target and ch == channel]


def sample(target, channel, t0, t1):
    """The sample's keys for one track between t0 and t1 inclusive."""
    for tr in TIMELINE["tracks"]:
        if tr["target"] == target and tr["channel"] == channel:
            return [k for k in tr["keys"] if t0 - 1e-9 <= k[0] <= t1 + 1e-9]
    raise KeyError((target, channel))


def channels(keys):
    return {(tg, ch) for (tg, ch, _t, _v) in keys}


def assert_well_formed(keys):
    for target, channel, t, value in keys:
        assert target.startswith(("puppet:", "prop:")), target
        assert isinstance(t, float) or isinstance(t, int)
        if channel in ("root.position", "position"):
            assert isinstance(value, list) and len(value) == 3 and all(isinstance(x, float) for x in value)
    # every parent key has a position key at the same time
    for target, channel, t, _ in keys:
        if channel == "parent":
            assert any(tg == target and ch == "position" and tt == t for tg, ch, tt, _ in keys), (target, t)


# ------------------------------------------------------------------------------ geometry

def test_prop_geom_from_sample_prop():
    assert BOOKS.id == "hymn_books"
    assert BOOKS.height == pytest.approx(0.08)
    assert BOOKS.width == pytest.approx(0.08)
    assert BOOKS.anchor == (0.5, 1.0)
    assert BOOKS.grip == (0.5, 0.0)


def test_prop_geom_defaults_grip_to_centre_and_uses_aspect():
    prop = schemas.Prop(id="cake", name="cake", size="carried", art={"body": None}, aspect=2.0, anchor=[0.25, 0.9])
    geom = PropGeom.from_prop(prop)
    assert geom.grip == (0.5, 0.5)
    assert geom.height == pytest.approx(0.2)
    assert geom.width == pytest.approx(0.4)


def test_anchor_and_grip_are_inverse():
    geom = PropGeom("x", 0.2, 0.3, (0.2, 0.9), (0.7, 0.1))
    a = anchor_from_grip(geom, (0.4, 0.5))
    # anchor is left of the grip (0.5 * 0.3) and below it (0.8 * 0.2)
    assert a == pytest.approx((0.4 - 0.15, 0.5 - 0.16))
    assert grip_from_anchor(geom, a) == pytest.approx((0.4, 0.5))
    assert grip_from_anchor(geom, (a[0], a[1], 0.5)) == pytest.approx((0.4, 0.5))


def test_hold_pose_values():
    assert hold_pose("arm_r") == (0.3, 1.2)
    assert hold_pose("arm_l") == (0.3, 1.2)


# ------------------------------------------------------------------------ sample: drop

def test_drop_reproduces_sample():
    keys, landing = drop_keys(MOM, 4.45, HOLD, BOOKS, surface_y=0.3, z=0.5)
    assert_well_formed(keys)
    assert r3(landing) == list(LANDING)
    assert track(keys, "prop:hymn_books", "parent") == [[4.45, "ground"]]
    assert track(keys, "prop:hymn_books", "position") == [[4.45, [0.045, 0.513, 0.5]], [4.75, [0.12, 0.3, 0.5]]]
    assert track(keys, "puppet:mom", "arm_r.upper.rot") == [[4.45, 0.3], [4.75, 0.0]]
    assert track(keys, "puppet:mom", "arm_r.lower.rot") == [[4.45, 1.2], [4.75, 0.0]]
    for ch in ("arm_r.upper.rot", "arm_r.lower.rot"):
        assert track(keys, "puppet:mom", ch) == sample("puppet:mom", ch, 4.45, 4.75)
    assert track(keys, "prop:hymn_books", "parent") == sample("prop:hymn_books", "parent", 4.45, 4.75)
    assert track(keys, "prop:hymn_books", "position") == sample("prop:hymn_books", "position", 4.45, 4.75)


def test_drop_lands_in_front_of_the_feet():
    right = Body("dad", (0.5, 0.0), 1, "tall")
    _, landing = drop_keys(right, 1.0, HOLD, BOOKS, surface_y=0.0, z=0.4)
    assert landing == pytest.approx((0.5 + DROP_OFFSET, 0.0, 0.4))


def test_drop_with_left_arm_starts_at_left_hand():
    keys, _ = drop_keys(MOM, 2.0, HOLD, BOOKS, surface_y=0.3, z=0.5, arm="arm_l")
    hand = socket_world("hand_l", {"arm_l.upper": 0.3, "arm_l.lower": 1.2}, MOM.ground, MOM.facing, MOM.build)
    start = [v for tg, ch, t, v in keys if ch == "position" and t == 2.0][0]
    assert start == pytest.approx([hand[0], hand[1] - BOOKS.height, 0.5])
    assert ("puppet:mom", "arm_l.upper.rot") in channels(keys)
    assert ("puppet:mom", "arm_r.upper.rot") not in channels(keys)


# --------------------------------------------------------------------- sample: pick up

def test_pick_up_reproduces_sample():
    keys = pick_up_keys(DAD, 6.7, (0.0, 0.0), BOOKS, LANDING)
    assert_well_formed(keys)
    assert track(keys, "puppet:dad", "arm_r.upper.rot") == [[6.7, 0.0], [7.1, -0.052], [7.5, 0.3]]
    assert track(keys, "puppet:dad", "arm_r.lower.rot") == [[6.7, 0.0], [7.1, 2.256], [7.5, 1.2]]
    assert track(keys, "prop:hymn_books", "parent") == [[7.1, "puppet:dad.hand_r"]]
    assert track(keys, "prop:hymn_books", "position") == [[7.1, [0.0, 0.0, 0.0]]]
    for ch in ("arm_r.upper.rot", "arm_r.lower.rot"):
        assert track(keys, "puppet:dad", ch) == sample("puppet:dad", ch, 6.7, 7.5)
    assert track(keys, "prop:hymn_books", "parent") == sample("prop:hymn_books", "parent", 7.1, 7.1)


def test_pick_up_hand_reaches_the_grip():
    keys = pick_up_keys(DAD, 6.7, (0.0, 0.0), BOOKS, LANDING)
    up = [v for tg, ch, t, v in keys if ch == "arm_r.upper.rot" and t == 6.7 + REACH_SECONDS][0]
    lo = [v for tg, ch, t, v in keys if ch == "arm_r.lower.rot" and t == 6.7 + REACH_SECONDS][0]
    hand = body_socket(DAD, "hand_r", {"arm_r.upper": up, "arm_r.lower": lo})
    assert hand == pytest.approx(grip_from_anchor(BOOKS, LANDING), abs=1e-9)


def test_pick_up_out_of_reach():
    with pytest.raises(OutOfReach) as info:
        pick_up_keys(DAD, 1.0, (0.0, 0.0), BOOKS, (0.8, 0.0, 0.5))
    assert info.value.who == "dad"
    assert info.value.distance > info.value.reach


def test_pick_up_with_left_arm_uses_hand_l():
    keys = pick_up_keys(DAD, 1.0, (0.0, 0.0), BOOKS, (0.05, 0.3, 0.5), arm="arm_l")  # off a step
    assert track(keys, "prop:hymn_books", "parent") == [[1.4, "puppet:dad.hand_l"]]
    assert track(keys, "puppet:dad", "arm_l.upper.rot")[-1] == [1.8, 0.3]
    assert track(keys, "puppet:dad", "arm_l.lower.rot")[-1] == [1.8, 1.2]


# --------------------------------------------------------------------- sample: hand to

def test_hand_to_reproduces_sample():
    keys = hand_to_keys(DAD, HOLD, MOM, (0.0, 0.0), "hymn_books", 7.8)
    assert_well_formed(keys)
    assert track(keys, "puppet:dad", "arm_r.upper.rot") == [[7.8, 0.3], [8.3, 2.167], [8.7, 0.0]]
    assert track(keys, "puppet:dad", "arm_r.lower.rot") == [[7.8, 1.2], [8.3, 1.183], [8.7, 0.0]]
    assert track(keys, "puppet:mom", "arm_r.upper.rot") == [[7.8, 0.0], [8.3, -0.342], [8.7, 0.3]]
    assert track(keys, "puppet:mom", "arm_r.lower.rot") == [[7.8, 0.0], [8.3, 1.45], [8.7, 1.2]]
    assert track(keys, "prop:hymn_books", "parent") == [[8.3, "puppet:mom.hand_r"]]
    assert track(keys, "prop:hymn_books", "position") == [[8.3, [0.0, 0.0, 0.0]]]
    for who in ("dad", "mom"):
        for ch in ("arm_r.upper.rot", "arm_r.lower.rot"):
            assert track(keys, f"puppet:{who}", ch) == sample(f"puppet:{who}", ch, 7.8, 8.7)
    assert track(keys, "prop:hymn_books", "parent") == sample("prop:hymn_books", "parent", 8.3, 8.3)


def test_hand_to_hands_meet():
    keys = hand_to_keys(DAD, HOLD, MOM, (0.0, 0.0), "hymn_books", 7.8)
    t = 7.8 + PASS_SECONDS
    vals = {(tg, ch): v for tg, ch, tt, v in keys if tt == t}
    d = body_socket(DAD, "hand_r", {"arm_r.upper": vals[("puppet:dad", "arm_r.upper.rot")],
                                    "arm_r.lower": vals[("puppet:dad", "arm_r.lower.rot")]})
    m = body_socket(MOM, "hand_r", {"arm_r.upper": vals[("puppet:mom", "arm_r.upper.rot")],
                                    "arm_r.lower": vals[("puppet:mom", "arm_r.lower.rot")]})
    assert d == pytest.approx(m, abs=1e-9)
    assert max(tt for _, _, tt, _ in keys) == pytest.approx(7.8 + PASS_SECONDS + RETURN_SECONDS)


def test_hand_to_out_of_reach():
    far = Body("mom", (0.9, 0.0), -1, "average")
    with pytest.raises(OutOfReach) as info:
        hand_to_keys(DAD, HOLD, far, (0.0, 0.0), "hymn_books", 1.0)
    assert info.value.who == "mom"


def test_hand_to_left_arms():
    a = Body("a", (0.0, 0.0), 1, "average")
    b = Body("b", (0.2, 0.0), -1, "average")
    keys = hand_to_keys(a, HOLD, b, (0.0, 0.0), "ring", 2.0, giver_arm="arm_l", taker_arm="arm_l")
    assert track(keys, "prop:ring", "parent") == [[2.5, "puppet:b.hand_l"]]
    assert ("puppet:a", "arm_l.upper.rot") in channels(keys)
    assert ("puppet:b", "arm_r.upper.rot") not in channels(keys)


# ------------------------------------------------------------------------------ poses

def test_bow_head_moves_only_the_head():
    keys = pose_keys("mom", 8.9, {}, "bow_head")
    assert keys == [("puppet:mom", "head.rot", 8.9, 0.0), ("puppet:mom", "head.rot", 8.9 + POSE_SECONDS, -0.4)]
    assert track(keys, "puppet:mom", "head.rot") == [[8.9, 0.0], [9.3, -0.4]]
    assert track(keys, "puppet:mom", "head.rot") == sample("puppet:mom", "head.rot", 8.9, 9.3)


def test_pose_starts_from_current_and_ignores_root_offset():
    keys = pose_keys("dad", 1.0, {"arm_r.upper": 0.3, "torso": 0.1}, "sit", dur=0.5)
    assert {ch for _, ch, _, _ in keys} == {f"{b}.rot" for b in ("leg_l.upper", "leg_l.lower", "leg_r.upper", "leg_r.lower")}
    assert ("puppet:dad", "leg_l.upper.rot", 1.0, 0.0) in keys
    assert ("puppet:dad", "leg_l.upper.rot", 1.5, 1.5) in keys


def test_stand_pose_resets_current_bones():
    keys = pose_keys("dad", 2.0, {"arm_r.upper": 0.3, "head": -0.4}, "stand")
    assert sorted(keys) == sorted([
        ("puppet:dad", "arm_r.upper.rot", 2.0, 0.3), ("puppet:dad", "arm_r.upper.rot", 2.4, 0.0),
        ("puppet:dad", "head.rot", 2.0, -0.4), ("puppet:dad", "head.rot", 2.4, 0.0),
    ])


def test_unknown_pose():
    with pytest.raises(ValueError, match="unknown pose"):
        pose_keys("dad", 0.0, {}, "moonwalk")


def test_every_library_pose_compiles():
    for pose in POSES["poses"]:
        keys = pose_keys("dad", 0.0, {}, pose)
        bones = {b for b in POSES["poses"][pose] if b != "rootOffset"}
        assert {ch.removesuffix(".rot") for _, ch, _, _ in keys} == bones


# ---------------------------------------------------------------------- hold / state

def test_hold_keys():
    keys = hold_keys(DAD, 3.0, (0.0, 0.0))
    assert track(keys, "puppet:dad", "arm_r.upper.rot") == [[3.0, 0.0], [3.4, 0.3]]
    assert track(keys, "puppet:dad", "arm_r.lower.rot") == [[3.0, 0.0], [3.4, 1.2]]


def test_state_keys():
    assert state_keys("box", 2.5, "open") == [("prop:box", "state", 2.5, "open")]


# ----------------------------------------------------------------------------- put on

CAP = PropGeom("cap", 0.08, 0.12, (0.5, 1.0), (0.5, 0.5))


def test_put_on_self():
    keys = put_on_keys(DAD, 2.0, HOLD, CAP, DAD)
    assert_well_formed(keys)
    assert track(keys, "prop:cap", "parent") == [[2.4, "puppet:dad.head"]]
    assert track(keys, "prop:cap", "position") == [[2.4, [0.0, 0.0, 0.0]]]
    assert track(keys, "puppet:dad", "arm_r.upper.rot")[0] == [2.0, 0.3]
    assert track(keys, "puppet:dad", "arm_r.upper.rot")[-1] == [2.8, 0.0]
    # At the reach the cap's anchor sits on the head socket.
    vals = {ch: v for _, ch, t, v in keys if t == 2.0 + REACH_SECONDS and ch.endswith(".rot")}
    hand = body_socket(DAD, "hand_r", {"arm_r.upper": vals["arm_r.upper.rot"], "arm_r.lower": vals["arm_r.lower.rot"]})
    assert anchor_from_grip(CAP, hand) == pytest.approx(body_socket(DAD, "head", {}), abs=1e-9)


def test_put_on_someone_else():
    kid = Body("kid", (0.15, 0.0), -1, "small")
    keys = put_on_keys(DAD, 1.0, HOLD, CAP, kid)
    assert track(keys, "prop:cap", "parent") == [[1.4, "puppet:kid.head"]]


def test_put_on_out_of_reach():
    with pytest.raises(OutOfReach):
        put_on_keys(DAD, 1.0, HOLD, CAP, Body("kid", (0.8, 0.0), -1, "small"))


# ---------------------------------------------------------------------------- place on

def test_place_on_ground():
    dest = (0.1, 0.3, 0.5)  # on a step: a standing puppet cannot reach the floor
    keys = place_on_keys(DAD, 3.0, HOLD, BOOKS, dest, "ground")
    assert_well_formed(keys)
    assert track(keys, "prop:hymn_books", "parent") == [[3.4, "ground"]]
    assert track(keys, "prop:hymn_books", "position") == [[3.4, [0.1, 0.3, 0.5]]]
    assert track(keys, "puppet:dad", "arm_r.upper.rot")[0] == [3.0, 0.3]
    assert track(keys, "puppet:dad", "arm_r.upper.rot")[-1] == [3.8, 0.0]
    vals = {ch: v for _, ch, t, v in keys if t == 3.0 + REACH_SECONDS and ch.endswith(".rot")}
    hand = body_socket(DAD, "hand_r", {"arm_r.upper": vals["arm_r.upper.rot"], "arm_r.lower": vals["arm_r.lower.rot"]})
    assert hand == pytest.approx(grip_from_anchor(BOOKS, dest), abs=1e-9)


def test_place_on_socket():
    keys = place_on_keys(DAD, 3.0, HOLD, BOOKS, (0.15, 0.2, 0.5), "prop:table.top")
    assert track(keys, "prop:hymn_books", "parent") == [[3.4, "prop:table.top"]]
    assert track(keys, "prop:hymn_books", "position") == [[3.4, [0.0, 0.0, 0.0]]]


def test_place_on_rejects_bad_parent_and_far_targets():
    with pytest.raises(ValueError, match="parent"):
        place_on_keys(DAD, 3.0, HOLD, BOOKS, (0.1, 0.0, 0.5), "puppet:mom.hand_r")
    with pytest.raises(OutOfReach):
        place_on_keys(DAD, 3.0, HOLD, BOOKS, (0.9, 0.0, 0.5), "ground")


# ------------------------------------------------------------------------------ wave

def test_wave():
    keys = wave_keys(DAD, 1.0, {})
    up = track(keys, "puppet:dad", "arm_r.upper.rot")
    lo = track(keys, "puppet:dad", "arm_r.lower.rot")
    assert up == [[1.0, 0.0], [1.4, 2.6], [1.8, 2.6], [2.2, 0.0]]
    assert lo[0] == [1.0, 0.0] and lo[-1] == [2.2, 0.0]
    swings = [v for t, v in lo if 1.4 < t < 1.8]
    assert swings == [0.7, 0.1, 0.7, 0.1]  # 0.4 +- 0.3, two oscillations
    assert not any(ch == "root.facing" for _, ch, _, _ in keys)


def test_wave_returns_to_current_and_turns():
    keys = wave_keys(MOM, 0.0, {"arm_l.upper": 0.2}, dur=1.5, arm="arm_l", toward_x=0.9)
    assert ("puppet:mom", "root.facing", 0.0, 1) in keys
    up = track(keys, "puppet:mom", "arm_l.upper.rot")
    assert up[0] == [0.0, 0.2] and up[-1] == [1.5, 0.2]
    assert ("puppet:mom", "arm_r.upper.rot") not in channels(keys)


def test_short_gesture_scales_its_ramps():
    keys = wave_keys(DAD, 0.0, {}, dur=0.6)
    times = sorted(t for _, _, t, _ in keys)
    assert times[0] == 0.0 and times[-1] == pytest.approx(0.6)
    with pytest.raises(ValueError):
        wave_keys(DAD, 0.0, {}, dur=0.0)


# ----------------------------------------------------------------------------- point

def test_point_ahead_aims_without_turning():
    target = (0.6, 0.418)  # level with dad's shoulder, ahead of him
    keys = point_keys(DAD, 2.0, {}, target)
    assert not any(ch == "root.facing" for _, ch, _, _ in keys)
    up = track(keys, "puppet:dad", "arm_r.upper.rot")
    assert up[0] == [2.0, 0.0] and up[-1] == [3.0, 0.0]
    assert up[1][1] == pytest.approx(math.pi / 2, abs=1e-3)
    assert track(keys, "puppet:dad", "arm_r.lower.rot")[1] == [2.333, 0.1]  # ramp = dur / 3


def test_point_behind_turns_first():
    keys = point_keys(DAD, 2.0, {}, (-0.8, 1.0))
    assert ("puppet:dad", "root.facing", 2.0, -1) in keys
    up = track(keys, "puppet:dad", "arm_r.upper.rot")
    assert math.pi / 2 < up[1][1] < math.pi  # aimed up and forward after turning


# ------------------------------------------------------------- kneel / sit / stand

def test_kneel_bakes_root_offset():
    keys = kneel_keys(DAD, 1.0, {})
    s = scale("tall")
    assert track(keys, "puppet:dad", "root.position") == [[1.0, [0.0, 0.0, 0.5]], [1.4, r3([0.0, -0.24 * s, 0.5])]]
    assert track(keys, "puppet:dad", "leg_l.upper.rot") == [[1.0, 0.0], [1.4, 1.4]]
    assert_well_formed(keys)


def test_sit_on_ground_and_on_seat():
    s = scale("average")
    on_ground = sit_keys(MOM, 0.0, {}, None)
    assert track(on_ground, "puppet:mom", "root.position")[-1] == [0.4, r3([0.2, 0.3 - 0.24 * s, 0.5])]
    on_seat = sit_keys(MOM, 0.0, {}, (0.4, 0.25), z=0.6)
    assert track(on_seat, "puppet:mom", "root.position") == [
        [0.0, [0.2, 0.3, 0.6]], [0.4, r3([0.4, 0.25 - RIG["hipHeight"] * s, 0.6])]]
    # the hips land on the seat
    end = [v for _, ch, t, v in on_seat if ch == "root.position" and t == 0.4][0]
    assert end[1] + RIG["hipHeight"] * s == pytest.approx(0.25)
    assert track(on_seat, "puppet:mom", "leg_r.lower.rot") == [[0.0, 0.0], [0.4, -1.5]]


def test_stand_resets_bones_and_root():
    seated = Body("mom", (0.4, 0.01), -1, "average")
    current = {"leg_l.upper": 1.5, "leg_l.lower": -1.5, "head": -0.4}
    keys = stand_keys(seated, 5.0, current, (0.4, 0.0))
    assert track(keys, "puppet:mom", "root.position") == [[5.0, [0.4, 0.01, 0.5]], [5.4, [0.4, 0.0, 0.5]]]
    final = last_values(keys)
    assert all(final[("puppet:mom", f"{b}.rot")] == 0.0 for b in current)


# ------------------------------------------------------------------------------- hug

def test_hug_faces_each_other_and_releases():
    a = Body("a", (0.0, 0.0), -1, "average")   # facing away from b
    b = Body("b", (0.2, 0.0), -1, "average")   # already facing a
    keys = hug_keys(a, b, 3.0, {}, {"torso": 0.05})
    assert ("puppet:a", "root.facing", 3.0, 1) in keys
    assert not any(tg == "puppet:b" and ch == "root.facing" for tg, ch, _, _ in keys)
    for who, start in (("a", 0.0), ("b", 0.05)):
        torso = track(keys, f"puppet:{who}", "torso.rot")
        assert torso == [[3.0, start], [3.4, -0.1], [4.1, -0.1], [4.5, start]]
        assert track(keys, f"puppet:{who}", "arm_l.upper.rot")[1] == [3.4, 1.3]


# ------------------------------------------------------------------------------ lean

def test_lean_toward():
    keys = lean_keys(MOM, 2.0, {}, toward_x=-0.5)
    assert not any(ch == "root.facing" for _, ch, _, _ in keys)
    assert track(keys, "puppet:mom", "torso.rot") == [[2.0, 0.0], [2.333, -0.2], [2.667, -0.2], [3.0, 0.0]]
    turned = lean_keys(MOM, 2.0, {}, toward_x=0.9, dur=2.0)
    assert ("puppet:mom", "root.facing", 2.0, 1) in turned
    assert track(turned, "puppet:mom", "torso.rot") == [[2.0, 0.0], [2.4, -0.2], [3.6, -0.2], [4.0, 0.0]]


# ------------------------------------------------------------------------- helpers

def test_last_values():
    keys = [("puppet:a", "head.rot", 2.0, -0.4), ("puppet:a", "head.rot", 1.0, 0.0), ("prop:b", "state", 0.5, "open")]
    assert last_values(keys) == {("puppet:a", "head.rot"): -0.4, ("prop:b", "state"): "open"}


def test_generators_are_deterministic():
    a = hand_to_keys(DAD, HOLD, MOM, (0.0, 0.0), "hymn_books", 7.8)
    b = hand_to_keys(DAD, HOLD, MOM, (0.0, 0.0), "hymn_books", 7.8)
    assert a == b
    assert FALL_SECONDS == 0.3 and REACH_SECONDS == 0.4
