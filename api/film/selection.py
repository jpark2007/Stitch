"""Select: drop near-duplicates and bursts, then pick a spread of photos across the event.

1. Cluster. Two photos are the same shot when their dHashes are close. How close depends on
   when they were taken: a burst (capture times within BURST_SECONDS) may differ by up to
   BURST_BITS bits, photos with unknown or nearby times by DUP_BITS, and photos taken far
   apart (more than APART_SECONDS) only by APART_BITS, so two different moments in the same
   spot are not merged. Clusters are transitive (union-find).
2. Keep the sharpest photo of each cluster (ties: the earlier upload).
3. Spread. The film wants about one shot per SECONDS_PER_SHOT seconds of narration
   (`target_count`, clamped to MIN_SHOTS..MAX_SHOTS), never more than the distinct photos.
   Photos are placed on the event's time line (capture times when every photo has one, else
   upload order) and one is picked nearest each of `count` evenly spaced points across it,
   so the film covers the whole day rather than its first hour.

The result is in chronological order (`chronological`).
"""

from __future__ import annotations

import re

from film.ingest import Ingested, hamming

DUP_BITS = 10
BURST_BITS = 20
APART_BITS = 6
BURST_SECONDS = 5.0
APART_SECONDS = 600.0

SECONDS_PER_SHOT = 5.0
MIN_SHOTS = 6
MAX_SHOTS = 24
SECONDS_PER_WORD = 0.4
"""Rough narration pace (about 150 words a minute) used before any speech exists."""
MAX_NARRATION_WORDS = 320
"""The most of the story the film narrates (about two minutes); see plan.trim_story."""

MIN_KEEP = 3


def _gap_seconds(a: Ingested, b: Ingested) -> float | None:
    if a.taken_at is None or b.taken_at is None:
        return None
    return abs((a.taken_at - b.taken_at).total_seconds())


def same_shot(a: Ingested, b: Ingested) -> bool:
    """True when b is a near-duplicate of a (see the module docstring for the thresholds)."""
    dist = hamming(a.dhash, b.dhash)
    gap = _gap_seconds(a, b)
    if gap is None:
        return dist <= DUP_BITS
    if gap <= BURST_SECONDS:
        return dist <= BURST_BITS
    if gap > APART_SECONDS:
        return dist <= APART_BITS
    return dist <= DUP_BITS


def clusters(photos: list[Ingested]) -> list[list[Ingested]]:
    """Groups of near-duplicates, each in upload order, ordered by their first member."""
    parent = list(range(len(photos)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(photos)):
        for j in range(i + 1, len(photos)):
            if same_shot(photos[i], photos[j]):
                ri, rj = find(i), find(j)
                if ri != rj:
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


def _positions(photos: list[Ingested]) -> list[float]:
    """Each photo's place on the event's time line (seconds, or rank without full EXIF)."""
    if all(p.taken_at is not None for p in photos):
        t0 = min(p.taken_at for p in photos)  # type: ignore[type-var]
        return [(p.taken_at - t0).total_seconds() for p in photos]  # type: ignore[operator]
    return [float(i) for i in range(len(photos))]


def spread(photos: list[Ingested], count: int) -> list[Ingested]:
    """`count` photos spread evenly over the event (input must be chronological)."""
    if count >= len(photos):
        return list(photos)
    if count <= 0:
        return []
    pos = _positions(photos)
    lo, hi = pos[0], pos[-1]
    chosen: set[int] = set()
    for k in range(count):
        target = lo if count == 1 else lo + (hi - lo) * k / (count - 1)
        best = min(
            (i for i in range(len(photos)) if i not in chosen),
            key=lambda i: (abs(pos[i] - target), -photos[i].sharpness, i),
        )
        chosen.add(best)
    return [photos[i] for i in sorted(chosen)]


def select(photos: list[Ingested], story_text: str) -> list[Ingested]:
    """The photos the film uses, chronological: one per near-duplicate cluster, spread out.

    Keeps at least MIN_KEEP photos when that many were readable, topping up with the
    sharpest leftovers when the uploads were nearly all one shot.
    """
    groups = clusters(photos)
    distinct = [best_of(g) for g in groups]
    if len(distinct) < MIN_KEEP:
        kept = {p.id for p in distinct}
        leftovers = sorted((p for p in photos if p.id not in kept), key=lambda p: (-p.sharpness, p.order))
        distinct += leftovers[: MIN_KEEP - len(distinct)]
    return spread(chronological(distinct), target_count(story_text))


__all__ = [
    "MAX_NARRATION_WORDS",
    "MAX_SHOTS",
    "MIN_SHOTS",
    "best_of",
    "chronological",
    "clusters",
    "same_shot",
    "select",
    "spread",
    "target_count",
    "word_count",
]
