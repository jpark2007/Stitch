"""Timeline key generators for body gestures and prop handling.

Every generator is pure and returns ``list[Key]``: ``(target, channel, t, value)`` tuples with
target ``puppet:<id>`` or ``prop:<id>``, absolute timeline time ``t`` (seconds) and the value
in the channel's units (shared/CONTRACTS.md, "Timeline"). Vec3 values are ``list[float]``.
Nothing is rounded here; the compiler rounds when it writes tracks.

Style: every animated channel gets a key at ``t`` holding its current value, then keys for the
target values, so a gesture never snaps from whatever came before. Step channels
(``parent``, ``root.facing``, ``state``) get a single key where they change. At every ``parent``
key there is a ``position`` key at the same time.

Arm choice (``arm_r`` / ``arm_l``) is always a parameter; the compiler picks it. Reaches start
from the Body's torso (``Body.torso``, 0 for upright), like the IK solver: the shoulder moves
with the torso and arm rotations are relative to it.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, replace

import schemas
from screenplay.ik import (
    POSES,
    RIG,
    Body,
    OutOfReach,
    Vec2,
    arm_bones,
    arm_reach,
    arm_socket,
    body_socket,
    handover_point,
    scale,
    shoulder_world,
    solve_body_arm,
)
from screenplay.schema import VOCABULARY

Vec3 = tuple[float, float, float]
Key = tuple[str, str, float, object]

POSE_SECONDS = 0.4    # blend into or out of a pose
REACH_SECONDS = 0.4   # hand travels to a grip point
FALL_SECONDS = 0.3    # a dropped prop falls to the ground
PASS_SECONDS = 0.5    # both hands travel to the handover point
RETURN_SECONDS = 0.4  # arms settle after a handover
DROP_OFFSET = 0.08    # a dropped prop lands this far in front of the dropper's feet (stage units)

# put_on with a grip the hand cannot carry all the way to the head (a cap held by its top edge, a
# side grip, a small build's short arm): the hand stops this fraction of full reach out along the
# shoulder-to-target line (not 1.0, so the elbow is not locked straight), and the prop settles from
# there onto the head socket over PUT_ON_SETTLE_SECONDS.
PUT_ON_REACH_FRACTION = 0.98
# Furthest the prop's anchor may be from the wearer's head socket when the hand lets go (stage
# units). 0.12 is one and a half hand-size props (sizes.hand = 0.08): it covers every grip of a
# hand-size prop for every build (worst case ~0.065, a corner grip on a small build) and the centre
# and side grips of a carried one, but not a carried prop held by its top (~0.17, a visible drop),
# and never a wearer who stands out of arm's reach.
PUT_ON_SETTLE_MAX = 0.12
# How long the released prop takes to settle onto the head. Shorter than REACH_SECONDS, so it is
# down before the arm is back at rest (verbs_objects.apply_put_on's end time relies on that).
PUT_ON_SETTLE_SECONDS = 0.15

ACTING_Z = 0.5
WAVE_SWING = 0.3      # lower-arm oscillation amplitude while waving
LEAN_TORSO = -0.2     # torso rotation when leaning toward something (negative leans forward)
REST_ARM: Vec2 = (0.0, 0.0)

__all__ = [
    "ACTING_Z", "DROP_OFFSET", "FALL_SECONDS", "Key", "PASS_SECONDS", "POSE_SECONDS", "PUT_ON_REACH_FRACTION",
    "PUT_ON_SETTLE_MAX", "PUT_ON_SETTLE_SECONDS", "PropGeom",
    "REACH_SECONDS", "RETURN_SECONDS", "Vec3", "anchor_from_grip", "drop_keys", "grip_from_anchor",
    "hand_to_keys", "hold_keys", "hold_pose", "hug_keys", "kneel_keys", "last_values", "lean_keys",
    "pick_up_keys", "place_on_keys", "point_keys", "pose_keys", "put_on_keys", "sit_keys",
    "stand_keys", "state_keys", "wave_keys", "OutOfReach",
]


# ------------------------------------------------------------------------------ props

@dataclass(frozen=True)
class PropGeom:
    """A prop's size and its anchor/grip points (image fractions, [0, 0] top-left)."""

    id: str
    height: float
    width: float
    anchor: Vec2
    grip: Vec2

    @classmethod
    def from_prop(cls, prop: schemas.Prop) -> PropGeom:
        height = VOCABULARY["sizes"][prop.size]
        grip = prop.grip if prop.grip is not None else [0.5, 0.5]
        return cls(
            id=prop.id,
            height=height,
            width=height * prop.aspect,
            anchor=(float(prop.anchor[0]), float(prop.anchor[1])),
            grip=(float(grip[0]), float(grip[1])),
        )


def anchor_from_grip(geom: PropGeom, grip_world: Vec2) -> Vec2:
    """World position of the anchor when the grip is at ``grip_world``. Image v runs downward."""
    return (
        grip_world[0] + (geom.anchor[0] - geom.grip[0]) * geom.width,
        grip_world[1] + (geom.grip[1] - geom.anchor[1]) * geom.height,
    )


def grip_from_anchor(geom: PropGeom, anchor_world: Vec2 | Vec3) -> Vec2:
    """World position of the grip when the anchor is at ``anchor_world`` (inverse of the above)."""
    return (
        anchor_world[0] - (geom.anchor[0] - geom.grip[0]) * geom.width,
        anchor_world[1] - (geom.grip[1] - geom.anchor[1]) * geom.height,
    )


# ---------------------------------------------------------------------------- helpers

def _puppet(who: str) -> str:
    return f"puppet:{who}"


def _prop(prop_id: str) -> str:
    return f"prop:{prop_id}"


def _rot_channel(bone: str) -> str:
    return f"{bone}.rot"


def _arm_keys(who: str, arm: str, t: float, values: Vec2) -> list[Key]:
    upper, lower = arm_bones(arm)
    return [
        (_puppet(who), _rot_channel(upper), t, float(values[0])),
        (_puppet(who), _rot_channel(lower), t, float(values[1])),
    ]


def _attach_keys(prop_id: str, t: float, parent: str, position: Vec3 | list[float]) -> list[Key]:
    """A parent swap with its same-time position key."""
    return [
        (_prop(prop_id), "parent", t, parent),
        (_prop(prop_id), "position", t, [float(v) for v in position]),
    ]


def _pose_values(pose: str) -> dict[str, float]:
    try:
        values = POSES["poses"][pose]
    except KeyError:
        raise ValueError(f"unknown pose {pose!r}; expected one of {sorted(POSES['poses'])}") from None
    return {bone: float(v) for bone, v in values.items() if bone != "rootOffset"}


def _for_arm(values: Mapping[str, float], arm: str) -> dict[str, float]:
    """Retarget an ``arm_r`` pose onto ``arm`` (other bones pass through unchanged)."""
    upper, lower = arm_bones(arm)
    out = {b: v for b, v in values.items() if not b.startswith("arm_r.")}
    if "arm_r.upper" in values:
        out[upper] = values["arm_r.upper"]
    if "arm_r.lower" in values:
        out[lower] = values["arm_r.lower"]
    return out


def hold_pose(arm: str = "arm_r") -> Vec2:
    """(upper, lower) of the hold pose for ``arm`` (arm_l mirrors arm_r's values)."""
    values = _pose_values("hold")
    return values["arm_r.upper"], values["arm_r.lower"]


def _ramp(dur: float) -> float:
    """Blend time at each end of a timed gesture: POSE_SECONDS, or a third of a short gesture."""
    if dur <= 0:
        raise ValueError(f"gesture duration must be positive, got {dur}")
    return min(POSE_SECONDS, dur / 3)


def _gesture_keys(who: str, t: float, current: Mapping[str, float], target: Mapping[str, float], dur: float) -> list[Key]:
    """Blend bones from current to target, hold, and blend back to current by t + dur."""
    ramp = _ramp(dur)
    keys: list[Key] = []
    for bone, value in target.items():
        now = float(current.get(bone, 0.0))
        ch = _rot_channel(bone)
        keys += [
            (_puppet(who), ch, t, now),
            (_puppet(who), ch, t + ramp, value),
            (_puppet(who), ch, t + dur - ramp, value),
            (_puppet(who), ch, t + dur, now),
        ]
    return keys


def _facing_toward(body: Body, x: float) -> int:
    """The facing that looks at world x (unchanged when x is straight above or below)."""
    dx = x - body.ground[0]
    if dx == 0:
        return body.facing
    return 1 if dx > 0 else -1


def _face_keys(body: Body, t: float, facing: int) -> list[Key]:
    return [] if facing == body.facing else [(_puppet(body.id), "root.facing", t, facing)]


def _root_offset(pose: str, build: str, facing: int) -> Vec2:
    off = POSES["poses"][pose].get("rootOffset", [0.0, 0.0])
    s = scale(build)
    return (facing * off[0] * s, off[1] * s)


def last_values(keys: list[Key]) -> dict[tuple[str, str], object]:
    """The final value of every (target, channel) in ``keys``: the state after the gesture."""
    out: dict[tuple[str, str], object] = {}
    for target, channel, _t, value in sorted(keys, key=lambda k: k[2]):
        out[(target, channel)] = value
    return out


# ---------------------------------------------------------------------------- poses

def pose_keys(who: str, t: float, current: Mapping[str, float], pose: str, dur: float = POSE_SECONDS) -> list[Key]:
    """Blend into a library pose: for each bone the pose lists, current at t, target at t + dur.

    Bones the pose does not list are untouched. ``stand`` returns every bone in ``current`` to 0.
    ``rootOffset`` is not handled here (see sit_keys and kneel_keys).
    """
    if pose == "stand":
        target = {bone: 0.0 for bone in current}
    else:
        target = _pose_values(pose)
    keys: list[Key] = []
    for bone, value in target.items():
        ch = _rot_channel(bone)
        keys.append((_puppet(who), ch, t, float(current.get(bone, 0.0))))
        keys.append((_puppet(who), ch, t + dur, value))
    return keys


def _posture_keys(
    body: Body, t: float, current: Mapping[str, float], pose: str, root_to: Vec2, z: float, dur: float
) -> list[Key]:
    keys = pose_keys(body.id, t, current, pose, dur)
    keys.append((_puppet(body.id), "root.position", t, [float(body.ground[0]), float(body.ground[1]), float(z)]))
    keys.append((_puppet(body.id), "root.position", t + dur, [float(root_to[0]), float(root_to[1]), float(z)]))
    return keys


def kneel_keys(body: Body, t: float, current: Mapping[str, float], z: float = ACTING_Z, dur: float = POSE_SECONDS) -> list[Key]:
    """Kneel where the puppet stands: kneel bones, and root lowered by rootOffset * scale."""
    off = _root_offset("kneel", body.build, body.facing)
    return _posture_keys(body, t, current, "kneel", (body.ground[0] + off[0], body.ground[1] + off[1]), z, dur)


def sit_keys(
    body: Body,
    t: float,
    current: Mapping[str, float],
    seat_world: Vec2 | None,
    z: float = ACTING_Z,
    dur: float = POSE_SECONDS,
) -> list[Key]:
    """Sit: on a seat socket (hips on ``seat_world``) or, with None, on the ground where it stands.

    Seated on a socket, root = (seat_x, seat_y - hipHeight * scale). On the ground, root is
    lowered by the sit pose's rootOffset * scale.
    """
    if seat_world is None:
        off = _root_offset("sit", body.build, body.facing)
        root = (body.ground[0] + off[0], body.ground[1] + off[1])
    else:
        root = (seat_world[0], seat_world[1] - RIG["hipHeight"] * scale(body.build))
    return _posture_keys(body, t, current, "sit", root, z, dur)


def stand_keys(
    body: Body, t: float, current: Mapping[str, float], stand_at: Vec2, z: float = ACTING_Z, dur: float = POSE_SECONDS
) -> list[Key]:
    """Stand up: every bone in ``current`` back to 0, root from body.ground to ``stand_at``.

    ``stand_at`` is the standing root position (ground under the hips, no rootOffset).
    """
    return _posture_keys(body, t, current, "stand", stand_at, z, dur)


# --------------------------------------------------------------------------- gestures

def wave_keys(
    body: Body,
    t: float,
    current: Mapping[str, float],
    dur: float = 1.2,
    arm: str = "arm_r",
    toward_x: float | None = None,
) -> list[Key]:
    """Wave: raise into the wave pose, two lower-arm oscillations of +-0.3, back to current.

    With ``toward_x`` the puppet first turns to face that world x if it is behind it.
    """
    ramp = _ramp(dur)
    target = _for_arm(_pose_values("wave"), arm)
    _upper, lower = arm_bones(arm)
    facing = body.facing if toward_x is None else _facing_toward(body, toward_x)
    keys = _face_keys(body, t, facing) + _gesture_keys(body.id, t, current, target, dur)
    # Two oscillations inside the held middle: +, -, +, - at fifths of the hold.
    mid = dur - 2 * ramp
    base = target[lower]
    for i, sign in enumerate((1, -1, 1, -1), start=1):
        keys.append((_puppet(body.id), _rot_channel(lower), t + ramp + mid * i / 5, base + sign * WAVE_SWING))
    return keys


def point_keys(
    body: Body, t: float, current: Mapping[str, float], target: Vec2, dur: float = 1.0, arm: str = "arm_r"
) -> list[Key]:
    """Point at a world point: turn first if it is behind, aim the point pose at it, then return.

    The upper arm aims along the shoulder-to-target direction; the lower arm keeps the point
    pose's slight bend. The shoulder is where the body's torso puts it, and the upper rotation
    is relative to the torso (CONTRACTS.md Clarification 2).
    """
    facing = _facing_toward(body, target[0])
    turned = replace(body, facing=facing)
    sh = shoulder_world(arm, turned.ground, facing, turned.build, torso=turned.torso)
    vx, vy = facing * (target[0] - sh[0]), target[1] - sh[1]
    values = _for_arm(_pose_values("point"), arm)
    upper, _lower = arm_bones(arm)
    if vx != 0 or vy != 0:
        values[upper] = math.atan2(vx, -vy) - turned.torso
    return _face_keys(body, t, facing) + _gesture_keys(body.id, t, current, values, dur)


def hug_keys(
    a: Body, b: Body, t: float, current_a: Mapping[str, float], current_b: Mapping[str, float], dur: float = 1.5
) -> list[Key]:
    """Both puppets face each other, blend into the hug pose, hold, and release by t + dur."""
    target = _pose_values("hug")
    keys: list[Key] = []
    for me, other, current in ((a, b, current_a), (b, a, current_b)):
        keys += _face_keys(me, t, _facing_toward(me, other.ground[0]))
        keys += _gesture_keys(me.id, t, current, target, dur)
    return keys


def lean_keys(body: Body, t: float, current: Mapping[str, float], toward_x: float, dur: float = 1.0) -> list[Key]:
    """Turn toward world x if needed, lean the torso forward (-0.2), then straighten."""
    facing = _facing_toward(body, toward_x)
    return _face_keys(body, t, facing) + _gesture_keys(body.id, t, current, {"torso": LEAN_TORSO}, dur)


# ------------------------------------------------------------------------ prop handling

def hold_keys(body: Body, t: float, current_arm: Vec2, arm: str = "arm_r") -> list[Key]:
    """Raise a held prop into the hold pose over POSE_SECONDS."""
    return _arm_keys(body.id, arm, t, current_arm) + _arm_keys(body.id, arm, t + POSE_SECONDS, hold_pose(arm))


def pick_up_keys(
    body: Body, t: float, current_arm: Vec2, prop: PropGeom, prop_anchor: Vec3, arm: str = "arm_r"
) -> list[Key]:
    """Reach the prop's grip (IK), take it into the hand socket, raise it into the hold pose.

    Raises OutOfReach (naming the puppet) when the grip is beyond the arm's reach.
    """
    grip = grip_from_anchor(prop, prop_anchor)
    reach = solve_body_arm(body, arm, grip)
    keys = _arm_keys(body.id, arm, t, current_arm)
    keys += _arm_keys(body.id, arm, t + REACH_SECONDS, reach)
    keys += _arm_keys(body.id, arm, t + 2 * REACH_SECONDS, hold_pose(arm))
    keys += _attach_keys(prop.id, t + REACH_SECONDS, f"puppet:{body.id}.{arm_socket(arm)}", (0.0, 0.0, 0.0))
    return keys


def drop_keys(
    body: Body, t: float, current_arm: Vec2, prop: PropGeom, surface_y: float, z: float, arm: str = "arm_r"
) -> tuple[list[Key], Vec3]:
    """Let go: the prop falls from the hand to the ground beside the feet; the arm drops to rest.

    Returns the keys and the landing anchor position (ground.x + 0.08 * facing, surface_y, z).
    """
    upper, lower = arm_bones(arm)
    hand = body_socket(body, arm_socket(arm), {upper: current_arm[0], lower: current_arm[1]})
    start = anchor_from_grip(prop, hand)
    landing: Vec3 = (body.ground[0] + DROP_OFFSET * body.facing, float(surface_y), float(z))
    keys = _arm_keys(body.id, arm, t, current_arm) + _arm_keys(body.id, arm, t + FALL_SECONDS, REST_ARM)
    keys += _attach_keys(prop.id, t, "ground", (start[0], start[1], z))
    keys.append((_prop(prop.id), "position", t + FALL_SECONDS, list(landing)))
    return keys, landing


def hand_to_keys(
    giver: Body,
    giver_arm_now: Vec2,
    taker: Body,
    taker_arm_now: Vec2,
    prop_id: str,
    t: float,
    giver_arm: str = "arm_r",
    taker_arm: str = "arm_r",
) -> list[Key]:
    """Both hands meet at the handover point; the prop changes hands; the giver's arm drops to
    rest and the taker's settles into the hold pose."""
    meet = handover_point(giver, taker, giver_arm, taker_arm)
    g = solve_body_arm(giver, giver_arm, meet)
    k = solve_body_arm(taker, taker_arm, meet)
    t_meet, t_done = t + PASS_SECONDS, t + PASS_SECONDS + RETURN_SECONDS
    keys = _arm_keys(giver.id, giver_arm, t, giver_arm_now)
    keys += _arm_keys(giver.id, giver_arm, t_meet, g)
    keys += _arm_keys(giver.id, giver_arm, t_done, REST_ARM)
    keys += _arm_keys(taker.id, taker_arm, t, taker_arm_now)
    keys += _arm_keys(taker.id, taker_arm, t_meet, k)
    keys += _arm_keys(taker.id, taker_arm, t_done, hold_pose(taker_arm))
    keys += _attach_keys(prop_id, t_meet, f"puppet:{taker.id}.{arm_socket(taker_arm)}", (0.0, 0.0, 0.0))
    return keys


def put_on_keys(
    body: Body, t: float, current_arm: Vec2, prop: PropGeom, wearer: Body, arm: str = "arm_r"
) -> list[Key]:
    """Put a held prop on a head (``wearer`` may be ``body`` itself).

    The hand carries the grip to where it sits when the prop's anchor rests on the wearer's head
    socket, the prop is parented to ``puppet:<wearer>.head`` (position [0, 0, 0]) at
    t + REACH_SECONDS, and the arm returns to rest by t + 2 * REACH_SECONDS.

    When that grip point is out of reach (a grip above the anchor puts it above the head), the
    hand goes as far as it can: PUT_ON_REACH_FRACTION of full reach along the shoulder-to-target
    line. There the prop is parented to the head with a position key holding the offset from the
    head socket to where the hand leaves the anchor, and settles to [0, 0, 0] over
    PUT_ON_SETTLE_SECONDS. The offset is in stage axes: the stage keeps a socket-parented prop
    upright and unmirrored (PropActor ``heldOrientation="upright"``, props/rig.ts ``socketFrame``
    drops the socket's rotation and its facing mirror), so the anchor lands on head + offset for
    either facing. If that offset is longer than PUT_ON_SETTLE_MAX (a wearer out of arm's reach,
    a large prop held by its top) it raises the OutOfReach of the exact grip point, naming the
    puppet.
    """
    head = body_socket(wearer, "head", {})
    target = grip_from_anchor(prop, head)
    t_on = t + REACH_SECONDS
    offset: Vec2 | None = None
    try:
        reach = solve_body_arm(body, arm, target)
    except OutOfReach as exact:
        hand = _reachable_toward(body, arm, target, PUT_ON_REACH_FRACTION)
        anchor = anchor_from_grip(prop, hand)
        offset = (anchor[0] - head[0], anchor[1] - head[1])
        if math.hypot(*offset) > PUT_ON_SETTLE_MAX:
            raise exact from None
        reach = solve_body_arm(body, arm, hand)
    keys = _arm_keys(body.id, arm, t, current_arm)
    keys += _arm_keys(body.id, arm, t_on, reach)
    keys += _arm_keys(body.id, arm, t + 2 * REACH_SECONDS, REST_ARM)
    parent = f"puppet:{wearer.id}.head"
    if offset is None:
        keys += _attach_keys(prop.id, t_on, parent, (0.0, 0.0, 0.0))
    else:
        keys += _attach_keys(prop.id, t_on, parent, (offset[0], offset[1], 0.0))
        keys.append((_prop(prop.id), "position", t_on + PUT_ON_SETTLE_SECONDS, [0.0, 0.0, 0.0]))
    return keys


def _reachable_toward(body: Body, arm: str, target: Vec2, fraction: float) -> Vec2:
    """The point ``fraction`` of the arm's full reach from the shoulder, toward ``target``.

    The shoulder is where the body's torso puts it (as the IK solver has it). ``target`` must not
    be the shoulder itself; callers use this only for a target beyond reach.
    """
    sh = shoulder_world(arm, body.ground, body.facing, body.build, torso=body.torso)
    dx, dy = target[0] - sh[0], target[1] - sh[1]
    f = fraction * arm_reach(body.build, arm) / math.hypot(dx, dy)
    return (sh[0] + dx * f, sh[1] + dy * f)


def place_on_keys(
    body: Body, t: float, current_arm: Vec2, prop: PropGeom, dest_anchor: Vec3, parent: str, arm: str = "arm_r"
) -> list[Key]:
    """Set a held prop down with its anchor at ``dest_anchor`` (world) and let go.

    ``parent`` is ``"ground"`` (position = the world anchor) or ``"prop:<id>.<socket>"``
    (position = [0, 0, 0] at the socket, whose world position is ``dest_anchor``).
    """
    if parent != "ground" and not (parent.startswith("prop:") and "." in parent):
        raise ValueError(f"place_on parent must be 'ground' or 'prop:<id>.<socket>', got {parent!r}")
    reach = solve_body_arm(body, arm, grip_from_anchor(prop, dest_anchor))
    position = dest_anchor if parent == "ground" else (0.0, 0.0, 0.0)
    keys = _arm_keys(body.id, arm, t, current_arm)
    keys += _arm_keys(body.id, arm, t + REACH_SECONDS, reach)
    keys += _arm_keys(body.id, arm, t + 2 * REACH_SECONDS, REST_ARM)
    keys += _attach_keys(prop.id, t + REACH_SECONDS, parent, position)
    return keys


def state_keys(prop_id: str, t: float, state: str) -> list[Key]:
    """A prop state change (open, closed, ...): one step key."""
    return [(_prop(prop_id), "state", t, state)]
