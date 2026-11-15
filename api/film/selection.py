"""Select: pick a distinct spread of photos across the event, never two that look alike.

A finished film never shows two photos that look alike. One rule decides it, `looks_alike`,
over the signatures film.ingest computes once per photo (dHash, colour layout and tone, and
the structure thumbnail). Two photos look alike when, in this order:

1. their dHashes differ by at most SAME_BITS (2) bits and neither is flat: the same picture
   (a black and white copy of a colour photo lands here) -> alike;
2. both are in colour and their tones differ by more than TONE_APART (0.25) -> not alike;
3. they are a burst (capture times within BURST_SECONDS, 5 s) and their dHashes differ by
   at most BURST_BITS (20) bits -> alike (the subject moves between frames of a burst);
4. both are in colour and their coarse colour layouts differ by more than LAYOUT_FAR (1.0,
   in units of each photo's own contrast; copies measure up to 0.56) -> not alike: a cheap
   test that only rules out clearly different photos, so most pairs never reach step 5;
5. otherwise the structure decides (`changed_cells`): the two 48x36 thumbnails are lined up
   over STRUCTURE_TRANSFORMS (the same frame; shifted 4% either way; or cropped 4% or 8%
   from either side, the top, the bottom or all round, either photo being the crop), and a
   cell has changed when it falls outside the other photo's 3x3 neighbourhood range by more
   than LUMA_STEP (24, about 0.75 of the photo's brightness spread) or CHROMA_STEP (80) in
   colour, counted away from a 2-cell border. Under the best alignment, at most
   MAX_CHANGED_CELLS (5) changed cells -> alike; or at most CLOSE_CHANGED_CELLS (15) when
   the dHashes differ by at most CLOSE_BITS (6), as for a much brighter copy whose sky
   burnt out.

The separating fact: a copy (re-encoded, resized, brighter, darker, more or less contrasty or
saturated, warmer, rotated a degree or two, cropped or shifted up to 8%) differs a little
everywhere and, lined up, in 0 to a few cells; a different moment in the same place differs a
lot where its people are. On the test generators, copies changed 0 cells except a few hard
combinations (up to 13); different moments in one park changed 7 cells and up.

Capture times only ever loosen the rule (bursts). They never make two look-alikes distinct:
EXIF times go missing or are wrong (a re-saved copy, a camera set to the wrong day).

Selection never groups photos transitively (A like B and B like C would chain a whole album
of one park into one photo). It refuses pairwise:

1. Spread. The film wants about one shot per SECONDS_PER_SHOT seconds of narration
   (`target_count`, clamped to MIN_SHOTS..MAX_SHOTS); `select` may be asked for a few more
   (`candidate_count`, never more than MAX_DESCRIBED) so the editor can leave repeats out.
   Photos are placed on the event's time line (capture times when every photo has one, else
   upload order). For each of `count` evenly spaced points on it, the nearest photo not alike
   any photo already taken is taken (ties: the sharper, the earlier); a photo refused once is
   never tried again. Of a taken photo and its look-alikes among its SHARPER_NEIGHBOURS
   nearest photos on either side that still sit within half a slot of its point, the
   sharpest is kept. Each photo is compared with at most the photos already taken and those
   few neighbours, so 1000 photos cost a few thousand comparisons. When no distinct photo is left the
   film gets fewer photos, never a look-alike.
2. `enforce_distinct` is the last step: it guarantees no two chosen photos look alike.

The result is in chronological order (`chronological`).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from operator import sub

from PIL import Image, ImageChops

from film.ingest import LAYOUT_SCALE, STRUCTURE_SIZE, Ingested, Structure, hamming

SAME_BITS = 2
BURST_BITS = 20
BURST_SECONDS = 5.0
TONE_APART = 0.25
LAYOUT_FAR = 1.0
MIN_DETAIL = 4.0
"""A photo whose brightness spread is below this is flat (its dHash is noise)."""
NEUTRAL_TONE = 0.03
"""A photo whose tone is this close to (1, 1, 1) is black and white: its colour is not compared."""

LUMA_STEP = 24
CHROMA_STEP = 80
MAX_CHANGED_CELLS = 5
CLOSE_BITS = 6
CLOSE_CHANGED_CELLS = 15
"""When the dHashes agree this closely, this many changed cells are still one picture (a much
brighter copy whose sky has burnt out)."""
STRUCTURE_MARGIN = 2
SHARPER_NEIGHBOURS = 6


def _transforms() -> tuple[tuple[float, float, float, float], ...]:
    """(scale x, scale y, offset x, offset y) as fractions of the frame: the ways one photo can
    be a re-framing of another. Each is tried with either photo as the re-framed one."""
    out = [(1.0, 1.0, 0.0, 0.0)]
    for shift in (-0.04, 0.04):
        out += [(1.0, 1.0, shift, 0.0), (1.0, 1.0, 0.0, shift)]
    scales = [(s, s) for s in (0.96, 0.92)] + [(s, 1.0) for s in (0.96, 0.92)] + [(1.0, s) for s in (0.96, 0.92)]
    for sx, sy in scales:
        for ox in (0.0,) if sx == 1.0 else (0.0, (1 - sx) / 2, 1 - sx):
            for oy in (0.0,) if sy == 1.0 else (0.0, (1 - sy) / 2, 1 - sy):
                out.append((sx, sy, ox, oy))
    return tuple(out)


STRUCTURE_TRANSFORMS = _transforms()

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

_W, _H = STRUCTURE_SIZE
_INSIDE = (STRUCTURE_MARGIN, STRUCTURE_MARGIN, _W - STRUCTURE_MARGIN, _H - STRUCTURE_MARGIN)
_CHROMA_ONTO_LUMA = [min(255, v * LUMA_STEP // CHROMA_STEP) for v in range(256)]


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


def _warp(image: Image.Image, t: tuple[float, float, float, float]) -> Image.Image:
    if t == (1.0, 1.0, 0.0, 0.0):
        return image
    sx, sy, ox, oy = t
    return image.transform(
        STRUCTURE_SIZE, Image.Transform.AFFINE, (sx, 0, ox * _W, 0, sy, oy * _H), Image.Resampling.BILINEAR
    )


def _changed(x: Image.Image, y: Structure, colour: bool) -> int:
    """Cells (inside the border) where `x` falls outside `y`'s 3x3 neighbourhood range."""
    off = ImageChops.lighter(ImageChops.subtract(x, y.high), ImageChops.subtract(y.low, x))
    luma, cb, cr = off.split()
    if colour:
        luma = ImageChops.lighter(luma, ImageChops.lighter(cb, cr).point(_CHROMA_ONTO_LUMA))
    return sum(luma.crop(_INSIDE).histogram()[LUMA_STEP:])


def changed_cells(
    a: Ingested, b: Ingested, enough: int = -1, transforms: tuple[tuple[float, float, float, float], ...] = STRUCTURE_TRANSFORMS
) -> int:
    """The fewest cells that differ between a and b under any of `transforms` (either photo
    re-framed); stops early once it finds `enough` or fewer."""
    sa, sb = a.structure, b.structure
    if sa is None or sb is None:
        return _W * _H
    colour = sa.colourful and sb.colourful
    best = _W * _H
    for t in transforms:
        for x, y in ((sa, sb), (sb, sa)):
            best = min(best, _changed(_warp(x.image, t), y, colour))
            if best <= enough:
                return best
    return best


def structure_alike(a: Ingested, b: Ingested) -> bool:
    return changed_cells(a, b, MAX_CHANGED_CELLS) <= MAX_CHANGED_CELLS


def _flat(p: Ingested) -> bool:
    return p.structure is not None and p.structure.detail < MIN_DETAIL


def _neutral(p: Ingested) -> bool:
    """A black and white photo: every channel's mean within NEUTRAL_TONE of the overall mean."""
    return max(abs(t - 1.0) for t in p.tone) < NEUTRAL_TONE


def looks_alike(a: Ingested, b: Ingested) -> bool:
    """True when a and b would read as the same picture (see the module docstring)."""
    bits = hamming(a.dhash, b.dhash)
    if bits <= SAME_BITS and not (_flat(a) or _flat(b)):
        return True
    colour = not (_neutral(a) or _neutral(b))
    if colour and tone_distance(a, b) > TONE_APART:
        return False
    gap = _gap_seconds(a, b)
    if gap is not None and gap <= BURST_SECONDS and bits <= BURST_BITS:
        return True
    if colour and layout_distance(a, b) > LAYOUT_FAR:
        return False
    if bits <= CLOSE_BITS:
        return changed_cells(a, b, CLOSE_CHANGED_CELLS) <= CLOSE_CHANGED_CELLS
    return structure_alike(a, b)


same_shot = looks_alike
"""The old name of looks_alike."""

Alike = Callable[[Ingested, Ingested], bool]


def remembering(alike: Alike = looks_alike) -> Alike:
    """`alike` with its answers remembered by photo id pair (for one selection)."""
    seen: dict[tuple[str, str], bool] = {}

    def check(a: Ingested, b: Ingested) -> bool:
        key = (a.id, b.id) if a.id <= b.id else (b.id, a.id)
        if key not in seen:
            seen[key] = alike(a, b)
        return seen[key]

    return check


def clusters(photos: list[Ingested]) -> list[list[Ingested]]:
    """Groups of look-alikes (transitive), each in upload order, ordered by their first member.
    A diagnostic: selection does not use it, since chains of look-alikes are not copies."""
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
    """The sharpest photo of a group; the earlier upload on a tie."""
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


def spread(photos: list[Ingested], count: int, alike: Alike = looks_alike) -> list[Ingested]:
    """Up to `count` photos spread evenly over the event, no two alike (input chronological).

    See the module docstring. Fewer than `count` only when no distinct photo is left.
    """
    if count <= 0 or not photos:
        return []
    count = min(count, len(photos))
    pos = _positions(photos)
    lo, hi = pos[0], pos[-1]
    slot = (hi - lo) / (count - 1) if count > 1 else hi - lo
    chosen: list[int] = []
    out: set[int] = set()  # chosen, or refused as alike a chosen photo

    def free(i: int) -> bool:
        if i in out:
            return False
        if any(alike(photos[i], photos[c]) for c in chosen):
            out.add(i)
            return False
        return True

    for k in range(count):
        target = lo if count == 1 else lo + (hi - lo) * k / (count - 1)
        ranked = sorted(
            (i for i in range(len(photos)) if i not in out),
            key=lambda i: (abs(pos[i] - target), -photos[i].sharpness, i),
        )
        pick = next((i for i in ranked if free(i)), None)
        if pick is None:
            break
        # A sharper look-alike nearby takes its place, if that keeps the slot covered.
        reach = max(abs(pos[pick] - target), slot / 2)
        near = range(max(0, pick - SHARPER_NEIGHBOURS), min(len(photos), pick + SHARPER_NEIGHBOURS + 1))
        sharper = [
            j
            for j in near
            if photos[j].sharpness > photos[pick].sharpness
            and abs(pos[j] - target) <= reach
            and j not in out
            and alike(photos[pick], photos[j])
            and free(j)
        ]
        if sharper:
            pick = max(sharper, key=lambda j: (photos[j].sharpness, -j))
        chosen.append(pick)
        out.add(pick)
    return [photos[i] for i in sorted(chosen)]


def enforce_distinct(chosen: list[Ingested], alike: Alike = looks_alike) -> list[Ingested]:
    """`chosen` with no two photos alike: the sharpest of any look-alike pair stays (ties: the
    earlier upload). Order is kept."""
    kept: list[Ingested] = []
    for p in sorted(chosen, key=lambda p: (-p.sharpness, p.order)):
        if not any(alike(p, k) for k in kept):
            kept.append(p)
    keep = {p.id for p in kept}
    return [p for p in chosen if p.id in keep]


def select(photos: list[Ingested], story_text: str, count: int | None = None) -> list[Ingested]:
    """The photos the film uses, chronological, spread out, no two alike. `count` defaults to
    `target_count(story_text)` and is capped at MAX_DESCRIBED."""
    want = target_count(story_text) if count is None else count
    want = max(0, min(want, MAX_DESCRIBED))
    alike = remembering()
    return enforce_distinct(spread(chronological(photos), want, alike), alike)


__all__ = [
    "MAX_DESCRIBED",
    "MAX_NARRATION_WORDS",
    "MAX_SHOTS",
    "MIN_SHOTS",
    "STRUCTURE_TRANSFORMS",
    "best_of",
    "candidate_count",
    "changed_cells",
    "chronological",
    "clusters",
    "enforce_distinct",
    "layout_distance",
    "looks_alike",
    "remembering",
    "same_shot",
    "select",
    "spread",
    "structure_alike",
    "target_count",
    "tone_distance",
    "word_count",
]
