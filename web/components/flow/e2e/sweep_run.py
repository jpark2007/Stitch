"""Browser sweep of the live agent run on /add?run=, in mock mode and against the offline API.

From api/ (it uses the API's uv environment, which has Playwright):

    cd api && uv run python ../web/components/flow/e2e/sweep_run.py [--mode mock|real|both]
        [--headed] [--out DIR] [--api-port N] [--web-port N]

Real mode starts `python -m seed.serve` (in-memory database, scripted runs 0.1 s apart) and
signs in as the demo family's Sam (sam@demo.stitch); mock mode (NEXT_PUBLIC_API_MOCK=1) signs
in as David (david@example.com) in the mock's Riveras. Each viewport (harness.VIEWPORTS:
desktop, then phone) walks every state in a fresh browser context:

 1. the feed while the studio works (and the slip of what was sent);
 2. the follow-up card ("Who else was there that day?"): an empty answer, an answer failing
    with 500 and with a dropped connection (the card stays, with what was typed and a friendly
    line), a 409 NOT_WAITING (the card closes quietly), and a sent answer;
 3. the prop review: keep, remove, a photo swap whose upload fails (no preview is left) and
    one that uploads, then the review sent;
 4. the merge card: the story names "Grandpa Lucho", and both modes ask
    "Is Grandpa Lucho the Luis in your family tree?" (api/seed/fake_runs.py merge_candidate);
 5. a reload while the follow-up is open: the card comes back with the typed answer, its
    heading takes focus without a focus ring, and Tab shows the ring on the answer field;
 6. the same run reattached in a second tab while it works;
 7. a finished run reattached: the feed replays to the end and no request is aborted;
 8. a dropped stream: "Lost the thread for a moment. Reconnecting…", then the card again;
 9. an unknown run id: "We can't find this one any more", with its two ways on;
10. the scene ready: "Open your page" is /memories/<id>?family=<fid>, "Watch the scene" is
    /render/<id>?family=<fid> (real mode; mock moments cannot play on the stage, so mock mode
    has no watch link), "Back to the family tree" is /, "Add another memory" is
    /add?family=<fid>; then Ethan's two pages are opened and their problems reported;
11. a second telling, started from the person's open question on /add: the moment's title and
    the question are shown, the run never shows the follow-up or the prop review, and the
    memory gets the teller's narration track.

Server errors are page.route answers in real mode and window.__stitchMock.inject faults in
mock mode, with the API's exact wording and statuses. Every check is one seam in seams.json,
with a screenshot per state at each viewport: <mode>/shots/NN-<mode>-<viewport>-<state>.png.
Browser problems (harness.app_problems) during a check are their own seam, owned by whoever
owns the page they happened on. Exit 1 when a 'pages' or 'api' seam failed, else 0. Without
Chromium it prints the SKIP line and exits 0.
"""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True  # keep __pycache__ out of web/

import base64  # noqa: E402
import json  # noqa: E402
import re  # noqa: E402
import time  # noqa: E402
import traceback  # noqa: E402
from collections.abc import Callable, Iterable  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Any  # noqa: E402
from urllib.parse import parse_qs, quote, urlsplit  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness import (  # noqa: E402
    VIEWPORTS,
    PageWatch,
    SeamLog,
    Stack,
    StackError,
    app_problems,
    finish,
    mock_faults_script,
    require_chromium,
    run_stack,
    shot,
    shots_taken,
    sweep_args,
    sweep_modes,
    sweep_out_dir,
    wait_port_free,
)

TIMEOUT_MS = 30_000
NAV_TIMEOUT_MS = 120_000  # the first visit to a route waits for `next dev` to compile it
RUN_TIMEOUT_MS = 90_000  # a mock run steps every 600 ms
RUN_STEP = 0.1  # seconds between scripted events in real mode

# Who signs in (api/seed/README.md; web/components/flow/mock/demoSeed.ts).
REAL = {"fid": "demo_family", "email": "sam@demo.stitch", "password": "stitch-demo"}
MOCK = {"fid": "sample_family", "email": "david@example.com", "password": "stitch-demo"}
# The second telling, from an open question per viewport. Real mode keeps its data across
# viewports, so each viewport answers a different person's question.
SECOND_REAL = {
    "desktop": ("sam@demo.stitch", "sam", "demo_lily_graduation"),
    "phone": ("ana@demo.stitch", "mom", "demo_sam_wedding_2019"),
}
SECOND_MOCK = ("ana@example.com", "mom", "sample_hymn_books")  # a fresh mock per context

# The API's words (api/seed/fake_runs.py, api/routes/memories.py) and the page's.
FOLLOW_UP = "Who else was there that day?"
MERGE_TEXT = "Is Grandpa Lucho the Luis in your family tree?"
NOT_WAITING = "This run is not waiting for an answer"
ANSWER_5XX = "The studio couldn’t take your answer just now. Nothing is lost: try again in a moment."
ANSWER_NETWORK = (
    "That didn’t reach the studio. Your answer is still here: check your connection and send again."
)
RECONNECTING = "Lost the thread for a moment. Reconnecting…"
GONE = "We can’t find this one any more"
STORY = "Grandpa Lucho sang the loudest at church that morning."
STORY_PLAIN = "We sang at church that morning and the hymn books went flying."
SECOND_STORY = "I saw it all from where I sat. Everyone cheered when it happened."

# Where a browser problem's fix belongs: the flow pages own these routes.
FLOW_PATHS = re.compile(r"^/(join|login|signup|add|families)(/|$)")
FAILED_5XX = re.compile(r"Failed to load resource: the server responded with a status of 5\d\d")

# A 1x1 PNG for the prop photo swap.
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)

# Notes whether a pause card was ever on screen (a second telling must never show one).
PAUSE_WATCH = """
(() => {
  window.__stitchSawPause = [];
  const look = () => {
    for (const h of document.querySelectorAll('h2')) {
      const t = (h.textContent || '').trim();
      if ((t === 'One quick question' || t === 'The things in your story' || t === 'Is this someone we know?')
          && !window.__stitchSawPause.includes(t)) window.__stitchSawPause.push(t);
    }
  };
  setInterval(look, 25);
  new MutationObserver(look).observe(document, { subtree: true, childList: true });
})();
"""


class Skip(Exception):
    """The check does not apply in this mode; recorded as an ok seam with the reason."""


class Seam(Exception):
    """A failure whose fix belongs to someone other than the step's owner."""

    def __init__(self, owner: str, message: str) -> None:
        super().__init__(message)
        self.owner = owner


def first_lines(text: str, n: int = 6) -> str:
    lines = [ln for ln in str(text).splitlines() if ln.strip()]
    out = "\n".join(lines[:n])
    return out if len(lines) <= n else out + "\n..."


def owner_for_url(url: str) -> str:
    path = urlsplit(url).path or "/"
    return "pages" if FLOW_PATHS.search(path) else "ethan"


def injected(problem: str) -> bool:
    """A browser problem a fault step caused on purpose (a 5xx it answered, a request it dropped)."""
    return bool(
        FAILED_5XX.search(problem)
        or re.search(r"\bhttp at \S+: 5\d\d ", problem)
        or re.search(r"\brequestfailed at \S+: .* net::ERR_FAILED$", problem)
        or re.search(r"Failed to load resource: net::ERR_FAILED", problem)
    )


def cors_headers(origin: str, ctype: str = "application/json") -> dict[str, str]:
    return {
        "Access-Control-Allow-Origin": origin,
        "Access-Control-Allow-Credentials": "true",
        "Content-Type": ctype,
    }


# --------------------------------------------------------------------------- the sweep


class RunSweep:
    """One viewport of one mode: a fresh browser context per person, every state in order."""

    def __init__(self, stack: Stack, browser: Any, seams: SeamLog, mode: str, view: str) -> None:
        from playwright.sync_api import expect

        expect.set_options(timeout=TIMEOUT_MS)
        self.expect = expect
        self.stack = stack
        self.browser = browser
        self.seams = seams
        self.mode = mode
        self.view = view
        self.real = mode == "real"
        self.web = stack.web_url
        self.api = stack.api_url or ""
        self.who = REAL if self.real else MOCK
        self.fid = self.who["fid"]
        self.viewport = dict(zip(("width", "height"), VIEWPORTS[view], strict=True))
        self.watch = PageWatch()
        self.contexts: list[Any] = []
        self.done: set[str] = set()
        self.current: Any = None
        # Handed from step to step.
        self.ctx: Any = None
        self.page: Any = None
        self.run_url = ""
        self.run_id = ""
        self.memory_id = ""
        self.typed = ""

    # ------------------------------------------------------------------ plumbing

    def new_context(self, label: str, init_script: str | None = None) -> tuple[Any, Any]:
        ctx = self.browser.new_context(viewport=self.viewport)
        ctx.set_default_timeout(TIMEOUT_MS)
        ctx.set_default_navigation_timeout(NAV_TIMEOUT_MS)
        if init_script:
            ctx.add_init_script(init_script)
        self.contexts.append(ctx)
        page = ctx.new_page()
        self.attach(page, label)
        return ctx, page

    def attach(self, page: Any, label: str) -> Any:
        self.watch.attach(page, f"{self.mode}/{self.view}:{label}")
        self.current = page
        return page

    def close_all(self) -> None:
        for ctx in self.contexts:
            try:
                ctx.close()
            except Exception:  # noqa: BLE001
                pass
        self.contexts.clear()

    def step(
        self,
        key: str,
        name: str,
        fn: Callable[[], str | None],
        *,
        faults: bool = False,
        needs: Iterable[str] = (),
        owner: str = "pages",
    ) -> None:
        label = f"[{self.mode}/{self.view}] {name}"
        missing = [n for n in needs if n not in self.done]
        if missing:
            self.seams.record(label, False, f"not run: needs step(s) {', '.join(missing)} first", owner)
            return
        print(f"\n== {label}", flush=True)
        mark = len(self.watch.problems)
        ok, result_owner = True, owner
        try:
            detail = fn() or ""
        except Skip as exc:
            detail = f"SKIPPED: {exc}"
        except Seam as exc:
            ok, result_owner, detail = False, exc.owner, first_lines(str(exc))
        except Exception as exc:  # noqa: BLE001 - every failure becomes a seam
            ok, detail = False, first_lines(f"{type(exc).__name__}: {exc}")
            if not isinstance(exc, AssertionError):
                print(first_lines(traceback.format_exc(), 14), flush=True)
            self.failure_shot(key)
        self.seams.record(label, ok, detail, result_owner)
        if ok:
            self.done.add(key)
        self.browser_problems(label, mark, faults)

    def failure_shot(self, key: str) -> None:
        if self.current is None:
            return
        try:
            shot(self.current, self.stack, f"{self.mode}-{self.view}-{key}-failed")
        except Exception:  # noqa: BLE001
            pass

    def browser_problems(self, label: str, mark: int, faults: bool) -> None:
        groups: dict[str, list[str]] = {}
        problems = app_problems(self.watch, mark)
        events = {p: e for p, e in zip(self.watch.since(mark), self.watch.events_since(mark), strict=True)}
        for problem in problems:
            if faults and injected(problem):
                continue
            event = events.get(problem, {})
            owner = "api" if event.get("kind") == "http" else owner_for_url(event.get("pageUrl", ""))
            groups.setdefault(owner, []).append(problem)
        for owner, lines in groups.items():
            text = "\n".join(lines)
            if owner == "ethan":
                text = f"REQUEST (Ethan): fix these browser problems on your page:\n{text}"
            self.seams.record(f"{label}: browser problems", False, text, owner)

    def snap(self, page: Any, state: str) -> Path:
        """The state at this viewport; a page that scrolls sideways fails the step."""
        page.wait_for_timeout(200)  # let focus moves, transitions and fonts settle
        path = shot(page, self.stack, f"{self.mode}-{self.view}-{state}")
        over = page.evaluate("() => document.documentElement.scrollWidth - window.innerWidth")
        if isinstance(over, (int, float)) and over > 1:
            raise AssertionError(f"{state}: the page scrolls sideways by {over}px at {self.viewport['width']}px wide")
        return path

    @staticmethod
    def vis(locator: Any) -> Any:
        return locator.filter(visible=True)

    def goto(self, page: Any, path: str) -> None:
        page.goto(f"{self.web}{path}")

    # ------------------------------------------------------------------ the API, both modes

    def api_call(self, page: Any, method: str, path: str, body: Any = None) -> tuple[int, Any]:
        """A request as the signed-in person: ctx.request in real mode, the mock's fetch in mock mode."""
        if self.real:
            req = page.context.request
            url = f"{self.api}{path}"
            res = req.get(url) if method == "GET" else req.fetch(url, method=method, data=body)
            try:
                data = res.json() if res.body() else None
            except Exception:  # noqa: BLE001
                data = None
            return res.status, data
        result = page.evaluate(
            """async ([method, path, body]) => {
                const init = { method, credentials: 'include' };
                if (body !== null) {
                  init.headers = { 'Content-Type': 'application/json' };
                  init.body = JSON.stringify(body);
                }
                const res = await window.__stitchMock.fetch('http://mock.local' + path, init);
                const text = await res.text();
                let data = null;
                try { data = text ? JSON.parse(text) : null; } catch { data = text; }
                return [res.status, data];
            }""",
            [method, path, body],
        )
        return int(result[0]), result[1]

    # ------------------------------------------------------------------ faults, both modes

    def fault(self, page: Any, method: str, path: str, *, status: int | None = None, detail: str | None = None,
              network: bool = False, times: int = 1) -> None:
        """Fail the next `times` `method path` requests ('{fid}'-style params) on this page."""
        if self.real:
            regex = "^" + re.escape(self.api) + re.sub(r"\\\{[a-z]+\\\}", "[^/?]+", re.escape(path)) + r"(\?.*)?$"
            left = [times]

            def handler(route: Any) -> None:
                if route.request.method != method or left[0] <= 0:
                    route.fallback()
                    return
                left[0] -= 1
                if network:
                    route.abort("failed")
                elif detail is not None:
                    route.fulfill(status=status or 500, headers=cors_headers(self.web),
                                  body=json.dumps({"detail": detail}))
                else:
                    route.fulfill(status=status or 500, headers=cors_headers(self.web, "text/plain"),
                                  body="Internal Server Error")

            page.route(re.compile(regex), handler)
            return
        fault: dict[str, Any] = {"method": method, "path": path, "times": times}
        if network:
            fault["network"] = True
        else:
            fault["status"] = status or 500
            if detail is not None:
                fault["detail"] = detail
        page.evaluate("(f) => window.__stitchMock.inject(f)", fault)

    def clear_faults(self, page: Any) -> None:
        if self.real:
            page.unroute_all(behavior="ignoreErrors")
        else:
            page.evaluate("() => window.__stitchMock.clearFaults()")

    # ------------------------------------------------------------------ UI helpers

    def sign_in(self, page: Any, email: str, password: str, then: str) -> None:
        self.goto(page, f"/login?next={quote(then, safe='')}")
        self.expect(page.get_by_role("heading", level=1, name="Welcome back")).to_be_visible()
        self.vis(page.get_by_label("Email")).fill(email)
        self.vis(page.get_by_label("Password", exact=True)).fill(password)
        page.get_by_role("button", name="Log in", exact=True).click()
        page.wait_for_url(re.compile(re.escape(self.web) + re.escape(then.split("?")[0]) + r"(\?|$)"))

    def start_run(self, page: Any, story: str) -> None:
        """Send a new memory from /add; the page swaps to the run and puts it in the URL."""
        self.expect(page.get_by_role("heading", level=1, name="Add a memory")).to_be_visible()
        self.vis(page.get_by_label(re.compile(r"^Your story"))).fill(story)
        self.vis(page.get_by_label(re.compile(r"^When was this"))).fill("1975")
        page.get_by_role("button", name="Save this memory").click()
        page.wait_for_url(re.compile(r"[?&]run="))
        self.run_url = page.url[len(self.web):]
        self.run_id = parse_qs(urlsplit(page.url).query)["run"][0]

    def feed_heading(self, page: Any, done: bool = False, telling: bool = False) -> Any:
        if done:
            name = "Your telling is stitched in" if telling else "Your memory is stitched in"
        else:
            name = "Adding your version" if telling else "Making your page"
        return page.get_by_role("heading", level=2, name=name, exact=True)

    def card(self, page: Any, title: str) -> Any:
        return page.get_by_role("heading", level=2, name=title, exact=True)

    def follow_up(self, page: Any) -> Any:
        return self.card(page, "One quick question")

    def answer_box(self, page: Any) -> Any:
        return page.get_by_role("textbox", name="Your answer")

    def focus_info(self, page: Any) -> dict[str, Any]:
        return page.evaluate(
            """() => {
                const el = document.activeElement;
                if (!el || el === document.body) return { tag: 'BODY' };
                const cs = getComputedStyle(el);
                return {
                  tag: el.tagName, text: (el.textContent || '').trim().slice(0, 60),
                  tabindex: el.getAttribute('tabindex'), label: el.getAttribute('aria-label'),
                  id: el.id, focusVisible: el.matches(':focus-visible'),
                  outlineStyle: cs.outlineStyle, outlineWidth: cs.outlineWidth,
                };
            }"""
        )

    def no_alert(self, page: Any) -> None:
        """No visible role=alert on the page (Next's route announcer lives in a shadow root, skipped)."""
        alerts = page.evaluate(
            """() => Array.from(document.querySelectorAll('[role=alert]'))
                .filter((el) => el.getClientRects().length > 0 && (el.textContent || '').trim())
                .map((el) => el.textContent.trim())"""
        )
        assert not alerts, f"unexpected alert(s): {alerts}"

    def wait_done(self, page: Any, telling: bool = False) -> None:
        self.expect(self.feed_heading(page, done=True, telling=telling)).to_be_visible(timeout=RUN_TIMEOUT_MS)

    # ------------------------------------------------------------------ walk

    def walk(self) -> None:
        try:
            self.step("signin", "sign in and open /add", self.s_signin)
            self.step("working", "1. the feed while the studio works", self.s_working, needs=["signin"])
            self.step("fu_empty", "2a. follow-up: an empty answer", self.s_followup_empty, needs=["working"])
            self.step("reload", "5. reload while paused: the card and the typed answer return, no ring on its heading",
                      self.s_reload, needs=["fu_empty"])
            self.step("fu_500", "2b. follow-up: the answer fails with 500", self.s_followup_500, faults=True,
                      needs=["fu_empty"])
            self.step("fu_net", "2c. follow-up: the answer meets a dropped connection", self.s_followup_network,
                      faults=True, needs=["fu_empty"])
            self.step("fu_sent", "2d. follow-up: a sent answer", self.s_followup_sent, needs=["fu_empty"])
            self.step("merge", "4. the merge card for Grandpa Lucho", self.s_merge, needs=["fu_sent"])
            self.step("props", "3a. prop review: keep and remove", self.s_props, needs=["merge"])
            self.step("upload_fail", "3b. prop review: a photo upload fails, its preview is dropped",
                      self.s_upload_fail, faults=True, needs=["props"])
            self.step("upload_ok", "3c. prop review: a photo uploads", self.s_upload_ok, needs=["props"])
            self.step("review_sent", "3d. prop review: sent, the studio works on", self.s_review_sent,
                      needs=["upload_ok"])
            self.step("second_tab", "6. the run reattached in a second tab while it works", self.s_second_tab,
                      needs=["review_sent"])
            self.step("ready", "10. scene ready: the way in, the scene, home and another memory", self.s_ready,
                      needs=["review_sent"])
            self.step("finished", "7. a finished run reattached: replays to the end, nothing aborted",
                      self.s_finished, needs=["ready"])
            self.step("book", "10b. the moment's book page and its scene (Ethan's pages)", self.s_book,
                      needs=["ready"], owner="ethan")
            self.step("run2", "start a second run for the 409 and the dropped stream", self.s_run2, needs=["signin"])
            self.step("drop", "8. a dropped stream: the reconnecting line, then the card again", self.s_drop,
                      faults=True, needs=["run2"])
            self.step("fu_409", "2e. follow-up: a 409 NOT_WAITING closes the card quietly", self.s_followup_409,
                      needs=["run2"])
            self.step("gone", "9. an unknown run id", self.s_gone, needs=["signin"])
            self.step("second", "11. a second telling from an open question: no follow-up, no prop review",
                      self.s_second)
        finally:
            self.close_all()

    # ------------------------------------------------------------------ the states

    def s_signin(self) -> str:
        self.ctx, self.page = self.new_context("teller")
        self.sign_in(self.page, self.who["email"], self.who["password"], f"/add?family={self.fid}")
        self.snap(self.page, "add-form")
        return f"{self.who['email']} on /add?family={self.fid}"

    def s_working(self) -> str:
        page = self.page
        self.start_run(page, STORY)
        self.expect(self.feed_heading(page)).to_be_visible()
        # The slip of what was sent sits above the feed.
        self.expect(self.vis(page.get_by_text(STORY))).to_be_visible()
        self.expect(page.get_by_role("link", name="Family tree")).to_have_attribute("href", "/")
        opening = self.vis(page.get_by_text("Opening the studio\u2026")).count() > 0
        self.snap(page, "01a-feed-opening")
        # Then the plan and the steps, while the studio works (0.1 s a step in real mode, so the
        # run may already wait on the follow-up by the time a shot is taken).
        working = self.vis(page.get_by_text("The studio is working on your moment."))
        self.expect(page.get_by_text("The plan", exact=True).or_(self.follow_up(page)).first).to_be_visible(
            timeout=RUN_TIMEOUT_MS
        )
        busy = working.count() > 0
        self.snap(page, "01b-feed-working")
        return (f"run {self.run_id}; {'opening' if opening else 'past opening'} at the first shot, "
                f"{'working' if busy else 'already paused'} at the second")

    def s_followup_empty(self) -> str:
        page = self.page
        self.expect(self.follow_up(page)).to_be_visible(timeout=RUN_TIMEOUT_MS)
        self.expect(self.vis(page.get_by_text(f"“{FOLLOW_UP}”"))).to_be_visible()
        page.get_by_role("button", name="Send answer").click()
        self.expect(self.vis(page.get_by_text("Write a few words before sending."))).to_be_visible()
        box = self.answer_box(page)
        self.expect(box).to_have_attribute("aria-invalid", "true")
        self.expect(box).to_be_focused()
        self.snap(page, "02a-followup-empty")
        return ""

    def s_reload(self) -> str:
        page = self.page
        self.typed = "Rosa and the whole choir"
        self.answer_box(page).fill(self.typed)
        page.wait_for_timeout(150)
        page.reload()
        self.expect(self.follow_up(page)).to_be_visible(timeout=RUN_TIMEOUT_MS)
        box = self.answer_box(page)
        self.expect(box).to_have_value(self.typed)
        page.wait_for_timeout(700)  # the card scrolls into view and takes focus
        heading = self.focus_info(page)
        assert heading.get("tag") == "H2" and heading.get("text") == "One quick question", (
            f"focus after the reload is on {heading}, not the card's heading"
        )
        assert heading.get("outlineStyle") == "none", f"the script-focused heading shows a focus ring: {heading}"
        self.snap(page, "05a-reload-card-restored")
        page.keyboard.press("Tab")
        field = self.focus_info(page)
        assert field.get("tag") == "TEXTAREA", f"Tab from the heading went to {field}, not the answer field"
        assert field.get("focusVisible") and field.get("outlineStyle") not in ("none", ""), (
            f"keyboard focus on the answer field shows no ring: {field}"
        )
        self.snap(page, "05b-reload-keyboard-focus")
        return f"heading outline {heading.get('outlineStyle')}; field outline {field.get('outlineStyle')}"

    def _answer_fails(self, state: str, message: str, **fault: Any) -> str:
        page = self.page
        if not self.typed:
            self.typed = "Rosa and the whole choir"
            self.answer_box(page).fill(self.typed)
        self.fault(page, "POST", "/families/{fid}/runs/{id}/answer", **fault)
        page.get_by_role("button", name="Send answer").click()
        self.expect(self.vis(page.get_by_role("alert").filter(has_text=message))).to_be_visible()
        self.expect(self.follow_up(page)).to_be_visible()
        self.expect(self.answer_box(page)).to_have_value(self.typed)
        self.expect(page.get_by_role("button", name="Send answer")).to_be_enabled()
        self.snap(page, state)
        self.clear_faults(page)
        return ""

    def s_followup_500(self) -> str:
        return self._answer_fails("02b-followup-500", ANSWER_5XX, status=500)

    def s_followup_network(self) -> str:
        return self._answer_fails("02c-followup-network", ANSWER_NETWORK, network=True)

    def s_followup_sent(self) -> str:
        page = self.page
        page.get_by_role("button", name="Send answer").click()
        # The card says thanks until the run's next step closes it (0.1 s in real mode).
        self.expect(self.follow_up(page)).to_have_count(0, timeout=RUN_TIMEOUT_MS)
        self.no_alert(page)
        self.expect(self.vis(page.get_by_text(self.typed))).to_be_visible()
        self.snap(page, "02d-followup-sent")
        return ""

    def s_merge(self) -> str:
        page = self.page
        self.expect(self.card(page, "Is this someone we know?")).to_be_visible(timeout=RUN_TIMEOUT_MS)
        self.expect(self.vis(page.get_by_text(MERGE_TEXT, exact=True))).to_be_visible()
        self.snap(page, "04a-merge-card")
        page.get_by_role("button", name="Yes, same person").click()
        self.expect(self.card(page, "Is this someone we know?")).to_have_count(0, timeout=RUN_TIMEOUT_MS)
        self.no_alert(page)
        self.snap(page, "04b-merge-decided")
        return ""

    def chip_options(self, page: Any, label: str) -> Any:
        return page.get_by_role("radio", name=label, exact=True)

    def pick(self, page: Any, label: str) -> None:
        """Click the first chip's option: its radio is visually hidden, its label is the target."""
        radio = self.chip_options(page, label).first
        radio.locator("xpath=ancestor::label[1]").click()
        self.expect(radio).to_be_checked()

    def s_props(self) -> str:
        page = self.page
        self.expect(self.card(page, "The things in your story")).to_be_visible(timeout=RUN_TIMEOUT_MS)
        chips = self.chip_options(page, "Keep").count()
        assert chips >= 1, "the review lists no props"
        self.snap(page, "03a-props")
        self.pick(page, "Remove")
        self.snap(page, "03b-props-remove")
        self.pick(page, "Keep")
        self.pick(page, "Use a photo")
        self.expect(self.vis(page.get_by_text(re.compile(r"^Choose a photo")).first)).to_be_visible()
        return f"{chips} prop(s) in the review"

    def photo_input(self, page: Any) -> Any:
        return page.locator("input[type=file]").first

    def s_upload_fail(self) -> str:
        page = self.page
        self.fault(page, "POST", "/families/{fid}/media", status=500)
        self.photo_input(page).set_input_files(files=[{"name": "hymn-books.png", "mimeType": "image/png", "buffer": PNG}])
        alert = self.vis(page.get_by_role("alert")).first
        self.expect(alert).to_be_visible()
        text = alert.inner_text()
        self.expect(page.get_by_role("img", name=re.compile(r"^Your photo for"))).to_have_count(0)
        self.expect(self.vis(page.get_by_text(re.compile(r"^Using ")))).to_have_count(0)
        self.snap(page, "03c-props-upload-failed")
        self.clear_faults(page)
        return f"alert: {text}"

    def s_upload_ok(self) -> str:
        page = self.page
        self.photo_input(page).set_input_files(files=[{"name": "hymn-books.png", "mimeType": "image/png", "buffer": PNG}])
        self.expect(self.vis(page.get_by_text("Using hymn-books.png"))).to_be_visible()
        self.expect(page.get_by_role("img", name=re.compile(r"^Your photo for"))).to_be_visible()
        self.no_alert(page)
        self.snap(page, "03d-props-upload-ok")
        return ""

    def s_review_sent(self) -> str:
        page = self.page
        page.get_by_role("button", name="Looks good, keep going").click()
        self.expect(self.card(page, "The things in your story")).to_have_count(0, timeout=RUN_TIMEOUT_MS)
        working = self.feed_heading(page).count() > 0
        self.snap(page, "03e-review-sent-working")
        self.no_alert(page)
        return "working when shot" if working else "already done when shot"

    def s_second_tab(self) -> str:
        tab = self.attach(self.ctx.new_page(), "second-tab")
        try:
            self.goto(tab, self.run_url)
            self.expect(self.feed_heading(tab).or_(self.feed_heading(tab, done=True))).to_be_visible()
            self.snap(tab, "06a-second-tab")
            self.wait_done(tab)
            self.expect(self.follow_up(tab)).to_have_count(0)
            self.snap(tab, "06b-second-tab-done")
        finally:
            self.current = self.page
        self._second_tab = tab
        return ""

    def s_ready(self) -> str:
        page = self.page
        self.current = page
        self.wait_done(page)
        fid = re.escape(quote(self.fid, safe=""))
        open_link = page.get_by_role("link", name=re.compile(r"^Open your page"))
        self.expect(open_link).to_have_attribute("href", re.compile(rf"^/memories/[^/?]+\?family={fid}$"))
        href = open_link.get_attribute("href") or ""
        self.memory_id = href.split("/memories/", 1)[1].split("?", 1)[0]
        watch = page.get_by_role("link", name="Watch the scene")
        if self.real:
            self.expect(watch).to_have_attribute("href", f"/render/{self.memory_id}?family={quote(self.fid, safe='')}")
        else:
            self.expect(watch).to_have_count(0)  # mock moments cannot play on the stage
        nav = page.get_by_role("navigation", name="What next")
        self.expect(nav.get_by_role("link", name="Back to the family tree")).to_have_attribute("href", "/")
        self.expect(nav.get_by_role("link", name="Add another memory")).to_have_attribute(
            "href", f"/add?family={quote(self.fid, safe='')}"
        )
        self.expect(page.get_by_role("link", name="Family tree", exact=True)).to_have_attribute("href", "/")
        self.snap(page, "10-scene-ready")
        status, body = self.api_call(page, "GET", f"/families/{self.fid}/memories/{self.memory_id}")
        if status != 200:
            raise Seam("api", f"GET the new memory answered {status}: {body}")
        memory = (body or {}).get("memory", {})
        if "grandpa_luis" not in memory.get("participantIds", []) and "luis" not in memory.get("participantIds", []):
            raise Seam("api" if self.real else "pages",
                       f"the approved merge did not add Luis: participants {memory.get('participantIds')}")
        return f"memory {self.memory_id}: {href}" + (" + watch link" if self.real else " (mock: no watch link)")

    def s_finished(self) -> str:
        page = self.attach(self.ctx.new_page(), "reattach-finished")
        try:
            mark = len(self.watch.problems)
            self.goto(page, self.run_url)
            self.wait_done(page)
            page.wait_for_timeout(2500)  # FINISH_DRAIN_MS and a margin: any abort would show by now
            aborted = [p for p in self.watch.since(mark) if "requestfailed" in p]
            self.snap(page, "07-reattach-finished")
            assert not aborted, "requests failed on a finished run's page:\n" + "\n".join(aborted)
            self.expect(page.get_by_role("link", name=re.compile(r"^Open your page"))).to_be_visible()
        finally:
            page.close()
            self.current = self.page
        return "no requestfailed line"

    def s_book(self) -> str:
        """Where "Open your page" and "Watch the scene" lead: each must show this moment."""
        if not self.real:
            raise Skip("mock moments only open on the book page in real mode checks")
        status, body = self.api_call(self.page, "GET", f"/families/{self.fid}/memories/{self.memory_id}")
        title = ((body or {}).get("memory") or {}).get("title", "") if status == 200 else ""
        notes, wrong = [], []
        fid = quote(self.fid, safe="")
        for name, path in (("book", f"/memories/{self.memory_id}?family={fid}"),
                           ("render", f"/render/{self.memory_id}?family={fid}")):
            page = self.attach(self.ctx.new_page(), name)
            try:
                self.goto(page, path)
                page.wait_for_load_state("networkidle", timeout=60_000)
                page.wait_for_timeout(1500)
                text = page.evaluate("() => document.body.innerText")
                try:
                    where = str(self.snap(page, f"10b-{name}"))
                except Exception as exc:  # noqa: BLE001 - a WebGL page can outlast the screenshot timeout
                    where = f"no screenshot: {type(exc).__name__}"
                notes.append(f"{path}: {where}")
                if "isn\u2019t in the book yet" in text or "isn't in the book yet" in text:
                    wrong.append(f"{path} says the moment is not in the book yet ({where})")
                elif title and title.split()[0] not in text:
                    wrong.append(f"{path} does not show the moment {title!r} ({where})")
            finally:
                page.close()
        self.current = self.page
        if wrong:
            raise Seam("ethan", "REQUEST (Ethan): the finished run links here with ?family=<fid>; please load "
                                "the moment from GET /families/{fid}/memories/{id}: " + "; ".join(wrong))
        return "; ".join(notes)

    def s_run2(self) -> str:
        page = self.page
        self.goto(page, f"/add?family={self.fid}")
        self.start_run(page, STORY_PLAIN)
        self.expect(self.follow_up(page)).to_be_visible(timeout=RUN_TIMEOUT_MS)
        return f"run {self.run_id}"

    def s_drop(self) -> str:
        if self.real:
            tab = self.attach(self.ctx.new_page(), "dropped-stream")
            self.fault(tab, "GET", "/families/{fid}/runs/{id}/stream", network=True, times=2)
        else:
            tab = self.ctx.new_page()
            tab.add_init_script(mock_faults_script(
                [{"method": "GET", "path": "/families/{fid}/runs/{id}/stream", "network": True, "times": 2}]
            ))
            self.attach(tab, "dropped-stream")
        try:
            self.goto(tab, self.run_url)
            line = self.vis(tab.get_by_text(RECONNECTING))
            self.expect(line).to_be_visible()
            self.snap(tab, "08a-reconnecting")
            self.expect(self.follow_up(tab)).to_be_visible(timeout=RUN_TIMEOUT_MS)
            self.expect(line).to_have_count(0)
            self.snap(tab, "08b-reconnected")
        finally:
            tab.close()
            self.current = self.page
        return ""

    def s_followup_409(self) -> str:
        page = self.page
        self.answer_box(page).fill("Just the two of us")
        self.fault(page, "POST", "/families/{fid}/runs/{id}/answer", status=409, detail=NOT_WAITING)
        page.get_by_role("button", name="Send answer").click()
        self.expect(self.follow_up(page)).to_have_count(0)
        self.no_alert(page)
        self.expect(self.vis(page.get_by_text("Answered earlier."))).to_be_visible()
        self.snap(page, "02e-followup-409-closed")
        self.clear_faults(page)
        # Someone did answer it elsewhere: do that now, so the run goes on.
        status, body = self.api_call(page, "POST", f"/families/{self.fid}/runs/{self.run_id}/answer",
                                     {"kind": "answer", "text": "Just the two of us"})
        assert status == 204, f"answering behind the page's back answered {status}: {body}"
        self.expect(self.card(page, "The things in your story")).to_be_visible(timeout=RUN_TIMEOUT_MS)
        page.get_by_role("button", name="Looks good, keep going").click()
        self.wait_done(page)
        return ""

    def s_gone(self) -> str:
        page = self.page
        self.goto(page, f"/add?family={self.fid}&run=no-such-run-{int(time.time())}")
        self.expect(page.get_by_role("heading", level=2, name=GONE)).to_be_visible()
        self.expect(page.get_by_role("link", name="Add a memory", exact=True)).to_have_attribute(
            "href", f"/add?family={quote(self.fid, safe='')}"
        )
        self.expect(page.get_by_role("main").get_by_role("link", name="Family tree", exact=True).last).to_have_attribute(
            "href", "/"
        )
        self.snap(page, "09-unknown-run")
        return ""

    def s_second(self) -> str:
        if self.real:
            email, person, memory_id = SECOND_REAL[self.view]
            password = "stitch-demo"
        else:
            email, person, memory_id = SECOND_MOCK
            password = MOCK["password"]
        ctx, page = self.new_context("second-teller", init_script=PAUSE_WATCH)
        self.sign_in(page, email, password, f"/add?family={self.fid}")
        status, body = self.api_call(page, "GET", f"/families/{self.fid}/questions")
        assert status == 200, f"GET questions answered {status}"
        asked = [q for q in (body or {}).get("questions", []) if q.get("memoryId") == memory_id]
        assert asked, f"{email} has no open question about {memory_id}: {body}"
        question = asked[0]["text"]
        status, body = self.api_call(page, "GET", f"/families/{self.fid}/memories/{memory_id}")
        assert status == 200, f"GET the moment answered {status}"
        title = body["memory"]["title"]
        before = set(((body["memory"].get("timeline") or {}).get("narrations") or {}).keys())

        # From the prompt on /add: "Add your version" opens the second-telling form.
        prompt = page.get_by_role("listitem").filter(has_text=question)
        self.expect(prompt).to_be_visible()
        self.snap(page, "11a-add-with-question")
        pick = prompt.get_by_role("button").first
        self.expect(pick).to_contain_text("Add your version")
        pick.click()
        self.expect(page.get_by_role("heading", level=1, name="Add your telling")).to_be_visible()
        in_url = f"memoryId={quote(memory_id, safe='')}" in page.url
        self.expect(self.vis(page.get_by_text(title)).first).to_be_visible()
        self.expect(self.vis(page.get_by_text(question)).first).to_be_visible()
        self.snap(page, "11b-second-telling-form")
        self.vis(page.get_by_label(re.compile(r"^Your story"))).fill(SECOND_STORY)
        page.get_by_role("button", name="Save my telling").click()
        page.wait_for_url(re.compile(r"[?&]run="))
        self.expect(self.feed_heading(page, telling=True).or_(self.feed_heading(page, done=True, telling=True))).to_be_visible()
        self.snap(page, "11c-second-telling-working")
        self.wait_done(page, telling=True)
        seen = page.evaluate("() => window.__stitchSawPause")
        assert not seen, f"a second telling showed pause card(s): {seen}"
        open_link = page.get_by_role("link", name=re.compile(r"^Open the moment"))
        self.expect(open_link).to_have_attribute(
            "href", f"/memories/{quote(memory_id, safe='')}?family={quote(self.fid, safe='')}"
        )
        self.snap(page, "11d-second-telling-done")
        status, body = self.api_call(page, "GET", f"/families/{self.fid}/memories/{memory_id}")
        memory = (body or {}).get("memory", {})
        tellers = [t.get("authorId") for t in memory.get("tellings", [])]
        narrations = ((memory.get("timeline") or {}).get("narrations") or {})
        owner = "api" if self.real else "pages"
        if person not in tellers:
            raise Seam(owner, f"{person}'s telling is not on {memory_id}: tellers {tellers}")
        if person not in narrations:
            raise Seam(owner, f"{person} has no narration track on {memory_id}: tracks {sorted(narrations)}")
        track = narrations[person] or {}
        said = " ".join(str(x.get("text", "")) for x in (track.get("lines") or []) + (track.get("captions") or []))
        if "from where I sat" not in said:
            raise Seam(owner, f"{person}'s narration track does not carry the new telling: {said[:160]!r}")
        status, body = self.api_call(page, "GET", f"/families/{self.fid}/questions")
        still = [q for q in (body or {}).get("questions", []) if q.get("memoryId") == memory_id]
        if still:
            raise Seam(owner, f"the question about {memory_id} is still open after the telling")
        self.current = self.page
        return (f"{email} told {memory_id} ({title!r}); tracks {sorted(before)} -> {sorted(narrations)}; "
                f"the form {'put' if in_url else 'did not put'} the moment in the URL")


# --------------------------------------------------------------------------- main


def run_mode(mock: bool, args: Any, out: Path, seams: SeamLog) -> None:
    from playwright.sync_api import sync_playwright

    mode = "mock" if mock else "real"
    print(f"\n######## {mode} mode", flush=True)
    if not wait_port_free(args.web_port) or (not mock and not wait_port_free(args.api_port)):
        seams.record(f"[{mode}] start the stack", False, "a port from the previous mode is still busy")
        return
    try:
        with run_stack(
            mock=mock,
            web_port=args.web_port,
            api_port=None if mock else args.api_port,
            run_step=RUN_STEP,
            out_dir=out / mode,
        ) as stack, sync_playwright() as pw:
            browser = pw.chromium.launch(headless=not args.headed)
            try:
                for view in VIEWPORTS:
                    RunSweep(stack, browser, seams, mode, view).walk()
            finally:
                browser.close()
    except StackError as exc:
        seams.record(f"[{mode}] start the stack", False, first_lines(str(exc), 12))
    except Exception as exc:  # noqa: BLE001 - anything outside a step is still a seam
        seams.record(f"[{mode}] sweep", False, first_lines(f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}", 12))


def main(argv: list[str] | None = None) -> int:
    args = sweep_args("Sweep the live agent run on /add?run=.", argv)
    require_chromium()
    out = sweep_out_dir(args.out, prefix="stitch-sweep-run-")
    print(f"Results: {out}", flush=True)
    seams = SeamLog()
    modes = sweep_modes(args.mode)
    for mock in modes:
        run_mode(mock, args, out, seams)
    print()
    code = finish(seams, out)
    counts = seams.to_json()["counts"]
    print(f"\nseams: {out / 'seams.json'}")
    print(f"counts: {counts['total']} checks, {counts['ok']} ok, {counts['failed']} failed "
          f"(by owner: {counts['failedByOwner']})")
    for mock in modes:
        mode = "mock" if mock else "real"
        print(f"screenshots ({mode}): {out / mode / 'shots'} ({len(shots_taken(out / mode))} taken)")
    requests = [e for e in seams.entries if e["owner"] == "ethan" and not e["ok"]]
    if requests:
        print("\nREQUESTs for Ethan:")
        for e in requests:
            print(f"- {e['step']}: {first_lines(e['detail'], 4)}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
