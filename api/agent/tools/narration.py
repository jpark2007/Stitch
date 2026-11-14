"""synthesize_narration: TTS with word timestamps, and one narration track per telling.

ARCHITECTURE.md sections 2-3 (narrator, narration timing) and shared/CONTRACTS.md
"Timeline" -> Narration:

- Every telling of a memory gets its own `NarrationTrack` (audio, captions, lines with
  word timings) over the same shared action. `Timeline.narrations` maps the teller's
  person id to that track; switching teller swaps audio and captions only, never tracks.
- The top-level `Timeline.audio` / `Timeline.captions` repeat the `defaultNarrator`'s.
- `NarrationLine.words` are in timeline time (seconds from timeline start).

Text-to-speech sits behind the `TTS` protocol. `FakeTTS` is deterministic and offline so
the whole pipeline runs in tests with no keys.

Cues on spoken words (ARCHITECTURE.md section 3: "the compiler shifts the cued action to
land on the word the narrator says, using the TTS word timestamps"). Moving a cued action
is the compiler's job, so when the tool has a compiler the voice pass:

1. synthesizes every non-blank line of the teller;
2. writes each clip's word timings into that line's `words` in the draft screenplay (each
   word's `t` in seconds from the line's start, the screenplay convention of
   `screenplay.narration.line_words`), moving a line later only where its real audio would
   still be playing from the line before (and, for the default telling, keeping later lines
   in step with the actions a moved cue word carries along);
3. recompiles that screenplay, so every cued action lands on its spoken word;
4. starts each line where the recompiled timeline puts it, pushing a line later only if
   the previous clip is still playing (a safety net: `LINE_GAP` after its real end).

A second telling plays over the memory's saved action, not over the draft compile: each
line keeps its offset from its own shot's `actionStart` in the recompiled draft and is laid
over the saved timeline's shots, so a longer telling never moves a later shot.

A failed recompile falls back to the timeline already in the files with estimated
placement, and the tool says so with one `warning:` line. A failing TTS or media store
never raises out of the tool: it returns an `Error: could not voice ...` string so the
narrator can call it again.
"""

from __future__ import annotations

import io
import json
import logging
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

from agent.tools.compile import Compiler, SceneInputs
from schemas import Person
from screenplay import narration as screenplay_narration
from screenplay.schema import Caption, NarrationLine, NarrationTrack, Screenplay, Timeline, WordTiming

log = logging.getLogger(__name__)

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


@dataclass
class _Voiced:
    """One of the teller's lines with its clip: `index` is its place in the shot's line list."""

    shot_id: str
    index: int
    line: NarrationLine
    speech: Speech


def _check_shots(timeline: Timeline, lines_by_shot: dict[str, list[NarrationLine]]) -> None:
    """ValueError naming any shot the lines are for that the timeline does not have."""
    shot_ids = [s.id for s in timeline.shots]
    unknown = sorted(sid for sid in set(lines_by_shot) - set(shot_ids) if lines_by_shot[sid])
    if unknown:
        raise ValueError(f"narration lines for unknown shot(s): {', '.join(unknown)}; timeline has {shot_ids}")


def _synthesize(lines_by_shot: dict[str, list[NarrationLine]], tts: TTS, voice: str | None) -> list[_Voiced]:
    """A clip for every non-blank line, in shot-key order then line order."""
    out: list[_Voiced] = []
    for shot_id, lines in lines_by_shot.items():
        for index, line in enumerate(lines):
            if line.text.strip():
                out.append(_Voiced(shot_id, index, line, tts.synthesize(line.text, voice=voice)))
    return out


def _lay_out(
    timeline: Timeline,
    voiced: list[_Voiced],
    desired: Callable[[_Voiced], float],
) -> tuple[list[tuple[float, NarrationLine, Speech]], float]:
    """Start time of every voiced line on `timeline`, and when the last one's audio ends.

    Shots are walked in `timeline.shots` order and lines within a shot by their desired
    start (then line order). Each line starts at max(desired start, end of the previous
    line's clip + LINE_GAP).
    """
    by_shot: dict[str, list[_Voiced]] = {}
    for v in voiced:
        by_shot.setdefault(v.shot_id, []).append(v)
    placed: list[tuple[float, NarrationLine, Speech]] = []
    prev_end: float | None = None
    for shot in timeline.shots:
        items = sorted(by_shot.get(shot.id, []), key=lambda v: (desired(v), v.index))
        for v in items:
            start = desired(v)
            if prev_end is not None:
                start = max(start, prev_end + LINE_GAP)
            start = _r(start)
            placed.append((start, v.line, v.speech))
            prev_end = start + v.speech.duration
    audio_end = _r(prev_end) if prev_end is not None else 0.0
    return placed, audio_end


def _track_lines(
    placed: list[tuple[float, NarrationLine, Speech]], author_id: str, audio_id: str | None
) -> NarrationTrack:
    """The track for placed lines: `t` and `words` in timeline time, one caption per line."""
    track_lines: list[NarrationLine] = []
    captions: list[Caption] = []
    for start, line, speech in placed:
        words = [WordTiming(t=_r(start + w.t), word=w.word) for w in speech.words]
        track_lines.append(line.model_copy(update={"t": start, "words": words}, deep=True))
        captions.append(Caption(t=start, text=line.text))
    return NarrationTrack(author_id=author_id, audio=audio_id, captions=captions, lines=track_lines)


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
    _check_shots(timeline, lines_by_shot)
    action_start = {s.id: s.action_start for s in timeline.shots}
    voiced = _synthesize(lines_by_shot, tts, voice)
    placed, audio_end = _lay_out(timeline, voiced, lambda v: action_start[v.shot_id] + v.line.t)
    audio_id = put_media(_mix(placed), "audio/wav") if placed else None
    return _track_lines(placed, author_id, audio_id), audio_end


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


DURATION_EPSILON = 1e-6


def apply_narration(timeline: Timeline, track: NarrationTrack, audio_end: float, *, make_default: bool) -> Timeline:
    """A copy of `timeline` with `track` as `narrations[track.author_id]`.

    The top-level `audio`/`captions` (and `defaultNarrator`) follow the track when
    `make_default` is set, when there is no default narrator yet, or when this author
    already is the default narrator (a re-telling replaces their own narration).
    `duration` grows to `audio_end + 0.5` if the narration outlasts the action; the closing
    shot, when it ended with the old duration, is held on stage until the new one (as
    `family._add_telling` does). Tracks and everything else are left untouched.
    """
    data = timeline.model_dump(by_alias=True)
    narrations = dict(data.get("narrations") or {})
    narrations[track.author_id] = track.model_dump(by_alias=True)
    data["narrations"] = narrations
    if make_default or timeline.default_narrator in (None, track.author_id):
        data["defaultNarrator"] = track.author_id
        data["audio"] = track.audio
        data["captions"] = [c.model_dump(by_alias=True) for c in track.captions]
    duration = _r(max(timeline.duration, audio_end + TAIL))
    if duration > timeline.duration:
        shots = data.get("shots") or []
        if shots and abs(shots[-1]["end"] - timeline.duration) <= DURATION_EPSILON:
            shots[-1]["end"] = duration
    data["duration"] = duration
    return Timeline.model_validate(data)


# --------------------------------------------------------------------------- cues on spoken words


def lines_for_author(
    screenplay: Screenplay, author_id: str, *, second_telling: bool = False
) -> dict[str, list[NarrationLine]]:
    """The author's lines by shot.

    `Screenplay.narrations[author_id]` if present. Otherwise, for a first telling, each
    shot's default-telling `narration`; for a second telling, nothing: the default telling
    belongs to someone else, so a second teller's lines only ever come from
    `narrations[author_id]`.
    """
    if screenplay.narrations and author_id in screenplay.narrations:
        return {shot_id: list(lines) for shot_id, lines in screenplay.narrations[author_id].items()}
    if second_telling:
        return {}
    return {shot.id: list(shot.narration) for shot in screenplay.shots if shot.narration}


def _from_default(screenplay: Screenplay, author_id: str) -> bool:
    """True when the author's lines are the default telling (each shot's `narration`)."""
    return not (screenplay.narrations and author_id in screenplay.narrations)


def _shot_lines(screenplay: Screenplay, author_id: str, from_default: bool) -> list[tuple[str, list[NarrationLine]]]:
    """(shot id, the author's lines in that shot) in screenplay shot order: the live lists."""
    if from_default:
        return [(shot.id, shot.narration) for shot in screenplay.shots]
    mine = (screenplay.narrations or {}).get(author_id) or {}
    return [(shot.id, mine[shot.id]) for shot in screenplay.shots if shot.id in mine]


def _cue_time(line: NarrationLine, start: float) -> float | None:
    """Shot time of the line's cue word if it starts at `start` (None: uncued or no such word)."""
    if line.cue is None:
        return None
    try:
        return screenplay_narration.cue_word_time(line, screenplay_narration.line_words(line, start))
    except screenplay_narration.NarrationError:
        return None


def voiced_screenplay(
    screenplay: Screenplay,
    author_id: str,
    voiced: list[_Voiced],
    *,
    from_default: bool,
) -> Screenplay:
    """A copy of the screenplay with the author's TTS word timings in their lines.

    Each voiced line gets `words` = its clip's word timings (seconds from the line's start).
    Within a shot, in line order, a line moves later to start no sooner than `LINE_GAP`
    after the previous line's clip ends. For the default telling, whose cue words move the
    action, every later line in the shot also moves by as much as the last cue word moved
    (the compiler carries the uncued actions after a cued one by that much), so lines stay
    with the actions they describe. Lines are never moved earlier than written plus that
    shift.
    """
    out = screenplay.model_copy(deep=True)
    speech_of = {(v.shot_id, v.index): v.speech for v in voiced}
    for shot_id, lines in _shot_lines(out, author_id, from_default):
        shift = 0.0
        prev_end: float | None = None
        for index, line in enumerate(lines):
            speech = speech_of.get((shot_id, index))
            if speech is None:
                continue
            start = line.t + shift
            if prev_end is not None:
                start = max(start, prev_end + LINE_GAP)
            start = _r(start)
            old_cue = _cue_time(line, line.t)
            new_line = line.model_copy(update={"t": start, "words": [w.model_copy() for w in speech.words]})
            new_cue = _cue_time(new_line, start)
            if from_default and old_cue is not None and new_cue is not None:
                shift = new_cue - old_cue
            lines[index] = new_line
            prev_end = start + speech.duration
    return out


def compiled_starts(
    screenplay: Screenplay, timeline: Timeline, author_id: str, *, from_default: bool
) -> dict[tuple[str, int], float]:
    """(shot id, line index) -> timeline start of each of the author's lines in a compiled timeline.

    The compiler keeps every telling's lines in screenplay shot order and line order, under
    the default teller's id for the default telling. Raises ValueError when the compiled
    track does not match the screenplay's lines.
    """
    key = screenplay_narration.default_author(screenplay) if from_default else author_id
    track = (timeline.narrations or {}).get(key)
    if track is None:
        raise ValueError(f"the compiled timeline has no narration for {key}")
    keys = [
        (shot_id, i)
        for shot_id, lines in _shot_lines(screenplay, author_id, from_default)
        for i in range(len(lines))
    ]
    if len(keys) != len(track.lines):
        raise ValueError(f"the compiled timeline has {len(track.lines)} line(s) for {key}, the screenplay {len(keys)}")
    return {k: line.t for k, line in zip(keys, track.lines, strict=True)}


class _RecompileFailed(Exception):
    pass


def _recompile(
    screenplay: Screenplay,
    compiler: Compiler,
    scene_for: Callable[[Screenplay], SceneInputs],
) -> Timeline:
    result = compiler(screenplay, scene_for(screenplay))
    if not result.ok or result.timeline is None:
        errors = "; ".join(e.message for e in result.errors[:3]) or "no timeline"
        raise _RecompileFailed(errors)
    return result.timeline


# --------------------------------------------------------------------------- tool


def _read_json(files: dict[str, Any], path: str) -> tuple[Any, str | None]:
    file_data = files.get(path)
    if file_data is None:
        return None, f"Error: {path} not found. Write it before calling synthesize_narration."
    try:
        return json.loads(file_data_to_string(file_data)), None
    except (TypeError, ValueError) as e:
        return None, f"Error: {path} is not valid JSON: {e}"


def _voice_error(exc: Exception) -> str:
    return f"Error: could not voice the narration ({type(exc).__name__}: {exc}); try synthesize_narration again"


def _dump(model: Screenplay | Timeline) -> str:
    return json.dumps(model.model_dump(mode="json", by_alias=True), indent=2)


def _file(files: dict[str, Any], path: str, content: str) -> Any:
    old = files.get(path)
    return update_file_data(old, content) if isinstance(old, dict) else create_file_data(content)


def make_narration_tool(
    tts: TTS,
    put_media: Callable[[bytes, str], str],
    *,
    author_id: str,
    voice: str | None,
    second_telling: bool,
    compiler: Compiler | None = None,
    scene_for: Callable[[Screenplay], SceneInputs] | None = None,
    base_timeline: Callable[[], Timeline | None] | None = None,
) -> BaseTool:
    """The synthesize_narration tool, bound to this run's teller and services.

    It reads /screenplay.json and /timeline.json from the agent's draft files, voices the
    teller's lines, adds their narration track to the timeline (as the default telling
    unless `second_telling`), and writes /timeline.json back.

    With `compiler` and `scene_for` the TTS word timings go into the screenplay's lines and
    the screenplay is recompiled, so cued actions land on the spoken words; a first telling
    then writes both /screenplay.json (with the words) and /timeline.json (the recompiled
    timeline plus the track). Without them the lines are laid on the timeline in the files
    as written (estimated placement).

    `base_timeline` (second tellings) returns the saved memory's timeline: the new track is
    laid over its shots, each line keeping its offset from its own shot's actionStart, and
    /timeline.json becomes that timeline plus the track.
    """

    @tool("synthesize_narration")
    def synthesize_narration(
        state: Annotated[dict, InjectedState],
        tool_call_id: Annotated[str, InjectedToolCallId],
    ) -> Command | str:
        """Voice the teller's narration lines from /screenplay.json with text-to-speech and add
        the narration track (audio, captions, word timings) to /timeline.json.

        Run this after the last successful compile. It writes the spoken word timings into
        the teller's lines in /screenplay.json and recompiles internally, so each cued action
        lands on its spoken word. Lines that would overlap are pushed later, and the scene is
        lengthened if the narration runs past the action.
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

        lines = lines_for_author(screenplay, author_id, second_telling=second_telling)
        if not any(line.text.strip() for shot_lines in lines.values() for line in shot_lines):
            if second_telling:
                return (
                    f"Error: the screenplay has no narration lines for {author_id}. A second telling is voiced "
                    f"only from narrations[{author_id}]: the narrator's write pass must put this teller's lines "
                    f"in narrations[{author_id}] of {SCREENPLAY_PATH}."
                )
            return f"Error: the screenplay has no narration lines for {author_id}."
        try:
            _check_shots(timeline, lines)
        except ValueError as e:
            return f"Error: {e}"

        base: Timeline | None = None
        if second_telling and base_timeline is not None:
            try:
                base = base_timeline()
            except Exception as e:  # noqa: BLE001 - a repo failure is a tool error, not a run failure
                log.warning("synthesize_narration: could not load the saved timeline", exc_info=True)
                return f"Error: could not load the saved memory's timeline ({type(e).__name__}: {e})"
            if base is None:
                return "Error: the saved memory has no timeline to add this telling to."
            try:
                _check_shots(base, lines)
            except ValueError as e:
                return f"Error: {e}"

        try:
            voiced = _synthesize(lines, tts, voice)
        except Exception as e:  # noqa: BLE001 - TTS is an outside service; the narrator may retry
            log.warning("synthesize_narration: TTS failed for %s", author_id, exc_info=True)
            return _voice_error(e)

        from_default = not second_telling and _from_default(screenplay, author_id)
        warning: str | None = None
        draft: Screenplay | None = None
        compiled: Timeline | None = None
        starts: dict[tuple[str, int], float] = {}
        if compiler is not None and scene_for is not None:
            try:
                draft = voiced_screenplay(screenplay, author_id, voiced, from_default=from_default)
                compiled = _recompile(draft, compiler, scene_for)
                starts = compiled_starts(draft, compiled, author_id, from_default=from_default)
            except Exception as e:  # noqa: BLE001 - a failed recompile only costs cue precision
                log.warning("synthesize_narration: recompile with word timings failed", exc_info=True)
                draft, compiled, starts = None, None, {}
                reason = f"compile errors: {e}" if isinstance(e, _RecompileFailed) else f"{type(e).__name__}: {e}"
                over = "the saved action" if base is not None else f"the timeline in {TIMELINE_PATH}"
                warning = (
                    f"warning: could not recompile with the spoken word timings ({reason}); voiced over "
                    f"{over} with estimated placement, so cued actions may not land exactly on their words."
                )

        layout_on = compiled if compiled is not None else timeline
        draft_start = {s.id: s.action_start for s in layout_on.shots}

        def offset(v: _Voiced) -> float:
            """Seconds from the line's own shot's actionStart where it wants to start."""
            if compiled is not None:
                return starts[(v.shot_id, v.index)] - draft_start[v.shot_id]
            return v.line.t

        target = base if base is not None else layout_on
        target_start = {s.id: s.action_start for s in target.shots}
        try:
            placed, audio_end = _lay_out(target, voiced, lambda v: target_start[v.shot_id] + offset(v))
            audio_id = put_media(_mix(placed), "audio/wav") if placed else None
        except Exception as e:  # noqa: BLE001 - the mix and the media store are outside the agent's control
            log.warning("synthesize_narration: could not mix or store the audio for %s", author_id, exc_info=True)
            return _voice_error(e)
        track = _track_lines(placed, author_id, audio_id)
        updated = apply_narration(target, track, audio_end, make_default=not second_telling)

        updates = {TIMELINE_PATH: _file(files, TIMELINE_PATH, _dump(updated))}
        wrote = TIMELINE_PATH
        if draft is not None:
            # The screenplay that produced this action: the teller's lines with their words.
            updates[SCREENPLAY_PATH] = _file(files, SCREENPLAY_PATH, _dump(draft))
            wrote = f"{SCREENPLAY_PATH} (word timings) and {TIMELINE_PATH}"
        seconds = round(audio_end, 2)
        summary = (
            f"Narrated {len(track.lines)} line(s) for {author_id}: {seconds} s of speech, "
            f"scene now {round(updated.duration, 2)} s. Wrote {wrote}."
        )
        if compiled is not None:
            summary += " Recompiled with the spoken word timings, so cued actions land on their words."
        if base is not None:
            summary += " Laid over the saved scene's action."
        if warning:
            summary += "\n" + warning
        return Command(
            update={
                "files": updates,
                "messages": [ToolMessage(summary, tool_call_id=tool_call_id, name="synthesize_narration")],
            }
        )

    return synthesize_narration
