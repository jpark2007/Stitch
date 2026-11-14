"""Where things are on stage at a timeline time, read back from the tracks written so far.

``make_locate`` builds the ``Locate`` callable every verb handler and the camera receive:
``locate(name, t)`` is the stage position, at timeline time ``t``, of a person (root position),
a prop (world anchor; a held or worn prop gives its holder's socket) or a mark. Precedence is
person, then prop, then mark.

The puppet helpers here (``puppet_root``, ``bone_at``, ``body_at``, ...) are the handler
contract's geometry rules in one place: geometry comes from the tracks, and falls back to the
world state when a puppet has no track yet.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

from screenplay import ik
from screenplay.motion import prop_socket_offset
from screenplay.state import (
    HANDS,
    PLACE_WITH_PROPSTART,
    StateError,
    World,
    parse_parent,
    unknown_mark_reason,
)
from screenplay.tracks import TrackSet

Vec3 = tuple[float, float, float]
Locate = Callable[[str, float], Vec3]

BONES: tuple[str, ...] = tuple(ik.RIG["bones"])
ARM_OF_HAND: dict[str, str] = {chain["socket"]: arm for arm, chain in ik.RIG["ik"].items()}

NOT_ON_STAGE = "{what} is not on stage; " + PLACE_WITH_PROPSTART
"""The not_on_stage reason, worded as state.World's (a prop nothing has placed or carried in)."""


def _names(items) -> str:
    """A list for messages: ``a, b, c``."""
    return ", ".join(items)


def _v3(v) -> Vec3:
    return (float(v[0]), float(v[1]), float(v[2]))


# ------------------------------------------------------------------- puppet geometry

def puppet_target(who: str) -> str:
    return f"puppet:{who}"


def puppet_root(world: World, tracks: TrackSet, who: str, t: float) -> Vec3:
    """``root.position`` at ``t`` from the tracks, else the world position."""
    v = tracks.value_at(puppet_target(who), "root.position", t)
    return _v3(v) if v is not None else _v3(world.people[who].pos)


def puppet_facing_at(world: World, tracks: TrackSet, who: str, t: float) -> int:
    """``root.facing`` at ``t`` from the tracks, else the world facing."""
    v = tracks.value_at(puppet_target(who), "root.facing", t)
    return int(v) if v is not None else world.people[who].facing


def bone_at(tracks: TrackSet, who: str, bone: str, t: float) -> float:
    """A bone's rotation at ``t``; a bone with no track is at 0."""
    v = tracks.value_at(puppet_target(who), f"{bone}.rot", t)
    return float(v) if v is not None else 0.0


def bones_at(tracks: TrackSet, who: str, t: float, *, all_bones: bool = False) -> dict[str, float]:
    """Bone -> rotation at ``t`` for every bone that has a track (or every rig bone)."""
    target = puppet_target(who)
    return {
        b: bone_at(tracks, who, b, t)
        for b in BONES
        if all_bones or tracks.has_track(target, f"{b}.rot")
    }


def arm_at(tracks: TrackSet, who: str, arm: str, t: float) -> tuple[float, float]:
    upper, lower = ik.arm_bones(arm)
    return bone_at(tracks, who, upper, t), bone_at(tracks, who, lower, t)


def body_at(world: World, tracks: TrackSet, who: str, t: float) -> ik.Body:
    """``ik.Body`` at ``t``: root and torso from the tracks, facing and build from the world."""
    x, y, _ = puppet_root(world, tracks, who, t)
    p = world.people[who]
    return ik.Body(who, (x, y), p.facing, p.build, bone_at(tracks, who, "torso", t))


def arm_of_hand(hand: str) -> str:
    """``hand_r`` -> ``arm_r``."""
    return ARM_OF_HAND[hand]


def held_arms(world: World, who: str) -> list[str]:
    """Arms whose hand is holding a prop, in hand order (hand_r first)."""
    holding = world.people[who].holding
    return [arm_of_hand(h) for h in HANDS if h in holding]


def held_arm_bones(world: World, who: str) -> set[str]:
    return {b for arm in held_arms(world, who) for b in ik.arm_bones(arm)}


def gesture_arm(world: World, who: str) -> str:
    """The arm for a reach or gesture with no prop: arm_r, unless hand_r holds something and
    hand_l is free."""
    holding = world.people[who].holding
    if "hand_r" in holding and "hand_l" not in holding:
        return "arm_l"
    return "arm_r"


def puppet_socket_at(world: World, tracks: TrackSet, who: str, socket: str, t: float) -> Vec3:
    """World position of a puppet socket (hand_l, hand_r, head) at ``t``, at the root's depth."""
    x, y, z = puppet_root(world, tracks, who, t)
    facing = puppet_facing_at(world, tracks, who, t)
    sx, sy = ik.socket_world(socket, bones_at(tracks, who, t), (x, y), facing, world.people[who].build)
    return (sx, sy, z)


# ---------------------------------------------------------------------------- locate

class Locator:
    """``Locate`` for one shot. Callable as ``locate(name, t)``; ``mark(name)`` resolves marks only."""

    def __init__(self, world: World, tracks: TrackSet, marks: Mapping[str, Vec3], shot_id: str):
        self.world = world
        self.tracks = tracks
        self.marks = {k: _v3(v) for k, v in marks.items()}
        self.shot_id = shot_id
        # Props off the stage when the shot starts (the compiler builds the Locator after the
        # start block). One a previous set left behind still has tracks (the start block parks
        # it at offstage_left), so the tracks alone would place it offstage. A snapshot, not a
        # live check, so a prop that was on stage at the start and left later still resolves
        # from its tracks for the end-of-shot camera pass.
        self._unplaced_at_start = frozenset(p for p in world.props if world.is_unplaced(p))

    def __call__(self, name: str, t: float) -> Vec3:
        if name in self.world.people:
            return puppet_root(self.world, self.tracks, name, t)
        if name in self.world.props:
            return self.prop(name, t)
        if name in self.marks:
            return self.marks[name]
        raise StateError(
            "unknown_target",
            f"{name} is not a person, prop or mark in this shot; the cast is {_names(self.world.people)}; "
            f"the props are {_names(self.world.props)}; the marks are {_names(self.marks)}",
        )

    def mark(self, name: str) -> Vec3:
        if name not in self.marks:
            raise StateError("unknown_mark", unknown_mark_reason(name, self.marks))
        return self.marks[name]

    def prop(self, what: str, t: float, _seen: frozenset[str] = frozenset()) -> Vec3:
        """A prop's world anchor at ``t`` (its holder's socket when held)."""
        if what in _seen:
            raise StateError("cycle", f"{what} cannot rest on itself")
        seen = _seen | {what}
        if what in self._unplaced_at_start and self.world.is_unplaced(what):
            raise StateError("not_on_stage", NOT_ON_STAGE.format(what=what))
        target = f"prop:{what}"
        parent = self.tracks.value_at(target, "parent", t)
        if parent is None:
            return self._prop_from_world(what, t, seen)
        pos = self.tracks.value_at(target, "position", t) or [0.0, 0.0, 0.0]
        parsed = parse_parent(parent)
        if parsed is None:
            return _v3(pos)
        kind, pid, socket = parsed
        if kind == "puppet" and pid in self.world.people:
            return puppet_socket_at(self.world, self.tracks, pid, socket, t)
        if kind == "prop" and pid in self.world.props:
            return self._on_prop(pid, socket, _v3(pos), t, seen)
        raise StateError("not_on_stage", NOT_ON_STAGE.format(what=what))

    def _on_prop(self, base: str, socket: str, offset: Vec3, t: float, seen: frozenset[str]) -> Vec3:
        bx, by, bz = self.prop(base, t, seen)
        try:
            ox, oy, oz = prop_socket_offset(self.world.catalog[base], socket)
        except KeyError:
            sockets = self.world.catalog[base].sockets
            known = f"its sockets are {_names(sockets)}" if sockets else "it has no sockets"
            raise StateError("unknown_socket", f"{base} has no socket {socket}; {known}") from None
        return (bx + ox + offset[0], by + oy + offset[1], bz + oz + offset[2])

    def _prop_from_world(self, what: str, t: float, seen: frozenset[str]) -> Vec3:
        st = self.world.props[what]
        parsed = parse_parent(st.parent)
        if parsed is not None and parsed[0] == "puppet" and parsed[1] in self.world.people:
            return puppet_socket_at(self.world, self.tracks, parsed[1], parsed[2], t)
        if parsed is not None and parsed[0] == "prop" and parsed[1] in self.world.props:
            return self._on_prop(parsed[1], parsed[2], _v3(st.pos), t, seen)
        if st.world_pos is None:
            raise StateError("not_on_stage", NOT_ON_STAGE.format(what=what))
        return _v3(st.world_pos)


def make_locate(world: World, tracks: TrackSet, marks: Mapping[str, Vec3], shot_id: str) -> Locator:
    """The shot's ``Locate``: person, then prop, then mark; anything else is ``unknown_target``."""
    return Locator(world, tracks, marks, shot_id)


def mark_of(locate: Locate, name: str, t: float) -> Vec3:
    """A mark's position. Uses ``Locator.mark`` (marks only) when available, else ``locate``."""
    mark = getattr(locate, "mark", None)
    if callable(mark):
        return mark(name)
    return locate(name, t)
