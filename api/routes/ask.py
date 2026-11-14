"""'Ask the family' search and the caller's pending questions (ARCHITECTURE.md section 6).

POST /families/{fid}/ask takes a natural-language question and points at one memory:

1. db.search.rank_memories ranks this family's memories (keywords, vector hits when an
   embedder is installed, and the people the question names, resolved for the caller's
   person: "grandpa and grandma" are the caller's grandparents). Deterministic.
2. `best` is the top candidate (None when nothing matches, with the NO_STORY answer);
   `confident` per db.search.Ranking.confident. `quotes` are the top candidates' best
   sentences (best first). `answer` is db.search.best_answer: a short paragraph about the
   best memory only (title, year, who tells it, one or two of its sentences), phrased as
   "the closest page" when not confident; `mode` is "search".
3. When asking.py has an answerer installed (main.py, from ANTHROPIC_API_KEY), the
   question, the caller, the relatives it may refer to (db.search.describe_mentions: as
   possibilities when a word like "grandpa" matches more people than it names) and the top
   candidates (this family's only) go to the model, which picks one candidate and writes the
   answer. A valid pick replaces `best` and `answer`, moves that memory's quotes first, and
   sets `mode` to "model". A valid reply that picks nothing (asking.NothingRelates) gives
   NO_STORY with no quotes and no best, `mode` "model", unless step 2 was confident (then
   step 2's result stands). Any failure, timeout or invalid output keeps step 2's result.
   `agent/` is never imported.
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
        answer=search_db.best_answer(
            query, ranking.candidates[0] if ranking.candidates else None, ranking.confident(), names
        ),
        quotes=quotes,
        best=ranking.best(),
        mode="search",
    )
    if not ranking.candidates:
        return response
    pick = asking.choose(_context(query, me, ranking, people))
    if pick is None:
        return response
    if isinstance(pick, asking.NothingRelates):
        if ranking.outweighs_a_null_pick():
            return response  # confident, or the top names the asked-about people or matches fully
        # The model read every candidate and found none about the question. Quotes go too:
        # they come only from those rejected candidates, and under "no one has told that
        # story" an "Also mentioned in" list would read as an answer.
        return schemas.AskResponse(answer=search_db.NO_STORY, quotes=[], best=None, mode="model")
    return schemas.AskResponse(
        answer=pick.answer,
        quotes=ranking.quotes(first=pick.memory_id),
        best=ranking.best(pick.memory_id, confident=pick.confident),
        mode="model",
    )


def _context(
    query: str, me: schemas.Person | None, ranking: search_db.Ranking, people: list[schemas.Person]
) -> asking.AskContext:
    """What the model sees: only this family's ranked candidates and people."""
    names = {person.id: person.name for person in people}
    return asking.AskContext(
        question=query,
        asker=me.name if me else None,
        relatives=[
            asking.Relative(group.phrase, _couples_together(group.person_ids, people), group.exact)
            for group in search_db.describe_mentions(query, ranking.mentions)
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


def _couples_together(ids: frozenset[str], people: list[schemas.Person]) -> list[str]:
    """Names of ids, alphabetical, each followed by their spouses among ids: "Carmen, Luis, Joseph, Rosa"."""
    by_id = {p.id: p for p in people if p.id in ids}
    spouses: dict[str, set[str]] = {pid: set() for pid in by_id}
    for p in by_id.values():
        for s in p.spouse_ids:
            if s in by_id and s != p.id:
                spouses[p.id].add(s)
                spouses[s].add(p.id)
    order: list[str] = []
    for pid in sorted(by_id, key=lambda i: (by_id[i].name, i)):
        if pid not in order:
            order.append(pid)
            order += sorted((s for s in spouses[pid] if s not in order), key=lambda i: (by_id[i].name, i))
    return [by_id[pid].name for pid in order] + ["someone"] * (len(ids) - len(by_id))


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
