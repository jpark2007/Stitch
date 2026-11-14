"""Prop and puppet state simulation over time (ARCHITECTURE.md section 3, step 2).

This is the compiler's logical model of the world: where each puppet stands, which way it
faces, what it holds in which socket, where it sits, and where each prop rests and in what
state. It produces no keys. The compiler calls one method per action, in time order, and
pairs it with the body/motion key generators.

Every verb method validates first and raises `StateError` without touching the world, then
mutates. Error codes are stable snake_case ids; the reason is the human tail of a
CompileError message, e.g. ``mom is holding it`` in
``shot s1, t=5: dad cannot hand_to "hymn_books"; mom is holding it``.

Static checks (unknown verbs, unknown marks in actions, prop too big to carry) belong to
validate.py. This module still raises ``StateError('unknown_id')`` instead of KeyError.
"""

from __future__ import annotations

import copy as _copy
import json
from collections.abc import Callable, Collection, Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

import schemas
from screenplay import character
from screenplay.schema import Shot

Vec3 = tuple[float, float, float]

_VOCABULARY = json.loads((Path(__file__).resolve().parents[2] / "shared" / "vocabulary.json").read_text())

Z_ACTING = 0.5
HANDS = ("hand_r", "hand_l")
HEAD = "head"
PUPPET_SOCKETS = (*HANDS, HEAD)
SEATED_POSES = ("sit", "kneel")
GROUND = "ground"
OPEN, CLOSED = "open", "closed"  # the states the open / close verbs set
TIME_EPS = 1e-6  # an action may start this close before the previous one ends


def _mark_to_vec(mark: list[float]) -> Vec3:
    """[u, h] or [u, h, d] -> stage position (shared/CONTRACTS.md, Marks)."""
    u, h = float(mark[0]), float(mark[1])
    d = float(mark[2]) if len(mark) > 2 else Z_ACTING
    return (2 * u - 1, h, d)


BUILTIN_MARKS: dict[str, Vec3] = {k: _mark_to_vec(v) for k, v in _VOCABULARY["builtinMarks"].items()}
OFFSTAGE_LEFT: Vec3 = BUILTIN_MARKS["offstage_left"]
OFFSTAGE_RIGHT: Vec3 = BUILTIN_MARKS["offstage_right"]


def fmt_t(t: float) -> str:
    """Seconds for messages: at most two decimals, no trailing zeros (4.74, 5, 0.5).

    Never ``-0`` and never exponent notation: 1234567.25 stays ``1234567.25``."""
    return f"{round(float(t), 2) + 0.0:.2f}".rstrip("0").rstrip(".")


# The fixes reasons end with, after a "; " (errors.ERROR_CODES has the hint templates).
ENTER_FIRST = "use enter first"
PLACE_WITH_PROPSTART = "place it with propStart"


def missing_param_reason(do: str, param: str) -> str:
    """validate's missing_param wording: ``walk_to requires who, to; "to" is missing``.

    The required list comes from shared/vocabulary.json; a verb it does not know names just
    ``param``."""
    spec = _VOCABULARY["actions"].get(do)
    required = list(spec["required"]) if spec else [param]
    return f'{do} requires {", ".join(required)}; "{param}" is missing'


def unknown_mark_reason(name: str, marks: Iterable[str]) -> str:
    """``porch is not a mark in this shot; the marks are a, b`` (begin_shot, Locator.mark).

    The shot is already in the message head, so the reason says "this shot"."""
    return f"{name} is not a mark in this shot; the marks are {', '.join(marks)}"


def self_target_reason(param: str, who: str) -> str:
    """validate's self_target wording: ``"target" is dad themselves``."""
    return f'"{param}" is {who} themselves'


def _vec(v: Iterable[float]) -> Vec3:
    x, y, z = (float(c) for c in v)
    return (x, y, z)


class StateError(Exception):
    """An impossible action. `code` is a stable id; `reason` names ids and the blocking fact."""

    def __init__(self, code: str, reason: str):
        super().__init__(reason)
        self.code = code
        self.reason = reason

    def __repr__(self) -> str:
        return f"StateError({self.code!r}, {self.reason!r})"


@dataclass
class PuppetState:
    id: str
    build: str
    pos: Vec3
    facing: int
    pose: str = "stand"
    holding: dict[str, str] = field(default_factory=dict)  # socket -> prop id
    seat: tuple[str, str] | None = None  # (prop id, socket)
    busy_until: float = 0.0
    activity: str | None = None
    speed_scale: float = 1.0  # walk/run speed factor from the sheet's notes (character.speed_scale)
    item: str | None = None  # signature item this puppet starts the story wearing on its head, if any


@dataclass
class PropState:
    id: str
    parent: str = GROUND  # "ground", "puppet:<id>.<socket>" or "prop:<id>.<socket>"
    pos: Vec3 = (0.0, 0.0, Z_ACTING)  # world anchor on the ground, else offset from the socket
    state: str | None = None
    world_pos: Vec3 | None = None  # None = not placed on stage yet, or held (the compiler knows the hand)
    occupants: dict[str, str] = field(default_factory=dict)  # socket -> "puppet:<id>" or "prop:<id>"


def parse_parent(parent: str) -> tuple[str, str, str] | None:
    """'puppet:dad.hand_r' -> ('puppet', 'dad', 'hand_r'); 'ground' -> None."""
    if parent == GROUND:
        return None
    kind, _, rest = parent.partition(":")
    pid, _, socket = rest.rpartition(".")
    return kind, pid, socket


class World:
    """The logical state of every puppet and prop. Deterministic and copyable."""

    def __init__(self, people: Iterable[schemas.Person], props: Iterable[schemas.Prop]):
        """Everyone starts offstage left facing right; every prop starts unplaced.

        A person's signature item (character.item_prop_id) starts worn on their head when that
        prop is passed in and its size is wearable (character.WEARABLE_SIZES). If two people name
        the same item, only the first in cast order wears it; the other's ``item`` stays None.
        """
        self.catalog: dict[str, schemas.Prop] = {}
        self.props: dict[str, PropState] = {}
        for pr in props:
            self.catalog[pr.id] = pr
            self.props[pr.id] = PropState(id=pr.id)
        self.people: dict[str, PuppetState] = {}
        for p in people:
            self.people[p.id] = PuppetState(
                id=p.id, build=p.character.build, pos=OFFSTAGE_LEFT, facing=1,
                speed_scale=character.speed_scale(p.character.notes),
            )
            item = character.signature_item(p)
            if (item is not None and item in self.catalog and self.catalog[item].size in character.WEARABLE_SIZES
                    and self.holder(item) is None):
                self.people[p.id].item = item
                self._attach_puppet(item, p.id, HEAD)
        self.now: float = 0.0  # shot time; the compiler may set it before calling occupy()
        # Added to every time a busy reason shows (check_free), so the compiler can state it in
        # the blocked action's written frame when cues moved the shot's actions. 0.0 = as is.
        self.busy_time_offset: float = 0.0

    # ------------------------------------------------------------------ basics

    def copy(self) -> World:
        w = World.__new__(World)
        w.people = _copy.deepcopy(self.people)
        w.props = _copy.deepcopy(self.props)
        w.catalog = self.catalog  # immutable definitions, shared
        w.now = self.now
        w.busy_time_offset = self.busy_time_offset
        return w

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, World):
            return NotImplemented
        return self.people == other.people and self.props == other.props and self.now == other.now

    __hash__ = None  # type: ignore[assignment]

    def snapshot(self) -> dict:
        """Plain-data view, handy for debugging and test diffs."""
        return {
            "now": self.now,
            "people": {k: vars(v).copy() for k, v in self.people.items()},
            "props": {k: vars(v).copy() for k, v in self.props.items()},
        }

    def _person(self, who: str | None) -> PuppetState:
        if who is None or who not in self.people:
            raise StateError("unknown_id", f"{who} is not in the cast")
        return self.people[who]

    def _prop(self, what: str | None) -> PropState:
        if what is None or what not in self.props:
            # The one-grammar wording would be "<what> is not in the screenplay's props" (#171
            # item 13, verbs_core's and validate's words), but test_verbs_body.py
            # test_sit_on_errors pins this text through World.sit_on.
            raise StateError("unknown_id", f"{what} is not a known prop")
        return self.props[what]

    def holder(self, prop_id: str) -> tuple[str, str] | None:
        """(person, socket) holding or wearing the prop, else None."""
        parsed = parse_parent(self._prop(prop_id).parent)
        if parsed and parsed[0] == "puppet":
            return parsed[1], parsed[2]
        return None

    def free_hand(self, who: str, prefer: str = "hand_r") -> str | None:
        p = self._person(who)
        order = [prefer] + [h for h in HANDS if h != prefer]
        for h in order:
            if h not in p.holding:
                return h
        return None

    def on_stage(self, who: str) -> bool:
        return abs(self._person(who).pos[0]) <= 1.0

    def is_vehicle(self, what: str) -> bool:
        d = self.catalog.get(what)
        return d is not None and d.size == "vehicle"

    def is_unplaced(self, what: str) -> bool:
        """True when the prop is nowhere on stage: not held, not on a socket, not on the ground."""
        st = self._prop(what)
        return st.parent == GROUND and st.world_pos is None

    def _placed(self, what: str) -> bool:
        """On stage somehow: on the ground, held or worn, or resting on something that is."""
        seen: set[str] = set()
        cur: str | None = what
        while cur is not None and cur not in seen:
            seen.add(cur)
            parsed = parse_parent(self.props[cur].parent)
            if parsed is None:
                return self.props[cur].world_pos is not None
            if parsed[0] == "puppet":
                return True
            cur = parsed[1]
        return False

    def riders(self, what: str) -> list[tuple[str, str, str]]:
        """(seat prop id, socket, person id) of every puppet seated on `what` or on any prop
        stacked on it, directly or through other props.

        Deterministic order: `what`'s own sockets first, then each prop resting on it in
        ``_descendants`` order (depth first); within a prop, sockets in the order they were
        taken (``occupants`` insertion order). Raises ``unknown_id`` for an unknown prop.
        """
        self._prop(what)
        out: list[tuple[str, str, str]] = []
        for pid in (what, *self._descendants(what)):
            for sock, occ in self.props[pid].occupants.items():
                kind, _, oid = occ.partition(":")
                if kind == "puppet":
                    out.append((pid, sock, oid))
        return out

    def _riders(self, what: str) -> list[str]:
        """Puppets seated on `what` or on anything stacked on it (``riders`` order)."""
        return [who for _, _, who in self.riders(what)]

    def supports(self, what: str) -> list[str]:
        """The props `what` rests on, nearest first: its parent prop, that prop's parent prop,
        and so on down to the one on the ground (or held). Empty for a prop on the ground or in
        a hand. Raises ``unknown_id`` for an unknown prop; stops at a (corrupt) loop."""
        self._prop(what)
        out: list[str] = []
        cur = what
        while True:
            parsed = parse_parent(self.props[cur].parent)
            if parsed is None or parsed[0] != "prop" or parsed[1] not in self.props:
                return out
            cur = parsed[1]
            if cur == what or cur in out:
                return out
            out.append(cur)

    def socket_world(self, what: str, socket: str) -> Vec3 | None:
        """World position of a prop's socket, or None if the prop is held / not placed."""
        st = self._prop(what)
        d = self.catalog[what]
        if st.world_pos is None or socket not in d.sockets:
            return None
        h = _VOCABULARY["sizes"][d.size]
        w = h * d.aspect
        su, sv = d.sockets[socket][0], d.sockets[socket][1]
        au, av = d.anchor[0], d.anchor[1]
        x, y, z = st.world_pos
        return (x + (su - au) * w, y + (av - sv) * h, z)

    def _descendants(self, what: str) -> Iterator[str]:
        """Props resting on `what`, recursively."""
        for occ in self.props[what].occupants.values():
            kind, _, oid = occ.partition(":")
            if kind == "prop":
                yield oid
                yield from self._descendants(oid)

    def _refresh_world(self, what: str) -> None:
        """Recompute world_pos of props stacked on `what` after it moved."""
        for sock, occ in self.props[what].occupants.items():
            kind, _, oid = occ.partition(":")
            if kind != "prop":
                continue
            child = self.props[oid]
            base = self.socket_world(what, sock)
            child.world_pos = None if base is None else (
                base[0] + child.pos[0], base[1] + child.pos[1], base[2] + child.pos[2])
            self._refresh_world(oid)

    # ------------------------------------------------------- reasons for errors

    def _where(self, what: str, who: str | None = None) -> str:
        """Why `who` is not holding `what` in a hand: who has it or where it is."""
        st = self.props[what]
        parsed = parse_parent(st.parent)
        if parsed is None:
            return "it is on the ground" if st.world_pos is not None else "it is not on stage"
        kind, pid, socket = parsed
        if kind == "puppet":
            return f"{pid} is {'wearing' if socket == HEAD else 'holding'} it"
        return f"it is on {pid}.{socket}"

    def _require_in_hand(self, who: str, what: str) -> str:
        h = self.holder(what)
        if h is None or h[0] != who or h[1] not in HANDS:
            raise StateError("not_holding", self._where(what, who))
        return h[1]

    def _sockets_list(self, what: str) -> str:
        """``its sockets are a, b`` (or ``it has no sockets``), for unknown_socket reasons."""
        sockets = self.catalog[what].sockets
        return f"its sockets are {', '.join(sockets)}" if sockets else "it has no sockets"

    def _states_list(self, what: str) -> str:
        """``its states are a, b`` (or ``it has no states``), for unknown_state reasons."""
        states = self.catalog[what].states
        return f"its states are {', '.join(states)}" if states else "it has no states"

    def _hands_full_reason(self, who: str) -> str:
        p = self.people[who]
        held = ", ".join(f"{s}: {p.holding[s]}" for s in HANDS if s in p.holding)
        return f"{who}'s hands are full ({held})"

    def seat_reason(self, who: str) -> str:
        """``mom is seated in chair.seat; use stand_up first`` (``use get_out first`` in a
        vehicle), for a seated puppet asked to do something only a standing one can."""
        prop, sock = self.people[who].seat  # type: ignore[misc]
        fix = "use get_out first" if self.is_vehicle(prop) else "use stand_up first"
        return f"{who} is seated in {prop}.{sock}; {fix}"

    def _require_on_stage(self, *who: str) -> None:
        """``offstage``: ``dad is offstage; use enter first``."""
        for w in who:
            if not self.on_stage(w):
                raise StateError("offstage", f"{w} is offstage; {ENTER_FIRST}")

    def _require_placed(self, what: str) -> None:
        """``not_on_stage``: ``cup is not on stage; place it with propStart``."""
        if self.is_unplaced(what):
            raise StateError("not_on_stage", f"{what} is not on stage; {PLACE_WITH_PROPSTART}")

    def _require_state(self, what: str, state: str) -> None:
        """``unknown_state``: ``lamp has no state broken; its states are lit, out``. A prop with
        no declared states takes any state."""
        if self.catalog[what].states and state not in self.catalog[what].states:
            raise StateError("unknown_state", f"{what} has no state {state}; {self._states_list(what)}")

    # ----------------------------------------------------- low-level mutations

    def _detach(self, what: str) -> None:
        """Remove a prop from wherever it is attached (a hand, a head, a prop socket)."""
        st = self.props[what]
        parsed = parse_parent(st.parent)
        if parsed is not None:
            kind, pid, socket = parsed
            if kind == "puppet":
                self.people[pid].holding.pop(socket, None)
            elif kind == "prop" and pid in self.props:
                self.props[pid].occupants.pop(socket, None)
        st.parent = GROUND
        st.world_pos = None

    def _attach_puppet(self, what: str, who: str, socket: str) -> None:
        self._detach(what)
        st = self.props[what]
        self.people[who].holding[socket] = what
        st.parent = f"puppet:{who}.{socket}"
        st.pos = (0.0, 0.0, 0.0)
        st.world_pos = None
        self._refresh_world(what)

    def _attach_prop(self, what: str, base: str, socket: str, offset: Vec3 = (0.0, 0.0, 0.0)) -> None:
        self._detach(what)
        st = self.props[what]
        st.parent = f"prop:{base}.{socket}"
        st.pos = _vec(offset)
        self.props[base].occupants[socket] = f"prop:{what}"
        b = self.socket_world(base, socket)
        st.world_pos = None if b is None else (b[0] + st.pos[0], b[1] + st.pos[1], b[2] + st.pos[2])
        self._refresh_world(what)

    def _to_ground(self, what: str, pos: Vec3) -> None:
        self._detach(what)
        st = self.props[what]
        st.pos = _vec(pos)
        st.world_pos = st.pos
        self._refresh_world(what)

    def _unseat(self, who: str) -> None:
        p = self.people[who]
        if p.seat is not None:
            prop, sock = p.seat
            occ = self.props[prop].occupants
            if occ.get(sock) == f"puppet:{who}":
                del occ[sock]
            p.seat = None

    def _seat(self, who: str, what: str, socket: str) -> None:
        self._unseat(who)
        p = self.people[who]
        p.seat = (what, socket)
        p.pose = "sit"
        self.props[what].occupants[socket] = f"puppet:{who}"

    def _check_socket(self, what: str, socket: str, who: str | None = None) -> None:
        d = self.catalog[what]
        if socket not in d.sockets:
            raise StateError("unknown_socket", f"{what} has no socket {socket}; {self._sockets_list(what)}")
        occ = self.props[what].occupants.get(socket)
        if occ is not None and occ != f"puppet:{who}":
            raise StateError("socket_taken", f"{what}.{socket} is taken by {occ.partition(':')[2]}")

    # ---------------------------------------------------------------- shots

    def reseat_riders(
        self,
        moved: Iterable[str],
        seated_root: Callable[[str, Vec3], Vec3],
        skip: Collection[str] = (),
    ) -> dict[str, Vec3 | None]:
        """Carry seated puppets along with props that a shot's prop_start re-placed.

        Every puppet not in ``skip`` that sits on one of the ``moved`` props, or on a prop resting
        on one of them, gets ``pos = seated_root(who, seat)``, where ``seat`` is the world
        position of its seat socket (the compiler's formula for a seated root). Returns
        ``{who: new pos}`` in cast order; the value is None, and ``pos`` is left alone, when the
        seat has no world position (the prop it rests on is held). Never raises.
        """
        riders: set[str] = set()
        for what in moved:
            if what in self.props:
                riders.update(self._riders(what))
        out: dict[str, Vec3 | None] = {}
        for who, p in self.people.items():
            if who not in riders or who in skip or p.seat is None:
                continue
            seat = self.socket_world(*p.seat)
            if seat is None:
                out[who] = None
                continue
            p.pos = _vec(seated_root(who, seat))
            out[who] = p.pos
        return out

    def begin_shot(self, shot: Shot, marks: dict[str, Vec3], same_set_as_previous: bool) -> None:
        """Apply a shot's start and prop_start. All-or-nothing: on error the world is unchanged."""
        w = self.copy()
        w._begin_shot(shot, {**BUILTIN_MARKS, **{k: _vec(v) for k, v in marks.items()}}, same_set_as_previous)
        self.people, self.props, self.now = w.people, w.props, w.now

    def _begin_shot(self, shot: Shot, marks: dict[str, Vec3], same_set: bool) -> None:
        def mark(name: str) -> Vec3:
            if name not in marks:
                raise StateError("unknown_mark", unknown_mark_reason(name, marks))
            return marks[name]

        # Static-ish checks on the start block before applying anything.
        held_by: dict[str, str] = {}
        seats: dict[tuple[str, str], str] = {}
        for who, s in shot.start.items():
            self._person(who)
            mark(s.at)
            if s.holding is not None:
                self._prop(s.holding)
                if s.holding in held_by:
                    raise StateError(
                        "double_hold",
                        f"{held_by[s.holding]} and {who} both start holding {s.holding}; give it to one of them",
                    )
                held_by[s.holding] = who
                if s.holding in shot.prop_start:
                    raise StateError(
                        "prop_conflict",
                        f"{who} starts holding {s.holding}, and propStart also places it; "
                        f"remove {s.holding} from propStart or from {who}'s holding",
                    )
            if s.in_ is not None:
                prop, _, sock = s.in_.partition(".")
                self._prop(prop)
                if not sock:
                    raise StateError(
                        "unknown_socket", f"{who} starts in {prop} with no socket; {self._sockets_list(prop)}"
                    )
                if sock not in self.catalog[prop].sockets:
                    raise StateError("unknown_socket", f"{prop} has no socket {sock}; {self._sockets_list(prop)}")
                if (prop, sock) in seats:
                    raise StateError(
                        "socket_taken",
                        f"{seats[(prop, sock)]} and {who} both start in {prop}.{sock}; seat one elsewhere",
                    )
                seats[(prop, sock)] = who
        for what, ps in shot.prop_start.items():
            self._prop(what)
            if ps.state is not None:
                self._require_state(what, ps.state)

        self.now = 0.0
        for p in self.people.values():
            p.busy_until = 0.0
            p.activity = None

        # A new set: props nobody brings along leave the stage, and unlisted cast go offstage.
        if not same_set:
            self._clear_stale_props(shot, held_by)
            for who, p in self.people.items():
                if who not in shot.start:
                    self._unseat(who)
                    p.pose = "stand"
                    p.pos = OFFSTAGE_LEFT
                    p.facing = 1

        # Listed cast: position, facing, pose. Everyone listed is unseated first, so seat
        # swaps between shots work.
        for who, s in shot.start.items():
            p = self.people[who]
            self._unseat(who)
            p.pos = mark(s.at)
            if s.facing is not None:
                p.facing = -1 if s.facing == "left" else 1
            p.pose = "stand"

        # Holdings: take the prop from any previous holder. A worn head item (a signature item or
        # a hat) leaves the head silently, for another puppet's hand or the wearer's own; the head
        # never counts as a busy hand. prop_start below likewise takes a worn item off the head.
        for what, who in held_by.items():
            h = self.holder(what)
            if h is not None and h[0] == who and h[1] in HANDS:
                continue
            riders = self._riders(what)
            if riders:
                raise StateError("occupied", f"{riders[0]} is sitting on {what}; start {riders[0]} somewhere else")
            hand = self.free_hand(who)
            if hand is None:
                raise StateError("hands_full", self._hands_full_reason(who))
            self._attach_puppet(what, who, hand)

        self._apply_prop_start(shot, mark)

        # Seats last, so a seat can be on a prop that prop_start just placed.
        for (prop, sock), who in seats.items():
            if self.props[prop].world_pos is None:
                raise StateError("not_on_stage", f"{prop} is not on stage in this shot; {PLACE_WITH_PROPSTART}")
            occ = self.props[prop].occupants.get(sock)
            if occ is not None and occ != f"puppet:{who}":
                raise StateError("socket_taken", f"{prop}.{sock} is taken by {occ.partition(':')[2]}")
            self._seat(who, prop, sock)
        for who, s in shot.start.items():
            if s.pose is not None:
                self.people[who].pose = s.pose

    def _clear_stale_props(self, shot: Shot, held_by: dict[str, str]) -> None:
        """On a set change, every prop that is not carried in by a listed puppet, not worn on
        anyone's head, not placed by prop_start (``at`` or ``on``) and not in a start `holding`
        leaves the stage (parent ground, world_pos None), and anything seated on it is unseated.
        Props riding on a carried prop come along.

        A prop_start entry that only sets a state does not keep the prop: it says nothing about
        where the prop is on the new set, so it must not stay at the old set's coordinates (or
        in the hand of someone waiting offstage).

        Head items (a signature item, or a hat put on with put_on) stay on the head even for
        unlisted cast, who wait offstage still wearing them; only their hand-held props stay behind.
        """
        placed = {what for what, ps in shot.prop_start.items() if ps.at is not None or ps.on is not None}
        keep = placed | set(held_by)
        carried = [what for who in shot.start for what in self.people[who].holding.values()]
        carried += [p.holding[HEAD] for who, p in self.people.items() if who not in shot.start and HEAD in p.holding]
        for what in carried:
            keep.add(what)
            keep.update(self._descendants(what))
        stale = [what for what in self.props if what not in keep]
        for what in stale:
            st = self.props[what]
            for occ in list(st.occupants.values()):
                kind, _, oid = occ.partition(":")
                if kind == "puppet":
                    self._unseat(oid)
            st.occupants.clear()
            self._detach(what)
        # A kept prop that rested on a stale one is set loose (prop_start may place it again).
        for what in self.props:
            parsed = parse_parent(self.props[what].parent)
            if what in keep and parsed is not None and parsed[0] == "prop" and parsed[1] not in keep:
                self._detach(what)

    def _apply_prop_start(self, shot: Shot, mark) -> None:
        """Place every prop_start prop. The order of the entries does not matter.

        Every prop that moves (``at`` or ``on``) is first taken off wherever it was, so a socket
        it leaves is free for another entry, whichever comes first in the shot. Then ``at``
        entries go to the ground, ``on`` entries naming a socket take it, and bare ``on`` entries
        take their base's first socket still free. Two entries that really claim one socket fail
        with ``socket_taken`` on the later one; a prop resting on itself, directly or through
        other props, fails with ``cycle`` before any socket is checked (on the later entry of
        the loop, as a socket conflict does).
        """
        ons: list[tuple[str, str, str]] = []  # (prop, base, socket or "")
        for what, ps in shot.prop_start.items():
            if ps.on is not None:
                base, _, sock = ps.on.partition(".")
                self._prop(base)
                if base == what:
                    raise StateError("cycle", f"{what} cannot rest on itself")
                ons.append((what, base, sock))
        moved = [what for what, ps in shot.prop_start.items() if ps.on is not None or ps.at is not None]
        self._check_no_prop_start_cycle(ons, moved)
        for what in moved:
            self._detach(what)
        for what, ps in shot.prop_start.items():
            if ps.on is None and ps.at is not None:
                self._to_ground(what, mark(ps.at))
        for what, base, sock in [o for o in ons if o[2]] + [o for o in ons if not o[2]]:
            if not sock:
                free = [k for k in self.catalog[base].sockets if k not in self.props[base].occupants]
                if not free:
                    raise StateError("socket_taken", f"every socket on {base} is taken")
                sock = free[0]
            self._check_socket_for_prop(base, sock, what)
            self._attach_prop(what, base, sock)
        for what, ps in shot.prop_start.items():
            if ps.state is not None:
                self.props[what].state = ps.state
        # Checked after every prop_start is applied, so their order in the shot does not matter.
        for what, base, _ in ons:
            if not self._placed(base):
                raise StateError("not_on_stage", f"{base} is not on stage in this shot; {PLACE_WITH_PROPSTART}")

    def _check_no_prop_start_cycle(self, ons: list[tuple[str, str, str]], moved: list[str]) -> None:
        """``cycle`` when the props would rest on each other once every prop_start entry is
        applied: each ``on`` entry's prop rests on its base, a moved ``at`` entry on the ground,
        and every other prop where it is now. The last entry in the loop is named, so for
        ``{table: on crate.top, crate: on table.top}`` it is ``crate cannot rest on table, which
        rests on crate``, whatever else the shot gets wrong."""
        final: dict[str, str | None] = {}
        for what, st in self.props.items():
            parsed = parse_parent(st.parent)
            final[what] = parsed[1] if parsed is not None and parsed[0] == "prop" else None
        for what in moved:
            final[what] = None
        for what, base, _ in ons:
            final[what] = base
        for what, base, _ in reversed(ons):
            seen: set[str] = set()
            cur: str | None = base
            while cur is not None and cur not in seen:
                if cur == what:
                    raise StateError("cycle", f"{what} cannot rest on {base}, which rests on {what}")
                seen.add(cur)
                cur = final.get(cur)

    def _check_no_cycle(self, what: str, base: str) -> None:
        """``cycle`` when ``what`` would rest on itself or on a prop that rests on it."""
        if base == what:
            raise StateError("cycle", f"{what} cannot rest on itself")
        if base in set(self._descendants(what)):
            raise StateError("cycle", f"{what} cannot rest on {base}, which rests on {what}")

    def _check_socket_for_prop(self, base: str, socket: str, what: str) -> None:
        if socket not in self.catalog[base].sockets:
            raise StateError("unknown_socket", f"{base} has no socket {socket}; {self._sockets_list(base)}")
        occ = self.props[base].occupants.get(socket)
        if occ is not None and occ != f"prop:{what}":
            raise StateError("socket_taken", f"{base}.{socket} is taken by {occ.partition(':')[2]}")

    # --------------------------------------------------------------- movement

    def move(self, who: str, pos: Vec3, facing: int | None = None) -> None:
        """Walk/run/enter/exit/follow. Kneeling or sitting on the floor ends; seated puppets cannot move."""
        p = self._person(who)
        if p.seat is not None:
            raise StateError("seated", self.seat_reason(who))
        if facing is not None and facing not in (1, -1):
            raise StateError("bad_facing", f"facing must be 1 or -1, not {facing}")
        p.pos = _vec(pos)
        if facing is not None:
            p.facing = facing
        if p.pose in SEATED_POSES:
            p.pose = "stand"

    def set_facing(self, who: str, facing: int) -> None:
        p = self._person(who)
        if facing not in (1, -1):
            raise StateError("bad_facing", f"facing must be 1 or -1, not {facing}")
        p.facing = facing

    def set_pose(self, who: str, pose: str) -> None:
        """`pose` verb: the logical pose name the puppet holds afterwards."""
        self._person(who).pose = pose

    def check_free(self, who: str, at: float | None = None) -> None:
        """Raise ``busy`` when ``who`` is still busy at ``at`` (default now). The reason shows
        ``busy_until + busy_time_offset``."""
        p = self._person(who)
        t = self.now if at is None else at
        if t < p.busy_until - TIME_EPS:
            shown = p.busy_until + self.busy_time_offset if self.busy_time_offset else p.busy_until
            raise StateError("busy", f"{who} is still {p.activity or 'busy'} until t={fmt_t(shown)}")

    def occupy(self, who: str, until: float, activity: str, at: float | None = None) -> None:
        """Mark `who` busy with `activity` (an -ing word, e.g. 'walking') from `at` (default now) to `until`."""
        self.check_free(who, at)
        p = self.people[who]
        p.busy_until = float(until)
        p.activity = activity

    def exit(self, who: str, pos: Vec3 | None = None) -> Vec3:
        """Leave the stage. Default: the nearest side. Returns the offstage position."""
        p = self._person(who)
        if p.seat is not None:
            raise StateError("seated", self.seat_reason(who))
        if pos is None:
            pos = OFFSTAGE_LEFT if p.pos[0] < 0 else OFFSTAGE_RIGHT
        pos = _vec(pos)
        p.facing = 1 if pos[0] > p.pos[0] else -1 if pos[0] < p.pos[0] else p.facing
        p.pos = pos
        if p.pose in SEATED_POSES:
            p.pose = "stand"
        return pos

    # ------------------------------------------------------------------- props

    def pick_up(self, who: str, what: str, socket: str | None = None) -> str:
        p = self._person(who)
        self._prop(what)
        h = self.holder(what)
        if h is not None:
            if h[0] == who:
                verb = "wearing" if h[1] == HEAD else "holding"
                raise StateError("already_held", f"{who} is already {verb} it")
            raise StateError("already_held", self._where(what, who))
        self._require_on_stage(who)
        self._require_placed(what)
        self._check_takeable(who, what)
        if socket is not None:
            if socket not in HANDS:
                raise StateError("bad_socket", f"{socket} is not a hand; use hand_r or hand_l")
            if socket in p.holding:
                raise StateError("hands_full", f"{who}'s {socket} is holding {p.holding[socket]}")
        else:
            socket = self.free_hand(who)
            if socket is None:
                raise StateError("hands_full", self._hands_full_reason(who))
        self._attach_puppet(what, who, socket)
        return socket

    def _check_takeable(self, who: str, what: str) -> None:
        """Seat rules for taking a prop off the ground or a surface (pick_up, put_on).

        Nobody may sit on it or on anything stacked on it (the taker included), and a puppet
        seated in a vehicle cannot reach it.
        """
        st = self.props[what]
        direct = [o.partition(":")[2] for o in st.occupants.values() if o.startswith("puppet:")]
        if direct:
            raise StateError("occupied", f"{direct[0]} is sitting on it")
        for sub in self._descendants(what):
            for occ in self.props[sub].occupants.values():
                kind, _, oid = occ.partition(":")
                if kind == "puppet":
                    raise StateError("occupied", f"{oid} is sitting on {sub}, which rests on it")
        p = self.people[who]
        if p.seat is not None and self.is_vehicle(p.seat[0]):
            raise StateError("seated_vehicle", self.seat_reason(who))

    def hold(self, who: str, what: str) -> str:
        """Raise a prop already in hand into the hold pose. Returns its hand."""
        self._person(who)
        self._prop(what)
        hand = self._require_in_hand(who, what)
        self._require_on_stage(who)
        return hand

    def drop(self, who: str, what: str, landing: Vec3) -> None:
        self._person(who)
        self._prop(what)
        self._require_in_hand(who, what)
        self._require_on_stage(who)
        self._to_ground(what, landing)

    def hand_to(self, who: str, what: str, to: str) -> str:
        """Hand a prop to another puppet. Returns the taker's socket."""
        self._person(who)
        self._prop(what)
        self._person(to)
        if to == who:
            raise StateError("self_hand_to", self_target_reason("to", who))
        self._require_in_hand(who, what)
        self._require_on_stage(who, to)
        hand = self.free_hand(to)
        if hand is None:
            raise StateError("hands_full", self._hands_full_reason(to))
        self._attach_puppet(what, to, hand)
        return hand

    def put_on(self, who: str, what: str, target: str | None = None) -> None:
        """Wear a prop in the head socket of `target` (default: who).

        The prop comes from one of who's hands, or straight off the ground (which needs a free hand).
        """
        self._person(who)
        st = self._prop(what)
        target = who if target is None else target
        t = self._person(target)
        h = self.holder(what)
        if h is not None and h == (target, HEAD):
            raise StateError("already_held", f"{target} is already wearing it")
        if h is not None and (h[0] != who or h[1] not in HANDS):
            raise StateError("not_holding", self._where(what, who))
        if h is None and st.parent != GROUND:
            raise StateError("not_holding", self._where(what, who))
        self._require_on_stage(who)
        if h is None:
            self._require_placed(what)
            self._check_takeable(who, what)
            if self.free_hand(who) is None:
                raise StateError("hands_full", self._hands_full_reason(who))
        if HEAD in t.holding:
            raise StateError("head_full", f"{target} is already wearing {t.holding[HEAD]}")
        if target != who:
            self._require_on_stage(target)
        self._attach_puppet(what, target, HEAD)

    def place_on(self, who: str, what: str, parent: str, pos: Vec3) -> None:
        """Put a held prop down: on the ground (pos = world anchor) or on 'prop:<id>.<socket>' (pos = offset)."""
        self._person(who)
        self._prop(what)
        self._require_in_hand(who, what)
        self._require_on_stage(who)
        parsed = parse_parent(parent)
        if parsed is None:
            self._to_ground(what, pos)
            return
        kind, base, sock = parsed
        if kind != "prop":
            raise StateError("bad_parent", f"{parent} is not the ground or a prop socket")
        self._prop(base)
        self._check_no_cycle(what, base)
        self._check_socket_for_prop(base, sock, what)
        self._attach_prop(what, base, sock, pos)

    def set_prop_state(self, what: str, state: str) -> None:
        self._prop(what)
        self._require_state(what, state)
        self.props[what].state = state

    def open(self, who: str, what: str) -> None:
        """`open` verb: the prop's state becomes "open"."""
        self._open_close(who, what, OPEN)

    def close(self, who: str, what: str) -> None:
        """`close` verb: the prop's state becomes "closed"."""
        self._open_close(who, what, CLOSED)

    def _open_close(self, who: str, what: str, state: str) -> None:
        self._person(who)
        st = self._prop(what)
        self._require_on_stage(who)
        self._require_placed(what)
        self._require_state(what, state)
        if st.state == state:
            raise StateError(f"already_{state}", f"it is already {state}")
        st.state = state

    # --------------------------------------------------------------- seating

    def sit_on(self, who: str, what: str, socket: str | None = None) -> str:
        p = self._person(who)
        self._prop(what)
        if self.is_vehicle(what):
            raise StateError("vehicle_seat", f"{what} is a vehicle; use get_in")
        if p.seat is not None:
            raise StateError("already_seated", self.seat_reason(who))
        sockets = self.catalog[what].sockets
        occ = self.props[what].occupants
        if socket is None:
            if not sockets:
                raise StateError("no_seat", f"{what} has no sockets to sit on")
            if "seat" in sockets:
                socket = "seat"
            else:
                free = [k for k in sockets if k not in occ]
                seatish = [k for k in free if "seat" in k]
                if not free:
                    raise StateError("no_seat", f"every seat on {what} is taken")
                socket = (seatish or free)[0]
        self._check_socket(what, socket, who)
        self._seat(who, what, socket)
        return socket

    def stand_up(self, who: str) -> None:
        p = self._person(who)
        if p.seat is None and p.pose not in SEATED_POSES:
            raise StateError("not_sitting", f"{who} is not sitting or kneeling")
        if p.seat is not None and self.is_vehicle(p.seat[0]):
            raise StateError("seated_vehicle", self.seat_reason(who))
        self._unseat(who)
        p.pose = "stand"

    def kneel(self, who: str) -> None:
        p = self._person(who)
        if p.seat is not None:
            raise StateError("seated", self.seat_reason(who))
        p.pose = "kneel"

    def get_in(self, who: str, what: str, socket: str | None = None) -> str:
        p = self._person(who)
        self._prop(what)
        if not self.is_vehicle(what):
            raise StateError("not_a_vehicle", f"{what} is not a vehicle")
        if p.seat is not None:
            raise StateError("already_seated", self.seat_reason(who))
        sockets = self.catalog[what].sockets
        occ = self.props[what].occupants
        if socket is None:
            order = (["driver"] if "driver" in sockets else []) + [k for k in sockets if k != "driver"]
            free = [k for k in order if k not in occ]
            if not free:
                raise StateError("no_free_seat", f"every seat in {what} is taken")
            socket = free[0]
        self._check_socket(what, socket, who)
        self._seat(who, what, socket)
        return socket

    def get_out(self, who: str, what: str) -> None:
        p = self._person(who)
        self._prop(what)
        if p.seat is None or p.seat[0] != what:
            raise StateError("not_in_vehicle", f"{who} is not in {what}; use get_in first")
        self._unseat(who)
        p.pose = "stand"

    def check_driver(self, who: str, what: str) -> None:
        p = self._person(who)
        self._prop(what)
        if p.seat != (what, "driver"):
            if p.seat is not None and p.seat[0] == what:
                raise StateError("not_driver", f"{who} is in {what}.{p.seat[1]}, not {what}.driver")
            raise StateError("not_driver", f"{who} is not in {what}.driver; use get_in with socket driver first")

    def check_rider(self, who: str, what: str) -> None:
        """`who` may ride_to in `what`: seated in it, or on a prop resting on it (directly or
        through other props), and someone is in its driver's seat."""
        p = self._person(who)
        self._prop(what)
        if p.seat is None or (p.seat[0] != what and what not in self.supports(p.seat[0])):
            raise StateError("not_in_vehicle", f"{who} is not in {what}; use get_in first")
        if not self.props[what].occupants.get("driver", "").startswith("puppet:"):
            raise StateError("no_driver", f"nobody is in {what}.driver; someone must get_in it with socket driver first")

    def move_vehicle(self, what: str, pos: Vec3) -> None:
        """Move a prop on the ground; stacked props and puppets seated on it, or on anything
        stacked on it, follow."""
        st = self._prop(what)
        if st.parent != GROUND:
            raise StateError("not_on_ground", self._where(what))
        self._require_placed(what)
        pos = _vec(pos)
        old = st.pos
        delta = (pos[0] - old[0], pos[1] - old[1], pos[2] - old[2])
        st.pos = pos
        st.world_pos = pos
        for _, _, oid in self.riders(what):
            q = self.people[oid]
            q.pos = (q.pos[0] + delta[0], q.pos[1] + delta[1], q.pos[2] + delta[2])
        self._refresh_world(what)

    # ----------------------------------------------------------------- people

    def hug_check(self, who: str, target: str) -> None:
        self._person(who)
        self._person(target)
        if who == target:
            raise StateError("hug_self", self_target_reason("target", who))
        self._require_on_stage(who, target)
        for w in (who, target):
            if self.people[w].seat is not None:
                raise StateError("seated", self.seat_reason(w))
