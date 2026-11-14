"""Family tools: save_scene (new memory and second telling) and the tools bound to one family."""

from __future__ import annotations

import json
from typing import Annotated, Any, TypedDict

import pytest
from deepagents.backends.utils import create_file_data
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

import schemas
from agent.repo import SAMPLE_MEMORY_PATH, InMemoryRepo, load_sample_family
from agent.run_info import RunInfo
from agent.tools.family import era_for_year, make_family_tools, save_scene
from schemas import Memory, MemoryResponse, SubmitMemoryFields
from screenplay.schema import Screenplay, Timeline

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


def _moms_draft(*, via_shot_narration: bool = False) -> tuple[Screenplay, Timeline]:
    """A draft scene carrying mom's narration, with action that must NOT replace the saved action."""
    screenplay, timeline = sample_scene()
    assert screenplay.narrations is not None and timeline.narrations is not None
    moms_lines = screenplay.narrations["mom"]
    draft_sp = screenplay.model_copy(deep=True)
    if via_shot_narration:
        draft_sp.narrations = None
        for shot in draft_sp.shots:
            shot.narration = moms_lines.get(shot.id, [])
    draft_sp.shots[0].actions = draft_sp.shots[0].actions[:1]  # a different (wrong) action
    draft_tl = timeline.model_copy(deep=True)
    draft_tl.tracks = draft_tl.tracks[:1]  # different tracks
    return draft_sp, draft_tl


@pytest.mark.parametrize("via_shot_narration", [False, True])
def test_second_telling_adds_narration_over_the_existing_action(via_shot_narration: bool) -> None:
    repo = make_repo()
    existing = _existing_memory_without_mom(repo)
    assert existing.screenplay is not None and existing.timeline is not None
    draft_sp, draft_tl = _moms_draft(via_shot_narration=via_shot_narration)
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


def test_second_telling_requires_the_tellers_timeline_narration() -> None:
    repo = make_repo()
    existing = _existing_memory_without_mom(repo)
    draft_sp, draft_tl = _moms_draft()
    draft_tl.narrations = {"dad": draft_tl.narrations["dad"]}  # type: ignore[index]
    with pytest.raises(ValueError, match="mom"):
        save_scene(repo, mom_run(existing.id), title="x", participant_ids=[], screenplay=draft_sp, timeline=draft_tl)
    assert repo.get_memory(FAM, existing.id) == existing


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


class _State(TypedDict, total=False):
    messages: Annotated[list, add_messages]
    files: dict[str, Any]


def _run_save_memory(repo: InMemoryRepo, run: RunInfo, files: dict[str, Any]) -> tuple[str, list[dict]]:
    graph = StateGraph(_State)
    graph.add_node("tools", ToolNode(make_family_tools(repo, run)))
    graph.add_edge(START, "tools")
    graph.add_edge("tools", END)
    app = graph.compile()
    call = {"name": "save_memory", "args": {"title": "How I met your mother", "participant_ids": ["mom"]},
            "id": "call_1", "type": "tool_call"}
    events: list[dict] = []
    final: dict[str, Any] = {}
    for mode, chunk in app.stream({"messages": [AIMessage(content="", tool_calls=[call])], "files": files},
                                  stream_mode=["custom", "values"]):
        if mode == "custom":
            events.append(chunk)
        else:
            final = chunk
    message = final["messages"][-1]
    assert isinstance(message, ToolMessage) and message.tool_call_id == "call_1"
    return str(message.content), events


def _scene_files() -> dict[str, Any]:
    screenplay, timeline = sample_scene()
    return {
        "/screenplay.json": create_file_data(screenplay.model_dump_json(by_alias=True)),
        "/timeline.json": create_file_data(timeline.model_dump_json(by_alias=True)),
    }


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
