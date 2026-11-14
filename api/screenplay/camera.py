"""Camera moves resolved to numbers (shared/CONTRACTS.md Decision 1).

The camera is three tracks on target "camera": 'position' [x, y, z], 'lookAt' [x, y, z] and
'fov' (degrees). Named moves are resolved here so the stage only interpolates. Arithmetic
matches api/tests/gen_hymn_books_sample.py: the first shot flies in from FLY_IN while the set
rises, shot t=0 lands on WIDE unless a t=0 cue says otherwise, and every later cue holds the
current pose at its start time and reaches the resolved pose after `dur` seconds (``cue_span``).

A target beyond the stage edge is framed at CAMERA_X_LIMIT on its side. Aiming at a target that
is offstage for the whole move is refused before this runs (compiler._camera: ``bad_camera``);
for that check a t=0 cue spans its stated ``dur`` (DEFAULT_MOVE_SECONDS without one) although
its keys land on actionStart, so an opening shot on someone walking on is valid.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from screenplay.schema import CameraCue
from screenplay.tracks import Key, Vec3

CAMERA = "camera"
DEFAULT_MOVE_SECONDS = 1.5
CAMERA_X_LIMIT = 0.7
CLOSE_UP_FOV = 28.0
TARGET_MOVES = frozenset({"dolly_to", "pan_to", "close_up"})
KNOWN_MOVES = TARGET_MOVES | {"wide", "pull_back"}


@dataclass(frozen=True)
class CameraPose:
    position: Vec3
    look_at: Vec3
    fov: float


FLY_IN = CameraPose((0, 1.1, 3.4), (0, 0.2, 0.5), 35)
WIDE = CameraPose((0, 0.6, 2.6), (0, 0.35, 0.5), 35)


def _clamp_x(x: float) -> float:
    return max(-CAMERA_X_LIMIT, min(CAMERA_X_LIMIT, x))


def _pose(position: Sequence[float], look_at: Sequence[float], fov: float) -> CameraPose:
    p = (_clamp_x(position[0]), position[1], position[2])
    la = (_clamp_x(look_at[0]), look_at[1], look_at[2])
    return CameraPose(p, la, fov)


def resolve_move(move: str, current: CameraPose, target: Vec3 | None) -> CameraPose:
    """The pose a named move ends on. Raises ValueError for an unknown move or a missing target."""
    if move not in KNOWN_MOVES:
        raise ValueError(f"unknown camera move {move!r}; expected one of {sorted(KNOWN_MOVES)}")
    if move in TARGET_MOVES and target is None:
        raise ValueError(f"camera move {move!r} needs a target")
    cp, cl = current.position, current.look_at
    if move == "wide":
        return WIDE
    if move == "pull_back":
        return _pose((cp[0] * 0.5, 0.75, 3.0), (cl[0] * 0.5, 0.35, 0.5), 35)
    if target is None:  # unreachable: every non-target move returned above
        raise ValueError(f"camera move {move!r} needs a target")
    tx, ty, tz = target
    if move == "dolly_to":
        return _pose((tx, 0.6, 1.5), (tx, ty + 0.25, tz), current.fov)
    if move == "close_up":
        return _pose((tx, ty + 0.3, 1.1), (tx, ty + 0.25, tz), CLOSE_UP_FOV)
    if move == "pan_to":
        return _pose((tx, cp[1], cp[2]), (tx, cl[1], cl[2]), current.fov)
    raise ValueError(f"unknown camera move {move!r}")


def cue_span(cue: CameraCue, action_start: float) -> tuple[float, float]:
    """Timeline times a cue's move starts and lands, as ``shot_camera_keys`` plays it: a t=0
    cue lands on ``action_start`` (its ``dur`` is not used), a later one starts at
    ``action_start + t`` and lands ``dur`` (default DEFAULT_MOVE_SECONDS) later."""
    t0 = action_start + cue.t
    if cue.t == 0:
        return t0, t0
    return t0, t0 + (DEFAULT_MOVE_SECONDS if cue.dur is None else cue.dur)


def _lerp3(a: Vec3, b: Vec3, f: float) -> Vec3:
    return (a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f, a[2] + (b[2] - a[2]) * f)


def lerp_pose(a: CameraPose, b: CameraPose, f: float) -> CameraPose:
    return CameraPose(_lerp3(a.position, b.position, f), _lerp3(a.look_at, b.look_at, f), a.fov + (b.fov - a.fov) * f)


def shot_camera_keys(
    cues: list[CameraCue],
    shot_start: float,
    action_start: float,
    prev: CameraPose | None,
    target_at: Callable[[str, float], Vec3],
) -> tuple[list[Key], CameraPose]:
    """Camera keys for one shot (timeline time) and the pose the shot ends on.

    target_at(name, t) gives the stage position of a person, prop or mark at timeline time t;
    it is called with the time the move ends.
    """
    start_pose = FLY_IN if prev is None else prev
    frames: list[tuple[float, CameraPose]] = [(shot_start, start_pose)]

    ordered = sorted(cues, key=lambda c: c.t)  # stable
    opening = [c for c in ordered if c.t == 0]
    later = [c for c in ordered if c.t != 0]
    if any(c.t < 0 for c in later):
        raise ValueError("camera cue times must be >= 0")

    def target_for(cue: CameraCue, at: float) -> Vec3 | None:
        if cue.move in TARGET_MOVES:
            if cue.target is None:
                raise ValueError(f"camera move {cue.move!r} at t={cue.t} needs a target")
            return target_at(cue.target, at)
        return None

    pose = start_pose
    if opening:
        for cue in opening:  # several t=0 cues: each resolves from the last, all land on action_start
            pose = resolve_move(cue.move, pose, target_for(cue, action_start))
    else:
        pose = WIDE
    frames.append((action_start, pose))

    # the running move: (t0, pose0, t1, pose1)
    move = (action_start, pose, action_start, pose)
    for cue in later:
        t0 = action_start + cue.t
        m0, p0, m1, p1 = move
        dur = DEFAULT_MOVE_SECONDS if cue.dur is None else cue.dur
        if dur < 0:
            raise ValueError(f"camera cue at t={cue.t} has a negative dur")
        if m0 <= t0 < m1:
            # interrupts the running move: it stops where it has got to, so drop its end key
            hold = lerp_pose(p0, p1, (t0 - m0) / (m1 - m0))
            frames.pop()
        else:
            hold = p1
        t1 = t0 + dur
        end = resolve_move(cue.move, hold, target_for(cue, t1))
        frames.append((t0, hold))
        frames.append((t1, end))
        move = (t0, hold, t1, end)
        pose = end

    keys: list[Key] = []
    for i, (t, p) in enumerate(frames):
        keys.append((CAMERA, "position", t, list(p.position)))
        keys.append((CAMERA, "lookAt", t, list(p.look_at)))
        # fov only where it is needed to reproduce the same linear curve
        changes_in = i > 0 and frames[i - 1][1].fov != p.fov
        changes_out = i + 1 < len(frames) and frames[i + 1][1].fov != p.fov
        if i == 0 or changes_in or changes_out:
            keys.append((CAMERA, "fov", t, p.fov))
    return keys, pose
