"""Second tellings that overlap on one memory never lose a narration track (seed/fake_runs.py).

A scripted second telling sleeps through several steps before it narrates and saves. Before
the per-memory lock, two runs on one memory both narrated over the snapshot they read at the
start, and the run that saved last dropped the other teller's `narrations[teller]` track (and,
for one teller twice, could store one run's telling with the other run's track). Now each run
re-reads the memory under `ScriptedRunManager._memory_lock`, narrates over that fresh scene and
saves with `save_scene(expected_version=...)`.

mongomock + seed_demo, steps 50 ms apart so the runs really overlap; no MongoDB, no keys, no
network. The HTTP case runs the STITCH_FAKE_DB app with STITCH_AGENT=fake.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

import agent.runs
import main
import routes.memories
import schemas
from db import client as db_client
from db import memories as memories_db
from seed import demo, fake_runs
from seed.fake_runs import ScriptedRunManager, split_sentences
from seed.smoke import parse_sse

FID = demo.DEMO_FAMILY_ID
ADMIN = demo.DEMO_ACCOUNTS[0]
ACCOUNTS = {a.person_id: a for a in demo.DEMO_ACCOUNTS}
STEP = 0.05
DEADLINE = 30.0


# ------------------------------------------------------------------------------ fixtures


@pytest.fixture
def db(mdb):
    db_client.ensure_indexes(mdb)
    demo.seed_demo(mdb)
    db_client.set_db(mdb)
    try:
        yield mdb
    finally:
        db_client.set_db(None)


@pytest.fixture
def manager(db):
    mgr = ScriptedRunManager(db_getter=lambda: db, step_seconds=STEP)
    yield mgr
    mgr.shutdown()


def _memory(db, memory_id: str) -> schemas.Memory:
    memory = memories_db.get_memory(db, FID, memory_id)
    assert memory is not None and memory.screenplay is not None and memory.timeline is not None
    return memory


def _text(person_id: str, n: int = 1) -> str:
    return (
        f"This is version {n} from {person_id}. We laughed the whole way home! "
        f"Nobody wanted that day to end, not even {person_id}."
    )


def _start(mgr: ScriptedRunManager, person_id: str, memory: schemas.Memory, text: str) -> str:
    fields = schemas.SubmitMemoryFields(text=text, year=memory.year, memory_id=memory.id)
    return mgr.start_run(FID, ADMIN.user_id, person_id, fields, [])


def _finish(mgr: ScriptedRunManager, run_id: str) -> list[tuple[str, Any]]:
    assert mgr.wait(FID, run_id, timeout=DEADLINE) in ("done", "error")
    return mgr.events(FID, run_id)


def _track_text(memory: schemas.Memory, author: str) -> list[str]:
    tl = memory.timeline
    assert tl is not None and tl.narrations is not None
    assert author in tl.narrations, f"no narration track for {author}"
    return [line.text for line in tl.narrations[author].lines]


def assert_every_teller_has_a_track(memory: schemas.Memory) -> None:
    """Every telling of the memory is heard: the timeline has a narration track for its teller,
    and the track says that telling's words (one line per sentence)."""
    tl = memory.timeline
    assert tl is not None and tl.narrations is not None
    for telling in memory.tellings:
        assert telling.author_id in tl.narrations, (
            f"{telling.author_id} told this moment but has no narration track: {sorted(tl.narrations)}"
        )
    assert tl.default_narrator in tl.narrations


# ------------------------------------------------------------------------------ two tellers


def test_two_tellers_at_once_both_keep_their_track(db, manager):
    memory_id = "demo_lily_graduation"
    before = _memory(db, memory_id)
    told = {t.author_id for t in before.tellings}
    first, second = [pid for pid in ("dad", "sam") if pid not in told]
    texts = {first: _text(first), second: _text(second)}

    runs = {pid: _start(manager, pid, before, texts[pid]) for pid in (first, second)}
    events = {pid: _finish(manager, run_id) for pid, run_id in runs.items()}

    for pid, evs in events.items():
        assert evs[-1][0] == "done", (pid, evs[-1])
        assert [name for name, _ in evs].count("done") == 1
        assert "error" not in [name for name, _ in evs]
    after = _memory(db, memory_id)
    assert after.version == before.version + 2
    by_author = {t.author_id: t for t in after.tellings}
    assert set(by_author) == told | {first, second}
    for pid in (first, second):
        assert by_author[pid].text == texts[pid]
        assert _track_text(after, pid) == split_sentences(texts[pid])
        assert pid in after.participant_ids
    for pid in told:  # the tellers who were there before keep theirs too
        assert _track_text(after, pid) == _track_text(before, pid)
    assert_every_teller_has_a_track(after)
    # The two runs report the two versions, one each.
    versions = sorted(p.version for evs in events.values() for name, p in evs if name == "scene_ready")
    assert versions == [before.version + 1, before.version + 2]


def test_the_same_teller_twice_at_once_keeps_one_telling_matching_its_track(db, manager, monkeypatch):
    """The first save is slowed down, so the second run reaches its own save meanwhile: the
    stored telling and the stored track must still come from the same run."""
    memory_id = "demo_lily_graduation"
    before = _memory(db, memory_id)
    teller = "dad"
    assert teller not in {t.author_id for t in before.tellings}

    real_save = memories_db.save_scene
    calls = {"n": 0}
    calls_lock = threading.Lock()

    def slow_first_save(*args: Any, **kw: Any):
        with calls_lock:
            calls["n"] += 1
            n = calls["n"]
        if n == 1:
            time.sleep(0.4)
        return real_save(*args, **kw)

    monkeypatch.setattr(memories_db, "save_scene", slow_first_save)
    one, two = _text(teller, 1), _text(teller, 2) + " And then it rained."
    run_one = _start(manager, teller, before, one)
    time.sleep(STEP / 2)
    run_two = _start(manager, teller, before, two)
    for run_id in (run_one, run_two):
        evs = _finish(manager, run_id)
        assert evs[-1][0] == "done", evs[-1]

    after = _memory(db, memory_id)
    mine = [t for t in after.tellings if t.author_id == teller]
    assert len(mine) == 1, [t.text for t in mine]
    assert _track_text(after, teller) == split_sentences(mine[0].text)
    assert after.version == before.version + 2
    assert_every_teller_has_a_track(after)


# ------------------------------------------------------------------------------ outside writers


def test_a_save_from_outside_the_manager_is_retried_on_the_fresh_scene(db, manager, monkeypatch):
    """Someone else saves the scene between the fresh read and this run's save: the run gets
    VersionConflict, re-reads, narrates over the new scene and saves on top of it."""
    memory_id = "demo_lily_graduation"
    before = _memory(db, memory_id)
    real_save = memories_db.save_scene
    bumped = {"done": False}

    def outside_writer_first(*args: Any, **kw: Any):
        if kw.get("expected_version") is not None and not bumped["done"]:
            bumped["done"] = True
            db["memories"].update_one({"_id": memory_id, "familyId": FID}, {"$inc": {"version": 1}})
        return real_save(*args, **kw)

    monkeypatch.setattr(memories_db, "save_scene", outside_writer_first)
    run_id = _start(manager, "sam", before, _text("sam"))
    evs = _finish(manager, run_id)
    assert evs[-1][0] == "done", evs[-1]
    after = _memory(db, memory_id)
    assert after.version == before.version + 2  # the outside bump, then this run's save
    assert [t.author_id for t in after.tellings].count("sam") == 1
    assert _track_text(after, "sam") == split_sentences(_text("sam"))
    assert_every_teller_has_a_track(after)


def test_a_memory_that_keeps_changing_ends_the_run_with_one_plain_error(db, manager, monkeypatch):
    memory_id = "demo_lily_graduation"
    before = _memory(db, memory_id)
    attempts = {"n": 0}

    def always_conflict(*_a: Any, **kw: Any):
        assert kw.get("expected_version") is not None
        attempts["n"] += 1
        raise memories_db.VersionConflict("changed again")

    monkeypatch.setattr(memories_db, "save_scene", always_conflict)
    run_id = _start(manager, "sam", before, _text("sam"))
    evs = _finish(manager, run_id)
    names = [name for name, _ in evs]
    assert names[-1] == "error" and names.count("error") == 1 and "done" not in names
    assert evs[-1][1].message == fake_runs.ERROR_MESSAGE
    assert attempts["n"] == fake_runs.SECOND_TELLING_ATTEMPTS
    after = _memory(db, memory_id)
    assert after.version == before.version and after.timeline == before.timeline


def test_the_memory_lock_is_per_memory_and_not_the_family_lock(manager):
    a = manager._memory_lock(FID, "m1")
    assert manager._memory_lock(FID, "m1") is a
    assert manager._memory_lock(FID, "m2") is not a
    assert manager._memory_lock("other_family", "m1") is not a
    assert manager._family_lock(FID) is not a


# ------------------------------------------------------------------------------ over HTTP


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[Any, TestClient]]:
    monkeypatch.setenv("STITCH_FAKE_DB", "1")
    monkeypatch.setenv("STITCH_AGENT", "fake")
    monkeypatch.setenv("STITCH_FAKE_RUN_STEP", str(STEP))
    monkeypatch.delenv("STITCH_CORS_ORIGINS", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(routes.memories, "HEARTBEAT_SECONDS", 0.05)
    db_client.set_db(None)
    app = main.create_app()
    try:
        with TestClient(app) as root:
            assert isinstance(agent.runs.get_manager(), ScriptedRunManager)
            yield app, root
    finally:
        agent.runs.configure(None)
        db_client.set_db(None)


def _browser(app: Any, person_id: str) -> TestClient:
    c = TestClient(app)
    res = c.post("/auth/login", json={"email": ACCOUNTS[person_id].email, "password": demo.DEMO_PASSWORD})
    assert res.status_code == 200, res.text
    return c


def _stream(c: TestClient, run_id: str) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    deadline = time.monotonic() + DEADLINE
    with c.stream("GET", f"/families/{FID}/runs/{run_id}/stream") as stream:
        assert stream.status_code == 200
        for name, data in parse_sse(stream.iter_lines()):
            assert time.monotonic() < deadline, "the run did not finish in time"
            schemas.RUN_EVENTS[name].model_validate(data)
            events.append((name, data))
            if name in ("done", "error"):
                break
    return events


def test_two_accounts_tell_the_same_moment_at_once_over_http(api):
    app, _root = api
    memory_id = "demo_green_sedan_trip"
    db = db_client.get_db()
    before = _memory(db, memory_id)
    told = {t.author_id for t in before.tellings}
    tellers = [pid for pid in ACCOUNTS if pid not in told][:2]
    assert len(tellers) == 2, "the demo needs two account holders who have not told this moment"
    browsers = {pid: _browser(app, pid) for pid in tellers}
    try:
        runs = {}
        for pid, c in browsers.items():  # back to back: both are in flight before either saves
            res = c.post(
                f"/families/{FID}/memories",
                data={"text": _text(pid), "year": str(before.year), "memoryId": memory_id},
            )
            assert res.status_code == 202, res.text
            runs[pid] = schemas.SubmitMemoryResponse.model_validate(res.json()).run_id
        for pid, c in browsers.items():
            events = _stream(c, runs[pid])
            assert events[-1] == ("done", {"memoryId": memory_id}), events[-1]
    finally:
        for c in browsers.values():
            c.close()

    after = _memory(db, memory_id)
    assert after.version == before.version + 2
    by_author = {t.author_id: t for t in after.tellings}
    assert set(by_author) == told | set(tellers)
    for pid in tellers:
        assert by_author[pid].text == _text(pid)
        assert _track_text(after, pid) == split_sentences(_text(pid))
    assert_every_teller_has_a_track(after)
