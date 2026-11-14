"""MongoDB Atlas connection and indexes (ARCHITECTURE.md section 5).

Everything here also works on plain MongoDB and on mongomock: the Atlas-only
vector search indexes are best effort and never raise.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from typing import Any, Literal

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
    "traditions": [
        # list_traditions: one family's traditions, oldest first (createdAt, then _id).
        ("traditions_familyId_createdAt", [("familyId", ASCENDING), ("createdAt", ASCENDING), ("_id", ASCENDING)], {}),
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


SearchIndexAction = Literal["created", "existed", "updated", "wrong_type", "not_created"]
SearchIndexState = Literal["ready", "pending", "missing", "stale", "wrong_type", "unsupported"]


@dataclass(frozen=True)
class SearchIndexOutcome:
    """What ensure_search_indexes_report did to one vector index, and why.

    action: 'created' (requested now), 'existed' (already there with a covering definition),
    'updated' (it was there with a stale definition; an update was requested), 'wrong_type'
    (a same-named search index of another type is in the way; left alone) or 'not_created'
    (the server refused or cannot do search indexes). `detail` is the server's own message,
    with any MongoDB URI and its credentials removed (empty when there is nothing to add).
    """

    collection: str
    name: str
    action: SearchIndexAction
    detail: str = ""


@dataclass(frozen=True)
class SearchIndexStatus:
    """One vector index as the server reports it now (search_index_status).

    state: 'ready' (Atlas says READY, or the index is queryable), 'pending' (PENDING,
    BUILDING, or listed as DOES_NOT_EXIST while it is being created), 'missing' (not listed,
    or being deleted), 'stale' (its definition does not cover what the app needs, or Atlas
    says STALE), 'wrong_type' (a search index of another type has the name) or
    'unsupported' (the server cannot list search indexes, or Atlas says FAILED).
    """

    collection: str
    name: str
    state: SearchIndexState
    detail: str = ""


_URI_RE = re.compile(r"mongodb(?:\+srv)?://[^\s'\"<>]+", re.IGNORECASE)


def _clean_error(exc: BaseException) -> str:
    """An exception as text for a report: any MongoDB URI is cut down to its hosts, and the
    credentials of MONGODB_URI never appear. Callers that print it scrub it again."""
    text = " ".join(f"{type(exc).__name__}: {exc}".split())

    def hosts_only(match: re.Match[str]) -> str:
        scheme, _, rest = match.group(0).partition("://")
        hosts = re.split(r"[/?#]", rest.rpartition("@")[2], maxsplit=1)[0]
        return f"{scheme}://" + ("<credentials redacted>@" if "@" in rest else "") + hosts

    text = _URI_RE.sub(hosts_only, text)
    uri = os.environ.get("MONGODB_URI") or ""
    userinfo = uri.partition("://")[2].rpartition("@")[0]
    for secret in sorted({userinfo, *userinfo.split(":", 1)}, key=len, reverse=True):
        if len(secret) >= 4:
            text = re.sub(re.escape(secret), "[redacted]", text, flags=re.IGNORECASE)
    return text[:500]


def ensure_search_indexes_report(db: Database) -> list[SearchIndexOutcome]:
    """Best-effort creation of the Atlas Vector Search indexes: one outcome per index, in
    VECTOR_INDEXES order. Never raises.

    An index that already exists under the right name but with a stale definition (made by
    hand in the Atlas UI without the familyId filter field, or for another EMBEDDING_DIM)
    is updated in place and logged: without the filter field every family scoped
    $vectorSearch would fail and silently fall back to keywords. A same-named index of
    another type (a plain Atlas Search index) cannot be converted; that is logged.

    Plain MongoDB and mongomock do not support search indexes; that is logged, not raised,
    the outcome is 'not_created' with the server's reason, and the db layer falls back to
    keyword search. So is an Atlas refusal, such as the M0 and Flex tiers' limit of three
    search indexes per cluster (this app needs exactly three).
    """
    outcomes: list[SearchIndexOutcome] = []
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
                outcomes.append(SearchIndexOutcome(collection, name, "created"))
                continue
            kind = current.get("type")
            if kind not in (None, "vectorSearch"):
                log.warning(
                    "Search index %s.%s is of type %r, not vectorSearch; drop it in Atlas so it "
                    "can be recreated. Vector search falls back to keywords until then.",
                    collection, name, kind,
                )
                outcomes.append(SearchIndexOutcome(
                    collection, name, "wrong_type",
                    f"a search index of type {kind!r} has this name; drop it in Atlas so it can be recreated",
                ))
                continue
            definition = current.get("latestDefinition") or current.get("definition")
            if not vector_definition_covers(definition, wanted):
                db[collection].update_search_index(name, wanted)
                log.warning(
                    "Vector search index %s.%s had a stale definition %r; updated to %r",
                    collection, name, definition, wanted,
                )
                outcomes.append(SearchIndexOutcome(
                    collection, name, "updated", "the definition was stale; an update was requested",
                ))
                continue
            outcomes.append(SearchIndexOutcome(collection, name, "existed"))
        except (OperationFailure, NotImplementedError, AttributeError, TypeError) as exc:
            log.warning("Vector search index %s.%s not created: %s", collection, name, exc)
            outcomes.append(SearchIndexOutcome(collection, name, "not_created", _clean_error(exc)))
        except Exception as exc:  # noqa: BLE001 - startup must never fail on a best-effort index
            log.warning("Vector search index %s.%s not created (unexpected): %r", collection, name, exc)
            outcomes.append(SearchIndexOutcome(collection, name, "not_created", _clean_error(exc)))
    return outcomes


def ensure_search_indexes(db: Database) -> list[str]:
    """Best-effort creation of the Atlas Vector Search indexes. Returns the names created.

    A thin wrapper over ensure_search_indexes_report, which also says why an index was not
    created. Never raises.
    """
    return [o.name for o in ensure_search_indexes_report(db) if o.action == "created"]


# Atlas `status` values (listSearchIndexes) -> state. READY, or `queryable` true, is ready.
_ATLAS_PENDING = frozenset({"PENDING", "BUILDING", "DOES_NOT_EXIST"})


def _index_state(collection: str, name: str, listed: dict[str, Any] | None) -> SearchIndexStatus:
    if listed is None:
        return SearchIndexStatus(collection, name, "missing", "not listed by the server")
    kind = listed.get("type")
    if kind not in (None, "vectorSearch"):
        return SearchIndexStatus(collection, name, "wrong_type", f"a search index of type {kind!r} has this name")
    status = str(listed.get("status") or "").upper()
    definition = listed.get("latestDefinition") or listed.get("definition")
    if not vector_definition_covers(definition, _vector_definition()):
        return SearchIndexStatus(
            collection, name, "stale",
            f"its definition does not cover the app's (numDimensions {VECTOR_DIM}, "
            f"{VECTOR_SIMILARITY}, filter on {', '.join(VECTOR_FILTER_PATHS)}); status {status or 'unknown'}",
        )
    if status == "FAILED":
        reason = listed.get("message") or listed.get("statusDetail") or "no reason given"
        return SearchIndexStatus(collection, name, "unsupported", f"Atlas says FAILED: {reason}")
    if status == "DELETING":
        return SearchIndexStatus(collection, name, "missing", "Atlas is deleting it")
    if status == "STALE":
        return SearchIndexStatus(collection, name, "stale", "Atlas says STALE: queryable, but not syncing new data")
    if status == "READY" or listed.get("queryable") is True:
        return SearchIndexStatus(collection, name, "ready", f"status {status or 'unknown'}, queryable")
    if status in _ATLAS_PENDING or not status:
        return SearchIndexStatus(collection, name, "pending", f"status {status or 'unknown'}, not queryable yet")
    return SearchIndexStatus(collection, name, "pending", f"status {status}, not queryable yet")


def search_index_status(db: Database) -> dict[str, SearchIndexStatus]:
    """The state of every vector index, keyed by its VECTOR_INDEXES name. Never raises.

    A server that cannot list search indexes (mongomock, plain MongoDB, an Atlas tier
    without search) gives 'unsupported' with the reason; nothing is created or changed.
    """
    statuses: dict[str, SearchIndexStatus] = {}
    for collection, name in VECTOR_INDEXES.items():
        try:
            listed = {ix.get("name"): ix for ix in db[collection].list_search_indexes()}
            statuses[name] = _index_state(collection, name, listed.get(name))
        except Exception as exc:  # noqa: BLE001 - OperationFailure, NotImplementedError, AttributeError, TypeError...
            statuses[name] = SearchIndexStatus(
                collection, name, "unsupported", f"cannot list search indexes here ({_clean_error(exc)})"
            )
    return statuses


def ping(db: Database) -> bool:
    """True if the server answers a ping."""
    try:
        return bool(db.command("ping").get("ok"))
    except Exception:  # noqa: BLE001 - any failure means "not reachable"
        return False
