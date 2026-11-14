"""'Ask the family': memory search with quotes from the tellings (ARCHITECTURE.md section 5).

A keyword search over titles and telling texts (never the prompt a teller answered) always
runs. When a query vector is available
and the server can run Atlas Vector Search over `memories.embedding` (index VECTOR_INDEX,
`familyId` pre-filter), its hits are merged with the keyword matches (_merge_memory_hits):
keyword matches first, then vector-only hits scoring at least VECTOR_MIN_SCORE. When there
is no vector, or the server cannot run `$vectorSearch` (plain MongoDB raises
OperationFailure, mongomock raises NotImplementedError), keywords alone answer, so search
works offline and on plain MongoDB.

No network by default: `embed` returns None until something calls `set_embedder`.
Answers are composed deterministically from quotes; no model is involved. A hit quotes
only its tellings that share a word with the query, each by its best sentence; when none
does (a title-only or vector-only match), it quotes the first sentence of every telling.

Keyword matching is on normalised words, the same for the query, titles, tellings and
the quoted sentence (see _tokens). It folds plurals (books/book), regular verb tenses
(rain/rained/raining, marry/married, stop/stopped), a short table of irregular pasts
(met -> meet, ran -> run, wore -> wear, ...: _IRREGULAR_PAST) and kinship synonyms (mom,
mama, mum -> mother; dad, papa, pop -> father; nana, granny, abuela -> grandma; granddad,
abuelo -> grandpa: _KINSHIP). So "How did you meet Mom?" finds "How I met your mother".
Sentences do not end at the period of a title or initial ("Dr. Kim", "St. Mary's",
"J. R.") or of "a.m." and the like inside a sentence (_ABBREVIATION).
"""

from __future__ import annotations

import logging
import math
import os
import re
import unicodedata
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
    happen happened happens happening like really
    """.split()
)

# Letters and digits in any script (Hangul, accented Latin, Cyrillic, ...); underscores,
# punctuation and spaces separate words. Same approach as db/props.py.
_WORD = re.compile(r"[^\W_]+")
# A possessive 's at the end of a word: grandma's -> grandma, James's -> James.
_POSSESSIVE = re.compile(r"(?<=[^\W_])'s\b")
_SENTENCE_END = re.compile(
    r"(?<=[.!?…])\s+|(?<=[.!?…][\"'”’)])\s+"
    # Full-width and Devanagari sentence ends need no space after them. A closing quote
    # or bracket right after one stays with its sentence, which ends there only when
    # whitespace follows (「おいしい。」と言った。 is one sentence).
    r"|(?<=[。！？।])(?![」』”’)）\"'])\s*|(?<=[。！？।][」』”’)）\"'])\s+"
    # A line break. It must start the match: a leading \s* here backtracks over every
    # whitespace run that has no newline, which made splitting quadratic in the run's
    # length (40k spaces in one telling took seconds per ask). Whitespace before the
    # newline stays on the sentence, which _sentences strips.
    r"|\n\s*"
)

_VOWELS = frozenset("aeiou")
# For verb endings: what is left after stripping '-ed'/'-ing' must hold one of these.
_VERB_VOWELS = frozenset("aeiouy")
# A doubled final consonant left by '-ed'/'-ing' is undoubled (stopped -> stop), except
# these, which English doubles in the base word too (called, kissed, buzzing).
_KEEP_DOUBLED = frozenset("lsz")

# Irregular past forms, mapped to their present before stemming, so "met" matches "meet".
# 'saw' (also a noun) and 'left' (also a direction) are deliberately missing.
_IRREGULAR_PAST = {
    "met": "meet", "ran": "run", "gave": "give", "took": "take", "came": "come",
    "went": "go", "bought": "buy", "brought": "bring", "sang": "sing", "fell": "fall",
    "held": "hold", "made": "make", "said": "say", "wore": "wear", "drove": "drive",
    "rode": "ride", "ate": "eat", "sat": "sit", "got": "get", "lost": "lose",
    "found": "find", "taught": "teach", "wrote": "write", "built": "build", "flew": "fly",
    "threw": "throw", "caught": "catch", "grew": "grow", "knew": "know",
}

# Kinship synonyms, folded to one token after possessive stripping (Mom's -> mother).
# Plurals fold too (moms, mothers): a word is looked up as written and once de-pluralised.
# The grandparents fold to "grandma"/"grandpa", the tokens search has always produced for
# those words.
_KINSHIP = {
    **dict.fromkeys(("mom", "mother", "mama", "mum", "mommy", "momma"), "mother"),
    **dict.fromkeys(("dad", "father", "papa", "daddy", "pop"), "father"),
    **dict.fromkeys(("grandma", "grandmother", "granny", "nana", "abuela"), "grandma"),
    **dict.fromkeys(("grandpa", "grandfather", "granddad", "grandad", "abuelo"), "grandpa"),
}


def _plural(word: str) -> str:
    """Small plural stripper; singular and plural map to the same stem (as db/props.py).

    books/book -> book, churches/church -> church, stories/story -> story. '-ies' plurals
    come from '-y' (stories, flies) or from '-ie' (cookies, cookie), so '-ies' and '-ie'
    both fold to '-y' (story, fly, cooky); the stem need not be a word. Short words and
    words in other scripts pass through unchanged.
    """
    if word.endswith("ies") and len(word) > 4 and word[-4] not in _VOWELS:
        return word[:-3] + "y"  # stories, cookies -> story, cooky
    if len(word) <= 3:
        return word  # fly, pie, gas
    if word.endswith("ie") and word[-3] not in _VOWELS:
        return word[:-2] + "y"  # cookie, movie -> cooky, movy
    if word.endswith(("sses", "xes", "zes", "ches", "shes")):
        return word[:-2]
    if word.endswith("s") and not word.endswith(("ss", "us", "is")):
        return word[:-1]
    return word


def _verb(word: str) -> str:
    """Fold a regular '-ed'/'-ing' ending, then a final silent 'e' (the stem need not be a word).

    married -> marry, raining/rained -> rain, stopped/stopping -> stop, wedding -> wed,
    dance/danced/dancing -> danc. An ending is stripped only when at least 3 letters,
    one of them a vowel (or y), remain, so string, thing, king, bring, red and bed keep
    their own stems; '-eed' words (need, agreed) and '-ee' words (tree, agree) are kept.
    """
    if len(word) <= 3:
        return word
    if word.endswith("ied") and len(word) > 4 and word[-4] not in _VOWELS:
        return word[:-3] + "y"  # married, carried -> marry, carry
    base = None
    if word.endswith("ed") and not word.endswith("eed"):
        base = word[:-2]
    elif word.endswith("ing"):
        base = word[:-3]
    if base is not None and len(base) >= 3 and _VERB_VOWELS.intersection(base):
        if (
            len(base) >= 4
            and base[-1] == base[-2]
            and base[-1] not in _VERB_VOWELS
            and base[-1] not in _KEEP_DOUBLED
        ):
            base = base[:-1]  # stopp, sitt, wedd -> stop, sit, wed
        word = base
    if len(word) >= 4 and word.endswith("e") and not word.endswith("ee"):
        word = word[:-1]  # dance, bake, danc(e)d -> danc, bak
    return word


def _stem(word: str) -> str:
    """One stem for a casefolded word: irregular pasts, kinship, plurals, then verb endings.

    Singular and plural map to the same stem (books/book, stories/story, cookies/cookie
    -> cooky), as do verb tenses (rained/raining/rains -> rain, married -> marry), the
    irregular pasts in _IRREGULAR_PAST (met -> meet) and the kinship synonyms in _KINSHIP
    (mom, moms, mama -> mother). Words of 3 or fewer letters and words in other scripts
    pass through unchanged unless a table names them.
    """
    word = _IRREGULAR_PAST.get(word, word)
    if word in _KINSHIP:
        return _KINSHIP[word]
    word = _plural(word)
    if word in _KINSHIP:
        return _KINSHIP[word]  # moms, mothers, grannies -> mother, grandma
    return _verb(word)


def _tokens(text: str | None) -> list[str]:
    """Word tokens in any script, in order: NFKC, casefolded, stopwords dropped, stemmed.

    Possessive 's is dropped and other apostrophes are removed (didn't -> didnt). Scripts
    written without spaces (Chinese, Japanese) come out as one token per run, and Korean
    words keep their attached particles (할머니가), so those match on whole words only.
    """
    return [_stem(word) for word in _words(text)]


def _words(text: str | None) -> list[str]:
    """The words _tokens stems: NFKC, casefolded, possessives and stopwords dropped."""
    if not text:
        return []
    text = unicodedata.normalize("NFKC", text).casefold().replace("’", "'")
    text = _POSSESSIVE.sub("", text).replace("'", "")
    return [word for word in _WORD.findall(text) if word not in _STOPWORDS]


# A sentence piece ending in one of these did not end a sentence: the period belongs to
# (a) a title or place abbreviation (any case, whole word): "Dr. Kim", "St. Mary's";
# (b) an initial, one uppercase Latin letter other than I: "J. R. drove"
#     ("So did I. We went home." is two sentences);
# (c) "a.m.", "p.m.", "e.g.", "i.e." or "etc.", but only when the next piece starts with a
#     lowercase letter ("at 6 a.m. and drove" is one sentence; "...until 6 a.m. Then" two).
# The same lists are used by seed/fake_runs.py; keep them in step.
_ABBREVIATION = re.compile(
    r"(?<![^\W_])(?i:mr|mrs|ms|dr|st|sr|jr|mt|ft|rev|prof|sgt|capt|lt|col|gen|gov|hon"
    r"|ave|blvd|rd|vs|approx)\.$"
    r"|(?<![^\W_])[A-HJ-Z]\.$"
)
_ABBREVIATION_BEFORE_LOWERCASE = re.compile(r"(?<![^\W_])(?:a\.m|p\.m|e\.g|i\.e|etc)\.$")
# Longer than any abbreviation above plus the character before it, so testing only the
# tail of a piece gives the same answer as testing all of it (and keeps splitting linear).
_ABBREVIATION_TAIL = 12


def _continues(piece: str, separator: str, following: str) -> bool:
    """True when the split between piece and following was at an abbreviation's period."""
    if "\n" in separator:
        return False  # a line break always ends a sentence
    tail = piece.rstrip()[-_ABBREVIATION_TAIL:]
    if _ABBREVIATION.search(tail):
        return True
    return bool(_ABBREVIATION_BEFORE_LOWERCASE.search(tail)) and following[:1].islower()


def _sentences(text: str) -> list[str]:
    """Sentences of text, stripped, in order; a period after an abbreviation does not split.

    Linear in len(text): one pass of _SENTENCE_END, then each piece is either a new
    sentence or appended to the one before it, judged on its last few characters only.
    """
    text = text or ""
    pieces: list[tuple[str, str]] = []  # (piece, separator before it)
    start, separator = 0, ""
    for match in _SENTENCE_END.finditer(text):
        piece = text[start:match.start()]
        if piece.strip():
            pieces.append((piece, separator))
            separator = match.group()
        else:
            separator += piece + match.group()  # keep a line break as a hard break
        start = match.end()
    if text[start:].strip():
        pieces.append((text[start:], separator))

    sentences: list[list[str]] = []
    previous = ""
    for piece, before in pieces:
        if sentences and _continues(previous, before, piece.lstrip()):
            sentences[-1].extend((before, piece))
        else:
            sentences.append([piece])
        previous = piece
    return [s for s in ("".join(parts).strip() for parts in sentences) if s]


def best_sentences(text: str, query: str, k: int = 1) -> list[str]:
    """The k sentences of text that share the most tokens with query, in text order.

    Ties go to the sentence sharing more of the query's words as written (asked "went",
    "Then we went." beats "He said go."), then to the earlier sentence. Falls back to the
    first sentence when nothing overlaps. Empty text gives [].
    """
    sentences = _sentences(text)
    if not sentences or k <= 0:
        return []
    wanted = set(_tokens(query))
    wanted_words = set(_words(query))
    scored = []
    for index, sentence in enumerate(sentences):
        words = _words(sentence)
        shared = len(wanted & {_stem(word) for word in words})
        if shared:
            scored.append((-shared, -len(wanted_words.intersection(words)), index))
    if not scored:
        return [sentences[0]]
    chosen = sorted(index for *_, index in sorted(scored)[:k])
    return [sentences[index] for index in chosen]


# ------------------------------------------------------------------ search

@dataclass
class SearchHit:
    memory_id: str
    title: str
    year: int
    score: float
    quotes: list[schemas.AskQuote] = field(default_factory=list)


# Atlas rejects a $vectorSearch whose numCandidates is above 10,000 or below its limit.
MAX_NUM_CANDIDATES = 10_000
CANDIDATES_PER_RESULT = 20


def vector_sizes(limit: int) -> tuple[int, int]:
    """(numCandidates, limit) for a $vectorSearch stage, inside Atlas's bounds.

    numCandidates is 20 per wanted result, and 1 <= limit <= numCandidates <= 10,000,
    so a large limit never turns into a rejected query (and a silent keyword fallback).
    """
    limit = max(1, min(int(limit), MAX_NUM_CANDIDATES))
    return min(limit * CANDIDATES_PER_RESULT, MAX_NUM_CANDIDATES), limit


def vector_pipeline(family_id: str, vector: list[float], limit: int) -> list[dict]:
    """The $vectorSearch aggregation, pre-filtered and re-checked on familyId."""
    num_candidates, limit = vector_sizes(limit)
    return [
        {
            "$vectorSearch": {
                "index": VECTOR_INDEX,
                "path": "embedding",
                "queryVector": list(vector),
                "numCandidates": num_candidates,
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


def _overlaps(text: str, wanted: set[str]) -> bool:
    """True when some sentence of text shares a token with wanted."""
    return any(wanted & set(_tokens(sentence)) for sentence in _sentences(text))


def _quotes_for(doc: dict, query: str) -> list[schemas.AskQuote]:
    """One quote per telling, in telling order; empty tellings are skipped.

    When any telling shares a word with the query, only those tellings are quoted, each by
    its best sentence. Otherwise (a title-only keyword match or a vector-only hit) every
    telling is quoted by its first sentence, so such matches still give an answer.
    """
    memory_id = str(doc["_id"])
    wanted = set(_tokens(query))
    texts = [
        (telling, text)
        for telling in doc.get("tellings") or []
        if (text := (telling or {}).get("text") or "")
    ]
    overlapping = [(telling, text) for telling, text in texts if _overlaps(text, wanted)]
    quotes: list[schemas.AskQuote] = []
    for telling, text in overlapping or texts:
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

    The index is the memory title plus each telling's text; telling questions are ignored.

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
            # Only what the teller said: a memory must not match on the prompt they answered.
            body.update(_tokens((telling or {}).get("text")))
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
