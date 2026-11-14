"""Narration: FakeTTS timing rule, per-telling narration tracks, and the synthesize_narration tool."""

from __future__ import annotations

import io
import json
import wave
from pathlib import Path

import pytest
from deepagents.backends.utils import create_file_data, file_data_to_string
from langchain_core.messages import AIMessage, ToolMessage
from deepagents.middleware.filesystem import FilesystemState
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode

from agent import wiring
from agent.repo import InMemoryRepo, load_sample_family
from agent.run_info import RunInfo
from agent.tools.compile import load_compiler, make_compile_tool
from agent.tools.family import save_scene
from agent.tools.narration import (
    LINE_GAP,
    FakeTTS,
    Speech,
    apply_narration,
    build_narration_track,
    lines_for_author,
    make_narration_tool,
    voice_for,
)
from schemas import CharacterSheet, Person, SubmitMemoryFields
from screenplay.schema import (
    CompileError,
    CompileResult,
    NarrationLine,
    NarrationTrack,
    Screenplay,
    Timeline,
    WordTiming,
)

SAMPLES = Path(__file__).resolve().parents[3] / "web" / "public" / "samples"


def load(name: str) -> dict:
    return json.loads((SAMPLES / name).read_text())


@pytest.fixture
def screenplay() -> Screenplay:
    return Screenplay.model_validate(load("hymn_books.screenplay.json"))


@pytest.fixture
def bare_timeline() -> Timeline:
    """The sample timeline with its hand-written narration removed, as the compiler hands it over."""
    data = load("hymn_books.timeline.json")
    data.update(narrations=None, defaultNarrator=None, audio=None, captions=[])
    return Timeline.model_validate(data)


class MediaStore:
    def __init__(self) -> None:
        self.items: dict[str, tuple[bytes, str]] = {}

    def put(self, data: bytes, content_type: str) -> str:
        media_id = f"media_{len(self.items) + 1}"
        self.items[media_id] = (data, content_type)
        return media_id


def wav_info(data: bytes) -> tuple[int, int, int, int]:
    """(channels, sample width, rate, frames)"""
    with wave.open(io.BytesIO(data), "rb") as w:
        return w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()


def approx(x: float) -> pytest.approx:
    return pytest.approx(x, abs=1e-9)


# --------------------------------------------------------------------------- FakeTTS


def test_fake_tts_follows_the_timing_rule_exactly():
    speech = FakeTTS().synthesize("Hello, world. Again; and so... on! Really? Yes…")
    words = [(w.word, w.t) for w in speech.words]
    expected = [
        ("Hello", 0.0),  # then , -> +0.20
        ("world", 0.50),  # then . -> +0.35
        ("Again", 1.15),  # then ; -> +0.20
        ("and", 1.65),
        ("so", 1.95),  # then ... -> +0.35
        ("on", 2.60),  # then ! -> +0.35
        ("Really", 3.25),  # then ? -> +0.35
        ("Yes", 3.90),  # then … -> +0.35
    ]
    assert [w for w, _ in words] == [w for w, _ in expected]
    for (_, got), (_, want) in zip(words, expected):
        assert got == approx(want)
    assert speech.duration == approx(3.90 + 0.30 + 0.35)
    assert speech.sample_rate == 16_000


def test_fake_tts_wav_header_matches_duration():
    tts = FakeTTS()
    for text in ["I saw her at the top of the church steps…", "Hi", "One, two; three.", ""]:
        speech = tts.synthesize(text)
        channels, width, rate, frames = wav_info(speech.audio)
        assert (channels, width, rate) == (1, 2, 16_000)
        assert frames / rate == speech.duration
    assert tts.synthesize("I saw her at the top of the church steps…").duration == approx(10 * 0.30 + 0.35)
    assert tts.synthesize("").words == []


def test_fake_tts_strips_punctuation_but_keeps_apostrophes():
    speech = FakeTTS().synthesize("…and she wouldn't — not once.")
    assert [w.word for w in speech.words] == ["and", "she", "wouldn't", "not", "once"]
    # The bare dash is not a word and adds no time.
    assert speech.duration == approx(5 * 0.30 + 0.35)


def test_fake_tts_is_deterministic():
    a = FakeTTS().synthesize("Mom bowed her head, and that was that.", voice="voice_a")
    b = FakeTTS().synthesize("Mom bowed her head, and that was that.", voice="voice_a")
    assert a == b


def test_voice_for_is_a_stable_placeholder():
    assert voice_for(None) is None
    dad = Person(id="dad", family_id="f1", name="Dad", character=CharacterSheet(color="#335577"))
    assert voice_for(dad) == voice_for(dad.model_copy())
    assert isinstance(voice_for(dad), str)


# --------------------------------------------------------------------------- build_narration_track


def test_build_track_on_hymn_books_with_dads_lines(screenplay, bare_timeline):
    media = MediaStore()
    tts = FakeTTS()
    lines = lines_for_author(screenplay, "dad")
    assert [line.text for line in lines["s1"]] == [line.text for line in screenplay.shots[0].narration]

    track, audio_end = build_narration_track(
        bare_timeline, lines, author_id="dad", tts=tts, put_media=media.put, voice="voice_x"
    )

    shot = bare_timeline.shots[0]
    assert shot.action_start == 1.2
    assert track.author_id == "dad"
    assert len(track.lines) == 3
    # Line 1 starts at actionStart + t.
    assert track.lines[0].t == approx(shot.action_start + 0)
    # Line 1 lasts 10 words + ellipsis pause = 3.35 s -> ends 4.55. Line 2 wants 1.2 + 2.5 = 3.7,
    # which overlaps, so it is pushed to 4.55 + 0.15.
    assert track.lines[1].t == approx(4.55 + LINE_GAP)
    assert track.lines[1].t > shot.action_start + screenplay.shots[0].narration[1].t
    # Line 2: 8 words + final period = 2.75 s -> ends 7.45. Line 3 wants 7.55 < 7.60 -> pushed.
    assert track.lines[2].t == approx(7.45 + LINE_GAP)
    # Line 3: 11 words, one comma, final period = 3.85 s.
    assert audio_end == approx(7.60 + 3.85)

    # Words are in timeline time, start on their line, and only ever increase.
    all_words = [w for line in track.lines for w in line.words]
    for line in track.lines:
        assert line.words[0].t == line.t
    times = [w.t for w in all_words]
    assert times == sorted(times) and len(set(times)) == len(times)
    assert all_words[0].word == "I" and all_words[-1].word == "me"
    assert track.lines[1].words[3].word == "dropped"
    assert track.lines[1].words[3].t == approx(4.70 + 3 * 0.30)

    # Lines are copies: cue, cueWord and source survive; the screenplay is untouched.
    assert track.lines[1].cue == "drop" and track.lines[1].cue_word == "dropped"
    assert track.lines[2].source == "telling:dad:s4"
    assert screenplay.shots[0].narration[1].t == 2.5 and screenplay.shots[0].narration[1].words is None

    assert [(c.t, c.text) for c in track.captions] == [(line.t, line.text) for line in track.lines]
    assert all(voice == "voice_x" for _, voice in tts.calls)

    # One WAV from timeline 0 to the end of the last word.
    assert track.audio == "media_1"
    data, content_type = media.items["media_1"]
    assert content_type == "audio/wav"
    channels, width, rate, frames = wav_info(data)
    assert (channels, width, rate) == (1, 2, 16_000)
    assert frames / rate == approx(audio_end)


def test_build_track_keeps_screenplay_times_when_nothing_overlaps(bare_timeline):
    lines = {
        "s1": [
            NarrationLine(t=4.0, text="Then the books fell."),
            NarrationLine(t=0.0, text="Sunday."),
        ]
    }
    track, audio_end = build_narration_track(
        bare_timeline, lines, author_id="dad", tts=FakeTTS(), put_media=MediaStore().put
    )
    # Sorted by t within the shot, each at actionStart + t.
    assert [line.t for line in track.lines] == [approx(1.2), approx(5.2)]
    assert [w.t for w in track.lines[1].words] == [approx(5.2), approx(5.5), approx(5.8), approx(6.1)]
    assert audio_end == approx(5.2 + 4 * 0.30 + 0.35)


def test_build_track_rejects_lines_for_unknown_shots(bare_timeline):
    with pytest.raises(ValueError, match="s9"):
        build_narration_track(
            bare_timeline,
            {"s9": [NarrationLine(t=0, text="Nowhere.")]},
            author_id="dad",
            tts=FakeTTS(),
            put_media=MediaStore().put,
        )


def test_build_track_walks_shots_in_timeline_order(bare_timeline):
    data = bare_timeline.model_dump(by_alias=True)
    s1 = data["shots"][0]
    data["shots"] = [
        {**s1, "id": "a", "start": 0, "actionStart": 1.0, "end": 5.0},
        {**s1, "id": "b", "start": 5.0, "actionStart": 6.0, "end": 12.0},
    ]
    timeline = Timeline.model_validate(data)

    lines = {"b": [NarrationLine(t=0.5, text="Second.")], "a": [NarrationLine(t=0, text="First of all, hello.")]}
    track, _ = build_narration_track(timeline, lines, author_id="dad", tts=FakeTTS(), put_media=MediaStore().put)
    assert [line.text for line in track.lines] == ["First of all, hello.", "Second."]
    assert [line.t for line in track.lines] == [approx(1.0), approx(6.5)]


def test_build_track_rejects_mixed_sample_rates(bare_timeline):
    class MixedRates:
        """A TTS that is not the protocol's friend: every other clip at a different rate."""

        def __init__(self) -> None:
            self.n = 0

        def synthesize(self, text: str, *, voice: str | None = None) -> Speech:
            self.n += 1
            return FakeTTS(sample_rate=16_000 if self.n % 2 else 22_050).synthesize(text)

    lines = {"s1": [NarrationLine(t=0, text="One."), NarrationLine(t=3, text="Two.")]}
    with pytest.raises(ValueError, match="sample rates"):
        build_narration_track(bare_timeline, lines, author_id="dad", tts=MixedRates(), put_media=MediaStore().put)


# --------------------------------------------------------------------------- apply_narration


def _track(screenplay, timeline, author, media):
    return build_narration_track(
        timeline, lines_for_author(screenplay, author), author_id=author, tts=FakeTTS(), put_media=media.put
    )


def test_apply_narration_default_then_second_telling(screenplay, bare_timeline):
    media = MediaStore()
    dad_track, dad_end = _track(screenplay, bare_timeline, "dad", media)
    with_dad = apply_narration(bare_timeline, dad_track, dad_end, make_default=True)

    assert with_dad.default_narrator == "dad"
    assert with_dad.audio == dad_track.audio == "media_1"
    assert with_dad.captions == dad_track.captions
    assert with_dad.narrations == {"dad": dad_track}
    assert bare_timeline.narrations is None  # input untouched

    mom_lines = lines_for_author(screenplay, "mom")
    assert mom_lines["s1"][0].text == "I was carrying far too many hymn books."
    mom_track, mom_end = _track(screenplay, with_dad, "mom", media)
    with_both = apply_narration(with_dad, mom_track, mom_end, make_default=False)

    assert with_both.default_narrator == "dad"
    assert with_both.audio == "media_1"
    assert with_both.captions == dad_track.captions
    assert set(with_both.narrations) == {"dad", "mom"}
    assert with_both.narrations["mom"].audio == "media_2"
    assert with_both.narrations["dad"] == dad_track
    assert with_both.tracks == bare_timeline.tracks
    # Each narration outlasts the action before it, so the closing shot is held until the
    # longest one ends; nothing else about the shots changes.
    assert bare_timeline.duration < with_dad.duration < with_both.duration
    assert with_dad.shots[-1].end == with_dad.duration
    assert with_both.shots[-1].end == with_both.duration
    assert [s.model_copy(update={"end": 0}) for s in with_both.shots] == [
        s.model_copy(update={"end": 0}) for s in bare_timeline.shots
    ]

    # Round-trips through JSON as a valid Timeline.
    Timeline.model_validate(json.loads(json.dumps(with_both.model_dump(mode="json", by_alias=True))))


def test_apply_narration_first_track_becomes_default_even_if_not_asked(screenplay, bare_timeline):
    track, end = _track(screenplay, bare_timeline, "mom", MediaStore())
    result = apply_narration(bare_timeline, track, end, make_default=False)
    assert result.default_narrator == "mom"
    assert result.audio == track.audio and result.captions == track.captions


def test_apply_narration_make_default_switches_teller(screenplay, bare_timeline):
    media = MediaStore()
    dad_track, dad_end = _track(screenplay, bare_timeline, "dad", media)
    mom_track, mom_end = _track(screenplay, bare_timeline, "mom", media)
    t = apply_narration(bare_timeline, dad_track, dad_end, make_default=True)
    t = apply_narration(t, mom_track, mom_end, make_default=True)
    assert t.default_narrator == "mom" and t.audio == mom_track.audio
    assert set(t.narrations) == {"dad", "mom"}


def test_duration_extends_when_narration_runs_past_the_action(screenplay, bare_timeline):
    track, end = _track(screenplay, bare_timeline, "dad", MediaStore())
    assert end > bare_timeline.duration
    result = apply_narration(bare_timeline, track, end, make_default=True)
    assert result.duration == approx(end + 0.5)


def test_duration_never_shrinks(bare_timeline):
    lines = {"s1": [NarrationLine(t=0, text="Short.")]}
    track, end = build_narration_track(bare_timeline, lines, author_id="dad", tts=FakeTTS(), put_media=MediaStore().put)
    assert end + 0.5 < bare_timeline.duration
    assert apply_narration(bare_timeline, track, end, make_default=True).duration == bare_timeline.duration


# --------------------------------------------------------------------------- synthesize_narration tool


def _call(tool, files: dict) -> dict:
    """Run the tool the way the agent does: a ToolNode in a graph over deepagents' file state.

    Returns the final state: `messages` (the tool's reply last) and the merged `files`.
    """
    graph = StateGraph(FilesystemState)
    graph.add_node("tools", ToolNode([tool]))
    graph.add_edge(START, "tools")
    graph.add_edge("tools", END)
    msg = AIMessage(content="", tool_calls=[{"name": "synthesize_narration", "args": {}, "id": "call_1"}])
    return graph.compile().invoke({"messages": [msg], "files": files})


def _files(screenplay: dict | None, timeline: dict | None) -> dict:
    files = {}
    if screenplay is not None:
        files["/screenplay.json"] = create_file_data(json.dumps(screenplay))
    if timeline is not None:
        files["/timeline.json"] = create_file_data(json.dumps(timeline))
    return files


def _bare_timeline_json() -> dict:
    data = load("hymn_books.timeline.json")
    data.update(narrations=None, defaultNarrator=None, audio=None, captions=[])
    return data


def _timeline_in(state: dict) -> Timeline:
    return Timeline.model_validate_json(file_data_to_string(state["files"]["/timeline.json"]))


def _tool_text(state: dict) -> str:
    message = state["messages"][-1]
    assert isinstance(message, ToolMessage) and message.tool_call_id == "call_1"
    return message.content


def test_tool_rewrites_timeline_json():
    media = MediaStore()
    tool = make_narration_tool(FakeTTS(), media.put, author_id="dad", voice=None, second_telling=False)
    assert tool.name == "synthesize_narration"
    assert tool.args == {}  # nothing for the model to fill in; family and author are bound

    files = _files(load("hymn_books.screenplay.json"), _bare_timeline_json())
    state = _call(tool, files)
    timeline = _timeline_in(state)
    assert timeline.default_narrator == "dad"
    assert timeline.audio == "media_1"
    assert timeline.captions[0].text == "I saw her at the top of the church steps…"
    assert timeline.duration == approx(7.60 + 3.85 + 0.5)
    assert timeline.tracks == Timeline.model_validate(_bare_timeline_json()).tracks
    # The screenplay draft is left as it was.
    assert state["files"]["/screenplay.json"] == files["/screenplay.json"]

    text = _tool_text(state)
    assert "3 line(s)" in text and "11.45 s" in text


def test_tool_second_telling_adds_track_without_changing_default():
    media = MediaStore()
    files = _files(load("hymn_books.screenplay.json"), _bare_timeline_json())
    dad_tool = make_narration_tool(FakeTTS(), media.put, author_id="dad", voice=None, second_telling=False)
    files = _call(dad_tool, files)["files"]

    mom_tool = make_narration_tool(FakeTTS(), media.put, author_id="mom", voice="voice_b", second_telling=True)
    timeline = _timeline_in(_call(mom_tool, files))
    assert timeline.default_narrator == "dad" and timeline.audio == "media_1"
    assert set(timeline.narrations) == {"dad", "mom"}
    assert timeline.narrations["mom"].audio == "media_2"
    assert timeline.narrations["mom"].lines[0].text == "I was carrying far too many hymn books."


def test_tool_reports_missing_timeline():
    tool = make_narration_tool(FakeTTS(), MediaStore().put, author_id="dad", voice=None, second_telling=False)
    files = _files(load("hymn_books.screenplay.json"), None)
    state = _call(tool, files)
    assert "/timeline.json not found" in _tool_text(state)
    assert "/timeline.json" not in state["files"]


def test_tool_reports_missing_screenplay_and_unknown_shot():
    tool = make_narration_tool(FakeTTS(), MediaStore().put, author_id="dad", voice=None, second_telling=False)
    assert "/screenplay.json not found" in _tool_text(_call(tool, _files(None, _bare_timeline_json())))

    sp = load("hymn_books.screenplay.json")
    sp["narrations"] = {"dad": {"s7": [{"t": 0, "text": "Somewhere else."}]}}
    text = _tool_text(_call(tool, _files(sp, _bare_timeline_json())))
    assert "Error" in text and "s7" in text


def test_tool_reports_invalid_json():
    tool = make_narration_tool(FakeTTS(), MediaStore().put, author_id="dad", voice=None, second_telling=False)
    files = _files(load("hymn_books.screenplay.json"), None)
    files["/timeline.json"] = create_file_data("{not json")
    assert "not valid JSON" in _tool_text(_call(tool, files))


def test_word_timing_model_is_reused():
    # The track's words are the contract's WordTiming, so they serialize as {t, word}.
    speech = FakeTTS().synthesize("Hi there.")
    assert all(isinstance(w, WordTiming) for w in speech.words)
    assert speech.words[1].model_dump(by_alias=True) == {"t": 0.3, "word": "there"}


# --------------------------------------------------------------------------- the voice pass on real timings

FAMILY = "fam_voice"


def _tool_node_graph(*tools):
    graph = StateGraph(FilesystemState)
    graph.add_node("tools", ToolNode(list(tools)))
    graph.add_edge(START, "tools")
    graph.add_edge("tools", END)
    return graph.compile()


def _run_tool(tool, files: dict) -> dict:
    """One call of `tool` (no args) over `files`, as the agent makes it; returns the final state."""
    msg = AIMessage(content="", tool_calls=[{"name": tool.name, "args": {}, "id": "call_1"}])
    return _tool_node_graph(tool).invoke({"messages": [msg], "files": files})


@pytest.fixture
def family_repo() -> InMemoryRepo:
    repo = InMemoryRepo()
    load_sample_family(repo, FAMILY)
    return repo


@pytest.fixture
def lookup(family_repo):
    return wiring.scene_for(family_repo, FAMILY)


@pytest.fixture
def compiler():
    return load_compiler()


def _compiled(screenplay: Screenplay, compiler, lookup) -> Timeline:
    result = compiler(screenplay, lookup(screenplay))
    assert result.ok and result.timeline is not None, [e.message for e in result.errors]
    return result.timeline


def _draft_files(screenplay: Screenplay, timeline: Timeline) -> dict:
    return {
        "/screenplay.json": create_file_data(screenplay.model_dump_json(by_alias=True)),
        "/timeline.json": create_file_data(timeline.model_dump_json(by_alias=True)),
    }


def _screenplay_in(state: dict) -> Screenplay:
    return Screenplay.model_validate_json(file_data_to_string(state["files"]["/screenplay.json"]))


def _ground_time(timeline: Timeline, prop: str = "hymn_books") -> float:
    """When the prop first goes to the ground: the first key of the drop."""
    (parent,) = [t for t in timeline.tracks if t.target == f"prop:{prop}" and t.channel == "parent"]
    return next(t for t, value in parent.keys if value == "ground")


def _word_time(track, word: str) -> float:
    return next(w.t for line in track.lines for w in line.words if w.word == word)


def _all_words(timeline: Timeline):
    return [w for track in (timeline.narrations or {}).values() for line in track.lines for w in line.words or []]


def test_the_drop_lands_on_the_spoken_word_dropped(screenplay, compiler, lookup):
    """Finding 9: the voice pass recompiles with the TTS word timings, so the cue holds."""
    timeline = _compiled(screenplay, compiler, lookup)
    media = MediaStore()
    tool = make_narration_tool(
        FakeTTS(), media.put, author_id="dad", voice=None, second_telling=False, compiler=compiler, scene_for=lookup
    )
    state = _run_tool(tool, _draft_files(screenplay, timeline))
    text = _tool_text(state)
    assert text.startswith("Narrated") and "warning" not in text, text

    voiced = _timeline_in(state)
    dad = voiced.narrations["dad"]
    assert dad.audio == "media_1" and voiced.default_narrator == "dad"
    assert _ground_time(voiced) == pytest.approx(_word_time(dad, "dropped"), abs=0.01)
    # The unvoiced compile had the drop elsewhere: the spoken words really moved it.
    assert _ground_time(timeline) != pytest.approx(_word_time(dad, "dropped"), abs=0.01)
    # The same holds for the hand-back on "handed".
    (hand_line,) = [line for line in dad.lines if line.cue == "hand_back"]
    assert hand_line.words[1].word == "handed"

    # /screenplay.json now carries dad's word timings, seconds from each line's start.
    written = _screenplay_in(state)
    for line in written.shots[0].narration:
        assert line.words and line.words[0].t == 0.0
        assert [w.word for w in line.words] == [w.word for w in FakeTTS().synthesize(line.text).words]
    # And recompiling it gives exactly the action in /timeline.json.
    assert _compiled(written, compiler, lookup).tracks == voiced.tracks
    assert written.narrations == screenplay.narrations  # mom's telling untouched


def test_a_first_telling_that_outlasts_the_action_holds_the_last_shot(screenplay, compiler, lookup):
    """Finding 10: the closing shot ends with the narration, and no word is said after it."""
    timeline = _compiled(screenplay, compiler, lookup)
    tool = make_narration_tool(
        FakeTTS(), MediaStore().put, author_id="dad", voice=None, second_telling=False,
        compiler=compiler, scene_for=lookup,
    )
    voiced = _timeline_in(_run_tool(tool, _draft_files(screenplay, timeline)))
    assert voiced.duration > timeline.duration
    assert voiced.shots[-1].end == voiced.duration
    assert max(w.t for w in _all_words(voiced)) < voiced.shots[-1].end


def test_apply_narration_holds_the_last_shot_only_when_it_ended_the_scene(bare_timeline):
    track = NarrationTrack(author_id="dad", audio=None, captions=[], lines=[])
    longer = apply_narration(bare_timeline, track, bare_timeline.duration + 2.0, make_default=True)
    assert longer.duration == approx(bare_timeline.duration + 2.5)
    assert longer.shots[-1].end == longer.duration

    # A closing shot that ended before the scene did is left alone.
    data = bare_timeline.model_dump(by_alias=True)
    data["shots"][-1]["end"] = bare_timeline.duration - 1.0
    early = Timeline.model_validate(data)
    assert apply_narration(early, track, early.duration + 2.0, make_default=True).shots[-1].end == early.shots[-1].end


@pytest.mark.parametrize("broken", ["scene_for", "compiler", "compile_error"])
def test_a_failed_recompile_still_voices_with_a_warning(screenplay, compiler, lookup, broken):
    timeline = _compiled(screenplay, compiler, lookup)

    def bad_lookup(_sp):
        raise RuntimeError("scene lookup is down")

    def bad_compiler(_sp, _scene):
        raise RuntimeError("compiler crashed")

    def failing_compiler(_sp, _scene):
        return CompileResult(ok=False, errors=[CompileError(code="x", message="shot s1: something is wrong")])

    kwargs = {
        "scene_for": {"scene_for": bad_lookup, "compiler": compiler},
        "compiler": {"scene_for": lookup, "compiler": bad_compiler},
        "compile_error": {"scene_for": lookup, "compiler": failing_compiler},
    }[broken]
    files = _draft_files(screenplay, timeline)
    tool = make_narration_tool(FakeTTS(), MediaStore().put, author_id="dad", voice=None, second_telling=False, **kwargs)
    state = _run_tool(tool, files)
    text = _tool_text(state)
    assert text.startswith("Narrated") and "\nwarning: " in text, text
    voiced = _timeline_in(state)
    assert voiced.narrations["dad"].audio and voiced.tracks == timeline.tracks  # the action in the files
    # Estimated placement, as without a compiler; the screenplay is not rewritten.
    assert voiced.narrations["dad"].lines[0].t == approx(timeline.shots[0].action_start)
    assert state["files"]["/screenplay.json"] == files["/screenplay.json"]


# --------------------------------------------------------------------------- second tellings


def test_lines_for_a_second_telling_never_borrow_the_default_telling(screenplay):
    without = screenplay.model_copy(update={"narrations": None})
    assert lines_for_author(without, "mom", second_telling=True) == {}
    assert lines_for_author(without, "mom")["s1"] == screenplay.shots[0].narration  # a first telling may
    assert lines_for_author(screenplay, "mom", second_telling=True)["s1"][0].source == "telling:mom:s1"


@pytest.mark.parametrize("narrations", [None, {"dad": {}}, {"mom": {"s1": [{"t": 0, "text": "   "}]}}])
def test_a_second_telling_without_its_own_lines_is_an_error(narrations, screenplay, compiler, lookup):
    """Finding 1: mom's second telling never voices dad's default lines as hers."""
    sp = load("hymn_books.screenplay.json")
    sp["narrations"] = narrations
    timeline = _compiled(screenplay.model_copy(update={"narrations": None}), compiler, lookup)
    files = _draft_files(Screenplay.model_validate(sp), timeline)
    for with_compiler in (False, True):
        extra = {"compiler": compiler, "scene_for": lookup} if with_compiler else {}
        tts = FakeTTS()
        tool = make_narration_tool(tts, MediaStore().put, author_id="mom", voice=None, second_telling=True, **extra)
        state = _run_tool(tool, files)
        text = _tool_text(state)
        assert text.startswith("Error"), text
        assert "narrations[mom]" in text
        assert state["files"]["/timeline.json"] == files["/timeline.json"]  # nothing written
        assert state["files"]["/screenplay.json"] == files["/screenplay.json"]
        assert tts.calls == []


def test_mom_never_speaks_the_default_telling(screenplay):
    files = _files(load("hymn_books.screenplay.json"), _bare_timeline_json())
    dad = make_narration_tool(FakeTTS(), MediaStore().put, author_id="dad", voice=None, second_telling=False)
    files = _call(dad, files)["files"]
    mom = make_narration_tool(FakeTTS(), MediaStore().put, author_id="mom", voice=None, second_telling=True)
    timeline = _timeline_in(_call(mom, files))
    default_texts = {line.text for shot in screenplay.shots for line in shot.narration}
    assert {c.text for c in timeline.narrations["mom"].captions}.isdisjoint(default_texts)
    assert [c.text for c in timeline.narrations["mom"].captions] == [
        line.text for line in screenplay.narrations["mom"]["s1"]
    ]


# Mom's lines are written as a narrator writes them: each starts after the line before has been
# said at the compiler's estimate (WORD_SECONDS per word), so her write pass compiles; they are
# long enough that, voiced, they push shot s2 later in the draft compile.
MOM_S1 = [
    {"t": 0, "text": "I was carrying far too many hymn books that Sunday, the whole choir's worth of them.",
     "source": "telling:mom:s1"},
    {"t": 5.0, "text": "And of course they went everywhere, right down the steps, right in front of him, "
     "every single one of them, and I could not stop it.", "source": "telling:mom:s2"},
    {"t": 12.0, "text": "He handed them back so carefully, one by one, without a word, and I still could not "
     "look up at him at all.", "source": "telling:mom:s3"},
]
MOM_S2 = [
    {"t": 0.5, "text": "Then it all happened again, the very same way, and I wanted the steps to swallow me whole.",
     "source": "telling:mom:s4"},
    {"t": 5.5, "text": "He only smiled.", "source": "telling:mom:s5"},
]


def _two_shot(screenplay: Screenplay) -> Screenplay:
    """The hymn_books scene played twice: shot s2 is a copy of s1."""
    data = screenplay.model_dump(mode="json", by_alias=True)
    s2 = json.loads(json.dumps(data["shots"][0]))
    s2["id"] = "s2"
    data["shots"].append(s2)
    data["narrations"] = None
    return Screenplay.model_validate(data)


def _run(author: str, memory_id: str | None = None) -> RunInfo:
    fields = SubmitMemoryFields(text="The hymn books went everywhere.", year=1988, memory_id=memory_id)
    return RunInfo(run_id=f"run_{author}", family_id=FAMILY, user_id=f"user_{author}", author_id=author, fields=fields)


def _steps(files: dict, *tools) -> dict:
    """Call each tool in turn over the draft files; returns the last state (files merged).

    Every step must succeed: compile_screenplay answers "ok: ..." (its failure text does not
    start with "Error", and a failed compile blanks /timeline.json), and the voice pass answers
    "Narrated ...".
    """
    state: dict = {"files": files}
    for tool in tools:
        state = _run_tool(tool, state["files"])
        text = _tool_text(state)
        assert text.startswith("ok:" if tool.name == "compile_screenplay" else "Narrated"), text
    return state


def test_a_second_telling_plays_over_the_saved_action(screenplay, family_repo, compiler, lookup):
    """Findings 2 and 8: mom's longer telling never moves dad's saved shots, and her s2 lines
    keep their place relative to s2's saved actionStart."""
    two = _two_shot(screenplay)
    media = MediaStore()

    # Dad's first telling: compile, voice (recompiling with his word timings), save.
    dad_run = _run("dad")
    state = _steps(
        {"/screenplay.json": create_file_data(two.model_dump_json(by_alias=True))},
        make_compile_tool(compiler, lookup),
        make_narration_tool(FakeTTS(), media.put, author_id="dad", voice=None, second_telling=False,
                            compiler=compiler, scene_for=lookup),
    )
    first = save_scene(family_repo, dad_run, title="Hymn books", participant_ids=["dad", "mom"],
                       screenplay=_screenplay_in(state), timeline=_timeline_in(state))
    saved = first.timeline
    assert saved is not None and [s.id for s in saved.shots] == ["s1", "s2"]

    # Mom's second telling over the saved scene: longer lines in both shots.
    draft = first.screenplay.model_dump(mode="json", by_alias=True)
    draft["narrations"] = {"mom": {"s1": MOM_S1, "s2": MOM_S2}}
    mom_run = _run("mom", memory_id=first.id)
    files = {
        "/screenplay.json": create_file_data(json.dumps(draft)),
        "/timeline.json": create_file_data(saved.model_dump_json(by_alias=True)),
    }
    state = _steps(
        files,
        make_compile_tool(compiler, lookup),
        make_narration_tool(FakeTTS(), media.put, author_id="mom", voice=None, second_telling=True,
                            compiler=compiler, scene_for=lookup,
                            base_timeline=lambda: family_repo.get_memory(FAMILY, first.id).timeline),
    )
    text = _tool_text(state)
    assert "warning" not in text, text
    voiced_sp = _screenplay_in(state)
    assert all(line.words for lines in voiced_sp.narrations["mom"].values() for line in lines)
    # Her lines really are longer: in the draft compile, s2 starts later than in the saved scene.
    recompiled = _compiled(voiced_sp, compiler, lookup)
    draft_s2 = next(s for s in recompiled.shots if s.id == "s2")
    saved_s2 = next(s for s in saved.shots if s.id == "s2")
    assert draft_s2.action_start > saved_s2.action_start + 0.5

    memory = save_scene(family_repo, mom_run, title="Hymn books", participant_ids=["mom"],
                        screenplay=voiced_sp, timeline=_timeline_in(state))
    assert memory.id == first.id and [t.author_id for t in memory.tellings] == ["dad", "mom"]
    timeline = memory.timeline
    assert [(s.id, s.action_start) for s in timeline.shots] == [(s.id, s.action_start) for s in saved.shots]
    assert timeline.tracks == saved.tracks
    assert timeline.default_narrator == "dad" and timeline.narrations["dad"] == saved.narrations["dad"]

    # Mom's first s2 line: at saved s2.actionStart + its offset in the draft compile, unless
    # her previous line is still being said.
    mom = timeline.narrations["mom"]
    draft_mom = recompiled.narrations["mom"].lines
    n_s1 = len(MOM_S1)
    offset = draft_mom[n_s1].t - draft_s2.action_start
    prev = mom.lines[n_s1 - 1]
    prev_end = prev.t + FakeTTS().synthesize(prev.text).duration
    assert mom.lines[n_s1].text == MOM_S2[0]["text"]
    assert mom.lines[n_s1].t == pytest.approx(max(saved_s2.action_start + offset, prev_end + LINE_GAP), abs=0.01)
    # Each line keeps its words in step with its start.
    for line in mom.lines:
        assert line.words[0].t == pytest.approx(line.t, abs=1e-6)

    # No word is ever said after the last shot ends.
    assert max(w.t for w in _all_words(timeline)) <= timeline.shots[-1].end
    Timeline.model_validate(timeline.model_dump(mode="json", by_alias=True))


def test_a_second_telling_needs_the_saved_timeline(screenplay):
    files = _files(load("hymn_books.screenplay.json"), _bare_timeline_json())
    tool = make_narration_tool(FakeTTS(), MediaStore().put, author_id="mom", voice=None, second_telling=True,
                               base_timeline=lambda: None)
    state = _call(tool, files)
    assert _tool_text(state).startswith("Error")
    assert state["files"]["/timeline.json"] == files["/timeline.json"]


def test_a_second_telling_without_a_compiler_offsets_from_its_written_t(screenplay, bare_timeline):
    data = bare_timeline.model_dump(by_alias=True)
    data["shots"][0]["actionStart"] = 2.0
    base = Timeline.model_validate(data)
    tool = make_narration_tool(FakeTTS(), MediaStore().put, author_id="mom", voice=None, second_telling=True,
                               base_timeline=lambda: base)
    timeline = _timeline_in(_call(tool, _files(load("hymn_books.screenplay.json"), _bare_timeline_json())))
    assert timeline.shots[0].action_start == 2.0
    assert timeline.narrations["mom"].lines[0].t == approx(2.0 + screenplay.narrations["mom"]["s1"][0].t)


# --------------------------------------------------------------------------- outside failures


class BrokenTTS:
    def synthesize(self, text: str, *, voice: str | None = None) -> Speech:
        raise RuntimeError("TTS provider returned HTTP 503")


def test_a_failing_tts_is_an_error_string_not_an_exception():
    """Finding 3: an outside failure never reaches langgraph's ToolNode."""
    files = _files(load("hymn_books.screenplay.json"), _bare_timeline_json())
    tool = make_narration_tool(BrokenTTS(), MediaStore().put, author_id="dad", voice=None, second_telling=False)
    state = _call(tool, files)
    text = _tool_text(state)
    assert text.startswith("Error: could not voice the narration (RuntimeError: TTS provider returned HTTP 503)")
    assert "try synthesize_narration again" in text
    assert state["files"]["/timeline.json"] == files["/timeline.json"]


def test_a_failing_media_store_is_an_error_string_not_an_exception(screenplay, compiler, lookup):
    def put(_data: bytes, _content_type: str) -> str:
        raise ConnectionError("media store unreachable")

    timeline = _compiled(screenplay, compiler, lookup)
    files = _draft_files(screenplay, timeline)
    tool = make_narration_tool(FakeTTS(), put, author_id="dad", voice=None, second_telling=False,
                               compiler=compiler, scene_for=lookup)
    state = _run_tool(tool, files)
    text = _tool_text(state)
    assert text.startswith("Error: could not voice the narration (ConnectionError: media store unreachable)")
    assert state["files"] == files  # neither draft was rewritten
