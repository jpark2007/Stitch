"""Family tools: save_scene (new memory and second telling) and the tools bound to one family."""

from __future__ import annotations

import json
from typing import Any

import pytest
from deepagents.backends.utils import create_file_data, file_data_to_string
from deepagents.middleware.filesystem import FilesystemState
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import BaseTool
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode

import schemas
from agent.fake_compiler import FakeCompiler
from agent.repo import SAMPLE_MEMORY_PATH, InMemoryRepo, load_sample_family
from agent.run_info import RunInfo
from agent.tools.compile import ERRORS_PATH, Compiler, SceneInputs, load_compiler, make_compile_tool
from agent.tools.family import (
    ASKED_QUESTIONS_DIR,
    asked_question_path,
    era_for_year,
    make_family_tools,
    save_scene,
    save_scene_with_report,
)
from schemas import Memory, MemoryResponse, SubmitMemoryFields
from screenplay.schema import Caption, NarrationLine, Screenplay, Timeline

FAM = "fam_mao"
OTHER = "fam_other"
DAD_TEXT = "It was a Sunday in 1988. I saw her at the top of the church steps."
MOM_TEXT = "I was carrying far too many hymn books. I couldn't look up."


def sample() -> Memory:
    return MemoryResponse.model_validate_json(SAMPLE_MEMORY_PATH.read_text()).memory


def sample_scene() -> tuple[Screenplay, Timeline]:
    m = sample()
    assert m.screenplay is not None and m.timeline is not None
    return m.screenplay, m.timeline


def make_repo() -> InMemoryRepo:
    repo = InMemoryRepo()
    load_sample_family(repo, FAM)
    load_sample_family(repo, OTHER)
    repo.add_person(OTHER, schemas.Person(id="stranger", family_id=OTHER, name="Stranger",
                                          character=schemas.CharacterSheet(color="#000000")))
    return repo


def dad_run(**fields: Any) -> RunInfo:
    base = {"text": DAD_TEXT, "year": 1988, "question": "How did you meet your wife?"}
    return RunInfo(
        run_id="run_1",
        family_id=FAM,
        user_id="user_dad",
        author_id="dad",
        fields=SubmitMemoryFields(**{**base, **fields}),
        photo_ids=("media/p1",),
    )


def test_era_for_year() -> None:
    assert era_for_year(1988) == "1980s"
    assert era_for_year(1939) == "pre1940"
    assert era_for_year(2026) == "2020s"


# ------------------------------------------------------------------- save_scene


def test_save_scene_new_memory_from_sample() -> None:
    repo = make_repo()
    screenplay, timeline = sample_scene()
    run = dad_run()

    memory = save_scene(repo, run, title="How I met your mother", participant_ids=["mom", "stranger", "nobody"],
                        screenplay=screenplay, timeline=timeline)

    Memory.model_validate(memory.model_dump(mode="json", by_alias=True))
    assert memory.id == "memory_1"
    assert memory.family_id == FAM
    assert memory.title == "How I met your mother"
    assert memory.year == 1988 and memory.era == "1980s"
    assert memory.version == 1
    assert memory.prop_ids == ["hymn_books"]
    assert memory.set_ids == ["church_steps_1988"]
    # Author first; people outside this family (or unknown) are dropped.
    assert memory.participant_ids == ["dad", "mom"]
    assert len(memory.tellings) == 1
    telling = memory.tellings[0]
    assert (telling.author_id, telling.text, telling.question, telling.photo_ids) == (
        "dad", DAD_TEXT, "How did you meet your wife?", ["media/p1"],
    )
    assert memory.screenplay is not None and memory.screenplay.memory_id == memory.id
    assert memory.screenplay.shots == screenplay.shots
    assert memory.timeline == timeline
    assert repo.get_memory(FAM, memory.id) == memory
    assert repo.get_memory(OTHER, memory.id) is None


def test_save_scene_prefers_the_title_the_family_typed() -> None:
    repo = make_repo()
    screenplay, timeline = sample_scene()
    memory = save_scene(repo, dad_run(title="Church steps"), title="Agent title", participant_ids=[],
                        screenplay=screenplay, timeline=timeline)
    assert memory.title == "Church steps"


def _existing_memory_without_mom(repo: InMemoryRepo) -> Memory:
    """The sample saved as dad's memory only: mom's narration and telling removed."""
    m = sample().model_copy(deep=True)
    m.family_id = FAM
    m.id = "mem_hymns"
    assert m.screenplay is not None and m.timeline is not None
    m.screenplay.memory_id = m.id
    m.screenplay.narrations = None
    assert m.timeline.narrations is not None
    del m.timeline.narrations["mom"]
    m.tellings = [t for t in m.tellings if t.author_id == "dad"]
    return repo.save_memory(FAM, m)


def mom_run(memory_id: str) -> RunInfo:
    return RunInfo(
        run_id="run_2",
        family_id=FAM,
        user_id="user_mom",
        author_id="mom",
        fields=SubmitMemoryFields(text=MOM_TEXT, year=1988, memory_id=memory_id,
                                  question="Dad says you met on the church steps. What do you remember?"),
    )


def _moms_draft() -> tuple[Screenplay, Timeline]:
    """A draft scene carrying mom's narration, with action that must NOT replace the saved action."""
    screenplay, timeline = sample_scene()
    assert screenplay.narrations is not None and timeline.narrations is not None
    draft_sp = screenplay.model_copy(deep=True)
    draft_sp.shots[0].actions = draft_sp.shots[0].actions[:1]  # a different (wrong) action
    draft_tl = timeline.model_copy(deep=True)
    draft_tl.tracks = draft_tl.tracks[:1]  # different tracks
    return draft_sp, draft_tl


def test_second_telling_adds_narration_over_the_existing_action() -> None:
    repo = make_repo()
    existing = _existing_memory_without_mom(repo)
    assert existing.screenplay is not None and existing.timeline is not None
    draft_sp, draft_tl = _moms_draft()
    full_sp, full_tl = sample_scene()

    memory = save_scene(repo, mom_run(existing.id), title="ignored", participant_ids=[],
                        screenplay=draft_sp, timeline=draft_tl)

    Memory.model_validate(memory.model_dump(mode="json", by_alias=True))
    assert memory.id == existing.id
    assert memory.version == existing.version + 1 == 2
    assert [t.author_id for t in memory.tellings] == ["dad", "mom"]
    assert memory.tellings[-1].text == MOM_TEXT
    assert "mom" in memory.participant_ids
    # Mom's narration is added for both screenplay and timeline.
    assert memory.screenplay is not None and memory.timeline is not None
    assert memory.screenplay.narrations == {"mom": full_sp.narrations["mom"]}  # type: ignore[index]
    assert memory.timeline.narrations is not None
    assert memory.timeline.narrations["mom"] == full_tl.narrations["mom"]  # type: ignore[index]
    assert memory.timeline.narrations["dad"] == existing.timeline.narrations["dad"]  # type: ignore[index]
    # The shared action is untouched.
    assert memory.screenplay.shots == existing.screenplay.shots
    assert memory.timeline.tracks == existing.timeline.tracks
    assert memory.timeline.default_narrator == existing.timeline.default_narrator == "dad"
    assert memory.timeline.audio == existing.timeline.audio
    assert memory.title == existing.title
    # No new memory was created.
    assert [m.id for m in repo.find_memories(FAM)] == [existing.id]
    assert repo.get_memory(FAM, existing.id) == memory


def _moms_draft_in_shot_narration_only() -> tuple[Screenplay, Timeline]:
    """Mom's draft with no narrations["mom"]: the shots' `narration` holds dad's default telling."""
    draft_sp, draft_tl = _moms_draft()
    draft_sp.narrations = None
    return draft_sp, draft_tl


def test_second_telling_without_the_tellers_lines_is_refused() -> None:
    """Finding 1: shot.narration is the default telling (dad's words); it is never saved as mom's."""
    repo = make_repo()
    existing = _existing_memory_without_mom(repo)
    draft_sp, draft_tl = _moms_draft_in_shot_narration_only()
    with pytest.raises(ValueError, match=r"no narrations\['mom'\]"):
        save_scene_with_report(repo, mom_run(existing.id), title="x", participant_ids=[], screenplay=draft_sp,
                               timeline=draft_tl)
    assert repo.get_memory(FAM, existing.id) == existing

    content, events = _run_save_memory(repo, mom_run(existing.id), _files(draft_sp, draft_tl))
    assert content.startswith("error: could not save:") and "narrations['mom']" in content
    assert events == []
    assert repo.get_memory(FAM, existing.id) == existing


def test_second_telling_requires_the_tellers_timeline_narration() -> None:
    repo = make_repo()
    existing = _existing_memory_without_mom(repo)
    draft_sp, draft_tl = _moms_draft()
    draft_tl.narrations = {"dad": draft_tl.narrations["dad"]}  # type: ignore[index]
    with pytest.raises(ValueError, match="mom"):
        save_scene(repo, mom_run(existing.id), title="x", participant_ids=[], screenplay=draft_sp, timeline=draft_tl)
    assert repo.get_memory(FAM, existing.id) == existing


def _existing_ten_second_memory(repo: InMemoryRepo) -> Memory:
    """Dad's memory with a 10 s action whose last shot ends at 10, and dad's audio as the default."""
    m = _existing_memory_without_mom(repo).model_copy(deep=True)
    assert m.timeline is not None and m.timeline.narrations is not None
    m.timeline.duration = 10.0
    m.timeline.shots[-1].end = 10.0
    m.timeline.audio = "media/dad_narration"
    m.timeline.narrations["dad"].audio = "media/dad_narration"
    return repo.save_memory(FAM, m)


def _moms_draft_lasting(duration: float) -> tuple[Screenplay, Timeline]:
    draft_sp, draft_tl = _moms_draft()
    assert draft_tl.narrations is not None
    draft_tl.duration = duration
    draft_tl.narrations["mom"].audio = "media/mom_narration"
    return draft_sp, draft_tl


def test_a_longer_second_telling_grows_the_duration_and_holds_the_last_shot() -> None:
    repo = make_repo()
    existing = _existing_ten_second_memory(repo)
    assert existing.timeline is not None
    draft_sp, draft_tl = _moms_draft_lasting(14.5)  # mom's audio runs past the 10 s action

    memory = save_scene(repo, mom_run(existing.id), title="x", participant_ids=[], screenplay=draft_sp,
                        timeline=draft_tl)

    tl = memory.timeline
    assert tl is not None and tl.narrations is not None
    assert tl.duration == 14.5
    assert tl.shots[-1].end == 14.5
    assert tl.shots[:-1] == existing.timeline.shots[:-1]
    assert tl.shots[-1].model_copy(update={"end": 10.0}) == existing.timeline.shots[-1]
    assert tl.tracks == existing.timeline.tracks
    assert tl.effects == existing.timeline.effects
    # The default narrator (dad) keeps his audio and captions.
    assert tl.default_narrator == "dad"
    assert tl.audio == existing.timeline.audio == "media/dad_narration"
    assert tl.captions == existing.timeline.captions
    assert tl.narrations["dad"] == existing.timeline.narrations["dad"]  # type: ignore[index]
    assert tl.narrations["mom"].audio == "media/mom_narration"
    assert repo.get_memory(FAM, existing.id) == memory


def test_a_last_shot_that_ended_early_is_not_stretched() -> None:
    repo = make_repo()
    m = _existing_ten_second_memory(repo).model_copy(deep=True)
    assert m.timeline is not None
    m.timeline.shots[-1].end = 9.0  # a closing beat after the last shot
    existing = repo.save_memory(FAM, m)
    draft_sp, draft_tl = _moms_draft_lasting(14.5)
    memory = save_scene(repo, mom_run(existing.id), title="x", participant_ids=[], screenplay=draft_sp,
                        timeline=draft_tl)
    assert memory.timeline is not None
    assert memory.timeline.duration == 14.5
    assert memory.timeline.shots[-1].end == 9.0


def test_a_shorter_second_telling_keeps_the_existing_duration() -> None:
    repo = make_repo()
    existing = _existing_ten_second_memory(repo)
    assert existing.timeline is not None
    draft_sp, draft_tl = _moms_draft_lasting(6.25)

    memory = save_scene(repo, mom_run(existing.id), title="x", participant_ids=[], screenplay=draft_sp,
                        timeline=draft_tl)

    assert memory.timeline is not None
    assert memory.timeline.duration == existing.timeline.duration == 10.0
    assert memory.timeline.shots == existing.timeline.shots
    assert memory.timeline.tracks == existing.timeline.tracks


def test_the_same_author_telling_twice_replaces_their_telling_in_place() -> None:
    repo = make_repo()
    existing = _existing_memory_without_mom(repo)
    assert existing.timeline is not None

    first_sp, first_tl = _moms_draft()
    first = save_scene(repo, mom_run(existing.id), title="x", participant_ids=[], screenplay=first_sp,
                       timeline=first_tl)
    assert [t.author_id for t in first.tellings] == ["dad", "mom"]
    assert first.version == existing.version + 1

    # A third teller, so mom's telling is no longer the last one.
    repo.add_person(FAM, schemas.Person(id="aunt", family_id=FAM, name="Aunt May",
                                        character=schemas.CharacterSheet(color="#00aa00")))
    aunt = RunInfo(run_id="run_3", family_id=FAM, user_id="user_aunt", author_id="aunt",
                   fields=SubmitMemoryFields(text="I was across the street.", year=1988, memory_id=existing.id))
    aunt_sp, aunt_tl = _moms_draft()
    assert aunt_sp.narrations is not None and aunt_tl.narrations is not None
    aunt_sp.narrations = {"aunt": aunt_sp.narrations["mom"]}
    aunt_tl.narrations = {"aunt": aunt_tl.narrations["mom"].model_copy(update={"author_id": "aunt"})}
    second = save_scene(repo, aunt, title="x", participant_ids=[], screenplay=aunt_sp, timeline=aunt_tl)
    assert [t.author_id for t in second.tellings] == ["dad", "mom", "aunt"]
    assert second.version == first.version + 1

    # Mom tells it again, with new words and new narration.
    retold_text = "Actually, it was raining, and the hymn books got wet."
    new_line = NarrationLine(t=0.5, text="It was raining.")
    retold_sp, retold_tl = _moms_draft()
    assert retold_sp.narrations is not None and retold_tl.narrations is not None
    retold_sp.narrations["mom"] = {"s1": [new_line]}
    retold_tl.narrations["mom"].audio = "media/mom_take_two"
    retold_tl.narrations["mom"].captions = [Caption(t=0.5, text="It was raining.")]
    run = mom_run(existing.id)
    run = RunInfo(run_id="run_4", family_id=run.family_id, user_id=run.user_id, author_id=run.author_id,
                  fields=run.fields.model_copy(update={"text": retold_text}))
    third = save_scene(repo, run, title="x", participant_ids=[], screenplay=retold_sp, timeline=retold_tl)

    assert [t.author_id for t in third.tellings] == ["dad", "mom", "aunt"]
    assert third.tellings[1].text == retold_text
    assert third.tellings[0] == second.tellings[0] and third.tellings[2] == second.tellings[2]
    assert third.version == second.version + 1 == existing.version + 3
    assert third.screenplay is not None and third.screenplay.narrations is not None
    assert third.screenplay.narrations["mom"] == {"s1": [new_line]}
    assert third.screenplay.narrations["aunt"] == second.screenplay.narrations["aunt"]  # type: ignore[union-attr,index]
    assert third.timeline is not None and third.timeline.narrations is not None
    assert third.timeline.narrations["mom"].audio == "media/mom_take_two"
    assert third.timeline.narrations["mom"].captions == [Caption(t=0.5, text="It was raining.")]
    assert set(third.timeline.narrations) == {"dad", "mom", "aunt"}
    assert third.timeline.tracks == existing.timeline.tracks
    assert repo.get_memory(FAM, existing.id) == third


def test_draft_narration_for_an_unknown_shot_is_dropped_and_reported() -> None:
    repo = make_repo()
    existing = _existing_memory_without_mom(repo)
    draft_sp, draft_tl = _moms_draft()
    assert draft_sp.narrations is not None
    moms_lines = draft_sp.narrations["mom"]["s1"]
    draft_sp.narrations["mom"] = {"s1": moms_lines, "s9": [NarrationLine(t=0, text="A shot that never was.")]}
    files = {
        "/screenplay.json": create_file_data(draft_sp.model_dump_json(by_alias=True)),
        "/timeline.json": create_file_data(draft_tl.model_dump_json(by_alias=True)),
    }

    content, events = _run_save_memory(repo, mom_run(existing.id), files)

    assert content == f"saved {existing.id} v2; ignored narration for unknown shot(s): s9"
    assert events == [{"event": "scene_ready", "data": {"memoryId": existing.id, "version": 2}}]
    saved = repo.get_memory(FAM, existing.id)
    assert saved is not None and saved.screenplay is not None and saved.screenplay.narrations is not None
    assert saved.screenplay.narrations["mom"] == {"s1": moms_lines}


def test_second_telling_with_only_known_shots_reports_nothing_ignored() -> None:
    repo = make_repo()
    existing = _existing_memory_without_mom(repo)
    draft_sp, draft_tl = _moms_draft()
    files = {
        "/screenplay.json": create_file_data(draft_sp.model_dump_json(by_alias=True)),
        "/timeline.json": create_file_data(draft_tl.model_dump_json(by_alias=True)),
    }
    content, _ = _run_save_memory(repo, mom_run(existing.id), files)
    assert content == f"saved {existing.id} v2"


def test_second_telling_of_another_familys_memory_is_not_found() -> None:
    repo = make_repo()
    theirs = sample().model_copy(update={"family_id": OTHER, "id": "their_memory"}, deep=True)
    repo.save_memory(OTHER, theirs)
    draft_sp, draft_tl = _moms_draft()
    with pytest.raises(ValueError, match="not found"):
        save_scene(repo, mom_run("their_memory"), title="x", participant_ids=[], screenplay=draft_sp,
                   timeline=draft_tl)
    assert repo.get_memory(OTHER, "their_memory") == theirs


# ------------------------------------------------------------------------ tools


def tools_by_name(repo: InMemoryRepo, run: RunInfo) -> dict[str, Any]:
    return {t.name: t for t in make_family_tools(repo, run)}


def test_tool_names_and_no_family_id_in_any_schema() -> None:
    repo = make_repo()
    run = dad_run()
    tools = make_family_tools(repo, run)
    assert [t.name for t in tools] == ["get_person", "find_memories", "link_participants", "save_memory"]
    for t in tools:
        schema = t.tool_call_schema.model_json_schema()
        dumped = json.dumps(schema).lower()
        props = {p.lower() for p in schema.get("properties", {})}
        assert not props & {"family_id", "familyid", "family", "state", "user_id", "run_id"}, (t.name, props)
        assert "family_id" not in dumped and "familyid" not in dumped, t.name
        assert FAM not in dumped and run.user_id not in dumped and run.run_id not in dumped, t.name


def test_get_person_is_bound_to_the_family() -> None:
    repo = make_repo()
    get_person = tools_by_name(repo, dad_run())["get_person"]
    dad = json.loads(get_person.invoke({"person_id": "dad"}))
    assert dad["id"] == "dad" and dad["familyId"] == FAM
    assert "character" in dad and dad["character"]["color"]
    assert get_person.invoke({"person_id": "stranger"}).startswith("not found")
    assert get_person.invoke({"person_id": "nobody"}).startswith("not found")


def test_find_memories_is_compact_and_bound_to_the_family() -> None:
    repo = make_repo()
    long_text = "word " * 100
    for fam, mid in ((FAM, "mine"), (OTHER, "theirs")):
        repo.save_memory(fam, Memory(id=mid, family_id=fam, title="Church day", year=1988, participant_ids=["dad"],
                                     tellings=[schemas.Telling(author_id="dad", text=long_text)]))
    find = tools_by_name(repo, dad_run())["find_memories"]
    found = json.loads(find.invoke({"query": "church"}))
    assert [m["id"] for m in found] == ["mine"]
    assert set(found[0]) == {"id", "title", "year", "participants", "tellings"}
    assert len(found[0]["tellings"][0]["text"]) == 200
    assert json.loads(find.invoke({"person_id": "mom"})) == []
    assert [m["id"] for m in json.loads(find.invoke({}))] == ["mine"]


def test_link_participants_adds_people_and_asks_each_for_their_version() -> None:
    repo = make_repo()
    repo.add_person(FAM, schemas.Person(id="aunt", family_id=FAM, name="Aunt May",
                                        character=schemas.CharacterSheet(color="#00aa00")))
    repo.save_memory(FAM, Memory(id="m1", family_id=FAM, title="Church", year=1988, participant_ids=["dad"],
                                 tellings=[schemas.Telling(author_id="dad", text=DAD_TEXT)]))
    link = tools_by_name(repo, dad_run())["link_participants"]
    out = link.invoke({"memory_id": "m1", "person_ids": ["dad", "mom", "aunt", "stranger"],
                       "question": "Dad says you met on the church steps. What do you remember?"})

    assert "stranger" in out  # reported as not found
    assert repo.get_memory(FAM, "m1").participant_ids == ["dad", "mom", "aunt"]  # type: ignore[union-attr]
    questions = repo.list_questions(FAM)
    assert sorted(q.person_id for q in questions) == ["aunt", "mom"]  # never the teller
    assert all(q.memory_id == "m1" and q.status == "open" and q.text.startswith("Dad says") for q in questions)
    assert repo.list_questions(OTHER) == []
    assert repo.get_person(OTHER, "stranger") is not None

    assert link.invoke({"memory_id": "nope", "person_ids": ["mom"], "question": "?"}).startswith("not found")


# ---------------------------------------------------------- save_memory via ToolNode


SAVE_ARGS = {"title": "How I met your mother", "participant_ids": ["mom"]}


def _call_tool(tools: list[BaseTool], files: dict[str, Any], name: str, args: dict[str, Any],
               call_id: str = "call_1") -> tuple[str, list[dict], dict[str, Any]]:
    """Run one tool call through a ToolNode over deepagents' FilesystemState.

    Returns (tool message text, custom events, the state's files afterwards), so a test can
    carry the files into the next call the way a run's state does.
    """
    graph = StateGraph(FilesystemState)
    graph.add_node("tools", ToolNode(tools))
    graph.add_edge(START, "tools")
    graph.add_edge("tools", END)
    app = graph.compile()
    call = {"name": name, "args": args, "id": call_id, "type": "tool_call"}
    events: list[dict] = []
    final: dict[str, Any] = {}
    for mode, chunk in app.stream({"messages": [AIMessage(content="", tool_calls=[call])], "files": files},
                                  stream_mode=["custom", "values"]):
        if mode == "custom":
            events.append(chunk)
        else:
            final = chunk
    message = final["messages"][-1]
    assert isinstance(message, ToolMessage) and message.tool_call_id == call_id and message.name == name
    return str(message.content), events, dict(final.get("files") or {})


def _call_tools_in_parallel(tools: list[BaseTool], files: dict[str, Any], name: str,
                            many_args: list[dict[str, Any]]) -> tuple[list[str], dict[str, Any]]:
    """Run several calls of one tool from ONE AI message (the tool node runs them in parallel).

    Returns (each tool message text in call order, the state's files afterwards).
    """
    graph = StateGraph(FilesystemState)
    graph.add_node("tools", ToolNode(tools))
    graph.add_edge(START, "tools")
    graph.add_edge("tools", END)
    calls = [{"name": name, "args": args, "id": f"call_{i}", "type": "tool_call"} for i, args in enumerate(many_args)]
    final = graph.compile().invoke({"messages": [AIMessage(content="", tool_calls=calls)], "files": files})
    by_id = {m.tool_call_id: str(m.content) for m in final["messages"] if isinstance(m, ToolMessage)}
    return [by_id[c["id"]] for c in calls], dict(final.get("files") or {})


def _asked_pairs(files: dict[str, Any]) -> list[tuple[str, str]]:
    """The (memoryId, personId) pairs link_participants recorded in the state files."""
    pairs = []
    for path, data in files.items():
        if path.startswith(ASKED_QUESTIONS_DIR):
            record = json.loads(file_data_to_string(data))
            assert path == asked_question_path(record["memoryId"], record["personId"])
            pairs.append((record["memoryId"], record["personId"]))
    return sorted(pairs)


def _run_save_memory(repo: InMemoryRepo, run: RunInfo, files: dict[str, Any]) -> tuple[str, list[dict]]:
    content, events, _ = _call_tool(make_family_tools(repo, run), files, "save_memory", SAVE_ARGS)
    return content, events


def _files(screenplay: Screenplay, timeline: Timeline) -> dict[str, Any]:
    return {
        "/screenplay.json": create_file_data(screenplay.model_dump_json(by_alias=True)),
        "/timeline.json": create_file_data(timeline.model_dump_json(by_alias=True)),
    }


def _scene_files() -> dict[str, Any]:
    return _files(*sample_scene())


def test_save_memory_tool_saves_from_state_files_and_emits_scene_ready() -> None:
    repo = make_repo()
    content, events = _run_save_memory(repo, dad_run(), _scene_files())
    assert content == "saved memory_1 v1"
    assert events == [{"event": "scene_ready", "data": {"memoryId": "memory_1", "version": 1}}]
    schemas.SceneReadyEvent.model_validate(events[0]["data"])
    saved = repo.get_memory(FAM, "memory_1")
    assert saved is not None and saved.participant_ids == ["dad", "mom"]
    assert repo.get_memory(OTHER, "memory_1") is None


def test_save_memory_tool_names_the_missing_file() -> None:
    repo = make_repo()
    files = _scene_files()
    del files["/timeline.json"]
    content, events = _run_save_memory(repo, dad_run(), files)
    assert content.startswith("error") and "/timeline.json" in content
    assert events == []
    assert repo.find_memories(FAM) == []


def test_save_memory_tool_reports_an_invalid_file() -> None:
    repo = make_repo()
    files = _scene_files()
    files["/screenplay.json"] = create_file_data('{"memoryId": "x", "shots": "not a list"}')
    content, _ = _run_save_memory(repo, dad_run(), files)
    assert content.startswith("error") and "/screenplay.json" in content
    assert repo.find_memories(FAM) == []


# ------------------------------------------------- save_memory guards (findings 4, 5, 6)


def _scene_inputs() -> SceneInputs:
    response = MemoryResponse.model_validate_json(SAMPLE_MEMORY_PATH.read_text())
    return SceneInputs(
        sets={s.id: s for s in response.sets},
        props={p.id: p for p in response.props},
        people={p.id: p for p in response.people},
    )


def _studio_tools(repo: InMemoryRepo, run: RunInfo) -> list[BaseTool]:
    """What the studio holds: compile_screenplay beside the family tools."""
    scene = _scene_inputs()
    return [make_compile_tool(FakeCompiler(), lambda _: scene), *make_family_tools(repo, run)]


def _sample_screenplay_text() -> str:
    screenplay, _ = sample_scene()
    return screenplay.model_dump_json(by_alias=True)


def _bad_screenplay_text() -> str:
    """The sample with dad handing back books nobody picked up: the compiler refuses it."""
    bad = json.loads(_sample_screenplay_text())
    bad["shots"][0]["actions"][4]["t"] = 5
    bad["shots"][0]["actions"].pop(3)
    return json.dumps(bad)


def _text(files: dict[str, Any], path: str) -> str:
    return file_data_to_string(files[path])


def _compiled_files(repo: InMemoryRepo, run: RunInfo) -> dict[str, Any]:
    files = {"/screenplay.json": create_file_data(_sample_screenplay_text())}
    content, _, files = _call_tool(_studio_tools(repo, run), files, "compile_screenplay", {})
    assert content.startswith("ok:"), content
    assert "/timeline.json" in files and _text(files, ERRORS_PATH).strip() == ""
    return files


def test_save_memory_saves_a_fresh_compile() -> None:
    repo = make_repo()
    files = _compiled_files(repo, dad_run())
    content, events, _ = _call_tool(_studio_tools(repo, dad_run()), files, "save_memory", SAVE_ARGS)
    assert content == "saved memory_1 v1"
    assert [e["event"] for e in events] == ["scene_ready"]


def test_save_memory_refuses_after_a_failed_compile() -> None:
    """Finding 4: a stale /timeline.json from an earlier good compile is never saved."""
    repo = make_repo()
    run = dad_run()
    files = _compiled_files(repo, run)
    files["/screenplay.json"] = create_file_data(_bad_screenplay_text())
    content, _, files = _call_tool(_studio_tools(repo, run), files, "compile_screenplay", {})
    assert not content.startswith("ok:")
    assert _text(files, ERRORS_PATH).strip()
    assert "/timeline.json" in files  # the earlier compile's timeline is still there

    content, events, _ = _call_tool(_studio_tools(repo, run), files, "save_memory", SAVE_ARGS)

    assert content == ("error: could not save: the last compile failed; fix /screenplay.json "
                       "and compile again before saving")
    assert events == []
    assert repo.find_memories(FAM) == []


def test_save_memory_refuses_an_unvoiced_timeline() -> None:
    repo = make_repo()
    run = dad_run()
    files = _compiled_files(repo, run)
    timeline = Timeline.model_validate_json(_text(files, "/timeline.json"))
    for narrations in (None, {"mom": timeline.narrations["mom"]},  # type: ignore[index]
                       {"dad": timeline.narrations["dad"].model_copy(update={"lines": []})}):  # type: ignore[index]
        unvoiced = timeline.model_copy(update={"narrations": narrations})
        files["/timeline.json"] = create_file_data(unvoiced.model_dump_json(by_alias=True))
        content, events, _ = _call_tool(_studio_tools(repo, run), files, "save_memory", SAVE_ARGS)
        assert content == ("error: could not save: the scene has no narration for dad; "
                           "run the narrator's voice pass first")
        assert events == []
    assert repo.find_memories(FAM) == []


def test_save_scene_refuses_an_unvoiced_new_memory() -> None:
    repo = make_repo()
    screenplay, timeline = sample_scene()
    with pytest.raises(ValueError, match="no narration for dad"):
        save_scene(repo, dad_run(), title="x", participant_ids=[], screenplay=screenplay,
                   timeline=timeline.model_copy(update={"narrations": None}))
    assert repo.find_memories(FAM) == []


def test_save_memory_refuses_a_timeline_not_compiled_from_the_screenplay() -> None:
    repo = make_repo()
    screenplay, timeline = sample_scene()
    stale = timeline.model_copy(deep=True)
    stale.shots[0].id = "s0"
    with pytest.raises(ValueError, match="compile /screenplay.json again"):
        save_scene_with_report(repo, dad_run(), title="x", participant_ids=[], screenplay=screenplay,
                               timeline=stale)
    content, events = _run_save_memory(repo, dad_run(), _files(screenplay, stale))
    assert content.startswith("error: could not save:") and "s0" in content
    assert events == []

    # A second shot in the screenplay that the timeline never compiled.
    longer = screenplay.model_copy(deep=True)
    longer.shots.append(longer.shots[0].model_copy(update={"id": "s2"}, deep=True))
    content, _ = _run_save_memory(repo, dad_run(), _files(longer, timeline))
    assert content.startswith("error: could not save:")
    assert repo.find_memories(FAM) == []


@pytest.mark.parametrize("rebuild_tools", [False, True])
def test_saving_twice_in_one_run_updates_the_same_memory(rebuild_tools: bool) -> None:
    """Finding 6: one memory per run, even when the tools are rebuilt (a resumed run)."""
    repo = make_repo()
    run = dad_run()
    tools = make_family_tools(repo, run)
    content, events, files = _call_tool(tools, _scene_files(), "save_memory", SAVE_ARGS)
    assert content == "saved memory_1 v1"
    first = repo.get_memory(FAM, "memory_1")
    assert first is not None

    if rebuild_tools:
        tools = make_family_tools(repo, run)
    content, events, files = _call_tool(tools, files, "save_memory",
                                        {"title": "The hymn books", "participant_ids": ["mom"]}, call_id="call_2")

    assert content == "saved memory_1 v2"
    assert events == [{"event": "scene_ready", "data": {"memoryId": "memory_1", "version": 2}}]
    memories = repo.find_memories(FAM)
    assert [(m.id, m.version) for m in memories] == [("memory_1", 2)]
    second = memories[0]
    assert second.title == "The hymn books"
    assert [t.author_id for t in second.tellings] == ["dad"]
    assert second.participant_ids == ["dad", "mom"]
    assert second.screenplay is not None and second.screenplay.memory_id == "memory_1"
    assert json.loads(_text(files, "/saved_memory.json"))["memoryId"] == "memory_1"


def test_a_second_save_keeps_people_the_linker_added() -> None:
    repo = make_repo()
    repo.add_person(FAM, schemas.Person(id="aunt", family_id=FAM, name="Aunt May",
                                        character=schemas.CharacterSheet(color="#00aa00")))
    run = dad_run()
    tools = make_family_tools(repo, run)
    _, _, files = _call_tool(tools, _scene_files(), "save_memory", SAVE_ARGS)
    _, _, files = _call_tool(tools, files, "link_participants",
                             {"memory_id": "memory_1", "person_ids": ["aunt"], "question": "What do you remember?"})
    content, _, _ = _call_tool(tools, files, "save_memory", SAVE_ARGS, call_id="call_3")
    assert content == "saved memory_1 v2"
    saved = repo.get_memory(FAM, "memory_1")
    assert saved is not None and saved.participant_ids == ["dad", "mom", "aunt"]


def test_a_saved_memory_record_from_another_run_is_refused() -> None:
    repo = make_repo()
    files = _scene_files()
    files["/saved_memory.json"] = create_file_data(json.dumps({"memoryId": "memory_9", "runId": "someone_else"}))
    content, events = _run_save_memory(repo, dad_run(), files)
    assert content.startswith("error: could not save:")
    assert events == []
    assert repo.find_memories(FAM) == []


def test_a_second_telling_run_never_records_a_saved_memory() -> None:
    repo = make_repo()
    existing = _existing_memory_without_mom(repo)
    content, _, files = _call_tool(make_family_tools(repo, mom_run(existing.id)), _files(*_moms_draft()),
                                   "save_memory", SAVE_ARGS)
    assert content == f"saved {existing.id} v2"
    assert "/saved_memory.json" not in files


# ------------------------------------------------- default-narrator retelling (finding 5)


def _sample_memory(repo: InMemoryRepo, *, default_narrator: str | None = "dad") -> Memory:
    m = sample().model_copy(deep=True)
    m.family_id = FAM
    m.id = "mem_hymns"
    assert m.screenplay is not None and m.timeline is not None
    m.screenplay.memory_id = m.id
    m.timeline.default_narrator = default_narrator
    return repo.save_memory(FAM, m)


def dad_retell_run(memory_id: str) -> RunInfo:
    return RunInfo(run_id="run_5", family_id=FAM, user_id="user_dad", author_id="dad",
                   fields=SubmitMemoryFields(text="It was raining that Sunday.", year=1988, memory_id=memory_id))


RETOLD = NarrationLine(t=0.5, text="It was raining that Sunday.")


def _dads_retelling() -> tuple[Screenplay, Timeline]:
    draft_sp, draft_tl = sample_scene()
    draft_sp = draft_sp.model_copy(deep=True)
    draft_tl = draft_tl.model_copy(deep=True)
    assert draft_sp.narrations is not None and draft_tl.narrations is not None
    draft_sp.narrations["dad"] = {"s1": [RETOLD]}
    draft_tl.narrations["dad"] = draft_tl.narrations["dad"].model_copy(update={
        "audio": "media/dad_take_two",
        "captions": [Caption(t=1.7, text=RETOLD.text)],
        "lines": [RETOLD.model_copy(update={"t": 1.7})],
    })
    return draft_sp, draft_tl


@pytest.mark.parametrize("default_narrator", ["dad", None])
def test_the_default_narrator_retelling_rewrites_the_default_telling(default_narrator: str | None) -> None:
    repo = make_repo()
    existing = _sample_memory(repo, default_narrator=default_narrator)
    assert existing.screenplay is not None and existing.timeline is not None
    assert existing.tellings[0].author_id == "dad"  # the fallback when defaultNarrator is unset
    draft_sp, draft_tl = _dads_retelling()

    memory = save_scene(repo, dad_retell_run(existing.id), title="x", participant_ids=[],
                        screenplay=draft_sp, timeline=draft_tl)

    sp, tl = memory.screenplay, memory.timeline
    assert sp is not None and tl is not None
    assert sp.shots[0].narration == [RETOLD]
    assert sp.narrations is not None and "dad" not in sp.narrations
    assert sp.narrations["mom"] == existing.screenplay.narrations["mom"]  # type: ignore[index]
    assert [a for s in sp.shots for a in s.actions] == [a for s in existing.screenplay.shots for a in s.actions]
    assert tl.captions == [Caption(t=1.7, text=RETOLD.text)]
    assert tl.audio == "media/dad_take_two"
    assert tl.narrations is not None and tl.narrations["dad"].audio == "media/dad_take_two"
    assert tl.narrations["mom"] == existing.timeline.narrations["mom"]  # type: ignore[index]
    assert [t.author_id for t in memory.tellings] == ["dad", "mom"]
    assert memory.tellings[0].text == "It was raining that Sunday."
    assert memory.version == existing.version + 1
    Memory.model_validate(memory.model_dump(mode="json", by_alias=True))


def test_a_default_narrator_retelling_clears_shots_it_has_no_lines_for() -> None:
    repo = make_repo()
    m = _sample_memory(repo).model_copy(deep=True)
    assert m.screenplay is not None and m.timeline is not None
    m.screenplay.shots.append(m.screenplay.shots[0].model_copy(update={"id": "s2"}, deep=True))
    m.screenplay.narrations = {"dad": {"s1": [RETOLD]}}  # a stale entry for the default teller
    existing = repo.save_memory(FAM, m)
    draft_sp, draft_tl = _dads_retelling()

    memory = save_scene(repo, dad_retell_run(existing.id), title="x", participant_ids=[],
                        screenplay=draft_sp, timeline=draft_tl)

    assert memory.screenplay is not None
    assert [s.narration for s in memory.screenplay.shots] == [[RETOLD], []]
    assert memory.screenplay.narrations is None


def test_another_teller_still_goes_under_narrations() -> None:
    repo = make_repo()
    existing = _existing_memory_without_mom(repo)
    assert existing.screenplay is not None
    memory = save_scene(repo, mom_run(existing.id), title="x", participant_ids=[], screenplay=_moms_draft()[0],
                        timeline=_moms_draft()[1])
    assert memory.screenplay is not None and memory.screenplay.narrations is not None
    assert "mom" in memory.screenplay.narrations
    assert [s.narration for s in memory.screenplay.shots] == [s.narration for s in existing.screenplay.shots]


RETOLD_TEXT = "It rained. I saw her."


def _dads_compilable_retelling(existing: Memory) -> Screenplay:
    """Dad's retelling as the compiler accepts it: his new words in each shot's `narration`.

    narrations['dad'] beside a shot narration sourced to dad is refused (duplicate_telling),
    so this is the only form a compiled default-narrator retelling can take.
    """
    assert existing.screenplay is not None
    draft = existing.screenplay.model_copy(deep=True)
    draft.shots[0].narration[0] = draft.shots[0].narration[0].model_copy(update={"text": RETOLD_TEXT})
    return draft


@pytest.mark.parametrize("compiler_name", ["fake", "real"])
def test_a_compiled_default_narrator_retelling_saves(compiler_name: str) -> None:
    """Reviewer finding: the default-narrator retelling must be reachable through compile + save_memory."""
    compiler: Compiler = FakeCompiler() if compiler_name == "fake" else load_compiler()
    repo = make_repo()
    existing = _sample_memory(repo)
    assert existing.screenplay is not None and existing.timeline is not None
    run = dad_retell_run(existing.id)
    scene = _scene_inputs()
    tools = [make_compile_tool(compiler, lambda _: scene), *make_family_tools(repo, run)]

    files = {"/screenplay.json": create_file_data(_dads_compilable_retelling(existing).model_dump_json(by_alias=True))}
    content, _, files = _call_tool(tools, files, "compile_screenplay", {})
    assert content.startswith("ok:"), content
    content, events, _ = _call_tool(tools, files, "save_memory", SAVE_ARGS, call_id="call_2")

    assert content == f"saved {existing.id} v{existing.version + 1}"
    assert [e["event"] for e in events] == ["scene_ready"]
    saved = repo.get_memory(FAM, existing.id)
    assert saved is not None and saved.screenplay is not None and saved.timeline is not None
    assert [line.text for line in saved.screenplay.shots[0].narration] == [
        RETOLD_TEXT, *[line.text for line in existing.screenplay.shots[0].narration[1:]]
    ]
    assert saved.screenplay.narrations is not None and "dad" not in saved.screenplay.narrations
    assert saved.screenplay.narrations["mom"] == existing.screenplay.narrations["mom"]  # type: ignore[index]
    assert saved.timeline.captions[0].text == RETOLD_TEXT
    assert saved.timeline.narrations is not None
    assert saved.timeline.narrations["mom"] == existing.timeline.narrations["mom"]  # type: ignore[index]
    assert [t.author_id for t in saved.tellings] == ["dad", "mom"]
    assert [m.id for m in repo.find_memories(FAM)] == [existing.id]


def test_shot_narration_is_never_read_as_the_default_narrators_when_another_teller_owns_it() -> None:
    """The shot-narration form counts as dad's retelling only when the draft's default teller is dad."""
    repo = make_repo()
    existing = _sample_memory(repo)
    draft_sp, draft_tl = _dads_retelling()
    draft_sp.narrations = None
    for shot in draft_sp.shots:
        shot.narration = [line.model_copy(update={"source": "telling:mom:x"}) for line in shot.narration]
    with pytest.raises(ValueError, match=r"no narrations\['dad'\]"):
        save_scene(repo, dad_retell_run(existing.id), title="x", participant_ids=[], screenplay=draft_sp,
                   timeline=draft_tl)
    assert repo.get_memory(FAM, existing.id) == existing


# ------------------------------------------------- link_participants asks once (finding 14)


@pytest.mark.parametrize("rebuild_tools", [False, True])
def test_linking_the_same_person_twice_asks_once(rebuild_tools: bool) -> None:
    repo = make_repo()
    repo.save_memory(FAM, Memory(id="m1", family_id=FAM, title="Church", year=1988, participant_ids=["dad"],
                                 tellings=[schemas.Telling(author_id="dad", text=DAD_TEXT)]))
    args = {"memory_id": "m1", "person_ids": ["mom"], "question": "Dad says you met on the church steps."}
    tools = make_family_tools(repo, dad_run())

    first, _, files = _call_tool(tools, {}, "link_participants", args)
    assert first == "linked mom to m1; asked mom"
    if rebuild_tools:
        tools = make_family_tools(repo, dad_run())
    second, _, files = _call_tool(tools, files, "link_participants", args, call_id="call_2")

    assert second == "linked mom to m1; no one new to ask; already asked: mom"
    questions = repo.list_questions(FAM)
    assert [(q.person_id, q.memory_id, q.status) for q in questions] == [("mom", "m1", "open")]
    assert _asked_pairs(files) == [("m1", "mom")]


def test_the_asked_record_is_per_memory() -> None:
    repo = make_repo()
    for mid in ("m1", "m2"):
        repo.save_memory(FAM, Memory(id=mid, family_id=FAM, title="Church", year=1988, participant_ids=["dad"],
                                     tellings=[schemas.Telling(author_id="dad", text=DAD_TEXT)]))
    tools = make_family_tools(repo, dad_run())
    _, _, files = _call_tool(tools, {}, "link_participants",
                             {"memory_id": "m1", "person_ids": ["mom"], "question": "?"})
    content, _, files = _call_tool(tools, files, "link_participants",
                                   {"memory_id": "m2", "person_ids": ["mom", "dad"], "question": "?"})
    assert content == "linked mom, dad to m2; asked mom"
    assert sorted((q.person_id, q.memory_id) for q in repo.list_questions(FAM)) == [("mom", "m1"), ("mom", "m2")]
    assert _asked_pairs(files) == [("m1", "mom"), ("m2", "mom")]


def _repo_with_aunt_and_m1() -> InMemoryRepo:
    repo = make_repo()
    repo.add_person(FAM, schemas.Person(id="aunt", family_id=FAM, name="Aunt May",
                                        character=schemas.CharacterSheet(color="#00aa00")))
    repo.save_memory(FAM, Memory(id="m1", family_id=FAM, title="Church", year=1988, participant_ids=["dad"],
                                 tellings=[schemas.Telling(author_id="dad", text=DAD_TEXT)]))
    return repo


@pytest.mark.parametrize("rebuild_tools", [False, True])
def test_parallel_link_calls_record_everyone_they_asked(rebuild_tools: bool) -> None:
    """Reviewer finding: one message with a call per person (the linker's pattern) loses no record."""
    repo = _repo_with_aunt_and_m1()
    tools = make_family_tools(repo, dad_run())
    replies, files = _call_tools_in_parallel(tools, {}, "link_participants", [
        {"memory_id": "m1", "person_ids": ["mom"], "question": "What do you remember?"},
        {"memory_id": "m1", "person_ids": ["aunt"], "question": "What do you remember?"},
    ])
    assert replies == ["linked mom to m1; asked mom", "linked aunt to m1; asked aunt"]
    assert _asked_pairs(files) == [("m1", "aunt"), ("m1", "mom")]
    memory = repo.get_memory(FAM, "m1")
    assert memory is not None and sorted(memory.participant_ids) == ["aunt", "dad", "mom"]

    if rebuild_tools:
        tools = make_family_tools(repo, dad_run())
    again, _, _ = _call_tool(tools, files, "link_participants",
                             {"memory_id": "m1", "person_ids": ["mom", "aunt"], "question": "?"}, call_id="call_9")
    assert again == "linked mom, aunt to m1; no one new to ask; already asked: mom, aunt"
    assert sorted((q.person_id, q.memory_id) for q in repo.list_questions(FAM)) == [("aunt", "m1"), ("mom", "m1")]


def test_parallel_link_calls_for_the_same_person_ask_once() -> None:
    repo = _repo_with_aunt_and_m1()
    args = {"memory_id": "m1", "person_ids": ["mom"], "question": "What do you remember?"}
    replies, files = _call_tools_in_parallel(make_family_tools(repo, dad_run()), {}, "link_participants",
                                             [args, args, args])
    assert sorted(replies) == ["linked mom to m1; asked mom", *["linked mom to m1; no one new to ask; already asked: mom"] * 2]
    assert [(q.person_id, q.memory_id) for q in repo.list_questions(FAM)] == [("mom", "m1")]
    assert _asked_pairs(files) == [("m1", "mom")]


def test_asked_record_paths_escape_ids() -> None:
    assert asked_question_path("m/1", "mom") == f"{ASKED_QUESTIONS_DIR}m%2F1/mom"
    assert asked_question_path("m1", "a/b") != asked_question_path("m1/a", "b")


def test_two_linker_passes_through_deepagents_task_ask_once() -> None:
    """The linker runs as a sub-agent: its asked-question files reach the studio's state
    through deepagents' `task`, so a second linker pass in the same run sees it."""
    from deepagents import create_deep_agent
    from langchain_core.messages import HumanMessage

    from agent.fake_llm import ScriptedChatModel, tool_call

    repo = make_repo()
    repo.save_memory(FAM, Memory(id="m1", family_id=FAM, title="Church", year=1988, participant_ids=["dad"],
                                 tellings=[schemas.Telling(author_id="dad", text=DAD_TEXT)]))
    tools = {t.name: t for t in make_family_tools(repo, dad_run())}
    studio_turns = iter([
        tool_call("task", {"subagent_type": "linker", "description": "Ask mom about m1."}),
        tool_call("task", {"subagent_type": "linker", "description": "Ask mom about m1 again."}),
        AIMessage(content="Linked."),
    ])

    def responder(messages: list, names: list[str]) -> AIMessage:
        if "task" in names:
            return next(studio_turns)
        if isinstance(messages[-1], HumanMessage):
            return tool_call("link_participants",
                             {"memory_id": "m1", "person_ids": ["mom"], "question": "What do you remember?"})
        return AIMessage(content=str(messages[-1].content))

    agent = create_deep_agent(
        model=ScriptedChatModel(responder=responder),
        tools=[],
        subagents=[{"name": "linker", "description": "Links people.", "system_prompt": "Link.",
                    "tools": [tools["get_person"], tools["link_participants"]]}],
    )
    final = agent.invoke({"messages": [HumanMessage(content="Go.")]})

    replies = [str(m.content) for m in final["messages"] if isinstance(m, ToolMessage) and m.name == "task"]
    assert replies == ["linked mom to m1; asked mom", "linked mom to m1; no one new to ask; already asked: mom"]
    assert [(q.person_id, q.memory_id) for q in repo.list_questions(FAM)] == [("mom", "m1")]
    assert _asked_pairs(final["files"]) == [("m1", "mom")]
