"""ask_teller / merge_people / confirm_props: interrupts map to run events, answers resume."""

from __future__ import annotations

import itertools
from typing import Any

import pytest
from langchain.agents import create_agent
from langchain.agents.middleware import HumanInTheLoopMiddleware
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

import schemas
from agent.tools.ask import (
    INTERRUPT_ON,
    NO_ANSWER,
    answer_to_resume,
    ask_teller,
    interrupt_to_event,
    merge_people,
    reject_to_resume,
)

_thread_ids = itertools.count()


class ScriptedModel(BaseChatModel):
    """Replies with the scripted messages in order and records what it was shown."""

    script: list[AIMessage]
    seen: list[list[BaseMessage]] = []
    turn: int = 0

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools: Any, **kwargs: Any) -> ScriptedModel:
        return self

    def _generate(self, messages: list[BaseMessage], stop: Any = None, run_manager: Any = None, **kw: Any) -> ChatResult:
        self.seen.append(list(messages))
        message = self.script[min(self.turn, len(self.script) - 1)]
        self.turn += 1
        return ChatResult(generations=[ChatGeneration(message=message)])


def call(name: str, args: dict[str, Any], call_id: str = "call_1") -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id, "type": "tool_call"}])


DONE = AIMessage(content="All set.")

received_props: list[dict[str, Any]] = []


@tool
def confirm_props(props: list[dict], edits: list[dict] | None = None) -> str:
    """Stand-in for agent/tools/props.py confirm_props: records what it receives."""
    received_props.append({"props": props, "edits": edits})
    return f"{len(edits or [])} edits"


CHIPS = [
    {"id": "prop_hymnal", "name": "Hymn book", "size": "hand", "reused": False},
    {"id": "prop_cap", "name": "Blue flat cap", "description": "Grandpa's cap", "size": "hand", "reused": True},
]


def build(script: list[AIMessage], mode: str = "batched") -> tuple[Any, ScriptedModel, dict]:
    model = ScriptedModel(script=script, seen=[])
    graph = create_agent(
        model,
        tools=[ask_teller, merge_people, confirm_props],
        middleware=[HumanInTheLoopMiddleware(interrupt_on=INTERRUPT_ON, interrupt_mode=mode)],
        checkpointer=InMemorySaver(),
    )
    config = {"configurable": {"thread_id": f"t{next(_thread_ids)}"}}
    return graph, model, config


def start(graph: Any, config: dict) -> Any:
    """Invoke until the first pause and return the single pending Interrupt."""
    graph.invoke({"messages": [{"role": "user", "content": "Tell the story."}]}, config)
    pending = graph.get_state(config).interrupts
    assert len(pending) == 1, pending
    return pending[0]


def tool_messages(model: ScriptedModel) -> list[ToolMessage]:
    return [m for m in model.seen[-1] if isinstance(m, ToolMessage)]


# ----------------------------------------------------------------------- ask_teller


@pytest.mark.parametrize("mode", ["batched", "per_call"])
def test_ask_teller_pauses_maps_and_resumes_with_answer(mode: str) -> None:
    graph, model, config = build([call("ask_teller", {"question": "Where were you standing?"}), DONE], mode)
    pending = start(graph, config)
    assert model.turn == 1  # paused before any tool ran

    name, event = interrupt_to_event(pending.value)
    assert (name, event) == ("question", schemas.QuestionEvent(text="Where were you standing?"))
    assert isinstance(event, schemas.RUN_EVENTS[name])
    assert interrupt_to_event(pending) == (name, event)  # Interrupt objects work too

    resume = answer_to_resume(pending.value, schemas.AnswerRequest(kind="answer", text="On the church steps"))
    if mode == "batched":
        assert resume == {"decisions": [{"type": "respond", "message": "On the church steps"}]}
    graph.invoke(Command(resume=resume), config)

    assert not graph.get_state(config).interrupts
    msgs = tool_messages(model)
    assert len(msgs) == 1
    assert msgs[0].name == "ask_teller"
    assert "On the church steps" in msgs[0].content
    assert NO_ANSWER not in msgs[0].content


def test_answer_requires_text() -> None:
    graph, _, config = build([call("ask_teller", {"question": "When?"}), DONE])
    pending = start(graph, config)
    for text in (None, "", "   "):
        with pytest.raises(ValueError):
            answer_to_resume(pending.value, schemas.AnswerRequest(kind="answer", text=text))


def test_parallel_questions_answer_first_and_defer_rest() -> None:
    two = AIMessage(
        content="",
        tool_calls=[
            {"name": "ask_teller", "args": {"question": "Where?"}, "id": "a", "type": "tool_call"},
            {"name": "ask_teller", "args": {"question": "Who?"}, "id": "b", "type": "tool_call"},
        ],
    )
    graph, model, config = build([two, DONE])
    pending = start(graph, config)
    assert interrupt_to_event(pending.value) == ("question", schemas.QuestionEvent(text="Where?"))
    resume = answer_to_resume(pending.value, schemas.AnswerRequest(kind="answer", text="The church"))
    assert [d["type"] for d in resume["decisions"]] == ["respond", "reject"]
    graph.invoke(Command(resume=resume), config)
    by_id = {m.tool_call_id: m for m in tool_messages(model)}
    assert by_id["a"].content == "The church"
    assert "Ask this again" in by_id["b"].content


# --------------------------------------------------------------------- merge_people

MERGE_ARGS = {
    "name": "Uncle Joe",
    "person_id": "p_joseph",
    "candidate_name": "Joseph",
    "question": "Is Uncle Joe the Joseph in your tree?",
}


@pytest.mark.parametrize(
    ("approve", "expected"),
    [
        (True, "approved: p_joseph is Joseph"),
        (False, "rejected: Uncle Joe is a different person"),
    ],
)
def test_merge_people_maps_and_responds(approve: bool, expected: str) -> None:
    graph, model, config = build([call("merge_people", MERGE_ARGS), DONE])
    pending = start(graph, config)

    assert interrupt_to_event(pending.value) == (
        "merge",
        schemas.MergeEvent(text=MERGE_ARGS["question"], person_id="p_joseph", candidate_name="Joseph"),
    )
    resume = answer_to_resume(pending.value, schemas.AnswerRequest(kind="merge", approve=approve))
    (decision,) = resume["decisions"]
    assert decision["type"] == "respond"
    assert decision["message"].startswith(expected)

    graph.invoke(Command(resume=resume), config)
    (msg,) = tool_messages(model)
    assert msg.name == "merge_people"
    assert msg.content.startswith(expected)


def test_merge_requires_approve() -> None:
    graph, _, config = build([call("merge_people", MERGE_ARGS), DONE])
    pending = start(graph, config)
    with pytest.raises(ValueError):
        answer_to_resume(pending.value, schemas.AnswerRequest(kind="merge"))


# -------------------------------------------------------------------- confirm_props


def test_confirm_props_maps_to_props_found_and_edits_reach_the_tool() -> None:
    received_props.clear()
    graph, model, config = build([call("confirm_props", {"props": CHIPS}), DONE])
    pending = start(graph, config)

    name, event = interrupt_to_event(pending.value)
    assert name == "props_found"
    assert event == schemas.PropsFoundEvent(props=[schemas.PropChip.model_validate(c) for c in CHIPS])

    answer = schemas.AnswerRequest(
        kind="props",
        props=[
            schemas.PropEdit(id="prop_hymnal", action="rename", name="Red hymnal"),
            schemas.PropEdit(id="prop_cap", action="replace_photo", photo_id="media_9"),
        ],
    )
    resume = answer_to_resume(pending.value, answer)
    (decision,) = resume["decisions"]
    assert decision["type"] == "edit"
    assert decision["edited_action"]["name"] == "confirm_props"
    edits = [
        {"id": "prop_hymnal", "action": "rename", "name": "Red hymnal", "photoId": None},
        {"id": "prop_cap", "action": "replace_photo", "name": None, "photoId": "media_9"},
    ]
    assert decision["edited_action"]["args"] == {"props": CHIPS, "edits": edits}

    graph.invoke(Command(resume=resume), config)
    assert received_props == [{"props": CHIPS, "edits": edits}]
    (msg,) = tool_messages(model)
    assert "2 edits" in msg.content


def test_confirm_props_empty_review_keeps_all() -> None:
    received_props.clear()
    graph, _, config = build([call("confirm_props", {"props": CHIPS}), DONE])
    pending = start(graph, config)
    resume = answer_to_resume(pending.value, schemas.AnswerRequest(kind="props", props=[]))
    graph.invoke(Command(resume=resume), config)
    assert received_props == [{"props": CHIPS, "edits": []}]


# -------------------------------------------------------------------------- errors


def test_mismatched_kind_raises() -> None:
    graph, _, config = build([call("ask_teller", {"question": "Where?"}), DONE])
    pending = start(graph, config)
    with pytest.raises(ValueError, match="does not match"):
        answer_to_resume(pending.value, schemas.AnswerRequest(kind="props", props=[]))
    with pytest.raises(ValueError, match="does not match"):
        answer_to_resume(pending.value, schemas.AnswerRequest(kind="merge", approve=True))

    merge_pending = {"action_requests": [{"name": "merge_people", "args": MERGE_ARGS}], "review_configs": []}
    with pytest.raises(ValueError, match="does not match"):
        answer_to_resume(merge_pending, schemas.AnswerRequest(kind="answer", text="yes"))


@pytest.mark.parametrize(
    "value",
    [
        None,
        "ask",
        {"something": "else"},
        {"action_requests": [], "review_configs": []},
        {"action_requests": [{"name": "delete_file", "args": {}}], "review_configs": []},
        {"action_requests": [{"name": "ask_teller", "args": {}}], "review_configs": []},
        {"action_requests": [{"name": "ask_teller"}], "review_configs": []},
        {"action_requests": [{"name": "merge_people", "args": {"question": "Same?"}}], "review_configs": []},
        {"action_requests": [{"name": "confirm_props", "args": {"props": "hat"}}], "review_configs": []},
        {"action_requests": [{"name": "confirm_props", "args": {"props": [{"id": "x"}]}}], "review_configs": []},
        {"type": "tool_approval", "tool_call_id": "c", "name": "nope", "args": {}, "description": ""},
    ],
)
def test_malformed_or_unknown_interrupts_raise(value: Any) -> None:
    with pytest.raises(ValueError):
        interrupt_to_event(value)


def test_a_malformed_prop_chip_gets_a_short_message_naming_the_chip_and_field() -> None:
    bad = {"id": "hymn_book", "name": "hymn book", "size": "medium"}
    value = {"action_requests": [{"name": "confirm_props", "args": {"props": [CHIPS[0], bad]}}], "review_configs": []}
    with pytest.raises(ValueError) as caught:
        interrupt_to_event(value)
    message = str(caught.value)
    assert "'hymn_book'" in message and "'size'" in message and "'medium'" in message
    for size in schemas.VOCABULARY["sizes"]:
        assert size in message
    # Pydantic's own dump never reaches the model (or the family).
    assert "validation error" not in message.lower()
    assert "pydantic" not in message.lower() and "errors.pydantic.dev" not in message
    assert caught.value.__cause__ is None
    assert len(message) < 200


@pytest.mark.parametrize(
    ("chip", "expected"),
    [
        ({"name": "hat", "size": "hand"}, ["props[0]", "'id' is missing"]),
        ({"id": "hat", "size": "hand", "colour": "red"}, ["prop 'hat'", "'name' is missing", "'colour' is not"]),
        ({"id": "hat", "name": ["hat"], "size": "hand"}, ["prop 'hat'", "'name' has the wrong type"]),
        ("hat", ["props[0] is not an object"]),
    ],
)
def test_other_malformed_chips_get_short_messages(chip: Any, expected: list[str]) -> None:
    value = {"type": "tool_approval", "tool_call_id": "c", "name": "confirm_props", "args": {"props": [chip]},
             "description": ""}
    with pytest.raises(ValueError) as caught:
        interrupt_to_event(value)
    message = str(caught.value)
    for part in expected:
        assert part in message
    assert "validation error" not in message.lower()


def test_reject_to_resume_batched_rejects_every_action() -> None:
    value = {
        "action_requests": [
            {"name": "ask_teller", "args": {"question": "  "}},
            {"name": "ask_teller", "args": {"question": "Who?"}},
        ],
        "review_configs": [],
    }
    assert reject_to_resume(value, "fix it") == {
        "decisions": [{"type": "reject", "message": "fix it"}, {"type": "reject", "message": "fix it"}],
    }


def test_reject_to_resume_per_call_is_one_decision() -> None:
    value = {"type": "tool_approval", "tool_call_id": "c", "name": "confirm_props", "args": {"props": "x"},
             "description": ""}
    assert reject_to_resume(value, "fix it") == {"type": "reject", "message": "fix it"}


@pytest.mark.parametrize("value", [None, "ask", {"something": "else"}, {"action_requests": []}])
def test_reject_to_resume_refuses_what_is_not_a_hitl_pause(value: Any) -> None:
    with pytest.raises(ValueError):
        reject_to_resume(value, "fix it")


@pytest.mark.parametrize("mode", ["batched", "per_call"])
def test_a_rejected_pause_reaches_the_model_and_the_tool_never_runs(mode: str) -> None:
    received_props.clear()
    bad = call("confirm_props", {"props": [{"id": "hymn_book", "name": "hymn book", "size": "medium"}]})
    graph, model, config = build([bad, DONE], mode)
    pending = start(graph, config)
    with pytest.raises(ValueError) as caught:
        interrupt_to_event(pending)
    resume = reject_to_resume(pending, f"not shown to the family: {caught.value}")
    if mode == "batched":
        assert resume == {"decisions": [{"type": "reject", "message": f"not shown to the family: {caught.value}"}]}
    graph.invoke(Command(resume=resume), config)

    assert not graph.get_state(config).interrupts
    assert received_props == []  # rejected: the tool body never ran
    (msg,) = tool_messages(model)
    assert msg.name == "confirm_props"
    assert "not shown to the family" in msg.content and "hymn_book" in msg.content


def test_interrupt_on_decisions() -> None:
    assert INTERRUPT_ON["ask_teller"]["allowed_decisions"] == ["respond", "reject"]
    assert INTERRUPT_ON["merge_people"]["allowed_decisions"] == ["respond", "reject"]
    assert INTERRUPT_ON["confirm_props"]["allowed_decisions"] == ["edit", "approve", "reject"]
    # The middleware accepts the config as-is.
    HumanInTheLoopMiddleware(interrupt_on=INTERRUPT_ON)


def test_tool_bodies_are_neutral() -> None:
    assert ask_teller.invoke({"question": "Where?"}) == NO_ANSWER
    assert merge_people.invoke(MERGE_ARGS) == NO_ANSWER


def test_a_good_question_beside_a_malformed_call_is_shown_and_the_other_is_deferred() -> None:
    # One model turn gates a good question and a prop review the web could not show: the
    # question is shown (only the first action is read), the answer reaches it, and the
    # malformed call is refused with the ask-again note: its tool body never runs.
    received_props.clear()
    turn = AIMessage(
        content="",
        tool_calls=[
            {"name": "ask_teller", "args": {"question": "Which church?"}, "id": "q", "type": "tool_call"},
            {"name": "confirm_props", "args": {"props": [{"id": "x", "name": "x", "size": "huge"}]}, "id": "p",
             "type": "tool_call"},
        ],
    )
    graph, model, config = build([turn, DONE])
    pending = start(graph, config)
    assert interrupt_to_event(pending) == ("question", schemas.QuestionEvent(text="Which church?"))
    resume = answer_to_resume(pending, schemas.AnswerRequest(kind="answer", text="St. Mark's"))
    assert [d["type"] for d in resume["decisions"]] == ["respond", "reject"]
    graph.invoke(Command(resume=resume), config)
    by_id = {m.tool_call_id: m for m in tool_messages(model)}
    assert by_id["q"].content == "St. Mark's"
    assert "Ask this again" in by_id["p"].content
    assert received_props == []
    assert not graph.get_state(config).interrupts


def test_a_pause_whose_whole_turn_is_rejected_round_trips_with_every_call_refused() -> None:
    # The first action cannot be shown, so runs.py rejects the whole pause: every gated
    # call of the turn gets the reason, and the model can ask again.
    turn = AIMessage(
        content="",
        tool_calls=[
            {"name": "ask_teller", "args": {"question": "   "}, "id": "a", "type": "tool_call"},
            {"name": "ask_teller", "args": {"question": "Who?"}, "id": "b", "type": "tool_call"},
        ],
    )
    graph, model, config = build([turn, call("ask_teller", {"question": "Where?"}, "c"), DONE])
    pending = start(graph, config)
    with pytest.raises(ValueError, match="non-empty string 'question'"):
        interrupt_to_event(pending)
    graph.invoke(Command(resume=reject_to_resume(pending, "not shown to the family: empty question")), config)
    by_id = {m.tool_call_id: m for m in tool_messages(model)}
    assert {k: "not shown to the family" in m.content for k, m in by_id.items()} == {"a": True, "b": True}
    # The model asked again; that pause is a normal question.
    (again,) = graph.get_state(config).interrupts
    assert interrupt_to_event(again) == ("question", schemas.QuestionEvent(text="Where?"))
