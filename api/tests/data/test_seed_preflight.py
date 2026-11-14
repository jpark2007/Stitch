"""seed/preflight.py: the real-MongoDB preflight, run offline on mongomock through its seams.

Nothing here touches the network or waits: connect returns a mongomock client, the clock
and sleep are fakes, and the checkpointer is a fake that records each call and writes into
the same mongomock database (MongoDBStore cannot run on mongomock). The last test runs the
real thing against MONGODB_URI_TEST and skips without it.
"""

from __future__ import annotations

import io
import json
import logging
import os
import uuid
from types import SimpleNamespace
from typing import Any
from urllib.parse import quote_plus

import mongomock
import pytest
from pymongo.errors import OperationFailure, ServerSelectionTimeoutError

from db import client as db_client
from db import families as families_db
from db import memories as memories_db
from db import users as users_db
from seed import preflight

USER = "presenter"
PASSWORD = "pa:ss@w0rd%21"
QUERY_VALUE = "StitchCluster0Presenter"
QUERY = f"appName={QUERY_VALUE}&retryWrites=true"
URI = f"mongodb+srv://{USER}:{quote_plus(PASSWORD)}@cluster0.example.mongodb.net/?{QUERY}"
FORBIDDEN = (USER, PASSWORD, quote_plus(PASSWORD), QUERY, QUERY_VALUE, "pa%3Ass", "w0rd%2521", URI)

EXPECTED_CHECKS = [
    "connect", "server", "indexes",
    *(f"vector index {name}" for name in db_client.VECTOR_INDEXES.values()),
    "round trip", "tree", "search", "checkpointer", "cleanup",
]


# ------------------------------------------------------------------------------ fakes


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0
        self.slept: list[float] = []

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


class FakeSaver:
    """Records calls; writes the documents MongoDBSaver would (thread_id-keyed) into the db."""

    def __init__(self, db: Any, calls: list[str]) -> None:
        self.db, self.calls = db, calls

    def put(self, config, checkpoint, metadata, versions):
        self.calls.append("put")
        thread = config["configurable"]["thread_id"]
        self.db.agent_checkpoints.insert_one({"thread_id": thread, "checkpoint_id": checkpoint["id"]})
        return {"configurable": {**config["configurable"], "checkpoint_id": checkpoint["id"]}}

    def get_tuple(self, config):
        self.calls.append("get_tuple")
        doc = self.db.agent_checkpoints.find_one({"thread_id": config["configurable"]["thread_id"]})
        return SimpleNamespace(checkpoint={"id": doc["checkpoint_id"]}) if doc else None

    def put_writes(self, config, writes, task_id):
        self.calls.append("put_writes")
        for channel, value in writes:
            self.db.agent_checkpoint_writes.insert_one(
                {"thread_id": config["configurable"]["thread_id"], "channel": channel, "value": value, "task": task_id}
            )

    def delete_thread(self, thread):
        self.calls.append("delete_thread")
        self.db.agent_checkpoints.delete_many({"thread_id": thread})
        self.db.agent_checkpoint_writes.delete_many({"thread_id": thread})


class FakeStore:
    sep = "/"

    def __init__(self, db: Any, calls: list[str]) -> None:
        self.db, self.calls = db, calls

    def put(self, namespace, key, value):
        self.calls.append("store.put")
        self.db.agent_store.insert_one(
            {"namespace": list(namespace), "namespace_str": "/".join(namespace), "key": key, "value": value}
        )

    def get(self, namespace, key):
        self.calls.append("store.get")
        doc = self.db.agent_store.find_one({"namespace_str": "/".join(namespace), "key": key})
        return SimpleNamespace(value=doc["value"]) if doc else None

    def delete(self, namespace, key):
        self.calls.append("store.delete")
        self.db.agent_store.delete_many({"namespace_str": "/".join(namespace), "key": key})


@pytest.fixture
def mongo():
    client = mongomock.MongoClient(tz_aware=True)
    yield client
    client.close()


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("MONGODB_URI", URI)
    monkeypatch.setenv("MONGODB_DB", "stitch_preflight_test")
    return monkeypatch


def make_seams(mongo, clock: FakeClock | None = None, calls: list[str] | None = None, **overrides):
    clock = clock or FakeClock()
    calls = [] if calls is None else calls
    seen: dict[str, Any] = {}

    def connect(uri, db_name, *, timeout_ms):
        seen["connect"] = (db_name, timeout_ms)
        return mongo, mongo[db_name]

    def checkpointer(uri, db_name, timeout_ms):
        calls.append("open")
        db = mongo[db_name]
        return preflight.Persistence(FakeSaver(db, calls), FakeStore(db, calls), lambda: calls.append("close"))

    seams = preflight.PreflightSeams(
        connect=overrides.pop("connect", connect),
        checkpointer=overrides.pop("checkpointer", checkpointer),
        clock=clock.clock,
        sleep=clock.sleep,
        token=overrides.pop("token", lambda: "0123456789ab"),
    )
    return seams, seen


def run(argv: list[str], seams) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = preflight.main(argv, seams=seams, out=out, err=err)
    return code, out.getvalue(), err.getvalue()


def table_rows(text: str) -> dict[str, str]:
    """check -> status, from the printed table."""
    rows: dict[str, str] = {}
    lines = text.splitlines()
    header = next(i for i, line in enumerate(lines) if line.startswith("CHECK"))
    for line in lines[header + 1:]:
        if not line.strip():
            break
        for check in EXPECTED_CHECKS:
            if line.startswith(check + " ") and line[len(check):].split()[0] in {"PASS", "WARN", "FAIL", "SKIP"}:
                rows[check] = line[len(check):].split()[0]
    return rows


def assert_no_secrets(*texts: str) -> None:
    for text in texts:
        for secret in FORBIDDEN:
            assert secret.lower() not in text.lower(), (secret, text)


def everything(db) -> dict[str, list]:
    return {name: sorted(map(str, (d["_id"] for d in db[name].find({}, {"_id": 1}))))
            for name in sorted(db.list_collection_names())}


def preexisting(db) -> None:
    """Data that must survive the preflight untouched."""
    db_client.ensure_indexes(db)
    users_db.create_user(db, email="keep@example.com", password_hash="h", name="Keep", user_id="keep_user")
    families_db.create_family(db, name="Keep", creator_user_id="keep_user", family_id="keep_family")


# ------------------------------------------------------------------------- full runs


def test_full_preflight_on_mongomock_passes_with_warnings_and_leaves_nothing(mongo, env):
    db = mongo["stitch_preflight_test"]
    preexisting(db)
    before = everything(db)
    calls: list[str] = []
    seams, seen = make_seams(mongo, calls=calls)

    code, out, err = run(["--wait", "30"], seams)

    assert code == preflight.EXIT_OK, out
    assert seen["connect"] == ("stitch_preflight_test", 5000)
    rows = table_rows(out)
    assert list(rows) == EXPECTED_CHECKS, out
    assert rows["connect"] == rows["indexes"] == rows["round trip"] == rows["tree"] == "PASS"
    assert rows["checkpointer"] == rows["cleanup"] == "PASS"
    # mongomock: no buildInfo, no search indexes, no $vectorSearch. $graphLookup works on it.
    assert rows["server"] == "WARN"
    assert all(rows[f"vector index {n}"] == "WARN" for n in db_client.VECTOR_INDEXES.values())
    assert rows["search"] == "WARN"
    assert out.rstrip().splitlines()[-1] == "PREFLIGHT PASS with 5 warnings"
    assert "mongomock has no search indexes" in out
    assert "keyword fallback found the memory" in out
    assert "native $graphLookup" in out
    assert "is_atlas no" in out
    # The redacted target and the database name are shown; nothing else of the URI.
    assert "mongodb+srv://<credentials redacted>@cluster0.example.mongodb.net" in out
    assert "stitch_preflight_test" in out
    assert_no_secrets(out, err)

    # Cleanup: exactly what was there before is left (the collections preflight created are empty).
    after = everything(db)
    assert {k: v for k, v in after.items() if v} == {k: v for k, v in before.items() if v}
    assert db.families.find_one({"_id": "keep_family"}) is not None
    for name in db.list_collection_names():
        assert db[name].count_documents({"$or": [
            {"familyId": "preflight_0123456789ab"}, {"email": "preflight-0123456789ab@preflight.invalid"},
            {"thread_id": "preflight-0123456789ab"}, {"namespace_str": "preflight/0123456789ab"},
        ]}) == 0, name

    assert calls == [
        "open", "put", "get_tuple", "put_writes", "delete_thread",
        "store.put", "store.get", "store.delete", "store.get", "close",
    ]


def test_json_output_has_the_same_rows(mongo, env):
    seams, _ = make_seams(mongo)
    code, out, _err = run(["--json", "--wait", "0", "--skip-checkpointer"], seams)
    assert code == preflight.EXIT_OK
    report = json.loads(out)
    assert [r["check"] for r in report["rows"]] == EXPECTED_CHECKS
    assert {r["check"]: r["status"] for r in report["rows"]}["checkpointer"] == "SKIP"
    assert report["summary"] == "PREFLIGHT PASS with 5 warnings"
    assert report["exitCode"] == 0
    assert report["database"] == "stitch_preflight_test"
    assert report["target"] == "mongodb+srv://<credentials redacted>@cluster0.example.mongodb.net"
    assert all(set(r) == {"check", "status", "detail", "seconds"} for r in report["rows"])
    assert_no_secrets(out)


def test_unset_uri_exits_3(monkeypatch, mongo):
    monkeypatch.delenv("MONGODB_URI", raising=False)
    seams, seen = make_seams(mongo)
    code, _out, err = run([], seams)
    assert code == preflight.EXIT_NO_DATABASE == 3
    assert "MONGODB_URI is not set (looked in the environment, api/.env, ../.env)" in err
    assert seen == {}  # never connected


def test_unreachable_server_exits_3_with_the_access_list_hint(mongo, env):
    def unreachable(uri, db_name, *, timeout_ms):
        raise ServerSelectionTimeoutError(f"{uri}: connection timed out, Timeout: 5.0s")

    seams, _ = make_seams(mongo, connect=unreachable)
    code, out, err = run([], seams)
    assert code == preflight.EXIT_NO_DATABASE
    rows = table_rows(out)
    assert rows == {"connect": "FAIL"}
    assert "IP access list" in out and "PREFLIGHT FAIL: connect" in out
    assert_no_secrets(out, err)

    code, out, err = run(["--json"], seams)
    assert code == 3 and json.loads(out)["rows"][0]["status"] == "FAIL"
    assert_no_secrets(out, err)


def test_refused_credentials_exit_3_without_the_uri(mongo, env):
    def refused(uri, db_name, *, timeout_ms):
        raise OperationFailure(f"bad auth : authentication failed for {uri}", code=8000)

    seams, _ = make_seams(mongo, connect=refused)
    code, out, err = run(["--json"], seams)
    assert code == 3
    detail = json.loads(out)["rows"][0]["detail"]
    assert "authentication failed" in detail and "Database Access" in detail
    assert_no_secrets(out, err)


def test_a_failing_round_trip_step_still_cleans_up_and_exits_1(mongo, env, monkeypatch):
    db = mongo["stitch_preflight_test"]
    preexisting(db)
    before = everything(db)

    def broken(*_args, **_kwargs):
        raise RuntimeError(f"insert failed talking to {URI}")

    monkeypatch.setattr(memories_db, "create_memory", broken)
    seams, _ = make_seams(mongo)
    code, out, err = run(["--wait", "0"], seams)

    assert code == preflight.EXIT_FAILED == 1
    rows = table_rows(out)
    assert rows["round trip"] == "FAIL"
    assert rows["tree"] == rows["search"] == "SKIP"
    assert rows["cleanup"] == "PASS"
    assert out.rstrip().splitlines()[-1] == "PREFLIGHT FAIL: round trip"
    assert "insert failed talking to" in out
    assert "at memories (after users, families, people x2 passed)" in out
    assert_no_secrets(out, err)
    # The account, family and people written before the failure are gone.
    after = everything(db)
    assert {k: v for k, v in after.items() if v} == {k: v for k, v in before.items() if v}


def test_leftovers_are_a_cleanup_failure_with_their_ids(mongo, env, monkeypatch):
    monkeypatch.setattr(families_db, "purge_family", lambda db, family_id: None)
    seams, _ = make_seams(mongo)
    code, out, _err = run(["--wait", "0", "--skip-checkpointer", "--json"], seams)
    assert code == 1
    cleanup = json.loads(out)["rows"][-1]
    assert cleanup["check"] == "cleanup" and cleanup["status"] == "FAIL"
    assert "leftovers" in cleanup["detail"]
    assert "preflight_0123456789ab" in cleanup["detail"]
    assert "preflight_memory_0123456789ab" in cleanup["detail"]


def test_the_uri_never_reaches_text_json_or_logs(mongo, env, monkeypatch, caplog):
    """Every step that raises or logs with the URI in it is scrubbed."""
    original = db_client.ensure_search_indexes_report

    def chatty(db):
        logging.getLogger("seed.test").warning("creating search indexes on %s", URI)
        return original(db)

    def failing_checkpointer(uri, db_name, timeout_ms):
        raise RuntimeError(f"could not open {uri}")

    monkeypatch.setattr(db_client, "ensure_search_indexes_report", chatty)
    seams, _ = make_seams(mongo, checkpointer=failing_checkpointer)
    caplog.set_level(logging.WARNING)
    for argv in ([], ["--json"]):
        code, out, err = run([*argv, "--wait", "0"], seams)
        assert code == 1  # the checkpointer failed
        assert "could not open" in out
        assert_no_secrets(out, err)
    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert "creating search indexes on" in logged
    assert_no_secrets(logged, caplog.text)


def test_bad_arguments_exit_2(mongo, env):
    seams, _ = make_seams(mongo)
    for argv in (["--bogus"], ["--wait", "-1"], ["--timeout-ms", "0"]):
        with pytest.raises(SystemExit) as stop:
            preflight.main(argv, seams=seams, out=io.StringIO(), err=io.StringIO())
        assert stop.value.code == 2


def test_timeout_flag_reaches_connect(mongo, env):
    seams, seen = make_seams(mongo)
    run(["--timeout-ms", "1500", "--wait", "0", "--skip-checkpointer"], seams)
    assert seen["connect"] == ("stitch_preflight_test", 1500)


# ------------------------------------------------------------- vector index polling


class AtlasSearchCollection:
    """A collection whose search-index listing follows a script of Atlas statuses."""

    def __init__(self, name: str, statuses: list[str], *, exists: bool = False, kind: str = "vectorSearch",
                 refuse: Exception | None = None) -> None:
        self.name, self.statuses, self.kind, self.refuse = name, list(statuses), kind, refuse
        self.exists = exists
        self.lists = 0

    def list_search_indexes(self):
        if not self.exists:
            return iter([])
        status = self.statuses[min(self.lists, len(self.statuses) - 1)]
        self.lists += 1
        return iter([{"name": self.name, "type": self.kind, "status": status, "queryable": status == "READY",
                      "latestDefinition": db_client._vector_definition()}])

    def create_search_index(self, model):
        if self.refuse:
            raise self.refuse
        self.exists = True
        return model.document["name"]

    def update_search_index(self, name, definition):  # pragma: no cover - not used here
        pass


class AtlasDb:
    def __init__(self, collections: dict[str, AtlasSearchCollection]) -> None:
        self.collections = collections

    def __getitem__(self, name):
        return self.collections[name]

    def list_collection_names(self):
        return sorted(self.collections)

    def create_collection(self, name):  # pragma: no cover - every collection exists
        pass


def atlas_preflight(collections, wait_s: float, clock: FakeClock) -> preflight.Preflight:
    seams = preflight.PreflightSeams(clock=clock.clock, sleep=clock.sleep, token=lambda: "abcdefabcdef")
    pf = preflight.Preflight(URI, "stitch", wait_s=wait_s, seams=seams, secrets=["presenter"])
    pf.db = AtlasDb(collections)
    pf.is_atlas = True
    return pf


def test_atlas_vector_indexes_go_from_pending_to_ready_within_wait():
    clock = FakeClock()
    collections = {
        coll: AtlasSearchCollection(name, ["PENDING", "BUILDING", "READY"])
        for coll, name in db_client.VECTOR_INDEXES.items()
    }
    pf = atlas_preflight(collections, 60, clock)
    pf.check_vector_indexes()
    rows = pf.report.rows
    assert [r.check for r in rows] == [f"vector index {n}" for n in db_client.VECTOR_INDEXES.values()]
    assert all(r.status == "PASS" for r in rows), rows
    assert all(r.detail.startswith("created now; ready") for r in rows)
    assert clock.slept == [preflight.POLL_S, preflight.POLL_S]  # two polls, no real waiting
    assert all(r.seconds == 2 * preflight.POLL_S for r in rows)


def test_atlas_vector_indexes_still_pending_after_wait_are_warnings_with_the_hint():
    clock = FakeClock()
    collections = {
        coll: AtlasSearchCollection(name, ["PENDING"]) for coll, name in db_client.VECTOR_INDEXES.items()
    }
    pf = atlas_preflight(collections, 12, clock)
    pf.check_vector_indexes()
    assert all(r.status == "WARN" and "re-run with --wait 180" in r.detail for r in pf.report.rows)
    assert sum(clock.slept) == 12  # waited exactly --wait, in POLL_S steps


def test_wait_zero_does_not_wait():
    clock = FakeClock()
    collections = {
        coll: AtlasSearchCollection(name, ["PENDING", "READY"]) for coll, name in db_client.VECTOR_INDEXES.items()
    }
    pf = atlas_preflight(collections, 0, clock)
    pf.check_vector_indexes()
    assert clock.slept == []
    assert all(r.status == "WARN" and "(--wait 0)" in r.detail for r in pf.report.rows)


def test_an_existing_ready_index_passes_and_an_atlas_refusal_fails_with_the_server_message():
    clock = FakeClock()
    message = "The maximum number of FTS indexes has been reached for this instance size."
    names = list(db_client.VECTOR_INDEXES.items())
    collections = {
        names[0][0]: AtlasSearchCollection(names[0][1], ["READY"], exists=True),
        names[1][0]: AtlasSearchCollection(names[1][1], ["READY"],
                                           refuse=OperationFailure(message, code=68)),
        names[2][0]: AtlasSearchCollection(names[2][1], ["READY"], exists=True, kind="search"),
    }
    pf = atlas_preflight(collections, 0, clock)
    pf.check_vector_indexes()
    first, second, third = pf.report.rows
    assert first.status == "PASS" and first.detail.startswith("already existed; ready")
    assert second.status == "FAIL" and message in second.detail and "at most 3 search indexes" in second.detail
    assert third.status == "FAIL" and "wrong type" in third.detail


# -------------------------------------------------------------------------- helpers


@pytest.mark.parametrize(("uri", "expected"), [
    ("mongodb://h:27017", "mongodb://h:27017/?serverSelectionTimeoutMS=900&connectTimeoutMS=900"),
    ("mongodb://h:27017/", "mongodb://h:27017/?serverSelectionTimeoutMS=900&connectTimeoutMS=900"),
    ("mongodb+srv://u:p@c.mongodb.net/?appName=x",
     "mongodb+srv://u:p@c.mongodb.net/?appName=x&serverSelectionTimeoutMS=900&connectTimeoutMS=900"),
    ("mongodb://h/db?a=1;b=2", "mongodb://h/db?a=1;b=2;serverSelectionTimeoutMS=900;connectTimeoutMS=900"),
    ("mongodb://h/db?", "mongodb://h/db?serverSelectionTimeoutMS=900&connectTimeoutMS=900"),
])
def test_with_timeouts(uri, expected):
    assert preflight.with_timeouts(uri, 900) == expected


def test_the_summary_line():
    report = preflight.Report("t", "d", [preflight.Row("a", "PASS", "")])
    assert report.summary() == "PREFLIGHT PASS"
    report.rows.append(preflight.Row("b", "WARN", ""))
    assert report.summary() == "PREFLIGHT PASS with 1 warning"
    report.rows += [preflight.Row("c", "FAIL", ""), preflight.Row("d", "FAIL", "")]
    assert report.summary() == "PREFLIGHT FAIL: c, d"


# ------------------------------------------------------------------- real MongoDB


def test_preflight_against_a_real_mongodb(monkeypatch):
    """The whole preflight, checkpointer included, against MONGODB_URI_TEST (skips without it)."""
    uri = os.environ.get("MONGODB_URI_TEST")
    if not uri:
        pytest.skip("MONGODB_URI_TEST is not set")
    name = f"stitch_preflight_{uuid.uuid4().hex[:8]}"
    monkeypatch.setenv("MONGODB_URI", uri)
    monkeypatch.setenv("MONGODB_DB", name)
    out, err = io.StringIO(), io.StringIO()
    try:
        code = preflight.main(["--json", "--wait", "0"], out=out, err=err)
        report = json.loads(out.getvalue())
        rows = {r["check"]: r for r in report["rows"]}
        assert list(rows) == EXPECTED_CHECKS, report
        assert rows["cleanup"]["status"] == "PASS", report
        must_pass = ["connect", "indexes", "round trip", "tree", "checkpointer"]
        assert all(rows[c]["status"] == "PASS" for c in must_pass), report
        assert "GridFS" in rows["round trip"]["detail"]
        if code != 0:
            # Only Atlas search-index trouble (pending, tier limits) may fail it.
            assert all(c.startswith("vector index") or c == "search"
                       for c, r in rows.items() if r["status"] == "FAIL"), report
    finally:
        from pymongo import MongoClient

        client = MongoClient(uri, serverSelectionTimeoutMS=3000)
        client.drop_database(name)
        client.close()
