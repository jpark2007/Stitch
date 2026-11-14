"""FakeCompiler: a deterministic stand-in for screenplay.compiler, for agent tests.

It validates a screenplay the way the director needs (exact, machine-readable errors in the
`shot <id>, t=<t>: <detail>` shape) and, when the screenplay is valid, emits a Timeline that
validates against `screenplay.schema.Timeline` and follows the channel grammar in
shared/CONTRACTS.md. Motion is deliberately simple (straight-line walks, fixed camera numbers,
no IK, no cue retiming): validity and determinism matter here, not exact motion.

This is NOT the real compiler. `agent.tools.compile.load_compiler()` only uses it while
`screenplay.compiler.compile` does not exist.

Error codes produced:
  unknown_set, unknown_person, unknown_prop, unknown_mark, unknown_target, unknown_pose,
  missing_param, unexpected_param, invalid_value, not_holding, already_held, unknown_cue,
  unknown_cue_word, unknown_shot, duplicate_shot.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

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
TIMES_OF_DAY: list[str] = VOCABULARY["timesOfDay"]
CAMERA_MOVES: dict[str, dict[str, Any]] = VOCABULARY["camera"]

MOVEMENT_TO = {"walk_to", "run_to", "enter", "exit", "drive_to", "ride_to"}
PERSON_TARGET = {"follow", "hug", "put_on"}
ANY_TARGET = {"turn_to", "wave", "point_at", "lean_toward"}
ALWAYS_ALLOWED = {"t", "do", "id"}

CAM_PULL_BACK = ([0.0, 1.1, 3.4], [0.0, 0.2, 0.5], 35.0)
CAM_WIDE = ([0.0, 0.6, 2.6], [0.0, 0.35, 0.5], 35.0)

Vec = list[float]


def fmt_t(t: float) -> str:
    """5.0 -> '5', 3.25 -> '3.25'."""
    return f"{t:g}"


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


# ------------------------------------------------------------------------- tracks


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

    def last(self, target: str, channel: str) -> tuple[float, Any] | None:
        keys = self._sorted(target, channel)
        return keys[-1] if keys else None

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


# -------------------------------------------------------------------------- state


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


# --------------------------------------------------------------------------- compiler


class FakeCompiler:
    """Deterministic validator + simple timeline builder. Call it like a Compiler."""

    def __call__(self, screenplay: Screenplay, scene: SceneInputs) -> CompileResult:
        return _Run(screenplay, scene).compile()

    def __repr__(self) -> str:
        return "FakeCompiler()"


class _Run:
    def __init__(self, screenplay: Screenplay, scene: SceneInputs) -> None:
        self.sp = screenplay
        self.scene = scene
        self.errors: list[CompileError] = []
        self.tracks = _Tracks()
        self.effects: list[TimelineEffect] = []
        self.cast = list(screenplay.cast)
        self.puppets: dict[str, _Puppet] = {}
        for pid in self.cast:
            person = scene.people.get(pid)
            build = person.character.build if person is not None else "average"
            self.puppets[pid] = _Puppet(pid, PUPPET["stageHeight"] * BUILDS.get(build, 1.0))
        self.props: dict[str, _Prop] = {pid: _Prop(pid) for pid in screenplay.props}
        self.shot_times: dict[str, TimelineShot] = {}
        # per-shot context
        self.shot: Shot | None = None
        self.marks: dict[str, list[float]] = {}
        self.t0 = 0.0  # action_start of the current shot
        self.cam = [list(CAM_PULL_BACK[0]), list(CAM_PULL_BACK[1]), CAM_PULL_BACK[2]]
        self.cam_t = 0.0

    # ----------------------------------------------------------------- errors

    def err(self, code: str, t: float, detail: str, *, who: str | None = None, do: str | None = None) -> None:
        sid = self.shot.id if self.shot is not None else "?"
        self.errors.append(
            CompileError(code=code, message=f"shot {sid}, t={fmt_t(t)}: {detail}", shot=sid, t=t, who=who, do=do)
        )

    def plain_err(self, code: str, message: str) -> None:
        self.errors.append(CompileError(code=code, message=message))

    # -------------------------------------------------------------- lookups

    def is_person(self, x: str | None) -> bool:
        return x is not None and x in self.puppets

    def is_prop(self, x: str | None) -> bool:
        return x is not None and x in self.props and x in self.scene.props

    def is_mark(self, x: str | None) -> bool:
        return x is not None and x in self.marks

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

    def resolve(self, x: str, t: float) -> Vec | None:
        """Person, prop or mark -> world position at timeline time t."""
        if self.is_person(x):
            return self.tracks.sample(f"puppet:{x}", "root.position", t) or self.puppets[x].pos()
        if self.is_prop(x):
            return self.prop_world(self.props[x], t)
        if self.is_mark(x):
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

    def set_pose(self, p: _Puppet, t: float, bones: dict[str, float], offset_y: float | None = None, dur: float = POSE_DUR) -> float:
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

    # ----------------------------------------------------------- validation

    def check_params(self, a: Action) -> set[str]:
        """missing_param / unexpected_param. Returns the params that are allowed and present."""
        spec = ACTIONS[a.do]
        allowed = set(spec["required"]) | set(spec["optional"])
        present = {k for k, v in a.model_dump(by_alias=True, exclude_none=True).items() if k not in ALWAYS_ALLOWED}
        for name in sorted(present - allowed):
            self.err("unexpected_param", a.t, f'{a.do} does not take "{name}"', who=a.who, do=a.do)
        for name in spec["required"]:
            if name not in present:
                self.err("missing_param", a.t, f'{a.do} needs "{name}"', who=a.who, do=a.do)
        return present & allowed

    def check_refs(self, a: Action, params: set[str]) -> bool:
        ok = True

        def bad(code: str, detail: str) -> None:
            nonlocal ok
            ok = False
            self.err(code, a.t, detail, who=a.who, do=a.do)

        if "who" in params and not self.is_person(a.who):
            bad("unknown_person", f'unknown person "{a.who}"')
        if "to" in params:
            if a.do == "hand_to":
                if not self.is_person(a.to):
                    bad("unknown_person", f'unknown person "{a.to}"')
            elif a.do in MOVEMENT_TO and not self.is_mark(a.to):
                bad("unknown_mark", f'unknown mark "{a.to}"')
        if "from" in params and not self.is_mark(a.from_):
            bad("unknown_mark", f'unknown mark "{a.from_}"')
        if "target" in params:
            if a.do in PERSON_TARGET:
                if not self.is_person(a.target):
                    bad("unknown_person", f'unknown person "{a.target}"')
            elif self.resolve(a.target or "", 0.0) is None:
                bad("unknown_target", f'unknown target "{a.target}" (not a person, prop or mark)')
        if "what" in params and not self.is_prop(a.what):
            bad("unknown_prop", f'unknown prop "{a.what}"')
        if "on" in params and not (self.is_prop(a.on) or self.is_mark(a.on)):
            bad("unknown_mark", f'unknown mark or prop "{a.on}"')
        if "at" in params and self.resolve(a.at or "", 0.0) is None:
            bad("unknown_target", f'unknown target "{a.at}" (not a person, prop or mark)')
        if "pose" in params and a.pose not in POSES:
            bad("unknown_pose", f'unknown pose "{a.pose}"')
        if "value" in params and a.do == "time_of_day" and a.value not in TIMES_OF_DAY:
            bad("invalid_value", f'time_of_day value "{a.value}" is not one of {", ".join(TIMES_OF_DAY)}')
        return ok

    def holding_error(self, a: Action, prop: _Prop) -> None:
        holder = prop.holder() or prop.wearer()
        if holder is None:
            tail = "nobody is holding it"
        elif prop.wearer() is not None:
            tail = f"{holder} is wearing it"
        else:
            tail = f"{holder} is holding it"
        self.err("not_holding", a.t, f'{a.who} cannot {a.do} "{a.what}"; {tail}', who=a.who, do=a.do)

    # ---------------------------------------------------------------- shots

    def compile(self) -> CompileResult:
        seen: set[str] = set()
        start = 0.0
        for i, shot in enumerate(self.sp.shots):
            self.shot = shot
            if shot.id in seen:
                self.err("duplicate_shot", 0.0, f'shot id "{shot.id}" is used twice')
            seen.add(shot.id)
            start = self.run_shot(shot, start, i)
        self.shot = None
        self.check_screenplay_level()
        if self.errors:
            return CompileResult(ok=False, errors=self.errors)
        return CompileResult(ok=True, timeline=self.build_timeline(start))

    def run_shot(self, shot: Shot, start: float, index: int) -> float:
        set_def = self.scene.sets.get(shot.set)
        if set_def is None:
            self.err("unknown_set", 0.0, f'unknown set "{shot.set}"')
        self.marks = {**BUILTIN_MARKS, **(set_def.marks if set_def else {}), **shot.marks}
        self.t0 = start + RISE

        self.shot_start(shot, start)
        ends: list[float] = []
        actions = sorted(shot.actions, key=lambda a: a.t)  # stable: same-t actions keep their order
        for a in actions:
            before = len(self.errors)
            params = self.check_params(a)
            refs_ok = self.check_refs(a, params)
            if refs_ok and len(self.errors) == before:  # only simulate fully valid actions
                ends.append(self.do_action(a))
        self.check_narration(shot)

        last_action = max((max(a.t + ACTION_TAIL, 0.0) for a in actions), default=0.0)
        last_motion = max((e - self.t0 + 0.5 for e in ends), default=0.0)
        narr_ts = [ln.t for ln in shot.narration]
        for by_shot in (self.sp.narrations or {}).values():
            narr_ts += [ln.t for ln in by_shot.get(shot.id, [])]
        last_narr = max((t + NARRATION_TAIL for t in narr_ts), default=0.0)
        end = self.t0 + max(last_action, last_motion, last_narr, 2.0)

        self.camera(shot, start, end)
        self.shot_times[shot.id] = TimelineShot(
            id=shot.id, set=shot.set, start=start, action_start=self.t0, end=end,
            transition=shot.transition, page_text=shot.page_text,
        )
        is_last = index == len(self.sp.shots) - 1
        if set_def is not None:
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
        for pid, st in shot.start.items():
            if pid not in self.puppets:
                self.err("unknown_person", 0.0, f'unknown person "{pid}"', who=pid)
            if st.at not in self.marks:
                self.err("unknown_mark", 0.0, f'unknown mark "{st.at}"', who=pid)
            if st.pose is not None and st.pose not in POSES:
                self.err("unknown_pose", 0.0, f'unknown pose "{st.pose}"', who=pid)
            if st.holding is not None and not self.is_prop(st.holding):
                self.err("unknown_prop", 0.0, f'unknown prop "{st.holding}"', who=pid)
            if st.in_ is not None and not self.is_prop(st.in_.split(".")[0]):
                self.err("unknown_prop", 0.0, f'unknown prop "{st.in_.split(".")[0]}"', who=pid)
        for prop_id, ps in shot.prop_start.items():
            if not self.is_prop(prop_id):
                self.err("unknown_prop", 0.0, f'unknown prop "{prop_id}"')
            if ps.at is not None and ps.at not in self.marks:
                self.err("unknown_mark", 0.0, f'unknown mark "{ps.at}"')
            if ps.on is not None and not self.is_prop(ps.on.split(".")[0]):
                self.err("unknown_prop", 0.0, f'unknown prop "{ps.on.split(".")[0]}"')

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
                self.prop_parent(prop, start, f"prop:{ps.on}", [0.0, 0.0, 0.0])
            elif ps.at is not None and ps.at in self.marks:
                self.prop_parent(prop, start, "ground", self.mark_pos(ps.at))
            if ps.state is not None:
                self.tracks.key(prop.target, "state", start, ps.state)
        for prop in self.props.values():
            if not self.tracks.has(prop.target, "parent"):
                self.prop_parent(prop, 0.0, prop.parent, prop.pos)

    # --------------------------------------------------------------- actions

    def do_action(self, a: Action) -> float:
        """Simulate one valid-looking action; record holding errors. Returns when it ends."""
        T = self.t0 + a.t
        kind = ACTIONS[a.do]["kind"]
        if kind == "effect":
            at = self.resolve(a.at, T) if a.at else None
            self.effects.append(TimelineEffect(t=T, kind=a.do, at=_r(at), dur=a.dur, value=a.value))
            return T + (a.dur or 0.0)
        p = self.puppets.get(a.who or "")
        prop = self.props.get(a.what or "")

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
                tx = self.resolve(a.target, T)[0]
                if abs(tx - p.ground[0]) > 1e-9:
                    self.face(p, max(T, p.busy_until), 1 if tx > p.ground[0] else -1)
                return T
            case "follow":
                other = self.resolve(a.target, T)
                direction = 1 if other[0] >= p.ground[0] else -1
                return self.move(p, T, [other[0] - 0.15 * direction, other[1], other[2]], SPEEDS["walk"])
            case "pose":
                return self.set_pose(p, T, _bones(a.pose), _root_offset_y(a.pose), dur=a.dur or POSE_DUR)
            case "wave":
                return self.gesture(p, T, "wave", a.dur)
            case "point_at":
                tx = self.resolve(a.target, T)[0]
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
                self.gesture(other, T, "hug", a.dur or 1.5)
                return self.gesture(p, T, "hug", a.dur or 1.5)
            case "lean_toward":
                tx = self.resolve(a.target, T)[0]
                if abs(tx - p.ground[0]) > 1e-9:
                    self.face(p, T, 1 if tx > p.ground[0] else -1)
                before = dict(p.bones)
                end = self.set_pose(p, T, {**before, "torso": -0.2})
                return self.set_pose(p, end + (a.dur or GESTURE_DUR), before)
            case "pick_up":
                holder = prop.holder() or prop.wearer()
                if holder is not None:
                    verb = "holding" if prop.holder() else "wearing"
                    self.err("already_held", a.t, f'{a.who} cannot pick_up "{a.what}"; {holder} is {verb} it',
                             who=a.who, do=a.do)
                    return T
                grab = max(T, p.busy_until) + POSE_DUR
                self.gesture(p, max(T, p.busy_until), "reach", 0.1)
                self.prop_parent(prop, grab, f"puppet:{p.id}.hand_r", [0.0, 0.0, 0.0])
                return grab + POSE_DUR
            case "hold":
                if prop.holder() != p.id:
                    self.holding_error(a, prop)
                    return T
                return self.set_pose(p, T, {**p.bones, **_bones("hold")})
            case "drop":
                if prop.holder() != p.id:
                    self.holding_error(a, prop)
                    return T
                x = p.ground[0] + 0.08 * p.facing
                self.prop_parent(prop, T, "ground", [x, p.ground[1] + 0.2, p.ground[2]])
                landed = [x, p.ground[1], p.ground[2]]
                self.tracks.key(prop.target, "position", T + 0.3, landed)
                prop.pos = landed
                return T + 0.3
            case "hand_to":
                if prop.holder() != p.id:
                    self.holding_error(a, prop)
                    return T
                other = self.puppets[a.to]
                self.face(p, T, 1 if other.ground[0] >= p.ground[0] else -1)
                self.prop_parent(prop, T + POSE_DUR, f"puppet:{other.id}.hand_r", [0.0, 0.0, 0.0])
                return T + POSE_DUR
            case "put_on":
                holder = prop.holder() or prop.wearer()
                if holder is not None and holder != p.id:
                    self.err("already_held", a.t, f'{a.who} cannot put_on "{a.what}"; {holder} has it',
                             who=a.who, do=a.do)
                    return T
                wearer = a.target or p.id
                self.prop_parent(prop, T, f"puppet:{wearer}.head", [0.0, 0.0, 0.0])
                return T + POSE_DUR
            case "place_on":
                if prop.holder() != p.id:
                    self.holding_error(a, prop)
                    return T
                if self.is_mark(a.on):
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

    def check_narration(self, shot: Shot) -> None:
        ids = {a.id for a in shot.actions if a.id}
        verbs = {a.do for a in shot.actions}
        tellings = [shot.narration] + [by_shot.get(shot.id, []) for by_shot in (self.sp.narrations or {}).values()]
        for lines in tellings:
            for line in lines:
                if line.cue is not None and line.cue not in ids and line.cue not in verbs:
                    self.err("unknown_cue", line.t, f'narration cue "{line.cue}" matches no action in this shot')
                if line.cue_word is not None:
                    words = {w.strip(".,!?;:\"'…").lower() for w in line.text.split()}
                    if line.cue_word.strip().lower() not in words:
                        self.err("unknown_cue_word", line.t, f'cue word "{line.cue_word}" is not in "{line.text}"')

    def check_screenplay_level(self) -> None:
        shot_ids = {s.id for s in self.sp.shots}
        for author, by_shot in (self.sp.narrations or {}).items():
            for sid in by_shot:
                if sid not in shot_ids:
                    self.plain_err("unknown_shot", f'narrations.{author}: unknown shot "{sid}"')
        for inter in self.sp.interactions or []:
            if inter.shot is not None and inter.shot not in shot_ids:
                self.plain_err("unknown_shot", f'interaction {inter.id}: unknown shot "{inter.shot}"')
        for ex in self.sp.explanations or []:
            kind, _, ident = ex.target.partition(":")
            if not ((kind == "puppet" and ident in self.puppets) or (kind == "prop" and ident in self.props)):
                self.plain_err("unknown_target", f'explanation: unknown target "{ex.target}"')

    def default_author(self) -> str | None:
        for shot in self.sp.shots:
            for line in shot.narration:
                m = re.match(r"telling:([^:]+):", line.source or "")
                if m:
                    return m.group(1)
        others = set(self.sp.narrations or {})
        for pid in self.cast:
            if pid not in others:
                return pid
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

    def camera(self, shot: Shot, start: float, end: float) -> None:
        self.cam_key(start, CAM_PULL_BACK, 0.0)
        self.cam_key(self.t0, CAM_WIDE, 0.0)
        for cue in sorted(shot.camera, key=lambda c: c.t):
            spec = CAMERA_MOVES[cue.move]
            T = self.t0 + cue.t
            if "target" in spec["required"] and cue.target is None:
                self.err("missing_param", cue.t, f'camera {cue.move} needs "target"')
                continue
            tgt = self.resolve(cue.target, T) if cue.target is not None else None
            if cue.target is not None and tgt is None:
                self.err("unknown_target", cue.t, f'camera target "{cue.target}" is not a person, prop or mark')
                continue
            match cue.move:
                case "wide":
                    pose = CAM_WIDE
                case "pull_back":
                    pose = CAM_PULL_BACK
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
        default = next((pid for pid in self.cast if pid in narrations), next(iter(narrations), None))
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
