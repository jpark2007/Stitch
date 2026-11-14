"""MongoDB Atlas connection and indexes (ARCHITECTURE.md section 5).

Everything here also works on plain MongoDB and on mongomock: the Atlas-only
vector search indexes are best effort and never raise.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from pymongo import ASCENDING, MongoClient
from pymongo.database import Database
from pymongo.errors import OperationFailure
from pymongo.operations import SearchIndexModel

log = logging.getLogger(__name__)

MONGODB_URI = os.environ.get("MONGODB_URI") or "mongodb://localhost:27017"
MONGODB_DB = os.environ.get("MONGODB_DB") or "stitch"
VECTOR_DIM = int(os.environ.get("EMBEDDING_DIM") or 1024)

# collection -> Atlas Vector Search index name
VECTOR_INDEXES: dict[str, str] = {
    "memories": "memories_vector",
    "props": "props_vector",
    "sets": "sets_vector",
}

# collection -> [(index name, keys, extra create_index options)]
INDEXES: dict[str, list[tuple[str, list[tuple[str, int]], dict[str, Any]]]] = {
    "users": [
        ("users_email_unique", [("email", ASCENDING)], {"unique": True}),
    ],
    "families": [
        ("families_members_userId", [("members.userId", ASCENDING)], {}),
        (
            # Partial, so families with no invites (no key at all) never collide on null.
            "families_invites_code_unique",
            [("invites.code", ASCENDING)],
            {"unique": True, "partialFilterExpression": {"invites.code": {"$exists": True}}},
        ),
    ],
    "people": [
        ("people_familyId_userId", [("familyId", ASCENDING), ("userId", ASCENDING)], {}),
        ("people_familyId_parentIds", [("familyId", ASCENDING), ("parentIds", ASCENDING)], {}),
    ],
    "memories": [
        (
            "memories_familyId_participantIds_year",
            [("familyId", ASCENDING), ("participantIds", ASCENDING), ("year", ASCENDING)],
            {},
        ),
    ],
    "questions": [
        (
            "questions_familyId_personId_status",
            [("familyId", ASCENDING), ("personId", ASCENDING), ("status", ASCENDING)],
            {},
        ),
    ],
    "props": [
        ("props_familyId_name", [("familyId", ASCENDING), ("name", ASCENDING)], {}),
    ],
    "sets": [
        ("sets_familyId_name", [("familyId", ASCENDING), ("name", ASCENDING)], {}),
    ],
    "revoked_sessions": [
        # TTL: a revoked session id is dropped once its token would have expired anyway.
        ("revoked_sessions_expiresAt_ttl", [("expiresAt", ASCENDING)], {"expireAfterSeconds": 0}),
    ],
}

_client: MongoClient | None = None
_db_override: Database | None = None


def get_client() -> MongoClient:
    """The process-wide client. Connecting is lazy, so this never blocks or raises on a dead server."""
    global _client
    if _client is None:
        _client = MongoClient(
            MONGODB_URI,
            tz_aware=True,
            serverSelectionTimeoutMS=5000,
            appname="stitch",
        )
    return _client


def get_db() -> Database:
    """The app database, or the test override. Usable as a FastAPI dependency."""
    if _db_override is not None:
        return _db_override
    return get_client()[MONGODB_DB]


def set_db(db: Database | None) -> None:
    """Point get_db() at another database (tests). None restores the real one."""
    global _db_override
    _db_override = db


def _same_spec(existing: dict[str, Any], keys: list[tuple[str, int]], options: dict[str, Any]) -> bool:
    return (
        [(k, int(v)) for k, v in existing.get("key", [])] == [(k, int(v)) for k, v in keys]
        and bool(existing.get("unique")) == bool(options.get("unique"))
        and existing.get("partialFilterExpression") == options.get("partialFilterExpression")
        and _ttl(existing.get("expireAfterSeconds")) == _ttl(options.get("expireAfterSeconds"))
    )


def _ttl(value: Any) -> int | None:
    """expireAfterSeconds as an int (servers may report it as a float), or None."""
    return None if value is None else int(value)


def ensure_indexes(db: Database) -> None:
    """Create every index from ARCHITECTURE.md section 5. Safe to call on every startup.

    An index that already exists with the same name and spec is left alone (mongomock
    re-validates uniqueness on re-creation and cannot evaluate multikey partial indexes).
    A name collision with a different spec is passed to the server, which raises the conflict.
    """
    for collection, specs in INDEXES.items():
        existing = db[collection].index_information()
        for name, keys, options in specs:
            if name in existing and _same_spec(existing[name], keys, options):
                continue
            db[collection].create_index(keys, name=name, **options)


def _vector_definition() -> dict[str, Any]:
    return {
        "fields": [
            {"type": "vector", "path": "embedding", "numDimensions": VECTOR_DIM, "similarity": "cosine"},
            {"type": "filter", "path": "familyId"},
        ]
    }


def ensure_search_indexes(db: Database) -> list[str]:
    """Best-effort creation of the Atlas Vector Search indexes. Returns the names created.

    Plain MongoDB and mongomock do not support search indexes; that is logged, not raised,
    and the db layer falls back to keyword search.
    """
    created: list[str] = []
    for collection, name in VECTOR_INDEXES.items():
        try:
            existing = {ix.get("name") for ix in db[collection].list_search_indexes()}
            if name in existing:
                continue
            db[collection].create_search_index(
                SearchIndexModel(definition=_vector_definition(), name=name, type="vectorSearch")
            )
            created.append(name)
        except (OperationFailure, NotImplementedError, AttributeError, TypeError) as exc:
            log.warning("Vector search index %s.%s not created: %s", collection, name, exc)
        except Exception as exc:  # noqa: BLE001 - startup must never fail on a best-effort index
            log.warning("Vector search index %s.%s not created (unexpected): %r", collection, name, exc)
    return created


def ping(db: Database) -> bool:
    """True if the server answers a ping."""
    try:
        return bool(db.command("ping").get("ok"))
    except Exception:  # noqa: BLE001 - any failure means "not reachable"
        return False
