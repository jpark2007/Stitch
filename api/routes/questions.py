"""Ask a person in the family a question (schemas.CreateQuestionRequest).

Route:
- POST /families/{fid}/questions   201 schemas.Question

The question is stored as an open question for that person (db.memories.create_question),
so it shows in GET /families/{fid}/questions when that person's member asks, and in the
weekly prompt (db.memories.open_questions) that texts them.

Depends on `auth.family_member`: signed out is 401, and a caller who is not in family `fid`
(or a family that does not exist) gets 404. Any member may ask anyone in the family. An
unknown person id and another family's person id are both 404 "Person not found". Text
bounds (1-300 characters after trimming) are on the request model (422).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pymongo.database import Database

import schemas
from auth import FamilyAccess, family_member
from db import memories as memories_db
from db import people as people_db
from db.client import get_db

router = APIRouter(tags=["questions"])

PERSON_NOT_FOUND = "Person not found"

_ERRORS = {404: {"model": schemas.ErrorResponse}}


@router.post(
    "/families/{fid}/questions",
    status_code=status.HTTP_201_CREATED,
    response_model=schemas.Question,
    responses=_ERRORS,
)
def create_question(
    body: schemas.CreateQuestionRequest,
    access: FamilyAccess = Depends(family_member),
    db: Database = Depends(get_db),
) -> schemas.Question:
    fid = access.family_id
    if people_db.get_person(db, fid, body.person_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=PERSON_NOT_FOUND)
    return memories_db.create_question(db, fid, person_id=body.person_id, text=body.text)
