"""Agent runs: the seam the run routes call (ARCHITECTURE.md sections 2 and 6).

    POST /families/{fid}/memories          -> start_run(...)   -> run id (awaitable on a loop)
    GET  /families/{fid}/runs/{id}/stream  -> stream_run(...)  -> (event name, payload model), ...
    POST /families/{fid}/runs/{id}/answer  -> answer_run(...)  -> resumes the same thread

The event loop never waits on the database here: `start_run` called on a loop returns
an awaitable that does its repo reads and first record write on a worker thread,
`stream_run` only takes a run's lock for in-memory work, and `answer_run` is sync and
belongs in the threadpool.

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

A pause the web cannot show (the HITL middleware pauses on the model's raw tool args,
before the tool's schema is checked: an empty question, a prop chip with a size outside
the vocabulary) is not shown and does not end the run. It is rejected back to the model
with the reason (`ask.reject_to_resume`), through the same queued resume an answer uses,
so the model can fix its arguments and call the tool again; the run stays running and
no event is sent. After MAX_MALFORMED_PAUSES such rejections in one run, the next one
ends the run with a short, family-safe error. The count is kept in the run record.

The time limit (STITCH_RUN_TIMEOUT_S, `RunLimits.run_timeout_s`; `RunManager(...,
run_timeout_s=...)` overrides it). Only time a worker spends driving the run counts,
including a sibling branch still working after the step's question went out: time waiting
on the teller once the stream has closed (question, props review, merge) does not, nor
time queued for a free worker, nor time the API process was down. The run
keeps `elapsed_s` (time already used; in the record) plus the clock reading taken when its
current driving stretch began; the stretch is added into `elapsed_s` whenever the record
is marked changed (`persist`, so every status change, event and end) and when a worker
lets the run go. The limit is checked on every stream chunk (`_translate`), before a
queued answer or a malformed-pause rejection resumes the thread and before the run settles
into waiting for an answer (`_settle`), before an answer resumes it (`answer_run`, which
covers a waiting run restored with its time already used), and before a run cut short by
a restart is driven again (`_redrive`). Past it, the run ends with one
error event (RUN_TIMEOUT_ERROR) and one warning log line (run id, seconds used, limit).
The limit travels in the record, so a restarted process keeps it. Starting the story again
starts a new run with a fresh budget.

A blocking model or tool call cannot be interrupted mid-call, so the limit is checked when
that call returns: a run overshoots it by at most one model request with its retries
(STITCH_MODEL_TIMEOUT_S x (STITCH_MODEL_MAX_RETRIES + 1)) or one tool call (an image
generation, a compile, a narration), whichever was in flight. A stream chunk writes the
record once ELAPSED_RECORD_INTERVAL_S of running time is not in it yet, so when the process
dies at most that much (plus the call in flight) goes uncounted after the restart.

The model call cap (STITCH_MAX_MODEL_CALLS, `RunLimits.max_model_calls`). Every chat model
call of a run, the studio's and every sub-agent's, is counted in a `guards.RunUsage` registry
under the run id (`studio.build_studio` binds a `guards.ModelCallBudget` on the graph; the
manager's `usage` is the same registry, `wiring.Services.usage`, which the art service also
counts images in). The count does not restart with a new stream, so it holds across every
answer, queued answer and malformed-pause rejection. The run record keeps `model_calls` and
`images_used` next to `elapsed_s` (written whenever either moves, at the next stream chunk),
and a restored run seeds them back into the registry before it is driven again, so a restart
gives a run neither cap again. The call past the cap raises `guards.RunBudgetExceeded` before
it reaches the model; the worker ends the run through the same safe path as the time limit:
one error event (`run_budget_message`), status error, the record written, one warning log
line. Nothing here waits on the event loop.

Interrupts are shown one at a time. `AnswerRequest` carries no interrupt id, so when
one step pauses on several gated calls at once (two sub-agents in parallel), the first
one is shown and answered; it is resumed with `Command(resume={id: payload})` (langgraph
refuses a bare resume when several are pending), and the others surface again after
the resume, each as its own event.

Live runs are kept in a process-local dict. A finished run (done or error) drops its
graph at once and leaves the dict `RunManager.finished_run_ttl` seconds after it ended
(checked whenever a run starts), so the dict does not grow without bound.

Surviving a restart: given a langgraph `BaseStore` (`RunManager(..., store=...)`, the
same store the studio uses; MongoDBStore in production), every run is also written to
the store under namespace `('stitch_runs', family_id)`, key `run_id` (see `run_record`):
on start, after every event (before subscribers are told) and on every status change.
A run id missing from the dict is looked up there, only under its own family:

- done / error: the stored events are replayed and the stream ends.
- waiting: the studio is rebuilt with `build_graph(RunInfo)` on the same thread, the
  pending interrupt is read back from `graph.get_state`, and the starting user's answer
  resumes that thread as usual.
- running: the process died mid-step. The run is driven again from its last checkpoint
  (streaming input None on the same thread, or the answer it had accepted when that
  answer's interrupt is still pending); a log line, not an event, says so.

Writing the run record: the run's lock (`_Run.cond`) is never held across a store write.
A change is made under the lock and marks the run dirty; once the lock is released, the
thread that made it takes a snapshot (record, a rising version and the event count)
under the lock, releases it and writes. Writes of one run are serialized by its own
`write_lock` (never taken on the event loop), and a snapshot older than one already
written is skipped, so a stale record never overwrites a newer one. Subscribers are
only served `events[:published]`, and `published` moves past an event only after a
record holding it has been written (or the write failed and was logged), so a
subscriber still never sees an event a restart would lose.

The graph state itself lives in whatever checkpointer `build_graph` uses, so this only
survives a restart when that checkpointer persists too (MongoDBSaver in production).
Without a store, a restart forgets the run ids, as before. One API process per store:
a second process sharing it would drive again a run the first one is still driving.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import threading
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from deepagents.backends.utils import create_file_data
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langgraph.store.base import BaseStore
from langgraph.types import Command, Interrupt
from pydantic import BaseModel, ValidationError

import schemas
from agent import studio
from agent.guards import RunBudgetExceeded, RunLimits, RunUsage
from agent.repo import AgentRepo
from agent.run_info import RunInfo
from agent.tools.ask import answer_to_resume, interrupt_to_event, reject_to_resume
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
MAX_MALFORMED_PAUSES = 3
"""How many pauses the web cannot show are handed back to the model in one run; the next one ends it."""
MALFORMED_PAUSE_ERROR = "the studio could not put together a question for you; please try again"
"""The error the family sees when the studio keeps pausing on arguments the web cannot show."""
RUN_TIMEOUT_ERROR = "the studio took too long on this story (over {minutes} minutes); please try again"
"""The error the family sees when a run passes its time limit; `minutes` is the limit, rounded up."""
RUN_ERROR = "the studio ran into a problem ({kind}); please try again"
"""The error the family sees when building, driving or restoring a run raises; `kind` is the
exception's class name. The exception's own text (which can carry a provider's response body,
a URL or a connection string) goes to the log only (`run_error_message`)."""
RUN_BUDGET_ERROR = "this run reached its limit of {limit} model calls; please try again"
"""The error the family sees when a run reaches STITCH_MAX_MODEL_CALLS; `limit` is the cap."""
ELAPSED_RECORD_INTERVAL_S = 30.0
"""Running time after which a stream chunk writes the record even with no new event, so a
restart loses at most this much of the time used (plus the call in flight)."""
FINISHED_RUN_TTL_SECONDS = 3600.0
"""How long a finished run stays replayable before the manager forgets it."""

RUNS_NAMESPACE = "stitch_runs"
"""Root label of the store namespace that holds run records: ('stitch_runs', family_id)."""

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


def run_timeout_message(limit_s: float) -> str:
    """RUN_TIMEOUT_ERROR for a run whose limit is `limit_s` seconds (minutes rounded up, at least 1)."""
    return RUN_TIMEOUT_ERROR.format(minutes=max(1, math.ceil(limit_s / 60)))


def run_budget_message(limit: int) -> str:
    """RUN_BUDGET_ERROR for a run whose model call cap is `limit`."""
    return RUN_BUDGET_ERROR.format(limit=limit)


def budget_exceeded(exc: BaseException) -> RunBudgetExceeded | None:
    """The RunBudgetExceeded behind `exc` (itself, or its cause or context, a few levels deep),
    or None. A sub-agent's call past the cap is raised inside the `task` tool, so a wrapper
    might carry it as the cause."""
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen and len(seen) < 8:
        if isinstance(current, RunBudgetExceeded):
            return current
        seen.add(id(current))
        current = current.__cause__ or current.__context__
    return None


def _count(value: Any) -> int | None:
    """`value` as a count (a whole number >= 0), or None."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def run_error_message(exc: BaseException) -> str:
    """RUN_ERROR for `exc`: its class name only, never its text (log that with logger.exception)."""
    return RUN_ERROR.format(kind=type(exc).__name__)


def _seconds(value: Any, *, positive: bool) -> float | None:
    """`value` as a finite number of seconds (> 0 when `positive`, else >= 0), or None."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    seconds = float(value)
    if not math.isfinite(seconds) or seconds < 0 or (positive and seconds == 0):
        return None
    return seconds


def run_namespace(family_id: str) -> tuple[str, str]:
    """Where a family's run records live in the store."""
    return (RUNS_NAMESPACE, family_id)


def _info_from_record(record: dict[str, Any]) -> RunInfo:
    return RunInfo(
        run_id=str(record["run_id"]),
        family_id=str(record["family_id"]),
        user_id=str(record["user_id"]),
        author_id=str(record["author_id"]),
        fields=schemas.SubmitMemoryFields.model_validate_json(record["fields"]),
        photo_ids=tuple(str(p) for p in record.get("photo_ids") or ()),
    )


def _events_from_record(record: dict[str, Any]) -> list[Event]:
    events: list[Event] = []
    for name, payload in record.get("events") or ():
        events.append((name, schemas.RUN_EVENTS[name].model_validate_json(payload)))
    return events


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
    # Where the run record is written (None: the run lives in this process only).
    store: BaseStore | None = None
    # The last accepted answer, (interrupt id, resume payload), until the run asks again or
    # ends: a restart while the resume is still in flight replays it from the record.
    answered: tuple[str, Any] | None = None
    # A question already in the event log that the restored thread has not reached yet
    # (the process died before checkpointing it): when the thread asks it again, it is
    # shown without being logged twice.
    logged_interrupt: str | None = None
    # Pauses the web could not show that were rejected back to the model (MAX_MALFORMED_PAUSES).
    malformed_pauses: int = 0
    # ---- the time limit (see "The time limit" in the module docstring); all under cond
    # Seconds of running time this run may use (kept in the record).
    run_timeout_s: float = RunLimits.run_timeout_s
    # Seconds of running time already used, up to `stretch_started` (kept in the record).
    elapsed_s: float = 0.0
    # Reads the time for the limit (RunManager's clock; time.monotonic in production).
    clock: Callable[[], float] = time.monotonic
    # The clock reading when the current driving stretch began; None while not counting.
    stretch_started: float | None = None
    # How many worker threads are in _drive / _redrive for this run (an answer can start the
    # next one just before the last one lets go, so this counts rather than flags).
    workers: int = 0
    # elapsed_s as of the last persist (what the next record will hold, at least).
    persisted_elapsed_s: float = 0.0
    # ---- the model call cap and the image budget (see "The model call cap" in the module docstring)
    # Where this run's model calls and images are counted (the manager's registry).
    usage: RunUsage | None = None
    # (model calls, images used) as of the last persist.
    persisted_usage: tuple[int, int] = (0, 0)
    # ---- record writes (see "Writing the run record" in the module docstring)
    # Serializes the store writes of this run. Never taken on the event loop, never with cond held.
    write_lock: threading.Lock = field(default_factory=threading.Lock)
    # True when the run changed since its last snapshot (under cond).
    dirty: bool = False
    # Version of the last snapshot taken (under cond); every snapshot gets the next one.
    version: int = 0
    # Highest snapshot version whose write has returned (under cond).
    stored_version: int = 0
    # Highest snapshot version written to the store (under write_lock).
    written_version: int = 0
    # How many events are stored, so subscribers may see them (under cond). Always <= len(events).
    published: int = 0
    # Each event as the record keeps it, [name, payload JSON], built once in append.
    event_json: list[list[str]] = field(default_factory=list)

    def __post_init__(self) -> None:
        # Events a run starts with were read back from the store: already stored.
        if self.events and not self.event_json:
            self.event_json = [[name, payload.model_dump_json(by_alias=True)] for name, payload in self.events]
        self.published = len(self.events)

    @property
    def terminal(self) -> bool:
        return self.status in ("done", "error")

    @property
    def saved(self) -> bool:
        """Every change so far is in the store (or there is no store). Read with cond held."""
        return not self.dirty and self.stored_version >= self.version

    @property
    def counting(self) -> bool:
        """Running time is being used: a worker is driving the run and it has not ended. Read
        with cond held. That includes a run already 'waiting' whose step is still streaming (a
        sibling branch working while the question is up): only the teller's own time is free."""
        return self.workers > 0 and not self.terminal

    def tick(self) -> None:
        """Add the current driving stretch into `elapsed_s`, then open a new stretch if the run
        is still counting, or close it. Call with cond held, at every change of `counting`."""
        now = self.clock()
        if self.stretch_started is not None:
            self.elapsed_s += max(0.0, now - self.stretch_started)
        self.stretch_started = now if self.counting else None

    def usage_now(self) -> tuple[int, int]:
        """(model calls, images used) so far, from the usage registry. Cheap, no I/O."""
        if self.usage is None:
            return self.persisted_usage
        calls, images = self.usage.counts(self.info.run_id)
        # Never below what the record already says (a registry that forgot the run).
        return max(calls, self.persisted_usage[0]), max(images, self.persisted_usage[1])

    def over_time(self) -> bool:
        """The run has used more running time than its limit. Call with cond held."""
        self.tick()
        return self.elapsed_s > self.run_timeout_s

    # Every mutation below happens with `cond` held. None of them does I/O: a change that
    # the record must keep marks the run dirty (`persist`), and whoever made it calls
    # `flush` once cond is released (`changing` does both).

    @contextmanager
    def changing(self) -> Iterator[None]:
        """Hold cond for a change, then write the record with cond released."""
        try:
            with self.cond:
                yield
        finally:
            self.flush()

    def append(self, name: str, payload: BaseModel) -> None:
        if self.terminal:
            logger.warning("run %s: dropped %s after the run ended", self.info.run_id, name)
            return
        expected = schemas.RUN_EVENTS[name]
        if not isinstance(payload, expected):
            raise TypeError(f"{name} needs a {expected.__name__}, got {type(payload).__name__}")
        self.events.append((name, payload))
        self.event_json.append([name, payload.model_dump_json(by_alias=True)])
        if name in TERMINAL_EVENTS:
            self.status = "done" if name == "done" else "error"
            self.finished_at = time.monotonic()
            # Nothing resumes a finished run: let its graph, pending answer and maps go.
            self.graph = None
            self.pending = None
            self.queued = None
            self.answered = None
            self.tasks.clear()
            self.ended_tasks.clear()
            self.seen_interrupts.clear()
        # Stored before anyone hears of it, so a subscriber never sees an event a restart
        # would lose: subscribers are served events[:published], and published only moves
        # past this event once a record holding it has been written (`flush`).
        self.persist()
        self.notify()

    def record(self) -> dict[str, Any]:
        """The run as the store keeps it (plain JSON; event payloads and fields as JSON strings)."""
        info = self.info
        return {
            "run_id": info.run_id,
            "family_id": info.family_id,
            "user_id": info.user_id,
            "author_id": info.author_id,
            "fields": info.fields.model_dump_json(by_alias=True),
            "photo_ids": list(info.photo_ids),
            "status": self.status,
            "memory_id": self.memory_id,
            "pending_interrupt_id": self.pending.id if self.pending is not None else None,
            "answered": self._answered_record(),
            "malformed_pauses": self.malformed_pauses,
            "elapsed_s": self.elapsed_s,
            "run_timeout_s": self.run_timeout_s,
            "model_calls": self.persisted_usage[0],
            "images_used": self.persisted_usage[1],
            "tasks": [[call_id, name] for call_id, name in self.tasks.items()],
            "ended_tasks": sorted(self.ended_tasks),
            "events": [list(pair) for pair in self.event_json],
        }

    def _answered_record(self) -> dict[str, str] | None:
        if self.answered is None:
            return None
        interrupt_id, payload = self.answered
        try:
            return {"interrupt_id": interrupt_id, "payload": json.dumps(payload, ensure_ascii=False)}
        except (TypeError, ValueError) as exc:  # the rest of the record still matters more
            logger.warning("run %s: the accepted answer is not JSON, so a restart cannot replay it: %s",
                           self.info.run_id, exc)
            return None

    def persist(self) -> None:
        """Mark the run as changed, so the next `flush` writes its record. Call with cond held.

        Without a store nothing is written: every event is published at once. Either way
        the running time so far is added into `elapsed_s` (every status change comes here)."""
        self.tick()
        self.persisted_elapsed_s = self.elapsed_s
        self.persisted_usage = self.usage_now()
        if self.store is None:
            self.published = len(self.events)
            return
        self.dirty = True

    def snapshot(self) -> tuple[int, dict[str, Any], int] | None:
        """(version, record, event count) of the run as it is now, if it changed since the
        last snapshot; None otherwise. Call with cond held; cheap and does no I/O."""
        if self.store is None or not self.dirty:
            return None
        self.dirty = False
        self.version += 1
        return self.version, self.record(), len(self.events)

    def write(self, snap: tuple[int, dict[str, Any], int]) -> None:
        """Write a snapshot, then publish its events. Call WITHOUT cond held.

        Writes of one run are serialized by write_lock, and a snapshot older than one
        already written is skipped, so a stale record never overwrites a newer one. A
        failed write is logged, never raised; its events are published all the same
        (the run goes on in this process even if the store is down)."""
        version, record, count = snap
        store = self.store
        with self.write_lock:
            if store is not None and version > self.written_version:
                try:
                    store.put(run_namespace(self.info.family_id), self.info.run_id, record, index=False)
                except Exception:  # noqa: BLE001 - see above
                    logger.exception("run %s: could not write the run record", self.info.run_id)
                self.written_version = version
        with self.cond:
            self.stored_version = max(self.stored_version, version)
            self.published = max(self.published, min(count, len(self.events)))
            self.notify()

    def flush(self) -> None:
        """Write the record if the run changed since its last snapshot. Call WITHOUT cond held.

        Called while this thread still holds cond (a `changing` block nested in another
        hold; cond is reentrant) it does nothing, so the store is never written with cond
        held: the outermost `changing` block flushes once cond is really released."""
        if self.store is None or self.cond._is_owned():  # type: ignore[attr-defined]
            return
        with self.cond:
            snap = self.snapshot()
        if snap is not None:
            self.write(snap)

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

    def __init__(
        self,
        *,
        repo: AgentRepo,
        build_graph: GraphFactory,
        max_workers: int = 4,
        store: BaseStore | None = None,
        run_timeout_s: float | None = None,
        clock: Callable[[], float] = time.monotonic,
        usage: RunUsage | None = None,
    ) -> None:
        """`store`: where run records are kept so runs survive a restart (module docstring).
        None keeps runs in this process only.

        `run_timeout_s`: seconds of running time one run may use ("The time limit" in the
        module docstring). None reads STITCH_RUN_TIMEOUT_S (`RunLimits.from_env`) as each
        run starts. Each run keeps its limit in its record. `clock`: what the limit is
        measured with (monotonic seconds; tests pass a fake one).

        `usage`: the registry the runs' model calls and images are counted in (the one
        `build_graph`'s studios and art service count in; `wiring.make_manager` passes
        `Services.usage`). Each run record keeps both counts, and a restored run seeds them
        back. None: a registry of this manager's own (nothing counts in it unless the graphs do)."""
        if run_timeout_s is not None and _seconds(run_timeout_s, positive=True) is None:
            raise ValueError(f"run_timeout_s must be a number of seconds > 0, not {run_timeout_s!r}")
        self._repo = repo
        self._build_graph = build_graph
        self._store = store
        self._run_timeout_s = None if run_timeout_s is None else float(run_timeout_s)
        self._clock = clock
        self._usage = usage if usage is not None else RunUsage()
        self._runs: dict[str, _Run] = {}
        self._lock = threading.Lock()
        # Serializes loading runs from the store, so one run id is never restored twice.
        self._restore_lock = threading.Lock()
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
        run_id = uuid.uuid4().hex
        info = RunInfo(
            run_id=run_id,
            family_id=family_id,
            user_id=user_id,
            author_id=person_id,
            fields=fields,
            photo_ids=tuple(photo_ids),
        )
        graph_input = self._initial_input(info)  # ValueError / MemoryNotFound: nothing started

        run = _Run(info=info, config=studio.thread_config(run_id), driving=True, store=self._store,
                   run_timeout_s=self._run_limit(), clock=self._clock, usage=self._usage)
        with run.changing():
            run.persist()
        with self._lock:
            self._prune_finished()
            self._runs[run_id] = run
        self._pool.submit(self._drive, run, graph_input)
        return run_id

    async def start_run_async(
        self,
        family_id: str,
        user_id: str,
        person_id: str,
        fields: schemas.SubmitMemoryFields,
        photo_ids: list[str],
    ) -> str:
        """`start_run` on a worker thread, for callers on an event loop.

        start_run reads the repo (the teller, the memory of a second telling) and writes
        the first run record, so on the loop it would stall every other request while the
        database answers. Raises what start_run raises."""
        return await asyncio.to_thread(self.start_run, family_id, user_id, person_id, fields, photo_ids)

    async def stream_run(self, family_id: str, run_id: str) -> AsyncIterator[Event]:
        """Every event of the run from the first one, then live events, until done or error.

        RunNotFound is raised on the first iteration, before anything is yielded, when
        the run is unknown or belongs to another family. While the run waits for an
        answer the iterator stays open. Any number of subscribers may follow one run;
        each gets the full sequence. A run this process does not hold is loaded from the
        store on a worker thread, so the event loop never waits on the database.

        Only events already stored are served (`_Run.published`), and the run's lock is
        only taken for in-memory work: no store write ever happens with it held, so the
        event loop never waits on the database here either.
        """
        run = self._cached(family_id, run_id)
        if run is None:
            run = await asyncio.to_thread(self._get, family_id, run_id)
        loop = asyncio.get_running_loop()
        wake = asyncio.Event()
        with run.cond:
            run.subscribers.append((loop, wake))
        index = 0
        try:
            while True:
                wake.clear()
                with run.cond:
                    published = run.published
                    batch = run.events[index:published]
                    # Nothing is appended after done or error; the end is the last event, stored.
                    finished = run.terminal and published == len(run.events)
                for event in batch:
                    yield event
                index += len(batch)
                if finished and index >= published:
                    return
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
        pending question (wrong kind, empty text); the run stays paused. A run already past
        its time limit is not resumed: it ends with RUN_TIMEOUT_ERROR and this returns.

        The run is waiting as soon as its question is out, even if a parallel branch of
        the same step is still working. Then the answer is queued on the run and the
        worker resumes with it once that stream closes; otherwise a worker resumes now.
        Either way this call only takes the run's lock briefly and never waits for it.
        The run record is written after the lock is released (it may wait for another
        write of the same run), so call this off the event loop.
        """
        run = self._get(family_id, run_id)
        if run.info.user_id != user_id:
            raise NotRunOwner("only the person who started this run can answer it")
        with run.changing():
            if run.status != "waiting" or run.pending is None:
                raise RunNotWaiting(f"run {run_id} is {run.status}, not waiting for an answer")
            if self._end_if_over_time(run):
                # Already past its limit (a restored record, say): the answer cannot be used,
                # and the run's stream now says why it ended.
                return
            pending = run.pending
            payload = answer_to_resume(pending.value, answer)  # ValueError propagates; still waiting
            run.status = "running"
            run.pending = None
            run.answered = (pending.id, payload)
            if run.driving:
                # The step that asked is still streaming (a sibling branch is working);
                # _settle resumes with this answer when the stream closes.
                run.queued = (pending, payload)
                run.persist()
                run.notify()
                return
            resume = _resume_command(pending, payload, run.pending_count)
            run.pending_count = 0
            # Interrupts still pending after this resume surface again with the same id.
            run.seen_interrupts.clear()
            run.driving = True
            run.persist()
            run.notify()
        self._pool.submit(self._drive, run, resume)

    def status(self, family_id: str, run_id: str) -> schemas.RunStatus:
        """running, waiting (for an answer), done or error."""
        run = self._get(family_id, run_id)
        with run.cond:
            return run.status

    def events(self, family_id: str, run_id: str) -> list[Event]:
        """A snapshot of the run's events so far, as a subscriber would see them: only
        the stored ones (tests and debugging)."""
        run = self._get(family_id, run_id)
        with run.cond:
            return run.events[: run.published]

    def wait(self, family_id: str, run_id: str, timeout: float | None = None) -> schemas.RunStatus:
        """Block until the run is no longer running (waiting, done or error) and its record
        says so, or `timeout` passes.

        For scripts and tests; never call it on the API's event loop.
        """
        run = self._get(family_id, run_id)
        with run.cond:
            run.cond.wait_for(lambda: run.status != "running" and run.saved, timeout)
            return run.status

    def shutdown(self, *, wait: bool = True) -> None:
        """Stop the worker pool (tests and process exit)."""
        self._pool.shutdown(wait=wait)

    # ------------------------------------------------------------------ internals

    def _run_limit(self) -> float:
        """The time limit a run takes when it starts (or when its record has none)."""
        if self._run_timeout_s is not None:
            return self._run_timeout_s
        return RunLimits.from_env().run_timeout_s

    def _end_if_over_time(self, run: _Run) -> bool:
        """End the run with RUN_TIMEOUT_ERROR if it has used more running time than its
        limit, and say so (True). Call with cond held (inside `changing`)."""
        if run.terminal or not run.over_time():
            return False
        logger.warning("run %s: over its time limit (%.1fs of running time, limit %.0fs); ending it",
                       run.info.run_id, run.elapsed_s, run.run_timeout_s)
        run.driving = False
        run.append("error", schemas.ErrorEvent(message=run_timeout_message(run.run_timeout_s)))
        return True

    def _fail(self, run: _Run, exc: BaseException, what: str) -> None:
        """End the run after `exc` escaped its graph: past the model call cap, the cap's own
        message and a warning; anything else, RUN_ERROR and the traceback in the log. Call
        WITHOUT cond held, from the worker; the record is written once cond is released."""
        run_id = run.info.run_id
        budget = budget_exceeded(exc)
        if budget is not None:
            calls, _ = run.usage_now() if run.usage is not None else (budget.limit, 0)
            logger.warning("run %s: reached its limit of %d model calls (%d made); ending it",
                           run_id, budget.limit, calls)
            message = run_budget_message(budget.limit)
        else:
            logger.exception("run %s %s", run_id, what, exc_info=exc)
            message = run_error_message(exc)
        with run.changing():
            run.driving = False
            run.append("error", schemas.ErrorEvent(message=message))

    @contextmanager
    def _on_worker(self, run: _Run) -> Iterator[None]:
        """Mark the run as driven by this worker thread, so its running time counts."""
        with run.cond:
            run.workers += 1
            run.tick()
        try:
            yield
        finally:
            # In memory only: the next persist writes it (a dying process writes nothing).
            with run.cond:
                run.workers -= 1
                run.tick()

    def _initial_input(self, info: RunInfo) -> dict[str, Any]:
        """The studio's first input for a run: the run context message, plus the memory's
        screenplay and timeline as draft files for a second telling.

        Raises ValueError if the teller is not a person in the family or `fields.audio_id`
        is not an audio file of this family, and MemoryNotFound
        if `fields.memory_id` is not a memory of this family with a compiled scene.
        """
        family_id, fields = info.family_id, info.fields
        person = self._repo.get_person(family_id, info.author_id)
        if person is None:
            raise ValueError(f"person {info.author_id!r} is not in this family")
        if fields.audio_id is not None:
            # Saved on the telling, so it must be this family's own recording (as a photo
            # must be: agent/conversation/service.py _validate_photos), never another family's.
            found = self._repo.get_media(family_id, fields.audio_id)
            if found is None or not str(found[1]).lower().startswith("audio/"):
                raise ValueError("Audio is not available in this family")

        files: dict[str, Any] = {}
        if fields.memory_id is not None:
            memory = self._repo.get_memory(family_id, fields.memory_id)
            if memory is None or memory.screenplay is None or memory.timeline is None:
                raise MemoryNotFound(f"memory {fields.memory_id!r} not found, or it has no scene yet")
            files = {
                SCREENPLAY_PATH: create_file_data(_scene_json(memory.screenplay)),
                TIMELINE_PATH: create_file_data(_scene_json(memory.timeline)),
            }

        context = json.dumps(run_context(info, person.name), indent=2, ensure_ascii=False)
        graph_input: dict[str, Any] = {"messages": [HumanMessage(content=context)]}
        if files:
            graph_input["files"] = files
        return graph_input

    def _cached(self, family_id: str, run_id: str) -> _Run | None:
        """The run if this process holds it. RunNotFound if it belongs to another family."""
        with self._lock:
            run = self._runs.get(run_id)
        if run is not None and run.info.family_id != family_id:
            raise RunNotFound(f"run {run_id!r} not found")
        return run

    def _get(self, family_id: str, run_id: str) -> _Run:
        run = self._cached(family_id, run_id)
        if run is None:
            run = self._restore(family_id, run_id)
        if run is None or run.info.family_id != family_id:
            raise RunNotFound(f"run {run_id!r} not found")
        return run

    def _prune_finished(self) -> None:
        """Forget runs that ended more than `finished_run_ttl` seconds ago. Call with `_lock` held.

        Only the process forgets them: their records stay in the store, so they still replay.
        """
        cutoff = time.monotonic() - self.finished_run_ttl
        stale = [rid for rid, r in self._runs.items() if r.finished_at is not None and r.finished_at <= cutoff]
        for rid in stale:
            del self._runs[rid]
            self._usage.forget(rid)  # its counts are in its record

    # ------------------------------------------------------------------ restore after a restart

    def _restore(self, family_id: str, run_id: str) -> _Run | None:
        """Load a run this process does not hold from the store (None if there is none).

        Only the record under this family's namespace is read. A finished run is restored
        as a replay; a waiting one gets its graph and pending interrupt back; a running
        one is driven again from its last checkpoint on a worker.
        """
        if self._store is None:
            return None
        with self._restore_lock:
            with self._lock:
                run = self._runs.get(run_id)
            if run is not None:  # restored (or started) meanwhile
                return run
            try:
                item = self._store.get(run_namespace(family_id), run_id)
            except Exception:  # noqa: BLE001 - an unreachable store reads as an unknown run
                logger.exception("run %s: could not read the run record", run_id)
                return None
            if item is None:
                return None
            record = dict(item.value)
            try:
                info = _info_from_record(record)
                events = _events_from_record(record)
                status = record.get("status")
                if status not in ("running", "waiting", "done", "error"):
                    raise ValueError(f"unknown status {status!r}")
                answered = record.get("answered")
                if isinstance(answered, dict) and answered.get("interrupt_id"):
                    answered = (str(answered["interrupt_id"]), json.loads(answered["payload"]))
                else:
                    answered = None
                malformed_pauses = int(record.get("malformed_pauses") or 0)
                # Records from before the time limit have neither: nothing used, today's limit.
                elapsed_s = _seconds(record.get("elapsed_s", 0.0), positive=False)
                if elapsed_s is None:
                    logger.warning("run %s: unreadable elapsed_s %r in the run record; counting from 0",
                                   run_id, record.get("elapsed_s"))
                    elapsed_s = 0.0
                run_timeout_s = _seconds(record.get("run_timeout_s"), positive=True)
                if run_timeout_s is None:
                    run_timeout_s = self._run_limit()
                # Records from before the model call cap have neither: nothing used.
                used: list[int] = []
                for key in ("model_calls", "images_used"):
                    count = _count(record.get(key, 0))
                    if count is None:
                        logger.warning("run %s: unreadable %s %r in the run record; counting from 0",
                                       run_id, key, record.get(key))
                        count = 0
                    used.append(count)
                model_calls, images_used = used
            except (KeyError, TypeError, ValueError) as exc:  # pydantic ValidationError is a ValueError
                logger.error("run %s: unreadable run record: %s", run_id, exc)
                return None
            if info.family_id != family_id or info.run_id != run_id:
                logger.error("run %s: the record under family %s names another run", run_id, family_id)
                return None

            # The clock starts again in this process: time while the API was down is not counted.
            # Both caps go on from what the run had used: seeded before its graph is built or driven.
            if status in ("running", "waiting"):
                self._usage.seed(run_id, model_calls=model_calls, images_used=images_used)
            run = _Run(info=info, config=studio.thread_config(run_id), store=self._store, events=events,
                       run_timeout_s=run_timeout_s, elapsed_s=elapsed_s, persisted_elapsed_s=elapsed_s,
                       clock=self._clock, usage=self._usage, persisted_usage=(model_calls, images_used))
            run.memory_id = record.get("memory_id")
            run.tasks = {str(call_id): str(name) for call_id, name in record.get("tasks") or ()}
            run.ended_tasks = {str(call_id) for call_id in record.get("ended_tasks") or ()}
            run.last_plan = next((p for n, p in reversed(events) if n == "plan"), None)  # type: ignore[misc]
            run.answered = answered
            run.malformed_pauses = malformed_pauses

            redrive = False
            if status in ("done", "error"):
                run.status = status
                run.finished_at = time.monotonic()
            elif status == "waiting":
                redrive = not self._restore_waiting(run, record.get("pending_interrupt_id"))
            else:
                redrive = True
            if redrive:
                with run.changing():
                    run.status = "running"
                    run.driving = True
                    run.persist()
            with self._lock:
                self._runs[run_id] = run
        if redrive:
            self._pool.submit(self._redrive, run)
        return run

    def _restore_waiting(self, run: _Run, pending_id: Any) -> bool:
        """Rebuild a waiting run's graph and read its pending interrupt back.

        True when the run is ready for an answer (or ended in error); False when nothing
        is pending on the thread any more and it has to be driven on instead.
        """
        try:
            graph = self._build_graph(run.info)
            interrupts = list(graph.get_state(run.config).interrupts)
        except Exception as exc:  # noqa: BLE001 - the family sees why the run cannot go on
            logger.exception("run %s: could not restore its studio", run.info.run_id)
            with run.changing():
                run.append("error", schemas.ErrorEvent(message=run_error_message(exc)))
            return True
        with run.changing():
            run.graph = graph
            if not interrupts:
                # The record got ahead of the checkpoint: drive on, and when the thread
                # asks this question again it is not logged a second time.
                run.logged_interrupt = pending_id if isinstance(pending_id, str) else None
                return False
            run.pending_count = len(interrupts)
            shown = next((i for i in interrupts if i.id == pending_id), None)
            if shown is not None:
                run.pending = shown
                run.status = "waiting"
                run.seen_interrupts = {shown.id}
                return True
            # The question on record is gone; show what the thread is waiting for now.
            run.status = "running"
            self._interrupt(run, interrupts[0])
            if run.queued is not None:
                # A pause the web cannot show, rejected back to the model: drive on with that
                # rejection, which _redrive finds in `answered`.
                run.queued = None
                return False
            return True

    def _redrive(self, run: _Run) -> None:
        """Drive a run that was mid-step when the process died, from its last checkpoint.

        No checkpoint yet: start it again from its first input. Its accepted answer still
        pending on the thread: resume with that answer. Otherwise stream input None on the
        same thread, which reruns the step that was cut short. A run already past its time
        limit is not driven: it ends with RUN_TIMEOUT_ERROR. Never raises.
        """
        with self._on_worker(run):
            with run.changing():
                if self._end_if_over_time(run):
                    return
            self._redrive_on(run)

    def _redrive_on(self, run: _Run) -> None:
        run_id = run.info.run_id
        logger.warning("run %s: was running when the API stopped; resuming it from its last checkpoint", run_id)
        try:
            with run.cond:
                graph = run.graph
                answered = run.answered
            if graph is None:
                graph = self._build_graph(run.info)
                with run.cond:
                    run.graph = graph
            state = graph.get_state(run.config)
            graph_input: Any = None
            if state.created_at is None and not state.values:
                graph_input = self._initial_input(run.info)
            else:
                interrupts = list(state.interrupts)
                match = next((i for i in interrupts if answered is not None and i.id == answered[0]), None)
                if match is not None and answered is not None:
                    graph_input = _resume_command(match, answered[1], len(interrupts))
        except Exception as exc:  # noqa: BLE001 - every failure becomes the run's error event
            self._fail(run, exc, "failed to resume")
            return
        self._drive_on(run, graph_input, from_checkpoint=graph_input is None)

    # ------------------------------------------------------------------ driving

    def _drive(self, run: _Run, graph_input: Any, *, from_checkpoint: bool = False) -> None:
        """Stream the graph (start or resume) and translate it, then resume with any
        answer queued meanwhile, until the run waits or ends. Never raises.

        `from_checkpoint`: stream once with input None (continue the thread where it stopped)."""
        with self._on_worker(run):
            self._drive_on(run, graph_input, from_checkpoint=from_checkpoint)

    def _drive_on(self, run: _Run, graph_input: Any, *, from_checkpoint: bool = False) -> None:
        """`_drive` on a worker already marked (`_on_worker`)."""
        try:
            with run.cond:
                graph = run.graph
            if graph is None:
                graph = self._build_graph(run.info)
                with run.cond:
                    run.graph = graph
            while graph_input is not None or from_checkpoint:
                from_checkpoint = False
                for chunk in graph.stream(graph_input, run.config, stream_mode=["updates", "custom"], subgraphs=True):
                    if not self._translate(run, chunk):
                        with run.cond:
                            run.driving = False
                        return
                graph_input = self._settle(run, graph)
        except Exception as exc:  # noqa: BLE001 - every failure becomes the run's error event
            # RunBudgetExceeded (the model call cap) ends the run here too, with its own message.
            self._fail(run, exc, "failed")

    def _translate(self, run: _Run, chunk: Any) -> bool:
        """Turn one stream chunk into events. Returns False once the run has ended (or has
        just ended past its time limit; the chunk is then dropped)."""
        namespace, mode, data = chunk
        with run.changing():
            if run.terminal or self._end_if_over_time(run):
                return False
            if run.elapsed_s - run.persisted_elapsed_s >= ELAPSED_RECORD_INTERVAL_S:
                run.persist()  # keep the time used in the record through a quiet stretch
            elif run.usage_now() != run.persisted_usage:
                run.persist()  # a model call or an image since the last write: keep the counts
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
        if interrupt_id == run.logged_interrupt:
            run.logged_interrupt = None
            run.pending = interrupt
            run.status = "waiting"
            run.answered = None
            run.persist()
            run.notify()
            return
        try:
            name, payload = interrupt_to_event(interrupt)
        except ValueError as exc:
            self._reject_malformed(run, interrupt, str(exc))
            return
        # Waiting from the moment the family can see it, even if a sibling branch is still working.
        run.pending = interrupt
        run.status = "waiting"
        run.answered = None  # the answer before this one has been taken in
        run.append(name, payload)

    def _reject_malformed(self, run: _Run, interrupt: Interrupt, reason: str) -> None:
        """Hand a pause the web cannot show back to the model, or end the run past the cap.

        The rejection is queued like an answer (`run.queued`), so _settle resumes the same
        thread with it once the stream closes; it is also kept in `run.answered`, so a
        restart while that resume is in flight replays it. No event, and the run stays
        running. Call with `cond` held.
        """
        run_id = run.info.run_id
        payload: Any = None
        if run.malformed_pauses < MAX_MALFORMED_PAUSES:
            message = f"not shown to the family: {reason}. Fix the arguments and call the tool again."
            try:
                payload = reject_to_resume(interrupt, message)
            except ValueError:
                payload = None  # not a human-in-the-loop pause: nothing the model can fix
        if payload is None:
            logger.warning("run %s: ended on a pause the web cannot show (%d handed back already): %s",
                           run_id, run.malformed_pauses, reason)
            run.append("error", schemas.ErrorEvent(message=MALFORMED_PAUSE_ERROR))
            return
        run.malformed_pauses += 1
        logger.warning("run %s: handed a pause the web cannot show back to the studio (%d of %d): %s",
                       run_id, run.malformed_pauses, MAX_MALFORMED_PAUSES, reason)
        run.queued = (interrupt, payload)
        run.answered = (interrupt.id, payload)
        run.persist()

    def _settle(self, run: _Run, graph: Any) -> Command | None:
        """After a stream closes: the Command to resume with if an answer was queued meanwhile;
        otherwise wait for an answer, or end with done / error, and release the run (None)."""
        state = graph.get_state(run.config)
        interrupts = list(state.interrupts)
        with run.changing():
            if run.terminal:
                run.driving = False
                return None
            ids = {i.id for i in interrupts}
            if run.queued is not None:
                pending, payload = run.queued
                run.queued = None
                if pending.id in ids:
                    if self._end_if_over_time(run):
                        return None
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
                    shown = next((i for i in interrupts if i.id == run.logged_interrupt), interrupts[0])
                    self._interrupt(run, shown)
                    if run.terminal:
                        return None
                    if run.queued is not None:
                        # Not showable: rejected back to the model; resume with that now.
                        pending, payload = run.queued
                        run.queued = None
                        if self._end_if_over_time(run):
                            return None
                        run.driving = True
                        run.pending_count = 0
                        run.seen_interrupts.clear()
                        return _resume_command(pending, payload, len(interrupts))
                run.pending_count = len(interrupts)
                run.status = "waiting"
                if self._end_if_over_time(run):
                    # The step ran out the clock as it closed: never wait on an answer that
                    # could not be used.
                    return None
                run.persist()
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
            # The studio's own last words, capped; no dangling colon when it said nothing.
            said = _clip(_text_of(last), ERROR_TEXT_LIMIT) if last is not None else ""
            message = "the studio finished without saving a scene" + (f": {said}" if said else "")
            run.append("error", schemas.ErrorEvent(message=message))
            return None


# --------------------------------------------------------------------------- module seam

_manager: RunManager | None = None
_manager_lock = threading.Lock()


def configure(manager: RunManager | None) -> None:
    """Install the process's RunManager (None removes it, and the next get_manager() builds one)."""
    global _manager  # noqa: PLW0603
    _manager = manager


def get_manager() -> RunManager:
    """The configured RunManager, or, when none is, the production one built on first use.

    The production manager (`agent.production.production_manager()`) is built once under
    a lock and installed with `configure`. If it cannot be built (no ANTHROPIC_API_KEY,
    for example) this raises `RuntimeError('agent runs are not configured: <reason>')`,
    which the routes answer with 503; nothing is cached, so a later call tries again.
    """
    manager = _manager
    if manager is not None:
        return manager
    with _manager_lock:
        if _manager is not None:
            return _manager
        try:
            from agent.production import production_manager

            manager = production_manager()
        except Exception as exc:  # noqa: BLE001 - every reason becomes the one not-configured error
            raise RuntimeError(f"agent runs are not configured: {exc}") from exc
        configure(manager)
        return manager


def start_run(
    family_id: str,
    user_id: str,
    person_id: str,
    fields: schemas.SubmitMemoryFields,
    photo_ids: list[str],
) -> str | Awaitable[str]:
    """Start a run (see `RunManager.start_run`).

    Called outside an event loop (scripts, demo.py, tests) it starts the run and returns
    the run id. Called from a thread running an event loop (the submit route) it returns
    an awaitable instead, which does the work on a worker thread, so the loop never waits
    on the repo or the store; the route awaits it (`_maybe_await`). Then every error
    (ValueError, MemoryNotFound, the not-configured RuntimeError) is raised on await.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return get_manager().start_run(family_id, user_id, person_id, fields, photo_ids)
    return _start_run_async(family_id, user_id, person_id, fields, photo_ids)


async def _start_run_async(
    family_id: str,
    user_id: str,
    person_id: str,
    fields: schemas.SubmitMemoryFields,
    photo_ids: list[str],
) -> str:
    # Building the production manager on first use opens connections: off the loop too.
    manager = _manager if _manager is not None else await asyncio.to_thread(get_manager)
    start_async = getattr(manager, "start_run_async", None)
    if start_async is not None:
        return await start_async(family_id, user_id, person_id, fields, photo_ids)
    # A stand-in installed with configure() that only has the sync start_run
    # (seed/fake_runs.ScriptedRunManager): run it on a worker thread all the same.
    return await asyncio.to_thread(manager.start_run, family_id, user_id, person_id, fields, photo_ids)


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
    "RUNS_NAMESPACE",
    "RUN_BUDGET_ERROR",
    "RUN_ERROR",
    "RUN_TIMEOUT_ERROR",
    "answer_run",
    "budget_exceeded",
    "configure",
    "get_manager",
    "run_budget_message",
    "run_error_message",
    "run_namespace",
    "run_timeout_message",
    "start_run",
    "stream_run",
]
