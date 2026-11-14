"""The agent's database seam (ARCHITECTURE.md sections 2 and 5).

`AgentRepo` is the narrow set of reads and writes the agent needs. Every method takes
the family id first, and a lookup in another family returns None or an empty list:
it never raises with, or returns, another family's data.

`find_props` / `find_sets` match a free-text description ("the old trumpet case") to
the family's saved props and sets, best first, for the prop and set finders.

`InMemoryRepo` is the deterministic fake used in tests and offline runs; its finders are
a plain keyword match. `agent.mongo_repo.MongoRepo` is the production adapter over
api/db/ (which also searches the built-in prop library and, when an embedder is set,
Atlas Vector Search). Both pass the same conformance suite
(tests/agent/test_agent_mongo_repo.py).
"""

from __future__ import annotations

import itertools
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, TypeVar, runtime_checkable

from pydantic import BaseModel

from schemas import Memory, MemoryResponse, Person, Prop, Question, SetDef, Telling

SAMPLE_MEMORY_PATH = Path(__file__).resolve().parents[2] / "web" / "public" / "samples" / "hymn_books.memory.json"

M = TypeVar("M", bound=BaseModel)
PS = TypeVar("PS", Prop, SetDef)


@runtime_checkable
class AgentRepo(Protocol):
    """Family-scoped data access for the agent. All methods are sync."""

    def get_person(self, family_id: str, person_id: str) -> Person | None: ...

    def list_people(self, family_id: str) -> list[Person]: ...

    def find_memories(
        self,
        family_id: str,
        *,
        person_id: str | None = None,
        query: str | None = None,
        limit: int = 10,
    ) -> list[Memory]: ...

    def get_memory(self, family_id: str, memory_id: str) -> Memory | None: ...

    def new_id(self, family_id: str, kind: str) -> str: ...

    def save_memory(self, family_id: str, memory: Memory) -> Memory: ...

    def create_question(
        self, family_id: str, *, person_id: str, text: str, memory_id: str | None
    ) -> Question: ...

    def list_props(self, family_id: str) -> list[Prop]: ...

    def get_prop(self, family_id: str, prop_id: str) -> Prop | None: ...

    def save_prop(self, family_id: str, prop: Prop) -> Prop: ...

    def list_sets(self, family_id: str) -> list[SetDef]: ...

    def get_set(self, family_id: str, set_id: str) -> SetDef | None: ...

    def save_set(self, family_id: str, set_def: SetDef) -> SetDef: ...

    def find_props(self, family_id: str, text: str, *, limit: int = 5) -> list[Prop]:
        """Props whose name or description matches `text`, best match first, at most `limit`."""
        ...

    def find_sets(self, family_id: str, text: str, *, limit: int = 5) -> list[SetDef]:
        """Sets whose name or description matches `text`, best match first, at most `limit`."""
        ...

    def put_media(self, family_id: str, data: bytes, content_type: str, *, kind: str) -> str: ...

    def get_media(self, family_id: str, media_id: str) -> tuple[bytes, str] | None: ...


@runtime_checkable
class SeedableRepo(AgentRepo, Protocol):
    """An AgentRepo that can also be seeded with people (tests, demo runs)."""

    def add_person(self, family_id: str, person: Person) -> Person: ...


# ------------------------------------------------------------------------- fake


def _copy(model: M) -> M:
    return model.model_copy(deep=True)


# Words that carry no meaning for matching a prop or set ("the old trumpet case").
_STOPWORDS = frozenset(
    "a an the of and or to in on at for with from by my our your his her their its this that "
    "these those some".split()
)
_WORD = re.compile(r"[^\W_]+")


def _stem(token: str) -> str:
    """Light plural stem: books -> book, boxes -> box, glasses -> glass, shoes -> shoe.

    Trailing 'es' goes after s, x, z, ch and sh; otherwise a trailing 's' goes, except in
    -ss, -us and -is words and words of three letters or fewer (bus, gas).
    """
    if len(token) <= 3:
        return token
    if token.endswith(("sses", "xes", "zes", "ches", "shes")):
        return token[:-2]
    if token.endswith("s") and not token.endswith(("ss", "us", "is")):
        return token[:-1]
    return token


def _tokens(text: str | None) -> set[str]:
    """Case-insensitive, stemmed word tokens with stopwords dropped."""
    if not text:
        return set()
    words = _WORD.findall(text.casefold().replace("'", "").replace("\u2019", ""))
    return {_stem(w) for w in words if w not in _STOPWORDS}


def _keyword_find(items: list[PS], text: str, limit: int) -> list[PS]:
    """Keyword match of `text` against each item's name + description.

    Score = shared tokens. An exact name match (same token set as the query) ranks first,
    then score descending, then name. Only score > 0 is returned.
    """
    if limit <= 0:
        return []
    query = _tokens(text)
    if not query:
        return []
    ranked: list[tuple[bool, int, str, str, PS]] = []
    for item in items:
        name = _tokens(item.name)
        shared = len(query & (name | _tokens(item.description)))
        if shared > 0:
            exact = name == query
            ranked.append((not exact, -shared, item.name.casefold(), item.id, item))
    ranked.sort(key=lambda r: r[:4])
    return [_copy(r[4]) for r in ranked[:limit]]


def _telling_key(telling: Telling) -> tuple[object, ...]:
    """What makes two tellings the same, ignoring created_at (as in MongoRepo)."""
    return (telling.author_id, telling.question, telling.text, tuple(telling.photo_ids))


def _answers(question: Question, memory_id: str, telling: Telling) -> bool:
    """Whether `telling` on `memory_id` answers this open question, as db.memories decides.

    Mirrors db.memories._close_answered_questions: the author's open question pointing at
    this memory, or one whose text equals telling.question after strip() and that is free
    (memory_id None) or bound to this same memory. A question bound to a different memory
    stays open even when its text is identical.
    """
    if question.status != "open" or question.person_id != telling.author_id:
        return False
    if question.memory_id == memory_id:
        return True
    asked = (telling.question or "").strip()
    return bool(asked) and question.text == asked and question.memory_id in (None, memory_id)


@dataclass
class _Media:
    data: bytes
    content_type: str
    kind: str


@dataclass
class _FamilyData:
    people: dict[str, Person] = field(default_factory=dict)
    memories: dict[str, Memory] = field(default_factory=dict)
    questions: dict[str, Question] = field(default_factory=dict)
    props: dict[str, Prop] = field(default_factory=dict)
    sets: dict[str, SetDef] = field(default_factory=dict)
    media: dict[str, _Media] = field(default_factory=dict)


class InMemoryRepo:
    """Dict-per-family fake of AgentRepo.

    Saving a memory with a new telling closes the questions it answers, as MongoRepo does
    through the data layer, so tests see the same question lifecycle on both.

    Models are deep-copied on the way in and on the way out, so a caller can never
    mutate stored state through an object it passed in or got back. Ids from `new_id`
    and `put_media` come from repo-wide counters, so they are deterministic and never
    collide across families.
    """

    def __init__(self) -> None:
        self._families: dict[str, _FamilyData] = defaultdict(_FamilyData)
        self._counters: dict[str, itertools.count[int]] = defaultdict(lambda: itertools.count(1))

    def _fam(self, family_id: str) -> _FamilyData | None:
        # Reads never create a family as a side effect.
        return self._families.get(family_id)

    # ------------------------------------------------------------------ people

    def add_person(self, family_id: str, person: Person) -> Person:
        """Seed helper (not part of AgentRepo): the agent never creates people directly."""
        if person.family_id != family_id:
            raise ValueError(f"person {person.id!r} belongs to family {person.family_id!r}, not {family_id!r}")
        self._families[family_id].people[person.id] = _copy(person)
        return _copy(person)

    def get_person(self, family_id: str, person_id: str) -> Person | None:
        fam = self._fam(family_id)
        person = fam.people.get(person_id) if fam else None
        return _copy(person) if person else None

    def list_people(self, family_id: str) -> list[Person]:
        fam = self._fam(family_id)
        return [_copy(p) for p in fam.people.values()] if fam else []

    # ---------------------------------------------------------------- memories

    def find_memories(
        self,
        family_id: str,
        *,
        person_id: str | None = None,
        query: str | None = None,
        limit: int = 10,
    ) -> list[Memory]:
        fam = self._fam(family_id)
        if fam is None or limit <= 0:
            return []
        needle = query.strip().lower() if query else ""
        found: list[Memory] = []
        for memory in fam.memories.values():
            if person_id is not None and person_id not in memory.participant_ids:
                continue
            if needle:
                haystack = " ".join([memory.title, *(t.text for t in memory.tellings)]).lower()
                if needle not in haystack:
                    continue
            found.append(memory)
        found.sort(key=lambda m: (m.year, m.id))
        return [_copy(m) for m in found[:limit]]

    def get_memory(self, family_id: str, memory_id: str) -> Memory | None:
        fam = self._fam(family_id)
        memory = fam.memories.get(memory_id) if fam else None
        return _copy(memory) if memory else None

    def new_id(self, family_id: str, kind: str) -> str:
        return f"{kind}_{next(self._counters[kind])}"

    def save_memory(self, family_id: str, memory: Memory) -> Memory:
        """Store the memory. Like MongoRepo (db.memories.create_memory / add_telling), every
        telling not in the stored version closes the author's open questions it answers
        (status 'answered'; see `_answers`)."""
        if memory.family_id != family_id:
            raise ValueError(f"memory {memory.id!r} belongs to family {memory.family_id!r}, not {family_id!r}")
        fam = self._families[family_id]
        stored = fam.memories.get(memory.id)
        known = {_telling_key(t) for t in stored.tellings} if stored else set()
        fam.memories[memory.id] = _copy(memory)
        for telling in memory.tellings:
            if _telling_key(telling) in known:
                continue
            for qid, question in fam.questions.items():
                if _answers(question, memory.id, telling):
                    fam.questions[qid] = question.model_copy(update={"status": "answered"})
        return _copy(memory)

    # --------------------------------------------------------------- questions

    def create_question(
        self, family_id: str, *, person_id: str, text: str, memory_id: str | None
    ) -> Question:
        question = Question(
            id=self.new_id(family_id, "question"),
            family_id=family_id,
            person_id=person_id,
            text=text,
            memory_id=memory_id,
            status="open",
        )
        self._families[family_id].questions[question.id] = question
        return _copy(question)

    def list_questions(self, family_id: str) -> list[Question]:
        """Test helper (not part of AgentRepo): every open question in the family, oldest first."""
        fam = self._fam(family_id)
        # Dicts keep insertion order, which is creation order.
        return [_copy(q) for q in fam.questions.values() if q.status == "open"] if fam else []

    # ------------------------------------------------------------------- props

    def list_props(self, family_id: str) -> list[Prop]:
        fam = self._fam(family_id)
        return [_copy(p) for p in fam.props.values()] if fam else []

    def get_prop(self, family_id: str, prop_id: str) -> Prop | None:
        fam = self._fam(family_id)
        prop = fam.props.get(prop_id) if fam else None
        return _copy(prop) if prop else None

    def save_prop(self, family_id: str, prop: Prop) -> Prop:
        # Library props (family_id None) may be saved into a family's view of the library.
        if prop.family_id not in (None, family_id):
            raise ValueError(f"prop {prop.id!r} belongs to family {prop.family_id!r}, not {family_id!r}")
        self._families[family_id].props[prop.id] = _copy(prop)
        return _copy(prop)

    # -------------------------------------------------------------------- sets

    def list_sets(self, family_id: str) -> list[SetDef]:
        fam = self._fam(family_id)
        return [_copy(s) for s in fam.sets.values()] if fam else []

    def get_set(self, family_id: str, set_id: str) -> SetDef | None:
        fam = self._fam(family_id)
        set_def = fam.sets.get(set_id) if fam else None
        return _copy(set_def) if set_def else None

    def save_set(self, family_id: str, set_def: SetDef) -> SetDef:
        if set_def.family_id not in (None, family_id):
            raise ValueError(f"set {set_def.id!r} belongs to family {set_def.family_id!r}, not {family_id!r}")
        self._families[family_id].sets[set_def.id] = _copy(set_def)
        return _copy(set_def)

    # ------------------------------------------------------------------ search

    def find_props(self, family_id: str, text: str, *, limit: int = 5) -> list[Prop]:
        fam = self._fam(family_id)
        return _keyword_find(list(fam.props.values()), text, limit) if fam else []

    def find_sets(self, family_id: str, text: str, *, limit: int = 5) -> list[SetDef]:
        fam = self._fam(family_id)
        return _keyword_find(list(fam.sets.values()), text, limit) if fam else []

    # ------------------------------------------------------------------- media

    def put_media(self, family_id: str, data: bytes, content_type: str, *, kind: str) -> str:
        media_id = f"media/{next(self._counters['media'])}"
        self._families[family_id].media[media_id] = _Media(bytes(data), content_type, kind)
        return media_id

    def get_media(self, family_id: str, media_id: str) -> tuple[bytes, str] | None:
        """Accepts 'media/<id>' or the bare '<id>', like the MongoDB adapter."""
        fam = self._fam(family_id)
        if fam is None or not isinstance(media_id, str):
            return None
        key = media_id if media_id.startswith("media/") else f"media/{media_id}"
        media = fam.media.get(key)
        return (media.data, media.content_type) if media else None


def load_sample_family(repo: SeedableRepo, family_id: str, path: Path = SAMPLE_MEMORY_PATH) -> None:
    """Seed people, props and sets from the hymn_books sample into `family_id`.

    Family ids are rewritten to `family_id`; library props and sets (family_id None)
    keep None but are stored in this family's view. The memory itself is not seeded.
    """
    sample = MemoryResponse.model_validate_json(path.read_text())
    for person in sample.people:
        repo.add_person(family_id, person.model_copy(update={"family_id": family_id}))
    for prop in sample.props:
        owner = None if prop.family_id is None else family_id
        repo.save_prop(family_id, prop.model_copy(update={"family_id": owner}))
    for set_def in sample.sets:
        owner = None if set_def.family_id is None else family_id
        repo.save_set(family_id, set_def.model_copy(update={"family_id": owner}))
