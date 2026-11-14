"""The weekend demo path in a real browser, against the offline API, in two browser contexts.

From api/ (it uses the API's uv environment, which has Playwright):

    cd api && uv run python ../web/components/flow/e2e/demo_path.py [--headed] [--out DIR]
        [--join claim|add-self] [--keep-going] [--api-port N] [--web-port N]

Person A signs up, starts a family and makes an invite link. Person B, in a second
browser context, signs up from the invite and claims the relative A added (or adds
themselves). A tells a story with the scripted agent, answers the follow-up, keeps the
hymn books and waits for the scene. Both books get the memory. B answers the question
the linker left them, which opens /add as a second telling, and B's narration track
appears on the memory. Last, it looks at where the finished flow leads (the scene link,
the home page, B's book page) and records what is missing there for Ethan.

Every step lands in seams.json (see harness.SeamLog) with a screenshot. Exit code 1 when
a step owned by the flow pages or the API failed, 0 otherwise (Ethan's seams are
reported, not failed). It exits 0 with a SKIP line when Playwright's Chromium is missing.
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
from urllib.parse import unquote, urlsplit  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness import (  # noqa: E402
    PageWatch,
    SeamLog,
    Stack,
    StackError,
    require_chromium,
    run_stack,
    shot,
    shots_taken,
)

TIMEOUT_MS = 30_000
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
    "for every id except 'demo'. The add page links there when a scene is ready (\"Open your page\" "
    "-> /memories/<memoryId>). Please load real moments: GET /families/{fid}/memories/{id} "
    "(MemoryResponse: memory.timeline, memory.screenplay, props, sets, people) with the session "
    "cookie, the family from localStorage 'stitch.familyId' or the signed-in user's first family "
    "(GET /me), and render that moment's spread."
)
REQUEST_HOME_FAMILY = (
    "REQUEST for Ethan: the home page (/) always shows the demo tree \"{shown}\" "
    "(components/tree/demoFamily.ts) instead of the signed-in family \"{family}\". Every flow page "
    "sends people home when they finish (sign in, join, settings 'Home', add 'Family tree'). Please "
    "read GET /me (credentials: 'include'), pick the family from localStorage 'stitch.familyId' "
    "(else the first), and draw GET /families/{{fid}}/tree; keep the demo tree only when signed out."
)
REQUEST_HOME_LINKS = (
    "REQUEST for Ethan: the home page (/) has no way into the flow pages. Please add, signed in: "
    "'Add a memory' -> /add?family=<fid>, 'Family settings' -> /families/<fid>/settings; signed "
    "out: 'Log in' -> /login and 'Sign up' -> /signup. (Missing now: {missing}.)"
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
        self.title = ""

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
    ) -> None:
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
        self.record_browser_problems(name, marks)
        if not ok and result_owner == "pages" and not self.keep_going:
            raise Abort(name)

    def page(self, label: str) -> Any:
        return self.page_a if label == "A" else self.page_b

    def record_browser_problems(self, name: str, marks: dict[str, int]) -> None:
        for label, watch in (("A", self.watch_a), ("B", self.watch_b)):
            mark = marks[label]
            new = watch.since(mark)
            if not new:
                continue
            groups: dict[str, list[str]] = {}
            for problem, event in zip(new, watch.events_since(mark), strict=True):
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

    def s6_add_story(self) -> str:
        page = self.page_a
        expect = self.expect
        self.pid_a = self.person_id(self.ctx_a)
        page.goto(f"{self.web}/add?family={self.fid}")
        expect(page.get_by_role("heading", level=1, name="Add a memory")).to_be_visible()
        self.label(page,"Your story").fill(STORY)
        self.label(page,"When was this?").fill(STORY_YEAR)
        who = page.get_by_role("group", name="Who was there?")
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
        match = re.fullmatch(r"/memories/([^/?#]+)", self.scene_href)
        if not match:
            raise AssertionError(f"'Open your page' links to {self.scene_href!r}, not /memories/<id>")
        self.memory_id = unquote(match.group(1))
        expect(page.get_by_role("link", name="Add another memory")).to_be_visible()
        return f"memory {self.memory_id}; scene link {self.scene_href}; run page {page.url}"

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
        shot(page, self.stack, "8-B-question")
        note.click()
        expect(note).to_have_attribute("aria-pressed", "true")
        expect(page.get_by_role("heading", level=1, name="Add your telling")).to_be_visible()
        banner = self.text(page,re.compile(r"adding your version of"))
        expect(banner).to_contain_text(self.title)
        expect(self.text(page,qtext, exact=True).first).to_be_visible()
        expect(self.label(page,"When was this?")).to_have_count(0)
        shot(page, self.stack, "8-B-second-telling-form")
        self.label(page,"Your story").fill(SECOND_STORY)
        page.get_by_role("button", name="Save my telling").click()
        expect(page.get_by_role("heading", level=1, name="Your telling", exact=True)).to_be_visible()
        expect(page.get_by_role("heading", name="Your telling is stitched in")).to_be_visible()
        return f"B answered '{qtext}' at {page.url}"

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
            self.step("5", f"5 B signs up and joins ({self.join_mode})", self.s5_join, pages=("B",), needs=join_needs)
            self.step("6", "6 A adds a story (follow-up, props, scene)", self.s6_add_story, needs=("5",))
            self.step("7", "7 the memory is in A's and B's books", self.s7_books, owner="api", pages=(), needs=("6",))
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


def summarize(seams: SeamLog, out: Path) -> int:
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
    return summarize(seams, out)


if __name__ == "__main__":
    raise SystemExit(main())
