"""agent/wiring.py: per-run tools, the studio graph, scene lookup, family isolation, the manager.

Everything runs on `fake_services`: ScriptedChatModel, InMemoryRepo seeded from the
hymn_books sample, InMemorySaver / InMemoryStore, placeholder art, FakeTTS, FakeVision,
and the deterministic compiler. No keys, no network.
"""

from __future__ import annotations

import asyncio
import io
import json
from collections.abc import Callable
from typing import Any

import pytest
from deepagents.backends.utils import create_file_data, file_data_to_string
from deepagents.middleware.filesystem import FilesystemState
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode
from PIL import Image

import schemas
from agent import runs, studio, subagents, wiring
from agent.fake_llm import ScriptedChatModel, tool_call
from agent.repo import SAMPLE_MEMORY_PATH, InMemoryRepo, load_sample_family
from agent.run_info import RunInfo
from agent.tools import ask
from agent.tools.compile import SceneLookupError
from agent.tools.frames import FakeFrameCritic, FakeFrameRenderer
from agent.tools.photos import UNKNOWN_PHOTO
from screenplay.schema import Screenplay, Timeline

FAMILY = "fam_wiring"
OTHER = "fam_other"
USER = "user_dad"

SAMPLE_RAW = json.loads(SAMPLE_MEMORY_PATH.read_text())
SAMPLE = schemas.MemoryResponse.model_validate(SAMPLE_RAW)
SCREENPLAY_RAW: dict[str, Any] = SAMPLE_RAW["memory"]["screenplay"]
TIMELINE_RAW: dict[str, Any] = SAMPLE_RAW["memory"]["timeline"]
SCREENPLAY_JSON = json.dumps(SCREENPLAY_RAW)

EXPECTED_TOOLS = {
    "get_person",
    "find_memories",
    "link_participants",
    "save_memory",
    "ask_teller",
    "merge_people",
    "describe_photo",
    "find_prop",
    "generate_prop",
    "confirm_props",
    "find_set",
    "generate_set_layer",
    "cut_out",
    "compile_screenplay",
    "synthesize_narration",
}

Step = Callable[[list[BaseMessage]], AIMessage] | AIMessage


# ------------------------------------------------------------------ helpers


def png(color: tuple[int, int, int] = (200, 40, 40)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (24, 16), color).save(out, format="PNG")
    return out.getvalue()


def seeded_repo() -> InMemoryRepo:
    """FAMILY and OTHER both hold the hymn_books people, props and sets; OTHER also has 'zed'."""
    repo = InMemoryRepo()
    load_sample_family(repo, FAMILY)
    load_sample_family(repo, OTHER)
    dad = next(p for p in SAMPLE.people if p.id == "dad")
    zed = dad.model_copy(update={"id": "zed", "family_id": OTHER, "name": "Zed", "parent_ids": [], "spouse_ids": []})
    repo.add_person(OTHER, zed)
    return repo


def run_info(*, family_id: str = FAMILY, author_id: str = "dad", memory_id: str | None = None,
             photo_ids: tuple[str, ...] = (), run_id: str = "run_1") -> RunInfo:
    fields = schemas.SubmitMemoryFields(
        text="Dad passed the hymn books down the pew to Mom.", year=1988, memory_id=memory_id
    )
    return RunInfo(run_id=run_id, family_id=family_id, user_id=USER, author_id=author_id,
                   fields=fields, photo_ids=photo_ids)


def idle_model() -> ScriptedChatModel:
    return ScriptedChatModel(responder=lambda _m, _t: AIMessage(content="nothing to do"))


def services(repo: InMemoryRepo | None = None, model: ScriptedChatModel | None = None) -> wiring.Services:
    return wiring.fake_services(model=model or idle_model(), repo=repo or seeded_repo())


def studio_script(*steps: Step) -> Callable[[list[BaseMessage], list[str]], AIMessage]:
    """A studio that plays `steps` in order, one per tool result it has seen. Only the studio
    (the agent bound with the `task` tool) may be called."""

    def responder(messages: list[BaseMessage], tool_names: list[str]) -> AIMessage:
        assert "task" in tool_names, f"only the studio is scripted, got tools {tool_names}"
        seen = sum(isinstance(m, ToolMessage) for m in messages)
        step = steps[min(seen, len(steps) - 1)]
        return step(messages) if callable(step) else step

    return responder


def run_tool(tool: Any, files: dict[str, Any], args: dict[str, Any] | None = None) -> dict[str, Any]:
    """Run one tool the way the agent does: a ToolNode over deepagents' file state."""
    graph = StateGraph(FilesystemState)
    graph.add_node("tools", ToolNode([tool]))
    graph.add_edge(START, "tools")
    graph.add_edge("tools", END)
    message = AIMessage(content="", tool_calls=[{"name": tool.name, "args": args or {}, "id": "call_1"}])
    return graph.compile().invoke({"messages": [message], "files": files})


def sample_files() -> dict[str, Any]:
    return {
        "/screenplay.json": create_file_data(json.dumps(SCREENPLAY_RAW)),
        "/timeline.json": create_file_data(json.dumps(TIMELINE_RAW)),
    }


# ------------------------------------------------------------------ tools


def test_build_run_tools_returns_every_tool_by_name() -> None:
    tools = wiring.build_run_tools(run_info(), services())
    assert EXPECTED_TOOLS <= set(tools)
    for name, tool in tools.items():
        assert tool.name == name
    assert tools["ask_teller"] is ask.ask_teller
    assert tools["merge_people"] is ask.merge_people


def test_build_run_tools_includes_the_frame_tools() -> None:
    tools = wiring.build_run_tools(run_info(), services())
    assert {"render_frames", "inspect_frames"} <= set(tools)
    assert set(wiring.STUDIO_TOOL_NAMES) <= set(tools)


@pytest.mark.parametrize("missing", ["renderer", "critic"])
def test_frame_tools_are_left_out_without_a_renderer_or_critic(missing: str) -> None:
    base = services()
    without = wiring.Services(**{**base.__dict__, missing: None})
    tools = wiring.build_run_tools(run_info(), without)
    assert "render_frames" not in tools
    assert "inspect_frames" not in tools
    assert EXPECTED_TOOLS <= set(tools)


def test_studio_tool_names_match_the_architecture() -> None:
    assert wiring.STUDIO_TOOL_NAMES == (
        "get_person", "find_memories", "save_memory", "link_participants", "describe_photo", "find_prop",
        "generate_prop", "find_set", "generate_set_layer", "cut_out", "compile_screenplay", "render_frames",
        "inspect_frames", "synthesize_narration", "ask_teller",
    )


def test_fake_services_are_all_offline_fakes() -> None:
    svc = services()
    assert isinstance(svc.repo, InMemoryRepo)
    assert svc.art.image_model is None and svc.art.remover is None
    assert svc.subagent_model is None
    # The compiler is deterministic and offline whether it is the fake or screenplay.compiler.
    others = {k: v for k, v in svc.labels.items() if k != "compiler"}
    assert set(others.values()) <= {"fake", "placeholder"}, svc.labels
    assert svc.labels["art"] == "placeholder"
    assert svc.labels["renderer"] == "fake"
    assert isinstance(svc.renderer, FakeFrameRenderer)
    assert isinstance(svc.critic, FakeFrameCritic)


# ------------------------------------------------------------------ graph


def test_build_run_graph_wires_the_studio_and_subagents(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []
    real_build = studio.build_studio

    def spy(**kwargs: Any) -> Any:
        calls.append(kwargs)
        return real_build(**kwargs)

    monkeypatch.setattr(wiring.studio, "build_studio", spy)
    svc = services()
    run = run_info()
    graph = wiring.build_run_graph(run, svc)
    assert graph is not None
    (kwargs,) = calls
    tools = wiring.build_run_tools(run, svc)

    assert kwargs["family_id"] == FAMILY
    assert kwargs["model"] is svc.model
    assert kwargs["checkpointer"] is svc.checkpointer
    assert kwargs["store"] is svc.store
    assert kwargs["interrupt_on"] == ask.INTERRUPT_ON
    assert kwargs["system_prompt"] == subagents.STUDIO_PROMPT
    assert [t.name for t in kwargs["tools"]] == [n for n in wiring.STUDIO_TOOL_NAMES if n in tools]

    specs = {spec["name"]: spec for spec in kwargs["subagents"]}
    assert list(specs) == list(subagents.SUBAGENT_NAMES)
    for name, wanted in subagents.SUBAGENT_TOOLS.items():
        assert [t.name for t in specs[name]["tools"]] == [t for t in wanted if t in tools], name
        assert "model" not in specs[name]  # sub-agents use the studio's model unless one is configured


def test_build_run_graph_passes_the_subagent_model(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(wiring.studio, "build_studio", lambda **kw: calls.append(kw) or object())
    sub_model = idle_model()
    svc = wiring.Services(**{**services().__dict__, "subagent_model": sub_model})
    wiring.build_run_graph(run_info(), svc)
    assert all(spec["model"] is sub_model for spec in calls[0]["subagents"])


def test_build_run_graph_for_a_new_memory_and_a_second_telling() -> None:
    svc = services()
    first = wiring.build_run_graph(run_info(), svc)
    second = wiring.build_run_graph(run_info(author_id="mom", memory_id="memory_1", run_id="run_2"), svc)
    assert first is not second
    for graph in (first, second):
        assert isinstance(graph, CompiledStateGraph)
        assert graph.checkpointer is svc.checkpointer


def _narrate(run: RunInfo, repo: InMemoryRepo, svc: wiring.Services | None = None) -> Timeline:
    tools = wiring.build_run_tools(run, svc or services(repo))
    state = run_tool(tools["synthesize_narration"], sample_files())
    reply = state["messages"][-1]
    assert reply.content.startswith("Narrated"), reply.content
    return Timeline.model_validate_json(file_data_to_string(state["files"]["/timeline.json"]))


def save_sample_memory(repo: InMemoryRepo, memory_id: str = "memory_1", **timeline_changes: Any) -> schemas.Memory:
    """The hymn_books sample saved in FAMILY as `memory_id`, its timeline changed by `timeline_changes`."""
    memory = SAMPLE.memory.model_copy(update={"id": memory_id, "family_id": FAMILY}, deep=True)
    if timeline_changes:
        assert memory.timeline is not None
        memory.timeline = memory.timeline.model_copy(update=timeline_changes, deep=True)
    return repo.save_memory(FAMILY, memory)


class SpyCompiler:
    """Passes every call to the real compiler and counts them."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.calls: list[Screenplay] = []

    def __call__(self, screenplay: Screenplay, scene: Any) -> Any:
        self.calls.append(screenplay)
        return self.inner(screenplay, scene)


def test_narration_is_bound_to_the_teller_and_the_kind_of_run() -> None:
    assert TIMELINE_RAW["defaultNarrator"] == "dad"
    repo = seeded_repo()
    save_sample_memory(repo)

    # A second telling by mom adds her track but keeps dad as the default narrator.
    second = _narrate(run_info(author_id="mom", memory_id="memory_1"), repo)
    assert second.default_narrator == "dad"
    assert second.narrations is not None and "mom" in second.narrations

    # A new memory told by mom makes her the default narrator.
    first = _narrate(run_info(author_id="mom"), repo)
    assert first.default_narrator == "mom"

    # The audio is stored in the run's family only.
    audio = first.narrations["mom"].audio
    assert audio is not None
    stored = repo.get_media(FAMILY, audio)
    assert stored is not None and stored[1].startswith("audio/")
    assert repo.get_media(OTHER, audio) is None


def test_narration_recompiles_with_the_services_compiler() -> None:
    repo = seeded_repo()
    base = services(repo)
    spy = SpyCompiler(base.compiler)
    svc = wiring.Services(**{**base.__dict__, "compiler": spy})
    tools = wiring.build_run_tools(run_info(), svc)

    state = run_tool(tools["synthesize_narration"], sample_files())
    assert "Recompiled" in state["messages"][-1].content, state["messages"][-1].content
    # One recompile, of the screenplay with dad's spoken word timings in his lines.
    (voiced,) = spy.calls
    assert all(line.words for line in voiced.shots[0].narration)
    written = Screenplay.model_validate_json(file_data_to_string(state["files"]["/screenplay.json"]))
    assert written == voiced


def test_second_telling_narration_lays_over_the_saved_memorys_timeline() -> None:
    repo = seeded_repo()
    # The saved action starts later than the draft's: the track must follow the saved one.
    assert SAMPLE.memory.timeline is not None
    saved_shots = [
        shot.model_copy(update={"action_start": shot.action_start + 0.5}) for shot in SAMPLE.memory.timeline.shots
    ]
    saved = save_sample_memory(repo, "memory_1", shots=saved_shots)
    assert saved.timeline is not None
    base = services(repo)
    spy = SpyCompiler(base.compiler)
    svc = wiring.Services(**{**base.__dict__, "compiler": spy})

    # Mom's draft lines, uncued so they compile with her real (FakeTTS) durations.
    draft_raw = json.loads(json.dumps(SCREENPLAY_RAW))
    for line in draft_raw["narrations"]["mom"]["s1"]:
        line.pop("cue", None)
        line.pop("cueWord", None)
    files = {**sample_files(), "/screenplay.json": create_file_data(json.dumps(draft_raw))}
    tools = wiring.build_run_tools(run_info(author_id="mom", memory_id="memory_1"), svc)
    state = run_tool(tools["synthesize_narration"], files)
    reply = state["messages"][-1].content
    assert reply.startswith("Narrated") and "warning" not in reply, reply
    timeline = Timeline.model_validate_json(file_data_to_string(state["files"]["/timeline.json"]))
    assert len(spy.calls) == 1
    assert timeline.tracks == saved.timeline.tracks
    assert [s.action_start for s in timeline.shots] == [s.action_start for s in saved_shots]
    assert [s.action_start for s in timeline.shots] != [s["actionStart"] for s in TIMELINE_RAW["shots"]]
    mom = (timeline.narrations or {})["mom"]
    # Mom's first line keeps its offset from the shot's actionStart in the draft compile.
    draft = spy.calls[0]
    compiled = base.compiler(draft, wiring.scene_for(repo, FAMILY)(draft))
    assert compiled.ok and compiled.timeline is not None
    offset = compiled.timeline.narrations["mom"].lines[0].t - compiled.timeline.shots[0].action_start
    assert mom.lines[0].t == pytest.approx(saved_shots[0].action_start + offset, abs=0.01)
    assert timeline.default_narrator == "dad"

    # A second telling of a memory that is not saved is an error, not a guess.
    tools = wiring.build_run_tools(run_info(author_id="mom", memory_id="memory_404"), services(repo))
    reply = run_tool(tools["synthesize_narration"], sample_files())["messages"][-1]
    assert reply.content.startswith("Error"), reply.content


def test_studio_compiles_the_sample_screenplay() -> None:
    repo = seeded_repo()
    model = ScriptedChatModel(responder=studio_script(
        tool_call("write_file", {"file_path": "/screenplay.json", "content": SCREENPLAY_JSON}),
        tool_call("compile_screenplay", {}),
        AIMessage(content="compiled"),
    ))
    graph = wiring.build_run_graph(run_info(), services(repo, model))
    result = graph.invoke({"messages": [HumanMessage(content="stage it")]}, studio.thread_config("run_compile"))
    compiled = [m for m in result["messages"] if isinstance(m, ToolMessage) and m.name == "compile_screenplay"]
    assert len(compiled) == 1
    assert compiled[0].content.startswith("ok:"), compiled[0].content
    assert "/timeline.json" in result["files"]
    Timeline.model_validate_json(file_data_to_string(result["files"]["/timeline.json"]))


# ------------------------------------------------------------------ scene_for


def test_scene_for_returns_the_full_scene_for_the_sample() -> None:
    scene = wiring.scene_for(seeded_repo(), FAMILY)(Screenplay.model_validate(SCREENPLAY_RAW))
    assert set(scene.people) == {"dad", "mom"}
    assert set(scene.props) == {"hymn_books"}
    assert set(scene.sets) == {"church_steps_1988"}
    assert scene.people["dad"].family_id == FAMILY
    assert scene.props["hymn_books"].id == "hymn_books"
    assert scene.sets["church_steps_1988"].id == "church_steps_1988"


def _with(**changes: Any) -> Screenplay:
    data = json.loads(json.dumps(SCREENPLAY_RAW))
    for key, value in changes.items():
        if key == "set":
            data["shots"][0]["set"] = value
        else:
            data[key] = value
    return Screenplay.model_validate(data)


@pytest.mark.parametrize(
    ("screenplay", "kind", "missing"),
    [
        (lambda: _with(cast=["dad", "grandpa"]), "person", "grandpa"),
        (lambda: _with(cast=["zed"]), "person", "zed"),  # zed is in the other family only
        (lambda: _with(props=["hymn_books", "umbrella"]), "prop", "umbrella"),
        (lambda: _with(set="beach_1990"), "set", "beach_1990"),
    ],
)
def test_scene_for_names_the_first_missing_id(screenplay: Callable[[], Screenplay], kind: str, missing: str) -> None:
    lookup = wiring.scene_for(seeded_repo(), FAMILY)
    with pytest.raises(SceneLookupError) as info:
        lookup(screenplay())
    assert (info.value.kind, info.value.id) == (kind, missing)


def test_compile_reports_another_familys_person_as_unknown() -> None:
    tools = wiring.build_run_tools(run_info(), services())
    files = {"/screenplay.json": create_file_data(_with(cast=["zed"]).model_dump_json(by_alias=True))}
    reply = run_tool(tools["compile_screenplay"], files)["messages"][-1]
    assert not reply.content.startswith("ok:")
    assert "zed" in reply.content


# ------------------------------------------------------------------ isolation


def test_describe_photo_only_reads_this_runs_photos() -> None:
    repo = seeded_repo()
    mine = repo.put_media(FAMILY, png(), "image/png", kind="photo")
    other_photo = repo.put_media(FAMILY, png((0, 0, 200)), "image/png", kind="photo")
    theirs = repo.put_media(OTHER, png((0, 200, 0)), "image/png", kind="photo")
    # Even if another family's media id is listed on the run, it is never reachable.
    tools = wiring.build_run_tools(run_info(photo_ids=(mine, theirs)), services(repo))
    describe = tools["describe_photo"]

    assert describe.invoke({"photo_id": mine}) != UNKNOWN_PHOTO
    assert describe.invoke({"photo_id": other_photo}) == UNKNOWN_PHOTO  # same family, not this telling
    assert describe.invoke({"photo_id": theirs}) == UNKNOWN_PHOTO  # other family


def test_tools_never_reach_another_familys_people_memories_or_media() -> None:
    repo = seeded_repo()
    theirs = repo.put_media(OTHER, png(), "image/png", kind="photo")
    their_memory = SAMPLE.memory.model_copy(update={"id": "their_memory", "family_id": OTHER, "title": "Zed's boat"})
    repo.save_memory(OTHER, their_memory)
    tools = wiring.build_run_tools(run_info(), services(repo))

    assert tools["get_person"].invoke({"person_id": "zed"}).startswith("not found")
    assert json.loads(tools["get_person"].invoke({"person_id": "dad"}))
    assert json.loads(tools["find_memories"].invoke({"query": "boat"})) == []
    assert "not found" in tools["link_participants"].invoke(
        {"memory_id": "their_memory", "person_ids": ["mom"], "question": "What do you remember?"}
    )
    assert "error" in json.loads(tools["cut_out"].invoke({"media_id": theirs}))

    # Tools bound to OTHER see zed; the two bindings do not leak into each other.
    other_tools = wiring.build_run_tools(run_info(family_id=OTHER), services(repo))
    assert json.loads(other_tools["get_person"].invoke({"person_id": "zed"}))["name"] == "Zed"
    assert [m["id"] for m in json.loads(other_tools["find_memories"].invoke({"query": "boat"}))] == ["their_memory"]


# ------------------------------------------------------------------ manager


def test_make_manager_runs_a_studio_that_saves_a_scene() -> None:
    repo = seeded_repo()
    model = ScriptedChatModel(responder=studio_script(
        tool_call("write_todos", {"todos": [
            {"content": "Direct and compile", "status": "in_progress"},
            {"content": "Narrate and save", "status": "pending"},
        ]}),
        tool_call("write_file", {"file_path": "/screenplay.json", "content": SCREENPLAY_JSON}),
        tool_call("compile_screenplay", {}),
        tool_call("synthesize_narration", {}),
        tool_call("save_memory", {"title": "Hymn books down the pew", "participant_ids": ["dad", "mom"]}),
        AIMessage(content="Saved the scene."),
    ))
    svc = services(repo, model)
    manager = wiring.make_manager(svc, max_workers=1)
    assert manager._store is svc.store  # run records go where the studio keeps its notes
    try:
        fields = schemas.SubmitMemoryFields(text="Dad passed the hymn books down the pew.", year=1988)
        run_id = manager.start_run(FAMILY, USER, "dad", fields, [])

        async def follow() -> list[tuple[str, Any]]:
            return [event async for event in manager.stream_run(FAMILY, run_id)]

        events = asyncio.run(asyncio.wait_for(follow(), 30))
    finally:
        manager.shutdown()

    names = [name for name, _ in events]
    assert names == ["plan", "scene_ready", "done"], names
    done = events[-1][1]
    assert isinstance(done, schemas.DoneEvent)
    memory = repo.get_memory(FAMILY, done.memory_id)
    assert memory is not None and memory.timeline is not None and memory.screenplay is not None
    Timeline.model_validate(memory.timeline.model_dump(by_alias=True))
    assert memory.timeline.default_narrator == "dad"
    assert memory.timeline.narrations is not None and "dad" in memory.timeline.narrations
    assert repo.get_memory(OTHER, done.memory_id) is None

    # The run's record is in the services' store, under its family.
    record = svc.store.get(runs.run_namespace(FAMILY), run_id)
    assert record is not None and record.value["status"] == "done"
    assert record.value["memory_id"] == done.memory_id
    assert [name for name, _ in record.value["events"]] == names
    assert svc.store.get(runs.run_namespace(OTHER), run_id) is None


def test_make_manager_passes_the_store_and_its_runs_survive_a_restart() -> None:
    svc = services()
    first = wiring.make_manager(svc, max_workers=1)
    second = wiring.make_manager(svc, max_workers=1)
    try:
        assert first._store is svc.store and second._store is svc.store
        fields = schemas.SubmitMemoryFields(text="Dad passed the hymn books down the pew.", year=1988)
        run_id = first.start_run(FAMILY, USER, "dad", fields, [])
        assert first.wait(FAMILY, run_id, timeout=30) == "error"  # the idle studio never saves
        first.shutdown()
        # A manager made from the same services (a restart) replays it from the store.
        assert [name for name, _ in second.events(FAMILY, run_id)] == ["error"]
        with pytest.raises(runs.RunNotFound):
            second.events(OTHER, run_id)
    finally:
        first.shutdown()
        second.shutdown()


# --------------------------------------------------------------------------- run usage


def test_one_usage_registry_is_shared_by_art_studio_and_manager(monkeypatch: pytest.MonkeyPatch) -> None:
    """Both per-run caps count in one registry: the art service's images, the studio's model
    calls (its ModelCallBudget) and the run manager's records all use `Services.usage`."""
    from agent.guards import ModelCallBudget, RunUsage
    from agent.tools.art import ArtService

    monkeypatch.setenv("STITCH_MAX_MODEL_CALLS", "321")
    usage = RunUsage()
    art = ArtService(None, None, usage=usage)
    svc = wiring.fake_services(model=ScriptedChatModel(responder=lambda m, n: AIMessage(content="ok")), art=art)
    assert svc.usage is usage is svc.art.usage
    manager = wiring.make_manager(svc)
    try:
        assert manager._usage is usage
    finally:
        manager.shutdown()
    graph = wiring.build_run_graph(run_info(), svc)
    (budget,) = [cb for cb in graph.config["callbacks"] if isinstance(cb, ModelCallBudget)]
    assert budget.usage is usage and budget.run_id == run_info().run_id and budget.limit == 321
