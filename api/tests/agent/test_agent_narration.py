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
from schemas import CharacterSheet, Person
from screenplay.schema import NarrationLine, Screenplay, Timeline, WordTiming

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
    assert with_both.shots == bare_timeline.shots

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
