"""Screenplay -> timeline, plus machine-readable errors for the agent (ARCHITECTURE.md section 3).

``compile(screenplay, sets=..., props=..., people=...)`` runs the whole pipeline:

1. Static validation (validate.py): every verb, parameter, mark, prop, person, pose, cue and
   camera move against shared/vocabulary.json and the scene. Any error stops here.
2. For each shot, in order: apply its start block to the world state, raise the set (unless
   the previous shot used the same set), key where everyone starts, retime cued actions onto
   their narration words, run each action through its verb handler (state + keys) in schedule
   order (below), move the camera, time the narration, and work out when the shot ends and the
   next one starts (the outgoing shot's transition folds its set).
   On a later shot of the same set, a prop that propStart re-places holds its old position,
   parent and state up to the shot's start and steps there, and a puppet still seated in it
   (or in a prop resting on it) and not listed in the start rides along to its seat's new place.
3. Join the tellings, carry interactions (a scrub's from/to follow cue-moved actions) and
   explanations, collect the effects, and return the Timeline, or every error found as exact
   CompileError messages such as
   ``shot s1, t=5: dad cannot hand_to "hymn_books"; mom is holding it``.

Effects (confetti, steam, rain, smoke, time_of_day) have no verb handler: effects.effect_for
turns each into a ``Timeline.effects`` entry at timeline time, its ``at`` placed by the shot's
locate. They occupy nobody, but still go through the schedule, so an effect ``at`` a puppet or
prop reads it as of its own time. A shot's end covers every effect: an effect counts like a key
at ``t + dur`` (rain with no ``dur`` at its start, and it then lasts until the shot's end, its
outgoing fold included). ``Timeline.effects`` is ordered by (t, shot, action index).

It never raises for a bad screenplay, and the same input gives byte-identical JSON.

Schedule order within a shot (``_schedule``): the earliest runnable action, by (retimed t, list
index), runs next. An action is runnable only once every earlier action that touches the same
puppet or prop (its ``who`` or a cast member or prop it names) has run, so it reads everything
as of its own time: a puppet's own actions keep their order, and an action on a follower (mom
hands dad the books while dad follows kid) sees the follower where the follow put him, never a
stale position. A ``follow`` of a cast member is runnable only once every action that can move
its leader has run (the leader's own actions except STILL_VERBS such as wave or point_at, and
any drive_to / ride_to of a vehicle while the leader sits in it or on a prop resting on it), so
the follower sees the leader's whole path; the follower's later actions wait behind the follow.
Without follows this is plain time order. When nothing can run, the waits form a loop through
a follow: a loop of follows only (puppets that follow one another round a loop) is reported as
``follow_cycle`` on its earliest follow, and any other loop as ``follow_order`` on the move of
the leader that needs its follower first (kid hugs dad while dad follows kid). The reported
action is skipped and the rest of the shot is scheduled as before. An action that only waits on
a loop is not part of it and is not reported.

A follow's window: a follow with ``dur`` only moves its follower from ``t`` to ``t + dur``, so
waiting for a leader move that starts after that can make a false loop (dad follows kid for 3 s
at t=1, hands kid the books at t=8, and kid walks off at t=12: the follow waits for the walk,
which waits for the hand-over, which waits for the follow). Each shot is first scheduled with
every follow waiting for every move of its leader, as above. Only when that leaves something out
is it scheduled again with windows (``_schedule(windows=True)``): a follow with ``dur`` then
waits only for the leader's moves that start before ``t + dur`` and reads the leader's path only
up to then (``_LeaderWindow``), so a later leader move changes nothing whether it has run or
not. A shot that schedules without a loop runs exactly as it always has, each follow reading
its leader's whole path: the handlers read past ``t + dur`` in one place (a leader that stands
still all window long and moves only later is held behind, or walked up to when that later move
leaves its ground path unchanged), and a windowed follow cannot tell those apart.

Because of that reordering, ``World.busy_until`` only describes the action running now. The
compiler keeps every puppet's busy spans in shot time and, before each handler, sets each
puppet's ``busy_until`` / ``activity`` to the span covering ``world.now`` (if any). A new span
that runs into a span the puppet starts later is reported as ``busy`` too. Spans are in
cue-retimed shot time, but a busy message states its times in the blocked action's written
frame (``World.busy_time_offset``), like the ``t=`` in its head.

Verb handlers come from every ``screenplay.verbs_*`` module's ``HANDLERS`` dict (verbs_core
first, then the rest by module name), so a new verb module only has to exist to be used.
A verb with no handler is reported as ``unsupported_verb``. Discovery never raises: a module
that fails to import, or a verb two modules both claim, is logged and listed in
``HANDLER_PROBLEMS`` (the tests require it to be empty).
"""

from __future__ import annotations

import importlib
import logging
import pkgutil
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from itertools import pairwise

import schemas
import screenplay as _pkg
from screenplay import body, character, effects, ik, layout, motion, verbs_core
from screenplay import camera as camera_mod
from screenplay import errors as E
from screenplay import narration as N
from screenplay.locate import BONES, Locate, arm_of_hand, make_locate
from screenplay.schema import (
    Action,
    CompileError,
    CompileResult,
    NarrationLine,
    Screenplay,
    Shot,
    Timeline,
    TimelineEffect,
    TimelineShot,
)
from screenplay.state import (
    GROUND,
    HANDS,
    HEAD,
    OFFSTAGE_LEFT,
    TIME_EPS,
    PuppetState,
    StateError,
    World,
    fmt_t,
)
from screenplay.tracks import Key, TrackSet, is_step, round_value
from screenplay.validate import validate

Vec3 = tuple[float, float, float]
log = logging.getLogger(__name__)

# An -ing word for every verb, for "dad is still walking until t=3.2".
ACTIVITY: dict[str, str] = {
    "walk_to": "walking",
    "run_to": "running",
    "enter": "entering",
    "exit": "exiting",
    "turn_to": "turning",
    "follow": "following",
    "pose": "posing",
    "wave": "waving",
    "point_at": "pointing",
    "sit_on": "sitting down",
    "stand_up": "standing up",
    "kneel": "kneeling",
    "hug": "hugging",
    "lean_toward": "leaning",
    "pick_up": "picking up",
    "hold": "holding up",
    "drop": "dropping",
    "hand_to": "handing over",
    "put_on": "putting on",
    "place_on": "placing",
    "open": "opening",
    "close": "closing",
    "set_state": "changing",
    "get_in": "getting in",
    "get_out": "getting out",
    "drive_to": "driving",
    "ride_to": "riding",
    "confetti": "throwing confetti",
    "steam": "steaming",
    "rain": "raining",
    "smoke": "smoking",
    "time_of_day": "changing the light",
}


def _discover_handlers() -> tuple[dict[str, verbs_core.Handler], list[str]]:
    """verbs_core.HANDLERS, then every other ``screenplay.verbs_*`` module's, by module name.

    Never raises, so a broken verb module cannot stop the agent's compile tool from loading:
    a module that fails to import is skipped (its verbs then compile as ``unsupported_verb``),
    and a verb claimed by two modules keeps the first owner. Each problem is logged and
    returned; tests/screenplay/test_compiler.py asserts there are none in the real package.
    """
    handlers: dict[str, verbs_core.Handler] = dict(verbs_core.HANDLERS)
    owner = dict.fromkeys(handlers, "verbs_core")
    problems: list[str] = []
    names = sorted(m.name for m in pkgutil.iter_modules(_pkg.__path__) if m.name.startswith("verbs_"))
    for name in names:
        if name == "verbs_core":
            continue
        try:
            module = importlib.import_module(f"screenplay.{name}")
        except Exception as e:  # noqa: BLE001 - any import failure in another stream's module
            problems.append(f"screenplay.{name} failed to import: {type(e).__name__}: {e}")
            continue
        for verb, fn in getattr(module, "HANDLERS", {}).items():
            if verb in handlers:
                problems.append(f"verb {verb!r} has handlers in both {owner[verb]} and {name}; using {owner[verb]}")
                continue
            handlers[verb] = fn
            owner[verb] = name
    for problem in problems:
        log.warning("screenplay.compiler: %s", problem)
    return handlers, problems


HANDLERS: dict[str, verbs_core.Handler]
HANDLER_PROBLEMS: list[str]
HANDLERS, HANDLER_PROBLEMS = _discover_handlers()

_POSES: dict[str, dict] = ik.POSES["poses"]


# ------------------------------------------------------------------ error bookkeeping

@dataclass
class _Errors:
    """Errors with their sort key: (shot index, action index, order found)."""

    items: list[tuple[tuple[int, int, int], CompileError]] = field(default_factory=list)

    def add(self, err: CompileError, shot_index: int = -1, action_index: int = -1) -> None:
        self.items.append(((shot_index, action_index, len(self.items)), err))

    def __bool__(self) -> bool:
        return bool(self.items)

    def sorted(self) -> list[CompileError]:
        return [e for _, e in sorted(self.items, key=lambda it: it[0])]


# ------------------------------------------------------------------- schedule and busy

@dataclass(frozen=True)
class _Span:
    """A puppet is busy with ``activity`` from ``start`` to ``end`` (shot time)."""

    start: float
    end: float
    activity: str


Spans = dict[str, list[_Span]]


# Action fields that can name another puppet or a prop (marks and offstage sides are not shared).
_REF_FIELDS = ("to", "target", "at", "on", "what")

# Verbs that never change their puppet's root.position (they turn it, move its arms or torso,
# or change a prop). A follow reads only its leader's root.position path, so it does not wait
# for these. Every other verb, including any verb added later, counts as moving its puppet.
# tests/screenplay/test_compiler_schedule.py checks every ok verb case of these verbs leaves
# the actor's root where it was.
STILL_VERBS = frozenset({
    "turn_to", "wave", "point_at", "lean_toward",
    "hold", "drop", "hand_to", "put_on", "place_on", "open", "close", "set_state",
})
# Verbs that move everyone seated in their vehicle (``what``), not only their own puppet.
_VEHICLE_MOVES = frozenset({"drive_to", "ride_to"})
# Verbs that take their prop (``what``) off whatever it rested on: into a hand, onto a head,
# or onto the ground.
_LIFTS = frozenset({"pick_up", "put_on", "drop"})


def _follows_a_leader(action: Action, cast: Mapping[str, object]) -> str | None:
    """The leader a ``follow`` waits for: its target, when that is another cast member."""
    if action.do == "follow" and action.target is not None and action.target in cast and action.target != action.who:
        return action.target
    return None


def _touches(action: Action, cast: Mapping[str, object], props: Collection[str]) -> frozenset[str]:
    """The puppets and props an action reads or changes: its ``who`` and every cast member or
    prop it names. A follow touches only its follower; its leader is waited for separately."""
    names = {action.who} if action.who is not None else set()
    if action.do != "follow":
        for name in (getattr(action, f) for f in _REF_FIELDS):
            if name is not None and (name in cast or name in props):
                names.add(name)
    return frozenset(names)


SeatedIn = Mapping[str, "str | Sequence[str]"]
"""Who sits on what at a shot's start: a puppet -> its seat prop, or the tuple (seat prop, the
prop that rests on, ..., down to the one on the ground) (``World.supports``). A plain string
is a seat with nothing under it."""


def _seat_chain(value: str | Sequence[str] | None) -> tuple[str, ...]:
    if value is None:
        return ()
    return (value,) if isinstance(value, str) else tuple(value)


def _moves(
    actions: list[Action],
    who: str,
    seated_in: SeatedIn,
    by_time: list[int],
    rests_on: Mapping[str, Sequence[str]] | None = None,
    vehicles: Collection[str] = (),
) -> list[int]:
    """Every action in the shot that can move ``who``'s root, in ``by_time`` order: its own
    actions except the STILL_VERBS, and every drive_to / ride_to of a vehicle that carries
    ``who`` when it runs. A vehicle carries ``who`` while ``who`` sits in it, or on a prop
    resting on it, directly or through other props (a chair on a pickup's bed).

    ``who``'s seat: the one it starts the shot on (``seated_in``), then the vehicle of its
    get_in until its get_out, or the prop of its sit_on until its stand_up. A stand_up from a
    seat in one of ``vehicles`` changes nothing (the world refuses it: that takes a get_out).
    What each prop rests on starts as ``rests_on`` (the shot's start, ``World.supports``; a
    ``seated_in`` chain fills in the rest) and follows the shot's place_on (onto a prop's
    socket, or a mark), pick_up, put_on and drop, so a chair put on the sedan's bed mid-shot
    rides with it from then on, and one lifted off no longer does. A drive before ``who`` is
    on a seat over the vehicle, or after it is off, leaves ``who`` where it is.

    ``by_time`` is the (retimed t, index) order. It is also the order those actions run in:
    the get_in, get_out, drive and a place_on onto the vehicle all name the vehicle, so they
    keep time order."""
    under: dict[str, str] = {}  # prop -> the prop it rests on, as the shot plays out
    for what, chain in (rests_on or {}).items():
        for a, b in pairwise((what, *chain)):
            under.setdefault(a, b)
    for value in seated_in.values():
        for a, b in pairwise(_seat_chain(value)):
            under.setdefault(a, b)

    def carries(vehicle: str) -> bool:
        cur, seen = seat, set()
        while cur is not None and cur not in seen:
            if cur == vehicle:
                return True
            seen.add(cur)
            cur = under.get(cur)
        return False

    chain = _seat_chain(seated_in.get(who))
    seat: str | None = chain[0] if chain else None
    out: list[int] = []
    for i in by_time:
        a = actions[i]
        if a.who == who:
            if a.do not in STILL_VERBS:
                out.append(i)
            if a.do in ("get_in", "sit_on") and a.what is not None:
                seat = a.what
            elif a.do == "get_out" and seat is not None and a.what == seat:
                seat = None
            elif a.do == "stand_up" and seat is not None and seat not in vehicles:
                seat = None
        elif a.do in _VEHICLE_MOVES and a.what is not None and carries(a.what):
            out.append(i)
        if a.what is not None:
            if a.do == "place_on" and a.on is not None:
                under[a.what] = a.on  # a mark is never a seat's support: the chain just ends there
            elif a.do in _LIFTS:
                under.pop(a.what, None)
    return out


def _window_end(action: Action, shot_t: float) -> float | None:
    """Where a follow's window ends (shot time): ``t + dur``, as verbs_motion.apply_follow
    clips the follow (any stand-up time counts inside dur). None for a follow without dur, or
    any other action."""
    if action.do != "follow" or action.dur is None:
        return None
    return shot_t + action.dur


def _in_window(retimed_j: float, end: float | None) -> bool:
    """A leader move at ``retimed_j`` can change a follow whose window ends at ``end``."""
    return end is None or retimed_j < end - TIME_EPS


def _called(action: Action) -> str:
    """``dad's hand_to at t=2`` (or ``the rain at t=2`` for an action without ``who``)."""
    whose = f"{action.who}'s" if action.who is not None else "the"
    return f"{whose} {action.do} at t={fmt_t(action.t)}"


def _cycle_reason(actions: list[Action], loop: list[int]) -> str:
    """Why a follow cycle cannot run, naming every puppet on it."""
    if len(loop) == 2:
        a = actions[loop[0]]
        return f"{a.who} and {a.target} follow each other; only one can lead"
    links = [f"{actions[i].who} follows {actions[i].target}" for i in loop]
    return f"{', '.join(links[:-1])} and {links[-1]}; only one can lead"


def _order_reason(actions: list[Action], loop: list[int], is_follow: set[int]) -> str:
    """Why ``loop[0]`` cannot run: one readable sentence for the directing agent.

    ``loop`` is a waits-for cycle, each action waiting on the next, that starts with an
    action some follow waits for (it moves that follow's leader) and that itself waits, maybe
    through other actions, on a follow. For example::

        kid's hug at t=3 needs dad where dad's follow of kid at t=1 leaves dad, but that
        follow is only worked out after everything that moves kid, this hug included; move
        the hug before t=1, or drop the follow
    """
    blocked = actions[loop[0]]
    k = 1
    between: list[Action] = []
    while loop[k] not in is_follow:  # what the blocked action waits on, up to the follow
        between.append(actions[loop[k]])
        k += 1
    gate = actions[loop[k]]
    links: list[Action] = []
    while loop[k % len(loop)] in is_follow:  # the follows that wait on one another
        links.append(actions[loop[k % len(loop)]])
        k += 1
    mover = actions[loop[k % len(loop)]]
    leader = links[-1].target
    follow = f"{gate.who}'s follow of {gate.target} at t={fmt_t(gate.t)}"
    if between:
        need = "has to come after " + ", which comes after ".join(_called(a) for a in between) + f", which comes after {follow}"
    else:
        need = f"needs {gate.who} where {follow} leaves {gate.who}"
    chain = "that follow is"
    if len(links) > 1:
        chain = f"{' and '.join(f'{a.who} follows {a.target}' for a in links)}, so that follow is"
    included = f"this {blocked.do}" if mover is blocked else _called(mover)
    drop = "drop the follow" if len(links) == 1 else "drop one of those follows"
    return (
        f"{_called(blocked)} {need}, but {chain} only worked out after everything that moves "
        f"{leader}, {included} included; move the {blocked.do} before t={fmt_t(gate.t)}, or {drop}"
    )


def _schedule(
    actions: list[Action],
    retimed: list[float],
    cast: Mapping[str, object],
    props: Collection[str] = (),
    seated_in: SeatedIn | None = None,
    rests_on: Mapping[str, Sequence[str]] | None = None,
    vehicles: Collection[str] = (),
    windows: bool = True,
) -> tuple[list[int], list[tuple[int, str, str]]]:
    """The order to run a shot's actions in, and the actions left out: (index, code, reason).

    ``seated_in`` maps a puppet to the seat it starts the shot in, with every prop that seat
    rests on (``SeatedIn``); ``rests_on`` maps a prop to the props it rests on at the shot's
    start, and ``vehicles`` names the vehicles (see ``_moves``). The earliest runnable action,
    by (retimed t, index), runs next. An action is runnable when

    - every earlier action (by retimed t, index) that touches one of its puppets or props has
      run, so whatever it reads (where a puppet stands, who holds a prop) is as of its own
      time; that includes every earlier action of its own puppet; and
    - for a follow of a cast member, every action that can move the leader inside the
      follow's window has run (``_moves``), so the follower sees the leader's whole path. The
      leader's still actions (a wave, a point_at, a hand_to), and a drive of a vehicle that
      does not carry the leader at the time (it is not in it, nor on a prop resting on it),
      do not hold the follow back. A follow with ``dur`` only reads its leader's path up to
      ``t + dur``, so it waits only for the leader's moves that start before then; a later
      move keeps plain time order with it (it starts after the follow, so it runs after it
      unless they share nothing). A follow without ``dur`` waits for every move of its
      leader. With ``windows`` False every follow does (the rule compile() tries first).

    Without follows that is plain time order. When nothing can run, the waits form a loop
    that goes through a follow's wait for its leader. A loop of follows only is reported as
    ``follow_cycle`` on its earliest follow. Any other loop is reported as ``follow_order``
    on the earliest action on it that a follow waits for (say the leader hugs its own
    follower). Each reported action is left out and the rest are scheduled as before; an
    action that only waits on a loop is not reported.
    """
    seated_in = seated_in or {}
    n = len(actions)
    by_time = sorted(range(n), key=lambda i: (retimed[i], i))
    rank = {i: r for r, i in enumerate(by_time)}
    touches = [_touches(a, cast, props) for a in actions]
    movers: list[list[int]] = []
    for i, a in enumerate(actions):
        leader = _follows_a_leader(a, cast)
        if leader is None:
            movers.append([])
            continue
        end = _window_end(a, retimed[i]) if windows else None
        movers.append([j for j in _moves(actions, leader, seated_in, by_time, rests_on, vehicles)
                       if _in_window(retimed[j], end)])
    done: set[int] = set()
    order: list[int] = []
    left_out: list[tuple[int, str, str]] = []

    def blocked_by(i: int) -> tuple[int, bool] | None:
        """What ``i`` waits for: (an action not yet run, whether it is a follow's leader wait)."""
        for j in movers[i]:
            if j not in done:
                return j, True
        for j in by_time[:rank[i]]:
            if j not in done and touches[j] & touches[i]:
                return j, False
        return None

    while len(done) < n:
        waiting = [i for i in by_time if i not in done]
        waits = {i: blocked_by(i) for i in waiting}
        runnable = next((i for i in waiting if waits[i] is None), None)
        if runnable is not None:
            done.add(runnable)
            order.append(runnable)
            continue
        succ = {i: w[0] for i, w in waits.items() if w is not None}
        is_follow = {i for i, w in waits.items() if w is not None and w[1]}
        reports: list[tuple[int, str, str]] = []
        seen: set[int] = set()
        for start in waiting:
            path: list[int] = []
            j = start
            while j not in seen and j not in path:
                path.append(j)
                j = succ[j]
            seen.update(path)
            if j not in path:
                continue  # this walk only leads into a loop already found
            loop = path[path.index(j):]
            if all(i in is_follow for i in loop):
                code = "follow_cycle"
                first = min(loop, key=rank.__getitem__)
            else:
                # the actions on the loop that a follow waits for, and that wait on something else
                code = "follow_order"
                gated = [loop[k] for k in range(len(loop)) if loop[k - 1] in is_follow and loop[k] not in is_follow]
                first = min(gated, key=rank.__getitem__)
            k = loop.index(first)
            loop = loop[k:] + loop[:k]
            reason = _cycle_reason(actions, loop) if code == "follow_cycle" else _order_reason(actions, loop, is_follow)
            reports.append((first, code, reason))
        done.update(i for i, _, _ in reports)
        left_out.extend(sorted(reports, key=lambda r: rank[r[0]]))
    return order, left_out


@dataclass(frozen=True)
class _FollowWindow:
    """A follow with ``dur`` of cast member ``leader``: it reads the leader's path only up to
    ``t + dur``. ``later``: the leader has a move in the shot (``_moves``) that starts after
    that."""

    leader: str
    later: bool


def _follow_windows(
    actions: list[Action],
    retimed: list[float],
    cast: Mapping[str, object],
    seated_in: SeatedIn | None = None,
    rests_on: Mapping[str, Sequence[str]] | None = None,
    vehicles: Collection[str] = (),
) -> dict[int, _FollowWindow]:
    """The window of every follow of a cast member that has a ``dur``, by action index."""
    seated_in = seated_in or {}
    by_time = sorted(range(len(actions)), key=lambda i: (retimed[i], i))
    out: dict[int, _FollowWindow] = {}
    for i, a in enumerate(actions):
        leader = _follows_a_leader(a, cast)
        end = _window_end(a, retimed[i])
        if leader is None or end is None:
            continue
        moves = _moves(actions, leader, seated_in, by_time, rests_on, vehicles)
        out[i] = _FollowWindow(leader, any(not _in_window(retimed[j], end) for j in moves))
    return out


def _clip_after(keys: list[tuple[float, object]], end: float) -> list[tuple[float, object]]:
    """A raw key list (insertion order) as it reads up to ``end``: the keys before it, by time,
    and the first key at or after it, which ends any move under way at ``end``. A move that
    starts at or after ``end`` is gone."""
    ordered = sorted(keys, key=lambda k: k[0])  # stable: insertion order breaks ties, as TrackSet does
    cut = end - TIME_EPS
    before = [k for k in ordered if k[0] < cut]
    after = [k for k in ordered if k[0] >= cut]
    return before + after[:1]


# The stand-in for a leader's moves after a follow's window (``_LeaderWindow``): a step this
# far in depth (it survives the tracks' 3 dp rounding), this long after the window ends.
_STAND_IN_STEP = 0.01
_STAND_IN_SECONDS = 1.0


class _LeaderWindow(TrackSet):
    """``tracks`` as a follow with ``dur`` reads them: the leader's ``root.position`` ends at the
    window's end (``_clip_after``), so a leader move that starts after the follow ends, and may
    run before or after it, never changes it. Every other track reads as is, and every key the
    follow adds goes straight into ``tracks``.

    ``later`` (``_FollowWindow.later``): the leader moves after the window. Whether that move
    has run yet or not, the path then ends with a stand-in for it: a hold at
    ``max(t + dur, last key)`` and a small depth step ``_STAND_IN_SECONDS`` after that. The
    follow cannot reach it (motion.follow_keys clips at ``t + dur`` and a move only sets the
    follower off ``lag`` after it starts), so it changes nothing the follow does after a leader
    move inside its window; it keeps apply_follow from reporting ``nothing_to_follow`` for a
    leader that does move, only later. Such a leader, standing still all window long, is
    followed by holding still. (A follow that read the real later move would walk up to the
    leader instead when that move leaves the leader's ground path still, a kneel say: that is
    why compile() only uses windows for a shot that loops without them.)"""

    def __init__(self, tracks: TrackSet, leader: str, end: float, later: bool = False) -> None:
        super().__init__()
        self._base = tracks
        self._raw = dict(tracks._raw)  # the same key lists; only the leader's root is replaced
        key = (f"puppet:{leader}", "root.position")
        if key in self._raw:
            clipped = _clip_after(self._raw[key], end)
            if later and clipped:
                last_t, last = clipped[-1]
                x, y, z = (float(c) for c in last)  # type: ignore[union-attr]
                hold = max(end, float(last_t))
                clipped += [(hold, [x, y, z]), (hold + _STAND_IN_SECONDS, [x, y, z + _STAND_IN_STEP])]
            self._raw[key] = clipped

    def add(self, target: str, channel: str, t: float, value: object) -> None:
        self._base.add(target, channel, t, value)


def _set_busy_from_spans(world: World, spans: Spans, now: float) -> None:
    """Each puppet's busy_until / activity from the span covering ``now`` (the one that ends
    last), else free: (0.0, None)."""
    for who, p in world.people.items():
        covering = [s for s in spans.get(who, ()) if s.start - TIME_EPS <= now < s.end - TIME_EPS]
        if covering:
            s = max(covering, key=lambda s: s.end)
            p.busy_until, p.activity = s.end, s.activity
        else:
            p.busy_until, p.activity = 0.0, None


def _record_spans(
    world: World,
    spans: Spans,
    now: float,
    before: dict[str, tuple[float, str | None]],
    first: str | None,
) -> tuple[str, _Span, _Span] | None:
    """Add a span (now, busy_until, activity) for every puppet the action occupied (``first``,
    the acting puppet, before the others in cast order). Returns the first new span that runs
    into a span its puppet starts later: (who, new span, later span)."""
    clash: tuple[str, _Span, _Span] | None = None
    names = ([first] if first in world.people else []) + [w for w in world.people if w != first]
    for who in names:
        p = world.people[who]
        if (p.busy_until, p.activity) == before[who] or p.busy_until <= now + TIME_EPS:
            continue
        new = _Span(now, round(p.busy_until, 6), p.activity or "busy")
        later = [s for s in spans.get(who, ()) if now + TIME_EPS < s.start < new.end - TIME_EPS]
        if later and clash is None:
            clash = (who, new, min(later, key=lambda s: (s.start, s.end)))
        spans.setdefault(who, []).append(new)
    return clash


# ------------------------------------------------------------------------ start keys

def _hold_then(tracks: TrackSet, target: str, channel: str, t: float, value: object) -> list[Key]:
    """A key setting ``value`` at ``t``. On a track that already has keys, the old value is held
    up to ``t`` first, so it steps at ``t`` instead of drifting there through the previous shot.
    A step channel (parent, state, facing) holds on its own, so it gets only the new key. An
    unchanged value gets no key."""
    if not tracks.has_track(target, channel):
        return [(target, channel, t, value)]
    old = tracks.value_at(target, channel, t)
    if round_value(old) == round_value(value):
        return []
    if is_step(channel, value):
        return [(target, channel, t, value)]
    return [(target, channel, t, old), (target, channel, t, value)]


def _seated_root(p: PuppetState, seat: Vec3) -> Vec3:
    """A seated puppet's root for a seat socket at world position ``seat``: the hip sits on it."""
    return (seat[0], seat[1] - ik.RIG["hipHeight"] * ik.scale(p.build), seat[2])


def _start_keys(world: World, tracks: TrackSet, shot: Shot, t: float, same_set: bool) -> tuple[list[Key], list[str]]:
    """Keys for where everyone starts, at the shot's start. Returns (keys, problems)."""
    keys: list[Key] = []
    problems: list[str] = []

    def puppet(who: str, root: Vec3, bones: dict[str, float]) -> None:
        p = world.people[who]
        target = f"puppet:{who}"
        keys.extend(_hold_then(tracks, target, "root.position", t, [float(c) for c in root]))
        keys.append((target, "root.facing", t, p.facing))
        for bone in BONES:
            value = bones.get(bone, 0.0)
            if value != 0.0 or tracks.has_track(target, f"{bone}.rot"):
                keys.extend(_hold_then(tracks, target, f"{bone}.rot", t, float(value)))

    def held_bones(who: str) -> dict[str, float]:
        out: dict[str, float] = {}
        for hand in HANDS:
            if hand in world.people[who].holding:
                arm = arm_of_hand(hand)
                upper, lower = ik.arm_bones(arm)
                out[upper], out[lower] = body.hold_pose(arm)
        return out

    for who, start in shot.start.items():
        p = world.people[who]
        scale = ik.scale(p.build)
        bones: dict[str, float] = {}
        root: Vec3 = p.pos
        if p.seat is not None:
            prop, sock = p.seat
            seat = world.socket_world(prop, sock)
            if seat is None:
                problems.append(f"{who} starts in {prop}.{sock}, but {prop} is not on stage")
            else:
                root = _seated_root(p, seat)
            bones.update({b: float(v) for b, v in _POSES["sit"].items() if b != "rootOffset"})
        if p.pose not in _POSES:
            problems.append(f"{p.pose} is not a pose; poses are: {', '.join(sorted(_POSES))}")
        elif not (p.seat is not None and p.pose == "sit"):
            values = _POSES[p.pose]
            bones.update({b: float(v) for b, v in values.items() if b != "rootOffset"})
            if p.seat is None and "rootOffset" in values:
                off = values["rootOffset"]
                root = (root[0] + p.facing * off[0] * scale, root[1] + off[1] * scale, root[2])
        bones.update(held_bones(who))
        puppet(who, root, bones)
        if start.holding is not None:
            hand = world.holder(start.holding)
            assert hand is not None  # begin_shot put it in a hand
            keys.append((f"prop:{start.holding}", "parent", t, f"puppet:{who}.{hand[1]}"))
            keys.append((f"prop:{start.holding}", "position", t, [0.0, 0.0, 0.0]))

    if not same_set:  # everyone not listed waits offstage
        for who in world.people:
            if who not in shot.start:
                puppet(who, OFFSTAGE_LEFT, held_bones(who))
        # Props the new shot does not bring along (state.py took them off the stage) leave
        # with the old set: on the ground at offstage_left, stepping there at the shot's start.
        for what in world.props:
            target = f"prop:{what}"
            if not world.is_unplaced(what) or not tracks.has_track(target, "parent"):
                continue
            if tracks.value_at(target, "parent", t) != GROUND:
                keys.append((target, "parent", t, GROUND))
            keys.extend(_hold_then(tracks, target, "position", t, [float(c) for c in OFFSTAGE_LEFT]))

    # propStart: on a prop that already has keys (an earlier shot of the same set, or a vehicle
    # driven earlier), each channel holds its old value up to the shot's start and steps there,
    # instead of gliding to the new place through the previous shot. A prop seen for the first
    # time gets one key per channel, as before.
    for what, ps in shot.prop_start.items():
        st = world.props[what]
        target = f"prop:{what}"
        if ps.at is not None or ps.on is not None:
            pos = [float(c) for c in st.pos]
            parent_keys = _hold_then(tracks, target, "parent", t, st.parent)
            pos_keys = _hold_then(tracks, target, "position", t, pos)
            if parent_keys and not pos_keys:  # a position key at every parent key (CONTRACTS.md)
                pos_keys = [(target, "position", t, pos)]
            keys.extend(parent_keys)
            keys.extend(pos_keys)
        if ps.state is not None:
            keys.extend(_hold_then(tracks, target, "state", t, ps.state))
    return keys, problems


def _reseat_keys(world: World, tracks: TrackSet, shot: Shot, t: float) -> tuple[list[Key], list[str]]:
    """Puppets still seated in a prop that the shot's propStart re-placed (or in a prop resting
    on one), and not listed in its start, ride along: the World moves each to the seated root
    at its seat's new place, and its root.position holds until the shot's start and steps
    there. Props they hold, or that rest on the moved prop, are parented and follow on their
    own. Returns (keys, problems)."""
    moved = [what for what, ps in shot.prop_start.items() if ps.at is not None or ps.on is not None]
    if not moved:
        return [], []
    keys: list[Key] = []
    problems: list[str] = []
    placed = world.reseat_riders(moved, lambda who, seat: _seated_root(world.people[who], seat), skip=shot.start)
    for who, root in placed.items():
        if root is None:
            prop, sock = world.people[who].seat  # type: ignore[misc]
            problems.append(
                f"{who} is still seated in {prop}.{sock}, but {prop} now rests on something a puppet is "
                f"holding; list {who} in this shot's start"
            )
            continue
        keys.extend(_hold_then(tracks, f"puppet:{who}", "root.position", t, [float(c) for c in root]))
    return keys, problems


# --------------------------------------------------------------------------- compile

def compile(
    screenplay: Screenplay,
    *,
    sets: Mapping[str, schemas.SetDef],
    props: Mapping[str, schemas.Prop],
    people: Mapping[str, schemas.Person],
) -> CompileResult:
    """Compile a screenplay into a timeline. Never raises for a bad screenplay."""
    sp = screenplay
    static = validate(sp, sets, props, people)
    if static:  # fix these first: simulating an invalid screenplay only adds knock-on errors
        return CompileResult(ok=False, errors=static)
    if not sp.shots:
        return CompileResult(ok=False, errors=[E.error("no_shots", "there are no shots; a screenplay needs at least one")])

    errors = _Errors()
    try:
        default = N.default_author(sp)
    except N.NarrationError as e:
        return CompileResult(ok=False, errors=[E.from_narration_error(e)])

    world = World([people[c] for c in dict.fromkeys(sp.cast)], [props[p] for p in dict.fromkeys(sp.props)])
    tracks = TrackSet()
    timeline_shots: list[TimelineShot] = []
    per_shot_lines: list[dict[str, list[NarrationLine]]] = []
    action_start_by_shot: dict[str, float] = {}
    retimed_by_shot: dict[str, tuple[list[float], list[float]]] = {}
    all_effects: list[tuple[float, int, int, TimelineEffect]] = []  # (t, shot index, action index, entry)
    cam_pose: camera_mod.CameraPose | None = None
    shot_start, action_start = 0.0, layout.RISE

    for si, shot in enumerate(sp.shots):
        set_def = sets[shot.set]
        n_layers = len(set_def.layers)
        same_set = si > 0 and sp.shots[si - 1].set == shot.set
        try:
            marks = motion.resolve_marks(set_def.marks, shot.marks)
        except ValueError as e:
            errors.add(E.error("bad_mark", str(e), shot.id), si)
            break
        try:
            world.begin_shot(shot, marks, same_set)
        except StateError as e:
            errors.add(E.error(e.code, e.reason, shot.id), si)
            break
        action_start_by_shot[shot.id] = action_start

        if not same_set:
            tracks.extend(layout.rise_keys(shot.set, n_layers, shot_start, shot.transition))
        start_keys, problems = _start_keys(world, tracks, shot, shot_start, same_set)
        for reason in problems:
            errors.add(E.error("bad_start", reason, shot.id), si)
        tracks.extend(start_keys)
        reseat_keys, problems = _reseat_keys(world, tracks, shot, shot_start)
        for reason in problems:
            errors.add(E.error("bad_start", reason, shot.id), si)
        tracks.extend(reseat_keys)
        if si == 0:
            # Signature items (state.py puts them on the head when the World is built): parent
            # each to its wearer's head, so it goes wherever the puppet goes. One already keyed
            # (the start block put it in a hand or set it down) is left alone.
            for who, p in world.people.items():
                item = p.holding.get(HEAD)
                if item is not None and not tracks.has_track(f"prop:{item}", "parent"):
                    tracks.extend(character.worn_item_keys(world, who, shot_start))

        try:
            retimed = N.retime_actions(shot, action_start, author=default)
        except N.NarrationError as e:
            errors.add(E.from_narration_error(e), si)
            retimed = [a.t for a in shot.actions]
        retimed_by_shot[shot.id] = ([a.t for a in shot.actions], list(retimed))

        locate = make_locate(world, tracks, marks, shot.id)
        rests_on = {what: tuple(world.supports(what)) for what in world.props}
        seated_in = {who: (p.seat[0], *rests_on[p.seat[0]]) for who, p in world.people.items() if p.seat is not None}
        vehicles = frozenset(what for what in world.props if world.is_vehicle(what))
        # Every follow waits for every move of its leader and reads its whole path, unless that
        # leaves something out (a loop): then follows with dur use their windows (module docstring).
        sched = (shot.actions, retimed, world.people, world.props, seated_in, rests_on, vehicles)
        order, left_out = _schedule(*sched, windows=False)
        windows: dict[int, _FollowWindow] = {}
        if left_out:
            order, left_out = _schedule(*sched, windows=True)
            windows = _follow_windows(shot.actions, retimed, world.people, seated_in, rests_on, vehicles)
        for ci, code, reason in left_out:
            errors.add(E.error(code, reason, shot.id, shot.actions[ci]), si, ci)
        spans: Spans = {}
        shot_effects: list[tuple[int, TimelineEffect]] = []
        for ai in order:
            action = shot.actions[ai]
            err, effect = _run_action(world, tracks, action, retimed[ai], action_start, locate, shot.id, spans,
                                      windows.get(ai))
            if err is not None:
                errors.add(err, si, ai)
            elif effect is not None:
                shot_effects.append((ai, effect))

        cam_errors, cam_pose = _camera(shot, shot_start, action_start, cam_pose, locate, tracks)
        for err in cam_errors:
            errors.add(err, si, len(shot.actions))

        other = {a: by_shot[shot.id] for a, by_shot in (sp.narrations or {}).items() if shot.id in by_shot}
        try:
            lines = N.build_shot_narration(shot, action_start, retimed, other, default)
        except N.NarrationError as e:
            errors.add(E.from_narration_error(e), si)
            lines = {default: []}
        per_shot_lines.append(lines)

        content_end = layout.content_end(_last_moment(tracks, shot_effects), N.last_word_time(lines))
        if si + 1 < len(sp.shots):
            nxt = sp.shots[si + 1]
            end, next_start, next_action_start, fold = layout.next_shot_timing(
                content_end, shot.set, n_layers, nxt.set, shot.transition
            )
            tracks.extend(fold)
        else:
            end, next_start, next_action_start = content_end, content_end, content_end
        timeline_shots.append(TimelineShot(
            id=shot.id, set=shot.set, start=shot_start, action_start=action_start, end=end,
            transition=shot.transition, page_text=shot.page_text,
        ))
        for ai, effect in shot_effects:
            if effect.kind == "rain" and shot.actions[ai].dur is None:  # rain with no dur lasts the shot
                t_rain = round(action_start + retimed[ai], 6)
                try:
                    effect = effects.effect_for(shot.actions[ai], t_rain, locate, end)
                except StateError as e:  # cannot happen: the shot's end covers the rain's start
                    errors.add(E.from_state_error(e, shot.id, shot.actions[ai]), si, ai)
                    continue
            all_effects.append((effect.t, si, ai, effect))
        shot_start, action_start = next_start, next_action_start

    narrations, captions = N.assemble(per_shot_lines, default)
    interactions = None
    if sp.interactions is not None and len(action_start_by_shot) == len(sp.shots):
        try:
            interactions = N.convert_interactions(
                sp.interactions, action_start_by_shot, sp.shots[0].id, retimed_by_shot=retimed_by_shot,
                end_by_shot={s.id: s.end for s in timeline_shots},
            )
        except N.NarrationError as e:
            errors.add(E.from_narration_error(e), len(sp.shots))
    explanations = [ex.model_copy(deep=True) for ex in sp.explanations] if sp.explanations is not None else None

    if errors:
        return CompileResult(ok=False, errors=errors.sorted())
    timeline = Timeline(
        duration=timeline_shots[-1].end,
        audio=None,
        tracks=tracks.tracks(),
        captions=captions,
        shots=timeline_shots,
        effects=[e for *_, e in sorted(all_effects, key=lambda it: it[:3])],
        narrations=narrations,
        default_narrator=default,
        interactions=interactions,
        explanations=explanations,
    )
    return CompileResult(ok=True, timeline=timeline)


def _last_moment(tracks: TrackSet, shot_effects: list[tuple[int, TimelineEffect]]) -> float:
    """The shot's last key time, where an effect counts as a key at its end (``t + dur``), so
    the next shot's fold never cuts a burst short. Rain with no ``dur`` lasts until the shot's
    end, so it counts at its start: the shot still reaches every effect it has."""
    last = tracks.last_key_time()
    for _, e in shot_effects:
        last = max(last, round(e.t + (e.dur if e.dur is not None else 0.0), 3))
    return last


def _run_action(
    world: World,
    tracks: TrackSet,
    action: Action,
    shot_t: float,
    action_start: float,
    locate: Locate,
    shot_id: str,
    spans: Spans,
    window: _FollowWindow | None = None,
) -> tuple[CompileError | None, TimelineEffect | None]:
    """Run one action through its handler, with the busy checks around it.
    Returns (its error, its Timeline.effects entry for an effect verb).

    A follow with ``dur`` (``window``) reads its leader's path only up to ``t + dur``
    (``_LeaderWindow``), whatever ran before it. A leader that stands still all that time but
    moves later in the shot is followed by holding still, as when the follow waited for that
    move; only a leader that never moves after ``t`` leaves ``nothing_to_follow``.

    Busy state comes from ``spans`` (this shot's busy spans so far), and the spans of every
    puppet the action occupies are added to it. Every time a busy reason shows is in the
    blocked action's written frame: a span time T (cue-retimed shot time) shows as
    ``action.t + (T - shot_t)``, so moving the action to the stated time clears the error
    (when that does not change its cue shift). With no cue shift that is T itself.

    An effect verb (effects.EFFECT_VERBS) with no handler of its own runs no puppet handler and
    occupies nobody: effects.effect_for builds its entry at timeline time, with ``at`` placed
    by the shot's locate. A rain with no ``dur`` gets a placeholder ``dur`` here; compile()
    rebuilds it once the shot's end is known."""
    handler = HANDLERS.get(action.do)
    t = round(action_start + shot_t, 6)
    if handler is None:
        if action.do in effects.EFFECT_VERBS:
            return _run_effect(action, t, locate, shot_id)
        return E.error("unsupported_verb", f"the compiler does not support {action.do} yet", shot_id, action), None
    now = round(shot_t, 6)
    world.now = now
    _set_busy_from_spans(world, spans, now)
    before = {w: (p.busy_until, p.activity) for w, p in world.people.items()}
    shift = action.t - shot_t  # written frame - retimed frame; 0.0 when no cue moved the action
    world.busy_time_offset = shift
    reads = tracks
    if window is not None and action.dur is not None:
        reads = _LeaderWindow(tracks, window.leader, t + action.dur, window.later)
    try:
        if action.who is not None:
            world.check_free(action.who)
        end = handler(world, reads, action, t, action_start, locate)
        if action.who is not None:
            world.occupy(action.who, round(end - action_start, 6), ACTIVITY[action.do], at=now)
    except StateError as e:
        return E.from_state_error(e, shot_id, action), None
    except ik.OutOfReach as e:
        return E.from_out_of_reach(e, shot_id, action), None
    except N.NarrationError as e:
        return E.from_narration_error(e), None
    except ValueError as e:
        return E.error("invalid_action", str(e), shot_id, action), None
    finally:
        world.busy_time_offset = 0.0
    clash = _record_spans(world, spans, now, before, action.who)
    if clash is not None:
        who, new, later = clash

        def shown(x: float) -> str:
            return fmt_t(x + shift if shift else x)

        reason = (
            f"{who} would still be {new.activity} until t={shown(new.end)}, "
            f"but starts {later.activity} at t={shown(later.start)}"
        )
        return E.error("busy", reason, shot_id, action), None
    return None, None


def _run_effect(action: Action, t: float, locate: Locate, shot_id: str) -> tuple[CompileError | None, TimelineEffect | None]:
    """An effect action's Timeline.effects entry at timeline time ``t``, or its error (an ``at``
    that cannot be placed, say a prop that is not on stage). A rain with no ``dur`` is checked
    with a zero-length placeholder; compile() rebuilds it with the shot's end."""
    try:
        return None, effects.effect_for(action, t, locate, t)
    except StateError as e:
        return E.from_state_error(e, shot_id, action), None
    except ValueError as e:
        return E.error("invalid_action", str(e), shot_id, action), None


def _camera(
    shot: Shot,
    shot_start: float,
    action_start: float,
    prev: camera_mod.CameraPose | None,
    locate: Locate,
    tracks: TrackSet,
) -> tuple[list[CompileError], camera_mod.CameraPose | None]:
    """The shot's camera keys (added to ``tracks``) and the pose it ends on.

    validate.py has checked the cues statically; what is left is a target that cannot be
    placed when the move happens (a prop that is not on stage)."""
    errs: list[CompileError] = []
    for cue in shot.camera:
        if cue.move in camera_mod.TARGET_MOVES:
            reason = None
            if cue.target is None:
                reason = "it needs a target"
            else:
                try:
                    locate(cue.target, action_start + cue.t + (cue.dur or 0.0))
                except StateError as e:
                    reason = e.reason
            if reason is not None:
                errs.append(CompileError(
                    code="bad_camera",
                    message=E.error_message(shot.id, cue.t, None, cue.move, cue.target, reason),
                    shot=shot.id, t=float(cue.t), do=cue.move,
                ))
    if errs:
        return errs, prev
    try:
        keys, pose = camera_mod.shot_camera_keys(shot.camera, shot_start, action_start, prev, locate)
    except (ValueError, StateError) as e:
        reason = e.reason if isinstance(e, StateError) else str(e)
        return [E.error("bad_camera", f"camera: {reason}", shot.id)], prev
    tracks.extend(keys)
    return errs, pose
