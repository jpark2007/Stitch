"""CloudflareImageModel, the three-provider rule and the Cloudflare preflight report. All offline:
httpx.MockTransport for the API, a no-op sleep and a fake clock. No real Cloudflare call is made."""

from __future__ import annotations

import base64
import gzip
import io
import json
import logging
from collections.abc import Iterator
from typing import Any

import httpx
import mongomock
import pytest
from PIL import Image

from agent import preflight, production
from agent.adapters import (
    CloudflareImageModel,
    HiggsfieldImageModel,
    OpenAIImageModel,
    choose_image_provider,
    fitting,
)
from agent.adapters import cloudflare as cf
from agent.adapters import higgsfield as hf
from agent.adapters import images as images_mod
from agent.tools.art import ArtService, CanvasSizer, ImageModel, accepts_plain_background
from db import client as db_client

ACCOUNT = "0123456789abcdef0123456789abcdef"
TOKEN = "cfTOKEN-AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"
HF_KEY = "hfKEYid-0123456789abcdef"
HF_SECRET = "hfSECRET-fedcba9876543210-secret"
OPENAI_KEY = "sk-openai-dummy-0000"
RUN_URL = f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT}/ai/run/@cf/black-forest-labs/flux-2-klein-4b"


def png(size: tuple[int, int], color: tuple[int, ...] = (200, 60, 40), mode: str = "RGB") -> bytes:
    buf = io.BytesIO()
    Image.new(mode, size, color).save(buf, format="PNG")
    return buf.getvalue()


def jpeg(size: tuple[int, int]) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, (30, 90, 160)).save(buf, format="JPEG")
    return buf.getvalue()


def open_png(data: bytes) -> Image.Image:
    im = Image.open(io.BytesIO(data))
    im.load()
    return im


def b64_answer(image: bytes) -> httpx.Response:
    body = {"result": {"image": base64.b64encode(image).decode()}, "success": True, "errors": [], "messages": []}
    return httpx.Response(200, json=body)


def cf_error(status: int, code: int, message: str, **kwargs: Any) -> httpx.Response:
    body = {"result": None, "success": False, "errors": [{"code": code, "message": message}], "messages": []}
    return httpx.Response(status, json=body, **kwargs)


ALLOWANCE_MSG = (
    "You have used up your daily free allocation of 10,000 neurons. Please upgrade to Cloudflare's "
    "Workers Paid plan if you would like to continue usage."
)


class Clock:
    """Fake monotonic clock: every sleep advances it."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class FakeCloudflare:
    """Scripted Workers AI. ``answers`` are served in order (the last one repeats); an answer is an
    httpx.Response, an exception to raise, or None for the default (a base64 JSON image)."""

    def __init__(
        self,
        *,
        answers: list[Any] | None = None,
        image: bytes | None = None,
        clock: Clock | None = None,
        cost: float = 0.0,
    ) -> None:
        self.answers = answers or [None]
        self.image = image if image is not None else png((1024, 1024))
        self.requests: list[httpx.Request] = []
        self.clock = clock
        self.cost = cost
        self.timeouts: list[tuple[float, dict[str, float]]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        request.read()
        self.requests.append(request)
        if self.clock is not None:
            self.timeouts.append((self.clock.now, dict(request.extensions.get("timeout") or {})))
            self.clock.now += self.cost
        step = self.answers[min(len(self.requests) - 1, len(self.answers) - 1)]
        if isinstance(step, Exception):
            raise step
        if not isinstance(step, httpx.Response):
            step = b64_answer(self.image)
        if step.is_stream_consumed:
            # httpx.Response(content=/json=) is read at once; a real answer arrives as a stream.
            # Serve a fresh stream of the same raw (still encoded) bytes, so it can repeat too.
            raw = b"".join(step.stream)  # type: ignore[arg-type]
            return httpx.Response(step.status_code, headers=step.headers, stream=httpx.ByteStream(raw))
        return step

    def form(self, index: int = 0) -> dict[str, Any]:
        """The multipart fields of request ``index``: text fields as str, files as bytes."""
        request = self.requests[index]
        ctype = request.headers["content-type"]
        assert ctype.startswith("multipart/form-data; boundary=")
        boundary = ctype.split("boundary=", 1)[1].encode()
        fields: dict[str, Any] = {}
        for part in request.content.split(b"--" + boundary)[1:-1]:
            head, _, body = part.strip(b"\r\n").partition(b"\r\n\r\n")
            name = head.split(b'name="', 1)[1].split(b'"', 1)[0].decode()
            fields[name] = body if b"filename=" in head else body.decode()
        return fields


def model_for(api: FakeCloudflare, clock: Clock | None = None, **kwargs: Any) -> CloudflareImageModel:
    clock = clock or api.clock or Clock()
    return CloudflareImageModel(
        account_id=ACCOUNT,
        api_token=TOKEN,
        transport=httpx.MockTransport(api),
        sleep=clock.sleep,
        clock=clock,
        **kwargs,
    )


def assert_no_secret(text: str) -> None:
    assert TOKEN not in text and ACCOUNT not in text
    assert TOKEN.lower() not in text.lower() and ACCOUNT.lower() not in text.lower()


@pytest.fixture(autouse=True)
def _no_cloudflare_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for name in (cf.ACCOUNT_ENV, cf.TOKEN_ENV, cf.URL_ENV, cf.MODEL_ENV, cf.STYLE_ENV, "IMAGE_PROVIDER"):
        monkeypatch.delenv(name, raising=False)
    # production_services() opens the family database: a mongomock one, never a real MongoClient
    # (its monitor thread would trip a later test's socket spy); constructing one fails the test.
    def no_real_client(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("a real MongoClient was constructed; the family database must be mongomock here")

    monkeypatch.setattr(db_client, "MongoClient", no_real_client)
    db_client.set_db(mongomock.MongoClient(tz_aware=True)[db_client.MONGODB_DB])
    yield
    db_client.set_db(None)


# --------------------------------------------------------------------------- protocol and happy paths


def test_it_satisfies_the_image_model_and_canvas_sizer_protocols() -> None:
    model = model_for(FakeCloudflare())
    assert isinstance(model, ImageModel) and isinstance(model, CanvasSizer)
    assert_no_secret(repr(model))
    # No background control on any model: the studio's prompt asks for white instead.
    assert not accepts_plain_background(model)


def test_happy_path_klein_multipart_base64() -> None:
    api = FakeCloudflare()
    out = model_for(api).generate("a red teapot", size=(800, 600))
    assert open_png(out).size == (800, 600)
    request = api.requests[0]
    assert request.method == "POST" and str(request.url) == RUN_URL
    assert request.headers["authorization"] == f"Bearer {TOKEN}"
    assert request.headers["accept-encoding"] == "identity"
    assert api.form() == {
        "prompt": "a red teapot\n\n" + cf.DEFAULT_STYLE_SUFFIX,
        "width": "1024",
        "height": "768",
    }


def test_klein_9b_uses_the_same_multipart_request() -> None:
    api = FakeCloudflare()
    model_for(api, model="@cf/black-forest-labs/flux-2-klein-9b").generate("x", size=(64, 64))
    assert api.requests[0].url.path.endswith("/ai/run/@cf/black-forest-labs/flux-2-klein-9b")
    assert set(api.form()) == {"prompt", "width", "height"}


def test_happy_path_phoenix_json_body_and_raw_jpeg_answer() -> None:
    api = FakeCloudflare(answers=[httpx.Response(200, content=jpeg((1024, 768)), headers={"content-type": "image/jpeg"})])
    out = model_for(api, model="@cf/leonardo/phoenix-1.0").generate("a lamp", size=(400, 300))
    assert open_png(out).size == (400, 300)
    request = api.requests[0]
    assert request.headers["content-type"] == "application/json"
    assert json.loads(request.content) == {
        "prompt": "a lamp\n\n" + cf.DEFAULT_STYLE_SUFFIX,
        "width": 1024,
        "height": 768,
    }


def test_a_data_url_and_a_bare_image_field_are_accepted() -> None:
    data = base64.b64encode(png((100, 100))).decode()
    for body in ({"result": {"image": f"data:image/png;base64,{data}"}, "success": True}, {"image": data}):
        api = FakeCloudflare(answers=[httpx.Response(200, json=body)])
        assert open_png(model_for(api).generate("x", size=(50, 50))).size == (50, 50)


@pytest.mark.parametrize(
    ("size", "canvas"),
    [
        ((1024, 1024), (1024, 1024)),
        ((800, 600), (1024, 768)),
        ((600, 800), (768, 1024)),
        ((2048, 1024), (1024, 512)),
        ((2048, 464), (1136, 256)),  # the middle set layer: the short side is held at the minimum
        ((2048, 192), (1920, 256)),  # the front layer: past the widest canvas, the fitter crops
        ((300, 300), (1024, 1024)),
        ((1530, 1024), (1024, 688)),
    ],
)
def test_canvas_and_fitting_for_each_requested_size(size: tuple[int, int], canvas: tuple[int, int]) -> None:
    api = FakeCloudflare(image=png(canvas))
    model = model_for(api)
    assert model.canvas_size(size) == canvas
    assert all(side % cf.SIZE_STEP == 0 and 256 <= side <= 1920 for side in canvas)
    out = model.generate("x", size=size)
    assert open_png(out).size == size
    assert (int(api.form()["width"]), int(api.form()["height"])) == canvas


def test_fitting_is_the_shared_rule() -> None:
    im = Image.new("RGB", (1136, 256))
    for y in range(256):
        im.paste((y, 40, 200 - y % 200), (0, y, 1136, y + 1))
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    data = buf.getvalue()
    out = model_for(FakeCloudflare(image=data)).generate("x", size=(2048, 464))
    assert out == fitting.to_png(data, (2048, 464)) == images_mod._to_png(data, (2048, 464))


def test_phoenix_canvas_allows_up_to_2048() -> None:
    model = model_for(FakeCloudflare(), model="@cf/leonardo/phoenix-1.0")
    assert model.canvas_size((2048, 192)) == (2048, 256)


def test_alpha_is_kept() -> None:
    api = FakeCloudflare(image=png((1024, 1024), (10, 20, 30, 0), mode="RGBA"))
    assert open_png(model_for(api).generate("x", size=(500, 500))).mode == "RGBA"


def test_style_suffix_is_configurable_and_can_be_turned_off(monkeypatch: pytest.MonkeyPatch) -> None:
    api = FakeCloudflare()
    model_for(api, style_suffix="none").generate("a cup", size=(100, 100))
    assert api.form()["prompt"] == "a cup"
    monkeypatch.setenv(cf.STYLE_ENV, "Paper only.")
    api = FakeCloudflare()
    model_for(api).generate("a cup", size=(100, 100))
    assert api.form()["prompt"] == "a cup\n\nPaper only."
    monkeypatch.setenv(cf.STYLE_ENV, "NONE")
    assert cf.configured_style_suffix() == ""


# --------------------------------------------------------------------------- references


def test_references_go_as_input_images_under_512() -> None:
    api = FakeCloudflare()
    refs = [png((2000, 1000)), png((300, 300)), jpeg((640, 480))]
    model_for(api).generate("x", reference_images=refs, size=(64, 64))
    form = api.form()
    assert [k for k in form if k.startswith("input_image_")] == ["input_image_0", "input_image_1", "input_image_2"]
    for key in ("input_image_0", "input_image_1", "input_image_2"):
        im = open_png(form[key])
        assert im.format == "PNG" and max(im.size) < 512
    assert open_png(form["input_image_0"]).size == (511, 256)


def test_bad_and_extra_references_are_skipped_with_a_warning(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(cf, "_reference_warned", False)
    caplog.set_level(logging.WARNING, logger=cf.__name__)
    api = FakeCloudflare()
    refs = [b"not an image", png((10, 10)), png((10, 10)), png((10, 10)), png((10, 10)), png((10, 10))]
    model_for(api).generate("x", reference_images=refs, size=(64, 64))
    assert sorted(k for k in api.form() if k.startswith("input_image_")) == [
        "input_image_0",
        "input_image_1",
        "input_image_2",
    ]
    assert "reference image 0 skipped" in caplog.text
    assert "at most 4 reference images" in caplog.text


def test_phoenix_ignores_references() -> None:
    api = FakeCloudflare(answers=[httpx.Response(200, content=jpeg((64, 64)), headers={"content-type": "image/jpeg"})])
    model_for(api, model="@cf/leonardo/phoenix-1.0").generate("x", reference_images=[png((10, 10))], size=(64, 64))
    assert set(json.loads(api.requests[0].content)) == {"prompt", "width", "height"}


# --------------------------------------------------------------------------- retries and deadline


@pytest.mark.parametrize("status", [408, 500, 502, 503, 504])
def test_a_fast_failure_is_retried_then_succeeds(status: int) -> None:
    clock = Clock()
    api = FakeCloudflare(answers=[httpx.Response(status, headers={"retry-after": "3"}), None])
    assert open_png(model_for(api, clock).generate("x", size=(64, 64))).size == (64, 64)
    assert len(api.requests) == 2 and clock.sleeps == [3.0]


def test_capacity_429_is_retried_and_retry_after_is_capped() -> None:
    clock = Clock()
    busy = cf_error(429, 3040, "Capacity temporarily exceeded, please try again.", headers={"retry-after": "600"})
    api = FakeCloudflare(answers=[busy, busy, None])
    assert open_png(model_for(api, clock).generate("x", size=(64, 64))).size == (64, 64)
    assert len(api.requests) == 3 and clock.sleeps == [images_mod.MAX_BACKOFF_S] * 2


def test_retries_are_bounded() -> None:
    api = FakeCloudflare(answers=[httpx.Response(503)])
    with pytest.raises(RuntimeError, match="HTTP 503"):
        model_for(api).generate("x", size=(64, 64))
    assert len(api.requests) == 1 + cf.DEFAULT_MAX_RETRIES


def test_a_connection_failure_is_retried_but_a_read_timeout_is_not() -> None:
    api = FakeCloudflare(answers=[httpx.ConnectError("refused"), None])
    model_for(api).generate("x", size=(64, 64))
    assert len(api.requests) == 2
    api = FakeCloudflare(answers=[httpx.ReadTimeout("slow")])
    with pytest.raises(RuntimeError, match="request failed: ReadTimeout"):
        model_for(api).generate("x", size=(64, 64))
    assert len(api.requests) == 1


@pytest.mark.parametrize("status", [400, 401, 403, 404, 413])
def test_a_client_error_is_not_retried(status: int) -> None:
    api = FakeCloudflare(answers=[cf_error(status, 5007, f"No such model for token {TOKEN} account {ACCOUNT}")])
    with pytest.raises(RuntimeError, match=f"HTTP {status}: 5007 No such model") as exc:
        model_for(api).generate("x", size=(64, 64))
    assert len(api.requests) == 1
    assert_no_secret(str(exc.value))


def test_a_redirect_is_not_followed() -> None:
    api = FakeCloudflare(answers=[httpx.Response(307, headers={"location": "https://evil.example.com/collect"})])
    with pytest.raises(RuntimeError, match="redirect .HTTP 307.; redirects are not followed"):
        model_for(api).generate("x", size=(64, 64))
    assert [r.url.host for r in api.requests] == ["api.cloudflare.com"]


def test_every_request_timeout_fits_inside_the_deadline() -> None:
    clock = Clock()
    start = clock.now
    api = FakeCloudflare(answers=[httpx.Response(503, headers={"retry-after": "1"})], clock=clock, cost=50.0)
    with pytest.raises(RuntimeError):
        model_for(api).generate("x", size=(64, 64))
    for at, timeout in api.timeouts:
        left = start + cf.DEFAULT_DEADLINE_S - at
        assert left > 0
        assert 0 < timeout["read"] <= min(cf.REQUEST_TIMEOUT_S, left)
        assert 0 < timeout["connect"] <= min(cf.CONNECT_TIMEOUT_S, left)
    # 50 s, wait 1, 50 s: a third attempt would start past 101 s with under 19 s left; it still
    # fits, but its timeout is capped by the time left.
    assert len(api.requests) == 3
    assert api.timeouts[-1][1]["read"] <= start + cf.DEFAULT_DEADLINE_S - api.timeouts[-1][0]


def test_no_retry_when_its_wait_reaches_the_deadline() -> None:
    clock = Clock()
    api = FakeCloudflare(answers=[httpx.Response(503, headers={"retry-after": "8"})], clock=clock, cost=115.0)
    with pytest.raises(RuntimeError, match="HTTP 503"):
        model_for(api).generate("x", size=(64, 64))
    assert len(api.requests) == 1 and clock.sleeps == []


def test_no_request_starts_after_the_deadline() -> None:
    clock = Clock()
    api = FakeCloudflare(answers=[httpx.Response(503, headers={"retry-after": "0"})], clock=clock, cost=60.0)
    with pytest.raises(RuntimeError, match="no time left|HTTP 503"):
        model_for(api).generate("x", size=(64, 64))
    assert all(at < 1000.0 + cf.DEFAULT_DEADLINE_S for at, _ in api.timeouts)
    assert len(api.requests) == 2


def test_a_slow_answer_is_bounded_in_total() -> None:
    clock = Clock()
    start = clock.now

    class SlowStream(httpx.SyncByteStream):
        def __iter__(self):  # type: ignore[override]
            for _ in range(100):
                clock.now += 10.0  # each chunk arrives in time for a per-read timeout
                yield b"{" * 10

    api = FakeCloudflare(answers=[httpx.Response(200, stream=SlowStream(), headers={"content-type": "application/json"})])
    with pytest.raises(RuntimeError, match="did not finish within 120 s"):
        model_for(api, clock).generate("x", size=(64, 64))
    assert clock.now - start <= cf.DEFAULT_DEADLINE_S + 10.0


@pytest.mark.parametrize("bad", [{"deadline_s": 0}, {"deadline_s": float("inf")}, {"max_retries": -1}])
def test_bad_bounds_are_refused(bad: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        CloudflareImageModel(account_id=ACCOUNT, api_token=TOKEN, **bad)


# --------------------------------------------------------------------------- allowance


def test_the_daily_allowance_exhausted_raises_at_once_without_retrying() -> None:
    api = FakeCloudflare(answers=[cf_error(429, 3036, ALLOWANCE_MSG, headers={"retry-after": "1"})])
    with pytest.raises(cf.CloudflareAllowanceExhausted, match="free daily allowance is used up") as exc:
        model_for(api).generate("x", size=(64, 64))
    assert isinstance(exc.value, RuntimeError)
    assert "3036" in str(exc.value) and "00:00 UTC" in str(exc.value)
    assert len(api.requests) == 1


def test_the_allowance_message_is_recognised_without_its_code() -> None:
    api = FakeCloudflare(answers=[cf_error(429, 9999, ALLOWANCE_MSG)])
    with pytest.raises(cf.CloudflareAllowanceExhausted):
        model_for(api).generate("x", size=(64, 64))
    assert len(api.requests) == 1


def test_allowance_exhausted_becomes_placeholder_art(caplog: pytest.LogCaptureFixture) -> None:
    from agent.adapters import AlphaAwareRemover

    caplog.set_level(logging.WARNING)
    api = FakeCloudflare(answers=[cf_error(429, 3036, ALLOWANCE_MSG)])
    service = ArtService(model_for(api), AlphaAwareRemover())
    assert service.make_cutout("a cup", size=(256, 256)) is None
    assert service.make_layer("a hill", layer="back", size=(2048, 1024)) is None
    assert "CloudflareAllowanceExhausted" in caplog.text
    assert_no_secret(caplog.text)


def test_art_service_draws_a_prop_and_a_layer_through_cloudflare() -> None:
    from agent.adapters import AlphaAwareRemover

    white_cup = Image.new("RGB", (1024, 768), (255, 255, 255))
    white_cup.paste((180, 40, 40), (400, 300, 620, 700))
    buf = io.BytesIO()
    white_cup.save(buf, format="PNG")
    service = ArtService(model_for(FakeCloudflare(image=buf.getvalue())), AlphaAwareRemover())
    assert service.make_cutout("a cup", size=(400, 300)) is not None
    assert service.make_layer("sky", layer="back", size=(2048, 1024)) is not None


# --------------------------------------------------------------------------- bad answers


def test_an_oversized_answer_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cf, "MAX_RESPONSE_BYTES", 1000)
    big = json.dumps({"result": {"image": "A" * 5000}}).encode()
    declared = httpx.Response(200, content=big, headers={"content-type": "application/json"})
    with pytest.raises(RuntimeError, match="larger than"):
        model_for(FakeCloudflare(answers=[declared])).generate("x", size=(64, 64))
    undeclared = httpx.Response(200, stream=httpx.ByteStream(big), headers={"content-type": "application/json"})
    with pytest.raises(RuntimeError, match="larger than"):
        model_for(FakeCloudflare(answers=[undeclared])).generate("x", size=(64, 64))


def test_a_gzip_answer_is_inflated_within_the_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    good = b64_answer(png((64, 64))).content
    ok = httpx.Response(200, content=gzip.compress(good), headers={"content-type": "application/json", "content-encoding": "gzip"})
    assert open_png(model_for(FakeCloudflare(answers=[ok])).generate("x", size=(32, 32))).size == (32, 32)
    monkeypatch.setattr(cf, "MAX_RESPONSE_BYTES", 100_000)
    bomb = gzip.compress(b"\0" * 50_000_000)
    assert len(bomb) < 100_000
    answer = httpx.Response(200, content=bomb, headers={"content-type": "application/json", "content-encoding": "gzip"})
    with pytest.raises(RuntimeError, match="larger than"):
        model_for(FakeCloudflare(answers=[answer])).generate("x", size=(32, 32))


def test_another_content_encoding_is_refused() -> None:
    answer = httpx.Response(200, content=b"xx", headers={"content-type": "application/json", "content-encoding": "br"})
    with pytest.raises(RuntimeError, match="content encoding 'br'"):
        model_for(FakeCloudflare(answers=[answer])).generate("x", size=(32, 32))


@pytest.mark.parametrize(
    ("answer", "match"),
    [
        (httpx.Response(200, text="<html>hello</html>", headers={"content-type": "text/html"}), "neither an image nor JSON"),
        (httpx.Response(200, json={"result": {}, "success": True}), "JSON with no image"),
        (httpx.Response(200, json=[1, 2]), "JSON with no image"),
        (httpx.Response(200, json={"result": {"image": "!!!not base64!!!"}}), "not valid base64"),
        (httpx.Response(200, json={"result": {"image": base64.b64encode(b"just text").decode()}}), "not a readable image"),
        (httpx.Response(200, content=b"<svg/>", headers={"content-type": "image/png"}), "not a readable image"),
        (
            httpx.Response(200, json={"success": False, "errors": [{"code": 1, "message": f"oops {TOKEN}"}]}),
            "success=false: 1 oops",
        ),
    ],
)
def test_a_non_image_payload_raises(answer: httpx.Response, match: str) -> None:
    with pytest.raises(RuntimeError, match=match) as exc:
        model_for(FakeCloudflare(answers=[answer])).generate("x", size=(64, 64))
    assert_no_secret(str(exc.value))


def _bomb() -> bytes:
    buf = io.BytesIO()
    Image.new("1", (13000, 13000)).save(buf, format="PNG")
    return buf.getvalue()


@pytest.mark.filterwarnings("ignore::PIL.Image.DecompressionBombWarning")
def test_a_pixel_bomb_is_refused_before_decoding_and_becomes_placeholder_art() -> None:
    from agent.adapters import AlphaAwareRemover

    bomb = _bomb()
    assert len(bomb) < 100_000
    with pytest.raises(RuntimeError, match="more than 40000000 pixels"):
        model_for(FakeCloudflare(image=bomb)).generate("x", size=(64, 64))
    service = ArtService(model_for(FakeCloudflare(image=bomb)), AlphaAwareRemover())
    assert service.make_cutout("a cup", size=(256, 256)) is None
    with pytest.raises(ValueError, match="pixels"):
        cf.reference_png(bomb)


# --------------------------------------------------------------------------- secrets and config


def test_the_token_and_account_never_appear_in_any_error_or_log(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    echo = f"{TOKEN} {ACCOUNT} {TOKEN.upper()} /accounts/{ACCOUNT}/ai/run"
    cases = [
        FakeCloudflare(answers=[cf_error(401, 10000, f"Authentication error {echo}")]),
        FakeCloudflare(answers=[cf_error(429, 3036, f"{ALLOWANCE_MSG} {echo}")]),
        FakeCloudflare(answers=[cf_error(503, 3040, echo)]),
        FakeCloudflare(answers=[httpx.ConnectError(echo)]),
        FakeCloudflare(answers=[httpx.ReadTimeout(echo)]),
        FakeCloudflare(answers=[httpx.Response(200, json={"success": False, "errors": [{"message": echo}]})]),
    ]
    for api in cases:
        with pytest.raises(RuntimeError) as exc:
            model_for(api).generate("x", size=(64, 64))
        assert_no_secret(str(exc.value))
        assert_no_secret(repr(exc.value))
    assert_no_secret(caplog.text)


@pytest.mark.parametrize(
    ("url", "ok"),
    [
        ("https://api.cloudflare.com/client/v4", True),
        ("http://localhost:9999/client/v4", True),
        ("http://127.0.0.1:9999", True),
        ("http://[::1]:9999", True),
        ("http://api.cloudflare.com/client/v4", False),
        ("http://10.0.0.5", False),
        ("ftp://api.cloudflare.com", False),
        ("https://user:pw@api.cloudflare.com", False),
        ("https://api.cloudflare.com/client/v4?x=1", False),
    ],
)
def test_a_cleartext_or_odd_api_url_is_refused(url: str, ok: bool) -> None:
    assert (cf.url_problem(url) is None) == ok
    if ok:
        CloudflareImageModel(account_id=ACCOUNT, api_token=TOKEN, base_url=url)
    else:
        with pytest.raises(RuntimeError, match="CLOUDFLARE_API_URL") as exc:
            CloudflareImageModel(account_id=ACCOUNT, api_token=TOKEN, base_url=url)
        assert_no_secret(str(exc.value))


def test_a_local_stub_url_is_used_as_given() -> None:
    api = FakeCloudflare()
    model_for(api, base_url="http://localhost:9999/client/v4").generate("x", size=(64, 64))
    assert str(api.requests[0].url).startswith(f"http://localhost:9999/client/v4/accounts/{ACCOUNT}/ai/run/")


def test_missing_or_malformed_config_raises_without_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(RuntimeError, match="CLOUDFLARE_ACCOUNT_ID is not set"):
        CloudflareImageModel()
    monkeypatch.setenv(cf.ACCOUNT_ENV, ACCOUNT)
    with pytest.raises(RuntimeError, match="CLOUDFLARE_API_TOKEN is not set") as exc:
        CloudflareImageModel()
    assert_no_secret(str(exc.value))
    monkeypatch.setenv(cf.TOKEN_ENV, TOKEN)
    for bad_account in ("../../user", "abc/def", "acc ount"):
        with pytest.raises(RuntimeError, match="letters and digits only"):
            CloudflareImageModel(account_id=bad_account)
    with pytest.raises(RuntimeError, match="non-ASCII") as exc:
        CloudflareImageModel(api_token="tok-é-" + TOKEN)
    assert_no_secret(str(exc.value))
    monkeypatch.setenv(cf.MODEL_ENV, "@cf/black-forest-labs/flux-1-schnell")
    with pytest.raises(RuntimeError, match="not a supported model") as exc:
        CloudflareImageModel()
    assert_no_secret(str(exc.value))
    assert "not a supported model" in (cf.config_problem() or "")


# --------------------------------------------------------------------------- provider selection

CF_ENV = {cf.ACCOUNT_ENV: ACCOUNT, cf.TOKEN_ENV: TOKEN}


@pytest.mark.parametrize("provider", [None, "higgsfield", "cloudflare", "openai", "CloudFlare", "dalle"])
@pytest.mark.parametrize("has_hf", [False, True])
@pytest.mark.parametrize("cf_vars", ["none", "account", "token", "both"])
@pytest.mark.parametrize("has_openai", [False, True])
def test_provider_selection_for_every_combination(
    provider: str | None, has_hf: bool, cf_vars: str, has_openai: bool
) -> None:
    env: dict[str, str] = {}
    if provider is not None:
        env["IMAGE_PROVIDER"] = provider
    if has_hf:
        env[hf.API_KEY_ENV] = f"{HF_KEY}:{HF_SECRET}"
    if cf_vars in ("account", "both"):
        env[cf.ACCOUNT_ENV] = ACCOUNT
    if cf_vars in ("token", "both"):
        env[cf.TOKEN_ENV] = TOKEN
    if has_openai:
        env[images_mod.API_KEY_ENV] = OPENAI_KEY
    has_cf = cf_vars == "both"
    choice = choose_image_provider(env)
    wanted = (provider or "").lower()
    if wanted == "higgsfield":
        expected = "higgsfield" if has_hf else "placeholder"
    elif wanted == "cloudflare":
        expected = "cloudflare" if has_cf else "placeholder"
    elif wanted == "openai":
        expected = "openai" if has_openai else "placeholder"
    else:
        expected = "higgsfield" if has_hf else "cloudflare" if has_cf else "openai" if has_openai else "placeholder"
    assert choice.provider == expected
    assert (choice.warning is not None) == (provider == "dalle")
    assert preflight.modes(env, compiler="FakeCompiler")["art"] == expected


def test_an_explicit_cloudflare_without_keys_names_what_is_missing() -> None:
    choice = choose_image_provider({"IMAGE_PROVIDER": "cloudflare", images_mod.API_KEY_ENV: OPENAI_KEY})
    assert choice.provider == "placeholder"
    assert choice.reason == "IMAGE_PROVIDER=cloudflare but CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN are not set"
    choice = choose_image_provider({"IMAGE_PROVIDER": "cloudflare", cf.ACCOUNT_ENV: ACCOUNT})
    assert choice.reason == "IMAGE_PROVIDER=cloudflare but CLOUDFLARE_API_TOKEN is not set"


def _production_env(monkeypatch: pytest.MonkeyPatch, env: dict[str, str]) -> Any:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-dummy")
    monkeypatch.setenv("STITCH_FAKE_DB", "1")
    for name in ("IMAGE_API_KEY", "ELEVENLABS_API_KEY", "MONGODB_URI", "STITCH_WEB_URL", hf.API_KEY_ENV, hf.API_SECRET_ENV):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    return production.production_services()


@pytest.mark.parametrize(
    ("env", "label", "cls"),
    [
        (CF_ENV, "cloudflare", CloudflareImageModel),
        ({**CF_ENV, "IMAGE_API_KEY": OPENAI_KEY}, "cloudflare", CloudflareImageModel),
        ({**CF_ENV, hf.API_KEY_ENV: HF_KEY, hf.API_SECRET_ENV: HF_SECRET}, "higgsfield", HiggsfieldImageModel),
        (
            {"IMAGE_PROVIDER": "cloudflare", **CF_ENV, hf.API_KEY_ENV: HF_KEY, hf.API_SECRET_ENV: HF_SECRET},
            "cloudflare",
            CloudflareImageModel,
        ),
        ({"IMAGE_PROVIDER": "openai", **CF_ENV, "IMAGE_API_KEY": OPENAI_KEY}, "openai", OpenAIImageModel),
        ({cf.TOKEN_ENV: TOKEN, "IMAGE_API_KEY": OPENAI_KEY}, "openai", OpenAIImageModel),  # a wrangler token alone
        ({"IMAGE_PROVIDER": "cloudflare", "IMAGE_API_KEY": OPENAI_KEY}, "placeholder", type(None)),
        ({**CF_ENV, cf.MODEL_ENV: "@cf/nope/model"}, "placeholder", type(None)),
        ({cf.ACCOUNT_ENV: "../evil", cf.TOKEN_ENV: TOKEN}, "placeholder", type(None)),
        ({**CF_ENV, cf.URL_ENV: "http://api.cloudflare.com/client/v4"}, "placeholder", type(None)),
    ],
)
def test_production_builds_the_chosen_provider(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, env: dict[str, str], label: str, cls: type
) -> None:
    caplog.set_level(logging.DEBUG)
    svc = _production_env(monkeypatch, env)
    assert svc.labels["art"] == label
    assert isinstance(svc.art.image_model, cls)
    assert f"art={label}" in production.describe(svc)
    assert_no_secret(caplog.text)
    assert preflight.modes(dict(**env, ANTHROPIC_API_KEY="sk-ant-dummy"), compiler="x")["art"] == label


def test_a_cloudflare_model_that_cannot_be_built_gives_placeholder_art(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.ERROR)

    def broken(*_args: Any, **_kwargs: Any) -> Any:
        raise UnicodeEncodeError("ascii", "x", 0, 1, f"bad {TOKEN} {ACCOUNT}")

    monkeypatch.setattr(production, "CloudflareImageModel", broken)
    svc = _production_env(monkeypatch, CF_ENV)
    assert svc.labels["art"] == "placeholder" and svc.art.image_model is None
    assert "the cloudflare image model could not be built (UnicodeEncodeError)" in caplog.text
    assert_no_secret(caplog.text)


def test_unknown_provider_lists_cloudflare(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING)
    svc = _production_env(monkeypatch, {"IMAGE_PROVIDER": "midjourney", **CF_ENV})
    assert svc.labels["art"] == "cloudflare"
    assert "is not one of higgsfield, openai, cloudflare" in caplog.text


# --------------------------------------------------------------------------- preflight


def art_report(env: dict[str, str], **kwargs: Any) -> preflight.ServiceReport:
    env = {"STITCH_FAKE_DB": "1", **env}
    return {r.service: r for r in preflight.build_report(env, **kwargs)}["art"]


def var_of(report: preflight.ServiceReport, name: str) -> preflight.VarReport:
    return next(v for v in report.vars if v.name == name)


CF_NAMES = (cf.ACCOUNT_ENV, cf.TOKEN_ENV, cf.URL_ENV, cf.MODEL_ENV, cf.STYLE_ENV)


def test_preflight_without_keys_lists_every_cloudflare_var_unset() -> None:
    report = art_report({})
    assert report.mode == "placeholder"
    for name in CF_NAMES:
        assert not var_of(report, name).set
        assert name in preflight.ENV_NAMES
    assert cf.DEFAULT_MODEL in var_of(report, cf.MODEL_ENV).shown
    assert report.errors() == []


def test_preflight_with_cloudflare_keys_shows_lengths_only() -> None:
    report = art_report(CF_ENV)
    assert report.mode == "cloudflare"
    assert f"{cf.DEFAULT_MODEL} at https://api.cloudflare.com/client/v4" in report.summary
    assert var_of(report, cf.ACCOUNT_ENV).shown == f"set ({len(ACCOUNT)} chars), format ok"
    assert var_of(report, cf.TOKEN_ENV).shown == f"set ({len(TOKEN)} chars), format ok"
    assert report.errors() == [] and report.warnings() == []
    assert any("about 96 a day" in n for n in report.notes)
    assert "00:00 UTC" in (report.fallback or "")
    assert_no_secret(json.dumps(report.as_json()))


@pytest.mark.parametrize("argv", [[], ["--json"]])
def test_preflight_output_never_shows_the_token_or_account(argv: list[str]) -> None:
    out = io.StringIO()
    code = preflight.main(argv, environ={"STITCH_NO_DOTENV": "1", "STITCH_FAKE_DB": "1", **CF_ENV}, out=out)
    text = out.getvalue()
    assert_no_secret(text)
    if argv:
        art = next(r for r in json.loads(text) if r["service"] == "art")
        assert art["mode"] == "cloudflare"
    else:
        assert "[art]  mode: cloudflare" in text
    assert code == 1  # no ANTHROPIC_API_KEY: the model block errors, art does not


def test_preflight_format_checks() -> None:
    report = art_report({cf.ACCOUNT_ENV: "../evil", cf.TOKEN_ENV: TOKEN})
    assert report.mode == "placeholder"
    assert any("letters and digits only" in e for e in report.errors())
    assert "Cloudflare is selected" in report.summary
    report = art_report({cf.ACCOUNT_ENV: "ABC123", cf.TOKEN_ENV: TOKEN})
    assert report.mode == "cloudflare" and report.errors() == []
    assert any("32 lowercase hex" in w for w in report.warnings())
    report = art_report({cf.ACCOUNT_ENV: ACCOUNT, cf.TOKEN_ENV: f'"{TOKEN}"'})
    assert any("quotes" in e for e in report.errors())
    report = art_report({**CF_ENV, cf.MODEL_ENV: "@cf/black-forest-labs/flux-1-schnell"})
    assert report.mode == "placeholder"
    assert any("not a supported model" in e for e in report.errors())
    report = art_report({**CF_ENV, cf.URL_ENV: "http://api.cloudflare.com/client/v4"})
    assert any("must be https" in e for e in report.errors())


def test_preflight_one_variable_without_the_other() -> None:
    report = art_report({cf.TOKEN_ENV: TOKEN})  # not selected: a warning only
    assert report.errors() == []
    assert any(f"needed with {cf.TOKEN_ENV}" in w for w in report.warnings())
    report = art_report({"IMAGE_PROVIDER": "cloudflare", cf.TOKEN_ENV: TOKEN})  # selected: an error
    assert any(f"needed with {cf.TOKEN_ENV}" in e for e in report.errors())


def test_preflight_style_suffix_is_shown() -> None:
    assert "none (no suffix)" == var_of(art_report({**CF_ENV, cf.STYLE_ENV: "none"}), cf.STYLE_ENV).shown
    assert "'Paper.'" == var_of(art_report({**CF_ENV, cf.STYLE_ENV: "Paper."}), cf.STYLE_ENV).shown


def _live(env: dict[str, str], handler: Any) -> preflight.ServiceReport:
    return art_report(env, seams=preflight.LiveSeams(http_transport=httpx.MockTransport(handler)), live=True)


def test_preflight_live_lists_models_and_draws_nothing() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200, json={"success": True, "errors": [], "messages": [], "result": [{"name": cf.DEFAULT_MODEL}]}
        )

    report = _live(CF_ENV, handler)
    assert report.live.status == "pass"
    assert f"200, {cf.DEFAULT_MODEL} is offered" in report.live.detail
    assert len(seen) == 1
    request = seen[0]
    assert request.method == "GET"
    assert request.url.path == f"/client/v4/accounts/{ACCOUNT}/ai/models/search"
    assert request.url.params["search"] == "flux-2-klein-4b"
    assert request.headers["authorization"] == f"Bearer {TOKEN}"
    assert "/ai/run/" not in str(request.url)
    assert_no_secret(report.live.detail)


def test_preflight_live_warns_when_the_model_is_not_listed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"success": True, "result": [{"name": "@cf/other/model"}]})

    report = _live(CF_ENV, handler)
    assert report.live.status == "warn" and "is not among" in report.live.detail


def test_preflight_live_failure_is_scrubbed_in_text_and_json() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403, json={"success": False, "errors": [{"code": 10000, "message": f"bad token {TOKEN} for {ACCOUNT}"}]}
        )

    report = _live(CF_ENV, handler)
    assert report.live.status == "fail" and "HTTP 403" in report.live.detail
    assert_no_secret(report.live.detail)
    for argv in (["--live"], ["--live", "--json"]):
        out = io.StringIO()
        preflight.main(
            argv,
            environ={"STITCH_NO_DOTENV": "1", "STITCH_FAKE_DB": "1", **CF_ENV},
            out=out,
            seams=preflight.LiveSeams(http_transport=httpx.MockTransport(handler)),
        )
        text = out.getvalue()
        assert "HTTP 403" in text
        assert_no_secret(text)


def test_preflight_live_does_not_follow_redirects() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(302, headers={"location": "https://evil.example.com/collect"})

    report = _live(CF_ENV, handler)
    assert report.live.status == "fail" and "HTTP 302" in report.live.detail
    assert [r.url.host for r in seen] == ["api.cloudflare.com"]


def test_preflight_live_skips_a_setup_that_cannot_build() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no call expected")

    report = _live({"IMAGE_PROVIDER": "cloudflare", cf.ACCOUNT_ENV: "../evil", cf.TOKEN_ENV: TOKEN}, refuse)
    assert report.live.status == "skipped"


def test_readme_documents_the_allowance_arithmetic_and_the_label() -> None:
    from pathlib import Path

    readme = (Path(preflight.__file__).parent / "README.md").read_text()
    assert "art=cloudflare" in readme
    assert "https://developers.cloudflare.com/workers-ai/platform/pricing/" in readme
    assert "about 96" in readme and "26.05" in readme
    for name in CF_NAMES:
        assert f"`{name}`" in readme
