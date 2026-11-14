"""Set layers keep their proportions: the image adapter crops instead of stretching, and
``ArtService.make_layer`` tells the model which band of its canvas is kept.

All offline: ``OpenAIImageModel`` talks to an ``httpx.MockTransport``.
"""

from __future__ import annotations

import base64
import io
import json

import httpx
import pytest
from PIL import Image, ImageChops, ImageDraw

from agent.adapters import OpenAIImageModel
from agent.adapters import images as images_mod
from agent.tools import art
from agent.tools.art import (
    ArtService,
    CanvasSizer,
    Cutout,
    FakeBackgroundRemover,
    FakeImageModel,
    build_art_prompt,
    kept_fraction,
    layer_band_line,
)
from agent.tools.sets import LAYER_HEIGHT, layer_size

SQUARE = 100


def _png(im: Image.Image) -> bytes:
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def _open(data: bytes) -> Image.Image:
    with Image.open(io.BytesIO(data)) as im:
        im.load()
        return im.copy()


def square_canvas(size=(1536, 1024), *, side: int = SQUARE, transparent: bool = True) -> Image.Image:
    """A canvas with a black ``side`` x ``side`` square centered horizontally, touching the bottom edge."""
    w, h = size
    if transparent:
        im = Image.new("RGBA", size, (255, 255, 255, 0))
        fill: tuple[int, ...] = (0, 0, 0, 255)
    else:
        im = Image.new("RGB", size, (255, 255, 255))
        fill = (0, 0, 0)
    left = (w - side) // 2
    ImageDraw.Draw(im).rectangle((left, h - side, left + side - 1, h - 1), fill=fill)
    return im


class ImageAPI:
    """MockTransport handler that returns one fixed image and records each request."""

    def __init__(self, image: Image.Image) -> None:
        self.b64 = base64.b64encode(_png(image)).decode()
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(200, json={"data": [{"b64_json": self.b64}]})

    def body(self, i: int = -1) -> dict:
        return json.loads(self.requests[i].content)


def model_for(api: ImageAPI) -> OpenAIImageModel:
    return OpenAIImageModel(api_key="sk-test", transport=httpx.MockTransport(api))


def opaque_bbox(im: Image.Image) -> tuple[int, int, int, int]:
    alpha = im.convert("RGBA").getchannel("A").point(lambda a: 255 if a >= 128 else 0)
    bbox = alpha.getbbox()
    assert bbox is not None, "the square disappeared"
    return bbox


def bbox_aspect(bbox: tuple[int, int, int, int]) -> float:
    left, top, right, bottom = bbox
    return (right - left) / (bottom - top)


# --------------------------------------------------------------------------- adapter: crop, never stretch


@pytest.mark.parametrize("size", [(2048, 464), (2048, 192)])
def test_wide_strip_is_cropped_not_squashed(size) -> None:
    api = ImageAPI(square_canvas((1536, 1024)))
    out = _open(model_for(api).generate("a strip", size=size))

    assert api.body()["size"] == "1536x1024"
    assert out.size == size
    bbox = opaque_bbox(out)
    assert abs(bbox_aspect(bbox) - 1.0) <= 0.02
    assert bbox[3] == size[1]  # still touches the bottom row
    # Centered horizontally, as on the canvas.
    assert abs((bbox[0] + bbox[2]) / 2 - size[0] / 2) <= 2


def test_matching_aspect_is_not_cropped() -> None:
    source = square_canvas((1024, 1024))
    api = ImageAPI(source)
    out = _open(model_for(api).generate("a square", size=(1024, 1024)))

    assert api.body()["size"] == "1024x1024"
    assert out.size == (1024, 1024)
    assert out.mode == source.mode
    assert ImageChops.difference(out, source).getbbox() is None  # pixel-identical


def test_near_matching_aspect_is_resized_whole() -> None:
    """Within 1% of the target aspect the whole canvas is kept (a resize, no crop)."""
    source = Image.new("RGB", (1536, 1024), (255, 255, 255))
    ImageDraw.Draw(source).rectangle((0, 0, 9, 9), fill=(0, 0, 0))  # top-left marker
    out = images_mod.fit_to_size(source, (1530, 1024))  # aspect differs by 0.4%
    assert out.size == (1530, 1024)
    assert out.getpixel((2, 2)) == (0, 0, 0)  # top-left survives: nothing was cropped


def test_tall_target_crops_the_sides_centered() -> None:
    source = Image.new("RGB", (1536, 1024), (255, 255, 255))
    ImageDraw.Draw(source).rectangle((718, 0, 817, 1023), fill=(0, 0, 0))  # centered column
    out = images_mod.fit_to_size(source, (1000, 1000))
    assert out.size == (1000, 1000)
    dark = out.convert("L").point(lambda v: 255 if v < 128 else 0)  # ignore resampling fringe
    left, _, right, _ = dark.getbbox()
    assert abs((left + right) / 2 - 500) <= 2
    assert right - left == pytest.approx(100 * 1000 / 1024, abs=2)  # even scale, no squash


def test_canvas_size_is_the_requested_api_size() -> None:
    model = model_for(ImageAPI(square_canvas()))
    assert isinstance(model, CanvasSizer)
    assert model.canvas_size((2048, 464)) == (1536, 1024)
    assert model.canvas_size((2048, 192)) == (1536, 1024)
    assert model.canvas_size((1024, 1024)) == (1024, 1024)
    assert model.canvas_size((600, 1000)) == (1024, 1536)


@pytest.mark.parametrize("layer", sorted(LAYER_HEIGHT))
def test_no_anisotropic_scaling_for_any_layer(layer: str) -> None:
    size = layer_size(LAYER_HEIGHT[layer])
    canvas = images_mod.closest_size(size)
    side = 120
    api = ImageAPI(square_canvas(canvas, side=side))
    out = _open(model_for(api).generate(f"{layer} layer", size=size))

    assert out.size == size
    assert api.body()["size"] == f"{canvas[0]}x{canvas[1]}"
    content_aspect = 1.0  # the generated content is a square
    distortion = bbox_aspect(opaque_bbox(out)) / content_aspect
    assert max(distortion, 1 / distortion) <= 1.01, f"{layer}: distortion {distortion:.4f}"


# --------------------------------------------------------------------------- make_layer: the band line


def _layer_prompt(layer: str) -> str:
    return build_art_prompt("church steps on a sunny morning", kind="set_layer", layer=layer)


@pytest.mark.parametrize(("layer", "size", "pct"), [("middle", (2048, 464), 34), ("front", (2048, 192), 14)])
def test_make_layer_tells_the_model_the_bottom_band(layer: str, size, pct: int) -> None:
    api = ImageAPI(square_canvas((1536, 1024), transparent=False))
    service = ArtService(model_for(api), FakeBackgroundRemover())
    prompt = _layer_prompt(layer)

    cut = service.make_layer(prompt, layer=layer, size=size)

    sent = api.body()["prompt"]
    assert api.body()["size"] == "1536x1024"
    assert sent.startswith(prompt)
    band = sent[len(prompt):].strip()
    assert band.count("\n") == 0  # exactly one added line
    assert f"bottom {pct}%" in band
    assert f"top {100 - pct}%" in band
    assert "plain pure white" in band
    # The existing contract still holds.
    assert isinstance(cut, Cutout)
    assert _open(cut.png).size == size
    assert cut.bbox == (0, 0, size[0], size[1])
    assert cut.aspect == pytest.approx(size[0] / size[1])
    assert cut.anchor == [0.5, 1.0]


def test_make_layer_back_says_the_top_is_cut_off() -> None:
    api = ImageAPI(square_canvas((1536, 1024), transparent=False))
    service = ArtService(model_for(api), FakeBackgroundRemover())
    prompt = _layer_prompt("back")

    cut = service.make_layer(prompt, layer="back", size=(2048, 1024))

    band = api.body()["prompt"][len(prompt):].strip()
    assert "top 25%" in band and "cut off" in band
    assert "bottom 75%" in band
    assert "white" not in band  # the back layer is full-bleed
    assert cut is not None and _open(cut.png).size == (2048, 1024)
    assert cut.anchor == [0.5, 1.0]


def test_fake_model_without_canvas_size_gets_no_band_line() -> None:
    model = FakeImageModel()
    assert not isinstance(model, CanvasSizer)
    service = ArtService(model, FakeBackgroundRemover())
    for layer in ("back", "middle", "front"):
        prompt = _layer_prompt(layer)
        assert service.make_layer(prompt, layer=layer, size=layer_size(LAYER_HEIGHT[layer])) is not None
        assert model.calls[-1] == prompt


def test_matching_canvas_gets_no_band_line() -> None:
    class _ExactModel(FakeImageModel):
        def canvas_size(self, size):
            return size

    model = _ExactModel()
    prompt = _layer_prompt("middle")
    assert ArtService(model, FakeBackgroundRemover()).make_layer(prompt, layer="middle", size=(2048, 464))
    assert model.calls == [prompt]


def test_broken_canvas_size_falls_back_to_no_band_line() -> None:
    class _BrokenSizer(FakeImageModel):
        def canvas_size(self, size):
            raise RuntimeError("no idea")

    model = _BrokenSizer()
    prompt = _layer_prompt("front")
    assert ArtService(model, FakeBackgroundRemover()).make_layer(prompt, layer="front", size=(2048, 192))
    assert model.calls == [prompt]


def test_make_cutout_never_gets_a_band_line() -> None:
    api = ImageAPI(square_canvas((1024, 1024), transparent=False))
    prompt = build_art_prompt("a kettle", kind="prop")
    assert ArtService(model_for(api), FakeBackgroundRemover()).make_cutout(prompt) is not None
    assert api.body()["prompt"] == prompt


# --------------------------------------------------------------------------- helpers


def test_kept_fraction() -> None:
    assert kept_fraction((1536, 1024), (2048, 464)) == pytest.approx(0.3398, abs=1e-4)
    assert kept_fraction((1536, 1024), (2048, 192)) == pytest.approx(0.1406, abs=1e-4)
    assert kept_fraction((1536, 1024), (2048, 1024)) == pytest.approx(0.75)
    assert kept_fraction((1024, 1024), (1024, 1024)) == 1.0
    assert kept_fraction((1536, 1024), (1000, 1000)) == 1.0  # taller target: sides cut, capped


def test_layer_band_line_threshold() -> None:
    assert layer_band_line("middle", 0.995) is None
    assert layer_band_line("back", 1.0) is None
    assert "bottom 98%" in (layer_band_line("middle", 0.98) or "")


def test_style_prompt_defers_to_the_band_line() -> None:
    """The set-layer rules must not ask the middle layer to fill the frame or the lower third."""
    style = art.STYLE_PROMPT
    assert "lower third" not in style
    assert "composition line" in style
    assert "ground line at the bottom edge" in style
