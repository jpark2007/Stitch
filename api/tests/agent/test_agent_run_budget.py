"""The per-run model call cap (STITCH_MAX_MODEL_CALLS) and the image cap, across sub-agents,
resumes and a restart.

Every run here is built the production way, through `wiring.build_run_graph` and
`wiring.make_manager` over `wiring.fake_services(model=ScriptedChatModel)`: the real studio
graph, the real eight sub-agents, the real RunManager, in memory and offline (no keys, no
network). Every wait has a timeout.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from typing import Any

import pytest
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from pydantic import Field

import schemas
from agent import guards, runs, wiring
from agent.fake_llm import ScriptedChatModel as _ScriptedChatModel
from agent.fake_llm import tool_call
from agent.repo import InMemoryRepo, load_sample_family
from agent.run_info import RunInfo
from agent.tools.art import ArtService, FakeBackgroundRemover, FakeImageModel

FAMILY = "fam_budget"
USER = "user_dad"
AUTHOR = "dad"
CAP = 100
"""The model call cap of the runs here: guards.MIN_MODEL_CALLS, the lowest STITCH_MAX_MODEL_CALLS takes."""
WAIT = 60.0
FORM = schemas.SubmitMemoryFields(text="We drove to the lake in the old station wagon.", year=1979)
LIMIT_MESSAGE = f"this run reached its limit of {CAP} model calls; please try again"


class ScriptedChatModel(_ScriptedChatModel):
    """ScriptedChatModel with its call log left out of repr.

    LangChain serializes the chat model for its callbacks on every call, and a model that is not
    serializable is serialized as its repr, which includes every message of every earlier call:
    a hundred calls then take minutes. The runs here make CAP calls each, so they keep the log
    out of repr (the log itself is unchanged)."""

    calls: list[tuple[list[str], list[BaseMessage]]] = Field(default_factory=list, repr=False)


@pytest.fixture(autouse=True)
def model_call_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    assert guards.MIN_MODEL_CALLS == CAP
    monkeypatch.setenv(guards.ENV_MAX_MODEL_CALLS, str(CAP))


@pytest.fixture
def managers() -> Iterator[list[runs.RunManager]]:
    made: list[runs.RunManager] = []
    yield made
    for manager in made:
        manager.shutdown(wait=True)


def family_repo() -> InMemoryRepo:
    repo = InMemoryRepo()
    load_sample_family(repo, FAMILY)
    return repo


def is_studio(names: list[str]) -> bool:
    """Only the studio has the `task` tool."""
    return "task" in names


def studio_results(messages: list[BaseMessage]) -> int:
    return sum(isinstance(m, ToolMessage) for m in messages)


def stored_record(services: wiring.Services, run_id: str) -> dict[str, Any]:
    item = services.store.get(runs.run_namespace(FAMILY), run_id)
    assert item is not None
    return dict(item.value)


def events_of(manager: runs.RunManager, run_id: str) -> list[tuple[str, Any]]:
    return manager.events(FAMILY, run_id)


def wait_for(manager: runs.RunManager, run_id: str, status: str) -> None:
    assert manager.wait(FAMILY, run_id, timeout=WAIT) == status


# ------------------------------------------------------------------ sub-agent calls count


def test_a_looping_sub_agent_stops_at_the_model_call_cap(
    managers: list[runs.RunManager], caplog: pytest.LogCaptureFixture
) -> None:
    """The studio always delegates to the interviewer, and the interviewer always calls a tool:
    no step limit stops it (deepagents binds recursion_limit 9999 on the sub-agent). The cap does,
    counting the interviewer's calls with the studio's."""
    caplog.set_level(logging.WARNING, logger="agent.runs")

    def responder(messages: list[BaseMessage], names: list[str]) -> AIMessage:
        if is_studio(names):
            return tool_call("task", {"description": "Check the story.", "subagent_type": "interviewer"})
        assert "get_person" in names  # the interviewer, looping
        return tool_call("get_person", {"person_id": AUTHOR})

    model = ScriptedChatModel(responder=responder)
    services = wiring.fake_services(model=model, repo=family_repo())
    manager = wiring.make_manager(services)
    managers.append(manager)

    run_id = manager.start_run(FAMILY, USER, AUTHOR, FORM, [])
    wait_for(manager, run_id, "error")

    studio_calls = [names for names, _ in model.calls if is_studio(names)]
    assert len(model.calls) == CAP  # the call past the cap never reached the model
    assert len(studio_calls) == 1  # all the rest were the interviewer's
    assert services.usage.model_calls(run_id) == CAP
    events = events_of(manager, run_id)
    assert [name for name, _ in events][-1] == "error"
    assert [name for name, _ in events].count("error") == 1
    assert events[-1][1].message == LIMIT_MESSAGE == runs.run_budget_message(CAP)
    record = stored_record(services, run_id)
    assert record["status"] == "error" and record["model_calls"] == CAP
    assert any(f"reached its limit of {CAP} model calls" in r.getMessage() for r in caplog.records)


def test_the_general_purpose_sub_agent_counts_too(managers: list[runs.RunManager]) -> None:
    """deepagents adds a general-purpose sub-agent the eight specs do not declare; its calls count."""

    def responder(messages: list[BaseMessage], names: list[str]) -> AIMessage:
        if is_studio(names):
            return tool_call("task", {"description": "Look around.", "subagent_type": "general-purpose"})
        return tool_call("get_person", {"person_id": AUTHOR})

    model = ScriptedChatModel(responder=responder)
    services = wiring.fake_services(model=model, repo=family_repo())
    manager = wiring.make_manager(services)
    managers.append(manager)
    run_id = manager.start_run(FAMILY, USER, AUTHOR, FORM, [])
    wait_for(manager, run_id, "error")
    assert len(model.calls) == CAP == services.usage.model_calls(run_id)
    assert events_of(manager, run_id)[-1][1].message == LIMIT_MESSAGE


# ------------------------------------------------------------------ one count across resumes


CALLS_PER_QUESTION = 10


def asking_studio(messages: list[BaseMessage], names: list[str]) -> AIMessage:
    """Nine get_person calls, then a question, over and over: ten model calls per stream."""
    assert is_studio(names)
    since_question = 0
    for message in reversed(messages):
        if isinstance(message, ToolMessage):
            if message.name == "ask_teller":
                break
            since_question += 1
    if since_question < CALLS_PER_QUESTION - 1:
        return tool_call("get_person", {"person_id": AUTHOR})
    return tool_call("ask_teller", {"question": "Who else was in the car?"})


def test_resumes_share_one_count_and_the_run_fails_at_the_cap(managers: list[runs.RunManager]) -> None:
    """Each answer starts a new studio stream (with a fresh step budget); the model call count
    does not start over, so the run ends at CAP calls in all, not CAP calls per stream."""
    model = ScriptedChatModel(responder=asking_studio)
    services = wiring.fake_services(model=model, repo=family_repo())
    manager = wiring.make_manager(services)
    managers.append(manager)
    run_id = manager.start_run(FAMILY, USER, AUTHOR, FORM, [])

    answers = 0
    while True:
        status = manager.wait(FAMILY, run_id, timeout=WAIT)
        if status != "waiting":
            break
        assert services.usage.model_calls(run_id) == CALLS_PER_QUESTION * (answers + 1)
        assert stored_record(services, run_id)["model_calls"] == CALLS_PER_QUESTION * (answers + 1)
        manager.answer_run(FAMILY, run_id, USER, schemas.AnswerRequest(kind="answer", text=f"Aunt {answers}."))
        answers += 1
        assert answers <= CAP // CALLS_PER_QUESTION, "the run outlived its model call cap"

    assert status == "error"
    assert answers == CAP // CALLS_PER_QUESTION  # ten streams of ten calls, then the refusal
    assert len(model.calls) == CAP == services.usage.model_calls(run_id)
    names = [name for name, _ in events_of(manager, run_id)]
    assert names.count("question") == CAP // CALLS_PER_QUESTION
    assert names[-1] == "error" and names.count("error") == 1
    assert events_of(manager, run_id)[-1][1].message == LIMIT_MESSAGE
    assert stored_record(services, run_id)["model_calls"] == CAP


# ------------------------------------------------------------------ across a restart


IMAGE_CAP = 2


def restart_studio(messages: list[BaseMessage], names: list[str]) -> AIMessage:
    """Before the restart: three props (the image cap is 2), get_person up to 60 tool results,
    then a question. After it: three more props, then get_person until the cap ends the run."""
    assert is_studio(names)
    done = studio_results(messages)
    if done < 3:
        return tool_call("generate_prop", {"name": f"basket {done}", "description": "wicker", "size": "hand"})
    if done < 60:
        return tool_call("get_person", {"person_id": AUTHOR})
    if done == 60:
        return tool_call("ask_teller", {"question": "What color was the station wagon?"})
    if done < 64:
        return tool_call("generate_prop", {"name": f"rod {done}", "description": "bamboo", "size": "hand"})
    return tool_call("get_person", {"person_id": AUTHOR})


def test_both_caps_hold_across_a_restart(managers: list[runs.RunManager]) -> None:
    """Finding 4 and the model call cap together: the run record keeps model_calls and
    images_used, and a new process (new RunManager, new ArtService, a fresh usage registry over
    the same store and checkpointer) goes on from them. Three cutouts before and three after the
    restart, image cap 2: exactly 2 image model calls in all; and the run ends at CAP model calls
    in all, not CAP after the restart."""
    model = ScriptedChatModel(responder=restart_studio)
    images = FakeImageModel()
    art1 = ArtService(images, FakeBackgroundRemover(), max_images_per_run=IMAGE_CAP)
    first = wiring.fake_services(model=model, repo=family_repo(), art=art1)
    assert first.usage is art1.usage
    manager1 = wiring.make_manager(first)
    managers.append(manager1)
    run_id = manager1.start_run(FAMILY, USER, AUTHOR, FORM, [])
    wait_for(manager1, run_id, "waiting")

    record = stored_record(first, run_id)
    assert record["status"] == "waiting"
    assert record["model_calls"] == 61 and record["images_used"] == IMAGE_CAP
    assert len(images.calls) == IMAGE_CAP and len(model.calls) == 61
    manager1.shutdown(wait=True)

    # The API restarts: everything in memory is gone but the store and the checkpoints.
    art2 = ArtService(images, FakeBackgroundRemover(), max_images_per_run=IMAGE_CAP)
    second = wiring.Services(**{**first.__dict__, "art": art2, "usage": art2.usage})
    assert second.usage is not first.usage and second.usage.counts(run_id) == (0, 0)
    manager2 = wiring.make_manager(second)
    managers.append(manager2)

    assert manager2.status(FAMILY, run_id) == "waiting"  # restored, and seeded from the record
    assert second.usage.counts(run_id) == (61, IMAGE_CAP)
    assert art2.images_left(run_id) == 0
    manager2.answer_run(FAMILY, run_id, USER, schemas.AnswerRequest(kind="answer", text="Green."))
    wait_for(manager2, run_id, "error")

    assert len(images.calls) == IMAGE_CAP  # the three props after the restart are placeholders
    assert len(model.calls) == CAP  # 61 before, 39 after: one count across the restart
    assert second.usage.counts(run_id) == (CAP, IMAGE_CAP)
    final = stored_record(second, run_id)
    assert final["status"] == "error" and final["model_calls"] == CAP and final["images_used"] == IMAGE_CAP
    assert events_of(manager2, run_id)[-1][1].message == LIMIT_MESSAGE
    placeholder_notes = [
        json.loads(m.content) for m in model.calls[-1][1]
        if isinstance(m, ToolMessage) and m.name == "generate_prop"
    ]
    assert [p["art"] for p in placeholder_notes] == ["generated", "generated", "placeholder"] + ["placeholder"] * 3


def test_a_running_run_cut_short_is_redriven_from_its_persisted_count(managers: list[runs.RunManager]) -> None:
    """A run that was running when the process died is driven again from its last checkpoint;
    its count is seeded from the record before the redrive, so the redrive cannot reach the
    model more than the cap allows."""

    def responder(messages: list[BaseMessage], names: list[str]) -> AIMessage:
        if is_studio(names):
            return tool_call("get_person", {"person_id": AUTHOR})
        raise AssertionError("no sub-agent here")

    model = ScriptedChatModel(responder=responder)
    services = wiring.fake_services(model=model, repo=family_repo())
    run_id = "run_redrive"
    info = RunInfo(run_id=run_id, family_id=FAMILY, user_id=USER, author_id=AUTHOR, fields=FORM)
    record = {
        "run_id": run_id, "family_id": FAMILY, "user_id": USER, "author_id": AUTHOR,
        "fields": info.fields.model_dump_json(by_alias=True), "photo_ids": [], "status": "running",
        "memory_id": None, "pending_interrupt_id": None, "answered": None, "malformed_pauses": 0,
        "elapsed_s": 0.0, "run_timeout_s": 1200.0, "model_calls": CAP - 5, "images_used": 1,
        "tasks": [], "ended_tasks": [], "events": [],
    }
    services.store.put(runs.run_namespace(FAMILY), run_id, record, index=False)
    manager = wiring.make_manager(services)
    managers.append(manager)

    assert manager.status(FAMILY, run_id) in ("running", "error")  # restored, redriving
    wait_for(manager, run_id, "error")
    assert len(model.calls) == 5  # only what the cap had left
    assert services.usage.counts(run_id) == (CAP, 1)
    assert stored_record(services, run_id)["model_calls"] == CAP
    assert events_of(manager, run_id)[-1][1].message == LIMIT_MESSAGE


@pytest.mark.parametrize(("value", "expected"), [(None, 0), ("lots", 0), (-3, 0), (True, 0), (7, 7)])
def test_old_or_unreadable_counts_in_a_record_restore_as_nothing_used(
    managers: list[runs.RunManager], value: Any, expected: int
) -> None:
    model = ScriptedChatModel(responder=asking_studio)
    services = wiring.fake_services(model=model, repo=family_repo())
    manager = wiring.make_manager(services)
    managers.append(manager)
    run_id = manager.start_run(FAMILY, USER, AUTHOR, FORM, [])
    wait_for(manager, run_id, "waiting")
    manager.shutdown(wait=True)

    record = stored_record(services, run_id)
    if value is None:
        del record["model_calls"]
        del record["images_used"]
    else:
        record["model_calls"] = value
        record["images_used"] = value
    services.store.put(runs.run_namespace(FAMILY), run_id, record, index=False)

    art = ArtService(None, None)
    fresh = wiring.Services(**{**services.__dict__, "art": art, "usage": art.usage})
    restored = wiring.make_manager(fresh)
    managers.append(restored)
    assert restored.status(FAMILY, run_id) == "waiting"
    assert fresh.usage.counts(run_id) == (expected, expected)


# ------------------------------------------------------------------ the error path itself


def test_budget_exceeded_is_found_behind_a_wrapping_exception() -> None:
    inner = guards.RunBudgetExceeded("r", 7)
    try:
        try:
            raise inner
        except guards.RunBudgetExceeded as exc:
            raise RuntimeError("a tool failed") from exc
    except RuntimeError as outer:
        assert runs.budget_exceeded(outer) is inner
    assert runs.budget_exceeded(ValueError("other")) is None
    assert runs.run_budget_message(7) == "this run reached its limit of 7 model calls; please try again"


def test_without_a_run_the_graph_has_no_budget() -> None:
    """build_studio binds the budget only for a run (run_id and usage); a bare graph is unchanged."""
    from agent import studio

    model = ScriptedChatModel(responder=lambda m, n: AIMessage(content="done"))
    services = wiring.fake_services(model=model)
    graph = studio.build_studio(
        family_id=FAMILY, model=model, tools=[], subagents=[], interrupt_on={},
        checkpointer=services.checkpointer, store=services.store, system_prompt="s",
    )
    assert not any(isinstance(cb, guards.ModelCallBudget) for cb in (graph.config.get("callbacks") or []))
    run = RunInfo(run_id="run_x", family_id=FAMILY, user_id=USER, author_id=AUTHOR, fields=FORM)
    built = wiring.build_run_graph(run, wiring.fake_services(model=model, repo=family_repo()))
    budgets = [cb for cb in built.config["callbacks"] if isinstance(cb, guards.ModelCallBudget)]
    assert len(budgets) == 1 and budgets[0].run_id == "run_x" and budgets[0].limit == CAP


def test_services_reject_a_usage_registry_the_art_service_does_not_count_in() -> None:
    model = ScriptedChatModel(responder=lambda m, n: AIMessage(content="done"))
    services = wiring.fake_services(model=model)
    assert services.usage is services.art.usage
    with pytest.raises(ValueError, match="art service's registry"):
        wiring.Services(**{**services.__dict__, "usage": guards.RunUsage()})
