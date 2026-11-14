"""Two-bone inverse kinematics and forward kinematics for the shared paper puppet.

Pure functions over shared/rig/puppet.json. Conventions (shared/CONTRACTS.md, "The puppet"):

- Rig units: standing height 1.0, origin at the hip, local +x is the way the puppet faces.
- On stage a puppet is ``stageHeight * builds[build]`` units tall (``scale(build)``).
- ``ground`` is ``root.position`` (x, y): the point on the ground under the hips, with any
  ``rootOffset`` (sit, kneel) already baked in. The hip is ``ground + hipHeight * scale``.
- ``facing`` is 1 (faces +x) or -1 (mirrored). Rotations are always in the unmirrored local frame.
- ``torso`` is the torso bone's rotation. The arms hang from the torso, so the shoulder moves with
  it and an arm's upper rotation is relative to it (CONTRACTS.md Clarification 2: the
  shoulder-to-target vector is solved in the torso's frame). With ``torso=0`` every result is
  bit-identical to the upright solver.

The arithmetic matches api/tests/gen_hymn_books_sample.py exactly, so the compiler reproduces
the reference timeline.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass

from screenplay.schema import SHARED_DIR, VOCABULARY

RIG: dict = json.loads((SHARED_DIR / "rig" / "puppet.json").read_text())
POSES: dict = json.loads((SHARED_DIR / "rig" / "poses.json").read_text())

Vec2 = tuple[float, float]

# Slack on the reach test so a target placed exactly at full stretch still solves.
REACH_EPS = 1e-9

# A handover's hands meet at most this fraction of the way from the giver's shoulder to the
# taker's (handover_point), so a taker standing close never reaches back behind itself.
HANDOVER_MAX_ACROSS = 2 / 3


@dataclass(frozen=True)
class Body:
    """Where a puppet stands: enough to place its sockets in the world."""

    id: str
    ground: Vec2
    facing: int
    build: str
    torso: float = 0.0  # last, so positional Body(id, ground, facing, build) still works


class OutOfReach(Exception):
    """A hand cannot reach a target. ``distance`` and ``reach`` are in stage units."""

    def __init__(self, arm: str, distance: float, reach: float, who: str | None = None):
        self.arm = arm
        self.distance = distance
        self.reach = reach
        self.who = who
        whose = f"{who}'s {arm}" if who else arm
        super().__init__(f"{whose} cannot reach: target is {distance:.3f} from the shoulder, reach is {reach:.3f}")

    def with_who(self, who: str) -> OutOfReach:
        """The same error naming the puppet (the solver itself only knows the arm)."""
        return OutOfReach(self.arm, self.distance, self.reach, who)


def scale(build: str) -> float:
    """Stage units per rig unit for a build."""
    try:
        return RIG["stageHeight"] * VOCABULARY["builds"][build]
    except KeyError:
        raise ValueError(f"unknown build {build!r}; expected one of {sorted(VOCABULARY['builds'])}") from None


def _rot(v: Vec2, a: float) -> Vec2:
    c, s = math.cos(a), math.sin(a)
    return (v[0] * c - v[1] * s, v[0] * s + v[1] * c)


def bone_frame(bone: str, rots: Mapping[str, float]) -> tuple[Vec2, float]:
    """Pivot of ``bone`` in rig units (origin at the hip) and its accumulated angle."""
    if bone == RIG["root"]:
        return (0.0, 0.0), 0.0
    try:
        b = RIG["bones"][bone]
    except KeyError:
        raise ValueError(f"unknown bone {bone!r}") from None
    ppos, pang = bone_frame(b["parent"], rots)
    off = _rot(b["pivot"], pang)
    return (ppos[0] + off[0], ppos[1] + off[1]), pang + rots.get(bone, 0.0)


def socket_world(socket: str, rots: Mapping[str, float], ground: Vec2, facing: int, build: str) -> Vec2:
    """World (stage) position of a puppet socket for the given bone rotations."""
    try:
        sk = RIG["sockets"][socket]
    except KeyError:
        raise ValueError(f"unknown socket {socket!r}; expected one of {sorted(RIG['sockets'])}") from None
    s = scale(build)
    pos, ang = bone_frame(sk["bone"], rots)
    off = _rot(sk["at"], ang)
    lx, ly = pos[0] + off[0], pos[1] + off[1] + RIG["hipHeight"]
    return (ground[0] + facing * lx * s, ground[1] + ly * s)


def _arm(arm: str) -> dict:
    try:
        return RIG["ik"][arm]
    except KeyError:
        raise ValueError(f"unknown arm {arm!r}; expected one of {sorted(RIG['ik'])}") from None


def arm_socket(arm: str) -> str:
    """The hand socket an arm's IK chain ends in (``arm_r`` -> ``hand_r``)."""
    return _arm(arm)["socket"]


def arm_bones(arm: str) -> tuple[str, str]:
    """(upper, lower) bone names of an arm."""
    chain = _arm(arm)
    return chain["upper"], chain["lower"]


def shoulder_world(arm: str, ground: Vec2, facing: int, build: str, *, torso: float = 0.0) -> Vec2:
    """World position of an arm's shoulder pivot, after the torso's rotation (upright at 0)."""
    s = scale(build)
    (px, py), _ = bone_frame(_arm(arm)["upper"], {"torso": torso})
    return (ground[0] + facing * px * s, ground[1] + (py + RIG["hipHeight"]) * s)


def _bone_length(arm: str) -> float:
    return RIG["bones"][_arm(arm)["upper"]]["length"]


def arm_reach(build: str, arm: str = "arm_r") -> float:
    """Full stretch of an arm, shoulder to wrist, in stage units."""
    return 2 * _bone_length(arm) * scale(build)


def _arm_vector(arm: str, target: Vec2, ground: Vec2, facing: int, build: str, torso: float) -> Vec2:
    """Shoulder-to-target in rig units, mirrored for ``facing`` (not yet rotated by -torso)."""
    s = scale(build)
    sh = shoulder_world(arm, ground, facing, build, torso=torso)
    # A mirrored zero is -0.0; atan2(-0.0, -y) is -pi, not pi. Normalize so a target straight
    # above the shoulder gives the same rotation for either facing (no 2*pi jump between keys).
    return facing * (target[0] - sh[0]) / s + 0.0, (target[1] - sh[1]) / s


def arm_solution(
    arm: str, target: Vec2, ground: Vec2, facing: int, build: str, *, torso: float = 0.0
) -> tuple[float, tuple[float, float] | None]:
    """The two-bone solve without raising: (shoulder-to-target distance in rig units,
    (upper, lower), or None when the target is out of reach). ``solve_arm`` documents the
    formula; this is the one place it is computed."""
    vx, vy = _arm_vector(arm, target, ground, facing, build, torso)
    length = _bone_length(arm)
    d = math.hypot(vx, vy)
    if d > 2 * length + REACH_EPS:
        return d, None
    a = math.acos(min(1.0, d / (2 * length)))
    # Rotating v by -torso before atan2 is the same as taking torso off the angle afterwards.
    return d, (math.atan2(vx, -vy) - a - torso, 2 * a)


def solve_arm(
    arm: str, target: Vec2, ground: Vec2, facing: int, build: str, *, clamp: bool = False, torso: float = 0.0
) -> tuple[float, float]:
    """Two-bone IK: (upper, lower) rotations that put the arm's hand socket on ``target``.

    Contract formula: v = shoulder-to-target in local rig units, theta = atan2(v.x, -v.y),
    a = acos(|v| / 2L), upper = theta - a - torso, lower = 2a. Rotations are in the puppet's
    local, unmirrored frame (``facing`` flips v.x). The shoulder is where ``torso`` puts it,
    and the upper rotation is relative to the torso (CONTRACTS.md Clarification 2), so
    ``socket_world(hand, {"torso": torso, upper: u, lower: l}, ...)`` lands on the target.

    When |v| > 2L it raises OutOfReach, or with ``clamp=True`` returns the arm at full
    extension aimed at the target, ``(theta - torso, 0.0)``. The result is always finite.
    """
    if facing not in (1, -1):
        raise ValueError(f"facing must be 1 or -1, got {facing!r}")
    if not all(math.isfinite(v) for v in (*target, *ground, torso)):
        raise ValueError(
            f"IK target, ground and torso must be finite, got target={target!r} ground={ground!r} torso={torso!r}"
        )
    d, angles = arm_solution(arm, target, ground, facing, build, torso=torso)
    if angles is not None:
        return angles
    if clamp:
        vx, vy = _arm_vector(arm, target, ground, facing, build, torso)
        return math.atan2(vx, -vy) - torso, 0.0
    s = scale(build)
    raise OutOfReach(arm, d * s, 2 * _bone_length(arm) * s)


def can_reach(arm: str, target: Vec2, ground: Vec2, facing: int, build: str, *, torso: float = 0.0) -> bool:
    """True when ``solve_arm`` would succeed for this target."""
    try:
        solve_arm(arm, target, ground, facing, build, torso=torso)
    except OutOfReach:
        return False
    return True


def solve_body_arm(body: Body, arm: str, target: Vec2, *, clamp: bool = False) -> tuple[float, float]:
    """``solve_arm`` for a Body, from its torso; an OutOfReach names the puppet."""
    try:
        return solve_arm(arm, target, body.ground, body.facing, body.build, clamp=clamp, torso=body.torso)
    except OutOfReach as e:
        raise e.with_who(body.id) from None


def body_socket(body: Body, socket: str, rots: Mapping[str, float]) -> Vec2:
    """``socket_world`` for a Body. The body's torso applies unless ``rots`` names one."""
    return socket_world(socket, {"torso": body.torso, **rots}, body.ground, body.facing, body.build)


def handover_point(giver: Body, taker: Body, giver_arm: str = "arm_r", taker_arm: str = "arm_r") -> Vec2:
    """Where two hands meet to pass a prop.

    The point lies on the line from the giver's shoulder to the taker's, 0.83 of the giver's
    reach out from the giver: shoulder_g + (shoulder_t - shoulder_g) * (0.83 * reach_g / gap).
    Two puppets standing close would put that beyond the taker's shoulder (the taker reaching
    back behind itself), so the fraction is capped at HANDOVER_MAX_ACROSS: the hands meet at
    most two thirds of the way across and the taker always reaches out for the last third.
    The cap is continuous and leaves every handover at arm's length (the reference sample's
    included) unchanged. Each shoulder is where that body's torso puts it. Raises OutOfReach
    (naming the puppet) if either hand cannot get there.
    """
    sg = shoulder_world(giver_arm, giver.ground, giver.facing, giver.build, torso=giver.torso)
    st = shoulder_world(taker_arm, taker.ground, taker.facing, taker.build, torso=taker.torso)
    gap = math.hypot(st[0] - sg[0], st[1] - sg[1])
    if gap == 0.0:
        meet = sg
    else:
        f = min(0.83 * arm_reach(giver.build, giver_arm) / gap, HANDOVER_MAX_ACROSS)
        meet = (sg[0] + (st[0] - sg[0]) * f, sg[1] + (st[1] - sg[1]) * f)
    solve_body_arm(giver, giver_arm, meet)
    solve_body_arm(taker, taker_arm, meet)
    return meet
