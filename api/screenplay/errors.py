"""CompileError construction: one exact, machine-readable message per problem.

The director agent reads these messages and fixes its draft, so the wording is stable:

    shot s1, t=5: dad cannot hand_to "hymn_books"; mom is holding it
    shot s1: steps_top is not a mark in shot s1
    screenplay: church_steps is not a known set

For an action error, ``CompileError.t`` is the action's ``t`` as written in the screenplay (not
the cue-retimed time), and ``who`` / ``do`` are filled in.
"""

from __future__ import annotations

from screenplay.ik import OutOfReach
from screenplay.narration import NarrationError
from screenplay.schema import Action, CompileError
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
    ``shot`` is None (``t`` is then ignored).
    """
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
