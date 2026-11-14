"""routes/memories.py: multipart submit, the run SSE stream, and answers, against a fake agent seam."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import types
from pathlib import Path
from typing import Any

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
        (ValueError("The run is not waiting for an answer"), 409, "The run is not waiting for an answer"),
    ],
)
def test_answer_seam_errors(env, exc, code, detail):
    env.fake.answer_error = exc
    res = env.a.post(f"/families/{env.fam_a.id}/runs/run-1/answer", json={"kind": "answer", "text": "yes"})
    assert res.status_code == code
    assert res.json() == {"detail": detail}


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
