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

Schedule order within a shot: each puppet's actions form a queue in (retimed t, list index)
order (an action without ``who`` is a queue of its own), and the earliest runnable queue head
runs next. A ``follow`` of a cast member is runnable only once every action of its leader in
the shot has run, so the follower can see where the leader goes; the follower's later actions
wait behind the follow. When no head can run, each follow cycle (puppets that follow one
another round a loop) is reported as ``follow_cycle`` on its earliest follow, which is skipped,
and the rest of the shot then runs in time order. A puppet that only follows a member of a
cycle is not part of it and is not reported.

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
from collections.abc import Callable, Mapping
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


def _follows_a_leader(action: Action, cast: Mapping[str, object]) -> str | None:
    """The leader a ``follow`` waits for: its target, when that is another cast member."""
    if action.do == "follow" and action.target is not None and action.target in cast and action.target != action.who:
        return action.target
    return None


def _follow_cycles(
    actions: list[Action], heads: list[int], cast: Mapping[str, object],
    when: Callable[[int], tuple[float, int]],
) -> list[list[int]]:
    """The follow cycles among ``heads`` (the queue heads when none can run, all blocked follows).

    Each puppet whose head is a blocked follow waits for that follow's leader, so the waits-for
    graph has one edge per puppet. Every walk along it ends in a loop; a puppet that only waits
    on a loop (a follower of one of its members) is not part of it. Returns each loop as the
    indexes of its follows, starting from its earliest follow and going leader by leader, the
    loops ordered by that earliest follow.
    """
    head_of: dict[str, int] = {}
    for i in heads:
        who = actions[i].who
        if who is not None and _follows_a_leader(actions[i], cast) is not None:
            head_of[who] = i
    on_a_cycle: set[str] = set()
    for start in sorted(head_of):
        path: list[str] = []
        who: str | None = start
        while who in head_of and who not in path and who not in on_a_cycle:
            path.append(who)
            who = actions[head_of[who]].target
        if who in path:
            on_a_cycle.update(path[path.index(who):])
    cycles: list[list[int]] = []
    seen: set[str] = set()
    for i in sorted((head_of[w] for w in on_a_cycle), key=when):
        who = actions[i].who
        assert who is not None
        if who in seen:
            continue
        loop: list[int] = []
        while who not in seen:
            seen.add(who)
            loop.append(head_of[who])
            who = actions[head_of[who]].target
        cycles.append(loop)
    return cycles


def _cycle_reason(actions: list[Action], loop: list[int]) -> str:
    """Why a follow cycle cannot run, naming every puppet on it."""
    if len(loop) == 2:
        a = actions[loop[0]]
        return f"{a.who} and {a.target} follow each other; only one can lead"
    links = [f"{actions[i].who} follows {actions[i].target}" for i in loop]
    return f"{', '.join(links[:-1])} and {links[-1]}; only one can lead"


def _schedule(
    actions: list[Action], retimed: list[float], cast: Mapping[str, object]
) -> tuple[list[int], list[tuple[int, str]]]:
    """The order to run a shot's actions in, and the follows reported as cycles (index, reason).

    Per-puppet queues in (retimed t, index) order; the earliest runnable head runs next (ties
    by retimed t, then index). A follow of a cast member waits until every action of its
    leader has run. When no head can run, each follow cycle among the blocked heads is
    reported on its earliest follow (in (retimed t, index) order), which is left out of the
    order; everything that is left runs in time order. A follow that only waits on a cycle is
    not reported and still runs.
    """
    def when(i: int) -> tuple[float, int]:
        return (retimed[i], i)

    queues: dict[tuple[str, object], list[int]] = {}
    queue_of: dict[int, tuple[str, object]] = {}
    for i in sorted(range(len(actions)), key=when):
        who = actions[i].who
        queue_of[i] = ("who", who) if who is not None else ("action", i)
        queues.setdefault(queue_of[i], []).append(i)
    remaining: dict[str, int] = {}
    for a in actions:
        if a.who is not None:
            remaining[a.who] = remaining.get(a.who, 0) + 1
    heads = {key: 0 for key in queues}
    order: list[int] = []

    def blocked(i: int) -> bool:
        leader = _follows_a_leader(actions[i], cast)
        return leader is not None and remaining.get(leader, 0) > 0

    while len(order) < len(actions):
        waiting = [queues[k][heads[k]] for k in queues if heads[k] < len(queues[k])]
        runnable = [i for i in waiting if not blocked(i)]
        if not runnable:
            loops = _follow_cycles(actions, waiting, cast, when)
            dropped = {loop[0] for loop in loops}
            rest = sorted((i for k in queues for i in queues[k][heads[k]:] if i not in dropped), key=when)
            return order + rest, [(loop[0], _cycle_reason(actions, loop)) for loop in loops]
        i = min(runnable, key=when)
        heads[queue_of[i]] += 1
        who = actions[i].who
        if who is not None:
            remaining[who] -= 1
        order.append(i)
    return order, []


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
        order, cycles = _schedule(shot.actions, retimed, world.people)
        for ci, reason in cycles:
            errors.add(E.error("follow_cycle", reason, shot.id, shot.actions[ci]), si, ci)
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
