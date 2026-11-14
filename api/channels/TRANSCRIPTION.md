# Opt-in transcription adapter

This module is a bounded HTTP adapter, **not a working voice-note feature**. It
does not register a route, download attachments, check consent, save a memory,
or start a worker. Integration must reserve a durable inbox item and verify
current contact consent before calling it, and recheck consent before saving.
Uncertain results need reconciliation rather than automatic replay.

Server-only configuration (never expose these in frontend environment variables):

- `STITCH_CHANNELS_TRANSCRIPTION=1` explicitly enables configuration.
- `OPENAI_API_KEY` supplies the server credential. No dotenv files are loaded.
- `CHANNELS_TRANSCRIPTION_MODEL` optionally selects an allowed model. The default
  is the user-approved `gpt-4o-mini-transcribe`; the pinned
  `gpt-4o-mini-transcribe-2025-12-15` and deliberate migration target
  `gpt-transcribe` are also allowed. Other values fail closed.

OpenAI currently marks mini-transcribe deprecated with shutdown February 26,
2027. Plan an explicit migration before then; this adapter never silently changes
the selected model. See the official [speech-to-text guide](https://developers.openai.com/api/docs/guides/speech-to-text)
and [deprecations](https://developers.openai.com/api/docs/deprecations).

```python
from channels.transcription import OpenAITranscriber, TranscriptionConfig

transcriber = OpenAITranscriber(TranscriptionConfig.from_env())
# Only after caller-owned validation, consent and durable reservation:
text = await transcriber.transcribe(audio_bytes, content_type="audio/m4a")
```

Only this explicit call sends audio to OpenAI. Imports and configuration do not
make requests. The adapter accepts immutable bytes, never filenames or URLs.
The caller must validate actual media bytes: an allowlisted MIME does not prove
the file is genuine audio. Accepted MIME types map to generated `voice.*`
filenames: MPEG/MP3/MPGA, MP4/M4A, WAV, WebM, Ogg and FLAC (including the listed
`audio/x-m4a`, `audio/x-wav`, `audio/x-flac` aliases in `MIME_FILENAMES`).

Each call makes one multipart POST to the fixed HTTPS transcription endpoint,
with `file`, `model`, and `response_format=json`. No redirects, environment
proxies, automatic retries, or arbitrary endpoint overrides are enabled.
Limits: nonempty audio at most 20 MiB; response at most 64 KiB; nonblank transcript
at most 8,000 UTF-8 bytes. Transcript whitespace is preserved. Encoded/compressed
responses are rejected. HTTP timeouts are connect 10 s, read/write 30 s, pool 5 s,
under a total 60 s deadline (constructor override must be positive and at most
120 s). Cancellation propagates to the caller.

`TranscriptionError` exposes a generic `code` and `uncertain` flag without raw
provider bodies, credentials or audio. Timeout, transport, server failures and
invalid success responses are uncertain: provider processing/billing may have
occurred. The adapter never retries, including rate limits. Caller cancellation
can also leave an uncertain provider result. Do not log request objects or
provider credentials in caller code.

The existing API environment supplies `httpx`; no dependencies are added here.
Run offline tests from `api/`: `uv run pytest tests/channels/test_transcription.py`.
Tests inject `httpx.MockTransport`, use synthetic bytes and never call a live
provider. Real voice ingestion, durable job/outbox integration, audio container
validation and consent race handling remain future integration work.
