"""Photo films: many photos of one event plus the teller's story, cut into a short film.

Collection `films`: {_id, familyId, authorId, title, requestedTitle, year, text,
participantIds, status, error, photoIds, shots, beats, words, audio, duration, createdAt,
updatedAt}. `shots`, `beats` and `words` hold the contract models as camelCase JSON.
`requestedTitle` is what the teller typed (None when they left it blank); `title` is what
the film shows: a placeholder while it is processing, then the cut's title.

Every function takes the database first and the family id second, and every filter
includes `familyId`, so another family's film id reads exactly like an unknown one (None),
never as an error. Bounds and trimming live on schemas.CreateFilmRequest; whether the
photos and participants belong to the family is checked by the route (routes/films.py).

A film still "processing" STALE_AFTER after it was created is reported as "failed" with
STALE_ERROR when read (the build died with the process). Nothing is written for that.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from bson import ObjectId
from pymongo import DESCENDING
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError

import schemas

COLLECTION = "films"

STALE_AFTER = timedelta(minutes=10)
STALE_ERROR = "This film took too long to make. Please try again."


# ------------------------------------------------------------------------------ helpers

def _new_id() -> str:
    return str(ObjectId())


def _now(now: datetime | None = None) -> datetime:
    """Aware UTC, truncated to the millisecond a BSON date keeps (as db/traditions.py)."""
    value = now if now is not None else datetime.now(UTC)
    value = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    return value.replace(microsecond=value.microsecond // 1000 * 1000)


def _utc(value: datetime) -> datetime:
    """A naive datetime (a client without tz_aware) is UTC, as pymongo stores it."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def _dedupe(ids: list[str]) -> list[str]:
    return list(dict.fromkeys(ids))


def placeholder_title(year: int) -> str:
    """The title a film without one shows while it is being made."""
    return f"A film from {year}"


def _status(doc: dict[str, Any], now: datetime | None) -> tuple[str, str | None]:
    status = doc["status"]
    error = doc.get("error")
    if status == "processing" and _utc(doc["createdAt"]) + STALE_AFTER < _now(now):
        return "failed", STALE_ERROR
    return status, error


def _to_film(doc: dict[str, Any] | None, now: datetime | None = None) -> schemas.Film | None:
    if doc is None:
        return None
    status, error = _status(doc, now)
    return schemas.Film.model_validate(
        {
            "id": doc["_id"],
            "familyId": doc["familyId"],
            "authorId": doc["authorId"],
            "title": doc["title"],
            "year": doc["year"],
            "text": doc["text"],
            "participantIds": list(doc.get("participantIds") or []),
            "status": status,
            "error": error,
            "photoIds": list(doc.get("photoIds") or []),
            "shots": list(doc.get("shots") or []),
            "beats": list(doc.get("beats") or []),
            "words": list(doc.get("words") or []),
            "audio": doc.get("audio"),
            "duration": doc.get("duration") or 0,
            "createdAt": _utc(doc["createdAt"]),
        }
    )


def _cover(doc: dict[str, Any]) -> str | None:
    shots = doc.get("shots") or []
    if shots:
        return shots[0].get("photoId")
    photos = doc.get("photoIds") or []
    return photos[0] if photos else None


def _to_summary(doc: dict[str, Any], now: datetime | None = None) -> schemas.FilmSummary:
    status, _ = _status(doc, now)
    return schemas.FilmSummary(
        id=doc["_id"],
        title=doc["title"],
        year=doc["year"],
        author_id=doc["authorId"],
        status=status,
        duration=doc.get("duration") or 0,
        cover_photo_id=_cover(doc),
        created_at=_utc(doc["createdAt"]),
    )


def _dump(models: list[Any]) -> list[dict[str, Any]]:
    return [m.model_dump(mode="json", by_alias=True) for m in models]


# ------------------------------------------------------------------------------- writes

def create_film(
    db: Database,
    family_id: str,
    *,
    author_id: str,
    text: str,
    year: int,
    photo_ids: list[str],
    title: str | None = None,
    participant_ids: list[str] | None = None,
    film_id: str | None = None,
    now: datetime | None = None,
) -> schemas.Film:
    """Insert a film in status "processing" with an empty cut. Repeated photo and
    participant ids collapse, first one kept. A blank or missing title shows
    placeholder_title(year) until the cut names it.

    ValueError when `film_id` is given and already taken.
    """
    requested = (title or "").strip() or None
    created = _now(now)
    doc: dict[str, Any] = {
        "_id": film_id or _new_id(),
        "familyId": family_id,
        "authorId": author_id,
        "title": requested or placeholder_title(year),
        "requestedTitle": requested,
        "year": year,
        "text": text,
        "participantIds": _dedupe(participant_ids or []),
        "status": "processing",
        "error": None,
        "photoIds": _dedupe(photo_ids),
        "shots": [],
        "beats": [],
        "words": [],
        "audio": None,
        "duration": 0.0,
        "createdAt": created,
        "updatedAt": created,
    }
    try:
        db[COLLECTION].insert_one(doc)
    except DuplicateKeyError as exc:
        raise ValueError(f"film id already exists: {doc['_id']}") from exc
    film = _to_film(doc, created)
    assert film is not None
    return film


def finish_film(
    db: Database,
    family_id: str,
    film_id: str,
    *,
    title: str,
    shots: list[schemas.FilmShot],
    beats: list[schemas.FilmBeat],
    words: list[schemas.FilmWord],
    audio: str | None,
    duration: float,
    now: datetime | None = None,
) -> schemas.Film | None:
    """Store the cut and mark the film "ready". `audio` is a 'media/<id>' ref or None
    (silent). A teller's own title is kept; a blank `title` keeps the current one.
    None when the film does not exist in this family."""
    doc = db[COLLECTION].find_one({"_id": film_id, "familyId": family_id}, {"requestedTitle": 1, "title": 1})
    if doc is None:
        return None
    shown = doc.get("requestedTitle") or (title or "").strip() or doc["title"]
    changes = {
        "title": shown,
        "status": "ready",
        "error": None,
        "shots": _dump(shots),
        "beats": _dump(beats),
        "words": _dump(words),
        "audio": audio,
        "duration": float(duration),
        "updatedAt": _now(now),
    }
    db[COLLECTION].update_one({"_id": film_id, "familyId": family_id}, {"$set": changes})
    return get_film(db, family_id, film_id, now=now)


def fail_film(
    db: Database,
    family_id: str,
    film_id: str,
    message: str,
    *,
    now: datetime | None = None,
) -> schemas.Film | None:
    """Mark the film "failed" with `message`, which people see (keep it safe and plain).
    None when the film does not exist in this family."""
    result = db[COLLECTION].update_one(
        {"_id": film_id, "familyId": family_id},
        {"$set": {"status": "failed", "error": message, "updatedAt": _now(now)}},
    )
    if result.matched_count == 0:
        return None
    return get_film(db, family_id, film_id, now=now)


# -------------------------------------------------------------------------------- reads

def get_film(db: Database, family_id: str, film_id: str, *, now: datetime | None = None) -> schemas.Film | None:
    return _to_film(db[COLLECTION].find_one({"_id": film_id, "familyId": family_id}), now)


def requested_title(db: Database, family_id: str, film_id: str) -> str | None:
    """The title the teller typed, or None when they left it blank (or the film is unknown)."""
    doc = db[COLLECTION].find_one({"_id": film_id, "familyId": family_id}, {"requestedTitle": 1})
    return (doc or {}).get("requestedTitle")


def list_films(db: Database, family_id: str, *, now: datetime | None = None) -> list[schemas.FilmSummary]:
    """Every film in the family, newest first (createdAt, then id, both descending).
    cover_photo_id is the first shot's photo, else the first uploaded photo."""
    cursor = db[COLLECTION].find(
        {"familyId": family_id},
        {"shots": {"$slice": 1}, "words": 0, "beats": 0, "text": 0},
    ).sort([("createdAt", DESCENDING), ("_id", DESCENDING)])
    return [_to_summary(doc, now) for doc in cursor]
