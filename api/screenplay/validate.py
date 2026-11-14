"""Static screenplay validation (ARCHITECTURE.md section 3, compiler step 1 'Validate').

A pure pass over the screenplay and its scene inputs: every verb, parameter, mark, prop,
person, pose, socket, state, time, camera cue, narration cue, telling, explanation and
interaction is checked against ``shared/vocabulary.json``, ``shared/rig/poses.json`` and the
scene's sets, props and people. No world is simulated here (who holds what, who sits where:
that is state.py). Every problem is reported, in a fixed order, as an exact CompileError:

1. screenplay level: cast, props, names used as both, duplicate tellers, tellings for
   unknown shots, explanations, interactions;
2. then each shot in order: duplicate id, set, marks, start, propStart, camera cues, actions
   (in list order), narration (the default telling's line checks, then each other telling,
   then the default telling's overlap, checked once per shot).

Message grammar (shared with the compiler's other steps)::

    shot s1, t=5: dad cannot hand_to "hymn_books"; <reason>     action or camera cue
    shot s1, t=5: cannot rain; <reason>                           no `who`
    shot s1: <reason>                                             shot-level
    screenplay: <reason>                                          screenplay-level

Narration problems keep narration.py's own location format
(``shot s1, dad's narration line 1: ...``).

Reasons follow one style (errors.ERROR_CODES lists every code): lower case, no trailing
period, lists as ``the <things> are a, b, c``, and the fix after a ``; `` where it is obvious.
Three lists still read the older way because tests outside the compiler-error task pin them:
``poses are a, b`` (unknown_pose) and ``known sets: a, b`` / ``known people: a, b`` /
``known props: a, b``. They move to ``the poses are a, b`` (and so on) together with
verbs_core's and compiler.py's unknown_pose wording, in one change.
Here the name the screenplay wrote that is wrong is quoted (``"the_moon" is not a mark``); a
name of the wrong kind says what it is instead (``"to" must be a mark, and "mom" is a person``,
then the list to pick from); offending numbers are shown as written (``:g``), not rounded. A
written t that is not finite stays in the message head (``t=inf``) but not in CompileError.t
(errors.error_t), so the errors are JSON-safe.

Each problem has the code that names it, so the director's fix follows from the code alone:
bad_exit (enter from a mark on stage), bad_target (a person-only field given a prop or mark;
place_on onto a person), bad_mark (a malformed shot mark, or one whose h or d is out of range),
unknown_state (open or close on a prop without that state), bad_time / bad_dur (a t or dur over
MAX_SECONDS). Two keep an older code because another stream's test pins it: exit to a mark is
unknown_mark, and set_state to a state the prop lacks is invalid_value.

When a shot's set is unknown its marks are unknown too, so mark checks for that shot are
skipped (one unknown_set error instead of a cascade of unknown_mark errors).
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

import schemas
from screenplay import narration
from screenplay.errors import error_message, error_t, from_narration_error
from screenplay.narration import NarrationError
from screenplay.schema import (
    SHARED_DIR,
    VOCABULARY,
    Action,
    CameraCue,
    CompileError,
    Interaction,
    NarrationLine,
    PropStart,
    Screenplay,
    Shot,
    StartState,
)
from screenplay.state import self_target_reason

VERBS: dict[str, dict] = VOCABULARY["actions"]
CAMERA: dict[str, dict] = VOCABULARY["camera"]
POSES: dict[str, dict] = json.loads((SHARED_DIR / "rig" / "poses.json").read_text())["poses"]
BUILTIN_MARKS: dict[str, list[float]] = VOCABULARY["builtinMarks"]
TIMES_OF_DAY: list[str] = VOCABULARY["timesOfDay"]

OFFSTAGE = ("offstage_left", "offstage_right")
TOO_BIG = ("furniture", "vehicle", "building")
CARRY_VERBS = frozenset({"pick_up", "hold", "hand_to", "drop", "put_on"})
VEHICLE_VERBS = frozenset({"get_in", "get_out", "drive_to", "ride_to"})
MARK_TO_VERBS = frozenset({"walk_to", "run_to", "enter", "drive_to", "ride_to"})
MIXED_TARGET_VERBS = frozenset({"turn_to", "point_at", "lean_toward", "wave"})
PERSON_TARGET_VERBS = frozenset({"follow", "hug", "put_on"})
SELF_TARGET_VERBS = frozenset({"follow", "hug"})  # put_on may target oneself (the default)
SEAT_SOCKET_VERBS = frozenset({"sit_on", "get_in"})
STATE_VERBS = {"open": "open", "close": "closed"}

# The latest an action, camera cue or narration line may start, and the longest dur, in seconds.
# A story's shot plays for well under a minute; anything near this is a typo (an extra zero, ms
# for s), and a huge dur would make the compiler build keys or rain for hours.
MAX_SECONDS = 600
# A shot mark's h (the standing surface's height) and d (its depth) must lie in these ranges;
# u is free, since offstage marks lie past the stage edge.
MARK_H_RANGE = (0.0, 1.5)
MARK_D_RANGE = (0.0, 1.0)

# Action parameters in Action field order: (python attribute, JSON name).
_PARAMS: list[tuple[str, str]] = [
    (name, field.alias or name) for name, field in Action.model_fields.items() if name not in ("t", "do", "id")
]


def action_params(action: Action) -> dict[str, object]:
    """The parameters an action sets: every non-None field except t, do and id, keyed by
    JSON name ('from', not 'from_'), in Action field order."""
    return {key: getattr(action, name) for name, key in _PARAMS if getattr(action, name) is not None}


def _names(items: Iterable[str]) -> str:
    return ", ".join(items)


def _takes(name: str, spec: dict) -> str:
    params = [*spec["required"], *spec["optional"]]
    return f"{name} takes {_names(params)}" if params else f"{name} takes no parameters"


def _finite(x: float) -> bool:
    return isinstance(x, (int, float)) and math.isfinite(x)


def _numbers(values: Iterable[float]) -> str:
    """A list of numbers as written: ``[0.5, nan]``."""
    return "[" + ", ".join(f"{v:g}" for v in values) + "]"


def _over_max_reason(name: str, value: float) -> str:
    """``t must be at most 600 s, not 1e+07`` (the value as written, ``:g``)."""
    return f"{name} must be at most {MAX_SECONDS:g} s, not {value:g}"


@dataclass(frozen=True)
class _Site:
    """Where an error is reported: the CompileError location fields. The message head follows
    from them (errors.error_message): ``screenplay`` without a shot, ``shot <id>`` without a
    t, else ``shot <id>, t=<t>``."""

    shot: str | None = None
    t: float | None = None
    who: str | None = None
    do: str | None = None
    what: str | None = None

    def error(self, code: str, reason: str) -> CompileError:
        return CompileError(
            code=code,
            message=error_message(self.shot, self.t, self.who, self.do, self.what, reason),
            shot=self.shot,
            t=error_t(self.t),  # None when not finite: JSON-safe (the message still says t=inf)
            who=self.who,
            do=self.do,
        )


_from_narration = from_narration_error


# --------------------------------------------------------------------------- validator

class _Validator:
    def __init__(
        self,
        screenplay: Screenplay,
        sets: Mapping[str, schemas.SetDef],
        props: Mapping[str, schemas.Prop],
        people: Mapping[str, schemas.Person],
    ) -> None:
        self.sp = screenplay
        self.sets = sets
        self.props = props
        self.people = people
        self.cast = list(screenplay.cast)
        self.cast_set = set(self.cast)
        self.sp_props = list(screenplay.props)
        self.sp_props_set = set(self.sp_props)
        self.shot_ids = [s.id for s in screenplay.shots]
        self.errors: list[CompileError] = []
        try:
            self.default_author: str | None = narration.default_author(screenplay)
        except NarrationError:
            self.default_author = None

    # ------------------------------------------------------------------ helpers

    def add(self, site: _Site, code: str, reason: str) -> None:
        self.errors.append(site.error(code, reason))

    def cast_list(self) -> str:
        return f"the cast is {_names(self.cast)}" if self.cast else "the cast is empty"

    def props_list(self) -> str:
        return f"the props are {_names(self.sp_props)}" if self.sp_props else "the screenplay lists no props"

    def shots_list(self) -> str:
        return f"the shots are {_names(self.shot_ids)}" if self.shot_ids else "the screenplay has no shots"

    @staticmethod
    def marks_list(marks: Mapping[str, object]) -> str:
        return f"the marks are {_names(marks)}"

    def prop_def(self, prop_id: str) -> schemas.Prop | None:
        """The definition of a prop the screenplay lists (None if unlisted or unknown)."""
        return self.props.get(prop_id) if prop_id in self.sp_props_set else None

    @staticmethod
    def sockets_list(d: schemas.Prop) -> str:
        return f"its sockets are {_names(d.sockets)}" if d.sockets else "it has no sockets"

    @staticmethod
    def states_list(d: schemas.Prop) -> str:
        return f"its states are {_names(d.states)}"

    def check_time(self, site: _Site, name: str, value: float | None, *, positive: bool) -> None:
        """bad_time for a t (``positive=False``) or dur (``positive=True``) that is not finite, or
        negative (a dur: not > 0); then, over MAX_SECONDS, bad_time for a t and bad_dur for a
        dur."""
        if value is None:
            return
        if positive:
            if not _finite(value) or value <= 0:
                self.add(site, "bad_time", f"{name} must be a finite number > 0, not {value:g}")
                return
        elif not _finite(value) or value < 0:
            self.add(site, "bad_time", f"{name} must be a finite number >= 0, not {value:g}")
            return
        if value > MAX_SECONDS:
            reason = _over_max_reason(name, value)
            if positive:
                self.add(site, "bad_dur", reason)
            else:
                self.add(site, "bad_time", reason)

    def check_params(self, site: _Site, name: str, spec: dict, given: Mapping[str, object]) -> set[str]:
        """missing_param / unexpected_param. Returns the given params the verb accepts."""
        allowed = [*spec["required"], *spec["optional"]]
        for p in spec["required"]:
            if p not in given:
                self.add(site, "missing_param", f'{name} requires {_names(spec["required"])}; "{p}" is missing')
        for p in given:
            if p not in allowed:
                self.add(site, "unexpected_param", f'{_takes(name, spec)}; "{p}" is not one of them')
        return {p for p in given if p in allowed}

    def kind_of(self, name: str, marks: Mapping[str, object] | None) -> str | None:
        """``a person``, ``a prop`` or ``a mark`` when ``name`` is one in this screenplay and shot."""
        if name in self.cast_set:
            return "a person"
        if name in self.sp_props_set:
            return "a prop"
        if marks is not None and name in marks:
            return "a mark"
        return None

    def check_mark(self, site: _Site, marks: Mapping[str, object] | None, name: str, lead: str = "") -> None:
        """unknown_mark unless `name` is a mark of the shot (skipped when the marks are unknown).
        `lead` names the field, e.g. 'dad starts at '."""
        if marks is None or name in marks:
            return
        what = f'{lead}"{name}", which' if lead else f'"{name}"'
        self.add(site, "unknown_mark", f"{what} is not a mark in this shot; {self.marks_list(marks)}")

    def check_offstage(self, site: _Site, param: str, value: str) -> None:
        """enter's "from" (bad_exit) or exit's "to" must be offstage_left or offstage_right.

        exit's "to" keeps the code unknown_mark: tests/agent/test_agent_skills.py (another
        stream's) pins it as the code the director sees, and the directing skill's error table
        lists it there. errors.HINT_VARIANTS gives it the exit hint all the same. Move it to
        bad_exit together with that test and the skill's table."""
        if value in OFFSTAGE:
            return
        reason = f'"{param}" must be offstage_left or offstage_right, not "{value}"'
        if param == "from":
            self.add(site, "bad_exit", reason)
        else:
            self.add(site, "unknown_mark", reason)

    def check_prop_listed(self, site: _Site, prop_id: str, lead: str = "") -> bool:
        """unknown_prop unless the screenplay lists the prop. `lead` names the field."""
        if prop_id in self.sp_props_set:
            return True
        what = f'{lead}"{prop_id}", which' if lead else f'"{prop_id}"'
        self.add(site, "unknown_prop", f"{what} is not in the screenplay's props; {self.props_list()}")
        return False

    def check_person(self, site: _Site, param: str, value: str, marks: Mapping[str, object] | None) -> bool:
        """A parameter that must name a cast member (hand_to.to, hug/follow/put_on target)."""
        if value in self.cast_set:
            return True
        if value in self.sp_props_set:
            self.add(site, "bad_target", f'"{param}" must be a person in the cast, and "{value}" is a prop')
        elif marks is not None and value in marks:
            self.add(site, "bad_target", f'"{param}" must be a person in the cast, and "{value}" is a mark')
        else:
            self.add(site, "unknown_person", f'"{value}" is not in the cast; {self.cast_list()}')
        return False

    def check_any_target(self, site: _Site, value: str, marks: Mapping[str, object] | None) -> None:
        """A parameter that may name a person, a prop or a mark (turn_to target, effect at, camera)."""
        if value in self.cast_set or value in self.sp_props_set or (marks is not None and value in marks):
            return
        if value in self.people:
            self.add(site, "unknown_person", f'"{value}" is not in the cast; {self.cast_list()}')
        elif value in self.props:
            self.add(site, "unknown_prop", f"\"{value}\" is not in the screenplay's props; {self.props_list()}")
        elif marks is not None:
            self.add(
                site,
                "unknown_target",
                f'"{value}" is not a person, prop or mark in this shot; {self.cast_list()}; '
                f"{self.props_list()}; {self.marks_list(marks)}",
            )

    def check_socket_ref(self, site: _Site, ref: str, lead: str, *, socket_optional: bool) -> None:
        """'<prop>.<socket>' (start.in, propStart.on). A bare '<prop>' is allowed when
        `socket_optional` and the prop has sockets (state.py picks a free one)."""
        base, dot, sock = ref.partition(".")
        if base not in self.sp_props_set:
            self.add(
                site, "unknown_prop",
                f"{lead}\"{ref}\", but \"{base}\" is not in the screenplay's props; {self.props_list()}",
            )
            return
        d = self.props.get(base)
        if d is None:
            return
        if not dot or not sock:
            if socket_optional and d.sockets:
                return
            reason = f'{lead}"{ref}", which is not "<prop>.<socket>"; {base}: {self.sockets_list(d)}'
            self.add(site, "invalid_value", reason)
            return
        if sock not in d.sockets:
            reason = f'{lead}"{ref}", but "{base}" has no socket "{sock}"; {self.sockets_list(d)}'
            self.add(site, "invalid_value", reason)

    # --------------------------------------------------------------- screenplay

    def screenplay_level(self) -> None:
        site = _Site()
        for pid in self.cast:
            if pid not in self.people:
                known = _names(self.people) or "none"
                self.add(site, "unknown_person", f'cast member "{pid}" is not a known person; known people: {known}')
        for prop_id in self.sp_props:
            if prop_id not in self.props:
                known = _names(self.props) or "none"
                self.add(site, "unknown_prop", f'prop "{prop_id}" is not a known prop; known props: {known}')
        for pid in self.cast:
            if pid in self.sp_props_set:
                self.add(site, "ambiguous_name", f'"{pid}" is both a cast member and a prop; rename one')
        self.tellings()
        self.explanations()
        for i, it in enumerate(self.sp.interactions or []):
            self.interaction(i, it)

    def tellings(self) -> None:
        site = _Site()
        for author, by_shot in (self.sp.narrations or {}).items():
            if author == self.default_author:
                self.add(
                    site,
                    "duplicate_telling",
                    f"{author} tells the default narration (Shot.narration) and also has a telling in "
                    f"Screenplay.narrations; give each teller one telling",
                )
            for shot_id in by_shot:
                if shot_id not in self.shot_ids:
                    self.add(
                        site,
                        "telling_unknown_shot",
                        f'{author}\'s telling has lines for shot "{shot_id}", which is not in the screenplay; '
                        f"{self.shots_list()}",
                    )

    def explanations(self) -> None:
        site = _Site()
        for i, ex in enumerate(self.sp.explanations or []):
            kind, sep, ident = ex.target.partition(":")
            if not sep or kind not in ("puppet", "prop") or not ident:
                self.add(
                    site,
                    "explanation_bad_target",
                    f'explanation {i} targets "{ex.target}"; a target is "puppet:<personId>" or "prop:<propId>"',
                )
            elif kind == "puppet" and ident not in self.cast_set:
                self.add(
                    site,
                    "explanation_unknown_target",
                    f'explanation {i} targets "{ex.target}", but "{ident}" is not in the cast; {self.cast_list()}',
                )
            elif kind == "prop" and ident not in self.sp_props_set:
                self.add(
                    site,
                    "explanation_unknown_target",
                    f"explanation {i} targets \"{ex.target}\", but \"{ident}\" is not in the screenplay's props; "
                    f"{self.props_list()}",
                )

    def interaction(self, i: int, it: Interaction) -> None:
        site = _Site()
        label = f'interaction "{it.id}"'
        shot: Shot | None = None
        if it.shot is not None:
            if it.shot not in self.shot_ids:
                self.add(
                    site,
                    "interaction_unknown_shot",
                    f'{label} belongs to shot "{it.shot}", which is not in the screenplay; '
                    f"{self.shots_list()}",
                )
            else:
                shot = self.sp.shots[self.shot_ids.index(it.shot)]
        if len(it.at) != 2 or not all(_finite(v) for v in it.at):
            self.add(
                site, "invalid_value",
                f"{label} is at {_numbers(it.at)}, but at is [u, v]: two finite fractions of the page",
            )
        d = it.drives
        if d.type == "scrub":
            for name, value in (("from", d.from_), ("to", d.to)):
                if value is not None and (not _finite(value) or value < 0):
                    self.add(site, "bad_time", f'{label}: scrub "{name}" must be a finite number >= 0, not {value:g}')
            if d.from_ is not None and d.to is not None and _finite(d.from_) and _finite(d.to) and d.from_ >= d.to:
                self.add(site, "bad_time", f'{label}: scrub "from" ({d.from_:g}) must be before "to" ({d.to:g})')
            for target in d.targets or []:
                self.scrub_target(site, label, target, shot)
            return
        if d.prop is None:
            self.add(site, "missing_param", f'{label} drives {d.type}, which requires prop; "prop" is missing')
            return
        if d.prop not in self.sp_props_set:
            self.add(
                site,
                "interaction_unknown_prop",
                f"{label} drives \"{d.prop}\", which is not in the screenplay's props; {self.props_list()}",
            )
            return
        pd = self.props.get(d.prop)
        if d.type == "prop_state":
            for state in d.states or []:
                if pd is not None and pd.states and state not in pd.states:
                    self.add(
                        site, "invalid_value",
                        f'{label} steps "{d.prop}" to state "{state}", which it does not have; '
                        f"{self.states_list(pd)}",
                    )
        elif d.type == "prop_part":
            if d.range is not None and (len(d.range) != 2 or not all(_finite(v) for v in d.range)):
                self.add(
                    site, "invalid_value",
                    f"{label} gives range {_numbers(d.range)}, but a range is [min, max]: two finite numbers",
                )
            if d.part is None:
                reason = f'{label} drives prop_part, which requires prop, part; "part" is missing'
                self.add(site, "missing_param", reason)
            elif pd is not None and d.part not in pd.parts:
                parts = f"its parts are {_names(pd.parts)}" if pd.parts else "it has no moving parts"
                self.add(
                    site, "interaction_unknown_part", f'{label} drives part "{d.part}" of "{d.prop}"; {parts}'
                )

    def scrub_target(self, site: _Site, label: str, target: str, shot: Shot | None) -> None:
        lead = f'{label} scrubs "{target}"'
        if target == "camera":
            return
        kind, sep, ident = target.partition(":")
        if sep and kind == "puppet" and ident:
            if ident not in self.cast_set:
                reason = f'{lead}, but "{ident}" is not in the cast; {self.cast_list()}'
                self.add(site, "interaction_bad_target", reason)
            return
        if sep and kind == "prop" and ident:
            if ident not in self.sp_props_set:
                self.add(
                    site, "interaction_bad_target",
                    f"{lead}, but \"{ident}\" is not in the screenplay's props; {self.props_list()}",
                )
            return
        set_id, dot, layer = ident.rpartition(".")
        n_text = layer[5:]
        if sep and kind == "set" and dot and set_id and layer.startswith("layer") and n_text.isascii() and n_text.isdigit():
            n = int(n_text)
            allowed = [shot.set] if shot is not None else list(dict.fromkeys(s.set for s in self.sp.shots))
            if set_id not in allowed:
                where = f"shot {shot.id}'s set is {shot.set}" if shot is not None else f"the sets are {_names(allowed)}"
                self.add(site, "interaction_bad_target", f"{lead}, but {where}")
                return
            sd = self.sets.get(set_id)
            if sd is not None and n >= len(sd.layers):
                self.add(
                    site, "interaction_bad_target",
                    f"{lead}, but {set_id} has {len(sd.layers)} layers (layer0 to layer{len(sd.layers) - 1})",
                )
            return
        self.add(
            site,
            "interaction_bad_target",
            f'{lead}; a scrub target is "puppet:<personId>", "prop:<propId>", "set:<setId>.layer<n>" or "camera"',
        )

    # -------------------------------------------------------------------- shots

    def shot(self, index: int, shot: Shot) -> None:
        site = _Site(shot=shot.id)
        if shot.id in self.shot_ids[:index]:
            self.add(site, "duplicate_shot", f'another shot already has id "{shot.id}"; shot ids must be unique')

        marks: dict[str, object] | None
        set_def = self.sets.get(shot.set)
        if set_def is None:
            known = _names(self.sets) or "none"
            self.add(site, "unknown_set", f'set "{shot.set}" is not a known set; known sets: {known}')
            marks = None
        else:
            marks = {**BUILTIN_MARKS, **set_def.marks, **shot.marks}
        self.shot_marks(site, shot, set_def)

        for who, s in shot.start.items():
            self.start(shot, who, s, marks)
        for what, ps in shot.prop_start.items():
            self.prop_start(site, what, ps, marks)
        for cue in shot.camera:
            self.camera(shot, cue, marks)
        seen_ids: set[str] = set()
        for action in shot.actions:
            self.action(shot, action, marks, seen_ids)
        self.narration(shot)

    def shot_marks(self, site: _Site, shot: Shot, set_def: schemas.SetDef | None) -> None:
        """bad_mark for a shot mark that is not [u, h] or [u, h, d] (numbers), or whose h or d
        is out of range (MARK_H_RANGE, MARK_D_RANGE); then ambiguous_name for any mark of the
        shot named like a cast member or a prop. The set's own marks are not range-checked."""
        for name, value in shot.marks.items():
            if len(value) not in (2, 3) or not all(_finite(v) for v in value):
                reason = f'mark "{name}" is {value}, but a mark is [u, h] or [u, h, d] (numbers)'
                self.add(site, "bad_mark", reason)
                continue
            lo, hi = MARK_H_RANGE
            if not lo <= value[1] <= hi:
                reason = (f'mark "{name}" has h {value[1]:g}, but h is the standing surface\'s height above '
                          f"the ground, from {lo:g} to {hi:g}")
                self.add(site, "bad_mark", reason)
            lo, hi = MARK_D_RANGE
            if len(value) == 3 and not lo <= value[2] <= hi:
                reason = f'mark "{name}" has d {value[2]:g}, but d is a depth from {lo:g} (back of the set) to {hi:g}'
                self.add(site, "bad_mark", reason)
        names = {**BUILTIN_MARKS, **(set_def.marks if set_def else {}), **shot.marks}
        for name in names:
            if name in self.cast_set:
                self.add(site, "ambiguous_name", f'mark "{name}" has the same name as a cast member; rename the mark')
            elif name in self.sp_props_set:
                self.add(site, "ambiguous_name", f'mark "{name}" has the same name as a prop; rename the mark')

    def start(self, shot: Shot, who: str, s: StartState, marks: Mapping[str, object] | None) -> None:
        site = _Site(shot=shot.id, who=who)
        if who not in self.cast_set:
            self.add(site, "unknown_person", f'start names "{who}", who is not in the cast; {self.cast_list()}')
        self.check_mark(site, marks, s.at, f"{who} starts at ")
        if s.holding is not None and self.check_prop_listed(site, s.holding, lead=f"{who} starts holding "):
            d = self.props.get(s.holding)
            if d is not None and d.size in TOO_BIG:
                reason = f'{who} starts holding "{s.holding}", which is {d.size}-sized and too big to carry'
                self.add(site, "too_big", reason)
        if s.pose is not None and s.pose not in POSES:
            reason = f'{who} starts in pose "{s.pose}", which is not a pose; poses are {_names(POSES)}'
            self.add(site, "unknown_pose", reason)
        if s.in_ is not None:
            self.check_socket_ref(site, s.in_, lead=f"{who} starts in ", socket_optional=False)

    def prop_start(self, site: _Site, what: str, ps: PropStart, marks: Mapping[str, object] | None) -> None:
        listed = self.check_prop_listed(site, what, lead="propStart names ")
        if ps.at is not None and ps.on is not None:
            self.add(site, "invalid_value", f'propStart gives "{what}" both at and on; give one')
        if ps.at is not None:
            self.check_mark(site, marks, ps.at, f'"{what}" starts at ')
        if ps.on is not None:
            self.check_socket_ref(site, ps.on, lead=f'"{what}" starts on ', socket_optional=True)
        d = self.props.get(what) if listed else None
        if ps.state is not None and d is not None and d.states and ps.state not in d.states:
            reason = f'"{what}" starts in state "{ps.state}", which it does not have; {self.states_list(d)}'
            self.add(site, "invalid_value", reason)

    def camera(self, shot: Shot, cue: CameraCue, marks: Mapping[str, object] | None) -> None:
        site = _Site(shot=shot.id, t=cue.t, do=cue.move, what=cue.target)
        self.check_time(site, "t", cue.t, positive=False)
        given = {k: v for k, v in (("target", cue.target), ("dur", cue.dur)) if v is not None}
        spec = CAMERA.get(cue.move)
        if spec is None:
            self.add(site, "unknown_move", f'"{cue.move}" is not a camera move; moves are {_names(CAMERA)}')
            return
        ok = self.check_params(site, cue.move, spec, given)
        if "target" in ok:
            self.check_any_target(site, cue.target, marks)  # type: ignore[arg-type]
        self.check_time(site, "dur", cue.dur, positive=True)

    # ------------------------------------------------------------------ actions

    def action(self, shot: Shot, a: Action, marks: Mapping[str, object] | None, seen_ids: set[str]) -> None:
        do = a.do
        site = _Site(shot=shot.id, t=a.t, who=a.who, do=do, what=a.what)
        if a.id is not None:
            if a.id in seen_ids:
                self.add(site, "duplicate_action_id", f'another action in this shot already has id "{a.id}"')
            seen_ids.add(a.id)
        self.check_time(site, "t", a.t, positive=False)
        spec = VERBS.get(do)
        if spec is None:
            self.add(site, "unknown_verb", f'"{do}" is not a verb; verbs are {_names(VERBS)}')
            return
        ok = self.check_params(site, do, spec, action_params(a))

        if "who" in ok and a.who not in self.cast_set:
            self.add(site, "unknown_person", f'"{a.who}" is not in the cast; {self.cast_list()}')
        if "to" in ok:
            self.action_to(site, a, marks)
        if "from" in ok:
            self.check_offstage(site, "from", a.from_)  # type: ignore[arg-type]
        if "what" in ok:
            self.action_what(site, a, marks)
        if "target" in ok:
            self.action_target(site, a, marks)
        on_def: schemas.Prop | None = None
        on_is_mark = False
        if "on" in ok:
            on_def, on_is_mark = self.action_on(site, a, marks)
        if "socket" in ok:
            self.action_socket(site, a, on_def, on_is_mark)
        if "pose" in ok and a.pose not in POSES:
            self.add(site, "unknown_pose", f'"{a.pose}" is not a pose; poses are {_names(POSES)}')
        if "state" in ok:
            d = self.prop_def(a.what) if a.what is not None else None
            if d is not None and d.states and a.state not in d.states:
                # invalid_value, not unknown_state (open / close's code): tests/agent/
                # test_agent_skills.py (another stream's) pins it as the code the director sees.
                self.add(site, "invalid_value", f'"{a.what}" has no state "{a.state}"; {self.states_list(d)}')
        if "at" in ok:
            self.check_any_target(site, a.at, marks)  # type: ignore[arg-type]
        if "value" in ok and do == "time_of_day" and a.value not in TIMES_OF_DAY:
            self.add(site, "invalid_value", f'"{a.value}" is not a time of day; use one of {_names(TIMES_OF_DAY)}')
        self.check_time(site, "dur", a.dur, positive=True)

    def action_to(self, site: _Site, a: Action, marks: Mapping[str, object] | None) -> None:
        to = a.to
        assert to is not None
        if a.do in MARK_TO_VERBS:
            kind = self.kind_of(to, marks)
            if marks is not None and to not in marks and kind is not None:
                self.add(site, "unknown_mark", f'"to" must be a mark, and "{to}" is {kind}; {self.marks_list(marks)}')
            else:
                self.check_mark(site, marks, to)
        elif a.do == "exit":
            self.check_offstage(site, "to", to)
        elif a.do == "hand_to" and self.check_person(site, "to", to, marks) and to == a.who:
            self.add(site, "self_target", self_target_reason("to", to))

    def action_what(self, site: _Site, a: Action, marks: Mapping[str, object] | None) -> None:
        what = a.what
        assert what is not None
        kind = self.kind_of(what, marks)
        if what not in self.sp_props_set and kind is not None:
            self.add(site, "unknown_prop", f'"what" must be a prop, and "{what}" is {kind}; {self.props_list()}')
            return
        if not self.check_prop_listed(site, what):
            return
        d = self.props.get(what)
        if d is None:
            return  # reported once at screenplay level
        if a.do in CARRY_VERBS and d.size in TOO_BIG:
            self.add(site, "too_big", f'"{what}" is {d.size}-sized and too big to carry')
        if a.do in VEHICLE_VERBS and d.size != "vehicle":
            self.add(site, "not_a_vehicle", f'"{what}" is {d.size}-sized, not a vehicle')
        if a.do == "sit_on" and not d.sockets:
            self.add(site, "no_seat", f'"{what}" has no sockets to sit on')
        if a.do in STATE_VERBS and d.states and STATE_VERBS[a.do] not in d.states:
            self.add(site, "unknown_state", f'"{what}" has no state "{STATE_VERBS[a.do]}"; {self.states_list(d)}')

    def action_target(self, site: _Site, a: Action, marks: Mapping[str, object] | None) -> None:
        target = a.target
        assert target is not None
        if a.do in PERSON_TARGET_VERBS:
            if self.check_person(site, "target", target, marks) and a.do in SELF_TARGET_VERBS and target == a.who:
                self.add(site, "self_target", self_target_reason("target", target))
        else:  # MIXED_TARGET_VERBS
            self.check_any_target(site, target, marks)

    def action_on(
        self, site: _Site, a: Action, marks: Mapping[str, object] | None
    ) -> tuple[schemas.Prop | None, bool]:
        """place_on `on`: a listed prop or a mark. Returns (the prop's definition, is a mark)."""
        on = a.on
        assert on is not None
        if on in self.sp_props_set:
            if on == a.what:  # one error: whether it has sockets does not matter
                self.add(site, "self_target", f'"on" is {on} itself')
                return None, False
            d = self.props.get(on)
            if d is not None and not d.sockets:
                self.add(site, "no_socket", f'"{on}" has no sockets to place on')
            return d, False
        if marks is None:
            return None, False
        if on in marks:
            return None, True
        if on in self.cast_set:
            self.add(
                site,
                "bad_target",
                f'"on" must be a prop or a mark, and "{on}" is a person; {self.props_list()}; '
                f"{self.marks_list(marks)}",
            )
            return None, False
        self.add(
            site,
            "unknown_mark",
            f'"{on}" is not a prop in the screenplay or a mark in this shot; {self.props_list()}; '
            f"{self.marks_list(marks)}",
        )
        return None, False

    def action_socket(self, site: _Site, a: Action, on_def: schemas.Prop | None, on_is_mark: bool) -> None:
        sock = a.socket
        assert sock is not None
        if a.do in SEAT_SOCKET_VERBS:
            d = self.prop_def(a.what) if a.what is not None else None
            base = a.what
        else:  # place_on
            if on_is_mark:
                self.add(site, "invalid_value", f'socket only applies when placing on a prop, and "{a.on}" is a mark')
                return
            d, base = on_def, a.on
        if d is not None and d.sockets and sock not in d.sockets:
            self.add(site, "invalid_value", f'"{base}" has no socket "{sock}"; {self.sockets_list(d)}')

    # ---------------------------------------------------------------- narration

    def narration(self, shot: Shot) -> None:
        """Every telling of one shot, timed statically (action start 0, which only shifts times).

        Each other teller is built on its own call with ``check_default=False`` so its own
        problems are reported in ``narrations`` order; the default telling's overlap is then
        checked once per shot (``other={}``), after the tellers, as narration.py documents.
        """
        default = self.default_author
        retimed: list[float] | None = None
        if self.lines(shot, default, shot.narration):
            try:
                retimed = narration.retime_actions(shot, 0.0, author=default)
            except NarrationError as e:
                self.errors.append(_from_narration(e))
        for author, by_shot in (self.sp.narrations or {}).items():
            lines = by_shot.get(shot.id)
            if lines is None or author == default:
                continue  # a duplicate teller is reported once at screenplay level
            if self.lines(shot, author, lines) and retimed is not None:
                try:
                    narration.build_shot_narration(
                        shot, 0.0, retimed, {author: lines}, default or "", check_default=False
                    )
                except NarrationError as e:
                    self.errors.append(_from_narration(e))
        if shot.narration and retimed is not None:
            try:
                narration.build_shot_narration(shot, 0.0, retimed, {}, default or "", check_default=True)
            except NarrationError as e:
                self.errors.append(_from_narration(e))

    def lines(self, shot: Shot, author: str | None, lines: list[NarrationLine]) -> bool:
        """Per-line checks for one telling of one shot. True if every line passed."""
        ok = True
        for li, line in enumerate(lines):
            if not _finite(line.t) or line.t < 0 or line.t > MAX_SECONDS:
                ok = False
                reason = (_over_max_reason("t", line.t) if _finite(line.t) and line.t > MAX_SECONDS
                          else f"t must be a finite number >= 0, not {line.t:g}")
                self.errors.append(_from_narration(NarrationError(
                    "bad_time", reason, author=author, shot=shot.id, line_index=li,
                )))
            if line.cue is None:
                continue
            try:
                narration.cue_action_index(shot, line.cue, author=author, line_index=li)
            except NarrationError as e:
                ok = False
                self.errors.append(_from_narration(e))
            try:
                narration.cue_word_time(
                    line, narration.line_words(line, 0.0), author=author, shot=shot.id, line_index=li
                )
            except NarrationError as e:
                ok = False
                self.errors.append(_from_narration(e))
        return ok


def validate(
    screenplay: Screenplay,
    sets: Mapping[str, schemas.SetDef],
    props: Mapping[str, schemas.Prop],
    people: Mapping[str, schemas.Person],
) -> list[CompileError]:
    """Every static problem in the screenplay, in a deterministic order ([] when it is valid).

    `sets`, `props` and `people` map ids to the scene's definitions. Pure: nothing is mutated.
    """
    v = _Validator(screenplay, sets, props, people)
    v.screenplay_level()
    for i, shot in enumerate(screenplay.shots):
        v.shot(i, shot)
    return v.errors
