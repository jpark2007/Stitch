"""Marks to stage positions and puppet locomotion (walk / run / enter / exit / turn / follow).

Pure, deterministic functions. Everything is in stage units and timeline seconds
(see shared/CONTRACTS.md 'Marks' and 'The puppet'). The walk arithmetic matches
api/tests/gen_hymn_books_sample.py walk() exactly, so the compiler reproduces the
reference sample.

Keys are (target, channel, t, value) tuples. Positions are emitted as [x, y, z]
lists, rotations as floats, facing as the int 1 or -1. Values are not rounded;
rounding is the compiler's job when it writes the Timeline.
"""
from __future__ import annotations

import json
import math
from typing import Iterable, Literal

from .schema import SHARED_DIR, VOCABULARY

Vec3 = tuple[float, float, float]
Key = tuple[str, str, float, object]

RIG: dict = json.loads((SHARED_DIR / "rig" / "puppet.json").read_text())
POSES: dict = json.loads((SHARED_DIR / "rig" / "poses.json").read_text())
SPEEDS: dict[str, float] = VOCABULARY["speeds"]
BUILDS: dict[str, float] = VOCABULARY["builds"]

ACTING_Z = 0.5

# Same order as gen_hymn_books_sample.LIMBS.
LIMBS: tuple[str, ...] = (
    "leg_l.upper", "leg_l.lower", "leg_r.upper", "leg_r.lower",
    "arm_l.upper", "arm_l.lower", "arm_r.upper", "arm_r.lower",
)

_EPS = 1e-9


def _target(who: str) -> str:
    return f"puppet:{who}"


def _sign(v: float) -> int:
    if v > _EPS:
        return 1
    if v < -_EPS:
        return -1
    return 0


# --------------------------------------------------------------------------- marks

def mark_position(mark: list[float] | tuple[float, ...]) -> Vec3:
    """[u, h] or [u, h, d] -> (2u - 1, h, d or 0.5)."""
    if len(mark) not in (2, 3):
        raise ValueError(f"a mark is [u, h] or [u, h, d], got {list(mark)!r}")
    u, h = float(mark[0]), float(mark[1])
    d = float(mark[2]) if len(mark) == 3 else ACTING_Z
    return (2.0 * u - 1.0, h, d)


def resolve_marks(set_marks: dict[str, list[float]] | None,
                  shot_marks: dict[str, list[float]] | None) -> dict[str, Vec3]:
    """Builtin marks, then set marks, then shot marks; later entries override earlier ones."""
    out: dict[str, Vec3] = {}
    for source in (VOCABULARY["builtinMarks"], set_marks or {}, shot_marks or {}):
        for name, mark in source.items():
            out[name] = mark_position(mark)
    return out


def nearest_offstage(x: float) -> str:
    return "offstage_left" if x <= 0 else "offstage_right"


def puppet_scale(build: str) -> float:
    return RIG["stageHeight"] * BUILDS[build]


# ---------------------------------------------------------------------- locomotion

def locomotion_keys(
    who: str,
    build: str,
    t0: float,
    start: Vec3,
    end: Vec3,
    facing_now: int,
    gait: Literal["walk", "run"] = "walk",
    skip_bones: Iterable[str] = (),
    speed_scale: float = 1.0,
) -> tuple[list[Key], float, int]:
    """Root slide from start to end plus cycle keys. Returns (keys, arrival, facing after)."""
    if gait not in ("walk", "run"):
        raise ValueError(f"unknown gait {gait!r}")
    if speed_scale <= 0:
        raise ValueError(f"speed_scale must be positive, got {speed_scale}")
    dx, dz = end[0] - start[0], end[2] - start[2]
    dist = math.hypot(dx, dz)
    target = _target(who)
    if dist <= _EPS:
        dy = end[1] - start[1]
        if abs(dy) <= _EPS:
            return [], t0, facing_now
        # Straight up or down onto a mark (a step, a riser): no ground distance, so no
        # walk cycle, but the root still has to reach the new height. Climb at gait speed.
        t1 = t0 + abs(dy) / (SPEEDS[gait] * speed_scale)
        return [
            (target, "root.position", t0, [start[0], start[1], start[2]]),
            (target, "root.position", t1, [end[0], end[1], end[2]]),
        ], t1, facing_now

    dur = dist / (SPEEDS[gait] * speed_scale)
    t1 = t0 + dur
    keys: list[Key] = []

    facing = facing_now
    direction = _sign(dx)
    if direction != 0 and direction != facing_now:
        facing = direction
        keys.append((target, "root.facing", t0, facing))

    keys.append((target, "root.position", t0, [start[0], start[1], start[2]]))
    keys.append((target, "root.position", t1, [end[0], end[1], end[2]]))

    cycle = POSES["cycles"][gait]
    frames = cycle["frames"]
    skip = set(skip_bones)
    bones = [b for b in LIMBS if b not in skip]
    if gait == "run" and "torso" not in skip:
        bones.append("torso")
    stride = cycle["strideLength"] * puppet_scale(build)
    n = max(1, round(dist / stride * 4))  # quarter-cycle keys

    for b in bones:
        keys.append((target, f"{b}.rot", t0, 0.0))
    for i in range(1, n):
        frame = frames[i % 4]
        t = t0 + dur * i / n
        for b in bones:
            keys.append((target, f"{b}.rot", t, float(frame.get(b, 0.0))))
    for b in bones:
        keys.append((target, f"{b}.rot", t1, 0.0))
    return keys, t1, facing


def enter_keys(who: str, build: str, t0: float, from_pos: Vec3, to: Vec3, facing_now: int,
               gait: Literal["walk", "run"] = "walk", skip_bones: Iterable[str] = (),
               speed_scale: float = 1.0) -> tuple[list[Key], float, int]:
    """Place the puppet at from_pos at t0 (usually offstage), then move it to `to`."""
    first: Key = (_target(who), "root.position", t0, [from_pos[0], from_pos[1], from_pos[2]])
    keys, t1, facing = locomotion_keys(who, build, t0, from_pos, to, facing_now, gait, skip_bones, speed_scale)
    return [first] + [k for k in keys if k != first], t1, facing


def exit_keys(who: str, build: str, t0: float, start: Vec3, to: Vec3, facing_now: int,
              gait: Literal["walk", "run"] = "walk", skip_bones: Iterable[str] = (),
              speed_scale: float = 1.0) -> tuple[list[Key], float, int]:
    """Move the puppet from start to an offstage position `to`."""
    return locomotion_keys(who, build, t0, start, to, facing_now, gait, skip_bones, speed_scale)


def turn_keys(who: str, t: float, pos_x: float, target_x: float, facing_now: int) -> tuple[list[Key], int]:
    """Face toward target_x. One root.facing key at t if the facing changes, else none."""
    direction = _sign(target_x - pos_x)
    if direction == 0 or direction == facing_now:
        return [], facing_now
    return [(_target(who), "root.facing", t, direction)], direction


# -------------------------------------------------------------------------- follow

def _leader_at(path: list[tuple[float, Vec3]], t: float) -> Vec3:
    """Linear interpolation of the leader's root.position keys (held before/after)."""
    if t <= path[0][0]:
        return path[0][1]
    for (ta, pa), (tb, pb) in zip(path, path[1:]):
        if ta <= t <= tb:
            if tb - ta <= _EPS:
                return pb
            f = (t - ta) / (tb - ta)
            return (pa[0] + (pb[0] - pa[0]) * f, pa[1] + (pb[1] - pa[1]) * f, pa[2] + (pb[2] - pa[2]) * f)
    return path[-1][1]


def _lerp3(a: Vec3, b: Vec3, f: float) -> Vec3:
    return (a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f, a[2] + (b[2] - a[2]) * f)


def follow_keys(
    who: str,
    build: str,
    t0: float,
    start: Vec3,
    facing_now: int,
    target_path: list[tuple[float, Vec3]],
    dur: float | None,
    spacing: float = 0.2,
    lag: float = 0.5,
    gait: Literal["walk", "run"] = "walk",
    skip_bones: Iterable[str] = (),
) -> tuple[list[Key], float, int]:
    """Walk behind a leader along its root.position keys (timeline time).

    The leader's path is split into moves (consecutive keys at different positions; holds
    are ignored). For each move that ends after t0 the follower walks to
    (end x - spacing * d, end y, end z), where d is the sign of the move's x travel
    (a depth- or height-only move keeps the previous d). It sets off `lag` seconds after
    the leader set off on that move, and is slowed (never sped up) so that it does not
    arrive before the leader's arrival + lag.

    The follower never overtakes: it only ever walks in the leader's travel direction d,
    and it never walks into or through the leader:
    - if it stands ahead of the leader (or within `spacing` of it) it waits until the
      leader is `spacing` past it, then follows;
    - if its spot behind the leader lies behind it, it holds instead of backing up;
    - if the leader turns back while the follower is still walking, the follower stops
      where it is when the leader sets off the other way.
    So while the follower moves, the leader is always level with or ahead of it in the
    direction it walks. A leader that never moves after t0 is walked up to, from t0.
    With `dur`, motion is clipped at t0 + dur.
    Returns (keys, time the follower stops, facing after).
    """
    skip = tuple(skip_bones)
    if dur is not None and dur <= 0:
        return [], t0, facing_now
    path: list[tuple[float, Vec3]] = sorted(
        ((float(t), (float(p[0]), float(p[1]), float(p[2]))) for t, p in target_path), key=lambda k: k[0])
    if not path:
        return [], t0, facing_now
    end_t = None if dur is None else t0 + dur
    speed = SPEEDS[gait]

    # leader moves: (set off, arrive, from, to, d)
    moves: list[tuple[float, float, Vec3, Vec3, int]] = []
    d_prev = 0
    for (ta, pa), (tb, pb) in zip(path, path[1:]):
        if max(abs(pb[0] - pa[0]), abs(pb[1] - pa[1]), abs(pb[2] - pa[2])) <= _EPS:
            continue
        d = _sign(pb[0] - pa[0]) or d_prev
        d_prev = d
        if tb > t0 + _EPS:
            moves.append((ta, tb, pa, pb, d))

    keys: list[Key] = []
    cur_t, cur_p, facing = t0, start, facing_now

    if not moves:
        # The leader stands still from t0 on: walk up to `spacing` short of them.
        here = _leader_at(path, t0)
        d = _sign(here[0] - start[0]) or facing_now or 1
        goal: Vec3 = (here[0] - spacing * d, here[1], here[2])
        if _sign(goal[0] - start[0]) == -d:
            return [], t0, facing_now  # already within spacing: do not back away
        if end_t is not None:
            natural = math.hypot(goal[0] - start[0], goal[2] - start[2]) / speed
            if natural > _EPS and t0 + natural > end_t + _EPS:
                goal = _lerp3(start, goal, (end_t - t0) / natural)
        seg_keys, t_arr, facing = locomotion_keys(who, build, t0, start, goal, facing, gait, skip)
        return seg_keys, (t_arr if seg_keys else t0), facing

    # A move whose d is 0 (leader never moved in x yet) takes its direction from where the
    # leader stands relative to the follower.
    for i, (set_off, arrive, pa, pb, d) in enumerate(moves):
        if d == 0:
            d = _sign(pb[0] - cur_p[0]) or facing or 1
        goal = (pb[0] - spacing * d, pb[1], pb[2])
        m = _sign(goal[0] - cur_p[0])
        if m == -d:
            continue  # the spot is behind the follower: hold rather than back up or pass the leader
        seg_start = max(cur_t, set_off + lag)
        # Wait until the leader is at least `spacing` ahead of the follower.
        lead_x = _leader_at(path, seg_start)[0]
        if lead_x * d < cur_p[0] * d + spacing - _EPS and abs(pb[0] - pa[0]) > _EPS:
            f = ((cur_p[0] + spacing * d) - pa[0]) / (pb[0] - pa[0])
            seg_start = max(seg_start, set_off + (arrive - set_off) * min(1.0, max(0.0, f)))
        # The leader turning back bounds this walk.
        cutoff = math.inf
        if i + 1 < len(moves):
            nxt = moves[i + 1]
            if nxt[4] == -d:
                cutoff = nxt[0]
        if end_t is not None:
            cutoff = min(cutoff, end_t)
        if seg_start >= cutoff - _EPS:
            if end_t is not None and seg_start >= end_t - _EPS:
                break
            continue
        dist = math.hypot(goal[0] - cur_p[0], goal[2] - cur_p[2])
        if dist <= _EPS:
            continue
        natural = dist / speed
        window = (arrive + lag) - seg_start
        scale = natural / window if window > natural else 1.0
        seg_end = seg_start + natural / scale
        if seg_end > cutoff + _EPS:
            goal = _lerp3(cur_p, goal, (cutoff - seg_start) / (seg_end - seg_start))
        seg_keys, t_arr, facing = locomotion_keys(who, build, seg_start, cur_p, goal, facing, gait, skip, scale)
        if seg_keys:
            keys.extend(seg_keys)
            cur_t, cur_p = t_arr, goal
        if end_t is not None and cur_t >= end_t - _EPS:
            break
    return keys, cur_t, facing
