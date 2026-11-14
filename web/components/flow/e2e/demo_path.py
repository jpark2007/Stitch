"""The weekend demo path in a real browser, against the offline API, in two browser contexts.

From api/ (it uses the API's uv environment, which has Playwright):

    cd api && uv run python ../web/components/flow/e2e/demo_path.py [--headed] [--out DIR]
        [--join claim|add-self] [--keep-going] [--strict-ethan] [--api-port N] [--web-port N]

Person A signs up, starts a family and makes an invite link. Person B, in a second
browser context, signs up from the invite and claims the relative A added (or adds
themselves). A tells a story with the scripted agent, answers the follow-up, keeps the
hymn books and waits for the scene; the finished run links to /memories/<id>?family=<fid>
and 'Watch the scene' plays it on /render/<id>?family=<fid> until the stage says it is
ready. Both books get the memory. B answers the question the linker left them, which
opens /add as a second telling naming the moment, and the finished telling links into
the moment and home; B's narration track appears on the memory. Last, it looks at where
the finished flow leads (the scene link, the home page, B's book page, the way home from
settings and /add) and records what is missing there for Ethan.

Along the way A and B each leave /add open while the family moves on (4b, 5b). Coming
back to the window (visibilitychange + focus, no reload) must show what changed: B under
A's 'Who was there?' (6r) and the question A's story left B, which opens a second telling
of that new moment when picked (7b). These checks record a 'pages' seam on failure and
never stop the run.

Every step lands in seams.json (see harness.SeamLog) with a screenshot. Exit code 1 when
a step owned by the flow pages or the API failed, 0 otherwise (Ethan's seams are
reported, not failed, unless --strict-ethan). It exits 0 with a SKIP line when
Playwright's Chromium is missing.
"""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True  # keep __pycache__ out of web/

import argparse  # noqa: E402
import re  # noqa: E402
import struct  # noqa: E402
import tempfile  # noqa: E402
import time  # noqa: E402
import traceback  # noqa: E402
import uuid  # noqa: E402
import zlib  # noqa: E402
from collections.abc import Callable  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Any  # noqa: E402
from urllib.parse import parse_qs, unquote, urlsplit  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness import (  # noqa: E402
    PageWatch,
    SeamLog,
    Stack,
    StackError,
    app_problems,
    expected_noise,
    require_chromium,
    run_stack,
    shot,
    shots_taken,
    wait_stage_ready,
)

TIMEOUT_MS = 30_000
STAGE_READY_MS = 60_000
PASSWORD = "stitch-e2e-password"
STORY = (
    "We sang from the green hymn books on the church steps. "
    "The wind took the pages and everyone laughed. "
    "Afterwards we shared peaches on the wall by the gate."
)
FOLLOW_UP_ANSWER = "My cousin held the umbrella the whole time."
SECOND_STORY = (
    "I remember the books slipping out of my arms on the top step. "
    "Someone caught the last one before it hit the puddle."
)
STORY_YEAR = "1998"

# The routes Ethan owns; a problem on one of them is his to look at.
ETHAN_PATHS = re.compile(r"^/(people|memories|threads|render)(/|$)|^/$")

REQUEST_SCENE = (
    "REQUEST for Ethan: /memories/[id] shows the placeholder \"This page isn’t in the book yet\" "
    "for every id except 'demo' (app/memories/[id]/MemoryBook.tsx:48, `if (id !== \"demo\")`). The "
    "add page links there when a scene is ready (\"Open your page\" -> /memories/<memoryId>?family=<fid>). "
    "Please load real moments: GET /families/{fid}/memories/{id} (MemoryResponse: memory.timeline, "
    "memory.screenplay, props, sets, people) with the session cookie, the family from ?family= (else "
    "localStorage 'stitch.familyId', else the signed-in user's first family from GET /me), and render "
    "that moment's spread."
)
REQUEST_HOME_FAMILY = (
    "REQUEST for Ethan: the home page (/) shows \"{shown}\" instead of the signed-in family \"{family}\" "
    "(components/tree/TreeHome.tsx:170, FamilyHome). Every flow page sends people home when they finish "
    "(sign in, join, settings and add 'Family tree'). Please read GET /me (credentials: 'include'), pick "
    "the family from localStorage 'stitch.familyId' (else the first), and draw GET /families/{{fid}}/tree; "
    "keep the demo tree only when signed out."
)
REQUEST_HOME_LINKS = (
    "REQUEST for Ethan: the home page (/) has no way into the flow pages (components/tree/TreeHome.tsx:170, "
    "the signed-in FamilyHome inside HomeShell, whose nav has only 'Family threads'). Please add, signed in: "
    "'Add a memory' -> /add?family=<fid>, 'Family settings' -> /families/<fid>/settings; signed "
    "out: 'Log in' -> /login and 'Sign up' -> /signup. (Missing now: {missing}.)"
)
REQUEST_STAGE = (
    "REQUEST for Ethan: the finished run's 'Watch the scene' ({href}) opens /render/[memoryId], but the "
    "stage never set <html data-stage-ready=\"1\"> / window.__STITCH_READY__ within {seconds} s for a moment "
    "the scripted agent made (app/render/[memoryId]/RenderView.tsx). Please load "
    "GET /families/{{fid}}/memories/{{id}} from ?family= with the session cookie, play it, and set the "
    "ready flag once the first frame is drawn (or show an error and still settle)."
)
REQUEST_PERSON_BOOK = (
    "REQUEST for Ethan: /people/[id] is a stub (it renders only \"Storybook\"), so a person's "
    "book never shows the moments they are in. Please load GET /families/{{fid}}/people/{{id}}/book "
    "(BookResponse: person, memories ordered by year, props, sets, people) and render it; the "
    "new memory '{title}' should be on {name}'s pages."
)


class Seam(Exception):
    """A step failure whose fix belongs to someone other than the step's default owner."""

    def __init__(self, owner: str, message: str) -> None:
        super().__init__(message)
        self.owner = owner


class Abort(Exception):
    """Stop the run: a flow-page step failed and --keep-going was not given."""


def tiny_png(width: int = 8, height: int = 8) -> bytes:
    """A small solid-colour PNG, built in memory (no files, no Pillow)."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    row = b"\x00" + bytes((0x6B, 0x8F, 0x71)) * width
    raw = row * height
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def check_scene_href(href: str, route: str, fid: str, what: str) -> str:
    """The moment id in a finished run's link `/<route>/<id>?family=<fid>`.

    Raises a 'pages' Seam naming the exact href when it is not that shape, or when its
    `family` is missing or is not `fid` (the page would open the moment in the wrong family).
    """
    match = re.fullmatch(rf"/{re.escape(route)}/([^/?#]+)(?:\?([^#]*))?", href)
    if not match:
        raise Seam("pages", f"{what} links to {href!r}, not /{route}/<id>?family={fid}")
    family = parse_qs(match.group(2) or "").get("family", [])
    if family != [fid]:
        shown = ", ".join(repr(f) for f in family) if family else "missing"
        raise Seam("pages", f"{what} links to {href!r}: its family is {shown}, not the created family {fid!r}")
    return unquote(match.group(1))


def visible_hrefs(page: Any) -> list[str]:
    """The href of every link a person can see on the page (role locators skip hidden ones)."""
    return [h or "" for h in page.get_by_role("link").evaluate_all("els => els.map(e => e.getAttribute('href'))")]


def banner_text(locator: Any) -> str:
    try:
        return str(locator.first.inner_text(timeout=1_000)).strip()
    except Exception:  # noqa: BLE001 - only for a failure message
        return "(not found)"


def first_lines(text: str, n: int = 6) -> str:
    lines = [ln for ln in str(text).splitlines() if ln.strip()]
    out = "\n".join(lines[:n])
    return out if len(lines) <= n else out + "\n..."


def owner_for_url(url: str) -> str:
    """Who owns the page at `url` (a page URL on the web app, not a script or an API URL)."""
    path = urlsplit(url).path or "/"
    return "ethan" if ETHAN_PATHS.search(path) else "pages"


def problem_owner(event: dict[str, str]) -> str:
    """Who looks at a PageWatch event: the API for a 5xx, else whoever owns the page it was on."""
    if event.get("kind") == "http":
        return "api"
    return owner_for_url(event.get("pageUrl", ""))


class DemoPath:
    def __init__(self, stack: Stack, browser: Any, seams: SeamLog, args: argparse.Namespace) -> None:
        from playwright.sync_api import expect

        self.expect = expect
        self.stack = stack
        self.web = stack.web_url
        self.api = stack.api_url or ""
        self.seams = seams
        self.keep_going = args.keep_going
        self.join_mode = args.join

        tag = uuid.uuid4().hex[:8]
        self.a_name = f"Avery Quill {tag[:4]}"
        self.b_name = f"Blair Quill {tag[4:]}"
        self.a_email = f"a-{tag}@example.com"
        self.b_email = f"b-{tag}@example.com"
        self.family = f"The Quills {tag[:4]}"

        self.ctx_a = browser.new_context(viewport={"width": 1280, "height": 900})
        self.ctx_b = browser.new_context(viewport={"width": 390, "height": 844})
        self.ctx_a.set_default_timeout(TIMEOUT_MS)
        self.ctx_b.set_default_timeout(TIMEOUT_MS)
        self.page_a = self.ctx_a.new_page()
        self.page_b = self.ctx_b.new_page()
        self.watch_a = PageWatch()
        self.watch_b = PageWatch()
        self.watch_a.attach(self.page_a, "A")
        self.watch_b.attach(self.page_b, "B")

        # Filled in as the steps run.
        self.done: set[str] = set()
        self.fid = ""
        self.code = ""
        self.pid_a = ""
        self.pid_b = ""
        self.memory_id = ""
        self.scene_href = ""
        self.run_url = ""
        self.title = ""
        # /add left open in a window before the family moved on (steps 4b and 5b).
        self.a_add_open = False
        self.b_add_open = False

    # ------------------------------------------------------------------ the runner

    def step(
        self,
        key: str,
        name: str,
        fn: Callable[[], str | None],
        *,
        owner: str = "pages",
        pages: tuple[str, ...] = ("A",),
        needs: tuple[str, ...] = (),
        app_only: bool = False,
        fatal: bool = True,
    ) -> None:
        """Run one step: record its seam, screenshot `pages`, then the browser problems it caused.

        With `app_only`, those browser problems leave out harness.expected_noise() (see
        harness.app_problems); otherwise every problem PageWatch saw is recorded. A step
        that is not `fatal` records its seam and never stops the run (a regression check
        the later steps do not depend on).
        """
        missing = [n for n in needs if n not in self.done]
        if missing:
            self.seams.record(name, False, f"not run: needs step(s) {', '.join(missing)} first", owner)
            return
        print(f"\n== {name}", flush=True)
        marks = {"A": len(self.watch_a.problems), "B": len(self.watch_b.problems)}
        ok = True
        result_owner = owner
        try:
            detail = fn() or ""
        except Seam as exc:
            ok, result_owner, detail = False, exc.owner, self.failure_detail(exc, pages)
        except Exception as exc:  # noqa: BLE001 - every failure becomes a recorded seam
            ok, detail = False, self.failure_detail(exc, pages)
        for label in pages:
            page = self.page(label)
            try:
                shot(page, self.stack, f"{key}-{label}{'' if ok else '-failed'}")
            except Exception as exc:  # noqa: BLE001
                print(f"  (screenshot of {label} failed: {first_lines(str(exc), 1)})")
        self.seams.record(name, ok, detail, result_owner)
        if ok:
            self.done.add(key)
        self.record_browser_problems(name, marks, app_only=app_only)
        if not ok and fatal and result_owner == "pages" and not self.keep_going:
            raise Abort(name)

    def page(self, label: str) -> Any:
        return self.page_a if label == "A" else self.page_b

    def record_browser_problems(self, name: str, marks: dict[str, int], *, app_only: bool = False) -> None:
        for label, watch in (("A", self.watch_a), ("B", self.watch_b)):
            mark = marks[label]
            new = watch.since(mark)
            if not new:
                continue
            groups: dict[str, list[str]] = {}
            for problem, event in zip(new, watch.events_since(mark), strict=True):
                if app_only and expected_noise(problem):
                    continue
                groups.setdefault(problem_owner(event), []).append(problem)
            for owner, problems in groups.items():
                self.seams.record(f"{name}: browser problems ({label})", False, "\n".join(problems), owner)

    def failure_detail(self, exc: BaseException, pages: tuple[str, ...]) -> str:
        parts = [first_lines(f"{type(exc).__name__}: {exc}")]
        for label in pages:
            page = self.page(label)
            parts.append(f"{label} at {page.url}")
            try:
                # Leave out Next's route announcer (a role=alert in a shadow root naming the page).
                texts = page.get_by_role("alert").evaluate_all(
                    "els => els.filter(e => { const h = e.getRootNode().host;"
                    " return !(h && h.localName === 'next-route-announcer'); }).map(e => e.innerText)"
                )
                alerts = [t.strip() for t in texts if t and t.strip()]
            except Exception:  # noqa: BLE001
                alerts = []
            if alerts:
                parts.append(f"{label} shows: " + " | ".join(alerts))
            if type(exc).__name__ == "TimeoutError":
                # What a role locator could see when it gave up waiting: tells a missing
                # element from one outside the accessibility tree (aria-hidden, inert).
                try:
                    names = page.get_by_role("button").evaluate_all(
                        "els => els.map(e => (e.innerText || e.getAttribute('aria-label') || '').trim()"
                        " + (e.disabled ? ' (disabled)' : ''))"
                    )
                    parts.append(f"{label} buttons: " + " | ".join(n for n in names if n)[:400])
                except Exception:  # noqa: BLE001
                    pass
        return "\n".join(parts)

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def label(page: Any, text: str | re.Pattern[str], **kw: Any) -> Any:
        """get_by_label, visible elements only.

        With cacheComponents on, a page navigated away from stays mounted (hidden by
        React Activity), so /signup's "Your name" is still in the DOM on /families/new.
        get_by_role skips hidden elements by itself; get_by_label and get_by_text do not.
        """
        return page.get_by_label(text, **kw).filter(visible=True)

    @staticmethod
    def text(page: Any, text: str | re.Pattern[str], **kw: Any) -> Any:
        """get_by_text, visible elements only (see label())."""
        return page.get_by_text(text, **kw).filter(visible=True)

    def check_box(self, box: Any, text: Any | None = None) -> None:
        """Tick a kit checkbox or radio.

        The kit draws them as chips: a visually hidden (sr-only) input inside its label.
        Playwright's check() on that input sometimes cannot place the click (the label
        covers it), so when it does not take, click the label's visible text instead,
        as a person would.
        """
        try:
            box.scroll_into_view_if_needed(timeout=5_000)
            box.check(timeout=5_000)
        except Exception as exc:  # noqa: BLE001
            if text is None:
                raise
            print(f"  (check() did not take, clicking the label text: {first_lines(str(exc), 1)})", flush=True)
            text.scroll_into_view_if_needed()
            text.click()
        self.expect(box).to_be_checked()

    def tick(self, scope: Any, name: str) -> None:
        """Tick the chip labelled exactly `name` inside `scope` (a group or the page).

        PersonPicker, RelationshipPicker and PropChips draw a chip over an sr-only input
        (clipped to nothing), so a click aimed at the input lands on the label's text and
        check() waits it out. Click the text a person sees, then check the input took it.
        """
        box = scope.get_by_label(name, exact=True).filter(visible=True)
        if box.is_checked():
            return
        text = scope.get_by_text(name, exact=True).filter(visible=True)
        text.scroll_into_view_if_needed()
        text.click()
        self.expect(box).to_be_checked()

    @staticmethod
    def refocus(page: Any) -> None:
        """The person comes back to this window: bring it to the front, then fire what the
        browser fires on return (visibilitychange while visible, and a window focus)."""
        page.bring_to_front()
        page.evaluate(
            "() => { document.dispatchEvent(new Event('visibilitychange'));"
            " window.dispatchEvent(new Event('focus')); }"
        )

    @staticmethod
    def mark_page(page: Any, mark: str) -> None:
        """Leave a mark on the page's window, gone after any reload (see marked())."""
        page.evaluate("m => { window.__stitchE2eMark = m; }", mark)

    @staticmethod
    def marked(page: Any, mark: str) -> bool:
        """Whether the window still has the mark: the page was not reloaded since mark_page()."""
        return bool(page.evaluate("m => window.__stitchE2eMark === m", mark))

    def on_add(self, page: Any) -> bool:
        """Whether `page` is still on /add for this family (a new memory, no run)."""
        parts = urlsplit(page.url)
        query = parse_qs(parts.query)
        return parts.path == "/add" and query.get("family") == [self.fid] and "run" not in query

    def api_json(self, ctx: Any, path: str) -> Any:
        res = ctx.request.get(f"{self.api}{path}")
        if not res.ok:
            raise Seam("api", f"GET {path} answered {res.status}: {first_lines(res.text(), 3)}")
        return res.json()

    def person_id(self, ctx: Any) -> str:
        me = self.api_json(ctx, "/me")
        for fam in me.get("families", []):
            if fam.get("id") == self.fid:
                return fam.get("personId") or ""
        raise Seam("api", f"GET /me has no family {self.fid}: {me.get('families')}")

    def signup(self, page: Any, name: str, email: str) -> None:
        expect = self.expect
        expect(page.get_by_role("heading", name="Make your account")).to_be_visible()
        self.label(page,"Your name").fill(name)
        self.label(page,"Email").fill(email)
        self.label(page,"Password", exact=True).fill(PASSWORD)
        page.get_by_role("button", name="Create account").click()

    # ------------------------------------------------------------------ steps

    def s1_signup_a(self) -> str:
        page = self.page_a
        page.goto(f"{self.web}/signup")
        self.signup(page, self.a_name, self.a_email)
        self.expect(page).to_have_url(re.compile(r"/families/new$"))
        self.expect(page.get_by_role("heading", name="Start your family")).to_be_visible()
        return f"{self.a_email} -> {page.url}"

    def s2_family(self) -> str:
        page = self.page_a
        expect = self.expect
        self.label(page,"Family name").fill(self.family)
        expect(self.label(page,"Your name")).to_have_value(self.a_name)
        page.get_by_role("button", name="Start the family").click()
        expect(page).to_have_url(re.compile(r"/families/[^/?]+/settings\?welcome=1$"))
        match = re.search(r"/families/([^/?]+)/settings", page.url)
        assert match, page.url
        self.fid = unquote(match.group(1))
        expect(page.get_by_role("heading", level=1, name=self.family)).to_be_visible()
        return f"family {self.fid} '{self.family}' -> {page.url}"

    def s3_invite(self) -> str:
        page = self.page_a
        expect = self.expect
        page.get_by_role("button", name="Make an invite link").click()
        link = self.label(page,"Invite link")
        expect(link).to_have_value(re.compile(r"/join/[^/]+$"))
        url = link.input_value()
        if not url.startswith(f"{self.web}/join/"):
            raise AssertionError(f"invite link {url!r} is not on {self.web}")
        self.code = unquote(url.rsplit("/join/", 1)[1])
        return f"invite {url}"

    def s4_relative(self) -> str:
        page = self.page_a
        expect = self.expect
        page.goto(f"{self.web}/families/{self.fid}/people/new")
        expect(page.get_by_role("heading", level=1, name="Add a relative")).to_be_visible()
        self.label(page,"Their name").fill(self.b_name)
        married = page.get_by_role("group", name="Married to")
        self.tick(married, self.a_name)
        page.get_by_role("button", name="Add to the tree").click()
        expect(page.get_by_role("heading", name=f"{self.b_name} is in the tree.")).to_be_visible()
        expect(page.get_by_role("link", name="Back to family settings")).to_be_visible()
        return f"added {self.b_name} (married to {self.a_name}) at {page.url}"

    def s4b_a_opens_add(self) -> str:
        """A opens /add before B joins and leaves it open (step 6 comes back to it)."""
        page = self.page_a
        expect = self.expect
        page.goto(f"{self.web}/add?family={self.fid}")
        expect(page.get_by_role("heading", level=1, name="Add a memory")).to_be_visible()
        expect(self.label(page, "Your story")).to_be_visible()
        self.mark_page(page, "a-add")
        self.a_add_open = True
        return f"A's /add is open at {page.url}"

    def s5_join(self) -> str:
        page = self.page_b
        expect = self.expect
        page.goto(f"{self.web}/join/{self.code}")
        expect(page.get_by_role("heading", level=1, name=re.compile(r"invited to"))).to_be_visible()
        page.get_by_role("link", name="Create an account").click()
        expect(page).to_have_url(re.compile(r"/signup\?next=%2Fjoin%2F"))
        expect(self.text(page,"Make an account to join your family.")).to_be_visible()
        self.signup(page, self.b_name, self.b_email)
        expect(page).to_have_url(re.compile(rf"/join/{re.escape(self.code)}$"))

        if self.join_mode == "claim":
            expect(page.get_by_role("group", name=re.compile("Which one is you"))).to_be_visible()
            self.check_box(
                page.get_by_role("radio", name=re.compile(re.escape(self.b_name))),
                self.text(page, self.b_name, exact=True),
            )
            page.get_by_role("button", name=f"Join as {self.b_name}").click()
        else:
            claimable = page.get_by_role("radio", name=re.compile("not on the tree yet"))
            if claimable.count() > 0:
                self.check_box(claimable, self.text(page, re.compile("not on the tree yet")))
            expect(self.label(page, "Your name")).to_have_value(self.b_name)
            page.get_by_role("button", name="Join the family").click()
            expect(page.get_by_role("heading", name=re.compile(r"Where do you fit"))).to_be_visible()
            shot(page, self.stack, "5-B-relationships")
            married = page.get_by_role("group", name="Married to")
            self.tick(married, self.a_name)
            page.get_by_role("button", name="Save and continue").click()

        expect(page.get_by_role("heading", level=1, name=re.compile(r"^Welcome to"))).to_be_visible()
        shot(page, self.stack, "5-B-welcome")
        home = page.get_by_role("link", name=re.compile(r"(Open|See) the family tree"))
        label = home.inner_text().strip()
        home.click()
        expect(page).to_have_url(re.compile(rf"^{re.escape(self.web)}/?$"))
        self.pid_b = self.person_id(self.ctx_b)
        return f"B joined ({self.join_mode}) as person {self.pid_b}; '{label}' -> {page.url}"

    def s5b_b_opens_add(self) -> str:
        """B opens /add before A tells the story, and leaves it open (step 7b comes back to it)."""
        page = self.page_b
        expect = self.expect
        page.goto(f"{self.web}/add?family={self.fid}")
        expect(page.get_by_role("heading", level=1, name="Add a memory")).to_be_visible()
        expect(self.label(page, "Your story")).to_be_visible()
        expect(page.get_by_role("heading", name="Someone asked you")).to_have_count(0)
        self.mark_page(page, "b-add")
        self.b_add_open = True
        return f"B's /add is open at {page.url}, nothing asked yet"

    def a_refocus_add(self, page: Any, who: Any) -> bool:
        """Step 6 on A's /add left open since step 4b: the window comes back and B should
        appear under 'Who was there?' without a reload. Records its own seam; False sends
        step 6 back to a fresh page.goto."""
        name = "6r A's open /add gains B after the window comes back"
        if not self.a_add_open:
            return False  # 4b already recorded why
        if not self.on_add(page):
            self.seams.record(name, False, f"not run: A's page is at {page.url}, not the /add left open in 4b", "pages")
            return False
        try:
            self.refocus(page)
            chip = who.get_by_label(self.b_name, exact=True).filter(visible=True)
            self.expect(chip).to_have_count(1, timeout=10_000)
            if not self.marked(page, "a-add"):
                raise AssertionError(f"A's page reloaded (the window mark from 4b is gone) at {page.url}")
        except Exception as exc:  # noqa: BLE001 - recorded, then step 6 carries on from a goto
            detail = (
                f"A's /add, open since 4b ({self.join_mode}), did not list {self.b_name} under 'Who was there?' "
                f"within 10 s of visibilitychange + focus, without a reload: {first_lines(str(exc), 3)}"
            )
            self.seams.record(name, False, detail, "pages")
            shot(page, self.stack, "6r-A-refocus-failed")
            return False
        note = " (B was on the tree since step 4)" if self.join_mode == "claim" else ""
        self.seams.record(name, True, f"{self.b_name} listed after refocus, no reload{note}", "pages")
        return True

    def s6_add_story(self) -> str:
        page = self.page_a
        expect = self.expect
        self.pid_a = self.person_id(self.ctx_a)
        who = page.get_by_role("group", name="Who was there?")
        # A's /add has been open since before B joined: coming back to the window should
        # be enough. Without it (or when it fails), open the page again.
        if not self.a_refocus_add(page, who):
            page.goto(f"{self.web}/add?family={self.fid}")
        expect(page.get_by_role("heading", level=1, name="Add a memory")).to_be_visible()
        self.label(page,"Your story").fill(STORY)
        self.label(page,"When was this?").fill(STORY_YEAR)
        self.tick(who, self.b_name)
        self.label(page,re.compile(r"^Photos")).first.set_input_files(
            files=[{"name": "steps.png", "mimeType": "image/png", "buffer": tiny_png()}]
        )
        expect(page.get_by_role("list", name="Photos added").get_by_role("listitem")).to_have_count(1)
        shot(page, self.stack, "6-A-form-filled")
        page.get_by_role("button", name="Save this memory").click()

        expect(page.get_by_role("heading", level=1, name="Your memory", exact=True)).to_be_visible()
        expect(page).to_have_url(re.compile(r"[?&]run="))
        follow_up = page.get_by_role("region", name="One quick question")
        expect(follow_up).to_be_visible()
        expect(follow_up.get_by_text("Who else was there that day?")).to_be_visible()
        shot(page, self.stack, "6-A-follow-up")
        follow_up.get_by_label("Your answer").fill(FOLLOW_UP_ANSWER)
        follow_up.get_by_role("button", name="Send answer").click()

        review = page.get_by_role("region", name="The things in your story")
        expect(review).to_be_visible()
        expect(review.get_by_text("hymn books", exact=True)).to_be_visible()
        self.tick(review.get_by_role("group", name="What to do with hymn books"), "Keep")
        shot(page, self.stack, "6-A-prop-review")
        review.get_by_role("button", name="Looks good, keep going").click()

        expect(page.get_by_role("heading", name="Your memory is stitched in")).to_be_visible()
        open_page = page.get_by_role("link", name=re.compile(r"Open your page"))
        expect(open_page).to_be_visible()
        self.scene_href = open_page.get_attribute("href") or ""
        self.run_url = page.url
        self.memory_id = check_scene_href(self.scene_href, "memories", self.fid, "'Open your page'")
        expect(page.get_by_role("link", name="Add another memory")).to_be_visible()
        return f"memory {self.memory_id}; scene link {self.scene_href}; run page {page.url}"

    def s6b_watch_scene(self) -> str:
        """The finished run's 'Watch the scene' plays the moment on the stage until it is ready."""
        page = self.page_a
        if self.run_url and page.url != self.run_url:
            page.goto(self.run_url)  # reattach: the finished run shows its links again
        watch = page.get_by_role("link", name="Watch the scene")
        try:
            self.expect(watch).to_be_visible()
        except AssertionError as exc:
            raise Seam("pages", f"the finished run at {page.url} has no 'Watch the scene' link") from exc
        href = watch.get_attribute("href") or ""
        memory_id = check_scene_href(href, "render", self.fid, "'Watch the scene'")
        if memory_id != self.memory_id:
            raise Seam("pages", f"'Watch the scene' links to {href!r}, a different moment than {self.memory_id!r}")

        mark = len(self.watch_a.problems)
        watch.click()
        self.expect(page).to_have_url(re.compile(rf"/render/{re.escape(self.memory_id)}\?"))
        ready = wait_stage_ready(page, STAGE_READY_MS)
        problems = app_problems(self.watch_a, mark)
        if not ready:
            seen = "\n".join(problems) if problems else "(no console errors, failed requests or 5xx)"
            raise Seam(
                "ethan",
                REQUEST_STAGE.format(href=href, seconds=STAGE_READY_MS // 1000) + f"\nseen at {page.url}\n{seen}",
            )
        return f"{href}: stage ready" + (f"; {len(problems)} browser problem(s)" if problems else "")

    def s7_books(self) -> str:
        memory = self.api_json(self.ctx_a, f"/families/{self.fid}/memories/{self.memory_id}")["memory"]
        self.title = memory.get("title") or ""
        found = []
        for who, pid in (("A", self.pid_a), ("B", self.pid_b)):
            book = self.api_json(self.ctx_a, f"/families/{self.fid}/people/{pid}/book")
            ids = [m.get("id") for m in book.get("memories", [])]
            if self.memory_id not in ids:
                raise Seam("api", f"memory {self.memory_id} is not in {who}'s book ({pid}): {ids}")
            found.append(f"{who}={pid}")
        return f"'{self.title}' is in both books ({', '.join(found)})"

    def open_question(self) -> str:
        questions = self.api_json(self.ctx_b, f"/families/{self.fid}/questions").get("questions", [])
        mine = [q for q in questions if q.get("memoryId") == self.memory_id]
        if not mine:
            raise Seam("api", f"B ({self.pid_b}) has no open question for {self.memory_id}: {questions}")
        return str(mine[0]["text"])

    def s7b_b_refocus(self) -> str:
        """B comes back to the /add left open in 5b: the question A's story left them shows up
        ('Someone asked you') without a reload, and what B had typed stays. Runs before 8a,
        whose page.goto would replace that window's page."""
        page = self.page_b
        expect = self.expect
        qtext = self.open_question()
        if not (self.b_add_open and self.on_add(page)):
            raise Seam("pages", f"B's page is at {page.url}, not the /add left open in 5b")
        heading = page.get_by_role("heading", name="Someone asked you")
        before = heading.count()
        # Something typed before coming back must still be there after the reload under it.
        story = self.label(page, "Your story")
        typed = "Typed before the window came back."
        story.fill(typed)
        try:
            self.refocus(page)
            try:
                expect(heading).to_be_visible(timeout=10_000)
            except AssertionError as exc:
                raise Seam(
                    "pages",
                    "B's /add, open since 5b, did not show 'Someone asked you' within 10 s of the window "
                    f"coming back (visibilitychange + focus) without a reload; GET questions has {qtext!r}",
                ) from exc
            expect(page.get_by_role("button", name=re.compile(re.escape(qtext)))).to_be_visible()
            if not self.marked(page, "b-add"):
                raise Seam("pages", f"B's /add reloaded to show the question (the 5b window mark is gone) at {page.url}")
            expect(story).to_have_value(typed)
            shot(page, self.stack, "7b-B-question-after-refocus")
            # The question is about a moment made after this page opened: picking it opens a
            # second telling of that moment (not "couldn't find the moment"). Then let it go
            # again, so the draft kept for this tab has no pick in it for step 8.
            note = page.get_by_role("button", name=re.compile(re.escape(qtext)))
            note.click()
            expect(note).to_have_attribute("aria-pressed", "true")
            expect(page.get_by_role("heading", level=1, name="Add your telling")).to_be_visible()
            if self.text(page, re.compile(r"couldn.t find the moment")).count():
                raise Seam("pages", "picking the question says it couldn't find the moment made after /add opened")
            banner = self.text(page, re.compile(r"adding your version of"))
            if not (self.title in note.inner_text() or self.title in banner_text(banner)):
                raise Seam(
                    "pages",
                    f"the picked question's second telling does not name '{self.title}' "
                    f"(note {note.inner_text()!r}, header {banner_text(banner)!r})",
                )
            shot(page, self.stack, "7b-B-picked-after-refocus")
            note.click()
            expect(note).to_have_attribute("aria-pressed", "false")
            expect(page.get_by_role("heading", level=1, name="Add a memory")).to_be_visible()
        finally:
            # Leave the form empty again, as later steps expect (the typed draft is kept per tab).
            try:
                story.fill("", timeout=5_000)
            except Exception as exc:  # noqa: BLE001 - the step's own failure is what matters
                print(f"  (could not empty B's story again: {first_lines(str(exc), 1)})", flush=True)
        seen = "already there before the refocus" if before else "appeared after the refocus"
        return (
            f"'Someone asked you' with {qtext!r} {seen}; no reload; the typed story stayed; "
            f"picking it opened the second telling of '{self.title}'"
        )

    def s8a_by_moment_id(self) -> str:
        """/add?memoryId= opens the second telling straight away, with the moment's own question."""
        page = self.page_b
        expect = self.expect
        qtext = self.open_question()
        page.goto(f"{self.web}/add?family={self.fid}&memoryId={self.memory_id}")
        expect(page.get_by_role("heading", level=1, name="Add your telling")).to_be_visible()
        banner = self.text(page, re.compile(r"adding your version of"))
        expect(banner).to_contain_text(self.title)
        expect(self.text(page, "You were asked")).to_be_visible()
        expect(self.text(page, qtext, exact=True).first).to_be_visible()
        expect(page.get_by_role("heading", name="Someone asked you")).to_have_count(0)
        expect(page.get_by_role("button", name="Save my telling")).to_be_visible()
        return f"second-telling mode for '{self.title}' with '{qtext}' at {page.url} (not sent)"

    def s8_second_telling(self) -> str:
        page = self.page_b
        expect = self.expect
        qtext = self.open_question()

        page.goto(f"{self.web}/add?family={self.fid}")
        expect(page.get_by_role("heading", level=1, name="Add a memory")).to_be_visible()
        expect(page.get_by_role("heading", name="Someone asked you")).to_be_visible()
        note = page.get_by_role("button", name=re.compile(re.escape(qtext)))
        expect(note).to_be_visible()
        if not self.title:
            raise Seam("api", f"memory {self.memory_id} has no title to show in the second telling")
        # The prompt names the moment: on the question note itself, or (once picked) in
        # the second-telling header. Either is enough; the detail says which.
        named_on_note = self.title in note.inner_text()
        shot(page, self.stack, "8-B-question")
        note.click()
        expect(note).to_have_attribute("aria-pressed", "true")
        expect(page.get_by_role("heading", level=1, name="Add your telling")).to_be_visible()
        banner = self.text(page,re.compile(r"adding your version of"))
        try:
            expect(banner).to_contain_text(self.title)
            named_in_header = True
        except AssertionError:
            named_in_header = False
        if not (named_on_note or named_in_header):
            raise Seam(
                "pages",
                f"the second telling never names the moment '{self.title}': not on the question note "
                f"({note.inner_text()!r}) and not in the header ({banner_text(banner)!r})",
            )
        expect(self.text(page,qtext, exact=True).first).to_be_visible()
        expect(self.label(page,"When was this?")).to_have_count(0)
        shot(page, self.stack, "8-B-second-telling-form")
        self.label(page,"Your story").fill(SECOND_STORY)
        page.get_by_role("button", name="Save my telling").click()
        expect(page.get_by_role("heading", level=1, name="Your telling", exact=True)).to_be_visible()
        expect(page.get_by_role("heading", name="Your telling is stitched in")).to_be_visible()

        # The finished telling leads on: into the moment (in this family) and home.
        scene = page.get_by_role("link", name=re.compile(r"^Open the moment|^Open your page"))
        try:
            expect(scene).to_be_visible()
        except AssertionError as exc:
            raise Seam("pages", f"the finished second telling at {page.url} has no link into the moment") from exc
        scene_href = scene.get_attribute("href") or ""
        moment = check_scene_href(scene_href, "memories", self.fid, "the finished telling's 'Open the moment'")
        if moment != self.memory_id:
            raise Seam("pages", f"the finished telling links to {scene_href!r}, not the moment {self.memory_id!r}")
        homes = visible_hrefs(page).count("/")
        if not homes:
            raise Seam("pages", f"the finished second telling at {page.url} has no link home ('/')")
        where = "on the question note" if named_on_note else "in the second-telling header"
        return (
            f"B answered '{qtext}' at {page.url}; '{self.title}' named {where}; "
            f"links: {scene_href} and {homes} to '/'"
        )

    def s9_narration(self) -> str:
        res = self.api_json(self.ctx_b, f"/families/{self.fid}/memories/{self.memory_id}")
        memory = res["memory"]
        authors = [t.get("authorId") for t in memory.get("tellings", [])]
        if self.pid_b not in authors:
            raise Seam(
                "pages",
                f"no telling by B ({self.pid_b}) on {self.memory_id}; tellings by {authors}. "
                "The second telling probably went out without memoryId (a new memory instead).",
            )
        narrations = (memory.get("timeline") or {}).get("narrations") or {}
        track = narrations.get(self.pid_b)
        if not track or not (track.get("captions") or track.get("lines") or track.get("words")):
            raise Seam("api", f"timeline.narrations has no track with lines for B ({self.pid_b}): keys {list(narrations)}")
        sp = (memory.get("screenplay") or {}).get("narrations") or {}
        return (
            f"tellings by {authors}; timeline.narrations keys {list(narrations)}; "
            f"screenplay.narrations keys {list(sp)}; version {memory.get('version')}"
        )

    def s10_scene_and_home(self) -> str:
        page = self.page_a
        notes = []

        page.goto(f"{self.web}{self.scene_href}")
        placeholder = self.text(page,re.compile(r"isn[’']t in the book yet"))
        try:
            self.expect(placeholder).to_be_visible(timeout=10_000)
            shown = True
        except AssertionError:
            shown = False
        shot(page, self.stack, "10-A-scene")
        if shown:
            self.seams.record("10a scene link opens the moment", False, f"{REQUEST_SCENE}\nseen at {page.url}", "ethan")
        else:
            self.seams.record("10a scene link opens the moment", True, page.url, "ethan")
        notes.append(f"scene {'placeholder' if shown else 'opened'}")

        page.goto(f"{self.web}/")
        h1 = page.get_by_role("heading", level=1)
        self.expect(h1).to_be_visible()
        shown_name = re.sub(r"[⁠]", "", h1.inner_text()).strip()
        shot(page, self.stack, "10-A-home")
        if self.family not in shown_name:
            detail = REQUEST_HOME_FAMILY.format(shown=shown_name, family=self.family)
            self.seams.record("10b home shows the signed-in family", False, detail, "ethan")
        else:
            self.seams.record("10b home shows the signed-in family", True, shown_name, "ethan")

        wanted = {
            "Add a memory": re.compile(r"/add(\?|$)"),
            "Family settings": re.compile(r"/families/[^/]+/settings"),
        }
        hrefs = [
            h or "" for h in page.get_by_role("link").evaluate_all("els => els.map(e => e.getAttribute('href'))")
        ]
        missing = [name for name, rx in wanted.items() if not any(rx.search(h) for h in hrefs)]
        if missing:
            self.seams.record(
                "10c home links into the flow", False, REQUEST_HOME_LINKS.format(missing=", ".join(missing)), "ethan"
            )
        else:
            self.seams.record("10c home links into the flow", True, "links to /add and settings", "ethan")

        page.goto(f"{self.web}/people/{self.pid_b}")
        try:
            self.expect(self.text(page,self.title, exact=False).first).to_be_visible(timeout=10_000)
            in_book = True
        except AssertionError:
            in_book = False
        shot(page, self.stack, "10-A-person-book")
        if in_book:
            self.seams.record("10d B's book page shows the memory", True, page.url, "ethan")
        else:
            detail = REQUEST_PERSON_BOOK.format(title=self.title, name=self.b_name) + f"\nseen at {page.url}"
            self.seams.record("10d B's book page shows the memory", False, detail, "ethan")

        # The flow pages lead back home: family settings and /add each have a link to '/'.
        home_checks = (
            ("10e family settings links home", "settings", f"/families/{self.fid}/settings", self.family),
            ("10f /add links home", "add", f"/add?family={self.fid}", "Add a memory"),
        )
        for check, tag, path, heading in home_checks:
            page.goto(f"{self.web}{path}")
            try:
                self.expect(page.get_by_role("heading", level=1, name=heading)).to_be_visible()
                hrefs = visible_hrefs(page)
                why = f"no visible link to '/' at {page.url} (links: {sorted(set(hrefs))})"
            except Exception as exc:  # noqa: BLE001 - recorded as this check's failure
                hrefs, why = [], first_lines(f"{type(exc).__name__}: {exc}", 2) + f"\nat {page.url}"
            shot(page, self.stack, f"10-A-{tag}-home-link")
            homes = hrefs.count("/")
            self.seams.record(check, homes > 0, f"{homes} link(s) to '/' at {path}" if homes else why, "pages")
            notes.append(f"{tag}: {homes} home link(s)")
        return "; ".join(notes)

    # ------------------------------------------------------------------ the path

    def run(self) -> None:
        print(f"A: {self.a_name} <{self.a_email}>   B: {self.b_name} <{self.b_email}>   family: {self.family}")
        try:
            self.step("1", "1 A signs up", self.s1_signup_a)
            self.step("2", "2 A starts a family", self.s2_family, needs=("1",))
            self.step("3", "3 A makes an invite link", self.s3_invite, needs=("2",))
            if self.join_mode == "claim":
                self.step("4", "4 A adds B as a relative", self.s4_relative, needs=("2",))
                join_needs: tuple[str, ...] = ("3", "4")
            else:
                join_needs = ("3",)
            # Regression checks for /add loading again when its window comes back: A and B
            # each leave /add open while the family moves on (4b, 5b; checked in 6 and 7b).
            # They record a seam on failure and never stop the run.
            self.step("4b", "4b A opens /add before B joins", self.s4b_a_opens_add, needs=("2",), fatal=False)
            self.step("5", f"5 B signs up and joins ({self.join_mode})", self.s5_join, pages=("B",), needs=join_needs)
            self.step(
                "5b", "5b B opens /add before the story (nothing asked yet)", self.s5b_b_opens_add,
                pages=("B",), needs=("5",), fatal=False,
            )
            self.step("6", "6 A adds a story (follow-up, props, scene)", self.s6_add_story, needs=("5",))
            self.step("6b", "6b A watches the scene", self.s6b_watch_scene, needs=("6",), app_only=True)
            self.step("7", "7 the memory is in A's and B's books", self.s7_books, owner="api", pages=(), needs=("6",))
            self.step(
                "7b", "7b B's open /add shows the question after the window comes back", self.s7b_b_refocus,
                pages=("B",), needs=("7", "5b"), fatal=False,
            )
            self.step(
                "8a", "8a B opens /add?memoryId= (second telling by moment)", self.s8a_by_moment_id,
                pages=("B",), needs=("7",),
            )
            self.step("8", "8 B answers the question (second telling)", self.s8_second_telling, pages=("B",), needs=("7",))
            self.step("9", "9 B's telling and narration track exist", self.s9_narration, owner="api", pages=(), needs=("8",))
            self.step("10", "10 where the finished flow leads", self.s10_scene_and_home, owner="ethan", needs=("6",))
        except Abort as stop:
            print(f"\nStopped at the first flow-page failure ({stop}); --keep-going runs the rest.")
        finally:
            for ctx in (self.ctx_a, self.ctx_b):
                try:
                    ctx.close()
                except Exception:  # noqa: BLE001
                    pass


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="demo_path.py",
        description="Drive the weekend demo path in Chromium against the offline API (seed.serve).",
    )
    parser.add_argument("--headed", action="store_true", help="show the browser windows")
    parser.add_argument("--out", type=Path, default=None, help="where seams.json, shots/ and the logs go (default: a new temp dir)")
    parser.add_argument("--join", choices=("claim", "add-self"), default="claim", help="how B joins (default %(default)s)")
    parser.add_argument("--keep-going", action="store_true", help="run every step even after a flow-page failure")
    parser.add_argument(
        "--strict-ethan",
        action="store_true",
        help="also exit 1 when one of Ethan's pages has a seam (default: reported only)",
    )
    parser.add_argument("--api-port", type=int, default=None, help="API port (default: a free one)")
    parser.add_argument("--web-port", type=int, default=None, help="web port (default: a free one)")
    return parser.parse_args(argv)


RUN_OUTPUTS = ("seams.json", "shots", "api.log", "web.log")


def archive_previous_run(out: Path) -> Path | None:
    """Move an earlier run's outputs in a reused --out dir into out/previous-<time>/.

    Nothing is deleted; the out dir then holds only this run's seams, shots and logs.
    """
    old = [out / name for name in RUN_OUTPUTS if (out / name).exists()]
    if not old:
        return None
    dest = out / f"previous-{time.strftime('%Y%m%d-%H%M%S')}"
    n = 1
    while dest.exists():
        n += 1
        dest = out / f"previous-{time.strftime('%Y%m%d-%H%M%S')}-{n}"
    dest.mkdir(parents=True)
    for path in old:
        path.rename(dest / path.name)
    return dest


def summarize(seams: SeamLog, out: Path, strict_ethan: bool = False) -> int:
    """Write seams.json, print the summary and every REQUEST for Ethan; return the exit code.

    1 when a 'pages' or 'api' seam failed (or, with `strict_ethan`, an 'ethan' one), else 0.
    """
    path = seams.write(out)
    shots = shots_taken(out)
    print(f"\nout dir:     {out}")
    print(f"seams:       {path}")
    print(f"logs:        {out / 'api.log'}, {out / 'web.log'}")
    print(f"screenshots: {len(shots)}")
    for s in shots:
        print(f"  {s.name}")
    failing = seams.failed("pages") + seams.failed("api")
    ethan = seams.failed("ethan")
    print(
        f"\n{len(seams.entries)} checks: {len(seams.entries) - len(seams.failed())} ok, "
        f"{len(seams.failed('pages'))} pages, {len(seams.failed('api'))} api, {len(ethan)} ethan seams"
    )
    for e in ethan:
        for line in e["detail"].splitlines():
            if line.startswith("REQUEST"):
                print(f"  {line}")
    if strict_ethan and ethan:
        print("--strict-ethan: Ethan's seams fail this run.")
        return 1
    return 1 if failing else 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    require_chromium()
    from playwright.sync_api import expect, sync_playwright

    expect.set_options(timeout=TIMEOUT_MS)
    out = args.out.resolve() if args.out else Path(tempfile.mkdtemp(prefix="stitch-e2e-"))
    out.mkdir(parents=True, exist_ok=True)
    moved = archive_previous_run(out)
    if moved is not None:
        print(f"(an earlier run's outputs in {out} were moved to {moved})", flush=True)
    seams = SeamLog()
    started = False
    try:
        with run_stack(mock=False, web_port=args.web_port, api_port=args.api_port, run_step=0.1, out_dir=out) as stack:
            started = True
            print(f"API {stack.api_url}   web {stack.web_url}   out {stack.out_dir}", flush=True)
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=not args.headed)
                try:
                    DemoPath(stack, browser, seams, args).run()
                finally:
                    browser.close()
    except StackError as exc:
        seams.record("start the stack (seed.serve + next dev)", False, str(exc), "pages")
    except Exception as exc:  # noqa: BLE001 - always end with seams.json and a summary
        where = "run the demo path" if started else "start the stack (seed.serve + next dev)"
        tb = "".join(traceback.format_exception(exc)[-6:]).rstrip()
        seams.record(where, False, f"{type(exc).__name__}: {exc}\n{tb}", "pages")
    print()
    return summarize(seams, out, strict_ethan=args.strict_ethan)


if __name__ == "__main__":
    raise SystemExit(main())
