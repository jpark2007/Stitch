"""make_film: photos plus the teller's story in, a timed film cut out (see film.types).

ingest -> select -> describe -> plan -> narrate -> time. Every model-backed step degrades
instead of failing: a describer error gives that photo the offline description, an editor
error the fallback plan, a narrator error (or a narration that does not fit its lines) a
silent narration with estimated timings. Only what no fallback can fix raises FilmError: an
empty story, or fewer than MIN_USABLE_PHOTOS photos Pillow can read.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor

from film.describe import FakeDescriber, normalize
from film.ingest import Ingested, ingest
from film.narrate import silent_narration
from film.plan import make_plan, sentences, title_from
from film.selection import select
from film.timing import time_film
from film.types import Describer, FilmCut, FilmError, Narration, Narrator, Services, SourcePhoto, Story

log = logging.getLogger(__name__)

MIN_USABLE_PHOTOS = 3
DESCRIBE_WORKERS = 4
"""Photos described at once (one vision request each)."""
TITLE_WORDS = 6


def _describe(describer: Describer, photo: Ingested) -> dict:
    offline = FakeDescriber().describe(photo.data)
    try:
        raw = describer.describe(photo.data)
    except Exception as exc:  # noqa: BLE001 - one photo's description is never worth the film
        log.warning("film: describing %s failed (%s); using the offline description", photo.id, type(exc).__name__)
        return offline
    if not isinstance(raw, dict):
        return offline
    return normalize(raw, offline)


def _describe_all(describer: Describer, photos: list[Ingested]) -> dict[str, dict]:
    with ThreadPoolExecutor(max_workers=DESCRIBE_WORKERS) as pool:
        found = list(pool.map(lambda p: _describe(describer, p), photos))
    return {p.id: d for p, d in zip(photos, found, strict=True)}


def _narrate(narrator: Narrator, lines: list[str]) -> Narration:
    try:
        return narrator.speak(lines)
    except Exception as exc:  # noqa: BLE001 - narration is an outside service; go silent
        log.warning("film: narration failed (%s); the film is silent", type(exc).__name__)
        return silent_narration(lines)


def film_title(story: Story) -> str:
    if story.title and story.title.strip():
        return " ".join(story.title.split())
    opening = sentences(story.text)
    return title_from(opening[0], TITLE_WORDS) if opening else "A family film"


def make_film(photos: list[SourcePhoto], story: Story, services: Services) -> FilmCut:
    """The film for these photos and this story. Raises FilmError with a safe message."""
    if not story.text or not story.text.split():
        raise FilmError("The story is empty. Write a few sentences about the day and try again.")
    readable = ingest(photos)
    if len(readable) < MIN_USABLE_PHOTOS:
        raise FilmError(
            f"Stitch could only open {len(readable)} of the {len(photos)} photos, and a film needs at "
            f"least {MIN_USABLE_PHOTOS}. Try JPEG or PNG photos."
        )
    chosen = select(readable, story.text)
    descriptions = _describe_all(services.describer, chosen)
    photo_facts = [
        {**descriptions[p.id], "id": p.id, "takenAt": p.taken_at.isoformat() if p.taken_at else None} for p in chosen
    ]
    beats = make_plan(story, photo_facts, services.editor)
    lines = [b.narration for b in beats]
    narration = _narrate(services.narrator, lines)
    try:
        timed = time_film(beats, narration, descriptions)
    except ValueError as exc:
        log.warning("film: the narration did not fit its lines (%s); timing it silently", exc)
        narration = silent_narration(lines)
        timed = time_film(beats, narration, descriptions)
    return FilmCut(
        title=film_title(story),
        shots=timed.shots,
        beats=timed.beats,
        words=timed.words,
        audio=narration.audio,
        audio_content_type=narration.content_type if narration.audio is not None else None,
        duration=timed.duration,
    )


__all__ = ["MIN_USABLE_PHOTOS", "film_title", "make_film"]
