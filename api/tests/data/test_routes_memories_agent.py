"""routes/memories.py against the REAL agent.runs.RunManager (no fakes at the seam).

The run routes are driven over HTTP (straight through ASGI) while a real studio graph runs
on the RunManager's worker threads: `studio.build_studio` with ScriptedChatModel, the real
ask_teller and save_memory tools, an interviewer sub-agent, InMemorySaver and InMemoryStore,
over MongoRepo on mongomock seeded with the demo family. No keys, no network.

The SSE stream is read chunk by chunk as the app sends it (`SSEClient`), so a test can
answer a question while its stream is still open, and every read is bounded by a timeout.
"""

from __future__ import annotations

import asyncio
import io
import json
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore
from PIL import Image
from pymongo.database import Database

import auth
import main
import schemas
from agent import runs, studio
from agent.fake_llm import ScriptedChatModel, last_tool_message, system_prompt_of, tool_call
from agent.mongo_repo import MongoRepo
from agent.run_info import RunInfo
from agent.tools.ask import INTERRUPT_ON, ask_teller
from agent.tools.family import make_family_tools
from db import client as db_client
from db import families as families_db
from db import media as media_db
from db import memories as memories_db
from db import people as people_db
from db import users as users_db
from routes import memories as memories_routes
from seed.demo import DEMO_FAMILY_ID, HYMN_MEMORY_ID, SAMPLE_PATH, seed_demo

FID = DEMO_FAMILY_ID
DAVID = "demo_user_david"  # person "dad", admin
ANA = "demo_user_ana"  # person "mom", member
SAM = "demo_user_sam"  # person "sam", member
# Every seeded demo memory has a scene, so the `world` fixture adds this one without a
# screenplay: a moment someone started that the director has not staged yet.
NO_SCENE_MEMORY = "test_memory_without_a_scene"

READ_TIMEOUT = 10.0  # one stream read, one HTTP call, or one wait on the run
SCENARIO_TIMEOUT = 25.0  # a whole async scenario

STUDIO_PROMPT = "You are the STUDIO."
INTERVIEWER_PROMPT = "You are the INTERVIEWER."
QUESTION = "Which church was it?"
ANSWER_TEXT = "St. Mark's, in Trenton."

SAMPLE_RAW = json.loads(SAMPLE_PATH.read_text())
SCREENPLAY_JSON = json.dumps(SAMPLE_RAW["memory"]["screenplay"])
TIMELINE_JSON = json.dumps(SAMPLE_RAW["memory"]["timeline"])

FULL_RUN = ["plan", "subagent_start", "question", "subagent_end", "plan", "scene_ready", "done"]


def _png() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (4, 4), "#c9b48a").save(out, format="PNG")
    return out.getvalue()


PNG = _png()


# ------------------------------------------------------------------------------ the scripted studio


def _tool_results(messages: list[BaseMessage]) -> int:
    return sum(isinstance(m, ToolMessage) for m in messages)


def _script(*steps: AIMessage | Callable[[list[BaseMessage]], AIMessage]) -> Callable[[list[BaseMessage]], AIMessage]:
    """An agent that plays `steps` in order, one per tool result seen so far."""

    def play(messages: list[BaseMessage]) -> AIMessage:
        step = steps[min(_tool_results(messages), len(steps) - 1)]
        return step(messages) if callable(step) else step

    return play


def _todos(*items: tuple[str, str]) -> AIMessage:
    return tool_call("write_todos", {"todos": [{"content": c, "status": s} for c, s in items]})


def _report(messages: list[BaseMessage]) -> AIMessage:
    last = last_tool_message(messages)
    return AIMessage(content=f"the teller said: {last.text if last else '-'}")


# Plan, ask the interviewer (who asks the teller one question), then write the hymn-book
# sample screenplay and timeline and save them with the real save_memory.
STUDIO_SCRIPT = _script(
    _todos(("Ask about the setting", "in_progress"), ("Stage and save", "pending")),
    tool_call("task", {"subagent_type": "interviewer", "description": "Can the hymn-book story be staged?"}),
    _todos(("Ask about the setting", "completed"), ("Stage and save", "in_progress")),
    tool_call("write_file", {"file_path": "/screenplay.json", "content": SCREENPLAY_JSON}),
    tool_call("write_file", {"file_path": "/timeline.json", "content": TIMELINE_JSON}),
    tool_call("save_memory", {"title": "Hymn books down the pew", "participant_ids": ["dad", "mom"]}),
    AIMessage(content="Saved the hymn-book scene."),
)
INTERVIEWER_SCRIPT = _script(tool_call("ask_teller", {"question": QUESTION}), _report)

INTERVIEWER = {
    "name": "interviewer",
    "description": "Decides whether the story can be staged; asks the teller when it cannot.",
    "system_prompt": INTERVIEWER_PROMPT,
    "tools": [ask_teller],
}


class Studio:
    """The graph factory handed to the RunManager (the pattern of tests/agent/test_agent_runs.py)."""

    def __init__(self, repo: MongoRepo) -> None:
        self.repo = repo
        self.saver = InMemorySaver()
        self.store = InMemoryStore()
        self.gate = threading.Event()  # the studio's first model call waits for it
        self.gate.set()
        self.model = ScriptedChatModel(responder=self.respond)
        self.built: list[RunInfo] = []

    def respond(self, messages: list[BaseMessage], tool_names: list[str]) -> AIMessage:
        assert self.gate.wait(READ_TIMEOUT), "the studio gate never opened"
        prompt = system_prompt_of(messages)
        if prompt.startswith(STUDIO_PROMPT):
            return STUDIO_SCRIPT(messages)
        if prompt.startswith(INTERVIEWER_PROMPT):
            return INTERVIEWER_SCRIPT(messages)
        raise AssertionError(f"unexpected agent: {prompt[:60]!r}")

    def build(self, run: RunInfo) -> Any:
        self.built.append(run)
        save_memory = next(t for t in make_family_tools(self.repo, run) if t.name == "save_memory")
        return studio.build_studio(
            family_id=run.family_id,
            model=self.model,
            tools=[ask_teller, save_memory],
            subagents=[INTERVIEWER],
            interrupt_on=INTERRUPT_ON,
            checkpointer=self.saver,
            store=self.store,
            system_prompt=STUDIO_PROMPT,
        )

    def run_context(self) -> dict[str, Any]:
        """The run context JSON the studio got in its first message."""
        first_studio_call = next(
            msgs for _, msgs in self.model.calls if system_prompt_of(msgs).startswith(STUDIO_PROMPT)
        )
        return json.loads(first_studio_call[1].content)


# ------------------------------------------------------------------------------ SSE over raw ASGI


class SSEClient:
    """One GET on the run stream, driven straight through ASGI.

    httpx's ASGITransport and Starlette's TestClient both wait for the whole response, which
    never comes while a run waits for an answer. This hands the app its own receive/send,
    so every chunk is seen as the app sends it, and every read has a timeout.
    """

    def __init__(self, app: FastAPI, path: str, user_id: str) -> None:
        self.app = app
        self.path = path
        self.cookie = f"{auth.COOKIE_NAME}={auth.create_session_token(user_id)}".encode()
        self.status: int | None = None
        self.headers: dict[str, str] = {}
        self.text = ""  # everything received so far
        self._buffer = ""
        self._started = asyncio.Event()
        self._chunks: asyncio.Queue[bytes | None] = asyncio.Queue()
        self._disconnect = asyncio.Event()
        self._task: asyncio.Task | None = None
        self.ended = False

    async def open(self, timeout: float = READ_TIMEOUT) -> int:
        scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": self.path,
            "raw_path": self.path.encode(),
            "query_string": b"",
            "root_path": "",
            "headers": [(b"host", b"test"), (b"accept", b"text/event-stream"), (b"cookie", self.cookie)],
            "client": ("127.0.0.1", 50000),
            "server": ("test", 80),
        }
        requested = False

        async def receive() -> dict[str, Any]:
            nonlocal requested
            if not requested:
                requested = True
                return {"type": "http.request", "body": b"", "more_body": False}
            await self._disconnect.wait()
            return {"type": "http.disconnect"}

        async def send(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                self.status = message["status"]
                self.headers = {k.decode().lower(): v.decode() for k, v in message.get("headers", [])}
                self._started.set()
            elif message["type"] == "http.response.body":
                body = message.get("body", b"")
                if body:
                    self._chunks.put_nowait(body)
                if not message.get("more_body", False):
                    self._chunks.put_nowait(None)

        async def run_app() -> None:
            try:
                await self.app(scope, receive, send)
            finally:
                self._chunks.put_nowait(None)

        self._task = asyncio.create_task(run_app())
        started = asyncio.create_task(self._started.wait())
        done, _ = await asyncio.wait({started, self._task}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
        started.cancel()
        if self._task in done and self._task.exception() is not None:
            raise self._task.exception()
        assert self.status is not None, f"no response headers within {timeout}s"
        return self.status

    async def _block(self, timeout: float) -> str | None:
        """The next SSE block (text between blank lines), or None when the body has ended."""
        while "\n\n" not in self._buffer:
            if self.ended:
                return None
            chunk = await asyncio.wait_for(self._chunks.get(), timeout)
            if chunk is None:
                self.ended = True
                assert self._buffer == "", f"unterminated SSE block: {self._buffer!r}"
                return None
            text = chunk.decode()
            self.text += text
            self._buffer += text
        block, self._buffer = self._buffer.split("\n\n", 1)
        return block

    async def read_all(self, timeout: float = READ_TIMEOUT) -> str:
        """The rest of the body as raw text (for a JSON error response)."""
        while not self.ended:
            chunk = await asyncio.wait_for(self._chunks.get(), timeout)
            if chunk is None:
                self.ended = True
            else:
                self.text += chunk.decode()
        return self.text

    async def next_block(self, timeout: float = READ_TIMEOUT) -> str | None:
        return await self._block(timeout)

    async def next_event(self, timeout: float = READ_TIMEOUT) -> tuple[str, dict[str, Any]] | None:
        """The next event (heartbeat comments are skipped), or None at the end of the stream."""
        while True:
            block = await self._block(timeout)
            if block is None:
                return None
            if all(line.startswith(":") for line in block.split("\n")):
                continue
            return parse_event(block)

    async def events_through(self, name: str, timeout: float = READ_TIMEOUT) -> list[tuple[str, dict[str, Any]]]:
        """Events up to and including the first `name`."""
        seen: list[tuple[str, dict[str, Any]]] = []
        while True:
            event = await self.next_event(timeout)
            assert event is not None, f"stream ended before {name!r}: {[n for n, _ in seen]}"
            seen.append(event)
            if event[0] == name:
                return seen

    async def rest(self, timeout: float = READ_TIMEOUT) -> list[tuple[str, dict[str, Any]]]:
        """Every remaining event, until the app ends the body."""
        seen: list[tuple[str, dict[str, Any]]] = []
        while (event := await self.next_event(timeout)) is not None:
            seen.append(event)
        return seen

    async def close(self) -> None:
        self._disconnect.set()
        if self._task is not None and not self._task.done():
            try:
                await asyncio.wait_for(self._task, READ_TIMEOUT)
            except (TimeoutError, asyncio.CancelledError):
                self._task.cancel()


def parse_event(block: str) -> tuple[str, dict[str, Any]]:
    """`event: <name>` + `data: <json>` -> (name, data), checked against schemas.RUN_EVENTS by alias."""
    fields = dict(line.split(": ", 1) for line in block.split("\n"))
    assert set(fields) == {"event", "data"}, block
    name, data = fields["event"], json.loads(fields["data"])
    assert name in schemas.RUN_EVENTS, name
    model = schemas.RUN_EVENTS[name].model_validate(data)
    # camelCase on the wire: the frame is exactly the model dumped by alias.
    assert model.model_dump(mode="json", by_alias=True) == data, (name, data)
    return name, data


# ------------------------------------------------------------------------------ world


@dataclass
class World:
    db: Database
    app: FastAPI
    studio: Studio
    manager: runs.RunManager
    fid_b: str
    bob: str
    clients: dict[str, httpx.AsyncClient] = field(default_factory=dict)

    def client(self, user_id: str) -> httpx.AsyncClient:
        if user_id not in self.clients:
            self.clients[user_id] = httpx.AsyncClient(
                transport=httpx.ASGITransport(app=self.app),
                base_url="http://test",
                cookies={auth.COOKIE_NAME: auth.create_session_token(user_id)},
                timeout=READ_TIMEOUT,
            )
        return self.clients[user_id]

    async def aclose(self) -> None:
        for c in self.clients.values():
            await c.aclose()
        self.clients.clear()

    def stream(self, user_id: str, run_id: str, fid: str = FID) -> SSEClient:
        return SSEClient(self.app, f"/families/{fid}/runs/{run_id}/stream", user_id)

    async def submit(self, user_id: str = DAVID, fid: str = FID, photos: int = 1, **form: Any) -> httpx.Response:
        data = {"text": "Dad passed the hymn books down the pew.", "year": "1962", **form}
        files = [("photos", (f"pew_{i}.png", PNG, "image/png")) for i in range(photos)]
        return await asyncio.wait_for(
            self.client(user_id).post(f"/families/{fid}/memories", data=data, files=files), READ_TIMEOUT
        )

    async def answer(self, user_id: str, run_id: str, body: dict[str, Any] | None = None, fid: str = FID):
        body = body if body is not None else {"kind": "answer", "text": ANSWER_TEXT}
        return await asyncio.wait_for(
            self.client(user_id).post(f"/families/{fid}/runs/{run_id}/answer", json=body), READ_TIMEOUT
        )

    async def wait(self, run_id: str) -> schemas.RunStatus:
        status = await asyncio.to_thread(self.manager.wait, FID, run_id, READ_TIMEOUT)
        assert status != "running", f"run {run_id} still running after {READ_TIMEOUT}s"
        return status

    def media_ids(self, fid: str = FID) -> set[str]:
        """Every stored media id of the family (mongomock keeps media inline; GridFS for completeness)."""
        query = {"metadata.familyId": fid}
        inline = {str(d["_id"]) for d in self.db[media_db.INLINE].find(query, {"_id": 1})}
        files = {str(d["_id"]) for d in self.db[media_db.FILES].find(query, {"_id": 1})}
        return inline | files


def scenario(world: World, body: Callable[[World], Any]) -> Any:
    """Run one async scenario with an overall timeout, closing the HTTP clients after."""

    async def go() -> Any:
        try:
            return await asyncio.wait_for(body(world), SCENARIO_TIMEOUT)
        finally:
            await world.aclose()

    return asyncio.run(go())


@pytest.fixture
def world(mdb) -> Iterator[World]:
    seed_demo(mdb)
    unstaged = memories_db.create_memory(
        mdb, DEMO_FAMILY_ID, title="The day the lights went out", year=2003, participant_ids=["mom", "sam"],
        telling=schemas.Telling(author_id="mom", text="The whole street lost power, so we ate ice cream by candlelight."),
        memory_id=NO_SCENE_MEMORY,
    )
    assert unstaged.screenplay is None and unstaged.timeline is None
    # Family B, with one member who has a person.
    bob = users_db.create_user(mdb, email="bob@b.example", password_hash="x", name="Bob")
    fam_b = families_db.create_family(mdb, name="Baker", creator_user_id=bob.id)
    people_db.create_person(mdb, fam_b.id, schemas.NewPerson(name="Bob"), added_by=bob.id, user_id=bob.id)

    db_client.set_db(mdb)
    repo = MongoRepo(mdb)
    studio_ = Studio(repo)
    manager = runs.RunManager(repo=repo, build_graph=studio_.build)
    runs.configure(manager)
    try:
        yield World(db=mdb, app=main.create_app(), studio=studio_, manager=manager, fid_b=fam_b.id, bob=bob.id)
    finally:
        studio_.gate.set()
        manager.shutdown(wait=True)
        runs.configure(None)
        db_client.set_db(None)


def no_secrets(*texts: str) -> None:
    for text in texts:
        assert "passwordHash" not in text and "password_hash" not in text


# ------------------------------------------------------------------------------ (a) + (b) the full run


def test_submit_stream_question_answer_done_and_the_memory_is_readable(world: World) -> None:
    async def body(w: World) -> None:
        photos_before = w.media_ids()
        res = await w.submit(question="What do you remember about church?", title="")
        assert res.status_code == 202, res.text
        submitted = schemas.SubmitMemoryResponse.model_validate(res.json())
        assert submitted.memory_id is None
        run_id = submitted.run_id
        (photo_id,) = w.media_ids() - photos_before  # one photo stored for the run

        live = w.stream(DAVID, run_id)
        assert await live.open() == 200
        assert live.headers["content-type"].startswith("text/event-stream")
        assert live.headers["cache-control"] == "no-cache"
        first = await live.events_through("question")
        assert [n for n, _ in first] == ["plan", "subagent_start", "question"]
        assert first[0][1] == {"todos": [
            {"text": "Ask about the setting", "status": "in_progress"},
            {"text": "Stage and save", "status": "pending"},
        ]}
        assert first[1][1] == {"name": "interviewer", "task": "Can the hymn-book story be staged?"}
        assert first[2][1] == {"text": QUESTION}
        assert await w.wait(run_id) == "waiting"

        # A late subscriber, while the run waits, gets the replay from the first event.
        late = w.stream(DAVID, run_id)
        assert await late.open() == 200
        assert await late.events_through("question") == first

        # The answer resumes the run; both open streams follow it to exactly one done.
        res = await w.answer(DAVID, run_id)
        assert res.status_code == 204 and res.content == b""
        live_rest = await live.rest()
        late_rest = await late.rest()
        assert live.ended and late.ended
        events = first + live_rest
        assert [n for n, _ in events] == FULL_RUN
        assert late_rest == live_rest
        assert [n for n, _ in events].count("done") == 1
        subagent_end = events[3][1]
        assert subagent_end["name"] == "interviewer" and ANSWER_TEXT in subagent_end["summary"]
        scene_ready, done = events[5][1], events[6][1]
        memory_id = done["memoryId"]
        assert scene_ready["memoryId"] == memory_id and scene_ready["version"] >= 1
        await live.close()
        await late.close()

        # A subscriber after the end replays the whole run, frame for frame, then the body ends.
        after = w.stream(DAVID, run_id)
        assert await after.open() == 200
        assert await after.rest() == events
        await after.close()

        # The saved memory is readable through the tree route, with its screenplay.
        res = await w.client(ANA).get(f"/families/{FID}/memories/{memory_id}")
        assert res.status_code == 200, res.text
        read = schemas.MemoryResponse.model_validate(res.json())
        assert read.memory.id == memory_id and read.memory.family_id == FID
        assert read.memory.screenplay is not None and read.memory.timeline is not None
        assert read.memory.screenplay.memory_id == memory_id
        assert len(read.memory.screenplay.shots) == len(SAMPLE_RAW["memory"]["screenplay"]["shots"])
        assert sorted(read.memory.participant_ids) == ["dad", "mom"]
        assert read.memory.id != HYMN_MEMORY_ID

        # The real seam passed the stored photo: in the run context and on the saved telling.
        telling = read.memory.tellings[0]
        assert telling.author_id == "dad"
        assert telling.photo_ids == [photo_id]
        assert telling.question == "What do you remember about church?"
        context = w.studio.run_context()
        assert context["photoIds"] == [photo_id]
        assert context["authorId"] == "dad" and context["title"] is None
        assert w.studio.built[0].photo_ids == (photo_id,) and w.studio.built[0].user_id == DAVID
        assert w.media_ids() == photos_before | {photo_id}  # the photo is kept on success

        # Done: one more answer is refused as not waiting.
        res = await w.answer(DAVID, run_id)
        assert res.status_code == 409
        assert res.json() == {"detail": memories_routes.NOT_WAITING}
        no_secrets(live.text, after.text, res.text)

    scenario(world, body)


def test_a_stream_opened_before_the_first_event_sends_headers_and_heartbeats(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The web opens the stream right after the submit, before the studio has said anything;
    # the real stream_run yields nothing until the first event. Headers and heartbeats
    # must still go out at once, and a not-yet-waiting run refuses answers.
    monkeypatch.setattr(memories_routes, "HEARTBEAT_SECONDS", 0.05)
    world.studio.gate.clear()

    async def body(w: World) -> None:
        res = await w.submit()
        assert res.status_code == 202, res.text
        run_id = res.json()["runId"]

        early = w.stream(DAVID, run_id)
        assert await early.open(timeout=2.0) == 200
        assert await early.next_block(timeout=2.0) == ": ping"
        assert w.manager.status(FID, run_id) == "running"

        res = await w.answer(DAVID, run_id)
        assert res.status_code == 409
        assert res.json() == {"detail": memories_routes.NOT_WAITING}

        # A client that leaves before the first event is unsubscribed from the run.
        quitter = w.stream(DAVID, run_id)
        assert await quitter.open(timeout=2.0) == 200
        assert len(w.manager._runs[run_id].subscribers) == 2
        await quitter.close()
        assert len(w.manager._runs[run_id].subscribers) == 1

        w.studio.gate.set()
        seen = await early.events_through("question")
        assert [n for n, _ in seen] == ["plan", "subagent_start", "question"]
        assert (await w.answer(DAVID, run_id)).status_code == 204
        rest = await early.rest()
        assert [n for n, _ in seen + rest] == FULL_RUN
        await early.close()

    scenario(world, body)


# ------------------------------------------------------------------------------ (c) answer and access errors


def test_answer_and_stream_errors_map_to_their_status_codes(world: World) -> None:
    async def body(w: World) -> None:
        res = await w.submit()
        assert res.status_code == 202, res.text
        run_id = res.json()["runId"]
        assert await w.wait(run_id) == "waiting"

        # Another member of the same family cannot answer David's run.
        res = await w.answer(ANA, run_id)
        assert res.status_code == 403
        assert res.json() == {"detail": memories_routes.NOT_RUN_OWNER}

        # The wrong kind of answer for a question: 409 (ValueError), and the run keeps waiting.
        res = await w.answer(DAVID, run_id, {"kind": "props", "props": []})
        assert res.status_code == 409
        assert "does not match" in res.json()["detail"]
        res = await w.answer(DAVID, run_id, {"kind": "merge", "approve": True})
        assert res.status_code == 409
        assert w.manager.status(FID, run_id) == "waiting"

        # Unknown run: 404 on stream and answer.
        unknown = w.stream(DAVID, "no-such-run")
        assert await unknown.open() == 404
        assert json.loads((await _body(unknown))) == {"detail": memories_routes.RUN_NOT_FOUND}
        res = await w.answer(DAVID, "no-such-run")
        assert res.status_code == 404
        assert res.json() == {"detail": memories_routes.RUN_NOT_FOUND}

        # Family B's member, with family B's fid and family A's run id: 404 (RunNotFound).
        foreign = w.stream(w.bob, run_id, fid=w.fid_b)
        assert await foreign.open() == 404
        assert json.loads(await _body(foreign)) == {"detail": memories_routes.RUN_NOT_FOUND}
        res = await w.answer(w.bob, run_id, fid=w.fid_b)
        assert res.status_code == 404
        assert res.json() == {"detail": memories_routes.RUN_NOT_FOUND}

        # A member of family A asking with family B's fid: 404 before the seam, on every run route.
        before = w.media_ids(w.fid_b)
        res = await w.submit(fid=w.fid_b)
        assert res.status_code == 404
        assert w.media_ids(w.fid_b) == before
        cross = w.stream(DAVID, run_id, fid=w.fid_b)
        assert await cross.open() == 404
        await _body(cross)
        res = await w.answer(DAVID, run_id, fid=w.fid_b)
        assert res.status_code == 404

        # None of that touched the run: it still takes David's answer and finishes.
        assert w.manager.status(FID, run_id) == "waiting"
        assert [n for n, _ in w.manager.events(FID, run_id)] == ["plan", "subagent_start", "question"]
        res = await w.answer(DAVID, run_id)
        assert res.status_code == 204
        assert await w.wait(run_id) == "done"
        res = await w.answer(DAVID, run_id)
        assert res.status_code == 409
        assert res.json() == {"detail": memories_routes.NOT_WAITING}
        assert len(w.studio.built) == 1  # only the one run was ever built

    scenario(world, body)


async def _body(stream: SSEClient) -> str:
    """The whole (non-streaming) body of a response opened with SSEClient."""
    text = await stream.read_all()
    await stream.close()
    return text


# ------------------------------------------------------------------------------ (d) submit errors


def test_submit_errors_from_the_real_seam_delete_the_photos(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    async def body(w: World) -> None:
        before = w.media_ids()

        # A participant who is not in this family: refused before any photo is stored
        # (400 is the route's documented code; the real start_run never sees participants).
        res = await w.submit(photos=2, participantIds=["mom", "not_a_person"])
        assert res.status_code == 400
        assert w.media_ids() == before
        bob_person = people_db.get_person_for_user(w.db, w.fid_b, w.bob)
        assert bob_person is not None
        res = await w.submit(photos=2, participantIds=[bob_person.id])
        assert res.status_code == 400
        assert w.media_ids() == before

        # A memory with no scene yet: the real start_run raises MemoryNotFound after the
        # photos were stored; 409 (the route found the memory, so it is the scene that is
        # missing), and the photos are gone again.
        res = await w.submit(photos=2, memoryId=NO_SCENE_MEMORY)
        assert res.status_code == 409
        assert res.json() == {"detail": memories_routes.NO_SCENE}
        assert w.media_ids() == before

        # A memory of another family: 404 too (the route's own family check).
        res = await w.submit(photos=2, memoryId=_family_b_memory(w))
        assert res.status_code == 404
        assert res.json() == {"detail": "Memory not found"}
        assert w.media_ids() == before

        # The teller's person is removed from the family between the route's check and the
        # start: the real start_run raises ValueError after the photos were stored; 422,
        # and the photos are deleted.
        real_lookup = people_db.get_person_for_user

        def lookup_then_remove(db: Database, family_id: str, user_id: str) -> schemas.Person | None:
            person = real_lookup(db, family_id, user_id)
            if person is not None:
                db["people"].delete_one({"_id": person.id, "familyId": family_id})
            return person

        monkeypatch.setattr(people_db, "get_person_for_user", lookup_then_remove)
        res = await w.submit(SAM, photos=2)
        assert res.status_code == 422
        # A fixed sentence: the agent's own message (ids, internals) goes to the log only.
        assert res.json() == {"detail": memories_routes.SUBMIT_REFUSED}
        assert w.media_ids() == before

        assert w.studio.built == []  # no run was ever started
        no_secrets(res.text)

    scenario(world, body)


def _family_b_memory(w: World) -> str:
    """A memory of family B (the route refuses it before the seam is reached)."""
    bob_person = people_db.get_person_for_user(w.db, w.fid_b, w.bob)
    assert bob_person is not None
    memory = memories_db.create_memory(w.db, w.fid_b, title="Baker picnic", year=1990, participant_ids=[bob_person.id])
    return memory.id


# ------------------------------------------------------------------------------ (e) not configured


def test_unconfigured_runs_are_503_and_photos_are_cleaned_up(world: World) -> None:
    async def body(w: World) -> None:
        # A run that existed before the manager was removed.
        res = await w.submit()
        assert res.status_code == 202, res.text
        run_id = res.json()["runId"]
        assert await w.wait(run_id) == "waiting"

        runs.configure(None)
        before = w.media_ids()
        res = await w.submit(photos=2)
        assert res.status_code == 503
        assert res.json() == {"detail": memories_routes.START_FAILED}
        assert w.media_ids() == before

        stream = w.stream(DAVID, run_id)
        assert await stream.open() == 503
        assert json.loads(await _body(stream)) == {"detail": memories_routes.STUDIO_UNAVAILABLE}

        res = await w.answer(DAVID, run_id)
        assert res.status_code == 503
        assert res.json() == {"detail": memories_routes.STUDIO_UNAVAILABLE}

        # Reconfigured, the run is still there, waiting, and finishes.
        runs.configure(w.manager)
        assert (await w.answer(DAVID, run_id)).status_code == 204
        assert await w.wait(run_id) == "done"

    scenario(world, body)
