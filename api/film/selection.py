"""Select: drop look-alikes and bursts, then pick a distinct spread of photos across the event.

A finished film never shows two photos that look alike. One rule decides it, `looks_alike`,
over two signatures film.ingest computes once per photo:

- dHash distance: Hamming distance in bits between the 64-bit difference hashes (structure).
- layout distance (`layout_distance`): mean absolute difference of the contrast-normalised
  8x8 colour layouts, in units of each photo's own contrast. On the synthetic test photos a
  re-encoded, resized, 25% brighter or darker copy, or a 5% crop or shift, measures 0.0 to
  0.31; different scenes measure 0.98 and up, including scenes in one dominant colour.
- tone distance (`tone_distance`): mean absolute difference of the photos' colour balance
  (copies measure under 0.07). It only keeps two near-flat photos of different colours apart.

Two photos look alike when their tones are within TONE_APART (0.25) AND any of:

1. layout distance <= LAYOUT_ALIKE (0.4): the same picture, whatever the capture times;
2. dHash distance <= DUP_BITS (10) and layout distance <= LAYOUT_NEAR (0.7): the same
   structure and broadly the same colours;
3. a burst (capture times within BURST_SECONDS, 5 s): dHash distance <= BURST_BITS (20) or
   layout distance <= LAYOUT_NEAR, since the subject moves between frames of a burst.

Capture times only ever loosen the rule (bursts). They never make two look-alikes distinct:
EXIF times go missing or are wrong (a re-saved copy, a camera set to the wrong day), and the
film must not show the same picture twice.

1. Cluster. Look-alikes are grouped (union-find, so transitively) and the sharpest of each
   group is kept (ties: the earlier upload).
2. Spread. The film wants about one shot per SECONDS_PER_SHOT seconds of narration
   (`target_count`, clamped to MIN_SHOTS..MAX_SHOTS); `select` may be asked for a few more
   (`candidate_count`, never more than MAX_DESCRIBED) so the editor can leave repeats out.
   Photos are placed on the event's time line (capture times when every photo has one, else
   upload order) and, for each of `count` evenly spaced points across it, the nearest photo
   is taken unless it looks like one already taken; then the next nearest, and so on. When no
   distinct photo is left the film gets fewer photos, never a look-alike.
3. `enforce_distinct` is the last step: it guarantees no two chosen photos look alike.

The result is in chronological order (`chronological`).
"""

from __future__ import annotations

import re
from operator import sub

from film.ingest import LAYOUT_SCALE, Ingested, hamming

DUP_BITS = 10
BURST_BITS = 20
BURST_SECONDS = 5.0
LAYOUT_ALIKE = 0.4
LAYOUT_NEAR = 0.7
TONE_APART = 0.25

SECONDS_PER_SHOT = 5.0
MIN_SHOTS = 6
MAX_SHOTS = 24
MAX_DESCRIBED = 30
"""The most photos ever sent to the vision describer for one film, whatever was uploaded."""
SURPLUS_SHARE = 0.25
MIN_SURPLUS = 2
SECONDS_PER_WORD = 0.4
"""Rough narration pace (about 150 words a minute) used before any speech exists."""
MAX_NARRATION_WORDS = 320
"""The most of the story the film narrates (about two minutes); see plan.trim_story."""


def _gap_seconds(a: Ingested, b: Ingested) -> float | None:
    if a.taken_at is None or b.taken_at is None:
        return None
    return abs((a.taken_at - b.taken_at).total_seconds())


def layout_distance(a: Ingested, b: Ingested) -> float:
    """Mean absolute difference of the colour layouts, in units of the photos' contrast."""
    if not a.layout or len(a.layout) != len(b.layout):
        return float("inf")
    return sum(map(abs, map(sub, a.layout, b.layout))) / (len(a.layout) * LAYOUT_SCALE)


def tone_distance(a: Ingested, b: Ingested) -> float:
    return (abs(a.tone[0] - b.tone[0]) + abs(a.tone[1] - b.tone[1]) + abs(a.tone[2] - b.tone[2])) / 3


def looks_alike(a: Ingested, b: Ingested) -> bool:
    """True when a and b would read as the same picture (see the module docstring)."""
    if tone_distance(a, b) > TONE_APART:
        return False
    bits = hamming(a.dhash, b.dhash)
    gap = _gap_seconds(a, b)
    burst = gap is not None and gap <= BURST_SECONDS
    if burst and bits <= BURST_BITS:
        return True
    layout = layout_distance(a, b)  # the costly comparison, last
    if layout <= LAYOUT_ALIKE:
        return True
    return layout <= LAYOUT_NEAR and (bits <= DUP_BITS or burst)


same_shot = looks_alike
"""The old name of looks_alike."""


def clusters(photos: list[Ingested]) -> list[list[Ingested]]:
    """Groups of look-alikes, each in upload order, ordered by their first member."""
    parent = list(range(len(photos)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(photos)):
        for j in range(i + 1, len(photos)):
            ri, rj = find(i), find(j)
            if ri != rj and looks_alike(photos[i], photos[j]):
                parent[max(ri, rj)] = min(ri, rj)
    groups: dict[int, list[Ingested]] = {}
    for i, photo in enumerate(photos):
        groups.setdefault(find(i), []).append(photo)
    return [groups[k] for k in sorted(groups)]


def best_of(group: list[Ingested]) -> Ingested:
    """The sharpest photo of a cluster; the earlier upload on a tie."""
    return max(group, key=lambda p: (p.sharpness, -p.order))


def chronological(photos: list[Ingested]) -> list[Ingested]:
    """By capture time when every photo has one, else by upload order."""
    if photos and all(p.taken_at is not None for p in photos):
        return sorted(photos, key=lambda p: (p.taken_at, p.order))
    return sorted(photos, key=lambda p: p.order)


def word_count(text: str) -> int:
    return len(re.findall(r"\S+", text))


def target_count(story_text: str) -> int:
    """Shots the film wants: one per SECONDS_PER_SHOT of estimated narration, clamped."""
    words = min(word_count(story_text), MAX_NARRATION_WORDS)
    seconds = words * SECONDS_PER_WORD
    return max(MIN_SHOTS, min(MAX_SHOTS, round(seconds / SECONDS_PER_SHOT)))


def candidate_count(wanted: int) -> int:
    """Photos to describe for a film that wants `wanted` shots: about a quarter more (at least
    MIN_SURPLUS more) so the editor can drop repeats, never more than MAX_DESCRIBED."""
    return min(MAX_DESCRIBED, wanted + max(MIN_SURPLUS, round(wanted * SURPLUS_SHARE)))


def _positions(photos: list[Ingested]) -> list[float]:
    """Each photo's place on the event's time line (seconds, or rank without full EXIF)."""
    if all(p.taken_at is not None for p in photos):
        t0 = min(p.taken_at for p in photos)  # type: ignore[type-var]
        return [(p.taken_at - t0).total_seconds() for p in photos]  # type: ignore[operator]
    return [float(i) for i in range(len(photos))]


def spread(photos: list[Ingested], count: int) -> list[Ingested]:
    """Up to `count` photos spread evenly over the event, no two alike (input chronological).

    For each of `count` evenly spaced points on the time line, the nearest photo not yet taken
    and not alike any taken one (ties: the sharper, the earlier). Fewer than `count` only when
    no distinct photo is left.
    """
    if count <= 0 or not photos:
        return []
    count = min(count, len(photos))
    pos = _positions(photos)
    lo, hi = pos[0], pos[-1]
    chosen: list[int] = []
    for k in range(count):
        target = lo if count == 1 else lo + (hi - lo) * k / (count - 1)
        ranked = sorted(
            (i for i in range(len(photos)) if i not in chosen),
            key=lambda i: (abs(pos[i] - target), -photos[i].sharpness, i),
        )
        pick = next((i for i in ranked if not any(looks_alike(photos[i], photos[c]) for c in chosen)), None)
        if pick is not None:
            chosen.append(pick)
    return [photos[i] for i in sorted(chosen)]


def enforce_distinct(chosen: list[Ingested]) -> list[Ingested]:
    """`chosen` with no two photos alike: the sharpest of any look-alike pair stays (ties: the
    earlier upload). Order is kept."""
    kept: list[Ingested] = []
    for p in sorted(chosen, key=lambda p: (-p.sharpness, p.order)):
        if not any(looks_alike(p, k) for k in kept):
            kept.append(p)
    keep = {p.id for p in kept}
    return [p for p in chosen if p.id in keep]


def select(photos: list[Ingested], story_text: str, count: int | None = None) -> list[Ingested]:
    """The photos the film uses, chronological: one per look-alike group, spread out, no two
    alike. `count` defaults to `target_count(story_text)` and is capped at MAX_DESCRIBED."""
    want = target_count(story_text) if count is None else count
    want = max(0, min(want, MAX_DESCRIBED))
    distinct = [best_of(g) for g in clusters(photos)]
    return enforce_distinct(spread(chronological(distinct), want))


__all__ = [
    "MAX_DESCRIBED",
    "MAX_NARRATION_WORDS",
    "MAX_SHOTS",
    "MIN_SHOTS",
    "best_of",
    "candidate_count",
    "chronological",
    "clusters",
    "enforce_distinct",
    "layout_distance",
    "looks_alike",
    "same_shot",
    "select",
    "spread",
    "target_count",
    "tone_distance",
    "word_count",
]
