import json
import math
from pathlib import Path

import pytest

from screenplay import motion
from screenplay.motion import (
    LIMBS,
    enter_keys,
    exit_keys,
    follow_keys,
    locomotion_keys,
    mark_position,
    nearest_offstage,
    puppet_scale,
    resolve_marks,
    turn_keys,
)

REPO_DIR = Path(__file__).resolve().parents[3]
SAMPLE = json.loads((REPO_DIR / "web/public/samples/hymn_books.timeline.json").read_text())


def r(v):
    if isinstance(v, (list, tuple)):
        return [round(x, 3) for x in v]
    if isinstance(v, float):
        return round(v, 3)
    return v


def as_tracks(keys):
    out: dict[tuple[str, str], list] = {}
    for target, channel, t, v in keys:
        out.setdefault((target, channel), []).append([round(t, 3), r(v)])
    return out


def sample_track(target, channel):
    return next(tr["keys"] for tr in SAMPLE["tracks"] if tr["target"] == target and tr["channel"] == channel)


def channels(keys):
    return {k[1] for k in keys}


# ----------------------------------------------------------------------------- marks

def test_mark_position_two_and_three():
    assert mark_position([0.6, 0.3]) == pytest.approx((0.2, 0.3, 0.5))
    assert mark_position([0.5, 0, 0.7])[2] == 0.7
    assert mark_position([0.5, 0, 0.7]) == (0.0, 0.0, 0.7)


@pytest.mark.parametrize("bad", [[], [0.5], [0.1, 0.2, 0.3, 0.4]])
def test_mark_position_bad_length(bad):
    with pytest.raises(ValueError):
        mark_position(bad)


def test_resolve_marks_builtins_and_overrides():
    m = resolve_marks({"door": [0.9, 0.0], "steps": [0.6, 0.3]}, {"steps": [0.7, 0.2, 0.8]})
    assert m["offstage_left"] == pytest.approx((-1.3, 0.0, 0.5))
    assert m["offstage_right"] == pytest.approx((1.3, 0.0, 0.5))
    assert m["door"] == pytest.approx((0.8, 0.0, 0.5))
    assert m["steps"] == pytest.approx((0.4, 0.2, 0.8))


def test_resolve_marks_set_overrides_builtin_and_none_ok():
    m = resolve_marks({"offstage_left": [0.0, 0.0]}, None)
    assert m["offstage_left"] == (-1.0, 0.0, 0.5)
    assert set(resolve_marks(None, None)) == {"offstage_left", "offstage_right"}


def test_nearest_offstage_and_scale():
    assert nearest_offstage(-0.2) == "offstage_left"
    assert nearest_offstage(0.0) == "offstage_left"
    assert nearest_offstage(0.01) == "offstage_right"
    assert puppet_scale("tall") == pytest.approx(0.55)
    assert puppet_scale("average") == pytest.approx(0.5)


# ------------------------------------------------------------------------ sample walk

def test_reproduces_sample_dad_walks():
    k1, a1, f1 = locomotion_keys("dad", "tall", 1.2, (-1.3, 0, 0.5), (-0.6, 0, 0.5), 1)
    k2, a2, f2 = locomotion_keys("dad", "tall", 4.8, (-0.6, 0, 0.5), (0, 0, 0.5), 1)
    assert round(a1, 3) == 3.2 and round(a2, 3) == 6.514
    assert f1 == f2 == 1
    keys = k1 + k2
    assert "root.facing" not in channels(keys)
    got = as_tracks(keys)

    assert got[("puppet:dad", "root.position")] == sample_track("puppet:dad", "root.position")[1:]
    for bone in LIMBS:
        want = [k for k in sample_track("puppet:dad", f"{bone}.rot") if k[0] <= 6.514]
        assert got[("puppet:dad", f"{bone}.rot")] == want, bone
    assert set(got) == {("puppet:dad", "root.position")} | {("puppet:dad", f"{b}.rot") for b in LIMBS}


def test_positions_are_lists():
    keys, _, _ = locomotion_keys("dad", "tall", 0, (0, 0, 0.5), (0.5, 0, 0.5), 1)
    pos = [k for k in keys if k[1] == "root.position"]
    assert all(isinstance(k[3], list) and len(k[3]) == 3 for k in pos)


# ---------------------------------------------------------------- locomotion variants

def test_walk_left_flips_facing_at_t0():
    keys, t1, facing = locomotion_keys("mom", "average", 2.0, (0.5, 0, 0.5), (-0.2, 0, 0.5), 1)
    assert facing == -1
    fk = [k for k in keys if k[1] == "root.facing"]
    assert fk == [("puppet:mom", "root.facing", 2.0, -1)]
    assert t1 == pytest.approx(2.0 + 0.7 / 0.35)


def test_walk_left_already_facing_left_no_facing_key():
    keys, _, facing = locomotion_keys("mom", "average", 0, (0.5, 0, 0.5), (-0.2, 0, 0.5), -1)
    assert facing == -1 and "root.facing" not in channels(keys)


def test_pure_depth_move_keeps_facing():
    keys, t1, facing = locomotion_keys("mom", "average", 0, (0, 0, 0.5), (0, 0, 0.9), -1)
    assert facing == -1 and "root.facing" not in channels(keys)
    assert t1 == pytest.approx(0.4 / 0.35)


def test_skip_bones():
    keys, _, _ = locomotion_keys("dad", "tall", 0, (0, 0, 0.5), (1, 0, 0.5), 1,
                                 skip_bones=("arm_r.upper", "arm_r.lower"))
    ch = channels(keys)
    assert "arm_r.upper.rot" not in ch and "arm_r.lower.rot" not in ch
    assert {"arm_l.upper.rot", "leg_l.upper.rot", "leg_r.lower.rot"} <= ch


def test_run_uses_run_speed_and_torso():
    keys, t1, _ = locomotion_keys("dad", "average", 1.0, (0, 0, 0.5), (0.8, 0, 0.5), 1, gait="run")
    assert t1 == pytest.approx(2.0)
    torso = [k for k in keys if k[1] == "torso.rot"]
    assert torso[0][2:] == (1.0, 0.0) and torso[-1][2] == pytest.approx(2.0) and torso[-1][3] == 0.0
    assert any(k[3] == -0.15 for k in torso)
    # n = round(0.8 / (1.3 * 0.5) * 4) = 5 -> 6 keys per bone
    assert len(torso) == 6
    walk_keys, _, _ = locomotion_keys("dad", "average", 1.0, (0, 0, 0.5), (0.8, 0, 0.5), 1)
    assert "torso.rot" not in channels(walk_keys)


def test_speed_scale():
    _, t1, _ = locomotion_keys("dad", "average", 0, (0, 0, 0.5), (0.35, 0, 0.5), 1, speed_scale=0.5)
    assert t1 == pytest.approx(2.0)
    with pytest.raises(ValueError):
        locomotion_keys("dad", "average", 0, (0, 0, 0.5), (0.35, 0, 0.5), 1, speed_scale=0)


def test_short_move_has_at_least_two_keys_per_bone():
    keys, _, _ = locomotion_keys("dad", "average", 0, (0, 0, 0.5), (0.01, 0, 0.5), 1)
    leg = [k for k in keys if k[1] == "leg_l.upper.rot"]
    assert [k[3] for k in leg] == [0.0, 0.0]


def test_zero_distance():
    assert locomotion_keys("dad", "tall", 3.0, (0.2, 0, 0.5), (0.2, 0, 0.5), 1) == ([], 3.0, 1)


def test_cycle_keys_evenly_spaced_and_within_move():
    keys, t1, _ = locomotion_keys("dad", "tall", 0.0, (-1, 0, 0.5), (1, 0, 0.5), 1)
    ts = [k[2] for k in keys if k[1] == "leg_l.upper.rot"]
    assert ts[0] == 0.0 and ts[-1] == pytest.approx(t1)
    gaps = [b - a for a, b in zip(ts, ts[1:])]
    assert max(gaps) - min(gaps) < 1e-9


def test_deterministic():
    a = locomotion_keys("dad", "tall", 0.3, (-1, 0, 0.5), (0.7, 0.1, 0.6), 1, gait="run")
    b = locomotion_keys("dad", "tall", 0.3, (-1, 0, 0.5), (0.7, 0.1, 0.6), 1, gait="run")
    assert a == b


# --------------------------------------------------------------------- enter / exit / turn

def test_enter_places_then_walks():
    keys, t1, facing = enter_keys("dad", "tall", 1.2, (-1.3, 0, 0.5), (-0.6, 0, 0.5), 1)
    assert keys[0] == ("puppet:dad", "root.position", 1.2, [-1.3, 0, 0.5])
    pos = [k for k in keys if k[1] == "root.position"]
    assert len(pos) == 2  # the placement key is not duplicated
    assert round(t1, 3) == 3.2 and facing == 1


def test_enter_zero_distance_still_places():
    keys, t1, facing = enter_keys("dad", "tall", 1.0, (0.2, 0, 0.5), (0.2, 0, 0.5), -1)
    assert keys == [("puppet:dad", "root.position", 1.0, [0.2, 0, 0.5])]
    assert t1 == 1.0 and facing == -1


def test_enter_from_right_faces_left():
    keys, _, facing = enter_keys("mom", "average", 0, (1.3, 0, 0.5), (0.4, 0, 0.5), 1)
    assert facing == -1
    assert ("puppet:mom", "root.facing", 0, -1) in keys


def test_exit_walks_offstage():
    keys, t1, facing = exit_keys("mom", "average", 2.0, (0.4, 0, 0.5), (1.3, 0, 0.5), -1)
    assert facing == 1
    pos = [k for k in keys if k[1] == "root.position"]
    assert pos[-1][3] == [1.3, 0, 0.5] and pos[-1][2] == pytest.approx(t1)
    assert t1 == pytest.approx(2.0 + 0.9 / 0.35)


def test_turn_keys():
    assert turn_keys("dad", 2.0, 0.0, 0.5, 1) == ([], 1)
    assert turn_keys("dad", 2.0, 0.0, -0.5, 1) == ([("puppet:dad", "root.facing", 2.0, -1)], -1)
    assert turn_keys("dad", 2.0, 0.3, 0.3, -1) == ([], -1)


# ----------------------------------------------------------------------------- follow

def interp(path, t):
    if t <= path[0][0]:
        return path[0][1][0]
    for (ta, pa), (tb, pb) in zip(path, path[1:]):
        if ta <= t <= tb:
            return pa[0] if tb == ta else pa[0] + (pb[0] - pa[0]) * (t - ta) / (tb - ta)
    return path[-1][1][0]


def follower_path(keys):
    return [(k[2], tuple(k[3])) for k in keys if k[1] == "root.position"]


def leader_path(t0, start, waypoints, who="mom", build="average"):
    """Build a leader path with locomotion_keys so the follower test uses real leader speeds."""
    path = [(t0, start)]
    t, cur = t0, start
    for hold, nxt in waypoints:
        t += hold
        keys, t, _ = locomotion_keys(who, build, t, cur, nxt, 1)
        path += [(k[2], tuple(k[3])) for k in keys if k[1] == "root.position"]
        cur = nxt
    return path


def test_follow_never_overtakes_and_keeps_spacing():
    leader = leader_path(1.0, (-0.8, 0, 0.5), [(0.0, (-0.2, 0, 0.5)), (0.8, (0.6, 0, 0.5))])
    keys, t_end, facing = follow_keys("dad", "tall", 1.0, (-1.3, 0, 0.5), 1, leader, None)
    fpath = follower_path(keys)
    assert fpath, "follower should move"
    assert fpath[-1][1][0] == pytest.approx(0.6 - 0.2)
    assert t_end == pytest.approx(fpath[-1][0])
    assert facing == 1
    end = max(t_end, leader[-1][0])
    steps = 400
    for i in range(steps + 1):
        t = 1.0 + (end - 1.0) * i / steps
        assert interp(fpath, t) <= interp(leader, t) + 1e-9, t
    # follower starts lag after leader moves on later segments
    assert all(k[1] != "root.position" or k[2] >= 1.0 for k in keys)


def test_follow_slow_leader_does_not_get_passed():
    # leader walks at a quarter speed; the follower must slow down too
    leader = [(0.0, (-0.5, 0.0, 0.5)), (8.0, (0.2, 0.0, 0.5))]
    keys, _, _ = follow_keys("dad", "tall", 0.0, (-0.7, 0, 0.5), 1, leader, None)
    fpath = follower_path(keys)
    for i in range(201):
        t = 9.0 * i / 200
        assert interp(fpath, t) <= interp(leader, t) + 1e-9, t
    assert fpath[-1][1][0] == pytest.approx(0.0)


def test_follow_leftward_spacing_on_right_side():
    leader = [(0.0, (0.5, 0, 0.5)), (2.0, (-0.2, 0, 0.5))]
    keys, _, facing = follow_keys("dad", "tall", 0.0, (0.9, 0, 0.5), -1, leader, None)
    fpath = follower_path(keys)
    assert fpath[-1][1][0] == pytest.approx(0.0)
    assert facing == -1
    for i in range(101):
        t = 4.0 * i / 100
        assert interp(fpath, t) >= interp(leader, t) - 1e-9


def test_follow_dur_clips():
    leader = leader_path(0.0, (-0.8, 0, 0.5), [(0.0, (0.8, 0, 0.5))])
    keys, t_end, _ = follow_keys("dad", "tall", 0.0, (-1.3, 0, 0.5), 1, leader, 2.0)
    assert t_end == pytest.approx(2.0)
    assert max(k[2] for k in keys) <= 2.0 + 1e-9
    fpath = follower_path(keys)
    assert fpath[-1][1][0] < 0.8 - 0.2


def test_follow_stationary_leader_walks_up_to_them():
    keys, t_end, facing = follow_keys("dad", "tall", 1.0, (-1.0, 0, 0.5), 1, [(0.0, (0.3, 0, 0.5))], None)
    fpath = follower_path(keys)
    assert fpath[0][0] == 1.0
    assert fpath[-1][1] == pytest.approx((0.1, 0, 0.5))
    assert t_end == pytest.approx(1.0 + 1.1 / 0.35)


def test_follow_nothing_to_do():
    assert follow_keys("dad", "tall", 1.0, (0.1, 0, 0.5), 1, [(0.0, (0.3, 0, 0.5))], None) == ([], 1.0, 1)
    assert follow_keys("dad", "tall", 1.0, (0.1, 0, 0.5), 1, [], None) == ([], 1.0, 1)
    assert follow_keys("dad", "tall", 1.0, (-1, 0, 0.5), 1, [(0.0, (0.3, 0, 0.5))], 0) == ([], 1.0, 1)


def moving_dir(path, t):
    """Sign of the follower's x travel at time t (0 when standing)."""
    for (ta, pa), (tb, pb) in zip(path, path[1:]):
        if ta < t < tb:
            return (pb[0] > pa[0] + 1e-12) - (pb[0] < pa[0] - 1e-12)
    return 0


def assert_never_walks_into_leader(fpath, leader, t_lo, t_hi, steps=800):
    """While the follower walks, the leader is level with or ahead of it in its direction."""
    for i in range(steps + 1):
        t = t_lo + (t_hi - t_lo) * i / steps
        m = moving_dir(fpath, t)
        if m:
            assert (interp(leader, t) - interp(fpath, t)) * m >= -1e-9, t


def test_follow_from_leaders_spot_waits_and_never_backs_up():
    # leader stands until t=1, then walks right; the follower starts on the leader's spot
    leader = [(0.0, (-0.5, 0, 0.5)), (1.0, (-0.5, 0, 0.5)), (3.0, (0.2, 0, 0.5))]
    keys, t_end, facing = follow_keys("mom", "average", 0.0, (-0.5, 0, 0.5), 1, leader, None)
    assert "root.facing" not in channels(keys)
    assert facing == 1
    fpath = follower_path(keys)
    assert fpath[0] == (pytest.approx(1.0 + 0.2 / 0.35), (-0.5, 0, 0.5))  # leader 0.2 ahead
    assert fpath[0][0] >= 1.0 + 0.5  # and not before lag after the leader set off
    xs = [p[0] for _, p in fpath]
    assert xs == sorted(xs)
    assert fpath[-1][1][0] == pytest.approx(0.0)
    assert t_end >= 3.0 + 0.5 - 1e-9
    assert_never_walks_into_leader(fpath, leader, 0.0, t_end)


def test_follow_both_enter_from_same_side():
    leader = leader_path(1.2, (-1.3, 0, 0.5), [(0.0, (-0.6, 0, 0.5))])
    keys, _, facing = follow_keys("mom", "average", 1.2, (-1.3, 0, 0.5), 1, leader, None)
    assert "root.facing" not in channels(keys) and facing == 1
    fpath = follower_path(keys)
    assert fpath[0][0] >= 1.2 + 0.5
    assert fpath[-1][1][0] == pytest.approx(-0.8)
    assert_never_walks_into_leader(fpath, leader, 1.2, fpath[-1][0])


def test_follow_starting_ahead_does_not_walk_through_leader():
    # the leader walks right but stops short of the follower: the follower holds
    leader = [(0.0, (-0.5, 0, 0.5)), (2.0, (0.2, 0, 0.5))]
    assert follow_keys("mom", "average", 0.0, (0.5, 0, 0.5), 1, leader, None) == ([], 0.0, 1)


def test_follow_starting_ahead_waits_for_leader_to_pass():
    leader = [(0.0, (-0.5, 0, 0.5)), (4.0, (0.6, 0, 0.5))]
    keys, _, facing = follow_keys("mom", "average", 0.0, (0.0, 0, 0.5), 1, leader, None)
    fpath = follower_path(keys)
    assert fpath[0][1][0] == 0.0
    assert fpath[0][0] == pytest.approx(4.0 * 0.7 / 1.1)  # leader reaches 0.2
    assert fpath[-1][1][0] == pytest.approx(0.4) and facing == 1
    assert_never_walks_into_leader(fpath, leader, 0.0, fpath[-1][0])


def test_follow_leader_reverses():
    leader = [(0.0, (-0.5, 0, 0.5)), (2.0, (0.2, 0, 0.5)), (4.0, (-0.6, 0, 0.5))]
    keys, t_end, facing = follow_keys("mom", "average", 0.0, (-0.7, 0, 0.5), 1, leader, None)
    fpath = follower_path(keys)
    assert_never_walks_into_leader(fpath, leader, 0.0, t_end)
    assert fpath[-1][1][0] == pytest.approx(-0.4)
    assert facing == -1
    assert [k[3] for k in keys if k[1] == "root.facing"] == [-1]


def test_follow_leader_reverses_while_follower_walking():
    # the leader turns back right away; the follower stops when the leader sets off again
    leader = [(0.0, (-0.5, 0, 0.5)), (2.0, (0.2, 0, 0.5)), (2.0, (0.2, 0, 0.5)), (4.0, (-0.9, 0, 0.5))]
    keys, t_end, _ = follow_keys("mom", "average", 0.0, (-1.2, 0, 0.5), 1, leader, None)
    fpath = follower_path(keys)
    assert_never_walks_into_leader(fpath, leader, 0.0, t_end)
    first_stop = next(p for t, p in fpath if t == pytest.approx(2.0))
    assert first_stop[0] < 0.0


def test_follow_reversal_probe_sweep():
    # a spread of starts and reversing leaders; the invariant must hold for all of them
    for fx in (-1.2, -0.7, -0.3, 0.0, 0.3, 0.9):
        for back in (-0.6, 0.0, 0.5):
            leader = [(0.0, (-0.5, 0, 0.5)), (2.0, (0.2, 0, 0.5)), (2.5, (0.2, 0, 0.5)), (4.5, (back, 0, 0.5))]
            keys, t_end, _ = follow_keys("mom", "average", 0.0, (fx, 0, 0.5), 1, leader, None)
            fpath = follower_path(keys)
            if fpath:
                assert_never_walks_into_leader(fpath, leader, 0.0, t_end)


def test_height_only_move_reaches_new_height():
    keys, t1, facing = locomotion_keys("dad", "tall", 1.0, (0, 0, 0.5), (0, 0.3, 0.5), -1)
    assert facing == -1
    assert t1 == pytest.approx(1.0 + 0.3 / 0.35)
    assert keys == [("puppet:dad", "root.position", 1.0, [0, 0, 0.5]),
                    ("puppet:dad", "root.position", t1, [0, 0.3, 0.5])]


def test_data_loaded():
    assert motion.SPEEDS["run"] == 0.8 and motion.ACTING_Z == 0.5
    assert "walk" in motion.POSES["cycles"] and "bones" in motion.RIG
    assert math.isclose(motion.SPEEDS["walk"], 0.35)


# ------------------------------------------------- hymn_books walk = generator arithmetic

SCREENPLAY = json.loads((REPO_DIR / "web/public/samples/hymn_books.screenplay.json").read_text())
RISE = 1.2  # gen_hymn_books_sample.RISE: screenplay t=0 is timeline t=1.2


@pytest.mark.parametrize("t_shot,frm,to", [(0.0, "offstage_left", "steps_left"), (3.6, "steps_left", "steps_foot")])
def test_hymn_books_walk_timing_matches_generator_arithmetic(t_shot, frm, to):
    shot = SCREENPLAY["shots"][0]
    marks = resolve_marks(shot["marks"], None)
    x0, x1 = marks[frm][0], marks[to][0]
    # gen_hymn_books_sample.walk(): t1 = t0 + |x1 - x0| / WALK, n = max(1, round(dist / stride * 4))
    # and keys at A(t0 + (t1 - t0) * i / n), all in shot time then shifted by RISE.
    dist = abs(x1 - x0)
    shot_t1 = t_shot + dist / motion.VOCABULARY["speeds"]["walk"]
    stride = motion.POSES["cycles"]["walk"]["strideLength"] * motion.RIG["stageHeight"] * motion.BUILDS["tall"]
    n = max(1, round(dist / stride * 4))
    keys, t1, _ = locomotion_keys("dad", "tall", RISE + t_shot, marks[frm], marks[to], 1)
    assert t1 == pytest.approx(RISE + shot_t1, abs=1e-12)
    leg = [k for k in keys if k[1] == "leg_l.upper.rot"]
    assert len(leg) == n + 1
    want = [RISE + (t_shot + (shot_t1 - t_shot) * i / n) for i in range(n + 1)]
    assert [k[2] for k in leg] == pytest.approx(want, abs=1e-12)
    pos = [k for k in keys if k[1] == "root.position"]
    assert [p[3][0] for p in pos] == [x0, x1]


# --------------------------------------------------------------------------- vehicles

CAR = {
    "id": "green_sedan_1985", "name": "green sedan", "size": "vehicle", "aspect": 2.0,
    "art": {"body": None, "wheel": None}, "anchor": [0.5, 1.0], "grip": None,
    "sockets": {"driver": [0.35, 0.55], "passenger": [0.6, 0.55]},
    "parts": {"wheel_front": {"art": "wheel", "at": [0.22, 0.9], "spin": True},
              "wheel_back": {"art": "wheel", "at": [0.8, 0.9], "spin": True},
              "door": {"art": "door", "at": [0.4, 0.5], "spin": False}},
    "states": {"parked": {}, "driving": {}},
}


def test_drive_speed_from_vocabulary():
    assert motion.DRIVE_SPEED == motion.VOCABULARY["speeds"]["drive"] == 0.9
    assert motion.DEFAULT_DRIVE_SPEED == 0.9


def test_drive_keys_path_and_timing():
    keys, t1 = motion.drive_keys("car", 2.0, (-0.9, 0.0, 0.5), (0.9, 0.0, 0.5))
    assert t1 == pytest.approx(2.0 + 1.8 / 0.9)
    assert keys == [("prop:car", "position", 2.0, [-0.9, 0.0, 0.5]),
                    ("prop:car", "position", t1, [0.9, 0.0, 0.5])]
    _, t_fast = motion.drive_keys("car", 2.0, (-0.9, 0.0, 0.5), (0.9, 0.0, 0.5), speed=1.8)
    assert t_fast == pytest.approx(3.0)


def test_drive_keys_degenerate_and_bad_speed():
    assert motion.drive_keys("car", 1.0, (0.2, 0, 0.5), (0.2, 0, 0.5)) == ([], 1.0)
    keys, t1 = motion.drive_keys("car", 1.0, (0.2, 0, 0.5), (0.2, 0.09, 0.5))
    assert t1 == pytest.approx(1.1) and keys[-1][3] == [0.2, 0.09, 0.5]
    with pytest.raises(ValueError):
        motion.drive_keys("car", 0, (0, 0, 0.5), (1, 0, 0.5), speed=0)


def test_drive_to_mark_uses_mark_position():
    marks = resolve_marks({"garage": [0.1, 0.0], "curb": [0.9, 0.0]}, None)
    keys, t1 = motion.drive_keys("car", 0.0, marks["garage"], marks["curb"])
    assert keys[-1][3] == pytest.approx([0.8, 0.0, 0.5])
    assert t1 == pytest.approx(1.6 / 0.9)


@pytest.mark.parametrize("x0,x1", [(-0.9, 0.9), (0.9, -0.9), (-1.3, 0.25), (0.6, -0.05)])
@pytest.mark.parametrize("radius", [0.05, 0.11, 0.3])
def test_wheel_spin_final_rotation_is_minus_d_over_r(x0, x1, radius):
    t0 = 1.7
    keys, t1 = motion.drive_keys("car", t0, (x0, 0.0, 0.5), (x1, 0.0, 0.5))
    path = motion.path_from_keys(keys, "prop:car")
    spin = motion.wheel_spin_keys("car", CAR["parts"], path, radius)
    d = abs(x1 - x0)
    want = -d / radius if x1 > x0 else d / radius
    assert {k[1] for k in spin} == {"part.wheel_front.rot", "part.wheel_back.rot"}  # not the door
    for ch in ("part.wheel_front.rot", "part.wheel_back.rot"):
        track = [k for k in spin if k[1] == ch]
        assert all(k[0] == "prop:car" for k in track)
        assert track[0][2] == t0 and track[0][3] == 0.0
        assert track[-1][2] == pytest.approx(t1)
        assert abs(track[-1][3] - want) <= 1e-9
    ts = [k[2] for k in spin]
    assert ts == sorted(ts) and ts[0] == t0


def test_wheel_spin_accumulates_over_path_with_holds_and_start_rot():
    path = [(0.0, (-1.0, 0, 0.5)), (2.0, (0.0, 0, 0.5)), (3.0, (0.0, 0, 0.5)), (4.0, (-0.5, 0, 0.5))]
    spin = motion.wheel_spin_keys("car", {"w": {"spin": True}}, path, 0.1, start_rot=0.5)
    assert [k[2] for k in spin] == [0.0, 2.0, 3.0, 4.0]
    assert [k[3] for k in spin] == pytest.approx([0.5, 0.5 - 10.0, 0.5 - 10.0, 0.5 - 10.0 + 5.0])


def test_wheel_spin_depth_move_rolls_in_last_direction():
    path = [(0.0, (0.0, 0, 0.5)), (1.0, (-0.3, 0, 0.5)), (2.0, (-0.3, 0, 0.9))]
    spin = motion.wheel_spin_keys("car", {"w": {"spin": True}}, path, 0.1)
    assert spin[-1][3] == pytest.approx(3.0 + 4.0)
    first_depth = motion.wheel_spin_keys("car", {"w": {"spin": True}}, path[1:], 0.1, heading=-1)
    assert first_depth[-1][3] == pytest.approx(4.0)


def test_wheel_spin_with_schema_models_and_edge_cases():
    from schemas import Prop

    car = Prop.model_validate(CAR)
    assert motion.spinning_parts(car.parts) == ["wheel_back", "wheel_front"]
    path = [(0.0, (0.0, 0, 0.5)), (1.0, (0.4, 0, 0.5))]
    assert len(motion.wheel_spin_keys("car", car.parts, path, 0.1)) == 4
    assert motion.wheel_spin_keys("car", {}, path, 0.1) == []
    assert motion.wheel_spin_keys("car", None, path, 0.1) == []
    assert motion.wheel_spin_keys("car", car.parts, [], 0.1) == []
    with pytest.raises(ValueError):
        motion.wheel_spin_keys("car", car.parts, path, 0.0)
    with pytest.raises(ValueError):
        motion.wheel_spin_keys("car", car.parts, path, 0.1, heading=0)


def test_wheel_rotation_sign_convention():
    assert motion.wheel_rotation(1.0, 0.25, 1) == -4.0
    assert motion.wheel_rotation(1.0, 0.25, -1) == 4.0
    with pytest.raises(ValueError):
        motion.wheel_rotation(1.0, -0.1, 1)


def test_prop_dimensions_and_socket_offset():
    assert motion.prop_dimensions("vehicle", 2.0) == pytest.approx((1.8, 0.9))
    off = motion.socket_offset([0.35, 0.55], [0.5, 1.0], "vehicle", 2.0)
    assert off == pytest.approx(((0.35 - 0.5) * 1.8, (1.0 - 0.55) * 0.9, 0.0))
    assert motion.prop_socket_offset(CAR, "driver") == pytest.approx(off)
    with pytest.raises(KeyError):
        motion.prop_socket_offset(CAR, "trunk")
    with pytest.raises(ValueError):
        motion.prop_dimensions("planet")


def test_socket_follows_vehicle_through_drive():
    from schemas import Prop

    t0, start, end = 1.0, (-0.8, 0.0, 0.5), (0.7, 0.0, 0.5)
    keys, t1 = motion.drive_keys("car", t0, start, end)
    path = motion.path_from_keys(keys, "prop:car")
    off = motion.prop_socket_offset(CAR, "driver")
    tm = (t0 + t1) / 2
    for car in (CAR, Prop.model_validate(CAR)):
        for t, anchor in ((t0, start), (tm, (-0.05, 0.0, 0.5)), (t1, end)):
            got = motion.vehicle_socket_position(car, "driver", path, t)
            assert got == pytest.approx((anchor[0] + off[0], anchor[1] + off[1], anchor[2] + off[2]), abs=1e-12)
    # held before and after the drive
    assert motion.vehicle_socket_position(CAR, "driver", path, 0.0) == pytest.approx(
        motion.vehicle_socket_position(CAR, "driver", path, t0))
    assert motion.vehicle_socket_position(CAR, "driver", path, t1 + 5) == pytest.approx(
        motion.vehicle_socket_position(CAR, "driver", path, t1))
    # passenger and driver keep their spacing all the way
    for t in (t0, tm, t1):
        p = motion.vehicle_socket_position(CAR, "passenger", path, t)
        d = motion.vehicle_socket_position(CAR, "driver", path, t)
        assert p[0] - d[0] == pytest.approx(0.25 * 1.8)


def test_seated_root_keys_track_the_socket():
    keys, t1 = motion.drive_keys("car", 0.0, (-0.8, 0.0, 0.5), (0.7, 0.0, 0.5))
    path = motion.path_from_keys(keys, "prop:car")
    off = motion.prop_socket_offset(CAR, "driver")
    seat = motion.seated_root_keys("dad", path, off, root_offset=(0.0, -0.1, 0.0))
    assert [k[:3] for k in seat] == [("puppet:dad", "root.position", 0.0), ("puppet:dad", "root.position", t1)]
    for t in (0.0, t1 / 3, t1):
        got = motion.position_at([(k[2], k[3]) for k in seat], t)
        want = motion.socket_position_at(path, t, off)
        assert got == pytest.approx((want[0], want[1] - 0.1, want[2]))


def test_position_at_and_path_from_keys():
    with pytest.raises(ValueError):
        motion.position_at([], 0.0)
    assert motion.position_at([(1.0, [0, 0, 0]), (0.0, [1, 0, 0])], 0.5) == pytest.approx((0.5, 0, 0))
    keys = [("prop:a", "position", 2.0, [1, 0, 0]), ("prop:b", "position", 0.0, [9, 9, 9]),
            ("prop:a", "position", 0.0, [0, 0, 0]), ("prop:a", "rotation", 1.0, 0.3)]
    assert motion.path_from_keys(keys, "prop:a") == [(0.0, (0.0, 0.0, 0.0)), (2.0, (1.0, 0.0, 0.0))]


def test_vehicle_helpers_deterministic():
    a = motion.drive_keys("car", 0.3, (-1, 0, 0.5), (0.7, 0, 0.6))
    b = motion.drive_keys("car", 0.3, (-1, 0, 0.5), (0.7, 0, 0.6))
    assert a == b
    p = motion.path_from_keys(a[0], "prop:car")
    assert motion.wheel_spin_keys("car", CAR["parts"], p, 0.1) == motion.wheel_spin_keys("car", CAR["parts"], p, 0.1)


# ----------------------------------------------------------- follow: walking speed

def test_follow_speed_scale_slows_the_walk_up_to_a_stationary_leader():
    leader = [(0.0, (0.3, 0, 0.5))]
    plain, t_plain, _ = follow_keys("dad", "tall", 1.0, (-1.0, 0, 0.5), 1, leader, None)
    slow, t_slow, _ = follow_keys("dad", "tall", 1.0, (-1.0, 0, 0.5), 1, leader, None, speed_scale=0.6)
    assert t_plain == pytest.approx(1.0 + 1.1 / 0.35)
    assert (t_slow - 1.0) == pytest.approx((t_plain - 1.0) / 0.6, abs=1e-3)
    # same spot, same stride count: only the timing stretches
    assert follower_path(slow)[-1][1] == follower_path(plain)[-1][1] == pytest.approx((0.1, 0, 0.5))
    assert len(slow) == len(plain)
    assert slow == follow_keys("dad", "tall", 1.0, (-1.0, 0, 0.5), 1, leader, None, speed_scale=0.6)[0]


def test_follow_speed_scale_with_dur_clips_a_slow_walker_short():
    leader = [(0.0, (0.3, 0, 0.5))]
    keys, t_end, _ = follow_keys("dad", "tall", 1.0, (-1.0, 0, 0.5), 1, leader, 2.0, speed_scale=0.6)
    assert t_end == pytest.approx(3.0)
    # in 2 s at 0.6 * 0.35 a slow walker covers 0.42 of the 1.1 to go
    assert follower_path(keys)[-1][1][0] == pytest.approx(-1.0 + 2.0 * 0.35 * 0.6)


@pytest.mark.parametrize("scale", [0.6, 1.0, 1.25, 3.0])
def test_follow_speed_scale_never_arrives_before_leader_arrival_plus_lag(scale):
    leader = leader_path(1.0, (-0.8, 0, 0.5), [(0.0, (-0.2, 0, 0.5)), (0.8, (0.6, 0, 0.5))])
    keys, t_end, _ = follow_keys("dad", "tall", 1.0, (-1.3, 0, 0.5), 1, leader, None, speed_scale=scale)
    fpath = follower_path(keys)
    assert fpath[-1][1][0] == pytest.approx(0.6 - 0.2)
    assert t_end >= leader[-1][0] + 0.5 - 1e-9
    # every walk segment ends no earlier than the leader's matching arrival + lag
    arrivals = [leader[2][0], leader[-1][0]]
    seg_ends = [t for t, _ in fpath[1::2]]
    assert len(seg_ends) == len(arrivals)
    for end, arrive in zip(seg_ends, arrivals):
        assert end >= arrive + 0.5 - 1e-9
    assert_never_walks_into_leader(fpath, leader, 1.0, t_end)


def test_follow_slow_walker_takes_its_own_time_behind_a_moving_leader():
    # The leader hops 0.2 in 0.1 s, so the follower is never held back by it: its walk time
    # is its own natural time, 1 / 0.6 as long for a slow walker.
    leader = [(0.0, (0.2, 0, 0.5)), (2.0, (0.2, 0, 0.5)), (2.1, (0.4, 0, 0.5))]
    _, t_plain, _ = follow_keys("kid", "small", 2.0, (-1.3, 0, 0.5), 1, leader, None)
    keys, t_slow, _ = follow_keys("kid", "small", 2.0, (-1.3, 0, 0.5), 1, leader, None, speed_scale=0.6)
    set_off = 2.0 + 0.5  # leader set off at 2.0, plus lag
    assert t_plain == pytest.approx(set_off + 1.5 / 0.35)
    assert (t_slow - set_off) == pytest.approx((t_plain - set_off) / 0.6, abs=1e-3)
    assert follower_path(keys)[-1][1][0] == pytest.approx(0.2)


@pytest.mark.parametrize("scale", [0.0, -0.5])
def test_follow_speed_scale_must_be_positive(scale):
    with pytest.raises(ValueError, match="speed_scale must be positive"):
        follow_keys("dad", "tall", 1.0, (-1.0, 0, 0.5), 1, [(0.0, (0.3, 0, 0.5))], None, speed_scale=scale)
    with pytest.raises(ValueError, match="speed_scale must be positive"):
        follow_keys("dad", "tall", 1.0, (-1.0, 0, 0.5), 1, [(0.0, (0.3, 0, 0.5)), (2.0, (0.6, 0, 0.5))], None,
                    speed_scale=scale)


def test_follow_speed_scale_is_keyword_only():
    import inspect

    param = inspect.signature(follow_keys).parameters["speed_scale"]
    assert param.kind is inspect.Parameter.KEYWORD_ONLY and param.default == 1.0
