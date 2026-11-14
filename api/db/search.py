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

POST /families/{fid}/ask uses rank_memories, which adds the asker to all this: kinship words
and names in the question resolve to people of the family tree relative to the caller's
person (resolve_people), memories those people take part in or tell are boosted, and the
result is an ordered candidate list (Ranking) whose top one is `best`, `confident` when it
clearly leads (Ranking.confident). search_memories and ask_quotes are unchanged.
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
    if len(word) >= 9 and word.endswith("ation"):
        return word[:-3]  # graduation, celebration -> graduat, celebrat (as graduate, celebrate)
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


# ------------------------------------------------------------------ kinship for the asker
#
# "grandpa and grandma", "my mom", "uncle Joe", "dad's parents" name different people
# depending on who asks. resolve_people turns those words into person ids, relative to the
# asker's person in the family tree (parent_ids, spouse_ids; spouses are symmetrised). The
# tree has no gender, so a gendered word names the whole relation: "grandpa" is every
# grandparent, "mom" both parents, "sister" every sibling, "wife" every spouse.

# A kinship word (as written, casefolded, or once de-pluralised) -> the relation it names.
_KIN_RELATION = {
    **dict.fromkeys(
        ("mom", "mother", "mama", "mum", "mommy", "momma", "dad", "father", "papa", "daddy",
         "pop", "parent"), "parents"),
    **dict.fromkeys(
        ("grandma", "grandmother", "granny", "nana", "abuela", "grandpa", "grandfather",
         "granddad", "grandad", "abuelo", "grandparent", "grandparents"), "grandparents"),
    **dict.fromkeys(("brother", "sister", "sibling", "bro", "sis"), "siblings"),
    **dict.fromkeys(("son", "daughter", "child", "children", "kid"), "children"),
    **dict.fromkeys(("husband", "wife", "wives", "spouse"), "spouses"),
    **dict.fromkeys(("uncle", "aunt", "auntie", "aunty", "tia", "tio"), "aunts_uncles"),
    **dict.fromkeys(("cousin",), "cousins"),
    **dict.fromkeys(
        ("grandson", "granddaughter", "grandchild", "grandchildren", "grandkid"), "grandchildren"),
    **dict.fromkeys(("nephew", "niece"), "nieces_nephews"),
}
# The asker as the subject ("how did I meet my wife"). "my" alone only says whose relative.
_SELF_WORDS = frozenset({"i", "me", "myself"})
# A question addressed to someone else ("How did you meet Mom?", "your wife") says "Mom"
# from the listener's side, not the asker's, so kinship is not resolved for the asker. These
# words always mean that; plain "you" only as the subject of the story's verb (_listener_side).
_SECOND_PERSON = frozenset({"your", "yours", "youre", "yourself", "yall"})
# "did you meet", "were you there": an auxiliary right before "you".
_AUX_BEFORE_YOU = frozenset({"did", "do", "does", "were", "was", "have", "had", "are"})
# Polite openers that address the app, not a relative: "Can you show me ...", "Do you know
# ...", "Tell me ...", "please". Stripped before kinship and keywords are read.
_OPENER = re.compile(
    r"\b(?:(?:can|could|would|will)\s+you\s+(?:please\s+)?(?:(?:tell|show|remind|help|find|give)\s+)?(?:me\s+)?"
    r"|do\s+you\s+(?:know|remember|recall)\b"
    r"|(?:tell|show|remind|find|give)\s+me\b"
    r"|please\b)",
    re.IGNORECASE,
)
# Light verbs that say nothing about which memory ("did they get married", "we got a flat
# tire"): left out of /ask ranking keywords (rank_memories), not out of search in general.
_RANK_SKIP = frozenset({"get", "gets", "got", "gotten", "getting", "go", "goes", "going", "went", "gone"})


# Words that tie the kinship word after them to a particular person ("my grandma").
_ANCHORS = frozenset({"my", "our", "his", "her", "their"})


def strip_openers(query: str) -> str:
    """The question without polite openers ("Can you show me where ..." -> "where ...")."""
    return " ".join(_OPENER.sub(" ", query or "").split())


def _listener_side(words: list[str]) -> bool:
    """True when the question speaks about the listener's life ("did you meet", "your wife")."""
    for i, word in enumerate(words):
        if word in _SECOND_PERSON:
            return True
        if word == "you":
            before = words[i - 1] if i else ""
            after = words[i + 1] if i + 1 < len(words) else ""
            if before in _AUX_BEFORE_YOU or after in _IRREGULAR_PAST or (len(after) > 3 and after.endswith("ed")):
                return True
    return False
# Common English nicknames -> the start of the formal name they shorten ("Joe" -> Joseph).
_NICKNAMES = {
    "joe": "jos", "bill": "will", "billy": "will", "bob": "rob", "bobby": "rob", "liz": "eliz",
    "beth": "eliz", "betty": "eliz", "kate": "kat", "katie": "kat", "jim": "jam", "jimmy": "jam",
    "mike": "mich", "dick": "rich", "rick": "rich", "peggy": "marg", "maggie": "marg",
    "tony": "ant", "jack": "john", "johnny": "john", "chuck": "charl", "charlie": "charl",
    "lou": "lu", "lucho": "luis", "pepe": "jos", "paco": "franc", "chris": "chris", "nick": "nich",
}
# A word plus whether it carried a possessive 's: "dad's" -> ("dad", True).
_QUERY_WORD = re.compile(r"([^\W_]+)('s)?(?![^\W_])")


@dataclass(frozen=True)
class Mention:
    """People a phrase of the query names. relation is a _KIN_RELATION value, 'self' or 'name'."""

    phrase: str
    relation: str
    person_ids: frozenset[str]
    words: tuple[str, ...] = ()  # the query words it consumed, casefolded


class _Tree:
    """Relations over one family's people (all from the same family_id)."""

    def __init__(self, people: list[schemas.Person]) -> None:
        self.ids = {p.id for p in people}
        self.parents: dict[str, set[str]] = {p.id: {i for i in p.parent_ids if i in self.ids} for p in people}
        self.children: dict[str, set[str]] = {p.id: set() for p in people}
        self.spouses: dict[str, set[str]] = {p.id: set() for p in people}
        for p in people:
            for parent in self.parents[p.id]:
                self.children[parent].add(p.id)
            for spouse in p.spouse_ids:
                if spouse in self.ids and spouse != p.id:
                    self.spouses[p.id].add(spouse)
                    self.spouses[spouse].add(p.id)

    def _step(self, edges: dict[str, set[str]], start: set[str]) -> set[str]:
        return set().union(*(edges.get(i, set()) for i in start)) if start else set()

    def relation(self, name: str, start: set[str]) -> set[str]:
        parents = lambda s: self._step(self.parents, s)  # noqa: E731
        children = lambda s: self._step(self.children, s)  # noqa: E731
        spouses = lambda s: self._step(self.spouses, s)  # noqa: E731

        def siblings(s: set[str]) -> set[str]:
            return children(parents(s)) - s

        if name == "parents":
            return parents(start)
        if name == "grandparents":
            return parents(parents(start))
        if name == "siblings":
            return siblings(start)
        if name == "children":
            return children(start)
        if name == "spouses":
            return spouses(start)
        if name == "grandchildren":
            return children(children(start))
        if name == "aunts_uncles":
            blood = siblings(parents(start))
            return blood | spouses(blood)
        if name == "cousins":
            blood = siblings(parents(start))
            return children(blood | spouses(blood)) - start
        if name == "nieces_nephews":
            return children(siblings(start))
        return set()


def _kin_relation(word: str) -> str | None:
    return _KIN_RELATION.get(word) or _KIN_RELATION.get(_plural(word))


def _name_words(name: str) -> list[str]:
    return _WORD.findall(unicodedata.normalize("NFKC", name or "").casefold())


def resolve_people(
    query: str, people: list[schemas.Person], asker_id: str | None
) -> list[Mention]:
    """The people the query names, in query order, relative to asker_id. Deterministic, no I/O.

    - Polite openers ("Can you show me", "Do you know", "please") are stripped first.
    - Kinship words (_KIN_RELATION) resolve through the tree from the asker: "grandma" is the
      asker's grandparents. Possessives chain: "dad's parents" is the parents of the asker's
      parents, "Sam's wife" Sam's spouses. A kinship word followed by a name narrows to that
      name when it can ("uncle Joe" is the aunt or uncle called Joe).
    - A bare grandparent word ("grandma", not "my grandma" or "Sam's grandma") the tree
      cannot resolve from the asker is read from the asker's children's side (a parent calls
      their own parents "Grandma" and "Grandpa"). No other relation gets that fallback.
      Failing that, a kinship word falls back to people whose name holds it ("Grandma Rose",
      a person named "Dad"). Still nobody: the word is unresolved (Ranking.unresolved), unless
      kinship was skipped on purpose (no asker, or a question about the listener's life).
    - Names: a query word equal to a word of a person's name, or (when written with a capital
      but not as the question's first word, or right after a kinship word, at least 3 letters)
      the start of one: "Sam" -> Samuel; and through _NICKNAMES "Joe" -> Joseph.
    - "I"/"me", and "my" before a word that is not kinship ("my wedding"), name the asker.
      Without an asker, or when the question is about the listener's life ("How did you
      meet Mom?", "your wife": _listener_side), kinship and "I" are skipped and only names
      resolve.
    Mentions that name nobody are dropped.
    """
    return _resolve(query, people, asker_id)[0]


def _resolve(
    query: str, people: list[schemas.Person], asker_id: str | None
) -> tuple[list[Mention], list[str]]:
    """(mentions, kinship words that named nobody): see resolve_people."""
    if not query or not people:
        return [], []
    tree = _Tree(people)
    asker = asker_id if asker_id in tree.ids else None
    text = unicodedata.normalize("NFKC", strip_openers(query)).replace("’", "'")
    raw = [(m.group(1), m.group(1).casefold(), bool(m.group(2))) for m in _QUERY_WORD.finditer(text)]
    if _listener_side([word for _, word, _ in raw]):
        asker = None
    names = [(p.id, _name_words(p.name)) for p in people]

    def by_name(word: str, loose: bool, nickname: bool) -> set[str]:
        if word in _STOPWORDS and word not in _SELF_WORDS or len(word) < 2:
            return set()
        exact = {pid for pid, words in names if word in words}
        if exact or len(word) < 3:
            return exact
        if nickname and word in _NICKNAMES:
            prefix = _NICKNAMES[word]
        elif loose:
            prefix = word
        else:
            return exact
        return {pid for pid, words in names if any(w.startswith(prefix) for w in words)}

    mentions: list[Mention] = []
    unresolved: list[str] = []
    pending: Mention | None = None  # the possessor before a possessive 's ("dad's")
    previous_kin: Mention | None = None
    for index, (written, word, possessive) in enumerate(raw):
        relation = _kin_relation(word)
        mention: Mention | None = None
        if word == "my":
            following = raw[index + 1][1] if index + 1 < len(raw) else ""
            has_self = any(m.relation == "self" for m in mentions)
            if asker is not None and following and _kin_relation(following) is None and not has_self:
                mention = Mention(written, "self", frozenset({asker}), ())
        elif word in _SELF_WORDS:
            if asker is not None and not any(m.relation == "self" for m in mentions):
                mention = Mention(written, "self", frozenset({asker}), (word,))
        elif relation is not None:
            if pending is not None:
                base, words = set(pending.person_ids), pending.words
                phrase = f"{pending.phrase}'s {written}"
            elif asker is not None:
                base, words, phrase = {asker}, (), written
            else:
                base, words, phrase = set(), (), written
            ids = tree.relation(relation, base) if base else set()
            anchored = pending is not None or (index > 0 and raw[index - 1][1] in _ANCHORS)
            if not ids and relation == "grandparents" and not anchored and asker is not None:
                # A parent calls their own parents "Grandma" and "Grandpa", as their children
                # do: read a bare grandparent word from the asker's children's side. Only for
                # grandparents, and never for "my grandma" or "Sam's grandpa".
                ids = tree.relation(relation, tree.children.get(asker, set())) - {asker}
            if not ids and pending is None:
                ids = {pid for pid, nwords in names if word in nwords}
            if ids:
                if pending is not None and mentions and mentions[-1] is pending:
                    mentions.pop()  # "dad's parents" replaces "dad"
                mention = Mention(phrase, relation, frozenset(ids), (*words, word))
            elif base:
                # Resolution was attempted and named nobody. Not when kinship was skipped on
                # purpose (no asker, or a question about the listener's life).
                unresolved.append(word)
        else:
            capital = written[:1].isupper()
            loose = (capital and index > 0) or previous_kin is not None
            ids = by_name(word, loose, capital or previous_kin is not None)
            if ids and previous_kin is not None and mentions and mentions[-1] is previous_kin:
                narrowed = ids & previous_kin.person_ids
                if narrowed:  # "uncle Joe": the uncle called Joe
                    mentions.pop()
                    mention = Mention(f"{previous_kin.phrase} {written}", previous_kin.relation,
                                      frozenset(narrowed), (*previous_kin.words, word))
            if mention is None and ids:
                mention = Mention(written, "name", frozenset(ids), (word,))
        if mention is not None:
            mentions.append(mention)
        pending = mention if possessive and mention is not None else None
        previous_kin = mention if mention is not None and relation is not None else None
    return mentions, unresolved


# ------------------------------------------------------------------ ranking for /ask

# How much the people a query names count against keyword relevance (which is in [0, 1]).
PERSON_WEIGHT = 1.5
# A vector-only hit (no shared word) ranks at this fraction of its vector score.
VECTOR_ONLY_WEIGHT = 0.5
# The asker naming themself ("I", "me") says less than naming a relative.
SELF_MENTION_WEIGHT = 0.5
# best is confident when its score is at least CONFIDENT_MIN_SCORE and at least
# CONFIDENT_RATIO times the runner-up's, and none of Ranking.confident's vetoes apply.
CONFIDENT_MIN_SCORE = 0.3
CONFIDENT_RATIO = 1.5
# A keyword-only top match must cover at least this share of the query's word weight.
CONFIDENT_MIN_COVERAGE = 0.5
MAX_CANDIDATES = 20
MAX_QUOTES = 5


@dataclass
class Candidate:
    memory_id: str
    title: str
    year: int | None
    participant_ids: list[str]
    teller_ids: list[str]
    score: float
    quotes: list[schemas.AskQuote] = field(default_factory=list)
    keyword: float = 0.0  # the keyword (or vector-only) part of score
    person: float = 0.0  # _person_score, before PERSON_WEIGHT
    coverage: float = 0.0  # share of the query's word weight this memory matches


@dataclass
class Ranking:
    """Candidate memories for a question, best first, and the people it named."""

    candidates: list[Candidate]
    mentions: list[Mention]
    unresolved: list[str] = field(default_factory=list)  # kinship words that named nobody

    def quotes(self, limit: int = MAX_QUOTES, first: str | None = None) -> list[schemas.AskQuote]:
        """Quotes in candidate order (memory `first` leading, when given), at most limit."""
        ordered = sorted(self.candidates, key=lambda c: c.memory_id != first) if first else self.candidates
        out: list[schemas.AskQuote] = []
        for candidate in ordered:
            for quote in candidate.quotes:
                if len(out) >= limit:
                    return out
                out.append(quote)
        return out

    def confident(self) -> bool:
        """The top candidate clearly leads.

        True only when its score is at least CONFIDENT_MIN_SCORE and at least CONFIDENT_RATIO
        times the runner-up's (no runner-up counts as 0), and none of these hold:
        - a kinship word in the question named nobody (the people may be the wrong ones);
        - the top match is keyword-only and covers under CONFIDENT_MIN_COVERAGE of the
          query's word weight (words matching no memory count at the highest weight);
        - the top match is person-only (no keyword overlap) and is the only candidate.
        """
        if not self.candidates or self.unresolved:
            return False
        first = self.candidates[0]
        if first.person <= 0 and first.coverage < CONFIDENT_MIN_COVERAGE:
            return False
        if first.keyword <= 0 and len(self.candidates) == 1:
            return False
        runner_up = self.candidates[1].score if len(self.candidates) > 1 else 0.0
        return first.score >= CONFIDENT_MIN_SCORE and first.score >= CONFIDENT_RATIO * runner_up

    def best(self, memory_id: str | None = None, confident: bool | None = None) -> schemas.AskBest | None:
        """AskBest for memory_id (default: the top candidate), or None when there is none."""
        if not self.candidates:
            return None
        candidate = next((c for c in self.candidates if c.memory_id == memory_id), None) if memory_id \
            else self.candidates[0]
        if candidate is None:
            return None
        teller = next((q.author_id for q in candidate.quotes if q.author_id), None)
        person_id = teller or (candidate.participant_ids[0] if candidate.participant_ids else None)
        return schemas.AskBest(
            memory_id=candidate.memory_id,
            title=candidate.title or None,
            year=candidate.year,
            person_id=person_id,
            confident=self.confident() if confident is None else confident,
        )


def _person_score(participants: set[str], tellers: set[str], mentions: list[Mention]) -> float:
    """In [0, 1]: how many named groups take part (participant or teller), and how focused on them.

    coverage = weighted share of mentions with someone in the memory; focus = share of the
    memory's participants who are named. score = coverage * (0.5 + 0.5 * focus), so a
    memory about exactly the named people beats a crowded one that merely includes them.
    """
    if not mentions:
        return 0.0
    present = participants | tellers
    total = covered = 0.0
    named: set[str] = set()
    for mention in mentions:
        weight = SELF_MENTION_WEIGHT if mention.relation == "self" else 1.0
        total += weight
        named |= mention.person_ids
        if mention.person_ids & present:
            covered += weight
    if not covered:
        return 0.0
    focus = len(participants & named) / len(participants) if participants else 0.0
    return (covered / total) * (0.5 + 0.5 * focus)


def rank_memories(
    db: Database,
    family_id: str,
    query: str,
    *,
    people: list[schemas.Person] | None = None,
    asker_id: str | None = None,
    vector: list[float] | None = None,
    limit: int = MAX_CANDIDATES,
) -> Ranking:
    """Rank this family's memories for an /ask question, best first. One read of the family's memories.

    score = keyword score (as _keyword_search, in [0, 1]; a vector-only hit scoring at least
    VECTOR_MIN_SCORE counts VECTOR_ONLY_WEIGHT * its vector score instead)
          + PERSON_WEIGHT * _person_score for the people the query names (resolve_people).
    Kinship words that resolved for the asker are dropped from the keywords: tellings use
    "Mom" from the teller's side, so they would match the wrong mothers. Polite openers
    (strip_openers) and light verbs (_RANK_SKIP: get, got, went, ...) are dropped too. A memory counts when
    its score is above 0. Ties go to the nearer vector hit, then the earlier year, then id.
    `people` must be this family's people (the route passes people_db.list_people).
    """
    if not family_id or limit <= 0:
        return Ranking([], [])
    mentions, unresolved = _resolve(query, people or [], asker_id)
    dropped = {w for m in mentions if m.relation not in ("name", "self") for w in m.words} | _RANK_SKIP
    wanted = list(dict.fromkeys(_stem(w) for w in _words(strip_openers(query)) if w not in dropped))

    docs = list(db[MEMORIES].find({"familyId": family_id}, _HEAVY_FIELDS))
    if not docs:
        return Ranking([], mentions, unresolved)
    indexed = []
    for doc in docs:
        title = set(_tokens(doc.get("title")))
        body: set[str] = set()
        for telling in doc.get("tellings") or []:
            body.update(_tokens((telling or {}).get("text")))
        indexed.append((doc, title, body))
    n = len(indexed)
    weights = {}
    for token in wanted:
        df = sum(1 for _, title, body in indexed if token in title or token in body)
        weights[token] = math.log(1 + n / df) if df else 0.0
    best_possible = TITLE_WEIGHT * sum(weights.values())
    # For `coverage` only: a word no memory holds weighs as much as the rarest possible word.
    unseen = math.log(1 + 2 * n)
    query_weight = sum(w or unseen for w in weights.values())

    vector = vector or (embed(query) if wanted else None)
    vector_docs = _vector_search(db, family_id, vector, limit) if vector else None
    vscores: dict[str, float] = {}
    for doc in sorted(vector_docs or [], key=lambda d: -float(d.get("score") or 0.0)):
        vscores.setdefault(str(doc["_id"]), round(float(doc.get("score") or 0.0), 9))
    vrank = {mid: rank for rank, mid in enumerate(vscores)}

    scored: list[tuple[float, int, int, str, dict, tuple[float, float, float]]] = []
    for doc, title, body in indexed:
        memory_id = str(doc["_id"])
        keyword = coverage = 0.0
        if best_possible > 0:
            raw = sum(
                TITLE_WEIGHT * weights[t] if t in title else weights[t] if t in body else 0.0
                for t in wanted
            )
            keyword = raw / best_possible
            coverage = sum(weights[t] for t in wanted if t in title or t in body) / query_weight
        if keyword <= 0 and vscores.get(memory_id, 0.0) >= VECTOR_MIN_SCORE:
            keyword = VECTOR_ONLY_WEIGHT * vscores[memory_id]
        participants = {str(p) for p in doc.get("participantIds") or []}
        tellers = {str((t or {}).get("authorId")) for t in doc.get("tellings") or [] if (t or {}).get("authorId")}
        person = _person_score(participants, tellers, mentions)
        score = round(keyword + PERSON_WEIGHT * person, 9)
        if score > 0:
            scored.append((-score, vrank.get(memory_id, len(vrank)), _year_key(doc), memory_id, doc,
                           (keyword, person, coverage)))
    scored.sort(key=lambda item: item[:4])

    candidates = []
    for neg_score, _, _, memory_id, doc, (keyword, person, coverage) in scored[:limit]:
        year = doc.get("year") if isinstance(doc.get("year"), int) else None
        candidates.append(Candidate(
            memory_id=memory_id,
            title=doc.get("title") or "",
            year=year,
            participant_ids=[str(p) for p in doc.get("participantIds") or []],
            teller_ids=list(dict.fromkeys(
                str((t or {}).get("authorId")) for t in doc.get("tellings") or [] if (t or {}).get("authorId")
            )),
            score=-neg_score,
            quotes=_quotes_for(doc, query),
            keyword=keyword,
            person=person,
            coverage=coverage,
        ))
    return Ranking(candidates, mentions, unresolved)


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
