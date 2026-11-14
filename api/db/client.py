"""MongoDB Atlas connection and indexes (ARCHITECTURE.md section 5).

Everything here also works on plain MongoDB and on mongomock: the Atlas-only
vector search indexes are best effort and never raise.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from pymongo import ASCENDING, MongoClient
from pymongo.collection import Collection
from pymongo.database import Database
from pymongo.errors import CollectionInvalid, DuplicateKeyError, OperationFailure
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
        (
            # At most one person per user per family. Partial, so unclaimed people
            # (userId null) never collide. The key order differs from
            # people_familyId_userId on purpose: a database that already has that
            # non-unique index gets this one alongside it instead of a spec conflict.
            "people_userId_familyId_unique",
            [("userId", ASCENDING), ("familyId", ASCENDING)],
            {"unique": True, "partialFilterExpression": {"userId": {"$type": "string"}}},
        ),
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

    Afterwards, props and sets saved before the family-scoped storage key are migrated to
    it (db.props.migrate_legacy_ids, a no-op once done).
    """
    for collection, specs in INDEXES.items():
        existing = db[collection].index_information()
        for name, keys, options in specs:
            if name in existing and _same_spec(existing[name], keys, options):
                continue
            _create_index(db[collection], name, keys, options)

    from db import props  # here, not at the top: db.props must stay free to import db.client

    migrated = props.migrate_legacy_ids(db)
    if migrated:
        log.info("Migrated %d props/sets to family-scoped storage keys", migrated)


def _create_index(
    coll: Collection, name: str, keys: list[tuple[str, int]], options: dict[str, Any]
) -> None:
    """create_index, working around mongomock's check of a new partial unique index.

    mongomock validates existing documents against a new unique index without applying
    its partialFilterExpression, so two unclaimed people (userId null) in one family
    would wrongly fail people_userId_familyId_unique. Only on mongomock, and only when
    the documents the filter selects really are unique, is the index registered anyway;
    mongomock does apply the filter on every later write. Real servers are untouched.

    Registering it goes through mongomock's internal store (tested against mongomock
    4.3). If a mongomock upgrade removes or changes that hook, the index is skipped with
    a warning instead of crashing startup: db/people.py's post-insert check still keeps
    one person per user per family, and tests/data/test_client.py fails loudly on the
    missing index. This path only runs on mongomock with data already present; fake-DB
    mode creates its indexes on an empty database and never reaches it.
    """
    try:
        coll.create_index(keys, name=name, **options)
    except DuplicateKeyError:
        partial = options.get("partialFilterExpression")
        if not (_is_mongomock(coll) and options.get("unique") and partial is not None):
            raise
        if _has_duplicates(coll, keys, partial):
            raise
        _register_mongomock_index(coll, name, keys, partial)


def _register_mongomock_index(
    coll: Collection, name: str, keys: list[tuple[str, int]], partial: dict[str, Any]
) -> None:
    """Add a partial unique index to mongomock's index store without its creation check."""
    index: dict[str, Any] = {"key": list(keys), "unique": True, "partialFilterExpression": partial}
    register = getattr(getattr(coll, "_store", None), "create_index", None)
    if not callable(register):
        log.warning("mongomock has no index-store hook; index %s skipped", name)
        return
    try:
        register(name, index)
    except Exception:  # noqa: BLE001  # an internal API changed shape: degrade, never crash
        log.warning("mongomock index-store hook failed; index %s skipped", name, exc_info=True)


def _is_mongomock(coll: Collection) -> bool:
    return type(coll).__module__.split(".")[0] == "mongomock"


def _has_duplicates(coll: Collection, keys: list[tuple[str, int]], partial: dict[str, Any]) -> bool:
    """True if two documents selected by ``partial`` share the same values for ``keys``."""
    fields = [k for k, _ in keys]
    seen: set[str] = set()
    for doc in coll.find(partial, {f: 1 for f in fields}):
        value = repr([doc.get(f) for f in fields])
        if value in seen:
            return True
        seen.add(value)
    return False


# The vector field every vector index covers, and the fields its $vectorSearch stages may
# use in `filter`. Atlas rejects a $vectorSearch whose filter names a field that is not
# declared as a "filter" field in the index (db/search.py and db/props.py filter on
# familyId; tests/data/test_db_hardening.py checks the two stay in step).
VECTOR_PATH = "embedding"
VECTOR_FILTER_PATHS: tuple[str, ...] = ("familyId",)
VECTOR_SIMILARITY = "cosine"


def _vector_definition() -> dict[str, Any]:
    return {
        "fields": [
            {
                "type": "vector",
                "path": VECTOR_PATH,
                "numDimensions": VECTOR_DIM,
                "similarity": VECTOR_SIMILARITY,
            },
            *({"type": "filter", "path": path} for path in VECTOR_FILTER_PATHS),
        ]
    }


def vector_definition_covers(existing: dict[str, Any] | None, wanted: dict[str, Any]) -> bool:
    """True when every field of `wanted` appears in `existing` with the same settings.

    Atlas may report extra defaults on a field (e.g. a quantization setting) and an index
    may declare extra filter fields; neither makes it stale. A missing filter field, or a
    different numDimensions, path or similarity, does.
    """
    have = [f for f in ((existing or {}).get("fields") or []) if isinstance(f, dict)]

    def same(a: Any, b: Any) -> bool:
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            return float(a) == float(b)
        return a == b

    return all(
        any(all(same(field_have.get(k), v) for k, v in field.items()) for field_have in have)
        for field in wanted.get("fields") or []
    )


def _ensure_collection(db: Database, name: str) -> None:
    """Create the collection if it does not exist yet (Atlas cannot index a missing one)."""
    if name in db.list_collection_names():
        return
    try:
        db.create_collection(name)
    except CollectionInvalid:
        pass  # created concurrently


def ensure_search_indexes(db: Database) -> list[str]:
    """Best-effort creation of the Atlas Vector Search indexes. Returns the names created.

    An index that already exists under the right name but with a stale definition (made by
    hand in the Atlas UI without the familyId filter field, or for another EMBEDDING_DIM)
    is updated in place and logged, not returned: without the filter field every family
    scoped $vectorSearch would fail and silently fall back to keywords. A same-named index
    of another type (a plain Atlas Search index) cannot be converted; that is logged.

    Plain MongoDB and mongomock do not support search indexes; that is logged, not raised,
    and the db layer falls back to keyword search.
    """
    created: list[str] = []
    wanted = _vector_definition()
    for collection, name in VECTOR_INDEXES.items():
        try:
            _ensure_collection(db, collection)
            existing = {ix.get("name"): ix for ix in db[collection].list_search_indexes()}
            current = existing.get(name)
            if current is None:
                db[collection].create_search_index(
                    SearchIndexModel(definition=wanted, name=name, type="vectorSearch")
                )
                created.append(name)
                continue
            kind = current.get("type")
            if kind not in (None, "vectorSearch"):
                log.warning(
                    "Search index %s.%s is of type %r, not vectorSearch; drop it in Atlas so it "
                    "can be recreated. Vector search falls back to keywords until then.",
                    collection, name, kind,
                )
                continue
            definition = current.get("latestDefinition") or current.get("definition")
            if not vector_definition_covers(definition, wanted):
                db[collection].update_search_index(name, wanted)
                log.warning(
                    "Vector search index %s.%s had a stale definition %r; updated to %r",
                    collection, name, definition, wanted,
                )
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
