"""Timeline tracks: collect keys, normalise them, and evaluate them like the stage does.

Conventions are shared/CONTRACTS.md 'Timeline':
- one track per (target, channel); keys are [t, value] sorted by t;
- 'root.facing', 'parent', 'state' and any string value step; numbers and lists are linear;
- before the first key hold the first value, after the last key hold the last value;
- a prop's 'position' never interpolates across a 'parent' key.

Rounding and de-duplication match api/tests/gen_hymn_books_sample.py: every float (including
inside lists) is rounded to 3 dp, ints are not touched by rounding (the Track model itself stores
numbers as floats, so facing 1 / -1 comes out as 1.0 / -1.0, equal in value), keys are stable-sorted by t and a key is
dropped only when it equals the previous key exactly.
"""

from __future__ import annotations

import bisect
from collections.abc import Iterable, Sequence
from typing import Any

from screenplay.schema import Track

Vec3 = tuple[float, float, float]
Key = tuple[str, str, float, Any]
"""(target, channel, t, value) as accepted by TrackSet.extend."""

STEP_CHANNELS = frozenset({"root.facing", "parent", "state"})
DECIMALS = 3


def round_value(v: Any) -> Any:
    """Round floats to 3 dp, recursing into lists/tuples (returned as lists). Ints, str, None as is."""
    if isinstance(v, bool):
        raise TypeError(f"booleans are not valid key values: {v!r}")
    if isinstance(v, float):
        r = round(v, DECIMALS)
        return 0.0 if r == 0 else r  # no -0.0 in the output
    if isinstance(v, int) or v is None or isinstance(v, str):
        return v
    if isinstance(v, (list, tuple)):
        return [round_value(x) for x in v]
    raise TypeError(f"unsupported key value: {v!r}")


def is_step(channel: str, value: Any) -> bool:
    return channel in STEP_CHANNELS or isinstance(value, str) or value is None


def _lerp(a: Any, b: Any, f: float) -> Any:
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        if len(a) != len(b):
            return a if f < 1 else b
        return [x + (y - x) * f for x, y in zip(a, b)]
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return a + (b - a) * f
    return a if f < 1 else b


def _sample(keys: Sequence[tuple[float, Any]], channel: str, t: float, parent_times: Sequence[float] = ()) -> Any:
    """Value of one sorted key list at time t. parent_times: sorted parent-key times (prop position only)."""
    if not keys:
        return None
    times = [k[0] for k in keys]
    # last key at or before t (ties: the last inserted key at that time wins)
    i = bisect.bisect_right(times, t) - 1
    if i < 0:
        return keys[0][1]
    if i >= len(keys) - 1:
        return keys[-1][1]
    (t0, v0), (t1, v1) = keys[i], keys[i + 1]
    if is_step(channel, v0) or is_step(channel, v1):
        return v0
    if parent_times:
        # a parent key in (t0, t1]: hold v0 until it, then v1
        j = bisect.bisect_right(parent_times, t0)
        if j < len(parent_times) and parent_times[j] <= t1:
            return v0 if t < parent_times[j] else v1
    if t1 == t0:
        return v1
    return _lerp(v0, v1, (t - t0) / (t1 - t0))


def _parent_guard(target: str, channel: str) -> bool:
    return target.startswith("prop:") and channel == "position"


class TrackSet:
    """Mutable collection of keys, grouped by (target, channel) in insertion order."""

    def __init__(self) -> None:
        self._raw: dict[tuple[str, str], list[tuple[float, Any]]] = {}

    def add(self, target: str, channel: str, t: float, value: Any) -> None:
        if isinstance(t, bool) or not isinstance(t, (int, float)):
            raise TypeError(f"key time must be a number: {t!r}")
        round_value(value)  # type check early, so the bad key is reported where it was added
        self._raw.setdefault((target, channel), []).append((t, value))

    def extend(self, keys: Iterable[Key]) -> None:
        for target, channel, t, value in keys:
            self.add(target, channel, t, value)

    def has_track(self, target: str, channel: str) -> bool:
        return bool(self._raw.get((target, channel)))

    def last_key_time(self) -> float:
        """Latest key time over every track (rounded), 0.0 if empty."""
        ts = [round_value(float(t)) for keys in self._raw.values() for t, _ in keys]
        return max(ts, default=0.0)

    def _normalised(self, target: str, channel: str) -> list[tuple[float | int, Any]]:
        keys = [(round_value(t), round_value(v)) for t, v in self._raw.get((target, channel), [])]
        keys.sort(key=lambda k: k[0])  # stable: insertion order breaks ties
        out: list[tuple[float | int, Any]] = []
        for k in keys:
            if out and out[-1][0] == k[0] and out[-1][1] == k[1]:
                continue
            out.append(k)
        return out

    def tracks(self) -> list[Track]:
        result = []
        for target, channel in sorted(self._raw):
            keys = self._normalised(target, channel)
            if not keys:
                continue
            # Validated, so the model coerces numbers to float (1 -> 1.0); equal in value to the
            # reference JSON's ints, and serialisation stays warning-free downstream.
            result.append(Track.model_validate({"target": target, "channel": channel, "keys": keys}))
        return result

    def value_at(self, target: str, channel: str, t: float) -> Any:
        if not self.has_track(target, channel):
            return None
        parents: list[float] = []
        if _parent_guard(target, channel):
            parents = [k[0] for k in self._normalised(target, "parent")]
        return _sample(self._normalised(target, channel), channel, t, parents)


def evaluate(tracks: list[Track], target: str, channel: str, t: float) -> Any:
    """value_at over a finished list of tracks (e.g. a golden timeline). None if there is no track."""
    by_key = {(tr.target, tr.channel): tr for tr in tracks}
    tr = by_key.get((target, channel))
    if tr is None or not tr.keys:
        return None
    keys = sorted((tuple(k) for k in tr.keys), key=lambda k: k[0])
    parents: list[float] = []
    if _parent_guard(target, channel) and (target, "parent") in by_key:
        parents = sorted(k[0] for k in by_key[(target, "parent")].keys)
    return _sample(keys, channel, t, parents)
