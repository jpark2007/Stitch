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
from types import SimpleNamespace

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


def image_model(api: ImageAPI, **kwargs) -> OpenAIImageModel:
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

    model = OpenAIImageModel(api_key=SECRET, transport=httpx.MockTransport(down))
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
        ElevenLabsTTS(api_key=SECRET, transport=httpx.MockTransport(api)).synthesize("Hi.")
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
