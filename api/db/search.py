"""'Ask the family': memory search with quotes from the tellings (ARCHITECTURE.md section 5).

A keyword search over titles and tellings always runs. When a query vector is available
and the server can run Atlas Vector Search over `memories.embedding` (index VECTOR_INDEX,
`familyId` pre-filter), its hits are merged with the keyword matches (_merge_memory_hits):
keyword matches first, then vector-only hits scoring at least VECTOR_MIN_SCORE. When there
is no vector, or the server cannot run `$vectorSearch` (plain MongoDB raises
OperationFailure, mongomock raises NotImplementedError), keywords alone answer, so search
works offline and on plain MongoDB.

No network by default: `embed` returns None until something calls `set_embedder`.
Answers are composed deterministically from quotes; no model is involved.
"""

from __future__ import annotations

import logging
import math
import os
import re
from collections.abc import Callable
from dataclasses import dataclass, field

from pymongo.database import Database
from pymongo.errors import OperationFailure

import schemas

log = logging.getLogger(__name__)

VECTOR_INDEX = "memories_vector"
MEMORIES = "memories"
TITLE_WEIGHT = 2.0
NO_STORY = "No one in the family has told that story yet."


def _env_float(name: str, default: float) -> float:
    """A finite float from the environment, or default when unset or unparsable."""
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw)
    except ValueError:
        value = math.nan
    if not math.isfinite(value):
        log.warning("ignoring %s=%r; using %s", name, raw, default)
        return default
    return value


# The least vector score a hit needs to be returned when it shares no word with the query.
# Atlas reports cosine similarity as vectorSearchScore = (1 + cos) / 2, in [0, 1], so 0.8
# means cos >= 0.6. A nearest neighbour exists even for an unrelated question; this floor
# is what still lets such a question get NO_STORY. Override with STITCH_VECTOR_MIN_SCORE.
VECTOR_MIN_SCORE = _env_float("STITCH_VECTOR_MIN_SCORE", 0.8)

Embedder = Callable[[str], list[float]]

_embedder: Embedder | None = None

# Fields never needed for search results: the scene and the vector itself are large.
_HEAVY_FIELDS = {"screenplay": 0, "timeline": 0, "embedding": 0}


# ------------------------------------------------------------------ embeddings

def set_embedder(fn: Embedder | None) -> None:
    """Install (or with None, remove) the function that turns text into a vector."""
    global _embedder
    _embedder = fn


def embed(text: str) -> list[float] | None:
    """The query vector, or None when no embedder is set or the embedder fails."""
    fn = _embedder
    if fn is None or not text or not text.strip():
        return None
    try:
        vector = fn(text)
    except Exception:  # any embedder failure means "use keywords"
        log.warning("embedder failed; falling back to keyword search", exc_info=True)
        return None
    try:
        vector = [float(x) for x in vector]
    except (TypeError, ValueError):
        log.warning("embedder returned a non-numeric vector; ignoring it")
        return None
    return vector or None


# ------------------------------------------------------------------ text helpers

_STOPWORDS = frozenset(
    """
    a about above after again against all am an and any are as at be because been before
    being below between both but by can could did do does doing down during each few for
    from further had has have having he her here hers herself him himself his how i if in
    into is it its itself just me more most my myself no nor not now of off on once only or
    other our ours ourselves out over own same she should so some such than that the their
    theirs them themselves then there these they this those through to too under until up
    very was we were what when where which while who whom why will with would you your
    yours yourself yourselves tell told remember remembers story stories anyone someone
    family ever did does dont didnt wasnt isnt im ive id youre hes shes theyre thats
    """.split()
)

_WORD = re.compile(r"[a-z0-9]+(?:'[a-z]+)?")
_SENTENCE_END = re.compile(
    r"(?<=[.!?…])\s+|(?<=[.!?…][\"'”’)])\s+|\s*\n+\s*"
)


def _stem(word: str) -> str:
    """Light plural stripping: books->book, churches->church, stories->story."""
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 4 and word.endswith(("ches", "shes", "sses", "xes", "zes")):
        return word[:-2]
    if len(word) > 3 and word.endswith("s") and not word.endswith(("ss", "us", "is")):
        return word[:-1]
    return word


def _tokens(text: str | None) -> list[str]:
    """Lower-cased, stopword-free, lightly stemmed tokens, in order."""
    if not text:
        return []
    out: list[str] = []
    for raw in _WORD.findall(text.lower().replace("’", "'")):
        word = raw.replace("'", "")
        if raw.endswith("'s"):
            word = raw[:-2]
        if not word or word in _STOPWORDS or raw in _STOPWORDS:
            continue
        out.append(_stem(word))
    return out


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_END.split(text or "") if s and s.strip()]


def best_sentences(text: str, query: str, k: int = 1) -> list[str]:
    """The k sentences of text that share the most tokens with query, in text order.

    Falls back to the first sentence when nothing overlaps. Empty text gives [].
    """
    sentences = _sentences(text)
    if not sentences or k <= 0:
        return []
    wanted = set(_tokens(query))
    scored = [
        (len(wanted & set(_tokens(sentence))), index)
        for index, sentence in enumerate(sentences)
    ]
    ranked = [item for item in sorted(scored, key=lambda s: (-s[0], s[1])) if item[0] > 0]
    if not ranked:
        return [sentences[0]]
    chosen = sorted(index for _, index in ranked[:k])
    return [sentences[index] for index in chosen]


# ------------------------------------------------------------------ search

@dataclass
class SearchHit:
    memory_id: str
    title: str
    year: int
    score: float
    quotes: list[schemas.AskQuote] = field(default_factory=list)


def vector_pipeline(family_id: str, vector: list[float], limit: int) -> list[dict]:
    """The $vectorSearch aggregation, pre-filtered and re-checked on familyId."""
    return [
        {
            "$vectorSearch": {
                "index": VECTOR_INDEX,
                "path": "embedding",
                "queryVector": list(vector),
                "numCandidates": limit * 20,
                "limit": limit,
                "filter": {"familyId": {"$eq": family_id}},
            }
        },
        {"$match": {"familyId": family_id}},
        {"$project": {**_HEAVY_FIELDS, "score": {"$meta": "vectorSearchScore"}}},
    ]


def _year_key(doc: dict) -> int:
    year = doc.get("year")
    return year if isinstance(year, int) else 10**6


def _quotes_for(doc: dict, query: str) -> list[schemas.AskQuote]:
    quotes: list[schemas.AskQuote] = []
    memory_id = str(doc["_id"])
    for telling in doc.get("tellings") or []:
        text = (telling or {}).get("text") or ""
        best = best_sentences(text, query, 1)
        if not best:
            continue
        quotes.append(
            schemas.AskQuote(
                memory_id=memory_id,
                author_id=str(telling.get("authorId") or ""),
                text=best[0],
                title=doc.get("title"),
                year=doc.get("year") if isinstance(doc.get("year"), int) else None,
            )
        )
    return quotes


def _hit(doc: dict, score: float, query: str) -> SearchHit:
    return SearchHit(
        memory_id=str(doc["_id"]),
        title=doc.get("title") or "",
        year=doc.get("year") if isinstance(doc.get("year"), int) else 0,
        score=float(score),
        quotes=_quotes_for(doc, query),
    )


def _vector_search(
    db: Database, family_id: str, vector: list[float], limit: int
) -> list[dict] | None:
    """Docs with a 'score', or None when this server cannot run $vectorSearch."""
    try:
        docs = list(db[MEMORIES].aggregate(vector_pipeline(family_id, vector, limit)))
    except (OperationFailure, NotImplementedError) as exc:
        log.info("vector search unavailable (%s); using keyword search", type(exc).__name__)
        return None
    # Defence in depth: never trust the server-side filter alone.
    return [doc for doc in docs if doc.get("familyId") == family_id]


def _keyword_search(
    db: Database, family_id: str, query: str, limit: int | None = None
) -> list[tuple[dict, float]]:
    """(doc, score in (0, 1]) for every family memory sharing a word with query, best first.

    limit=None returns every match.
    """
    wanted = list(dict.fromkeys(_tokens(query)))
    if not wanted:
        return []
    docs = list(db[MEMORIES].find({"familyId": family_id}, _HEAVY_FIELDS))
    if not docs:
        return []

    indexed = []
    for doc in docs:
        title = set(_tokens(doc.get("title")))
        body: set[str] = set()
        for telling in doc.get("tellings") or []:
            body.update(_tokens((telling or {}).get("text")))
            body.update(_tokens((telling or {}).get("question")))
        indexed.append((doc, title, body))

    # Rare words say more than common ones: weight each query token by its IDF.
    n = len(indexed)
    weights = {}
    for token in wanted:
        df = sum(1 for _, title, body in indexed if token in title or token in body)
        weights[token] = math.log(1 + n / df) if df else 0.0
    best_possible = TITLE_WEIGHT * sum(weights.values())
    if best_possible <= 0:
        return []

    scored: list[tuple[dict, float]] = []
    for doc, title, body in indexed:
        raw = 0.0
        for token in wanted:
            if token in title:
                raw += TITLE_WEIGHT * weights[token]
            elif token in body:
                raw += weights[token]
        if raw > 0:
            scored.append((doc, round(raw / best_possible, 9)))
    scored.sort(key=lambda item: (-item[1], _year_key(item[0]), str(item[0]["_id"])))
    return scored[:limit]


def search_memories(
    db: Database,
    family_id: str,
    query: str,
    *,
    vector: list[float] | None = None,
    limit: int = 5,
) -> list[SearchHit]:
    """The family's memories that best match query, best first; ties go to the earlier year."""
    if not family_id or limit <= 0:
        return []
    vector = vector or embed(query)
    vector_docs = _vector_search(db, family_id, vector, limit) if vector else None
    if vector_docs is None:
        keyword = _keyword_search(db, family_id, query, limit)
        return [_hit(doc, score, query) for doc, score in keyword]
    ranked = _merge_memory_hits(_keyword_search(db, family_id, query), vector_docs)
    return [_hit(doc, score, query) for doc, score in ranked[:limit]]


def _merge_memory_hits(
    keyword: list[tuple[dict, float]], vector_docs: list[dict]
) -> list[tuple[dict, float]]:
    """Rank the union of keyword matches and vector hits, so neither path hides the other.

    $vectorSearch only sees memories that have an `embedding`; seeded memories and any
    saved without an embedder have none, so vector hits alone would hide them as soon as
    one embedded memory exists. And a nearest neighbour comes back even for an unrelated
    question, so vector hits alone would never let the answer be NO_STORY. Hence:

    1. Every keyword match, by keyword score, ties broken by vector rank (a vector hit
       beats a non-hit), then year, then id. Its score is the keyword score.
    2. Then vector hits sharing no word with the query, nearest first, then by year, but
       only those scoring at least VECTOR_MIN_SCORE. Their score is the vector score.

    Each memory appears once.
    """

    def vscore(doc: dict) -> float:
        return round(float(doc.get("score") or 0.0), 9)

    vector_docs = sorted(vector_docs, key=lambda d: (-vscore(d), _year_key(d), str(d["_id"])))
    vrank: dict[str, int] = {}
    for rank, doc in enumerate(vector_docs):
        vrank.setdefault(str(doc["_id"]), rank)
    unranked = len(vector_docs)

    ranked = sorted(
        keyword,
        key=lambda item: (
            -item[1],
            vrank.get(str(item[0]["_id"]), unranked),
            _year_key(item[0]),
            str(item[0]["_id"]),
        ),
    )
    seen = {str(doc["_id"]) for doc, _ in ranked}
    for doc in vector_docs:
        if str(doc["_id"]) not in seen and vscore(doc) >= VECTOR_MIN_SCORE:
            seen.add(str(doc["_id"]))
            ranked.append((doc, vscore(doc)))
    return ranked


def ask_quotes(
    db: Database,
    family_id: str,
    query: str,
    *,
    vector: list[float] | None = None,
    limit: int = 5,
) -> list[schemas.AskQuote]:
    """The best sentence from each telling of the best-matching memories, at most `limit`."""
    if limit <= 0:
        return []
    quotes: list[schemas.AskQuote] = []
    for hit in search_memories(db, family_id, query, vector=vector, limit=limit):
        for quote in hit.quotes:
            quotes.append(quote)
            if len(quotes) >= limit:
                return quotes
    return quotes


def compose_answer(
    query: str,
    quotes: list[schemas.AskQuote],
    names: dict[str, str] | None = None,
) -> str:
    """A plain answer built from quotes, one line per quote. Deterministic, no model.

    names maps person id -> display name; the route supplies it.
    """
    if not quotes:
        return NO_STORY
    names = names or {}
    lines = []
    for quote in quotes:
        who = names.get(quote.author_id) or "Someone in the family"
        when = f" ({quote.year})" if quote.year is not None else ""
        lines.append(f"{who} remembers{when}: '{quote.text.strip()}'")
    return "\n".join(lines)
