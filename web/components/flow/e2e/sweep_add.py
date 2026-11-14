"""Browser sweep of the add-a-memory page (/add) and its form, in mock mode and against the offline API.

From api/ (it uses the API's uv environment, which has Playwright):

    cd api && uv run python ../web/components/flow/e2e/sweep_add.py \
        [--mode mock|real|both] [--headed] [--out DIR] [--api-port N] [--web-port N]

For each mode (mock: the in-browser mock backend; real: the offline API, seed.serve) and
each viewport (harness.VIEWPORTS), it walks every state of /add:

- signed out: /add goes to /login?next=%2Fadd and back to /add after logging in;
- loading: GET /me, then GET /families/{fid}/tree held (real) or slowed (mock); the
  loading sheet has the form's width, so nothing jumps sideways when the form arrives;
- the empty form: every input labelled, tab order follows the visual order;
- validation: an empty story, a year in the future and before 1850, a file that is not
  a photo, seven photos (six kept, one over-cap line);
- server errors on submit: 500, 503, 413 and a dropped connection: a friendly message
  (413 on the photos), focus on it, nothing typed lost, still on the form;
- success: a double click sends one memory and the page moves to /add?run=; the draft
  kept for a reload is gone afterwards;
- a typed draft (story, year, people, typed question; photos noted) survives a reload;
- family choice: someone in two families sees which one and can switch, keeping the draft;
- the questions waiting for the person, listed above the story; picking one about a
  moment opens the second telling with the moment's title and the question, puts
  ?questionId= in the URL, and a reload keeps both the pick and the story;
- /add?memoryId= opens the second telling straight away (with the person's open question
  about it), and a moment the person told before says so;
- the add-relative round trip: the link goes to /families/{fid}/people/new?next=..., and
  back on /add the new person is ticked and the draft is as it was;
- a picked note says so: a question not about a moment reads 'Answering this' once
  pressed (its accessible name ends ', press again to let it go', the typed
  '+ Answering a question?' toggle stays hidden), and pressing it again restores
  'Answer this'; a moment's question reads 'Adding your version' once picked;
- a run waiting on the person: a new memory pauses on the follow-up, the person leaves
  through the '← Family tree' back link, opens /add again and finds one calm note
  ('... is waiting for your answer.') above a usable form; its 'Go back to it' link
  brings the follow-up card back. Then the run is finished (follow-up answered, props
  kept) and /add shows no note any more.

After both viewports (real mode: after the end-to-end check, since it adds David to a
second family on the shared server), at each viewport:

- a moment in another family: David in two families, the new one remembered, opens
  /add?memoryId=<a demo moment> with no ?family=; the page finds the family that holds
  it, opens the second telling there with the moment's title, names that family in the
  family line, remembers it, and the switch link to the other family is its plain form.

Real mode only, end to end: Ana answers her question about Sam's wedding from the prompt,
the run finishes ("stitched in"), and over the API the memory has Ana's telling and her
narration track in timeline.narrations (an 'api' seam if not).

Accounts. Real: the seeded david@demo.stitch and ana@demo.stitch (password stitch-demo,
api/seed/README.md), plus accounts made over the API. Mock: david@example.com and, for
the moment questions (David has none about a moment in the mock seed), ana@example.com,
password DEMO_PASSWORD from components/flow/mock/demoSeed.ts.

Every check is one seam in seams.json, with a screenshot at the viewport it ran at and a
check of harness.app_problems (minus the failures the check injected on purpose). At
390 px a page that scrolls sideways fails its check. Server errors are injected with
page.route on the API (real) or the mock backend's faults (mock: window.__stitchMock.inject
on a live page, harness.mock_faults_script() before one loads). Exit 1 when a 'pages' or
'api' seam failed, else 0. Without Chromium it prints the SKIP line and exits 0.
"""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True  # keep __pycache__ out of web/

import json  # noqa: E402
import re  # noqa: E402
import struct  # noqa: E402
import time  # noqa: E402
import traceback  # noqa: E402
import uuid  # noqa: E402
import zlib  # noqa: E402
from collections.abc import Callable  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Any  # noqa: E402
from urllib.parse import quote, urlsplit  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness import (  # noqa: E402
    VIEWPORTS,
    WEB_DIR,
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

DESCRIPTION = "Sweep the add-a-memory page (/add) and its form in Chromium, in mock mode and against the offline API."

TIMEOUT_MS = 45_000  # next dev compiles a route on its first visit
EXPECT_MS = 30_000
RUN_TIMEOUT_MS = 120_000  # a scripted run, start to "stitched in"
PASSWORD = "stitch-sweep-pass"

# Real: api/seed/README.md. Mock: components/flow/mock/demoSeed.ts.
REAL_PASSWORD = "stitch-demo"
REAL_DAVID = "david@demo.stitch"
REAL_ANA = "ana@demo.stitch"
REAL_FAMILY = "demo_family"
# David's open question is about Lily's graduation, which he has not told; he told Sam's
# first bike. Ana's question about Sam's wedding is the one the end-to-end check answers.
REAL_ASKED_MOMENT = "demo_lily_graduation"
REAL_TOLD_MOMENT = "demo_sams_first_bike"
REAL_ANA_MOMENT = "demo_sam_wedding_2019"
REAL_ANA_PERSON = "mom"
MOCK_DAVID = "david@example.com"
MOCK_ANA = "ana@example.com"
# The sample moment both David and Ana are in; David told it, Ana is asked about it.
MOCK_MOMENT = "sample_hymn_books"
# Who has an open question that is not about a moment: Ana ("mom", the day Lily was born) in
# the real seed, David ("dad", the day Maya was born) in the mock seed.
REAL_PLAIN_ASKED = "ana"
MOCK_PLAIN_ASKED = "david"

# Copy the page shows (memoryForm.ts, AddMemoryForm.tsx, validation.ts, photos.ts).
STORY_EMPTY = "Tell the story in your own words."
YEAR_EMPTY = "Enter a year."
YEAR_FUTURE = "That year hasn't happened yet."
YEAR_EARLY = "Pick a year from 1850 on."
PHOTOS_TOO_BIG = "Those photos are too big to send together. Try fewer or smaller ones."
NOT_A_PHOTO = "“notes.txt” isn't a photo we can use. Try a JPEG, PNG, WebP, HEIC or GIF."
OVER_CAP = "“photo-7.png” wasn't added: a memory can hold up to 6 photos."
PHOTOS_LEFT_ONE = "Your photo didn’t come along"
ALREADY_TOLD = "You’ve told this one before"
# The calm note on /add about a run left waiting (addPage.ts openRunNotice).
RUN_WAITING = "is waiting for your answer."
RUN_STILL_MAKING = "is still being made."
PICKED_SUFFIX = ", press again to let it go"
WAIT_STORY = "The porch swing creaked every spring and we all took turns on it until the chain gave out."
STORY = "Every Sunday we squeezed into the green car and Dad sang the whole way to church."
YEAR = "1994"

# The routes the flow pages own; a problem anywhere else is Ethan's to look at.
FLOW_PATHS = re.compile(r"^/(add|login|signup|join|families)(/|$)")

SENTINEL_STATUS = 418
SENTINEL_DETAIL = "sweep sentinel: a second memory was sent"


# ----------------------------------------------------------------------------- source facts


def _source(path: Path) -> str:
    try:
        return path.read_text()
    except OSError:
        return ""


def mock_password() -> str:
    """DEMO_PASSWORD from the mock demo seed ("stitch-demo" if it cannot be read)."""
    m = re.search(r'export const DEMO_PASSWORD = "([^"]+)"', _source(WEB_DIR / "components/flow/mock/demoSeed.ts"))
    return m.group(1) if m else "stitch-demo"


def submit_errors() -> dict[str, str]:
    """SUBMIT_ERRORS from memoryForm.ts: what a failed send says (server, network...)."""
    src = _source(WEB_DIR / "components/flow/memoryForm.ts")
    block = re.search(r"export const SUBMIT_ERRORS = \{(.*?)\n\} as const;", src, re.S)
    out: dict[str, str] = {}
    if block:
        for key, text in re.findall(r'(\w+):\s*\n?\s*"([^"]*)"', block.group(1)):
            out[key] = text
    return out


# ----------------------------------------------------------------------------- pure helpers


class Skip(Exception):
    """The check cannot run in this mode; recorded as ok with the reason."""


class Seam(Exception):
    """A failure whose fix belongs to someone other than the check's owner."""

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


def problem_owner(event: dict[str, str]) -> str:
    if event.get("kind") == "http":
        return "api"
    return owner_for_url(event.get("pageUrl", ""))


def slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").lower()[:60] or "step"


def tiny_png(seed: int = 0, width: int = 8, height: int = 8) -> bytes:
    """A small solid-colour PNG, built in memory; `seed` changes the colour (and the bytes)."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    row = b"\x00" + bytes((0x6B, (0x8F + seed * 23) % 256, 0x71)) * width
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(row * height))
        + chunk(b"IEND", b"")
    )


def png_file(name: str, seed: int = 0) -> dict[str, Any]:
    return {"name": name, "mimeType": "image/png", "buffer": tiny_png(seed)}


def query_of(url: str) -> dict[str, str]:
    from urllib.parse import parse_qsl

    return dict(parse_qsl(urlsplit(url).query))


FOCUSED_JS = """() => {
  const a = document.activeElement;
  if (!a || a === document.body) return '(nothing)';
  if (a.hasAttribute('data-form-error')) return 'the form error';
  const by = a.getAttribute('aria-labelledby');
  const lab = by ? document.getElementById(by.split(/\\s+/)[0]) : (a.labels && a.labels[0]);
  if (lab) return 'field "' + lab.textContent.replace(/\\s+/g, ' ').replace(/\\s*\\*\\s*/g, ' ').trim() + '"';
  return a.tagName.toLowerCase() + ' "' + (a.textContent || '').trim().replace(/\\s+/g, ' ').slice(0, 40) + '"';
}"""

FOCUS_ON_FIRST_PROBLEM_JS = """() => {
  const a = document.activeElement;
  const form = a && a.closest('form');
  if (!form) return false;
  const first = form.querySelector('[aria-invalid="true"]') || form.querySelector('[data-form-error]');
  return first === a;
}"""

TAB_STOP_JS = """() => {
  const a = document.activeElement;
  if (!a || a === document.body) return null;
  const r = a.getBoundingClientRect();
  const by = a.getAttribute('aria-labelledby');
  const lab = by ? document.getElementById(by.split(/\\s+/)[0]) : (a.labels && a.labels[0]);
  const raw = lab ? lab.textContent : (a.getAttribute('aria-label') || a.innerText || a.textContent || '');
  const name = raw.trim().replace(/\\s+/g, ' ').slice(0, 48);
  return {
    name, inMain: !!a.closest('main'),
    top: Math.round(r.top + window.scrollY), left: Math.round(r.left + window.scrollX),
    key: a.tagName + '|' + (a.id || name) + '|' + Math.round(r.top + window.scrollY) + '|' + Math.round(r.left),
  };
}"""

DESCRIBED_BY_TEXTS_JS = """el => (el.getAttribute('aria-describedby') || '').split(/\\s+/).filter(Boolean)
  .map(id => { const t = document.getElementById(id); return t ? t.textContent.trim() : null; })"""

UNLABELLED_JS = """() => [...document.querySelectorAll('main input, main textarea, main select')]
  .filter(el => el.type !== 'hidden' && el.offsetParent !== null && !(el.labels && el.labels.length)
                && !el.getAttribute('aria-label') && !el.getAttribute('aria-labelledby'))
  .map(el => el.name || el.id)"""

OVERFLOW_JS = "() => document.documentElement.scrollWidth - window.innerWidth"

ARM_MOCK_FAULT_JS = """fault => {
  const m = window.__stitchMock;
  if (!m || typeof m.inject !== 'function') return false;
  m.inject(fault);
  return true;
}"""

CLEAR_MOCK_FAULTS_JS = """() => { const m = window.__stitchMock; if (m && m.clearFaults) m.clearFaults(); }"""

MOCK_FETCH_JS = """async ([path, method, body]) => {
  const m = window.__stitchMock;
  if (!m) return { status: -1, body: null };
  const init = { method };
  if (body !== null) { init.headers = { 'Content-Type': 'application/json' }; init.body = JSON.stringify(body); }
  const r = await m.fetch(path, init);
  const text = await r.text();
  let parsed = null;
  try { parsed = text ? JSON.parse(text) : null; } catch { parsed = text; }
  return { status: r.status, body: parsed };
}"""

# A sentinel fault still armed means the second request it waited for never came.
SENTINEL_UNUSED_JS = """async path => {
  const m = window.__stitchMock;
  if (!m) return null;
  const r = await m.fetch(path, { method: 'POST', body: new FormData() });
  m.clearFaults();
  return r.status;
}"""

SESSION_DRAFT_JS = "() => { try { return sessionStorage.getItem('stitch.addDraft'); } catch { return 'blocked'; } }"


# ----------------------------------------------------------------------------- one person


class Person:
    """One browser context at one window size, with its page, problem watch and request log."""

    def __init__(self, browser: Any, label: str, viewport: tuple[int, int]) -> None:
        width, height = viewport
        self.label = label
        self.ctx = browser.new_context(viewport={"width": width, "height": height})
        self.ctx.set_default_timeout(TIMEOUT_MS)
        self.ctx.set_default_navigation_timeout(TIMEOUT_MS * 2)
        self.page = self.ctx.new_page()
        self.watch = PageWatch()
        self.watch.attach(self.page, label)
        self.requests: list[tuple[str, str]] = []
        self.page.on("request", lambda r: self.requests.append((r.method, r.url)))
        self.fid = ""
        self.person_id = ""

    def posts_to(self, url: str) -> int:
        return sum(1 for method, u in self.requests if method == "POST" and u.split("?")[0] == url)

    def close(self) -> None:
        try:
            self.ctx.close()
        except Exception:  # noqa: BLE001
            pass


# ----------------------------------------------------------------------------- the sweep


class AddSweep:
    """Every state of /add at one viewport, in one mode."""

    def __init__(self, stack: Stack, browser: Any, seams: SeamLog, view: str) -> None:
        from playwright.sync_api import expect

        self.expect = expect
        self.stack = stack
        self.browser = browser
        self.seams = seams
        self.mock = stack.mock
        self.mode = "mock" if stack.mock else "real"
        self.view = view
        self.viewport = VIEWPORTS[view]
        self.web = stack.web_url
        self.api = stack.api_url or ""
        self.errors = submit_errors()
        self.mock_pw = mock_password()
        self.people: list[Person] = []
        self.run_tag = uuid.uuid4().hex[:6]

    # ------------------------------------------------------------------ runner

    def person(self, label: str) -> Person:
        p = Person(self.browser, f"{self.mode}/{self.view} {label}", self.viewport)
        self.people.append(p)
        return p

    def check(
        self,
        name: str,
        who: Person,
        fn: Callable[[], str | None],
        *,
        owner: str = "pages",
        allow: Callable[[str], bool] | None = None,
        shoot: bool = True,
    ) -> bool:
        full = f"[{self.mode}] {self.view}: {name}"
        print(f"\n== {full}", flush=True)
        mark = len(who.watch.problems)
        result_owner = owner
        try:
            detail = fn() or ""
            ok = True
        except Skip as exc:
            ok, detail = True, f"SKIPPED: {exc}"
        except Seam as exc:
            ok, result_owner, detail = False, exc.owner, self.failure_detail(exc, who)
        except Exception as exc:  # noqa: BLE001 - every failure becomes a recorded seam
            ok, detail = False, self.failure_detail(exc, who)
            if not isinstance(exc, AssertionError):
                print(first_lines(traceback.format_exc(), 12), flush=True)
        if ok and self.view == "phone":
            over = self.overflow(who)
            if over > 1:
                ok, detail = False, f"the page scrolls sideways by {over}px at {self.viewport[0]}px wide; {detail}"
        if shoot or not ok:
            self.snap(who, f"{slug(name)}{'' if ok else '-FAILED'}")
        self.seams.record(full, ok, detail, result_owner)
        self.record_problems(full, who, mark, allow)
        return ok

    def snap(self, who: Person, name: str) -> None:
        try:
            who.page.wait_for_timeout(150)  # focus moves and transitions settle
            shot(who.page, self.stack, f"{self.mode}-{self.view}-{name}")
        except Exception as exc:  # noqa: BLE001
            print(f"  (screenshot failed: {first_lines(str(exc), 1)})", flush=True)

    def overflow(self, who: Person) -> int:
        try:
            over = who.page.evaluate(OVERFLOW_JS)
        except Exception:  # noqa: BLE001
            return 0
        return int(over) if isinstance(over, (int, float)) else 0

    def record_problems(self, name: str, who: Person, mark: int, allow: Callable[[str], bool] | None) -> None:
        """harness.app_problems since the check started, minus what it injected, as seams by owner."""
        kept = set(app_problems(who.watch, mark))
        groups: dict[str, list[str]] = {}
        for problem, event in zip(who.watch.since(mark), who.watch.events_since(mark), strict=True):
            if problem not in kept or (allow is not None and allow(problem)):
                continue
            groups.setdefault(problem_owner(event), []).append(problem)
        for owner, problems in groups.items():
            self.seams.record(f"{name}: browser problems", False, "\n".join(problems), owner)

    def failure_detail(self, exc: BaseException, who: Person) -> str:
        parts = [first_lines(f"{type(exc).__name__}: {exc}"), f"at {who.page.url}"]
        try:
            texts = who.page.locator("form [role=alert]").filter(visible=True).all_inner_texts()
            alerts = [t.strip() for t in texts if t.strip()]
        except Exception:  # noqa: BLE001
            alerts = []
        if alerts:
            parts.append("form shows: " + " | ".join(alerts))
        return "\n".join(parts)

    def close_all(self) -> None:
        for p in self.people:
            p.close()
        self.people.clear()

    # ------------------------------------------------------------------ locators

    @staticmethod
    def story(page: Any) -> Any:
        return page.get_by_label("Your story").filter(visible=True)

    @staticmethod
    def year(page: Any) -> Any:
        return page.get_by_label("When was this?").filter(visible=True)

    @staticmethod
    def photos(page: Any) -> Any:
        return page.get_by_label(re.compile(r"^Photos")).first

    @staticmethod
    def who_group(page: Any) -> Any:
        return page.get_by_role("group", name="Who was there?")

    @staticmethod
    def save(page: Any, label: str = "Save this memory") -> Any:
        return page.get_by_role("button", name=label, exact=True)

    @staticmethod
    def form_alert(page: Any, text: str | None = None) -> Any:
        alerts = page.locator("form [data-form-error]").filter(visible=True)
        return alerts.filter(has_text=text) if text is not None else alerts

    @staticmethod
    def prompts(page: Any) -> Any:
        return page.get_by_role("region", name="Someone asked you")

    def tick_someone(self, page: Any) -> str:
        """Tick the first person not yet ticked under "Who was there?" (by their chip); their name."""
        box = self.who_group(page).get_by_role("checkbox", checked=False).first
        name = (box.evaluate("el => (el.closest('label')?.innerText || '').trim()") or "").strip()
        if not name:
            raise AssertionError("nobody to tick under 'Who was there?'")
        box.locator("xpath=ancestor::label[1]").click()
        self.expect(self.who_group(page).get_by_role("checkbox", name=name, exact=True)).to_be_checked()
        return name

    def heading(self, page: Any, name: str) -> Any:
        return page.get_by_role("heading", level=1, name=name, exact=True)

    def wait_form(self, page: Any, heading: str = "Add a memory") -> None:
        self.expect(self.heading(page, heading)).to_be_visible()
        self.expect(self.story(page)).to_be_visible()

    def path_of(self, page: Any) -> str:
        parts = urlsplit(page.url)
        return parts.path + (f"?{parts.query}" if parts.query else "")

    def expect_field_error(self, page: Any, field: Any, message: str) -> None:
        self.expect(field).to_have_attribute("aria-invalid", "true")
        texts = field.evaluate(DESCRIBED_BY_TEXTS_JS)
        if not any(t and message in t for t in texts):
            raise AssertionError(f"the field is aria-describedby {texts}, which does not include {message!r}")
        self.expect(page.get_by_text(message, exact=True).filter(visible=True)).to_be_visible()

    def expect_focus(self, page: Any, expected: str) -> str:
        """Focus is on the form's first problem (an invalid field, else the form error), named `expected`."""
        try:
            page.wait_for_function(FOCUS_ON_FIRST_PROBLEM_JS, timeout=5_000)
        except Exception as exc:  # noqa: BLE001
            raise AssertionError(f"focus is on {page.evaluate(FOCUSED_JS)}, not the first problem") from exc
        focused = page.evaluate(FOCUSED_JS)
        if not focused.startswith(expected):
            raise AssertionError(f"focus is on {focused}, expected {expected}")
        return focused

    # ------------------------------------------------------------------ accounts and API

    def creds(self, who: str) -> tuple[str, str]:
        if self.mock:
            return (MOCK_ANA if who == "ana" else MOCK_DAVID), self.mock_pw
        return (REAL_ANA if who == "ana" else REAL_DAVID), REAL_PASSWORD

    def api_call(self, p: Person, method: str, path: str, data: Any = None) -> Any:
        """Real mode: the API as this person (cookies shared with the browser). Mock: the mock backend."""
        if self.mock:
            res = p.page.evaluate(MOCK_FETCH_JS, [path, method, data])
            status, body = res["status"], res["body"]
            if status < 200 or status >= 300:
                raise AssertionError(f"mock {method} {path} answered {status}: {body}")
            return body
        url = f"{self.api}{path}"
        req = p.ctx.request
        if method == "GET":
            res = req.get(url)
        elif method == "POST":
            res = req.post(url, data=data if data is not None else {})
        else:
            raise ValueError(method)
        if not res.ok:
            raise Seam("api", f"{method} {path} answered {res.status}: {first_lines(res.text(), 3)}")
        if res.status == 204 or not res.body():
            return None
        return res.json()

    def learn_family(self, p: Person) -> None:
        me = self.api_call(p, "GET", "/me")
        families = me.get("families") or []
        if not families:
            raise Seam("api" if not self.mock else "pages", f"GET /me lists no family: {me}")
        fam = next((f for f in families if f.get("id") == REAL_FAMILY), families[0])
        p.fid, p.person_id = fam["id"], fam.get("personId") or ""

    def sign_in(self, label: str, who: str = "david", target: str | None = "/add",
                faults: list[dict[str, Any]] | None = None) -> Person:
        """A person signed in as `who`, then on `target` ("{fid}" is their family) with its form
        ready; no target stays put. `faults` (mock) are armed for every page load from then on."""
        p = self.person(label)
        email, password = self.creds(who)
        if self.mock:
            # Log in through the page: the mock's accounts and session live in this context.
            # It lands on /families/new (a quiet flow page), which also creates the backend.
            p.page.goto(f"{self.web}/login?next={quote('/families/new', safe='')}")
            p.page.get_by_label("Email", exact=True).filter(visible=True).fill(email)
            p.page.get_by_label("Password", exact=True).filter(visible=True).fill(password)
            p.page.get_by_role("button", name="Log in", exact=True).click()
            self.expect(p.page).to_have_url(re.compile(r"/families/new$"))
            p.page.wait_for_function("() => !!window.__stitchMock")
        else:
            res = p.ctx.request.post(f"{self.api}/auth/login", data={"email": email, "password": password})
            if not res.ok:
                raise Seam("api", f"POST /auth/login as {email} answered {res.status}")
        self.learn_family(p)
        if faults and self.mock:
            p.ctx.add_init_script(mock_faults_script(faults))
        if target is not None:
            target = target.replace("{fid}", quote(p.fid, safe=""))
            p.page.goto(f"{self.web}{target}")
            self.wait_form(p.page, "Add your telling" if "memoryId=" in target else "Add a memory")
        return p

    # ------------------------------------------------------------------ faults

    def arm_mock(self, p: Person, fault: dict[str, Any]) -> None:
        if not p.page.evaluate(ARM_MOCK_FAULT_JS, fault):
            raise AssertionError("window.__stitchMock.inject is missing: the mock backend cannot fail a request")

    def submit_url(self, p: Person) -> str:
        return f"{self.api}/families/{quote(p.fid, safe='')}/memories"

    def fail_submit(self, p: Person, *, status: int | None = None, abort: bool = False,
                    hold: list[Any] | None = None, times: int = 1) -> Callable[[], None]:
        """Fail (or hold) the next POST /families/{fid}/memories; returns the disarm."""
        if self.mock:
            fault: dict[str, Any] = {"method": "POST", "path": "/families/{fid}/memories", "times": max(times, 2)}
            if abort:
                fault["network"] = True
            else:
                fault["status"] = status or 500
            self.arm_mock(p, fault)
            return lambda: p.page.evaluate(CLEAR_MOCK_FAULTS_JS)
        url = self.submit_url(p)
        left = [times]
        headers = {
            "Access-Control-Allow-Origin": self.web,
            "Access-Control-Allow-Credentials": "true",
            "Content-Type": "text/plain",
        }

        def handler(route: Any) -> None:
            if route.request.method != "POST" or left[0] <= 0:
                route.fallback()
                return
            left[0] -= 1
            if hold is not None:
                hold.append(route)
            elif abort:
                route.abort("failed")
            else:
                route.fulfill(status=status or 500, headers=headers, body="Injected failure")

        p.page.route(url, handler)
        return lambda: p.page.unroute(url, handler)

    def fault_allow(self, path_re: str) -> Callable[[str], bool]:
        """The browser lines an injected failure on the API's `path_re` causes by design (real mode)."""
        target = re.compile(re.escape(self.api) + path_re + r"(?![\w/])")

        def allow(problem: str) -> bool:
            if self.mock or not target.search(problem):
                return False
            return (
                " http at " in problem
                or " requestfailed at " in problem
                or ("console.error at " in problem and "Failed to load resource" in problem)
            )

        return allow

    # ------------------------------------------------------------------ the walk

    def run(self) -> None:
        print(f"\n=== {self.mode}/{self.view}", flush=True)
        try:
            self.signed_out_checks()
            self.loading_checks()
            self.form_checks()
            self.server_error_checks()
            self.success_checks()
            self.draft_reload_checks()
            self.family_checks()
            self.prompt_checks()
            self.memory_id_checks()
            self.add_relative_checks()
            self.picked_label_checks()
            self.waiting_run_checks()
        finally:
            self.close_all()

    # ----- 1. signed out

    def signed_out_checks(self) -> None:
        p = self.person("signed-out")
        page = p.page

        def run() -> str:
            page.goto(f"{self.web}/add")
            self.expect(page).to_have_url(re.compile(r"/login\?next=%2Fadd$"))
            self.expect(self.heading(page, "Welcome back")).to_be_visible()
            self.snap(p, "signed-out-login")
            email, password = self.creds("david")
            page.get_by_label("Email", exact=True).filter(visible=True).fill(email)
            page.get_by_label("Password", exact=True).filter(visible=True).fill(password)
            page.get_by_role("button", name="Log in", exact=True).click()
            self.expect(page).to_have_url(re.compile(rf"^{re.escape(self.web)}/add(\?|$)"))
            self.wait_form(page)
            return f"/add -> /login?next=%2Fadd -> log in -> {self.path_of(page)}"

        self.check("signed out: /add goes to /login?next= and back after logging in", p, run)

    # ----- 2. loading

    def loading_checks(self) -> None:
        for what, path, label in (
            ("GET /me", "/me", "Opening your family’s pages…"),
            ("GET /tree", "/families/{fid}/tree", "Getting the page ready…"),
        ):
            self._loading_check(what, path, label)

    def _loading_check(self, what: str, path: str, label: str) -> None:
        held: list[Any] = []
        # Mock: React runs the page's load effect twice in development, so both tries wait.
        faults = [{"method": "GET", "path": path, "delayMs": 3500, "times": 2}] if self.mock else None
        p = self.sign_in(f"loading-{slug(what)}", target=None, faults=faults)
        page = p.page
        api_path = path.replace("{fid}", quote(p.fid, safe=""))
        pattern = re.compile(rf"^{re.escape(self.api)}{re.escape(api_path)}(\?.*)?$")

        def run() -> str:
            if not self.mock:

                def hold(route: Any) -> None:
                    if route.request.method == "GET":
                        held.append(route)
                    else:
                        route.fallback()

                page.route(pattern, hold)
            page.goto(f"{self.web}/add")
            sheet = page.locator("main [role=status][aria-busy=true]").filter(visible=True).filter(has_text=label)
            self.expect(sheet).to_be_visible()
            page.wait_for_timeout(300)
            self.expect(sheet).to_be_visible()
            box = sheet.bounding_box()
            header = page.locator("main header").filter(visible=True).first.bounding_box()
            self.snap(p, f"loading-{slug(what)}")
            if not self.mock:
                if not held:
                    raise AssertionError(f"the page never asked {what}")
                for route in held:
                    route.continue_()
                held.clear()
                page.unroute(pattern)
            self.wait_form(page)
            form = page.locator("main form").filter(visible=True).first.bounding_box()
            header_after = page.locator("main header").filter(visible=True).first.bounding_box()
            if not box or not form or not header or not header_after:
                raise AssertionError("no loading sheet, form or page header to measure")
            dx, dw = abs(box["x"] - form["x"]), abs(box["width"] - form["width"])
            if dx > 2 or dw > 2:
                raise AssertionError(
                    f"the loading sheet is {box['width']:.0f}px wide at x={box['x']:.0f}, the form "
                    f"{form['width']:.0f}px at x={form['x']:.0f}: the page jumps sideways when it loads"
                )
            # The back link, title and lede are the same while loading, so the content starts
            # where the sheet did.
            top = header["y"] + header["height"]
            top_after = header_after["y"] + header_after["height"]
            if abs(header["y"] - header_after["y"]) > 2 or abs(top - top_after) > 2:
                raise AssertionError(
                    f"the page header moved from y={header['y']:.0f}..{top:.0f} to "
                    f"y={header_after['y']:.0f}..{top_after:.0f} when the form arrived"
                )
            return (f"'{label}' at {box['width']:.0f}px wide, the form {form['width']:.0f}px; "
                    "the back link, title and lede stay put")

        try:
            self.check(f"loading: {what} slow, the sheet keeps the form's width", p, run, shoot=False)
        finally:
            for route in held:
                try:
                    route.continue_()
                except Exception:  # noqa: BLE001
                    pass

    # ----- 3 and 4. the empty form, validation

    def form_checks(self) -> None:
        p = self.sign_in("form", target="/add?family={fid}")
        page = p.page

        def render() -> str:
            self.expect(self.save(page)).to_be_enabled()
            self.expect(self.story(page)).to_have_value("")
            self.expect(self.year(page)).to_have_value("")
            self.expect(self.photos(page)).to_be_attached()
            self.expect(self.who_group(page)).to_be_visible()
            unlabelled = page.evaluate(UNLABELLED_JS)
            if unlabelled:
                raise AssertionError(f"inputs with no label: {unlabelled}")
            asked = self.prompts(page).get_by_role("button").count()
            mine = self.who_group(page).get_by_role("checkbox", checked=True).count()
            if mine != 1:
                raise AssertionError(f"{mine} people ticked on an empty form, expected only the author")
            return f"story, year, photos and people labelled; {asked} question note(s) above the story"

        self.check("empty form: renders, every input labelled", p, render)
        self.check("empty form: tab order follows the visual order", p, lambda: self.tab_order(p), shoot=False)

        def empty_submit() -> str:
            page.reload()
            self.wait_form(page)
            self.save(page).click()
            self.expect_field_error(page, self.story(page), STORY_EMPTY)
            self.expect_field_error(page, self.year(page), YEAR_EMPTY)
            focused = self.expect_focus(page, 'field "Your story')
            return f"story and year flagged; focus on {focused}"

        self.check("validation: an empty story (and no year)", p, empty_submit)

        def bad_years() -> str:
            self.story(page).fill(STORY)
            self.year(page).fill("3000")
            self.year(page).press("Enter")
            self.expect_field_error(page, self.year(page), YEAR_FUTURE)
            self.expect(self.story(page)).not_to_have_attribute("aria-invalid", "true")
            focused = self.expect_focus(page, 'field "When was this?')
            self.snap(p, "validation-year-future")
            self.year(page).fill("1700")
            self.save(page).click()
            self.expect_field_error(page, self.year(page), YEAR_EARLY)
            self.expect(self.story(page)).to_have_value(STORY)
            return f"Enter submitted; 3000 and 1700 flagged; focus on {focused}; story kept"

        self.check("validation: a bad year (the future, before 1850)", p, bad_years)

        def wrong_type() -> str:
            self.photos(page).set_input_files(
                files=[{"name": "notes.txt", "mimeType": "text/plain", "buffer": b"not a photo"}]
            )
            alert = page.get_by_role("alert").filter(has_text="a photo we can use").filter(visible=True)
            self.expect(alert).to_have_text(NOT_A_PHOTO)
            self.expect(page.get_by_role("list", name="Photos added")).to_have_count(0)
            self.expect(self.photos(page)).to_have_attribute("aria-invalid", "true")
            return f"'{NOT_A_PHOTO}'; nothing added"

        self.check("validation: a file that is not a photo", p, wrong_type)

        def too_many() -> str:
            files = [png_file(f"photo-{i}.png", i) for i in range(1, 8)]
            self.photos(page).set_input_files(files=files)
            added = page.get_by_role("list", name="Photos added").get_by_role("listitem")
            self.expect(added).to_have_count(6)
            problems = page.get_by_role("alert").filter(has_text="a memory can hold up to").filter(visible=True)
            self.expect(problems).to_be_visible()
            lines = [t.strip() for t in problems.locator("p").all_inner_texts() if t.strip()]
            if lines != [OVER_CAP]:
                raise AssertionError(f"expected the one over-cap line {OVER_CAP!r}, got {lines}")
            self.expect(page.get_by_text("6 of 6", exact=False).filter(visible=True).first).to_be_visible()
            return f"6 kept; one line: '{OVER_CAP}'"

        self.check("validation: seven photos, one over the cap", p, too_many)

    def tab_order(self, p: Person) -> str:
        page = p.page
        page.reload()
        self.wait_form(page)
        stops: list[dict[str, Any]] = []
        seen: set[str] = set()
        for _ in range(60):
            page.keyboard.press("Tab")
            stop = page.evaluate(TAB_STOP_JS)
            if stop is None:
                continue
            if stop["key"] in seen:
                break
            seen.add(stop["key"])
            if not stop["inMain"]:
                if stops:
                    break
                continue
            stops.append(stop)
        names = [s["name"] for s in stops]
        order = " -> ".join(names)
        for a, b in zip(stops, stops[1:]):
            same_row = abs(a["top"] - b["top"]) <= 12
            if (same_row and b["left"] < a["left"]) or (not same_row and b["top"] < a["top"] - 12):
                raise AssertionError(f'"{b["name"]}" comes after "{a["name"]}" but sits above or left of it: {order}')
        expected = ["Your story", "When was this?", "Photos", "Save this memory"]
        at = []
        for want in expected:
            idx = next((i for i, n in enumerate(names) if n.startswith(want)), -1)
            if idx < 0:
                raise AssertionError(f"'{want}' is not reachable by Tab: {order}")
            at.append(idx)
        if at != sorted(at):
            raise AssertionError(f"fields out of order: {order}")
        return order

    # ----- 5. server errors on submit

    def server_error_checks(self) -> None:
        p = self.sign_in("server-errors", target="/add?family={fid}")
        page = p.page
        cases = (
            (500, self.errors.get("server", ""), "form"),
            (503, self.errors.get("server", ""), "form"),
            (413, PHOTOS_TOO_BIG, "photos"),
            (0, self.errors.get("network", ""), "form"),
        )
        for status, message, where in cases:
            what = f"HTTP {status}" if status else "a dropped connection"

            def run(status: int = status, message: str = message, where: str = where) -> str:
                if not message:
                    raise AssertionError("memoryForm.ts SUBMIT_ERRORS could not be read")
                start = page.url
                self.story(page).fill(STORY)
                self.year(page).fill(YEAR)
                if status == 413:
                    self.photos(page).set_input_files(files=[png_file("big-day.png", 3)])
                before = p.posts_to(self.submit_url(p))
                disarm = self.fail_submit(p, status=status or None, abort=status == 0)
                try:
                    self.save(page).click()
                    if where == "form":
                        self.expect(self.form_alert(page, message)).to_be_visible()
                        focused = self.expect_focus(page, "the form error")
                    else:
                        self.expect_field_error(page, self.photos(page), message)
                        self.expect(self.form_alert(page)).to_have_count(0)
                        focused = self.expect_focus(page, 'field "Photos')
                    self.expect(self.save(page)).to_be_enabled()
                    self.expect(self.story(page)).to_have_value(STORY)
                    self.expect(self.year(page)).to_have_value(YEAR)
                    if page.url != start:
                        raise AssertionError(f"left the form: {start} -> {page.url}")
                    if not self.mock and p.posts_to(self.submit_url(p)) - before != 1:
                        raise AssertionError(f"{p.posts_to(self.submit_url(p)) - before} memories sent, not 1")
                finally:
                    disarm()
                if status == 413:
                    page.get_by_role("button", name="Remove big-day.png").click()
                    self.expect(page.get_by_role("list", name="Photos added")).to_have_count(0)
                return f"'{message}'; focus on {focused}; story and year kept; still at {self.path_of(page)}"

            self.check(f"server error on submit: {what}", p, run,
                       allow=self.fault_allow(r"/families/[^/]+/memories"))

    # ----- 6. success (and the double click)

    def success_checks(self) -> None:
        p = self.sign_in("success", target="/add?family={fid}")
        page = p.page

        def run() -> str:
            self.story(page).fill(f"{STORY} ({self.run_tag} {self.view})")
            self.year(page).fill(YEAR)
            self.photos(page).set_input_files(files=[png_file("sunday.png", 5)])
            url = self.submit_url(p)
            before = p.posts_to(url)
            held: list[Any] = []
            disarm: Callable[[], None] | None = None
            if self.mock:
                # The first send waits; a second one would use up the sentinel.
                self.arm_mock(p, {"method": "POST", "path": "/families/{fid}/memories", "delayMs": 2500, "times": 1})
                self.arm_mock(p, {"method": "POST", "path": "/families/{fid}/memories", "status": SENTINEL_STATUS,
                                  "detail": SENTINEL_DETAIL, "times": 1})
            else:
                disarm = self.fail_submit(p, hold=held)
            try:
                self.save(page).dblclick()
                busy = page.get_by_role("button", name="Saving…", exact=True)
                self.expect(busy).to_be_disabled()
                self.expect(busy).to_have_attribute("aria-busy", "true")
                self.expect(self.story(page)).to_be_disabled()
                self.expect(self.year(page)).to_be_disabled()
                page.wait_for_timeout(400)  # room for a second request, if one were coming
                self.snap(p, "success-pending")
                if not self.mock:
                    sent = p.posts_to(url) - before
                    if sent != 1:
                        raise AssertionError(f"a double click sent {sent} memories, not 1")
                    for route in held:
                        route.fallback()
                    held.clear()
            finally:
                for route in held:
                    try:
                        route.fallback()
                    except Exception:  # noqa: BLE001
                        pass
                if disarm:
                    disarm()
            busy_seen = "busy 'Saving…', the button and fields disabled"
            self.expect(page).to_have_url(re.compile(r"[?&]run="), timeout=EXPECT_MS)
            self.expect(self.heading(page, "Your memory")).to_be_visible()
            if self.mock:
                status = page.evaluate(SENTINEL_UNUSED_JS, f"/families/{p.fid}/memories")
                if status != SENTINEL_STATUS:
                    raise AssertionError(f"a double click sent a second memory (sentinel used; probe got {status})")
            else:
                sent = p.posts_to(url) - before
                if sent != 1:
                    raise AssertionError(f"a double click sent {sent} memories, not 1")
            run_id = query_of(page.url).get("run", "")
            draft = page.evaluate(SESSION_DRAFT_JS)
            if draft and p.fid in json.loads(draft):
                raise AssertionError(f"the draft kept for a reload is still there after sending: {draft}")
            return f"{busy_seen}; one memory sent; -> {self.path_of(page)} (run {run_id}); kept draft cleared"

        self.check("success: a double click sends one memory, then /add?run=", p, run)

        def after() -> str:
            page.goto(f"{self.web}/add?family={quote(p.fid, safe='')}")
            self.wait_form(page)
            self.expect(self.story(page)).to_have_value("")
            self.expect(self.year(page)).to_have_value("")
            return "a new /add after sending starts blank"

        self.check("success: the next /add starts blank", p, after, shoot=False)

    # ----- B2. the typed draft survives a reload

    def draft_reload_checks(self) -> None:
        p = self.sign_in("draft-reload", target="/add?family={fid}")
        page = p.page

        def run() -> str:
            story = f"The porch light was always on. ({self.run_tag})"
            self.story(page).fill(story)
            self.year(page).fill("1987")
            name = self.tick_someone(page)
            open_q = page.get_by_role("button", name=re.compile(r"Answering a question\?"))
            question = "What was the porch like?"
            if open_q.count():
                open_q.click()
            page.get_by_label(re.compile(r"^What question are you answering\?")).filter(visible=True).fill(question)
            self.photos(page).set_input_files(files=[png_file("porch.png", 7)])
            self.expect(page.get_by_role("list", name="Photos added").get_by_role("listitem")).to_have_count(1)
            page.wait_for_timeout(200)
            page.reload()
            self.wait_form(page)
            self.expect(self.story(page)).to_have_value(story)
            self.expect(self.year(page)).to_have_value("1987")
            ticked = self.who_group(page).get_by_role("checkbox", name=name, exact=True)
            self.expect(ticked).to_be_checked()
            self.expect(page.get_by_label(re.compile(r"^What question are you answering\?")).filter(visible=True)
                        ).to_have_value(question)
            self.expect(page.get_by_role("list", name="Photos added")).to_have_count(0)
            self.expect(page.get_by_text(PHOTOS_LEFT_ONE, exact=False).filter(visible=True)).to_be_visible()
            return f"story, year 1987, '{name}' ticked and the typed question back; the photo noted as left behind"

        self.check("reload keeps the typed draft (photos noted)", p, run)

    # ----- 7. family choice

    def family_checks(self) -> None:
        p = self.person("two-families")
        page = p.page
        first, second = f"The Sweeps {self.run_tag}", f"The Others {self.run_tag}"

        def run() -> str:
            if self.mock:
                page.goto(f"{self.web}/signup")
                page.get_by_label("Your name").filter(visible=True).fill("Tess Twofam")
                page.get_by_label("Email", exact=True).filter(visible=True).fill(
                    f"two-{self.view}-{self.run_tag}@example.com")
                page.get_by_label("Password", exact=True).filter(visible=True).fill(PASSWORD)
                page.get_by_role("button", name="Create account", exact=True).click()
                self.expect(page).to_have_url(re.compile(r"/families/new$"))
                page.wait_for_function("() => !!window.__stitchMock")
            else:
                email = f"two-{self.view}-{self.run_tag}@example.com"
                self.api_call(p, "POST", "/auth/signup", {"email": email, "password": PASSWORD, "name": "Tess Twofam"})
            fids = []
            for name in (first, second):
                made = self.api_call(p, "POST", "/families", {"name": name, "person": {"name": "Tess Twofam"}})
                fids.append(made["family"]["id"])
            page.goto(f"{self.web}/add?family={quote(fids[0], safe='')}")
            self.wait_form(page)
            line = page.get_by_text("Adding to").filter(visible=True).first
            self.expect(line).to_contain_text(first)
            switch = page.get_by_role("navigation", name="Add to another family").get_by_role("link", name=second)
            self.expect(switch).to_have_attribute("href", f"/add?family={quote(fids[1], safe='')}")
            self.story(page).fill(STORY)
            self.year(page).fill(YEAR)
            self.snap(p, "family-choice")
            switch.click()
            self.expect(page).to_have_url(re.compile(rf"/add\?family={re.escape(quote(fids[1], safe=''))}$"))
            self.wait_form(page)
            self.expect(page.get_by_text("Adding to").filter(visible=True).first).to_contain_text(second)
            self.expect(self.story(page)).to_have_value(STORY)
            self.expect(self.year(page)).to_have_value(YEAR)
            # The draft went along: the family left behind starts blank.
            page.goto(f"{self.web}/add?family={quote(fids[0], safe='')}")
            self.wait_form(page)
            self.expect(self.story(page)).to_have_value("")
            page.go_back()
            self.wait_form(page)
            self.expect(self.story(page)).to_have_value(STORY)
            return f"'Adding to {first}', switched to '{second}' with the story and year; the first family starts blank"

        self.check("family choice: two families, the switch keeps the draft", p, run)

    # ----- 8. the questions waiting for this person

    def prompt_checks(self) -> None:
        # Real: David's question is about Lily's graduation. Mock: only Ana is asked about a
        # moment (the hymn books), so she is the one who picks.
        who = "david" if not self.mock else "ana"
        p = self.sign_in("prompts", who=who, target="/add?family={fid}")
        page = p.page
        state: dict[str, Any] = {}

        def listed() -> str:
            questions = self.api_call(p, "GET", f"/families/{quote(p.fid, safe='')}/questions")["questions"]
            if not questions:
                raise Seam("api" if not self.mock else "pages", f"{who} has no open questions")
            region = self.prompts(page)
            self.expect(region).to_be_visible()
            for q in questions[:2]:
                self.expect(region.get_by_role("button", name=re.compile(re.escape(q["text"])))).to_be_visible()
            about = [q for q in questions if q.get("memoryId")]
            if not about:
                raise Seam("api" if not self.mock else "pages", f"none of {who}'s questions is about a moment")
            state["q"] = about[0]
            moment = self.api_call(p, "GET", f"/families/{quote(p.fid, safe='')}/memories/"
                                              f"{quote(about[0]['memoryId'], safe='')}")["memory"]
            state["title"], state["year"] = moment["title"], moment["year"]
            note = region.get_by_role("button", name=re.compile(re.escape(about[0]["text"])))
            self.expect(note).to_contain_text("Add your version")
            return f"{len(questions)} question(s) listed under 'Someone asked you'"

        if not self.check("prompts: the questions waiting for the person are listed", p, listed):
            return

        def pick() -> str:
            q = state["q"]
            note = self.prompts(page).get_by_role("button", name=re.compile(re.escape(q["text"])))
            self.story(page).fill("Before I picked.")
            note.click()
            self.expect(note).to_have_attribute("aria-pressed", "true")
            self.expect(note).to_contain_text("Adding your version")
            self.expect(self.heading(page, "Add your telling")).to_be_visible()
            banner = page.get_by_text(re.compile(r"adding your version of")).filter(visible=True)
            self.expect(banner).to_contain_text(state["title"])
            self.expect(banner).to_contain_text(str(state["year"]))
            self.expect(page.get_by_text(q["text"], exact=True).filter(visible=True).first).to_be_visible()
            self.expect(self.year(page)).to_have_count(0)
            self.expect(self.save(page, "Save my telling")).to_be_visible()
            self.expect(self.story(page)).to_have_value("Before I picked.")
            self.expect(page).to_have_url(re.compile(rf"[?&]questionId={re.escape(quote(q['id'], safe=''))}(&|$)"))
            return f"'{q['text'][:50]}…' opens the telling of '{state['title']}' ({state['year']}); URL {self.path_of(page)}"

        if not self.check("prompts: picking a moment's question opens the second telling", p, pick):
            return

        def reload() -> str:
            q = state["q"]
            self.story(page).fill("I remember it differently.")
            page.wait_for_timeout(200)
            page.reload()
            self.wait_form(page, "Add your telling")
            note = self.prompts(page).get_by_role("button", name=re.compile(re.escape(q["text"])))
            self.expect(note).to_have_attribute("aria-pressed", "true")
            banner = page.get_by_text(re.compile(r"adding your version of")).filter(visible=True)
            self.expect(banner).to_contain_text(state["title"])
            self.expect(self.story(page)).to_have_value("I remember it differently.")
            self.expect(page).to_have_url(re.compile(rf"[?&]questionId={re.escape(quote(q['id'], safe=''))}(&|$)"))
            # Letting the question go drops it from the URL and keeps the story.
            note.click()
            self.expect(self.heading(page, "Add a memory")).to_be_visible()
            self.expect(self.story(page)).to_have_value("I remember it differently.")
            self.expect(page).not_to_have_url(re.compile(r"questionId="))
            return "after a reload: still the second telling, the question picked, the story kept; " \
                   "letting it go drops ?questionId="

        self.check("prompts: a reload keeps the picked question and the story", p, reload)

    # ----- 9. /add?memoryId=

    def memory_id_checks(self) -> None:
        asked_who, asked_moment = ("ana", MOCK_MOMENT) if self.mock else ("david", REAL_ASKED_MOMENT)
        p = self.sign_in("memory-id", who=asked_who,
                         target=f"/add?family={{fid}}&memoryId={quote(asked_moment, safe='')}")
        page = p.page

        def run() -> str:
            questions = self.api_call(p, "GET", f"/families/{quote(p.fid, safe='')}/questions")["questions"]
            q = next((q for q in questions if q.get("memoryId") == asked_moment), None)
            moment = self.api_call(p, "GET", f"/families/{quote(p.fid, safe='')}/memories/"
                                             f"{quote(asked_moment, safe='')}")["memory"]
            banner = page.get_by_text(re.compile(r"adding your version of")).filter(visible=True)
            self.expect(banner).to_contain_text(moment["title"])
            self.expect(self.prompts(page)).to_have_count(0)
            self.expect(self.year(page)).to_have_count(0)
            self.expect(self.who_group(page)).to_have_count(0)
            if q:
                self.expect(page.get_by_text("You were asked", exact=True).filter(visible=True)).to_be_visible()
                self.expect(page.get_by_text(q["text"], exact=True).filter(visible=True)).to_be_visible()
            told = p.person_id in [t.get("authorId") for t in moment.get("tellings", [])]
            note = page.get_by_text(ALREADY_TOLD, exact=False).filter(visible=True)
            self.expect(note).to_have_count(1 if told else 0)
            self.expect(self.save(page, "Save my telling")).to_be_visible()
            # The story typed here survives a reload too.
            self.story(page).fill("My side of it.")
            page.wait_for_timeout(200)
            page.reload()
            self.wait_form(page, "Add your telling")
            self.expect(self.story(page)).to_have_value("My side of it.")
            return (f"'{moment['title']}' ({moment['year']})" + (f", asked '{q['text'][:40]}…'" if q else "")
                    + ("; told before, and says so" if told else "")
                    + "; no question notes, year or people; the story survives a reload")

        self.check("?memoryId= opens the second telling directly", p, run)

        told = MOCK_MOMENT if self.mock else REAL_TOLD_MOMENT
        p2 = self.sign_in("memory-id-told", who="david",
                          target=f"/add?family={{fid}}&memoryId={quote(told, safe='')}")

        def already() -> str:
            moment = self.api_call(p2, "GET", f"/families/{quote(p2.fid, safe='')}/memories/{quote(told, safe='')}")
            authors = [t.get("authorId") for t in moment["memory"].get("tellings", [])]
            if p2.person_id not in authors:
                raise AssertionError(f"the seed changed: {p2.person_id} has not told {told} ({authors})")
            note = p2.page.get_by_text(ALREADY_TOLD, exact=False).filter(visible=True)
            self.expect(note).to_be_visible()
            return f"'{note.inner_text().strip()}'"

        self.check("?memoryId= of a moment told before says so", p2, already)

    # ----- 10. the add-relative round trip

    def add_relative_checks(self) -> None:
        p = self.sign_in("add-relative", target="/add?family={fid}")
        page = p.page
        new_name = f"Aunt Ines {self.run_tag}{self.view[0]}"

        def run() -> str:
            self.story(page).fill(STORY)
            self.year(page).fill(YEAR)
            first = self.tick_someone(page)
            link = page.get_by_role("link", name="Add them to the family")
            fid = quote(p.fid, safe="")
            expected = f"/families/{fid}/people/new?next={quote(f'/add?family={fid}', safe='')}"
            self.expect(link).to_have_attribute("href", expected)
            link.click()
            self.expect(page).to_have_url(f"{self.web}{expected}")
            self.expect(self.heading(page, "Add a relative")).to_be_visible()
            self.snap(p, "add-relative-page")
            page.get_by_label("Their name").filter(visible=True).fill(new_name)
            page.get_by_role("button", name="Add to the tree", exact=True).click()
            self.expect(page).to_have_url(re.compile(rf"/add\?family={re.escape(fid)}(&|$)"))
            self.wait_form(page)
            self.expect(self.who_group(page).get_by_role("checkbox", name=new_name, exact=True)).to_be_checked()
            if first:
                self.expect(self.who_group(page).get_by_role("checkbox", name=first, exact=True)).to_be_checked()
            self.expect(self.story(page)).to_have_value(STORY)
            self.expect(self.year(page)).to_have_value(YEAR)
            self.expect(page).not_to_have_url(re.compile(r"added="))
            return f"-> {expected} -> back at {self.path_of(page)}; '{new_name}' and '{first}' ticked; story and year kept"

        self.check("add-relative round trip: the new person ticked, the draft kept", p, run)

    # ----- 11. a picked note says so

    def picked_label_checks(self) -> None:
        who = MOCK_PLAIN_ASKED if self.mock else REAL_PLAIN_ASKED
        p = self.sign_in("picked-label", who=who, target="/add?family={fid}")
        page = p.page

        def run() -> str:
            questions = self.api_call(p, "GET", f"/families/{quote(p.fid, safe='')}/questions")["questions"]
            q = next((q for q in questions if not q.get("memoryId")), None)
            if not q:
                raise Seam("api" if not self.mock else "pages",
                           f"{who} has no open question that is not about a moment: {questions}")
            region = self.prompts(page)
            note = region.get_by_role("button", name=re.compile(re.escape(q["text"])))
            if not note.count():
                more = region.get_by_role("button", name=re.compile(r"^More questions"))
                if more.count():
                    more.click()
            self.expect(note).to_have_attribute("aria-pressed", "false")
            self.expect(note).to_contain_text("Answer this")
            self.expect(note).not_to_contain_text("Answering this")
            self.expect(note).to_have_accessible_name(re.compile(r"Answer this\s*$"))
            note.click()
            self.expect(note).to_have_attribute("aria-pressed", "true")
            self.expect(note).to_contain_text("Answering this")
            # Chromium may put a space before the visually hidden part; speech is the same.
            self.expect(note).to_have_accessible_name(re.compile(r"Answering this\s*,\s*press again to let it go\s*$"))
            hidden = note.get_by_text(PICKED_SUFFIX, exact=True)
            box = hidden.bounding_box()
            if box is None or box["width"] > 1 or box["height"] > 1:
                raise AssertionError(f"'{PICKED_SUFFIX}' is meant for screen readers only, but takes {box}")
            toggle = page.get_by_role("button", name=re.compile(r"Answering a question\?"))
            self.expect(toggle).to_have_count(0)
            self.snap(p, "picked-label-answering")
            note.click()
            self.expect(note).to_have_attribute("aria-pressed", "false")
            self.expect(note).to_contain_text("Answer this")
            self.expect(note).not_to_contain_text("Answering this")
            self.expect(note).to_have_accessible_name(re.compile(r"Answer this\s*$"))
            return (f"'{q['text'][:40]}…': 'Answer this' -> 'Answering this' (+ hidden '{PICKED_SUFFIX}', "
                    "no typed-question toggle) -> 'Answer this' again")

        self.check("prompts: a picked note reads 'Answering this', and lets go back to 'Answer this'", p, run)

    # ----- 12. a run waiting on the person

    def waiting_run_checks(self) -> None:
        p = self.sign_in("waiting-run", target="/add?family={fid}")
        page = p.page
        fid = quote(p.fid, safe="")
        state: dict[str, str] = {}

        def notice() -> Any:
            return page.locator("main [role=status]").filter(has_text=re.compile(r"^Your memory")).filter(visible=True)

        def leave_and_come_back() -> str:
            story = f"{WAIT_STORY} ({self.run_tag}{self.view[0]})"
            self.story(page).fill(story)
            self.year(page).fill("1988")
            self.save(page).click()
            self.expect(page).to_have_url(re.compile(r"[?&]run="), timeout=EXPECT_MS)
            run_id = query_of(page.url).get("run", "")
            state["run"] = run_id
            answer = page.get_by_role("textbox", name="Your answer")
            self.expect(answer).to_be_visible(timeout=RUN_TIMEOUT_MS)
            back = page.get_by_role("link", name="Family tree", exact=True).filter(visible=True).first
            self.expect(back).to_be_visible()
            self.expect(back).to_have_attribute("href", "/")
            back.click()
            self.expect(page).to_have_url(re.compile(rf"^{re.escape(self.web)}/?(\?.*)?$"))
            page.goto(f"{self.web}/add?family={fid}")
            self.wait_form(page)
            note = notice()
            self.expect(note).to_have_count(1)
            self.expect(note).to_contain_text(RUN_WAITING)
            self.expect(note).to_contain_text("‘The porch swing creaked every spring…’")
            link = note.get_by_role("link", name="Go back to it")
            self.expect(link).to_have_attribute("href", f"/add?family={fid}&run={quote(run_id, safe='')}")
            # The note does not block the form.
            self.expect(self.save(page)).to_be_enabled()
            self.expect(self.story(page)).to_be_editable()
            self.snap(p, "waiting-notice")
            text = note.inner_text().strip()
            link.click()
            self.expect(page).to_have_url(re.compile(rf"[?&]run={re.escape(quote(run_id, safe=''))}(&|$)"))
            self.expect(self.heading(page, "Your memory")).to_be_visible()
            self.expect(page.get_by_role("textbox", name="Your answer")).to_be_visible(timeout=RUN_TIMEOUT_MS)
            self.snap(p, "waiting-notice-back-to-follow-up")
            return f"paused on the follow-up, left by '← Family tree'; /add says '{text}'; 'Go back to it' -> the follow-up"

        if not self.check("open run: left waiting, /add says so and 'Go back to it' brings the follow-up back",
                          p, leave_and_come_back, shoot=False):
            return

        def finish_then_gone() -> str:
            follow_up = page.get_by_role("region", name="One quick question")
            follow_up.get_by_label("Your answer").fill("Just the two of us and the dog.")
            follow_up.get_by_role("button", name="Send answer").click()
            review = page.get_by_role("region", name="The things in your story")
            done = page.get_by_role("heading", name="Your memory is stitched in")
            self.expect(review.or_(done).first).to_be_visible(timeout=RUN_TIMEOUT_MS)
            reviewed = 0
            if review.count() and review.is_visible():
                # While the prop review is up the note would say it waits too: check it on /add.
                groups = review.get_by_role("group", name=re.compile(r"^What to do with"))
                for i in range(groups.count()):
                    group = groups.nth(i)
                    box = group.get_by_label("Keep", exact=True).filter(visible=True)
                    if not box.is_checked():
                        group.get_by_text("Keep", exact=True).filter(visible=True).click()
                        self.expect(box).to_be_checked()
                    reviewed += 1
                review.get_by_role("button", name="Looks good, keep going").click()
            self.expect(done).to_be_visible(timeout=RUN_TIMEOUT_MS)
            page.goto(f"{self.web}/add?family={fid}")
            self.wait_form(page)
            page.wait_for_timeout(300)
            self.expect(notice()).to_have_count(0)
            return f"follow-up answered, {reviewed} prop(s) kept, stitched in; /add shows no note afterwards"

        self.check("open run: once it is stitched in, /add has no note", p, finish_then_gone)


# ----------------------------------------------------------------------------- a moment in another family


def cross_family_checks(stack: Stack, browser: Any, seams: SeamLog) -> None:
    """David in two families, the other one remembered, opens /add?memoryId= with no ?family=.

    Run after every other check of the mode: against the shared offline API it leaves David
    in a second family, which would change the pages the other checks expect.
    """
    for view in VIEWPORTS:
        sweep = AddSweep(stack, browser, seams, view)
        try:
            _cross_family_check(sweep)
        finally:
            sweep.close_all()


def _cross_family_check(sweep: AddSweep) -> None:
    moment_id = MOCK_MOMENT if sweep.mock else REAL_ASKED_MOMENT
    p = sweep.sign_in("cross-family", who="david", target=None)
    page = p.page

    def run() -> str:
        home_fid = p.fid
        me = sweep.api_call(p, "GET", "/me")
        families = me.get("families") or []
        home = next((f for f in families if f.get("id") == home_fid), None)
        if home is None:
            raise AssertionError(f"David's family {home_fid} is not in /me: {families}")
        other = next((f for f in families if f.get("id") != home_fid), None)
        if other is None:
            made = sweep.api_call(p, "POST", "/families",
                                  {"name": f"The Elsewheres {sweep.run_tag}", "person": {"name": "David"}})
            other = made["family"]
        moment = sweep.api_call(p, "GET", f"/families/{quote(home_fid, safe='')}/memories/"
                                          f"{quote(moment_id, safe='')}")["memory"]
        # The other family is the one remembered, so it is asked first (and 404s).
        p.ctx.add_init_script(
            "try { if (!sessionStorage.getItem('sweep.remembered')) {"
            f" localStorage.setItem('stitch.familyId', {json.dumps(other['id'])});"
            " sessionStorage.setItem('sweep.remembered', '1'); } } catch (e) {}"
        )
        page.goto(f"{sweep.web}/add?memoryId={quote(moment_id, safe='')}")
        sweep.wait_form(page, "Add your telling")
        banner = page.get_by_text(re.compile(r"adding your version of")).filter(visible=True)
        sweep.expect(banner).to_contain_text(moment["title"])
        line = page.get_by_text("Adding to").filter(visible=True).first
        sweep.expect(line).to_contain_text(home["name"])
        switch = page.get_by_role("navigation", name="Add to another family").get_by_role("link", name=other["name"])
        sweep.expect(switch).to_have_attribute("href", f"/add?family={quote(other['id'], safe='')}")
        sweep.expect(sweep.save(page, "Save my telling")).to_be_visible()
        remembered = page.evaluate("() => { try { return localStorage.getItem('stitch.familyId'); } catch { return null; } }")
        if remembered != home_fid:
            raise AssertionError(f"the family holding the moment was not remembered: stitch.familyId={remembered!r}")
        sweep.snap(p, "cross-family-second-telling")
        return (f"remembered '{other['name']}', moment '{moment['title']}' found in '{home['name']}': "
                f"second telling opened there, now remembered; switch link {switch.get_attribute('href')}")

    # A 404 from the family asked first is how the lookup moves on.
    sweep.check("a moment in the other family: /add?memoryId= finds it and opens the second telling", p, run,
                allow=lambda problem: "status of 404" in problem and "/memories/" in problem)


# ----------------------------------------------------------------------------- real mode, end to end


def second_telling_end_to_end(stack: Stack, browser: Any, seams: SeamLog) -> None:
    """Ana answers her question about Sam's wedding from the prompt; her telling and track land."""
    from playwright.sync_api import expect

    sweep = AddSweep(stack, browser, seams, "desktop")
    p = sweep.person("e2e-ana")
    page = p.page
    state: dict[str, Any] = {}

    def answer() -> str:
        res = p.ctx.request.post(f"{stack.api_url}/auth/login", data={"email": REAL_ANA, "password": REAL_PASSWORD})
        if not res.ok:
            raise Seam("api", f"POST /auth/login as {REAL_ANA} answered {res.status}")
        sweep.learn_family(p)
        questions = sweep.api_call(p, "GET", f"/families/{p.fid}/questions")["questions"]
        q = next((q for q in questions if q.get("memoryId") == REAL_ANA_MOMENT), None)
        if not q:
            raise Seam("api", f"Ana has no open question about {REAL_ANA_MOMENT}: {questions}")
        before = sweep.api_call(p, "GET", f"/families/{p.fid}/memories/{REAL_ANA_MOMENT}")["memory"]
        state["version"] = before.get("version")
        page.goto(f"{stack.web_url}/add?family={p.fid}")
        sweep.wait_form(page)
        note = sweep.prompts(page).get_by_role("button", name=re.compile(re.escape(q["text"])))
        note.click()
        expect(sweep.heading(page, "Add your telling")).to_be_visible()
        expect(page).to_have_url(re.compile(r"[?&]questionId="))
        sweep.story(page).fill(
            "I hugged him so hard at the door of the hall that the flower on his lapel fell right off. "
            "Lily picked it up and pinned it back on, crooked, and he wore it like that all night."
        )
        sweep.snap(p, "e2e-ana-second-telling")
        sweep.save(page, "Save my telling").click()
        expect(page).to_have_url(re.compile(r"[?&]run="))
        expect(sweep.heading(page, "Your telling")).to_be_visible()
        expect(page.get_by_role("heading", name="Your telling is stitched in")).to_be_visible(timeout=RUN_TIMEOUT_MS)
        state["run"] = query_of(page.url).get("run", "")
        page.set_viewport_size({"width": VIEWPORTS["phone"][0], "height": VIEWPORTS["phone"][1]})
        sweep.snap(p, "e2e-ana-stitched-in-phone")
        page.set_viewport_size({"width": VIEWPORTS["desktop"][0], "height": VIEWPORTS["desktop"][1]})
        return f"Ana answered '{q['text'][:50]}…' from the prompt; run {state['run']} stitched in"

    ok = sweep.check("end to end: Ana answers her question from the prompt (second telling)", p, answer)

    def landed() -> str:
        if not ok:
            raise AssertionError("not run: needs the answer to go through first")
        memory = sweep.api_call(p, "GET", f"/families/{p.fid}/memories/{REAL_ANA_MOMENT}")["memory"]
        authors = [t.get("authorId") for t in memory.get("tellings", [])]
        if REAL_ANA_PERSON not in authors:
            raise Seam("pages", f"no telling by Ana ({REAL_ANA_PERSON}) on {REAL_ANA_MOMENT}; tellings by {authors}. "
                                "The telling probably went out without memoryId (a new memory instead).")
        narrations = (memory.get("timeline") or {}).get("narrations") or {}
        track = narrations.get(REAL_ANA_PERSON)
        if not track or not (track.get("captions") or track.get("lines") or track.get("words")):
            raise Seam("api", f"timeline.narrations has no track with lines for Ana ({REAL_ANA_PERSON}): "
                              f"keys {list(narrations)}")
        open_now = sweep.api_call(p, "GET", f"/families/{p.fid}/questions")["questions"]
        still = [q for q in open_now if q.get("memoryId") == REAL_ANA_MOMENT]
        if still:
            raise Seam("api", f"Ana's question about {REAL_ANA_MOMENT} is still open after she answered: {still}")
        return (f"tellings by {authors}; timeline.narrations keys {list(narrations)}; "
                f"version {state.get('version')} -> {memory.get('version')}; the question is closed")

    sweep.check("end to end: the memory has Ana's telling and narration track (API)", p, landed,
                owner="api", shoot=False)
    sweep.close_all()


# ----------------------------------------------------------------------------- main


def probe_mock_faults(stack: Stack, browser: Any) -> bool:
    """True when the mock backend can inject faults (window.__stitchMock.inject)."""
    p = Person(browser, "mock probe", VIEWPORTS["desktop"])
    try:
        p.page.goto(f"{stack.web_url}/add")
        p.page.wait_for_url(re.compile(r"/login"), timeout=TIMEOUT_MS * 2)
        return bool(p.page.evaluate("() => typeof window.__stitchMock?.inject === 'function'"))
    finally:
        p.close()


def warm_up(stack: Stack, browser: Any) -> str:
    """Visit each route once so `next dev` compiles it before the timed checks."""
    p = Person(browser, "warm-up", VIEWPORTS["desktop"])
    took = []
    try:
        for path in ("/login", "/add", "/signup", "/families/new", "/families/x/people/new"):
            start = time.monotonic()
            try:
                p.page.goto(f"{stack.web_url}{path}", timeout=180_000)
                p.page.wait_for_load_state("networkidle", timeout=60_000)
            except Exception as exc:  # noqa: BLE001 - warm-up only
                print(f"  (warm-up of {path}: {first_lines(str(exc), 1)})", flush=True)
            took.append(f"{path} {time.monotonic() - start:.0f}s")
    finally:
        p.close()
    return ", ".join(took)


def summarize(seams: SeamLog, out: Path) -> int:
    code = finish(seams, out)
    counts = seams.to_json()["counts"]
    total_shots = 0
    print(f"\nout dir:     {out}")
    print(f"seams:       {out / 'seams.json'}")
    for mode in ("mock", "real"):
        taken = shots_taken(out / mode)
        total_shots += len(taken)
        if taken:
            print(f"screenshots: {len(taken)} in {out / mode / 'shots'}")
    print(f"logs:        {out}/<mode>/web.log, {out}/real/api.log")
    skipped = sum(1 for e in seams.entries if e["ok"] and e["detail"].startswith("SKIPPED"))
    print(
        f"\n{counts['total']} checks: {counts['ok']} ok ({skipped} skipped), {counts['failed']} failed "
        f"(pages {counts['failedByOwner']['pages']}, api {counts['failedByOwner']['api']}, "
        f"ethan {counts['failedByOwner']['ethan']}); {total_shots} screenshots"
    )
    return code


def main(argv: list[str] | None = None) -> int:
    args = sweep_args(DESCRIPTION, argv)
    require_chromium()
    from playwright.sync_api import expect, sync_playwright

    expect.set_options(timeout=EXPECT_MS)
    out = sweep_out_dir(args.out, prefix="stitch-sweep-add-")
    print(f"out dir: {out}", flush=True)
    seams = SeamLog()
    for mock in sweep_modes(args.mode):
        mode = "mock" if mock else "real"
        api_port = None if mock else args.api_port
        if not (wait_port_free(args.web_port) and wait_port_free(api_port)):
            seams.record(f"[{mode}] start the stack", False,
                         f"port {args.web_port} or {api_port} is still busy after the previous stack", "pages")
            continue
        started = False
        try:
            with run_stack(mock=mock, web_port=args.web_port, api_port=api_port, run_step=0.1,
                           out_dir=out / mode) as stack:
                started = True
                print(f"\n##### {mode}: web {stack.web_url}" + (f"   API {stack.api_url}" if stack.api_url else ""),
                      flush=True)
                with sync_playwright() as pw:
                    browser = pw.chromium.launch(headless=not args.headed)
                    try:
                        seams.record(f"[{mode}] warm up the routes", True, warm_up(stack, browser))
                        if mock and not probe_mock_faults(stack, browser):
                            seams.record("[mock] mock backend fault injection", False,
                                         "window.__stitchMock.inject is missing: mock mode cannot slow or fail "
                                         "a request", "pages")
                        for view in VIEWPORTS:
                            AddSweep(stack, browser, seams, view).run()
                        if not mock:
                            second_telling_end_to_end(stack, browser, seams)
                        cross_family_checks(stack, browser, seams)
                    finally:
                        browser.close()
        except StackError as exc:
            seams.record(f"[{mode}] start the stack", False, str(exc), "pages")
        except Exception as exc:  # noqa: BLE001 - always end with seams.json and a summary
            where = f"[{mode}] run the sweep" if started else f"[{mode}] start the stack"
            tb = "".join(traceback.format_exception(exc)[-6:]).rstrip()
            seams.record(where, False, f"{type(exc).__name__}: {exc}\n{tb}", "pages")
    print()
    return summarize(seams, out)


if __name__ == "__main__":
    raise SystemExit(main())
