"""Shot timing, set rise/fold tracks and effect entries (shared/CONTRACTS.md Decision 5).

Each set layer has its own 'fold' track on target "set:<setId>.layer<i>" (0 = flat on the page,
1 = risen). A shot's set starts rising at TimelineShot.start and the shot's screenplay t=0
happens RISE seconds later. Rising goes back layer first; folding goes front layer first.
Arithmetic matches api/tests/gen_hymn_books_sample.py.
"""

from __future__ import annotations

from screenplay.schema import TimelineEffect
from screenplay.tracks import Key, Vec3

RISE = 1.2
LAYER_STAGGER = 0.2
LAYER_RISE = 0.8
LAYER_FOLD = 0.4
FOLD_STAGGER = 0.1
WORD_TAIL = 0.6
ACTION_TAIL = 0.5
SNAP = 0.01

EFFECT_SECONDS = {"confetti": 2.0, "steam": 3.0, "smoke": 3.0, "time_of_day": 1.0}

_R = lambda v: round(v, 3)  # noqa: E731


def layer_target(set_id: str, i: int) -> str:
    return f"set:{set_id}.layer{i}"


def _check(transition: str) -> None:
    if transition not in ("fold", "none"):
        raise ValueError(f"unknown transition {transition!r}")


def rise_keys(set_id: str, n_layers: int, start: float, transition: str) -> list[Key]:
    """Layers rise 0 -> 1, back layer (0) first, staggered by LAYER_STAGGER; 'none' snaps up."""
    _check(transition)
    keys: list[Key] = []
    for i in range(n_layers):
        target = layer_target(set_id, i)
        if transition == "fold":
            t0 = start + LAYER_STAGGER * i
            keys += [(target, "fold", t0, 0.0), (target, "fold", t0 + LAYER_RISE, 1.0)]
        else:
            keys += [(target, "fold", start, 0.0), (target, "fold", start + SNAP, 1.0)]
    return keys


def fold_out_seconds(n_layers: int) -> float:
    """Seconds a set of ``n_layers`` takes to fold flat; a set with no layers takes none."""
    if n_layers <= 0:
        return 0.0
    return LAYER_FOLD + FOLD_STAGGER * (n_layers - 1)


def fold_keys(set_id: str, n_layers: int, t: float, transition: str) -> tuple[list[Key], float]:
    """Layers fold 1 -> 0 from t, front layer first. Returns (keys, seconds the fold takes)."""
    _check(transition)
    keys: list[Key] = []
    if n_layers <= 0:
        return keys, 0.0
    for i in range(n_layers):
        target = layer_target(set_id, i)
        if transition == "fold":
            t0 = t + FOLD_STAGGER * (n_layers - 1 - i)
            keys += [(target, "fold", t0, 1.0), (target, "fold", t0 + LAYER_FOLD, 0.0)]
        else:
            keys += [(target, "fold", t, 1.0), (target, "fold", t + SNAP, 0.0)]
    return keys, (fold_out_seconds(n_layers) if transition == "fold" else 0.0)


def content_end(last_key_t: float, last_word_t: float | None) -> float:
    """When a shot's content is over: action settles, the last word is heard."""
    end = last_key_t + ACTION_TAIL
    if last_word_t is not None:
        end = max(end, last_word_t + WORD_TAIL)
    return _R(end)


def next_shot_timing(
    prev_content_end: float,
    prev_set_id: str,
    prev_n_layers: int,
    next_set_id: str,
    transition: str,
) -> tuple[float, float, float, list[Key]]:
    """(prev_end, next_start, next_action_start, fold keys for the previous set)."""
    _check(transition)
    keys: list[Key] = []
    prev_end = prev_content_end
    if prev_set_id != next_set_id:
        keys, used = fold_keys(prev_set_id, prev_n_layers, prev_content_end, transition)
        prev_end = prev_content_end + used
    prev_end = _R(prev_end)
    return prev_end, prev_end, _R(prev_end + RISE), keys


def effect_entry(
    do: str,
    t: float,
    at: Vec3 | None,
    dur: float | None,
    value: str | None,
    shot_end: float,
) -> TimelineEffect:
    """One Timeline.effects entry with the default duration for its kind.

    Rain with no ``dur`` lasts until ``shot_end``, measured from its rounded start so the
    rounded ``t + dur`` never passes the shot's end (and is never negative from rounding alone:
    rain at the shot's very end lasts 0)."""
    if dur is None:
        if do == "rain":
            dur = shot_end - t if shot_end < t else max(shot_end - _R(t), 0.0)
        else:
            dur = EFFECT_SECONDS.get(do)
    if dur is not None and dur < 0:
        raise ValueError(f"effect {do!r} at t={t} has a negative duration")
    return TimelineEffect(
        t=_R(t),
        kind=do,
        at=None if at is None else [_R(float(x)) for x in at],
        dur=None if dur is None else _R(dur),
        value=value,
    )
