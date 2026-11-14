"""FakeCompiler: a deterministic stand-in for screenplay.compiler, for agent tests.

`agent.tools.compile.load_compiler()` only uses it while `screenplay.compiler.compile` does not
exist. It has two halves:

1. **Checking** (`check`) decides ok versus not-ok the way the real compiler does, with the real
   compiler's error codes and message grammar (``shot s1, t=5: dad cannot hand_to "hymn_books";
   mom is holding it``, ``shot s1: ...``, ``screenplay: ...``, and narration.py's
   ``shot s1, dad's narration line 1: ...``):

   - static validation is `screenplay.validate.validate`, the exact pass the real compiler runs
     first (verbs, parameters, marks, props, people, poses, sockets, states, times, camera cues,
     narration cues and cue words, tellings, explanations, interactions);
   - then a shot-by-shot simulation on `screenplay.state.World`, the real compiler's world
     model: start blocks, cue retiming (`narration.retime_actions`), actions in retimed order,
     one action at a time per puppet (``busy``), holding and hands (``not_holding``,
     ``already_held``, ``hands_full``, ...), on/offstage (``offstage``, ``already_on_stage``),
     seats and vehicles, and arm reach (``out_of_reach``) through `screenplay.ik` exactly where
     the real verb handlers have no auto-walk: ``hand_to``, ``place_on``, ``open``/``close``
     and ``put_on``. ``pick_up``, ``hug`` and ``sit_on`` walk up first, as the real ones do.
     Durations come from the same constants (`screenplay.body`, `motion.locomotion_keys`).

2. **Output** (`_Run`): for a screenplay that passes, a simple Timeline that validates against
   `screenplay.schema.Timeline` and follows the channel grammar in shared/CONTRACTS.md
   (straight-line walks, fixed camera numbers, no IK keys). Actions are placed at their
   cue-retimed times. Validity and determinism matter here, not exact motion.

Known gaps (the parity corpus in tests/agent/test_agent_real_compiler.py excludes these
explicitly):

- **Effects** (``confetti``, ``steam``, ``rain``, ``smoke``, ``time_of_day``): the fake
  accepts them and emits `Timeline.effects`, as shared/CONTRACTS.md specifies. The merged real
  compiler (#60 + #66 + #70) has `screenplay/effects.py` but no ``HANDLERS`` for it, so it
  rejects every effect with ``unsupported_verb``. That is a compiler-side gap.
- **Positions are logical, not keyframed**: reach is measured from where a puppet's last move
  ended and a held prop is located at its holder's root, so an action that only works or fails
  because of where a limb is mid-gesture can differ. ``pick_up`` of a prop far below the
  shoulder always succeeds here (the real compiler may still find no crouch that reaches).
- **follow** uses `motion.follow_keys` over the leader's logical path (shot start plus each
  move), not its full keyframed track, so its end time can differ slightly.
- **Camera** targets are checked against the world at the end of the shot (``bad_camera`` for a
  prop that is not on stage), not at the cue's own time.
- Gesture facings (``wave``/``point_at``/``lean_toward`` turning toward a target) are
  approximated by the target's x.
- **Wording**: messages are the real ones (same codes, same ``shot s1, t=5: ...`` heads), except
  ``not_holding`` for a prop lying on the ground, which reads "nobody is holding it" where the
  real compiler says "it is on the ground" (`LEGACY_REASONS`).
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from screenplay import body as B
from screenplay import ik, motion
from screenplay import narration as N
from screenplay.layout import RISE as LAYOUT_RISE
from screenplay.schema import (
    SHARED_DIR,
    VOCABULARY,
    Action,
    Caption,
    CompileError,
    CompileResult,
    NarrationLine,
    NarrationTrack,
    Screenplay,
    Shot,
    Timeline,
    TimelineEffect,
    TimelineShot,
    Track,
)
from screenplay.state import HANDS, HEAD, SEATED_POSES, StateError, World, fmt_t, parse_parent
from screenplay.validate import validate

if TYPE_CHECKING:
    from agent.tools.compile import SceneInputs

PUPPET = json.loads((SHARED_DIR / "rig" / "puppet.json").read_text())
POSES: dict[str, dict[str, Any]] = json.loads((SHARED_DIR / "rig" / "poses.json").read_text())["poses"]

RISE = 1.2  # seconds from a shot's start to its action start (set rising)
ACTION_TAIL = 1.5  # seconds after the last action starts
NARRATION_TAIL = 2.5  # seconds after the last narration line starts
POSE_DUR = 0.4
GESTURE_DUR = 1.0
MIN_MOVE = 0.3
FOLD_DOWN = 0.6
SETTLE = 0.05
ACTING_Z = 0.5

ACTIONS: dict[str, dict[str, Any]] = VOCABULARY["actions"]
BUILTIN_MARKS: dict[str, list[float]] = VOCABULARY["builtinMarks"]
SPEEDS: dict[str, float] = VOCABULARY["speeds"]
BUILDS: dict[str, float] = VOCABULARY["builds"]
CAMERA_MOVES: dict[str, dict[str, Any]] = VOCABULARY["camera"]
EFFECTS: set[str] = set(VOCABULARY["effects"])

CAM_PULL_BACK = ([0.0, 1.1, 3.4], [0.0, 0.2, 0.5], 35.0)
CAM_WIDE = ([0.0, 0.6, 2.6], [0.0, 0.35, 0.5], 35.0)

# Real compiler constants that are not on main yet (screenplay/verbs_*.py in the compiler PRs).
AUTO_WALK_REACH = 0.6  # verbs_core: pick_up walks to this fraction of the arm's reach
WAVE_SECONDS, POINT_SECONDS, HUG_SECONDS, LEAN_SECONDS = 1.2, 1.0, 1.5, 1.0  # verbs_body
SEAT_WALK_EPS, HUG_GAP, HUG_SLACK = 0.05, 0.18, 0.02  # verbs_body
WALK_UP_THRESHOLD = 0.05  # verbs_vehicles: walk to the door first when further than this in x
DRIVER_SOCKET = "driver"
TARGET_MOVES = {move for move, spec in CAMERA_MOVES.items() if "target" in spec["required"]}

# The one wording the fake keeps from before it used the real world model: the real compiler
# says "it is on the ground" here, and tests/agent/test_agent_compile_tool.py pins this text.
LEGACY_REASONS: dict[tuple[str, str], str] = {("not_holding", "it is on the ground"): "nobody is holding it"}

# An -ing word for every verb, for "dad is still walking until t=3.2" (the real compiler's map).
ACTIVITY: dict[str, str] = {
    "walk_to": "walking", "run_to": "running", "enter": "entering", "exit": "exiting",
    "turn_to": "turning", "follow": "following", "pose": "posing", "wave": "waving",
    "point_at": "pointing", "sit_on": "sitting down", "stand_up": "standing up", "kneel": "kneeling",
    "hug": "hugging", "lean_toward": "leaning", "pick_up": "picking up", "hold": "holding up",
    "drop": "dropping", "hand_to": "handing over", "put_on": "putting on", "place_on": "placing",
    "open": "opening", "close": "closing", "set_state": "changing", "get_in": "getting in",
    "get_out": "getting out", "drive_to": "driving", "ride_to": "riding",
    "confetti": "throwing confetti", "steam": "steaming", "rain": "raining", "smoke": "smoking",
    "time_of_day": "changing the light",
}

Vec = list[float]
Vec3 = tuple[float, float, float]


def _r(v: Any) -> Any:
    if isinstance(v, float):
        out = round(v, 4)
        return 0.0 if out == 0 else out
    if isinstance(v, list):
        return [_r(x) for x in v]
    return v


def _bones(pose: str) -> dict[str, float]:
    return {k: float(v) for k, v in POSES.get(pose, {}).items() if k != "rootOffset"}


def _root_offset_y(pose: str) -> float:
    off = POSES.get(pose, {}).get("rootOffset")
    return float(off[1]) if off else 0.0


def _sign(v: float) -> int:
    return 1 if v > 1e-9 else -1 if v < -1e-9 else 0


# ======================================================================= checking


def error_message(shot: str | None, t: float | None, who: str | None, do: str | None, what: str | None,
                  reason: str) -> str:
    """The real compiler's grammar: ``<head>: <who> cannot <do> "<what>"; <reason>`` and its
    shorter forms. The head is ``shot <id>, t=<t>``, ``shot <id>`` or ``screenplay``."""
    if shot is None:
        head = "screenplay"
    elif t is None:
        head = f"shot {shot}"
    else:
        head = f"shot {shot}, t={fmt_t(t)}"
    obj = f' "{what}"' if what else ""
    if do and who:
        return f"{head}: {who} cannot {do}{obj}; {reason}"
    if do:
        return f"{head}: cannot {do}{obj}; {reason}"
    return f"{head}: {reason}"


def _error(code: str, reason: str, shot: str | None = None, action: Action | None = None) -> CompileError:
    if action is None:
        return CompileError(code=code, message=error_message(shot, None, None, None, None, reason), shot=shot)
    return CompileError(
        code=code,
        message=error_message(shot, action.t, action.who, action.do, action.what, reason),
        shot=shot, t=float(action.t), who=action.who, do=action.do,
    )


def _out_of_reach(e: ik.OutOfReach, shot: str, action: Action) -> CompileError:
    name = e.who or action.who
    whose = f"{name}'s {e.arm}" if name else e.arm
    reason = f"{whose} cannot reach: target is {e.distance:.2f} from the shoulder, reach is {e.reach:.2f}"
    return _error("out_of_reach", reason, shot, action)


def _from_narration(e: N.NarrationError) -> CompileError:
    return CompileError(code=e.code, message=e.message, shot=e.shot or None)


def _arm_of_hand(hand: str) -> str:
    return "arm_l" if hand == "hand_l" else "arm_r"


@dataclass
class CheckResult:
    """What `check` found: errors (empty when the screenplay compiles) and, per shot id, each
    action's cue-retimed shot time (in list order)."""

    errors: list[CompileError] = field(default_factory=list)
    retimed: dict[str, list[float]] = field(default_factory=dict)


def check(screenplay: Screenplay, scene: SceneInputs) -> CheckResult:
    """Every error the real compiler would report for this screenplay, as closely as the fake can.

    Static problems stop here (as in the real compiler); otherwise each shot is simulated on a
    `World` and every action that cannot happen is reported, sorted by (shot, action) index.
    """
    sp = screenplay
    static = validate(sp, scene.sets, scene.props, scene.people)
    if static:
        return CheckResult(errors=static)
    if not sp.shots:
        return CheckResult(errors=[_error("no_shots", "there are no shots; a screenplay needs at least one")])
    try:
        default = N.default_author(sp)
    except N.NarrationError as e:
        return CheckResult(errors=[_from_narration(e)])

    world = World([scene.people[c] for c in dict.fromkeys(sp.cast)],
                  [scene.props[p] for p in dict.fromkeys(sp.props)])
    found: list[tuple[tuple[int, int, int], CompileError]] = []

    def add(err: CompileError, si: int, ai: int = -1) -> None:
        found.append(((si, ai, len(found)), err))

    out = CheckResult()
    for si, shot in enumerate(sp.shots):
        set_def = scene.sets[shot.set]
        same_set = si > 0 and sp.shots[si - 1].set == shot.set
        try:
            marks = motion.resolve_marks(set_def.marks, shot.marks)
        except ValueError as e:
            add(_error("bad_mark", str(e), shot.id), si)
            break
        try:
            world.begin_shot(shot, marks, same_set)
        except StateError as e:
            add(_error(e.code, e.reason, shot.id), si)
            break
        for who in shot.start:
            p = world.people[who]
            if p.seat is not None and world.socket_world(*p.seat) is None:
                add(_error("bad_start", f"{who} starts in {p.seat[0]}.{p.seat[1]}, but {p.seat[0]} is not on stage",
                           shot.id), si)

        try:
            retimed = N.retime_actions(shot, 0.0, author=default)
        except N.NarrationError as e:
            add(_from_narration(e), si)
            retimed = [float(a.t) for a in shot.actions]
        out.retimed[shot.id] = retimed

        sim = _ShotSim(world, marks, shot)
        for ai in sorted(range(len(shot.actions)), key=lambda i: (retimed[i], i)):
            err = sim.run(shot.actions[ai], retimed[ai])
            if err is not None:
                add(err, si, ai)
        for err in sim.camera():
            add(err, si, len(shot.actions))

        other = {a: by_shot[shot.id] for a, by_shot in (sp.narrations or {}).items() if shot.id in by_shot}
        try:
            N.build_shot_narration(shot, LAYOUT_RISE, [LAYOUT_RISE + t for t in retimed], other, default)
        except N.NarrationError as e:
            add(_from_narration(e), si)

    out.errors = [e for _, e in sorted(found, key=lambda it: it[0])]
    return out


class _ShotSim:
    """One shot's actions on the World, in shot time (the real handlers' checks and durations)."""

    def __init__(self, world: World, marks: dict[str, Vec3], shot: Shot) -> None:
        self.world = world
        self.marks = {k: (float(v[0]), float(v[1]), float(v[2])) for k, v in marks.items()}
        self.shot = shot
        # Logical root paths (t, position) per puppet, for follow; vehicle motion windows.
        self.paths: dict[str, list[tuple[float, Vec3]]] = {who: [(0.0, p.pos)] for who, p in world.people.items()}
        self.moving_until: dict[str, float] = {}

    # ------------------------------------------------------------------ driver

    def run(self, a: Action, t: float) -> CompileError | None:
        w = self.world
        w.now = round(t, 6)
        try:
            if a.who is not None:
                w.check_free(a.who)
            if a.do in EFFECTS:
                end = t  # effects occupy nobody (see the module docstring for the real compiler's gap)
            else:
                end = getattr(self, f"do_{a.do}")(a, t)
            if a.who is not None:
                w.occupy(a.who, round(end, 6), ACTIVITY[a.do], at=w.now)
        except StateError as e:
            return _error(e.code, LEGACY_REASONS.get((e.code, e.reason), e.reason), self.shot.id, a)
        except ik.OutOfReach as e:
            return _out_of_reach(e, self.shot.id, a)
        except ValueError as e:
            return _error("invalid_action", str(e), self.shot.id, a)
        return None

    def camera(self) -> list[CompileError]:
        errs = []
        for cue in self.shot.camera:
            if cue.move not in TARGET_MOVES or cue.target is None:
                continue
            try:
                self.locate(cue.target)
            except StateError as e:
                errs.append(CompileError(
                    code="bad_camera",
                    message=error_message(self.shot.id, cue.t, None, cue.move, cue.target, e.reason),
                    shot=self.shot.id, t=float(cue.t), do=cue.move,
                ))
        return errs

    # ----------------------------------------------------------------- helpers

    def locate(self, name: str) -> Vec3:
        w = self.world
        if name in w.people:
            return w.people[name].pos
        if name in w.props:
            return self.prop_pos(name)
        if name in self.marks:
            return self.marks[name]
        raise StateError("unknown_target", f"{name} is not a person, prop or mark in shot {self.shot.id}")

    def prop_pos(self, what: str, seen: frozenset[str] = frozenset()) -> Vec3:
        w = self.world
        if what in seen:
            raise StateError("cycle", f"{what} rests on itself")
        st = w.props[what]
        parsed = parse_parent(st.parent)
        if parsed is not None and parsed[0] == "puppet":
            p = w.people[parsed[1]]
            return (p.pos[0], p.pos[1] + ik.shoulder_world("arm_r", (0.0, 0.0), 1, p.build)[1], p.pos[2])
        if parsed is not None and parsed[0] == "prop":
            base = self.prop_pos(parsed[1], seen | {what})
            off = motion.prop_socket_offset(w.catalog[parsed[1]], parsed[2])
            return (base[0] + off[0] + st.pos[0], base[1] + off[1] + st.pos[1], base[2] + off[2] + st.pos[2])
        if st.world_pos is None:
            raise StateError("not_on_stage", f"{what} is not on stage")
        return st.world_pos

    def body(self, who: str, facing: int | None = None, at: Vec3 | None = None) -> ik.Body:
        p = self.world.people[who]
        pos = at if at is not None else p.pos
        return ik.Body(who, (pos[0], pos[1]), p.facing if facing is None else facing, p.build)

    def geom(self, what: str) -> B.PropGeom:
        return B.PropGeom.from_prop(self.world.catalog[what])

    def on_stage(self, who: str, reason: str = "{who} is offstage; use enter first") -> None:
        if not self.world.on_stage(who):
            raise StateError("offstage", reason.format(who=who))

    def not_seated(self, who: str) -> None:
        seat = self.world.people[who].seat
        if seat is not None:
            raise StateError("seated", f"{who} is seated in {seat[0]}.{seat[1]}")

    def not_standing(self, who: str) -> None:
        p = self.world.people[who]
        if p.seat is None and p.pose in SEATED_POSES:
            what = "kneeling" if p.pose == "kneel" else "sitting on the floor"
            raise StateError("not_standing", f"{who} is {what}; stand_up first")

    def stand_first(self, who: str, t: float) -> float:
        """A puppet kneeling or sitting on the floor stands up before it moves."""
        p = self.world.people[who]
        return t + B.POSE_SECONDS if p.seat is None and p.pose in SEATED_POSES else t

    def travel(self, who: str, t: float, start: Vec3, dest: Vec3, gait: str = "walk") -> tuple[float, int]:
        p = self.world.people[who]
        _, arrival, facing = motion.locomotion_keys(who, p.build, t, start, dest, p.facing, gait)  # type: ignore[arg-type]
        if arrival > t:
            self.paths.setdefault(who, []).extend([(t, start), (arrival, dest)])
        return arrival, facing

    def dur(self, a: Action, default: float) -> float:
        dur = default if a.dur is None else float(a.dur)
        if not math.isfinite(dur) or dur <= 0:
            raise StateError("bad_dur", f"dur must be a positive number of seconds, got {a.dur}")
        return dur

    def face_toward(self, who: str, x: float) -> None:
        p = self.world.people[who]
        d = _sign(x - p.pos[0])
        if d:
            self.world.set_facing(who, d)

    def free_arm(self, who: str) -> str:
        holding = self.world.people[who].holding
        return "arm_l" if "hand_r" in holding and "hand_l" not in holding else "arm_r"

    # ---------------------------------------------------------------- movement

    def do_enter(self, a: Action, t: float) -> float:
        w, who = self.world, a.who
        to = self.marks[a.to]
        src = self.marks[a.from_ or motion.nearest_offstage(to[0])]
        self.not_seated(who)
        if w.on_stage(who):
            raise StateError("already_on_stage", f"{who} is already on stage; use walk_to")
        arrival, facing = self.travel(who, t, src, to)
        w.move(who, to, facing)
        return arrival

    def _locomote(self, a: Action, t: float, gait: str) -> float:
        w, who = self.world, a.who
        dest = self.marks[a.to]
        self.on_stage(who)
        self.not_seated(who)
        t0 = self.stand_first(who, t)
        arrival, facing = self.travel(who, t0, w.people[who].pos, dest, gait)
        w.move(who, dest, facing)
        return arrival

    def do_walk_to(self, a: Action, t: float) -> float:
        return self._locomote(a, t, "walk")

    def do_run_to(self, a: Action, t: float) -> float:
        return self._locomote(a, t, "run")

    def do_exit(self, a: Action, t: float) -> float:
        w, who = self.world, a.who
        explicit = self.marks[a.to] if a.to is not None else None
        dest = w.copy().exit(who, explicit)
        self.on_stage(who, "{who} is already offstage")
        t0 = self.stand_first(who, t)
        p = w.people[who]
        arrival, facing = self.travel(who, t0, p.pos, dest)
        w.exit(who, dest)
        w.set_facing(who, facing)
        return max(t, arrival)

    def do_turn_to(self, a: Action, t: float) -> float:
        if a.target == a.who:
            raise StateError("self_target", f"{a.who} cannot turn to face themselves")
        self.face_toward(a.who, self.locate(a.target)[0])
        return t

    def do_follow(self, a: Action, t: float) -> float:
        w, who, target = self.world, a.who, a.target
        if target == who:
            raise StateError("self_target", f"{who} cannot follow themselves")
        if target not in w.people:
            self.locate(target)
            raise StateError("bad_target", f"{who} can only follow a person, and {target} is not one")
        p = w.people[who]
        w.copy().move(who, p.pos)  # a seated puppet cannot walk
        path = sorted(self.paths.get(target, []), key=lambda k: k[0])
        moves_after = any(tb > t + 1e-9 and max(abs(pb[i] - pa[i]) for i in range(3)) > 1e-9
                          for (_, pa), (tb, pb) in zip(path, path[1:]))
        if not moves_after:
            raise StateError("nothing_to_follow", f"{target} does not move after t={fmt_t(w.now)}")
        t_go = self.stand_first(who, t)
        if a.dur is not None and t_go - t > a.dur + 1e-9:
            raise StateError("bad_time", f"{who} needs {fmt_t(t_go - t)}s to stand up before following; "
                                         f"follow dur {a.dur:g} is shorter than that")
        dur = None if a.dur is None else a.dur - (t_go - t)
        keys, stop, facing = motion.follow_keys(who, p.build, t_go, p.pos, p.facing, path, dur)
        final = next((k[3] for k in reversed(keys) if k[1] == "root.position"), None)
        end = p.pos if final is None else (float(final[0]), float(final[1]), float(final[2]))
        self.paths.setdefault(who, []).extend([(t_go, p.pos), (stop, end)])
        w.move(who, end, facing)
        return max(t, t_go, stop)

    # -------------------------------------------------------------------- body

    def do_pose(self, a: Action, t: float) -> float:
        w, who, pose = self.world, a.who, a.pose
        if a.dur is not None and a.dur <= 0:
            raise StateError("bad_dur", f"dur must be positive, not {fmt_t(a.dur)}")
        self.on_stage(who)
        p = w.people[who]
        if pose in (*SEATED_POSES, "stand"):
            self.not_seated(who)
        if a.dur is None:
            changes_legs = "rootOffset" in POSES[pose] or any(b.startswith("leg_") for b in POSES[pose])
            if not (p.pose in SEATED_POSES and pose != "stand" and not changes_legs):
                w.set_pose(who, pose)
            return t + B.POSE_SECONDS
        return t + a.dur + B.POSE_SECONDS

    def _gesture(self, a: Action, t: float, default: float) -> float:
        self.on_stage(a.who)
        dur = self.dur(a, default)
        if a.target is not None:
            self.face_toward(a.who, self.locate(a.target)[0])
        return t + dur

    def do_wave(self, a: Action, t: float) -> float:
        return self._gesture(a, t, WAVE_SECONDS)

    def do_point_at(self, a: Action, t: float) -> float:
        return self._gesture(a, t, POINT_SECONDS)

    def do_lean_toward(self, a: Action, t: float) -> float:
        return self._gesture(a, t, LEAN_SECONDS)

    def do_sit_on(self, a: Action, t: float) -> float:
        w, who, what = self.world, a.who, a.what
        socket = w.copy().sit_on(who, what, a.socket)
        self.on_stage(who)
        self.not_standing(who)
        holder = w.holder(what)
        if holder is not None:
            raise StateError("held", f"{holder[0]} is {'wearing' if holder[1] == HEAD else 'holding'} it")
        seat, anchor = w.socket_world(what, socket), w.props[what].world_pos
        if seat is None or anchor is None:
            raise StateError("not_on_stage", f"{what} is not on stage")
        p = w.people[who]
        stand_at = (seat[0], anchor[1], seat[2])
        t_sit, facing = t, p.facing
        if math.hypot(seat[0] - p.pos[0], seat[2] - p.pos[2]) > SEAT_WALK_EPS:
            t_sit, facing = self.travel(who, t, p.pos, stand_at)
        w.move(who, stand_at, facing=facing)
        w.sit_on(who, what, socket)
        return t_sit + B.POSE_SECONDS

    def do_stand_up(self, a: Action, t: float) -> float:
        w, who = self.world, a.who
        w.copy().stand_up(who)
        p = w.people[who]
        stand_at = p.pos
        if p.seat is not None:
            seat, anchor = w.socket_world(*p.seat), w.props[p.seat[0]].world_pos
            if seat is not None and anchor is not None:
                stand_at = (seat[0], anchor[1], seat[2])
        w.stand_up(who)
        w.move(who, stand_at)
        return t + B.POSE_SECONDS

    def do_kneel(self, a: Action, t: float) -> float:
        w, who = self.world, a.who
        dur = None if a.dur is None else self.dur(a, 0.0)
        w.copy().kneel(who)
        self.on_stage(who)
        if w.people[who].pose == "kneel":
            raise StateError("already_kneeling", f"{who} is already kneeling")
        self.not_standing(who)
        w.kneel(who)
        if dur is None:
            return t + B.POSE_SECONDS
        w.stand_up(who)
        return t + dur + B.POSE_SECONDS

    def do_hug(self, a: Action, t: float) -> float:
        w, who, other = self.world, a.who, a.target
        dur = self.dur(a, HUG_SECONDS)
        w.hug_check(who, other)
        w.check_free(other)
        me, them = w.people[who], w.people[other]
        dx = them.pos[0] - me.pos[0]
        t_hug = t
        if abs(dx) > HUG_GAP + HUG_SLACK:
            self.not_standing(who)
            side = 1 if dx > 0 else -1
            goal = (them.pos[0] - side * HUG_GAP, them.pos[1], them.pos[2])
            t_hug, facing = self.travel(who, t, me.pos, goal)
            w.move(who, goal, facing=facing)
        self.face_toward(who, them.pos[0])
        self.face_toward(other, w.people[who].pos[0])
        end = t_hug + dur
        w.occupy(other, round(end, 6), "hugging")
        return end

    # ------------------------------------------------------------------- props

    def do_pick_up(self, a: Action, t: float) -> float:
        w, who, what = self.world, a.who, a.what
        hand = w.copy().pick_up(who, what)
        self.on_stage(who, "{who} is offstage")
        anchor = w.props[what].world_pos
        if anchor is None:
            raise StateError("not_on_stage", f"{what} is not on stage")
        p = w.people[who]
        arm = _arm_of_hand(hand)
        grip = B.grip_from_anchor(self.geom(what), anchor)
        here = p.pos
        d = _sign(grip[0] - here[0])
        facing = d or p.facing
        t_reach, walk_to = t, None
        ground = (here[0], here[1])
        if not ik.can_reach(arm, grip, ground, facing, p.build):
            reach = ik.arm_reach(p.build, arm)
            if d != 0 and abs(grip[0] - here[0]) > AUTO_WALK_REACH * reach:
                self.not_seated(who)
                walk_to = (grip[0] - d * AUTO_WALK_REACH * reach, p.pos[1], p.pos[2])
                t_reach, facing = self.travel(who, self.stand_first(who, t), here, walk_to)
                ground = (walk_to[0], walk_to[1])
        if ik.can_reach(arm, grip, ground, facing, p.build) or grip[1] >= ik.shoulder_world(arm, ground, facing, p.build)[1]:
            end = t_reach + 2 * B.REACH_SECONDS
        else:  # bends (kneeling if needed) down to a low grip and comes back up
            end = t_reach + 2 * B.POSE_SECONDS + B.REACH_SECONDS
        if walk_to is not None:
            w.move(who, walk_to, facing)
        else:
            w.set_facing(who, facing)
        w.pick_up(who, what, hand)
        return end

    def do_hold(self, a: Action, t: float) -> float:
        self.world.hold(a.who, a.what)
        return t + B.POSE_SECONDS

    def do_drop(self, a: Action, t: float) -> float:
        w, who, what = self.world, a.who, a.what
        w.hold(who, what)
        p = w.people[who]
        w.drop(who, what, (p.pos[0] + B.DROP_OFFSET * p.facing, p.pos[1], p.pos[2]))
        return t + B.FALL_SECONDS

    def do_hand_to(self, a: Action, t: float) -> float:
        w, who, what, to = self.world, a.who, a.what, a.to
        taker_hand = w.copy().hand_to(who, what, to)
        w.check_free(to)
        holder = w.holder(what)
        assert holder is not None
        g, k = w.people[who], w.people[to]
        g_facing = _sign(k.pos[0] - g.pos[0]) or g.facing
        k_facing = _sign(g.pos[0] - k.pos[0]) or k.facing
        ik.handover_point(self.body(who, g_facing), self.body(to, k_facing),
                          _arm_of_hand(holder[1]), _arm_of_hand(taker_hand))
        end = t + B.PASS_SECONDS + B.RETURN_SECONDS
        w.set_facing(who, g_facing)
        w.set_facing(to, k_facing)
        w.hand_to(who, what, to)
        w.occupy(to, round(end, 6), f"taking {what}", at=w.now)
        return end

    def do_put_on(self, a: Action, t: float) -> float:
        w, who, what = self.world, a.who, a.what
        w.copy().put_on(who, what, a.target)
        target = who if a.target is None else a.target
        if target != who:
            w.check_free(target)
        geom = self.geom(what)
        me = self.body(who)
        held = w.holder(what)
        if held is not None:
            arm = _arm_of_hand(held[1])
            end = t + 2 * B.REACH_SECONDS
        else:
            hand = w.free_hand(who)
            assert hand is not None
            arm = _arm_of_hand(hand)
            ik.solve_body_arm(me, arm, B.grip_from_anchor(geom, w.props[what].world_pos))
            end = t + 3 * B.REACH_SECONDS
        head = ik.body_socket(me if target == who else self.body(target), HEAD, {})
        ik.solve_body_arm(me, arm, B.grip_from_anchor(geom, head))
        w.put_on(who, what, a.target)
        if target != who:
            w.occupy(target, round(end, 6), "putting it on", at=w.now)
        return end

    def do_place_on(self, a: Action, t: float) -> float:
        w, who, what, on = self.world, a.who, a.what, a.on
        hand = w.hold(who, what)
        if on in w.catalog:
            socket = a.socket
            if socket is None:
                sockets = w.catalog[on].sockets
                if not sockets:
                    raise StateError("no_socket", f"{on} has no sockets to place on")
                free = [s for s in sockets if s not in w.props[on].occupants]
                if not free:
                    raise StateError("socket_taken", f"{on} has no free socket")
                socket = free[0]
            parent = f"prop:{on}.{socket}"
            w.copy().place_on(who, what, parent, (0.0, 0.0, 0.0))
            dest = w.socket_world(on, socket)
            if dest is None:
                h = w.holder(on)
                if h is not None:
                    raise StateError("held", f"{h[0]} is holding {on}")
                raise StateError("not_on_stage", f"{on} is not on stage")
            pos: Vec3 = (0.0, 0.0, 0.0)
        elif on in w.people:
            raise StateError("bad_target", f"{on} is a person; place_on needs a prop or a mark")
        else:
            dest = self.locate(on)
            if a.socket is not None:
                raise StateError("unknown_socket", f"{on} is a mark and has no socket {a.socket}")
            parent = "ground"
            w.copy().place_on(who, what, parent, dest)
            pos = dest
        ik.solve_body_arm(self.body(who), _arm_of_hand(hand), B.grip_from_anchor(self.geom(what), dest))
        w.place_on(who, what, parent, pos)
        return t + 2 * B.REACH_SECONDS

    def _open_close(self, a: Action, t: float, opening: bool) -> float:
        w, who, what = self.world, a.who, a.what
        scratch = w.copy()
        (scratch.open if opening else scratch.close)(who, what)
        if w.holder(what) is None:
            anchor = self.prop_pos(what)
            geom = self.geom(what)
            if w.catalog[what].grip is not None:
                handle = B.grip_from_anchor(geom, anchor)
            else:
                handle = (anchor[0], anchor[1] + geom.height / 2)
            ik.solve_body_arm(self.body(who), self.free_arm(who), handle)
        (w.open if opening else w.close)(who, what)
        return t + 2 * B.REACH_SECONDS

    def do_open(self, a: Action, t: float) -> float:
        return self._open_close(a, t, True)

    def do_close(self, a: Action, t: float) -> float:
        return self._open_close(a, t, False)

    def do_set_state(self, a: Action, t: float) -> float:
        self.world.set_prop_state(a.what, a.state)
        return t

    # ---------------------------------------------------------------- vehicles

    def _vehicle(self, what: str) -> None:
        if not self.world.is_vehicle(what):
            raise StateError("not_a_vehicle", f"{what} is not a vehicle")

    def _still(self, what: str, t: float) -> None:
        until = self.moving_until.get(what)
        if until is not None and until > t + 1e-9:
            raise StateError("vehicle_moving", f"{what} is moving until t={fmt_t(until)}")

    def do_get_in(self, a: Action, t: float) -> float:
        w, who, what = self.world, a.who, a.what
        self._vehicle(what)
        anchor = w.props[what].world_pos
        if anchor is None:
            raise StateError("not_on_stage", f"{what} is not on stage")
        socket = w.copy().get_in(who, what, a.socket)
        self._still(what, t)
        p = w.people[who]
        off = motion.prop_socket_offset(w.catalog[what], socket)
        seat = (anchor[0] + off[0], anchor[1] + off[1], anchor[2] + off[2])
        t_sit, facing = t, p.facing
        if abs(seat[0] - p.pos[0]) > WALK_UP_THRESHOLD or abs(seat[2] - p.pos[2]) > 1e-6:
            t_sit, facing = self.travel(who, t, p.pos, (seat[0], anchor[1], seat[2]))
        hip = PUPPET["hipHeight"] * ik.scale(p.build)
        w.move(who, (seat[0], seat[1] - hip, seat[2]), facing)
        w.get_in(who, what, socket)
        return t_sit + B.POSE_SECONDS

    def do_get_out(self, a: Action, t: float) -> float:
        w, who, what = self.world, a.who, a.what
        self._vehicle(what)
        w.copy().get_out(who, what)
        self._still(what, t)
        socket = w.people[who].seat[1]  # type: ignore[index]
        anchor = self.prop_pos(what)
        off = motion.prop_socket_offset(w.catalog[what], socket)
        w.get_out(who, what)
        w.move(who, (anchor[0] + off[0], anchor[1], ACTING_Z))
        return t + B.POSE_SECONDS

    def _drive(self, what: str, to: str, t: float) -> tuple[float, Vec3]:
        w = self.world
        if w.props[what].world_pos is None:
            raise StateError("not_on_stage", f"{what} is not on stage")
        if w.props[what].parent != "ground":
            raise StateError("not_on_ground", f"{what} is not on the ground")
        self._still(what, t)
        end = self.locate(to)
        _, arrival = motion.drive_keys(what, t, w.props[what].world_pos, end)
        if arrival > t:
            self.moving_until[what] = arrival
        return arrival, end

    def _riders(self, what: str) -> list[str]:
        return [o.partition(":")[2] for o in self.world.props[what].occupants.values() if o.startswith("puppet:")]

    def do_drive_to(self, a: Action, t: float) -> float:
        w = self.world
        self._vehicle(a.what)
        w.check_driver(a.who, a.what)
        arrival, end = self._drive(a.what, a.to, t)
        w.move_vehicle(a.what, end)
        return arrival

    def do_ride_to(self, a: Action, t: float) -> float:
        w = self.world
        self._vehicle(a.what)
        w.check_rider(a.who, a.what)
        driver = w.props[a.what].occupants[DRIVER_SOCKET].partition(":")[2]
        others = [r for r in self._riders(a.what) if r != a.who]
        for other in others:
            w.check_free(other)
        arrival, end = self._drive(a.what, a.to, t)
        w.move_vehicle(a.what, end)
        for other in others:
            w.occupy(other, round(arrival, 6), "driving" if other == driver else "riding", at=w.now)
        return arrival


# ========================================================================= output


class _Tracks:
    def __init__(self) -> None:
        self._keys: dict[tuple[str, str], list[tuple[float, Any]]] = {}

    def key(self, target: str, channel: str, t: float, value: Any) -> None:
        self._keys.setdefault((target, channel), []).append((t, value))

    def has(self, target: str, channel: str) -> bool:
        return bool(self._keys.get((target, channel)))

    def truncate(self, target: str, channel: str, t: float) -> None:
        """Drop keys at or after t (a later event supersedes planned motion)."""
        keys = self._keys.get((target, channel))
        if keys:
            self._keys[(target, channel)] = [k for k in keys if k[0] < t]

    def _sorted(self, target: str, channel: str) -> list[tuple[float, Any]]:
        keys = sorted(self._keys.get((target, channel), []), key=lambda k: k[0])  # stable
        out: list[tuple[float, Any]] = []
        for t, v in keys:
            if out and math.isclose(out[-1][0], t, abs_tol=1e-9):
                out[-1] = (t, v)  # same time: the later write wins
            else:
                out.append((t, v))
        return out

    def sample(self, target: str, channel: str, t: float) -> Any:
        keys = self._sorted(target, channel)
        if not keys:
            return None
        if t <= keys[0][0]:
            return keys[0][1]
        for (t0, v0), (t1, v1) in zip(keys, keys[1:]):
            if t0 <= t <= t1:
                if isinstance(v0, list) and isinstance(v1, list) and t1 > t0:
                    a = (t - t0) / (t1 - t0)
                    return [x0 + (x1 - x0) * a for x0, x1 in zip(v0, v1)]
                if isinstance(v0, (int, float)) and isinstance(v1, (int, float)) and t1 > t0:
                    a = (t - t0) / (t1 - t0)
                    return v0 + (v1 - v0) * a
                return v0 if t < t1 else v1
        return keys[-1][1]

    def build(self) -> list[Track]:
        out = []
        for target, channel in sorted(self._keys):
            keys = [(_r(float(t)), _r(v)) for t, v in self._sorted(target, channel)]
            if keys:
                out.append(Track(target=target, channel=channel, keys=keys))
        return out


@dataclass
class _Puppet:
    id: str
    scale: float
    ground: Vec = field(default_factory=lambda: [-1.3, 0.0, ACTING_Z])
    facing: int = 1
    bones: dict[str, float] = field(default_factory=dict)
    offset_y: float = 0.0
    busy_until: float = 0.0
    pose_busy: float = 0.0

    @property
    def target(self) -> str:
        return f"puppet:{self.id}"

    def pos(self) -> Vec:
        return [self.ground[0], self.ground[1] + self.offset_y * self.scale, self.ground[2]]


@dataclass
class _Prop:
    id: str
    parent: str = "ground"
    pos: Vec = field(default_factory=lambda: [-1.3, 0.0, ACTING_Z])

    @property
    def target(self) -> str:
        return f"prop:{self.id}"

    def holder(self) -> str | None:
        m = re.fullmatch(r"puppet:([^.]+)\.hand_[lr]", self.parent)
        return m.group(1) if m else None

    def wearer(self) -> str | None:
        m = re.fullmatch(r"puppet:([^.]+)\.head", self.parent)
        return m.group(1) if m else None


class FakeCompiler:
    """Deterministic checker + simple timeline builder. Call it like a Compiler."""

    def __call__(self, screenplay: Screenplay, scene: SceneInputs) -> CompileResult:
        checked = check(screenplay, scene)
        if checked.errors:
            return CompileResult(ok=False, errors=checked.errors)
        return CompileResult(ok=True, timeline=_Run(screenplay, scene, checked.retimed).timeline())

    def __repr__(self) -> str:
        return "FakeCompiler()"


class _Run:
    """Builds the Timeline for a screenplay `check` has passed. It reports no errors itself."""

    def __init__(self, screenplay: Screenplay, scene: SceneInputs, retimed: dict[str, list[float]]) -> None:
        self.sp = screenplay
        self.scene = scene
        self.retimed = retimed
        self.tracks = _Tracks()
        self.effects: list[TimelineEffect] = []
        self.cast = list(dict.fromkeys(screenplay.cast))
        self.puppets: dict[str, _Puppet] = {}
        for pid in self.cast:
            person = scene.people.get(pid)
            build = person.character.build if person is not None else "average"
            self.puppets[pid] = _Puppet(pid, PUPPET["stageHeight"] * BUILDS.get(build, 1.0))
        self.props: dict[str, _Prop] = {pid: _Prop(pid) for pid in screenplay.props}
        self.shot_times: dict[str, TimelineShot] = {}
        # per-shot context
        self.marks: dict[str, list[float]] = {}
        self.t0 = 0.0  # action_start of the current shot
        self.cam = [list(CAM_PULL_BACK[0]), list(CAM_PULL_BACK[1]), CAM_PULL_BACK[2]]
        self.cam_t = 0.0

    # -------------------------------------------------------------- lookups

    def mark_pos(self, name: str) -> Vec:
        m = self.marks[name]
        return [2 * m[0] - 1, m[1], m[2] if len(m) > 2 else ACTING_Z]

    def prop_world(self, prop: _Prop, t: float) -> Vec:
        holder = prop.holder() or prop.wearer()
        if holder is not None:
            p = self.tracks.sample(f"puppet:{holder}", "root.position", t) or self.puppets[holder].pos()
            return [p[0], p[1] + 0.25, p[2]]
        if prop.parent.startswith("prop:"):
            other = self.props.get(prop.parent[5:].split(".")[0])
            return list(other.pos) if other is not None else list(prop.pos)
        return list(prop.pos)

    def resolve(self, x: str | None, t: float) -> Vec | None:
        """Person, prop or mark -> world position at timeline time t."""
        if x is None:
            return None
        if x in self.puppets:
            return self.tracks.sample(f"puppet:{x}", "root.position", t) or self.puppets[x].pos()
        if x in self.props:
            return self.prop_world(self.props[x], t)
        if x in self.marks:
            return self.mark_pos(x)
        return None

    # --------------------------------------------------------- emit helpers

    def face(self, p: _Puppet, t: float, facing: int) -> None:
        if facing != p.facing or not self.tracks.has(p.target, "root.facing"):
            p.facing = facing
            self.tracks.key(p.target, "root.facing", t, facing)

    def place(self, p: _Puppet, t: float, ground: Vec) -> None:
        p.ground = list(ground)
        self.tracks.key(p.target, "root.position", t, p.pos())

    def move(self, p: _Puppet, t: float, dest: Vec, speed: float) -> float:
        start = max(t, p.busy_until)
        dist = math.dist(p.ground, dest)
        dur = max(dist / speed, MIN_MOVE)
        self.tracks.key(p.target, "root.position", start, p.pos())
        dx = dest[0] - p.ground[0]
        if abs(dx) > 1e-9:
            self.face(p, start, 1 if dx > 0 else -1)
        p.ground = list(dest)
        self.tracks.key(p.target, "root.position", start + dur, p.pos())
        p.busy_until = start + dur
        return start + dur

    def set_pose(self, p: _Puppet, t: float, bones: dict[str, float], offset_y: float | None = None,
                 dur: float = POSE_DUR) -> float:
        start = max(t, p.pose_busy)
        for bone in sorted(set(p.bones) | set(bones)):
            channel = f"{bone}.rot"
            self.tracks.key(p.target, channel, start, p.bones.get(bone, 0.0))
            self.tracks.key(p.target, channel, start + dur, bones.get(bone, 0.0))
        p.bones = dict(bones)
        if offset_y is not None and offset_y != p.offset_y:
            self.tracks.key(p.target, "root.position", start, p.pos())
            p.offset_y = offset_y
            self.tracks.key(p.target, "root.position", start + dur, p.pos())
        p.pose_busy = start + dur
        return start + dur

    def gesture(self, p: _Puppet, t: float, pose: str, dur: float | None) -> float:
        before, before_off = dict(p.bones), p.offset_y
        end = self.set_pose(p, t, _bones(pose))
        return self.set_pose(p, end + (dur if dur is not None else GESTURE_DUR), before, before_off)

    def prop_parent(self, prop: _Prop, t: float, parent: str, pos: Vec) -> None:
        for channel in ("parent", "position"):
            self.tracks.truncate(prop.target, channel, t)
        prop.parent = parent
        prop.pos = list(pos)
        self.tracks.key(prop.target, "parent", t, parent)
        self.tracks.key(prop.target, "position", t, [0.0, 0.0, 0.0] if parent != "ground" else list(pos))

    # ---------------------------------------------------------------- shots

    def timeline(self) -> Timeline:
        start = 0.0
        for i, shot in enumerate(self.sp.shots):
            start = self.run_shot(shot, start, i)
        return self.build_timeline(start)

    def run_shot(self, shot: Shot, start: float, index: int) -> float:
        set_def = self.scene.sets[shot.set]
        self.marks = {**BUILTIN_MARKS, **set_def.marks, **shot.marks}
        self.t0 = start + RISE

        self.shot_start(shot, start)
        times = self.retimed.get(shot.id) or [float(a.t) for a in shot.actions]
        order = sorted(range(len(shot.actions)), key=lambda i: (times[i], i))
        ends = [self.do_action(shot.actions[i], times[i]) for i in order]

        last_action = max((max(t + ACTION_TAIL, 0.0) for t in times), default=0.0)
        last_motion = max((e - self.t0 + 0.5 for e in ends), default=0.0)
        narr_ts = [ln.t for ln in shot.narration]
        for by_shot in (self.sp.narrations or {}).values():
            narr_ts += [ln.t for ln in by_shot.get(shot.id, [])]
        last_narr = max((t + NARRATION_TAIL for t in narr_ts), default=0.0)
        end = self.t0 + max(last_action, last_motion, last_narr, 2.0)

        self.camera(shot, start)
        self.shot_times[shot.id] = TimelineShot(
            id=shot.id, set=shot.set, start=start, action_start=self.t0, end=end,
            transition=shot.transition, page_text=shot.page_text,
        )
        is_last = index == len(self.sp.shots) - 1
        for n, _layer in enumerate(set_def.layers):
            target = f"set:{shot.set}.layer{n}"
            self.tracks.key(target, "fold", start, 0.0)
            self.tracks.key(target, "fold", self.t0, 1.0)
            if not is_last:
                self.tracks.key(target, "fold", end - FOLD_DOWN, 1.0)
                self.tracks.key(target, "fold", end, 0.0)
        if not is_last:
            self.settle(end - SETTLE)
        return end

    def settle(self, t: float) -> None:
        """Hold every puppet and prop where it is until the next shot starts."""
        for p in self.puppets.values():
            for channel in ["root.position"] + [f"{b}.rot" for b in sorted(p.bones)]:
                if self.tracks.has(p.target, channel):
                    self.tracks.key(p.target, channel, t, self.tracks.sample(p.target, channel, t))
        for prop in self.props.values():
            if self.tracks.has(prop.target, "position"):
                self.tracks.key(prop.target, "position", t, self.tracks.sample(prop.target, "position", t))

    def shot_start(self, shot: Shot, start: float) -> None:
        # puppets: named ones at their mark, everyone else offstage left
        for pid in self.cast:
            p = self.puppets[pid]
            st = shot.start.get(pid)
            at = st.at if st is not None and st.at in self.marks else "offstage_left"
            ground = self.mark_pos(at)
            pose = st.pose if st is not None and st.pose in POSES else "stand"
            p.busy_until = p.pose_busy = start
            self.set_pose(p, start, _bones(pose), _root_offset_y(pose), dur=0.0)
            self.place(p, start, ground)
            if st is not None and st.facing is not None:
                facing = 1 if st.facing == "right" else -1
            else:
                facing = 1 if ground[0] <= 0 else -1
            self.tracks.key(p.target, "root.facing", start, facing)
            p.facing = facing

        # props: explicit start states, otherwise whatever the last shot left
        for pid, st in shot.start.items():
            if pid in self.puppets and st.holding is not None and st.holding in self.props:
                self.prop_parent(self.props[st.holding], start, f"puppet:{pid}.hand_r", [0.0, 0.0, 0.0])
        for prop_id, ps in shot.prop_start.items():
            prop = self.props.get(prop_id)
            if prop is None:
                continue
            if ps.on is not None:
                base = ps.on if "." in ps.on else f"{ps.on}.{sorted(self.scene.props[ps.on].sockets or {'top': 0})[0]}"
                self.prop_parent(prop, start, f"prop:{base}", [0.0, 0.0, 0.0])
            elif ps.at is not None and ps.at in self.marks:
                self.prop_parent(prop, start, "ground", self.mark_pos(ps.at))
            if ps.state is not None:
                self.tracks.key(prop.target, "state", start, ps.state)
        for prop in self.props.values():
            if not self.tracks.has(prop.target, "parent"):
                self.prop_parent(prop, 0.0, prop.parent, prop.pos)

    # --------------------------------------------------------------- actions

    def do_action(self, a: Action, t: float) -> float:
        """Key one action that `check` accepted, starting at shot time t. Returns when it ends."""
        T = self.t0 + t
        if ACTIONS[a.do]["kind"] == "effect":
            at = self.resolve(a.at, T)
            self.effects.append(TimelineEffect(t=T, kind=a.do, at=_r(at), dur=a.dur, value=a.value))
            return T + (a.dur or 0.0)
        p = self.puppets.get(a.who or "")
        prop = self.props.get(a.what or "")
        if p is None and a.do != "set_state":
            return T

        match a.do:
            case "walk_to" | "run_to":
                return self.move(p, T, self.mark_pos(a.to), SPEEDS["run" if a.do == "run_to" else "walk"])
            case "enter":
                dest = self.mark_pos(a.to)
                side = a.from_ or ("offstage_left" if dest[0] <= 0 else "offstage_right")
                begin = self.mark_pos(side)
                if math.dist(p.ground, begin) > 1e-9:
                    start = max(T, p.busy_until)
                    self.tracks.key(p.target, "root.position", start, p.pos())
                    self.place(p, start + 0.01, begin)
                    p.busy_until = start + 0.01
                return self.move(p, T, dest, SPEEDS["walk"])
            case "exit":
                side = a.to or ("offstage_left" if p.ground[0] <= 0 else "offstage_right")
                return self.move(p, T, self.mark_pos(side), SPEEDS["walk"])
            case "turn_to":
                tx = (self.resolve(a.target, T) or p.ground)[0]
                if abs(tx - p.ground[0]) > 1e-9:
                    self.face(p, max(T, p.busy_until), 1 if tx > p.ground[0] else -1)
                return T
            case "follow":
                other = self.resolve(a.target, T) or p.ground
                direction = 1 if other[0] >= p.ground[0] else -1
                return self.move(p, T, [other[0] - 0.15 * direction, other[1], other[2]], SPEEDS["walk"])
            case "pose":
                return self.set_pose(p, T, _bones(a.pose), _root_offset_y(a.pose), dur=a.dur or POSE_DUR)
            case "wave":
                return self.gesture(p, T, "wave", a.dur)
            case "point_at":
                tx = (self.resolve(a.target, T) or p.ground)[0]
                if abs(tx - p.ground[0]) > 1e-9:
                    self.face(p, T, 1 if tx > p.ground[0] else -1)
                return self.gesture(p, T, "point", a.dur)
            case "kneel":
                end = self.set_pose(p, T, _bones("kneel"), _root_offset_y("kneel"))
                return self.set_pose(p, end + a.dur, _bones("stand"), 0.0) if a.dur else end
            case "stand_up" | "get_out":
                return self.set_pose(p, T, _bones("stand"), 0.0)
            case "sit_on" | "get_in":
                seat = self.prop_world(prop, T)
                end = self.move(p, T, [seat[0], p.ground[1], p.ground[2]], SPEEDS["walk"])
                return self.set_pose(p, end, _bones("sit"), _root_offset_y("sit"))
            case "hug":
                other = self.puppets[a.target]
                self.face(p, T, 1 if other.ground[0] >= p.ground[0] else -1)
                self.face(other, T, -p.facing)
                self.gesture(other, T, "hug", a.dur or HUG_SECONDS)
                return self.gesture(p, T, "hug", a.dur or HUG_SECONDS)
            case "lean_toward":
                tx = (self.resolve(a.target, T) or p.ground)[0]
                if abs(tx - p.ground[0]) > 1e-9:
                    self.face(p, T, 1 if tx > p.ground[0] else -1)
                before = dict(p.bones)
                end = self.set_pose(p, T, {**before, "torso": -0.2})
                return self.set_pose(p, end + (a.dur or GESTURE_DUR), before)
            case "pick_up":
                grab = max(T, p.busy_until) + POSE_DUR
                self.gesture(p, max(T, p.busy_until), "reach", 0.1)
                self.prop_parent(prop, grab, f"puppet:{p.id}.hand_r", [0.0, 0.0, 0.0])
                return grab + POSE_DUR
            case "hold":
                return self.set_pose(p, T, {**p.bones, **_bones("hold")})
            case "drop":
                x = p.ground[0] + B.DROP_OFFSET * p.facing
                self.prop_parent(prop, T, "ground", [x, p.ground[1] + 0.2, p.ground[2]])
                landed = [x, p.ground[1], p.ground[2]]
                self.tracks.key(prop.target, "position", T + B.FALL_SECONDS, landed)
                prop.pos = landed
                return T + B.FALL_SECONDS
            case "hand_to":
                other = self.puppets[a.to]
                self.face(p, T, 1 if other.ground[0] >= p.ground[0] else -1)
                self.prop_parent(prop, T + POSE_DUR, f"puppet:{other.id}.hand_r", [0.0, 0.0, 0.0])
                return T + POSE_DUR
            case "put_on":
                wearer = a.target or p.id
                self.prop_parent(prop, T, f"puppet:{wearer}.head", [0.0, 0.0, 0.0])
                return T + POSE_DUR
            case "place_on":
                if a.on in self.marks:
                    self.prop_parent(prop, T + POSE_DUR, "ground", self.mark_pos(a.on))
                else:
                    host = self.scene.props[a.on]
                    socket = a.socket or (sorted(host.sockets)[0] if host.sockets else "top")
                    self.prop_parent(prop, T + POSE_DUR, f"prop:{a.on}.{socket}", [0.0, 0.0, 0.0])
                return T + POSE_DUR
            case "open" | "close" | "set_state":
                state = {"open": "open", "close": "closed"}.get(a.do, a.state)
                self.tracks.key(prop.target, "state", T, state)
                return T
            case "drive_to" | "ride_to":
                dest = self.mark_pos(a.to)
                was = self.prop_world(prop, T) if prop.parent == "ground" else None
                end = self.move(p, T, dest, SPEEDS["drive"])
                if was is not None:
                    start = end - max(math.dist(was, dest) / SPEEDS["drive"], MIN_MOVE)
                    self.tracks.key(prop.target, "position", max(start, T), list(prop.pos))
                    prop.pos = [dest[0], prop.pos[1], prop.pos[2]]
                    self.tracks.key(prop.target, "position", end, list(prop.pos))
                return end
        return T  # pragma: no cover - every verb is matched above

    # -------------------------------------------------------------- narration

    def default_author(self) -> str | None:
        try:
            return N.default_author(self.sp)
        except N.NarrationError:  # pragma: no cover - check() rejects this
            return self.cast[0] if self.cast else None

    def narration_tracks(self) -> dict[str, NarrationTrack]:
        def convert(shot_id: str, lines: list[NarrationLine]) -> list[NarrationLine]:
            t0 = self.shot_times[shot_id].action_start
            return [ln.model_copy(update={"t": _r(t0 + ln.t)}, deep=True) for ln in lines]

        out: dict[str, NarrationTrack] = {}
        default_lines = [ln for s in self.sp.shots for ln in convert(s.id, s.narration)]
        author = self.default_author()
        if default_lines and author is not None:
            out[author] = NarrationTrack(author_id=author, lines=default_lines)
        for other, by_shot in (self.sp.narrations or {}).items():
            lines = [ln for s in self.sp.shots for ln in convert(s.id, by_shot.get(s.id, []))]
            if other in out:
                out[other].lines = sorted(out[other].lines + lines, key=lambda ln: ln.t)
            else:
                out[other] = NarrationTrack(author_id=other, lines=lines)
        for track in out.values():
            track.captions = [Caption(t=ln.t, text=ln.text) for ln in track.lines]
        return out

    # ----------------------------------------------------------------- camera

    def camera(self, shot: Shot, start: float) -> None:
        self.cam_key(start, CAM_PULL_BACK, 0.0)
        self.cam_key(self.t0, CAM_WIDE, 0.0)
        for cue in sorted(shot.camera, key=lambda c: c.t):
            T = self.t0 + cue.t
            tgt = self.resolve(cue.target, T)
            match cue.move:
                case "wide":
                    pose = CAM_WIDE
                case "pull_back":
                    pose = CAM_PULL_BACK
                case _ if tgt is None:
                    continue
                case "dolly_to":
                    pose = ([tgt[0], tgt[1] + 0.3, 1.5], [tgt[0], tgt[1] + 0.25, 0.5], 35.0)
                case "pan_to":
                    pose = ([tgt[0], 0.6, 2.6], [tgt[0], 0.35, 0.5], 35.0)
                case _:  # close_up
                    pose = ([tgt[0], tgt[1] + 0.3, 1.0], [tgt[0], tgt[1] + 0.3, 0.5], 30.0)
            self.cam_key(T, pose, cue.dur if cue.dur is not None else (0.0 if cue.t == 0 else 1.0))

    def cam_key(self, t: float, pose: tuple[Vec, Vec, float], dur: float) -> None:
        start = max(t, self.cam_t)
        if dur > 0:
            self.tracks.key("camera", "position", start, list(self.cam[0]))
            self.tracks.key("camera", "lookAt", start, list(self.cam[1]))
            self.tracks.key("camera", "fov", start, self.cam[2])
        self.cam = [list(pose[0]), list(pose[1]), pose[2]]
        self.tracks.key("camera", "position", start + dur, list(pose[0]))
        self.tracks.key("camera", "lookAt", start + dur, list(pose[1]))
        self.tracks.key("camera", "fov", start + dur, pose[2])
        self.cam_t = start + dur

    # --------------------------------------------------------------- timeline

    def build_timeline(self, duration: float) -> Timeline:
        narrations = self.narration_tracks()
        default = self.default_author() if self.default_author() in narrations else next(iter(narrations), None)
        interactions = None
        if self.sp.interactions is not None:
            interactions = []
            first = self.sp.shots[0].id if self.sp.shots else None
            for inter in self.sp.interactions:
                inter = inter.model_copy(deep=True)
                sid = inter.shot or first
                d = inter.drives
                if d.type == "scrub" and sid in self.shot_times:
                    t0 = self.shot_times[sid].action_start
                    d.from_ = _r(t0 + d.from_) if d.from_ is not None else None
                    d.to = _r(t0 + d.to) if d.to is not None else None
                interactions.append(inter)
        explanations = [e.model_copy(deep=True) for e in self.sp.explanations] if self.sp.explanations is not None else None
        timeline = Timeline(
            duration=_r(duration),
            audio=None,
            tracks=self.tracks.build(),
            captions=list(narrations[default].captions) if default else [],
            shots=[s.model_copy(update={"start": _r(s.start), "action_start": _r(s.action_start), "end": _r(s.end)})
                   for s in self.shot_times.values()],
            effects=sorted(self.effects, key=lambda e: e.t),
            narrations=narrations or None,
            default_narrator=default,
            interactions=interactions,
            explanations=explanations,
        )
        # Round-trip through validation so the result is exactly what a consumer would parse.
        return Timeline.model_validate(timeline.model_dump(mode="json", by_alias=True))
