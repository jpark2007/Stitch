"""Vehicle verbs: get_in, get_out, drive_to, ride_to (ARCHITECTURE.md section 3, step 3).

"Vehicles move with their seated puppets, and wheels spin by the distance travelled."

A vehicle is a prop of size ``vehicle`` standing on the ground. Its ``position`` track is the
world position of its anchor. Puppets have no ``parent`` channel (shared/CONTRACTS.md channel
table), so riding is expressed as ``root.position`` keys that keep each seated puppet's hips
exactly on its socket while the vehicle moves (motion.seated_root_keys). Props resting on the
vehicle's sockets need no keys: their position is socket-relative.

Every handler follows the shared handler contract (see compiler.py):

    def apply_<verb>(world, tracks, action, t, action_start, locate) -> float

``t`` is the timeline time the action starts, ``action_start`` the timeline time of the shot's
t=0, and ``world.now == round(t - action_start, 6)`` (shot time) on entry. The handler returns
the timeline time when ``action.who`` is free again. It validates everything first and raises
``StateError`` before mutating the world or emitting keys; then it mutates the world and calls
``tracks.extend`` once.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

from screenplay import motion
from screenplay.body import POSE_SECONDS, sit_keys, stand_keys
from screenplay.ik import RIG, Body, scale
from screenplay.schema import Action
from screenplay.state import GROUND, StateError, World, fmt_t
from screenplay.tracks import TrackSet

Vec3 = tuple[float, float, float]
Key = tuple[str, str, float, object]
Locate = Callable[[str, float], Vec3]
Handler = Callable[[World, TrackSet, Action, float, float, Locate], float]

ACTING_Z = motion.ACTING_Z
WHEEL_RADIUS_FRACTION = 0.15  # wheel radius as a fraction of the vehicle's height
WALK_UP_THRESHOLD = 0.05      # walk to the door first when the seat is further away than this in x
_EPS = 1e-6

DRIVER_SOCKET = "driver"
DRIVING = "driving"
RIDING = "riding"


# --------------------------------------------------------------------------- helpers

def _require(action: Action, field: str) -> str:
    value = getattr(action, field)
    if value is None:
        name = "from" if field == "from_" else field
        raise StateError("missing_param", f"{action.do} needs {name}")
    return value


def _vehicle(world: World, what: str) -> None:
    """`what` is a known prop of size vehicle, else StateError."""
    if what not in world.props:
        raise StateError("unknown_id", f"{what} is not a known prop")
    if not world.is_vehicle(what):
        raise StateError("not_a_vehicle", f"{what} is not a vehicle")


def _person(world: World, who: str) -> None:
    if who not in world.people:
        raise StateError("unknown_id", f"{who} is not in the cast")


def _vec3(v: Iterable[float]) -> Vec3:
    x, y, z = (float(c) for c in v)
    return (x, y, z)


def _root(world: World, tracks: TrackSet, who: str, t: float) -> Vec3:
    """A puppet's root.position at timeline time t: its track, else the world's position."""
    v = tracks.value_at(f"puppet:{who}", "root.position", t)
    return _vec3(v) if v is not None else _vec3(world.people[who].pos)


def _anchor(world: World, tracks: TrackSet, what: str, t: float) -> Vec3:
    """A vehicle's anchor at timeline time t (its position track while on the ground)."""
    st = world.props[what]
    if st.parent == GROUND:
        v = tracks.value_at(f"prop:{what}", "position", t)
        if v is not None:
            return _vec3(v)
    if st.world_pos is None:
        raise StateError("not_on_stage", f"{what} is not on stage")
    return _vec3(st.world_pos)


def _on_stage(world: World, what: str) -> None:
    if world.props[what].world_pos is None:
        raise StateError("not_on_stage", f"{what} is not on stage")


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
    """Current rotation of every bone that has a track (or a pending key), at timeline time t."""
    out: dict[str, float] = {}
    target = f"puppet:{who}"
    for bone in RIG["bones"]:
        ch = f"{bone}.rot"
        v = tracks.value_at(target, ch, t)
        if v is not None:
            out[bone] = float(v)
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
    """(socket, person id) of every puppet seated in the vehicle, in socket order."""
    occ = world.props[what].occupants
    return [(sock, o.partition(":")[2]) for sock, o in occ.items() if o.startswith("puppet:")]


# --------------------------------------------------------------------------- get_in

def apply_get_in(world: World, tracks: TrackSet, action: Action, t: float, action_start: float,
                 locate: Locate) -> float:
    """Walk up to the seat (if it is more than 0.05 away in x) and sit with the hips on it."""
    who = _require(action, "who")
    what = _require(action, "what")
    _person(world, who)
    _vehicle(world, what)
    _on_stage(world, what)
    socket = world.copy().get_in(who, what, action.socket)  # validates seat choice and occupancy
    _check_still(tracks, what, t, action_start)

    p = world.people[who]
    anchor = _anchor(world, tracks, what, t)
    off = motion.prop_socket_offset(world.catalog[what], socket)
    seat: Vec3 = (anchor[0] + off[0], anchor[1] + off[1], anchor[2] + off[2])
    root = _root(world, tracks, who, t)

    keys: list[Key] = []
    t_sit, facing, ground = t, p.facing, root
    if abs(seat[0] - root[0]) > WALK_UP_THRESHOLD or abs(seat[2] - root[2]) > _EPS:
        door: Vec3 = (seat[0], anchor[1], seat[2])
        walk, t_sit, facing = motion.locomotion_keys(
            who, p.build, t, root, door, p.facing, speed_scale=p.speed_scale
        )
        keys += walk
        if walk:
            ground = door
    body = Body(who, (ground[0], ground[1]), facing, p.build)
    keys += sit_keys(body, t_sit, _bones(tracks, who, t_sit, keys), (seat[0], seat[1]), z=seat[2])
    ro = _sit_root_offset(p.build)
    sit_root: Vec3 = (seat[0] + ro[0], seat[1] + ro[1], seat[2])

    world.move(who, sit_root, facing)
    world.get_in(who, what, socket)
    tracks.extend(keys)
    return t_sit + POSE_SECONDS


# -------------------------------------------------------------------------- get_out

def apply_get_out(world: World, tracks: TrackSet, action: Action, t: float, action_start: float,
                  locate: Locate) -> float:
    """Stand up out of the seat onto the ground beside it, at the acting plane."""
    who = _require(action, "who")
    what = _require(action, "what")
    _person(world, who)
    _vehicle(world, what)
    world.copy().get_out(who, what)  # validates: who is in that vehicle
    _check_still(tracks, what, t, action_start)

    p = world.people[who]
    socket = p.seat[1]  # type: ignore[index]
    anchor = _anchor(world, tracks, what, t)
    off = motion.prop_socket_offset(world.catalog[what], socket)
    seat_x = anchor[0] + off[0]
    stand_at = (seat_x, anchor[1])
    root = _root(world, tracks, who, t)
    body = Body(who, (root[0], root[1]), p.facing, p.build)
    keys = stand_keys(body, t, _bones(tracks, who, t), stand_at, z=ACTING_Z)
    # stand_keys starts the root at the acting plane; start it where the puppet really sits.
    keys = [(tg, ch, kt, [root[0], root[1], root[2]]) if ch == "root.position" and kt == t else (tg, ch, kt, v)
            for tg, ch, kt, v in keys]

    world.get_out(who, what)
    world.move(who, (stand_at[0], stand_at[1], ACTING_Z))
    tracks.extend(keys)
    return t + POSE_SECONDS


# -------------------------------------------------------------- drive_to / ride_to

def _drive(world: World, tracks: TrackSet, what: str, to: str, t: float, action_start: float,
           locate: Locate) -> tuple[list[Key], float, Vec3]:
    """The vehicle's path to `to`, wheel spin, and seated puppets' root keys. Pure: no mutation.

    Returns (keys, arrival time, end anchor position).
    """
    _on_stage(world, what)
    st = world.props[what]
    if st.parent != GROUND:
        raise StateError("not_on_ground", f"{what} is not on the ground")
    _check_still(tracks, what, t, action_start)
    for sock, rider in _seated(world, what):
        until = _moving_until(tracks, f"puppet:{rider}", "root.position", t)
        if until is not None:
            raise StateError("busy", f"{rider} is still settling into {what}.{sock} until t={fmt_t(until - action_start)}")
    end = _vec3(locate(to, t))
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
    return out, arrival, end


def apply_drive_to(world: World, tracks: TrackSet, action: Action, t: float, action_start: float,
                   locate: Locate) -> float:
    """The driver drives the vehicle to a mark; everyone seated rides along."""
    who = _require(action, "who")
    what = _require(action, "what")
    to = _require(action, "to")
    _person(world, who)
    _vehicle(world, what)
    world.check_driver(who, what)
    keys, arrival, end = _drive(world, tracks, what, to, t, action_start, locate)

    world.move_vehicle(what, end)
    tracks.extend(keys)
    return arrival


def apply_ride_to(world: World, tracks: TrackSet, action: Action, t: float, action_start: float,
                  locate: Locate) -> float:
    """A rider asks to go to a mark: the puppet in the driver's seat drives; all ride along."""
    who = _require(action, "who")
    what = _require(action, "what")
    to = _require(action, "to")
    _person(world, who)
    _vehicle(world, what)
    world.check_rider(who, what)
    driver = world.props[what].occupants[DRIVER_SOCKET].partition(":")[2]
    others = [r for _, r in _seated(world, what) if r != who]  # the driver and every other rider
    for other in others:
        world.check_free(other)
    keys, arrival, end = _drive(world, tracks, what, to, t, action_start, locate)

    world.move_vehicle(what, end)
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
    "HANDLERS", "Handler", "Locate", "WHEEL_RADIUS_FRACTION",
    "apply_drive_to", "apply_get_in", "apply_get_out", "apply_ride_to",
]
