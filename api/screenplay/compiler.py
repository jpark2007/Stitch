"""Screenplay -> timeline, plus machine-readable errors for the agent (ARCHITECTURE.md section 3).

``compile(screenplay, sets=..., props=..., people=...)`` runs the whole pipeline:

1. Static validation (validate.py): every verb, parameter, mark, prop, person, pose, cue and
   camera move against shared/vocabulary.json and the scene. Any error stops here.
2. For each shot, in order: apply its start block to the world state, raise the set (unless
   the previous shot used the same set), key where everyone starts, retime cued actions onto
   their narration words, run each action through its verb handler (state + keys) in schedule
   order (below), move the camera, time the narration, and work out when the shot ends and the
   next one starts (the outgoing shot's transition folds its set).
3. Join the tellings, carry interactions and explanations, and return the Timeline, or every
   error found as exact CompileError messages such as
   ``shot s1, t=5: dad cannot hand_to "hymn_books"; mom is holding it``.

It never raises for a bad screenplay, and the same input gives byte-identical JSON.

Schedule order within a shot (``_schedule``): the earliest runnable action, by (retimed t, list
index), runs next. An action is runnable only once every earlier action that touches the same
puppet or prop (its ``who`` or a cast member or prop it names) has run, so it reads everything
as of its own time: a puppet's own actions keep their order, and an action on a follower (mom
hands dad the books while dad follows kid) sees the follower where the follow put him, never a
stale position. A ``follow`` of a cast member is runnable only once every action that can move
its leader has run (the leader's own actions except STILL_VERBS such as wave or point_at, and
any drive_to / ride_to of a vehicle the leader rides in), so the follower sees the leader's
whole path; the follower's later actions wait behind the follow. Without follows this is plain
time order. When nothing can run, the waits form a loop through a follow: a loop of follows
only (puppets that follow one another round a loop) is reported as ``follow_cycle`` on its
earliest follow, and any other loop as ``follow_order`` on the move of the leader that needs
its follower first (kid hugs dad while dad follows kid). The reported action is skipped and
the rest of the shot is scheduled as before. An action that only waits on a loop is not part
of it and is not reported.

Because of that reordering, ``World.busy_until`` only describes the action running now. The
compiler keeps every puppet's busy spans in shot time and, before each handler, sets each
puppet's ``busy_until`` / ``activity`` to the span covering ``world.now`` (if any). A new span
that runs into a span the puppet starts later is reported as ``busy`` too.

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
from collections.abc import Collection, Mapping
from dataclasses import dataclass, field

import schemas
import screenplay as _pkg
from screenplay import body, ik, layout, motion, verbs_core
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
    TimelineShot,
)
from screenplay.state import GROUND, HANDS, OFFSTAGE_LEFT, TIME_EPS, StateError, World, fmt_t
from screenplay.tracks import Key, TrackSet, round_value
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


def _moves(actions: list[Action], who: str, seated_in: Mapping[str, str]) -> list[int]:
    """Every action in the shot that can move ``who``'s root: its own actions except the
    STILL_VERBS, and any drive_to / ride_to of a vehicle it starts the shot in or gets into."""
    rides = {a.what for a in actions if a.who == who and a.do == "get_in"}
    if who in seated_in:
        rides.add(seated_in[who])
    return [
        i for i, a in enumerate(actions)
        if (a.who == who and a.do not in STILL_VERBS) or (a.do in _VEHICLE_MOVES and a.what in rides)
    ]


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
    seated_in: Mapping[str, str] | None = None,
) -> tuple[list[int], list[tuple[int, str, str]]]:
    """The order to run a shot's actions in, and the actions left out: (index, code, reason).

    ``seated_in`` maps a puppet to the vehicle (or seat prop) it starts the shot in. The
    earliest runnable action, by (retimed t, index), runs next. An action is runnable when

    - every earlier action (by retimed t, index) that touches one of its puppets or props has
      run, so whatever it reads (where a puppet stands, who holds a prop) is as of its own
      time; that includes every earlier action of its own puppet; and
    - for a follow of a cast member, every action that can move the leader has run
      (``_moves``), so the follower sees the leader's whole path. The leader's still actions
      (a wave, a point_at, a hand_to) do not hold the follow back.

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
        movers.append(sorted(_moves(actions, leader, seated_in), key=rank.__getitem__) if leader else [])
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
    up to ``t`` first, so it steps at ``t`` instead of drifting there through the previous shot."""
    if not tracks.has_track(target, channel):
        return [(target, channel, t, value)]
    old = tracks.value_at(target, channel, t)
    if round_value(old) == round_value(value):
        return []
    return [(target, channel, t, old), (target, channel, t, value)]


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
                root = (seat[0], seat[1] - ik.RIG["hipHeight"] * scale, seat[2])
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

    for what, ps in shot.prop_start.items():
        st = world.props[what]
        if ps.at is not None or ps.on is not None:
            keys.append((f"prop:{what}", "parent", t, st.parent))
            keys.append((f"prop:{what}", "position", t, [float(c) for c in st.pos]))
        if ps.state is not None:
            keys.append((f"prop:{what}", "state", t, ps.state))
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

        try:
            retimed = N.retime_actions(shot, action_start, author=default)
        except N.NarrationError as e:
            errors.add(E.from_narration_error(e), si)
            retimed = [a.t for a in shot.actions]

        locate = make_locate(world, tracks, marks, shot.id)
        seated_in = {who: p.seat[0] for who, p in world.people.items() if p.seat is not None}
        order, left_out = _schedule(shot.actions, retimed, world.people, world.props, seated_in)
        for ci, code, reason in left_out:
            errors.add(E.error(code, reason, shot.id, shot.actions[ci]), si, ci)
        spans: Spans = {}
        for ai in order:
            action = shot.actions[ai]
            err = _run_action(world, tracks, action, retimed[ai], action_start, locate, shot.id, spans)
            if err is not None:
                errors.add(err, si, ai)

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

        content_end = layout.content_end(tracks.last_key_time(), N.last_word_time(lines))
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
        shot_start, action_start = next_start, next_action_start

    narrations, captions = N.assemble(per_shot_lines, default)
    interactions = None
    if sp.interactions is not None and len(action_start_by_shot) == len(sp.shots):
        try:
            interactions = N.convert_interactions(sp.interactions, action_start_by_shot, sp.shots[0].id)
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
        effects=[],
        narrations=narrations,
        default_narrator=default,
        interactions=interactions,
        explanations=explanations,
    )
    return CompileResult(ok=True, timeline=timeline)


def _run_action(
    world: World,
    tracks: TrackSet,
    action: Action,
    shot_t: float,
    action_start: float,
    locate: Locate,
    shot_id: str,
    spans: Spans,
) -> CompileError | None:
    """Run one action through its handler, with the busy checks around it. Returns its error.

    Busy state comes from ``spans`` (this shot's busy spans so far), and the spans of every
    puppet the action occupies are added to it."""
    handler = HANDLERS.get(action.do)
    if handler is None:
        return E.error("unsupported_verb", f"the compiler does not support {action.do} yet", shot_id, action)
    now = round(shot_t, 6)
    world.now = now
    _set_busy_from_spans(world, spans, now)
    before = {w: (p.busy_until, p.activity) for w, p in world.people.items()}
    t = round(action_start + shot_t, 6)
    try:
        if action.who is not None:
            world.check_free(action.who)
        end = handler(world, tracks, action, t, action_start, locate)
        if action.who is not None:
            world.occupy(action.who, round(end - action_start, 6), ACTIVITY[action.do], at=now)
    except StateError as e:
        return E.from_state_error(e, shot_id, action)
    except ik.OutOfReach as e:
        return E.from_out_of_reach(e, shot_id, action)
    except N.NarrationError as e:
        return E.from_narration_error(e)
    except ValueError as e:
        return E.error("invalid_action", str(e), shot_id, action)
    clash = _record_spans(world, spans, now, before, action.who)
    if clash is not None:
        who, new, later = clash
        reason = (
            f"{who} would still be {new.activity} until t={fmt_t(new.end)}, "
            f"but starts {later.activity} at t={fmt_t(later.start)}"
        )
        return E.error("busy", reason, shot_id, action)
    return None


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
