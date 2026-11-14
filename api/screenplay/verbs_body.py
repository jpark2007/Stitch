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
from collections.abc import Mapping

from screenplay import body as gen
from screenplay import motion
from screenplay.effects import STAGE_EDGE
from screenplay.ik import RIG, Body
from screenplay.locate import Locate, bones_at, gesture_arm, held_arm_bones, puppet_root, puppet_target
from screenplay.schema import Action
from screenplay.state import HEAD, PLACE_WITH_PROPSTART, SEATED_POSES, StateError, World
from screenplay.tracks import TrackSet
from screenplay.verb_helpers import Handler, Key, Vec3, require_on_stage, require_param, require_person

BONES: tuple[str, ...] = tuple(RIG["bones"])

WAVE_SECONDS = 1.2
POINT_SECONDS = 1.0
HUG_SECONDS = 1.5
LEAN_SECONDS = 1.0

SEAT_WALK_EPS = 0.05  # walk to a seat first when the root is farther than this from it
HUG_GAP = 0.18        # stage units between two hugging roots
HUG_SLACK = 0.02      # no walk when already within HUG_GAP + HUG_SLACK
# A puppet that gets up from a seat (stand_up) or out of a vehicle (get_out) whose spot lies past
# the stage edge (|x| > STAGE_EDGE) steps inward to this far inside the edge (dismount_step).
DISMOUNT_EDGE_MARGIN = 0.05

__all__ = [
    "BONES",
    "DISMOUNT_EDGE_MARGIN",
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
    "dismount_step",
    "require_gesture_on_stage",
    "seated_world_pos",
]


# ---------------------------------------------------------------------------- helpers

# Shared checks and geometry come from verb_helpers (require_person: unknown_id;
# require_on_stage: "<who> is offstage; use enter first"; require_param: "<verb> needs <field>")
# and locate (puppet_root, bones_at, gesture_arm, held_arm_bones, puppet_target).

def _dur(action: Action, default: float) -> float:
    # Body verbs' own bad_dur wording; verbs_core's pose says "dur must be positive, not <dur>".
    dur = default if action.dur is None else float(action.dur)
    if not math.isfinite(dur) or dur <= 0:
        raise StateError("bad_dur", f"dur must be a positive number of seconds, got {action.dur}")
    return dur


def _body(world: World, tracks: TrackSet, who: str, t: float) -> Body:
    # Intentionally not locate.body_at: the body verbs build an upright Body (torso 0), and
    # body_at carries the torso track, which would change their gestures' geometry.
    root = puppet_root(world, tracks, who, t)
    p = world.people[who]
    return Body(who, (root[0], root[1]), p.facing, p.build)


def _settle_rots(world: World, rots: Mapping[str, float], who: str) -> dict[str, float]:
    """The bones a stand-up returns to 0: every bent bone except the arms holding props."""
    keep = held_arm_bones(world, who)
    return {b: v for b, v in rots.items() if v != 0.0 and b not in keep}


def _final_facing(keys: list[Key], who: str, facing: int) -> int:
    for target, channel, _t, value in sorted(keys, key=lambda k: k[2]):
        if target == puppet_target(who) and channel == "root.facing":
            facing = int(value)  # type: ignore[call-overload]
    return facing


def _walk(world: World, who: str, t: float, start: Vec3, end: Vec3, facing: int) -> tuple[list[Key], float, int]:
    """motion.locomotion_keys, leaving the arms that hold props in their hold pose."""
    return motion.locomotion_keys(
        who, world.people[who].build, t, start, end, facing, "walk",
        skip_bones=sorted(held_arm_bones(world, who)),
        speed_scale=world.people[who].speed_scale,
    )


def _after_walk(rots: Mapping[str, float], keys: list[Key], who: str) -> dict[str, float]:
    """Bone rotations once a walk has finished: every walked bone ends at its last key (0)."""
    out = dict(rots)
    for (target, channel), value in gen.last_values(keys).items():
        if target == puppet_target(who) and channel.endswith(".rot"):
            out[channel[: -len(".rot")]] = float(value)  # type: ignore[arg-type]
    return out


def dismount_step(world: World, who: str, t: float, stand_at: Vec3, seat_x: float,
                  facing: int) -> tuple[list[Key], float, Vec3, int]:
    """The short step inward after getting up from a seat (or out of a vehicle) by the edge.

    ``stand_at`` is where the puppet stands once up, at timeline time ``t``; ``seat_x`` is the x
    of the seat's prop (its anchor). When ``stand_at`` lies past the stage edge (|x| >
    STAGE_EDGE) while the prop stands on stage (|seat_x| <= STAGE_EDGE), the puppet walks
    (motion.locomotion_keys, so foot planting covers it; held arms keep the hold pose) to
    ``DISMOUNT_EDGE_MARGIN`` inside that edge, on the same surface height and z. A prop that is
    itself offstage (a car driven off) leaves its puppet offstage: getting out there is not an
    entrance. Returns (keys, when the step ends, where it ends, facing after); with no step,
    ([], t, stand_at, facing). Pure: reads ``world``, changes nothing.
    """
    x = stand_at[0]
    if abs(x) <= STAGE_EDGE or abs(seat_x) > STAGE_EDGE:
        return [], t, stand_at, facing
    goal: Vec3 = (math.copysign(STAGE_EDGE - DISMOUNT_EDGE_MARGIN, x), stand_at[1], stand_at[2])
    keys, end, facing = _walk(world, who, t, stand_at, goal, facing)
    return keys, end, goal, facing


def seated_world_pos(pos: Vec3, seat_x: float) -> Vec3:
    """The world position ``World`` records for a puppet seated at ``pos`` on a prop whose
    anchor is at ``seat_x``.

    A seat that overhangs the stage edge (the aisle end of a pew at the gate, the far door of a
    car parked by the edge) puts ``pos`` past |x| = STAGE_EDGE while the prop stands on stage.
    The puppet is on stage with its seat, as when a start block seats it there (that records
    the start mark), so its recorded x is held at the edge; its root keys stay on the seat. A
    seat inside the band, or a prop that is itself offstage, returns ``pos`` unchanged."""
    if abs(pos[0]) <= STAGE_EDGE or abs(seat_x) > STAGE_EDGE:
        return pos
    return (math.copysign(STAGE_EDGE, pos[0]), pos[1], pos[2])


def require_gesture_on_stage(world: World, who: str) -> None:
    """verb_helpers.require_on_stage for wave, point_at and lean_toward, except that a puppet
    seated in a vehicle driven offstage is told to bring the vehicle back: enter would fail
    there (it is seated). ``offstage``: "dad is offstage in sedan.driver; drive_to or ride_to a
    mark on stage first"."""
    p = world.people[who]
    if p.seat is not None and world.is_vehicle(p.seat[0]) and not world.on_stage(who):
        what, sock = p.seat
        raise StateError("offstage", f"{who} is offstage in {what}.{sock}; drive_to or ride_to a mark on stage first")
    require_on_stage(world, who)


def _commit_facing(world: World, keys: list[Key], *bodies: Body) -> None:
    for b in bodies:
        facing = _final_facing(keys, b.id, b.facing)
        if facing != world.people[b.id].facing:
            world.set_facing(b.id, facing)


# ---------------------------------------------------------------------------- gestures

def apply_wave(world: World, tracks: TrackSet, action: Action, t: float, action_start: float,
               locate: Locate) -> float:
    """wave(target?, dur=1.2): raise an arm and wave; turn first to face ``target`` if behind."""
    who = require_person(world, action.who)
    require_gesture_on_stage(world, who)
    dur = _dur(action, WAVE_SECONDS)
    toward_x = None if action.target is None else locate(action.target, t)[0]
    me = _body(world, tracks, who, t)
    keys = gen.wave_keys(me, t, bones_at(tracks, who, t, all_bones=True), dur, gesture_arm(world, who), toward_x)
    _commit_facing(world, keys, me)
    tracks.extend(keys)
    return t + dur


def apply_point_at(world: World, tracks: TrackSet, action: Action, t: float, action_start: float,
                   locate: Locate) -> float:
    """point_at(target, dur=1.0): aim the point pose at the target's stage position."""
    who = require_person(world, action.who)
    require_gesture_on_stage(world, who)
    target = require_param(action, "target")
    dur = _dur(action, POINT_SECONDS)
    at = locate(target, t)
    me = _body(world, tracks, who, t)
    keys = gen.point_keys(me, t, bones_at(tracks, who, t, all_bones=True), (at[0], at[1]), dur, gesture_arm(world, who))
    _commit_facing(world, keys, me)
    tracks.extend(keys)
    return t + dur


def apply_lean_toward(world: World, tracks: TrackSet, action: Action, t: float, action_start: float,
                      locate: Locate) -> float:
    """lean_toward(target, dur=1.0): face the target and lean the torso toward it."""
    who = require_person(world, action.who)
    require_gesture_on_stage(world, who)
    target = require_param(action, "target")
    dur = _dur(action, LEAN_SECONDS)
    x = locate(target, t)[0]
    me = _body(world, tracks, who, t)
    keys = gen.lean_keys(me, t, bones_at(tracks, who, t, all_bones=True), x, dur)
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
    who = require_person(world, action.who)
    what = require_param(action, "what")
    # World.sit_on validates (unknown_id, vehicle_seat, already_seated, no_seat, unknown_socket,
    # socket_taken) on a copy, so the real world is untouched until every check has passed.
    socket = world.copy().sit_on(who, what, action.socket)
    require_on_stage(world, who)
    _not_standing(world, who)
    holder = world.holder(what)
    if holder is not None:
        verb = "wearing" if holder[1] == HEAD else "holding"
        raise StateError("held", f"{holder[0]} is {verb} it")
    seat = world.socket_world(what, socket)
    anchor = world.props[what].world_pos
    if seat is None or anchor is None:
        raise StateError("not_on_stage", f"{what} is not on stage; {PLACE_WITH_PROPSTART}")

    p = world.people[who]
    root = puppet_root(world, tracks, who, t)
    stand_at: Vec3 = (seat[0], anchor[1], seat[2])  # the floor under the seat
    rots = bones_at(tracks, who, t, all_bones=True)
    keys: list[Key] = []
    t_sit, facing, ground = t, p.facing, (root[0], root[1])
    if math.hypot(seat[0] - root[0], seat[2] - root[2]) > SEAT_WALK_EPS:
        walk, t_sit, facing = _walk(world, who, t, root, stand_at, p.facing)
        keys += walk
        rots = _after_walk(rots, walk, who)
        ground = (stand_at[0], stand_at[1])
    me = Body(who, ground, facing, p.build)
    keys += gen.sit_keys(me, t_sit, rots, (seat[0], seat[1]), z=seat[2])

    world.move(who, seated_world_pos(stand_at, anchor[0]), facing=facing)
    world.sit_on(who, what, socket)
    tracks.extend(keys)
    return t_sit + gen.POSE_SECONDS


def apply_stand_up(world: World, tracks: TrackSet, action: Action, t: float, action_start: float,
                   locate: Locate) -> float:
    """stand_up: from a seat onto the floor in front of it, or up from kneeling / the floor.

    A seat past the stage edge (a pew whose aisle end overhangs it) is followed by a short step
    back on stage (dismount_step)."""
    who = require_person(world, action.who)
    world.copy().stand_up(who)  # validates: not_sitting, seated_vehicle
    p = world.people[who]
    stand_at: Vec3 = p.pos
    seat_x: float | None = None
    if p.seat is not None:
        what, sock = p.seat
        seat = world.socket_world(what, sock)
        anchor = world.props[what].world_pos
        if seat is not None and anchor is not None:
            stand_at = (seat[0], anchor[1], seat[2])
            seat_x = anchor[0]
    me = _body(world, tracks, who, t)
    current = _settle_rots(world, bones_at(tracks, who, t, all_bones=True), who)
    keys = gen.stand_keys(me, t, current, (stand_at[0], stand_at[1]), z=stand_at[2])
    end = t + gen.POSE_SECONDS
    facing = p.facing
    if seat_x is not None:
        step, end, stand_at, facing = dismount_step(world, who, end, stand_at, seat_x, facing)
        keys += step

    world.stand_up(who)
    world.move(who, stand_at, facing=facing)
    tracks.extend(keys)
    return end


def apply_kneel(world: World, tracks: TrackSet, action: Action, t: float, action_start: float,
                locate: Locate) -> float:
    """kneel(dur?): kneel where the puppet stands; with dur, stand back up at t + dur."""
    who = require_person(world, action.who)
    dur = None if action.dur is None else _dur(action, 0.0)
    world.copy().kneel(who)  # validates: seated
    require_on_stage(world, who)
    p = world.people[who]
    if p.pose == "kneel":
        raise StateError("already_kneeling", f"{who} is already kneeling")
    _not_standing(world, who)

    root = puppet_root(world, tracks, who, t)
    me = _body(world, tracks, who, t)
    rots = bones_at(tracks, who, t, all_bones=True)
    blend = gen.POSE_SECONDS if dur is None else min(gen.POSE_SECONDS, dur)
    keys = gen.kneel_keys(me, t, rots, z=root[2], dur=blend)
    end = t + blend
    if dur is not None:
        knelt = gen.last_values(keys)
        down = knelt[(puppet_target(who), "root.position")]
        bent = dict(rots)
        for (target, channel), value in knelt.items():
            if target == puppet_target(who) and channel.endswith(".rot"):
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

    The target must be on stage and free; it is occupied ('hugging') until the hug ends. It may
    be kneeling: its posed root is then lowered by the kneel rootOffset, so the walk goal takes
    x and z from that root but y from the target's standing surface (``World`` keeps
    ``people[id].pos`` at standing height while a puppet kneels). The hugger stands on its own
    surface: the walk ends at standing height, which is what ``world.move`` stores.
    """
    who = require_person(world, action.who)
    other = require_param(action, "target")
    dur = _dur(action, HUG_SECONDS)
    world.hug_check(who, other)  # unknown_id, hug_self, offstage, seated
    world.check_free(other)

    a_root = puppet_root(world, tracks, who, t)
    b_root = puppet_root(world, tracks, other, t)
    a = world.people[who]
    rots_a = bones_at(tracks, who, t, all_bones=True)
    keys: list[Key] = []
    t_hug, facing, moved_to = t, a.facing, None
    dx = b_root[0] - a_root[0]
    if abs(dx) > HUG_GAP + HUG_SLACK:
        _not_standing(world, who)  # a kneeling puppet cannot walk over
        side = 1 if dx > 0 else -1
        surface = world.people[other].pos[1]  # not b_root[1]: a kneeling target's root is lowered
        goal: Vec3 = (b_root[0] - side * HUG_GAP, surface, b_root[2])
        walk, t_hug, facing = _walk(world, who, t, a_root, goal, a.facing)
        keys += walk
        rots_a = _after_walk(rots_a, walk, who)
        moved_to = goal
    me = Body(who, (moved_to or a_root)[:2], facing, a.build)  # type: ignore[arg-type]
    them = _body(world, tracks, other, t_hug)
    keys += gen.hug_keys(me, them, t_hug, rots_a, bones_at(tracks, other, t_hug, all_bones=True), dur)
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
