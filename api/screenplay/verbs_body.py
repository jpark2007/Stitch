"""Body verbs: wave, point_at, sit_on, stand_up, kneel, hug, lean_toward.

Each handler wires a merged key generator from ``screenplay.body`` (and, for walking up to a
seat or a hug partner, ``screenplay.motion.locomotion_keys``) to the World state. No gesture
math lives here. ``pose`` belongs to compiler-core.

Handler contract (shared with the other verb modules)::

    def apply_<verb>(world, tracks, action, t, action_start, locate) -> float

- ``t`` is the timeline time the action starts (already cue-retimed); ``action_start`` is the
  timeline time of the shot's t=0. On entry ``world.now == round(t - action_start, 6)``.
- The return value is the timeline time when ``action.who`` is free again (>= t).
- The compiler runs ``world.check_free(who)`` before and ``world.occupy(who, ...)`` after. A
  handler checks and occupies any second puppet itself (hug's target), in shot time.
- Atomic: every check (StateError, ik.OutOfReach) happens before the world is mutated or a key
  is emitted. Keys are computed into a list, the world is mutated, then ``tracks.extend``.

Geometry comes from the tracks: a puppet's root is its ``root.position`` track at t when there
is one, else ``world.people[id].pos``; bones are their ``<bone>.rot`` tracks at t, else 0.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping

from screenplay import body as gen
from screenplay import motion
from screenplay.ik import RIG, Body, arm_bones
from screenplay.schema import Action
from screenplay.state import HEAD, SEATED_POSES, StateError, World
from screenplay.tracks import TrackSet

Vec3 = tuple[float, float, float]
Locate = Callable[[str, float], Vec3]
Handler = Callable[[World, TrackSet, Action, float, float, Locate], float]
Key = tuple[str, str, float, object]

BONES: tuple[str, ...] = tuple(RIG["bones"])

WAVE_SECONDS = 1.2
POINT_SECONDS = 1.0
HUG_SECONDS = 1.5
LEAN_SECONDS = 1.0

SEAT_WALK_EPS = 0.05  # walk to a seat first when the root is farther than this from it
HUG_GAP = 0.18        # stage units between two hugging roots
HUG_SLACK = 0.02      # no walk when already within HUG_GAP + HUG_SLACK

__all__ = [
    "BONES",
    "HANDLERS",
    "HUG_GAP",
    "HUG_SLACK",
    "SEAT_WALK_EPS",
    "Handler",
    "Locate",
    "apply_hug",
    "apply_kneel",
    "apply_lean_toward",
    "apply_point_at",
    "apply_sit_on",
    "apply_stand_up",
    "apply_wave",
]


# ---------------------------------------------------------------------------- helpers

def _puppet_target(who: str) -> str:
    return f"puppet:{who}"


def _who(world: World, action: Action) -> str:
    who = action.who
    if who is None or who not in world.people:
        raise StateError("unknown_id", f"{who} is not in the cast")
    return who


def _on_stage(world: World, who: str) -> None:
    """Gestures and postures need the puppet on stage; ``enter`` brings it on (as verbs_core)."""
    if not world.on_stage(who):
        raise StateError("offstage", f"{who} is offstage; use enter first")


def _require(action: Action, field: str) -> str:
    value = getattr(action, field)
    if value is None:
        raise StateError("missing_param", f"{action.do} needs {field}")
    return value


def _dur(action: Action, default: float) -> float:
    dur = default if action.dur is None else float(action.dur)
    if not math.isfinite(dur) or dur <= 0:
        raise StateError("bad_dur", f"dur must be a positive number of seconds, got {action.dur}")
    return dur


def _root(world: World, tracks: TrackSet, who: str, t: float) -> Vec3:
    v = tracks.value_at(_puppet_target(who), "root.position", t)
    if v is None:
        return world.people[who].pos
    return (float(v[0]), float(v[1]), float(v[2]))


def _rots_now(tracks: TrackSet, who: str, t: float) -> dict[str, float]:
    out: dict[str, float] = {}
    for bone in BONES:
        v = tracks.value_at(_puppet_target(who), f"{bone}.rot", t)
        out[bone] = 0.0 if v is None else float(v)
    return out


def _body(world: World, tracks: TrackSet, who: str, t: float) -> Body:
    root = _root(world, tracks, who, t)
    p = world.people[who]
    return Body(who, (root[0], root[1]), p.facing, p.build)


def _gesture_arm(world: World, who: str) -> str:
    """arm_r, unless hand_r holds something and hand_l is free. Both full: arm_r keeps its prop."""
    holding = world.people[who].holding
    if "hand_r" in holding and "hand_l" not in holding:
        return "arm_l"
    return "arm_r"


def _held_arm_bones(world: World, who: str) -> set[str]:
    """Bones of every arm whose hand holds a prop: walks and stand-ups leave them alone."""
    out: set[str] = set()
    for arm in RIG["ik"]:
        upper, lower = arm_bones(arm)
        if RIG["ik"][arm]["socket"] in world.people[who].holding:
            out |= {upper, lower}
    return out


def _settle_rots(world: World, rots: Mapping[str, float], who: str) -> dict[str, float]:
    """The bones a stand-up returns to 0: every bent bone except the arms holding props."""
    keep = _held_arm_bones(world, who)
    return {b: v for b, v in rots.items() if v != 0.0 and b not in keep}


def _final_facing(keys: list[Key], who: str, facing: int) -> int:
    for target, channel, _t, value in sorted(keys, key=lambda k: k[2]):
        if target == _puppet_target(who) and channel == "root.facing":
            facing = int(value)  # type: ignore[call-overload]
    return facing


def _walk(world: World, who: str, t: float, start: Vec3, end: Vec3, facing: int) -> tuple[list[Key], float, int]:
    """motion.locomotion_keys, leaving the arms that hold props in their hold pose."""
    return motion.locomotion_keys(
        who, world.people[who].build, t, start, end, facing, "walk",
        skip_bones=sorted(_held_arm_bones(world, who)),
    )


def _after_walk(rots: Mapping[str, float], keys: list[Key], who: str) -> dict[str, float]:
    """Bone rotations once a walk has finished: every walked bone ends at its last key (0)."""
    out = dict(rots)
    for (target, channel), value in gen.last_values(keys).items():
        if target == _puppet_target(who) and channel.endswith(".rot"):
            out[channel[: -len(".rot")]] = float(value)  # type: ignore[arg-type]
    return out


def _commit_facing(world: World, keys: list[Key], *bodies: Body) -> None:
    for b in bodies:
        facing = _final_facing(keys, b.id, b.facing)
        if facing != world.people[b.id].facing:
            world.set_facing(b.id, facing)


# ---------------------------------------------------------------------------- gestures

def apply_wave(world: World, tracks: TrackSet, action: Action, t: float, action_start: float,
               locate: Locate) -> float:
    """wave(target?, dur=1.2): raise an arm and wave; turn first to face ``target`` if behind."""
    who = _who(world, action)
    _on_stage(world, who)
    dur = _dur(action, WAVE_SECONDS)
    toward_x = None if action.target is None else locate(action.target, t)[0]
    me = _body(world, tracks, who, t)
    keys = gen.wave_keys(me, t, _rots_now(tracks, who, t), dur, _gesture_arm(world, who), toward_x)
    _commit_facing(world, keys, me)
    tracks.extend(keys)
    return t + dur


def apply_point_at(world: World, tracks: TrackSet, action: Action, t: float, action_start: float,
                   locate: Locate) -> float:
    """point_at(target, dur=1.0): aim the point pose at the target's stage position."""
    who = _who(world, action)
    _on_stage(world, who)
    target = _require(action, "target")
    dur = _dur(action, POINT_SECONDS)
    at = locate(target, t)
    me = _body(world, tracks, who, t)
    keys = gen.point_keys(me, t, _rots_now(tracks, who, t), (at[0], at[1]), dur, _gesture_arm(world, who))
    _commit_facing(world, keys, me)
    tracks.extend(keys)
    return t + dur


def apply_lean_toward(world: World, tracks: TrackSet, action: Action, t: float, action_start: float,
                      locate: Locate) -> float:
    """lean_toward(target, dur=1.0): face the target and lean the torso toward it."""
    who = _who(world, action)
    _on_stage(world, who)
    target = _require(action, "target")
    dur = _dur(action, LEAN_SECONDS)
    x = locate(target, t)[0]
    me = _body(world, tracks, who, t)
    keys = gen.lean_keys(me, t, _rots_now(tracks, who, t), x, dur)
    _commit_facing(world, keys, me)
    tracks.extend(keys)
    return t + dur


# ---------------------------------------------------------------------------- postures

def _not_standing(world: World, who: str) -> None:
    """Sitting on the floor or kneeling (no seat): stand_up comes first."""
    p = world.people[who]
    if p.seat is None and p.pose in SEATED_POSES:
        what = "kneeling" if p.pose == "kneel" else "sitting on the floor"
        raise StateError("not_standing", f"{who} is {what}; stand_up first")


def apply_sit_on(world: World, tracks: TrackSet, action: Action, t: float, action_start: float,
                 locate: Locate) -> float:
    """sit_on(what, socket?): walk to the seat if needed, then sit with the hips on the socket."""
    who = _who(world, action)
    what = _require(action, "what")
    # World.sit_on validates (unknown_id, vehicle_seat, already_seated, no_seat, unknown_socket,
    # socket_taken) on a copy, so the real world is untouched until every check has passed.
    socket = world.copy().sit_on(who, what, action.socket)
    _on_stage(world, who)
    _not_standing(world, who)
    holder = world.holder(what)
    if holder is not None:
        verb = "wearing" if holder[1] == HEAD else "holding"
        raise StateError("held", f"{holder[0]} is {verb} it")
    seat = world.socket_world(what, socket)
    anchor = world.props[what].world_pos
    if seat is None or anchor is None:
        raise StateError("not_on_stage", f"{what} is not on stage")

    p = world.people[who]
    root = _root(world, tracks, who, t)
    stand_at: Vec3 = (seat[0], anchor[1], seat[2])  # the floor under the seat
    rots = _rots_now(tracks, who, t)
    keys: list[Key] = []
    t_sit, facing, ground = t, p.facing, (root[0], root[1])
    if math.hypot(seat[0] - root[0], seat[2] - root[2]) > SEAT_WALK_EPS:
        walk, t_sit, facing = _walk(world, who, t, root, stand_at, p.facing)
        keys += walk
        rots = _after_walk(rots, walk, who)
        ground = (stand_at[0], stand_at[1])
    me = Body(who, ground, facing, p.build)
    keys += gen.sit_keys(me, t_sit, rots, (seat[0], seat[1]), z=seat[2])

    world.move(who, stand_at, facing=facing)
    world.sit_on(who, what, socket)
    tracks.extend(keys)
    return t_sit + gen.POSE_SECONDS


def apply_stand_up(world: World, tracks: TrackSet, action: Action, t: float, action_start: float,
                   locate: Locate) -> float:
    """stand_up: from a seat onto the floor in front of it, or up from kneeling / the floor."""
    who = _who(world, action)
    world.copy().stand_up(who)  # validates: not_sitting, seated_vehicle
    p = world.people[who]
    stand_at: Vec3 = p.pos
    if p.seat is not None:
        what, sock = p.seat
        seat = world.socket_world(what, sock)
        anchor = world.props[what].world_pos
        if seat is not None and anchor is not None:
            stand_at = (seat[0], anchor[1], seat[2])
    me = _body(world, tracks, who, t)
    current = _settle_rots(world, _rots_now(tracks, who, t), who)
    keys = gen.stand_keys(me, t, current, (stand_at[0], stand_at[1]), z=stand_at[2])

    world.stand_up(who)
    world.move(who, stand_at)
    tracks.extend(keys)
    return t + gen.POSE_SECONDS


def apply_kneel(world: World, tracks: TrackSet, action: Action, t: float, action_start: float,
                locate: Locate) -> float:
    """kneel(dur?): kneel where the puppet stands; with dur, stand back up at t + dur."""
    who = _who(world, action)
    dur = None if action.dur is None else _dur(action, 0.0)
    world.copy().kneel(who)  # validates: seated
    _on_stage(world, who)
    p = world.people[who]
    if p.pose == "kneel":
        raise StateError("already_kneeling", f"{who} is already kneeling")
    _not_standing(world, who)

    root = _root(world, tracks, who, t)
    me = _body(world, tracks, who, t)
    rots = _rots_now(tracks, who, t)
    blend = gen.POSE_SECONDS if dur is None else min(gen.POSE_SECONDS, dur)
    keys = gen.kneel_keys(me, t, rots, z=root[2], dur=blend)
    end = t + blend
    if dur is not None:
        knelt = gen.last_values(keys)
        down = knelt[(_puppet_target(who), "root.position")]
        bent = dict(rots)
        for (target, channel), value in knelt.items():
            if target == _puppet_target(who) and channel.endswith(".rot"):
                bent[channel[: -len(".rot")]] = float(value)  # type: ignore[arg-type]
        low = Body(who, (down[0], down[1]), me.facing, me.build)  # type: ignore[index]
        current = _settle_rots(world, bent, who)
        keys += gen.stand_keys(low, t + dur, current, me.ground, z=root[2])
        end = t + dur + gen.POSE_SECONDS

    world.kneel(who)
    if dur is not None:
        world.stand_up(who)
    tracks.extend(keys)
    return end


# ---------------------------------------------------------------------------- people

def apply_hug(world: World, tracks: TrackSet, action: Action, t: float, action_start: float,
              locate: Locate) -> float:
    """hug(target, dur=1.5): walk up to the target if needed, then both puppets hug.

    The target must be on stage and free; it is occupied ('hugging') until the hug ends.
    """
    who = _who(world, action)
    other = _require(action, "target")
    dur = _dur(action, HUG_SECONDS)
    world.hug_check(who, other)  # unknown_id, hug_self, offstage, seated
    world.check_free(other)

    a_root = _root(world, tracks, who, t)
    b_root = _root(world, tracks, other, t)
    a = world.people[who]
    rots_a = _rots_now(tracks, who, t)
    keys: list[Key] = []
    t_hug, facing, moved_to = t, a.facing, None
    dx = b_root[0] - a_root[0]
    if abs(dx) > HUG_GAP + HUG_SLACK:
        _not_standing(world, who)  # a kneeling puppet cannot walk over
        side = 1 if dx > 0 else -1
        goal: Vec3 = (b_root[0] - side * HUG_GAP, b_root[1], b_root[2])
        walk, t_hug, facing = _walk(world, who, t, a_root, goal, a.facing)
        keys += walk
        rots_a = _after_walk(rots_a, walk, who)
        moved_to = goal
    me = Body(who, (moved_to or a_root)[:2], facing, a.build)  # type: ignore[arg-type]
    them = _body(world, tracks, other, t_hug)
    keys += gen.hug_keys(me, them, t_hug, rots_a, _rots_now(tracks, other, t_hug), dur)
    end = t_hug + dur

    if moved_to is not None:
        world.move(who, moved_to, facing=facing)
    _commit_facing(world, keys, me, them)
    world.occupy(other, round(end - action_start, 6), "hugging")
    tracks.extend(keys)
    return end


HANDLERS: dict[str, Handler] = {
    "wave": apply_wave,
    "point_at": apply_point_at,
    "sit_on": apply_sit_on,
    "stand_up": apply_stand_up,
    "kneel": apply_kneel,
    "hug": apply_hug,
    "lean_toward": apply_lean_toward,
}
