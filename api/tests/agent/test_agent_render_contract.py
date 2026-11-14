"""PlaywrightFrameRenderer against the render page, through a fake page driver.

The Issue #1 render-page contract: the page draws `window.__STITCH_RENDER__`, honours `?t=`,
and sets `window.__STITCH_READY__ = true` once the frame is drawn. The page on main
(web/app/render/[memoryId]/TimelineView.tsx) implements it; an older page only marked
`<html data-stage-ready="1">` (the legacy page here), and the renderer must notice that, fall
back to diagrams per render, and say so to the critic. A page that cannot draw the scene shows
"This moment could not be opened." (or, once the Stage owner adds it, sets
`window.__STITCH_RENDER_ERROR__`): the renderer fails at once, and after two failed renders
in a row stops trying the browser for FAILURE_COOLDOWN_S.

Everything runs offline: the browser is a fake page driver injected as `page_factory`, the
clock is a fake one, and Playwright is never imported. Fake waits never sleep: they add the
time they would have waited to `FakeBrowser.waited_ms`.
"""

from __future__ import annotations

import asyncio
import json
import contextlib
import logging
import sys
import types
from pathlib import Path

import pytest

import schemas
from agent.tools.frames import (
    CONTRACT_EXPR,
    DIAGRAM_CRITIC_NOTE,
    ERROR_PAGE_TEXTS,
    FAILURE_COOLDOWN_S,
    FAILURE_STREAK,
    FALLBACK_COOLDOWN_S,
    READY_EXPR,
    READY_GRACE_MS,
    RENDER_ERROR_FLAG,
    FakeFrameRenderer,
    FrameRenderError,
    PlaywrightFrameRenderer,
    RenderContractMissing,
    RenderPageError,
    probe_render_page,
    render_with_note,
)

API = Path(__file__).resolve().parents[2]
SAMPLES = API.parent / "web" / "public" / "samples"
SAMPLE = schemas.MemoryResponse.model_validate_json((SAMPLES / "hymn_books.memory.json").read_text())
TIMES = [0.0, 3.2, 7.5]
DIAGRAMS = FakeFrameRenderer().render(SAMPLE, TIMES)


# ------------------------------------------------------------------------- fakes


class FakeRenderPage:
    """A fake Playwright page that behaves like one kind of render page.

    - "contract": sets __STITCH_READY__ (and data-stage-ready) once loaded;
    - "contract_value": the same, and its READY_EXPR wait resolves with "contract", as
      Playwright's JSHandle does on the real page (the other kinds resolve with True);
    - "legacy": sets only data-stage-ready, like the render page before the contract;
    - "error_page": shows "This moment could not be opened." and never marks ready, like
      TimelineView when preloadScene throws (READY_EXPR resolves with {error});
    - "error_flag": sets window.__STITCH_RENDER_ERROR__ (the proposed flag);
    - "http_500": the navigation answers HTTP 500;
    - "hang": loads but never becomes ready (every wait times out);
    - "down": navigation fails (connection refused).

    Waits never sleep: a wait that would time out raises TimeoutError at once, records the
    timeout it was given, and adds it to `waited` (the time a real browser would have spent).
    """

    ERROR_TEXT = "This moment could not be opened."

    def __init__(self, kind: str, log: list, waited: list | None = None) -> None:
        self.kind = kind
        self.log = log
        self.waited = waited if waited is not None else []
        self.window: dict[str, object] = {}
        self.dataset: dict[str, str] = {}
        self.text = ""

    def add_init_script(self, script: str) -> None:
        self.log.append(("init", script))

    def goto(self, url: str, timeout: int):
        self.log.append(("goto", url, timeout))
        if self.kind == "down":
            raise ConnectionError("net::ERR_CONNECTION_REFUSED at " + url)
        if self.kind in ("contract", "contract_value", "legacy"):
            self.dataset["stageReady"] = "1"
        if self.kind in ("contract", "contract_value"):
            self.window["__STITCH_READY__"] = True
        if self.kind == "error_page":
            self.text = self.ERROR_TEXT
        if self.kind == "error_flag":
            self.window["__STITCH_RENDER_ERROR__"] = "  the scene has no timeline  "
        return types.SimpleNamespace(status=500 if self.kind == "http_500" else 200)

    def _value(self, expression: str) -> object:
        """What the expression evaluates to on this page (falsy: keep polling)."""
        ready = self.window.get("__STITCH_READY__") is True
        if expression == CONTRACT_EXPR:
            return ready
        if expression == READY_EXPR:
            err = self.window.get("__STITCH_RENDER_ERROR__")
            if isinstance(err, str) and err.strip():
                return {"error": err.strip()}
            if ready:
                return "contract" if self.kind == "contract_value" else True
            if self.dataset.get("stageReady") == "1":
                return True
            if self.text and any(self.text.startswith(s) for s in ERROR_PAGE_TEXTS):
                return {"error": self.text}
            return False
        raise AssertionError(f"unexpected expression {expression!r}")

    def wait_for_function(self, expression: str, timeout: int) -> object:
        self.log.append(("wait", expression, timeout))
        value = self._value(expression)
        if not value:
            self.waited.append(timeout)
            raise TimeoutError(f"Timeout {timeout}ms exceeded.")
        return value

    def screenshot(self, type: str, full_page: bool) -> bytes:  # noqa: A002 - Playwright's name
        self.log.append(("shot", type, full_page))
        return b"\x89PNG screenshot %d" % len(self.log)


class FakeBrowser:
    """A page factory: each call opens one fake page of the current `kind`."""

    def __init__(self, kind: str) -> None:
        self.kind = kind
        self.log: list = []
        self.waited: list[int] = []  # ms a real browser would have spent in timed-out waits
        self.opened = 0
        self.closed = 0

    @property
    def waited_ms(self) -> int:
        return sum(self.waited)

    @contextlib.contextmanager
    def __call__(self, viewport: tuple[int, int]):
        self.opened += 1
        self.log.append(("page", viewport))
        try:
            yield FakeRenderPage(self.kind, self.log, self.waited)
        finally:
            self.closed += 1


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class SpyDiagrams(FakeFrameRenderer):
    """The default fallback, counting its renders."""


def renderer_for(browser: FakeBrowser, clock: FakeClock | None = None, **kwargs) -> tuple[PlaywrightFrameRenderer, SpyDiagrams]:
    fallback = SpyDiagrams()
    renderer = PlaywrightFrameRenderer(
        "http://web:3000", page_factory=browser, fallback=fallback, clock=clock or FakeClock(), **kwargs
    )
    return renderer, fallback


def fallback_warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING and r.name == "agent.tools.frames"]


# --------------------------------------------------------------------- the contract page


def test_a_contract_page_gives_screenshots_with_no_note_and_no_fallback(caplog):
    caplog.set_level(logging.WARNING, logger="agent.tools.frames")
    browser = FakeBrowser("contract")
    renderer, fallback = renderer_for(browser)

    pngs, note = renderer.render_with_note(SAMPLE, TIMES)

    assert note is None
    assert len(pngs) == 3 and all(p.startswith(b"\x89PNG screenshot") for p in pngs)
    assert fallback.calls == []
    assert browser.opened == browser.closed == 1  # one browser for all the times
    gotos = [e[1] for e in browser.log if e[0] == "goto"]
    assert gotos == [f"http://web:3000/render/{SAMPLE.memory.id}?t={t:.3f}" for t in TIMES]
    init = next(e[1] for e in browser.log if e[0] == "init")
    assert init.startswith("window.__STITCH_RENDER__ = ") and "participantIds" in init
    waits = [e[1:] for e in browser.log if e[0] == "wait"]
    assert waits == [(READY_EXPR, 15000), (CONTRACT_EXPR, READY_GRACE_MS)] * 3
    assert fallback_warnings(caplog) == []
    assert renderer.render(SAMPLE, [1.0])[0].startswith(b"\x89PNG")  # render() is the PNGs alone
    assert render_with_note(renderer, SAMPLE, [1.0])[1] is None


# ----------------------------------------------------------------------- the legacy page


def test_a_legacy_page_falls_back_to_diagrams_and_cools_down(caplog):
    caplog.set_level(logging.INFO, logger="agent.tools.frames")
    clock = FakeClock()
    browser = FakeBrowser("legacy")
    renderer, fallback = renderer_for(browser, clock)

    pngs, note = renderer.render_with_note(SAMPLE, TIMES)

    # The page was ready the legacy way, got the short grace for __STITCH_READY__, never set it.
    assert pngs == DIAGRAMS
    assert note == DIAGRAM_CRITIC_NOTE
    assert fallback.calls == [(SAMPLE.memory.id, tuple(TIMES))]
    waits = [e[1:] for e in browser.log if e[0] == "wait"]
    assert waits == [(READY_EXPR, 15000), (CONTRACT_EXPR, READY_GRACE_MS)]  # no screenshot, no 15 s wait
    assert not [e for e in browser.log if e[0] == "shot"]
    assert browser.closed == 1
    assert renderer.cooling_down
    (warning,) = fallback_warnings(caplog)
    assert "diagrams" in warning and "__STITCH_READY__" in warning and "600" in warning

    # Within the cool-down: straight to the fallback, no browser, no new warning.
    clock.now += FALLBACK_COOLDOWN_S - 1
    pngs, note = renderer.render_with_note(SAMPLE, [3.2])
    assert note == DIAGRAM_CRITIC_NOTE and pngs == [DIAGRAMS[1]]
    assert browser.opened == 1
    assert len(fallback_warnings(caplog)) == 1

    # After the cool-down it probes the page again: still legacy, so a new cool-down, quietly.
    clock.now += 2
    assert not renderer.cooling_down
    _, note = renderer.render_with_note(SAMPLE, [3.2])
    assert note == DIAGRAM_CRITIC_NOTE
    assert browser.opened == 2
    assert renderer.cooling_down
    assert len(fallback_warnings(caplog)) == 1

    # The Stage owner ships the contract: after the next cool-down, real screenshots again.
    browser.kind = "contract"
    clock.now += FALLBACK_COOLDOWN_S + 1
    pngs, note = renderer.render_with_note(SAMPLE, [3.2])
    assert note is None and pngs[0].startswith(b"\x89PNG screenshot")
    assert browser.opened == 3 and not renderer.cooling_down
    warnings = fallback_warnings(caplog)
    assert len(warnings) == 2 and "real screenshots again" in warnings[1]


def test_a_legacy_page_raises_render_contract_missing_without_a_fallback():
    renderer = PlaywrightFrameRenderer("http://web:3000", page_factory=FakeBrowser("legacy"), fallback=None)
    with pytest.raises(RenderContractMissing, match="__STITCH_READY__") as info:
        renderer.render(SAMPLE, [1.0])
    assert isinstance(info.value, FrameRenderError)


def test_a_contract_page_that_is_slow_after_stage_ready_still_counts_within_the_grace():
    """data-stage-ready first, __STITCH_READY__ a moment later: the grace wait catches it."""

    class SlowContract(FakeRenderPage):
        def wait_for_function(self, expression, timeout):
            if expression == CONTRACT_EXPR:
                self.window["__STITCH_READY__"] = True  # set during the grace
            return super().wait_for_function(expression, timeout)

    @contextlib.contextmanager
    def factory(viewport):
        yield SlowContract("legacy", [])

    pngs, note = PlaywrightFrameRenderer("http://web:3000", page_factory=factory).render_with_note(SAMPLE, [1.0])
    assert note is None and pngs[0].startswith(b"\x89PNG")


# -------------------------------------------------- unreachable, hanging, no Playwright


@pytest.mark.parametrize("kind", ["down", "hang"])
def test_an_unreachable_or_hanging_page_falls_back_without_a_cool_down(caplog, kind):
    caplog.set_level(logging.WARNING, logger="agent.tools.frames")
    browser = FakeBrowser(kind)
    renderer, fallback = renderer_for(browser)

    pngs, note = renderer.render_with_note(SAMPLE, TIMES)
    assert pngs == DIAGRAMS and note == DIAGRAM_CRITIC_NOTE
    assert len(fallback.calls) == 1
    assert not renderer.cooling_down  # the web app may just be starting: try it next round

    renderer.render_with_note(SAMPLE, TIMES)
    assert browser.opened == 2
    assert len(fallback_warnings(caplog)) == 1  # one warning per switch, not per render
    if kind == "hang":
        waits = [e[1:] for e in browser.log if e[0] == "wait"]
        assert waits[0] == (READY_EXPR, 15000)


def _fake_playwright_module(
    monkeypatch, *, launch_error: Exception | None = None, launch_args: list | None = None
) -> list:
    """A stand-in `playwright.sync_api` whose chromium opens FakeRenderPage("contract").

    Each launch's `args` keyword is appended to `launch_args` when given.
    """
    log: list = []
    launch_args = launch_args if launch_args is not None else []

    class Browser:
        def new_page(self, viewport):
            log.append(("page", viewport))
            return FakeRenderPage("contract", log)

        def close(self):
            log.append(("close",))

    class Chromium:
        def launch(self, headless, args=None):
            log.append(("launch", headless))
            launch_args.append(args)
            if launch_error is not None:
                raise launch_error
            return Browser()

    class Manager:
        def __enter__(self):
            return types.SimpleNamespace(chromium=Chromium())

        def __exit__(self, *exc):
            return False

    package = types.ModuleType("playwright")
    sync_api = types.ModuleType("playwright.sync_api")
    sync_api.sync_playwright = Manager  # type: ignore[attr-defined]
    package.sync_api = sync_api  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "playwright", package)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", sync_api)
    return log


def test_without_playwright_the_default_page_factory_falls_back(monkeypatch):
    monkeypatch.setitem(sys.modules, "playwright", None)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", None)
    renderer = PlaywrightFrameRenderer("http://web:3000")  # production's construction
    assert isinstance(renderer.fallback, FakeFrameRenderer)
    pngs, note = renderer.render_with_note(SAMPLE, TIMES)
    assert pngs == DIAGRAMS and note == DIAGRAM_CRITIC_NOTE
    with pytest.raises(FrameRenderError, match="playwright is not installed"):
        PlaywrightFrameRenderer("http://web:3000", fallback=None).render(SAMPLE, TIMES)


def test_a_browser_that_will_not_start_falls_back(monkeypatch):
    log = _fake_playwright_module(monkeypatch, launch_error=RuntimeError("Executable doesn't exist at /ms-playwright"))
    pngs, note = PlaywrightFrameRenderer("http://web:3000").render_with_note(SAMPLE, [1.0])
    assert note == DIAGRAM_CRITIC_NOTE and pngs == FakeFrameRenderer().render(SAMPLE, [1.0])
    assert log == [("launch", True)]


def test_the_default_page_factory_drives_sync_playwright(monkeypatch):
    log = _fake_playwright_module(monkeypatch)
    pngs, note = PlaywrightFrameRenderer("http://web:3000", viewport=(800, 450)).render_with_note(SAMPLE, [1.0])
    assert note is None and len(pngs) == 1
    assert log[:2] == [("launch", True), ("page", {"width": 800, "height": 450})]
    assert log[-1] == ("close",)


def test_when_the_fallback_fails_too_both_reasons_are_reported():
    class Broken:
        def render(self, scene, times):
            raise FrameRenderError("no timeline")

    renderer = PlaywrightFrameRenderer("http://web:3000", page_factory=FakeBrowser("down"), fallback=Broken())
    with pytest.raises(FrameRenderError) as info:
        renderer.render(SAMPLE, [1.0])
    assert "ERR_CONNECTION_REFUSED" in str(info.value) and "no timeline" in str(info.value)


def test_the_fallback_works_inside_an_event_loop():
    browser = FakeBrowser("legacy")
    renderer, _ = renderer_for(browser)

    async def main():
        return renderer.render_with_note(SAMPLE, [1.0])

    pngs, note = asyncio.run(main())
    assert note == DIAGRAM_CRITIC_NOTE and len(pngs) == 1


def test_no_times_opens_no_browser():
    browser = FakeBrowser("contract")
    renderer, fallback = renderer_for(browser)
    assert renderer.render_with_note(SAMPLE, []) == ([], None)
    assert browser.opened == 0 and fallback.calls == []


# ------------------------------------------------------------------------- the probe


@pytest.mark.parametrize(
    ("kind", "state"),
    [("contract", "contract"), ("legacy", "legacy"), ("down", "unreachable"), ("hang", "unreachable")],
)
def test_probe_render_page_reports_what_the_page_supports(kind, state):
    browser = FakeBrowser(kind)
    assert probe_render_page("http://web:3000/", timeout_ms=4000, page_factory=browser) == state
    assert browser.opened == browser.closed == 1
    assert ("goto", "http://web:3000/render/draft-preflight?t=0.000", 4000) in browser.log
    assert not [e for e in browser.log if e[0] == "shot"]  # a probe takes no screenshot
    init = next(e[1] for e in browser.log if e[0] == "init")
    assert init.startswith("window.__STITCH_RENDER__ = ")


def test_probe_render_page_without_playwright(monkeypatch):
    monkeypatch.setitem(sys.modules, "playwright", None)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", None)
    assert probe_render_page("http://web:3000") == "no_playwright"


def test_probe_render_page_when_chromium_is_missing(monkeypatch):
    _fake_playwright_module(monkeypatch, launch_error=RuntimeError("Executable doesn't exist"))
    assert probe_render_page("http://web:3000") == "no_playwright"


def test_probe_render_page_through_sync_playwright_and_inside_an_event_loop(monkeypatch):
    _fake_playwright_module(monkeypatch)

    async def main():
        return probe_render_page("http://web:3000")

    assert asyncio.run(main()) == "contract"


# ------------------------------------------------------------- fail fast on a broken page


def test_ready_expr_watches_the_error_flag_and_the_error_page_texts():
    """The one wait that reads readiness also reads failure (checked by the live test in a browser)."""
    assert f"window.{RENDER_ERROR_FLAG}" in READY_EXPR and RENDER_ERROR_FLAG == "__STITCH_RENDER_ERROR__"
    assert "This moment could not be opened." in ERROR_PAGE_TEXTS
    for text in ERROR_PAGE_TEXTS:
        assert json.dumps(text) in READY_EXPR
    assert "window.__STITCH_READY__ === true" in READY_EXPR and "stageReady === '1'" in READY_EXPR


@pytest.mark.parametrize(
    ("kind", "message"),
    [
        ("error_page", "render page error: This moment could not be opened."),
        ("error_flag", "render page error: the scene has no timeline"),
        ("http_500", "render page error: HTTP 500 from http://web:3000/render/"),
    ],
)
def test_an_error_page_fails_at_once_without_a_screenshot_or_the_full_timeout(kind, message):
    browser = FakeBrowser(kind)
    renderer = PlaywrightFrameRenderer("http://web:3000", page_factory=browser, fallback=None, clock=FakeClock())

    with pytest.raises(RenderPageError) as info:
        renderer.render(SAMPLE, TIMES)

    assert str(info.value).startswith(message)
    assert isinstance(info.value, FrameRenderError) and not isinstance(info.value, RenderContractMissing)
    assert browser.waited_ms == 0  # no wait ran into its timeout (a hanging page costs 15000)
    assert not [e for e in browser.log if e[0] == "shot"]
    assert len([e for e in browser.log if e[0] == "goto"]) == 1  # the first time stops the render
    assert [e[1] for e in browser.log if e[0] == "wait"] == ([] if kind == "http_500" else [READY_EXPR])


def test_an_error_page_falls_back_to_diagrams_with_the_reason_logged(caplog):
    caplog.set_level(logging.WARNING, logger="agent.tools.frames")
    browser = FakeBrowser("error_page")
    renderer, fallback = renderer_for(browser)

    pngs, note = renderer.render_with_note(SAMPLE, TIMES)

    assert pngs == DIAGRAMS and note == DIAGRAM_CRITIC_NOTE
    assert len(fallback.calls) == 1 and browser.waited_ms == 0
    (warning,) = fallback_warnings(caplog)
    assert "render page error: This moment could not be opened." in warning


def test_a_hanging_page_costs_the_full_timeout_for_comparison():
    browser = FakeBrowser("hang")
    renderer = PlaywrightFrameRenderer("http://web:3000", page_factory=browser, fallback=None, clock=FakeClock())
    with pytest.raises(FrameRenderError):
        renderer.render(SAMPLE, TIMES)
    assert browser.waited_ms == 15000


def test_a_contract_value_skips_the_grace_wait():
    """On the real page READY_EXPR says "contract": no second wait that a busy page could miss."""
    browser = FakeBrowser("contract_value")
    renderer, fallback = renderer_for(browser)
    pngs, note = renderer.render_with_note(SAMPLE, TIMES)
    assert note is None and len(pngs) == 3 and fallback.calls == []
    assert [e[1:] for e in browser.log if e[0] == "wait"] == [(READY_EXPR, 15000)] * 3


def test_probe_render_page_reports_an_error_page_as_unreachable_at_once(caplog):
    caplog.set_level(logging.WARNING, logger="agent.tools.frames")
    browser = FakeBrowser("error_page")
    assert probe_render_page("http://web:3000", page_factory=browser) == "unreachable"
    assert browser.waited_ms == 0
    assert any("This moment could not be opened." in m for m in fallback_warnings(caplog))


# ----------------------------------------------- two failures in a row: the failure cool-down


def test_two_failed_renders_start_the_failure_cool_down(caplog):
    caplog.set_level(logging.INFO, logger="agent.tools.frames")
    clock = FakeClock()
    browser = FakeBrowser("hang")
    renderer, fallback = renderer_for(browser, clock)
    assert FAILURE_STREAK == 2 and FAILURE_COOLDOWN_S == 120.0

    renderer.render_with_note(SAMPLE, TIMES)
    assert not renderer.cooling_down  # one failure: the web app may just be starting
    renderer.render_with_note(SAMPLE, TIMES)
    assert renderer.cooling_down
    assert browser.opened == 2 and browser.waited_ms == 2 * 15000

    # The rest of the checker round: diagrams at once, no browser, no more timeouts.
    for _ in range(4):
        pngs, note = renderer.render_with_note(SAMPLE, [3.2])
        assert note == DIAGRAM_CRITIC_NOTE and pngs == [DIAGRAMS[1]]
    assert browser.opened == 2 and browser.waited_ms == 2 * 15000
    assert len(fallback.calls) == 6
    assert len(fallback_warnings(caplog)) == 1  # one warning for the one switch to diagrams
    infos = [r.getMessage() for r in caplog.records if r.levelno == logging.INFO and "in a row" in r.getMessage()]
    assert infos and "120" in infos[0]

    # Still within the cool-down a second before it ends; after it, one more try.
    clock.now += FAILURE_COOLDOWN_S - 1
    assert renderer.cooling_down
    clock.now += 2
    assert not renderer.cooling_down
    renderer.render_with_note(SAMPLE, [3.2])
    assert browser.opened == 3
    assert renderer.cooling_down  # still failing: straight back into the cool-down
    assert len(fallback_warnings(caplog)) == 1


def test_error_pages_count_toward_the_streak():
    browser = FakeBrowser("error_page")
    renderer, _ = renderer_for(browser)
    renderer.render_with_note(SAMPLE, TIMES)
    renderer.render_with_note(SAMPLE, TIMES)
    assert renderer.cooling_down
    renderer.render_with_note(SAMPLE, TIMES)
    assert browser.opened == 2


def test_a_success_resets_the_streak_and_ends_the_cool_down(caplog):
    caplog.set_level(logging.WARNING, logger="agent.tools.frames")
    clock = FakeClock()
    browser = FakeBrowser("down")
    renderer, _ = renderer_for(browser, clock)

    # fail, succeed, fail: never two in a row, so never a cool-down.
    renderer.render_with_note(SAMPLE, [1.0])
    browser.kind = "contract"
    assert renderer.render_with_note(SAMPLE, [1.0])[1] is None
    browser.kind = "down"
    renderer.render_with_note(SAMPLE, [1.0])
    assert not renderer.cooling_down
    assert browser.opened == 3

    # Two in a row: cool-down. When it ends the page works again: screenshots, streak cleared.
    renderer.render_with_note(SAMPLE, [1.0])
    assert renderer.cooling_down
    browser.kind = "contract"
    clock.now += FAILURE_COOLDOWN_S + 1
    pngs, note = renderer.render_with_note(SAMPLE, [1.0])
    assert note is None and pngs[0].startswith(b"\x89PNG screenshot")
    assert not renderer.cooling_down
    browser.kind = "down"
    renderer.render_with_note(SAMPLE, [1.0])
    assert not renderer.cooling_down  # one failure after the success: the streak starts over
    warnings = fallback_warnings(caplog)
    assert [("real screenshots again" in w) for w in warnings] == [False, True, False, True, False]


def test_a_legacy_page_does_not_count_toward_the_failure_streak():
    clock = FakeClock()
    browser = FakeBrowser("down")
    renderer, _ = renderer_for(browser, clock)
    renderer.render_with_note(SAMPLE, [1.0])  # one failure
    browser.kind = "legacy"
    clock_before = clock.now
    renderer.render_with_note(SAMPLE, [1.0])  # contract missing: its own (longer) cool-down
    assert renderer.cooling_down
    clock.now = clock_before + FALLBACK_COOLDOWN_S + 1
    browser.kind = "down"
    renderer.render_with_note(SAMPLE, [1.0])
    assert not renderer.cooling_down  # the legacy render reset the streak: this is failure one


def test_without_a_fallback_the_failure_cool_down_raises_at_once():
    clock = FakeClock()
    browser = FakeBrowser("hang")
    renderer = PlaywrightFrameRenderer("http://web:3000", page_factory=browser, fallback=None, clock=clock)
    for _ in range(FAILURE_STREAK):
        with pytest.raises(FrameRenderError, match="Timeout"):
            renderer.render(SAMPLE, [1.0])
    with pytest.raises(FrameRenderError, match="skipped for 120 s after 2 failed renders in a row") as info:
        renderer.render(SAMPLE, [1.0])
    assert "Timeout" in str(info.value)  # the last failure is named
    assert browser.opened == FAILURE_STREAK and browser.waited_ms == FAILURE_STREAK * 15000
    clock.now += FAILURE_COOLDOWN_S + 1
    browser.kind = "contract"
    assert renderer.render(SAMPLE, [1.0])[0].startswith(b"\x89PNG screenshot")


def test_the_streak_and_cool_down_are_configurable():
    clock = FakeClock()
    browser = FakeBrowser("down")
    renderer, _ = renderer_for(browser, clock, failure_streak=1, failure_cooldown_s=5.0)
    renderer.render_with_note(SAMPLE, [1.0])
    assert renderer.cooling_down
    clock.now += 6
    assert not renderer.cooling_down


# ------------------------------------------------------------- launch arguments


def test_the_default_page_factory_launches_with_the_chromium_args(monkeypatch):
    from agent.tools import frames

    launched: list = []
    _fake_playwright_module(monkeypatch, launch_args=launched)
    monkeypatch.delenv("STITCH_CHROMIUM_ARGS", raising=False)
    monkeypatch.setattr(frames, "CHROMIUM_ARGS", ("--use-angle=metal",))
    PlaywrightFrameRenderer("http://web:3000", fallback=None).render(SAMPLE, [1.0])
    monkeypatch.setenv("STITCH_CHROMIUM_ARGS", "--a  --b=1")
    PlaywrightFrameRenderer("http://web:3000", fallback=None).render(SAMPLE, [1.0])
    monkeypatch.setenv("STITCH_CHROMIUM_ARGS", "")
    PlaywrightFrameRenderer("http://web:3000", fallback=None).render(SAMPLE, [1.0])
    assert launched == [["--use-angle=metal"], ["--a", "--b=1"], None]
