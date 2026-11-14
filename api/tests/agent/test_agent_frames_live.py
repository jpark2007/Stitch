"""Opt-in: the checker's frame renderer against a running web app's real render page.

Runs only when STITCH_WEB_URL_TEST names a running web app (e.g. `cd web && npm run dev`, then
STITCH_WEB_URL_TEST=http://localhost:3000) AND Playwright can launch its Chromium
(`cd api && uv run playwright install chromium`); otherwise every test here skips. No API
server is needed: the scene is injected (`window.__STITCH_RENDER__`) and the draft's art is
served from a MediaSource by the renderer.

It checks the Issue #1 contract end to end: the probe says "contract", and the hymn_books
screenplay compiled by the real compiler (the fake one when it is not importable), built into
a draft the way render_frames builds one, comes back as real screenshots (note None) that
decode at the viewport size, are not blank, and change with t; the draft's art ids reach the
MediaSource; and a page that cannot open its moment fails at once instead of timing out.
"""

from __future__ import annotations

import io
import os
import time
from pathlib import Path

import pytest
from PIL import Image, ImageChops, ImageDraw, ImageStat

import schemas
from agent.repo import InMemoryRepo, load_sample_family
from agent.run_info import RunInfo
from agent.tools.compile import _parse_and_compile, load_compiler
from agent.tools.frames import (
    PlaywrightFrameRenderer,
    PlaywrightUnavailable,
    RenderPageError,
    _open_ready,
    draft_scene,
    playwright_page,
    probe_render_page,
)
from agent.wiring import scene_for
from screenplay.schema import Screenplay

WEB_URL = (os.environ.get("STITCH_WEB_URL_TEST") or "").strip().rstrip("/")
SAMPLES = Path(__file__).resolve().parents[3] / "web" / "public" / "samples"
FAM = "fam_live"
TIMEOUT_MS = 90_000
"""`next dev` compiles the route on first use and headless WebGL may be software: be generous."""
MIN_PIXEL_SD = 8.0
"""Mean per-channel standard deviation below this is a blank or near-uniform frame."""
MIN_FRAME_DIFF = 0.5
"""Mean absolute per-channel difference between frames at different times."""


def _chromium_launches() -> str | None:
    """None when Playwright can open a page, else why not."""
    try:
        with playwright_page((64, 64)):
            return None
    except PlaywrightUnavailable as exc:
        return str(exc)
    except Exception as exc:  # noqa: BLE001 - any launch failure means "skip"
        return f"{type(exc).__name__}: {exc}"


@pytest.fixture(scope="module")
def web_url() -> str:
    if not WEB_URL:
        pytest.skip("STITCH_WEB_URL_TEST is not set (no web app to render against)")
    why = _chromium_launches()
    if why is not None:
        pytest.skip(f"Playwright cannot launch Chromium: {why}")
    return WEB_URL


def _art_png(rgb: tuple[int, int, int]) -> bytes:
    img = Image.new("RGBA", (256, 128), (0, 0, 0, 0))
    ImageDraw.Draw(img).rounded_rectangle([4, 20, 252, 124], 16, fill=(*rgb, 255))
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


@pytest.fixture(scope="module")
def compiled() -> tuple[schemas.MemoryResponse, InMemoryRepo, list[str]]:
    """hymn_books compiled as the compile tool does, as a draft with two layers of unsaved art."""
    repo = InMemoryRepo()
    load_sample_family(repo, FAM)
    text = (SAMPLES / "hymn_books.screenplay.json").read_text()
    lookup = scene_for(repo, FAM)
    result = _parse_and_compile(text, load_compiler(), lookup).result
    assert result.ok and result.timeline is not None, result.errors
    screenplay = Screenplay.model_validate_json(text)
    scene = lookup(screenplay)
    art_ids: list[str] = []
    set_id = screenplay.shots[0].set
    set_def = scene.sets[set_id]
    layers = list(set_def.layers)
    for i, rgb in zip(range(min(2, len(layers))), [(70, 120, 200), (200, 80, 70)], strict=False):
        art_id = repo.put_media(FAM, _art_png(rgb), "image/png", kind="cutout")
        art_ids.append(art_id)
        layers[i] = layers[i].model_copy(update={"art": art_id})
    scene.sets[set_id] = set_def.model_copy(update={"layers": layers})
    run = RunInfo(
        run_id="live",
        family_id=FAM,
        user_id="user_dad",
        author_id="dad",
        fields=schemas.SubmitMemoryFields(text="I met your mother on the church steps.", year=1988, title="Hymn books"),
        photo_ids=(),
    )
    return draft_scene(run, screenplay, result.timeline, scene), repo, art_ids


def _rgb(png: bytes, viewport: tuple[int, int]) -> Image.Image:
    img = Image.open(io.BytesIO(png))
    img.load()
    assert img.format == "PNG"
    assert img.size == viewport
    return img.convert("RGB")


def _spread(img: Image.Image) -> float:
    return sum(ImageStat.Stat(img).stddev) / 3


def _diff(a: Image.Image, b: Image.Image) -> float:
    return sum(ImageStat.Stat(ImageChops.difference(a, b)).mean) / 3


def test_the_render_page_implements_the_contract(web_url):
    assert probe_render_page(web_url, timeout_ms=TIMEOUT_MS) == "contract"


def test_a_compiled_draft_renders_as_real_varying_screenshots_with_its_own_art(web_url, compiled):
    draft, repo, art_ids = compiled
    asked: list[str] = []

    def media(family_id: str, media_id: str):
        asked.append(media_id)
        return repo.get_media(family_id, media_id) if family_id == FAM else None

    renderer = PlaywrightFrameRenderer(web_url, fallback=None, timeout_ms=TIMEOUT_MS)
    duration = draft.memory.timeline.duration
    times = [round(duration * f, 2) for f in (0.15, 0.5, 0.85)]
    pngs, note = renderer.render_with_note(draft, times, media=media)

    assert note is None  # real screenshots, not the diagram fallback
    assert len(pngs) == len(times)
    frames = [_rgb(png, renderer.viewport) for png in pngs]
    spreads = [_spread(f) for f in frames]
    assert all(s > MIN_PIXEL_SD for s in spreads), spreads
    diffs = [_diff(a, b) for a, b in zip(frames, frames[1:], strict=False)]
    assert all(d > MIN_FRAME_DIFF for d in diffs), diffs
    # The stage asked for each piece of the draft's art by its bare id, and got it.
    assert {a.removeprefix("media/") for a in art_ids} <= {a.removeprefix("media/") for a in asked}, asked


def test_a_page_that_cannot_open_its_moment_fails_at_once(web_url):
    """No injected scene and an unknown family: TimelineView shows its error text, never ready."""
    url = f"{web_url}/render/m_missing?family=fam_missing&t=1"
    started = time.monotonic()
    with playwright_page((1280, 720)) as page, pytest.raises(RenderPageError, match="could not be opened"):
        _open_ready(page, url, timeout_ms=TIMEOUT_MS, grace_ms=1500)
    assert time.monotonic() - started < TIMEOUT_MS / 1000 / 3
