"""Agent runs: the seam the run routes call (ARCHITECTURE.md sections 2 and 6).

    POST /families/{fid}/memories          -> start_run(...)   -> run id
    GET  /families/{fid}/runs/{id}/stream  -> stream_run(...)  -> (event name, payload model), ...
    POST /families/{fid}/runs/{id}/answer  -> answer_run(...)  -> resumes the same thread

One run is one studio thread (`studio.thread_config(run_id)`). `RunManager` drives the
studio graph on a worker thread with sync `graph.stream(stream_mode=["updates", "custom"],
subgraphs=True)` and translates what streams out into run events: every event is a
pair `(name, model)` with `name` in `schemas.RUN_EVENTS` and `model` an instance of
`schemas.RUN_EVENTS[name]`. The events of a run are kept in one append-only log, so
a subscriber that arrives late gets everything from the first event, then live events.

Where events come from:

- `custom` chunks `{"event": name, "data": {...}}` written by tools (compile_error,
  frames, scene_ready, plan or subagent events if a tool ever sends them). The
  needs-input and terminal events (question, props_found, merge, done, error) belong
  to this module and are ignored when a tool sends them, so a run always ends with
  exactly one terminal event.
- root `updates` that set `todos` (write_todos) -> plan.
- root `task` tool calls -> subagent_start; their ToolMessage -> subagent_end.
- `__interrupt__` (in any namespace; with subgraphs=True one sub-agent interrupt
  surfaces in the sub-agent namespace and again at the root, so they are deduped by
  `Interrupt.id`) -> `agent.tools.ask.interrupt_to_event` -> question / props_found / merge.
- end of stream: done(memory_id) if a scene was saved, otherwise error.

A run is `waiting` from the moment its question / props review / merge streams out,
even while a parallel branch of the same step is still working (the props sub-agent
pauses on confirm_props while the sets sub-agent is still painting). An answer that
arrives then is accepted at once and queued on the run; the worker resumes with it as
soon as the current stream closes (langgraph can only resume a thread once its step
has finished). `answer_run` never blocks on the run.

Interrupts are shown one at a time. `AnswerRequest` carries no interrupt id, so when
one step pauses on several gated calls at once (two sub-agents in parallel), the first
one is shown and answered; it is resumed with `Command(resume={id: payload})` (langgraph
refuses a bare resume when several are pending), and the others surface again after
the resume, each as its own event.

Runs live in a process-local dict: a restart forgets the run ids (the graph state
itself persists in whatever checkpointer `build_graph` used). That is fine for one API
process; several processes would need a shared run registry. A finished run (done or
error) drops its graph at once and is forgotten `RunManager.finished_run_ttl` seconds
after it ended (checked whenever a run starts), so late subscribers have that long to
replay it and the dict does not grow without bound.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
import uuid
from collections.abc import AsyncIterator, Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from deepagents.backends.utils import create_file_data
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langgraph.types import Command, Interrupt
from pydantic import BaseModel, ValidationError

import schemas
from agent import studio
from agent.repo import AgentRepo
from agent.run_info import RunInfo
from agent.tools.ask import answer_to_resume, interrupt_to_event
from agent.tools.family import SCREENPLAY_PATH, TIMELINE_PATH

if TYPE_CHECKING:
    from langgraph.graph.state import CompiledStateGraph

logger = logging.getLogger(__name__)

GraphFactory = Callable[[RunInfo], "CompiledStateGraph"]
"""Builds the studio for one run, with its tools bound to that run (family, teller, form)."""

TASK_TOOL = "task"
TEXT_LIMIT = 500
"""Longest sub-agent task description or summary sent to the web."""
ERROR_TEXT_LIMIT = 300
"""Longest piece of the studio's last message quoted in an error event."""
FINISHED_RUN_TTL_SECONDS = 3600.0
"""How long a finished run stays replayable before the manager forgets it."""

NEEDS_INPUT_EVENTS = frozenset({"question", "props_found", "merge"})
TERMINAL_EVENTS = frozenset({"done", "error"})
_RESERVED_EVENTS = NEEDS_INPUT_EVENTS | TERMINAL_EVENTS
"""Events only this module emits; a tool's custom chunk with one of these names is ignored."""

Event = tuple[str, BaseModel]


class RunNotFound(LookupError):
    """Unknown run id, or the run belongs to another family (the route sends 404)."""


class MemoryNotFound(LookupError):
    """fields.memory_id is not a memory of this family, or it has no scene yet (the route sends 404)."""


class NotRunOwner(PermissionError):
    """An answer from a user other than the one who started the run."""


class RunNotWaiting(RuntimeError, ValueError):
    """An answer while the run is not paused on a question, prop review or merge (the route sends 409).

    Also a ValueError because routes/memories.py maps ValueError from answer_run to 409.
    """


# --------------------------------------------------------------------------- helpers


def _clip(text: str, limit: int) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _text_of(message: BaseMessage) -> str:
    text = message.text
    return text if isinstance(text, str) else str(message.content)


def _messages_in(update: dict[str, Any]) -> list[Any]:
    messages = update.get("messages")
    messages = getattr(messages, "value", messages)  # langgraph Overwrite(...)
    return list(messages) if isinstance(messages, list | tuple) else []


def run_context(info: RunInfo, author_name: str) -> dict[str, Any]:
    """The JSON object the studio reads from its first message (prompts/studio.md, 'Run context')."""
    fields = info.fields
    return {
        "authorId": info.author_id,
        "authorName": author_name,
        "text": fields.text,
        "year": fields.year,
        "title": fields.title,
        "question": fields.question,
        "photoIds": list(info.photo_ids),
        "participantIds": list(fields.participant_ids),
        "memoryId": fields.memory_id,
    }


def _scene_json(model: BaseModel) -> str:
    return json.dumps(model.model_dump(mode="json", by_alias=True), indent=2, ensure_ascii=False)


def _resume_command(pending: Interrupt, payload: Any, pending_count: int) -> Command:
    """langgraph takes a bare resume value for one pending interrupt, and needs it keyed
    by interrupt id when several are pending (it refuses a bare value then)."""
    return Command(resume={pending.id: payload}) if pending_count > 1 else Command(resume=payload)


# --------------------------------------------------------------------------- one run


@dataclass(eq=False)
class _Run:
    info: RunInfo
    config: dict[str, Any]
    cond: threading.Condition = field(default_factory=threading.Condition)
    graph: Any = None
    events: list[Event] = field(default_factory=list)
    status: schemas.RunStatus = "running"
    # The interrupt the family is looking at, and how many were pending when the stream closed.
    pending: Interrupt | None = None
    pending_count: int = 0
    # An answer accepted while the worker was still streaming: (its interrupt, resume payload).
    queued: tuple[Interrupt, Any] | None = None
    # True while a worker owns the run (between submitting _drive and its last stream closing).
    driving: bool = False
    seen_interrupts: set[str] = field(default_factory=set)
    memory_id: str | None = None
    last_plan: schemas.PlanEvent | None = None
    tasks: dict[str, str] = field(default_factory=dict)  # task tool_call_id -> sub-agent name
    ended_tasks: set[str] = field(default_factory=set)
    subscribers: list[tuple[asyncio.AbstractEventLoop, asyncio.Event]] = field(default_factory=list)
    finished_at: float | None = None  # time.monotonic() when done or error was appended

    @property
    def terminal(self) -> bool:
        return self.status in ("done", "error")

    # Every mutation below happens with `cond` held.

    def append(self, name: str, payload: BaseModel) -> None:
        if self.terminal:
            logger.warning("run %s: dropped %s after the run ended", self.info.run_id, name)
            return
        expected = schemas.RUN_EVENTS[name]
        if not isinstance(payload, expected):
            raise TypeError(f"{name} needs a {expected.__name__}, got {type(payload).__name__}")
        self.events.append((name, payload))
        if name in TERMINAL_EVENTS:
            self.status = "done" if name == "done" else "error"
            self.finished_at = time.monotonic()
            # Nothing resumes a finished run: let its graph, pending answer and maps go.
            self.graph = None
            self.pending = None
            self.queued = None
            self.tasks.clear()
            self.ended_tasks.clear()
            self.seen_interrupts.clear()
        self.notify()

    def notify(self) -> None:
        self.cond.notify_all()
        for loop, event in list(self.subscribers):
            try:
                loop.call_soon_threadsafe(event.set)
            except RuntimeError:  # that subscriber's loop is closed
                self.subscribers.remove((loop, event))


# --------------------------------------------------------------------------- manager


class RunManager:
    """Starts, streams and resumes studio runs. Thread-safe; one instance per API process."""

    finished_run_ttl: float = FINISHED_RUN_TTL_SECONDS
    """Seconds a finished run stays replayable; set it on an instance to change it."""

    def __init__(self, *, repo: AgentRepo, build_graph: GraphFactory, max_workers: int = 4) -> None:
        self._repo = repo
        self._build_graph = build_graph
        self._runs: dict[str, _Run] = {}
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="studio-run")

    # ------------------------------------------------------------------ public API

    def start_run(
        self,
        family_id: str,
        user_id: str,
        person_id: str,
        fields: schemas.SubmitMemoryFields,
        photo_ids: list[str],
    ) -> str:
        """Start a studio run for this telling and return its run id at once.

        Raises ValueError if `person_id` is not a person in the family, and
        MemoryNotFound if `fields.memory_id` is set but is not a memory of this family
        with a compiled scene. The graph is built and driven on a worker thread.
        """
        person = self._repo.get_person(family_id, person_id)
        if person is None:
            raise ValueError(f"person {person_id!r} is not in this family")

        files: dict[str, Any] = {}
        if fields.memory_id is not None:
            memory = self._repo.get_memory(family_id, fields.memory_id)
            if memory is None or memory.screenplay is None or memory.timeline is None:
                raise MemoryNotFound(f"memory {fields.memory_id!r} not found, or it has no scene yet")
            files = {
                SCREENPLAY_PATH: create_file_data(_scene_json(memory.screenplay)),
                TIMELINE_PATH: create_file_data(_scene_json(memory.timeline)),
            }

        run_id = uuid.uuid4().hex
        info = RunInfo(
            run_id=run_id,
            family_id=family_id,
            user_id=user_id,
            author_id=person_id,
            fields=fields,
            photo_ids=tuple(photo_ids),
        )
        context = json.dumps(run_context(info, person.name), indent=2, ensure_ascii=False)
        graph_input: dict[str, Any] = {"messages": [HumanMessage(content=context)]}
        if files:
            graph_input["files"] = files

        run = _Run(info=info, config=studio.thread_config(run_id), driving=True)
        with self._lock:
            self._prune_finished()
            self._runs[run_id] = run
        self._pool.submit(self._drive, run, graph_input)
        return run_id

    async def stream_run(self, family_id: str, run_id: str) -> AsyncIterator[Event]:
        """Every event of the run from the first one, then live events, until done or error.

        RunNotFound is raised on the first iteration, before anything is yielded, when
        the run is unknown or belongs to another family. While the run waits for an
        answer the iterator stays open. Any number of subscribers may follow one run;
        each gets the full sequence.
        """
        run = self._get(family_id, run_id)
        loop = asyncio.get_running_loop()
        wake = asyncio.Event()
        with run.cond:
            run.subscribers.append((loop, wake))
        index = 0
        try:
            while True:
                wake.clear()
                with run.cond:
                    batch = run.events[index:]
                    finished = run.terminal
                for event in batch:
                    yield event
                index += len(batch)
                if finished:
                    return  # nothing is appended after done or error
                if not batch:
                    await wake.wait()
        finally:
            with run.cond:
                if (loop, wake) in run.subscribers:
                    run.subscribers.remove((loop, wake))

    def answer_run(self, family_id: str, run_id: str, user_id: str, answer: schemas.AnswerRequest) -> None:
        """Resume a paused run with the family's answer, on the same thread. Returns at once.

        RunNotFound: unknown run or another family. NotRunOwner: not the user who
        started it. RunNotWaiting: not paused. ValueError: the answer does not fit the
        pending question (wrong kind, empty text); the run stays paused.

        The run is waiting as soon as its question is out, even if a parallel branch of
        the same step is still working. Then the answer is queued on the run and the
        worker resumes with it once that stream closes; otherwise a worker resumes now.
        Either way this call only takes the run's lock briefly and never waits for it.
        """
        run = self._get(family_id, run_id)
        if run.info.user_id != user_id:
            raise NotRunOwner("only the person who started this run can answer it")
        with run.cond:
            if run.status != "waiting" or run.pending is None:
                raise RunNotWaiting(f"run {run_id} is {run.status}, not waiting for an answer")
            pending = run.pending
            payload = answer_to_resume(pending.value, answer)  # ValueError propagates; still waiting
            run.status = "running"
            run.pending = None
            if run.driving:
                # The step that asked is still streaming (a sibling branch is working);
                # _settle resumes with this answer when the stream closes.
                run.queued = (pending, payload)
                run.notify()
                return
            resume = _resume_command(pending, payload, run.pending_count)
            run.pending_count = 0
            # Interrupts still pending after this resume surface again with the same id.
            run.seen_interrupts.clear()
            run.driving = True
            run.notify()
        self._pool.submit(self._drive, run, resume)

    def status(self, family_id: str, run_id: str) -> schemas.RunStatus:
        """running, waiting (for an answer), done or error."""
        run = self._get(family_id, run_id)
        with run.cond:
            return run.status

    def events(self, family_id: str, run_id: str) -> list[Event]:
        """A snapshot of the run's events so far (tests and debugging)."""
        run = self._get(family_id, run_id)
        with run.cond:
            return list(run.events)

    def wait(self, family_id: str, run_id: str, timeout: float | None = None) -> schemas.RunStatus:
        """Block until the run is no longer running (waiting, done or error), or `timeout` passes.

        For scripts and tests; never call it on the API's event loop.
        """
        run = self._get(family_id, run_id)
        with run.cond:
            run.cond.wait_for(lambda: run.status != "running", timeout)
            return run.status

    def shutdown(self, *, wait: bool = True) -> None:
        """Stop the worker pool (tests and process exit)."""
        self._pool.shutdown(wait=wait)

    # ------------------------------------------------------------------ internals

    def _get(self, family_id: str, run_id: str) -> _Run:
        with self._lock:
            run = self._runs.get(run_id)
        if run is None or run.info.family_id != family_id:
            raise RunNotFound(f"run {run_id!r} not found")
        return run

    def _prune_finished(self) -> None:
        """Forget runs that ended more than `finished_run_ttl` seconds ago. Call with `_lock` held."""
        cutoff = time.monotonic() - self.finished_run_ttl
        stale = [rid for rid, r in self._runs.items() if r.finished_at is not None and r.finished_at <= cutoff]
        for rid in stale:
            del self._runs[rid]

    def _drive(self, run: _Run, graph_input: Any) -> None:
        """Stream the graph (start or resume) and translate it, then resume with any
        answer queued meanwhile, until the run waits or ends. Never raises."""
        run_id = run.info.run_id
        try:
            with run.cond:
                graph = run.graph
            if graph is None:
                graph = self._build_graph(run.info)
                with run.cond:
                    run.graph = graph
            while graph_input is not None:
                for chunk in graph.stream(graph_input, run.config, stream_mode=["updates", "custom"], subgraphs=True):
                    if not self._translate(run, chunk):
                        with run.cond:
                            run.driving = False
                        return
                graph_input = self._settle(run, graph)
        except Exception as exc:  # noqa: BLE001 - every failure becomes the run's error event
            logger.exception("run %s failed", run_id)
            with run.cond:
                run.driving = False
                run.append("error", schemas.ErrorEvent(message=f"{type(exc).__name__}: {exc}"))

    def _translate(self, run: _Run, chunk: Any) -> bool:
        """Turn one stream chunk into events. Returns False once the run has ended."""
        namespace, mode, data = chunk
        with run.cond:
            if run.terminal:
                return False
            if mode == "custom":
                self._custom(run, data)
            elif mode == "updates" and isinstance(data, dict):
                for node, update in data.items():
                    if node == "__interrupt__":
                        for interrupt in update or ():
                            self._interrupt(run, interrupt)
                    elif not namespace and isinstance(update, dict):
                        self._root_update(run, update)
            return not run.terminal

    def _custom(self, run: _Run, data: Any) -> None:
        if not isinstance(data, dict) or not isinstance(data.get("event"), str):
            logger.info("run %s: ignored custom chunk %r", run.info.run_id, data)
            return
        name = data["event"]
        if name not in schemas.RUN_EVENTS or name in _RESERVED_EVENTS:
            logger.info("run %s: ignored custom event %r", run.info.run_id, name)
            return
        try:
            payload = schemas.RUN_EVENTS[name].model_validate(data.get("data") or {})
        except ValidationError as exc:
            logger.warning("run %s: ignored malformed %s event: %s", run.info.run_id, name, exc)
            return
        if isinstance(payload, schemas.SceneReadyEvent):
            run.memory_id = payload.memory_id
        if isinstance(payload, schemas.PlanEvent):
            if payload == run.last_plan:
                return
            run.last_plan = payload
        run.append(name, payload)

    def _root_update(self, run: _Run, update: dict[str, Any]) -> None:
        todos = update.get("todos")
        if isinstance(todos, list):
            plan = schemas.PlanEvent(
                todos=[
                    schemas.PlanItem(text=str(t.get("content", "")), status=t.get("status", "pending"))
                    for t in todos
                    if isinstance(t, dict)
                ]
            )
            if plan != run.last_plan:
                run.last_plan = plan
                run.append("plan", plan)
        for message in _messages_in(update):
            if isinstance(message, AIMessage):
                for call in message.tool_calls:
                    call_id = call.get("id") or ""
                    if call.get("name") != TASK_TOOL or call_id in run.tasks:
                        continue
                    args = call.get("args") or {}
                    name = str(args.get("subagent_type") or "subagent")
                    run.tasks[call_id] = name
                    description = args.get("description")
                    task = _clip(description, TEXT_LIMIT) if isinstance(description, str) else None
                    run.append("subagent_start", schemas.SubagentStartEvent(name=name, task=task))
            elif isinstance(message, ToolMessage) and message.name == TASK_TOOL:
                call_id = message.tool_call_id
                if call_id in run.ended_tasks:
                    continue
                run.ended_tasks.add(call_id)
                name = run.tasks.get(call_id, "subagent")
                summary = _clip(_text_of(message), TEXT_LIMIT)
                run.append("subagent_end", schemas.SubagentEndEvent(name=name, summary=summary or None))

    def _interrupt(self, run: _Run, interrupt: Any) -> None:
        interrupt_id = getattr(interrupt, "id", None)
        if not isinstance(interrupt_id, str) or interrupt_id in run.seen_interrupts:
            return
        if run.pending is not None or run.queued is not None:
            # One question at a time: this one surfaces again after the shown one is answered.
            return
        run.seen_interrupts.add(interrupt_id)
        try:
            name, payload = interrupt_to_event(interrupt)
        except ValueError as exc:
            run.append("error", schemas.ErrorEvent(message=f"the studio paused on something it cannot ask: {exc}"))
            return
        # Waiting from the moment the family can see it, even if a sibling branch is still working.
        run.pending = interrupt
        run.status = "waiting"
        run.append(name, payload)

    def _settle(self, run: _Run, graph: Any) -> Command | None:
        """After a stream closes: the Command to resume with if an answer was queued meanwhile;
        otherwise wait for an answer, or end with done / error, and release the run (None)."""
        state = graph.get_state(run.config)
        interrupts = list(state.interrupts)
        with run.cond:
            if run.terminal:
                run.driving = False
                return None
            ids = {i.id for i in interrupts}
            if run.queued is not None:
                pending, payload = run.queued
                run.queued = None
                if pending.id in ids:
                    run.pending_count = 0
                    # Interrupts still pending after this resume surface again with the same id.
                    run.seen_interrupts.clear()
                    return _resume_command(pending, payload, len(interrupts))
                logger.warning("run %s: dropped an answer to %s, which is no longer pending", run.info.run_id, pending.id)
            run.driving = False
            if interrupts:
                if run.pending is None or run.pending.id not in ids:
                    run.pending = None
                    run.status = "running"
                    run.seen_interrupts.clear()
                    self._interrupt(run, interrupts[0])
                    if run.terminal:
                        return None
                run.pending_count = len(interrupts)
                run.status = "waiting"
                run.notify()
                return None
            run.pending = None
            if run.memory_id is not None:
                memory_id = run.info.fields.memory_id or run.memory_id
                run.append("done", schemas.DoneEvent(memory_id=memory_id))
                return None
            last = next(
                (m for m in reversed(state.values.get("messages", [])) if isinstance(m, AIMessage)),
                None,
            )
            said = _clip(_text_of(last), ERROR_TEXT_LIMIT) if last is not None else ""
            run.append("error", schemas.ErrorEvent(message="the studio finished without saving a scene: " + said))
            return None


# --------------------------------------------------------------------------- module seam

_manager: RunManager | None = None


def configure(manager: RunManager | None) -> None:
    """Install the process's RunManager (None removes it). The API does this at startup."""
    global _manager  # noqa: PLW0603
    _manager = manager


def get_manager() -> RunManager:
    if _manager is None:
        raise RuntimeError("agent runs are not configured: call agent.runs.configure(RunManager(...)) at startup")
    return _manager


def start_run(
    family_id: str,
    user_id: str,
    person_id: str,
    fields: schemas.SubmitMemoryFields,
    photo_ids: list[str],
) -> str:
    return get_manager().start_run(family_id, user_id, person_id, fields, photo_ids)


def stream_run(family_id: str, run_id: str) -> AsyncIterator[Event]:
    return get_manager().stream_run(family_id, run_id)


def answer_run(family_id: str, run_id: str, user_id: str, answer: schemas.AnswerRequest) -> None:
    get_manager().answer_run(family_id, run_id, user_id, answer)


__all__ = [
    "GraphFactory",
    "MemoryNotFound",
    "NotRunOwner",
    "RunManager",
    "RunNotFound",
    "RunNotWaiting",
    "answer_run",
    "configure",
    "get_manager",
    "start_run",
    "stream_run",
]
