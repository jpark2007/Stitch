# Opt-in transcription adapter

This module is a bounded HTTP adapter. The separate signed voice inbox and manual
worker now handle receipt storage, consent, reservations and draft integration;
this adapter itself registers no route and starts no worker. Only invoke it after
reserving a durable inbox item and checking current consent. Uncertain results
need reconciliation rather than automatic replay.

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
provider. The inbox/worker tests separately cover signed audio storage, durable
transcription state, outbox behavior and consent races. Actual provider accounts,
real phone delivery, recording playback and production Mongo crash recovery still
need qualification.

## Native CAF conversion before transcription

The Stable iMessage SDK labels native Apple voice notes `audio/x-caf`. The bridge
preserves that MIME and the original signed bytes. OpenAI's adapter allowlist is
unchanged: CAF must never be mislabeled and uploaded directly as WAV.

`channels.audio.convert_caf(bytes)` validates a bounded CAF container, then uses
macOS `/usr/bin/afconvert` when available, or an installed `ffmpeg` on other POSIX
hosts. There is no automatic install or Python dependency change. Windows and
hosts without either converter fail with `missing_converter`; the worker blocks
for operator review before any transcription request. Only CAF takes this path.

Conversion happens after the worker's durable transcription reservation and
consent check. It uses fixed argument arrays, no shell, private temporary input
and output files, a 15-second wall deadline and 15-second child CPU limit. A fresh
isolated Python wrapper sets a 20 MiB output-file resource limit before execing
the converter under the same owned PID; this avoids `preexec_fn` in API threads.
The child environment contains no provider/shared-secret variables, stderr is
discarded, and ffmpeg is restricted to local file input. Only this owned process
is killed on timeout/cancellation, then reaped before temporary-file cleanup.

No duration-truncating converter option is used. Output must be complete PCM WAV,
mono, 16-bit, 16 kHz, at most 20 MiB and at most five minutes according to its
verified decoded frame count. Oversized, overlong, malformed or failed conversion
is rejected rather than silently clipped. This five-minute bound applies only to
converted CAF; other compressed formats still have no proven duration/billing cap.
Original family-scoped CAF media and its audioId stay unchanged. The worker
rechecks consent after decoding, then passes only verified WAV bytes with
`content_type="audio/wav"` to transcription. Converted files are not persisted.

Conversion failures leave the receipt held for manual reconciliation and set the
worker reason to `audio_conversion_<stable-code>`. Those conversion-specific
failures made no transcription request. A subsequent provider failure may still
be uncertain and potentially billed; the adapter never retries it automatically.
Raw decoder output, filenames, audio and credentials are not included in errors.

`tests/channels/test_audio.py` generates synthetic CAF using the real local
`afconvert`, checks its decoded WAV and worker integration, and exercises missing
tools, invalid inputs, output limits, timeout, cancellation, cleanup and STOP.
The real macOS round-trip tests skip elsewhere; ffmpeg support still needs a real
Linux deployment check. No actual voice recording or provider upload is used.
