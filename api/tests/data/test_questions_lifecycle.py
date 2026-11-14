"""A pending question closes once its person tells the story (ARCHITECTURE.md section 6, 'Pending prompts').

End to end over HTTP on `main.create_app()` with STITCH_FAKE_DB=1 (the lifespan installs the
seeded mongomock database) and `seed.fake_runs.ScriptedRunManager` behind `agent.runs`, the
same set-up as test_demo_path.py, so it runs offline.

Sam signs in, picks an open question from GET /families/demo_family/questions, answers it by
submitting a telling (to the question's memory when it has one, otherwise as a new memory
told in reply to the question's text), drives the run to done, and the question is gone
from the list. Every response body and SSE line is checked for `passwordHash`.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

import agent.runs
import main
import routes.memories
import schemas
from db import client as db_client
from db import memories as memories_db
from seed import demo
from seed.fake_runs import ScriptedRunManager

FID = demo.DEMO_FAMILY_ID
SAM_EMAIL = "sam@demo.stitch"
FORBIDDEN = ("passwordHash", "password_hash", demo.DEMO_PASSWORD)
STREAM_DEADLINE_SECONDS = 20.0
HEARTBEAT_SECONDS = 0.05
PAUSES = {"question": {"kind": "answer", "text": "Grandma Rosa was there too."},
          "props_found": {"kind": "props", "props": []}}


def _assert_clean(text: str, where: str) -> None:
    for secret in FORBIDDEN:
        assert secret not in text, f"{where} leaked {secret!r}"


@dataclass
class Guard:
    checked: int = 0
    lines: int = 0
    seen: list[str] = field(default_factory=list)

    def hook(self, response: httpx.Response) -> None:
        if response.headers.get("content-type", "").startswith("text/event-stream"):
            return  # scanned line by line in drive_to_done()
        response.read()
        where = f"{response.request.method} {response.request.url.path}"
        _assert_clean(response.content.decode("utf-8", "replace"), where)
        _assert_clean(str(response.headers), f"headers of {where}")
        self.checked += 1
        self.seen.append(where)


@dataclass
class App:
    client: TestClient
    guard: Guard
    manager: ScriptedRunManager


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch) -> Iterator[App]:
    monkeypatch.setenv("STITCH_FAKE_DB", "1")
    monkeypatch.setenv("STITCH_AGENT", "fake")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("STITCH_CORS_ORIGINS", raising=False)
    monkeypatch.setattr(routes.memories, "HEARTBEAT_SECONDS", HEARTBEAT_SECONDS)
    db_client.set_db(None)

    guard = Guard()
    try:
        client = TestClient(main.create_app())
        client.event_hooks = {"request": [], "response": [guard.hook]}
        with client:
            auto = agent.runs._manager  # the lifespan's scripted manager (STITCH_AGENT=fake)
            assert isinstance(auto, ScriptedRunManager), auto
            auto.shutdown()
            manager = ScriptedRunManager(step_seconds=0.0)
            agent.runs.configure(manager)
            try:
                yield App(client=client, guard=guard, manager=manager)
            finally:
                manager.shutdown()
                agent.runs.configure(None)
    finally:
        agent.runs.configure(None)
        db_client.set_db(None)


# ------------------------------------------------------------------------------ helpers


def _ok(res: httpx.Response, status: int = 200) -> Any:
    assert res.status_code == status, f"{res.request.method} {res.request.url} -> {res.status_code}: {res.text}"
    return res.json() if res.content else None


def _login(client: TestClient, email: str) -> schemas.MeResponse:
    body = _ok(client.post("/auth/login", json={"email": email, "password": demo.DEMO_PASSWORD}))
    return schemas.MeResponse.model_validate(body, by_alias=True, by_name=False)


def _open_questions(client: TestClient) -> list[schemas.Question]:
    body = _ok(client.get(f"/families/{FID}/questions"))
    return schemas.QuestionsResponse.model_validate(body, by_alias=True, by_name=False).questions


def _frames(res: httpx.Response, guard: Guard, started: float) -> Iterator[tuple[str, dict]]:
    """`(event, data)` frames of an open SSE response; `: ping` comments are skipped."""
    name: str | None = None
    data: list[str] = []
    for line in res.iter_lines():
        assert time.monotonic() - started < STREAM_DEADLINE_SECONDS, "stream stalled"
        _assert_clean(line, "SSE line")
        guard.lines += 1
        if line == "":
            if name is not None:
                yield name, json.loads("\n".join(data))
            name, data = None, []
        elif line.startswith(":"):
            continue
        else:
            key, _, value = line.partition(":")
            value = value[1:] if value.startswith(" ") else value
            if key == "event":
                name = value
            elif key == "data":
                data.append(value)


def drive_to_done(client: TestClient, guard: Guard, run_id: str) -> tuple[list[str], dict]:
    """Read the run's stream, answering every question / props pause, until done.

    The stream replays from the first event on every reconnect, so the n-th pause seen on a
    reconnect is the first one not answered yet. Returns the event names and the done data.
    """
    started = time.monotonic()
    answered = 0
    for _ in range(len(PAUSES) + 3):
        pause: str | None = None
        names: list[str] = []
        with client.stream("GET", f"/families/{FID}/runs/{run_id}/stream") as res:
            assert res.status_code == 200, res.read()
            seen_pauses = 0
            for name, data in _frames(res, guard, started):
                assert name in schemas.RUN_EVENTS, name
                schemas.RUN_EVENTS[name].model_validate(data, by_alias=True, by_name=False)
                names.append(name)
                if name == "error":
                    pytest.fail(f"run failed: {data}")
                if name == "done":
                    return names, data
                if name in PAUSES:
                    seen_pauses += 1
                    if seen_pauses > answered:
                        pause = name
                        break
        assert pause is not None, f"stream ended without done: {names}"
        res = client.post(f"/families/{FID}/runs/{run_id}/answer", json=PAUSES[pause])
        assert res.status_code == 204, res.text
        answered += 1
    pytest.fail("run did not finish")


def answer_question(a: App, question: schemas.Question) -> str:
    """Submit Sam's telling for this question and drive the run to done. Returns the memory id."""
    client = a.client
    form: dict[str, Any] = {"text": "I cried when Lily threw her cap. Grandma Carmen cried louder.", "year": "2019"}
    if question.memory_id is not None:
        memory = schemas.MemoryResponse.model_validate(
            _ok(client.get(f"/families/{FID}/memories/{question.memory_id}")), by_alias=True, by_name=False
        )
        form.update(memoryId=question.memory_id, year=str(memory.memory.year))
    else:
        form["question"] = question.text
    submitted = schemas.SubmitMemoryResponse.model_validate(
        _ok(client.post(f"/families/{FID}/memories", data=form), 202), by_alias=True, by_name=False
    )
    names, done = drive_to_done(client, a.guard, submitted.run_id)
    assert names[-1] == "done"
    assert a.manager.status(FID, submitted.run_id) == "done"
    memory_id = schemas.DoneEvent.model_validate(done, by_alias=True, by_name=False).memory_id
    if question.memory_id is not None:
        assert memory_id == question.memory_id
    return memory_id


def _sam_person_id(me: schemas.MeResponse) -> str:
    family = next(f for f in me.families if f.id == FID)
    assert family.person_id is not None
    return family.person_id


# ------------------------------------------------------------------------------ tests


def test_seeded_question_closes_when_sam_tells_it(app: App):
    me = _login(app.client, SAM_EMAIL)
    sam = _sam_person_id(me)
    before = _open_questions(app.client)
    assert before, "the demo seed should give Sam an open question"
    question = before[0]
    assert question.person_id == sam and question.status == "open"

    memory_id = answer_question(app, question)

    after = _open_questions(app.client)
    assert question.id not in {q.id for q in after}
    assert {q.id for q in after} == {q.id for q in before} - {question.id}
    stored = db_client.get_db()["questions"].find_one({"_id": question.id})
    assert stored["status"] == "answered"

    # Sam's telling is on the memory.
    memory = schemas.MemoryResponse.model_validate(
        _ok(app.client.get(f"/families/{FID}/memories/{memory_id}")), by_alias=True, by_name=False
    )
    assert sam in [t.author_id for t in memory.memory.tellings]

    # Other people's questions are untouched: Ana (mom) still has hers.
    assert app.client.post("/auth/logout").status_code == 204
    _login(app.client, "ana@demo.stitch")
    seeded_for_mom = [q for q in demo.DEMO_QUESTIONS if q[0] == "mom"]
    assert len(_open_questions(app.client)) == len(seeded_for_mom)

    assert app.guard.checked >= 6 and app.guard.lines > 0


def test_text_only_question_closes_when_answered_with_a_new_memory(app: App):
    """The other branch: a question with no memory is closed by a new memory told in reply to it."""
    me = _login(app.client, SAM_EMAIL)
    sam = _sam_person_id(me)
    text = "What do you remember about your first day of school?"
    created = memories_db.create_question(db_client.get_db(), FID, person_id=sam, text=text)
    before = _open_questions(app.client)
    question = next(q for q in before if q.id == created.id)
    assert question.memory_id is None

    memory_id = answer_question(app, question)

    after = _open_questions(app.client)
    assert created.id not in {q.id for q in after}
    assert {q.id for q in after} == {q.id for q in before} - {created.id}
    memory = schemas.MemoryResponse.model_validate(
        _ok(app.client.get(f"/families/{FID}/memories/{memory_id}")), by_alias=True, by_name=False
    )
    assert memory.memory.tellings[0].author_id == sam
    assert memory.memory.tellings[0].question == text
