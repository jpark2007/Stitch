"""Vehicle verbs: get_in, get_out, drive_to, ride_to (ARCHITECTURE.md section 3, step 3).

"Vehicles move with their seated puppets, and wheels spin by the distance travelled."

A vehicle is a prop of size ``vehicle`` standing on the ground. Its ``position`` track is the
world position of its anchor. Puppets have no ``parent`` channel (shared/CONTRACTS.md channel
table), so riding is expressed as ``root.position`` keys that keep each seated puppet's hips
exactly on its socket while the vehicle moves (motion.seated_root_keys). Props resting on the
vehicle's sockets need no keys: their position is socket-relative. A puppet seated on such a
prop (a chair on a pickup's bed) is carried too: its root keys translate it with the vehicle,
exactly as World.move_vehicle moves its world position.

Every handler follows the shared handler contract (see compiler.py):

    def apply_<verb>(world, tracks, action, t, action_start, locate) -> float

``t`` is the timeline time the action starts, ``action_start`` the timeline time of the shot's
t=0, and ``world.now == round(t - action_start, 6)`` (shot time) on entry. The handler returns
the timeline time when ``action.who`` is free again. It validates everything first and raises
``StateError`` before mutating the world or emitting keys; then it mutates the world and calls
``tracks.extend`` once.
"""

from __future__ import annotations

from collections.abc import Iterable

from screenplay import motion
from screenplay.body import POSE_SECONDS, sit_keys, stand_keys
from screenplay.ik import RIG, Body, scale
from screenplay.locate import Locate, bones_at, held_arm_bones, puppet_root, puppet_target
from screenplay.schema import Action
from screenplay.state import GROUND, StateError, World, fmt_t
from screenplay.tracks import TrackSet
from screenplay.verb_helpers import (
    Handler,
    Key,
    Vec3,
    require_param,
    require_person,
    require_prop_on_stage,
    stand_up_keys,
    vec3,
)
from screenplay.verbs_body import DISMOUNT_EDGE_MARGIN, dismount_step, seated_world_pos

ACTING_Z = motion.ACTING_Z
WHEEL_RADIUS_FRACTION = 0.15  # wheel radius as a fraction of the vehicle's height
WALK_UP_THRESHOLD = 0.05      # walk to the door first when the seat is further away than this in x
_EPS = 1e-6

DRIVER_SOCKET = "driver"
DRIVING = "driving"
RIDING = "riding"


# --------------------------------------------------------------------------- helpers

# Shared checks and geometry come from verb_helpers (require_param: "<verb> needs <field>";
# require_person: unknown_id; require_prop_on_stage: not_on_stage; stand_up_keys; vec3) and
# locate (puppet_root, bones_at, held_arm_bones, puppet_target).

def _vehicle(world: World, what: str) -> None:
    """`what` is a known prop of size vehicle, else StateError."""
    if what not in world.props:
        raise StateError("unknown_id", f"{what} is not a known prop")
    if not world.is_vehicle(what):
        raise StateError("not_a_vehicle", f"{what} is not a vehicle")


def _anchor(world: World, tracks: TrackSet, what: str, t: float) -> Vec3:
    """A vehicle's anchor at timeline time t (its position track while on the ground)."""
    st = world.props[what]
    if st.parent == GROUND:
        v = tracks.value_at(f"prop:{what}", "position", t)
        if v is not None:
            return vec3(v)
    return vec3(require_prop_on_stage(world, what))


def _moving_until(tracks: TrackSet, target: str, channel: str, t: float) -> float | None:
    """The timeline time a positional track stops changing, if it still changes after t."""
    if not tracks.has_track(target, channel):
        return None
    now = tracks.value_at(target, channel, t)
    later: list[tuple[float, object]] = []
    for tr in tracks.tracks():
        if tr.target == target and tr.channel == channel:
            later = [(float(k[0]), k[1]) for k in tr.keys if float(k[0]) > t + _EPS]
            break
    if all(_same(v, now) for _, v in later):
        return None
    return max(kt for kt, _ in later)


def _same(a: object, b: object) -> bool:
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)) and len(a) == len(b):
        return all(abs(float(x) - float(y)) <= 1e-3 for x, y in zip(a, b))
    return a == b


def _check_still(tracks: TrackSet, what: str, t: float, action_start: float) -> None:
    until = _moving_until(tracks, f"prop:{what}", "position", t)
    if until is not None:
        raise StateError("vehicle_moving", f"{what} is moving until t={fmt_t(until - action_start)}")


def _bones(tracks: TrackSet, who: str, t: float, pending: Iterable[Key] = ()) -> dict[str, float]:
    """Current rotation of every bone that has a track (locate.bones_at), overridden by the
    latest pending key at or before t, at timeline time t."""
    out = bones_at(tracks, who, t)
    target = puppet_target(who)
    latest: dict[str, tuple[float, float]] = {}
    for tgt, ch, kt, value in pending:
        if tgt != target or not ch.endswith(".rot") or kt > t + _EPS:
            continue
        bone = ch[: -len(".rot")]
        if bone in RIG["bones"] and (bone not in latest or kt >= latest[bone][0]):
            latest[bone] = (kt, float(value))  # type: ignore[arg-type]
    out.update({b: v for b, (_, v) in latest.items()})
    return out


def _sit_root_offset(build: str) -> Vec3:
    """Root relative to the seat socket for a seated puppet; agrees with body.sit_keys."""
    return (0.0, -RIG["hipHeight"] * scale(build), 0.0)


def _seated(world: World, what: str) -> list[tuple[str, str]]:
    """(socket, person id) of every puppet seated in the vehicle's own sockets, in socket order."""
    return [(sock, who) for seat, sock, who in world.riders(what) if seat == what]


def _stacked(world: World, what: str) -> list[tuple[str, str, str]]:
    """(seat prop, socket, person id) of every puppet seated on a prop stacked on the vehicle
    (a chair on its bed, or on something resting there), in ``World.riders`` order."""
    return [r for r in world.riders(what) if r[0] != what]


# --------------------------------------------------------------------------- get_in

def apply_get_in(world: World, tracks: TrackSet, action: Action, t: float, action_start: float,
                 locate: Locate) -> float:
    """Get up first if kneeling or sitting on the floor, walk up to the seat (if it is more than
    0.05 away in x), and sit with the hips on it. Arms holding props keep the hold pose."""
    who = require_param(action, "who")
    what = require_param(action, "what")
    require_person(world, who)
    _vehicle(world, what)
    require_prop_on_stage(world, what)
    socket = world.copy().get_in(who, what, action.socket)  # validates seat choice and occupancy
    _check_still(tracks, what, t, action_start)

    p = world.people[who]
    anchor = _anchor(world, tracks, what, t)
    off = motion.prop_socket_offset(world.catalog[what], socket)
    seat: Vec3 = (anchor[0] + off[0], anchor[1] + off[1], anchor[2] + off[2])
    held = held_arm_bones(world, who)

    # Kneeling or sitting on the floor: stand up where it is first (verb_helpers.stand_up_keys,
    # as verbs_core._travel_keys does).
    keys, t_up = stand_up_keys(world, tracks, who, t)
    root = vec3(p.pos) if keys else puppet_root(world, tracks, who, t)

    t_sit, facing, ground = t_up, p.facing, root
    if abs(seat[0] - root[0]) > WALK_UP_THRESHOLD or abs(seat[2] - root[2]) > _EPS:
        door: Vec3 = (seat[0], anchor[1], seat[2])
        walk, t_sit, facing = motion.locomotion_keys(
            who, p.build, t_up, root, door, p.facing,
            skip_bones=sorted(held), speed_scale=p.speed_scale,
        )
        keys += walk
        if walk:
            ground = door
    body = Body(who, (ground[0], ground[1]), facing, p.build)
    current = {b: v for b, v in _bones(tracks, who, t_sit, keys).items() if b not in held}
    keys += sit_keys(body, t_sit, current, (seat[0], seat[1]), z=seat[2])
    ro = _sit_root_offset(p.build)
    sit_root: Vec3 = (seat[0] + ro[0], seat[1] + ro[1], seat[2])

    world.move(who, seated_world_pos(sit_root, anchor[0]), facing)
    world.get_in(who, what, socket)
    tracks.extend(keys)
    return t_sit + POSE_SECONDS


# -------------------------------------------------------------------------- get_out

def apply_get_out(world: World, tracks: TrackSet, action: Action, t: float, action_start: float,
                  locate: Locate) -> float:
    """Stand up out of the seat onto the ground beside it, at the acting plane. Arms holding
    props keep the hold pose. A seat past the stage edge (the far door of a car parked by it)
    is followed by a short step back on stage, to DISMOUNT_EDGE_MARGIN inside the edge
    (verbs_body.dismount_step); a car that is itself offstage leaves its puppet offstage."""
    who = require_param(action, "who")
    what = require_param(action, "what")
    require_person(world, who)
    _vehicle(world, what)
    world.copy().get_out(who, what)  # validates: who is in that vehicle
    _check_still(tracks, what, t, action_start)

    p = world.people[who]
    socket = p.seat[1]  # type: ignore[index]
    anchor = _anchor(world, tracks, what, t)
    off = motion.prop_socket_offset(world.catalog[what], socket)
    seat_x = anchor[0] + off[0]
    stand_at = (seat_x, anchor[1])
    root = puppet_root(world, tracks, who, t)
    body = Body(who, (root[0], root[1]), p.facing, p.build)
    held = held_arm_bones(world, who)  # arms holding props keep the hold pose
    current = {b: v for b, v in _bones(tracks, who, t).items() if b not in held}
    keys = stand_keys(body, t, current, stand_at, z=ACTING_Z)
    # stand_keys starts the root at the acting plane; start it where the puppet really sits.
    keys = [(tg, ch, kt, [root[0], root[1], root[2]]) if ch == "root.position" and kt == t else (tg, ch, kt, v)
            for tg, ch, kt, v in keys]

    step, end, landed, facing = dismount_step(world, who, t + POSE_SECONDS, (stand_at[0], stand_at[1], ACTING_Z),
                                              anchor[0], p.facing)
    keys += step

    world.get_out(who, what)
    world.move(who, landed, facing)
    tracks.extend(keys)
    return end


# -------------------------------------------------------------- drive_to / ride_to

def _drive(world: World, tracks: TrackSet, what: str, to: str, t: float, action_start: float,
           locate: Locate) -> tuple[list[Key], float, Vec3]:
    """The vehicle's path to `to`, wheel spin, and seated puppets' root keys. Pure: no mutation.

    Every puppet ``World.move_vehicle`` carries gets root keys at exactly the path's key times:
    one in the vehicle's own sockets stays on its socket (motion.seated_root_keys); one seated
    on a prop stacked on the vehicle (a chair on its bed) keeps its root at t translated by
    the vehicle's travel so far (path position - start anchor), the same translation
    move_vehicle applies to its world position. A rider whose root is still moving at t
    (sitting down) is ``busy``.

    Returns (keys, arrival time, end anchor position).
    """
    require_prop_on_stage(world, what)
    st = world.props[what]
    if st.parent != GROUND:
        raise StateError("not_on_ground", f"{what} is not on the ground")
    _check_still(tracks, what, t, action_start)
    for seat, sock, rider in world.riders(what):
        until = _moving_until(tracks, puppet_target(rider), "root.position", t)
        if until is not None:
            where = f"{what}.{sock}" if seat == what else f"{seat}.{sock}, which rests on {what},"
            raise StateError("busy", f"{rider} is still settling into {where} until t={fmt_t(until - action_start)}")
    end = vec3(locate(to, t))
    start = _anchor(world, tracks, what, t)
    prop = world.catalog[what]

    keys, arrival = motion.drive_keys(what, t, start, end)
    if not keys:
        return [], t, end
    path = motion.path_from_keys(keys, f"prop:{what}")
    _, height = motion.prop_dimensions(prop.size, prop.aspect)
    radius = WHEEL_RADIUS_FRACTION * height
    heading = 1 if end[0] - start[0] >= 0 else -1
    out: list[Key] = list(keys)
    for name in motion.spinning_parts(prop.parts):
        rot = tracks.value_at(f"prop:{what}", f"part.{name}.rot", t)
        out += motion.wheel_spin_keys(what, {name: prop.parts[name]}, path, radius,
                                      0.0 if rot is None else float(rot), heading)
    for sock, rider in _seated(world, what):
        out += motion.seated_root_keys(rider, path, motion.prop_socket_offset(prop, sock),
                                       _sit_root_offset(world.people[rider].build))
    for _, _, rider in _stacked(world, what):
        out += _carried_root_keys(rider, puppet_root(world, tracks, rider, t), start, path)
    return out, arrival, end


def _carried_root_keys(who: str, root: Vec3, start: Vec3, path: Iterable[tuple[float, Vec3]]) -> list[Key]:
    """``root.position`` keys for a puppet carried along by a moving vehicle without sitting in
    one of its sockets (it sits on a prop stacked on the vehicle): one key per path key, at the
    puppet's root plus the vehicle's travel from ``start`` (its anchor when the drive starts)."""
    return [(puppet_target(who), "root.position", kt,
             [root[0] + a[0] - start[0], root[1] + a[1] - start[1], root[2] + a[2] - start[2]])
            for kt, a in path]


def _keep_riders_on_stage(world: World, what: str, end: Vec3) -> None:
    """After ``World.move_vehicle``: a rider whose seat overhangs the stage edge while the
    vehicle stands on stage stays on stage with it (verbs_body.seated_world_pos), as after
    get_in. ``World.move`` refuses seated puppets, so the recorded position is set directly;
    no key changes."""
    for _, _, rider in world.riders(what):
        p = world.people[rider]
        p.pos = seated_world_pos(p.pos, end[0])


def apply_drive_to(world: World, tracks: TrackSet, action: Action, t: float, action_start: float,
                   locate: Locate) -> float:
    """The driver drives the vehicle to a mark; everyone seated rides along."""
    who = require_param(action, "who")
    what = require_param(action, "what")
    to = require_param(action, "to")
    require_person(world, who)
    _vehicle(world, what)
    world.check_driver(who, what)
    keys, arrival, end = _drive(world, tracks, what, to, t, action_start, locate)

    world.move_vehicle(what, end)
    _keep_riders_on_stage(world, what, end)
    tracks.extend(keys)
    return arrival


def apply_ride_to(world: World, tracks: TrackSet, action: Action, t: float, action_start: float,
                  locate: Locate) -> float:
    """A rider asks to go to a mark: the puppet in the driver's seat drives; all ride along.

    The rider sits in the vehicle, or on a prop resting on it (World.check_rider). Everyone
    else carried along, the driver and riders on stacked props included, must be free, and is
    busy (driving / riding) until the vehicle arrives."""
    who = require_param(action, "who")
    what = require_param(action, "what")
    to = require_param(action, "to")
    require_person(world, who)
    _vehicle(world, what)
    world.check_rider(who, what)
    driver = world.props[what].occupants[DRIVER_SOCKET].partition(":")[2]
    others = [r for _, _, r in world.riders(what) if r != who]  # the driver and every other rider
    for other in others:
        world.check_free(other)
    keys, arrival, end = _drive(world, tracks, what, to, t, action_start, locate)

    world.move_vehicle(what, end)
    _keep_riders_on_stage(world, what, end)
    until = round(arrival - action_start, 6)
    for other in others:
        world.occupy(other, until, DRIVING if other == driver else RIDING, at=world.now)
    tracks.extend(keys)
    return arrival


HANDLERS: dict[str, Handler] = {
    "get_in": apply_get_in,
    "get_out": apply_get_out,
    "drive_to": apply_drive_to,
    "ride_to": apply_ride_to,
}

__all__ = [
    "DISMOUNT_EDGE_MARGIN", "HANDLERS", "Handler", "Locate", "WHEEL_RADIUS_FRACTION",
    "apply_drive_to", "apply_get_in", "apply_get_out", "apply_ride_to",
]
