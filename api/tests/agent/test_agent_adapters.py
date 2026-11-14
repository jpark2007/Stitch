"""Production adapters (agent/adapters), all offline: fake clients and httpx.MockTransport.

Live tests at the bottom run only when their key env var is set.
"""

from __future__ import annotations

import base64
import inspect
import io
import json
import os
import struct
import wave
from collections.abc import Callable
from types import SimpleNamespace

import anthropic
import anthropic._base_client as sdk_base
import httpx
import pytest
from PIL import Image, ImageDraw

from agent.adapters import (
    AlphaAwareRemover,
    AnthropicVision,
    ElevenLabsTTS,
    OpenAIImageModel,
)
from agent.adapters import images as images_mod
from agent.adapters import tts as tts_mod
from agent.adapters import vision as vision_mod
from agent.tools import art, narration, photos
from agent.tools.art import ArtService, BackgroundRemover, ImageModel, analyze_cutout
from agent.tools.narration import TTS, Speech, _read_wav
from agent.tools.photos import PHOTO_PROMPT, VisionModel
from screenplay.schema import WordTiming

SECRET = "sk-test-SECRET-key-123"

ENV_VARS = (
    "ANTHROPIC_API_KEY",
    "STITCH_VISION_MODEL",
    "IMAGE_API_KEY",
    "IMAGE_API_URL",
    "IMAGE_MODEL",
    "ELEVENLABS_API_KEY",
    "ELEVENLABS_MODEL",
    "ELEVENLABS_VOICES",
    "ELEVENLABS_DEFAULT_VOICE",
)

LIVE = {name: os.environ.get(name) for name in ("ANTHROPIC_API_KEY", "IMAGE_API_KEY", "ELEVENLABS_API_KEY")}

sdk_httpx = getattr(sdk_base, "httpx2", None) or sdk_base.httpx
"""The anthropic SDK speaks httpx2 (a separate package), so its fake transport is httpx2's."""


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def make_image(
    size: tuple[int, int],
    color: tuple[int, ...] = (200, 60, 40),
    *,
    fmt: str = "PNG",
    mode: str = "RGB",
) -> bytes:
    im = Image.new(mode, size, color)
    buf = io.BytesIO()
    im.save(buf, format=fmt)
    return buf.getvalue()


def open_image(data: bytes) -> Image.Image:
    im = Image.open(io.BytesIO(data))
    im.load()
    return im


# --------------------------------------------------------------------------- protocols


def _same_signature(impl: object, proto: type, name: str) -> None:
    got = inspect.signature(getattr(impl, name))
    want = inspect.signature(getattr(proto, name))
    want_params = [p for p in want.parameters.values() if p.name != "self"]
    assert [(p.name, p.kind) for p in got.parameters.values()] == [(p.name, p.kind) for p in want_params]


def test_adapters_satisfy_the_seams(clean_env: pytest.MonkeyPatch) -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(500))
    image_model = OpenAIImageModel(api_key=SECRET, transport=transport)
    remover = AlphaAwareRemover()
    tts = ElevenLabsTTS(api_key=SECRET, transport=transport)
    vision = AnthropicVision(client=SimpleNamespace(messages=None))

    # runtime_checkable protocols
    assert isinstance(image_model, ImageModel)
    assert isinstance(remover, BackgroundRemover)
    # VisionModel and TTS are plain Protocols: check the method shapes instead.
    _same_signature(vision, VisionModel, "describe")
    _same_signature(tts, TTS, "synthesize")
    _same_signature(image_model, ImageModel, "generate")
    _same_signature(remover, BackgroundRemover, "remove")
    # Static typing: these assignments are what wave-2 wiring will do.
    _v: photos.VisionModel = vision
    _t: narration.TTS = tts
    _i: art.ImageModel = image_model
    _r: art.BackgroundRemover = remover
    assert ArtService(image_model, remover).enabled


# --------------------------------------------------------------------------- missing keys


@pytest.mark.parametrize(
    ("factory", "env_var"),
    [
        (lambda: AnthropicVision(), "ANTHROPIC_API_KEY"),
        (lambda: OpenAIImageModel(), "IMAGE_API_KEY"),
        (lambda: ElevenLabsTTS(), "ELEVENLABS_API_KEY"),
    ],
)
def test_missing_key_raises_naming_the_env_var(clean_env: pytest.MonkeyPatch, factory, env_var: str) -> None:
    with pytest.raises(RuntimeError, match=env_var):
        factory()


def test_keys_are_read_from_env(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("IMAGE_API_KEY", SECRET)
    clean_env.setenv("ELEVENLABS_API_KEY", SECRET)
    clean_env.setenv("ANTHROPIC_API_KEY", SECRET)
    OpenAIImageModel()
    ElevenLabsTTS()
    AnthropicVision()  # builds a real SDK client, but makes no request
    assert SECRET not in repr(OpenAIImageModel()) + repr(ElevenLabsTTS())


# --------------------------------------------------------------------------- vision


class FakeMessages:
    def __init__(self, response: object) -> None:
        self.response = response
        self.calls: list[dict] = []

    def create(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        return self.response


def fake_client(response: object) -> SimpleNamespace:
    return SimpleNamespace(messages=FakeMessages(response))


def test_vision_request_shape_media_types_and_downscale(clean_env: pytest.MonkeyPatch) -> None:
    response = SimpleNamespace(
        content=[
            SimpleNamespace(type="text", text="  A kitchen in the 1970s."),
            SimpleNamespace(type="thinking", thinking="ignored"),
            SimpleNamespace(type="text", text="A red kettle.  "),
        ]
    )
    client = fake_client(response)
    vision = AnthropicVision(client=client)
    assert vision.model == "claude-opus-5-5"  # agent.studio.DEFAULT_MODEL

    small_png = make_image((640, 480))
    big_jpeg = make_image((3000, 2000), fmt="JPEG")
    gif = make_image((100, 50), fmt="GIF", mode="P", color=3)
    bmp = make_image((80, 60), fmt="BMP")
    big_rgba = make_image((2000, 3000), (10, 20, 30, 128), mode="RGBA")

    text = vision.describe([small_png, big_jpeg, gif, bmp, big_rgba], PHOTO_PROMPT)
    assert text == "A kitchen in the 1970s.\nA red kettle."

    (call,) = client.messages.calls
    assert call["model"] == "claude-opus-5-5"
    assert call["max_tokens"] == 1500
    (message,) = call["messages"]
    assert message["role"] == "user"
    blocks = message["content"]
    assert [b["type"] for b in blocks] == ["image"] * 5 + ["text"]
    assert blocks[-1] == {"type": "text", "text": PHOTO_PROMPT}
    sources = [b["source"] for b in blocks[:-1]]
    assert all(s["type"] == "base64" for s in sources)
    assert [s["media_type"] for s in sources] == [
        "image/png",
        "image/jpeg",
        "image/gif",
        "image/png",  # BMP is not sendable: re-encoded as PNG
        "image/png",  # has alpha: stays PNG after downscaling
    ]
    decoded = [base64.b64decode(s["data"]) for s in sources]
    assert decoded[0] == small_png  # small, sendable: sent as is
    assert decoded[2] == gif
    assert open_image(decoded[1]).size == (1568, 1045)  # 3000x2000 -> long edge 1568
    assert open_image(decoded[1]).format == "JPEG"
    assert open_image(decoded[3]).format == "PNG"
    big = open_image(decoded[4])
    assert big.size == (1045, 1568)
    assert big.mode == "RGBA"


def test_vision_model_from_env_and_dict_response(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("STITCH_VISION_MODEL", "claude-test-vision")
    client = fake_client({"content": [{"type": "text", "text": " ok "}]})
    vision = AnthropicVision(client=client, max_tokens=99)
    assert vision.describe([make_image((10, 10))], "describe") == "ok"
    assert client.messages.calls[0]["model"] == "claude-test-vision"
    assert client.messages.calls[0]["max_tokens"] == 99
    assert AnthropicVision(client=client, model="explicit").model == "explicit"


def test_vision_applies_exif_orientation(clean_env: pytest.MonkeyPatch) -> None:
    im = Image.new("RGB", (40, 20), (0, 0, 255))
    exif = Image.Exif()
    exif[0x0112] = 6  # rotate 90 degrees clockwise to display
    buf = io.BytesIO()
    im.save(buf, format="JPEG", exif=exif)
    data, media_type = vision_mod.prepare_image(buf.getvalue())
    assert media_type == "image/jpeg"
    assert open_image(data).size == (20, 40)


def test_vision_rejects_empty_and_unreadable(clean_env: pytest.MonkeyPatch) -> None:
    client = fake_client({"content": []})
    vision = AnthropicVision(client=client)
    with pytest.raises(ValueError):
        vision.describe([], PHOTO_PROMPT)
    with pytest.raises(ValueError):
        vision.describe([b"not an image"], PHOTO_PROMPT)
    assert client.messages.calls == []


def test_vision_errors_are_contained_by_describe_photo(clean_env: pytest.MonkeyPatch) -> None:
    class Boom:
        def create(self, **kwargs: object) -> object:
            raise ConnectionError("down")

    vision = AnthropicVision(client=SimpleNamespace(messages=Boom()))
    png = make_image((10, 10))
    (tool,) = photos.make_photo_tools(vision, {"p1": (png, "image/png")}.get, ["p1"])
    assert tool.invoke({"photo_id": "p1"}) == "could not describe photo p1: ConnectionError"


# --------------------------------------------------------------------------- images


class ImageAPI:
    """MockTransport handler for the images API; records requests."""

    def __init__(self, *, status: int = 200, body: dict | None = None, image_size=(1024, 1024), mode="RGB") -> None:
        self.requests: list[httpx.Request] = []
        self.status = status
        self.body = body
        color = (30, 160, 90, 0) if mode == "RGBA" else (30, 160, 90)
        im = Image.new(mode, image_size, color)
        if mode == "RGBA":
            ImageDraw.Draw(im).ellipse((100, 100, image_size[0] - 100, image_size[1] - 100), fill=(200, 40, 40, 255))
        buf = io.BytesIO()
        im.save(buf, format="PNG")
        self.b64 = base64.b64encode(buf.getvalue()).decode()

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.status != 200:
            return httpx.Response(self.status, json=self.body or {"error": {"message": "nope"}})
        return httpx.Response(200, json=self.body or {"data": [{"b64_json": self.b64}]})


def no_sleep(seconds: float) -> None:
    """Retry backoff in tests: never actually wait."""


def image_model(api: ImageAPI, **kwargs) -> OpenAIImageModel:
    kwargs.setdefault("sleep", no_sleep)
    return OpenAIImageModel(api_key=SECRET, transport=httpx.MockTransport(api), **kwargs)


@pytest.mark.parametrize(
    ("size", "expected"),
    [
        ((1024, 1024), (1024, 1024)),
        ((1200, 1000), (1024, 1024)),
        ((2048, 1024), (1536, 1024)),
        ((4096, 1024), (1536, 1024)),
        ((800, 1200), (1024, 1536)),
        ((300, 1000), (1024, 1536)),
    ],
)
def test_closest_size(size, expected) -> None:
    assert images_mod.closest_size(size) == expected


def test_images_generation_without_references(clean_env: pytest.MonkeyPatch) -> None:
    api = ImageAPI(image_size=(1536, 1024))
    model = image_model(api)
    out = model.generate("a red kettle", size=(2048, 1024))

    (request,) = api.requests
    assert request.method == "POST"
    assert str(request.url) == "https://api.openai.com/v1/images/generations"
    assert request.headers["authorization"] == f"Bearer {SECRET}"
    assert json.loads(request.content) == {"model": "gpt-image-1", "prompt": "a red kettle", "size": "1536x1024", "n": 1}
    im = open_image(out)
    assert im.format == "PNG"
    assert im.size == (2048, 1024)


def test_images_edits_with_references_and_alpha(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("IMAGE_API_URL", "https://images.example.test/v1/")
    clean_env.setenv("IMAGE_MODEL", "some-image-model")
    api = ImageAPI(image_size=(1024, 1536), mode="RGBA")
    model = image_model(api)
    refs = [make_image((64, 64)), make_image((32, 32), fmt="JPEG"), make_image((16, 16), fmt="BMP")]
    out = model.generate("the same kettle, lid open", reference_images=refs, size=(500, 700))

    (request,) = api.requests
    assert str(request.url) == "https://images.example.test/v1/images/edits"
    assert request.headers["content-type"].startswith("multipart/form-data")
    body = request.content
    assert body.count(b'name="image[]"') == 3
    assert b'filename="reference_0.png"' in body
    assert b'filename="reference_1.jpg"' in body
    assert b'filename="reference_2.png"' in body  # BMP re-encoded as PNG
    assert b"Content-Type: image/jpeg" in body
    for field, value in (("model", b"some-image-model"), ("size", b"1024x1536"), ("n", b"1")):
        assert f'name="{field}"'.encode() in body
        assert value in body
    assert b"the same kettle, lid open" in body

    im = open_image(out)
    assert im.size == (500, 700)
    assert im.mode == "RGBA"
    assert im.getpixel((0, 0))[3] == 0  # transparent corner kept
    assert im.getpixel((250, 350))[3] == 255


def test_images_url_result(clean_env: pytest.MonkeyPatch) -> None:
    png = make_image((1024, 1024), (1, 2, 3))

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "cdn.example.test":
            assert "authorization" not in request.headers  # never leak the key to another host
            return httpx.Response(200, content=png)
        return httpx.Response(200, json={"data": [{"url": "https://cdn.example.test/a.png"}]})

    model = OpenAIImageModel(api_key=SECRET, transport=httpx.MockTransport(handler))
    assert open_image(model.generate("x", size=(64, 64))).size == (64, 64)


@pytest.mark.parametrize("status", [400, 401, 429, 500])
def test_images_http_error_has_status_and_no_key(clean_env: pytest.MonkeyPatch, status: int) -> None:
    # A hostile/echoing error body that contains the key must still not leak it.
    api = ImageAPI(status=status, body={"error": {"message": f"Incorrect API key provided: {SECRET}"}})
    with pytest.raises(RuntimeError) as info:
        image_model(api).generate("x")
    message = str(info.value)
    assert str(status) in message
    assert SECRET not in message
    assert SECRET not in repr(info.value)


def test_images_bad_payload_and_network_error(clean_env: pytest.MonkeyPatch) -> None:
    with pytest.raises(RuntimeError, match="no image data"):
        image_model(ImageAPI(body={"data": []})).generate("x")

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"cannot connect with {request.headers['authorization']}")

    model = OpenAIImageModel(api_key=SECRET, transport=httpx.MockTransport(down), sleep=no_sleep)
    with pytest.raises(RuntimeError) as info:
        model.generate("x")
    assert SECRET not in str(info.value)


def test_images_service_failure_falls_back_to_placeholder(clean_env: pytest.MonkeyPatch) -> None:
    service = ArtService(image_model(ImageAPI(status=500)), AlphaAwareRemover())
    assert service.make_cutout("x") is None


# --------------------------------------------------------------------------- tts

HELLO_TEXT = "Hello, world."
CHAR_STEP = 0.05
HELLO_PCM_FRAMES = 16_000 + 4_000  # 1.25 s


def hello_payload() -> dict:
    chars = list(HELLO_TEXT)
    starts = [round(i * CHAR_STEP, 3) for i in range(len(chars))]
    ends = [round((i + 1) * CHAR_STEP, 3) for i in range(len(chars))]
    pcm = b"".join(struct.pack("<h", (i % 200) - 100) for i in range(HELLO_PCM_FRAMES))
    return {
        "audio_base64": base64.b64encode(pcm).decode(),
        "alignment": {
            "characters": chars,
            "character_start_times_seconds": starts,
            "character_end_times_seconds": ends,
        },
        "normalized_alignment": {
            "characters": chars,
            "character_start_times_seconds": starts,
            "character_end_times_seconds": ends,
        },
    }


class TTSAPI:
    def __init__(self, *, status: int = 200, body: dict | None = None) -> None:
        self.status = status
        self.body = body
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.status != 200:
            return httpx.Response(self.status, json=self.body or {"detail": {"message": "bad"}})
        return httpx.Response(200, json=self.body or hello_payload())


def test_tts_hello_world(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("ELEVENLABS_VOICES", "voice_a=VoiceAAAA111, voice_b=VoiceBBBB222")
    api = TTSAPI()
    tts = ElevenLabsTTS(api_key=SECRET, transport=httpx.MockTransport(api))
    speech = tts.synthesize(HELLO_TEXT, voice="voice_b")

    (request,) = api.requests
    assert request.method == "POST"
    assert request.url.host == "api.elevenlabs.io"
    assert request.url.path == "/v1/text-to-speech/VoiceBBBB222/with-timestamps"
    assert request.url.params["output_format"] == "pcm_16000"
    assert request.headers["xi-api-key"] == SECRET
    assert json.loads(request.content) == {"text": HELLO_TEXT, "model_id": "eleven_multilingual_v2"}

    assert isinstance(speech, Speech)
    assert speech.sample_rate == 16_000
    assert speech.duration == 1.25
    # 'H' is character 0; 'w' is character 7 ("Hello, " is 7 characters).
    assert speech.words == [WordTiming(t=0.0, word="Hello"), WordTiming(t=0.35, word="world")]

    with wave.open(io.BytesIO(speech.audio), "rb") as w:
        assert w.getnchannels() == 1
        assert w.getsampwidth() == 2
        assert w.getframerate() == 16_000
        assert w.getnframes() == HELLO_PCM_FRAMES
        assert w.getnframes() / w.getframerate() == speech.duration
    assert speech.audio[:4] == b"RIFF" and speech.audio[8:12] == b"WAVE"
    frames, rate = _read_wav(speech.audio)  # narration's own reader accepts it
    assert rate == 16_000 and len(frames) == 2 * HELLO_PCM_FRAMES
    assert frames == base64.b64decode(hello_payload()["audio_base64"])


def test_tts_voice_mapping(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("ELEVENLABS_VOICES", "voice_a=VoiceAAAA111,voice_b=VoiceBBBB222")
    clean_env.setenv("ELEVENLABS_MODEL", "eleven_test_model")
    api = TTSAPI()
    tts = ElevenLabsTTS(api_key=SECRET, transport=httpx.MockTransport(api))
    for voice in ("voice_a", "voice_b", "voice_c", None, "../../evil"):
        tts.synthesize("Hi.", voice=voice)
    paths = [r.url.path.split("/")[3] for r in api.requests]
    default = tts_mod.DEFAULT_VOICE_ID
    assert paths == ["VoiceAAAA111", "VoiceBBBB222", default, default, default]
    assert json.loads(api.requests[0].content)["model_id"] == "eleven_test_model"

    clean_env.setenv("ELEVENLABS_DEFAULT_VOICE", "MyDefault99")
    assert ElevenLabsTTS(api_key=SECRET, transport=httpx.MockTransport(api)).voice_id(None) == "MyDefault99"
    # Explicit mapping wins over env.
    explicit = ElevenLabsTTS(api_key=SECRET, voices={"voice_c": "CCC"}, transport=httpx.MockTransport(api))
    assert explicit.voice_id("voice_c") == "CCC"
    assert explicit.voice_id("voice_a") == "MyDefault99"


@pytest.mark.parametrize("spec", ["voice_a", "voice_a=", "voice_a=bad/id"])
def test_tts_bad_voice_env(clean_env: pytest.MonkeyPatch, spec: str) -> None:
    clean_env.setenv("ELEVENLABS_VOICES", spec)
    with pytest.raises(ValueError):
        ElevenLabsTTS(api_key=SECRET, transport=httpx.MockTransport(TTSAPI()))


def test_word_timings_punctuation_like_split_words() -> None:
    text = "“Grandma’s … steps—” she said... ok !"
    chars = list(text)
    starts = [i / 10 for i in range(len(chars))]
    words = tts_mod.word_timings(chars, starts)
    assert [w.word for w in words] == [w for w, _ in narration._split_words(text)]
    # Each word's time is the time of its first kept character.
    assert words[0] == WordTiming(t=0.1, word="Grandma’s")
    assert words[1] == WordTiming(t=text.index("steps") / 10, word="steps")
    by_word = {w.word: w.t for w in words}
    assert by_word["she"] == text.index("she") / 10
    with pytest.raises(ValueError):
        tts_mod.word_timings(["a"], [])


def test_tts_blank_text_makes_no_request(clean_env: pytest.MonkeyPatch) -> None:
    api = TTSAPI()
    speech = ElevenLabsTTS(api_key=SECRET, transport=httpx.MockTransport(api)).synthesize("   ")
    assert api.requests == []
    assert speech.duration == 0.0 and speech.words == []


@pytest.mark.parametrize("status", [401, 422, 500])
def test_tts_http_error_has_status_and_no_key(clean_env: pytest.MonkeyPatch, status: int) -> None:
    api = TTSAPI(status=status, body={"detail": {"message": f"invalid api key {SECRET}"}})
    with pytest.raises(RuntimeError) as info:
        ElevenLabsTTS(api_key=SECRET, transport=httpx.MockTransport(api), sleep=no_sleep).synthesize("Hi.")
    assert str(status) in str(info.value)
    assert SECRET not in str(info.value)


def test_tts_bad_payload(clean_env: pytest.MonkeyPatch) -> None:
    api = TTSAPI(body={"alignment": {}})
    with pytest.raises(RuntimeError, match="no decodable audio"):
        ElevenLabsTTS(api_key=SECRET, transport=httpx.MockTransport(api)).synthesize("Hi.")


def test_tts_feeds_build_narration_track(clean_env: pytest.MonkeyPatch) -> None:
    """The adapter's clips mix through narration._mix like FakeTTS's do."""
    api = TTSAPI()
    tts = ElevenLabsTTS(api_key=SECRET, transport=httpx.MockTransport(api))
    placed = [(0.0, None, tts.synthesize(HELLO_TEXT)), (2.0, None, tts.synthesize(HELLO_TEXT))]
    mixed, rate = _read_wav(narration._mix(placed))  # type: ignore[arg-type]
    assert rate == 16_000
    assert len(mixed) == 2 * (2 * 16_000 + HELLO_PCM_FRAMES)


# --------------------------------------------------------------------------- cutout


def ring_image() -> bytes:
    """White canvas, a blue ring, a white center inside the ring."""
    im = Image.new("RGB", (200, 200), (255, 255, 255))
    draw = ImageDraw.Draw(im)
    draw.ellipse((40, 40, 160, 160), fill=(40, 70, 200))
    draw.ellipse((70, 70, 130, 130), fill=(255, 255, 255))
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def test_cutout_keeps_enclosed_white_opaque() -> None:
    out = open_image(AlphaAwareRemover().remove(ring_image()))
    assert out.mode == "RGBA"
    assert out.size == (200, 200)
    alpha = out.getchannel("A")
    # Outer white background: gone.
    for xy in [(0, 0), (199, 199), (10, 100), (100, 20), (190, 5)]:
        assert alpha.getpixel(xy) == 0, xy
    # Enclosed white center: kept, and still white.
    assert alpha.getpixel((100, 100)) == 255
    assert out.getpixel((100, 100))[:3] == (255, 255, 255)
    # The ring body: opaque.
    assert alpha.getpixel((55, 100)) == 255
    # The fake remover would have cleared the center; this one does not.
    fake = open_image(art.FakeBackgroundRemover().remove(ring_image()))
    assert fake.getchannel("A").getpixel((100, 100)) == 0


def test_cutout_feathers_edge_inward_only() -> None:
    out = open_image(AlphaAwareRemover().remove(ring_image()))
    alpha = out.getchannel("A")
    row = [alpha.getpixel((x, 100)) for x in range(30, 50)]
    # 0s, then a soft step, then 255s: monotonic, with at least one partial value.
    assert row == sorted(row)
    assert row[0] == 0 and row[-1] == 255
    assert any(0 < a < 255 for a in row)
    # No background pixel gets alpha (no white halo): every partial pixel is a ring pixel.
    for x in range(30, 50):
        a = alpha.getpixel((x, 100))
        if 0 < a < 255:
            assert out.getpixel((x, 100))[:3] == (40, 70, 200)
    unfeathered = open_image(AlphaAwareRemover(feather=0).remove(ring_image())).getchannel("A")
    assert set(unfeathered.tobytes()) == {0, 255}


def test_cutout_passes_through_real_transparency() -> None:
    im = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
    ImageDraw.Draw(im).rectangle((20, 20, 80, 80), fill=(250, 250, 250, 255))
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    data = buf.getvalue()
    assert AlphaAwareRemover().remove(data) == data  # byte for byte

    # An LA image with transparency comes back as an RGBA PNG with the same alpha.
    la = Image.new("LA", (100, 100), (0, 0))
    ImageDraw.Draw(la).rectangle((10, 10, 50, 50), fill=(255, 255))
    buf = io.BytesIO()
    la.save(buf, format="PNG")
    out = open_image(AlphaAwareRemover().remove(buf.getvalue()))
    assert out.mode == "RGBA" and out.format == "PNG"
    assert out.getchannel("A").tobytes() == la.getchannel("A").tobytes()


def test_cutout_opaque_rgba_is_processed() -> None:
    # Fully opaque alpha (below the 1% threshold) is not "real transparency".
    im = Image.new("RGBA", (100, 100), (255, 255, 255, 255))
    ImageDraw.Draw(im).rectangle((30, 30, 70, 70), fill=(200, 30, 30, 255))
    im.putpixel((0, 0), (255, 255, 255, 0))  # one stray transparent pixel: 0.01% < 1%
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    out = open_image(AlphaAwareRemover().remove(buf.getvalue()))
    assert out.getchannel("A").getpixel((5, 5)) == 0
    assert out.getchannel("A").getpixel((50, 50)) == 255


def test_cutout_with_art_service_crops_to_subject() -> None:
    class RingModel:
        def generate(self, prompt, *, reference_images=(), size=(1024, 1024)) -> bytes:
            return ring_image()

    cutout = ArtService(RingModel(), AlphaAwareRemover()).make_cutout("a ring")
    assert cutout is not None
    assert cutout.bbox == (40, 40, 161, 161)
    assert analyze_cutout(cutout.png).aspect == pytest.approx(1.0)


def test_cutout_threshold_validation() -> None:
    with pytest.raises(ValueError):
        AlphaAwareRemover(threshold=300)
    with pytest.raises(ValueError):
        AlphaAwareRemover(feather=-1)


# --------------------------------------------------------------------------- bounded timeouts and retries


def _timeout_of(request: httpx.Request) -> dict:
    """The timeouts httpx sent this request with (connect, read, write, pool)."""
    return dict(request.extensions["timeout"])


class Flaky:
    """MockTransport handler: answers each request with the next of `script`, then the last one forever.

    A script item is an int status (200 means a good body from `ok`), an httpx.Response, or an
    exception class to raise.
    """

    def __init__(self, script: list, ok: Callable[[], httpx.Response]) -> None:
        self.script = list(script)
        self.ok = ok
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        step = self.script[min(len(self.requests), len(self.script)) - 1]
        if isinstance(step, type) and issubclass(step, Exception):
            raise step("simulated", request=request)
        if isinstance(step, httpx.Response):
            return step
        if step == 200:
            return self.ok()
        return httpx.Response(step, json={"error": {"message": "busy"}, "detail": {"message": "busy"}})


def image_ok() -> httpx.Response:
    return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(make_image((32, 32))).decode()}]})


def tts_ok() -> httpx.Response:
    return httpx.Response(200, json=hello_payload())


def flaky_image_model(script: list, sleeps: list[float], **kwargs) -> tuple[OpenAIImageModel, Flaky]:
    api = Flaky(script, image_ok)
    model = OpenAIImageModel(api_key=SECRET, transport=httpx.MockTransport(api), sleep=sleeps.append, **kwargs)
    return model, api


def flaky_tts(script: list, sleeps: list[float], **kwargs) -> tuple[ElevenLabsTTS, Flaky]:
    api = Flaky(script, tts_ok)
    tts = ElevenLabsTTS(api_key=SECRET, transport=httpx.MockTransport(api), sleep=sleeps.append, **kwargs)
    return tts, api


def test_image_requests_carry_bounded_timeouts(clean_env: pytest.MonkeyPatch) -> None:
    model, api = flaky_image_model([200], [])
    assert model.max_retries == images_mod.DEFAULT_MAX_RETRIES == 1
    model.generate("x", size=(32, 32))
    assert _timeout_of(api.requests[0]) == {"connect": 10.0, "read": 120.0, "write": 120.0, "pool": 120.0}

    model, api = flaky_image_model([200], [], timeout=30, max_retries=0)
    model.generate("x", size=(32, 32))
    assert _timeout_of(api.requests[0]) == {"connect": 10.0, "read": 30.0, "write": 30.0, "pool": 30.0}
    model, api = flaky_image_model([200], [], timeout=4)
    model.generate("x", size=(32, 32))
    assert _timeout_of(api.requests[0])["connect"] == 4.0  # never longer than the whole timeout


def test_an_image_url_result_is_fetched_with_the_same_bounded_timeout(clean_env: pytest.MonkeyPatch) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.host == "cdn.example.test":
            return httpx.Response(200, content=make_image((32, 32)))
        return httpx.Response(200, json={"data": [{"url": "https://cdn.example.test/a.png"}]})

    model = OpenAIImageModel(api_key=SECRET, transport=httpx.MockTransport(handler), timeout=45, sleep=no_sleep)
    model.generate("x", size=(32, 32))
    assert [r.url.host for r in seen] == ["api.openai.com", "cdn.example.test"]
    assert all(_timeout_of(r) == {"connect": 10.0, "read": 45.0, "write": 45.0, "pool": 45.0} for r in seen)


@pytest.mark.parametrize("status", sorted(images_mod.RETRY_STATUSES))
def test_images_retry_a_transient_status_once_then_succeed(clean_env: pytest.MonkeyPatch, status: int) -> None:
    sleeps: list[float] = []
    model, api = flaky_image_model([status, 200], sleeps)
    assert open_image(model.generate("x", size=(32, 32))).size == (32, 32)
    assert len(api.requests) == 2 and sleeps == [images_mod.RETRY_BACKOFF_S]


def test_images_give_up_after_max_retries(clean_env: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []
    model, api = flaky_image_model([503], sleeps)
    with pytest.raises(RuntimeError, match="HTTP 503"):
        model.generate("x")
    assert len(api.requests) == 1 + images_mod.DEFAULT_MAX_RETRIES

    sleeps.clear()
    model, api = flaky_image_model([503], sleeps, max_retries=3)
    with pytest.raises(RuntimeError, match="HTTP 503"):
        model.generate("x")
    assert len(api.requests) == 4 and sleeps == [0.5, 1.0, 2.0]

    model, api = flaky_image_model([503], sleeps, max_retries=0)
    with pytest.raises(RuntimeError, match="HTTP 503"):
        model.generate("x")
    assert len(api.requests) == 1


@pytest.mark.parametrize("status", [400, 401, 403, 404, 413, 422])
def test_images_never_retry_a_request_the_provider_refused(clean_env: pytest.MonkeyPatch, status: int) -> None:
    sleeps: list[float] = []
    model, api = flaky_image_model([status, 200], sleeps, max_retries=3)
    with pytest.raises(RuntimeError, match=f"HTTP {status}"):
        model.generate("x")
    assert len(api.requests) == 1 and sleeps == []


def test_images_retry_a_connection_that_was_never_made(clean_env: pytest.MonkeyPatch) -> None:
    for error in (httpx.ConnectError, httpx.ConnectTimeout):
        sleeps: list[float] = []
        model, api = flaky_image_model([error, 200], sleeps)
        assert model.generate("x", size=(32, 32))
        assert len(api.requests) == 2 and sleeps == [0.5]


@pytest.mark.parametrize("error", [httpx.ReadTimeout, httpx.WriteTimeout, httpx.ReadError, httpx.RemoteProtocolError])
def test_images_never_retry_once_the_request_was_sent(clean_env: pytest.MonkeyPatch, error: type[Exception]) -> None:
    """The provider may still be drawing (and billing) that image: waiting again doubles the worst case."""
    sleeps: list[float] = []
    model, api = flaky_image_model([error, 200], sleeps, max_retries=3)
    with pytest.raises(RuntimeError, match=error.__name__) as info:
        model.generate("x")
    assert len(api.requests) == 1 and sleeps == []
    assert SECRET not in str(info.value)


@pytest.mark.parametrize(
    ("retry_after", "waited"),
    [("3", 3.0), ("0.25", 0.25), ("600", images_mod.MAX_BACKOFF_S), ("Wed, 21 Oct 2015 07:28:00 GMT", 0.5), ("-1", 0.5)],
)
def test_images_honor_retry_after_up_to_the_cap(clean_env: pytest.MonkeyPatch, retry_after: str, waited: float) -> None:
    sleeps: list[float] = []
    busy = httpx.Response(429, headers={"Retry-After": retry_after}, json={"error": {"message": "slow down"}})
    model, _ = flaky_image_model([busy, 200], sleeps)
    model.generate("x", size=(32, 32))
    assert sleeps == [waited]


@pytest.mark.parametrize("bad", [{"timeout": 0}, {"timeout": -1}, {"timeout": float("inf")}, {"timeout": float("nan")},
                                 {"max_retries": -1}, {"max_retries": 1.5}])
def test_adapter_limits_are_validated(clean_env: pytest.MonkeyPatch, bad: dict) -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(500))
    with pytest.raises(ValueError):
        OpenAIImageModel(api_key=SECRET, transport=transport, **bad)
    with pytest.raises(ValueError):
        ElevenLabsTTS(api_key=SECRET, transport=transport, **bad)
    with pytest.raises(ValueError):
        AnthropicVision(client=SimpleNamespace(messages=None), **bad)


def test_tts_requests_carry_bounded_timeouts(clean_env: pytest.MonkeyPatch) -> None:
    tts, api = flaky_tts([200], [])
    assert tts.max_retries == tts_mod.DEFAULT_MAX_RETRIES == 2
    tts.synthesize(HELLO_TEXT)
    assert _timeout_of(api.requests[0]) == {"connect": 10.0, "read": 30.0, "write": 30.0, "pool": 30.0}
    tts, api = flaky_tts([200], [], timeout=12)
    tts.synthesize(HELLO_TEXT)
    assert _timeout_of(api.requests[0])["read"] == 12.0


def test_tts_retries_transient_failures_and_gives_up_after_max_retries(clean_env: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []
    tts, api = flaky_tts([503, httpx.ConnectError, 200], sleeps)
    speech = tts.synthesize(HELLO_TEXT)
    assert [w.word for w in speech.words] == ["Hello", "world"]
    assert len(api.requests) == 3 and sleeps == [0.5, 1.0]

    sleeps.clear()
    tts, api = flaky_tts([500], sleeps)
    with pytest.raises(RuntimeError, match="HTTP 500"):
        tts.synthesize(HELLO_TEXT)
    assert len(api.requests) == 1 + tts_mod.DEFAULT_MAX_RETRIES and sleeps == [0.5, 1.0]


@pytest.mark.parametrize("step", [401, 422, httpx.ReadTimeout])
def test_tts_never_retries_a_refusal_or_a_read_timeout(clean_env: pytest.MonkeyPatch, step: object) -> None:
    sleeps: list[float] = []
    tts, api = flaky_tts([step, 200], sleeps)
    with pytest.raises(RuntimeError) as info:
        tts.synthesize(HELLO_TEXT)
    assert len(api.requests) == 1 and sleeps == []
    assert SECRET not in str(info.value)


def test_vision_client_is_built_with_the_run_limits(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("ANTHROPIC_API_KEY", SECRET)
    for name in ("STITCH_MODEL_TIMEOUT_S", "STITCH_MODEL_MAX_RETRIES"):
        clean_env.delenv(name, raising=False)
    vision = AnthropicVision()
    assert (vision.timeout, vision.max_retries) == (120.0, 2)
    # The SDK never retries on its own: it would obey any Retry-After uncapped.
    assert vision.client.timeout == 120.0 and vision.client.max_retries == 0

    clean_env.setenv("STITCH_MODEL_TIMEOUT_S", "45")
    clean_env.setenv("STITCH_MODEL_MAX_RETRIES", "1")
    vision = AnthropicVision()
    assert (vision.timeout, vision.max_retries) == (45.0, 1)
    assert vision.client.timeout == 45.0 and vision.client.max_retries == 0

    vision = AnthropicVision(timeout=9, max_retries=0)  # explicit values win over the env
    assert (vision.timeout, vision.max_retries) == (9.0, 0)
    assert vision.client.timeout == 9.0 and vision.client.max_retries == 0


VISION_OK = {
    "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-test",
    "content": [{"type": "text", "text": "a red square"}],
    "stop_reason": "end_turn", "stop_sequence": None,
    "usage": {"input_tokens": 1, "output_tokens": 1},
}


class FakeAnthropicApi:
    """httpx2 MockTransport handler for the Messages API: answers each request with the next
    step of `script` (then the last one forever). A step is an int status (200 is VISION_OK),
    a (status, headers) pair, or an httpx2 exception class to raise."""

    def __init__(self, script: list) -> None:
        self.script = list(script)
        self.requests: list = []

    def __call__(self, request):  # noqa: ANN001, ANN204 - httpx2 types
        self.requests.append(request)
        step = self.script[min(len(self.requests), len(self.script)) - 1]
        if isinstance(step, type) and issubclass(step, Exception):
            raise step("simulated", request=request)
        status, headers = step if isinstance(step, tuple) else (step, {})
        if status == 200:
            return sdk_httpx.Response(200, json=VISION_OK)
        kind = {429: "rate_limit_error", 529: "overloaded_error"}.get(status, "api_error")
        return sdk_httpx.Response(status, headers=headers, json={"type": "error", "error": {"type": kind, "message": "busy"}})


def wired_vision(script: list, sleeps: list[float], **kwargs) -> tuple[AnthropicVision, FakeAnthropicApi]:
    """A real AnthropicVision on the real SDK client it builds, over a fake transport."""
    api = FakeAnthropicApi(script)
    http_client = sdk_httpx.Client(transport=sdk_httpx.MockTransport(api))
    return AnthropicVision(http_client=http_client, sleep=sleeps.append, **kwargs), api


@pytest.fixture
def sdk_never_sleeps(clean_env: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """The key is set, and any sleep inside the SDK fails the test: every wait must be the adapter's."""
    clean_env.setenv("ANTHROPIC_API_KEY", SECRET)

    def forbidden(seconds: float) -> None:
        raise AssertionError(f"the SDK slept {seconds}s on its own")

    clean_env.setattr(sdk_base.time, "sleep", forbidden)
    return clean_env


def describe_red(vision: AnthropicVision) -> str:
    return vision.describe([make_image((10, 10))], "what is this?")


def test_vision_stops_after_its_retries_over_the_wire(sdk_never_sleeps: pytest.MonkeyPatch) -> None:
    """Gives up after 1 + max_retries requests, each with the bounded timeout, waiting only in the adapter."""
    sleeps: list[float] = []
    vision, api = wired_vision([(529, {"retry-after-ms": "1"})], sleeps, timeout=7, max_retries=2)
    with pytest.raises(anthropic.APIStatusError) as info:
        describe_red(vision)
    assert info.value.status_code == 529
    assert len(api.requests) == 3 and sleeps == [0.001, 0.001]
    assert all(dict(r.extensions["timeout"])["read"] == 7.0 for r in api.requests)
    assert all(r.headers.get("x-stainless-retry-count") in (None, "0") for r in api.requests)


@pytest.mark.parametrize(
    ("headers", "waited"),
    [
        ({"retry-after": "3600"}, vision_mod.MAX_BACKOFF_S),  # the SDK alone would sleep an hour
        ({"retry-after-ms": "86400000"}, vision_mod.MAX_BACKOFF_S),  # ... or a day
        ({"retry-after": "3"}, 3.0),
        ({"retry-after-ms": "250"}, 0.25),
        ({"retry-after": "Wed, 21 Oct 2015 07:28:00 GMT"}, 0.5),  # a date: plain backoff
        ({"retry-after": "-5"}, 0.5),
        ({}, 0.5),
    ],
)
def test_vision_caps_retry_after_through_the_sdk(
    sdk_never_sleeps: pytest.MonkeyPatch, headers: dict, waited: float
) -> None:
    sleeps: list[float] = []
    vision, api = wired_vision([(429, headers), 200], sleeps, timeout=5, max_retries=2)
    assert describe_red(vision) == "a red square"
    assert len(api.requests) == 2 and sleeps == [waited]


def test_vision_worst_case_wait_is_bounded(sdk_never_sleeps: pytest.MonkeyPatch) -> None:
    """An API that answers 'overloaded, come back tomorrow' forever costs at most max_retries capped waits."""
    sleeps: list[float] = []
    vision, api = wired_vision([(529, {"retry-after": "86400"})], sleeps, timeout=30, max_retries=3)
    with pytest.raises(anthropic.APIStatusError):
        describe_red(vision)
    assert len(api.requests) == 4
    assert sleeps == [vision_mod.MAX_BACKOFF_S] * 3
    assert sum(sleeps) <= vision.worst_case_s - 4 * vision.timeout
    assert vision.worst_case_s == 4 * 30 + 3 * vision_mod.MAX_BACKOFF_S
    # With the run limits' defaults: three 120 s attempts and two capped waits.
    assert vision_mod.worst_case_s(120, 2) == 376.0


@pytest.mark.parametrize("status", [408, 409, 429, 500, 502, 503, 504, 529])
def test_vision_retries_what_the_sdk_would(sdk_never_sleeps: pytest.MonkeyPatch, status: int) -> None:
    sleeps: list[float] = []
    vision, api = wired_vision([status, status, 200], sleeps, max_retries=2)
    assert describe_red(vision) == "a red square"
    assert len(api.requests) == 3 and sleeps == [0.5, 1.0]


@pytest.mark.parametrize("status", [400, 401, 403, 404, 413, 422])
def test_vision_never_retries_a_refusal(sdk_never_sleeps: pytest.MonkeyPatch, status: int) -> None:
    sleeps: list[float] = []
    vision, api = wired_vision([status, 200], sleeps, max_retries=3)
    with pytest.raises(anthropic.APIStatusError) as info:
        describe_red(vision)
    assert info.value.status_code == status
    assert len(api.requests) == 1 and sleeps == []
    assert SECRET not in str(info.value)


def test_vision_obeys_x_should_retry(sdk_never_sleeps: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []
    vision, api = wired_vision([(503, {"x-should-retry": "false"}), 200], sleeps, max_retries=2)
    with pytest.raises(anthropic.APIStatusError):
        describe_red(vision)
    assert len(api.requests) == 1

    vision, api = wired_vision([(400, {"x-should-retry": "true"}), 200], sleeps, max_retries=2)
    assert describe_red(vision) == "a red square"
    assert len(api.requests) == 2 and sleeps == [0.5]


def test_vision_retries_a_failed_connection_but_not_a_timeout(sdk_never_sleeps: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []
    vision, api = wired_vision([sdk_httpx.ConnectError, 200], sleeps, max_retries=2)
    assert describe_red(vision) == "a red square"
    assert len(api.requests) == 2 and sleeps == [0.5]

    # A provider that hung once is not waited on again: that would double the worst case.
    sleeps.clear()
    vision, api = wired_vision([sdk_httpx.ReadTimeout, 200], sleeps, max_retries=2)
    with pytest.raises(anthropic.APITimeoutError):
        describe_red(vision)
    assert len(api.requests) == 1 and sleeps == []


def test_vision_retry_waits_double_up_to_the_cap() -> None:
    assert [vision_mod.retry_wait_s(a) for a in range(6)] == [0.5, 1.0, 2.0, 4.0, 8.0, 8.0]
    assert vision_mod.retry_wait_s(5000) == vision_mod.MAX_BACKOFF_S  # no float overflow on a huge retry count
    assert vision_mod.retry_wait_s(0, {"retry-after-ms": "-1", "retry-after": "2"}) == 2.0
    assert vision_mod.retry_wait_s(0, {"retry-after": "inf"}) == 0.5


def test_vision_with_an_injected_client_uses_the_same_bounded_retries(clean_env: pytest.MonkeyPatch) -> None:
    """A non-SDK error is never retried; an SDK error from an injected client is, with the cap."""
    request = sdk_httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    overloaded = sdk_httpx.Response(529, headers={"retry-after": "900"}, request=request)

    class Messages:
        def __init__(self, errors: list[Exception]) -> None:
            self.errors = errors
            self.calls = 0

        def create(self, **kwargs: object) -> dict:
            self.calls += 1
            if self.errors:
                raise self.errors.pop(0)
            return {"content": [{"type": "text", "text": "ok"}]}

    sleeps: list[float] = []
    messages = Messages([anthropic.APIStatusError("overloaded", response=overloaded, body=None)])
    vision = AnthropicVision(client=SimpleNamespace(messages=messages), sleep=sleeps.append, timeout=5, max_retries=1)
    assert describe_red(vision) == "ok"
    assert messages.calls == 2 and sleeps == [vision_mod.MAX_BACKOFF_S]

    messages = Messages([RuntimeError("boom"), RuntimeError("boom")])
    vision = AnthropicVision(client=SimpleNamespace(messages=messages), sleep=sleeps.append, timeout=5, max_retries=3)
    with pytest.raises(RuntimeError):
        describe_red(vision)
    assert messages.calls == 1


def test_worst_case_adapter_call_fits_well_inside_a_run() -> None:
    """A provider that hangs every request still lets the tool degrade long before the run timeout."""
    from agent.guards import RunLimits

    run_timeout = RunLimits().run_timeout_s
    image_worst = (1 + images_mod.DEFAULT_MAX_RETRIES) * (images_mod.CONNECT_TIMEOUT_S + images_mod.DEFAULT_TIMEOUT_S) \
        + images_mod.DEFAULT_MAX_RETRIES * images_mod.MAX_BACKOFF_S
    tts_worst = (1 + tts_mod.DEFAULT_MAX_RETRIES) * (images_mod.CONNECT_TIMEOUT_S + tts_mod.DEFAULT_TIMEOUT_S) \
        + tts_mod.DEFAULT_MAX_RETRIES * images_mod.MAX_BACKOFF_S
    limits = RunLimits()
    vision_worst = vision_mod.worst_case_s(limits.model_timeout_s, limits.model_max_retries)
    assert image_worst <= run_timeout / 4
    assert tts_worst <= run_timeout / 8
    assert vision_worst <= run_timeout / 3


# --------------------------------------------------------------------------- live (opt-in)


@pytest.mark.skipif(not LIVE["ANTHROPIC_API_KEY"], reason="ANTHROPIC_API_KEY not set")
def test_live_anthropic_vision() -> None:
    text = AnthropicVision(max_tokens=200).describe(
        [make_image((256, 256), (200, 30, 30))], "What color is this image? Answer in one word."
    )
    assert "red" in text.lower()


@pytest.mark.skipif(not LIVE["IMAGE_API_KEY"], reason="IMAGE_API_KEY not set")
def test_live_image_model_and_cutout() -> None:
    model = OpenAIImageModel()
    png = model.generate("A single red apple on a plain white background, paper cutout style.", size=(512, 512))
    assert open_image(png).size == (512, 512)
    assert analyze_cutout(AlphaAwareRemover().remove(png)).aspect > 0


@pytest.mark.skipif(not LIVE["ELEVENLABS_API_KEY"], reason="ELEVENLABS_API_KEY not set")
def test_live_elevenlabs_tts() -> None:
    speech = ElevenLabsTTS().synthesize("Hello, world.", voice="voice_a")
    assert speech.duration > 0.3
    assert [w.word for w in speech.words] == ["Hello", "world"]
    assert speech.words[0].t <= speech.words[1].t
