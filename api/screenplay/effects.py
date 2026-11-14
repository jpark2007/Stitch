"""Effect verbs -> ``Timeline.effects`` entries (shared/CONTRACTS.md 'Timeline': effects are a
list ``{t, kind, at, dur, value}``, not tracks).

``effect_for`` turns one effect action into one entry, in timeline time:

- ``at``: for confetti, steam and smoke, ``locate(action.at, t)`` when the action names a mark,
  person or prop, and ``DEFAULT_AT`` (centre stage, half a unit up, on the acting plane) when
  it names nothing. rain and time_of_day fill the whole stage and take no ``at``. A placed
  effect must be seen:

  - a target locate cannot place keeps locate's code (``not_on_stage`` for a prop that is not
    on stage, ``unknown_target`` for a name that is not a person, prop or mark), with a reason
    that names the effect and says how to fix it;
  - a target just past the stage edge (``|x|`` up to ``STAGE_EDGE + EDGE_SLACK``, say a puppet
    half a step off) is pulled in to ``|x| = STAGE_EDGE``;
  - a target further out (a person waiting offstage, a prop parked offstage, the builtin
    ``offstage_left`` / ``offstage_right`` marks) is ``offstage``;
  - ``y`` is raised to the floor (``y >= 0``); ``z`` is kept.

- ``dur``: as given, else layout's default for the kind; rain with no ``dur`` lasts until
  ``shot_end``.
- ``value``: only for time_of_day, one of ``vocabulary.timesOfDay``.

Durations, rounding and the entry itself come from ``layout.effect_entry``. Effects never
occupy a puppet and change no world state; the compiler appends the entry to the timeline.
Reasons are written without the message head; errors.error_message adds ``shot <id>, t=<t>:
cannot <effect>;`` in front, with ``t`` as written in the screenplay.
"""

from __future__ import annotations

from collections.abc import Callable

from screenplay.layout import effect_entry
from screenplay.locate import Locator
from screenplay.schema import VOCABULARY, Action, TimelineEffect
from screenplay.state import BUILTIN_MARKS, StateError, fmt_t, missing_param_reason

Vec3 = tuple[float, float, float]
Locate = Callable[[str, float], Vec3]

EFFECT_VERBS: list[str] = list(VOCABULARY["effects"])
TIMES_OF_DAY: list[str] = list(VOCABULARY["timesOfDay"])
DEFAULT_AT: Vec3 = (0.0, 0.5, 0.5)
STAGE_EDGE = 1.0  # the stage's edge, |x|; verbs_core.STAGE_EDGE has the same value
EDGE_SLACK = 0.1  # how far past the edge an effect is still pulled in rather than refused
_POINT_EFFECTS = ("confetti", "steam", "smoke")  # the effects that happen somewhere


def _takes(verb: str, param: str) -> bool:
    spec = VOCABULARY["actions"][verb]
    return param in spec["required"] or param in spec["optional"]


def _is_mark(locate: Locate, name: str) -> bool:
    """Whether ``name`` resolves to a fixed mark (locate's precedence is person, prop, mark).
    A plain callable cannot say, so only the builtin marks count there."""
    if isinstance(locate, Locator):
        world = locate.world
        return name in locate.marks and name not in world.people and name not in world.props
    return name in BUILTIN_MARKS


def _place(action: Action, t: float, locate: Locate) -> Vec3:
    """Where a point effect with an ``at`` plays: on stage, on or above the floor."""
    do, name = action.do, action.at
    assert name is not None
    when = f"t={fmt_t(action.t)}"
    try:
        x, y, z = locate(name, t)
    except StateError as e:
        if e.code == "not_on_stage":
            raise StateError(
                e.code,
                f'{do} at "{name}": {name} is not on stage at {when}; '
                "place it with propStart or aim the effect at a mark",
            ) from None
        if e.code == "unknown_target":
            raise StateError(e.code, f'{do} at "{name}": {e.reason}; aim it at a mark, person or prop') from None
        raise
    if abs(x) > STAGE_EDGE + EDGE_SLACK:
        side = "left" if x < 0 else "right"
        if _is_mark(locate, name):
            reason = f'{do} at "{name}": {name} is a mark offstage {side}; aim it at a mark on stage'
        else:
            reason = (
                f'{do} at "{name}": {name} is offstage {side} at {when}; '
                f"aim it at a mark or give it a t when {name} is on stage"
            )
        raise StateError("offstage", reason)
    return (min(max(x, -STAGE_EDGE), STAGE_EDGE), max(y, 0.0), z)


def effect_for(action: Action, t: float, locate: Locate, shot_end: float) -> TimelineEffect:
    """One ``Timeline.effects`` entry for an effect action starting at timeline time ``t``.

    Raises ``StateError``: ``not_an_effect`` for a non-effect verb, ``bad_param`` for an ``at``
    or ``value`` the effect does not take, ``missing_param`` for a time_of_day with no value,
    ``invalid_value`` for a bad one,
    ``bad_time`` for a negative duration (including rain that starts after ``shot_end``),
    ``unknown_target`` for an ``at`` that is not a mark, person or prop, ``not_on_stage`` for
    an ``at`` prop that is not on stage, and ``offstage`` for an ``at`` beyond the stage edge.
    """
    do = action.do
    if do not in EFFECT_VERBS:
        raise StateError("not_an_effect", f"{do} is not an effect; the effects are {', '.join(EFFECT_VERBS)}")
    if action.at is not None and not _takes(do, "at"):
        raise StateError("bad_param", f"{do} fills the whole stage and takes no at")
    if action.value is not None and do != "time_of_day":
        raise StateError("bad_param", f"{do} takes no value")
    if do == "time_of_day" and action.value is None:
        raise StateError("missing_param", missing_param_reason(do, "value"))
    if do == "time_of_day" and action.value not in TIMES_OF_DAY:
        times = ", ".join(TIMES_OF_DAY)  # validate's wording for the same value
        raise StateError("invalid_value", f'"{action.value}" is not a time of day; use one of {times}')

    if action.at is not None:
        at: Vec3 | None = _place(action, t, locate)
    elif do in _POINT_EFFECTS:
        at = DEFAULT_AT
    else:
        at = None

    try:
        return effect_entry(do, t, at, action.dur, action.value, shot_end)
    except ValueError:
        if action.dur is not None:
            reason = f"{do} dur must not be negative, not {action.dur:g}"
        else:
            reason = f"{do} starts {fmt_t(t - shot_end)} s after the shot ends; give it a dur"
        raise StateError("bad_time", reason) from None
