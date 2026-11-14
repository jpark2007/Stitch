"""The photo film contract: request bounds, camelCase JSON, the sample, the pipeline seam."""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import get_args

import pytest
from pydantic import ValidationError

import schemas
from film import types as film_types
from schemas import CreateFilmRequest, Film, FilmShot, FilmsResponse, FilmSummary

SAMPLE = Path(__file__).resolve().parents[3] / "web" / "public" / "samples" / "film.json"
PHOTOS = [f"m{i}" for i in range(schemas.MIN_FILM_PHOTOS)]
OK = {"text": "We drove to the lake.", "year": 1998, "photoIds": PHOTOS}


def fields_of(exc: ValidationError) -> set[str]:
    return {str(e["loc"][-1]) if not isinstance(e["loc"][-1], int) else str(e["loc"][-2]) for e in exc.errors()}


@pytest.fixture(scope="module")
def sample() -> Film:
    return Film.model_validate(json.loads(SAMPLE.read_text()))


def test_limits_and_enums():
    assert (schemas.MIN_FILM_PHOTOS, schemas.MAX_FILM_PHOTOS) == (8, 40)
    assert get_args(schemas.FilmStatus) == ("processing", "ready", "failed")
    assert get_args(schemas.FilmMotion) == ("in", "out", "left", "right")


def test_create_defaults_trim_and_camel_case():
    req = CreateFilmRequest.model_validate({**OK, "title": "  Lake Wren  ", "text": "  We drove.  "})
    assert req.title == "Lake Wren" and req.text == "We drove."
    assert req.participant_ids == [] and req.photo_ids == PHOTOS
    assert CreateFilmRequest.model_validate(OK).title is None
    dumped = req.model_dump(mode="json")
    assert dumped["photoIds"] == PHOTOS and dumped["participantIds"] == []


@pytest.mark.parametrize(
    ("patch", "field"),
    [
        ({"photoIds": PHOTOS[:-1]}, "photoIds"),
        ({"photoIds": [f"m{i}" for i in range(schemas.MAX_FILM_PHOTOS + 1)]}, "photoIds"),
        ({"text": "   "}, "text"),
        ({"text": "x" * (schemas.MAX_FILM_TEXT_CHARS + 1)}, "text"),
        ({"title": "x" * (schemas.MAX_FILM_TITLE_CHARS + 1)}, "title"),
        ({"year": schemas.MIN_FILM_YEAR - 1}, "year"),
        ({"year": datetime.now(UTC).year + 1}, "year"),
        ({"participantIds": [f"p{i}" for i in range(schemas.MAX_FILM_PEOPLE + 1)]}, "participantIds"),
        ({"video": True}, "video"),
    ],
)
def test_create_rejects_out_of_bounds(patch, field):
    with pytest.raises(ValidationError) as exc:
        CreateFilmRequest.model_validate({**OK, **patch})
    assert field in fields_of(exc.value)


def test_create_requires_text_year_and_photos():
    with pytest.raises(ValidationError) as exc:
        CreateFilmRequest.model_validate({})
    assert fields_of(exc.value) == {"text", "year", "photoIds"}


def test_bounds_are_inclusive():
    CreateFilmRequest.model_validate(
        {
            "title": "x" * schemas.MAX_FILM_TITLE_CHARS,
            "text": "x" * schemas.MAX_FILM_TEXT_CHARS,
            "year": datetime.now(UTC).year,
            "photoIds": ["m"] * schemas.MAX_FILM_PHOTOS,
            "participantIds": ["p"] * schemas.MAX_FILM_PEOPLE,
        }
    )


def test_shot_defaults_and_bounds():
    shot = FilmShot.model_validate({"photoId": "m1", "start": 0, "duration": 4, "beat": 0})
    assert shot.focus == (0.5, 0.5) and shot.motion == "in" and shot.caption is None
    assert shot.model_dump(mode="json")["focus"] == [0.5, 0.5]
    for bad in ({"focus": [1.2, 0.5]}, {"focus": [0.5]}, {"duration": 0}, {"motion": "spin"}, {"beat": -1}):
        with pytest.raises(ValidationError):
            FilmShot.model_validate({"photoId": "m1", "start": 0, "duration": 4, "beat": 0, **bad})


def test_sample_is_a_complete_ready_film(sample):
    assert sample.status == "ready" and sample.error is None and sample.audio is None
    assert len(sample.beats) == 4 and 10 <= len(sample.shots) <= 12
    assert 45 <= sample.duration <= 120
    assert {s.photo_id for s in sample.shots} <= set(sample.photo_ids)
    assert len({s.photo_id for s in sample.shots}) == len(sample.shots)  # no photo twice
    assert all(0 <= s.beat < len(sample.beats) for s in sample.shots)
    # shots tile the film in order, and words are in order inside it
    assert sample.shots[0].start == 0
    for a, b in zip(sample.shots, sample.shots[1:]):
        assert b.start == pytest.approx(a.start + a.duration, abs=0.02)
    assert sample.shots[-1].start + sample.shots[-1].duration == pytest.approx(sample.duration, abs=0.02)
    times = [w.t for w in sample.words]
    assert times == sorted(times) and times[-1] < sample.duration


def test_film_round_trips_camel_case(sample):
    dumped = sample.model_dump(mode="json")
    assert Film.model_validate(dumped) == sample
    assert {"familyId", "authorId", "participantIds", "photoIds", "createdAt"} <= dumped.keys()
    assert dumped["shots"][0].keys() >= {"photoId", "focus", "motion"}
    summary = FilmSummary.model_validate(
        {
            "id": sample.id,
            "title": sample.title,
            "year": sample.year,
            "authorId": sample.author_id,
            "status": "processing",
            "duration": 0,
            "createdAt": "2026-11-14T15:00:00Z",
        }
    )
    resp = FilmsResponse.model_validate({"films": [summary.model_dump(mode="json")]})
    assert resp.films[0].cover_photo_id is None
    assert FilmsResponse.model_validate(resp.model_dump(mode="json")) == resp


def test_film_requires_every_field():
    with pytest.raises(ValidationError) as exc:
        Film.model_validate({})
    assert {"shots", "beats", "words", "audio", "error", "duration", "participantIds"} <= fields_of(exc.value)


def test_pipeline_seam_shapes(sample):
    err = film_types.FilmError("Too few usable photos")
    assert str(err) == err.message == "Too few usable photos"
    cut = film_types.FilmCut(
        title=sample.title,
        shots=sample.shots,
        beats=sample.beats,
        words=sample.words,
        audio=None,
        audio_content_type=None,
        duration=sample.duration,
    )
    assert cut.shots[0].photo_id == "film_lake_01"
    photo = film_types.SourcePhoto(id="m1", data=b"\xff\xd8", content_type="image/jpeg", filename=None)
    story = film_types.Story(text="We drove.", title=None, year=1998, people=["Maya"])
    narration = film_types.Narration(audio=None, content_type=None, line_starts=[0.0], words=[(0.0, "We")], duration=1.0)
    assert (photo.id, story.year, narration.words[0][1]) == ("m1", 1998, "We")
