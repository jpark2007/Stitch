"""Scripted agent runs for local and offline demos: no model calls, no network, no keys.

`ScriptedRunManager` is a stand-in for `agent.runs.RunManager` with the same seam
(start_run, stream_run, answer_run, status, wait, shutdown). It plays a fixed script on a
daemon thread, writes through db/ only, and stages every new memory with the hand-written
hymn-book scene from web/public/samples/hymn_books.memory.json (via `seed.demo.load_sample`),
recast with the run's people. `install_fake_runs()` puts one behind `agent.runs`, so the
add-a-memory flow works with no API keys and no MongoDB (README "Scope for the weekend").

Script for a new memory (`fields.memory_id is None`), one step every `step_seconds`:

1. plan (Read the telling / Ask one follow-up / Find the props / Stage the scene / Narrate)
2. subagent_start interviewer, subagent_end interviewer
3. question "Who else was there that day?", then the run waits for AnswerRequest(kind="answer").
   The reply is kept: it is appended to the telling text as a new paragraph.
   (Skipped when `ask_followup=False`.)
4. plan, subagent_start prop_master, props_found with the hymn-book prop (reused=True), then
   the run waits for AnswerRequest(kind="props"). Edits for the chip's id are honored:
   `rename` renames the saved prop, `replace_photo` puts a photo of this family on it,
   `remove` leaves the prop out of the memory and strips it from the scene (the saved prop
   itself is never deleted, and is not created for this run if it did not exist yet).
   subagent_end prop_master.
5. plan, subagent_start director, subagent_end director, plan
6. the memory is saved: create_memory with the telling, then save_scene with the sample's
   screenplay and timeline. Person ids are recast: the sample's `mom` becomes the teller,
   and `dad` the first participant who is not the teller, else the teller's spouse, else
   another person of the family, else the teller. The sample's prop and set are created in
   the run's family when missing (keeping the sample ids when free, otherwise
   `<sampleId>_<familyId>`), and the scene points at the family's copies.
7. plan (all completed), scene_ready, done(memoryId)

A second telling (`fields.memory_id` set) adds the telling to that memory, links the teller
and participants to it, and sends plan, subagent_start, subagent_end, plan and done: no
question, no prop review, no scene save.

Any exception ends the run with a single error event with a plain message. Every payload is
an instance of `schemas.RUN_EVENTS[name]`, and every run ends with exactly one done or error.

This module never imports `agent` except inside `install_fake_runs`.
"""

from __future__ import annotations

import asyncio
import importlib
import logging
import re
import threading
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel
from pymongo.database import Database

import schemas
from db import client as db_client
from db import media as media_db
from db import memories as memories_db
from db import people as people_db
from db import props as props_db
from screenplay.schema import Screenplay, Timeline
from seed import demo

logger = logging.getLogger(__name__)

Event = tuple[str, schemas.Model]

SAMPLE_TELLER = "mom"
SAMPLE_OTHER = "dad"
FOLLOWUP_QUESTION = "Who else was there that day?"
ERROR_MESSAGE = "Something went wrong while making this memory. Please try again."
STOPPED_MESSAGE = "The studio stopped before this memory was finished."
TITLE_WORDS = 6

NEEDS_INPUT = {"question": "answer", "props_found": "props"}
"""Event that pauses the run -> the AnswerRequest kind that resumes it."""
TERMINAL = frozenset({"done", "error"})


class _Stopped(Exception):
    """The manager was shut down while the run was in flight."""


# --------------------------------------------------------------------------- one run


@dataclass(eq=False)
class _Run:
    run_id: str
    family_id: str
    user_id: str
    person_id: str
    fields: schemas.SubmitMemoryFields
    photo_ids: list[str]
    cond: threading.Condition = field(default_factory=threading.Condition)
    events: list[Event] = field(default_factory=list)
    status: schemas.RunStatus = "running"
    pending: str | None = None  # the needs-input event the run is paused on
    answer: schemas.AnswerRequest | None = None
    subscribers: list[tuple[asyncio.AbstractEventLoop, asyncio.Event]] = field(default_factory=list)

    @property
    def terminal(self) -> bool:
        return self.status in TERMINAL

    def notify(self) -> None:
        """Wake threads in wait() and every async subscriber. Call with `cond` held."""
        self.cond.notify_all()
        for loop, wake in list(self.subscribers):
            try:
                loop.call_soon_threadsafe(wake.set)
            except RuntimeError:  # that subscriber's loop is closed
                self.subscribers.remove((loop, wake))


# --------------------------------------------------------------------- scene recasting

_TOKEN = re.compile(r"[A-Za-z0-9_]+")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9_.:\-]+$")
# Fields that hold prose, never ids: left alone even when a word equals an id.
_PROSE_KEYS = frozenset({"text", "word", "label", "pageText", "title", "description", "notes", "critique"})


def _remap_str(value: str, ids: dict[str, str]) -> str:
    """Replace whole id tokens in an id-like string: "mom", "puppet:mom.hand_r", "telling:dad:s2"."""
    if value in ids:
        return ids[value]
    if not _IDENTIFIER.match(value):
        return value
    return _TOKEN.sub(lambda m: ids.get(m.group(0), m.group(0)), value)


def remap_ids(value: Any, ids: dict[str, str], key: str | None = None) -> Any:
    """Recursively rename ids in a JSON value (dict keys included; prose fields skipped).

    When two keys of one object map to the same id, the first one is kept.
    """
    if not ids:
        return value
    if isinstance(value, dict):
        out: dict[Any, Any] = {}
        for k, v in value.items():
            new_key = _remap_str(k, ids) if isinstance(k, str) else k
            if new_key not in out:
                out[new_key] = remap_ids(v, ids, k if isinstance(k, str) else None)
        return out
    if isinstance(value, list):
        return [remap_ids(v, ids, key) for v in value]
    if isinstance(value, str) and key not in _PROSE_KEYS:
        return _remap_str(value, ids)
    return value


def _dedupe(ids: list[str]) -> list[str]:
    return list(dict.fromkeys(ids))


def _strip_interactions(items: list[dict] | None, target: str, prop_id: str) -> list[dict] | None:
    if items is None:
        return None
    kept = []
    for item in items:
        drives = dict(item.get("drives") or {})
        if drives.get("prop") == prop_id:
            continue
        targets = drives.get("targets")
        if targets is not None:
            targets = [t for t in targets if t != target]
            if not targets:
                continue
            drives["targets"] = targets
        kept.append({**item, "drives": drives})
    return kept


def _strip_explanations(items: list[dict] | None, target: str) -> list[dict] | None:
    if items is None:
        return None
    return [e for e in items if e.get("target") != target]


def _mentions_prop(value: Any, prop_id: str) -> bool:
    return isinstance(value, str) and (value == prop_id or value.startswith(prop_id + "."))


def strip_prop(screenplay: dict, timeline: dict, prop_id: str) -> tuple[dict, dict]:
    """Remove one prop from a screenplay and timeline (camelCase JSON dicts)."""
    target = f"prop:{prop_id}"
    sp = dict(screenplay)
    sp["props"] = [p for p in sp.get("props", []) if p != prop_id]
    shots = []
    for shot in sp.get("shots", []):
        shot = dict(shot)
        start = {}
        for who, state in (shot.get("start") or {}).items():
            state = {k: v for k, v in state.items() if not (k in ("holding", "in") and _mentions_prop(v, prop_id))}
            start[who] = state
        shot["start"] = start
        shot["propStart"] = {
            pid: {k: v for k, v in ps.items() if not (k == "on" and _mentions_prop(v, prop_id))}
            for pid, ps in (shot.get("propStart") or {}).items()
            if pid != prop_id
        }
        shot["camera"] = [
            {k: v for k, v in cue.items() if not (k == "target" and v == prop_id)} for cue in shot.get("camera", [])
        ]
        actions = [
            a for a in shot.get("actions", [])
            if not any(_mentions_prop(a.get(k), prop_id) for k in ("what", "on", "target", "at"))
        ]
        shot["actions"] = actions
        cues = {a["id"] for a in actions if a.get("id")} | {a["do"] for a in actions}

        def clean(lines: list[dict], cues: set[str] = cues) -> list[dict]:
            out = []
            for line in lines:
                if line.get("cue") is not None and line["cue"] not in cues:
                    line = {k: v for k, v in line.items() if k not in ("cue", "cueWord")}
                out.append(line)
            return out

        shot["narration"] = clean(shot.get("narration", []))
        if sp.get("narrations"):
            sp["narrations"] = {
                author: {sid: (clean(lines) if sid == shot["id"] else lines) for sid, lines in by_shot.items()}
                for author, by_shot in sp["narrations"].items()
            }
        shots.append(shot)
    sp["shots"] = shots
    sp["interactions"] = _strip_interactions(sp.get("interactions"), target, prop_id)
    sp["explanations"] = _strip_explanations(sp.get("explanations"), target)

    tl = dict(timeline)
    tl["tracks"] = [t for t in tl.get("tracks", []) if t.get("target") != target]
    tl["interactions"] = _strip_interactions(tl.get("interactions"), target, prop_id)
    tl["explanations"] = _strip_explanations(tl.get("explanations"), target)
    return sp, tl


def short_title(text: str, words: int = TITLE_WORDS) -> str:
    """A short title from the first sentence of the telling."""
    first = re.split(r"(?<=[.!?])\s+", text.strip(), maxsplit=1)[0]
    tokens = first.split()
    title = " ".join(tokens[:words]).rstrip(".,;:!?-")
    if len(tokens) > words:
        title += "…"
    return title or "A memory"


# --------------------------------------------------------------------------- manager


class ScriptedRunManager:
    """A scripted stand-in for agent.runs.RunManager. Thread-safe; one per API process."""

    def __init__(
        self,
        *,
        db_getter: Callable[[], Database] = db_client.get_db,
        step_seconds: float = 0.6,
        ask_followup: bool = True,
    ) -> None:
        self._db_getter = db_getter
        self._step_seconds = max(0.0, float(step_seconds))
        self._ask_followup = ask_followup
        self._runs: dict[str, _Run] = {}
        self._threads: list[threading.Thread] = []
        self._lock = threading.Lock()
        self._stop = threading.Event()

    # ------------------------------------------------------------------ public API

    def start_run(
        self,
        family_id: str,
        user_id: str,
        person_id: str,
        fields: schemas.SubmitMemoryFields,
        photo_ids: list[str],
    ) -> str:
        """Start a scripted run and return its id at once.

        ValueError: `person_id` (or a participant) is not a person of the family.
        LookupError: `fields.memory_id` is set but is not a memory of the family.
        """
        if self._stop.is_set():
            raise RuntimeError("the scripted studio has been shut down")
        db = self._db_getter()
        if people_db.get_person(db, family_id, person_id) is None:
            raise ValueError(f"person {person_id!r} is not in this family")
        if fields.participant_ids:
            found = {p.id for p in people_db.get_people(db, family_id, fields.participant_ids)}
            missing = [pid for pid in fields.participant_ids if pid not in found]
            if missing:
                raise ValueError(f"participants not in this family: {', '.join(missing)}")
        if fields.memory_id is not None and memories_db.get_memory(db, family_id, fields.memory_id) is None:
            raise LookupError(f"memory {fields.memory_id!r} not found")

        run = _Run(
            run_id=uuid.uuid4().hex,
            family_id=family_id,
            user_id=user_id,
            person_id=person_id,
            fields=fields,
            photo_ids=list(photo_ids),
        )
        thread = threading.Thread(
            target=self._drive, args=(run, db), name=f"scripted-run-{run.run_id[:8]}", daemon=True
        )
        with self._lock:
            self._runs[run.run_id] = run
            self._threads = [t for t in self._threads if t.is_alive()] + [thread]
        thread.start()
        return run.run_id

    def stream_run(self, family_id: str, run_id: str) -> AsyncIterator[Event]:
        """Every event from the first, then live ones, until done or error.

        LookupError is raised by this call itself for an unknown or other-family run.
        """
        run = self._get(family_id, run_id)
        return self._follow(run)

    def answer_run(self, family_id: str, run_id: str, user_id: str, answer: schemas.AnswerRequest) -> None:
        """Resume a paused run. Returns at once.

        LookupError: unknown run or another family. PermissionError: not the user who
        started it. ValueError: not waiting, or the answer does not fit the pending event.
        """
        run = self._get(family_id, run_id)
        if run.user_id != user_id:
            raise PermissionError("only the person who started this run can answer it")
        with run.cond:
            if run.status != "waiting" or run.pending is None:
                raise ValueError(f"run {run_id} is {run.status}, not waiting for an answer")
            expected = NEEDS_INPUT[run.pending]
            if answer.kind != expected:
                raise ValueError(f"run {run_id} is waiting for kind={expected!r}, got {answer.kind!r}")
            if answer.kind == "answer" and (answer.text is None or not answer.text.strip()):
                raise ValueError("an answer needs text")
            if answer.kind == "props" and answer.props is None:
                raise ValueError("a props review needs the props list")
            run.answer = answer
            run.pending = None
            run.status = "running"
            run.notify()

    def status(self, family_id: str, run_id: str) -> schemas.RunStatus:
        run = self._get(family_id, run_id)
        with run.cond:
            return run.status

    def events(self, family_id: str, run_id: str) -> list[Event]:
        """A snapshot of the run's events so far (tests and debugging)."""
        run = self._get(family_id, run_id)
        with run.cond:
            return list(run.events)

    def wait(self, family_id: str, run_id: str, timeout: float | None = None) -> schemas.RunStatus:
        """Block until the run is not running (waiting, done or error) or `timeout` passes. Tests only."""
        run = self._get(family_id, run_id)
        with run.cond:
            run.cond.wait_for(lambda: run.status != "running", timeout)
            return run.status

    def shutdown(self) -> None:
        """Stop every run in flight (each ends with an error event) and refuse new ones."""
        self._stop.set()
        with self._lock:
            runs = list(self._runs.values())
            threads = list(self._threads)
        for run in runs:
            with run.cond:
                run.notify()
        for thread in threads:
            thread.join(timeout=2.0)

    # ------------------------------------------------------------------ internals

    def _get(self, family_id: str, run_id: str) -> _Run:
        with self._lock:
            run = self._runs.get(run_id)
        if run is None or run.family_id != family_id:
            raise LookupError(f"run {run_id!r} not found")
        return run

    async def _follow(self, run: _Run) -> AsyncIterator[Event]:
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
                    return
                if not batch:
                    await wake.wait()
        finally:
            with run.cond:
                if (loop, wake) in run.subscribers:
                    run.subscribers.remove((loop, wake))

    def _emit(self, run: _Run, name: str, payload: BaseModel) -> None:
        expected = schemas.RUN_EVENTS[name]
        if not isinstance(payload, expected):
            raise TypeError(f"{name} needs a {expected.__name__}, got {type(payload).__name__}")
        with run.cond:
            if run.terminal:
                logger.warning("run %s: dropped %s after the run ended", run.run_id, name)
                return
            run.events.append((name, payload))
            if name in TERMINAL:
                run.status = name  # type: ignore[assignment]
            elif name in NEEDS_INPUT:
                run.status = "waiting"
                run.pending = name
            run.notify()

    def _step(self) -> None:
        """Pause between steps; wakes early (and stops the script) on shutdown."""
        stopping = self._stop.wait(self._step_seconds) if self._step_seconds > 0 else self._stop.is_set()
        if stopping:
            raise _Stopped

    def _ask(self, run: _Run, name: str, payload: BaseModel) -> schemas.AnswerRequest:
        """Emit a needs-input event and block this run's thread until it is answered."""
        self._emit(run, name, payload)
        with run.cond:
            run.cond.wait_for(lambda: run.answer is not None or self._stop.is_set())
            if run.answer is None:
                raise _Stopped
            answer, run.answer = run.answer, None
            return answer

    def _drive(self, run: _Run, db: Database) -> None:
        """Play the script. Never raises: any failure becomes the run's one error event."""
        try:
            if run.fields.memory_id is None:
                self._new_memory(run, db)
            else:
                self._second_telling(run, db)
        except _Stopped:
            self._emit(run, "error", schemas.ErrorEvent(message=STOPPED_MESSAGE))
        except Exception:  # noqa: BLE001 - every failure becomes the run's error event
            logger.exception("scripted run %s failed", run.run_id)
            self._emit(run, "error", schemas.ErrorEvent(message=ERROR_MESSAGE))
        else:
            with run.cond:
                ended = run.terminal
            if not ended:  # defensive: a script must always end the run
                self._emit(run, "error", schemas.ErrorEvent(message=ERROR_MESSAGE))

    # ------------------------------------------------------------------ the scripts

    @staticmethod
    def _plan(items: list[str], done: int) -> schemas.PlanEvent:
        """Items before `done` completed, item `done` in progress, the rest pending."""
        return schemas.PlanEvent(
            todos=[
                schemas.PlanItem(
                    text=text,
                    status="completed" if i < done else "in_progress" if i == done else "pending",
                )
                for i, text in enumerate(items)
            ]
        )

    def _new_memory(self, run: _Run, db: Database) -> None:
        fields = run.fields
        todos = ["Read the telling"]
        if self._ask_followup:
            todos.append("Ask one follow-up")
        todos += ["Find the props", "Stage the scene", "Narrate"]
        at = {name: i for i, name in enumerate(todos)}

        self._emit(run, "plan", self._plan(todos, 0))
        self._step()
        self._emit(run, "subagent_start", schemas.SubagentStartEvent(
            name="interviewer", task="Read the telling and find what is missing"))
        self._step()
        self._emit(run, "subagent_end", schemas.SubagentEndEvent(
            name="interviewer",
            summary="One follow-up: who else was there." if self._ask_followup else "The telling is complete.",
        ))
        self._step()

        text = fields.text
        if self._ask_followup:
            reply = self._ask(run, "question", schemas.QuestionEvent(text=FOLLOWUP_QUESTION))
            if reply.text and reply.text.strip():
                text = f"{text.rstrip()}\n\n{reply.text.strip()}"
            self._step()

        # Props: review the sample's prop, as this family's copy.
        sample = demo.load_sample()
        sample_prop = sample.props[0]
        prop_id = self._planned_id(props_db.get_prop, db, run.family_id, sample_prop.id)
        current = props_db.get_prop(db, run.family_id, prop_id)
        shown = current if current is not None and current.family_id == run.family_id else sample_prop
        self._emit(run, "plan", self._plan(todos, at["Find the props"]))
        self._step()
        self._emit(run, "subagent_start", schemas.SubagentStartEvent(
            name="prop_master", task="Find the props in the telling"))
        self._step()
        chip = schemas.PropChip(
            id=prop_id,
            name=shown.name,
            description=shown.description,
            size=shown.size,
            art=shown.art.get("body"),
            reused=True,
        )
        review = self._ask(run, "props_found", schemas.PropsFoundEvent(props=[chip]))
        edits = [e for e in (review.props or []) if e.id == prop_id]
        removed = any(e.action == "remove" for e in edits)
        self._emit(run, "subagent_end", schemas.SubagentEndEvent(
            name="prop_master",
            summary=f"Left out the {chip.name}." if removed else f"Reused the {chip.name}.",
        ))
        self._step()

        self._emit(run, "plan", self._plan(todos, at["Stage the scene"]))
        self._step()
        self._emit(run, "subagent_start", schemas.SubagentStartEvent(
            name="director", task="Stage the scene on the church steps"))
        self._step()
        self._emit(run, "subagent_end", schemas.SubagentEndEvent(
            name="director", summary="One shot, compiled without errors."))
        self._step()
        self._emit(run, "plan", self._plan(todos, at["Narrate"]))
        self._step()

        memory = self._save(run, db, sample, text, edits, removed)

        self._emit(run, "plan", self._plan(todos, len(todos)))
        self._step()
        self._emit(run, "scene_ready", schemas.SceneReadyEvent(memory_id=memory.id, version=memory.version))
        self._step()
        self._emit(run, "done", schemas.DoneEvent(memory_id=memory.id))

    def _second_telling(self, run: _Run, db: Database) -> None:
        fields = run.fields
        memory_id = fields.memory_id
        assert memory_id is not None
        todos = ["Read the telling", "Add it to the moment"]
        self._emit(run, "plan", self._plan(todos, 0))
        self._step()
        self._emit(run, "subagent_start", schemas.SubagentStartEvent(
            name="interviewer", task="Read the new telling of this moment"))
        self._step()
        telling = schemas.Telling(
            author_id=run.person_id,
            question=fields.question,
            text=fields.text,
            photo_ids=list(run.photo_ids),
        )
        if memories_db.add_telling(db, run.family_id, memory_id, telling) is None:
            raise LookupError(f"memory {memory_id!r} not found")
        memories_db.link_participants(
            db, run.family_id, memory_id, _dedupe([run.person_id, *fields.participant_ids])
        )
        self._emit(run, "subagent_end", schemas.SubagentEndEvent(
            name="interviewer", summary="Added the telling to the moment."))
        self._step()
        self._emit(run, "plan", self._plan(todos, len(todos)))
        self._step()
        self._emit(run, "done", schemas.DoneEvent(memory_id=memory_id))

    # ------------------------------------------------------------------ saving

    @staticmethod
    def _planned_id(get: Callable[[Database, str, str], Any], db: Database, family_id: str, sample_id: str) -> str:
        """The sample id if this family already has it, otherwise a family-specific copy id.

        Document ids are unique across families, so a family other than the one holding
        the sample id gets `<sampleId>_<familyId>`.
        """
        found = get(db, family_id, sample_id)
        if found is not None and found.family_id == family_id:
            return sample_id
        return f"{sample_id}_{family_id}"

    @staticmethod
    def _ensure(
        get: Callable[[Database, str, str], Any],
        insert: Callable[[str | None], Any],
        db: Database,
        family_id: str,
        doc_id: str,
    ) -> str:
        """Make sure the family has a copy under `doc_id` (or under a fresh id if that one is taken)."""
        found = get(db, family_id, doc_id)
        if found is not None and found.family_id == family_id:
            return doc_id
        try:
            return insert(doc_id).id
        except ValueError:  # the id belongs to another family
            return insert(None).id

    def _people_map(self, run: _Run, db: Database) -> dict[str, str]:
        """Sample person id -> this family's person id."""
        teller = run.person_id
        other = next((p for p in run.fields.participant_ids if p != teller), None)
        if other is None:
            people = people_db.list_people(db, run.family_id)
            me = next((p for p in people if p.id == teller), None)
            spouses = [s for s in (me.spouse_ids if me else []) if any(p.id == s for p in people)]
            others = [p.id for p in people if p.id != teller]
            other = next(iter(spouses + others), teller)
        return {SAMPLE_TELLER: teller, SAMPLE_OTHER: other}

    def _save(
        self,
        run: _Run,
        db: Database,
        sample: schemas.MemoryResponse,
        text: str,
        edits: list[schemas.PropEdit],
        removed: bool,
    ) -> schemas.Memory:
        fid = run.family_id
        fields = run.fields
        sample_memory = sample.memory
        assert sample_memory.screenplay is not None and sample_memory.timeline is not None

        people = self._people_map(run, db)
        participants = _dedupe([run.person_id, people[SAMPLE_OTHER], *fields.participant_ids])

        # The family's set (and prop, unless the family left it out).
        ids: dict[str, str] = dict(people)
        set_ids: list[str] = []
        for set_def in sample.sets:
            planned = self._planned_id(props_db.get_set, db, fid, set_def.id)
            ids[set_def.id] = self._ensure(
                props_db.get_set,
                lambda doc_id, s=set_def: props_db.insert_set(db, fid, s, set_id=doc_id),
                db, fid, planned,
            )
            set_ids.append(ids[set_def.id])
        prop_ids: list[str] = []
        sample_prop = sample.props[0]
        removed_ids: list[str] = []
        for prop in sample.props:
            if prop is sample_prop and removed:
                removed_ids.append(prop.id)
                continue
            planned = self._planned_id(props_db.get_prop, db, fid, prop.id)
            final = self._ensure(
                props_db.get_prop,
                lambda doc_id, p=prop: props_db.insert_prop(db, fid, p, prop_id=doc_id),
                db, fid, planned,
            )
            ids[prop.id] = final
            prop_ids.append(final)
            if prop is sample_prop:
                self._apply_edits(db, fid, final, edits)

        screenplay = sample_memory.screenplay.model_dump(mode="json", by_alias=True, exclude_none=True)
        timeline = sample_memory.timeline.model_dump(mode="json", by_alias=True, exclude_none=True)
        for pid in removed_ids:
            screenplay, timeline = strip_prop(screenplay, timeline, pid)

        memory = memories_db.create_memory(
            db,
            fid,
            title=fields.title or short_title(fields.text),
            year=fields.year,
            participant_ids=participants,
            telling=schemas.Telling(
                author_id=run.person_id,
                question=fields.question,
                text=text,
                photo_ids=list(run.photo_ids),
            ),
            prop_ids=prop_ids,
            set_ids=set_ids,
        )

        screenplay = remap_ids(screenplay, ids)
        timeline = remap_ids(timeline, ids)
        screenplay["memoryId"] = memory.id
        screenplay["cast"] = _dedupe(screenplay.get("cast", []))
        screenplay["props"] = _dedupe(screenplay.get("props", []))
        saved = memories_db.save_scene(
            db,
            fid,
            memory.id,
            screenplay=Screenplay.model_validate(screenplay),
            timeline=Timeline.model_validate(timeline),
            prop_ids=prop_ids,
            set_ids=set_ids,
        )
        if saved is None:
            raise LookupError(f"memory {memory.id!r} vanished before its scene was saved")
        return saved

    @staticmethod
    def _apply_edits(db: Database, family_id: str, prop_id: str, edits: list[schemas.PropEdit]) -> None:
        for edit in edits:
            if edit.action == "rename" and edit.name and edit.name.strip():
                props_db.update_prop(db, family_id, prop_id, schemas.PropPatch(name=edit.name.strip()))
            elif edit.action == "replace_photo" and edit.photo_id:
                if media_db.media_exists(db, family_id, edit.photo_id):
                    props_db.update_prop(db, family_id, prop_id, schemas.PropPatch(photo_id=edit.photo_id))
                else:
                    logger.warning("prop %s: ignored replace_photo with unknown photo %s", prop_id, edit.photo_id)


# --------------------------------------------------------------------------- install


def install_fake_runs(*, step_seconds: float = 0.6) -> bool:
    """Put a ScriptedRunManager behind `agent.runs` (via its `configure`). True on success.

    Returns False, with a warning, when agent.runs cannot be imported or has no callable
    `configure`; the run routes then answer 503 as usual.
    """
    try:
        runs = importlib.import_module("agent.runs")
    except Exception as exc:  # noqa: BLE001 - any import failure means no scripted runs
        logger.warning("scripted runs not installed: agent.runs cannot be imported (%s)", exc)
        return False
    configure = getattr(runs, "configure", None)
    if not callable(configure):
        logger.warning("scripted runs not installed: agent.runs has no callable configure()")
        return False
    configure(ScriptedRunManager(step_seconds=step_seconds))
    logger.info("agent runs are scripted (seed.fake_runs), step %.2fs", step_seconds)
    return True


__all__ = ["ScriptedRunManager", "install_fake_runs", "remap_ids", "short_title", "strip_prop"]
