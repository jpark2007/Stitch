"""'Ask the family' search and the caller's pending questions (ARCHITECTURE.md section 6).

POST /families/{fid}/ask answers from the family's own tellings: quotes are picked by
db.search (vector search when an embedder is installed, keywords otherwise) and the answer
is composed deterministically from them. No model is called and `agent/` is never imported.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pymongo.database import Database

import auth
import schemas
from db import memories as memories_db
from db import people as people_db
from db import search as search_db
from db.client import get_db

router = APIRouter(tags=["ask"])


@router.post("/families/{fid}/ask", response_model=schemas.AskResponse)
def ask_family(
    body: schemas.AskRequest,
    access: auth.FamilyAccess = Depends(auth.family_member),
    db: Database = Depends(get_db),
) -> schemas.AskResponse:
    """Search this family's memories and answer with quotes from the tellings. 422 on a blank query."""
    query = body.query.strip()
    if not query:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Ask a question"
        )
    fid = access.family_id
    quotes = search_db.ask_quotes(db, fid, query)
    names = {person.id: person.name for person in people_db.list_people(db, fid)}
    answer = search_db.compose_answer(query, quotes, names)
    return schemas.AskResponse(answer=answer, quotes=quotes)


@router.get("/families/{fid}/questions", response_model=schemas.QuestionsResponse)
def list_questions(
    access: auth.FamilyAccess = Depends(auth.family_member),
    db: Database = Depends(get_db),
) -> schemas.QuestionsResponse:
    """Open questions for the caller's person in this family, newest first. [] if they have no person."""
    fid = access.family_id
    person = people_db.get_person_for_user(db, fid, access.user.id)
    if person is None:
        return schemas.QuestionsResponse(questions=[])
    return schemas.QuestionsResponse(questions=memories_db.open_questions(db, fid, person.id))
