"""The Book of Family Traditions: things a family does again and again.

Collection `traditions`: {_id, familyId, title, description, category, when: {kind,
label, month}, participantIds, photoIds, startedYear, createdBy, createdAt, updatedAt}.

Every function takes the database first and the family id second, and every filter
includes `familyId`, so another family's tradition id reads exactly like an unknown one
(None or False), never as an error. Bounds and trimming live on the request models
(schemas.CreateTraditionRequest / UpdateTraditionRequest); whether participantIds name
people of this family and photoIds name its media is checked by the route
(routes/traditions.py), which answers 400 like a memory's participants.

Stored documents use camelCase fields and a string `_id`; the API `id` is `_id`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from bson import ObjectId
from pymongo import ASCENDING, ReturnDocument
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError

import schemas

COLLECTION = "traditions"


# ------------------------------------------------------------------------------ helpers

def _new_id() -> str:
    return str(ObjectId())


def _now(now: datetime | None = None) -> datetime:
    """Aware UTC, truncated to the millisecond a BSON date keeps (as db/memories.py `_now`),
    so what a write returns equals what every later read returns."""
    value = now if now is not None else datetime.now(UTC)
    value = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    return value.replace(microsecond=value.microsecond // 1000 * 1000)


def _dedupe(ids: list[str]) -> list[str]:
    return list(dict.fromkeys(ids))


def _utc(value: datetime) -> datetime:
    """A naive datetime (a client without tz_aware) is UTC, as pymongo stores it."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def _when_doc(when: schemas.TraditionWhen) -> dict[str, Any]:
    return {"kind": when.kind, "label": when.label, "month": when.month}


def _to_tradition(doc: dict[str, Any] | None) -> schemas.Tradition | None:
    """Stored document to API model. Only known fields are copied, so storage-only ones drop."""
    if doc is None:
        return None
    return schemas.Tradition.model_validate(
        {
            "id": doc["_id"],
            "familyId": doc["familyId"],
            "title": doc["title"],
            "description": doc.get("description") or "",
            "category": doc["category"],
            "when": doc["when"],
            "participantIds": list(doc.get("participantIds") or []),
            "photoIds": list(doc.get("photoIds") or []),
            "startedYear": doc.get("startedYear"),
            "createdBy": doc["createdBy"],
            "createdAt": _utc(doc["createdAt"]),
            "updatedAt": _utc(doc.get("updatedAt") or doc["createdAt"]),
        }
    )


# ------------------------------------------------------------------------------- writes

def create_tradition(
    db: Database,
    family_id: str,
    body: schemas.CreateTraditionRequest,
    *,
    created_by: str,
    tradition_id: str | None = None,
    now: datetime | None = None,
) -> schemas.Tradition:
    """Insert a tradition. Repeated participant and photo ids collapse, first one kept.

    ValueError when `tradition_id` is given and already taken.
    """
    created = _now(now)
    doc: dict[str, Any] = {
        "_id": tradition_id or _new_id(),
        "familyId": family_id,
        "title": body.title,
        "description": body.description,
        "category": body.category,
        "when": _when_doc(body.when),
        "participantIds": _dedupe(body.participant_ids),
        "photoIds": _dedupe(body.photo_ids),
        "startedYear": body.started_year,
        "createdBy": created_by,
        "createdAt": created,
        "updatedAt": created,
    }
    try:
        db[COLLECTION].insert_one(doc)
    except DuplicateKeyError as exc:
        raise ValueError(f"tradition id already exists: {doc['_id']}") from exc
    tradition = _to_tradition(doc)
    assert tradition is not None
    return tradition


def update_tradition(
    db: Database,
    family_id: str,
    tradition_id: str,
    body: schemas.UpdateTraditionRequest,
    *,
    now: datetime | None = None,
) -> schemas.Tradition | None:
    """PATCH semantics. A field that is omitted or null is left as it is, except
    `startedYear`, where an explicit null clears it (told apart with model_fields_set).
    `when` is replaced whole. `updatedAt` moves only when something was sent.

    None when the tradition does not exist in this family.
    """
    sent = body.model_fields_set
    changes: dict[str, Any] = {}
    if body.title is not None:
        changes["title"] = body.title
    if body.description is not None:
        changes["description"] = body.description
    if body.category is not None:
        changes["category"] = body.category
    if body.when is not None:
        changes["when"] = _when_doc(body.when)
    if body.participant_ids is not None:
        changes["participantIds"] = _dedupe(body.participant_ids)
    if body.photo_ids is not None:
        changes["photoIds"] = _dedupe(body.photo_ids)
    if "started_year" in sent:
        changes["startedYear"] = body.started_year

    query = {"_id": tradition_id, "familyId": family_id}
    if not changes:
        return get_tradition(db, family_id, tradition_id)
    changes["updatedAt"] = _now(now)
    doc = db[COLLECTION].find_one_and_update(query, {"$set": changes}, return_document=ReturnDocument.AFTER)
    return _to_tradition(doc)


def delete_tradition(db: Database, family_id: str, tradition_id: str) -> bool:
    """Delete one tradition. False when it does not exist in this family. Its photos stay:
    media can be shared with memories."""
    return db[COLLECTION].delete_one({"_id": tradition_id, "familyId": family_id}).deleted_count == 1


# -------------------------------------------------------------------------------- reads

def get_tradition(db: Database, family_id: str, tradition_id: str) -> schemas.Tradition | None:
    return _to_tradition(db[COLLECTION].find_one({"_id": tradition_id, "familyId": family_id}))


def list_traditions(db: Database, family_id: str) -> list[schemas.Tradition]:
    """Every tradition in the family, oldest first (createdAt, then id), so the order is stable."""
    cursor = db[COLLECTION].find({"familyId": family_id}).sort([("createdAt", ASCENDING), ("_id", ASCENDING)])
    return [t for t in (_to_tradition(doc) for doc in cursor) if t is not None]
