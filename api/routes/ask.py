"""'Ask the family' search and the caller's pending questions (ARCHITECTURE.md section 6).

POST /families/{fid}/ask takes a natural-language question and points at one memory:

1. db.search.rank_memories ranks this family's memories (keywords, vector hits when an
   embedder is installed, and the people the question names, resolved for the caller's
   person: "grandpa and grandma" are the caller's grandparents). Deterministic.
2. `best` is the top candidate (None when nothing matches, with the NO_STORY answer);
   `confident` per db.search.Ranking.confident. `quotes` are the top candidates' best
   sentences and `answer` is composed from them; `mode` is "search".
3. When asking.py has an answerer installed (main.py, from ANTHROPIC_API_KEY), the
   question, the caller, the relatives it named and the top candidates (this family's only)
   go to the model, which picks one candidate and writes the answer. A valid pick replaces
   `best` and `answer`, moves that memory's quotes first, and sets `mode` to "model". Any
   failure keeps step 2's result. `agent/` is never imported.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pymongo.database import Database

import asking
import auth
import schemas
from db import memories as memories_db
from db import people as people_db
from db import search as search_db
from db.client import get_db

router = APIRouter(tags=["ask"])

# A question, not a document: longer queries are 422 before any search or embedding call.
MAX_QUERY_CHARS = 1_000


@router.post("/families/{fid}/ask", response_model=schemas.AskResponse)
def ask_family(
    body: schemas.AskRequest,
    access: auth.FamilyAccess = Depends(auth.family_member),
    db: Database = Depends(get_db),
) -> schemas.AskResponse:
    """Find the memory a question is about: best match, quotes from the tellings, an answer.

    422 on a blank query or one over MAX_QUERY_CHARS (after trimming).
    """
    query = body.query.strip()
    if not query:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Ask a question"
        )
    if len(query) > MAX_QUERY_CHARS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Keep the question to {MAX_QUERY_CHARS:,} characters or fewer",
        )
    fid = access.family_id
    people = people_db.list_people(db, fid)
    names = {person.id: person.name for person in people}
    me = people_db.get_person_for_user(db, fid, access.user.id)
    ranking = search_db.rank_memories(db, fid, query, people=people, asker_id=me.id if me else None)
    quotes = ranking.quotes()
    response = schemas.AskResponse(
        answer=search_db.compose_answer(query, quotes, names),
        quotes=quotes,
        best=ranking.best(),
        mode="search",
    )
    if not ranking.candidates:
        return response
    pick = asking.choose(_context(query, me, ranking, names))
    if pick is None:
        return response
    return schemas.AskResponse(
        answer=pick.answer,
        quotes=ranking.quotes(first=pick.memory_id),
        best=ranking.best(pick.memory_id, confident=pick.confident),
        mode="model",
    )


def _context(
    query: str, me: schemas.Person | None, ranking: search_db.Ranking, names: dict[str, str]
) -> asking.AskContext:
    """What the model sees: only this family's ranked candidates and people."""
    return asking.AskContext(
        question=query,
        asker=me.name if me else None,
        relatives=[
            (m.phrase, sorted(names.get(i, "someone") for i in m.person_ids))
            for m in ranking.mentions if m.relation != "self"
        ],
        candidates=[
            asking.AskCandidate(
                memory_id=c.memory_id,
                title=c.title,
                year=c.year,
                people=[names[i] for i in c.participant_ids if i in names],
                quotes=[(names.get(q.author_id, "someone"), q.text) for q in c.quotes],
            )
            for c in ranking.candidates[: asking.MAX_CANDIDATES]
        ],
    )


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
