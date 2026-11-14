"""The studio assembly: backend routing, sub-agent delegation, interrupts, persistence.

Every run here uses ScriptedChatModel, InMemorySaver and InMemoryStore: no keys, no network.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore
from langgraph.types import Command

from agent import studio
from agent.fake_llm import ScriptedChatModel, last_tool_message, system_prompt_of, tool_call

STUDIO_PROMPT = "You are the STUDIO. Plan, then delegate."
DIRECTOR_PROMPT = "You are the DIRECTOR. Write /screenplay.json."
INTERVIEWER_PROMPT = "You are the INTERVIEWER. Ask the teller what is missing."

SCREENPLAY = {"version": 1, "shots": [{"id": "shot1", "actions": []}]}

# ---------------------------------------------------------------- dummies


ask_teller_calls: list[str] = []


@tool
def ask_teller(question: str) -> str:
    """Ask the person telling the story a question and wait for the answer."""
    ask_teller_calls.append(question)
    return "the tool body should never run: the teller answers through the interrupt"


@tool
def find_prop(description: str) -> str:
    """Find a saved prop that matches a description."""
    return f"no prop matches {description!r}"


TOOLS = [ask_teller, find_prop]

DIRECTOR = {
    "name": "director",
    "description": "Writes the screenplay.",
    "system_prompt": DIRECTOR_PROMPT,
    "tools": [],
}
INTERVIEWER = {
    "name": "interviewer",
    "description": "Decides whether the story can be staged; asks the teller when it cannot.",
    "system_prompt": INTERVIEWER_PROMPT,
    "tools": [ask_teller],
}


def done(text: str = "done") -> AIMessage:
    return AIMessage(content=text)


def turns(messages: list[BaseMessage]) -> int:
    """How many tool results this agent has seen: which step of its script it is on."""
    return sum(isinstance(m, ToolMessage) for m in messages)


def make_studio(
    responder: Callable[[list[BaseMessage], list[str]], AIMessage],
    *,
    family_id: str = "fam_a",
    store: InMemoryStore | None = None,
    checkpointer: InMemorySaver | None = None,
    subagents: list[dict[str, Any]] | None = None,
    skills_dir: Path = studio.SKILLS_DIR,
):
    model = ScriptedChatModel(responder=responder)
    graph = studio.build_studio(
        family_id=family_id,
        model=model,
        tools=TOOLS,
        subagents=subagents if subagents is not None else [DIRECTOR, INTERVIEWER],
        interrupt_on={"ask_teller": True, "merge_people": True, "confirm_props": True},
        checkpointer=checkpointer or InMemorySaver(),
        store=store or InMemoryStore(),
        system_prompt=STUDIO_PROMPT,
        skills_dir=skills_dir,
    )
    return graph, model


def start(text: str = "Dad passed the hymn books down the pew.") -> dict[str, Any]:
    return {"messages": [HumanMessage(content=text)]}


def file_text(files: dict[str, Any], path: str) -> str:
    content = files[path]["content"]
    return "\n".join(content) if isinstance(content, list) else content


@pytest.fixture
def skills_dir(tmp_path: Path) -> Path:
    """A skills folder with valid frontmatter (the real SKILL.md files are another task's)."""
    for name, description in [
        ("popup-style", "The locked cut-paper pop-up look."),
        ("storyboarding", "Turn a story into a shot list."),
        ("directing", "The action vocabulary and how to stage a screenplay."),
    ]:
        folder = tmp_path / name
        folder.mkdir()
        (folder / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {description}\n---\n\n# {name}\n\nBody of {name}.\n")
    return tmp_path


# ---------------------------------------------------------------- the full delegation run


def test_studio_plans_delegates_and_subagent_files_reach_parent_state() -> None:
    def responder(messages: list[BaseMessage], tool_names: list[str]) -> AIMessage:
        prompt = system_prompt_of(messages)
        if prompt.startswith("You are the STUDIO"):
            assert "task" in tool_names
            step = turns(messages)
            if step == 0:
                return tool_call("write_todos", {"todos": [
                    {"content": "Direct the screenplay", "status": "in_progress"},
                    {"content": "Save the memory", "status": "pending"},
                ]})
            if step == 1:
                return tool_call("task", {"subagent_type": "director", "description": "Stage the hymn-book story."})
            return done("studio finished")
        if prompt.startswith("You are the DIRECTOR"):
            assert "task" not in tool_names and "write_file" in tool_names
            if turns(messages) == 0:
                return tool_call("write_file", {"file_path": "/screenplay.json", "content": json.dumps(SCREENPLAY)})
            return done("screenplay written")
        raise AssertionError(f"unexpected agent: {prompt[:80]!r}")

    graph, model = make_studio(responder)
    config = studio.thread_config("run-delegate")
    result = graph.invoke(start(), config)

    assert result["messages"][-1].content == "studio finished"
    assert [t["content"] for t in result["todos"]] == ["Direct the screenplay", "Save the memory"]
    assert json.loads(file_text(result["files"], "/screenplay.json")) == SCREENPLAY
    task_result = next(m for m in result["messages"] if isinstance(m, ToolMessage) and m.name == "task")
    assert "screenplay written" in task_result.text

    # The responder served both agents, told apart by prompt and bound tools.
    served = [system_prompt_of(msgs).split(".")[0] for _, msgs in model.calls]
    assert served == ["You are the STUDIO"] * 2 + ["You are the DIRECTOR"] * 2 + ["You are the STUDIO"]

    # The checkpointed thread holds the same files.
    assert "/screenplay.json" in graph.get_state(config).values["files"]


def test_thread_config_has_thread_and_recursion_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("STITCH_MAX_GRAPH_STEPS", raising=False)
    config = studio.thread_config("run-1")
    assert config["configurable"] == {"thread_id": "run-1"}
    assert config["recursion_limit"] == studio.RECURSION_LIMIT == 1000


def test_thread_config_reads_the_step_limit_from_env(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("STITCH_MAX_GRAPH_STEPS", "250")
    assert studio.thread_config("run-1")["recursion_limit"] == 250
    caplog.set_level("WARNING", logger="agent.guards")
    monkeypatch.setenv("STITCH_MAX_GRAPH_STEPS", "10")  # below the minimum: the default, with a warning
    assert studio.thread_config("run-1")["recursion_limit"] == studio.RECURSION_LIMIT
    assert any("STITCH_MAX_GRAPH_STEPS" in r.getMessage() for r in caplog.records)


def test_the_step_limit_stops_a_looping_studio(monkeypatch: pytest.MonkeyPatch) -> None:
    from langgraph.errors import GraphRecursionError

    monkeypatch.setenv("STITCH_MAX_GRAPH_STEPS", "50")
    # A studio that never stops calling a tool.
    looping = ScriptedChatModel(responder=lambda _messages, _tools: tool_call("find_prop", {"description": "a cup"}))
    graph = studio.build_studio(
        family_id="fam_loop", model=looping, tools=[find_prop], subagents=[], interrupt_on={},
        checkpointer=InMemorySaver(), store=InMemoryStore(), system_prompt=STUDIO_PROMPT,
    )
    with pytest.raises(GraphRecursionError):
        graph.invoke({"messages": [HumanMessage(content="go")]}, studio.thread_config("run-loop"))
    assert 0 < len(looping.calls) < 50  # each model turn plus its tool call is at least 2 steps


# ---------------------------------------------------------------- skills


def test_real_skill_file_is_readable_through_the_skills_route() -> None:
    def responder(messages: list[BaseMessage], tool_names: list[str]) -> AIMessage:
        if turns(messages) == 0:
            return tool_call("read_file", {"file_path": "/skills/directing/SKILL.md"})
        return done()

    graph, _ = make_studio(responder)
    result = graph.invoke(start(), studio.thread_config("run-real-skill"))
    read = last_tool_message(result["messages"])
    assert read is not None and read.status != "error"
    expected_line = (studio.SKILLS_DIR / "directing" / "SKILL.md").read_text().strip().splitlines()[0]
    assert expected_line in read.text


def test_skills_are_listed_readable_and_read_only(skills_dir: Path) -> None:
    def responder(messages: list[BaseMessage], tool_names: list[str]) -> AIMessage:
        step = turns(messages)
        if step == 0:
            return tool_call("read_file", {"file_path": "/skills/directing/SKILL.md"})
        if step == 1:
            return tool_call("write_file", {"file_path": "/skills/directing/notes.md", "content": "overwrite"})
        if step == 2:
            return tool_call("edit_file", {"file_path": "/skills/directing/SKILL.md", "old_string": "Body", "new_string": "Gone"})
        return done()

    graph, model = make_studio(responder, skills_dir=skills_dir)
    result = graph.invoke(start(), studio.thread_config("run-skills"))

    # The skills middleware lists all three skills in the studio's system prompt.
    first_prompt = system_prompt_of(model.calls[0][1])
    for name in ("popup-style", "storyboarding", "directing"):
        assert name in first_prompt
    assert "/skills/directing/SKILL.md" in first_prompt

    read, write, edit = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    assert "Body of directing." in read.text
    for refused in (write, edit):
        assert refused.status == "error" or "denied" in refused.text.lower() or "read-only" in refused.text.lower()
    assert not (skills_dir / "directing" / "notes.md").exists()
    assert "Body of directing." in (skills_dir / "directing" / "SKILL.md").read_text()
    assert "/skills/directing/notes.md" not in result.get("files", {})


def test_read_only_backend_refuses_writes_directly(skills_dir: Path) -> None:
    backend = studio.make_backend(family_id="fam_a", skills_dir=skills_dir)
    assert backend.write("/skills/directing/x.md", "x").error
    assert backend.edit("/skills/directing/SKILL.md", "Body", "Gone").error
    assert backend.upload_files([("/skills/directing/x.md", b"x")])[0].error
    assert not (skills_dir / "directing" / "x.md").exists()


def test_subagents_get_the_skills_too(skills_dir: Path) -> None:
    def responder(messages: list[BaseMessage], tool_names: list[str]) -> AIMessage:
        prompt = system_prompt_of(messages)
        if prompt.startswith("You are the STUDIO"):
            if turns(messages) == 0:
                return tool_call("task", {"subagent_type": "director", "description": "Stage it."})
            return done()
        # skills listed for the director as well, with the path it must read
        assert "/skills/directing/SKILL.md" in prompt
        assert "No skills available" not in prompt
        return done("ok")

    graph, _ = make_studio(responder, skills_dir=skills_dir)
    graph.invoke(start(), studio.thread_config("run-sub-skills"))


# ---------------------------------------------------------------- long-term memory


def test_memories_land_in_the_family_namespace_and_stay_there() -> None:
    store = InMemoryStore()
    note = "Dad walks slowly and always carries the hymn books."

    def writer(messages: list[BaseMessage], tool_names: list[str]) -> AIMessage:
        if turns(messages) == 0:
            return tool_call("write_file", {"file_path": "/memories/people/dad.md", "content": note})
        return done()

    graph_a, _ = make_studio(writer, family_id="fam_a", store=store)
    result = graph_a.invoke(start(), studio.thread_config("run-mem-write"))
    assert last_tool_message(result["messages"]).status != "error"
    # Not a draft: it went to the store, not to the thread's files.
    assert "/memories/people/dad.md" not in result.get("files", {})

    items = store.search(("families",), limit=100)
    assert len(items) == 1
    assert items[0].namespace == studio.memories_namespace("fam_a")
    assert "fam_a" in items[0].namespace
    assert note in json.dumps(items[0].value)

    def reader(messages: list[BaseMessage], tool_names: list[str]) -> AIMessage:
        step = turns(messages)
        if step == 0:
            return tool_call("read_file", {"file_path": "/memories/people/dad.md"})
        if step == 1:
            return tool_call("ls", {"path": "/memories/"})
        return done()

    # Same family, new run (new thread): the note persists.
    graph_a2, _ = make_studio(reader, family_id="fam_a", store=store)
    result = graph_a2.invoke(start(), studio.thread_config("run-mem-read-a"))
    read_a = [m for m in result["messages"] if isinstance(m, ToolMessage)][0]
    assert note in read_a.text

    # Another family on the same store sees nothing.
    graph_b, _ = make_studio(reader, family_id="fam_b", store=store)
    result = graph_b.invoke(start(), studio.thread_config("run-mem-read-b"))
    read_b, ls_b = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    assert note not in read_b.text
    assert "not found" in read_b.text.lower() or read_b.status == "error"
    assert "dad.md" not in ls_b.text


@pytest.mark.parametrize("bad", ["", "*", "fam/a", "fam a", "a?b"])
def test_family_id_must_be_a_safe_namespace_component(bad: str) -> None:
    with pytest.raises(ValueError, match="family_id"):
        studio.make_backend(family_id=bad)


# ---------------------------------------------------------------- interrupts


def respond(message: str) -> Command:
    return Command(resume={"decisions": [{"type": "respond", "message": message}]})


def test_ask_teller_pauses_the_studio_and_resumes_the_same_thread() -> None:
    ask_teller_calls.clear()

    def responder(messages: list[BaseMessage], tool_names: list[str]) -> AIMessage:
        last = last_tool_message(messages)
        if last is None:
            return tool_call("ask_teller", {"question": "Which church was it?"})
        return done(f"heard: {last.text}")

    graph, _ = make_studio(responder)
    config = studio.thread_config("run-ask-studio")
    paused = graph.invoke(start(), config)

    interrupts = paused["__interrupt__"]
    assert len(interrupts) == 1
    request = interrupts[0].value
    assert request["action_requests"][0]["name"] == "ask_teller"
    assert request["action_requests"][0]["args"] == {"question": "Which church was it?"}
    assert graph.get_state(config).next  # paused, not finished

    resumed = graph.invoke(respond("St. Mark's, in 1962."), config)
    assert "__interrupt__" not in resumed
    assert resumed["messages"][-1].content == "heard: St. Mark's, in 1962."
    assert ask_teller_calls == []  # answered by the teller, the tool body never ran
    assert not graph.get_state(config).next


def test_ask_teller_pauses_inside_a_subagent_and_resumes_the_same_thread() -> None:
    ask_teller_calls.clear()

    def responder(messages: list[BaseMessage], tool_names: list[str]) -> AIMessage:
        prompt = system_prompt_of(messages)
        last = last_tool_message(messages)
        if prompt.startswith("You are the STUDIO"):
            if last is None:
                return tool_call("task", {"subagent_type": "interviewer", "description": "Can this be staged?"})
            return done(f"studio got: {last.text}")
        if prompt.startswith("You are the INTERVIEWER"):
            if last is None:
                return tool_call("ask_teller", {"question": "Who was holding the books?"})
            return done(f"teller said {last.text}")
        raise AssertionError(prompt[:80])

    graph, _ = make_studio(responder)
    config = studio.thread_config("run-ask-sub")
    paused = graph.invoke(start(), config)

    interrupts = paused["__interrupt__"]
    assert len(interrupts) == 1
    assert interrupts[0].value["action_requests"][0]["name"] == "ask_teller"

    resumed = graph.invoke(respond("Mom was."), config)
    assert "__interrupt__" not in resumed
    assert resumed["messages"][-1].content == "studio got: teller said Mom was."
    assert ask_teller_calls == []
    assert not graph.get_state(config).next


def test_async_stream_pauses_in_a_subagent_and_resumes() -> None:
    """runs.py drives the studio with astream; the same pause/resume must hold there."""

    def responder(messages: list[BaseMessage], tool_names: list[str]) -> AIMessage:
        prompt = system_prompt_of(messages)
        last = last_tool_message(messages)
        if prompt.startswith("You are the STUDIO"):
            if last is None:
                return tool_call("task", {"subagent_type": "interviewer", "description": "Can this be staged?"})
            return done(f"studio got: {last.text}")
        if last is None:
            return tool_call("ask_teller", {"question": "Where were you sitting?"})
        return done(f"teller said {last.text}")

    graph, _ = make_studio(responder)
    config = studio.thread_config("run-ask-async")

    async def drive() -> tuple[list[Any], dict[str, Any]]:
        interrupts: list[Any] = []
        async for chunk in graph.astream(start(), config, stream_mode="updates", subgraphs=True):
            _namespace, update = chunk
            interrupts.extend(update.get("__interrupt__", ()))
        async for _ in graph.astream(respond("Third pew."), config, stream_mode="updates"):
            pass
        return interrupts, (await graph.aget_state(config)).values

    interrupts, values = asyncio.run(drive())
    # With subgraphs=True the same interrupt surfaces in the sub-agent's namespace
    # and again at the root: a consumer must de-duplicate by interrupt id.
    assert len({i.id for i in interrupts}) == 1
    assert {i.value["action_requests"][0]["name"] for i in interrupts} == {"ask_teller"}
    assert values["messages"][-1].content == "studio got: teller said Third pew."


def test_subagent_with_its_own_interrupt_config_keeps_the_studio_gates() -> None:
    spec = {**INTERVIEWER, "interrupt_on": {"find_prop": True}}
    prepared = studio._prepare_subagent(spec, {"ask_teller": True, "confirm_props": True})
    assert prepared["interrupt_on"] == {"ask_teller": True, "confirm_props": True, "find_prop": True}
    assert prepared["skills"] == studio.SKILL_SOURCES
    assert "interrupt_on" not in studio._prepare_subagent(DIRECTOR, {"ask_teller": True})  # inherits by default


def test_subagent_with_a_single_skill_folder_source_is_rejected() -> None:
    with pytest.raises(ValueError, match="single skill folder"):
        studio._prepare_subagent({**DIRECTOR, "skills": ["/skills/directing/"]}, {})
    assert studio._prepare_subagent({**DIRECTOR, "skills": ["/skills/"]}, {})["skills"] == ["/skills/"]


# ---------------------------------------------------------------- fake model


def test_scripted_model_records_bound_tools_per_copy() -> None:
    seen: list[list[str]] = []

    def responder(messages: list[BaseMessage], tool_names: list[str]) -> AIMessage:
        seen.append(tool_names)
        return done("hi")

    model = ScriptedChatModel(responder=responder)
    bound = model.bind_tools([ask_teller, find_prop])
    assert bound.invoke([HumanMessage(content="x")]).content == "hi"
    assert model.invoke([HumanMessage(content="x")]).content == "hi"
    assert seen == [["ask_teller", "find_prop"], []]
    assert len(model.calls) == 2  # the log is shared between the model and its bound copy
    assert model._llm_type == "scripted"


def test_tool_call_helper_builds_unique_ids() -> None:
    a = tool_call("find_prop", {"description": "hymn book"})
    b = tool_call("find_prop", {"description": "hymn book"})
    assert a.tool_calls[0]["name"] == "find_prop" and a.tool_calls[0]["args"] == {"description": "hymn book"}
    assert a.tool_calls[0]["id"] != b.tool_calls[0]["id"]
    assert tool_call("x", {}, id="fixed").tool_calls[0]["id"] == "fixed"
    assert last_tool_message([HumanMessage(content="x")]) is None


# ---------------------------------------------------------------- production wiring


def test_make_model_requires_the_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        studio.make_model()


def test_make_model_reads_the_model_id_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-a-real-key")  # constructing makes no request
    monkeypatch.setenv("STITCH_MODEL", "claude-haiku-5-5")
    assert studio.make_model().model == "claude-haiku-5-5"
    monkeypatch.delenv("STITCH_MODEL")
    assert studio.make_model().model == studio.DEFAULT_MODEL
    assert studio.make_model("claude-sonnet-5-5").model == "claude-sonnet-5-5"


def test_make_model_bounds_each_request(monkeypatch: pytest.MonkeyPatch) -> None:
    from langchain_anthropic import ChatAnthropic

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-a-real-key")  # constructing makes no request
    monkeypatch.delenv("STITCH_MODEL_TIMEOUT_S", raising=False)
    monkeypatch.delenv("STITCH_MODEL_MAX_RETRIES", raising=False)
    chat = studio.make_model()
    assert isinstance(chat, ChatAnthropic)
    assert chat.default_request_timeout == 120.0 and chat.max_retries == 2
    assert chat._client_params["timeout"] == 120.0 and chat._client_params["max_retries"] == 2

    monkeypatch.setenv("STITCH_MODEL_TIMEOUT_S", "30")
    monkeypatch.setenv("STITCH_MODEL_MAX_RETRIES", "0")
    chat = studio.make_model("claude-sonnet-5-5")
    assert chat.default_request_timeout == 30.0 and chat.max_retries == 0
    assert chat._client_params["timeout"] == 30.0 and chat._client_params["max_retries"] == 0


@pytest.mark.skipif(not os.environ.get("MONGODB_URI_TEST"), reason="MONGODB_URI_TEST is not set")
def test_mongo_persistence_runs_and_resumes_a_studio() -> None:
    db_name = f"stitch_test_{uuid.uuid4().hex[:8]}"
    saver, store = studio.mongo_persistence(os.environ["MONGODB_URI_TEST"], db_name=db_name)
    try:
        def responder(messages: list[BaseMessage], tool_names: list[str]) -> AIMessage:
            step = turns(messages)
            if step == 0:
                return tool_call("write_file", {"file_path": "/memories/style.md", "content": "warm paper"})
            if step == 1:
                return tool_call("ask_teller", {"question": "What year?"})
            return done()

        graph = studio.build_studio(
            family_id="fam_mongo", model=ScriptedChatModel(responder=responder), tools=TOOLS, subagents=[],
            interrupt_on={"ask_teller": True}, checkpointer=saver, store=store, system_prompt=STUDIO_PROMPT,
        )
        config = studio.thread_config("run-mongo")
        assert graph.invoke(start(), config)["__interrupt__"]
        assert not graph.invoke(respond("1962"), config).get("__interrupt__")
        assert store.search(studio.memories_namespace("fam_mongo"))
    finally:
        saver.client.drop_database(db_name)
