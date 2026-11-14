"""The studio end to end on fakes, driven only through the public seam (ARCHITECTURE.md section 2).

A RunManager from agent.wiring.make_manager(agent.wiring.fake_services(...)) over an
InMemoryRepo seeded with the hymn_books sample family, with agent.fake_studio playing the
model. Art is ArtService(None, None), so every prop and set layer the run makes is a
placeholder card (null art, a color). The compiler is whatever load_compiler() returns:
the real screenplay.compiler.compile once it exists, the FakeCompiler before that, so no
assertion here depends on one compiler's error codes or wording.

Runs are driven with start_run / answer_run and observed with stream_run, collected on an
asyncio loop while the run is live. No keys, no network; every wait has a timeout.
"""

from __future__ import annotations

import asyncio
import io
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from langchain_core.messages import ToolMessage
from PIL import Image
from pydantic import BaseModel

import schemas
from agent import runs, wiring
from agent.fake_studio import FakeStory, fake_studio_model
from agent.repo import InMemoryRepo, load_sample_family
from agent.tools.compile import MAX_COMPILE_ROUNDS
from agent.tools.narration import FakeTTS, Speech
from agent.tools.photos import FakeVision

FAMILY = "fam_e2e"
MOM_USER = "user_mom"
DAD_USER = "user_dad"
WAIT = 25.0
"""Seconds any one wait may take; a hung run fails the test instead of hanging it."""

FOLLOWUP = "Who else was on the dock that day?"
STORY = FakeStory(place="lake dock", prop="picnic basket", prop_size="carried", followup=FOLLOWUP, compile_failures=1)
"""A place and a prop the sample family has never saved, so the run makes both."""

MOM_TEXT = (
    "We carried the picnic basket down to the lake dock. "
    "I nearly dropped it at the end of the boards. "
    "He took it from me and laughed."
)
DAD_TEXT = "She was sure the basket would go in the lake. I took it before it did, and we both laughed."
ANSWER = "Only the two of us, early in the morning."
RENAMED = "wicker picnic basket"

Event = tuple[str, BaseModel]
Answer = schemas.AnswerRequest | Callable[[list[Event]], schemas.AnswerRequest]


# --------------------------------------------------------------------------- harness


class Studio:
    """One family's RunManager, built only from agent.wiring, and the repo behind it."""

    def __init__(self, story: FakeStory = STORY) -> None:
        self.repo = InMemoryRepo()
        load_sample_family(self.repo, FAMILY)
        self.services = wiring.fake_services(model=fake_studio_model(story), repo=self.repo)
        self.manager = wiring.make_manager(self.services)

    def start(self, user_id: str, person_id: str, text: str, **fields: Any) -> str:
        form = schemas.SubmitMemoryFields(text=text, year=1994, **fields)
        return self.manager.start_run(FAMILY, user_id, person_id, form, [])

    def wait(self, run_id: str) -> schemas.RunStatus:
        status = self.manager.wait(FAMILY, run_id, timeout=WAIT)
        assert status != "running", f"run still running after {WAIT}s: {self.names(run_id)}"
        return status

    def names(self, run_id: str) -> list[str]:
        return [name for name, _ in self.manager.events(FAMILY, run_id)]

    def collect(self, run_id: str) -> list[Event]:
        """stream_run from the start until the run ends (a late subscriber once it has)."""

        async def go() -> list[Event]:
            return [event async for event in self.manager.stream_run(FAMILY, run_id)]

        return asyncio.run(asyncio.wait_for(go(), WAIT))

    def drive(
        self, user_id: str, person_id: str, text: str, answers: list[Answer] | None = None,
        *, photo_ids: list[str] | None = None, **fields: Any,
    ) -> tuple[str, list[Event]]:
        """Start a run, follow it live with stream_run, and answer each pause in turn.

        `answers` may hold callables taking the events so far (to answer from what a
        pause showed). Returns the run id and every event the live subscriber saw.
        """
        form = schemas.SubmitMemoryFields(text=text, year=1994, **fields)
        pending = list(answers or [])

        async def go() -> tuple[str, list[Event]]:
            run_id = self.manager.start_run(FAMILY, user_id, person_id, form, list(photo_ids or []))
            seen: list[Event] = []

            async def follow() -> None:
                async for event in self.manager.stream_run(FAMILY, run_id):
                    seen.append(event)

            follower = asyncio.create_task(follow())
            while True:
                status = await asyncio.to_thread(self.manager.wait, FAMILY, run_id, WAIT)
                assert status != "running", f"run still running after {WAIT}s: {self.names(run_id)}"
                if status != "waiting":
                    break
                assert pending, f"run paused with no answer left: {self.names(run_id)}"
                answer = pending.pop(0)
                if callable(answer):
                    answer = answer(self.manager.events(FAMILY, run_id))
                self.manager.answer_run(FAMILY, run_id, user_id, answer)  # type: ignore[arg-type]
            await asyncio.wait_for(follower, WAIT)
            assert not pending, f"answers left over: {pending}"
            return run_id, seen

        return asyncio.run(asyncio.wait_for(go(), 4 * WAIT))

    def memory(self, memory_id: str) -> schemas.Memory:
        memory = self.repo.get_memory(FAMILY, memory_id)
        assert memory is not None, memory_id
        # Round-trip through the API contract's JSON so the saved shape is what the web reads.
        return schemas.Memory.model_validate(memory.model_dump(mode="json", by_alias=True))


@pytest.fixture
def studios() -> Iterator[list[Studio]]:
    made: list[Studio] = []
    yield made
    for s in made:
        s.manager.shutdown(wait=True)


def new_studio(studios: list[Studio], story: FakeStory = STORY) -> Studio:
    s = Studio(story)
    studios.append(s)
    return s


def photo_png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (64, 48), (40, 110, 170)).save(buf, format="PNG")
    return buf.getvalue()


def rename_first_prop(events: list[Event]) -> schemas.AnswerRequest:
    found = [p for n, p in events if n == "props_found"]
    assert len(found) == 1, [n for n, _ in events]
    chips = found[0].props  # type: ignore[attr-defined]
    assert chips, "props_found carried no props"
    return schemas.AnswerRequest(
        kind="props",
        props=[
            schemas.PropEdit(id=chip.id, action="rename", name=RENAMED) if i == 0
            else schemas.PropEdit(id=chip.id, action="keep")
            for i, chip in enumerate(chips)
        ],
    )


def assert_typed(events: list[Event]) -> None:
    for name, payload in events:
        assert name in schemas.RUN_EVENTS, name
        assert isinstance(payload, schemas.RUN_EVENTS[name]), (name, type(payload))


def assert_subsequence(names: list[str], expected: list[str]) -> None:
    it = iter(enumerate(names))
    for want in expected:
        for _, got in it:
            if got == want:
                break
        else:
            raise AssertionError(f"{expected} is not an ordered subsequence of {names} (missing {want!r})")


def assert_paired(events: list[Event]) -> None:
    """Every subagent_start has its own subagent_end later, matched per sub-agent name."""
    open_runs: dict[str, int] = {}
    for name, payload in events:
        if name == "subagent_start":
            open_runs[payload.name] = open_runs.get(payload.name, 0) + 1  # type: ignore[attr-defined]
        elif name == "subagent_end":
            who = payload.name  # type: ignore[attr-defined]
            assert open_runs.get(who, 0) > 0, f"subagent_end for {who} without a start"
            open_runs[who] -= 1
    assert all(n == 0 for n in open_runs.values()), open_runs


def non_narration_tracks(timeline: schemas.Timeline) -> Any:
    """The action: the timeline's tracks without anything a telling's voice adds."""
    data = timeline.model_dump(mode="json", by_alias=True)
    for key in ("audio", "captions", "narrations", "defaultNarrator"):
        data.pop(key, None)
    return data


# --------------------------------------------------------------------------- the new memory


@pytest.fixture
def first_run(studios: list[Studio]) -> tuple[Studio, str, list[Event], dict[str, Any]]:
    """(a): mom tells a new memory with dad in it and one photo; answers the follow-up and renames a prop."""
    s = new_studio(studios)
    photo_id = s.repo.put_media(FAMILY, photo_png(), "image/png", kind="photo")
    before = {
        "props": {p.id for p in s.repo.list_props(FAMILY)},
        "sets": {d.id for d in s.repo.list_sets(FAMILY)},
        "memories": {m.id for m in s.repo.find_memories(FAMILY, limit=1000)},
        "photo": photo_id,
    }
    run_id, events = s.drive(
        MOM_USER, "mom", MOM_TEXT,
        [schemas.AnswerRequest(kind="answer", text=ANSWER), rename_first_prop],
        photo_ids=[photo_id], participant_ids=["dad"],
    )
    return s, run_id, events, before


def test_new_memory_event_order(first_run: tuple[Studio, str, list[Event], dict[str, Any]]) -> None:
    s, run_id, events, _ = first_run
    assert_typed(events)
    names = [n for n, _ in events]
    assert "error" not in names, events[-1]
    assert names[0] == "plan" and names[-1] == "done" and names.count("done") == 1
    assert_subsequence(names, [
        "plan", "subagent_start", "question", "subagent_end", "props_found",
        "compile_error", "frames", "scene_ready", "done",
    ])
    assert_paired(events)
    assert names.count("compile_error") == 1 and names.count("question") == 1 and names.count("props_found") == 1

    question = next(p for n, p in events if n == "question")
    assert question == schemas.QuestionEvent(text=FOLLOWUP)
    compile_error = next(p for n, p in events if n == "compile_error")
    assert isinstance(compile_error, schemas.CompileErrorEvent) and len(compile_error.errors) >= 1

    started = [p.name for n, p in events if n == "subagent_start"]  # type: ignore[attr-defined]
    for role in ("interviewer", "storyboarder", "prop_master", "set_designer", "director",
                 "narrator", "checker", "linker"):
        assert role in started, (role, started)
    # The pipeline order of ARCHITECTURE.md section 2.
    order = ["interviewer", "storyboarder", "prop_master", "director", "narrator", "checker", "linker"]
    assert [started.index(r) for r in order] == sorted(started.index(r) for r in order)
    assert started.index("set_designer") < started.index("director")
    # The follow-up is asked inside the interviewer; the props pause inside the prop master,
    # before the director stages anything.
    def start_of(role: str) -> int:
        return next(i for i, (n, p) in enumerate(events) if n == "subagent_start" and p.name == role)  # type: ignore[attr-defined]

    assert start_of("interviewer") < names.index("question") < start_of("storyboarder")
    assert start_of("prop_master") < names.index("props_found") < start_of("director")
    assert start_of("director") < names.index("compile_error") < start_of("narrator")
    ready, done = events[names.index("scene_ready")][1], events[-1][1]
    assert isinstance(ready, schemas.SceneReadyEvent) and isinstance(done, schemas.DoneEvent)
    assert ready.memory_id == done.memory_id and ready.version == 1
    assert s.manager.status(FAMILY, run_id) == "done"


def test_new_memory_is_saved_with_a_valid_timeline(first_run: tuple[Studio, str, list[Event], dict[str, Any]]) -> None:
    s, _, events, before = first_run
    done = events[-1][1]
    assert isinstance(done, schemas.DoneEvent)
    memory = s.memory(done.memory_id)
    assert memory.id not in before["memories"]
    assert memory.version == 1
    assert [t.author_id for t in memory.tellings] == ["mom"] and memory.tellings[0].text == MOM_TEXT
    assert before["photo"] in memory.tellings[0].photo_ids

    # The timeline validates against the contract and has length.
    assert memory.timeline is not None and memory.screenplay is not None
    timeline = schemas.Timeline.model_validate(memory.timeline.model_dump(mode="json", by_alias=True))
    assert timeline.duration > 0
    assert timeline.shots and timeline.tracks

    # Storybook text on every shot, in the screenplay and the compiled timeline.
    assert all(shot.page_text and shot.page_text.strip() for shot in memory.screenplay.shots)
    assert all(shot.page_text and shot.page_text.strip() for shot in timeline.shots)
    # A pull tab and click-to-explain notes come through.
    assert timeline.interactions and timeline.interactions[0].kind == "pull_tab"
    assert timeline.explanations and {e.target for e in timeline.explanations} >= {"puppet:mom", "puppet:dad"}

    # Exactly one narration track, mom's, with its audio stored in the family.
    assert set(timeline.narrations or {}) == {"mom"}
    track = (timeline.narrations or {})["mom"]
    assert track.audio and s.repo.get_media(FAMILY, track.audio) is not None
    assert timeline.default_narrator == "mom" and timeline.audio == track.audio
    assert memory.screenplay.narrations in (None, {})
    lines = memory.screenplay.shots[0].narration
    assert lines and all(ln.source and ln.source.startswith("telling:mom:") for ln in lines)


def test_new_memory_reaches_both_books_and_asks_dad(first_run: tuple[Studio, str, list[Event], dict[str, Any]]) -> None:
    s, _, events, _ = first_run
    memory_id = events[-1][1].memory_id  # type: ignore[attr-defined]
    for person in ("mom", "dad"):
        assert memory_id in [m.id for m in s.repo.find_memories(FAMILY, person_id=person)], person
    questions = [q for q in s.repo.list_questions(FAMILY) if q.memory_id == memory_id]
    assert [q.person_id for q in questions] == ["dad"]
    assert questions[0].status == "open" and questions[0].text.strip()


def test_new_memory_makes_placeholder_props_and_sets(first_run: tuple[Studio, str, list[Event], dict[str, Any]]) -> None:
    s, _, events, before = first_run
    memory = s.memory(events[-1][1].memory_id)  # type: ignore[attr-defined]
    assert memory.screenplay is not None

    new_props = [p for p in s.repo.list_props(FAMILY) if p.id not in before["props"]]
    new_sets = [d for d in s.repo.list_sets(FAMILY) if d.id not in before["sets"]]
    assert len(new_props) == 1 and len(new_sets) == 1
    prop, set_def = new_props[0], new_sets[0]
    assert prop.art and all(media is None for media in prop.art.values()) and prop.color
    assert set_def.layers and all(layer.art is None and layer.color for layer in set_def.layers)

    # The family's rename is what was saved, and the scene uses what the run made.
    assert prop.name == RENAMED
    assert memory.screenplay.props == [prop.id] and memory.prop_ids == [prop.id]
    assert {shot.set for shot in memory.screenplay.shots} == {set_def.id}
    found = next(p for n, p in events if n == "props_found")
    assert [c.id for c in found.props] == [prop.id] and not found.props[0].reused  # type: ignore[attr-defined]


def test_new_memory_looks_at_the_uploaded_photo(first_run: tuple[Studio, str, list[Event], dict[str, Any]]) -> None:
    s, _, _, _ = first_run
    vision = s.services.vision
    assert isinstance(vision, FakeVision)
    assert len(vision.calls) == 1


# --------------------------------------------------------------------------- (b) replay


def test_late_subscriber_replays_the_identical_events(first_run: tuple[Studio, str, list[Event], dict[str, Any]]) -> None:
    s, run_id, live, _ = first_run
    assert s.manager.status(FAMILY, run_id) == "done"
    replay = s.collect(run_id)
    assert replay == live
    assert s.collect(run_id) == live  # and again


# --------------------------------------------------------------------------- (c) second telling


def test_second_telling_adds_a_narration_track_to_the_same_memory(
    first_run: tuple[Studio, str, list[Event], dict[str, Any]],
) -> None:
    s, _, first_events, _ = first_run
    original = s.memory(first_events[-1][1].memory_id)  # type: ignore[attr-defined]
    assert original.timeline is not None and original.screenplay is not None
    memories_before = {m.id for m in s.repo.find_memories(FAMILY, limit=1000)}
    props_before = s.repo.list_props(FAMILY)
    sets_before = s.repo.list_sets(FAMILY)

    _, events = s.drive(DAD_USER, "dad", DAD_TEXT, memory_id=original.id, participant_ids=["mom"])
    assert_typed(events)
    names = [n for n, _ in events]
    assert names[0] == "plan" and names[-1] == "done" and "error" not in names
    assert "props_found" not in names and "question" not in names
    assert_paired(events)
    started = {p.name for n, p in events if n == "subagent_start"}  # type: ignore[attr-defined]
    assert not started & {"interviewer", "storyboarder", "prop_master", "set_designer"}

    done = events[-1][1]
    ready = next(p for n, p in events if n == "scene_ready")
    assert isinstance(done, schemas.DoneEvent) and done.memory_id == original.id
    assert isinstance(ready, schemas.SceneReadyEvent) and ready.version == original.version + 1

    # No new memory; the same one moved on a version.
    assert {m.id for m in s.repo.find_memories(FAMILY, limit=1000)} == memories_before
    memory = s.memory(original.id)
    assert memory.version == original.version + 1
    assert [t.author_id for t in memory.tellings] == ["mom", "dad"] and memory.tellings[1].text == DAD_TEXT

    # Two narration tracks over the unchanged action.
    assert memory.timeline is not None and memory.screenplay is not None
    timeline = schemas.Timeline.model_validate(memory.timeline.model_dump(mode="json", by_alias=True))
    assert set(timeline.narrations or {}) == {"mom", "dad"}
    mom_track, dad_track = (timeline.narrations or {})["mom"], (timeline.narrations or {})["dad"]
    assert mom_track == (original.timeline.narrations or {})["mom"]
    assert dad_track.audio and dad_track.audio != mom_track.audio
    assert s.repo.get_media(FAMILY, dad_track.audio) is not None
    assert timeline.default_narrator == "mom" and timeline.audio == original.timeline.audio
    assert non_narration_tracks(timeline) == non_narration_tracks(original.timeline)
    assert timeline.tracks == original.timeline.tracks
    assert memory.screenplay.shots == original.screenplay.shots
    dad_lines = (memory.screenplay.narrations or {})["dad"]
    assert dad_lines and all(ln.source and ln.source.startswith("telling:dad:") for lines in dad_lines.values()
                             for ln in lines)

    # Nothing new was made for the scene.
    assert s.repo.list_props(FAMILY) == props_before
    assert s.repo.list_sets(FAMILY) == sets_before


# --------------------------------------------------------------------------- (d) ownership


def test_only_the_teller_answers_and_only_while_waiting(studios: list[Studio]) -> None:
    s = new_studio(studios)
    run_id = s.start(MOM_USER, "mom", MOM_TEXT, participant_ids=["dad"])
    assert s.wait(run_id) == "waiting"
    assert s.names(run_id)[-1] == "question"

    with pytest.raises(runs.NotRunOwner):
        s.manager.answer_run(FAMILY, run_id, DAD_USER, schemas.AnswerRequest(kind="answer", text="Not mine."))
    assert s.manager.status(FAMILY, run_id) == "waiting"  # the wrong user changed nothing

    s.manager.answer_run(FAMILY, run_id, MOM_USER, schemas.AnswerRequest(kind="answer", text=ANSWER))
    assert s.wait(run_id) == "waiting"
    s.manager.answer_run(FAMILY, run_id, MOM_USER, rename_first_prop(s.manager.events(FAMILY, run_id)))
    assert s.wait(run_id) == "done"

    with pytest.raises(runs.RunNotWaiting):
        s.manager.answer_run(FAMILY, run_id, MOM_USER, schemas.AnswerRequest(kind="answer", text="Too late."))
    with pytest.raises(runs.NotRunOwner):  # ownership is checked first, whatever the state
        s.manager.answer_run(FAMILY, run_id, DAD_USER, schemas.AnswerRequest(kind="answer", text="Too late."))


# --------------------------------------------------------------------------- (e) compile cap


def test_compile_rounds_stop_at_the_cap(studios: list[Studio]) -> None:
    s = new_studio(studios, FakeStory(place="lake dock", prop="picnic basket", compile_failures=3))
    memories_before = s.repo.find_memories(FAMILY, limit=1000)
    _, events = s.drive(MOM_USER, "mom", MOM_TEXT, [rename_first_prop], participant_ids=["dad"])
    assert_typed(events)
    names = [n for n, _ in events]

    errors = [p for n, p in events if n == "compile_error"]
    assert 1 <= len(errors) <= MAX_COMPILE_ROUNDS
    assert all(isinstance(e, schemas.CompileErrorEvent) and len(e.errors) >= 1 for e in errors)
    assert [e.round for e in errors] == list(range(1, len(errors) + 1))  # type: ignore[attr-defined]

    # The studio prompt: after 3 failed rounds the director stops and reports, and nothing
    # is saved, so the run ends with exactly one error and no scene.
    assert names[-1] == "error" and names.count("error") == 1
    assert "done" not in names and "scene_ready" not in names
    assert_paired(events)
    director_runs = [p for n, p in events if n == "subagent_start" and p.name == "director"]  # type: ignore[attr-defined]
    assert len(director_runs) == 1
    assert s.repo.find_memories(FAMILY, limit=1000) == memories_before


# --------------------------------------------------------------------------- (f) the voice pass


FAKE_KEY = "sk_live_0123456789abcdefFAKEKEY"


class FlakyTTS:
    """FakeTTS that fails its first `fail_first` calls, as a TTS provider answering HTTP 503
    would (`fail_first=None`: every call fails, as with a missing or revoked key)."""

    def __init__(self, fail_first: int | None = 1) -> None:
        self.inner = FakeTTS()
        self.fail_first = fail_first
        self.failures = 0

    def synthesize(self, text: str, *, voice: str | None = None) -> Speech:
        if self.fail_first is None or self.failures < self.fail_first:
            self.failures += 1
            raise RuntimeError(f"TTS provider returned HTTP 503 (key {FAKE_KEY})")
        return self.inner.synthesize(text, voice=voice)


def _with_tts(s: Studio, tts: Any) -> None:
    s.manager.shutdown(wait=True)
    s.services = wiring.Services(**{**s.services.__dict__, "tts": tts})
    s.manager = wiring.make_manager(s.services)


def _voice_replies(s: Studio) -> list[str]:
    """Every synthesize_narration answer the studio model saw, in call order."""
    replies: dict[str, str] = {}
    for _, messages in s.services.model.calls:  # type: ignore[attr-defined]
        for m in messages:
            if isinstance(m, ToolMessage) and m.name == "synthesize_narration":
                replies[m.tool_call_id] = str(m.content)
    return list(replies.values())


def _drive_mom(s: Studio) -> list[Event]:
    _, events = s.drive(MOM_USER, "mom", MOM_TEXT, [schemas.AnswerRequest(kind="answer", text=ANSWER),
                                                    rename_first_prop], participant_ids=["dad"])
    names = [n for n, _ in events]
    assert names[-1] == "done" and "error" not in names, names
    return events


def test_a_tts_failure_is_retried_in_the_tool_and_the_run_still_finishes(studios: list[Studio]) -> None:
    s = new_studio(studios)
    tts = FlakyTTS()
    _with_tts(s, tts)
    events = _drive_mom(s)
    assert tts.failures == 1

    # The voice tool retried the failed call itself: one voice pass, a real voice, no warning.
    (reply,) = _voice_replies(s)
    assert reply.startswith("Narrated") and "warning" not in reply, reply

    memory = s.memory(events[-1][1].memory_id)  # type: ignore[attr-defined]
    assert memory.timeline is not None
    track = (memory.timeline.narrations or {})["mom"]
    assert track.audio and s.repo.get_media(FAMILY, track.audio) is not None


def test_a_dead_tts_still_saves_a_silently_narrated_memory(studios: list[Studio]) -> None:
    """Degrade, don't fail: with every TTS call failing the run ends in done, and the saved
    memory has mom's narration with word timings (silent audio, estimated timings)."""
    s = new_studio(studios)
    tts = FlakyTTS(fail_first=None)
    _with_tts(s, tts)
    events = _drive_mom(s)
    assert tts.failures == 2  # one call and one retry, then the silent voice

    (reply,) = _voice_replies(s)  # a warning is not a reason to voice again
    assert reply.startswith("Narrated"), reply
    assert "warning: the speech service failed (RuntimeError); narrated silently with estimated word timings" in reply
    assert FAKE_KEY not in reply and "HTTP 503" not in reply

    memory = s.memory(events[-1][1].memory_id)  # type: ignore[attr-defined]
    assert memory.timeline is not None and memory.screenplay is not None
    assert memory.timeline.default_narrator == "mom"
    track = (memory.timeline.narrations or {})["mom"]
    assert track.lines and all(line.words for line in track.lines)
    assert track.audio and s.repo.get_media(FAMILY, track.audio) is not None
    assert all(line.words for shot in memory.screenplay.shots for line in shot.narration if line.text.strip())
    for event in events:
        assert FAKE_KEY not in event[1].model_dump_json()


def test_a_media_store_failure_is_voiced_again(studios: list[Studio]) -> None:
    """The one voice-pass failure left is the media store: the narrator voices once more."""
    s = new_studio(studios)
    put_media = s.repo.put_media
    failed: list[str] = []

    def flaky_put(family_id: str, data: bytes, content_type: str, *, kind: str) -> str:
        if kind == "audio" and not failed:
            failed.append(kind)
            raise ConnectionError("media store unreachable")
        return put_media(family_id, data, content_type, kind=kind)

    s.repo.put_media = flaky_put  # type: ignore[method-assign]
    events = _drive_mom(s)
    assert failed == ["audio"]
    first, second = _voice_replies(s)
    assert first.startswith("Error: could not voice the narration (ConnectionError)"), first
    assert second.startswith("Narrated"), second
    memory = s.memory(events[-1][1].memory_id)  # type: ignore[attr-defined]
    track = (memory.timeline.narrations or {})["mom"]  # type: ignore[union-attr]
    assert track.audio and s.repo.get_media(FAMILY, track.audio) is not None


def test_the_saved_scene_has_its_cue_on_the_spoken_word(
    first_run: tuple[Studio, str, list[Event], dict[str, Any]],
) -> None:
    """The voice pass recompiled with the TTS word timings: the saved screenplay carries them,
    and recompiling it gives the saved action, with mom's cued action on her spoken word."""
    s, _, events, _ = first_run
    memory = s.memory(events[-1][1].memory_id)  # type: ignore[attr-defined]
    assert memory.screenplay is not None and memory.timeline is not None
    lines = memory.screenplay.shots[0].narration
    assert all(line.words for line in lines if line.text.strip())
    lookup = wiring.scene_for(s.repo, FAMILY)
    result = s.services.compiler(memory.screenplay, lookup(memory.screenplay))
    assert result.ok and result.timeline is not None
    assert result.timeline.tracks == memory.timeline.tracks
    # The cued line's last word (no cueWord: the last word) is where the cued action starts.
    (cued,) = [line for line in memory.timeline.narrations["mom"].lines if line.cue]  # type: ignore[index]
    assert cued.words
    shot = memory.screenplay.shots[0]
    action = next(a for a in shot.actions if a.id == cued.cue)
    walk = [t for t in memory.timeline.tracks if t.target == f"puppet:{action.who}" and t.channel == "root.position"]
    assert walk and any(abs(k[0] - cued.words[-1].t) < 0.01 for k in walk[0].keys), (cued.words[-1].t, walk[0].keys[:4])
