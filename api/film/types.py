"""The photo film pipeline's seam: plain data types and protocols, no logic.

The pipeline and the routes are built against this module in parallel. The two entry
points (implemented elsewhere) are:

    film.pipeline.make_film(photos: list[SourcePhoto], story: Story, services: Services) -> FilmCut
        Pure and synchronous: ingest (EXIF time, upright size, sharpness, perceptual hash),
        select (drop near-duplicates and bursts, cap the count), describe, plan beats, then
        narrate and time the shots. Raises FilmError(message) with a message safe to show
        people (stored as Film.error); anything else it raises is a bug.

    film.services.default_services(mode: str) -> Services
        `mode` is the resolved STITCH_AGENT value. "fake" / "off" give offline fakes that
        still make a watchable film; "real" gives the real adapters (vision and TTS), each
        falling back to its fake when its key is missing.

The route (POST /families/{fid}/films, see schemas.CreateFilmRequest) reads each photo
from the media store into a SourcePhoto, builds a Story, runs make_film in the
background, stores FilmCut.audio as media and fills the Film from the cut. Times are
seconds from the start of the film.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

import schemas


class FilmError(Exception):
    """The film can't be made. str(error) is a safe, human message (no keys, no paths)."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True)
class SourcePhoto:
    """One uploaded photo as the media store holds it."""

    id: str  # bare media id
    data: bytes
    content_type: str  # e.g. "image/jpeg"
    filename: str | None = None


@dataclass(frozen=True)
class Story:
    """What the teller wrote, and who was there."""

    text: str
    title: str | None
    year: int
    people: list[str] = field(default_factory=list)  # display names of the participants


class Describer(Protocol):
    def describe(self, photo: bytes) -> dict:
        """Structured facts about one photo (image bytes in), with these keys:

        - "caption": str, a short plain description of what is in it
        - "scene" (optional; may be absent or empty, as from the offline describer): str, a
          short normalised label of the moment shown ("family at dinner table", "kids in the
          lake"). Two photos with the same label taken close together are one moment, and
          the plan leaves the repeat out when it has photos to spare.
        - "people": list[str], who or what kind of people are visible ("a girl in a red coat")
        - "setting": str, where it is ("a lake dock at dusk")
        - "mood": str, one or two words ("quiet", "giddy")
        - "focus": [u, v], floats 0..1 across and down the photo: the point of interest the
          pan/zoom moves toward
        - "quality": float 0..1, how usable it is (sharp, well lit, not a mis-shot)
        """
        ...


class Editor(Protocol):
    def plan(self, story: Story, photos: list[dict]) -> dict | None:
        """Plan the film's beats. `photos` are the chosen photos in capture order, each the
        Describer's dict plus "id" (bare media id) and "takenAt" (ISO time or None).

        Returns {"beats": [{"title": str, "narration": str, "shots": [{"photoId": str,
        "caption": str | None}, ...]}, ...]} where narration is the teller's own words
        (lightly trimmed, first person, nothing invented) and every photoId is one of the
        given photos, each used at most once. There are a few more photos than the film wants
        (film.plan.wanted_shots), so photos may be left out, and a photo that repeats the
        moment of another should be. None means: use the deterministic fallback
        (chronological order, the story's sentences spread over evenly sized beats).
        """
        ...


@dataclass(frozen=True)
class Narration:
    """Spoken narration for the beats' lines, in film time starting at 0."""

    audio: bytes | None  # None = silent; play on a clock
    content_type: str | None  # e.g. "audio/mpeg"; None when audio is None
    line_starts: list[float]  # when each line begins, one per line passed to speak()
    words: list[tuple[float, str]]  # (time, word) for every spoken word, in order
    duration: float  # seconds of narration in all


class Narrator(Protocol):
    def speak(self, lines: list[str]) -> Narration:
        """Voice the lines in order. With no speech service, return silent audio (None)
        and estimated timings."""
        ...


@dataclass(frozen=True)
class Services:
    """The model-backed steps make_film uses. editor None = always the fallback plan."""

    describer: Describer
    editor: Editor | None
    narrator: Narrator


@dataclass(frozen=True)
class FilmCut:
    """What make_film returns: everything a ready Film needs except storage."""

    title: str
    shots: list[schemas.FilmShot]
    beats: list[schemas.FilmBeat]
    words: list[schemas.FilmWord]
    audio: bytes | None
    audio_content_type: str | None
    duration: float
