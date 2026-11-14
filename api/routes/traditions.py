"""The Book of Family Traditions (schemas.Tradition).

Routes:
- GET    /families/{fid}/traditions          every tradition, oldest first
- POST   /families/{fid}/traditions          add one (201); createdBy is the caller
- GET    /families/{fid}/traditions/{id}     one tradition
- PATCH  /families/{fid}/traditions/{id}     change some fields (see UpdateTraditionRequest)
- DELETE /families/{fid}/traditions/{id}     remove it (204)

Every route depends on `auth.family_member`: signed out is 401, and a caller who is not in
family `fid` (or a family that does not exist) gets 404. An unknown tradition id and
another family's tradition id are both 404 "Tradition not found", since every read and
write in db/traditions.py is filtered by family.

Bounds are on the request models (422). Like a memory's participants
(routes/memories.py), every participantId must be a person in this family and every
photoId media of this family, else 400 with a fixed sentence, before anything is written.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pymongo.database import Database

import schemas
from auth import FamilyAccess, family_member
from db import media as media_db
from db import people as people_db
from db import traditions as traditions_db
from db.client import get_db

router = APIRouter(tags=["traditions"])

NOT_FOUND = "Tradition not found"
UNKNOWN_PARTICIPANT = "Every participant must be a person in this family"
UNKNOWN_PHOTO = "Every photo must be one of this family's photos"

_ERRORS = {
    400: {"model": schemas.ErrorResponse},
    404: {"model": schemas.ErrorResponse},
}


def _check_refs(db: Database, fid: str, participant_ids: list[str] | None, photo_ids: list[str] | None) -> None:
    """400 unless every participant is a person in family fid and every photo its media."""
    if participant_ids:
        wanted = set(participant_ids)
        found = {p.id for p in people_db.get_people(db, fid, list(wanted))}
        if found != wanted:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=UNKNOWN_PARTICIPANT)
    if photo_ids:
        if not all(media_db.media_exists(db, fid, photo_id) for photo_id in dict.fromkeys(photo_ids)):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=UNKNOWN_PHOTO)


@router.get("/families/{fid}/traditions", response_model=schemas.TraditionsResponse, responses=_ERRORS)
def list_traditions(
    access: FamilyAccess = Depends(family_member),
    db: Database = Depends(get_db),
) -> schemas.TraditionsResponse:
    return schemas.TraditionsResponse(traditions=traditions_db.list_traditions(db, access.family_id))


@router.post(
    "/families/{fid}/traditions",
    status_code=status.HTTP_201_CREATED,
    response_model=schemas.Tradition,
    responses=_ERRORS,
)
def create_tradition(
    body: schemas.CreateTraditionRequest,
    access: FamilyAccess = Depends(family_member),
    db: Database = Depends(get_db),
) -> schemas.Tradition:
    fid = access.family_id
    _check_refs(db, fid, body.participant_ids, body.photo_ids)
    return traditions_db.create_tradition(db, fid, body, created_by=access.user.id)


@router.get("/families/{fid}/traditions/{tradition_id}", response_model=schemas.Tradition, responses=_ERRORS)
def get_tradition(
    tradition_id: str,
    access: FamilyAccess = Depends(family_member),
    db: Database = Depends(get_db),
) -> schemas.Tradition:
    tradition = traditions_db.get_tradition(db, access.family_id, tradition_id)
    if tradition is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NOT_FOUND)
    return tradition


@router.patch("/families/{fid}/traditions/{tradition_id}", response_model=schemas.Tradition, responses=_ERRORS)
def update_tradition(
    tradition_id: str,
    body: schemas.UpdateTraditionRequest,
    access: FamilyAccess = Depends(family_member),
    db: Database = Depends(get_db),
) -> schemas.Tradition:
    fid = access.family_id
    # 404 before 400, so a request naming another family's tradition learns nothing else.
    if traditions_db.get_tradition(db, fid, tradition_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NOT_FOUND)
    _check_refs(db, fid, body.participant_ids, body.photo_ids)
    tradition = traditions_db.update_tradition(db, fid, tradition_id, body)
    if tradition is None:  # deleted in between
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NOT_FOUND)
    return tradition


@router.delete("/families/{fid}/traditions/{tradition_id}", status_code=status.HTTP_204_NO_CONTENT, responses=_ERRORS)
def delete_tradition(
    tradition_id: str,
    access: FamilyAccess = Depends(family_member),
    db: Database = Depends(get_db),
) -> None:
    if not traditions_db.delete_tradition(db, access.family_id, tradition_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NOT_FOUND)
