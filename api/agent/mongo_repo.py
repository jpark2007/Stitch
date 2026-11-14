"""`MongoRepo`: the production AgentRepo over api/db/ (ARCHITECTURE.md sections 2 and 5).

Every method delegates to the data layer (db.people, db.memories, db.props, db.media,
db.search), which filters every read on `familyId`. So, like InMemoryRepo, a lookup in
another family returns None or an empty list and never raises.

Differences from InMemoryRepo that callers may notice (all deliberate):

- Ids are global in MongoDB (`_id`), so two families cannot hold a person, memory, prop
  or set with the same id. `new_id` hands out ObjectId-based ids, which never collide.
- `save_memory` writes through the data layer's own operations (create_memory,
  add_telling, link_participants, save_scene), so the stored version is owned by the db:
  it goes up by one when the scene (screenplay, timeline, props, sets or title) changes
  on a memory that already had a timeline, and never otherwise. Changes the data layer
  has no operation for (removing a participant or telling, changing the year) are not
  applied. Tellings get a `created_at` when they had none.
- `get_prop` and `find_props` also see the built-in prop library (shared/props/), and
  `find_props` / `find_sets` use Atlas Vector Search too when an embedder is set
  (db.search.set_embedder). `list_props` is the family's saved props only.
- `put_media` needs non-empty bytes (db.media.put_media raises ValueError otherwise).
"""

from __future__ import annotations

from typing import Any, cast

from bson import ObjectId
from pymongo.database import Database

from db import media as db_media
from db import memories as db_memories
from db import people as db_people
from db import props as db_props
from db import search as db_search
from schemas import Memory, NewPerson, Person, Prop, Question, SetDef, Telling

# How many search hits to pull before the person filter and the oldest-first sort.
_SEARCH_POOL = 50


def _same_scene_part(a: Any, b: Any) -> bool:
    """Compare a screenplay or timeline the way db.memories stores it (None-free JSON)."""
    if a is None or b is None:
        return a is None and b is None
    dump = {"mode": "json", "by_alias": True, "exclude_none": True}
    return a.model_dump(**dump) == b.model_dump(**dump)


def _telling_key(telling: Telling) -> tuple[Any, ...]:
    """What makes two tellings the same, ignoring created_at (the db stamps it)."""
    return (telling.author_id, telling.question, telling.text, tuple(telling.photo_ids))


class MongoRepo:
    """AgentRepo over a pymongo (or mongomock) Database. All methods are sync."""

    def __init__(self, db: Database) -> None:
        self.db = db

    # ------------------------------------------------------------------ people

    def add_person(self, family_id: str, person: Person) -> Person:
        """Seed helper (not part of AgentRepo), mirroring InMemoryRepo.add_person.

        Inserts through db.people.create_person with the person's own id. Spouses not yet
        in the family are skipped: create_person links spouses both ways, so seeding the
        second spouse restores the link. Parents must already exist.
        """
        if person.family_id != family_id:
            raise ValueError(f"person {person.id!r} belongs to family {person.family_id!r}, not {family_id!r}")
        existing = {p.id for p in db_people.get_people(self.db, family_id, person.spouse_ids)}
        new = NewPerson(
            name=person.name,
            born=person.born,
            died=person.died,
            parent_ids=list(person.parent_ids),
            spouse_ids=[sid for sid in person.spouse_ids if sid in existing],
            character=person.character,
        )
        db_people.create_person(
            self.db, family_id, new, added_by=person.added_by, user_id=person.user_id, person_id=person.id
        )
        stored = db_people.get_person(self.db, family_id, person.id)
        assert stored is not None
        return stored

    def get_person(self, family_id: str, person_id: str) -> Person | None:
        return db_people.get_person(self.db, family_id, person_id)

    def list_people(self, family_id: str) -> list[Person]:
        return db_people.list_people(self.db, family_id)

    # ---------------------------------------------------------------- memories

    def _all_memories(self, family_id: str) -> list[Memory]:
        """Every memory in the family, by year, then by when it was added."""
        ids = [s.id for s in db_memories.moment_summaries(self.db, family_id)]
        return db_memories.get_memories(self.db, family_id, ids)

    def find_memories(
        self,
        family_id: str,
        *,
        person_id: str | None = None,
        query: str | None = None,
        limit: int = 10,
    ) -> list[Memory]:
        """Memories matching the filters, oldest first, at most `limit`.

        person_id: only memories this person is in (db.memories.get_book).
        query: the case-insensitive substring matches over the title and tellings (as in
        InMemoryRepo) first, then db.search.search_memories hits by relevance (words in
        any order, plurals, vectors when an embedder is set). The `limit` most relevant
        are returned, sorted oldest first.
        """
        if limit <= 0:
            return []
        if person_id is not None:
            pool = db_memories.get_book(self.db, family_id, person_id)
        else:
            pool = self._all_memories(family_id)
        needle = query.strip().casefold() if query else ""
        if not needle:
            return pool[:limit]

        by_id = {m.id: m for m in pool}
        ranked: list[str] = []
        for memory in pool:
            haystack = " ".join([memory.title, *(t.text for t in memory.tellings)]).casefold()
            if needle in haystack:
                ranked.append(memory.id)
        hits = db_search.search_memories(self.db, family_id, query or "", limit=max(_SEARCH_POOL, limit))
        for hit in hits:
            if hit.memory_id in by_id and hit.memory_id not in ranked:
                ranked.append(hit.memory_id)
        chosen = set(ranked[:limit])
        # The pool is already oldest first, so filtering it keeps that order.
        return [m for m in pool if m.id in chosen]

    def get_memory(self, family_id: str, memory_id: str) -> Memory | None:
        return db_memories.get_memory(self.db, family_id, memory_id)

    def new_id(self, family_id: str, kind: str) -> str:
        return f"{kind}_{ObjectId()}"

    def save_memory(self, family_id: str, memory: Memory) -> Memory:
        """Create or update the memory; returns it as stored. See the module docstring."""
        if memory.family_id != family_id:
            raise ValueError(f"memory {memory.id!r} belongs to family {memory.family_id!r}, not {family_id!r}")
        stored = db_memories.get_memory(self.db, family_id, memory.id)
        if stored is None:
            self._create_memory(family_id, memory)
        else:
            self._update_memory(family_id, memory, stored)
        saved = db_memories.get_memory(self.db, family_id, memory.id)
        assert saved is not None
        return saved

    def _create_memory(self, family_id: str, memory: Memory) -> None:
        first, *rest = memory.tellings or [None]
        db_memories.create_memory(
            self.db,
            family_id,
            memory_id=memory.id,
            title=memory.title,
            year=memory.year,
            participant_ids=list(memory.participant_ids),
            telling=first,
            prop_ids=list(memory.prop_ids),
            set_ids=list(memory.set_ids),
        )
        for telling in rest:
            if telling is not None:
                db_memories.add_telling(self.db, family_id, memory.id, telling)
        if memory.screenplay is not None or memory.timeline is not None:
            # The first scene save on a memory with no timeline keeps version 1.
            db_memories.save_scene(
                self.db,
                family_id,
                memory.id,
                screenplay=memory.screenplay,
                timeline=memory.timeline,
                prop_ids=list(memory.prop_ids),
                set_ids=list(memory.set_ids),
            )

    def _update_memory(self, family_id: str, memory: Memory, stored: Memory) -> None:
        stored_tellings = {_telling_key(t) for t in stored.tellings}
        for telling in memory.tellings:
            if _telling_key(telling) not in stored_tellings:
                # Appends, or replaces the author's own earlier telling.
                db_memories.add_telling(self.db, family_id, memory.id, telling)

        new_people = [pid for pid in memory.participant_ids if pid not in stored.participant_ids]
        if new_people:
            db_memories.link_participants(self.db, family_id, memory.id, new_people)

        scene_changed = (
            not _same_scene_part(memory.screenplay, stored.screenplay)
            or not _same_scene_part(memory.timeline, stored.timeline)
            or list(dict.fromkeys(memory.prop_ids)) != stored.prop_ids
            or list(dict.fromkeys(memory.set_ids)) != stored.set_ids
            or memory.title != stored.title
        )
        if scene_changed:
            db_memories.save_scene(
                self.db,
                family_id,
                memory.id,
                screenplay=memory.screenplay,
                timeline=memory.timeline,
                prop_ids=list(memory.prop_ids),
                set_ids=list(memory.set_ids),
                title=memory.title,
            )

    # --------------------------------------------------------------- questions

    def create_question(
        self, family_id: str, *, person_id: str, text: str, memory_id: str | None
    ) -> Question:
        return db_memories.create_question(
            self.db, family_id, person_id=person_id, text=text, memory_id=memory_id
        )

    def list_questions(self, family_id: str) -> list[Question]:
        """Test helper (not part of AgentRepo): every open question in the family, oldest first."""
        found: list[Question] = []
        for person in db_people.list_people(self.db, family_id):
            found.extend(db_memories.open_questions(self.db, family_id, person.id))
        # open_questions is newest first per person; ObjectId ids sort by creation time.
        return sorted(found, key=lambda q: q.id)

    # ------------------------------------------------------------------- props

    def list_props(self, family_id: str) -> list[Prop]:
        return db_props.list_props(self.db, family_id)

    def get_prop(self, family_id: str, prop_id: str) -> Prop | None:
        return db_props.get_prop(self.db, family_id, prop_id)

    def save_prop(self, family_id: str, prop: Prop) -> Prop:
        """Replace the family's prop with this id, else insert it under prop.id.

        A library prop (family_id None) saved here becomes the family's own copy.
        """
        if prop.family_id not in (None, family_id):
            raise ValueError(f"prop {prop.id!r} belongs to family {prop.family_id!r}, not {family_id!r}")
        replaced = db_props.replace_prop(self.db, family_id, prop)
        if replaced is not None:
            return replaced
        return db_props.insert_prop(self.db, family_id, prop, prop_id=prop.id)

    def find_props(self, family_id: str, text: str, *, limit: int = 5) -> list[Prop]:
        return db_props.find_props(self.db, family_id, text, vector=db_search.embed(text), limit=limit)

    # -------------------------------------------------------------------- sets

    def list_sets(self, family_id: str) -> list[SetDef]:
        return db_props.list_sets(self.db, family_id)

    def get_set(self, family_id: str, set_id: str) -> SetDef | None:
        return db_props.get_set(self.db, family_id, set_id)

    def save_set(self, family_id: str, set_def: SetDef) -> SetDef:
        if set_def.family_id not in (None, family_id):
            raise ValueError(f"set {set_def.id!r} belongs to family {set_def.family_id!r}, not {family_id!r}")
        replaced = db_props.replace_set(self.db, family_id, set_def)
        if replaced is not None:
            return replaced
        return db_props.insert_set(self.db, family_id, set_def, set_id=set_def.id)

    def find_sets(self, family_id: str, text: str, *, limit: int = 5) -> list[SetDef]:
        return db_props.find_sets(self.db, family_id, text, vector=db_search.embed(text), limit=limit)

    # ------------------------------------------------------------------- media

    def put_media(self, family_id: str, data: bytes, content_type: str, *, kind: str) -> str:
        """Store bytes and return 'media/<id>'. Kinds the db does not know are stored as 'other'."""
        db_kind = cast(db_media.MediaKind, kind if kind in db_media.MEDIA_KINDS else "other")
        media_id = db_media.put_media(self.db, family_id, data, content_type=content_type, kind=db_kind)
        return db_media.media_ref(media_id)

    def get_media(self, family_id: str, media_id: str) -> tuple[bytes, str] | None:
        """(bytes, content_type) for 'media/<id>' or a bare id; None for a bad or other-family id."""
        if db_media.parse_media_ref(media_id) is None:
            return None
        found = db_media.get_media(self.db, family_id, media_id)
        return (found.data, found.content_type) if found else None
