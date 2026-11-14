"""Memory reads and writes: moments, tellings, scenes, the book query, threads, questions.

Collections (ARCHITECTURE.md section 5):

- `memories`: {_id, familyId, title, year, participantIds, tellings, screenplay, timeline,
  propIds, setIds, version, embedding, createdAt}. `era` is never stored; it is derived
  from `year` on every read (shared/vocabulary.json `eras`).
- `questions`: {_id, familyId, personId, text, memoryId, status, createdAt}.

Every function takes `family_id` and every filter includes `familyId`, so another
family's id reads as "not found" (None, False or empty), never as an error.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any, get_args

import bson
from bson import ObjectId
from pymongo import ASCENDING, DESCENDING, ReturnDocument
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError

import schemas
from db import search as search_db
from screenplay.schema import VOCABULARY, Screenplay, Timeline

log = logging.getLogger(__name__)

# Stored fields that are never part of an API model.
_STORAGE_ONLY = ("embedding", "createdAt")
_SCENE_FIELDS = ("screenplay", "timeline")
_QUESTION_STATUSES = frozenset(get_args(schemas.QuestionStatus))

# The most a save_scene update may carry (screenplay, timeline and the rest of its $set).
# MongoDB refuses any document over 16 MB (BSONObjectTooLarge, or pymongo's
# DocumentTooLarge); mongomock stores it. Checking here makes both refuse the same scene
# the same way, with room left in the memory document for its tellings and embedding.
MAX_SCENE_BYTES = 12 * 1024 * 1024


class VersionConflict(Exception):
    """`save_scene(expected_version=n)`: the stored memory is no longer at version n.

    Someone saved the scene since it was read; nothing was written. Re-read and retry.
    """


# ------------------------------------------------------------------------------ helpers

def era_for_year(year: int) -> str | None:
    """The era id whose [from, to] range contains `year`, or None."""
    for era in VOCABULARY["eras"]:
        if era["from"] <= year <= era["to"]:
            return era["id"]
    return None


def _new_id() -> str:
    return str(ObjectId())


def _now(now: datetime | None = None) -> datetime:
    return _bson_ms(now if now is not None else datetime.now(UTC))


def _bson_ms(value: datetime) -> datetime:
    """Aware UTC, truncated to the millisecond a BSON date keeps.

    What a write returns then equals what every later read returns, on mongomock and real
    MongoDB alike (both drop the microseconds). A naive value is UTC, as pymongo stores it.
    """
    value = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    return value.replace(microsecond=value.microsecond // 1000 * 1000)


def _dedupe(ids: list[str]) -> list[str]:
    return list(dict.fromkeys(ids))


def _require_people(db: Database, family_id: str, person_ids: list[str]) -> None:
    """ValueError unless every id is a person in this family."""
    wanted = set(person_ids)
    if not wanted:
        return
    found = {
        doc["_id"]
        for doc in db["people"].find({"familyId": family_id, "_id": {"$in": list(wanted)}}, {"_id": 1})
    }
    missing = sorted(wanted - found)
    if missing:
        raise ValueError(f"unknown person id(s) in this family: {', '.join(missing)}")


def _telling_doc(telling: schemas.Telling, now: datetime | None = None) -> dict[str, Any]:
    doc = telling.model_dump(by_alias=True, exclude_none=True)
    created = doc.get("createdAt")
    doc["createdAt"] = _bson_ms(created) if isinstance(created, datetime) else _now(now)
    return doc


def _scene_doc(model: Screenplay | Timeline) -> dict[str, Any]:
    return model.model_dump(mode="json", by_alias=True, exclude_none=True)


def _to_memory(doc: dict[str, Any] | None) -> schemas.Memory | None:
    if doc is None:
        return None
    data = {k: v for k, v in doc.items() if k not in _STORAGE_ONLY and k != "_id"}
    data["id"] = doc["_id"]
    data["era"] = era_for_year(doc["year"])
    return schemas.Memory.model_validate(data)


def _to_question(doc: dict[str, Any] | None) -> schemas.Question | None:
    if doc is None:
        return None
    return schemas.Question(
        id=doc["_id"],
        family_id=doc["familyId"],
        person_id=doc["personId"],
        text=doc["text"],
        memory_id=doc.get("memoryId"),
        status=doc.get("status", "open"),
    )


def _close_answered_questions(
    db: Database, family_id: str, memory_id: str, telling: schemas.Telling
) -> int:
    """Mark the author's open questions that this telling answers as 'answered'.

    A question is answered when it points at this memory, or when the telling was given in
    reply to it: its text equals `telling.question` after strip() AND it is a free question
    (memoryId None or absent) or one bound to this same memory. A question bound to a
    DIFFERENT memory stays open even when its text is identical (two moments both titled
    "Christmas" get the same linker question text). Only the author's own questions in this
    family are touched. Returns how many were closed.

    `{"memoryId": {"$in": [None, memory_id]}}` matches a null and a missing memoryId alike,
    on mongomock and on real MongoDB.
    """
    matches: list[dict[str, Any]] = [{"memoryId": memory_id}]
    question = (telling.question or "").strip()
    if question:
        matches.append({"text": question, "memoryId": {"$in": [None, memory_id]}})
    result = db["questions"].update_many(
        {"familyId": family_id, "personId": telling.author_id, "status": "open", "$or": matches},
        {"$set": {"status": "answered"}},
    )
    return result.modified_count


def _refresh_embedding(db: Database, family_id: str, memory_id: str) -> None:
    """Re-embed the memory's title and tellings for Vector Search. Never raises.

    Does nothing when no embedder is installed (search_db.embed returns None), which is the
    default and always the case in tests.
    """
    try:
        doc = db["memories"].find_one(
            {"_id": memory_id, "familyId": family_id}, {"title": 1, "tellings.text": 1}
        )
        if doc is None:
            return
        texts = [t.get("text") or "" for t in doc.get("tellings") or [] if isinstance(t, dict)]
        vector = search_db.embed((doc.get("title") or "") + "\n\n" + "\n\n".join(texts))
        if vector is None:
            return
        db["memories"].update_one(
            {"_id": memory_id, "familyId": family_id},
            {"$set": {"embedding": [float(x) for x in vector]}},
        )
    except Exception:
        log.warning("could not refresh the embedding of memory %s", memory_id, exc_info=True)


# ----------------------------------------------------------------------------- memories

def create_memory(
    db: Database,
    family_id: str,
    *,
    title: str,
    year: int,
    participant_ids: list[str],
    telling: schemas.Telling | None = None,
    prop_ids: list[str] | None = None,
    set_ids: list[str] | None = None,
    memory_id: str | None = None,
    now: datetime | None = None,
) -> schemas.Memory:
    """Insert a new moment at version 1. Participants and the telling's author must be in the family.

    After the insert, the telling's author's open questions that it answers are closed (see
    `_close_answered_questions`: the question points at this memory, or its text is the
    telling's `question` after strip() and it is not bound to another memory), and the
    memory's embedding is refreshed when an
    embedder is installed (`_refresh_embedding`, which never raises).
    """
    if not title or not title.strip():
        raise ValueError("title is required")
    if not isinstance(year, int) or isinstance(year, bool):
        raise ValueError("year must be an int")
    participants = _dedupe(participant_ids)
    _require_people(db, family_id, participants + ([telling.author_id] if telling else []))

    created = _now(now)
    doc: dict[str, Any] = {
        "_id": memory_id or _new_id(),
        "familyId": family_id,
        "title": title,
        "year": year,
        "participantIds": participants,
        "tellings": [_telling_doc(telling, created)] if telling else [],
        "propIds": _dedupe(prop_ids or []),
        "setIds": _dedupe(set_ids or []),
        "version": 1,
        "createdAt": created,
    }
    try:
        db["memories"].insert_one(doc)
    except DuplicateKeyError as exc:
        raise ValueError(f"memory id already exists: {doc['_id']}") from exc
    if telling is not None:
        _close_answered_questions(db, family_id, doc["_id"], telling)
    _refresh_embedding(db, family_id, doc["_id"])
    memory = _to_memory(doc)
    assert memory is not None
    return memory


def get_memory(db: Database, family_id: str, memory_id: str) -> schemas.Memory | None:
    return _to_memory(db["memories"].find_one({"_id": memory_id, "familyId": family_id}, {"embedding": 0}))


def get_memories(db: Database, family_id: str, memory_ids: list[str]) -> list[schemas.Memory]:
    """The family's memories with these ids, in input order. Unknown ids are skipped, repeats collapse."""
    ids = _dedupe(memory_ids)
    if not ids:
        return []
    docs = {
        doc["_id"]: doc
        for doc in db["memories"].find({"familyId": family_id, "_id": {"$in": ids}}, {"embedding": 0})
    }
    return [m for m in (_to_memory(docs.get(i)) for i in ids) if m is not None]


def get_book(db: Database, family_id: str, person_id: str) -> list[schemas.Memory]:
    """Every memory this person is in, ordered by year, then by when it was added."""
    cursor = db["memories"].find(
        {"familyId": family_id, "participantIds": person_id}, {"embedding": 0}
    ).sort([("year", ASCENDING), ("createdAt", ASCENDING), ("_id", ASCENDING)])
    return [m for m in map(_to_memory, cursor) if m is not None]


def add_telling(
    db: Database, family_id: str, memory_id: str, telling: schemas.Telling
) -> schemas.Memory | None:
    """Replace the author's existing telling (editing your own), otherwise append it.

    None when the memory is not in this family; nothing else is touched then. After a
    successful write, the author's open questions that this telling answers are closed (the
    question points at this memory, or its text is the telling's `question` after strip()
    and it is not bound to another memory); other people's and other families' questions are never touched. The memory's embedding
    is then refreshed when an embedder is installed (never raises).
    """
    memories = db["memories"]
    base = {"_id": memory_id, "familyId": family_id}
    if memories.count_documents(base, limit=1) == 0:
        return None
    author = telling.author_id
    _require_people(db, family_id, [author])
    doc = _telling_doc(telling)

    # Replace by explicit index, guarded so the write only lands if that slot still belongs
    # to the author (or, for an append, while the author still has no telling). The guard
    # makes concurrent edits retry instead of clobbering. The positional `$` operator is
    # avoided on purpose: mongomock resolves it to the wrong element.
    for _ in range(5):
        current = memories.find_one(base, {"tellings.authorId": 1})
        if current is None:
            return None
        authors = [t.get("authorId") for t in current.get("tellings", [])]
        if author in authors:
            idx = authors.index(author)
            guard = {**base, f"tellings.{idx}.authorId": author}
            update = {"$set": {f"tellings.{idx}": doc}}
        else:
            guard = {**base, "tellings.authorId": {"$ne": author}}
            update = {"$push": {"tellings": doc}}
        updated = memories.find_one_and_update(
            guard, update, projection={"embedding": 0}, return_document=ReturnDocument.AFTER
        )
        if updated is not None:
            _close_answered_questions(db, family_id, memory_id, telling)
            _refresh_embedding(db, family_id, memory_id)
            return _to_memory(updated)
    raise RuntimeError(f"telling for {author} on memory {memory_id} kept changing; try again")


def link_participants(
    db: Database, family_id: str, memory_id: str, person_ids: list[str]
) -> schemas.Memory | None:
    """Add people to a moment (deduped). Every id must be a person in this family."""
    memories = db["memories"]
    if memories.count_documents({"_id": memory_id, "familyId": family_id}, limit=1) == 0:
        return None
    ids = _dedupe(person_ids)
    _require_people(db, family_id, ids)
    updated = memories.find_one_and_update(
        {"_id": memory_id, "familyId": family_id},
        {"$addToSet": {"participantIds": {"$each": ids}}},
        projection={"embedding": 0},
        return_document=ReturnDocument.AFTER,
    )
    return _to_memory(updated)


def save_scene(
    db: Database,
    family_id: str,
    memory_id: str,
    *,
    screenplay: Screenplay | None,
    timeline: Timeline | None,
    prop_ids: list[str],
    set_ids: list[str],
    title: str | None = None,
    expected_version: int | None = None,
) -> schemas.Memory | None:
    """Store the screenplay and compiled timeline.

    The first save on a memory that has no timeline yet keeps version 1; every later save
    increments the version. A None screenplay or timeline clears the stored one. None when
    the memory is not in this family.

    `expected_version` makes the write conditional (optimistic concurrency): with an int, the
    write lands only while the stored version still equals it (the first save, too, which
    keeps that version), otherwise `VersionConflict` is raised and nothing is written. Use it
    when the new scene was built from a read of the memory, so a scene saved in between
    (another teller's narration track) is never silently overwritten. With None (the
    default) the save is unconditional, as it always was.

    ValueError, before anything is written, when the scene is larger than MAX_SCENE_BYTES
    as BSON: real MongoDB would refuse the document (16 MB limit) where mongomock keeps it.
    """
    to_set: dict[str, Any] = {"propIds": _dedupe(prop_ids), "setIds": _dedupe(set_ids)}
    to_unset: dict[str, Any] = {}
    for field, value in (("screenplay", screenplay), ("timeline", timeline)):
        if value is None:
            to_unset[field] = ""
        else:
            to_set[field] = _scene_doc(value)
    if title is not None:
        if not title.strip():
            raise ValueError("title must not be empty")
        to_set["title"] = title

    size = len(bson.encode(to_set))
    if size > MAX_SCENE_BYTES:
        raise ValueError(
            f"scene is too large to store ({size} bytes, at most {MAX_SCENE_BYTES}); "
            "MongoDB documents are limited to 16 MB"
        )
    update: dict[str, Any] = {"$set": to_set}
    if to_unset:
        update["$unset"] = to_unset
    base: dict[str, Any] = {"_id": memory_id, "familyId": family_id}
    later: dict[str, Any] = dict(base)
    if expected_version is not None:
        if not isinstance(expected_version, int) or isinstance(expected_version, bool):
            raise ValueError("expected_version must be an int")
        base["version"] = expected_version
        later = {**base, "timeline": {"$ne": None}}
    memories = db["memories"]

    # `timeline: None` matches a missing or null timeline: the first save, no version bump.
    updated = memories.find_one_and_update(
        {**base, "timeline": None}, update, projection={"embedding": 0}, return_document=ReturnDocument.AFTER
    )
    if updated is None:
        updated = memories.find_one_and_update(
            later,
            {**update, "$inc": {"version": 1}},
            projection={"embedding": 0},
            return_document=ReturnDocument.AFTER,
        )
    if updated is None and expected_version is not None:
        current = memories.find_one({"_id": memory_id, "familyId": family_id}, {"version": 1})
        if current is not None:
            raise VersionConflict(
                f"memory {memory_id} is at version {current.get('version')}, not {expected_version}"
            )
    return _to_memory(updated)


def moment_summaries(db: Database, family_id: str) -> list[schemas.MomentSummary]:
    """Every moment in the family without its scene, ordered by year, then by when it was added."""
    cursor = db["memories"].find(
        {"familyId": family_id},
        {"_id": 1, "title": 1, "year": 1, "participantIds": 1, "tellings.authorId": 1},
    ).sort([("year", ASCENDING), ("createdAt", ASCENDING), ("_id", ASCENDING)])
    return [
        schemas.MomentSummary(
            id=doc["_id"],
            title=doc["title"],
            year=doc["year"],
            participant_ids=doc.get("participantIds", []),
            era=era_for_year(doc["year"]),
            telling_author_ids=_dedupe([t["authorId"] for t in doc.get("tellings", []) if "authorId" in t]),
        )
        for doc in cursor
    ]


def set_embedding(db: Database, family_id: str, memory_id: str, vector: list[float]) -> bool:
    """Store the Vector Search embedding. False when the memory is not in this family."""
    if not vector:
        raise ValueError("embedding vector is empty")
    result = db["memories"].update_one(
        {"_id": memory_id, "familyId": family_id}, {"$set": {"embedding": [float(x) for x in vector]}}
    )
    return result.matched_count == 1


# ---------------------------------------------------------------------------- questions

def create_question(
    db: Database,
    family_id: str,
    *,
    person_id: str,
    text: str,
    memory_id: str | None = None,
    question_id: str | None = None,
    now: datetime | None = None,
) -> schemas.Question:
    """A pending prompt for a person. The person (and memory, if given) must be in this family.

    `question_id` and `now` fix the id and creation time (a seed that must write the same
    data on every run); ValueError when that id is already taken.
    """
    if not text or not text.strip():
        raise ValueError("question text is required")
    if question_id is not None and (not isinstance(question_id, str) or not question_id.strip()):
        raise ValueError("question_id must be a non-empty string")
    _require_people(db, family_id, [person_id])
    if memory_id is not None and db["memories"].count_documents({"_id": memory_id, "familyId": family_id}, limit=1) == 0:
        raise ValueError(f"unknown memory id in this family: {memory_id}")
    doc = {
        "_id": question_id or _new_id(),
        "familyId": family_id,
        "personId": person_id,
        "text": text,
        "memoryId": memory_id,
        "status": "open",
        "createdAt": _now(now),
    }
    try:
        db["questions"].insert_one(doc)
    except DuplicateKeyError as exc:
        raise ValueError(f"question id already exists: {doc['_id']}") from exc
    question = _to_question(doc)
    assert question is not None
    return question


def open_questions(db: Database, family_id: str, person_id: str) -> list[schemas.Question]:
    """The person's open questions, newest first."""
    cursor = db["questions"].find({"familyId": family_id, "personId": person_id, "status": "open"}).sort(
        [("createdAt", DESCENDING), ("_id", DESCENDING)]
    )
    return [q for q in map(_to_question, cursor) if q is not None]


def set_question_status(
    db: Database, family_id: str, question_id: str, status: schemas.QuestionStatus
) -> schemas.Question | None:
    if status not in _QUESTION_STATUSES:
        raise ValueError(f"invalid question status: {status!r}")
    updated = db["questions"].find_one_and_update(
        {"_id": question_id, "familyId": family_id},
        {"$set": {"status": status}},
        return_document=ReturnDocument.AFTER,
    )
    return _to_question(updated)
