"""synthesize_narration: TTS with word timestamps, and one narration track per telling.

ARCHITECTURE.md sections 2-3 (narrator, narration timing) and shared/CONTRACTS.md
"Timeline" -> Narration:

- Every telling of a memory gets its own `NarrationTrack` (audio, captions, lines with
  word timings) over the same shared action. `Timeline.narrations` maps the teller's
  person id to that track; switching teller swaps audio and captions only, never tracks.
- The top-level `Timeline.audio` / `Timeline.captions` repeat the `defaultNarrator`'s.
- `NarrationLine.words` are in timeline time (seconds from timeline start).

Text-to-speech sits behind the `TTS` protocol. `FakeTTS` is deterministic and offline so
the whole pipeline runs in tests with no keys. Moving cued actions onto their words is
the compiler's job, not this module's.
"""

from __future__ import annotations

import io
import json
import math
import re
import struct
import wave
from dataclasses import dataclass, field
from typing import Annotated, Any, Callable, Protocol

from deepagents.backends.utils import create_file_data, file_data_to_string, update_file_data
from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool, InjectedToolCallId, tool
from langgraph.prebuilt import InjectedState
from langgraph.types import Command
from pydantic import ValidationError

from schemas import Person
from screenplay.schema import Caption, NarrationLine, NarrationTrack, Screenplay, Timeline, WordTiming

SCREENPLAY_PATH = "/screenplay.json"
TIMELINE_PATH = "/timeline.json"

LINE_GAP = 0.15
"""Minimum silence (seconds) between the end of one line and the start of the next."""

TAIL = 0.5
"""Seconds the scene holds after the last word before it may end."""

SAMPLE_RATE = 16_000

# Seconds are rounded to this many places in the timeline (sub-millisecond noise only).
_PLACES = 4


# --------------------------------------------------------------------------- TTS seam


@dataclass
class Speech:
    """One synthesized line."""

    audio: bytes
    """16-bit PCM mono WAV."""
    sample_rate: int
    duration: float
    """Seconds of audio in `audio`, including any trailing pause."""
    words: list[WordTiming] = field(default_factory=list)
    """Word start times, `t` relative to the start of this clip."""


class TTS(Protocol):
    def synthesize(self, text: str, *, voice: str | None = None) -> Speech: ...


# Pause rules for FakeTTS, keyed by the punctuation a token ends with.
WORD_SECONDS = 0.30
SHORT_PAUSE = 0.20  # after , ;
LONG_PAUSE = 0.35  # after . ! ? and an ellipsis

_EDGE = re.compile(r"^[^\w'’]+|[^\w'’]+$")
_CLOSERS = "\"'’”)]}»"


def _pause_after(token: str) -> float:
    """Pause FakeTTS adds after a token, from the punctuation it ends with."""
    tail = token.rstrip(_CLOSERS)
    if tail.endswith("…") or tail.endswith("..."):
        return LONG_PAUSE
    if tail and tail[-1] in ".!?":
        return LONG_PAUSE
    if tail and tail[-1] in ",;":
        return SHORT_PAUSE
    return 0.0


def _split_words(text: str) -> list[tuple[str, float]]:
    """(word, pause after it) for each spoken word.

    Words are whitespace tokens with surrounding punctuation stripped ("steps…" -> "steps",
    "…and" -> "and"; apostrophes inside a word stay). A token that is only punctuation
    ("—", "...") is not a word; its pause, if any, goes to the word before it.
    """
    out: list[tuple[str, float]] = []
    for token in text.split():
        word = _EDGE.sub("", token)
        pause = _pause_after(token)
        if word:
            out.append((word, pause))
        elif out and pause:
            prev_word, prev_pause = out[-1]
            out[-1] = (prev_word, max(prev_pause, pause))
    return out


def _wav_bytes(samples: bytes, sample_rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(samples)
    return buf.getvalue()


def _read_wav(data: bytes) -> tuple[bytes, int]:
    """(PCM frames, sample rate) of a 16-bit mono WAV."""
    with wave.open(io.BytesIO(data), "rb") as w:
        if w.getnchannels() != 1 or w.getsampwidth() != 2:
            raise ValueError("narration audio must be 16-bit mono WAV")
        return w.readframes(w.getnframes()), w.getframerate()


class FakeTTS:
    """Deterministic offline TTS.

    Each word takes 0.30 s; a word ending in `,` or `;` is followed by 0.20 s of pause,
    and one ending in `.`, `!`, `?` or an ellipsis by 0.35 s (the pause after the last
    word counts toward the duration too). The audio is a quiet 220 Hz hum during each
    word and silence elsewhere, at 16 kHz, exactly `duration` long. All arithmetic is
    in whole samples, so word times and the duration are exact multiples of 1/16000 s.
    """

    def __init__(self, sample_rate: int = SAMPLE_RATE, tone: bool = True):
        self.sample_rate = sample_rate
        self.tone = tone
        self.calls: list[tuple[str, str | None]] = []

    def synthesize(self, text: str, *, voice: str | None = None) -> Speech:
        self.calls.append((text, voice))
        sr = self.sample_rate
        word_frames = round(WORD_SECONDS * sr)
        hum = self._hum(word_frames)
        pcm = bytearray()
        words: list[WordTiming] = []
        for word, pause in _split_words(text):
            words.append(WordTiming(t=len(pcm) // 2 / sr, word=word))
            pcm += hum
            pcm += bytes(2 * round(pause * sr))
        frames = len(pcm) // 2
        return Speech(audio=_wav_bytes(bytes(pcm), sr), sample_rate=sr, duration=frames / sr, words=words)

    def _hum(self, frames: int) -> bytes:
        if not self.tone:
            return bytes(2 * frames)
        sr = self.sample_rate
        fade = max(1, frames // 10)
        out = bytearray()
        for i in range(frames):
            env = min(1.0, i / fade, (frames - 1 - i) / fade)
            out += struct.pack("<h", int(1200 * env * math.sin(2 * math.pi * 220 * i / sr)))
        return bytes(out)


# Placeholder voice ids until real voices are chosen per teller. Assigned by hashing the
# person id so a teller always gets the same voice across runs; None means the TTS
# provider's default voice.
PLACEHOLDER_VOICES = ("voice_a", "voice_b", "voice_c", "voice_d")


def voice_for(person: Person | None) -> str | None:
    """Placeholder voice for a teller (stable per person id). None: provider default."""
    if person is None:
        return None
    index = sum(person.id.encode("utf-8")) % len(PLACEHOLDER_VOICES)
    return PLACEHOLDER_VOICES[index]


# --------------------------------------------------------------------------- tracks


def _r(t: float) -> float:
    return round(t, _PLACES)


def build_narration_track(
    timeline: Timeline,
    lines_by_shot: dict[str, list[NarrationLine]],
    *,
    author_id: str,
    tts: TTS,
    put_media: Callable[[bytes, str], str],
    voice: str | None = None,
) -> tuple[NarrationTrack, float]:
    """Synthesize one telling's lines and lay them out on the timeline.

    `lines_by_shot` maps shot id -> lines in screenplay time (`t` from the shot's
    `actionStart`). Shots are walked in `timeline.shots` order and lines within a shot in
    `t` order. Each line starts at max(shot.actionStart + line.t, end of the previous
    line + 0.15 s), so a line that would overlap the one before is pushed later. A line's
    end is its start plus its clip duration. Blank lines are skipped.

    Returns the track (lines copied with `t` and `words` in timeline time, one caption per
    line, `audio` the media id of a single WAV that starts at timeline 0 with silence
    between lines) and `audio_end`, the timeline time the last line's audio ends.

    Raises ValueError for lines whose shot id is not in the timeline, or for TTS clips
    whose sample rates differ.
    """
    shot_ids = [s.id for s in timeline.shots]
    unknown = sorted(set(lines_by_shot) - set(shot_ids))
    if unknown:
        raise ValueError(f"narration lines for unknown shot(s): {', '.join(unknown)}; timeline has {shot_ids}")

    placed: list[tuple[float, NarrationLine, Speech]] = []
    prev_end: float | None = None
    for shot in timeline.shots:
        lines = sorted(lines_by_shot.get(shot.id, []), key=lambda line: line.t)
        for line in lines:
            if not line.text.strip():
                continue
            start = shot.action_start + line.t
            if prev_end is not None:
                start = max(start, prev_end + LINE_GAP)
            start = _r(start)
            speech = tts.synthesize(line.text, voice=voice)
            placed.append((start, line, speech))
            prev_end = start + speech.duration

    track_lines: list[NarrationLine] = []
    captions: list[Caption] = []
    for start, line, speech in placed:
        words = [WordTiming(t=_r(start + w.t), word=w.word) for w in speech.words]
        track_lines.append(line.model_copy(update={"t": start, "words": words}, deep=True))
        captions.append(Caption(t=start, text=line.text))

    audio_end = _r(prev_end) if prev_end is not None else 0.0
    audio_id = put_media(_mix(placed), "audio/wav") if placed else None
    track = NarrationTrack(author_id=author_id, audio=audio_id, captions=captions, lines=track_lines)
    return track, audio_end


def _mix(placed: list[tuple[float, NarrationLine, Speech]]) -> bytes:
    """One WAV from timeline 0: silence, then each clip at its start time."""
    rates = {s.sample_rate for _, _, s in placed}
    if len(rates) != 1:
        raise ValueError(f"TTS clips have different sample rates: {sorted(rates)}")
    sr = rates.pop()
    pcm = bytearray()
    for start, _, speech in placed:
        frames, clip_rate = _read_wav(speech.audio)
        if clip_rate != sr:
            raise ValueError(f"TTS clip header says {clip_rate} Hz but Speech.sample_rate is {sr}")
        offset = 2 * round(start * sr)
        if offset > len(pcm):
            pcm += bytes(offset - len(pcm))
        # Starts are >= previous end + 0.15 s, so a clip never lands inside the previous
        # one; guard against rounding by overwriting at most a sample.
        del pcm[offset:]
        pcm += frames
    return _wav_bytes(bytes(pcm), sr)


def apply_narration(timeline: Timeline, track: NarrationTrack, audio_end: float, *, make_default: bool) -> Timeline:
    """A copy of `timeline` with `track` as `narrations[track.author_id]`.

    The top-level `audio`/`captions` (and `defaultNarrator`) follow the track when
    `make_default` is set, when there is no default narrator yet, or when this author
    already is the default narrator (a re-telling replaces their own narration).
    `duration` grows to `audio_end + 0.5` if the narration outlasts the action.
    Tracks, shots and everything else are left untouched.
    """
    data = timeline.model_dump(by_alias=True)
    narrations = dict(data.get("narrations") or {})
    narrations[track.author_id] = track.model_dump(by_alias=True)
    data["narrations"] = narrations
    if make_default or timeline.default_narrator in (None, track.author_id):
        data["defaultNarrator"] = track.author_id
        data["audio"] = track.audio
        data["captions"] = [c.model_dump(by_alias=True) for c in track.captions]
    data["duration"] = _r(max(timeline.duration, audio_end + TAIL))
    return Timeline.model_validate(data)


# --------------------------------------------------------------------------- tool


def lines_for_author(screenplay: Screenplay, author_id: str) -> dict[str, list[NarrationLine]]:
    """The author's lines by shot: `Screenplay.narrations[author_id]` if present, else
    each shot's default-telling `narration`."""
    if screenplay.narrations and author_id in screenplay.narrations:
        return {shot_id: list(lines) for shot_id, lines in screenplay.narrations[author_id].items()}
    return {shot.id: list(shot.narration) for shot in screenplay.shots if shot.narration}


def _read_json(files: dict[str, Any], path: str) -> tuple[Any, str | None]:
    file_data = files.get(path)
    if file_data is None:
        return None, f"Error: {path} not found. Write it before calling synthesize_narration."
    try:
        return json.loads(file_data_to_string(file_data)), None
    except (TypeError, ValueError) as e:
        return None, f"Error: {path} is not valid JSON: {e}"


def make_narration_tool(
    tts: TTS,
    put_media: Callable[[bytes, str], str],
    *,
    author_id: str,
    voice: str | None,
    second_telling: bool,
) -> BaseTool:
    """The synthesize_narration tool, bound to this run's teller and services.

    It reads /screenplay.json and /timeline.json from the agent's draft files, voices
    the teller's lines, adds their narration track to the timeline (as the default
    telling unless `second_telling`), and writes /timeline.json back.
    """

    @tool("synthesize_narration")
    def synthesize_narration(
        state: Annotated[dict, InjectedState],
        tool_call_id: Annotated[str, InjectedToolCallId],
    ) -> Command | str:
        """Voice the teller's narration lines from /screenplay.json with text-to-speech and add
        the narration track (audio, captions, word timings) to /timeline.json.

        Run this after the screenplay compiles. Lines that would overlap are pushed later,
        and the scene is lengthened if the narration runs past the action.
        """
        files = state.get("files") or {}
        raw_sp, err = _read_json(files, SCREENPLAY_PATH)
        if err:
            return err
        raw_tl, err = _read_json(files, TIMELINE_PATH)
        if err:
            return err
        try:
            screenplay = Screenplay.model_validate(raw_sp)
        except ValidationError as e:
            return f"Error: {SCREENPLAY_PATH} is not a valid screenplay: {e}"
        try:
            timeline = Timeline.model_validate(raw_tl)
        except ValidationError as e:
            return f"Error: {TIMELINE_PATH} is not a valid timeline: {e}"

        lines = lines_for_author(screenplay, author_id)
        if not any(line.text.strip() for shot_lines in lines.values() for line in shot_lines):
            return f"Error: the screenplay has no narration lines for {author_id}."
        try:
            track, audio_end = build_narration_track(
                timeline, lines, author_id=author_id, tts=tts, put_media=put_media, voice=voice
            )
        except ValueError as e:
            return f"Error: {e}"
        updated = apply_narration(timeline, track, audio_end, make_default=not second_telling)

        content = json.dumps(updated.model_dump(mode="json", by_alias=True), indent=2)
        old = files[TIMELINE_PATH]
        new_file = update_file_data(old, content) if isinstance(old, dict) else create_file_data(content)
        seconds = round(audio_end, 2)
        summary = (
            f"Narrated {len(track.lines)} line(s) for {author_id}: {seconds} s of speech, "
            f"scene now {round(updated.duration, 2)} s. Wrote {TIMELINE_PATH}."
        )
        return Command(
            update={
                "files": {TIMELINE_PATH: new_file},
                "messages": [ToolMessage(summary, tool_call_id=tool_call_id, name="synthesize_narration")],
            }
        )

    return synthesize_narration
