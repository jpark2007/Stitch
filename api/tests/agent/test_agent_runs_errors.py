"""agent/runs.py: what the family sees when a run fails.

An exception's text can carry a provider's response body, a URL or a MONGODB_URI, so it
never reaches an error event: the family gets RUN_ERROR with the exception's class name,
and the full detail goes to the log. Each of the three failure sites is driven here with
an exception carrying fake secrets, and the streamed events, the replayed events (also
after a restart) and the stored run record are all checked for them.

Same approach as test_agent_runs.py: the real studio graph with ScriptedChatModel,
InMemorySaver, InMemoryStore and InMemoryRepo; no keys, no network.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from typing import Any

import pytest
from langchain_core.messages import AIMessage
from langchain_core.tools import tool

import schemas
from agent import runs
from agent.fake_llm import tool_call
from agent.run_info import RunInfo
from test_agent_runs import (  # pytest puts tests/agent on sys.path (no __init__.py)
    FAMILY,
    NEW_MEMORY_STUDIO,
    STRAIGHT_STUDIO,
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
    say,
    script,
    todos,
    tool_results,
    wait_stored_status,
)

SECRETS = ("sk-ant-TESTSECRET", "mongodb://u:p@h")
SECRET_TEXT = "provider said 401 for key sk-ant-TESTSECRET while writing to mongodb://u:p@h/stitch"


class StudioBroke(RuntimeError):
    """An exception whose text carries secrets, as a provider or driver error can."""


EXPECTED = "the studio ran into a problem (StudioBroke); please try again"


@tool
def leaky() -> str:
    """A tool whose failure message carries secrets."""
    raise StudioBroke(SECRET_TEXT)


class BreakableHarness(Harness):
    """A Harness whose studio cannot be built once `broken` is set."""

    def __init__(self, responder: Responder, **kwargs: Any) -> None:
        self.broken = False
        super().__init__(responder, **kwargs)

    def build(self, run: RunInfo) -> Any:
        if self.broken:
            raise StudioBroke(SECRET_TEXT)
        return super().build(run)


@pytest.fixture
def harnesses() -> Iterator[list[Harness]]:
    made: list[Harness] = []
    yield made
    for h in made:
        h.manager.shutdown(wait=True)
        for old in h.retired:
            old.shutdown(wait=True)


def assert_no_secrets(text: str) -> None:
    for secret in SECRETS:
        assert secret not in text, f"{secret!r} leaked into {text[:200]!r}"


def assert_family_safe(h: Harness, run_id: str, caplog: pytest.LogCaptureFixture) -> list[str]:
    """The run ended with exactly one error, EXPECTED, wherever the family can read it:
    the live stream, a replay from this process and from a restarted one, and the stored
    record. The log still has the full detail. Returns the event names."""
    streamed = collect(h.manager, run_id)
    assert_typed(streamed)
    names = [n for n, _ in streamed]
    assert names.count("error") == 1 and names[-1] == "error" and "done" not in names
    assert streamed[-1][1] == schemas.ErrorEvent(message=EXPECTED)
    assert dumped(collect(h.manager, run_id)) == dumped(streamed)
    assert dumped(h.manager.events(FAMILY, run_id)) == dumped(streamed)
    record = wait_stored_status(h, run_id, "error")
    assert_no_secrets(json.dumps(record))
    assert json.loads(record["events"][-1][1]) == {"message": EXPECTED}

    manager = h.restart()
    replayed = collect(manager, run_id)
    assert dumped(replayed) == dumped(streamed)
    for _, model in replayed:
        assert_no_secrets(model.model_dump_json())

    # The full detail is only in the log, with its traceback.
    assert any(r.exc_info and SECRET_TEXT in str(r.exc_info[1]) for r in caplog.records), "nothing logged"
    return names


# ------------------------------------------------------------------ the message


def test_run_error_message_names_the_kind_only() -> None:
    assert runs.RUN_ERROR == "the studio ran into a problem ({kind}); please try again"
    assert runs.run_error_message(StudioBroke(SECRET_TEXT)) == EXPECTED
    assert runs.run_error_message(RuntimeError("no model configured")) == (
        "the studio ran into a problem (RuntimeError); please try again"
    )
    assert runs.run_error_message(KeyError("mongodb://u:p@h")) == runs.RUN_ERROR.format(kind="KeyError")
    assert "RUN_ERROR" in runs.__all__ and "run_error_message" in runs.__all__
    # The two fixed messages are unchanged.
    assert runs.MALFORMED_PAUSE_ERROR == "the studio could not put together a question for you; please try again"
    assert runs.RUN_TIMEOUT_ERROR == "the studio took too long on this story (over {minutes} minutes); please try again"


# ------------------------------------------------------------------ site 1: driving (_drive_on)


def test_a_tool_failure_while_driving_never_shows_its_text(
    harnesses: list[Harness], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.ERROR, logger="agent.runs")
    h = BreakableHarness(make_responder(script(todos(("Break", "in_progress")), tool_call("leaky", {}), say("x"))),
                         extra_tools=[leaky], keep_runs=True)
    harnesses.append(h)
    run_id = h.start()
    assert h.wait(run_id) == "error"
    assert assert_family_safe(h, run_id, caplog) == ["plan", "error"]


def test_a_studio_that_cannot_be_built_never_shows_the_reason(
    harnesses: list[Harness], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.ERROR, logger="agent.runs")
    h = BreakableHarness(make_responder(STRAIGHT_STUDIO), keep_runs=True)
    h.broken = True
    harnesses.append(h)
    run_id = h.start()
    assert h.wait(run_id) == "error"
    assert assert_family_safe(h, run_id, caplog) == ["error"]


# ------------------------------------------------------------------ site 2: restoring a waiting run


def test_a_waiting_run_whose_studio_cannot_be_rebuilt_never_shows_the_reason(
    harnesses: list[Harness], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.ERROR, logger="agent.runs")
    h = BreakableHarness(make_responder(NEW_MEMORY_STUDIO, interviewer=script(ask("Which church?"), report)),
                         keep_runs=True)
    harnesses.append(h)
    run_id = h.start()
    assert h.wait(run_id) == "waiting"

    h.broken = True
    manager = h.restart()  # the restored run cannot get its studio back
    assert manager.status(FAMILY, run_id) == "error"
    with pytest.raises(runs.RunNotWaiting):
        h.answer(run_id, "St. Mark's.")
    assert assert_family_safe(h, run_id, caplog) == ["plan", "subagent_start", "question", "error"]


# ------------------------------------------------------------------ site 3: driving again after a restart


def test_a_run_cut_short_whose_studio_cannot_be_rebuilt_never_shows_the_reason(
    harnesses: list[Harness], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.ERROR, logger="agent.runs")
    studio_script = crash_once(STRAIGHT_STUDIO, lambda messages: tool_results(messages) == 2)
    h = BreakableHarness(make_responder(studio_script), keep_runs=True)
    harnesses.append(h)
    run_id = h.start()
    assert studio_script.crashed.wait(WAIT)
    wait_stored_status(h, run_id, "running")

    h.broken = True
    manager = h.restart()
    assert manager.wait(FAMILY, run_id, timeout=WAIT) == "error"
    assert assert_family_safe(h, run_id, caplog) == ["plan", "error"]


# ------------------------------------------------------------------ the studio's own last words


def test_the_studios_last_words_are_capped_at_300_characters(harnesses: list[Harness]) -> None:
    rambling = "I looked everywhere for the church. " * 40
    h = Harness(make_responder(script(todos(("Think", "in_progress")), say(rambling))))
    harnesses.append(h)
    run_id = h.start()
    assert h.wait(run_id) == "error"
    message = h.manager.events(FAMILY, run_id)[-1][1].message
    prefix = "the studio finished without saving a scene: "
    assert message.startswith(prefix)
    said = message[len(prefix):]
    assert len(said) <= runs.ERROR_TEXT_LIMIT == 300
    assert said.endswith("…") and rambling.startswith(said[:-1])


def test_a_studio_that_stops_without_a_word_ends_with_a_clean_message(harnesses: list[Harness]) -> None:
    h = Harness(make_responder(script(todos(("Think", "in_progress")), AIMessage(content=""))))
    harnesses.append(h)
    run_id = h.start()
    assert h.wait(run_id) == "error"
    assert h.manager.events(FAMILY, run_id)[-1][1].message == "the studio finished without saving a scene"

