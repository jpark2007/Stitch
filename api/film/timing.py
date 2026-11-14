"""Time: shots and beats in film seconds, derived from the narration.

`time_film(beats, narration, descriptions)`:

1. Each beat's span runs from its line's start to the next line's start (its narration plus
   the pause after it); the first beat starts at 0 and the last ends at the film's end, which
   is TAIL seconds after the narration, or longer when the photos need it (at least
   COMFORT_SECONDS each), so a short story still lets its photos breathe.
2. A beat whose span is under MIN_SHOT cannot hold a shot of its own: it is merged into the
   next beat (the last one into the one before), titles and narration joined.
3. Each beat can hold between ceil(span / MAX_SHOT) and floor(span / MIN_SHOT) shots. When
   the photos are more than all beats can hold, the lowest-quality ones are dropped (never a
   beat's last photo); when they are too few to keep every shot under MAX_SHOT, a beat's photo is
   shown again (with a different motion) as a last resort.
4. Shot counts per beat stay as close to the plan as those bounds allow, and the photos are
   dealt out in plan order, so a photo moves at most to a neighbouring beat.
5. Inside a beat, the cuts divide the span evenly, each moved to the nearest word start
   within SNAP seconds (a word that opens a phrase, after a comma or full stop, is preferred)
   while every shot stays within MIN_SHOT..MAX_SHOT.
6. Motion follows the photo's focus (left or right of centre pans that way, else a zoom) and
   never repeats on adjacent shots.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import schemas
from agent.tools.narration import _split_words
from film.plan import PlanBeat, PlanShot
from film.types import Narration

MIN_SHOT = 2.5
MAX_SHOT = 8.0
TAIL = 1.5
"""Seconds the last photo holds after the last word."""
COMFORT_SECONDS = 3.0
SNAP = 1.2
"""How far (seconds) a cut may move from its even split to land on a word."""
PHRASE_BONUS = 0.5

MOTIONS: tuple[schemas.FilmMotion, ...] = ("in", "out", "left", "right")
PAN_EDGE = 0.12
"""Focus this far left or right of centre pans toward it."""

_EPS = 1e-6


def _r(t: float) -> float:
    return round(t, 3)


def _ceil3(t: float) -> float:
    return math.ceil(t * 1000 - _EPS) / 1000


def _floor3(t: float) -> float:
    return math.floor(t * 1000 + _EPS) / 1000


@dataclass
class _Group:
    title: str
    narration: str
    start: float
    end: float
    shots: list[PlanShot] = field(default_factory=list)

    @property
    def span(self) -> float:
        return self.end - self.start

    @property
    def lo(self) -> int:
        return max(1, math.ceil(self.span / MAX_SHOT - _EPS))

    @property
    def hi(self) -> int:
        return max(1, math.floor(self.span / MIN_SHOT + _EPS))


@dataclass(frozen=True)
class TimedFilm:
    shots: list[schemas.FilmShot]
    beats: list[schemas.FilmBeat]
    words: list[schemas.FilmWord]
    duration: float


def check_narration(narration: Narration, n_lines: int) -> None:
    """ValueError when a narration does not fit its lines (a narrator bug or a bad stub)."""
    if len(narration.line_starts) != n_lines:
        raise ValueError(f"{len(narration.line_starts)} line starts for {n_lines} lines")
    values = [*narration.line_starts, narration.duration, *(t for t, _ in narration.words)]
    if not all(isinstance(v, (int, float)) and math.isfinite(v) and v >= 0 for v in values):
        raise ValueError("narration times must be finite and >= 0")
    if any(b < a for a, b in zip(narration.line_starts, narration.line_starts[1:])):
        raise ValueError("line starts go backwards")


def _groups(beats: list[PlanBeat], narration: Narration, end: float) -> list[_Group]:
    starts = [0.0, *(_r(t) for t in narration.line_starts[1:])]
    ends = [*starts[1:], end]
    groups = [
        _Group(b.title, b.narration, s, e, list(b.shots)) for b, s, e in zip(beats, starts, ends, strict=True)
    ]
    # Merge beats too short for a shot of their own.
    while len(groups) > 1:
        short = next((i for i, g in enumerate(groups) if g.span < MIN_SHOT - _EPS), None)
        if short is None:
            break
        a, b = (short, short + 1) if short + 1 < len(groups) else (short - 1, short)
        first, second = groups[a], groups[b]
        groups[a : b + 1] = [
            _Group(
                first.title,
                f"{first.narration} {second.narration}",
                first.start,
                second.end,
                first.shots + second.shots,
            )
        ]
    if groups[0].span < MIN_SHOT:
        groups[0].end = MIN_SHOT
    return groups


def _quality(descriptions: dict[str, dict], photo_id: str) -> float:
    q = descriptions.get(photo_id, {}).get("quality", 0.5)
    return float(q) if isinstance(q, (int, float)) else 0.5


def _fit_counts(groups: list[_Group], descriptions: dict[str, dict]) -> None:
    """Drop or repeat photos until sum(lo) <= photos <= sum(hi)."""
    while sum(len(g.shots) for g in groups) > sum(g.hi for g in groups):
        # The lowest-quality photo of any beat that keeps one; ties: the fullest beat, the later photo.
        gi, k = min(
            ((gi, k) for gi, g in enumerate(groups) if len(g.shots) > 1 for k in range(len(g.shots))),
            key=lambda c: (
                _quality(descriptions, groups[c[0]].shots[c[1]].photo_id),
                groups[c[0]].hi - len(groups[c[0]].shots),
                -c[0],
                -c[1],
            ),
        )
        del groups[gi].shots[k]
    while sum(len(g.shots) for g in groups) < sum(g.lo for g in groups):
        i = max(range(len(groups)), key=lambda i: (groups[i].lo - len(groups[i].shots), -i))
        g = groups[i]
        pool = g.shots or next(groups[j].shots for j in sorted(range(len(groups)), key=lambda j: abs(j - i)) if groups[j].shots)
        uses: dict[str, int] = {}
        for s in pool:
            uses[s.photo_id] = uses.get(s.photo_id, 0) + 1
        last = g.shots[-1].photo_id if g.shots else None
        pick = min(pool, key=lambda s: (uses[s.photo_id], s.photo_id == last, pool.index(s)))
        g.shots.append(PlanShot(pick.photo_id, None))


def _counts(groups: list[_Group]) -> list[int]:
    """Shots per beat within each beat's bounds, as close to the plan as possible."""
    total = sum(len(g.shots) for g in groups)
    counts = [min(max(len(g.shots), g.lo), g.hi) for g in groups]
    while sum(counts) < total:
        i = max(
            (i for i in range(len(groups)) if counts[i] < groups[i].hi),
            key=lambda i: (len(groups[i].shots) - counts[i], groups[i].span / counts[i], -i),
        )
        counts[i] += 1
    while sum(counts) > total:
        i = max(
            (i for i in range(len(groups)) if counts[i] > groups[i].lo),
            key=lambda i: (counts[i] - len(groups[i].shots), -groups[i].span / counts[i], -i),
        )
        counts[i] -= 1
    return counts


def _phrase_starts(lines: list[str], narration: Narration) -> set[int]:
    """Indexes of narration words that open a phrase, when the words match the lines' text."""
    flags: list[bool] = []
    for line in lines:
        split = _split_words(line)
        for k in range(len(split)):
            flags.append(k == 0 or split[k - 1][1] > 0)
    if len(flags) != len(narration.words):
        return set()
    return {i for i, f in enumerate(flags) if f}


def _cuts(a: float, b: float, count: int, words: list[tuple[float, bool]]) -> list[float]:
    """count + 1 cut times from a to b, each shot MIN_SHOT..MAX_SHOT, snapped to words."""
    cuts = [a]
    for k in range(1, count):
        prev = cuts[-1]
        after = count - k
        lo_t = _ceil3(max(prev + MIN_SHOT, b - after * MAX_SHOT))
        hi_t = _floor3(min(prev + MAX_SHOT, b - after * MIN_SHOT))
        ideal = min(max(prev + (b - prev) / (after + 1), lo_t), hi_t)
        best, best_score = _r(ideal), SNAP
        for t, phrase in words:
            if lo_t <= t <= hi_t and abs(t - ideal) <= SNAP:
                score = abs(t - ideal) - (PHRASE_BONUS if phrase else 0.0)
                if score < best_score:
                    best, best_score = t, score
        cuts.append(min(max(_r(best), lo_t), hi_t))
    cuts.append(b)
    return cuts


def _motion(focus: tuple[float, float], prev: str | None) -> schemas.FilmMotion:
    u = focus[0]
    want: schemas.FilmMotion = "left" if u < 0.5 - PAN_EDGE else "right" if u > 0.5 + PAN_EDGE else "in"
    if want != prev:
        return want
    k = MOTIONS.index(want)
    return next(m for m in MOTIONS[k + 1 :] + MOTIONS[:k] if m != prev)


def _focus(descriptions: dict[str, dict], photo_id: str) -> tuple[float, float]:
    focus = descriptions.get(photo_id, {}).get("focus")
    try:
        u, v = (min(1.0, max(0.0, float(x))) for x in focus)  # type: ignore[union-attr]
    except (TypeError, ValueError):
        return (0.5, 0.5)
    return (u, v)


def time_film(beats: list[PlanBeat], narration: Narration, descriptions: dict[str, dict]) -> TimedFilm:
    """Shots, beats and words in film time (see the module docstring)."""
    check_narration(narration, len(beats))
    n_photos = sum(len(b.shots) for b in beats)
    spoken_end = max([narration.duration, *narration.line_starts, *(t for t, _ in narration.words)])
    end = _r(max(spoken_end + TAIL, n_photos * COMFORT_SECONDS, MIN_SHOT))
    groups = _groups(beats, narration, end)
    _fit_counts(groups, descriptions)
    counts = _counts(groups)

    flat = [s for g in groups for s in g.shots]
    phrase = _phrase_starts([b.narration for b in beats], narration)
    word_marks = [(_r(t), i in phrase) for i, (t, _) in enumerate(narration.words)]

    shots: list[schemas.FilmShot] = []
    at = 0
    prev_motion: str | None = None
    for beat_index, (g, count) in enumerate(zip(groups, counts, strict=True)):
        cuts = _cuts(_r(g.start), _r(g.end), count, word_marks)
        for k in range(count):
            shot = flat[at]
            at += 1
            focus = _focus(descriptions, shot.photo_id)
            motion = _motion(focus, prev_motion)
            prev_motion = motion
            shots.append(
                schemas.FilmShot(
                    photo_id=shot.photo_id,
                    start=cuts[k],
                    duration=_r(cuts[k + 1] - cuts[k]),
                    beat=beat_index,
                    caption=shot.caption,
                    focus=focus,
                    motion=motion,
                )
            )
    film_beats = [schemas.FilmBeat(title=g.title, start=_r(g.start), narration=g.narration) for g in groups]
    duration = _r(groups[-1].end)
    words = [schemas.FilmWord(t=t, word=w) for t, w in ((_r(t), w) for t, w in narration.words) if t <= duration]
    return TimedFilm(shots=shots, beats=film_beats, words=words, duration=duration)


__all__ = ["COMFORT_SECONDS", "MAX_SHOT", "MIN_SHOT", "TAIL", "TimedFilm", "check_narration", "time_film"]
