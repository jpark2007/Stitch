"""Check a real MongoDB (or Atlas cluster) end to end before the API and the demo use it.

    cd api && uv run python -m seed.preflight [--json] [--timeout-ms N] [--wait SECONDS] [--skip-checkpointer]

It reads MONGODB_URI and MONGODB_DB from the environment, then api/.env, then ../.env (the
same rules as the API), and runs these checks in order, each one a row of a pass/fail table:

- connect: a ping within --timeout-ms (default 5000).
- server: the version (buildInfo), the topology (standalone, replica set or sharded), and
  whether it is Atlas (a host ending in .mongodb.net, or an answer to `atlasVersion`).
- indexes: db.client.ensure_indexes, then every index of db.client.INDEXES as present
  (already there) or created.
- vector index <name>: one row per Atlas Vector Search index (db.client.VECTOR_INDEXES):
  already existed, created now, ready, pending, stale (updated), wrong type or unsupported,
  polling the status for up to --wait seconds (default 60; 0 does not wait).
- round trip: one document per collection, written through the db/ functions in a
  throwaway family `preflight_<12 hex>` and read back, plus one media file (real GridFS
  on a real server: media.files and media.chunks).
- tree: db.people.get_tree, and the native $graphLookup pipeline run once directly (the
  db layer falls back to Python silently when the server refuses it).
- search: a synthetic unit vector on the throwaway memory, one $vectorSearch (polled for
  up to --wait seconds while it finds nothing), then the keyword fallback, which must find
  the memory. Voyage is never called: nothing here uses the network beyond MongoDB.
- checkpointer: the LangGraph checkpointer and store the model studio uses
  (agent.studio.mongo_persistence) write, read and delete one checkpoint, one write and
  one store item. --skip-checkpointer skips it.
- cleanup: always, even after a failure: the throwaway family, user, session and
  checkpoints are deleted, then every collection is checked for leftovers.

The last line is `PREFLIGHT PASS`, `PREFLIGHT PASS with N warnings` or
`PREFLIGHT FAIL: <checks>`. Exit codes: 0 every check passed (warnings allowed), 1 a
check failed, 2 bad arguments (argparse), 3 MONGODB_URI unset or MongoDB unreachable.

The URI, its user, password and query string are never printed, logged or put in the
JSON: only its scheme and hosts (agent.preflight.redact_mongo_uri) and the database name
are shown, every error text goes through agent.preflight.scrub, and the whole run is
inside agent.preflight.scrubbed_logs.

Everything that touches the network goes through PreflightSeams (connect, checkpointer,
clock, sleep), so the tests run the whole preflight offline on mongomock.
"""

from __future__ import annotations

import argparse
import io
import json
import math
import os
import secrets as secrets_mod
import sys
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, TextIO

from bson import ObjectId
from pymongo.database import Database
from pymongo.errors import OperationFailure, PyMongoError

import schemas
from agent.preflight import scrub, scrubbed_logs, secret_values
from db import client as db_client
from db import families as families_db
from db import media as media_db
from db import memories as memories_db
from db import people as people_db
from db import props as props_db
from db import search as search_db
from db import sessions as sessions_db
from db import traditions as traditions_db
from db import users as users_db
from seed import realdb

EXIT_OK = 0
EXIT_FAILED = 1  # a check failed
EXIT_NO_DATABASE = 3  # MONGODB_URI is unset, or MongoDB is unreachable or refused the login
# argparse exits with 2 on a bad command line.

PASS, WARN, FAIL, SKIP = "PASS", "WARN", "FAIL", "SKIP"
DEFAULT_WAIT_S = 60.0
POLL_S = 5.0
WAIT_HINT = "re-run with --wait 180"
M0_HINT = (
    "Atlas M0 and Flex clusters allow at most 3 search indexes per cluster and Stitch needs "
    "exactly 3, so drop any other search index on this cluster"
)
UNSET_MESSAGE = "MONGODB_URI is not set (looked in the environment, api/.env, ../.env)"
KEYWORD = "lighthouse"  # a word of the throwaway telling, found by the keyword fallback
CHECKPOINT_COLLECTIONS = ("agent_checkpoints", "agent_checkpoint_writes")
STORE_COLLECTION = "agent_store"


# --------------------------------------------------------------------------- seams


@dataclass
class Persistence:
    """What the checkpointer seam returns: a LangGraph checkpointer, a store, and how to close them."""

    saver: Any
    store: Any
    close: Callable[[], None]


def with_timeouts(uri: str, timeout_ms: int) -> str:
    """`uri` with serverSelectionTimeoutMS and connectTimeoutMS options appended, so a client
    built from it (agent.studio.mongo_persistence takes only a URI) cannot hang."""
    opts = f"serverSelectionTimeoutMS={int(timeout_ms)}&connectTimeoutMS={int(timeout_ms)}"
    base, question, query = uri.partition("?")
    if question:
        separator = ";" if (";" in query and "&" not in query) else "&"
        return f"{uri}{separator}{opts.replace('&', separator)}" if query else f"{uri}{opts}"
    rest = base.partition("://")[2]
    return f"{uri}?{opts}" if "/" in rest else f"{uri}/?{opts}"


def default_persistence(uri: str, db_name: str, timeout_ms: int) -> Persistence:
    """The studio's own MongoDB checkpointer and store (agent.studio.mongo_persistence)."""
    from agent.studio import mongo_persistence

    saver, store = mongo_persistence(with_timeouts(uri, timeout_ms), db_name=db_name)
    return Persistence(saver, store, saver.client.close)


def _token() -> str:
    return secrets_mod.token_hex(6)


@dataclass
class PreflightSeams:
    """Everything that touches the network or the clock, so tests run offline with no waiting."""

    connect: Callable[..., tuple[Any, Database]] = realdb.connect
    checkpointer: Callable[[str, str, int], Persistence] = default_persistence
    clock: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] = time.sleep
    token: Callable[[], str] = _token


# ---------------------------------------------------------------------------- rows


@dataclass
class Row:
    check: str
    status: str
    detail: str
    seconds: float = 0.0


@dataclass
class Report:
    target: str
    database: str
    rows: list[Row] = field(default_factory=list)
    exit_code: int = EXIT_OK

    @property
    def failed(self) -> list[str]:
        return [r.check for r in self.rows if r.status == FAIL]

    @property
    def warnings(self) -> int:
        return sum(1 for r in self.rows if r.status == WARN)

    def summary(self) -> str:
        if self.failed:
            return "PREFLIGHT FAIL: " + ", ".join(self.failed)
        if self.warnings:
            return f"PREFLIGHT PASS with {self.warnings} warning{'s' if self.warnings != 1 else ''}"
        return "PREFLIGHT PASS"

    def as_json(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "database": self.database,
            "rows": [asdict(r) for r in self.rows],
            "summary": self.summary(),
            "exitCode": self.exit_code,
        }


class CheckFailed(Exception):
    """A check found something wrong; the message is the row's detail."""


def _expect(condition: Any, what: str) -> None:
    if not condition:
        raise CheckFailed(what)


def format_table(report: Report) -> str:
    """The fixed-width table (check, status, seconds, detail) and the summary line."""
    width = max([len("CHECK"), *(len(r.check) for r in report.rows)])
    lines = [
        "Stitch MongoDB preflight",
        f"  target    {report.target}",
        f"  database  {report.database}",
        "",
        f"{'CHECK':<{width}}  STATUS  SECONDS  DETAIL",
    ]
    lines += [f"{r.check:<{width}}  {r.status:<6}  {r.seconds:>7.2f}  {r.detail}" for r in report.rows]
    lines += ["", report.summary()]
    return "\n".join(lines)


# --------------------------------------------------------------------------- the run


@dataclass
class _Ids:
    """Every id the run writes, so cleanup and the leftover check know exactly what to look for."""

    hex: str
    family_id: str
    user_id: str
    email: str
    parent_id: str
    child_id: str
    memory_id: str
    question_id: str
    prop_id: str
    set_id: str
    jti: str
    thread: str
    namespace: tuple[str, str]
    media_ids: list[str] = field(default_factory=list)

    @classmethod
    def make(cls, hex_: str) -> _Ids:
        return cls(
            hex=hex_,
            family_id=f"preflight_{hex_}",
            user_id=f"preflight_user_{hex_}",
            email=f"preflight-{hex_}@preflight.invalid",
            parent_id=f"preflight_parent_{hex_}",
            child_id=f"preflight_child_{hex_}",
            memory_id=f"preflight_memory_{hex_}",
            question_id=f"preflight_question_{hex_}",
            prop_id=f"preflight_prop_{hex_}",
            set_id=f"preflight_set_{hex_}",
            jti=f"preflight-{hex_}",
            thread=f"preflight-{hex_}",
            namespace=("preflight", hex_),
        )


class Preflight:
    """One preflight run against one database. `run()` returns the finished Report."""

    def __init__(
        self,
        uri: str,
        db_name: str,
        *,
        timeout_ms: int = realdb.DEFAULT_TIMEOUT_MS,
        wait_s: float = DEFAULT_WAIT_S,
        skip_checkpointer: bool = False,
        seams: PreflightSeams | None = None,
        secrets: Sequence[str] = (),
    ) -> None:
        self.uri = uri
        self.db_name = db_name
        self.timeout_ms = timeout_ms
        self.wait_s = max(0.0, float(wait_s))
        self.skip_checkpointer = skip_checkpointer
        self.seams = seams or PreflightSeams()
        self.secrets = list(secrets)
        self.report = Report(target=realdb.redacted(uri), database=db_name)
        self.client: Any = None
        self.db: Database | None = None
        self.is_atlas = False
        self.mongomock = False
        self.ids = _Ids.make(self.seams.token())
        self.round_trip_ok = False

    # ------------------------------------------------------------------ helpers

    def _clean(self, text: str) -> str:
        return " ".join(scrub(text, self.secrets).split())

    def _error(self, exc: BaseException) -> str:
        if isinstance(exc, CheckFailed):
            return self._clean(str(exc))
        if isinstance(exc, PyMongoError):
            return realdb.describe_failure(exc, self.secrets, uri=self.uri)
        return self._clean(f"{type(exc).__name__}: {exc}")

    def _add(self, check: str, status: str, detail: str, started: float) -> Row:
        row = Row(check, status, self._clean(detail), round(max(0.0, self.seams.clock() - started), 3))
        self.report.rows.append(row)
        return row

    def _step(self, check: str, fn: Callable[[], tuple[str, str]]) -> Row:
        """Run one check; an exception is a FAIL row with its scrubbed text, never a crash."""
        started = self.seams.clock()
        try:
            status, detail = fn()
        except Exception as exc:  # noqa: BLE001 - every failure becomes a row
            status, detail = FAIL, self._error(exc)
        return self._add(check, status, detail, started)

    @property
    def _db(self) -> Database:
        assert self.db is not None
        return self.db

    # --------------------------------------------------------------------- run

    def run(self) -> Report:
        started = self.seams.clock()
        try:
            self.client, self.db = self.seams.connect(self.uri, self.db_name, timeout_ms=self.timeout_ms)
        except Exception as exc:  # noqa: BLE001 - one sentence, exit 3
            self._add("connect", FAIL, realdb.describe_failure(exc, self.secrets, uri=self.uri), started)
            self.report.exit_code = EXIT_NO_DATABASE
            return self.report
        self.mongomock = type(self.db).__module__.startswith("mongomock")
        self._add("connect", PASS, f"ping answered within {self.timeout_ms} ms", started)
        try:
            self._step("server", self.check_server)
            self._step("indexes", self.check_indexes)
            self.check_vector_indexes()
            round_trip = self._step("round trip", self.check_round_trip)
            self.round_trip_ok = round_trip.status != FAIL
            for name, check in (("tree", self.check_tree), ("search", self.check_search)):
                if self.round_trip_ok:
                    self._step(name, check)
                else:
                    self._add(name, SKIP, "skipped: the round trip failed", self.seams.clock())
            if self.skip_checkpointer:
                self._add("checkpointer", SKIP, "skipped (--skip-checkpointer)", self.seams.clock())
            else:
                self._step("checkpointer", self.check_checkpointer)
        finally:
            self._step("cleanup", self.cleanup)
            close = getattr(self.client, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:  # noqa: BLE001 - closing must never hide the report
                    pass
        self.report.exit_code = EXIT_FAILED if self.report.failed else EXIT_OK
        return self.report

    # ------------------------------------------------------------------ server

    def _hosts(self) -> list[str]:
        hosts: list[str] = []
        nodes = getattr(self.client, "nodes", None)
        if isinstance(nodes, (set, frozenset, list, tuple)):
            hosts += [str(n[0]) for n in nodes if isinstance(n, tuple) and n]
        shown = realdb.redacted(self.uri).partition("://")[2].rpartition("@")[2]
        hosts += [h.rsplit(":", 1)[0] for h in shown.split(",") if h]
        return list(dict.fromkeys(h.lower() for h in hosts if h))

    def check_server(self) -> tuple[str, str]:
        admin = self.client.admin
        parts: list[str] = []
        status = PASS
        try:
            version = str(admin.command("buildInfo").get("version") or "unknown")
            parts.append(f"MongoDB {version}")
        except Exception as exc:  # noqa: BLE001
            status = WARN
            what = "mongomock (an in-memory test double), not a real server" if self.mongomock else "unknown"
            parts.append(f"version {what}: buildInfo not answered ({type(exc).__name__})")
        try:
            hello = admin.command("hello")
            if hello.get("msg") == "isdbgrid":
                parts.append("topology sharded (mongos)")
            elif hello.get("setName"):
                parts.append(f"topology replica set {hello.get('setName')}")
            else:
                parts.append("topology standalone")
        except Exception as exc:  # noqa: BLE001
            parts.append(f"topology unknown (hello not answered: {type(exc).__name__})")
        atlas_hosts = [h for h in self._hosts() if h.endswith(".mongodb.net")]
        try:
            answer = admin.command("atlasVersion")
            atlas_version = str(answer.get("atlasVersion") or answer.get("version") or "answered")
        except Exception:  # noqa: BLE001 - not Atlas (or not allowed): that is the answer
            atlas_version = None
        # mongomock answers for any host name, so it is never Atlas whatever the URI says.
        self.is_atlas = not self.mongomock and (bool(atlas_hosts) or atlas_version is not None)
        parts.append(
            f"is_atlas {'yes' if self.is_atlas else 'no'} "
            f"(.mongodb.net host: {'yes' if atlas_hosts else 'no'}; "
            f"atlasVersion: {atlas_version if atlas_version is not None else 'no answer'}"
            f"{'; mongomock' if self.mongomock else ''})"
        )
        return status, "; ".join(parts)

    # ----------------------------------------------------------------- indexes

    def check_indexes(self) -> tuple[str, str]:
        db = self._db
        before = {coll: set(db[coll].index_information()) for coll in db_client.INDEXES}
        try:
            db_client.ensure_indexes(db)
        except OperationFailure as exc:
            raise CheckFailed(
                f"ensure_indexes failed (an existing index with the same name but another spec?): "
                f"code {exc.code}: {exc}"
            ) from exc
        present: list[str] = []
        created: list[str] = []
        missing: list[str] = []
        for coll, specs in db_client.INDEXES.items():
            after = set(db[coll].index_information())
            for name, _keys, _options in specs:
                if name not in after:
                    missing.append(f"{coll}.{name}")
                elif name in before[coll]:
                    present.append(name)
                else:
                    created.append(name)
        _expect(not missing, f"missing after ensure_indexes: {', '.join(missing)}")
        detail = f"{len(present)} present, {len(created)} created"
        if created:
            detail += f" ({', '.join(created)})"
        return PASS, detail

    # ----------------------------------------------------------- vector indexes

    def check_vector_indexes(self) -> None:
        started = self.seams.clock()
        try:
            outcomes = {o.name: o for o in db_client.ensure_search_indexes_report(self._db)}
            statuses = db_client.search_index_status(self._db)
            deadline = started + self.wait_s
            while self._still_coming(outcomes, statuses) and self.seams.clock() < deadline:
                self.seams.sleep(min(POLL_S, max(0.0, deadline - self.seams.clock())))
                statuses = db_client.search_index_status(self._db)
        except Exception as exc:  # noqa: BLE001 - both functions never raise; belt and braces
            for name in db_client.VECTOR_INDEXES.values():
                self._add(f"vector index {name}", FAIL, self._error(exc), started)
            return
        for collection, name in db_client.VECTOR_INDEXES.items():
            outcome = outcomes.get(name)
            status = statuses.get(name) or db_client.SearchIndexStatus(collection, name, "missing", "no status")
            row_status, detail = self._vector_row(outcome, status)
            if not self.mongomock:
                detail += f"; the app wants numDimensions {db_client.VECTOR_DIM} (EMBEDDING_DIM)"
            self._add(f"vector index {name}", row_status, detail, started)

    @staticmethod
    def _still_coming(
        outcomes: dict[str, db_client.SearchIndexOutcome], statuses: dict[str, db_client.SearchIndexStatus]
    ) -> bool:
        """True while some index is pending, or was just requested but is not listed yet."""
        return any(
            s.state == "pending"
            or (s.state == "missing" and getattr(outcomes.get(name), "action", None) == "created")
            for name, s in statuses.items()
        )

    def _vector_row(
        self, outcome: db_client.SearchIndexOutcome | None, status: db_client.SearchIndexStatus
    ) -> tuple[str, str]:
        action = outcome.action if outcome else "not_created"
        reason = (outcome.detail if outcome else "") or status.detail
        if self.mongomock:
            reason = "mongomock has no search indexes"
        did = {
            "existed": "already existed",
            "created": "created now",
            "updated": "stale definition updated now",
            "wrong_type": "left alone",
            "not_created": "not created",
        }[action]
        waited = f"after waiting {self.wait_s:g}s" if self.wait_s else "(--wait 0)"
        if status.state == "ready":
            return PASS, f"{did}; ready ({status.detail})"
        if status.state == "pending":
            return WARN, f"{did}; pending {waited} ({status.detail}); {WAIT_HINT}"
        if status.state == "stale":
            return WARN, f"{did}; stale: {status.detail}; {WAIT_HINT} once the update is applied"
        if status.state == "wrong_type":
            return FAIL, f"wrong type: {status.detail}; drop it in Atlas so it can be recreated as vectorSearch"
        if status.state == "missing" and action == "created":
            return WARN, f"{did}; not listed yet ({status.detail}); {WAIT_HINT}"
        # unsupported, or missing because the server refused to create it
        if self.is_atlas:
            return FAIL, f"{did} on Atlas: {reason}; {M0_HINT}"
        return WARN, (
            f"unsupported on this server (not Atlas): {reason}; "
            "vector search falls back to keyword search"
        )

    # -------------------------------------------------------------- round trip

    ROUND_TRIP_STAGES = ("users", "families", "people", "memories", "questions", "props", "sets",
                         "traditions", "revoked_sessions", "media")

    def check_round_trip(self) -> tuple[str, str]:
        """One document per collection; a failure names the collection it happened at."""
        done: list[str] = []
        try:
            self._round_trip(done)
        except Exception as exc:  # noqa: BLE001 - becomes the row's detail
            stage = self.ROUND_TRIP_STAGES[min(len(done), len(self.ROUND_TRIP_STAGES) - 1)]
            ok = f" (after {', '.join(done)} passed)" if done else ""
            raise CheckFailed(f"at {stage}{ok}: {self._error(exc)}") from exc
        return PASS, "wrote and read back " + ", ".join(done)

    def _round_trip(self, done: list[str]) -> None:
        db, ids = self._db, self.ids
        now = datetime.now(UTC).replace(microsecond=0)

        user = users_db.create_user(
            db, email=ids.email, password_hash="preflight-not-a-password-hash", name="Preflight",
            user_id=ids.user_id, now=now,
        )
        back_user = users_db.get_user(db, ids.user_id)
        _expect(back_user is not None and back_user.email == user.email, "users: the account did not read back")
        done.append("users")

        families_db.create_family(db, name="Preflight", creator_user_id=ids.user_id, family_id=ids.family_id, now=now)
        family = families_db.get_family(db, ids.family_id)
        _expect(family is not None and family.members[0].user_id == ids.user_id,
                "families: the family did not read back with its admin")
        _expect(families_db.get_member_role(db, ids.family_id, ids.user_id) == "admin",
                "families: the creator is not the admin")
        done.append("families")

        people_db.create_person(db, ids.family_id, schemas.NewPerson(name="Preflight Parent", born=1950),
                                added_by=ids.user_id, user_id=ids.user_id, person_id=ids.parent_id)
        people_db.create_person(db, ids.family_id,
                                schemas.NewPerson(name="Preflight Child", born=1980, parent_ids=[ids.parent_id]),
                                added_by=ids.user_id, person_id=ids.child_id)
        child = people_db.get_person(db, ids.family_id, ids.child_id)
        _expect(child is not None and child.parent_ids == [ids.parent_id], "people: the child did not read back")
        _expect(people_db.get_person_for_user(db, ids.family_id, ids.user_id) is not None,
                "people: the parent is not linked to the account")
        done.append("people x2")

        text = f"We watched the {KEYWORD} keeper paint the railing red."
        memories_db.create_memory(
            db, ids.family_id, title="Preflight check", year=1990,
            participant_ids=[ids.parent_id, ids.child_id],
            telling=schemas.Telling(author_id=ids.parent_id, text=text),
            memory_id=ids.memory_id, now=now,
        )
        memory = memories_db.get_memory(db, ids.family_id, ids.memory_id)
        _expect(memory is not None and memory.tellings and memory.tellings[0].text == text,
                "memories: the telling did not read back")
        done.append("memories")

        memories_db.create_question(db, ids.family_id, person_id=ids.child_id, text="What do you remember?",
                                    memory_id=ids.memory_id, question_id=ids.question_id, now=now)
        open_ids = [q.id for q in memories_db.open_questions(db, ids.family_id, ids.child_id)]
        _expect(ids.question_id in open_ids, "questions: the open question did not read back")
        done.append("questions")

        prop = schemas.Prop(id=ids.prop_id, name="preflight lantern", description="a brass lantern",
                            size="hand", art={"body": None}, color="#c9a227")
        props_db.insert_prop(db, ids.family_id, prop, prop_id=ids.prop_id)
        back_prop = props_db.get_prop(db, ids.family_id, ids.prop_id)
        _expect(back_prop is not None and back_prop.name == prop.name, "props: the prop did not read back")
        done.append("props")

        set_def = schemas.SetDef(id=ids.set_id, name="preflight pier", layers=[
            schemas.SetLayer(art=None, depth=0, height=1.0, color="#8fb3c9"),
        ])
        props_db.insert_set(db, ids.family_id, set_def, set_id=ids.set_id)
        back_set = props_db.get_set(db, ids.family_id, ids.set_id)
        _expect(back_set is not None and back_set.name == set_def.name, "sets: the set did not read back")
        done.append("sets")

        tradition = traditions_db.create_tradition(
            db, ids.family_id,
            schemas.CreateTraditionRequest(title="Preflight picnic", category="food",
                                           when=schemas.TraditionWhen(kind="yearly", month=7),
                                           participant_ids=[ids.parent_id, ids.child_id]),
            created_by=ids.user_id, now=now,
        )
        back_tradition = traditions_db.get_tradition(db, ids.family_id, tradition.id)
        _expect(back_tradition is not None and back_tradition.title == tradition.title
                and back_tradition.participant_ids == [ids.parent_id, ids.child_id]
                and back_tradition.when.month == 7,
                "traditions: the tradition did not read back")
        done.append("traditions")

        sessions_db.revoke_session(db, ids.jti, now + timedelta(hours=1))
        _expect(sessions_db.is_revoked(db, ids.jti), "revoked_sessions: the revoked session did not read back")
        done.append("revoked_sessions")

        payload = _tiny_png()
        media_id = media_db.put_media(db, ids.family_id, payload, content_type="image/png",
                                      filename="preflight.png", kind="photo")
        ids.media_ids.append(media_id)
        got = media_db.get_media(db, ids.family_id, media_id)
        _expect(got is not None and got.data == payload, "media: the file did not read back byte for byte")
        _expect(media_db.get_media(db, f"{ids.family_id}_other", media_id) is None,
                "media: another family could read the file")
        if self.mongomock:
            done.append("media (mongomock inline store, not GridFS)")
        else:
            oid = ObjectId(media_id)
            files = db[media_db.FILES].count_documents({"_id": oid, "metadata.familyId": ids.family_id})
            chunks = db[media_db.CHUNKS].count_documents({"files_id": oid})
            _expect(files == 1 and chunks >= 1,
                    f"media: GridFS has {files} file document(s) and {chunks} chunk(s) for it")
            done.append(f"media (GridFS: {media_db.FILES} + {chunks} chunk(s) in {media_db.CHUNKS})")

    # -------------------------------------------------------------------- tree

    def check_tree(self) -> tuple[str, str]:
        db, ids = self._db, self.ids
        tree = people_db.get_tree(db, ids.family_id)
        order = [p.id for p in tree]
        _expect(order == [ids.parent_id, ids.child_id],
                f"get_tree returned {len(order)} people in the wrong order")
        try:
            results = list(db[people_db.COLLECTION].aggregate(people_db._tree_pipeline(ids.family_id)))
        except (OperationFailure, NotImplementedError) as exc:
            return WARN, (
                f"get_tree ok (2 people, 2 generations) via the Python fallback: the server refused "
                f"$graphLookup ({self._clean(type(exc).__name__ + ': ' + str(exc))})"
            )
        reached = {r["_id"] for r in results} | {d["_id"] for r in results for d in r.get("descendants") or []}
        _expect(reached == {ids.parent_id, ids.child_id},
                f"native $graphLookup reached {sorted(reached)}, not both people")
        where = " (mongomock's)" if self.mongomock else ""
        return PASS, f"get_tree ok (2 people, 2 generations); native $graphLookup{where} reached both"

    # ------------------------------------------------------------------ search

    def check_search(self) -> tuple[str, str]:
        db, ids = self._db, self.ids
        vector = [1.0] + [0.0] * (db_client.VECTOR_DIM - 1)
        _expect(math.isclose(sum(x * x for x in vector), 1.0), "the synthetic vector is not a unit vector")
        _expect(memories_db.set_embedding(db, ids.family_id, ids.memory_id, vector),
                "set_embedding did not match the throwaway memory")

        started = self.seams.clock()
        docs = search_db._vector_search(db, ids.family_id, vector, 5)
        while docs is not None and not _found(docs, ids.memory_id) and self.seams.clock() < started + self.wait_s:
            self.seams.sleep(min(POLL_S, max(0.0, started + self.wait_s - self.seams.clock())))
            docs = search_db._vector_search(db, ids.family_id, vector, 5)

        hits = search_db.search_memories(db, ids.family_id, KEYWORD, vector=None)
        keyword_ok = any(h.memory_id == ids.memory_id for h in hits)
        _expect(keyword_ok, f"the keyword fallback did not find the throwaway memory for {KEYWORD!r}")

        if docs is None:
            vector_part = "$vectorSearch unavailable on this server"
            status = FAIL if self.is_atlas else WARN
            if self.is_atlas:
                vector_part += " (on Atlas: is memories_vector ready, and is the server 6.0.11+ or 7.0.2+?)"
        elif _found(docs, ids.memory_id):
            vector_part, status = f"$vectorSearch ran: {len(docs)} hit(s), the throwaway memory among them", PASS
        else:
            waited = f"after {self.wait_s:g}s" if self.wait_s else "(--wait 0)"
            vector_part = (
                f"$vectorSearch ran but found {len(docs)} hit(s) {waited}: the index is pending or has not "
                f"synced the new document yet (Atlas syncs within seconds); {WAIT_HINT}"
            )
            status = WARN
        return status, f"{vector_part}; keyword fallback found the memory for {KEYWORD!r}"

    # ------------------------------------------------------------ checkpointer

    def check_checkpointer(self) -> tuple[str, str]:
        from langgraph.checkpoint.base import empty_checkpoint

        db, ids = self._db, self.ids
        persistence = self.seams.checkpointer(self.uri, self.db_name, self.timeout_ms)
        try:
            saver, store = persistence.saver, persistence.store
            config = {"configurable": {"thread_id": ids.thread, "checkpoint_ns": ""}}
            checkpoint = empty_checkpoint()
            saved = saver.put(config, checkpoint, {"source": "input", "step": -1, "parents": {}}, {})
            back = saver.get_tuple(saved)
            _expect(back is not None and back.checkpoint.get("id") == checkpoint["id"],
                    "the checkpoint did not read back")
            saver.put_writes(saved, [("preflight", "ok")], "preflight-task")
            counts = {c: db[c].count_documents({"thread_id": ids.thread}) for c in CHECKPOINT_COLLECTIONS}
            _expect(all(counts.values()), f"checkpoint documents not found in {counts}")
            saver.delete_thread(ids.thread)
            left = {c: db[c].count_documents({"thread_id": ids.thread}) for c in CHECKPOINT_COLLECTIONS}
            _expect(not any(left.values()), f"delete_thread left {left}")

            store.put(ids.namespace, "probe", {"ok": True})
            item = store.get(ids.namespace, "probe")
            _expect(item is not None and item.value == {"ok": True}, "the store item did not read back")
            stored = db[STORE_COLLECTION].count_documents(self._store_query(store))
            _expect(stored >= 1, f"{STORE_COLLECTION} has no document for the store item")
            store.delete(ids.namespace, "probe")
            _expect(store.get(ids.namespace, "probe") is None, "the store item was not deleted")
        finally:
            persistence.close()
        return PASS, (
            f"checkpoint put/get_tuple/put_writes/delete_thread in {', '.join(CHECKPOINT_COLLECTIONS)}; "
            f"store put/get/delete in {STORE_COLLECTION}"
        )

    def _store_query(self, store: Any = None) -> dict[str, Any]:
        sep = getattr(store, "sep", "/") if store is not None else "/"
        ns = list(self.ids.namespace)
        return {"$or": [{"namespace_str": sep.join(ns)}, {"namespace": ns}]}

    # ----------------------------------------------------------------- cleanup

    def cleanup(self) -> tuple[str, str]:
        db, ids = self.db, self.ids
        if db is None:
            return SKIP, "nothing to clean up"
        errors: list[str] = []

        def attempt(what: str, fn: Callable[[], Any]) -> None:
            try:
                fn()
            except Exception as exc:  # noqa: BLE001 - keep cleaning, report at the end
                errors.append(f"{what}: {self._error(exc)}")

        attempt("purge_family", lambda: families_db.purge_family(db, ids.family_id))
        attempt("delete_users", lambda: users_db.delete_users(db, emails=[ids.email], user_ids=[ids.user_id]))
        # Diagnostic-only raw deletes: db/ has no function to un-revoke a session or drop a checkpoint.
        attempt("revoked_sessions", lambda: db[sessions_db.COLLECTION].delete_one({"_id": ids.jti}))
        for coll in CHECKPOINT_COLLECTIONS:
            attempt(coll, lambda coll=coll: db[coll].delete_many({"thread_id": ids.thread}))
        attempt(STORE_COLLECTION, lambda: db[STORE_COLLECTION].delete_many(self._store_query()))

        leftovers: dict[str, list[str]] = {}
        try:
            query = self._leftover_query()
            for coll in sorted(db.list_collection_names()):
                if coll.startswith("system."):
                    continue
                found = [str(d.get("_id")) for d in db[coll].find(query, {"_id": 1}).limit(20)]
                if found:
                    leftovers[coll] = found
        except Exception as exc:  # noqa: BLE001
            errors.append(f"leftover check: {self._error(exc)}")
        if leftovers:
            errors.append("leftovers: " + "; ".join(f"{c}: {', '.join(v)}" for c, v in leftovers.items()))
        _expect(not errors, " | ".join(errors))
        return PASS, f"deleted family {ids.family_id} and its account, session and checkpoints; 0 documents left"

    def _leftover_query(self) -> dict[str, Any]:
        ids = self.ids
        oids = [ObjectId(m) for m in ids.media_ids if ObjectId.is_valid(m)]
        plain_ids: list[Any] = [
            ids.family_id, ids.user_id, ids.parent_id, ids.child_id, ids.memory_id, ids.question_id,
            ids.jti, f"{ids.family_id}:{ids.prop_id}", f"{ids.family_id}:{ids.set_id}", *oids,
        ]
        clauses: list[dict[str, Any]] = [
            {"_id": {"$in": plain_ids}},
            {"familyId": ids.family_id},
            {"metadata.familyId": ids.family_id},
            {"email": ids.email},
            {"thread_id": ids.thread},
            *self._store_query()["$or"],
        ]
        if oids:
            clauses.append({"files_id": {"$in": oids}})
        return {"$or": clauses}


def _found(docs: list[dict] | None, memory_id: str) -> bool:
    return bool(docs) and any(str(d.get("_id")) == memory_id for d in docs or [])


def _tiny_png() -> bytes:
    from PIL import Image

    out = io.BytesIO()
    Image.new("RGB", (8, 8), "#c9a227").save(out, format="PNG")
    return out.getvalue()


# ------------------------------------------------------------------------------- CLI


@contextmanager
def _quiet_db_warnings() -> Iterator[None]:
    """db.client logs a warning per vector index it cannot create; the table already says
    so, and with no logging configured Python would print each one to stderr."""
    import logging

    logger = logging.getLogger("db.client")
    level = logger.level
    logger.setLevel(logging.ERROR)
    try:
        yield
    finally:
        logger.setLevel(level)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m seed.preflight",
        description="Check a real MongoDB (or Atlas cluster) end to end before the API and the demo use it.",
    )
    parser.add_argument("--json", action="store_true", help="print the rows as JSON instead of a table")
    parser.add_argument("--timeout-ms", type=int, default=realdb.DEFAULT_TIMEOUT_MS,
                        help="server selection and connect timeout in milliseconds (default 5000)")
    parser.add_argument("--wait", type=float, default=DEFAULT_WAIT_S,
                        help="seconds to wait for pending vector indexes and search (default 60; 0 = no wait)")
    parser.add_argument("--skip-checkpointer", action="store_true",
                        help="skip the LangGraph checkpointer and store check")
    return parser


def main(
    argv: list[str] | None = None,
    *,
    seams: PreflightSeams | None = None,
    out: TextIO | None = None,
    err: TextIO | None = None,
) -> int:
    """`python -m seed.preflight`. Returns the exit code (0 pass, 1 a check failed, 3 no database)."""
    out = out or sys.stdout
    err = err or sys.stderr
    args = _parser().parse_args(argv)
    if args.timeout_ms <= 0:
        _parser().error("--timeout-ms must be positive")
    if args.wait < 0:
        _parser().error("--wait must be 0 or more")

    realdb.load_env()
    uri = realdb.uri_from_env()
    db_name = realdb.db_name_from_env()
    if uri is None:
        if args.json:
            print(json.dumps({"error": UNSET_MESSAGE, "exitCode": EXIT_NO_DATABASE}), file=out)
        print(UNSET_MESSAGE, file=err)
        return EXIT_NO_DATABASE

    secrets = sorted(set(secret_values(os.environ)) | set(secret_values({"MONGODB_URI": uri})), key=len, reverse=True)
    with scrubbed_logs(secrets), _quiet_db_warnings():
        preflight = Preflight(
            uri, db_name, timeout_ms=args.timeout_ms, wait_s=args.wait,
            skip_checkpointer=args.skip_checkpointer, seams=seams, secrets=secrets,
        )
        if not args.json:
            print(f"Checking {realdb.redacted(uri)}, database {db_name!r} ...", file=err)
        report = preflight.run()
        text = json.dumps(report.as_json(), indent=2) if args.json else format_table(report)
        print(scrub(text, secrets), file=out)
    return report.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
