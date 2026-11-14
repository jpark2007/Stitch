"""Narrate: the beats' lines voiced as one track (film.types.Narrator).

Built on the agent's narration seam (agent.tools.narration): any `TTS` gives one `Speech`
(16-bit mono WAV plus word timings) per line, and `FakeTTS(tone=False)` is the silent,
estimated-timings fallback the voice pass already uses.

`TTSNarrator.speak(lines)` lays the clips end to end: LEAD_IN seconds of silence, then each
line, with BEAT_PAUSE seconds between lines, all in one WAV. `line_starts` and `words` are in
film time. With `silent=True` (the offline narrator), or when the TTS fails twice (one retry
per film, as in the voice pass) or its clips do not mix, the narration is silent:
`audio` None and FakeTTS's estimated timings, laid out the same way.
"""

from __future__ import annotations

import logging

from agent.tools.narration import TTS, FakeTTS, Speech, _read_wav, _RetryOnce, _wav_bytes
from film.types import Narration

log = logging.getLogger(__name__)

LEAD_IN = 0.6
"""Seconds of picture before the first word."""
BEAT_PAUSE = 0.8
"""Seconds of silence between two beats' lines."""

_PLACES = 3


def _r(t: float) -> float:
    return round(t, _PLACES)


def _layout(speeches: list[Speech]) -> tuple[list[float], list[tuple[float, str]], float]:
    """(line starts, words in film time, end of the last clip) for clips laid end to end."""
    starts: list[float] = []
    words: list[tuple[float, str]] = []
    t = LEAD_IN
    for i, speech in enumerate(speeches):
        if i:
            t += BEAT_PAUSE
        starts.append(_r(t))
        words.extend((_r(t + w.t), w.word) for w in speech.words)
        t += speech.duration
    return starts, words, _r(t)


def _mix(speeches: list[Speech], starts: list[float]) -> bytes:
    """One WAV: silence, then each clip at its start. ValueError when sample rates differ."""
    rates = {s.sample_rate for s in speeches}
    if len(rates) != 1:
        raise ValueError(f"clips have different sample rates: {sorted(rates)}")
    sr = rates.pop()
    pcm = bytearray()
    for start, speech in zip(starts, speeches, strict=True):
        frames, clip_rate = _read_wav(speech.audio)
        if clip_rate != sr:
            raise ValueError("clip header and Speech.sample_rate disagree")
        offset = 2 * round(start * sr)
        if offset > len(pcm):
            pcm += bytes(offset - len(pcm))
        del pcm[offset:]
        pcm += frames
    return _wav_bytes(bytes(pcm), sr)


def silent_narration(lines: list[str]) -> Narration:
    """No audio; FakeTTS's estimated word timings laid out as a real track would be."""
    tts = FakeTTS(tone=False)
    starts, words, end = _layout([tts.synthesize(line) for line in lines])
    return Narration(audio=None, content_type=None, line_starts=starts, words=words, duration=end)


class TTSNarrator:
    """film.types.Narrator over a narration.TTS."""

    def __init__(self, tts: TTS | None = None, *, voice: str | None = None, silent: bool = False) -> None:
        self.tts = tts
        self.voice = voice
        self.silent = silent or tts is None

    def speak(self, lines: list[str]) -> Narration:
        if self.silent or self.tts is None:
            return silent_narration(lines)
        try:
            tts = _RetryOnce(self.tts)
            speeches = [tts.synthesize(line, voice=self.voice) for line in lines]
            starts, words, end = _layout(speeches)
            audio = _mix(speeches, starts)
        except Exception as exc:  # noqa: BLE001 - speech is an outside service; narrate silently
            log.warning("film narrate: speech failed (%s); the film is silent", type(exc).__name__)
            return silent_narration(lines)
        return Narration(audio=audio, content_type="audio/wav", line_starts=starts, words=words, duration=end)


def offline_narrator() -> TTSNarrator:
    return TTSNarrator(None, silent=True)


__all__ = ["BEAT_PAUSE", "LEAD_IN", "TTSNarrator", "offline_narrator", "silent_narration"]
