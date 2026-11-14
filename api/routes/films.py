"""Photo films (schemas.Film): many photos of one event plus the teller's story.

Routes:
- POST /families/{fid}/films             CreateFilmRequest -> 202 Film ("processing"); the
                                         cut is built in the background
- GET  /families/{fid}/films             FilmsResponse, newest first
- GET  /families/{fid}/films/{film_id}   one film; poll until "ready" or "failed"

Every route depends on `auth.family_member`: signed out is 401, and a caller who is not in
family `fid` gets 404. An unknown film id and another family's film id are both 404
"Film not found", since every read in db/films.py is filtered by family.

POST checks, before anything is written: the body's bounds (422, on the request model);
at least MIN_FILM_PHOTOS different photos once repeats collapse (422); the author is the
caller's person (409 NO_PERSON, as routes/memories.py); every participant is a person in
the family (400); every photo id is this family's media of kind "photo" (400, one generic
sentence, so another family's id reads like an unknown one).

The build (`build_film`) runs after the response, off the event loop (FastAPI
BackgroundTasks runs a plain function in the threadpool). It imports film.pipeline and
film.services lazily, so this module imports cleanly without them. The pipeline's
services come from `app.state.film_services` when set (tests, or a caller that wants its
own) - either a film.types.Services or a zero-argument callable returning one - else
film.services.default_services(mode), with mode the resolved STITCH_AGENT that main.py
keeps on `app.state.agent_mode` (routes.memories.AGENT_OFF_STATE); "fake" when unset.
A FilmError fails the film with its own message; anything else is logged and fails it
with BUILD_FAILED. A film left "processing" for 10 minutes reads as failed (db/films.py).
"""

from __future__ import annotations

import importlib
import logging
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from pymongo.database import Database

import schemas
from auth import FamilyAccess, family_member
from db import films as films_db
from db import media as media_db
from db import people as people_db
from db.client import get_db
from film.types import FilmError, Services, SourcePhoto, Story
from routes.memories import AGENT_OFF_STATE, NO_PERSON

log = logging.getLogger(__name__)

router = APIRouter(tags=["films"])

NOT_FOUND = "Film not found"
UNKNOWN_PARTICIPANT = "Every participant must be a person in this family"
UNKNOWN_PHOTO = "Every photo must be one of this family's photos"
TOO_FEW_PHOTOS = (
    f"A film needs {schemas.MIN_FILM_PHOTOS} to {schemas.MAX_FILM_PHOTOS} different photos"
)
BUILD_FAILED = "Something went wrong while making this film. Please try again."

# app.state attribute: a Services, or a zero-argument callable returning one.
SERVICES_STATE = "film_services"

_ERRORS = {
    400: {"model": schemas.ErrorResponse},
    404: {"model": schemas.ErrorResponse},
    409: {"model": schemas.ErrorResponse},
}


# ------------------------------------------------------------------------------ helpers

def _is_family_photo(db: Database, fid: str, media_id: str) -> bool:
    """True when media_id is this family's media of kind "photo". Reads only the file's
    metadata, not its bytes (up to 40 photos per request)."""
    oid = media_db._oid(media_id)  # noqa: SLF001 - metadata-only read; db/media has no public one
    if oid is None:
        return False
    doc = media_db._file_doc(db, fid, oid)  # noqa: SLF001
    return doc is not None and (doc.get("metadata") or {}).get("kind") == "photo"


def _bare_ids(ids: list[str]) -> list[str]:
    """'media/<id>' or '<id>' to bare ids, repeats collapsed. An id that is not a media
    id at all is kept as is, so the photo check rejects it."""
    bare = [media_db.parse_media_ref(i) or i for i in ids]
    return list(dict.fromkeys(bare))


def resolve_services(app: Any) -> Services:
    """app.state.film_services (a Services or a factory), else the default for the mode."""
    hook = getattr(app.state, SERVICES_STATE, None)
    if hook is not None:
        return hook() if callable(hook) and not isinstance(hook, Services) else hook
    mode = getattr(app.state, AGENT_OFF_STATE, None) or "fake"
    return importlib.import_module("film.services").default_services(mode)


# ------------------------------------------------------------------------------- build

def build_film(db: Database, fid: str, film_id: str, services: Services) -> None:
    """Make the film's cut and store it: ready on success, failed otherwise. Never raises."""
    audio_id: str | None = None
    try:
        film = films_db.get_film(db, fid, film_id)
        if film is None:
            return
        photos: list[SourcePhoto] = []
        for photo_id in film.photo_ids:
            found = media_db.get_media(db, fid, photo_id)
            if found is None or found.kind != "photo":
                continue  # deleted since the film was asked for; the pipeline copes or refuses
            photos.append(SourcePhoto(id=photo_id, data=found.data, content_type=found.content_type,
                                      filename=found.filename))
        people = [p.name for p in people_db.get_people(db, fid, film.participant_ids)]
        story = Story(text=film.text, title=films_db.requested_title(db, fid, film_id), year=film.year,
                      people=people)

        make_film = importlib.import_module("film.pipeline").make_film
        cut = make_film(photos, story, services)

        if cut.audio:
            audio_id = media_db.put_media(
                db, fid, cut.audio,
                content_type=cut.audio_content_type or "audio/mpeg",
                filename=f"film-{film_id}-narration",
                kind="audio",
            )
        finished = films_db.finish_film(
            db, fid, film_id,
            title=cut.title,
            shots=cut.shots,
            beats=cut.beats,
            words=cut.words,
            audio=media_db.media_ref(audio_id) if audio_id else None,
            duration=cut.duration,
        )
        if finished is None and audio_id:  # the film went away while it was being made
            media_db.delete_media(db, fid, audio_id)
    except FilmError as exc:
        films_db.fail_film(db, fid, film_id, exc.message)
    except Exception:  # noqa: BLE001 - a build must end in ready or failed, never hang
        log.exception("film %s in family %s could not be made", film_id, fid)
        if audio_id:
            try:
                media_db.delete_media(db, fid, audio_id)
            except Exception:  # noqa: BLE001
                log.exception("film %s: narration audio not cleaned up", film_id)
        try:
            films_db.fail_film(db, fid, film_id, BUILD_FAILED)
        except Exception:  # noqa: BLE001 - it will read as failed once stale
            log.exception("film %s: could not be marked failed", film_id)


def _run_build(app: Any, db: Database, fid: str, film_id: str) -> None:
    """Background entry point: resolve the services, then build. Never raises."""
    try:
        services = resolve_services(app)
    except Exception:  # noqa: BLE001
        log.exception("film %s: pipeline services could not be built", film_id)
        films_db.fail_film(db, fid, film_id, BUILD_FAILED)
        return
    build_film(db, fid, film_id, services)


# ------------------------------------------------------------------------------ routes

@router.post(
    "/families/{fid}/films",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=schemas.Film,
    responses=_ERRORS,
)
def create_film(
    body: schemas.CreateFilmRequest,
    request: Request,
    background: BackgroundTasks,
    access: FamilyAccess = Depends(family_member),
    db: Database = Depends(get_db),
) -> schemas.Film:
    fid = access.family_id
    photo_ids = _bare_ids(body.photo_ids)
    if not schemas.MIN_FILM_PHOTOS <= len(photo_ids) <= schemas.MAX_FILM_PHOTOS:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=TOO_FEW_PHOTOS)

    person = people_db.get_person_for_user(db, fid, access.user.id)
    if person is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=NO_PERSON)
    participant_ids = list(dict.fromkeys(body.participant_ids))
    if participant_ids:
        found = {p.id for p in people_db.get_people(db, fid, participant_ids)}
        if found != set(participant_ids):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=UNKNOWN_PARTICIPANT)
    if not all(_is_family_photo(db, fid, pid) for pid in photo_ids):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=UNKNOWN_PHOTO)

    film = films_db.create_film(
        db, fid,
        author_id=person.id,
        title=body.title,
        text=body.text,
        year=body.year,
        photo_ids=photo_ids,
        participant_ids=participant_ids,
    )
    background.add_task(_run_build, request.app, db, fid, film.id)
    return film


@router.get("/families/{fid}/films", response_model=schemas.FilmsResponse, responses=_ERRORS)
def list_films(
    access: FamilyAccess = Depends(family_member),
    db: Database = Depends(get_db),
) -> schemas.FilmsResponse:
    return schemas.FilmsResponse(films=films_db.list_films(db, access.family_id))


@router.get("/families/{fid}/films/{film_id}", response_model=schemas.Film, responses=_ERRORS)
def get_film(
    film_id: str,
    access: FamilyAccess = Depends(family_member),
    db: Database = Depends(get_db),
) -> schemas.Film:
    film = films_db.get_film(db, access.family_id, film_id)
    if film is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NOT_FOUND)
    return film
