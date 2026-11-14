"""agent/runs.py: a gated tool call the web cannot show goes back to the model.

HumanInTheLoopMiddleware pauses on the model's raw tool args, before the tool's schema
is checked. A pause `interrupt_to_event` refuses (an empty question, a prop chip with a
size outside the vocabulary) is rejected back to the model with the reason, the run
stays running, and the model calls the tool again. After MAX_MALFORMED_PAUSES such
rejections the next one ends the run with one short, family-safe error.

Same approach as test_agent_runs.py: the real `studio.build_studio` with
ScriptedChatModel, InMemorySaver, InMemoryStore and InMemoryRepo; no keys, no network.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.tools import tool

import schemas
from agent import runs, studio
from agent.fake_llm import tool_call, tool_calls, tool_messages
from agent.run_info import RunInfo
from agent.tools.ask import merge_people
from test_agent_runs import (  # pytest puts tests/agent on sys.path (no __init__.py)
    FAMILY,
    INTERVIEWER,
    OTHER_INTERVIEWER,
    SCREENPLAY_JSON,
    TIMELINE_JSON,
    USER,
    Harness,
    ask,
    assert_typed,
    collect,
    make_responder,
    report,
    save,
    say,
    script,
    stored,
    write,
)

NOT_SHOWN = "not shown to the family"

BAD_CHIP = {"id": "hymn_book", "name": "hymn book", "size": "medium"}
GOOD_CHIP = {"id": "hymn_book", "name": "hymn book", "size": "hand"}

received: list[dict[str, Any]] = []


@tool
def confirm_props(props: list[dict], edits: list[dict] | None = None) -> str:
    """Stand-in for agent/tools/props.py confirm_props (gated by INTERRUPT_ON): records its args."""
    received.append({"props": props, "edits": edits})
    return f"kept {len(props)} props, {len(edits or [])} edits"


@pytest.fixture
def harnesses() -> Iterator[list[Harness]]:
    made: list[Harness] = []
    yield made
    for h in made:
        h.manager.shutdown(wait=True)
        for old in h.retired:
            old.shutdown(wait=True)


def studio_tool_messages(h: Harness, name: str) -> list[ToolMessage]:
    """Every ToolMessage answering `name` that the model was shown, deduplicated by call id."""
    seen: dict[str, ToolMessage] = {}
    for _, messages in h.model.calls:
        for message in tool_messages(messages, name):
            seen.setdefault(message.tool_call_id, message)
    return list(seen.values())


def stage_and_save(*first: Callable[[list[BaseMessage]], AIMessage] | AIMessage):
    return script(*first, write("/screenplay.json", SCREENPLAY_JSON), write("/timeline.json", TIMELINE_JSON),
                  save(), say("Saved."))


# ------------------------------------------------------------------ ask_teller


def test_an_empty_question_goes_back_to_the_model_and_the_run_finishes(
    harnesses: list[Harness], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING, logger="agent.runs")
    studio_script = stage_and_save(
        tool_call("ask_teller", {"question": "   "}),
        tool_call("ask_teller", {"question": "Which church was it?"}),
    )
    h = Harness(make_responder(studio_script))
    harnesses.append(h)
    run_id = h.start()

    assert h.wait(run_id) == "waiting"
    events = h.manager.events(FAMILY, run_id)
    assert events == [("question", schemas.QuestionEvent(text="Which church was it?"))]

    h.answer(run_id, "St. Mark's, in Trenton.")
    assert h.wait(run_id) == "done"

    events = collect(h.manager, run_id)
    assert_typed(events)
    names = [n for n, _ in events]
    assert names == ["question", "scene_ready", "done"]
    assert "error" not in names

    rejected, answered = studio_tool_messages(h, "ask_teller")
    assert NOT_SHOWN in rejected.text
    assert "question" in rejected.text and "call the tool again" in rejected.text
    assert answered.text == "St. Mark's, in Trenton."
    assert len(h.graphs) == 1  # resumed on the same graph and thread
    assert h.manager._runs[run_id].malformed_pauses == 1
    assert any("handed a pause" in r.getMessage() for r in caplog.records)  # a log line, not an event


# ------------------------------------------------------------------ confirm_props


def test_a_prop_chip_with_a_bad_size_goes_back_to_the_model(harnesses: list[Harness]) -> None:
    received.clear()
    studio_script = stage_and_save(
        tool_call("confirm_props", {"props": [BAD_CHIP]}),
        tool_call("confirm_props", {"props": [GOOD_CHIP]}),
    )
    h = Harness(make_responder(studio_script), extra_tools=[confirm_props])
    harnesses.append(h)
    run_id = h.start()

    assert h.wait(run_id) == "waiting"
    events = h.manager.events(FAMILY, run_id)
    assert [n for n, _ in events] == ["props_found"]
    assert events[0][1] == schemas.PropsFoundEvent(props=[schemas.PropChip.model_validate(GOOD_CHIP)])
    assert received == []  # the rejected call never ran

    h.manager.answer_run(FAMILY, run_id, USER, schemas.AnswerRequest(kind="props", props=[]))
    assert h.wait(run_id) == "done"
    names = h.names(run_id)
    assert names == ["props_found", "scene_ready", "done"]
    assert received == [{"props": [GOOD_CHIP], "edits": []}]

    rejected, approved = studio_tool_messages(h, "confirm_props")
    text = rejected.text
    assert NOT_SHOWN in text
    assert "hymn_book" in text and "size" in text and "medium" in text
    for size in schemas.VOCABULARY["sizes"]:
        assert size in text
    assert "validation error" not in text.lower()
    assert "errors.pydantic.dev" not in text and "Traceback" not in text
    assert "kept 1 props" in approved.text


# ------------------------------------------------------------------ merge_people


def test_a_merge_with_a_blank_candidate_goes_back_to_the_model(harnesses: list[Harness]) -> None:
    merge = {"name": "Uncle Joe", "person_id": "dad", "question": "Is Uncle Joe the Joseph in your tree?"}
    studio_script = stage_and_save(
        tool_call("merge_people", {**merge, "candidate_name": "  "}),
        tool_call("merge_people", {**merge, "candidate_name": "Joseph"}),
    )
    h = Harness(make_responder(studio_script), extra_tools=[merge_people])
    harnesses.append(h)
    run_id = h.start()

    assert h.wait(run_id) == "waiting"
    events = h.manager.events(FAMILY, run_id)
    assert events == [("merge", schemas.MergeEvent(text=merge["question"], person_id="dad", candidate_name="Joseph"))]
    assert h.manager.status(FAMILY, run_id) == "waiting"

    h.manager.answer_run(FAMILY, run_id, USER, schemas.AnswerRequest(kind="merge", approve=True))
    assert h.wait(run_id) == "done"
    assert h.names(run_id) == ["merge", "scene_ready", "done"]

    rejected, approved = studio_tool_messages(h, "merge_people")
    assert NOT_SHOWN in rejected.text and "candidate_name" in rejected.text
    assert "call the tool again" in rejected.text and len(rejected.text) < 300
    assert approved.text.startswith("approved:")


# ------------------------------------------------------------------ the cap


def test_a_studio_that_keeps_pausing_on_bad_args_ends_in_one_short_error(
    harnesses: list[Harness], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING, logger="agent.runs")
    received.clear()
    def bad_review(messages: list[BaseMessage]) -> AIMessage:
        return tool_call("confirm_props", {"props": [BAD_CHIP]})  # a new call id every time

    h = Harness(make_responder(script(bad_review)),
                extra_tools=[confirm_props], keep_runs=True)
    harnesses.append(h)
    run_id = h.start()

    assert h.wait(run_id) == "error"
    events = collect(h.manager, run_id)
    assert_typed(events)
    assert [n for n, _ in events] == ["error"]
    message = events[0][1].message
    assert message == runs.MALFORMED_PAUSE_ERROR
    assert "validation error" not in message.lower() and "hymn_book" not in message
    assert h.manager.status(FAMILY, run_id) == "error"
    assert received == []

    # The model was handed MAX_MALFORMED_PAUSES rejections; the next bad call ended the run.
    rejected = studio_tool_messages(h, "confirm_props")
    assert len(rejected) == runs.MAX_MALFORMED_PAUSES
    assert all(NOT_SHOWN in m.text for m in rejected)
    assert len(h.model.calls) == runs.MAX_MALFORMED_PAUSES + 1

    record = stored(h, run_id)
    assert record is not None and record["status"] == "error"
    assert record["malformed_pauses"] == runs.MAX_MALFORMED_PAUSES
    with pytest.raises(runs.RunNotWaiting):
        h.manager.answer_run(FAMILY, run_id, USER, schemas.AnswerRequest(kind="props", props=[]))
    assert sum("handed a pause" in r.getMessage() for r in caplog.records) == runs.MAX_MALFORMED_PAUSES


def test_the_count_is_kept_in_the_run_record_across_a_restart(harnesses: list[Harness]) -> None:
    # One bad question before the restart, then a good one the family answers after it;
    # from then on every question is bad. The restored run remembers the first rejection,
    # so it ends after two more instead of three.
    def studio_script(messages: list[BaseMessage]) -> AIMessage:
        answered = [m for m in tool_messages(messages, "ask_teller") if NOT_SHOWN not in m.text]
        if len(tool_messages(messages)) == 1 and not answered:
            return tool_call("ask_teller", {"question": "Which church was it?"})
        return tool_call("ask_teller", {"question": ""})

    h = Harness(make_responder(studio_script), keep_runs=True)
    harnesses.append(h)
    run_id = h.start()
    assert h.wait(run_id) == "waiting"
    record = stored(h, run_id)
    assert record is not None and record["malformed_pauses"] == 1 and record["status"] == "waiting"

    manager = h.restart()
    assert manager.status(FAMILY, run_id) == "waiting"
    assert manager._runs[run_id].malformed_pauses == 1
    h.answer(run_id, "St. Mark's.")
    assert h.wait(run_id) == "error"
    assert h.names(run_id) == ["question", "error"]
    rejected = [m for m in studio_tool_messages(h, "ask_teller") if NOT_SHOWN in m.text]
    assert len(rejected) == runs.MAX_MALFORMED_PAUSES
    assert stored(h, run_id)["malformed_pauses"] == runs.MAX_MALFORMED_PAUSES


def test_a_restored_run_paused_on_a_bad_call_drives_on(harnesses: list[Harness]) -> None:
    # The record says waiting, but what the thread is paused on now is a call the web
    # cannot show (say the process died after the old question was answered and the
    # model's next call was a bad one). The restored run hands it back to the model.
    studio_script = stage_and_save(
        tool_call("ask_teller", {"question": ""}),
        tool_call("ask_teller", {"question": "Which church was it?"}),
    )
    h = Harness(make_responder(studio_script), keep_runs=True)
    harnesses.append(h)
    run_id = "run_restored_bad_pause"
    info = RunInfo(run_id=run_id, family_id=FAMILY, user_id=USER, author_id="dad",
                   fields=schemas.SubmitMemoryFields(text="Dad passed the hymn books down the pew.", year=1962),
                   photo_ids=())
    config = studio.thread_config(run_id)
    graph = h.build(info)
    for _ in graph.stream(h.manager._initial_input(info), config):
        pass
    assert len(graph.get_state(config).interrupts) == 1  # paused on the bad question
    record = runs._Run(info=info, config=config, status="waiting").record()
    record["pending_interrupt_id"] = "an_answered_question"
    h.store.put(runs.run_namespace(FAMILY), run_id, record)

    manager = h.restart()
    assert manager.wait(FAMILY, run_id, timeout=20.0) == "waiting"
    assert manager.events(FAMILY, run_id) == [("question", schemas.QuestionEvent(text="Which church was it?"))]
    assert stored(h, run_id)["malformed_pauses"] == 1
    h.answer(run_id, "St. Mark's.")
    assert h.wait(run_id) == "done"
    assert h.names(run_id) == ["question", "scene_ready", "done"]
    assert NOT_SHOWN in studio_tool_messages(h, "ask_teller")[0].text


# ------------------------------------------------------------------ one question at a time


def test_a_bad_pause_next_to_a_good_one_waits_its_turn(harnesses: list[Harness]) -> None:
    # Two sub-agents pause in the same step: one with a good question, one with an empty
    # one. Whichever surfaces first, the family sees exactly the two good questions, one
    # at a time, and the run ends done.
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
                       second=script(ask(" "), ask("Which year?"), report)),
        subagents=[INTERVIEWER, OTHER_INTERVIEWER],
    )
    harnesses.append(h)
    run_id = h.start()

    for _ in range(2):
        assert h.wait(run_id) == "waiting"
        questions = [m.text for n, m in h.manager.events(FAMILY, run_id) if n == "question"]
        h.answer(run_id, f"answer to {questions[-1]}")
    assert h.wait(run_id) == "done"

    events = h.manager.events(FAMILY, run_id)
    assert_typed(events)
    names = [n for n, _ in events]
    assert "error" not in names and names[-2:] == ["scene_ready", "done"]
    questions = [m.text for n, m in events if n == "question"]
    assert sorted(questions) == ["Which church?", "Which year?"]
    ends = {m.name: m.summary for n, m in events if n == "subagent_end"}
    assert "answer to Which church?" in ends["interviewer"]
    assert "answer to Which year?" in ends["second_interviewer"]
    assert any(NOT_SHOWN in m.text for m in studio_tool_messages(h, "ask_teller"))
