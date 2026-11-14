"""HiggsfieldImageModel, the image-provider rule and their preflight report. All offline:
httpx.MockTransport for the API and the result host, a no-op sleep and a fake clock."""

from __future__ import annotations

import io
import json
import logging
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from PIL import Image

from agent import preflight, production
from agent.adapters import HiggsfieldImageModel, OpenAIImageModel, choose_image_provider, fitting
from agent.adapters import higgsfield as hf
from agent.adapters import images as images_mod
from agent.tools.art import ArtService, CanvasSizer, ImageModel

KEY_ID = "hfKEYid-0123456789abcdef"
SECRET = "hfSECRET-fedcba9876543210-secret"
REQ = "d7e6c0f3-6699-4f6c-bb45-2ad7fd9158ff"
RESULT = "https://cdn.higgsfield.ai/out/image.png"


def png(size: tuple[int, int], color: tuple[int, ...] = (200, 60, 40), mode: str = "RGB") -> bytes:
    buf = io.BytesIO()
    Image.new(mode, size, color).save(buf, format="PNG")
    return buf.getvalue()


def open_png(data: bytes) -> Image.Image:
    im = Image.open(io.BytesIO(data))
    im.load()
    return im


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


class FakeHiggsfield:
    """Scripted API + result host. ``statuses`` are served in order (the last one repeats)."""

    def __init__(
        self,
        *,
        statuses: list[Any] | None = None,
        submits: list[Any] | None = None,
        image: bytes | None = None,
        result_url: str = RESULT,
        download: Callable[[httpx.Request], httpx.Response] | None = None,
    ) -> None:
        self.statuses = statuses or [{"status": "in_progress"}, "done"]
        self.submits = submits or []
        self.image = image if image is not None else png((1024, 1024))
        self.result_url = result_url
        self.download = download
        self.requests: list[httpx.Request] = []
        self.bodies: list[dict[str, Any]] = []
        self.polls = 0
        self.cancels = 0

    def done(self) -> dict[str, Any]:
        return {"status": "completed", "request_id": REQ, "images": [{"url": self.result_url}]}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if request.url.host == "api.higgsfield.ai":
            if request.method == "POST" and path.endswith("/cancel"):
                self.cancels += 1
                return httpx.Response(202)
            if request.method == "POST":
                self.bodies.append(json.loads(request.content))
                if self.submits:
                    step = self.submits.pop(0)
                    if isinstance(step, Exception):
                        raise step
                    if isinstance(step, httpx.Response):
                        return step
                return httpx.Response(200, json={"status": "queued", "request_id": REQ})
            if path == f"/requests/{REQ}/status":
                self.polls += 1
                step = self.statuses[min(self.polls - 1, len(self.statuses) - 1)]
                if isinstance(step, Exception):
                    raise step
                if isinstance(step, httpx.Response):
                    return step
                return httpx.Response(200, json=self.done() if step == "done" else {"request_id": REQ, **step})
        if self.download is not None:
            return self.download(request)
        if request.url == httpx.URL(self.result_url):
            return httpx.Response(200, content=self.image, headers={"content-type": "image/png"})
        return httpx.Response(404)

    @property
    def submit_count(self) -> int:
        return sum(1 for r in self.requests if r.method == "POST" and not r.url.path.endswith("/cancel"))


def model_for(api: FakeHiggsfield, clock: Clock | None = None, **kwargs: Any) -> HiggsfieldImageModel:
    clock = clock or Clock()
    return HiggsfieldImageModel(
        api_key=KEY_ID,
        api_secret=SECRET,
        transport=httpx.MockTransport(api),
        sleep=clock.sleep,
        clock=clock,
        **kwargs,
    )


def assert_no_key(text: str) -> None:
    assert KEY_ID not in text and SECRET not in text


# --------------------------------------------------------------------------- protocol


def test_it_satisfies_the_image_model_and_canvas_sizer_protocols() -> None:
    model = model_for(FakeHiggsfield())
    assert isinstance(model, ImageModel) and isinstance(model, CanvasSizer)
    assert_no_key(repr(model))


def test_happy_path_submit_poll_download() -> None:
    api = FakeHiggsfield()
    out = model_for(api).generate("a red teapot", size=(800, 600))
    assert open_png(out).size == (800, 600)
    submit = api.requests[0]
    assert submit.method == "POST" and str(submit.url) == "https://api.higgsfield.ai/recraft/v4.1/text-to-image"
    assert submit.headers["authorization"] == f"Key {KEY_ID}:{SECRET}"
    assert submit.headers["idempotency-key"]
    body = api.bodies[0]
    assert body == {
        "prompt": "a red teapot\n\n" + hf.DEFAULT_STYLE_SUFFIX,
        "aspect_ratio": "4:3",
        "resolution": "1k",
        "output_format": "png",
    }
    assert api.polls == 2
    download = api.requests[-1]
    assert str(download.url) == RESULT and "authorization" not in download.headers


@pytest.mark.parametrize("model_name", sorted(hf.MODELS))
def test_every_documented_aspect_ratio_round_trips(model_name: str) -> None:
    spec = hf.MODELS[model_name]
    for ratio in spec.aspect_ratios:
        a, b = (int(x) for x in ratio.split(":"))
        size = (a * 100, b * 100)
        nominal = hf.nominal_size(ratio)
        api = FakeHiggsfield(image=png(nominal))
        model = model_for(api, model=model_name)
        assert model.aspect_ratio(size) == ratio
        assert model.canvas_size(size) == nominal
        out = model.generate("a lamp", size=size)
        assert open_png(out).size == size
        assert api.bodies[0]["aspect_ratio"] == ratio
        for key, value in spec.params.items():
            assert api.bodies[0][key] == value


def test_wide_set_layer_strip_asks_for_the_widest_ratio() -> None:
    model = model_for(FakeHiggsfield())
    assert model.aspect_ratio((2048, 464)) == "2:1"
    assert model.canvas_size((2048, 464)) == (1024, 512)
    soul = model_for(FakeHiggsfield(), model="higgsfield-ai/soul/v2/standard")
    assert soul.aspect_ratio((2048, 464)) == "16:9"


@pytest.mark.parametrize(
    ("source", "target"),
    [((1024, 512), (2048, 464)), ((1024, 1024), (600, 800)), ((1024, 731), (1530, 1024)), ((683, 1024), (300, 300))],
)
def test_fitting_matches_the_openai_adapter(source: tuple[int, int], target: tuple[int, int]) -> None:
    im = Image.new("RGB", source)
    for y in range(source[1]):  # a vertical gradient, so a different crop gives different pixels
        im.paste((y % 256, 40, 200 - y % 200), (0, y, source[0], y + 1))
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    data = buf.getvalue()
    api = FakeHiggsfield(image=data)
    via_hf = model_for(api).generate("x", size=target)
    assert via_hf == images_mod._to_png(data, target) == fitting.to_png(data, target)
    assert images_mod.fit_to_size is fitting.fit_to_size


def test_alpha_is_kept() -> None:
    api = FakeHiggsfield(image=png((1024, 1024), (10, 20, 30, 0), mode="RGBA"))
    assert open_png(model_for(api).generate("x", size=(500, 500))).mode == "RGBA"


def test_style_suffix_is_configurable_and_can_be_turned_off(monkeypatch: pytest.MonkeyPatch) -> None:
    api = FakeHiggsfield()
    model_for(api, style_suffix="none").generate("a cup", size=(100, 100))
    assert api.bodies[0]["prompt"] == "a cup"
    monkeypatch.setenv(hf.STYLE_ENV, "Paper only.")
    api = FakeHiggsfield()
    model_for(api).generate("a cup", size=(100, 100))
    assert api.bodies[0]["prompt"] == "a cup\n\nPaper only."


def test_a_prompt_over_the_model_limit_raises_before_submitting() -> None:
    api = FakeHiggsfield()
    with pytest.raises(RuntimeError, match="at most 10000"):
        model_for(api).generate("x" * 10001, size=(100, 100))
    assert api.requests == []


# --------------------------------------------------------------------------- polling


def test_polling_backs_off_and_stops_at_the_deadline() -> None:
    clock = Clock()
    api = FakeHiggsfield(statuses=[{"status": "in_progress"}])
    with pytest.raises(RuntimeError, match=f"{REQ} did not finish within 120 s .last status in_progress") as exc:
        model_for(api, clock).generate("x", size=(100, 100))
    assert clock.now - 1000.0 <= hf.DEFAULT_DEADLINE_S
    assert clock.sleeps[:4] == [2.0, 3.0, 4.5, 6.75]
    assert max(clock.sleeps) == hf.POLL_MAX_S
    assert api.submit_count == 1 and api.cancels == 0  # in progress cannot be cancelled
    assert_no_key(str(exc.value))


def test_a_request_still_queued_at_the_deadline_is_cancelled() -> None:
    api = FakeHiggsfield(statuses=[{"status": "queued"}])
    with pytest.raises(RuntimeError, match="last status queued"):
        model_for(api, deadline_s=10).generate("x", size=(100, 100))
    assert api.cancels == 1


@pytest.mark.parametrize("state", ["failed", "nsfw", "canceled"])
def test_a_terminal_failure_raises_with_its_state(state: str) -> None:
    api = FakeHiggsfield(statuses=[{"status": state, "error": f"Generation {state} for {SECRET}"}])
    with pytest.raises(RuntimeError, match=f"ended {state}") as exc:
        model_for(api).generate("x", size=(100, 100))
    assert_no_key(str(exc.value))
    assert api.submit_count == 1


def test_status_5xx_and_dropped_polls_are_retried_then_succeed() -> None:
    api = FakeHiggsfield(statuses=[httpx.Response(503), httpx.ConnectError("down"), "done"])
    assert open_png(model_for(api).generate("x", size=(64, 64))).size == (64, 64)
    assert api.submit_count == 1 and api.polls == 3


def test_status_failing_too_often_gives_up() -> None:
    api = FakeHiggsfield(statuses=[httpx.Response(500)])
    with pytest.raises(RuntimeError, match="status check kept failing .HTTP 500"):
        model_for(api).generate("x", size=(64, 64))
    assert api.polls == hf.MAX_POLL_ERRORS + 1 and api.submit_count == 1


def test_status_401_stops_at_once() -> None:
    api = FakeHiggsfield(statuses=[httpx.Response(401, json={"detail": "Invalid credentials"})])
    with pytest.raises(RuntimeError, match="HTTP 401: Invalid credentials"):
        model_for(api).generate("x", size=(64, 64))
    assert api.polls == 1


# --------------------------------------------------------------------------- submit retries


@pytest.mark.parametrize("status", [408, 429, 500, 502, 503, 504])
def test_a_fast_submit_failure_is_retried_once_with_the_same_idempotency_key(status: int) -> None:
    clock = Clock()
    api = FakeHiggsfield(submits=[httpx.Response(status, headers={"retry-after": "3"})])
    model_for(api, clock).generate("x", size=(64, 64))
    submits = [r for r in api.requests if r.method == "POST"]
    assert len(submits) == 2
    assert submits[0].headers["idempotency-key"] == submits[1].headers["idempotency-key"]
    assert clock.sleeps[0] == 3.0


def test_a_submit_failing_twice_raises_with_the_status_and_message() -> None:
    queue_full = {"detail": {"code": "account_queue_full", "message": "Too many queued generations", "retryable": True}}
    api = FakeHiggsfield(submits=[httpx.Response(429, json=queue_full), httpx.Response(429, json=queue_full)])
    with pytest.raises(RuntimeError, match="HTTP 429: Too many queued generations"):
        model_for(api).generate("x", size=(64, 64))
    assert api.submit_count == 2


def test_a_connection_failure_on_submit_is_retried_once() -> None:
    api = FakeHiggsfield(submits=[httpx.ConnectError("refused")])
    model_for(api).generate("x", size=(64, 64))
    assert api.submit_count == 2


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
def test_a_client_error_on_submit_is_not_retried(status: int) -> None:
    api = FakeHiggsfield(submits=[httpx.Response(status, json={"detail": [{"msg": f"bad field {KEY_ID}"}]})])
    with pytest.raises(RuntimeError, match=f"HTTP {status}: bad field") as exc:
        model_for(api).generate("x", size=(64, 64))
    assert api.submit_count == 1
    assert_no_key(str(exc.value))


def test_never_resubmits_once_a_request_id_exists() -> None:
    api = FakeHiggsfield(statuses=[httpx.Response(502), httpx.Response(502), {"status": "failed"}])
    with pytest.raises(RuntimeError, match="ended failed"):
        model_for(api).generate("x", size=(64, 64))
    assert api.submit_count == 1


def test_a_submit_answer_without_a_usable_request_id_raises() -> None:
    for answer in ({"status": "queued"}, {"request_id": "../../etc/passwd"}):
        api = FakeHiggsfield(submits=[httpx.Response(200, json=answer)])
        with pytest.raises(RuntimeError, match="request_id"):
            model_for(api).generate("x", size=(64, 64))
        assert api.polls == 0


# --------------------------------------------------------------------------- the download boundary


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example.com/image.png",
        "https://higgsfield.ai.evil.com/x.png",
        "http://cdn.higgsfield.ai/x.png",
        "https://user:pw@cdn.higgsfield.ai/x.png",
        "https://169.254.169.254/latest/meta-data",
        "file:///etc/passwd",
    ],
)
def test_a_result_url_off_the_allowlist_is_refused_without_fetching(url: str) -> None:
    api = FakeHiggsfield(result_url=url)
    with pytest.raises(RuntimeError, match="result URL refused"):
        model_for(api).generate("x", size=(64, 64))
    assert not any(r.url.host != "api.higgsfield.ai" for r in api.requests)


def test_result_hosts_env_extends_the_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(hf.RESULT_HOSTS_ENV, " d111.cloudfront.net , *.files.example.org")
    api = FakeHiggsfield(result_url="https://d111.cloudfront.net/a.png")
    assert open_png(model_for(api).generate("x", size=(64, 64))).size == (64, 64)
    assert hf.configured_result_hosts() == ("higgsfield.ai", "d111.cloudfront.net", "files.example.org")


def test_a_redirect_to_a_foreign_host_is_refused() -> None:
    def download(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://evil.example.com/steal"})

    api = FakeHiggsfield(download=download)
    with pytest.raises(RuntimeError, match="host 'evil.example.com' is not an allowed result host"):
        model_for(api).generate("x", size=(64, 64))
    assert not any(r.url.host == "evil.example.com" for r in api.requests)


def test_a_redirect_within_allowed_hosts_is_followed() -> None:
    image = png((300, 300))

    def download(request: httpx.Request) -> httpx.Response:
        if request.url.host == "cdn.higgsfield.ai":
            return httpx.Response(302, headers={"location": "https://files.higgsfield.ai/final.png"})
        return httpx.Response(200, content=image, headers={"content-type": "image/png"})

    out = model_for(FakeHiggsfield(download=download)).generate("x", size=(64, 64))
    assert open_png(out).size == (64, 64)


def test_endless_redirects_are_refused() -> None:
    def download(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://cdn.higgsfield.ai/again.png"})

    with pytest.raises(RuntimeError, match="more than 3 redirects"):
        model_for(FakeHiggsfield(download=download)).generate("x", size=(64, 64))


def test_an_oversized_download_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hf, "MAX_DOWNLOAD_BYTES", 1000)
    big = png((1024, 1024), (1, 2, 3)) + b"\0" * 2000

    def declared(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=big, headers={"content-type": "image/png"})

    with pytest.raises(RuntimeError, match="larger than"):
        model_for(FakeHiggsfield(download=declared)).generate("x", size=(64, 64))

    def undeclared(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, stream=httpx.ByteStream(big), headers={"content-type": "image/png"}
        )  # no content-length: counted while streaming

    with pytest.raises(RuntimeError, match="larger than"):
        model_for(FakeHiggsfield(download=undeclared)).generate("x", size=(64, 64))


@pytest.mark.parametrize("ctype", ["text/html", "application/json", ""])
def test_a_non_image_content_type_is_refused(ctype: str) -> None:
    def download(request: httpx.Request) -> httpx.Response:
        headers = {"content-type": ctype} if ctype else {}
        return httpx.Response(200, content=png((10, 10)), headers=headers)

    with pytest.raises(RuntimeError, match="is not an image"):
        model_for(FakeHiggsfield(download=download)).generate("x", size=(64, 64))


def test_a_completed_request_without_images_raises() -> None:
    api = FakeHiggsfield(statuses=[{"status": "completed", "images": []}])
    with pytest.raises(RuntimeError, match="completed with no image"):
        model_for(api).generate("x", size=(64, 64))


# --------------------------------------------------------------------------- references


def test_references_are_ignored_with_one_warning_per_process(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(hf, "_reference_warned", False)
    caplog.set_level(logging.WARNING, logger=hf.__name__)
    api = FakeHiggsfield()
    model = model_for(api)
    model.generate("x", reference_images=[png((50, 50))], size=(64, 64))
    model.generate("y", reference_images=[b"not an image at all"], size=(64, 64))
    warnings = [r for r in caplog.records if "reference" in r.getMessage()]
    assert len(warnings) == 1
    assert set(api.bodies[0]) == {"prompt", "aspect_ratio", "resolution", "output_format"}
    assert api.submit_count == 2


def test_art_service_draws_a_prop_through_higgsfield() -> None:
    from agent.adapters import AlphaAwareRemover

    api = FakeHiggsfield(image=png((1024, 1024), (255, 255, 255)))
    service = ArtService(model_for(api), AlphaAwareRemover())
    assert service.enabled


# --------------------------------------------------------------------------- secrets and config


def test_the_key_never_appears_in_any_error(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    echo = f"{KEY_ID}:{SECRET} {KEY_ID} {SECRET}"
    cases = [
        FakeHiggsfield(submits=[httpx.Response(401, json={"detail": f"bad key {echo}"})]),
        FakeHiggsfield(statuses=[httpx.Response(404, json={"detail": f"no {echo}"})]),
        FakeHiggsfield(statuses=[{"status": "failed", "error": echo}]),
        FakeHiggsfield(submits=[httpx.ConnectError(echo), httpx.ConnectError(echo)]),
    ]
    for api in cases:
        with pytest.raises(RuntimeError) as exc:
            model_for(api).generate("x", size=(64, 64))
        assert_no_key(str(exc.value))
    assert_no_key(caplog.text)


def test_combined_id_secret_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(hf.API_KEY_ENV, f"{KEY_ID}:{SECRET}")
    api = FakeHiggsfield()
    clock = Clock()
    HiggsfieldImageModel(transport=httpx.MockTransport(api), sleep=clock.sleep, clock=clock).generate(
        "x", size=(64, 64)
    )
    assert api.requests[0].headers["authorization"] == f"Key {KEY_ID}:{SECRET}"


def test_missing_credentials_or_unknown_model_raise_without_the_key(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(RuntimeError, match="HIGGSFIELD_API_KEY is not set"):
        HiggsfieldImageModel()
    monkeypatch.setenv(hf.API_KEY_ENV, KEY_ID)
    with pytest.raises(RuntimeError, match="HIGGSFIELD_API_SECRET is not set") as exc:
        HiggsfieldImageModel()
    assert_no_key(str(exc.value))
    monkeypatch.setenv(hf.API_SECRET_ENV, SECRET)
    monkeypatch.setenv(hf.MODEL_ENV, "ideogram/v4.0")
    with pytest.raises(RuntimeError, match="not a supported endpoint") as exc:
        HiggsfieldImageModel()
    assert_no_key(str(exc.value))
    assert "not a supported endpoint" in (hf.config_problem() or "")


@pytest.mark.parametrize("bad", [{"deadline_s": 0}, {"deadline_s": float("nan")}, {"max_retries": -1}])
def test_bad_bounds_are_refused(bad: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        HiggsfieldImageModel(api_key=KEY_ID, api_secret=SECRET, **bad)


# --------------------------------------------------------------------------- provider selection

OPENAI_KEY = "sk-openai-dummy-0000"


@pytest.mark.parametrize("provider", [None, "higgsfield", "openai", "HiggsField", "dalle"])
@pytest.mark.parametrize("has_hf", [False, True])
@pytest.mark.parametrize("has_openai", [False, True])
def test_provider_selection_for_every_combination(provider: str | None, has_hf: bool, has_openai: bool) -> None:
    env: dict[str, str] = {}
    if provider is not None:
        env["IMAGE_PROVIDER"] = provider
    if has_hf:
        env[hf.API_KEY_ENV] = f"{KEY_ID}:{SECRET}"
    if has_openai:
        env[images_mod.API_KEY_ENV] = OPENAI_KEY
    choice = choose_image_provider(env)
    wanted = (provider or "").lower()
    if wanted == "higgsfield":
        expected = "higgsfield" if has_hf else "placeholder"
    elif wanted == "openai":
        expected = "openai" if has_openai else "placeholder"
    else:
        expected = "higgsfield" if has_hf else "openai" if has_openai else "placeholder"
    assert choice.provider == expected
    assert (choice.warning is not None) == (provider == "dalle")
    assert preflight.modes(env, compiler="FakeCompiler")["art"] == expected


def _production_env(monkeypatch: pytest.MonkeyPatch, env: dict[str, str]) -> Any:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-dummy")
    monkeypatch.setenv("STITCH_FAKE_DB", "1")
    for name in ("IMAGE_API_KEY", "ELEVENLABS_API_KEY", "MONGODB_URI", "STITCH_WEB_URL"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    return production.production_services()


@pytest.mark.parametrize(
    ("env", "label", "cls"),
    [
        ({hf.API_KEY_ENV: KEY_ID, hf.API_SECRET_ENV: SECRET}, "higgsfield", HiggsfieldImageModel),
        ({hf.API_KEY_ENV: KEY_ID, hf.API_SECRET_ENV: SECRET, "IMAGE_API_KEY": OPENAI_KEY}, "higgsfield", HiggsfieldImageModel),
        (
            {"IMAGE_PROVIDER": "openai", hf.API_KEY_ENV: KEY_ID, hf.API_SECRET_ENV: SECRET, "IMAGE_API_KEY": OPENAI_KEY},
            "openai",
            OpenAIImageModel,
        ),
        ({"IMAGE_API_KEY": OPENAI_KEY}, "openai", OpenAIImageModel),
        ({"IMAGE_PROVIDER": "higgsfield", "IMAGE_API_KEY": OPENAI_KEY}, "placeholder", type(None)),
        ({hf.API_KEY_ENV: KEY_ID}, "placeholder", type(None)),  # no secret: logged, placeholder
        ({hf.API_KEY_ENV: f"{KEY_ID}:{SECRET}", hf.MODEL_ENV: "nope/model"}, "placeholder", type(None)),
        ({}, "placeholder", type(None)),
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
    assert_no_key(caplog.text)
    assert preflight.modes(dict(**env, ANTHROPIC_API_KEY="sk-ant-dummy"), compiler="x")["art"] == label


def test_unknown_provider_warns_and_uses_the_default(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING)
    svc = _production_env(monkeypatch, {"IMAGE_PROVIDER": "midjourney", hf.API_KEY_ENV: f"{KEY_ID}:{SECRET}"})
    assert svc.labels["art"] == "higgsfield"
    assert "IMAGE_PROVIDER='midjourney' is not one of higgsfield, openai" in caplog.text


# --------------------------------------------------------------------------- preflight


def art_report(env: dict[str, str], **kwargs: Any) -> preflight.ServiceReport:
    env = {"STITCH_FAKE_DB": "1", **env}
    return {r.service: r for r in preflight.build_report(env, **kwargs)}["art"]


def var_of(report: preflight.ServiceReport, name: str) -> preflight.VarReport:
    return next(v for v in report.vars if v.name == name)


def test_preflight_without_keys_reports_placeholder_and_every_higgsfield_var() -> None:
    report = art_report({})
    assert report.mode == "placeholder"
    assert "neither HIGGSFIELD_API_KEY nor IMAGE_API_KEY is set" in report.summary
    for name in (
        "IMAGE_PROVIDER",
        hf.API_KEY_ENV,
        hf.API_SECRET_ENV,
        hf.URL_ENV,
        hf.MODEL_ENV,
        hf.STYLE_ENV,
        hf.RESULT_HOSTS_ENV,
    ):
        assert not var_of(report, name).set
        assert name in preflight.ENV_NAMES
    assert "recraft/v4.1/text-to-image" in var_of(report, hf.MODEL_ENV).shown
    assert report.errors() == []


def test_preflight_with_higgsfield_keys_reports_provider_model_and_url_never_the_values() -> None:
    env = {hf.API_KEY_ENV: KEY_ID, hf.API_SECRET_ENV: SECRET}
    report = art_report(env)
    assert report.mode == "higgsfield"
    assert "recraft/v4.1/text-to-image at https://api.higgsfield.ai" in report.summary
    assert var_of(report, hf.API_KEY_ENV).shown == f"set ({len(KEY_ID)} chars), format ok"
    assert var_of(report, hf.API_SECRET_ENV).shown == f"set ({len(SECRET)} chars), format ok"
    assert report.errors() == []
    assert any("reference" in n for n in report.notes)
    out = io.StringIO()
    code = preflight.main([], environ={"STITCH_NO_DOTENV": "1", **env}, out=out)
    text = out.getvalue()
    assert_no_key(text)
    assert "[art]  mode: higgsfield" in text
    assert code == 1  # no ANTHROPIC_API_KEY: the model block errors, art does not


def test_preflight_flags_a_key_without_a_secret_and_an_unknown_model() -> None:
    report = art_report({hf.API_KEY_ENV: KEY_ID})
    assert report.mode == "placeholder"
    assert any("no secret" in e for e in report.errors())
    report = art_report({hf.API_KEY_ENV: f"{KEY_ID}:{SECRET}", hf.MODEL_ENV: "ideogram/v4.0"})
    assert report.mode == "placeholder"
    assert any("not a supported endpoint" in e for e in report.errors())
    report = art_report({hf.API_KEY_ENV: f'"{KEY_ID}"', hf.API_SECRET_ENV: SECRET})
    assert any("quotes" in e for e in report.errors())


def test_preflight_live_uses_the_free_estimate_endpoint() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"usd": "0.04"})

    env = {hf.API_KEY_ENV: KEY_ID, hf.API_SECRET_ENV: SECRET}
    report = art_report(env, seams=preflight.LiveSeams(http_transport=httpx.MockTransport(handler)), live=True)
    assert report.live.status == "pass"
    assert "POST /estimate/recraft/v4.1/text-to-image: 200, about $0.04 per image" in report.live.detail
    assert len(seen) == 1
    assert str(seen[0].url) == "https://api.higgsfield.ai/estimate/recraft/v4.1/text-to-image"
    assert seen[0].headers["authorization"] == f"Key {KEY_ID}:{SECRET}"
    assert json.loads(seen[0].content)["aspect_ratio"] == "1:1"


def test_preflight_live_failure_is_scrubbed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"detail": f"Invalid credentials {KEY_ID}:{SECRET}"})

    env = {hf.API_KEY_ENV: KEY_ID, hf.API_SECRET_ENV: SECRET}
    report = art_report(env, seams=preflight.LiveSeams(http_transport=httpx.MockTransport(handler)), live=True)
    assert report.live.status == "fail" and "HTTP 401" in report.live.detail
    assert_no_key(report.live.detail)


def test_preflight_live_skips_a_higgsfield_setup_that_cannot_build() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no call expected")

    report = art_report(
        {hf.API_KEY_ENV: KEY_ID}, seams=preflight.LiveSeams(http_transport=httpx.MockTransport(refuse)), live=True
    )
    assert report.live.status == "skipped"
