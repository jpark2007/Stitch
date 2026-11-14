"""routes/memories.py: multipart submit, the run SSE stream, and answers, against a fake agent seam."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import threading
import time
import types
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import auth
import schemas
from db import client as db_client
from db import families as families_db
from db import media as media_db
from db import memories as memories_db
from db import people as people_db
from db import users as users_db
from db.client import get_db
from routes import memories as memories_routes

API_DIR = Path(__file__).resolve().parents[2]

PNG_1 = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32 + b"one"
PNG_2 = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32 + b"two"


# ------------------------------------------------------------------------------ fake seam


class FakeRuns:
    """Recording stand-ins for agent.runs.start_run / stream_run / answer_run."""

    def __init__(self) -> None:
        self.started: list[tuple] = []
        self.streamed: list[tuple] = []
        self.answered: list[tuple] = []
        self.run_id = "run-1"
        self.start_error: BaseException | None = None
        self.stream_sync_error: BaseException | None = None
        self.items: list[Any] = []
        self.raise_after: BaseException | None = None
        self.delay: float = 0.0
        self.closed = False
        self.answer_error: BaseException | None = None

    @property
    def calls(self) -> int:
        return len(self.started) + len(self.streamed) + len(self.answered)

    def start_run(self, family_id, user_id, person_id, fields, photo_ids):
        self.started.append((family_id, user_id, person_id, fields, photo_ids))
        if self.start_error is not None:
            raise self.start_error
        return self.run_id

    def stream_run(self, family_id, run_id):
        self.streamed.append((family_id, run_id))
        if self.stream_sync_error is not None:
            raise self.stream_sync_error
        return self._gen()

    async def _gen(self):
        try:
            for item in self.items:
                if self.delay:
                    await asyncio.sleep(self.delay)
                yield item
            if self.raise_after is not None:
                raise self.raise_after
        finally:
            self.closed = True

    async def answer_run(self, family_id, run_id, user_id, answer):
        # async on purpose: the route awaits whatever the seam returns
        self.answered.append((family_id, run_id, user_id, answer))
        if self.answer_error is not None:
            raise self.answer_error

    def module(self) -> types.ModuleType:
        mod = types.ModuleType("agent.runs")
        mod.start_run = self.start_run
        mod.stream_run = self.stream_run
        mod.answer_run = self.answer_run
        return mod


@pytest.fixture
def fake(monkeypatch) -> FakeRuns:
    runs = FakeRuns()
    monkeypatch.setitem(sys.modules, "agent.runs", runs.module())
    return runs


class RunNotFound(LookupError):
    pass


class MemoryNotFound(LookupError):
    pass


class NotRunOwner(PermissionError):
    pass


class RunNotWaiting(RuntimeError):
    pass


class RealShapedRuns:
    """Mirrors the real agent.runs: sync functions and its exception classes."""

    def __init__(self) -> None:
        self.start_error: BaseException | None = None
        self.stream_error: BaseException | None = None
        self.answer_error: BaseException | None = None
        self.answered: list[tuple] = []
        self.answer_delay: float = 0.0
        self.answer_started = threading.Event()
        self.answer_returned_at: float | None = None
        self.answer_thread: str | None = None
        self.started: list[tuple] = []
        self.on_start = None

    def start_run(self, family_id, user_id, person_id, fields, photo_ids):
        self.started.append((family_id, user_id, person_id, fields, photo_ids))
        if self.on_start is not None:
            self.on_start(photo_ids)
        if self.start_error is not None:
            raise self.start_error
        return "run-real"

    def stream_run(self, family_id, run_id):
        if self.stream_error is not None:
            raise self.stream_error

        async def gen():
            yield ("done", schemas.DoneEvent(memory_id="m"))

        return gen()

    def answer_run(self, family_id, run_id, user_id, answer):
        self.answer_thread = threading.current_thread().name
        self.answered.append((family_id, run_id, user_id, answer))
        self.answer_started.set()
        if self.answer_delay:
            time.sleep(self.answer_delay)  # like the real one waiting on a threading.Condition
        self.answer_returned_at = time.monotonic()
        if self.answer_error is not None:
            raise self.answer_error

    def module(self) -> types.ModuleType:
        mod = types.ModuleType("agent.runs")
        mod.start_run = self.start_run
        mod.stream_run = self.stream_run
        mod.answer_run = self.answer_run
        mod.RunNotFound = RunNotFound
        mod.MemoryNotFound = MemoryNotFound
        mod.NotRunOwner = NotRunOwner
        mod.RunNotWaiting = RunNotWaiting
        return mod


def not_configured_module() -> types.ModuleType:
    """agent.runs with no RunManager installed: every seam call raises a plain RuntimeError."""

    def fail(*args, **kwargs):
        raise RuntimeError("agent runs are not configured")

    mod = types.ModuleType("agent.runs")
    mod.start_run = mod.stream_run = mod.answer_run = fail
    mod.RunNotFound = RunNotFound
    mod.MemoryNotFound = MemoryNotFound
    mod.NotRunOwner = NotRunOwner
    mod.RunNotWaiting = RunNotWaiting
    return mod


@pytest.fixture
def real(env, monkeypatch) -> RealShapedRuns:
    runs = RealShapedRuns()
    monkeypatch.setitem(sys.modules, "agent.runs", runs.module())
    return runs


@pytest.fixture
def unconfigured(env, monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "agent.runs", not_configured_module())


# ------------------------------------------------------------------------------ app and data


class Env(types.SimpleNamespace):
    pass


@pytest.fixture
def env(mdb, fake) -> Env:
    db_client.ensure_indexes(mdb)
    alice = users_db.create_user(mdb, email="alice@x.com", password_hash="x", name="Alice")
    carol = users_db.create_user(mdb, email="carol@x.com", password_hash="x", name="Carol")
    bob = users_db.create_user(mdb, email="bob@x.com", password_hash="x", name="Bob")

    fam_a = families_db.create_family(mdb, name="Ahn", creator_user_id=alice.id)
    families_db.add_member(mdb, fam_a.id, carol.id)  # a member with no person yet
    alice_p = people_db.create_person(mdb, fam_a.id, schemas.NewPerson(name="Alice"), added_by=alice.id, user_id=alice.id)
    grandma = people_db.create_person(mdb, fam_a.id, schemas.NewPerson(name="Grandma"), added_by=alice.id)
    uncle = people_db.create_person(mdb, fam_a.id, schemas.NewPerson(name="Uncle"), added_by=alice.id)
    memory_a = memories_db.create_memory(mdb, fam_a.id, title="Hymn books", year=1962, participant_ids=[grandma.id])

    fam_b = families_db.create_family(mdb, name="Baker", creator_user_id=bob.id)
    bob_p = people_db.create_person(mdb, fam_b.id, schemas.NewPerson(name="Bob"), added_by=bob.id, user_id=bob.id)
    memory_b = memories_db.create_memory(mdb, fam_b.id, title="Other", year=1990, participant_ids=[bob_p.id])

    app = FastAPI()
    app.include_router(memories_routes.router)
    app.dependency_overrides[get_db] = lambda: mdb

    def client_for(user: schemas.User) -> TestClient:
        tc = TestClient(app)
        tc.cookies.set(auth.COOKIE_NAME, auth.create_session_token(user.id))
        return tc

    return Env(
        db=mdb, app=app, fake=fake, alice=alice, carol=carol, bob=bob, fam_a=fam_a, fam_b=fam_b,
        alice_p=alice_p, grandma=grandma, uncle=uncle, bob_p=bob_p, memory_a=memory_a, memory_b=memory_b,
        a=client_for(alice), c=client_for(carol), b=client_for(bob),
    )


def _all_media(db) -> list[dict]:
    return list(db[media_db.INLINE].find({}, {"data": 0}))


def _submit(tc: TestClient, fid: str, data: dict | None = None, files: list | None = None):
    form = {"text": "We sang from the green hymn books.", "year": "1962"}
    if data is not None:
        form = data
    return tc.post(f"/families/{fid}/memories", data=form, files=files or [])


def _parse_sse(body: str) -> list[tuple[str, dict]]:
    """[(event name, data JSON)] in order. Comment lines (pings) are skipped."""
    events: list[tuple[str, dict]] = []
    for block in body.split("\n\n"):
        lines = [ln for ln in block.split("\n") if ln and not ln.startswith(":")]
        if not lines:
            continue
        fields = dict(ln.split(": ", 1) for ln in lines)
        assert set(fields) == {"event", "data"}, block
        events.append((fields["event"], json.loads(fields["data"])))
    return events


# ------------------------------------------------------------------------------ submit


def test_submit_full_multipart_starts_run(env):
    form = {
        "text": "We sang from the green hymn books.",
        "year": "1962",
        "question": "What did Sundays sound like?",
        "memoryId": env.memory_a.id,
        "participantIds": [env.grandma.id, env.uncle.id],
    }
    files = [("photos", ("a.png", PNG_1, "image/png")), ("photos", ("b.png", PNG_2, "image/png"))]
    res = env.a.post(f"/families/{env.fam_a.id}/memories", data=form, files=files)

    assert res.status_code == 202, res.text
    assert res.json() == {"runId": "run-1", "memoryId": env.memory_a.id}
    assert len(env.fake.started) == 1
    fid, uid, pid, fields, photo_ids = env.fake.started[0]
    assert (fid, uid, pid) == (env.fam_a.id, env.alice.id, env.alice_p.id)
    assert fields == schemas.SubmitMemoryFields(
        text="We sang from the green hymn books.",
        year=1962,
        question="What did Sundays sound like?",
        memory_id=env.memory_a.id,
        participant_ids=[env.grandma.id, env.uncle.id],
    )
    assert len(photo_ids) == 2
    stored = [media_db.get_media(env.db, env.fam_a.id, pid) for pid in photo_ids]
    assert [m.data for m in stored] == [PNG_1, PNG_2]
    assert all(m.kind == "photo" and m.content_type == "image/png" for m in stored)
    assert media_db.get_media(env.db, env.fam_b.id, photo_ids[0]) is None


def test_submit_minimal_and_blank_optionals(env):
    res = _submit(env.a, env.fam_a.id, {"text": "Snow day", "year": "2001", "title": " ", "memoryId": ""})
    assert res.status_code == 202, res.text
    assert res.json() == {"runId": "run-1", "memoryId": None}
    fields = env.fake.started[0][3]
    assert fields == schemas.SubmitMemoryFields(text="Snow day", year=2001)
    assert env.fake.started[0][4] == []


def test_submit_async_start_run_is_awaited(env, monkeypatch):
    async def start_run(*args):
        env.fake.started.append(args)
        return "run-async"

    monkeypatch.setattr(sys.modules["agent.runs"], "start_run", start_run)
    res = _submit(env.a, env.fam_a.id)
    assert res.status_code == 202
    assert res.json()["runId"] == "run-async"


def test_submit_without_person_is_409(env):
    res = _submit(env.c, env.fam_a.id)
    assert res.status_code == 409
    assert res.json() == {"detail": "Add or claim your person in this family first"}
    assert env.fake.calls == 0


def test_submit_participant_from_other_family_is_400(env):
    res = _submit(env.a, env.fam_a.id, {"text": "x", "year": "1990", "participantIds": [env.grandma.id, env.bob_p.id]})
    assert res.status_code == 400
    assert env.fake.calls == 0


def test_submit_unknown_participant_is_400(env):
    res = _submit(env.a, env.fam_a.id, {"text": "x", "year": "1990", "participantIds": ["nobody"]})
    assert res.status_code == 400


def test_submit_memory_from_other_family_is_404(env):
    res = _submit(env.a, env.fam_a.id, {"text": "x", "year": "1990", "memoryId": env.memory_b.id})
    assert res.status_code == 404
    assert env.fake.calls == 0


def test_submit_non_image_photo_is_415_and_nothing_stored(env):
    files = [("photos", ("a.png", PNG_1, "image/png")), ("photos", ("notes.txt", b"hello", "text/plain"))]
    res = _submit(env.a, env.fam_a.id, files=files)
    assert res.status_code == 415
    assert _all_media(env.db) == []
    assert env.fake.calls == 0


def test_submit_svg_is_415(env):
    res = _submit(env.a, env.fam_a.id, files=[("photos", ("x.svg", b"<svg/>", "image/svg+xml"))])
    assert res.status_code == 415


def test_submit_oversized_photo_is_413(env, monkeypatch):
    monkeypatch.setattr(media_db, "MAX_BYTES", 16)
    res = _submit(env.a, env.fam_a.id, files=[("photos", ("a.png", PNG_1, "image/png"))])
    assert res.status_code == 413
    assert _all_media(env.db) == []


def test_submit_ignores_empty_file_input(env):
    res = _submit(env.a, env.fam_a.id, files=[("photos", ("", b"", "application/octet-stream"))])
    assert res.status_code == 202, res.text
    assert env.fake.started[0][4] == []


def test_submit_text_value_under_photos_is_422(env):
    res = _submit(env.a, env.fam_a.id, {"text": "x", "year": "1990", "photos": "not-a-file"})
    assert res.status_code == 422
    assert env.fake.calls == 0


def test_submit_empty_named_photo_is_422(env):
    res = _submit(env.a, env.fam_a.id, files=[("photos", ("a.png", b"", "image/png"))])
    assert res.status_code == 422
    assert _all_media(env.db) == []


def test_submit_too_many_photos_is_422(env):
    files = [("photos", (f"{i}.png", PNG_1, "image/png")) for i in range(11)]
    res = _submit(env.a, env.fam_a.id, files=files)
    assert res.status_code == 422
    assert _all_media(env.db) == []


@pytest.mark.parametrize(
    "form",
    [
        {"text": "   ", "year": "1990"},
        {"text": "", "year": "1990"},
        {"year": "1990"},
        {"text": "x"},
        {"text": "x", "year": "nineteen"},
        {"text": "x", "year": "1799"},
        {"text": "x", "year": "3000"},
    ],
)
def test_submit_bad_fields_are_422(env, form):
    res = _submit(env.a, env.fam_a.id, form)
    assert res.status_code == 422
    assert env.fake.calls == 0


def test_submit_start_run_failure_is_503_and_photos_deleted(env):
    env.fake.start_error = RuntimeError("model API down: secret-key-123")
    files = [("photos", ("a.png", PNG_1, "image/png")), ("photos", ("b.jpg", PNG_2, "image/jpeg"))]
    res = _submit(env.a, env.fam_a.id, files=files)
    assert res.status_code == 503
    assert res.json() == {"detail": "Could not start the studio"}
    photo_ids = env.fake.started[0][4]
    assert len(photo_ids) == 2
    assert all(media_db.get_media(env.db, env.fam_a.id, pid) is None for pid in photo_ids)
    assert _all_media(env.db) == []


def test_submit_to_a_moment_without_a_scene_is_409_and_photos_deleted(env):
    """The studio refuses (LookupError) a memory the route found: it has no scene to add to."""
    env.fake.start_error = LookupError("memory 'm' not found, or it has no scene yet")
    files = [("photos", ("a.png", PNG_1, "image/png")), ("photos", ("b.png", PNG_2, "image/png"))]
    res = _submit(env.a, env.fam_a.id, {"text": "x", "year": "1962", "memoryId": env.memory_a.id}, files)
    assert res.status_code == 409
    assert res.json() == {"detail": "This moment has no scene yet, so another telling cannot be added to it"}
    assert res.json()["detail"] == memories_routes.NO_SCENE
    assert len(env.fake.started) == 1 and len(env.fake.started[0][4]) == 2
    assert _all_media(env.db) == []


def test_submit_lookup_error_without_memory_id_stays_404(env):
    env.fake.start_error = LookupError("something the studio could not find")
    res = _submit(env.a, env.fam_a.id, files=[("photos", ("a.png", PNG_1, "image/png"))])
    assert res.status_code == 404
    assert res.json() == {"detail": "Memory not found"}
    assert len(env.fake.started) == 1
    assert _all_media(env.db) == []


def test_submit_unknown_memory_is_404_before_start_run(env):
    env.fake.start_error = LookupError("must not be reached")
    files = [("photos", ("a.png", PNG_1, "image/png"))]
    res = _submit(env.a, env.fam_a.id, {"text": "x", "year": "1990", "memoryId": "no_such_memory"}, files)
    assert res.status_code == 404
    assert res.json() == {"detail": "Memory not found"}
    assert env.fake.calls == 0
    assert _all_media(env.db) == []


def test_submit_without_agent_module_is_503(env, monkeypatch):
    monkeypatch.setitem(sys.modules, "agent.runs", None)  # import raises ImportError
    res = _submit(env.a, env.fam_a.id, files=[("photos", ("a.png", PNG_1, "image/png"))])
    assert res.status_code == 503
    assert _all_media(env.db) == []


def test_submit_signed_out_is_401(env):
    res = TestClient(env.app).post(f"/families/{env.fam_a.id}/memories", data={"text": "x", "year": "1990"})
    assert res.status_code == 401
    assert env.fake.calls == 0


# ------------------------------------------------------------------------------ stream


def _plan_question_done() -> list[tuple[str, schemas.Model]]:
    return [
        ("plan", schemas.PlanEvent(todos=[schemas.PlanItem(text="Find the props", status="in_progress")])),
        ("question", schemas.QuestionEvent(text="What colour were the hymn books?")),
        ("done", schemas.DoneEvent(memory_id="mem-1")),
    ]


def test_stream_sends_events_as_sse(env):
    env.fake.items = _plan_question_done()
    res = env.a.get(f"/families/{env.fam_a.id}/runs/run-1/stream")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/event-stream")
    assert res.headers["cache-control"] == "no-cache"
    assert res.headers["x-accel-buffering"] == "no"
    events = _parse_sse(res.text)
    assert events == [(name, model.model_dump(by_alias=True, mode="json")) for name, model in env.fake.items]
    assert events[0][1] == {"todos": [{"text": "Find the props", "status": "in_progress"}]}
    assert events[2][1] == {"memoryId": "mem-1"}
    assert env.fake.streamed == [(env.fam_a.id, "run-1")]
    assert env.fake.closed


def test_stream_stops_after_done(env):
    env.fake.items = [("done", schemas.DoneEvent(memory_id="m")), ("question", schemas.QuestionEvent(text="late"))]
    events = _parse_sse(env.a.get(f"/families/{env.fam_a.id}/runs/run-1/stream").text)
    assert [name for name, _ in events] == ["done"]
    assert env.fake.closed


@pytest.mark.parametrize(
    "bad",
    [
        ("nope", schemas.QuestionEvent(text="x")),
        ("question", schemas.DoneEvent(memory_id="m")),
        ("question", {"text": "a dict, not a model"}),
        "not a pair",
    ],
)
def test_stream_invalid_event_becomes_one_error(env, bad):
    env.fake.items = [("plan", schemas.PlanEvent(todos=[])), bad, ("done", schemas.DoneEvent(memory_id="m"))]
    events = _parse_sse(env.a.get(f"/families/{env.fam_a.id}/runs/run-1/stream").text)
    assert events == [("plan", {"todos": []}), ("error", {"message": "Internal stream error"})]
    assert env.fake.closed


def test_stream_invalid_first_event_becomes_one_error(env):
    env.fake.items = [("nope", schemas.QuestionEvent(text="x"))]
    events = _parse_sse(env.a.get(f"/families/{env.fam_a.id}/runs/run-1/stream").text)
    assert events == [("error", {"message": "Internal stream error"})]


def test_stream_exception_midway_is_one_generic_error(env, caplog):
    env.fake.items = [("plan", schemas.PlanEvent(todos=[]))]
    env.fake.raise_after = RuntimeError("boom: secret-key-123")
    with caplog.at_level("ERROR"):
        res = env.a.get(f"/families/{env.fam_a.id}/runs/run-1/stream")
    events = _parse_sse(res.text)
    assert [name for name, _ in events] == ["plan", "error"]
    assert "secret-key-123" not in res.text
    assert "boom" not in res.text
    assert "secret-key-123" in caplog.text  # details only in the log


def test_stream_agent_error_event_passes_through_and_ends(env):
    env.fake.items = [("error", schemas.ErrorEvent(message="Could not draw the set")), ("plan", schemas.PlanEvent(todos=[]))]
    events = _parse_sse(env.a.get(f"/families/{env.fam_a.id}/runs/run-1/stream").text)
    assert events == [("error", {"message": "Could not draw the set"})]


def test_stream_empty_generator_is_empty_200(env):
    env.fake.items = []
    res = env.a.get(f"/families/{env.fam_a.id}/runs/run-1/stream")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/event-stream")
    assert res.text == ""


def test_stream_unknown_run_is_404(env):
    env.fake.stream_sync_error = LookupError("no such run")
    res = env.a.get(f"/families/{env.fam_a.id}/runs/nope/stream")
    assert res.status_code == 404


@pytest.mark.parametrize("exc", [LookupError("gone"), PermissionError("not yours")])
def test_stream_error_on_first_item_is_404(env, exc):
    env.fake.raise_after = exc
    res = env.a.get(f"/families/{env.fam_a.id}/runs/run-1/stream")
    assert res.status_code == 404
    assert env.fake.closed


def test_stream_sends_heartbeat_while_waiting(env, monkeypatch):
    monkeypatch.setattr(memories_routes, "HEARTBEAT_SECONDS", 0.01)
    env.fake.items = [("plan", schemas.PlanEvent(todos=[])), ("done", schemas.DoneEvent(memory_id="m"))]
    env.fake.delay = 0.08
    res = env.a.get(f"/families/{env.fam_a.id}/runs/run-1/stream")
    assert ": ping\n\n" in res.text
    assert [name for name, _ in _parse_sse(res.text)] == ["plan", "done"]


def test_sse_events_closes_agent_generator_on_disconnect():
    state = {"closed": False, "cancelled": False}
    started = asyncio.Event()

    async def agent():
        try:
            yield ("plan", schemas.PlanEvent(todos=[]))
            started.set()
            await asyncio.sleep(3600)  # waiting on the model
            yield ("done", schemas.DoneEvent(memory_id="m"))
        except asyncio.CancelledError:
            state["cancelled"] = True
            raise
        finally:
            state["closed"] = True

    async def main():
        agen = agent()
        first = await anext(agen)
        out = memories_routes.sse_events(agen, first)
        assert (await anext(out)).startswith("event: plan\n")
        consumer = asyncio.ensure_future(anext(out))
        await asyncio.wait_for(started.wait(), 1)
        await asyncio.sleep(0)
        consumer.cancel()  # what the server does when the client goes away
        with pytest.raises(asyncio.CancelledError):
            await consumer
        await out.aclose()

    asyncio.run(main())
    assert state["closed"] and state["cancelled"]


def test_sse_events_aclose_between_events_closes_agent():
    state = {"closed": False}

    async def agent():
        try:
            yield ("plan", schemas.PlanEvent(todos=[]))
            yield ("plan", schemas.PlanEvent(todos=[]))
            yield ("done", schemas.DoneEvent(memory_id="m"))
        finally:
            state["closed"] = True

    async def main():
        agen = agent()
        out = memories_routes.sse_events(agen, await anext(agen))
        await anext(out)
        await out.aclose()

    asyncio.run(main())
    assert state["closed"]


# ------------------------------------------------------------------------------ answer


@pytest.mark.parametrize(
    "body",
    [
        {"kind": "answer", "text": "Green, with gold letters"},
        {"kind": "props", "props": []},
        {"kind": "props", "props": [{"id": "p1", "action": "rename", "name": "Hymnal"}, {"id": "p2", "action": "keep"}]},
        {"kind": "merge", "approve": False},
        {"kind": "merge", "approve": True},
    ],
)
def test_answer_valid_kinds_are_204(env, body):
    res = env.a.post(f"/families/{env.fam_a.id}/runs/run-1/answer", json=body)
    assert res.status_code == 204, res.text
    assert res.content == b""
    assert len(env.fake.answered) == 1
    fid, run_id, uid, answer = env.fake.answered[0]
    assert (fid, run_id, uid) == (env.fam_a.id, "run-1", env.alice.id)
    assert answer == schemas.AnswerRequest.model_validate(body)


@pytest.mark.parametrize(
    "body",
    [
        {"kind": "answer"},
        {"kind": "answer", "text": "   "},
        {"kind": "props"},
        {"kind": "props", "props": None},
        {"kind": "merge"},
        {"kind": "something"},
        {"kind": "answer", "text": "x", "extra": 1},
        {},
    ],
)
def test_answer_missing_fields_are_422(env, body):
    res = env.a.post(f"/families/{env.fam_a.id}/runs/run-1/answer", json=body)
    assert res.status_code == 422
    assert env.fake.calls == 0


@pytest.mark.parametrize(
    ("exc", "code", "detail"),
    [
        (LookupError("no run"), 404, "Run not found"),
        (PermissionError("other user"), 403, "Only the person who started this run can answer"),
        # The exception's own text never reaches the client; known cases map to a fixed sentence.
        (ValueError("run run-1 is done, not waiting for an answer"), 409, "This run is not waiting for an answer"),
    ],
)
def test_answer_seam_errors(env, exc, code, detail):
    env.fake.answer_error = exc
    res = env.a.post(f"/families/{env.fam_a.id}/runs/run-1/answer", json={"kind": "answer", "text": "yes"})
    assert res.status_code == code
    assert res.json() == {"detail": detail}


def test_answer_seam_other_error_is_503(env, caplog):
    env.fake.answer_error = OSError("disk: secret-key-123")
    with caplog.at_level("ERROR"):
        res = env.a.post(f"/families/{env.fam_a.id}/runs/run-1/answer", json={"kind": "answer", "text": "yes"})
    assert res.status_code == 503
    assert res.json() == {"detail": "The studio is unavailable"}
    assert "secret-key-123" in caplog.text


# ------------------------------------------------------------------------------ real-shaped seam


ANSWER = {"kind": "answer", "text": "Green"}


@pytest.mark.parametrize(
    ("exc", "code", "detail"),
    [
        (RunNotWaiting("run run-1 is done, not waiting for an answer"), 409, "This run is not waiting for an answer"),
        (RunNotFound("run 'run-1' not found"), 404, "Run not found"),
        (NotRunOwner("only the person who started this run can answer it"), 403, "Only the person who started this run can answer"),
        (ValueError("an answer needs text"), 409, "An answer needs text"),
        (ValueError("This question wants something else entirely"), 409, "Run cannot take that answer"),
        (ValueError(""), 409, "Run cannot take that answer"),
    ],
)
def test_real_answer_errors(env, real, exc, code, detail):
    real.answer_error = exc
    res = env.a.post(f"/families/{env.fam_a.id}/runs/run-1/answer", json=ANSWER)
    assert res.status_code == code
    assert res.json() == {"detail": detail}


def test_real_answer_runs_sync_seam_off_the_event_loop(env, real):
    res = env.a.post(f"/families/{env.fam_a.id}/runs/run-1/answer", json=ANSWER)
    assert res.status_code == 204
    assert real.answered[0][:3] == (env.fam_a.id, "run-1", env.alice.id)
    assert real.answer_thread != threading.main_thread().name


def test_sync_answer_run_does_not_block_the_loop(env, real):
    real.answer_delay = 1.0
    app = FastAPI()
    app.include_router(memories_routes.router)
    app.dependency_overrides[get_db] = lambda: env.db
    health_at: list[float] = []

    @app.get("/health")
    async def health() -> dict:
        health_at.append(time.monotonic())
        return {"ok": True}

    async def main():
        transport = httpx.ASGITransport(app=app)
        cookies = {auth.COOKIE_NAME: auth.create_session_token(env.alice.id)}
        async with httpx.AsyncClient(transport=transport, base_url="http://test", cookies=cookies) as client:

            async def answer():
                return await client.post(f"/families/{env.fam_a.id}/runs/run-1/answer", json=ANSWER)

            async def ping():
                deadline = time.monotonic() + 5
                while not real.answer_started.is_set():  # only polls if the loop is free
                    assert time.monotonic() < deadline
                    await asyncio.sleep(0.01)
                return await client.get("/health")

            return await asyncio.gather(answer(), ping())

    answer_res, health_res = asyncio.run(main())
    assert answer_res.status_code == 204
    assert health_res.status_code == 200
    assert len(health_at) == 1 and real.answer_returned_at is not None
    assert health_at[0] < real.answer_returned_at
    assert real.answer_returned_at - health_at[0] > 0.5  # health was served while answer_run slept


def test_unconfigured_answer_is_503(env, unconfigured):
    res = env.a.post(f"/families/{env.fam_a.id}/runs/run-1/answer", json=ANSWER)
    assert res.status_code == 503
    assert res.json() == {"detail": "The studio is unavailable"}


def test_unconfigured_stream_is_503(env, unconfigured):
    res = env.a.get(f"/families/{env.fam_a.id}/runs/run-1/stream")
    assert res.status_code == 503
    assert res.json() == {"detail": "The studio is unavailable"}


def test_unconfigured_submit_is_503_and_photos_deleted(env, unconfigured):
    res = _submit(env.a, env.fam_a.id, files=[("photos", ("a.png", PNG_1, "image/png"))])
    assert res.status_code == 503
    assert res.json() == {"detail": "Could not start the studio"}
    assert _all_media(env.db) == []


@pytest.mark.parametrize("exc", [RunNotFound("run 'x' not found"), NotRunOwner("not yours")])
def test_real_stream_lookup_at_call_is_404(env, real, exc):
    real.stream_error = exc
    res = env.a.get(f"/families/{env.fam_a.id}/runs/x/stream")
    assert res.status_code == 404
    assert res.json() == {"detail": "Run not found"}


def test_real_stream_sends_events(env, real):
    res = env.a.get(f"/families/{env.fam_a.id}/runs/run-1/stream")
    assert res.status_code == 200
    assert _parse_sse(res.text) == [("done", {"memoryId": "m"})]


def test_real_submit_starts_run(env, real):
    res = _submit(env.a, env.fam_a.id, files=[("photos", ("a.png", PNG_1, "image/png"))])
    assert res.status_code == 202
    assert res.json()["runId"] == "run-real"
    assert len(_all_media(env.db)) == 1


@pytest.mark.parametrize(
    ("exc", "code", "detail"),
    [
        (MemoryNotFound("memory 'm' not found, or it has no scene yet"), 404, "Memory not found"),
        (ValueError("person 'p' is not in this family"), 422, "This memory could not be started with those details"),
        (RuntimeError("graph exploded"), 503, "Could not start the studio"),
    ],
)
def test_real_submit_errors_delete_photos(env, real, exc, code, detail):
    real.start_error = exc
    stored_during_start: list[int] = []
    real.on_start = lambda photo_ids: stored_during_start.append(len(_all_media(env.db)))
    files = [("photos", ("a.png", PNG_1, "image/png")), ("photos", ("b.png", PNG_2, "image/png"))]
    before = len(_all_media(env.db))
    res = _submit(env.a, env.fam_a.id, files=files)
    assert res.status_code == code
    assert res.json() == {"detail": detail}
    assert before == 0
    assert stored_during_start == [2]  # both photos were stored when start_run ran
    photo_ids = real.started[0][4]
    assert all(media_db.get_media(env.db, env.fam_a.id, pid) is None for pid in photo_ids)
    assert len(_all_media(env.db)) == 0  # and both were deleted when it failed


def test_real_memory_not_found_for_a_found_memory_is_409(env, real):
    real.start_error = MemoryNotFound(f"memory {env.memory_a.id!r} not found, or it has no scene yet")
    files = [("photos", ("a.png", PNG_1, "image/png"))]
    res = _submit(env.a, env.fam_a.id, {"text": "x", "year": "1962", "memoryId": env.memory_a.id}, files)
    assert res.status_code == 409
    assert res.json() == {"detail": memories_routes.NO_SCENE}
    assert len(real.started) == 1
    assert _all_media(env.db) == []


# ------------------------------------------------------------------------------ cross-family


def test_other_family_gets_404_on_every_route_and_seam_untouched(env):
    fid_b = env.fam_b.id
    env.fake.items = _plan_question_done()
    form = {"text": "x", "year": "1990", "participantIds": [env.grandma.id]}
    files = [("photos", ("a.png", PNG_1, "image/png"))]

    responses = [
        env.a.post(f"/families/{fid_b}/memories", data=form, files=files),
        env.a.get(f"/families/{fid_b}/runs/run-1/stream"),
        env.a.post(f"/families/{fid_b}/runs/run-1/answer", json={"kind": "answer", "text": "x"}),
        # invalid bodies still get 404, not 422: membership is checked first
        env.a.post(f"/families/{fid_b}/memories", data={"year": "1990"}),
        env.a.post(f"/families/{fid_b}/runs/run-1/answer", json={"kind": "answer"}),
        env.a.post("/families/no-such-family/runs/run-1/answer", json={"kind": "answer", "text": "x"}),
    ]
    assert [r.status_code for r in responses] == [404] * len(responses)
    assert env.fake.calls == 0
    assert _all_media(env.db) == []


def test_responses_never_contain_password_hash(env):
    env.fake.items = _plan_question_done()
    bodies = [
        _submit(env.a, env.fam_a.id).text,
        env.a.get(f"/families/{env.fam_a.id}/runs/run-1/stream").text,
        _submit(env.c, env.fam_a.id).text,
    ]
    assert all("passwordHash" not in b and "password_hash" not in b for b in bodies)


# ------------------------------------------------------------------------------ STITCH_AGENT=off


class _Tripwire(types.ModuleType):
    """agent.runs stand-in that records every attribute the route looks up (none exist)."""

    def __init__(self) -> None:
        super().__init__("agent.runs")
        self.looked_up: list[str] = []

    def __getattr__(self, name: str):
        self.looked_up.append(name)
        raise AttributeError(name)


@pytest.fixture
def off(env, monkeypatch) -> _Tripwire:
    """The bare app started as if with STITCH_AGENT=off, and agent.runs replaced by a tripwire."""
    tripwire = _Tripwire()
    monkeypatch.setitem(sys.modules, "agent.runs", tripwire)
    env.app.state.agent_mode = "off"
    return tripwire


def _off_requests(tc: TestClient, fid: str) -> list[httpx.Response]:
    return [
        _submit(tc, fid, files=[("photos", ("a.png", PNG_1, "image/png"))]),
        tc.get(f"/families/{fid}/runs/run-1/stream"),
        tc.post(f"/families/{fid}/runs/run-1/answer", json=ANSWER),
    ]


def test_agent_off_constant():
    assert memories_routes.AGENT_OFF_STATE == "agent_mode"


def test_agent_off_every_run_route_is_503_without_touching_the_agent(env, off):
    submit, stream, answer = _off_requests(env.a, env.fam_a.id)
    assert submit.status_code == 503
    assert submit.json() == {"detail": "Could not start the studio"}
    assert stream.status_code == 503
    assert stream.json() == {"detail": "The studio is unavailable"}
    assert answer.status_code == 503
    assert answer.json() == {"detail": "The studio is unavailable"}
    assert off.looked_up == [], "agent.runs must not be imported under off"
    assert _all_media(env.db) == [], "no photo is stored under off"


def test_agent_off_does_not_call_a_working_seam(env):
    """Even when agent.runs would work (a key is set, say), off never reaches it."""
    env.app.state.agent_mode = "off"
    env.fake.items = _plan_question_done()
    responses = _off_requests(env.a, env.fam_a.id)
    assert [r.status_code for r in responses] == [503, 503, 503]
    assert env.fake.calls == 0
    assert _all_media(env.db) == []


def test_agent_off_membership_still_comes_first(env, off):
    """A non-member gets 404 and a signed-out caller 401, not 503."""
    assert [r.status_code for r in _off_requests(env.a, env.fam_b.id)] == [404, 404, 404]
    assert [r.status_code for r in _off_requests(env.b, env.fam_a.id)] == [404, 404, 404]
    assert [r.status_code for r in _off_requests(TestClient(env.app), env.fam_a.id)] == [401, 401, 401]
    assert off.looked_up == []
    assert _all_media(env.db) == []


def test_agent_off_ignores_bad_fields_and_member_without_person(env, off):
    """The gate runs before field checks: nothing about the submit is looked at."""
    assert _submit(env.a, env.fam_a.id, {"text": "   ", "year": "1962"}).status_code == 503
    assert _submit(env.c, env.fam_a.id).status_code == 503  # carol has no person yet
    assert off.looked_up == []


@pytest.mark.parametrize("mode", [None, "fake", "real", ""])
def test_agent_mode_other_than_off_behaves_as_before(env, mode):
    if mode is not None:
        env.app.state.agent_mode = mode
    env.fake.items = _plan_question_done()
    submit = _submit(env.a, env.fam_a.id, files=[("photos", ("a.png", PNG_1, "image/png"))])
    assert submit.status_code == 202, submit.text
    assert submit.json()["runId"] == "run-1"
    assert len(_all_media(env.db)) == 1
    stream = env.a.get(f"/families/{env.fam_a.id}/runs/run-1/stream")
    assert stream.status_code == 200
    assert [name for name, _ in _parse_sse(stream.text)] == ["plan", "question", "done"]
    assert env.a.post(f"/families/{env.fam_a.id}/runs/run-1/answer", json=ANSWER).status_code == 204
    assert len(env.fake.started) == len(env.fake.streamed) == len(env.fake.answered) == 1


# ------------------------------------------------------------------------------ import isolation


def test_importing_routes_does_not_import_agent():
    code = (
        "import sys\n"
        "import routes.memories\n"
        "leaked = sorted(m for m in sys.modules if m == 'agent' or m.startswith('agent.'))\n"
        "assert not leaked, leaked\n"
        "print('ok')\n"
    )
    result = subprocess.run([sys.executable, "-c", code], cwd=API_DIR, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"


def test_fresh_import_of_routes_does_not_import_agent(monkeypatch):
    import routes

    monkeypatch.setattr(routes, "memories", memories_routes)  # restored after the re-import
    for name in list(sys.modules):
        if name in ("agent", "routes.memories") or name.startswith("agent."):
            monkeypatch.delitem(sys.modules, name)
    import routes.memories  # noqa: F401

    assert not [m for m in sys.modules if m == "agent" or m.startswith("agent.")]
