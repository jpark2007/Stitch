"""CompileError construction: one exact, machine-readable message per problem.

The director agent reads these messages and fixes its draft, so the wording is stable:

    shot s1, t=5: dad cannot hand_to "hymn_books"; mom is holding it
    shot s1: steps_top is not a mark in this shot; the marks are offstage_left, offstage_right, curb
    screenplay: set "church_steps" is not a known set; known sets: church_steps_1988

One grammar: ``<head>: <who> cannot <do> "<what>"; <subject> <fact>; <fix>``. Lists read
``the <things> are a, b`` (or ``its sockets are a, b``), times go through ``state.fmt_t``
(``t=2.5``), and an action aimed at its own actor says ``"<field>" is <who> themselves``.

For an action error, ``CompileError.t`` is the action's ``t`` as written in the screenplay (not
the cue-retimed time), and ``who`` / ``do`` are filled in.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from screenplay.ik import OutOfReach
from screenplay.narration import NarrationError
from screenplay.schema import SHARED_DIR, Action, CompileError
from screenplay.state import StateError, fmt_t


def error_message(
    shot: str | None,
    t: float | None,
    who: str | None,
    do: str | None,
    what: str | None,
    reason: str,
) -> str:
    """``<head>: <who> cannot <do> "<what>"; <reason>`` and its shorter forms.

    The head is ``shot <id>, t=<t>``, ``shot <id>`` when ``t`` is None, or ``screenplay`` when
    ``shot`` is None (``t`` is then ignored). ``who`` and ``do`` count when they are not None;
    ``what`` only when it is non-empty. validate.py builds its messages here too, so every step
    of the compiler shares one grammar.
    """
    if shot is None:
        head = "screenplay"
    elif t is None:
        head = f"shot {shot}"
    else:
        head = f"shot {shot}, t={fmt_t(t)}"
    obj = f' "{what}"' if what else ""
    if do is not None and who is not None:
        return f"{head}: {who} cannot {do}{obj}; {reason}"
    if do is not None:
        return f"{head}: cannot {do}{obj}; {reason}"
    return f"{head}: {reason}"


def error(code: str, reason: str, shot: str | None = None, action: Action | None = None) -> CompileError:
    """A CompileError for ``reason``, placed on ``action`` (as written) when there is one."""
    if action is None:
        return CompileError(code=code, message=error_message(shot, None, None, None, None, reason), shot=shot)
    return CompileError(
        code=code,
        message=error_message(shot, action.t, action.who, action.do, action.what, reason),
        shot=shot,
        t=float(action.t),
        who=action.who,
        do=action.do,
    )


def from_state_error(e: StateError, shot: str | None, action: Action | None) -> CompileError:
    """A world-state rejection (``not_holding``, ``busy``, ``seated``, ...)."""
    return error(e.code, e.reason, shot, action)


def out_of_reach_reason(e: OutOfReach, who: str | None = None) -> str:
    """``dad's arm_r cannot reach: target is 0.62 from the shoulder, reach is 0.37``."""
    name = e.who or who
    whose = f"{name}'s {e.arm}" if name else e.arm
    return f"{whose} cannot reach: target is {e.distance:.2f} from the shoulder, reach is {e.reach:.2f}"


def from_out_of_reach(e: OutOfReach, shot: str | None, action: Action | None) -> CompileError:
    """An IK target beyond an arm's full stretch. The reason names the puppet whose arm failed."""
    return error("out_of_reach", out_of_reach_reason(e, action.who if action else None), shot, action)


def from_narration_error(e: NarrationError) -> CompileError:
    """A narration problem: its message already carries the shot and line."""
    return CompileError(code=e.code, message=e.message, shot=e.shot or None)


# --------------------------------------------------------------------------- catalogue

@dataclass(frozen=True)
class ErrorCode:
    """One compile-error code: what it means, and a fix template when the fix is obvious.

    ``hint`` is filled by ``hint_for``. Its fields are ``{who}``, ``{do}``, ``{shot}`` and
    ``{t}`` (from the CompileError), ``{what}`` (the action's object, quoted in the message
    head), ``{subject}`` (the id the reason is about: its first word, so ``mom`` in
    ``mom is offstage; use enter first`` even when dad's hand_to failed), ``{until}`` (the
    ``until t=<x>`` time in a busy reason) and ``{param}`` (the missing parameter, quoted:
    ``"<p>" is missing``, ``needs <p>`` or ``no <p> given``). A field the error cannot fill
    reads as a generic phrase (``_Fields.FALLBACK``), so a hint is always a whole sentence.

    Some codes cover situations with different fixes; HINT_VARIANTS refines ``hint`` for them
    from the reason's wording (an effect aimed offstage, a prop someone else is holding).
    """

    summary: str
    hint: str | None = None


_POSE_NAMES = ", ".join(json.loads((SHARED_DIR / "rig" / "poses.json").read_text())["poses"])

ERROR_CODES: dict[str, ErrorCode] = {
    # ---- screenplay and shot structure (validate.py, compiler.py)
    "no_shots": ErrorCode("the screenplay has no shots", "add at least one shot"),
    "duplicate_shot": ErrorCode("two shots share an id", "give each shot a unique id"),
    "duplicate_action_id": ErrorCode(
        "two actions in one shot share an id", "give each action in shot {shot} a unique id"
    ),
    "ambiguous_name": ErrorCode(
        "a name is used for two kinds of thing (person, prop, mark)", "rename one of them"
    ),
    "unknown_set": ErrorCode("a shot's set is not a known set", "use one of the sets the message lists"),
    "bad_mark": ErrorCode("a mark is not [u, h] or [u, h, d]", "write the mark as [u, h] or [u, h, d] (numbers)"),
    "bad_start": ErrorCode("a shot's start block cannot be applied", None),
    "prop_conflict": ErrorCode(
        "a prop starts both in a hand and in propStart", "remove the prop from propStart or from the start holding"
    ),
    "double_hold": ErrorCode("two people start holding the same prop", "give the prop to only one of them"),
    # ---- names (validate.py, state.py, locate.py, verbs)
    "unknown_person": ErrorCode(
        "a name is not in the cast", "add the person to the cast, or use a name the message lists"
    ),
    "unknown_prop": ErrorCode(
        "a prop is not in the screenplay's props",
        "add the prop to the screenplay's props, or use one the message lists",
    ),
    "unknown_id": ErrorCode(
        "a person or prop id is unknown to the world state",
        "use a person in the cast or a prop in the screenplay's props",
    ),
    "unknown_mark": ErrorCode(
        "a mark is not a mark of the shot",
        "use a mark the message lists, or add it to shot {shot}'s marks as [u, h]",
    ),
    "unknown_target": ErrorCode(
        "a target is not a person, prop or mark of the shot", "name a person, prop or mark the message lists"
    ),
    "unknown_verb": ErrorCode("an action's do is not a verb", "use a verb the message lists"),
    "unsupported_verb": ErrorCode("a vocabulary verb the compiler cannot run yet", "use another verb for now"),
    "unknown_move": ErrorCode("a camera move is not in the vocabulary", "use a camera move the message lists"),
    "unknown_pose": ErrorCode("a pose is not in shared/rig/poses.json", f"use one of the poses: {_POSE_NAMES}"),
    "unknown_socket": ErrorCode("a prop has no such socket", "use a socket the message lists"),
    "unknown_state": ErrorCode("a prop has no such state", "use a state the message lists"),
    # ---- parameters and values (validate.py, effects.py, verbs)
    "missing_param": ErrorCode("a required parameter is missing", "add {param} to this {do}"),
    "unexpected_param": ErrorCode("a parameter the verb does not take", "remove the parameter from this {do}"),
    "invalid_value": ErrorCode("a parameter's value is not allowed", "use a value the message lists"),
    "invalid_action": ErrorCode("an action the compiler cannot build keys for", None),
    "bad_param": ErrorCode("an effect parameter does not apply", "remove the parameter from this {do}"),
    "bad_time": ErrorCode(
        "a time is negative, not finite, or out of order", "use a finite t >= 0 and a dur > 0, in order"
    ),
    "bad_dur": ErrorCode("a dur is not a positive number of seconds", "give dur a positive number of seconds"),
    "bad_target": ErrorCode("a target of the wrong kind for the verb", None),
    "bad_exit": ErrorCode("an exit to somewhere that is not offstage", "exit to offstage_left or offstage_right"),
    "bad_facing": ErrorCode("a facing that is not 1 or -1", None),
    "bad_socket": ErrorCode("a hand socket that is not a hand", "use socket hand_r or hand_l"),
    "bad_parent": ErrorCode("a place_on parent that is not the ground or a prop socket", None),
    "bad_camera": ErrorCode("a camera cue whose target cannot be placed", "aim the camera at something on stage"),
    "not_an_effect": ErrorCode("an effect verb that is not an effect", None),
    "self_target": ErrorCode("an action aimed at its own actor", "name someone else"),
    "self_hand_to": ErrorCode("a hand_to to the giver", "name someone else in to"),
    "hug_self": ErrorCode("a hug of oneself", "name someone else in target"),
    "too_big": ErrorCode(
        "a furniture, vehicle or building prop used as a carried prop", "carry a smaller prop instead"
    ),
    "cycle": ErrorCode("a prop would rest on itself", "place it on a different prop"),
    # ---- people on and off stage (state.py, verbs)
    "offstage": ErrorCode("a person is offstage", "add an enter for {subject} before this {do}"),
    "already_on_stage": ErrorCode("an enter for someone already on stage", "use walk_to instead of enter"),
    "busy": ErrorCode("a person is still busy with an earlier action", "start this {do} at t={until} or later"),
    "follow_cycle": ErrorCode(
        "people follow each other in a loop", "drop one of the follows, or have that person walk_to a mark"
    ),
    "follow_order": ErrorCode("a follow and an action its leader depends on wait for each other", None),
    "nothing_to_follow": ErrorCode(
        "a follow of someone who does not move afterwards", "move the leader after this follow, or use walk_to"
    ),
    # ---- props (state.py, locate.py, verbs)
    "not_on_stage": ErrorCode(
        "a prop is not on stage", "place {subject} with propStart in shot {shot}, or have someone carry it in"
    ),
    "not_holding": ErrorCode("a prop is not in the actor's hand", "have {who} pick_up {what} before this {do}"),
    "already_held": ErrorCode("a prop is already held or worn", "remove this {do}, or hand_to from its holder"),
    "held": ErrorCode("someone is holding the prop", "have its holder drop or place_on it first"),
    "hands_full": ErrorCode("both hands are holding something", "have {subject} drop or place_on a prop first"),
    "head_full": ErrorCode("the head is already wearing something", None),
    "socket_taken": ErrorCode("a socket is occupied", "use a free socket, or move what is in it first"),
    "no_socket": ErrorCode("place_on a prop with no sockets", "place it on a prop with sockets, or on a mark"),
    "occupied": ErrorCode("someone is sitting on the prop", "add a stand_up for {subject} first"),
    "already_open": ErrorCode("open on a prop that is already open", "remove this open"),
    "already_closed": ErrorCode("close on a prop that is already closed", "remove this close"),
    # ---- seating and vehicles (state.py, verbs)
    "seated": ErrorCode(
        "a seated person cannot do this", "add a stand_up (or get_out, in a vehicle) for {subject} before this {do}"
    ),
    "already_seated": ErrorCode(
        "a person is already seated", "add a stand_up (or get_out, in a vehicle) for {subject} first"
    ),
    "already_kneeling": ErrorCode("a kneel by someone already kneeling", "remove this kneel"),
    "not_sitting": ErrorCode("a stand_up by someone already standing", "remove this stand_up"),
    "not_standing": ErrorCode("an action that needs a standing person", "add a stand_up for {subject} first"),
    "no_seat": ErrorCode("a prop with no free seat", "sit on another prop"),
    "no_free_seat": ErrorCode("every seat in a vehicle is taken", "free a seat with get_out first"),
    "vehicle_seat": ErrorCode("sit_on a vehicle", "use get_in instead of sit_on"),
    "seated_vehicle": ErrorCode("a person seated in a vehicle", "add a get_out for {subject} first"),
    "not_a_vehicle": ErrorCode("a vehicle verb on a prop that is not a vehicle", "use a vehicle prop, or sit_on"),
    "not_in_vehicle": ErrorCode("a person is not in the vehicle", "add a get_in for {subject} first"),
    "not_driver": ErrorCode("drive_to by someone not in the driver's seat", "get_in with socket driver first"),
    "no_driver": ErrorCode("ride_to with nobody in the driver's seat", "have someone get_in {what} with socket driver"),
    "vehicle_moving": ErrorCode("a vehicle is still moving", "wait until it stops, at the time the message gives"),
    "not_on_ground": ErrorCode("a vehicle that is not on the ground", None),
    # ---- reach (ik.py)
    "out_of_reach": ErrorCode(
        "an arm cannot reach its target", "walk {who} closer first, or move the target nearer"
    ),
    # ---- narration (narration.py)
    "no_narrator": ErrorCode("nobody to tell the narration", "add someone to the cast"),
    "cue_unknown_action": ErrorCode(
        "a narration cue names no action", "set cue to an action id or verb in shot {shot}"
    ),
    "cue_word_missing": ErrorCode("a cueWord is not a word of its line", "set cueWord to a word of the line"),
    "cue_conflict": ErrorCode("two lines cue the same action", "cue each action from one line only"),
    "cue_before_start": ErrorCode(
        "a cue would move something before the shot starts", "start the line later, or move the action later"
    ),
    "lines_overlap": ErrorCode(
        "a narration line starts before the previous one is said", "start the later line after the earlier one ends"
    ),
    "duplicate_telling": ErrorCode(
        "a teller has both the default narration and a telling", "give each teller one telling"
    ),
    "telling_unknown_shot": ErrorCode("a telling names a shot that is not in the screenplay", None),
    # ---- interactions and explanations (validate.py, narration.py)
    "interaction_unknown_shot": ErrorCode("an interaction names a shot that is not in the screenplay", None),
    "interaction_unknown_prop": ErrorCode("an interaction drives a prop that is not in the screenplay's props", None),
    "interaction_unknown_part": ErrorCode("an interaction drives a part the prop does not have", None),
    "interaction_bad_target": ErrorCode("a scrub target that cannot be found", None),
    "interaction_missing_to": ErrorCode("a scrub with no end", 'give the scrub a "to"'),
    "interaction_bad_range": ErrorCode("a scrub that would run backwards", 'give the scrub a "to" after its "from"'),
    "explanation_bad_target": ErrorCode(
        "an explanation target is not puppet:<id> or prop:<id>", "write the target as puppet:<id> or prop:<id>"
    ),
    "explanation_unknown_target": ErrorCode("an explanation names someone or something not in the screenplay", None),
}


# An effect's reason (effects.py) starts ``<effect> at "<name>": ``; its fix is to re-aim it.
_EFFECT_AT = r'^\w+ at "(?P<subject>[^"]*)": '

# Per code, (pattern, hint) pairs tried in order on the error's reason before the code's own
# hint. The first pattern that matches wins; its named groups become hint fields, and a None
# hint means the situation has no obvious fix.
HINT_VARIANTS: dict[str, tuple[tuple[re.Pattern[str], str | None], ...]] = {
    "offstage": (
        # a mark beyond the stage edge: no enter can bring it on
        (re.compile(_EFFECT_AT + r"\S+ is a mark offstage"), "aim the {do} at a mark on stage"),
        (re.compile(_EFFECT_AT), "aim the {do} at a mark on stage, or give it a t when {subject} is on stage"),
    ),
    "not_on_stage": (
        (re.compile(_EFFECT_AT), "place {subject} with propStart in shot {shot}, or aim the {do} at a mark"),
    ),
    "unknown_target": (
        (re.compile(_EFFECT_AT), "aim the {do} at a person, prop or mark the message lists"),
    ),
    "not_holding": (
        # someone else has it: picking it up would only fail with already_held
        (re.compile(r"^(?P<holder>\S+) is holding it$"), "have {holder} hand_to {what} to {who} before this {do}"),
        # worn on a head: no verb takes a worn prop off
        (re.compile(r"^\S+ is wearing it$"), None),
        (re.compile(r"^it is not on stage$"), "place {what} with propStart in shot {shot}, then have {who} pick_up {what}"),
    ),
    # state.World.seat_reason ends with the fix: get_out in a vehicle, stand_up anywhere else
    "seated": ((re.compile(r"; use get_out first$"), "add a get_out for {subject} before this {do}"),),
    "already_seated": ((re.compile(r"; use get_out first$"), "add a get_out for {subject} first"),),
    "prop_conflict": (
        (
            re.compile(r"^(?P<holder>\S+) starts holding (?P<what>\S+),"),
            "remove {what} from propStart or from {holder}'s start holding",
        ),
    ),
}


class _Fields(dict):
    """Hint fields; a field the error cannot fill reads as a generic phrase."""

    FALLBACK = {
        "who": "the actor",
        "subject": "it",
        "what": "the prop",
        "do": "action",
        "shot": "this shot",
        "t": "its t",
        "until": "a later time",
        "param": "the missing parameter",
        "holder": "its holder",
    }

    def __missing__(self, key: str) -> str:
        return self.FALLBACK.get(key, key)


_HEAD = re.compile(r"^(?:shot [^:]*|screenplay): ")
_WHAT = re.compile(r' cannot \S+ "([^"]*)"; ')
_UNTIL = re.compile(r"until t=(-?[0-9.]+(?:e-?[0-9]+)?)")
_PARAM = re.compile(r'"([^"]+)" is missing|needs "?(\w+)"?$|no (\w+) given')


def _reason(error: CompileError) -> str:
    """The reason part of an error_message (the text after the head and the action)."""
    body = _HEAD.sub("", error.message, count=1)
    action, sep, rest = body.partition("; ")
    if error.do is not None and sep and (action.startswith("cannot ") or " cannot " in action):
        return rest
    return body


def hint_for(error: CompileError) -> str | None:
    """A short fix for ``error`` from ERROR_CODES, filled from its fields; None when the code
    has no obvious fix (or is unknown)."""
    entry = ERROR_CODES.get(error.code)
    if entry is None or entry.hint is None:
        return None
    reason = _reason(error)
    fields = _Fields()
    for key, value in (("who", error.who), ("do", error.do), ("shot", error.shot)):
        if value:
            fields[key] = value
    if error.t is not None:
        fields["t"] = fmt_t(error.t)
    m = _WHAT.search(error.message)
    if m:
        fields["what"] = m.group(1)
    first = reason.split(" ", 1)[0].strip('"')
    if first.endswith("'s"):
        first = first[:-2]
    if first and first not in ("it", "the", "a", "an", "cannot", "nobody", "every"):
        fields["subject"] = first
    elif "who" in fields:
        fields["subject"] = fields["who"]
    m = _UNTIL.search(reason)
    if m:
        fields["until"] = m.group(1)
    m = _PARAM.search(reason)
    if m:
        fields["param"] = f'"{next(g for g in m.groups() if g)}"'
    hint: str | None = entry.hint
    for pattern, variant in HINT_VARIANTS.get(error.code, ()):
        m = pattern.search(reason)
        if m:
            fields.update({k: v for k, v in m.groupdict().items() if v})
            hint = variant
            break
    return None if hint is None else hint.format_map(fields)
