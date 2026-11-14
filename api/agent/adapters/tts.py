"""ElevenLabsTTS: the production ``narration.TTS``, with word timings from ElevenLabs alignment.

``synthesize(text, voice=...)`` calls
``POST https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/with-timestamps?output_format=pcm_16000``
and turns the response into a ``narration.Speech``:

- ``audio_base64`` is raw 16-bit little-endian PCM at 16 kHz. It is wrapped as a mono 16 kHz
  16-bit WAV (narration's own WAV writer), which is what ``Speech.audio`` promises and what
  ``narration._mix`` reads back.
- ``duration`` is the number of PCM frames / 16000.
- Word timings come from ``alignment.characters`` and
  ``alignment.character_start_times_seconds``. The text is split into whitespace tokens and
  the surrounding punctuation is stripped exactly as ``narration._split_words`` does
  (``"Hello,"`` -> ``"Hello"``; a token that is only punctuation is not a word). A word's
  time is the start time of its first character that survives the stripping (for a plain
  word that is its first non-space character).

Voices: the studio asks for the placeholder voices ``narration.PLACEHOLDER_VOICES``
(``voice_a`` ... ``voice_d``). ``ELEVENLABS_VOICES`` maps them to real voice ids, as
``voice_a=<id>,voice_b=<id>,...`` (or pass ``voices=``). A voice that is ``None`` or not in
the mapping uses ``DEFAULT_VOICE_ID`` (ElevenLabs' premade "Rachel" voice), or
``ELEVENLABS_DEFAULT_VOICE`` when that is set.

Env: ``ELEVENLABS_API_KEY`` (required unless a ``transport`` is injected),
``ELEVENLABS_MODEL`` (default ``eleven_multilingual_v2``), ``ELEVENLABS_VOICES``,
``ELEVENLABS_DEFAULT_VOICE``. Errors raise ``RuntimeError`` with the HTTP status and never
include the key. Tests inject an ``httpx.MockTransport`` (and a no-op ``sleep``).

Bounded, so a hung speech service cannot stall a voice pass (which, on any error here, narrates
silently with estimated word timings):

- Every request has a timeout: ``timeout`` seconds (default ``DEFAULT_TIMEOUT_S``, 30; one line
  of speech takes a few seconds) to write the request and between bytes of the response, and at
  most ``images.CONNECT_TIMEOUT_S`` (10) to connect.
- A request that fails fast is retried at most ``max_retries`` times (default
  ``DEFAULT_MAX_RETRIES``, 2), with the same rules as the image adapter
  (``images.retryable_error``, ``images.RETRY_STATUSES``, ``images.backoff_s``): a connection
  that could not be made, or HTTP 408, 429, 500, 502, 503 or 504. A read timeout is not retried.
"""

from __future__ import annotations

import base64
import math
import os
import re
import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any

import httpx

from agent.adapters.images import CONNECT_TIMEOUT_S, RETRY_STATUSES, backoff_s, retryable_error
from agent.tools.narration import _EDGE, PLACEHOLDER_VOICES, Speech, _wav_bytes
from screenplay.schema import WordTiming

API_KEY_ENV = "ELEVENLABS_API_KEY"
MODEL_ENV = "ELEVENLABS_MODEL"
VOICES_ENV = "ELEVENLABS_VOICES"
DEFAULT_VOICE_ENV = "ELEVENLABS_DEFAULT_VOICE"

BASE_URL = "https://api.elevenlabs.io"
DEFAULT_MODEL = "eleven_multilingual_v2"
DEFAULT_VOICE_ID = "21m00Tcm4TlvDq8ikWAM"
"""ElevenLabs' premade "Rachel" voice: used for None and unmapped voices."""

SAMPLE_RATE = 16_000
OUTPUT_FORMAT = "pcm_16000"

DEFAULT_TIMEOUT_S = 30.0
"""Seconds to write a request and between bytes of the response."""
DEFAULT_MAX_RETRIES = 2
"""Retries of a request that failed fast (no connection, HTTP 408/429/5xx); never of a read timeout."""

_VOICE_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_ERROR_DETAIL_CHARS = 200


def parse_voices(spec: str | None) -> dict[str, str]:
    """Parse ``'voice_a=<id>,voice_b=<id>'`` into a dict. Blank entries are ignored.

    Raises ValueError on an entry without ``=`` or a voice id that is not a plain id.
    """
    out: dict[str, str] = {}
    for entry in (spec or "").split(","):
        entry = entry.strip()
        if not entry:
            continue
        name, sep, voice_id = entry.partition("=")
        name, voice_id = name.strip(), voice_id.strip()
        if not sep or not name or not voice_id:
            raise ValueError(f"{VOICES_ENV} entry {entry!r} must look like voice_a=<voice id>")
        out[name] = voice_id
    return out


def _check_voice_id(voice_id: str) -> str:
    if not _VOICE_ID.match(voice_id):
        raise ValueError(f"not a valid ElevenLabs voice id: {voice_id!r}")
    return voice_id


def word_timings(characters: Sequence[str], starts: Sequence[float]) -> list[WordTiming]:
    """Word start times from per-character alignment, words split like narration._split_words."""
    if len(characters) != len(starts):
        raise ValueError("alignment characters and start times differ in length")
    words: list[WordTiming] = []
    token: list[tuple[str, float]] = []

    def flush() -> None:
        if not token:
            return
        text = "".join(ch for ch, _ in token)
        word = _EDGE.sub("", text)
        if word:
            # _EDGE strips only from the ends, so the word starts after the leading strip.
            words.append(WordTiming(t=token[_leading_strip(text)][1], word=word))
        token.clear()

    for entry, t in zip(characters, starts, strict=True):
        # ElevenLabs reports one entry per character; a multi-character entry shares its time.
        for ch in str(entry):
            if ch.isspace():
                flush()
            else:
                token.append((ch, float(t)))
    flush()
    return words


def _leading_strip(text: str) -> int:
    """How many leading characters narration's _EDGE pattern strips from ``text``."""
    match = _EDGE.match(text)
    return match.end() if match else 0


class ElevenLabsTTS:
    """``narration.TTS`` over the ElevenLabs text-to-speech ``with-timestamps`` endpoint."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        voices: Mapping[str, str] | None = None,
        model: str | None = None,
        transport: httpx.BaseTransport | None = None,
        timeout: float = DEFAULT_TIMEOUT_S,
        max_retries: int = DEFAULT_MAX_RETRIES,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not (isinstance(timeout, (int, float)) and math.isfinite(timeout) and timeout > 0):
            raise ValueError(f"timeout must be a number of seconds > 0, got {timeout!r}")
        if not isinstance(max_retries, int) or max_retries < 0:
            raise ValueError(f"max_retries must be a whole number >= 0, got {max_retries!r}")
        key = api_key or os.environ.get(API_KEY_ENV)
        if not key and transport is None:
            raise RuntimeError(f"{API_KEY_ENV} is not set; ElevenLabsTTS needs it (or an injected transport)")
        self._key = key or ""
        self.model = model or os.environ.get(MODEL_ENV) or DEFAULT_MODEL
        mapping = dict(voices) if voices is not None else parse_voices(os.environ.get(VOICES_ENV))
        self.voices = {name: _check_voice_id(vid) for name, vid in mapping.items()}
        self.default_voice_id = _check_voice_id(os.environ.get(DEFAULT_VOICE_ENV) or DEFAULT_VOICE_ID)
        self.max_retries = max_retries
        self._sleep = sleep
        self.timeout = httpx.Timeout(float(timeout), connect=min(CONNECT_TIMEOUT_S, float(timeout)))
        headers = {"xi-api-key": key} if key else {}
        self._client = httpx.Client(base_url=BASE_URL, headers=headers, timeout=self.timeout, transport=transport)

    def __repr__(self) -> str:  # never show the key
        return f"ElevenLabsTTS(model={self.model!r}, voices={sorted(self.voices)!r})"

    def close(self) -> None:
        self._client.close()

    def voice_id(self, voice: str | None) -> str:
        """The ElevenLabs voice id for a placeholder voice name (default when None or unmapped)."""
        if voice is not None and voice in self.voices:
            return self.voices[voice]
        return self.default_voice_id

    def synthesize(self, text: str, *, voice: str | None = None) -> Speech:
        if not text.strip():
            return Speech(audio=_wav_bytes(b"", SAMPLE_RATE), sample_rate=SAMPLE_RATE, duration=0.0, words=[])
        path = f"/v1/text-to-speech/{self.voice_id(voice)}/with-timestamps"
        response = self._post(path, params={"output_format": OUTPUT_FORMAT}, json={"text": text, "model_id": self.model})
        if response.status_code >= 400:
            raise RuntimeError(
                self._scrub(f"ElevenLabs text-to-speech returned HTTP {response.status_code}{self._detail(response)}")
            )
        try:
            payload: dict[str, Any] = response.json()
            pcm = base64.b64decode(payload["audio_base64"], validate=True)
        except Exception:  # noqa: BLE001
            raise RuntimeError("ElevenLabs returned no decodable audio") from None
        if len(pcm) % 2:
            pcm = pcm[:-1]
        alignment = payload.get("alignment") or payload.get("normalized_alignment") or {}
        try:
            words = word_timings(
                alignment.get("characters") or [], alignment.get("character_start_times_seconds") or []
            )
        except (ValueError, TypeError, AttributeError) as exc:
            raise RuntimeError(f"ElevenLabs returned a malformed alignment: {exc}") from None
        frames = len(pcm) // 2
        return Speech(
            audio=_wav_bytes(pcm, SAMPLE_RATE),
            sample_rate=SAMPLE_RATE,
            duration=frames / SAMPLE_RATE,
            words=words,
        )

    # ------------------------------------------------------------------ internals

    def _post(self, path: str, **kwargs: Any) -> httpx.Response:
        """POST with the bounded retry policy of the module docstring; an error status is returned."""
        attempt = 0
        while True:
            try:
                response = self._client.post(path, **kwargs)
            except httpx.HTTPError as exc:
                if attempt < self.max_retries and retryable_error(exc):
                    self._sleep(backoff_s(attempt, None))
                    attempt += 1
                    continue
                raise RuntimeError(self._scrub(f"ElevenLabs request failed: {type(exc).__name__}")) from None
            if response.status_code in RETRY_STATUSES and attempt < self.max_retries:
                self._sleep(backoff_s(attempt, response))
                attempt += 1
                continue
            return response

    def _scrub(self, text: str) -> str:
        return text.replace(self._key, "[redacted]") if self._key else text

    def _detail(self, response: httpx.Response) -> str:
        try:
            detail = response.json().get("detail")
        except Exception:  # noqa: BLE001
            return ""
        message = detail.get("message") if isinstance(detail, dict) else detail
        if not isinstance(message, str) or not message.strip():
            return ""
        return f": {self._scrub(message.strip())[:_ERROR_DETAIL_CHARS]}"


__all__ = [
    "DEFAULT_MAX_RETRIES",
    "DEFAULT_MODEL",
    "DEFAULT_TIMEOUT_S",
    "DEFAULT_VOICE_ID",
    "PLACEHOLDER_VOICES",
    "ElevenLabsTTS",
    "parse_voices",
    "word_timings",
]
