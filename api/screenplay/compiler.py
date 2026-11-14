"""Screenplay -> timeline, plus machine-readable errors for the agent (ARCHITECTURE.md section 3).

``compile(screenplay, sets=..., props=..., people=...)`` runs the whole pipeline:

1. Static validation (validate.py): every verb, parameter, mark, prop, person, pose, cue and
   camera move against shared/vocabulary.json and the scene. Any error stops here.
2. For each shot, in order: apply its start block to the world state, raise the set (unless
   the previous shot used the same set), key where everyone starts, retime cued actions onto
   their narration words, run each action through its verb handler (state + keys), move the
   camera, time the narration, and work out when the shot ends and the next one starts (the
   outgoing shot's transition folds its set).
3. Join the tellings, carry interactions and explanations, and return the Timeline, or every
   error found as exact CompileError messages such as
   ``shot s1, t=5: dad cannot hand_to "hymn_books"; mom is holding it``.

It never raises for a bad screenplay, and the same input gives byte-identical JSON.

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
from collections.abc import Mapping
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
from screenplay.state import HANDS, OFFSTAGE_LEFT, StateError, World
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
        order = sorted(range(len(shot.actions)), key=lambda i: (retimed[i], i))
        for ai in order:
            action = shot.actions[ai]
            err = _run_action(world, tracks, action, retimed[ai], action_start, locate, shot.id)
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
) -> CompileError | None:
    """Run one action through its handler, with the busy check around it. Returns its error."""
    handler = HANDLERS.get(action.do)
    if handler is None:
        return E.error("unsupported_verb", f"the compiler does not support {action.do} yet", shot_id, action)
    world.now = round(shot_t, 6)
    t = round(action_start + shot_t, 6)
    try:
        if action.who is not None:
            world.check_free(action.who)
        end = handler(world, tracks, action, t, action_start, locate)
        if action.who is not None:
            world.occupy(action.who, round(end - action_start, 6), ACTIVITY[action.do], at=world.now)
    except StateError as e:
        return E.from_state_error(e, shot_id, action)
    except ik.OutOfReach as e:
        return E.from_out_of_reach(e, shot_id, action)
    except N.NarrationError as e:
        return E.from_narration_error(e)
    except ValueError as e:
        return E.error("invalid_action", str(e), shot_id, action)
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
