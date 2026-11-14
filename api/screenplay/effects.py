"""Effect verbs -> ``Timeline.effects`` entries (shared/CONTRACTS.md 'Timeline': effects are a
list ``{t, kind, at, dur, value}``, not tracks).

``effect_for`` turns one effect action into one entry, in timeline time:

- ``at``: ``locate(action.at, t)`` when the action names a mark, person or prop. confetti,
  steam and smoke with no ``at`` default to ``DEFAULT_AT`` (centre stage, half a unit up, on
  the acting plane). rain and time_of_day fill the whole stage and have no ``at``.
- ``dur``: as given, else layout's default for the kind; rain with no ``dur`` lasts until
  ``shot_end``.
- ``value``: only for time_of_day, one of ``vocabulary.timesOfDay``.

Durations, rounding and the entry itself come from ``layout.effect_entry``. Effects never
occupy a puppet and change no world state; the compiler appends the entry to the timeline.
"""

from __future__ import annotations

from collections.abc import Callable

from screenplay.layout import effect_entry
from screenplay.schema import VOCABULARY, Action, TimelineEffect
from screenplay.state import StateError

Vec3 = tuple[float, float, float]
Locate = Callable[[str, float], Vec3]

EFFECT_VERBS: list[str] = list(VOCABULARY["effects"])
TIMES_OF_DAY: list[str] = list(VOCABULARY["timesOfDay"])
DEFAULT_AT: Vec3 = (0.0, 0.5, 0.5)
_POINT_EFFECTS = ("confetti", "steam", "smoke")  # the effects that happen somewhere


def _takes(verb: str, param: str) -> bool:
    spec = VOCABULARY["actions"][verb]
    return param in spec["required"] or param in spec["optional"]


def effect_for(action: Action, t: float, locate: Locate, shot_end: float) -> TimelineEffect:
    """One ``Timeline.effects`` entry for an effect action starting at timeline time ``t``.

    Raises ``StateError``: ``not_an_effect`` for a non-effect verb, ``bad_param`` for an ``at``
    or ``value`` the effect does not take, ``invalid_value`` for a bad time_of_day value,
    ``bad_time`` for a negative duration (including rain that starts after ``shot_end``),
    and locate's ``unknown_target`` for an ``at`` that is not a mark, person or prop.
    """
    do = action.do
    if do not in EFFECT_VERBS:
        raise StateError("not_an_effect", f"{do} is not an effect; effects are {', '.join(EFFECT_VERBS)}")
    if action.at is not None and not _takes(do, "at"):
        raise StateError("bad_param", f"{do} fills the whole stage and takes no at")
    if action.value is not None and do != "time_of_day":
        raise StateError("bad_param", f"{do} takes no value")
    if do == "time_of_day" and action.value not in TIMES_OF_DAY:
        raise StateError("invalid_value", f"time_of_day value must be one of {', '.join(TIMES_OF_DAY)}")

    if action.at is not None:
        at: Vec3 | None = locate(action.at, t)
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
            reason = f"{do} starts {t - shot_end:.2f} s after the shot ends; give it a dur"
        raise StateError("bad_time", reason) from None
