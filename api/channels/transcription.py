"""Opt-in, bounded file transcription. No routing, consent, storage or model calls on import."""

import asyncio
from dataclasses import dataclass, field
import json
import math
import os
from typing import Mapping

import httpx

from channels.models import WHITESPACE

ENDPOINT = "https://api.openai.com/v1/audio/transcriptions"
DEFAULT_MODEL = "gpt-4o-mini-transcribe"
ALLOWED_MODELS = frozenset({DEFAULT_MODEL, "gpt-4o-mini-transcribe-2025-12-15", "gpt-transcribe"})
MAX_AUDIO_BYTES = 20 * 1024 * 1024
MAX_RESPONSE_BYTES = 64 * 1024
MAX_TRANSCRIPT_BYTES = 8000
MIME_FILENAMES = {
    "audio/mpeg": "voice.mp3",
    "audio/mp3": "voice.mp3",
    "audio/mpga": "voice.mpga",
    "audio/mp4": "voice.m4a",
    "audio/m4a": "voice.m4a",
    "audio/x-m4a": "voice.m4a",
    "audio/wav": "voice.wav",
    "audio/x-wav": "voice.wav",
    "audio/webm": "voice.webm",
    "audio/ogg": "voice.ogg",
    "audio/flac": "voice.flac",
    "audio/x-flac": "voice.flac",
}


class TranscriptionError(RuntimeError):
    """Stable code only: never includes credentials, provider bodies, or audio."""

    def __init__(self, code: str, *, uncertain: bool = False):
        super().__init__(f"Transcription unavailable ({code})")
        self.code = code
        self.uncertain = uncertain


@dataclass(frozen=True)
class TranscriptionConfig:
    api_key: str = field(repr=False)
    model: str = DEFAULT_MODEL
    enabled: bool = False

    def __post_init__(self) -> None:
        if self.enabled is not True:
            raise TranscriptionError("disabled")
        if not isinstance(self.api_key, str) or not 1 <= len(self.api_key) <= 4096 or any(
            not 33 <= ord(char) <= 126 for char in self.api_key
        ):
            raise TranscriptionError("configuration")
        if not isinstance(self.model, str) or self.model not in ALLOWED_MODELS:
            raise TranscriptionError("model")

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "TranscriptionConfig":
        """Read explicit process configuration; never read a dotenv file."""
        env = os.environ if environ is None else environ
        if env.get("STITCH_CHANNELS_TRANSCRIPTION") != "1":
            raise TranscriptionError("disabled")
        return cls(api_key=env.get("OPENAI_API_KEY", ""),
                   model=env.get("CHANNELS_TRANSCRIPTION_MODEL", DEFAULT_MODEL), enabled=True)


class OpenAITranscriber:
    """One request per call. Caller owns consent, durable reservation and reconciliation.

    A transport can be injected for offline tests. A request failure is never
    retried here: even a lost response can mean the provider processed the audio.
    """

    def __init__(self, config: TranscriptionConfig, *,
                 transport: httpx.AsyncBaseTransport | None = None,
                 total_timeout_s: float = 60.0):
        if not isinstance(config, TranscriptionConfig):
            raise TranscriptionError("configuration")
        if (
            isinstance(total_timeout_s, bool)
            or not isinstance(total_timeout_s, (int, float))
            or not math.isfinite(total_timeout_s)
            or not 0 < total_timeout_s <= 120
        ):
            raise TranscriptionError("configuration")
        self._config = config
        self._transport = transport
        self._total_timeout_s = total_timeout_s

    async def transcribe(self, audio: bytes, *, content_type: str) -> str:
        """Transcribe supplied bytes only; never opens a path or downloads a URL."""
        if not isinstance(audio, bytes) or not 1 <= len(audio) <= MAX_AUDIO_BYTES:
            raise TranscriptionError("audio_size")
        if not isinstance(content_type, str) or content_type not in MIME_FILENAMES:
            raise TranscriptionError("audio_format")
        try:
            async with asyncio.timeout(self._total_timeout_s):
                # No proxy/environment configuration, cookies, redirects or retry transport.
                async with httpx.AsyncClient(
                    transport=self._transport, trust_env=False, follow_redirects=False,
                    timeout=httpx.Timeout(connect=10, read=30, write=30, pool=5),
                    headers={"Authorization": f"Bearer {self._config.api_key}",
                             "Accept": "application/json", "Accept-Encoding": "identity"},
                ) as client:
                    async with client.stream(
                        "POST", ENDPOINT,
                        data={"model": self._config.model, "response_format": "json"},
                        files={"file": (MIME_FILENAMES[content_type], audio, content_type)},
                    ) as response:
                        if not 200 <= response.status_code < 300:
                            code = "rate_limited" if response.status_code == 429 else "provider_rejected"
                            raise TranscriptionError(code, uncertain=response.status_code >= 500)
                        if response.headers.get("content-encoding", "identity").lower() != "identity":
                            raise TranscriptionError("response_encoding", uncertain=True)
                        if response.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
                            raise TranscriptionError("response_format", uncertain=True)
                        raw = bytearray()
                        async for chunk in response.aiter_bytes(chunk_size=16384):
                            if len(raw) + len(chunk) > MAX_RESPONSE_BYTES:
                                raise TranscriptionError("response_size", uncertain=True)
                            raw.extend(chunk)
                        return _transcript(bytes(raw))
        except TranscriptionError:
            raise
        except (TimeoutError, httpx.TimeoutException):
            raise TranscriptionError("timeout", uncertain=True) from None
        except httpx.HTTPError:
            raise TranscriptionError("transport", uncertain=True) from None


def _transcript(raw: bytes) -> str:
    try:
        data = json.loads(raw.decode("utf-8"))
        text = data.get("text") if isinstance(data, dict) else None
        if not isinstance(text, str) or not text.strip(WHITESPACE) or len(text.encode("utf-8")) > MAX_TRANSCRIPT_BYTES:
            raise ValueError("invalid transcript")
    except (ValueError, UnicodeError, RecursionError):
        raise TranscriptionError("transcript", uncertain=True) from None
    return text
