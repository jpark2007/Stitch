"""agent/runs.py: start, stream (with replay), answer/resume and event translation.

Every run drives a real `studio.build_studio` graph with ScriptedChatModel,
InMemorySaver, InMemoryStore and InMemoryRepo (seeded from the hymn_books sample):
no keys, no network. The scripted studio writes the sample screenplay and timeline
with write_file and saves them with the real `save_memory`.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import threading
import time
from collections.abc import Callable, Iterator
from typing import Annotated, Any

import pytest
from deepagents.backends.utils import create_file_data, file_data_to_string
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.tools import BaseTool, InjectedToolCallId, tool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.prebuilt import InjectedState
from langgraph.store.base import BaseStore
from langgraph.store.memory import InMemoryStore
from langgraph.types import Command
from pydantic import BaseModel

import schemas
from agent import runs, studio
from agent.fake_llm import ScriptedChatModel, last_tool_message, system_prompt_of, tool_call, tool_calls
from agent.repo import SAMPLE_MEMORY_PATH, InMemoryRepo, load_sample_family
from agent.run_info import RunInfo
from agent.tools.ask import INTERRUPT_ON, ask_teller
from agent.tools.family import make_family_tools

FAMILY = "fam_runs"
OTHER_FAMILY = "fam_other"
USER = "user_dad"
OTHER_USER = "user_mom"

STUDIO_PROMPT = "You are the STUDIO."
INTERVIEWER_PROMPT = "You are the INTERVIEWER."
OTHER_INTERVIEWER_PROMPT = "You are the SECOND INTERVIEWER."

SAMPLE = schemas.MemoryResponse.model_validate_json(SAMPLE_MEMORY_PATH.read_text())
SAMPLE_RAW = json.loads(SAMPLE_MEMORY_PATH.read_text())
SCREENPLAY_JSON = json.dumps(SAMPLE_RAW["memory"]["screenplay"])
TIMELINE_JSON = json.dumps(SAMPLE_RAW["memory"]["timeline"])

WAIT = 20.0

Responder = Callable[[list[BaseMessage], list[str]], AIMessage]


# ------------------------------------------------------------------ stub tools


@tool
def explode() -> str:
    """A tool that always fails."""
    raise RuntimeError("the paper tore")


@tool
def read_drafts(state: Annotated[dict, InjectedState]) -> str:
    """Report which drafts exist and the memory id inside /screenplay.json."""
    files = state.get("files") or {}
    screenplay = json.loads(file_data_to_string(files["/screenplay.json"])) if "/screenplay.json" in files else {}
    timeline = json.loads(file_data_to_string(files["/timeline.json"])) if "/timeline.json" in files else {}
    return json.dumps({
        "files": sorted(files),
        "memoryId": screenplay.get("memoryId"),
        "shots": len(screenplay.get("shots", [])),
        "narrators": sorted((timeline.get("narrations") or {}).keys()),
    })


@tool
def voice_mom(state: Annotated[dict, InjectedState], tool_call_id: Annotated[str, InjectedToolCallId]) -> Command:
    """Stand-in for the voice pass: add mom's lines to /screenplay.json (narrations['mom'])
    and the matching narration track to /timeline.json, as a second telling does."""
    screenplay = json.loads(file_data_to_string(state["files"]["/screenplay.json"]))
    screenplay.setdefault("narrations", {})["mom"] = SAMPLE_RAW["memory"]["screenplay"]["narrations"]["mom"]
    timeline = json.loads(file_data_to_string(state["files"]["/timeline.json"]))
    timeline["narrations"]["mom"] = SAMPLE_RAW["memory"]["timeline"]["narrations"]["mom"]
    return Command(update={
        "files": {
            "/screenplay.json": create_file_data(json.dumps(screenplay)),
            "/timeline.json": create_file_data(json.dumps(timeline)),
        },
        "messages": [ToolMessage(content="voiced mom", tool_call_id=tool_call_id, name="voice_mom")],
    })


@tool
def emit_noise() -> str:
    """Write custom chunks: one compile_error, plus ones the run must ignore."""
    from langgraph.config import get_stream_writer

    write = get_stream_writer()
    error = {"code": "not_holding", "shot": "shot1", "t": 1.0, "message": "dad cannot hand_to"}
    write({"event": "compile_error", "data": {"errors": [error], "round": 1}})
    write({"event": "not_an_event", "data": {}})
    write({"event": "done", "data": {"memoryId": "forged"}})
    write({"event": "frames", "data": {"frames": "not a list"}})
    write("not even a dict")
    return "noise made"


INTERVIEWER = {
    "name": "interviewer",
    "description": "Decides whether the story can be staged; asks the teller when it cannot.",
    "system_prompt": INTERVIEWER_PROMPT,
    "tools": [ask_teller],
}
OTHER_INTERVIEWER = {**INTERVIEWER, "name": "second_interviewer", "system_prompt": OTHER_INTERVIEWER_PROMPT}


# ------------------------------------------------------------------ scripts


def tool_results(messages: list[BaseMessage]) -> int:
    return sum(isinstance(m, ToolMessage) for m in messages)


def script(*steps: Callable[[list[BaseMessage]], AIMessage] | AIMessage) -> Callable[[list[BaseMessage]], AIMessage]:
    """An agent that plays `steps` in order, one per tool result seen so far."""

    def play(messages: list[BaseMessage]) -> AIMessage:
        step = steps[min(tool_results(messages), len(steps) - 1)]
        return step(messages) if callable(step) else step

    return play


def todos(*items: tuple[str, str]) -> AIMessage:
    return tool_call("write_todos", {"todos": [{"content": c, "status": s} for c, s in items]})


def write(path: str, content: str) -> AIMessage:
    return tool_call("write_file", {"file_path": path, "content": content})


def save() -> AIMessage:
    return tool_call("save_memory", {"title": "Hymn books down the pew", "participant_ids": ["dad", "mom"]})


def say(text: str) -> AIMessage:
    return AIMessage(content=text)


def make_responder(studio_script: Callable[[list[BaseMessage]], AIMessage],
                   interviewer: Callable[[list[BaseMessage]], AIMessage] | None = None,
                   second: Callable[[list[BaseMessage]], AIMessage] | None = None) -> Responder:
    def responder(messages: list[BaseMessage], tool_names: list[str]) -> AIMessage:
        prompt = system_prompt_of(messages)
        if prompt.startswith(STUDIO_PROMPT):
            return studio_script(messages)
        if prompt.startswith(INTERVIEWER_PROMPT) and interviewer is not None:
            return interviewer(messages)
        if prompt.startswith(OTHER_INTERVIEWER_PROMPT) and second is not None:
            return second(messages)
        raise AssertionError(f"unexpected agent: {prompt[:60]!r}")

    return responder


def ask(question: str) -> Callable[[list[BaseMessage]], AIMessage]:
    def step(messages: list[BaseMessage]) -> AIMessage:
        return tool_call("ask_teller", {"question": question})

    return step


def report(messages: list[BaseMessage]) -> AIMessage:
    last = last_tool_message(messages)
    return say(f"the teller said: {last.text if last else '-'}")


NEW_MEMORY_STUDIO = script(
    todos(("Ask about the setting", "in_progress"), ("Stage and save", "pending")),
    tool_call("task", {"subagent_type": "interviewer", "description": "Can the hymn-book story be staged?"}),
    todos(("Ask about the setting", "completed"), ("Stage and save", "in_progress")),
    todos(("Ask about the setting", "completed"), ("Stage and save", "in_progress")),  # identical: no new plan
    write("/screenplay.json", SCREENPLAY_JSON),
    write("/timeline.json", TIMELINE_JSON),
    save(),
    say("Saved the hymn-book scene."),
)

STRAIGHT_STUDIO = script(
    todos(("Stage and save", "in_progress")),
    write("/screenplay.json", SCREENPLAY_JSON),
    write("/timeline.json", TIMELINE_JSON),
    save(),
    say("Saved."),
)


# ------------------------------------------------------------------ harness


class Harness:
    def __init__(self, responder: Responder, *, extra_tools: list[BaseTool] | None = None,
                 subagents: list[dict[str, Any]] | None = None, keep_runs: bool = False,
                 store: BaseStore | None = None) -> None:
        self.repo = InMemoryRepo()
        load_sample_family(self.repo, FAMILY)
        load_sample_family(self.repo, OTHER_FAMILY)
        self.saver = InMemorySaver()
        self.store = store if store is not None else InMemoryStore()
        self.model = ScriptedChatModel(responder=responder)
        self.extra_tools = extra_tools or []
        self.subagents = subagents if subagents is not None else [INTERVIEWER]
        self.built: list[RunInfo] = []
        self.graphs: list[Any] = []
        # keep_runs: the manager writes its run records into the same store the studio uses.
        self.keep_runs = keep_runs
        self.manager = self.new_manager()
        self.retired: list[runs.RunManager] = []

    def new_manager(self) -> runs.RunManager:
        if self.keep_runs:
            return runs.RunManager(repo=self.repo, build_graph=self.build, store=self.store)
        return runs.RunManager(repo=self.repo, build_graph=self.build)

    def restart(self) -> runs.RunManager:
        """Simulate an API restart: a new RunManager over the same checkpointer, store and repo.

        The old manager's workers finish first (a crash test's worker ends with the crash),
        so it never writes to the thread or the record again."""
        self.manager.shutdown(wait=True)
        self.retired.append(self.manager)
        self.manager = self.new_manager()
        return self.manager

    def build(self, run: RunInfo):
        self.built.append(run)
        save_memory = next(t for t in make_family_tools(self.repo, run) if t.name == "save_memory")
        graph = studio.build_studio(
            family_id=run.family_id,
            model=self.model,
            tools=[ask_teller, save_memory, *self.extra_tools],
            subagents=self.subagents,
            interrupt_on=INTERRUPT_ON,
            checkpointer=self.saver,
            store=self.store,
            system_prompt=STUDIO_PROMPT,
        )
        self.graphs.append(graph)
        return graph

    def start(self, person_id: str = "dad", **fields: Any) -> str:
        form = schemas.SubmitMemoryFields(**{"text": "Dad passed the hymn books down the pew.", "year": 1962, **fields})
        return self.manager.start_run(FAMILY, USER, person_id, form, ["photo_1"])

    def wait(self, run_id: str) -> schemas.RunStatus:
        status = self.manager.wait(FAMILY, run_id, timeout=WAIT)
        assert status != "running", f"run still running after {WAIT}s: {self.names(run_id)}"
        return status

    def names(self, run_id: str) -> list[str]:
        return [name for name, _ in self.manager.events(FAMILY, run_id)]

    def answer(self, run_id: str, text: str, user: str = USER) -> None:
        self.manager.answer_run(FAMILY, run_id, user, schemas.AnswerRequest(kind="answer", text=text))


@pytest.fixture
def harnesses() -> Iterator[list[Harness]]:
    made: list[Harness] = []
    yield made
    for h in made:
        h.manager.shutdown(wait=True)
        for old in h.retired:
            old.shutdown(wait=True)


def collect(manager: runs.RunManager, run_id: str, family_id: str = FAMILY) -> list[tuple[str, BaseModel]]:
    async def go() -> list[tuple[str, BaseModel]]:
        return [event async for event in manager.stream_run(family_id, run_id)]

    return asyncio.run(asyncio.wait_for(go(), WAIT))


def dumped(events: list[tuple[str, BaseModel]]) -> list[tuple[str, dict[str, Any]]]:
    return [(name, model.model_dump(mode="json", by_alias=True)) for name, model in events]


def assert_typed(events: list[tuple[str, BaseModel]]) -> None:
    for name, model in events:
        assert name in schemas.RUN_EVENTS
        assert isinstance(model, schemas.RUN_EVENTS[name]), (name, type(model))


# ------------------------------------------------------------------ the main run


def test_new_memory_run_pauses_on_one_question_resumes_and_saves(harnesses: list[Harness]) -> None:
    h = Harness(make_responder(NEW_MEMORY_STUDIO, interviewer=script(ask("Which church was it?"), report)))
    harnesses.append(h)
    run_id = h.start(title=None)

    assert h.wait(run_id) == "waiting"
    assert h.names(run_id) == ["plan", "subagent_start", "question"]
    plan, start, question = (m for _, m in h.manager.events(FAMILY, run_id))
    assert plan == schemas.PlanEvent(todos=[
        schemas.PlanItem(text="Ask about the setting", status="in_progress"),
        schemas.PlanItem(text="Stage and save", status="pending"),
    ])
    assert start == schemas.SubagentStartEvent(name="interviewer", task="Can the hymn-book story be staged?")
    assert question == schemas.QuestionEvent(text="Which church was it?")

    # The run context reached the studio as JSON in its first message.
    context = json.loads(h.model.calls[0][1][1].content)
    assert context == {
        "authorId": "dad", "authorName": SAMPLE.people[0].name, "text": "Dad passed the hymn books down the pew.",
        "year": 1962, "title": None, "question": None, "photoIds": ["photo_1"], "participantIds": [], "memoryId": None,
    }
    assert h.built[0].run_id == run_id and h.built[0].author_id == "dad" and h.built[0].photo_ids == ("photo_1",)

    h.answer(run_id, "St. Mark's, in Trenton.")
    assert h.wait(run_id) == "done"

    events = h.manager.events(FAMILY, run_id)
    assert_typed(events)
    assert [n for n, _ in events] == [
        "plan", "subagent_start", "question", "subagent_end", "plan", "scene_ready", "done",
    ]
    end = events[3][1]
    assert end.name == "interviewer" and "St. Mark's, in Trenton." in (end.summary or "")
    assert events[4][1].todos[0].status == "completed"
    scene_ready, done = events[5][1], events[6][1]
    assert done == schemas.DoneEvent(memory_id=scene_ready.memory_id)
    assert scene_ready.version == 1

    memory = h.repo.get_memory(FAMILY, done.memory_id)
    assert memory is not None and memory.timeline is not None
    assert memory.timeline == SAMPLE.memory.timeline
    assert memory.tellings[0].author_id == "dad"
    assert memory.tellings[0].photo_ids == ["photo_1"]

    # The answer resumed the same thread: the graph was built once, and its one
    # checkpoint thread holds the question, the answer and the save.
    assert len(h.graphs) == 1
    values = h.graphs[0].get_state(studio.thread_config(run_id)).values
    tool_names = [m.name for m in values["messages"] if isinstance(m, ToolMessage)]
    assert tool_names.count("task") == 1 and "save_memory" in tool_names
    assert values["messages"][-1].content == "Saved the hymn-book scene."


def test_late_and_concurrent_subscribers_get_the_full_sequence(harnesses: list[Harness]) -> None:
    gate = threading.Event()

    def gated(messages: list[BaseMessage], tool_names: list[str]) -> AIMessage:
        assert gate.wait(WAIT), "gate never opened"
        return STRAIGHT_STUDIO(messages)

    h = Harness(gated)
    harnesses.append(h)
    run_id = h.start()

    async def two_subscribers() -> tuple[list[Any], list[Any]]:
        async def follow() -> list[Any]:
            return [e async for e in h.manager.stream_run(FAMILY, run_id)]

        tasks = [asyncio.create_task(follow()) for _ in range(2)]
        await asyncio.sleep(0.05)  # both subscribed before anything happened
        gate.set()
        first, second = await asyncio.wait_for(asyncio.gather(*tasks), WAIT)
        return first, second

    first, second = asyncio.run(two_subscribers())
    assert [n for n, _ in first] == ["plan", "scene_ready", "done"]
    assert dumped(first) == dumped(second)
    assert_typed(first)

    late = collect(h.manager, run_id)
    assert dumped(late) == dumped(first) == dumped(h.manager.events(FAMILY, run_id))


def test_a_subscriber_stays_open_across_a_question(harnesses: list[Harness]) -> None:
    h = Harness(make_responder(NEW_MEMORY_STUDIO, interviewer=script(ask("Which church?"), report)))
    harnesses.append(h)
    run_id = h.start()

    async def follow_and_answer() -> list[tuple[str, BaseModel]]:
        seen: list[tuple[str, BaseModel]] = []
        async for name, model in h.manager.stream_run(FAMILY, run_id):
            seen.append((name, model))
            if name == "question":
                # Answer straight from the event, as the web does; the run may still be closing its stream.
                h.answer(run_id, "St. Mark's.")
        return seen

    seen = asyncio.run(asyncio.wait_for(follow_and_answer(), WAIT))
    assert [n for n, _ in seen][-3:] == ["plan", "scene_ready", "done"]
    assert dumped(seen) == dumped(h.manager.events(FAMILY, run_id))


# ------------------------------------------------------------------ answering


def test_answer_rules(harnesses: list[Harness]) -> None:
    h = Harness(make_responder(NEW_MEMORY_STUDIO, interviewer=script(ask("Which church?"), report)))
    harnesses.append(h)
    run_id = h.start()
    good = schemas.AnswerRequest(kind="answer", text="St. Mark's.")

    with pytest.raises(runs.RunNotFound):
        h.manager.answer_run(FAMILY, "no-such-run", USER, good)
    with pytest.raises(runs.RunNotFound):
        h.manager.answer_run(OTHER_FAMILY, run_id, USER, good)
    assert h.wait(run_id) == "waiting"
    with pytest.raises(runs.NotRunOwner):
        h.manager.answer_run(FAMILY, run_id, OTHER_USER, good)

    # The wrong kind of answer is refused and the run keeps waiting.
    with pytest.raises(ValueError, match="does not match"):
        h.manager.answer_run(FAMILY, run_id, USER, schemas.AnswerRequest(kind="props", props=[]))
    with pytest.raises(ValueError, match="needs text"):
        h.manager.answer_run(FAMILY, run_id, USER, schemas.AnswerRequest(kind="answer", text="  "))
    assert h.manager.status(FAMILY, run_id) == "waiting"
    assert h.names(run_id) == ["plan", "subagent_start", "question"]

    h.manager.answer_run(FAMILY, run_id, USER, good)
    assert h.wait(run_id) == "done"
    with pytest.raises(runs.RunNotWaiting):
        h.manager.answer_run(FAMILY, run_id, USER, good)
    assert h.names(run_id)[-1] == "done"


def test_parallel_questions_are_asked_one_at_a_time(harnesses: list[Harness]) -> None:
    studio_script = script(
        tool_calls(
            ("task", {"subagent_type": "interviewer", "description": "Ask about the church."}),
            ("task", {"subagent_type": "second_interviewer", "description": "Ask about the year."}),
        ),
        say("unreachable: both task results arrive in the same step"),
        write("/screenplay.json", SCREENPLAY_JSON),
        write("/timeline.json", TIMELINE_JSON),
        save(),
        say("Saved."),
    )
    h = Harness(
        make_responder(studio_script, interviewer=script(ask("Which church?"), report),
                       second=script(ask("Which year?"), report)),
        subagents=[INTERVIEWER, OTHER_INTERVIEWER],
    )
    harnesses.append(h)
    run_id = h.start()

    assert h.wait(run_id) == "waiting"
    questions = [m.text for n, m in h.manager.events(FAMILY, run_id) if n == "question"]
    assert len(questions) == 1  # two pending, one shown
    first = questions[0]
    h.answer(run_id, f"answer to {first}")

    assert h.wait(run_id) == "waiting"  # the other one surfaces again after the resume
    questions = [m.text for n, m in h.manager.events(FAMILY, run_id) if n == "question"]
    assert len(questions) == 2 and set(questions) == {"Which church?", "Which year?"}
    h.answer(run_id, f"answer to {questions[1]}")

    assert h.wait(run_id) == "done"
    events = h.manager.events(FAMILY, run_id)
    ends = {m.name: m.summary for n, m in events if n == "subagent_end"}
    assert "answer to Which church?" in ends["interviewer"]
    assert "answer to Which year?" in ends["second_interviewer"]
    assert [n for n, _ in events][-2:] == ["scene_ready", "done"]


def test_a_question_is_answerable_while_a_parallel_branch_is_still_working(harnesses: list[Harness]) -> None:
    # The props sub-agent pauses on its review while the sets sub-agent is still painting:
    # the run is waiting at once, and the answer is taken without blocking.
    release = threading.Event()

    def slow_sibling(messages: list[BaseMessage]) -> AIMessage:
        assert release.wait(WAIT), "the sibling was never released"
        return say("It was 1962.")

    studio_script = script(
        tool_calls(
            ("task", {"subagent_type": "interviewer", "description": "Ask about the church."}),
            ("task", {"subagent_type": "second_interviewer", "description": "Work out the year."}),
        ),
        say("unreachable: both task results arrive in the same step"),
        write("/screenplay.json", SCREENPLAY_JSON),
        write("/timeline.json", TIMELINE_JSON),
        save(),
        say("Saved."),
    )
    h = Harness(
        make_responder(studio_script, interviewer=script(ask("Which church?"), report), second=slow_sibling),
        subagents=[INTERVIEWER, OTHER_INTERVIEWER],
    )
    harnesses.append(h)
    run_id = h.start()
    seen_at_answer: dict[str, Any] = {}

    async def follow_and_answer() -> list[tuple[str, BaseModel]]:
        seen: list[tuple[str, BaseModel]] = []
        async for name, model in h.manager.stream_run(FAMILY, run_id):
            seen.append((name, model))
            if name == "question":
                seen_at_answer["status"] = h.manager.status(FAMILY, run_id)
                seen_at_answer["names"] = [n for n, _ in seen]
                began = time.monotonic()
                h.answer(run_id, "St. Mark's, in Trenton.")
                seen_at_answer["took"] = time.monotonic() - began
                seen_at_answer["after"] = h.manager.status(FAMILY, run_id)
                with pytest.raises(runs.RunNotWaiting):  # already answered; the resume is queued
                    h.answer(run_id, "again")
                release.set()
        return seen

    seen = asyncio.run(asyncio.wait_for(follow_and_answer(), WAIT))

    assert seen_at_answer["status"] == "waiting"
    assert "subagent_end" not in seen_at_answer["names"]  # the sibling was still working
    assert seen_at_answer["took"] < 0.5
    assert seen_at_answer["after"] == "running"
    assert_typed(seen)
    names = [n for n, _ in seen]
    assert names.count("question") == 1
    assert names[-2:] == ["scene_ready", "done"]
    ends = {m.name: m.summary for n, m in seen if n == "subagent_end"}
    assert "St. Mark's, in Trenton." in ends["interviewer"]
    assert "1962" in ends["second_interviewer"]
    assert dumped(seen) == dumped(h.manager.events(FAMILY, run_id))
    assert len(h.graphs) == 1  # resumed on the same graph and thread


# ------------------------------------------------------------------ bookkeeping


def test_finished_runs_drop_their_graph_and_are_forgotten_after_the_ttl(harnesses: list[Harness]) -> None:
    h = Harness(make_responder(STRAIGHT_STUDIO))
    harnesses.append(h)
    first = h.start()
    assert h.wait(first) == "done"
    assert h.manager._runs[first].graph is None  # nothing resumes a finished run

    # Within the TTL a finished run is still replayable, even after other runs start.
    second = h.start()
    assert collect(h.manager, first)[-1][0] == "done"
    assert h.wait(second) == "done"

    h.manager.finished_run_ttl = 0
    third = h.start()  # starting a run forgets the expired ones
    for gone in (first, second):
        with pytest.raises(runs.RunNotFound):
            h.manager.status(FAMILY, gone)
    assert h.wait(third) == "done"
    assert h.names(third) == ["plan", "scene_ready", "done"]


# ------------------------------------------------------------------ access


def test_stream_from_another_family_raises_and_yields_nothing(harnesses: list[Harness]) -> None:
    h = Harness(make_responder(STRAIGHT_STUDIO))
    harnesses.append(h)
    run_id = h.start()
    assert h.wait(run_id) == "done"

    for family_id, rid in ((OTHER_FAMILY, run_id), (FAMILY, "no-such-run")):
        yielded: list[Any] = []

        async def iterate(family_id: str = family_id, rid: str = rid, yielded: list[Any] = yielded) -> None:
            async for event in h.manager.stream_run(family_id, rid):
                yielded.append(event)

        with pytest.raises(runs.RunNotFound):
            asyncio.run(iterate())
        assert yielded == []
    for call in (h.manager.status, h.manager.events):
        with pytest.raises(runs.RunNotFound):
            call(OTHER_FAMILY, run_id)


def test_start_run_validates_person_and_memory(harnesses: list[Harness]) -> None:
    h = Harness(make_responder(STRAIGHT_STUDIO))
    harnesses.append(h)
    with pytest.raises(ValueError, match="person"):
        h.start(person_id="grandpa")
    with pytest.raises(ValueError, match="person"):
        h.manager.start_run(FAMILY, USER, "nobody", schemas.SubmitMemoryFields(text="x", year=1962), [])
    with pytest.raises(runs.MemoryNotFound):
        h.start(memory_id="memory_404")

    # A memory of another family is not found either.
    seeded = SAMPLE.memory.model_copy(update={"family_id": OTHER_FAMILY, "id": "memory_other"})
    h.repo.save_memory(OTHER_FAMILY, seeded)
    with pytest.raises(runs.MemoryNotFound):
        h.start(memory_id="memory_other")

    # A memory without a scene yet cannot take a second telling.
    bare = SAMPLE.memory.model_copy(update={"family_id": FAMILY, "id": "memory_bare", "screenplay": None, "timeline": None})
    h.repo.save_memory(FAMILY, bare)
    with pytest.raises(runs.MemoryNotFound):
        h.start(memory_id="memory_bare")
    assert h.built == []  # nothing was started


# ------------------------------------------------------------------ second telling


def seed_memory_told_by_dad(repo: InMemoryRepo) -> str:
    memory = SAMPLE.memory.model_copy(deep=True, update={"family_id": FAMILY, "id": "memory_hymns"})
    memory.screenplay = memory.screenplay.model_copy(update={"memory_id": "memory_hymns"})
    if memory.screenplay.narrations:
        memory.screenplay.narrations.pop("mom", None)
    memory.timeline.narrations.pop("mom", None)
    memory.tellings = [t for t in memory.tellings if t.author_id == "dad"]
    memory = schemas.Memory.model_validate(memory.model_dump(mode="json", by_alias=True))
    repo.save_memory(FAMILY, memory)
    return memory.id


def test_second_telling_starts_with_the_scene_and_adds_a_narration(harnesses: list[Harness]) -> None:
    studio_script = script(
        tool_call("read_drafts", {}),
        tool_call("voice_mom", {}),
        save(),
        say("Added mom's telling."),
    )
    h = Harness(make_responder(studio_script), extra_tools=[read_drafts, voice_mom])
    harnesses.append(h)
    memory_id = seed_memory_told_by_dad(h.repo)
    before = h.repo.get_memory(FAMILY, memory_id)
    assert before is not None and "mom" not in before.timeline.narrations

    run_id = h.start(person_id="mom", memory_id=memory_id, text="I remember the organ, not the books.")
    assert h.wait(run_id) == "done"

    read = next(m for _, msgs in h.model.calls for m in msgs if isinstance(m, ToolMessage) and m.name == "read_drafts")
    drafts = json.loads(read.text)
    assert drafts["files"] == ["/screenplay.json", "/timeline.json"]
    assert drafts["memoryId"] == memory_id
    assert drafts["shots"] == len(before.screenplay.shots)
    assert drafts["narrators"] == ["dad"]

    context = json.loads(h.model.calls[0][1][1].content)
    assert context["memoryId"] == memory_id and context["authorId"] == "mom"

    events = h.manager.events(FAMILY, run_id)
    assert_typed(events)
    assert [n for n, _ in events] == ["scene_ready", "done"]
    assert events[0][1] == schemas.SceneReadyEvent(memory_id=memory_id, version=before.version + 1)
    assert events[1][1] == schemas.DoneEvent(memory_id=memory_id)

    after = h.repo.get_memory(FAMILY, memory_id)
    assert after is not None and after.version == before.version + 1
    assert [t.author_id for t in after.tellings] == ["dad", "mom"]
    assert sorted(after.timeline.narrations) == ["dad", "mom"]
    assert after.screenplay.narrations is not None and "mom" in after.screenplay.narrations
    assert after.timeline.tracks == before.timeline.tracks  # the shared action is untouched
    assert len(h.repo.find_memories(FAMILY)) == 1  # no new memory


# ------------------------------------------------------------------ failures


def test_a_tool_that_raises_ends_in_one_error(harnesses: list[Harness]) -> None:
    h = Harness(make_responder(script(todos(("Break", "in_progress")), tool_call("explode", {}), say("never"))),
                extra_tools=[explode])
    harnesses.append(h)
    run_id = h.start()
    assert h.wait(run_id) == "error"
    events = h.manager.events(FAMILY, run_id)
    assert_typed(events)
    names = [n for n, _ in events]
    assert names.count("error") == 1 and names[-1] == "error" and "done" not in names
    # The family sees the kind of failure only; the exception's text stays in the log.
    assert events[-1][1].message == runs.RUN_ERROR.format(kind="RuntimeError")
    assert "the paper tore" not in events[-1][1].message
    assert collect(h.manager, run_id)[-1][0] == "error"  # a subscriber ends too
    with pytest.raises(runs.RunNotWaiting):
        h.answer(run_id, "anything")


def test_a_studio_that_never_saves_ends_in_error(harnesses: list[Harness]) -> None:
    h = Harness(make_responder(script(todos(("Think", "in_progress")), say("I could not find the church."))))
    harnesses.append(h)
    run_id = h.start()
    assert h.wait(run_id) == "error"
    events = h.manager.events(FAMILY, run_id)
    assert [n for n, _ in events] == ["plan", "error"]
    assert events[-1][1].message == "the studio finished without saving a scene: I could not find the church."


def test_a_question_the_web_cannot_show_goes_back_to_the_model(harnesses: list[Harness]) -> None:
    # Not shown and not an error by itself: the model is told why and gives up here, so
    # the run ends because nothing was saved (more in test_agent_runs_malformed_pause.py).
    h = Harness(make_responder(script(tool_call("ask_teller", {"question": "   "}), say("never"))))
    harnesses.append(h)
    run_id = h.start()
    assert h.wait(run_id) == "error"
    names = h.names(run_id)
    assert names == ["error"]
    assert h.manager.events(FAMILY, run_id)[0][1].message == "the studio finished without saving a scene: never"
    rejected = last_tool_message(h.model.calls[-1][1])
    assert rejected is not None and rejected.name == "ask_teller" and "not shown to the family" in rejected.text


def test_a_graph_that_cannot_be_built_is_an_error(harnesses: list[Harness]) -> None:
    def broken(run: RunInfo):
        raise RuntimeError("no model configured")

    h = Harness(make_responder(STRAIGHT_STUDIO))
    h.manager = runs.RunManager(repo=h.repo, build_graph=broken)
    harnesses.append(h)
    run_id = h.start()
    assert h.wait(run_id) == "error"
    assert dumped(h.manager.events(FAMILY, run_id)) == [
        ("error", {"message": "the studio ran into a problem (RuntimeError); please try again"}),
    ]


def test_tool_custom_events_pass_through_and_reserved_ones_are_ignored(harnesses: list[Harness]) -> None:
    studio_script = script(
        tool_call("emit_noise", {}),
        write("/screenplay.json", SCREENPLAY_JSON),
        write("/timeline.json", TIMELINE_JSON),
        save(),
        say("Saved."),
    )
    h = Harness(make_responder(studio_script), extra_tools=[emit_noise])
    harnesses.append(h)
    run_id = h.start()
    assert h.wait(run_id) == "done"
    events = h.manager.events(FAMILY, run_id)
    assert_typed(events)
    assert [n for n, _ in events] == ["compile_error", "scene_ready", "done"]
    assert events[0][1].errors[0].message == "dad cannot hand_to"
    assert events[-1][1].memory_id != "forged"


# ------------------------------------------------------------------ the event loop


def test_start_run_inside_a_running_loop_returns_at_once(harnesses: list[Harness]) -> None:
    gate = threading.Event()

    def slow(messages: list[BaseMessage], tool_names: list[str]) -> AIMessage:
        assert gate.wait(WAIT), "gate never opened"
        return STRAIGHT_STUDIO(messages)

    h = Harness(slow)
    harnesses.append(h)

    async def main() -> tuple[float, list[tuple[str, BaseModel]]]:
        began = time.monotonic()
        form = schemas.SubmitMemoryFields(text="Dad passed the hymn books down the pew.", year=1962)
        run_id = await h.manager.start_run_async(FAMILY, USER, "dad", form, ["photo_1"])
        took = time.monotonic() - began
        assert h.manager.status(FAMILY, run_id) == "running"
        # The loop stays free while the run works: a timer still fires.
        await asyncio.sleep(0.01)
        gate.set()
        return took, [e async for e in h.manager.stream_run(FAMILY, run_id)]

    took, events = asyncio.run(asyncio.wait_for(main(), WAIT))
    assert took < 1.0
    assert [n for n, _ in events] == ["plan", "scene_ready", "done"]


SLOW = 0.2
"""How long the slow store takes to write a run record, and the slow repo to read."""


class SlowStore(InMemoryStore):
    """An InMemoryStore whose run-record writes take SLOW seconds (the studio's own
    namespaces stay fast). It remembers what each finished write held."""

    def __init__(self, delay: float = SLOW) -> None:
        super().__init__()
        self.delay = delay
        self.lock = threading.Lock()
        self.in_flight = 0
        # run id -> event count of the last record whose put has returned.
        self.written_count: dict[str, int] = {}
        # (status, event count) of every finished run-record write, in order.
        self.writes: list[tuple[str, int]] = []
        # Called with the record as each run-record write starts (on the writing thread).
        self.on_put: Callable[[dict[str, Any]], None] | None = None
        # The thread of every run-record write, in order.
        self.put_threads: list[threading.Thread] = []

    def put(self, namespace: tuple[str, ...], key: str, value: dict[str, Any], *args: Any, **kwargs: Any) -> None:
        if namespace[:1] != (runs.RUNS_NAMESPACE,):
            return super().put(namespace, key, value, *args, **kwargs)
        with self.lock:
            self.in_flight += 1
            self.put_threads.append(threading.current_thread())
        try:
            if self.on_put is not None:
                self.on_put(value)
            time.sleep(self.delay)
            super().put(namespace, key, value, *args, **kwargs)
            with self.lock:
                self.written_count[key] = len(value["events"])
                self.writes.append((value["status"], len(value["events"])))
        finally:
            with self.lock:
                self.in_flight -= 1


class SlowRepo:
    """The harness repo, with get_person and get_memory taking SLOW seconds (a database read)."""

    def __init__(self, inner: InMemoryRepo, delay: float = SLOW) -> None:
        self.inner = inner
        self.delay = delay
        self.read_threads: list[threading.Thread] = []  # the thread of every slow read

    def get_person(self, *args: Any, **kwargs: Any) -> Any:
        self.read_threads.append(threading.current_thread())
        time.sleep(self.delay)
        return self.inner.get_person(*args, **kwargs)

    def get_memory(self, *args: Any, **kwargs: Any) -> Any:
        self.read_threads.append(threading.current_thread())
        time.sleep(self.delay)
        return self.inner.get_memory(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)


def slow_harness(responder: Responder, *, store: InMemoryStore | None = None, delay: float = SLOW,
                 **kwargs: Any) -> Harness:
    """A Harness whose manager writes run records to a slow store and reads a slow repo."""
    h = Harness(responder, keep_runs=True, store=store if store is not None else SlowStore(delay), **kwargs)
    h.manager.shutdown(wait=True)
    h.manager = runs.RunManager(repo=SlowRepo(h.repo, delay), build_graph=h.build,  # type: ignore[arg-type]
                                store=h.store)
    return h


FORM = schemas.SubmitMemoryFields(text="Dad passed the hymn books down the pew.", year=1962)


def test_the_event_loop_never_waits_on_the_store_or_the_repo(harnesses: list[Harness]) -> None:
    # Regression: start_run read the repo and wrote the first record on the loop, and every
    # record write happened with run.cond held, so stream_run stalled the loop behind it.
    # The structural checks (which thread did each slow call, and whether it held the run's
    # lock) are exact; the gap check backs them up with a margin that scheduler jitter on a
    # busy machine does not reach (a stall is at least `delay`).
    delay = 0.5
    h = slow_harness(make_responder(STRAIGHT_STUDIO), delay=delay)
    harnesses.append(h)
    runs.configure(h.manager)
    store, repo = h.store, h.manager._repo
    assert isinstance(store, SlowStore) and isinstance(repo, SlowRepo)
    held_lock_while_writing: list[str] = []

    def check_lock(record: dict[str, Any]) -> None:
        run = h.manager._runs.get(record["run_id"])
        if run is not None and run.cond._is_owned():  # type: ignore[attr-defined]
            held_lock_while_writing.append(record["status"])

    store.on_put = check_lock
    loop_threads: list[threading.Thread] = []

    async def main() -> tuple[list[float], list[tuple[str, BaseModel]]]:
        loop_threads.append(threading.current_thread())
        gaps: list[float] = []
        stop = asyncio.Event()

        async def ticker() -> None:
            last = time.monotonic()
            while not stop.is_set():
                await asyncio.sleep(0.01)
                now = time.monotonic()
                gaps.append(now - last)
                last = now

        ticking = asyncio.create_task(ticker())
        await asyncio.sleep(0.05)
        pending = runs.start_run(FAMILY, USER, "dad", FORM, ["photo_1"])
        assert inspect.isawaitable(pending)
        run_id = await pending
        events = [e async for e in runs.stream_run(FAMILY, run_id)]
        stop.set()
        await ticking
        return gaps, events

    try:
        gaps, events = asyncio.run(asyncio.wait_for(main(), WAIT))
    finally:
        runs.configure(None)
    assert [n for n, _ in events] == ["plan", "scene_ready", "done"]
    assert len(store.writes) >= 4  # the start, three events: each write took `delay` seconds
    assert repo.read_threads, "the slow repo was never read"
    assert loop_threads[0] not in store.put_threads, "a run record was written on the event loop"
    assert loop_threads[0] not in repo.read_threads, "the repo was read on the event loop"
    assert held_lock_while_writing == [], "a run record was written with the run's lock held"
    assert max(gaps) < 0.8 * delay, f"the event loop stalled for {max(gaps):.3f}s"


def test_no_event_reaches_a_subscriber_before_a_record_holding_it_is_stored(harnesses: list[Harness]) -> None:
    store = SlowStore()
    h = slow_harness(make_responder(NEW_MEMORY_STUDIO, interviewer=script(ask("Which church?"), report)), store=store)
    harnesses.append(h)
    run_id = h.start()

    async def follow() -> list[tuple[str, int, int]]:
        seen: list[tuple[str, int, int]] = []
        async for name, _ in h.manager.stream_run(FAMILY, run_id):
            with store.lock:
                stored_count = store.written_count.get(run_id, 0)
            seen.append((name, len(seen) + 1, stored_count))
            if name == "question":
                # As the route does: answer_run is sync and runs off the loop.
                await asyncio.to_thread(h.answer, run_id, "St. Mark's.")
        return seen

    seen = asyncio.run(asyncio.wait_for(follow(), WAIT))
    assert [name for name, _, _ in seen] == QUESTION_RUN
    for name, position, stored_count in seen:
        assert stored_count >= position, f"{name} (event {position}) was yielded with only {stored_count} stored"
    assert stored(h, run_id)["status"] == "done"


def test_a_subscriber_joining_during_a_slow_write_gets_everything_once(harnesses: list[Harness]) -> None:
    store = SlowStore()
    h = slow_harness(make_responder(STRAIGHT_STUDIO), store=store)
    harnesses.append(h)
    writing_plan = threading.Event()
    writing_done = threading.Event()

    def on_put(record: dict[str, Any]) -> None:
        names = [n for n, _ in record["events"]]
        if names == ["plan"]:
            writing_plan.set()
        if record["status"] == "done":
            writing_done.set()

    store.on_put = on_put
    run_id = h.start()

    async def follow() -> list[tuple[str, BaseModel]]:
        return [e async for e in h.manager.stream_run(FAMILY, run_id)]

    async def main() -> tuple[list[Any], list[Any], dict[str, Any]]:
        at_join: dict[str, Any] = {}
        assert await asyncio.to_thread(writing_plan.wait, WAIT)
        with store.lock:
            at_join["plan_write_in_flight"] = store.in_flight
        during_plan = asyncio.create_task(follow())
        assert await asyncio.to_thread(writing_done.wait, WAIT)
        with store.lock:
            at_join["done_write_in_flight"] = store.in_flight
        # The run has ended in memory, but its last event is not stored yet.
        at_join["status"] = h.manager.status(FAMILY, run_id)
        at_join["visible"] = [n for n, _ in h.manager.events(FAMILY, run_id)]
        during_done = asyncio.create_task(follow())
        first, second = await asyncio.wait_for(asyncio.gather(during_plan, during_done), WAIT)
        return first, second, at_join

    first, second, at_join = asyncio.run(main())
    assert at_join["plan_write_in_flight"] >= 1 and at_join["done_write_in_flight"] >= 1
    assert at_join["status"] == "done" and "done" not in at_join["visible"]
    for events in (first, second):
        assert_typed(events)
        names = [n for n, _ in events]
        assert names == ["plan", "scene_ready", "done"]  # in order, no gaps, no duplicates
        assert names.count("done") == 1
    assert dumped(first) == dumped(second) == dumped(h.manager.events(FAMILY, run_id))


class LateLock:
    """A run's write_lock that makes every thread but the studio worker wait `delay`
    seconds before taking it: the answer's snapshot then reaches the store after the
    worker has written newer ones."""

    def __init__(self, delay: float) -> None:
        self.inner = threading.Lock()
        self.delay = delay
        self.delayed = threading.Event()

    def __enter__(self) -> bool:
        if not threading.current_thread().name.startswith("studio-run"):
            self.delayed.set()
            time.sleep(self.delay)
        return self.inner.__enter__()

    def __exit__(self, *exc: Any) -> None:
        self.inner.__exit__(*exc)


def test_a_late_answer_write_never_leaves_an_older_record(harnesses: list[Harness]) -> None:
    # answer_run and the worker both write the record: the answer (queued while a parallel
    # branch works) snapshots first but writes last. The newer records must win.
    release = threading.Event()

    def slow_sibling(messages: list[BaseMessage]) -> AIMessage:
        assert release.wait(WAIT), "the sibling was never released"
        return say("It was 1962.")

    studio_script = script(
        tool_calls(
            ("task", {"subagent_type": "interviewer", "description": "Ask about the church."}),
            ("task", {"subagent_type": "second_interviewer", "description": "Work out the year."}),
        ),
        say("unreachable: both task results arrive in the same step"),
        write("/screenplay.json", SCREENPLAY_JSON),
        write("/timeline.json", TIMELINE_JSON),
        save(),
        say("Saved."),
    )
    store = SlowStore(delay=0.0)
    h = slow_harness(
        make_responder(studio_script, interviewer=script(ask("Which church?"), report), second=slow_sibling),
        store=store, subagents=[INTERVIEWER, OTHER_INTERVIEWER],
    )
    harnesses.append(h)
    run_id = h.start()
    run = h.manager._runs[run_id]
    late = LateLock(delay=1.0)
    run.write_lock = late  # type: ignore[assignment]

    async def follow_and_answer() -> list[tuple[str, BaseModel]]:
        seen: list[tuple[str, BaseModel]] = []
        answering: asyncio.Task[None] | None = None
        async for name, model in h.manager.stream_run(FAMILY, run_id):
            seen.append((name, model))
            if name == "question":
                answering = asyncio.create_task(asyncio.to_thread(h.answer, run_id, "St. Mark's."))
                assert await asyncio.to_thread(late.delayed.wait, WAIT)  # its snapshot is taken
                release.set()
        assert answering is not None
        await answering  # the answer's (older) write has finished, or was skipped
        return seen

    seen = asyncio.run(asyncio.wait_for(follow_and_answer(), WAIT))
    assert h.wait(run_id) == "done"
    assert [n for n, _ in seen][-2:] == ["scene_ready", "done"]
    record = stored(h, run_id)
    assert record is not None
    with run.cond:
        assert record["status"] == run.status == "done"
        assert len(record["events"]) == len(run.events) == len(seen)
        assert record == run.record()
    assert store.writes[-1] == ("done", len(seen))


def test_a_stale_snapshot_is_never_written_over_a_newer_one() -> None:
    store = SlowStore(delay=0.0)
    info = RunInfo(run_id="run_stale", family_id=FAMILY, user_id=USER, author_id="dad", fields=FORM, photo_ids=())
    run = runs._Run(info=info, config={}, store=store)
    plan = schemas.PlanEvent(todos=[schemas.PlanItem(text="Stage", status="in_progress")])
    with run.cond:
        run.append("plan", plan)
        older = run.snapshot()
        run.append("compile_error", schemas.CompileErrorEvent(errors=[], round=1))
        newer = run.snapshot()
        assert run.published == 0  # nothing is visible before it is stored
    assert older is not None and newer is not None and newer[0] > older[0]

    run.write(newer)
    run.write(older)  # arrives late: skipped
    assert store.writes == [("running", 2)]
    assert [n for n, _ in stored_record(store, "run_stale")["events"]] == ["plan", "compile_error"]
    with run.cond:
        assert run.published == 2 and run.saved
    run.flush()  # nothing changed since: no write
    assert len(store.writes) == 1


def stored_record(store: BaseStore, run_id: str) -> dict[str, Any]:
    item = store.get(runs.run_namespace(FAMILY), run_id)
    assert item is not None
    return dict(item.value)


def test_module_start_run_is_sync_outside_a_loop_and_awaitable_inside(
    harnesses: list[Harness], monkeypatch: pytest.MonkeyPatch
) -> None:
    h = Harness(make_responder(STRAIGHT_STUDIO))
    harnesses.append(h)
    runs.configure(h.manager)
    try:
        run_id = runs.start_run(FAMILY, USER, "dad", FORM, [])
        assert isinstance(run_id, str) and run_id
        assert h.wait(run_id) == "done"
        with pytest.raises(ValueError, match="person"):
            runs.start_run(FAMILY, USER, "grandpa", FORM, [])
        with pytest.raises(runs.MemoryNotFound):
            runs.start_run(FAMILY, USER, "dad", FORM.model_copy(update={"memory_id": "memory_404"}), [])

        async def inside() -> str:
            pending = runs.start_run(FAMILY, USER, "dad", FORM, [])
            assert inspect.isawaitable(pending)
            started = await pending
            assert isinstance(started, str) and started
            unknown = runs.start_run(FAMILY, USER, "grandpa", FORM, [])
            assert inspect.isawaitable(unknown)  # nothing raised yet
            with pytest.raises(ValueError, match="person"):
                await unknown
            missing = runs.start_run(FAMILY, USER, "dad", FORM.model_copy(update={"memory_id": "memory_404"}), [])
            with pytest.raises(runs.MemoryNotFound):
                await missing
            return started

        started = asyncio.run(asyncio.wait_for(inside(), WAIT))
        assert h.wait(started) == "done"
        assert len(h.built) == 2  # the refused submits started nothing
    finally:
        runs.configure(None)

    # Not configured: raised at once outside a loop (see the delegate test), on await inside.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    async def unconfigured() -> None:
        pending = runs.start_run(FAMILY, USER, "dad", FORM, [])
        assert inspect.isawaitable(pending)
        with pytest.raises(RuntimeError, match="not configured"):
            await pending

    asyncio.run(unconfigured())


# ------------------------------------------------------------------ surviving a restart


class ProcessDied(BaseException):
    """Stands in for the API process dying mid-step: not an Exception, so nothing in the run catches it."""


def crash_once(step: Callable[[list[BaseMessage]], AIMessage], when: Callable[[list[BaseMessage]], bool]):
    """`step`, except that the first call matching `when` kills the 'process' instead."""
    crashed = threading.Event()

    def play(messages: list[BaseMessage]) -> AIMessage:
        if not crashed.is_set() and when(messages):
            crashed.set()
            raise ProcessDied("the API process stopped")
        return step(messages)

    play.crashed = crashed  # type: ignore[attr-defined]
    return play


def stored(h: Harness, run_id: str, family_id: str = FAMILY) -> dict[str, Any] | None:
    item = h.store.get(runs.run_namespace(family_id), run_id)
    return None if item is None else dict(item.value)


def wait_stored_status(h: Harness, run_id: str, status: str) -> dict[str, Any]:
    deadline = time.monotonic() + WAIT
    while time.monotonic() < deadline:
        record = stored(h, run_id)
        if record is not None and record["status"] == status:
            return record
        time.sleep(0.01)
    raise AssertionError(f"the record of {run_id} never reached {status}: {stored(h, run_id)}")


QUESTION_RUN = ["plan", "subagent_start", "question", "subagent_end", "plan", "scene_ready", "done"]


def mongo_store() -> Iterator[BaseStore]:
    """A MongoDBStore on the MongoDB at MONGODB_URI_TEST, in a throwaway collection."""
    import os
    import uuid

    uri = os.environ.get("MONGODB_URI_TEST")
    if not uri:
        # studio.mongo_persistence has no mongomock path, and MongoDBStore cannot run on
        # mongomock (it calls create_index with arguments mongomock does not take).
        pytest.skip("MONGODB_URI_TEST is not set")
    from langgraph.store.mongodb import MongoDBStore
    from pymongo import MongoClient

    client: MongoClient = MongoClient(uri, serverSelectionTimeoutMS=3000)
    collection = client["stitch_test"][f"agent_store_{uuid.uuid4().hex[:12]}"]
    try:
        yield MongoDBStore(collection=collection)
    finally:
        collection.drop()
        client.close()


@pytest.fixture(params=["memory", "mongo"])
def run_store(request: pytest.FixtureRequest) -> Iterator[BaseStore]:
    if request.param == "memory":
        yield InMemoryStore()
    else:
        yield from mongo_store()


def test_a_run_waiting_on_a_question_survives_a_restart(harnesses: list[Harness], run_store: BaseStore) -> None:
    h = Harness(make_responder(NEW_MEMORY_STUDIO, interviewer=script(ask("Which church was it?"), report)),
                keep_runs=True, store=run_store)
    harnesses.append(h)
    run_id = h.start()
    assert h.wait(run_id) == "waiting"
    before = dumped(h.manager.events(FAMILY, run_id))
    assert [n for n, _ in before] == ["plan", "subagent_start", "question"]

    record = stored(h, run_id)
    assert record is not None
    assert record["status"] == "waiting" and record["user_id"] == USER and record["author_id"] == "dad"
    assert record["family_id"] == FAMILY and record["photo_ids"] == ["photo_1"]
    assert json.loads(record["fields"])["text"] == "Dad passed the hymn books down the pew."
    assert [[n, json.loads(p)] for n, p in record["events"]] == [[n, p] for n, p in before]

    manager = h.restart()
    assert manager.status(FAMILY, run_id) == "waiting"
    assert len(h.graphs) == 2  # the studio was rebuilt for the restored run

    refused: list[BaseException] = []

    async def follow_and_answer() -> list[tuple[str, BaseModel]]:
        seen: list[tuple[str, BaseModel]] = []
        async for name, model in manager.stream_run(FAMILY, run_id):
            seen.append((name, model))
            if name == "question":
                try:
                    manager.answer_run(FAMILY, run_id, OTHER_USER,
                                       schemas.AnswerRequest(kind="answer", text="Not my run."))
                except runs.NotRunOwner as exc:
                    refused.append(exc)
                manager.answer_run(FAMILY, run_id, USER,
                                   schemas.AnswerRequest(kind="answer", text="St. Mark's, in Trenton."))
        return seen

    seen = asyncio.run(asyncio.wait_for(follow_and_answer(), WAIT))
    assert len(refused) == 1
    assert_typed(seen)
    assert dumped(seen)[:3] == before  # replayed first
    assert [n for n, _ in seen] == QUESTION_RUN
    assert dumped(seen) == dumped(manager.events(FAMILY, run_id))
    end = seen[3][1]
    assert end.name == "interviewer"  # the sub-agent's name came back with the record
    assert "St. Mark's, in Trenton." in (end.summary or "")

    done = seen[-1][1]
    memory = h.repo.get_memory(FAMILY, done.memory_id)
    assert memory is not None and memory.timeline == SAMPLE.memory.timeline

    # The answer resumed the same thread the first process started.
    values = h.graphs[1].get_state(studio.thread_config(run_id)).values
    tool_names = [m.name for m in values["messages"] if isinstance(m, ToolMessage)]
    assert tool_names.count("task") == 1 and "save_memory" in tool_names
    assert stored(h, run_id)["status"] == "done"


def test_a_finished_run_replays_from_the_store_after_a_restart(harnesses: list[Harness]) -> None:
    h = Harness(make_responder(STRAIGHT_STUDIO), keep_runs=True)
    harnesses.append(h)
    run_id = h.start()
    assert h.wait(run_id) == "done"
    original = dumped(h.manager.events(FAMILY, run_id))
    assert [n for n, _ in original] == ["plan", "scene_ready", "done"]

    # The process forgetting a finished run keeps its record.
    h.manager.finished_run_ttl = 0
    other = h.start()
    assert run_id not in h.manager._runs
    assert dumped(collect(h.manager, run_id)) == original
    assert h.wait(other) == "done"

    manager = h.restart()
    graphs = len(h.graphs)
    assert dumped(collect(manager, run_id)) == original
    assert manager.status(FAMILY, run_id) == "done"
    assert len(h.graphs) == graphs  # a replay builds no studio
    with pytest.raises(runs.RunNotWaiting):
        h.answer(run_id, "anything")
    assert [n for n, _ in collect(manager, other)] == ["plan", "scene_ready", "done"]


def test_an_errored_run_replays_its_error_after_a_restart(harnesses: list[Harness]) -> None:
    h = Harness(make_responder(script(todos(("Break", "in_progress")), tool_call("explode", {}), say("never"))),
                extra_tools=[explode], keep_runs=True)
    harnesses.append(h)
    run_id = h.start()
    assert h.wait(run_id) == "error"
    original = dumped(h.manager.events(FAMILY, run_id))
    manager = h.restart()
    replay = collect(manager, run_id)
    assert dumped(replay) == original and replay[-1][0] == "error"
    assert replay[-1][1].message == runs.RUN_ERROR.format(kind="RuntimeError")


def test_run_records_are_isolated_per_family(harnesses: list[Harness]) -> None:
    h = Harness(make_responder(NEW_MEMORY_STUDIO, interviewer=script(ask("Which church?"), report)), keep_runs=True)
    harnesses.append(h)
    run_id = h.start()
    assert h.wait(run_id) == "waiting"
    assert stored(h, run_id) is not None
    assert stored(h, run_id, OTHER_FAMILY) is None

    manager = h.restart()
    good = schemas.AnswerRequest(kind="answer", text="St. Mark's.")
    with pytest.raises(runs.RunNotFound):
        manager.answer_run(OTHER_FAMILY, run_id, USER, good)
    for call in (manager.status, manager.events):
        with pytest.raises(runs.RunNotFound):
            call(OTHER_FAMILY, run_id)
    yielded: list[Any] = []

    async def iterate() -> None:
        async for event in manager.stream_run(OTHER_FAMILY, run_id):
            yielded.append(event)

    with pytest.raises(runs.RunNotFound):
        asyncio.run(iterate())
    assert yielded == []
    assert len(h.graphs) == 1  # nothing was restored for the other family

    # Its own family still finds it, and it finishes.
    assert manager.status(FAMILY, run_id) == "waiting"
    manager.answer_run(FAMILY, run_id, USER, good)
    assert h.wait(run_id) == "done"
    # Once this process holds it, the other family still cannot see it.
    with pytest.raises(runs.RunNotFound):
        manager.status(OTHER_FAMILY, run_id)


def test_a_run_cut_short_mid_step_is_driven_on_after_a_restart(
    harnesses: list[Harness], caplog: pytest.LogCaptureFixture
) -> None:
    # The first process dies while the studio is writing the timeline (after the plan).
    studio_script = crash_once(STRAIGHT_STUDIO, lambda messages: tool_results(messages) == 2)
    h = Harness(make_responder(studio_script), keep_runs=True)
    harnesses.append(h)
    run_id = h.start()
    assert studio_script.crashed.wait(WAIT)
    record = wait_stored_status(h, run_id, "running")
    assert [n for n, _ in record["events"]] == ["plan"]

    caplog.set_level("WARNING", logger="agent.runs")
    manager = h.restart()
    events = collect(manager, run_id)
    assert_typed(events)
    assert [n for n, _ in events] == ["plan", "scene_ready", "done"]  # no event says it resumed
    assert any("resuming it from its last checkpoint" in r.getMessage() for r in caplog.records)
    memory = h.repo.get_memory(FAMILY, events[-1][1].memory_id)
    assert memory is not None and memory.timeline == SAMPLE.memory.timeline
    # Same thread: the screenplay write from before the restart was not repeated.
    values = h.graphs[-1].get_state(studio.thread_config(run_id)).values
    writes = [m for m in values["messages"] if isinstance(m, ToolMessage) and m.name == "write_file"]
    assert len(writes) == 2
    assert stored(h, run_id)["status"] == "done"


def test_an_accepted_answer_is_not_lost_when_the_process_dies_resuming(harnesses: list[Harness]) -> None:
    # The interviewer has the teller's answer, then the process dies before it can report.
    interviewer = crash_once(script(ask("Which church?"), report), lambda messages: tool_results(messages) == 1)
    h = Harness(make_responder(NEW_MEMORY_STUDIO, interviewer=interviewer), keep_runs=True)
    harnesses.append(h)
    run_id = h.start()
    assert h.wait(run_id) == "waiting"
    h.answer(run_id, "St. Mark's, in Trenton.")
    assert interviewer.crashed.wait(WAIT)
    record = wait_stored_status(h, run_id, "running")
    assert record["answered"] is not None

    manager = h.restart()
    events = collect(manager, run_id)
    assert [n for n, _ in events] == QUESTION_RUN  # not asked twice
    end = events[3][1]
    assert end.name == "interviewer" and "St. Mark's, in Trenton." in (end.summary or "")


def test_a_question_recorded_before_its_checkpoint_is_not_asked_twice(harnesses: list[Harness]) -> None:
    # The record says waiting, but the process died before the step's writes (the
    # interrupt) reached the checkpointer: the restored run drives on, the thread asks
    # the same question again, and the log keeps one question.
    h = Harness(make_responder(NEW_MEMORY_STUDIO, interviewer=script(ask("Which church?"), report)), keep_runs=True)
    harnesses.append(h)
    run_id = h.start()
    assert h.wait(run_id) == "waiting"
    h.manager.shutdown(wait=True)
    before = dumped(h.manager.events(FAMILY, run_id))
    # Lose the interrupt the last root step wrote (InMemorySaver keeps pending writes per
    # (thread, namespace, checkpoint id)); the sub-agent's own checkpoints stay.
    config = studio.thread_config(run_id)
    latest = h.graphs[0].get_state(config).config["configurable"]["checkpoint_id"]
    pending = h.saver.writes[(run_id, "", latest)]
    lost = [key for key, write in pending.items() if write[1] == "__interrupt__"]
    assert lost
    for key in lost:
        del pending[key]
    assert h.graphs[0].get_state(config).interrupts == ()

    manager = h.restart()
    assert manager.wait(FAMILY, run_id, timeout=WAIT) == "waiting"
    assert dumped(manager.events(FAMILY, run_id)) == before
    h.answer(run_id, "St. Mark's.")
    assert h.wait(run_id) == "done"
    assert h.names(run_id) == QUESTION_RUN


def test_unreadable_or_misfiled_records_are_not_found(harnesses: list[Harness]) -> None:
    h = Harness(make_responder(STRAIGHT_STUDIO), keep_runs=True)
    harnesses.append(h)
    run_id = h.start()
    assert h.wait(run_id) == "done"
    record = stored(h, run_id)
    assert record is not None
    # A copy of the record filed under the other family still names this family: not found there.
    h.store.put(runs.run_namespace(OTHER_FAMILY), run_id, record)
    h.store.put(runs.run_namespace(FAMILY), "garbled", {"run_id": "garbled", "status": "done"})
    h.store.put(runs.run_namespace(FAMILY), "odd_status", {**record, "run_id": "odd_status", "status": "paused"})

    manager = h.restart()
    with pytest.raises(runs.RunNotFound):
        manager.status(OTHER_FAMILY, run_id)
    for bad in ("garbled", "odd_status"):
        with pytest.raises(runs.RunNotFound):
            manager.status(FAMILY, bad)
    assert manager.status(FAMILY, run_id) == "done"


def test_a_manager_without_a_store_keeps_runs_in_process(harnesses: list[Harness]) -> None:
    h = Harness(make_responder(NEW_MEMORY_STUDIO, interviewer=script(ask("Which church?"), report)))
    harnesses.append(h)
    run_id = h.start()
    assert h.wait(run_id) == "waiting"
    assert h.store.search((runs.RUNS_NAMESPACE,)) == []  # nothing written anywhere
    manager = h.restart()
    with pytest.raises(runs.RunNotFound):
        manager.status(FAMILY, run_id)
    with pytest.raises(runs.RunNotFound):
        h.answer(run_id, "St. Mark's.")


# ------------------------------------------------------------------ module seam


def test_module_functions_delegate_to_the_configured_manager(
    harnesses: list[Harness], monkeypatch: pytest.MonkeyPatch
) -> None:
    # Without a key, get_manager() cannot build the production manager (never a real one in tests).
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    runs.configure(None)
    with pytest.raises(RuntimeError, match="not configured"):
        runs.get_manager()
    with pytest.raises(RuntimeError, match="not configured"):
        runs.start_run(FAMILY, USER, "dad", schemas.SubmitMemoryFields(text="x", year=1962), [])

    h = Harness(make_responder(NEW_MEMORY_STUDIO, interviewer=script(ask("Which church?"), report)))
    harnesses.append(h)
    runs.configure(h.manager)
    try:
        assert runs.get_manager() is h.manager
        run_id = runs.start_run(FAMILY, USER, "dad", schemas.SubmitMemoryFields(text="x", year=1962), [])
        assert h.wait(run_id) == "waiting"
        runs.answer_run(FAMILY, run_id, USER, schemas.AnswerRequest(kind="answer", text="St. Mark's."))
        assert h.wait(run_id) == "done"

        async def follow() -> list[tuple[str, BaseModel]]:
            return [e async for e in runs.stream_run(FAMILY, run_id)]

        assert dumped(asyncio.run(follow())) == dumped(h.manager.events(FAMILY, run_id))
    finally:
        runs.configure(None)


def test_public_interface() -> None:
    for name in ("RunNotFound", "MemoryNotFound", "NotRunOwner", "RunNotWaiting", "GraphFactory", "RunManager",
                 "configure", "get_manager", "start_run", "stream_run", "answer_run"):
        assert hasattr(runs, name), name
    assert issubclass(runs.RunNotFound, LookupError)
    assert issubclass(runs.MemoryNotFound, LookupError)
    assert issubclass(runs.NotRunOwner, PermissionError)
    assert issubclass(runs.RunNotWaiting, RuntimeError)
    # routes/memories.py answers 409 for a ValueError from answer_run.
    assert issubclass(runs.RunNotWaiting, ValueError)
    assert inspect.isasyncgenfunction(runs.RunManager.stream_run)
