"""agent/fake_studio.py: the deterministic multi-agent brain, driving the real studio end to end.

Every run builds the real studio graph through agent.wiring (make_manager over
fake_services: InMemoryRepo seeded from the hymn_books sample, ArtService(None, None)
placeholders, FakeTTS, FakeVision, the fake frame renderer and critic, the compiler from
load_compiler()), with fake_studio_model as the model, and drives it through
runs.RunManager with stream_run collected on asyncio. No keys, no network.

These tests pin the fake brain's own behaviour (detours, pauses, loops); the public
seam's end-to-end contract is in test_agent_e2e.py.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from pydantic import BaseModel

import schemas
from agent import wiring
from agent.fake_studio import (
    FIRST_BEAT_SECONDS,
    FakeStory,
    bad_screenplay,
    build_screenplay,
    early_cue_index,
    fake_studio_model,
    file_body,
    make_fake_studio_responder,
    narration_by_shot,
    narration_lines,
    role_of,
    shot_sentences,
    surface_sockets,
)
from agent.repo import InMemoryRepo, load_sample_family
from agent.subagents import STUDIO_PROMPT, SUBAGENT_NAMES, load_prompt
from agent.tools.props import SIZES
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

class Harness:
    """One RunManager from agent.wiring over one seeded InMemoryRepo, with the fake brain."""

    def __init__(self, story: FakeStory = FakeStory()) -> None:  # noqa: B008
        self.story = story
        self.repo = InMemoryRepo()
        load_sample_family(self.repo, FAMILY)
        self.model = fake_studio_model(story)
        self.services = wiring.fake_services(model=self.model, repo=self.repo)
        self.manager = wiring.make_manager(self.services)

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
    assert {a.do for a in sp.shots[0].actions} >= {"walk_to", "hand_to", "pose"}
    # The directing skill: the walk on is unnamed setup, every beat a line may cue has an id,
    # and the first named beat is 2.5 s or more in.
    named = [a for a in sp.shots[0].actions if a.id]
    assert [a.id for a in named] == ["hand_over", "wave"]
    assert all(a.id for a in sp.shots[0].actions if a.do != "walk_to")
    assert min(a.t for a in named) >= FIRST_BEAT_SECONDS
    lines = sp.shots[0].narration
    assert lines and all(ln.source and ln.source.startswith("telling:mom:s") for ln in lines)
    assert lines[0].cue == "hand_over" and lines[0].cue_word

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
    # A vehicle is too big to carry: it stands on its own mark, and nobody holds it.
    shot = memory.screenplay.shots[0]
    assert shot.prop_start[prop_id].at == "prop_spot" and "prop_spot" in shot.marks
    assert all(s.holding is None for s in shot.start.values())
    assert all(a.what != prop_id for a in shot.actions)


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
    # With no prop to hand over, the main beat is dad's wave, and the pull tab plays it.
    assert sp.interactions and sp.interactions[0].drives.targets == ["puppet:dad"]


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
    # The linker asked dad for his version after the first run; his telling answered it.
    assert h.repo.list_questions(FAMILY) == []


MOM_RETELLING = "The hymn books slipped and went everywhere. He gave every one of them back. I could not look up."


def test_the_default_narrator_retelling_rewrites_each_shots_narration(harnesses: list[Harness]) -> None:
    """narrator.md: when the teller is the memory's default narrator, the second-telling pass
    rewrites each shot's own narration (never narrations[<authorId>], a duplicate_telling), cued
    so its word falls where the action already is: the saved action stays where it was."""
    h = make(harnesses)
    original = saved_memory(h, h.run_through(h.start(), KEEP))
    assert original.screenplay is not None and original.timeline is not None

    run_id = h.start(person_id="mom", text=MOM_RETELLING, memory_id=original.id, participant_ids=["dad"])
    events = h.run_through(run_id)
    assert [n for n, _ in events][-1] == "done", [n for n, _ in events]
    assert not [p for n, p in events if n == "compile_error"]
    memory = saved_memory(h, events)
    sp = memory.screenplay
    assert sp is not None and memory.timeline is not None
    assert "mom" not in (sp.narrations or {})
    lines = sp.shots[0].narration
    assert [ln.source for ln in lines] == ["telling:mom:s1", "telling:mom:s2", "telling:mom:s3"]
    assert lines[0].text == "The hymn books slipped and went everywhere." and lines[0].cue == "hand_over"
    assert lines[0].cue_word == "hymn"  # the first word after the filler "The"
    assert memory.timeline.default_narrator == "mom"
    # The shared action and its shots, apart from the narration, are as saved.
    strip = {"narration"}
    assert [s.model_dump(exclude=strip) for s in sp.shots] == [
        s.model_dump(exclude=strip) for s in original.screenplay.shots]


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
    # Dad's telling answered his question; gran's is still open.
    assert [q.person_id for q in h.repo.list_questions(FAMILY)] == ["gran"]


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
    hand_over = next(a for a in good.shots[0].actions if a.id == "hand_over")
    # An early cue word ("I was carrying ..."), its line started so the word falls on the action.
    assert lines[0]["cue"] == "hand_over" and lines[0]["cueWord"] == "carrying"
    assert lines[0]["t"] == round(hand_over.t - 2 * 0.25, 2)


def test_the_default_draft_follows_the_directing_skill() -> None:
    """Unnamed setup, the first named beat 2.5 s or more in, a close-up that lands on it, a pull
    tab scrubbing it, and every shape (two people, one person, with and without a prop) compiles
    on the real compiler with nothing moved by its narration."""
    from agent.tools.compile import load_compiler, parse_and_compile
    from agent.wiring import scene_for
    from screenplay.narration import retime_actions

    repo = InMemoryRepo()
    load_sample_family(repo, FAMILY)
    base = {"names": {"mom": "Ana", "dad": "David"}, "setId": "church_steps_1988", "year": 1988,
            "place": "church steps", "marks": {"steps_left": [0.2, 0.0], "curb": [0.9, 0.0]}}
    books = [{"id": "hymn_books", "name": "hymn books", "size": "carried"}]
    for cast, props in ((["mom", "dad"], books), (["mom", "dad"], []), (["mom"], books), (["mom"], [])):
        ctx = {**base, "teller": "mom", "cast": cast, "props": props}
        draft = build_screenplay(ctx)
        shot = draft["shots"][0]
        named = [a for a in shot["actions"] if a.get("id")]
        assert shot["actions"][0]["do"] == "walk_to" and "id" not in shot["actions"][0], cast
        assert named and min(a["t"] for a in named) >= FIRST_BEAT_SECONDS, (cast, props)
        beat = min(named, key=lambda a: a["t"])
        wide, close = shot["camera"]
        assert wide == {"t": 0, "move": "wide"}
        assert close["move"] == "close_up" and close["t"] + close["dur"] == pytest.approx(beat["t"])
        (tab,) = draft["interactions"]
        assert tab["kind"] == "pull_tab" and tab["drives"]["from"] == beat["t"] < tab["drives"]["to"]
        shot["narration"] = narration_lines({"teller": "mom", "text": MOM_STORY}, draft)
        assert shot["narration"][0]["cue"] == beat["id"]
        sp = Screenplay.model_validate(draft)
        assert retime_actions(sp.shots[0], 0.0) == [a.t for a in sp.shots[0].actions]  # nothing moves
        res = parse_and_compile(json.dumps(draft), load_compiler(), scene_for(repo, FAMILY))
        assert res.ok, (cast, props, [e.message for e in res.errors])


def test_the_narrator_cues_an_early_word_and_never_starts_before_zero() -> None:
    words = "It was pouring at the bus stop".split()
    assert early_cue_index(words, latest=10) == 2  # "pouring": the first word that names something
    assert early_cue_index(words, latest=1) == 1  # the action is too early: an earlier word
    assert early_cue_index(["the", "and", "of", "it", "was"], latest=10) == 3  # all filler: the 4th word
    assert early_cue_index([], latest=3) == 0
    draft = {"shots": [{"id": "s1", "actions": [{"t": 0.3, "do": "wave", "id": "wave"}]}]}
    (line,) = narration_by_shot({"teller": "mom", "text": "It was pouring at the bus stop."}, draft)["s1"]
    assert line["cueWord"] == "was" and line["t"] == 0.05  # 0.3 - 1 word * 0.25, not below 0


def test_the_prop_master_gives_surface_furniture_its_socket() -> None:
    assert surface_sockets("picnic table", "furniture") == {"top": [0.5, 0.1]}
    assert surface_sockets("stove", "furniture") == {"burner": [0.5, 0.2]}
    assert surface_sockets("park bench", "furniture") is None  # the default seat
    assert surface_sockets("table lamp", "hand") is None


def test_file_body_strips_the_read_file_header_and_old_gutters() -> None:
    assert file_body('@@ lines 1-3 of 3 @@\n{\n  "a": 1\n}') == '{\n  "a": 1\n}'
    assert file_body('     1\t{\n     2\t  "a": 1\n     3\t}') == '{\n  "a": 1\n}'
    assert file_body("1  {\n2  \"a\": \"xx\n2.1  yy\"\n3  }") == '{\n"a": "xxyy"\n}'
    assert file_body("plain") == "plain"


def test_default_prop_size_is_a_size_class() -> None:
    assert FakeStory().prop_size in SIZES


# --------------------------------------------------------------------------- story options


def test_story_options_default_to_the_one_shot_scene() -> None:
    story = FakeStory()
    assert story.extra_props == () and story.max_cast == 2 and story.screenplay is None
    assert story.objects == ["hymn books"] and story.size_of("hymn books") == "carried"
    more = FakeStory(prop="cake", extra_props=(("table", "furniture"), ("cake", "hand")))
    assert more.objects == ["cake", "table", "cake"]
    assert more.size_of("table") == "furniture" and more.size_of("cake") == "carried"


def test_extra_props_are_confirmed_in_one_review_and_max_cast_limits_the_cast(harnesses: list[Harness]) -> None:
    h = make(harnesses, FakeStory(extra_props=(("picnic basket", "carried"), ("park bench", "furniture")), max_cast=1))
    run_id = h.start()  # mom tells it, dad was there
    assert h.wait(run_id) == "waiting"
    found = [p for n, p in h.manager.events(FAMILY, run_id) if n == "props_found"]
    assert len(found) == 1
    chips = found[0].props  # type: ignore[attr-defined]
    assert [c.id for c in chips][0] == "hymn_books" and len(chips) == 3
    events = h.run_through(run_id, KEEP)
    assert [n for n, _ in events][-1] == "done"
    assert len(h.tool_calls("confirm_props", "prop_master")) == 1
    assert [c["description"] for c in h.tool_calls("find_prop", "prop_master")] == [
        "hymn books", "picnic basket", "park bench"]
    board = h.tool_calls("write_file", "storyboarder")[0]["content"]
    assert "picnic_basket" in board and "park_bench" in board

    memory = saved_memory(h, events)
    sp = memory.screenplay
    assert sp is not None
    assert sp.cast == ["mom"]  # max_cast 1: the teller only, though dad was there
    assert sp.props[0] == "hymn_books" and len(sp.props) == 3
    bench = next(pid for pid in sp.props if "bench" in pid)
    assert h.repo.get_prop(FAMILY, bench).size == "furniture"  # type: ignore[union-attr]
    assert {e.target for e in sp.explanations or []} == {"puppet:mom", *(f"prop:{p}" for p in sp.props)}
    assert "dad" in memory.participant_ids


def test_a_story_screenplay_replaces_the_default_draft(harnesses: list[Harness]) -> None:
    """`FakeStory.screenplay` is the director: its first draft is broken by bad_screenplay, the
    second compiles, and the narrator narrates both of its shots."""
    seen: list[dict] = []

    def two_shots(ctx: dict) -> dict:
        seen.append(ctx)
        draft = build_screenplay(ctx)
        second = json.loads(json.dumps(draft["shots"][0]))
        second.update(id="s2", camera=[{"t": 0, "move": "close_up", "target": ctx["teller"], "dur": 1.5}],
                      pageText="And again.")
        draft["shots"].append(second)
        return draft

    h = make(harnesses, FakeStory(screenplay=two_shots))
    events = h.run_through(h.start(), KEEP)
    assert [n for n, _ in events][-1] == "done"
    errors = [p for n, p in events if n == "compile_error"]
    assert len(errors) == 1 and any("nowhere" in e.message for e in errors[0].errors)  # type: ignore[attr-defined]
    ctx = seen[-1]
    assert {"teller", "tellerName", "text", "year", "place", "cast", "names", "props", "setId", "marks"} <= set(ctx)
    assert ctx["props"] == [{"id": "hymn_books", "name": "hymn books", "size": "hand"}]

    sp = saved_memory(h, events).screenplay
    assert sp is not None and [s.id for s in sp.shots] == ["s1", "s2"]
    # Three sentences over two shots: two, then one; each shot's first line cued and voiced.
    assert [ln.source for ln in sp.shots[0].narration] == ["telling:mom:s1", "telling:mom:s2"]
    assert [ln.source for ln in sp.shots[1].narration] == ["telling:mom:s3"]
    for shot in sp.shots:
        assert shot.narration[0].cue == "hand_over" and all(ln.words for ln in shot.narration)


def test_bad_screenplay_breaks_the_given_draft() -> None:
    ctx = {"teller": "mom", "cast": ["mom"], "props": [], "setId": "set", "marks": {}}

    def custom(_: dict) -> dict:
        draft = build_screenplay(ctx)
        draft["shots"][0]["actions"].insert(0, {"t": 0, "do": "rain"})
        return draft

    bad = bad_screenplay(ctx, custom)
    assert bad["shots"][0]["actions"][0] == {"t": 0, "do": "rain", "to": "nowhere"}
    assert bad_screenplay(ctx)["shots"][0]["actions"][0]["to"] == "nowhere"


def test_shot_sentences_spreads_the_telling_in_order() -> None:
    five = ["a.", "b.", "c.", "d.", "e."]
    assert shot_sentences(five, 2) == [[(1, "a."), (2, "b."), (3, "c.")], [(4, "d."), (5, "e.")]]
    assert shot_sentences(five[:2], 3) == [[(1, "a.")], [(2, "b.")], [(2, "b.")]]  # the last one reused
    assert shot_sentences(five, 1) == [[(1, "a."), (2, "b."), (3, "c."), (4, "d.")]]  # at most 4 a shot
    assert shot_sentences([], 2) == [[], []]
    assert shot_sentences(five, 0) == []


def _two_shot_draft() -> dict:
    def shot(sid: str, actions: list[dict]) -> dict:
        return {"id": sid, "set": "set", "start": {}, "camera": [], "actions": actions, "narration": []}

    return {"shots": [
        shot("s1", [{"t": 0, "do": "rain"}, {"t": 3.5, "do": "walk_to", "id": "late"},
                    {"t": 0.5, "do": "enter", "id": "early"}]),
        shot("s2", [{"t": 0.2, "do": "wave", "id": "wave"}]),
        shot("s3", []),
    ]}


def test_narration_by_shot_cues_each_shots_first_named_action_without_overlap() -> None:
    text = "We got there late. Everyone was already inside the hall. She waved at me from the door."
    by_shot = narration_by_shot({"teller": "mom", "text": text}, _two_shot_draft())
    assert list(by_shot) == ["s1", "s2", "s3"]
    s1, s2, s3 = by_shot["s1"], by_shot["s2"], by_shot["s3"]
    # The earliest named action, not the first in the list, on an early word ("got", after the
    # filler "We") that lands on t=0.5: the line starts one word earlier.
    assert s1[0]["cue"] == "early" and s1[0]["cueWord"] == "got" and s1[0]["t"] == 0.25
    # An action at 0.2 leaves room for no word before the cue word: the first word, from 0.2.
    assert s2[0]["cue"] == "wave" and s2[0]["cueWord"] == "Everyone" and s2[0]["t"] == 0.2
    assert [ln["source"] for ln in s2] == ["telling:mom:s2"]
    assert s3 == [{"t": 0.0, "text": "She waved at me from the door.", "source": "telling:mom:s3"}]  # nothing to cue
    late = narration_by_shot({"teller": "mom", "text": "We got there late."},
                             {"shots": [{"id": "s1", "actions": [{"t": 3.5, "do": "walk_to", "id": "late"}]}]})
    assert late["s1"][0]["t"] == 3.25  # 3.5 - 1 word * 0.25: "got" is said as the walk starts
    for lines in by_shot.values():
        for a, b in zip(lines, lines[1:], strict=False):
            assert b["t"] >= a["t"] + 0.25 * len(a["text"].split())


def test_second_telling_narration_covers_every_shot_and_lands_on_the_shared_action() -> None:
    draft = _two_shot_draft()
    draft["shots"][0]["narration"] = [{"t": 0.0, "text": "We got there late.", "cue": "early",
                                       "words": [{"t": 0.0, "word": "We"}, {"t": 0.3, "word": "got"},
                                                 {"t": 0.6, "word": "there"}, {"t": 0.9, "word": "late"}]}]
    long = "I remember every single second of that long and very strange and wonderful evening at the hall."
    ctx = {"teller": "dad", "text": f"{long} We danced. Then she waved. It was late.", "mode": "second_telling"}
    by_shot = narration_by_shot(ctx, draft)
    assert list(by_shot) == ["s1", "s2", "s3"]
    first = by_shot["s1"][0]
    # The shared action is spoken at 0.9 s (mom's "late"). A last-word cue would start the line
    # 3.1 s before the shot's t=0 (more than the 1.2 s its set takes to rise); the narrator cues
    # an early word instead and starts the line so it falls on the action.
    assert first["cue"] == "early" and first["cueWord"] == "remember" and first["t"] == 0.65
    assert by_shot["s1"][1]["t"] > 0.9 + 0.25 * (len(long.split()) - 1)  # after the cued line is said
    # Nothing cues the wave in the default telling: its written t, 0.2; "waved" is the 3rd word,
    # so the line is written at 0 and the compiler starts it 0.3 s early, inside the 1.2 s rise.
    second = by_shot["s2"][0]
    assert second["cue"] == "wave" and second["cueWord"] == "waved" and second["t"] == 0.0
