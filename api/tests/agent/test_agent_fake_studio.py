"""agent/fake_studio.py: the deterministic multi-agent brain, driving the real studio end to end.

Every run builds the real studio graph (studio.build_studio with the eight sub-agents from
build_subagents) over the real tools on fake services (InMemoryRepo seeded from the
hymn_books sample, ArtService(None, None) placeholders, FakeTTS, FakeVision, the compiler
from load_compiler()), and drives it through runs.RunManager with stream_run collected on
asyncio. No keys, no network.

agent.wiring is written in parallel, so the graph is assembled here.
"""

from __future__ import annotations

import asyncio
import importlib
import inspect
import json
from collections.abc import Iterator
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore
from pydantic import BaseModel

import schemas
from agent import runs, studio
from agent.fake_studio import (
    FakeStory,
    bad_screenplay,
    build_screenplay,
    fake_studio_model,
    file_body,
    make_fake_studio_responder,
    narration_lines,
    role_of,
)
from agent.repo import InMemoryRepo, load_sample_family
from agent.run_info import RunInfo
from agent.subagents import STUDIO_PROMPT, SUBAGENT_NAMES, build_subagents, load_prompt
from agent.tools import ask
from agent.tools.art import ArtService, make_cut_out_tool
from agent.tools.compile import SceneInputs, SceneLookupError, load_compiler, make_compile_tool
from agent.tools.family import make_family_tools
from agent.tools.narration import FakeTTS, make_narration_tool, voice_for
from agent.tools.photos import FakeVision, make_photo_tools
from agent.tools.props import SIZES, make_prop_tools
from agent.tools.sets import make_set_tools
from screenplay.schema import Screenplay

FAMILY = "fam_fake_studio"
USER = "user_mom"
WAIT = 30.0
MOM_STORY = (
    "I was carrying far too many hymn books on the church steps. "
    "They went everywhere, right in front of him. "
    "He handed them back so carefully."
)
DAD_STORY = "She dropped every single hymn book. I handed them back, and she would not look at me."


# --------------------------------------------------------------------------- graph


def _frame_tools(repo: InMemoryRepo, run: RunInfo, scene_for: Any) -> list[BaseTool]:
    """render_frames + inspect_frames on fakes, when agent.tools.frames has them yet."""
    frames = importlib.import_module("agent.tools.frames")
    make = getattr(frames, "make_frame_tools", None)
    renderer_cls = getattr(frames, "FakeFrameRenderer", None)
    critic_cls = getattr(frames, "FakeFrameCritic", None)
    if make is None or renderer_cls is None or critic_cls is None:
        return []
    available = {
        "repo": repo, "run": run, "renderer": renderer_cls(), "vision": critic_cls(), "critic": critic_cls(),
        "scene_for": scene_for,
    }
    params = inspect.signature(make).parameters
    return list(make(**{name: available[name] for name in params if name in available}))


FRAMES_AVAILABLE = bool(getattr(importlib.import_module("agent.tools.frames"), "make_frame_tools", None))


class Harness:
    """One RunManager over one InMemoryRepo, building the real studio with the fake brain."""

    def __init__(self, story: FakeStory = FakeStory()) -> None:  # noqa: B008
        self.story = story
        self.repo = InMemoryRepo()
        load_sample_family(self.repo, FAMILY)
        self.saver = InMemorySaver()
        self.store = InMemoryStore()
        self.model = fake_studio_model(story)
        self.manager = runs.RunManager(repo=self.repo, build_graph=self.build)
        self.tool_names: list[str] = []

    def scene_for(self, screenplay: Screenplay) -> SceneInputs:
        scene = SceneInputs()
        for shot in screenplay.shots:
            set_def = self.repo.get_set(FAMILY, shot.set)
            if set_def is None:
                raise SceneLookupError("set", shot.set)
            scene.sets[shot.set] = set_def
        for prop_id in screenplay.props:
            prop = self.repo.get_prop(FAMILY, prop_id)
            if prop is None:
                raise SceneLookupError("prop", prop_id)
            scene.props[prop_id] = prop
        for person_id in screenplay.cast:
            person = self.repo.get_person(FAMILY, person_id)
            if person is None:
                raise SceneLookupError("person", person_id)
            scene.people[person_id] = person
        return scene

    def build(self, run: RunInfo):
        repo, fid = self.repo, run.family_id
        art = ArtService(None, None)

        def put_audio(data: bytes, content_type: str) -> str:
            return repo.put_media(fid, data, content_type, kind="audio")

        def put_cutout(data: bytes, content_type: str) -> str:
            return repo.put_media(fid, data, content_type, kind="cutout")

        def get_media(media_id: str) -> tuple[bytes, str] | None:
            return repo.get_media(fid, media_id)

        tools: list[BaseTool] = [
            *make_family_tools(repo, run),
            ask.ask_teller,
            ask.merge_people,
            *make_photo_tools(FakeVision(), get_media, run.photo_ids),
            *make_prop_tools(repo, run, art),
            *make_set_tools(repo, run, art),
            make_cut_out_tool(art, put_cutout, get_media),
            make_compile_tool(load_compiler(), self.scene_for),
            make_narration_tool(
                FakeTTS(),
                put_audio,
                author_id=run.author_id,
                voice=voice_for(repo.get_person(fid, run.author_id)),
                second_telling=run.is_second_telling,
            ),
            *_frame_tools(repo, run, self.scene_for),
        ]
        self.tool_names = [t.name for t in tools]
        return studio.build_studio(
            family_id=fid,
            model=self.model,
            tools=tools,
            subagents=build_subagents({t.name: t for t in tools}),
            interrupt_on=ask.INTERRUPT_ON,
            checkpointer=self.saver,
            store=self.store,
            system_prompt=STUDIO_PROMPT,
        )

    # ---------------------------------------------------------------- driving

    def start(self, person_id: str = "mom", text: str = MOM_STORY, **fields: Any) -> str:
        form = schemas.SubmitMemoryFields(**{"text": text, "year": 1988, "participant_ids": ["dad"], **fields})
        return self.manager.start_run(FAMILY, USER, person_id, form, [])

    def wait(self, run_id: str) -> schemas.RunStatus:
        status = self.manager.wait(FAMILY, run_id, timeout=WAIT)
        assert status != "running", f"run still running after {WAIT}s: {self.names(run_id)}"
        return status

    def names(self, run_id: str) -> list[str]:
        return [name for name, _ in self.manager.events(FAMILY, run_id)]

    def answer(self, run_id: str, answer: schemas.AnswerRequest) -> None:
        self.manager.answer_run(FAMILY, run_id, USER, answer)

    def collect(self, run_id: str) -> list[tuple[str, BaseModel]]:
        async def go() -> list[tuple[str, BaseModel]]:
            return [event async for event in self.manager.stream_run(FAMILY, run_id)]

        return asyncio.run(asyncio.wait_for(go(), WAIT))

    def run_through(self, run_id: str, *answers: schemas.AnswerRequest) -> list[tuple[str, BaseModel]]:
        """Answer each pause in order, then stream the whole run (replayed from the start)."""
        for answer in answers:
            assert self.wait(run_id) == "waiting", self.names(run_id)
            self.answer(run_id, answer)
        status = self.wait(run_id)
        assert status in ("done", "error"), (status, self.names(run_id))
        events = self.collect(run_id)
        assert_typed(events)
        return events

    def subagent_runs(self, role: str) -> int:
        """How many times `role` was started (its first model call has no tool results yet)."""
        return sum(
            1 for _, messages in self.model.calls
            if role_of(messages) == role and not any(isinstance(m, ToolMessage) for m in messages)
        )

    def tool_calls(self, name: str, role: str | None = None) -> list[dict[str, Any]]:
        """Args of every distinct call of tool `name` the model made (optionally by one role)."""
        seen: dict[str, dict[str, Any]] = {}
        for _, messages in self.model.calls:
            if role is not None and role_of(messages) != role:
                continue
            for m in messages:
                if isinstance(m, AIMessage):
                    for call in m.tool_calls:
                        if call["name"] == name:
                            seen.setdefault(call["id"], call["args"])
        return list(seen.values())


KEEP = schemas.AnswerRequest(kind="props", props=[])


@pytest.fixture
def harnesses() -> Iterator[list[Harness]]:
    made: list[Harness] = []
    yield made
    for h in made:
        h.manager.shutdown(wait=True)


def make(harnesses: list[Harness], story: FakeStory = FakeStory()) -> Harness:  # noqa: B008
    h = Harness(story)
    harnesses.append(h)
    return h


def assert_typed(events: list[tuple[str, BaseModel]]) -> None:
    for name, payload in events:
        assert name in schemas.RUN_EVENTS, name
        assert isinstance(payload, schemas.RUN_EVENTS[name]), (name, type(payload))


def first(events: list[tuple[str, BaseModel]], name: str, subagent: str | None = None) -> int:
    for i, (n, payload) in enumerate(events):
        if n == name and (subagent is None or getattr(payload, "name", None) == subagent):
            return i
    raise AssertionError(f"no {name} {subagent or ''} in {[n for n, _ in events]}")


def indexes(events: list[tuple[str, BaseModel]], name: str, subagent: str | None = None) -> list[int]:
    return [
        i for i, (n, payload) in enumerate(events)
        if n == name and (subagent is None or getattr(payload, "name", None) == subagent)
    ]


def saved_memory(h: Harness, events: list[tuple[str, BaseModel]]) -> schemas.Memory:
    done = events[-1][1]
    assert isinstance(done, schemas.DoneEvent)
    memory = h.repo.get_memory(FAMILY, done.memory_id)
    assert memory is not None
    return schemas.Memory.model_validate(memory.model_dump(mode="json", by_alias=True))


# --------------------------------------------------------------------------- the main run


def test_new_memory_run_end_to_end(harnesses: list[Harness]) -> None:
    h = make(harnesses)
    run_id = h.start()

    # The prop review pauses the run.
    # set_designer runs in parallel with prop_master, so its subagent_end may land after
    # props_found; only require that props_found has arrived and the run is waiting.
    assert h.wait(run_id) == "waiting"
    assert "props_found" in h.names(run_id)
    assert h.manager.status(FAMILY, run_id) == "waiting"
    events = h.run_through(run_id, KEEP)
    names = [n for n, _ in events]

    # Event order.
    assert names[0] == "plan"
    assert first(events, "subagent_start", "interviewer") < first(events, "subagent_start", "storyboarder")
    storyboard = first(events, "subagent_start", "storyboarder")
    assert storyboard < first(events, "subagent_start", "prop_master")
    assert storyboard < first(events, "subagent_start", "set_designer")
    props_found = first(events, "props_found")
    assert first(events, "subagent_start", "prop_master") < props_found
    found = events[props_found][1]
    assert isinstance(found, schemas.PropsFoundEvent)
    assert [p.id for p in found.props] == ["hymn_books"] and found.props[0].reused

    compile_errors = indexes(events, "compile_error")
    assert len(compile_errors) == 1
    error = events[compile_errors[0]][1]
    assert isinstance(error, schemas.CompileErrorEvent) and error.round == 1
    assert any("nowhere" in e.message for e in error.errors)
    director_ends = indexes(events, "subagent_end", "director")
    assert compile_errors[0] < director_ends[0]
    assert first(events, "subagent_start", "director") < compile_errors[0]

    scene_ready = first(events, "scene_ready")
    assert scene_ready < len(events) - 1
    assert names[-1] == "done" and names.count("done") == 1 and "error" not in names
    ready, done = events[scene_ready][1], events[-1][1]
    assert isinstance(ready, schemas.SceneReadyEvent) and isinstance(done, schemas.DoneEvent)
    assert done.memory_id == ready.memory_id and ready.version == 1

    # Every sub-agent ran, in pipeline order, and the last plan is all done.
    started = [p.name for n, p in events if n == "subagent_start"]  # type: ignore[attr-defined]
    assert started == [
        "interviewer", "storyboarder", "prop_master", "set_designer", "director",
        "narrator", "checker", "narrator", "linker",
    ]
    plans = [p for n, p in events if n == "plan"]
    assert len(plans) >= 3
    assert all(item.status == "completed" for item in plans[-1].todos)  # type: ignore[attr-defined]
    assert first(events, "subagent_end", "linker") < len(events) - 1

    # The saved memory.
    memory = saved_memory(h, events)
    assert memory.version == 1
    assert memory.tellings[0].author_id == "mom" and memory.tellings[0].text == MOM_STORY
    assert set(memory.participant_ids) >= {"mom", "dad"}
    assert memory.screenplay is not None and memory.timeline is not None
    sp, tl = memory.screenplay, memory.timeline
    assert sp.cast == ["mom", "dad"] and sp.props == ["hymn_books"]
    assert tl.shots and all(shot.page_text for shot in tl.shots)
    assert all(shot.page_text and len(shot.page_text.split()) <= 30 for shot in sp.shots)
    targets = {e.target for e in sp.explanations or []}
    assert targets == {"puppet:mom", "puppet:dad", "prop:hymn_books"}
    assert len(sp.explanations or []) == 3
    assert sp.interactions and sp.interactions[0].kind == "pull_tab"
    assert sp.interactions[0].drives.type == "scrub"
    assert {a.do for a in sp.shots[0].actions} >= {"walk_to", "hand_to", "wave"}
    assert all(a.id for a in sp.shots[0].actions)
    lines = sp.shots[0].narration
    assert lines and all(ln.source and ln.source.startswith("telling:mom:s") for ln in lines)
    assert lines[0].cue == "walk"

    assert tl.default_narrator == "mom"
    assert tl.audio is not None and h.repo.get_media(FAMILY, tl.audio) is not None
    assert tl.narrations is not None and tl.narrations["mom"].audio == tl.audio

    # Placeholder art: the reused prop and set are colored cards.
    prop = h.repo.get_prop(FAMILY, "hymn_books")
    assert prop is not None and prop.art.get("body") is None and prop.color
    set_def = h.repo.get_set(FAMILY, sp.shots[0].set)
    assert set_def is not None and set_def.layers
    assert all(layer.art is None and layer.color for layer in set_def.layers)

    # In both books, and dad (not the teller) is asked for his version.
    assert memory.id in [m.id for m in h.repo.find_memories(FAMILY, person_id="dad")]
    assert memory.id in [m.id for m in h.repo.find_memories(FAMILY, person_id="mom")]
    questions = [q for q in h.repo.list_questions(FAMILY) if q.memory_id == memory.id]
    assert [q.person_id for q in questions] == ["dad"]
    assert "Ana" in questions[0].text and "hymn books" in questions[0].text


def test_new_place_and_prop_are_generated_as_placeholders(harnesses: list[Harness]) -> None:
    h = make(harnesses, FakeStory(place="lake dock", prop="green rowboat", prop_size="vehicle", compile_failures=0))
    run_id = h.start(text="We rowed the green rowboat out from the lake dock.")
    events = h.run_through(run_id, KEEP)
    assert [n for n, _ in events][-1] == "done"
    assert "compile_error" not in [n for n, _ in events]

    memory = saved_memory(h, events)
    assert memory.screenplay is not None
    prop_id = memory.screenplay.props[0]
    prop = h.repo.get_prop(FAMILY, prop_id)
    assert prop is not None and prop.source == "generated" and prop.size == "vehicle"
    assert prop.art.get("body") is None and prop.color
    set_def = h.repo.get_set(FAMILY, memory.screenplay.shots[0].set)
    assert set_def is not None and set_def.id == "lake_dock"
    assert sorted(layer.depth for layer in set_def.layers) == [0, 1, 2]
    assert all(layer.art is None and layer.color for layer in set_def.layers)
    assert set_def.marks["left_spot"] == [0.3, 0] and set_def.marks["right_spot"] == [0.7, 0]
    assert memory.screenplay.shots[0].start["mom"].at == "left_spot"


# --------------------------------------------------------------------------- pauses


def test_followup_question_waits_for_the_answer(harnesses: list[Harness]) -> None:
    h = make(harnesses, FakeStory(followup="Which church was it?"))
    run_id = h.start()
    assert h.wait(run_id) == "waiting"
    assert h.names(run_id)[-1] == "question"
    events = h.run_through(
        run_id, schemas.AnswerRequest(kind="answer", text="St. Mary's, on Elm Street."), KEEP,
    )
    names = [n for n, _ in events]
    question = first(events, "question")
    assert events[question][1] == schemas.QuestionEvent(text="Which church was it?")
    assert question < first(events, "subagent_start", "storyboarder")
    interviewer_end = events[first(events, "subagent_end", "interviewer")][1]
    assert "St. Mary's, on Elm Street." in (interviewer_end.summary or "")  # type: ignore[attr-defined]
    assert names[-1] == "done"


def test_prop_review_rename_is_saved(harnesses: list[Harness]) -> None:
    h = make(harnesses)
    run_id = h.start()
    rename = schemas.AnswerRequest(
        kind="props", props=[schemas.PropEdit(id="hymn_books", action="rename", name="choir hymnals")],
    )
    events = h.run_through(run_id, rename)
    assert [n for n, _ in events][-1] == "done"
    prop = h.repo.get_prop(FAMILY, "hymn_books")
    assert prop is not None and prop.name == "choir hymnals"
    memory = saved_memory(h, events)
    assert memory.screenplay is not None and memory.screenplay.props == ["hymn_books"]
    # The director staged with the reviewed name.
    assert any("choir hymnals" in e.text for e in memory.screenplay.explanations or [])


def test_prop_review_remove_leaves_the_prop_out(harnesses: list[Harness]) -> None:
    h = make(harnesses)
    run_id = h.start()
    remove = schemas.AnswerRequest(kind="props", props=[schemas.PropEdit(id="hymn_books", action="remove")])
    events = h.run_through(run_id, remove)
    assert [n for n, _ in events][-1] == "done"
    memory = saved_memory(h, events)
    sp = memory.screenplay
    assert sp is not None
    assert sp.props == [] and memory.prop_ids == []
    assert all(a.what is None for a in sp.shots[0].actions)
    assert all(s.holding is None for s in sp.shots[0].start.values())
    assert not any(e.target.startswith("prop:") for e in sp.explanations or [])
    assert sp.interactions and sp.interactions[0].drives.targets == ["puppet:mom"]


# --------------------------------------------------------------------------- loops


def test_checker_problem_sends_the_director_back_and_recompiles(harnesses: list[Harness]) -> None:
    h = make(harnesses, FakeStory(checker_problems=1))
    run_id = h.start()
    events = h.run_through(run_id, KEEP)
    names = [n for n, _ in events]
    assert names[-1] == "done"

    starts = [p.name for n, p in events if n == "subagent_start"]  # type: ignore[attr-defined]
    assert starts.count("director") == 2 and starts.count("checker") == 2
    first_check_end = indexes(events, "subagent_end", "checker")[0]
    second_director = indexes(events, "subagent_start", "director")[1]
    assert first_check_end < second_director
    critique = events[first_check_end][1]
    assert "verdict: fix" in (critique.summary or "")  # type: ignore[attr-defined]

    # The studio compiled again after the director's fix, before the second check.
    studio_compiles = h.tool_calls("compile_screenplay", role="studio")
    assert len(studio_compiles) == 2
    director_compiles = h.tool_calls("compile_screenplay", role="director")
    assert len(director_compiles) == 3  # 1 failed + 1 ok while staging, 1 after the fix
    if FRAMES_AVAILABLE:
        assert "frames" in names
    memory = saved_memory(h, events)
    assert memory.screenplay is not None
    # The fix kept the narrator's lines.
    assert memory.screenplay.shots[0].narration


def test_three_failed_compiles_stop_the_run_with_one_error(harnesses: list[Harness]) -> None:
    h = make(harnesses, FakeStory(compile_failures=3))
    run_id = h.start()
    events = h.run_through(run_id, KEEP)
    names = [n for n, _ in events]
    assert names.count("compile_error") == 3
    rounds = [p.round for n, p in events if n == "compile_error"]  # type: ignore[attr-defined]
    assert rounds == [1, 2, 3]
    assert names[-1] == "error" and names.count("error") == 1
    assert "done" not in names and "scene_ready" not in names
    director_end = events[first(events, "subagent_end", "director")][1]
    assert "compiled: no" in (director_end.summary or "")  # type: ignore[attr-defined]
    assert "narrator" not in [p.name for n, p in events if n == "subagent_start"]  # type: ignore[attr-defined]
    assert h.repo.find_memories(FAMILY) == []
    assert h.repo.list_questions(FAMILY) == []


# --------------------------------------------------------------------------- second telling


def test_second_telling_adds_a_narration_over_the_same_action(harnesses: list[Harness]) -> None:
    h = make(harnesses)
    first_run = h.start()
    first_events = h.run_through(first_run, KEEP)
    original = saved_memory(h, first_events)
    assert original.timeline is not None

    second_run = h.start(person_id="dad", text=DAD_STORY, memory_id=original.id, participant_ids=["mom"])
    events = h.run_through(second_run)
    names = [n for n, _ in events]
    assert names[0] == "plan" and names[-1] == "done"
    assert "props_found" not in names and "question" not in names and "error" not in names
    done = events[-1][1]
    assert isinstance(done, schemas.DoneEvent) and done.memory_id == original.id
    ready = events[first(events, "scene_ready")][1]
    assert isinstance(ready, schemas.SceneReadyEvent) and ready.memory_id == original.id and ready.version == 2
    starts = [p.name for n, p in events if n == "subagent_start"]  # type: ignore[attr-defined]
    assert starts == ["narrator", "checker", "narrator"]  # mom already has the moment: no linker

    memory = saved_memory(h, events)
    assert memory.id == original.id and memory.version == 2
    assert [t.author_id for t in memory.tellings] == ["mom", "dad"]
    assert memory.timeline is not None and memory.screenplay is not None
    tl = memory.timeline
    assert set(tl.narrations or {}) == {"mom", "dad"}
    assert tl.default_narrator == original.timeline.default_narrator == "mom"
    assert tl.tracks == original.timeline.tracks
    assert tl.audio == original.timeline.audio
    assert (tl.narrations or {})["dad"].audio and (tl.narrations or {})["dad"].audio != tl.audio
    # The shared action is untouched; dad's lines live in narrations["dad"].
    assert memory.screenplay.shots == original.screenplay.shots  # type: ignore[union-attr]
    dad_lines = (memory.screenplay.narrations or {})["dad"]["s1"]
    assert [ln.source for ln in dad_lines] == ["telling:dad:s1", "telling:dad:s2"]
    assert h.repo.list_questions(FAMILY) and all(q.person_id == "dad" for q in h.repo.list_questions(FAMILY))


def test_second_telling_links_someone_new_to_the_moment(harnesses: list[Harness]) -> None:
    h = make(harnesses)
    dad = h.repo.get_person(FAMILY, "dad")
    assert dad is not None
    h.repo.add_person(FAMILY, dad.model_copy(update={"id": "gran", "name": "Rosa"}))
    first_run = h.start(participant_ids=[])  # mom alone
    original = saved_memory(h, h.run_through(first_run, KEEP))
    assert original.screenplay is not None and original.screenplay.cast == ["mom"]
    assert h.repo.list_questions(FAMILY) == []

    second_run = h.start(person_id="dad", text=DAD_STORY, memory_id=original.id, participant_ids=["mom", "gran"])
    events = h.run_through(second_run)
    assert [n for n, _ in events][-1] == "done"
    starts = [p.name for n, p in events if n == "subagent_start"]  # type: ignore[attr-defined]
    assert starts[-1] == "linker"
    memory = saved_memory(h, events)
    assert "gran" in memory.participant_ids and "dad" in memory.participant_ids
    assert [q.person_id for q in h.repo.list_questions(FAMILY)] == ["gran"]


def test_second_telling_does_not_relink_a_participant_who_was_not_cast(harnesses: list[Harness]) -> None:
    h = make(harnesses)
    dad = h.repo.get_person(FAMILY, "dad")
    assert dad is not None
    h.repo.add_person(FAMILY, dad.model_copy(update={"id": "gran", "name": "Rosa"}))
    first_run = h.start(participant_ids=["dad", "gran"])
    original = saved_memory(h, h.run_through(first_run, KEEP))
    assert original.screenplay is not None and original.screenplay.cast == ["mom", "dad"]
    assert set(original.participant_ids) == {"mom", "dad", "gran"}
    assert sorted(q.person_id for q in h.repo.list_questions(FAMILY)) == ["dad", "gran"]

    second_run = h.start(person_id="dad", text=DAD_STORY, memory_id=original.id, participant_ids=["mom", "gran"])
    events = h.run_through(second_run)
    assert [n for n, _ in events][-1] == "done"
    starts = [p.name for n, p in events if n == "subagent_start"]  # type: ignore[attr-defined]
    assert starts == ["narrator", "checker", "narrator"]  # gran is already in the moment: no linker
    assert sorted(q.person_id for q in h.repo.list_questions(FAMILY)) == ["dad", "gran"]


# --------------------------------------------------------------------------- unit pieces


def test_role_of_reads_the_system_prompt_prefix() -> None:
    assert role_of([SystemMessage(content=STUDIO_PROMPT + "\n\nmore"), HumanMessage(content="{}")]) == "studio"
    for name in SUBAGENT_NAMES:
        assert role_of([SystemMessage(content=load_prompt(name) + "\n\n## Skills"), HumanMessage(content="x")]) == name
    with pytest.raises(AssertionError):
        role_of([SystemMessage(content="You are someone else."), HumanMessage(content="x")])
    with pytest.raises(AssertionError):
        role_of([HumanMessage(content="no system prompt")])


def test_responder_skips_steps_whose_tools_are_not_bound() -> None:
    responder = make_fake_studio_responder(FakeStory())
    task = "Render the key moments.\nCONTEXT: " + json.dumps({"teller": "mom", "round": 0})
    messages = [SystemMessage(content=load_prompt("checker")), HumanMessage(content=task)]
    # No frame tools: it goes straight to the critique.
    reply = responder(messages, ["write_file", "read_file"])
    assert [c["name"] for c in reply.tool_calls] == ["write_file"]
    assert reply.tool_calls[0]["args"]["content"] == "verdict: pass (no frame tools)\n"
    # With them: render first.
    reply = responder(messages, ["write_file", "read_file", "render_frames", "inspect_frames"])
    assert reply.tool_calls[0]["name"] == "render_frames"
    assert reply.tool_calls[0]["args"] == {"times": [1.0, 2.0]}


def test_studio_starts_prop_master_and_set_designer_in_one_turn() -> None:
    responder = make_fake_studio_responder(FakeStory())
    context = {"authorId": "mom", "authorName": "Ana", "text": MOM_STORY, "year": 1988, "title": None,
               "question": None, "photoIds": [], "participantIds": ["dad"], "memoryId": None}
    messages: list[Any] = [SystemMessage(content=STUDIO_PROMPT), HumanMessage(content=json.dumps(context))]
    tools = ["write_todos", "task", "compile_screenplay", "save_memory"]
    replies = []
    for _ in range(8):
        reply = responder(messages, tools)
        replies.append(reply)
        messages.append(reply)
        if not reply.tool_calls:
            break
        for call in reply.tool_calls:
            if call["name"] == "task":
                result = {"interviewer": {"place": "church steps", "people": [
                    {"id": "mom", "name": "Ana"}, {"id": "dad", "name": "David"}]}}.get(
                    call["args"]["subagent_type"], {})
                content = "report\nRESULT: " + json.dumps(result)
            else:
                content = "ok"
            messages.append(ToolMessage(content=content, tool_call_id=call["id"], name=call["name"]))
        if len(reply.tool_calls) == 2:
            break
    parallel = replies[-1]
    assert [c["args"]["subagent_type"] for c in parallel.tool_calls] == ["prop_master", "set_designer"]
    for call in parallel.tool_calls:
        assert "\nCONTEXT: " in call["args"]["description"]
    assert replies[0].tool_calls[0]["name"] == "write_todos"


def test_drafts_validate_and_the_bad_one_names_an_unknown_mark() -> None:
    ctx = {"teller": "mom", "cast": ["mom", "dad"], "names": {"mom": "Ana", "dad": "David"},
           "props": [{"id": "hymn_books", "name": "hymn books"}], "setId": "church_steps_1988",
           "marks": {"steps_left": [0.2, 0.0], "steps_top": [0.6, 0.3], "curb": [0.9, 0.0]},
           "year": 1988, "place": "church steps"}
    good = Screenplay.model_validate(build_screenplay(ctx))
    assert good.shots[0].start["mom"].at == "steps_left" and good.shots[0].start["dad"].at == "curb"
    assert good.shots[0].start["mom"].holding == "hymn_books"
    bad = Screenplay.model_validate(bad_screenplay(ctx))
    assert bad.shots[0].actions[0].to == "nowhere"
    lines = narration_lines({"teller": "mom", "text": MOM_STORY}, build_screenplay(ctx))
    assert [ln["source"] for ln in lines] == ["telling:mom:s1", "telling:mom:s2", "telling:mom:s3"]
    assert lines[0]["cue"] == "walk"


def test_file_body_strips_the_read_file_header_and_old_gutters() -> None:
    assert file_body('@@ lines 1-3 of 3 @@\n{\n  "a": 1\n}') == '{\n  "a": 1\n}'
    assert file_body('     1\t{\n     2\t  "a": 1\n     3\t}') == '{\n  "a": 1\n}'
    assert file_body("1  {\n2  \"a\": \"xx\n2.1  yy\"\n3  }") == '{\n"a": "xxyy"\n}'
    assert file_body("plain") == "plain"


def test_default_prop_size_is_a_size_class() -> None:
    assert FakeStory().prop_size in SIZES
