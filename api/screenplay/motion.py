"""Marks to stage positions, puppet locomotion (walk / run / enter / exit / turn / follow)
and vehicles (drive_to / ride_to: the vehicle path, wheel spin, seat positions).

Pure, deterministic functions. Everything is in stage units and timeline seconds
(see shared/CONTRACTS.md 'Marks', 'The puppet' and 'Props'). The walk arithmetic matches
api/tests/gen_hymn_books_sample.py walk() exactly, so the compiler reproduces the
reference sample. This module does not track state or parent anything: the compiler
decides who is seated where and uses these helpers to place them.

Keys are (target, channel, t, value) tuples. Positions are emitted as [x, y, z]
lists, rotations as floats, facing as the int 1 or -1. Values are not rounded;
rounding is the compiler's job when it writes the Timeline.
"""
from __future__ import annotations

import json
import math
from typing import Any, Iterable, Literal, Mapping, Sequence

from .schema import SHARED_DIR, VOCABULARY

Vec3 = tuple[float, float, float]
Key = tuple[str, str, float, object]
Waypoints = list[tuple[float, Vec3]]  # (timeline t, position), sorted by t

RIG: dict = json.loads((SHARED_DIR / "rig" / "puppet.json").read_text())
POSES: dict = json.loads((SHARED_DIR / "rig" / "poses.json").read_text())
SPEEDS: dict[str, float] = VOCABULARY["speeds"]
BUILDS: dict[str, float] = VOCABULARY["builds"]
SIZES: dict[str, float] = VOCABULARY["sizes"]

ACTING_Z = 0.5

# Used only if shared/vocabulary.json ever stops listing speeds.drive (stage units / s).
DEFAULT_DRIVE_SPEED = 0.9
DRIVE_SPEED: float = float(SPEEDS.get("drive", DEFAULT_DRIVE_SPEED))

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


# ------------------------------------------------------------------------- vehicles
#
# A vehicle is a prop on the ground: its `position` track is the world position of its
# anchor (shared/CONTRACTS.md 'Props'). Props are never mirrored (there is no facing
# channel for props), so socket and part offsets do not flip when a vehicle drives left.
# drive_to and ride_to move the vehicle the same way; they differ only in who the
# compiler requires to be seated where.

def _field(obj: Any, name: str, default: Any = None) -> Any:
    """Read a field from a pydantic model (snake_case attribute) or a JSON-style mapping."""
    if isinstance(obj, Mapping):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _vec3(p: Sequence[float]) -> Vec3:
    if len(p) != 3:
        raise ValueError(f"a position is [x, y, z], got {list(p)!r}")
    return (float(p[0]), float(p[1]), float(p[2]))


def _waypoints(path: Iterable[tuple[float, Sequence[float]]]) -> Waypoints:
    """Normalise to (float t, Vec3) pairs sorted by t (stable, so equal-t steps keep order)."""
    return sorted(((float(t), _vec3(p)) for t, p in path), key=lambda k: k[0])


def prop_dimensions(size: str, aspect: float = 1.0) -> tuple[float, float]:
    """(width, height) in stage units: height = sizes[size], width = height * aspect."""
    if size not in SIZES:
        raise ValueError(f"unknown prop size {size!r}")
    if aspect <= 0:
        raise ValueError(f"aspect must be positive, got {aspect}")
    h = SIZES[size]
    return (h * aspect, h)


def position_at(path: Iterable[tuple[float, Sequence[float]]], t: float) -> Vec3:
    """Linear interpolation of position keys at time t; holds the first/last value outside."""
    pts = _waypoints(path)
    if not pts:
        raise ValueError("position_at needs at least one key")
    return _leader_at(pts, float(t))


def path_from_keys(keys: Iterable[Key], target: str, channel: str = "position") -> Waypoints:
    """The (t, [x, y, z]) keys of one target + channel, as a sorted path."""
    return _waypoints((k[2], k[3]) for k in keys if k[0] == target and k[1] == channel)  # type: ignore[misc]


def drive_keys(
    prop_id: str,
    t0: float,
    start: Vec3,
    end: Vec3,
    speed: float | None = None,
) -> tuple[list[Key], float]:
    """Move a vehicle's anchor from start (its current position) to end (the target mark).

    Duration = ground distance (x and z) / speed, with speed defaulting to the vocabulary's
    speeds.drive (DRIVE_SPEED). A height-only move (a ramp up to a raised mark) takes
    |dy| / speed so the vehicle still reaches the new height. Emits two linear `position`
    keys on `prop:<id>`, at t0 and at arrival. Returns (keys, arrival time); no keys and
    arrival t0 when start == end.
    """
    v = DRIVE_SPEED if speed is None else float(speed)
    if v <= 0:
        raise ValueError(f"drive speed must be positive, got {v}")
    start, end = _vec3(start), _vec3(end)
    dist = math.hypot(end[0] - start[0], end[2] - start[2])
    if dist <= _EPS:
        dist = abs(end[1] - start[1])
        if dist <= _EPS:
            return [], t0
    t1 = t0 + dist / v
    target = f"prop:{prop_id}"
    return [
        (target, "position", t0, [start[0], start[1], start[2]]),
        (target, "position", t1, [end[0], end[1], end[2]]),
    ], t1


def spinning_parts(parts: Mapping[str, Any] | None) -> list[str]:
    """Names of the parts with spin=true (PropPart models or {"spin": true} mappings), sorted."""
    return sorted(name for name, part in (parts or {}).items() if _field(part, "spin", False))


def wheel_rotation(distance: float, radius: float, direction: int) -> float:
    """Rolling-without-slipping angle (radians, CCW positive) after `distance` of travel.

    Travel toward +x (direction 1) turns a wheel clockwise seen from the front, so the
    angle is -distance / radius; toward -x (direction -1) it is +distance / radius.
    """
    if radius <= 0:
        raise ValueError(f"wheel radius must be positive, got {radius}")
    if direction not in (1, -1):
        raise ValueError(f"direction must be 1 or -1, got {direction}")
    return -direction * distance / radius


def wheel_spin_keys(
    prop_id: str,
    parts: Mapping[str, Any] | None,
    path: Iterable[tuple[float, Sequence[float]]],
    radius: float,
    start_rot: float = 0.0,
    heading: int = 1,
) -> list[Key]:
    """`part.<name>.rot` keys for every spinning part, following the vehicle's path.

    `path` is the vehicle's anchor positions over time (e.g. drive_keys' keys via
    path_from_keys); `radius` is the wheel radius in stage units. Each segment adds
    wheel_rotation(ground distance, radius, sign of its x travel) to the angle, starting
    from `start_rot` at the first key (pass the last angle to continue from an earlier
    drive). A segment with no x travel (a pure depth move) rolls in the last x direction,
    `heading` before any. One key per path key per part (holds included, so the wheels
    stay still while the vehicle does); linear; sorted by time, then part name.
    """
    if radius <= 0:
        raise ValueError(f"wheel radius must be positive, got {radius}")
    if heading not in (1, -1):
        raise ValueError(f"heading must be 1 or -1, got {heading}")
    names = spinning_parts(parts)
    pts = _waypoints(path)
    if not names or not pts:
        return []
    angles = [float(start_rot)]
    direction = heading
    for (_, a), (_, b) in zip(pts, pts[1:]):
        direction = _sign(b[0] - a[0]) or direction
        dist = math.hypot(b[0] - a[0], b[2] - a[2])
        angles.append(angles[-1] + wheel_rotation(dist, radius, direction))
    target = f"prop:{prop_id}"
    return [(target, f"part.{name}.rot", t, rot)
            for (t, _), rot in zip(pts, angles) for name in names]


def socket_offset(socket_at: Sequence[float], anchor: Sequence[float],
                  size: str, aspect: float = 1.0) -> Vec3:
    """Offset in stage units from a prop's anchor to a socket, both [u, v] image fractions
    ([0, 0] top-left): ((u - anchor_u) * width, (anchor_v - v) * height, 0)."""
    w, h = prop_dimensions(size, aspect)
    return ((float(socket_at[0]) - float(anchor[0])) * w, (float(anchor[1]) - float(socket_at[1])) * h, 0.0)


def prop_socket_offset(prop: Any, socket: str) -> Vec3:
    """socket_offset for a Prop (api/schemas.Prop model, or its JSON as a dict)."""
    sockets = _field(prop, "sockets") or {}
    if socket not in sockets:
        raise KeyError(f"prop {_field(prop, 'id')!r} has no socket {socket!r}")
    anchor = _field(prop, "anchor") or [0.5, 1.0]
    return socket_offset(sockets[socket], anchor, _field(prop, "size"), float(_field(prop, "aspect", 1.0) or 1.0))


def socket_position_at(path: Iterable[tuple[float, Sequence[float]]], t: float, offset: Vec3) -> Vec3:
    """World position of a socket on a moving prop at time t: anchor(t) + offset."""
    a = position_at(path, t)
    return (a[0] + offset[0], a[1] + offset[1], a[2] + offset[2])


def vehicle_socket_position(prop: Any, socket: str,
                            path: Iterable[tuple[float, Sequence[float]]], t: float) -> Vec3:
    """World position of a named socket of a Prop whose anchor follows `path`, at time t."""
    return socket_position_at(path, t, prop_socket_offset(prop, socket))


def seated_root_keys(
    who: str,
    path: Iterable[tuple[float, Sequence[float]]],
    offset: Vec3,
    root_offset: Sequence[float] = (0.0, 0.0, 0.0),
) -> list[Key]:
    """`root.position` keys for a puppet riding in a socket: one key per path key at
    anchor + socket offset + root_offset (e.g. a sit pose's baked rootOffset, in stage
    units). The vehicle moves linearly between its keys and the offset is constant, so
    these keys keep the puppet exactly on the socket at every t."""
    ro = _vec3(root_offset)
    out: list[Key] = []
    for t, a in _waypoints(path):
        out.append((_target(who), "root.position", t,
                    [a[0] + offset[0] + ro[0], a[1] + offset[1] + ro[1], a[2] + offset[2] + ro[2]]))
    return out
