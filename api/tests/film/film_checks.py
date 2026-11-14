"""Invariants every film cut must keep, shared by the timing and pipeline tests."""

from __future__ import annotations

import schemas
from film.ingest import Ingested, ingest
from film.selection import looks_alike
from film.timing import MAX_SHOT, MIN_SHOT
from film.types import SourcePhoto

EPS = 1e-6


def alike_pairs(photos: list[Ingested]) -> list[tuple[str, str]]:
    return [(a.id, b.id) for i, a in enumerate(photos) for b in photos[i + 1 :] if looks_alike(a, b)]


def assert_no_look_alikes(photo_ids: list[str], sources: dict[str, bytes]) -> None:
    """No two different photos among `photo_ids` look alike."""
    shown = list(dict.fromkeys(photo_ids))
    photos = ingest([SourcePhoto(pid, sources[pid], "image/jpeg") for pid in shown])
    assert len(photos) == len(shown), "every shown photo is readable"
    assert not alike_pairs(photos), alike_pairs(photos)


def assert_well_cut(
    shots: list[schemas.FilmShot],
    beats: list[schemas.FilmBeat],
    words: list[schemas.FilmWord],
    duration: float,
    photo_ids: set[str],
    sources: dict[str, bytes] | None = None,
) -> None:
    """Every film's invariants. With `sources` (photo id -> the uploaded bytes), also that no
    two shots show photos that look alike (selection.looks_alike)."""
    assert shots, "a film has at least one shot"
    if sources is not None:
        assert_no_look_alikes([s.photo_id for s in shots], sources)
    assert shots[0].start == 0
    for a, b in zip(shots, shots[1:]):
        assert abs(a.start + a.duration - b.start) < EPS, (a, b)  # contiguous, no overlap
        assert a.motion != b.motion, (a, b)
        assert a.beat <= b.beat
    assert abs(shots[-1].start + shots[-1].duration - duration) < EPS
    for s in shots:
        assert MIN_SHOT - EPS <= s.duration <= MAX_SHOT + EPS, s
        assert s.photo_id in photo_ids
        assert 0 <= s.beat < len(beats)
    for k, beat in enumerate(beats):
        first = next(s for s in shots if s.beat == k)  # every beat has a shot
        assert abs(first.start - beat.start) < EPS
        assert beat.narration.strip() and beat.title.strip()
    times = [w.t for w in words]
    assert times == sorted(times)
    assert all(0 <= t <= duration for t in times)
    # Every piece is a valid contract object.
    for model, items in ((schemas.FilmShot, shots), (schemas.FilmBeat, beats), (schemas.FilmWord, words)):
        for item in items:
            assert model.model_validate(item.model_dump(mode="json", by_alias=True)) == item
