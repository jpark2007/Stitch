"""agent/runs.py: the per-run time limit (RunLimits.run_timeout_s, STITCH_RUN_TIMEOUT_S).

Only running time counts: a worker driving the run while its status is 'running'. Time
waiting on the teller, and time the API process was down, does not. Past the limit the run
ends with exactly one error event (RUN_TIMEOUT_ERROR), and the limit and the time used
travel in the run record, so a restarted process keeps both.

Same approach as test_agent_runs.py (the real `studio.build_studio` with ScriptedChatModel,
InMemorySaver, InMemoryStore and InMemoryRepo; no keys, no network), plus a fake clock the
scripted model (or a hook on the graph's get_state) advances. Nothing sleeps in a
real-time loop, and every wait has a timeout.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import threading
import time
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from langchain_core.messages import AIMessage, BaseMessage
from pydantic import BaseModel

import schemas
from agent import runs, studio
from agent.fake_llm import tool_call, tool_calls
from agent.guards import ENV_RUN_TIMEOUT_S, RunLimits
from agent.run_info import RunInfo
from test_agent_runs import (  # pytest puts tests/agent on sys.path (no __init__.py)
    FAMILY,
    INTERVIEWER,
    NEW_MEMORY_STUDIO,
    OTHER_INTERVIEWER,
    SCREENPLAY_JSON,
    STRAIGHT_STUDIO,
    TIMELINE_JSON,
    WAIT,
    Harness,
    Responder,
    ask,
    assert_typed,
    collect,
    crash_once,
    dumped,
    make_responder,
    report,
    save,
    say,
    script,
    stored,
    todos,
    tool_results,
    wait_stored_status,
    write,
)

LIMIT = 600.0
"""The time limit of the runs here, in seconds: 10 minutes."""
TIMEOUT_MESSAGE = "the studio took too long on this story (over 10 minutes); please try again"

Step = Callable[[list[BaseMessage]], AIMessage] | AIMessage


class FakeClock:
    """A monotonic clock that only moves when a test (or the scripted model) moves it."""

    def __init__(self, start: float = 5000.0) -> None:
        self._now = start
        self._lock = threading.Lock()

    def __call__(self) -> float:
        with self._lock:
            return self._now

    def advance(self, seconds: float) -> None:
        with self._lock:
            self._now += seconds


class HookedGraph:
    """The studio graph, with a one-shot hook run before the next get_state (_settle reads
    the state right after a stream closes, so the hook lands between the two)."""

    def __init__(self, graph: Any, harness: ClockedHarness) -> None:
        self._graph = graph
        self._harness = harness

    def stream(self, *args: Any, **kwargs: Any) -> Any:
        self._harness.streams += 1
        return self._graph.stream(*args, **kwargs)

    def get_state(self, *args: Any, **kwargs: Any) -> Any:
        hook, self._harness.before_get_state = self._harness.before_get_state, None
        if hook is not None:
            hook()
        return self._graph.get_state(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._graph, name)


class ClockedHarness(Harness):
    """A Harness whose managers measure the time limit with a fake clock."""

    def __init__(self, responder: Responder, *, clock: FakeClock, run_timeout_s: float | None = LIMIT,
                 **kwargs: Any) -> None:
        self.clock = clock
        self.run_timeout_s = run_timeout_s  # what the next manager gets (restart() reads it)
        self.before_get_state: Callable[[], None] | None = None
        self.streams = 0  # graph.stream calls: one per start or resume
        super().__init__(responder, **kwargs)

    def new_manager(self) -> runs.RunManager:
        return runs.RunManager(repo=self.repo, build_graph=self.build, store=self.store if self.keep_runs else None,
                               run_timeout_s=self.run_timeout_s, clock=self.clock)

    def build(self, run: RunInfo) -> Any:
        return HookedGraph(super().build(run), self)

    def run(self, run_id: str) -> runs._Run:
        return self.manager._runs[run_id]


@pytest.fixture
def harnesses() -> Iterator[list[Harness]]:
    made: list[Harness] = []
    yield made
    for h in made:
        h.manager.shutdown(wait=True)
        for old in h.retired:
            old.shutdown(wait=True)


def advancing(
    clock: FakeClock, seconds: float, step: Step, *, once: bool = False
) -> Callable[[list[BaseMessage]], AIMessage]:
    """`step`, after moving the clock by `seconds`, as if the model call took that long.
    `once`: only the first call moves it (a later run of the same script is quick)."""
    done = threading.Event()

    def play(messages: list[BaseMessage]) -> AIMessage:
        if not (once and done.is_set()):
            done.set()
            clock.advance(seconds)
        return step(messages) if callable(step) else step

    return play


def errors(events: list[tuple[str, BaseModel]]) -> list[str]:
    return [model.message for name, model in events if name == "error"]  # type: ignore[attr-defined]


def answer(h: Harness, run_id: str, text: str = "St. Mark's, in Trenton.") -> None:
    h.answer(run_id, text)


# ------------------------------------------------------------------ the message and the seam


def test_the_message_rounds_the_limit_up_to_whole_minutes() -> None:
    assert runs.RUN_TIMEOUT_ERROR == "the studio took too long on this story (over {minutes} minutes); please try again"
    assert runs.run_timeout_message(LIMIT) == TIMEOUT_MESSAGE
    assert runs.run_timeout_message(1200.0) == runs.RUN_TIMEOUT_ERROR.format(minutes=20)
    assert runs.run_timeout_message(61.0) == runs.RUN_TIMEOUT_ERROR.format(minutes=2)
    assert runs.run_timeout_message(5.0) == runs.RUN_TIMEOUT_ERROR.format(minutes=1)


def test_run_manager_takes_the_limit_and_the_clock_as_keyword_only_parameters() -> None:
    params = inspect.signature(runs.RunManager.__init__).parameters
    assert params["run_timeout_s"].kind is inspect.Parameter.KEYWORD_ONLY
    assert params["run_timeout_s"].default is None
    assert params["clock"].kind is inspect.Parameter.KEYWORD_ONLY
    assert params["clock"].default is time.monotonic
    for bad in (0, -5.0, float("nan"), float("inf")):
        with pytest.raises(ValueError, match="run_timeout_s"):
            runs.RunManager(repo=None, build_graph=lambda info: None, run_timeout_s=bad)  # type: ignore[arg-type]


# ------------------------------------------------------------------ (a) past the limit mid-stream


def test_a_run_past_its_limit_ends_with_one_timeout_error_and_replays_it(
    harnesses: list[Harness], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING, logger="agent.runs")
    clock = FakeClock()
    # Writing the screenplay "takes" longer than the whole limit; the next chunk ends the run.
    studio_script = script(
        todos(("Stage and save", "in_progress")),
        advancing(clock, LIMIT + 1, write("/screenplay.json", SCREENPLAY_JSON), once=True),
        write("/timeline.json", TIMELINE_JSON),
        save(),
        say("Saved."),
    )
    h = ClockedHarness(make_responder(studio_script), clock=clock, keep_runs=True)
    harnesses.append(h)
    run_id = h.start()

    assert h.wait(run_id) == "error"
    events = collect(h.manager, run_id)
    assert_typed(events)
    assert [n for n, _ in events] == ["plan", "error"]  # one error, no scene_ready, no done
    assert errors(events) == [TIMEOUT_MESSAGE]
    assert dumped(events) == dumped(h.manager.events(FAMILY, run_id))
    assert dumped(collect(h.manager, run_id)) == dumped(events)  # a late subscriber gets the same

    with h.run(run_id).cond:
        run = h.run(run_id)
        assert run.graph is None and not run.driving
        assert run.elapsed_s == pytest.approx(LIMIT + 1)
        assert run.stretch_started is None  # nothing counts after the end
    record = stored(h, run_id)
    assert record is not None and record["status"] == "error"
    assert record["elapsed_s"] == pytest.approx(LIMIT + 1) and record["run_timeout_s"] == LIMIT

    # One warning naming the run, the time used and the limit; no story text in it.
    warnings = [r.getMessage() for r in caplog.records if "time limit" in r.getMessage()]
    assert len(warnings) == 1
    assert run_id in warnings[0] and "601" in warnings[0] and "600" in warnings[0]
    assert "hymn" not in warnings[0]

    # The replay survives a restart too.
    manager = h.restart()
    assert dumped(collect(manager, run_id)) == dumped(events)

    # Telling it again starts a new run with a fresh budget (the clock has moved on).
    again = h.start()
    assert h.wait(again) == "done"
    assert h.names(again) == ["plan", "scene_ready", "done"]
    with h.run(again).cond:
        assert h.run(again).elapsed_s == 0.0 and h.run(again).run_timeout_s == LIMIT


def test_a_run_just_under_its_limit_finishes(harnesses: list[Harness]) -> None:
    clock = FakeClock()
    studio_script = script(
        todos(("Stage and save", "in_progress")),
        advancing(clock, LIMIT / 2, write("/screenplay.json", SCREENPLAY_JSON)),
        advancing(clock, LIMIT / 2 - 1, write("/timeline.json", TIMELINE_JSON)),
        save(),
        say("Saved."),
    )
    h = ClockedHarness(make_responder(studio_script), clock=clock, keep_runs=True)
    harnesses.append(h)
    run_id = h.start()
    assert h.wait(run_id) == "done"
    assert h.names(run_id) == ["plan", "scene_ready", "done"]
    assert stored(h, run_id)["elapsed_s"] == pytest.approx(LIMIT - 1)


# ------------------------------------------------------------------ (b) waiting does not count


def test_time_waiting_on_the_teller_is_not_counted(harnesses: list[Harness]) -> None:
    clock = FakeClock()
    interviewer = script(advancing(clock, 0.6 * LIMIT, ask("Which church was it?")),
                         advancing(clock, 0.3 * LIMIT, report))
    h = ClockedHarness(make_responder(NEW_MEMORY_STUDIO, interviewer=interviewer), clock=clock, keep_runs=True)
    harnesses.append(h)
    run_id = h.start()

    assert h.wait(run_id) == "waiting"
    assert stored(h, run_id)["elapsed_s"] == pytest.approx(0.6 * LIMIT)
    clock.advance(10 * LIMIT)  # the teller takes their time
    assert h.manager.status(FAMILY, run_id) == "waiting"

    answer(h, run_id)
    assert h.wait(run_id) == "done"
    events = h.manager.events(FAMILY, run_id)
    assert [n for n, _ in events][-2:] == ["scene_ready", "done"]
    assert errors(events) == []
    assert stored(h, run_id)["elapsed_s"] == pytest.approx(0.9 * LIMIT)


# ------------------------------------------------------------------ checked before a queued resume


def test_the_limit_is_checked_before_a_rejected_pause_resumes(harnesses: list[Harness]) -> None:
    clock = FakeClock()
    h: ClockedHarness

    def empty_question(messages: list[BaseMessage]) -> AIMessage:
        # The stream closes on this pause; the time passes before _settle reads the state.
        h.before_get_state = lambda: clock.advance(LIMIT + 1)
        return tool_call("ask_teller", {"question": "   "})

    studio_script = script(empty_question, tool_call("ask_teller", {"question": "Which church?"}), say("never"))
    h = ClockedHarness(make_responder(studio_script), clock=clock, keep_runs=True)
    harnesses.append(h)
    run_id = h.start()

    assert h.wait(run_id) == "error"
    assert dumped(h.manager.events(FAMILY, run_id)) == [("error", {"message": TIMEOUT_MESSAGE})]
    assert h.run(run_id).malformed_pauses == 1  # it was rejected, then the resume was refused
    studio_calls = [c for c in h.model.calls if tool_results(c[1]) >= 1]
    assert studio_calls == []  # the model never saw the rejection
    assert h.graphs[0].get_state(studio.thread_config(run_id)).interrupts  # the thread was not resumed
    assert h.streams == 1
    assert stored(h, run_id)["status"] == "error"


def test_the_limit_is_checked_before_a_queued_answer_resumes(harnesses: list[Harness]) -> None:
    # The question is answered while a parallel branch is still working, so the answer is
    # queued; the time runs out before _settle would resume with it.
    clock = FakeClock()
    release = threading.Event()

    def slow_sibling(messages: list[BaseMessage]) -> AIMessage:
        assert release.wait(WAIT), "the sibling was never released"
        return say("It was 1962.")

    studio_script = script(
        tool_calls(
            ("task", {"subagent_type": "interviewer", "description": "Ask about the church."}),
            ("task", {"subagent_type": "second_interviewer", "description": "Work out the year."}),
        ),
        say("unreachable: the run ends first"),
    )
    h = ClockedHarness(
        make_responder(studio_script, interviewer=script(ask("Which church?"), report), second=slow_sibling),
        clock=clock, subagents=[INTERVIEWER, OTHER_INTERVIEWER],
    )
    harnesses.append(h)
    run_id = h.start()

    async def follow_and_answer() -> list[tuple[str, BaseModel]]:
        seen: list[tuple[str, BaseModel]] = []
        async for name, model in h.manager.stream_run(FAMILY, run_id):
            seen.append((name, model))
            if name == "question":
                clock.advance(10 * LIMIT)  # waiting: not counted
                answer(h, run_id)
                assert h.manager.status(FAMILY, run_id) == "running"
                h.before_get_state = lambda: clock.advance(LIMIT + 1)
                release.set()
        return seen

    seen = asyncio.run(asyncio.wait_for(follow_and_answer(), WAIT))
    names = [n for n, _ in seen]
    assert names.count("question") == 1 and names[-1] == "error"
    assert "done" not in names and "scene_ready" not in names
    assert errors(seen) == [TIMEOUT_MESSAGE]
    # The interviewer never got the answer: the thread was not resumed.
    assert not any("St. Mark's" in (m.summary or "") for n, m in seen if n == "subagent_end")
    assert h.streams == 1  # started once, never resumed


# ------------------------------------------------------------------ (c) surviving a restart


def test_the_time_used_and_the_limit_survive_a_restart(harnesses: list[Harness]) -> None:
    clock = FakeClock()
    interviewer = script(advancing(clock, 0.6 * LIMIT, ask("Which church was it?")),
                         advancing(clock, 0.5 * LIMIT, report))
    h = ClockedHarness(make_responder(NEW_MEMORY_STUDIO, interviewer=interviewer), clock=clock, keep_runs=True)
    harnesses.append(h)
    run_id = h.start()
    assert h.wait(run_id) == "waiting"
    record = stored(h, run_id)
    assert record["elapsed_s"] == pytest.approx(0.6 * LIMIT) and record["run_timeout_s"] == LIMIT

    # The next process has a far longer default limit, and was down for a long while.
    h.run_timeout_s = 10 * LIMIT
    manager = h.restart()
    clock.advance(100 * LIMIT)
    assert manager.status(FAMILY, run_id) == "waiting"
    with h.run(run_id).cond:
        assert h.run(run_id).elapsed_s == pytest.approx(0.6 * LIMIT)
        assert h.run(run_id).run_timeout_s == LIMIT  # the run's own limit, from its record

    # 0.6 + 0.5 of the run's own limit: over it, though far under the new process's default.
    answer(h, run_id)
    assert h.wait(run_id) == "error"
    events = collect(manager, run_id)
    assert [n for n, _ in events] == ["plan", "subagent_start", "question", "error"]
    assert errors(events) == [TIMEOUT_MESSAGE]
    record = stored(h, run_id)
    assert record["status"] == "error" and record["elapsed_s"] == pytest.approx(1.1 * LIMIT)


def test_a_run_cut_short_past_its_limit_ends_without_driving_the_graph(
    harnesses: list[Harness], caplog: pytest.LogCaptureFixture
) -> None:
    clock = FakeClock()
    studio_script = crash_once(STRAIGHT_STUDIO, lambda messages: tool_results(messages) == 2)
    h = ClockedHarness(make_responder(studio_script), clock=clock, keep_runs=True)
    harnesses.append(h)
    run_id = h.start()
    assert studio_script.crashed.wait(WAIT)
    record = wait_stored_status(h, run_id, "running")
    assert [n for n, _ in record["events"]] == ["plan"]
    assert "elapsed_s" in record and record["run_timeout_s"] == LIMIT
    # The record says this run had already used more than its limit when the API stopped.
    h.store.put(runs.run_namespace(FAMILY), run_id, {**record, "elapsed_s": LIMIT + 5})

    caplog.set_level(logging.WARNING, logger="agent.runs")
    built = len(h.built)
    manager = h.restart()
    events = collect(manager, run_id)
    assert_typed(events)
    assert [n for n, _ in events] == ["plan", "error"]
    assert errors(events) == [TIMEOUT_MESSAGE]
    assert len(h.built) == built  # no studio was built, so nothing was driven
    assert manager.status(FAMILY, run_id) == "error"
    assert wait_stored_status(h, run_id, "error")["elapsed_s"] == pytest.approx(LIMIT + 5)
    messages = [r.getMessage() for r in caplog.records]
    assert not any("resuming it from its last checkpoint" in m for m in messages)
    assert any("time limit" in m and run_id in m for m in messages)


def test_time_while_the_api_was_down_is_not_counted(harnesses: list[Harness]) -> None:
    clock = FakeClock()
    studio_script = crash_once(
        script(
            todos(("Stage and save", "in_progress")),
            advancing(clock, 0.4 * LIMIT, write("/screenplay.json", SCREENPLAY_JSON), once=True),
            write("/timeline.json", TIMELINE_JSON),
            save(),
            say("Saved."),
        ),
        lambda messages: tool_results(messages) == 2,
    )
    h = ClockedHarness(make_responder(studio_script), clock=clock, keep_runs=True)
    harnesses.append(h)
    run_id = h.start()
    assert studio_script.crashed.wait(WAIT)
    record = wait_stored_status(h, run_id, "running")
    assert record["elapsed_s"] == pytest.approx(0.4 * LIMIT)

    clock.advance(10 * LIMIT)  # the API is down
    manager = h.restart()
    events = collect(manager, run_id)
    assert [n for n, _ in events] == ["plan", "scene_ready", "done"]
    assert stored(h, run_id)["elapsed_s"] == pytest.approx(0.4 * LIMIT)


# ------------------------------------------------------------------ (d) the limit from the environment


def test_without_a_limit_each_run_reads_stitch_run_timeout_s_when_it_starts(
    harnesses: list[Harness], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(ENV_RUN_TIMEOUT_S, "90")
    clock = FakeClock()
    interviewer = script(ask("Which church was it?"), advancing(clock, 100.0, report))
    h = ClockedHarness(make_responder(NEW_MEMORY_STUDIO, interviewer=interviewer), clock=clock,
                       run_timeout_s=None, keep_runs=True)
    harnesses.append(h)
    run_id = h.start()
    assert h.wait(run_id) == "waiting"
    assert stored(h, run_id)["run_timeout_s"] == 90.0

    # A later change of the variable does not move the limit of a run already started.
    monkeypatch.setenv(ENV_RUN_TIMEOUT_S, "3600")
    answer(h, run_id)
    assert h.wait(run_id) == "error"
    assert errors(h.manager.events(FAMILY, run_id)) == [runs.RUN_TIMEOUT_ERROR.format(minutes=2)]


def test_without_a_limit_or_a_variable_the_default_applies(
    harnesses: list[Harness], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(ENV_RUN_TIMEOUT_S, raising=False)
    h = ClockedHarness(make_responder(STRAIGHT_STUDIO), clock=FakeClock(), run_timeout_s=None, keep_runs=True)
    harnesses.append(h)
    run_id = h.start()
    assert h.wait(run_id) == "done"
    assert stored(h, run_id)["run_timeout_s"] == RunLimits().run_timeout_s == 1200.0


# ------------------------------------------------------------------ (e) records from before the limit


def test_a_record_from_before_the_time_limit_restores_with_nothing_used(
    harnesses: list[Harness], monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = FakeClock()
    interviewer = script(advancing(clock, 0.9 * LIMIT, ask("Which church was it?")),
                         advancing(clock, 0.5 * LIMIT, report))
    h = ClockedHarness(make_responder(NEW_MEMORY_STUDIO, interviewer=interviewer), clock=clock, keep_runs=True)
    harnesses.append(h)
    run_id = h.start()
    assert h.wait(run_id) == "waiting"
    h.manager.shutdown(wait=True)  # its worker may still write the record once more
    record = stored(h, run_id)
    old = {k: v for k, v in record.items() if k not in ("elapsed_s", "run_timeout_s")}
    h.store.put(runs.run_namespace(FAMILY), run_id, old)

    monkeypatch.setenv(ENV_RUN_TIMEOUT_S, "2400")
    h.run_timeout_s = None
    manager = h.restart()
    assert manager.status(FAMILY, run_id) == "waiting"
    with h.run(run_id).cond:
        assert h.run(run_id).elapsed_s == 0.0
        assert h.run(run_id).run_timeout_s == 2400.0  # the limit of today's environment

    answer(h, run_id)
    assert h.wait(run_id) == "done"  # 0 + 0.5 of LIMIT, far under 2400 s
    record = stored(h, run_id)
    assert record["elapsed_s"] == pytest.approx(0.5 * LIMIT) and record["run_timeout_s"] == 2400.0


def test_an_unreadable_elapsed_time_restores_as_nothing_used(harnesses: list[Harness]) -> None:
    clock = FakeClock()
    h = ClockedHarness(make_responder(NEW_MEMORY_STUDIO, interviewer=script(ask("Which church?"), report)),
                       clock=clock, keep_runs=True)
    harnesses.append(h)
    run_id = h.start()
    assert h.wait(run_id) == "waiting"
    h.manager.shutdown(wait=True)  # its worker may still write the record once more
    record = stored(h, run_id)
    h.store.put(runs.run_namespace(FAMILY), run_id, {**record, "elapsed_s": "lots", "run_timeout_s": -1})

    manager = h.restart()
    assert manager.status(FAMILY, run_id) == "waiting"
    with h.run(run_id).cond:
        assert h.run(run_id).elapsed_s == 0.0 and h.run(run_id).run_timeout_s == LIMIT
    answer(h, run_id)
    assert h.wait(run_id) == "done"


# ------------------------------------------------------------------ the record shape


def test_the_record_carries_the_time_used_and_the_limit() -> None:
    clock = FakeClock()
    info = RunInfo(run_id="run_rec", family_id=FAMILY, user_id="user_dad", author_id="dad",
                   fields=schemas.SubmitMemoryFields(text="x", year=1962), photo_ids=())
    run = runs._Run(info=info, config={}, run_timeout_s=LIMIT, clock=clock)
    with run.cond:
        run.workers = 1
        run.tick()  # a worker starts driving: the stretch opens
        clock.advance(30.0)
        assert run.record()["elapsed_s"] == 0.0  # the open stretch is not in a record until persisted
        run.persist()
        assert run.record()["elapsed_s"] == 30.0 and run.record()["run_timeout_s"] == LIMIT
        run.status = "waiting"
        clock.advance(5.0)
        run.persist()  # the 5 s before this status change was noticed still counted
        clock.advance(1000.0)
        run.persist()  # waiting: nothing more
        assert run.elapsed_s == 35.0 and run.stretch_started is None
        assert not run.over_time()


def test_overlapping_workers_keep_the_clock_running() -> None:
    # An answer can start the next worker just before the last one lets the run go: the
    # last one leaving must not stop the clock for the next one.
    clock = FakeClock()
    manager = runs.RunManager(repo=None, build_graph=lambda info: None, run_timeout_s=LIMIT,  # type: ignore[arg-type]
                              clock=clock)
    try:
        info = RunInfo(run_id="run_overlap", family_id=FAMILY, user_id="user_dad", author_id="dad",
                       fields=schemas.SubmitMemoryFields(text="x", year=1962), photo_ids=())
        run = runs._Run(info=info, config={}, run_timeout_s=LIMIT, clock=clock)
        last, next_one = manager._on_worker(run), manager._on_worker(run)
        last.__enter__()
        next_one.__enter__()
        last.__exit__(None, None, None)
        clock.advance(LIMIT + 1)
        with run.cond:
            assert run.counting and run.over_time()
        next_one.__exit__(None, None, None)
        with run.cond:
            assert not run.counting and run.stretch_started is None
            assert run.elapsed_s == pytest.approx(LIMIT + 1)
    finally:
        manager.shutdown(wait=True)
