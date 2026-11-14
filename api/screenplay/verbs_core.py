"""Verb handlers for the demo path: enter, walk_to, run_to, pose, pick_up, hold, drop, hand_to.

Handler contract (shared with the other ``verbs_*`` modules):

    def apply_<verb>(world, tracks, action, t, action_start, locate) -> float

- ``t`` is the timeline time the action starts (already cue-retimed); ``action_start`` is the
  timeline time of the shot's t=0. On entry ``world.now == round(t - action_start, 6)``.
- Returns the timeline time when ``action.who`` is free again (>= t).
- The compiler runs ``world.check_free(who)`` before the call and ``world.occupy(who, ...)``
  after it. A handler checks and occupies any SECOND puppet itself (hand_to's taker).
- Atomic: every check (StateError, ik.OutOfReach) happens before any world mutation or key
  emission. Keys are computed into a list, then the world is mutated, then ``tracks.extend``.
"""

from __future__ import annotations

from collections.abc import Callable

from screenplay import body as B
from screenplay import ik, motion
from screenplay.locate import (
    Locate,
    arm_at,
    arm_of_hand,
    body_at,
    bones_at,
    held_arm_bones,
    mark_of,
    puppet_root,
)
from screenplay.schema import Action
from screenplay.state import SEATED_POSES, StateError, World, fmt_t
from screenplay.tracks import Key, TrackSet

Vec3 = tuple[float, float, float]
Handler = Callable[[World, TrackSet, Action, float, float, Locate], float]

AUTO_WALK_REACH = 0.6  # pick_up walks to this fraction of the arm's reach short of the grip
POSES: dict[str, dict] = ik.POSES["poses"]


# ------------------------------------------------------------------------- helpers

def _require(value: str | None, name: str, action: Action) -> str:
    if value is None:
        raise StateError("missing_param", f'it needs "{name}"')
    return value


def _who(world: World, action: Action) -> str:
    who = _require(action.who, "who", action)
    if who not in world.people:
        raise StateError("unknown_person", f"{who} is not in the cast")
    return who


def _what(world: World, action: Action) -> str:
    what = _require(action.what, "what", action)
    if what not in world.props:
        raise StateError("unknown_prop", f"{what} is not in the screenplay's props")
    return what


def _not_seated(world: World, who: str) -> None:
    seat = world.people[who].seat
    if seat is not None:
        raise StateError("seated", f"{who} is seated in {seat[0]}.{seat[1]}")


def _sign(v: float) -> int:
    return 1 if v > 1e-9 else -1 if v < -1e-9 else 0


def _stand_up_keys(world: World, tracks: TrackSet, who: str, t: float) -> tuple[list[Key], float]:
    """A puppet sitting or kneeling on the floor gets up before it moves. Held arms keep the
    hold pose. Returns (keys, when it is standing)."""
    p = world.people[who]
    if p.seat is not None or p.pose not in SEATED_POSES:
        return [], t
    skip = held_arm_bones(world, who)
    current = {b: v for b, v in bones_at(tracks, who, t).items() if b not in skip}
    keys = B.stand_keys(body_at(world, tracks, who, t), t, current, (p.pos[0], p.pos[1]), z=p.pos[2])
    return keys, t + B.POSE_SECONDS


def _travel_keys(world: World, tracks: TrackSet, who: str, t: float, dest: Vec3, gait: str) -> tuple[list[Key], float, int]:
    """Stand up if needed, then walk or run to ``dest`` with held arms kept in the hold pose.
    Returns (keys, arrival, facing after)."""
    p = world.people[who]
    keys, t0 = _stand_up_keys(world, tracks, who, t)
    start = p.pos if keys else puppet_root(world, tracks, who, t)
    move, t1, facing = motion.locomotion_keys(
        who, p.build, t0, start, dest, p.facing, gait,  # type: ignore[arg-type]
        skip_bones=sorted(held_arm_bones(world, who)),
    )
    return keys + move, t1, facing


def _blend_back(keys: list[Key], t: float, t_back: float) -> list[Key]:
    """Hold every channel ``keys`` animate at its final value until ``t_back``, then blend back
    over POSE_SECONDS to the value it had at ``t``."""
    first: dict[tuple[str, str], object] = {}
    for target, channel, kt, value in keys:
        if kt == t:
            first.setdefault((target, channel), value)
    out: list[Key] = []
    for (target, channel), last in B.last_values(keys).items():
        if (target, channel) not in first:
            continue
        out.append((target, channel, t_back, last))
        out.append((target, channel, t_back + B.POSE_SECONDS, first[(target, channel)]))
    return out


# ------------------------------------------------------------------------ movement

def apply_enter(world: World, tracks: TrackSet, action: Action, t: float, action_start: float, locate: Locate) -> float:
    """Come on from offstage (``from``, default the side nearest ``to``) and walk to ``to``."""
    who = _who(world, action)
    to = mark_of(locate, _require(action.to, "to", action), t)
    src = mark_of(locate, action.from_ or motion.nearest_offstage(to[0]), t)
    _not_seated(world, who)
    if world.on_stage(who):
        raise StateError("already_on_stage", f"{who} is already on stage; use walk_to")
    p = world.people[who]
    keys, t1, facing = motion.enter_keys(
        who, p.build, t, src, to, p.facing, "walk", skip_bones=sorted(held_arm_bones(world, who))
    )
    world.move(who, to, facing)
    tracks.extend(keys)
    return t1


def _locomote(world: World, tracks: TrackSet, action: Action, t: float, locate: Locate, gait: str) -> float:
    who = _who(world, action)
    dest = mark_of(locate, _require(action.to, "to", action), t)
    _not_seated(world, who)
    keys, t1, facing = _travel_keys(world, tracks, who, t, dest, gait)
    world.move(who, dest, facing)
    tracks.extend(keys)
    return t1


def apply_walk_to(world: World, tracks: TrackSet, action: Action, t: float, action_start: float, locate: Locate) -> float:
    """Walk to a mark at the walk speed, with the walk cycle."""
    return _locomote(world, tracks, action, t, locate, "walk")


def apply_run_to(world: World, tracks: TrackSet, action: Action, t: float, action_start: float, locate: Locate) -> float:
    """Run to a mark at the run speed, with the run cycle."""
    return _locomote(world, tracks, action, t, locate, "run")


# ---------------------------------------------------------------------------- body

def apply_pose(world: World, tracks: TrackSet, action: Action, t: float, action_start: float, locate: Locate) -> float:
    """Blend into a library pose over POSE_SECONDS. With ``dur``, hold it and blend back."""
    who = _who(world, action)
    pose = _require(action.pose, "pose", action)
    if pose not in POSES:
        raise StateError("unknown_pose", f"{pose} is not a pose; poses are: {', '.join(sorted(POSES))}")
    dur = action.dur
    if dur is not None and dur <= 0:
        raise StateError("bad_dur", f"dur must be positive, not {fmt_t(dur)}")
    p = world.people[who]
    if pose in (*SEATED_POSES, "stand"):
        _not_seated(world, who)

    current = bones_at(tracks, who, t)
    root_now = puppet_root(world, tracks, who, t)
    z = root_now[2]
    standing = (p.pos[0], p.pos[1])
    if pose in SEATED_POSES:
        upright = ik.Body(who, standing, p.facing, p.build)
        gen = (B.sit_keys(upright, t, current, None, z=z) if pose == "sit"
               else B.kneel_keys(upright, t, current, z=z))
        # Start from where the root really is (it may already be lowered by another floor pose).
        keys = [(k[0], k[1], k[2], list(root_now)) if k[1] == "root.position" and k[2] == t else k for k in gen]
    elif pose == "stand":
        skip = held_arm_bones(world, who)
        rest = {b: v for b, v in current.items() if b not in skip}
        if p.pose in SEATED_POSES:
            keys = B.stand_keys(body_at(world, tracks, who, t), t, rest, standing, z=z)
        else:
            keys = B.pose_keys(who, t, rest, "stand")
    else:
        keys = B.pose_keys(who, t, current, pose)

    if dur is None:
        world.set_pose(who, pose)
        end = t + B.POSE_SECONDS
    else:  # the pose is held for dur, then the puppet goes back to what it was doing
        keys += _blend_back(keys, t, t + dur)
        end = t + dur + B.POSE_SECONDS
    tracks.extend(keys)
    return end


# --------------------------------------------------------------------------- props

def apply_pick_up(world: World, tracks: TrackSet, action: Action, t: float, action_start: float, locate: Locate) -> float:
    """Reach the prop's grip and take it into the free hand (hand_r first), then raise it into
    the hold pose. Out of reach: walk toward it first, to AUTO_WALK_REACH of the arm's reach."""
    who = _who(world, action)
    what = _what(world, action)
    hand = world.copy().pick_up(who, what)  # validation only: the real world is untouched
    if not world.on_stage(who):
        raise StateError("offstage", f"{who} is offstage")
    anchor = world.props[what].world_pos
    if anchor is None:
        raise StateError("not_on_stage", f"{what} is not on stage")

    p = world.people[who]
    arm = arm_of_hand(hand)
    geom = B.PropGeom.from_prop(world.catalog[what])
    grip = B.grip_from_anchor(geom, anchor)
    here = puppet_root(world, tracks, who, t)
    d = _sign(grip[0] - here[0])
    facing = d or p.facing

    keys: list[Key] = []
    t_reach = t
    ground = (here[0], here[1])
    current = arm_at(tracks, who, arm, t)
    walk_to: Vec3 | None = None
    if not ik.can_reach(arm, grip, ground, facing, p.build):
        reach = ik.arm_reach(p.build, arm)
        if d != 0 and abs(grip[0] - here[0]) > AUTO_WALK_REACH * reach:
            _not_seated(world, who)
            walk_to = (grip[0] - d * AUTO_WALK_REACH * reach, p.pos[1], p.pos[2])
            keys, t_reach, facing = _travel_keys(world, tracks, who, t, walk_to, "walk")
            ground = (walk_to[0], walk_to[1])
            current = (0.0, 0.0)  # the walk cycle ends with the free arm at rest
    if walk_to is None and facing != p.facing:
        keys += [(f"puppet:{who}", "root.facing", t, facing)]
    keys += B.pick_up_keys(ik.Body(who, ground, facing, p.build), t_reach, current, geom, anchor, arm)

    if walk_to is not None:
        world.move(who, walk_to, facing)
    else:
        world.set_facing(who, facing)
    world.pick_up(who, what, hand)
    tracks.extend(keys)
    return t_reach + 2 * B.REACH_SECONDS


def apply_hold(world: World, tracks: TrackSet, action: Action, t: float, action_start: float, locate: Locate) -> float:
    """Raise a prop already in hand into the hold pose."""
    who = _who(world, action)
    what = _what(world, action)
    arm = arm_of_hand(world.hold(who, what))
    keys = B.hold_keys(body_at(world, tracks, who, t), t, arm_at(tracks, who, arm, t), arm)
    tracks.extend(keys)
    return t + B.POSE_SECONDS


def apply_drop(world: World, tracks: TrackSet, action: Action, t: float, action_start: float, locate: Locate) -> float:
    """Let go: the prop falls to the dropper's standing surface, DROP_OFFSET in front of the feet."""
    who = _who(world, action)
    what = _what(world, action)
    arm = arm_of_hand(world.hold(who, what))  # validation: who must hold it in a hand
    p = world.people[who]
    z = puppet_root(world, tracks, who, t)[2]
    keys, landing = B.drop_keys(
        body_at(world, tracks, who, t), t, arm_at(tracks, who, arm, t),
        B.PropGeom.from_prop(world.catalog[what]), surface_y=p.pos[1], z=z, arm=arm,
    )
    world.drop(who, what, landing)
    tracks.extend(keys)
    return t + B.FALL_SECONDS


def apply_hand_to(world: World, tracks: TrackSet, action: Action, t: float, action_start: float, locate: Locate) -> float:
    """Pass a held prop to another puppet: both hands meet at the handover point. The two face
    each other first. No auto-walk: OutOfReach when the hands cannot meet."""
    who = _who(world, action)
    what = _what(world, action)
    to = _require(action.to, "to", action)
    if to not in world.people:
        raise StateError("unknown_person", f"{to} is not in the cast")
    taker_hand = world.copy().hand_to(who, what, to)  # validation only
    world.check_free(to)
    holder = world.holder(what)
    assert holder is not None  # hand_to validated that who holds it
    giver_arm, taker_arm = arm_of_hand(holder[1]), arm_of_hand(taker_hand)

    g, k = body_at(world, tracks, who, t), body_at(world, tracks, to, t)
    g_facing = _sign(k.ground[0] - g.ground[0]) or g.facing
    k_facing = _sign(g.ground[0] - k.ground[0]) or k.facing
    keys: list[Key] = []
    for body, facing in ((g, g_facing), (k, k_facing)):
        if facing != body.facing:
            keys.append((f"puppet:{body.id}", "root.facing", t, facing))
    g = ik.Body(who, g.ground, g_facing, g.build)
    k = ik.Body(to, k.ground, k_facing, k.build)
    keys += B.hand_to_keys(
        g, arm_at(tracks, who, giver_arm, t), k, arm_at(tracks, to, taker_arm, t), what, t, giver_arm, taker_arm
    )
    end = t + B.PASS_SECONDS + B.RETURN_SECONDS

    world.set_facing(who, g_facing)
    world.set_facing(to, k_facing)
    world.hand_to(who, what, to)
    world.occupy(to, round(end - action_start, 6), f"taking {what}", at=world.now)
    tracks.extend(keys)
    return end


HANDLERS: dict[str, Handler] = {
    "enter": apply_enter,
    "walk_to": apply_walk_to,
    "run_to": apply_run_to,
    "pose": apply_pose,
    "pick_up": apply_pick_up,
    "hold": apply_hold,
    "drop": apply_drop,
    "hand_to": apply_hand_to,
}
