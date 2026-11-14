"""Offline contract tests: never contact OpenAI or consume credentials."""

import asyncio
from email.parser import BytesParser
from email.policy import default
import json

import httpx
import pytest

from channels.transcription import (
    DEFAULT_MODEL, ENDPOINT, MAX_AUDIO_BYTES, MAX_RESPONSE_BYTES, MIME_FILENAMES,
    OpenAITranscriber, TranscriptionConfig, TranscriptionError,
)


def config(**kwargs):
    return TranscriptionConfig(api_key="test-only-key", enabled=True, **kwargs)


def run(handler, audio=b"test-audio", content_type="audio/wav", **kwargs):
    adapter = OpenAITranscriber(config(), transport=httpx.MockTransport(handler), **kwargs)
    return asyncio.run(adapter.transcribe(audio, content_type=content_type))


@pytest.mark.parametrize("env,code", [
    ({}, "disabled"),
    ({"OPENAI_API_KEY": "test-key"}, "disabled"),
    ({"STITCH_CHANNELS_TRANSCRIPTION": "true"}, "disabled"),
    ({"STITCH_CHANNELS_TRANSCRIPTION": "1"}, "configuration"),
    ({"STITCH_CHANNELS_TRANSCRIPTION": "1", "OPENAI_API_KEY": " test-key"}, "configuration"),
    ({"STITCH_CHANNELS_TRANSCRIPTION": "1", "OPENAI_API_KEY": "test-key\n"}, "configuration"),
    ({"STITCH_CHANNELS_TRANSCRIPTION": "1", "OPENAI_API_KEY": "test-key", "CHANNELS_TRANSCRIPTION_MODEL": "whisper-1"}, "model"),
])
def test_explicit_opt_in_and_configuration(env, code):
    with pytest.raises(TranscriptionError) as error:
        TranscriptionConfig.from_env(env)
    assert error.value.code == code
    assert not error.value.uncertain


def test_user_selected_default_and_explicit_migration():
    current = TranscriptionConfig.from_env({"STITCH_CHANNELS_TRANSCRIPTION": "1", "OPENAI_API_KEY": "test-key"})
    assert current.model == DEFAULT_MODEL == "gpt-4o-mini-transcribe"
    assert "test-key" not in repr(current)
    assert config(model="gpt-transcribe").model == "gpt-transcribe"
    with pytest.raises(TranscriptionError):
        TranscriptionConfig(api_key="test-key")


@pytest.mark.parametrize("mime,filename", MIME_FILENAMES.items())
def test_fixed_endpoint_and_multipart_contract(mime, filename):
    async def handler(request):
        assert request.method == "POST"
        assert str(request.url) == ENDPOINT
        assert request.headers["authorization"] == "Bearer test-only-key"
        assert request.headers["accept-encoding"] == "identity"
        assert request.extensions["timeout"] == {"connect": 10, "read": 30, "write": 30, "pool": 5}
        body = await request.aread()
        message = BytesParser(policy=default).parsebytes(
            f'Content-Type: {request.headers["content-type"]}\r\n\r\n'.encode() + body
        )
        parts = {part.get_param("name", header="content-disposition"): part for part in message.iter_parts()}
        assert set(parts) == {"file", "model", "response_format"}
        assert parts["file"].get_filename() == filename
        assert parts["file"].get_content_type() == mime
        assert parts["file"].get_payload(decode=True) == b"test-audio"
        assert parts["model"].get_payload(decode=True) == DEFAULT_MODEL.encode()
        assert parts["response_format"].get_payload(decode=True) == b"json"
        return httpx.Response(200, json={"text": "  A family story.\n"})
    assert run(handler, content_type=mime) == "  A family story.\n"


@pytest.mark.parametrize("audio,mime,code", [
    (b"", "audio/wav", "audio_size"),
    (b"x" * (MAX_AUDIO_BYTES + 1), "audio/wav", "audio_size"),
    ("/tmp/audio.wav", "audio/wav", "audio_size"),
    (bytearray(b"audio"), "audio/wav", "audio_size"),
    (b"audio", "https://example.com/audio.wav", "audio_format"),
    (b"audio", "audio/wav; filename=secret", "audio_format"),
])
def test_invalid_input_never_sends(audio, mime, code):
    def handler(request):
        pytest.fail("invalid input reached transport")
    with pytest.raises(TranscriptionError) as error:
        run(handler, audio, mime)
    assert error.value.code == code


def test_exact_audio_limit_accepted():
    def handler(request):
        return httpx.Response(200, json={"text": "Accepted"})
    assert run(handler, b"a" * MAX_AUDIO_BYTES) == "Accepted"


@pytest.mark.parametrize("status", [302, 400, 401, 429, 500, 503])
def test_rejection_is_generic_and_never_retried_or_redirected(status):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(status, content=b"private-provider-body test-only-key", headers={"location": "https://evil.invalid"})
    with pytest.raises(TranscriptionError) as error:
        run(handler)
    assert len(calls) == 1
    assert "private" not in str(error.value) and "test-only-key" not in str(error.value)
    assert error.value.uncertain == (status >= 500)


@pytest.mark.parametrize("body", [
    b"not json", b"\xff", b"[]", b'{"text":null}', b'{"text":123}',
    b'{"text":"   "}', b'{"text":"\\ufeff"}', b'{"text":"\\ud800"}',
    json.dumps({"text": "é" * 4001}).encode(),
])
def test_invalid_transcripts_are_bounded_and_generic(body):
    with pytest.raises(TranscriptionError) as error:
        run(lambda request: httpx.Response(200, content=body, headers={"content-type": "application/json"}))
    assert error.value.code == "transcript"
    assert error.value.uncertain


class TrackingStream(httpx.AsyncByteStream):
    def __init__(self, chunks, *, delay=0):
        self.chunks = chunks
        self.delay = delay
        self.closed = False
        self.reads = 0

    async def __aiter__(self):
        for chunk in self.chunks:
            self.reads += 1
            if self.delay:
                await asyncio.sleep(self.delay)
            yield chunk

    async def aclose(self):
        self.closed = True


def test_response_limit_stops_reading_and_closes():
    stream = TrackingStream([b"x" * 16384] * 10)
    with pytest.raises(TranscriptionError) as error:
        run(lambda request: httpx.Response(200, stream=stream, headers={"content-type": "application/json"}))
    assert error.value.code == "response_size"
    assert stream.closed and stream.reads == MAX_RESPONSE_BYTES // 16384 + 1


def test_exact_response_and_transcript_limits():
    text = "é" * 4000
    body = json.dumps({"text": text}, ensure_ascii=False).encode()
    body += b" " * (MAX_RESPONSE_BYTES - len(body))
    assert run(lambda request: httpx.Response(200, content=body, headers={"content-type": "application/json"})) == text


@pytest.mark.parametrize("headers,code", [
    ({"content-type": "text/html"}, "response_format"),
    ({"content-type": "application/json", "content-encoding": "gzip"}, "response_encoding"),
])
def test_response_metadata_rejected_before_read(headers, code):
    stream = TrackingStream([b"private"])
    with pytest.raises(TranscriptionError) as error:
        run(lambda request: httpx.Response(200, stream=stream, headers=headers))
    assert error.value.code == code
    assert stream.reads == 0 and stream.closed


@pytest.mark.parametrize("exception,code", [(httpx.ReadTimeout, "timeout"), (httpx.ConnectError, "transport")])
def test_transport_errors_never_retry(exception, code):
    calls = []
    def handler(request):
        calls.append(request)
        raise exception("private-provider-data")
    with pytest.raises(TranscriptionError) as error:
        run(handler)
    assert len(calls) == 1 and error.value.code == code and error.value.uncertain
    assert "private" not in str(error.value)


def test_total_deadline_bounds_slow_stream_and_closes():
    stream = TrackingStream([b'{"text":"ok"}'], delay=1)
    with pytest.raises(TranscriptionError) as error:
        run(lambda request: httpx.Response(200, stream=stream, headers={"content-type": "application/json"}), total_timeout_s=0.01)
    assert error.value.code == "timeout" and error.value.uncertain
    assert stream.closed and stream.reads == 1


def test_caller_cancellation_propagates():
    async def handler(request):
        raise asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        run(handler)


def test_deadline_cancels_pending_request_without_retry():
    calls = []
    cancelled = []
    async def handler(request):
        calls.append(request)
        try:
            await asyncio.sleep(1)
        except asyncio.CancelledError:
            cancelled.append(True)
            raise
        return httpx.Response(200, json={"text": "late"})
    with pytest.raises(TranscriptionError) as error:
        run(handler, total_timeout_s=0.01)
    assert error.value.code == "timeout" and error.value.uncertain
    assert len(calls) == 1 and cancelled == [True]


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan"), True, 121])
def test_invalid_deadline(timeout):
    with pytest.raises(TranscriptionError):
        OpenAITranscriber(config(), total_timeout_s=timeout)
