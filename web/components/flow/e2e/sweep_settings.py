"""Browser sweep of family settings and add-a-relative, in mock mode and against the offline API.

From api/ (it uses the API's uv environment, which has Playwright):

    cd api && uv run python ../web/components/flow/e2e/sweep_settings.py [--mode mock|real|both]
        [--headed] [--out DIR] [--api-port N] [--web-port N]

It walks every state of /families/<fid>/settings and /families/<fid>/people/new:

- signed out (to /login?next=, and back after logging in);
- loading: the static shell, the session and the family held, with the column the same width
  in every loading frame as on the loaded page;
- a family that is not yours or does not exist (the not-found card);
- a member's view (read only, no admin controls) and an admin's view (the members list);
- quiet controls pulled flush with -ml-3 (the 'More' links, Sign out and the member rows'
  actions on a phone, '+ Answering a question?' on /add): the text's left edge within 2px of
  the parent's content edge, or of the member's name in a grid row, at both widths;
- promote, then demote, a member; the last admin cannot step down; removing a member with
  its confirm (focus after each action);
- making, copying (clipboard permission granted) and turning off an invite link;
- stale actions after another tab changed things: a 404 (a member who left, a link already
  off), a 409 (the last admin) and a 403 (no longer an admin), in real mode with a second
  browser context;
- server errors (500 and a dropped connection) on loading and on every action: in mock mode
  through the mock backend's fault injection, in real mode with page.route;
- the welcome after starting a family (?welcome=1), the family switcher with two families,
  and the links home ('/') and onwards;
- add a relative: empty, validation (blank name, bad years, a parent born after them, the same
  person picked twice), the duplicate-name hint, a 500 and a dropped connection, success, and
  the round trip /add?family=<fid> -> people/new?next= -> /add?family=<fid>&added=<pid>.

Real mode starts `python -m seed.serve` (in-memory database) and makes its own family through
the API: an admin, four members who join from an invite, and a second family for the
switcher. Mock mode (NEXT_PUBLIC_API_MOCK=1) uses the mock's demo family "The Riveras"
(david@example.com, admin; ana@example.com, member; password stitch-demo); every browser
context starts from the seed.

Every check is one seam in seams.json with screenshots of its states at 1280x900 and 390x844
(<mode>/shots/NN-<mode>-<desktop|phone>-<state>.png) and an app-problems check (console
errors, uncaught errors, failed requests, 5xx answers, minus what the check caused on
purpose). Exit 1 when a 'pages' or 'api' seam failed, else 0. Without Chromium it prints the
SKIP line and exits 0.
"""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True  # keep __pycache__ out of web/

import json  # noqa: E402
import re  # noqa: E402
import shutil  # noqa: E402
import time  # noqa: E402
import traceback  # noqa: E402
import uuid  # noqa: E402
from collections.abc import Callable, Iterable  # noqa: E402
from dataclasses import dataclass  # noqa: E402
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
    expected_noise,
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

DESCRIPTION = "Sweep family settings and add-a-relative in Chromium, in mock mode and against the offline API."

DESKTOP = {"width": VIEWPORTS["desktop"][0], "height": VIEWPORTS["desktop"][1]}
PHONE = {"width": VIEWPORTS["phone"][0], "height": VIEWPORTS["phone"][1]}
TIMEOUT_MS = 30_000
NAV_TIMEOUT_MS = 120_000  # the first visit to a route waits for `next dev` to compile it
PASSWORD = "stitch-sweep-password"

# Real mode: the offline API (api/seed/README.md). Mock mode: components/flow/mock/demoSeed.ts.
MOCK_ADMIN = ("david@example.com", "stitch-demo")
MOCK_MEMBER = ("ana@example.com", "stitch-demo")
MOCK_ADMIN_NAME = "David"
MOCK_MEMBER_NAME = "Ana"
MOCK_FAMILY_NAME = "The Riveras"

# What the pages say (components/flow/*.ts, lib/api.ts).
SERVER_TROUBLE = "Something went wrong on our side. Please try again."
NETWORK_ERROR = "We can't reach Stitch right now. Check your connection and try again."
NOT_FOUND_TITLE = "We couldn’t find that family"
OPENING = "Opening your family’s pages…"
GATHERING = "Gathering the family…"
LOAD_ERROR_TITLE = "We couldn’t open this family’s settings"
GATE_ERROR_TITLE = "We couldn’t open this page"
TREE_ERROR_TITLE = "We couldn’t open the family tree"
LINK_COPIED = "Link copied"
LINK_OFF = "That link was already turned off."
NO_LONGER_ADMIN = "You’re not an admin of this family any more"
WELCOME_ADMIN = "Your family is started. Make an invite link to bring everyone else in."
STEP_DOWN_409 = "You’re the only admin, so you can’t step down yet. Make someone else an admin first."
NOT_ADMIN_ROLE = "Only admins can change who is an admin, and you’re not an admin of this family any more."
TURN_OFF_Q = "Turn it off?"
NAME_EMPTY = "Enter their name."
YEAR_FUTURE = "That year hasn't happened yet."
DIED_BEFORE = "The year they died can’t be before the year they were born."
WHOS_HERE = "Who’s here"

# Flow pages own these routes; anything else on the web app is Ethan's (/, people, memories...).
FLOW_PATHS = re.compile(r"^/(join|login|signup|add|families)(/|$)")

FAILED_RESOURCE_STATUS = re.compile(r"Failed to load resource: the server responded with a status of (\d{3})")
FAILED_RESOURCE_NET = re.compile(r"Failed to load resource: net::ERR_FAILED")

# The column of the flow frame: <main> > nav ("Stitch") + div. Hidden Activity trees
# (cacheComponents keeps visited pages mounted, display:none) have no client rects.
COLUMN_JS = """() => {
  const mains = [...document.querySelectorAll('main')].filter(m => m.getClientRects().length > 0);
  const col = mains.length ? mains[0].querySelector(':scope > nav + div') : null;
  return col ? Math.round(col.getBoundingClientRect().width * 10) / 10 : -1;
}"""

# Quiet controls pulled flush with a -ml-3 (on the control or on its wrapper) must line their
# text up with what they sit under: the parent's content edge, or, in a grid row, the text of
# the sibling before them (a member's name). Only pulls in effect at this width count
# (`sm:ml-0` cancels one from 640px up). The text's left is a Range over its first visible
# text node (not .sr-only, not visibility:hidden), first line box. Returns one entry per
# control that starts a line inside a pulled element.
FLUSH_JS = """() => {
  const shown = el => el && el.getClientRects().length > 0 && getComputedStyle(el).visibility !== 'hidden';
  const round = n => Math.round(n * 10) / 10;
  const textLeft = root => {
    const walk = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, { acceptNode: n =>
      n.textContent.trim() && shown(n.parentElement) && !n.parentElement.closest('.sr-only')
        ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_SKIP });
    const node = walk.nextNode();
    if (!node) return null;
    const text = node.textContent;
    const range = document.createRange();
    range.setStart(node, text.length - text.trimStart().length);
    range.setEnd(node, text.trimEnd().length);
    const rects = range.getClientRects();
    return { left: rects.length ? rects[0].left : range.getBoundingClientRect().left, text: text.trim() };
  };
  const contentLeft = el => {
    const s = getComputedStyle(el);
    return el.getBoundingClientRect().left + parseFloat(s.borderLeftWidth) + parseFloat(s.paddingLeft);
  };
  const label = el => (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim().slice(0, 60);
  const out = [];
  const pulled = [...document.querySelectorAll('main [class*="-ml-3"]')]
    .filter(el => shown(el) && parseFloat(getComputedStyle(el).marginLeft) < -0.5);
  for (const el of pulled) {
    const parent = el.parentElement;
    let ref = null, refWhat = '';
    if (parent && getComputedStyle(parent).display.includes('grid')) {
      let sib = el.previousElementSibling;
      while (sib && !(shown(sib) && textLeft(sib))) sib = sib.previousElementSibling;
      if (sib) { ref = textLeft(sib).left; refWhat = `text of '${label(sib)}'`; }
    }
    if (ref === null && parent) { ref = contentLeft(parent); refWhat = `content edge of <${parent.localName}>`; }
    const controls = el.matches('button, a') ? [el]
      : [...el.querySelectorAll('button, a')].filter(c => shown(c) && Math.abs(c.getBoundingClientRect().left - contentLeft(el)) <= 1);
    for (const c of controls) {
      const t = textLeft(c);
      if (!t || ref === null) continue;
      out.push({ control: label(c), tag: c.localName, textLeft: round(t.left), ref: round(ref), refWhat,
                 delta: round(t.left - ref), boxLeft: round(c.getBoundingClientRect().left),
                 boxWidth: round(c.getBoundingClientRect().width) });
    }
  }
  return out;
}"""
FLUSH_TOLERANCE_PX = 2.0

FOCUS_JS = """() => {
  const a = document.activeElement;
  if (!a || a === document.body) return 'body';
  const text = (a.getAttribute('aria-label') || a.innerText || a.value || '').replace(/\\s+/g, ' ').trim();
  return `${a.tagName.toLowerCase()}${a.getAttribute('role') ? '[role=' + a.getAttribute('role') + ']' : ''}: ${text.slice(0, 90)}`;
}"""


# --------------------------------------------------------------------------- pure helpers


def owner_for_url(url: str) -> str:
    path = urlsplit(url).path or "/"
    return "pages" if FLOW_PATHS.search(path) else "ethan"


@dataclass(frozen=True)
class Allow:
    """What a step expects the browser to report, so it is not a problem."""

    statuses: tuple[int, ...] = ()
    """4xx answers the step asks for (Chromium logs each as 'Failed to load resource')."""
    faults: bool = False
    """The step injects 5xx answers and dropped connections itself."""


NO_JS_LABEL = ":no-js"


def is_expected(event: dict[str, str], problem: str, allow: Allow) -> bool:
    """True when a PageWatch event is what the step set out to cause, or harness noise
    that is not a 4xx the step did not ask for."""
    kind, text = event.get("kind", ""), event.get("text", "")
    if kind == "requestfailed" and event.get("label", "").endswith(NO_JS_LABEL) and text.endswith(" csp"):
        return True  # a script the browser did not fetch because scripts are off
    if kind == "console.error":
        m = FAILED_RESOURCE_STATUS.search(text)
        if m:
            status = int(m.group(1))
            if status in allow.statuses:
                return True
            return allow.faults and status >= 500
        return allow.faults and bool(FAILED_RESOURCE_NET.search(text))
    if allow.faults and kind == "http":
        return True
    if allow.faults and kind == "requestfailed" and "ERR_FAILED" in text:
        return True
    # An aborted RSC prefetch (expected_noise); its 4xx rule is handled above, stricter.
    return kind == "requestfailed" and expected_noise(problem)


def first_lines(text: str, n: int = 6) -> str:
    lines = [ln for ln in str(text).splitlines() if ln.strip()]
    out = "\n".join(lines[:n])
    return out if len(lines) <= n else out + "\n..."


def cors_headers(origin: str, content_type: str = "application/json") -> dict[str, str]:
    return {
        "Access-Control-Allow-Origin": origin,
        "Access-Control-Allow-Credentials": "true",
        "Content-Type": content_type,
    }


def prepare_out(out: Path) -> None:
    """Move an earlier run's results aside (never delete them)."""
    out.mkdir(parents=True, exist_ok=True)
    leftovers = [p for p in (out / "seams.json", out / "mock", out / "real") if p.exists()]
    if not leftovers:
        return
    keep = out / f"previous-{time.strftime('%Y%m%d-%H%M%S')}"
    keep.mkdir()
    for p in leftovers:
        shutil.move(str(p), str(keep / p.name))


def mock_family_id() -> str:
    """The mock demo family's id: the hymn books sample's familyId (demoSeed.ts)."""
    try:
        sample = json.loads((WEB_DIR / "public/samples/hymn_books.memory.json").read_text())
        return sample["memory"]["familyId"] or "sample_family"
    except (OSError, ValueError, KeyError, TypeError):
        return "sample_family"


class Seam(Exception):
    """A failure whose fix belongs to someone other than the step's owner."""

    def __init__(self, owner: str, message: str) -> None:
        super().__init__(message)
        self.owner = owner


class Skip(Exception):
    """The check cannot run in this mode; recorded as an ok seam with the reason."""


# --------------------------------------------------------------------------- the sweep


class SettingsSweep:
    def __init__(self, stack: Stack, browser: Any, seams: SeamLog, mode: str) -> None:
        from playwright.sync_api import expect

        expect.set_options(timeout=TIMEOUT_MS)
        self.expect = expect
        self.stack = stack
        self.browser = browser
        self.seams = seams
        self.mode = mode
        self.real = mode == "real"
        self.web = stack.web_url
        self.api = stack.api_url or ""
        self.watch = PageWatch()
        self.contexts: list[Any] = []
        self.layout: list[str] = []
        self.done: set[str] = set()
        self.tag = uuid.uuid4().hex[:5]
        self.current: Any = None
        self.faults_supported = False
        # The family under test and its people (filled in by setup).
        self.fid = mock_family_id() if not self.real else ""
        self.family = MOCK_FAMILY_NAME
        self.admin = MOCK_ADMIN
        self.admin_name = MOCK_ADMIN_NAME
        self.member = MOCK_MEMBER
        self.member_name = MOCK_MEMBER_NAME
        self.people: dict[str, tuple[str, str]] = {}  # key -> (email, display name) in real mode
        self.user_ids: dict[str, str] = {}
        self.admin_ctx: Any = None  # real mode: the admin's API context for out-of-band changes
        self.loaded_width: dict[str, float] = {}
        self.second_fid = ""
        self.welcome_ctx: Any = None  # the welcome step's context, carried on to the switcher
        self.welcome_page: Any = None

    # ------------------------------------------------------------------ plumbing

    def new_page(self, label: str, *, js: bool = True, clipboard: bool = False) -> tuple[Any, Any]:
        ctx = self.browser.new_context(
            viewport=DESKTOP,
            java_script_enabled=js,
            permissions=["clipboard-read", "clipboard-write"] if clipboard else [],
        )
        ctx.set_default_timeout(TIMEOUT_MS)
        ctx.set_default_navigation_timeout(NAV_TIMEOUT_MS)
        page = ctx.new_page()
        self.watch.attach(page, f"{self.mode}:{label}{'' if js else NO_JS_LABEL}")
        self.contexts.append(ctx)
        self.current = page
        return ctx, page

    def extra_page(self, ctx: Any, label: str) -> Any:
        page = ctx.new_page()
        self.watch.attach(page, f"{self.mode}:{label}")
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
        allow: Allow = Allow(),
        owner: str = "pages",
        needs: Iterable[str] = (),
    ) -> None:
        label = f"[{self.mode}] {name}"
        missing = [n for n in needs if n not in self.done]
        if missing:
            self.seams.record(label, False, f"not run: needs step(s) {', '.join(missing)} first", owner)
            return
        print(f"\n== {label}", flush=True)
        mark = len(self.watch.problems)
        layout_mark = len(self.layout)
        ctx_mark = len(self.contexts)
        self.current = None
        ok, result_owner = True, owner
        try:
            detail = fn() or ""
        except Skip as exc:
            detail = f"SKIP: {exc}"
        except Seam as exc:
            ok, result_owner, detail = False, exc.owner, first_lines(f"{exc}")
            self.failure_shots(key, ctx_mark)
        except Exception as exc:  # noqa: BLE001 - every failure becomes a seam
            ok, detail = False, first_lines(f"{type(exc).__name__}: {exc}")
            if not isinstance(exc, AssertionError):
                print(first_lines(traceback.format_exc(), 12), flush=True)
            self.failure_shots(key, ctx_mark)
        layout = self.layout[layout_mark:]
        if ok and layout:
            ok, detail = False, "; ".join(layout)
        self.seams.record(label, ok, detail, result_owner)
        if ok:
            self.done.add(key)
        self.app_problems(label, mark, allow)
        # Each step's contexts end with it, so pages do not pile up in the browser.
        for ctx in self.contexts[ctx_mark:]:
            if ctx is self.admin_ctx:
                continue
            try:
                ctx.close()
            except Exception:  # noqa: BLE001
                pass
        self.contexts[ctx_mark:] = [c for c in self.contexts[ctx_mark:] if c is self.admin_ctx]

    def failure_shots(self, key: str, ctx_mark: int) -> None:
        pages = [p for ctx in self.contexts[ctx_mark:] for p in ctx.pages]
        if self.current is not None and self.current not in pages:
            pages.append(self.current)
        for page in pages:
            try:
                shot(page, self.stack, f"{self.mode}-desktop-{key}-failed")
            except Exception:  # noqa: BLE001
                pass

    def app_problems(self, label: str, mark: int, allow: Allow) -> None:
        """The step's browser problems, minus what it caused on purpose: one seam per owner."""
        groups: dict[str, list[str]] = {}
        for problem, event in zip(self.watch.since(mark), self.watch.events_since(mark), strict=True):
            if is_expected(event, problem, allow):
                continue
            owner = "api" if event.get("kind") == "http" else owner_for_url(event.get("pageUrl", ""))
            groups.setdefault(owner, []).append(problem)
        for owner, problems in groups.items():
            self.seams.record(f"{label}: app problems", False, "\n".join(problems), owner)

    def snap(self, page: Any, name: str) -> None:
        """The state at desktop and at phone width; a page that scrolls sideways on a phone fails."""
        page.wait_for_timeout(150)  # let focus moves and transitions settle
        shot(page, self.stack, f"{self.mode}-desktop-{name}")
        page.set_viewport_size(PHONE)
        over: Any = 0
        try:
            page.wait_for_timeout(200)
            over = page.evaluate("() => document.documentElement.scrollWidth - window.innerWidth")
            shot(page, self.stack, f"{self.mode}-phone-{name}")
        finally:
            page.set_viewport_size(DESKTOP)
            page.wait_for_timeout(100)
        if isinstance(over, (int, float)) and over > 1:
            self.layout.append(f"{name}: the page scrolls sideways by {over}px at {PHONE['width']}px wide")

    def goto(self, page: Any, path: str) -> None:
        page.goto(f"{self.web}{path}")

    @staticmethod
    def vis(locator: Any) -> Any:
        return locator.filter(visible=True)

    def focus(self, page: Any) -> str:
        return str(page.evaluate(FOCUS_JS))

    def expect_focus(self, page: Any, pattern: str, what: str) -> str:
        """Wait (briefly) for focus to land on an element whose description matches `pattern`."""
        end = time.monotonic() + 3.0
        got = self.focus(page)
        while not re.search(pattern, got) and time.monotonic() < end:
            page.wait_for_timeout(100)
            got = self.focus(page)
        if not re.search(pattern, got):
            raise AssertionError(f"after {what}, focus is on '{got}', expected /{pattern}/")
        return got

    def column(self, page: Any) -> float:
        return float(page.evaluate(COLUMN_JS))

    def check_mark(self, page: Any) -> None:
        mark = page.get_by_role("navigation", name="Stitch").get_by_role("link", name="Stitch", exact=True)
        self.expect(mark).to_have_attribute("href", "/")

    def alert(self, page: Any, text: str) -> Any:
        return self.vis(page.get_by_role("alert").filter(has_text=text))

    def alert_texts(self, page: Any) -> list[str]:
        """The page's own visible alerts (not Next's route announcer, not hidden Activity trees)."""
        texts = page.get_by_role("alert").evaluate_all(
            "els => els.filter(e => { const h = e.getRootNode().host;"
            " return !(h && h.localName === 'next-route-announcer') && e.getClientRects().length > 0; })"
            ".map(e => e.innerText)"
        )
        return [t.strip() for t in texts if t and t.strip()]

    def expect_no_alert(self, page: Any, what: str) -> None:
        page.wait_for_timeout(150)
        left = self.alert_texts(page)
        if left:
            raise AssertionError(f"{what}: an alert is still showing: {left}")

    def settings_path(self, fid: str | None = None) -> str:
        return f"/families/{quote(fid or self.fid, safe='')}/settings"

    def relative_path(self, fid: str | None = None) -> str:
        return f"/families/{quote(fid or self.fid, safe='')}/people/new"

    # ------------------------------------------------------------------ API (real mode)

    def api_call(self, ctx: Any, method: str, path: str, data: Any = None, *, ok: Iterable[int] = ()) -> Any:
        req = ctx.request
        url = f"{self.api}{path}"
        if method == "GET":
            res = req.get(url)
        elif method == "POST":
            res = req.post(url, data=data if data is not None else {})
        elif method == "PATCH":
            res = req.patch(url, data=data)
        elif method == "DELETE":
            res = req.delete(url)
        else:
            raise ValueError(method)
        if not res.ok and res.status not in ok:
            raise Seam("api", f"{method} {path} answered {res.status}: {first_lines(res.text(), 3)}")
        if res.status == 204 or not res.body():
            return None
        try:
            return res.json()
        except Exception:  # noqa: BLE001
            return None

    def api_signup(self, ctx: Any, name: str) -> tuple[str, str]:
        email = f"{re.sub(r'[^a-z]+', '-', name.lower()).strip('-')}-{uuid.uuid4().hex[:6]}@example.com"
        res = self.api_call(ctx, "POST", "/auth/signup", {"email": email, "password": PASSWORD, "name": name})
        uid = ((res or {}).get("user") or {}).get("id") or ""
        if not uid:
            uid = (self.api_call(ctx, "GET", "/me") or {}).get("user", {}).get("id", "")
        return email, uid

    def api_login(self, ctx: Any, email: str, password: str = PASSWORD) -> None:
        self.api_call(ctx, "POST", "/auth/login", {"email": email, "password": password})

    def family_now(self) -> dict[str, Any]:
        return self.api_call(self.admin_ctx, "GET", f"/families/{self.fid}") or {}

    def role_of(self, key: str) -> str | None:
        uid = self.user_ids[key]
        for m in self.family_now().get("members") or []:
            if m.get("userId") == uid:
                return m.get("role")
        return None

    def set_role(self, ctx: Any, key: str, role: str) -> None:
        self.api_call(ctx, "PATCH", f"/families/{self.fid}/members/{self.user_ids[key]}", {"role": role})

    # ------------------------------------------------------------------ UI helpers

    def ui_login(self, page: Any, email: str, password: str) -> None:
        self.expect(page.get_by_role("heading", level=1, name="Welcome back")).to_be_visible()
        self.vis(page.get_by_label("Email")).fill(email)
        self.vis(page.get_by_label("Password", exact=True)).fill(password)
        page.get_by_role("button", name="Log in", exact=True).click()

    def sign_in(self, ctx: Any, page: Any, who: str) -> None:
        """Sign `who` ("admin", "member" or a real-mode key) into this context."""
        if self.real:
            email = self.people[who][0] if who in self.people else (self.admin if who == "admin" else self.member)[0]
            self.api_login(ctx, email)
            return
        email, password = self.admin if who == "admin" else self.member
        self.goto(page, f"/login?next={quote(self.settings_path(), safe='')}")
        self.ui_login(page, email, password)
        self.expect(page).to_have_url(re.compile(re.escape(self.settings_path()) + r"$"))

    def open_settings(self, who: str, label: str, *, clipboard: bool = False, fid: str | None = None) -> tuple[Any, Any]:
        """A context signed in as `who`, on the family's settings, loaded."""
        ctx, page = self.new_page(label, clipboard=clipboard)
        self.sign_in(ctx, page, who)
        self.goto(page, self.settings_path(fid))
        self.wait_loaded(page)
        return ctx, page

    def wait_loaded(self, page: Any) -> None:
        self.expect(self.members_list(page)).to_be_visible()

    def members_list(self, page: Any) -> Any:
        return page.get_by_role("list", name=WHOS_HERE)

    def row(self, page: Any, name: str) -> Any:
        return self.members_list(page).get_by_role("listitem").filter(has_text=name)

    def mock_inject(self, page: Any, fault: dict[str, Any]) -> None:
        if not self.faults_supported:
            raise Skip("the mock has no window.__stitchMock.inject on this branch; real mode covers it")
        page.evaluate("(f) => window.__stitchMock.inject(f)", fault)

    def fail_route(self, page: Any, path_re: str, method: str, *, status: int = 500, abort: bool = False,
                   times: int | None = 1) -> None:
        """Real mode: answer `method path_re` on the API with `status` (plain text, as uvicorn
        answers a crash), or drop the connection."""
        pattern = re.compile(rf"^{re.escape(self.api)}{path_re}(\?.*)?$")
        left = [times]

        def handler(route: Any) -> None:
            req = route.request
            if req.method != method or (left[0] is not None and left[0] <= 0):
                route.fallback()
                return
            if left[0] is not None:
                left[0] -= 1
            if abort:
                route.abort("failed")
            else:
                route.fulfill(status=status, headers=cors_headers(self.web, "text/plain; charset=utf-8"),
                              body="Internal Server Error")

        page.route(pattern, handler)

    def fail(self, page: Any, method: str, mock_path: str, real_re: str, *, network: bool, times: int | None = 1) -> None:
        """One failing answer for the next `method` request: a 500, or a dropped connection."""
        if self.real:
            self.fail_route(page, real_re, method, abort=network, times=times)
        else:
            fault: dict[str, Any] = {"method": method, "path": mock_path}
            fault.update({"network": True} if network else {"status": 500})
            if times:
                fault["times"] = times
            self.mock_inject(page, fault)

    def esc_fid(self) -> str:
        return re.escape(quote(self.fid, safe=""))

    # ------------------------------------------------------------------ the walk

    def run(self) -> None:
        try:
            self.step("warmup", "compile the routes the sweep visits", self.s_warmup)
            self.step("setup", "set up the family", self.s_setup, owner="api" if self.real else "pages")
            need = ("setup",)
            self.step("signed-out", "signed out: settings and add-a-relative go to /login?next= and back",
                      self.s_signed_out, needs=need)
            self.step("loading", "loading: every frame has the loaded page's column width", self.s_loading, needs=need)
            self.step("not-found", "not a member, or an unknown family: the not-found card",
                      self.s_not_found, allow=Allow(statuses=(404,)), needs=need)
            self.step("member", "a member's view: read only, no admin controls", self.s_member_view, needs=need)
            self.step("admin", "an admin's view: the members list, roles, the invite card", self.s_admin_view,
                      needs=need)
            self.step("flush", "quiet controls pulled flush with -ml-3 line their text up (settings, /add)",
                      self.s_flush, needs=need)
            self.step("roles", "promote, then demote, a member (confirm, announcement, focus)", self.s_roles,
                      needs=need)
            self.step("last-admin", "the last admin cannot step down; with two admins they can (cancel)",
                      self.s_last_admin, needs=need)
            self.step("remove", "remove a member: confirm, cancel, remove, focus", self.s_remove, needs=need)
            # 404: the old link, opened after it was turned off (real mode).
            self.step("invite", "invite link: copy (clipboard), turn off, make a new one", self.s_invite,
                      allow=Allow(statuses=(404,)), needs=need)
            stale = Allow(statuses=(403, 404, 409))
            self.step("stale-remove", "stale: removing someone another tab removed (404)", self.s_stale_remove,
                      allow=stale, needs=need)
            self.step("stale-role", "stale: promoting someone another tab removed (404)", self.s_stale_role,
                      allow=stale, needs=need)
            self.step("stale-revoke", "stale: turning off a link another tab turned off (idempotent 204)",
                      self.s_stale_revoke, allow=stale, needs=need)
            self.step("stale-last-admin", "stale: stepping down after the other admin stepped down (409)",
                      self.s_stale_last_admin, allow=stale, needs=need)
            self.step("stale-not-admin", "stale: an admin action after losing admin elsewhere (403)",
                      self.s_stale_not_admin, allow=stale, needs=need)
            fault = Allow(faults=True)
            self.step("err-load", "server errors: loading the family (500, dropped), then Try again",
                      self.s_err_load, allow=fault, needs=need)
            self.step("err-me", "server error: GET /me answers 500, the gate's sheet, then Try again",
                      self.s_err_me, allow=fault, needs=need)
            self.step("err-invite", "server errors: making and turning off a link (500, dropped)",
                      self.s_err_invite, allow=fault, needs=need)
            self.step("err-role", "server errors: changing a role (500, dropped)", self.s_err_role, allow=fault,
                      needs=need)
            self.step("err-remove", "server errors: removing a member (500, dropped)", self.s_err_remove,
                      allow=fault, needs=need)
            self.step("welcome", "welcome: starting a family lands on its settings with ?welcome=1",
                      self.s_welcome, needs=need)
            self.step("switcher", "the family switcher with two families", self.s_switcher, needs=("welcome",))
            self.step("links", "links: back to the family tree ('/'), the Stitch mark, the footer",
                      self.s_links, needs=need)
            self.step("rel-empty", "add a relative: the empty form", self.s_rel_empty, needs=need)
            self.step("rel-validation", "add a relative: validation and the duplicate-name hint",
                      self.s_rel_validation, needs=need)
            self.step("rel-errors", "add a relative: a 500 and a dropped connection keep what was typed",
                      self.s_rel_errors, allow=fault, needs=need)
            self.step("rel-success", "add a relative: success, the done card and its links", self.s_rel_success,
                      needs=need)
            self.step("rel-roundtrip", "add a relative from /add: ?next= round trip to /add?...&added=<pid>",
                      self.s_rel_roundtrip, needs=need)
        finally:
            self.close_all()

    # ------------------------------------------------------------------ steps

    def s_warmup(self) -> str:
        """Visit each route once so `next dev` compiles it before the timed checks."""
        ctx, page = self.new_page("warmup")
        mark = len(self.watch.problems)
        took = []
        for path in ("/families/warmup/settings", "/families/warmup/people/new", "/login", "/families/new",
                     "/add", "/"):
            start = time.monotonic()
            try:
                self.goto(page, path)
                page.wait_for_load_state("networkidle", timeout=60_000)
            except Exception as exc:  # noqa: BLE001 - warm-up only
                print(f"  (warm-up of {path}: {first_lines(str(exc), 1)})", flush=True)
            took.append(f"{path} {time.monotonic() - start:.0f}s")
        # Nothing seen here is a finding: every check below visits these pages again.
        del self.watch.problems[mark:]
        del self.watch.events[mark:]
        return ", ".join(took)

    def s_setup(self) -> str:
        if not self.real:
            ctx, page = self.new_page("setup")
            self.sign_in(ctx, page, "admin")
            self.wait_loaded(page)
            self.faults_supported = bool(page.evaluate("() => typeof window.__stitchMock?.inject === 'function'"))
            return (f"mock family {self.fid} '{self.family}'; fault injection "
                    f"{'available' if self.faults_supported else 'missing'}")
        ctx, page = self.new_page("admin-api")
        self.contexts.remove(ctx)  # kept for the whole run: out-of-band changes as the admin
        self.admin_ctx = ctx
        admin_name = f"Ada Sweep {self.tag}"
        email, uid = self.api_signup(ctx, admin_name)
        self.people["admin"] = (email, admin_name)
        self.user_ids["admin"] = uid
        self.admin = (email, PASSWORD)
        self.admin_name = admin_name
        self.family = f"The Sweeps {self.tag}"
        res = self.api_call(ctx, "POST", "/families",
                            {"name": self.family, "person": {"name": admin_name, "born": 1970}})
        self.fid = res["family"]["id"]
        code = self.api_call(ctx, "POST", f"/families/{self.fid}/invites")["code"]
        joined = []
        for key, first, born in (("member", "Bea", 1994), ("cal", "Cal", 1996), ("dov", "Dov", 1998),
                                 ("eve", "Eve", 2000)):
            name = f"{first} Sweep {self.tag}"
            jctx = self.browser.new_context()
            try:
                mail, juid = self.api_signup(jctx, name)
                self.api_call(jctx, "POST", f"/invites/{code}/join",
                              {"person": {"name": name, "born": born, "parentIds": [res['person']['id']]}})
            finally:
                jctx.close()
            self.people[key] = (mail, name)
            self.user_ids[key] = juid
            joined.append(name)
        self.member = (self.people["member"][0], PASSWORD)
        self.member_name = self.people["member"][1]
        return f"family {self.fid} '{self.family}', admin {admin_name}, members {', '.join(joined)}, invite {code}"

    def s_signed_out(self) -> str:
        expect = self.expect
        notes = []
        for path in (self.settings_path(), self.relative_path()):
            ctx, page = self.new_page("signed-out")
            self.goto(page, path)
            expect(page).to_have_url(re.compile(re.escape(f"/login?next={quote(path, safe='')}") + "$"))
            expect(page.get_by_role("heading", level=1, name="Welcome back")).to_be_visible()
            self.snap(page, f"signed-out-{'settings' if path.endswith('settings') else 'relative'}")
            notes.append(f"{path} -> {urlsplit(page.url).path}?{urlsplit(page.url).query}")
            if path.endswith("settings"):
                self.ui_login(page, *self.admin)
                expect(page).to_have_url(re.compile(re.escape(path) + "$"))
                self.wait_loaded(page)
                notes.append("logged in, back on settings")
        return "; ".join(notes)

    def s_loading(self) -> str:
        expect = self.expect
        widths: dict[str, float] = {}
        notes = []
        for route, path in (("settings", self.settings_path()), ("relative", self.relative_path())):
            # 1. The static shell (JS off): the Suspense fallback in the flow frame.
            ctx, page = self.new_page(f"no-js-{route}", js=False)
            self.goto(page, path)
            expect(page.get_by_role("status").filter(has_text=OPENING)).to_be_visible()
            self.check_mark(page)
            widths[f"{route}: static shell"] = self.column(page)
            self.snap(page, f"loading-{route}-static-shell")

            # 2. The session held, then 3. the family held, then 4. loaded.
            ctx, page = self.new_page(f"loading-{route}")
            self.sign_in(ctx, page, "admin")
            family_re = rf"/families/{self.esc_fid()}" + (r"/tree" if route == "relative" else "")
            if self.real:
                widths.update(self._held_real(page, path, route, family_re))
            else:
                widths.update(self._held_mock(ctx, path, route))
            loaded_check = (lambda p: self.wait_loaded(p)) if route == "settings" else (
                lambda p: expect(page.get_by_role("button", name="Add to the tree")).to_be_visible())
            loaded_check(page)
            widths[f"{route}: loaded"] = self.column(page)
            self.loaded_width[route] = widths[f"{route}: loaded"]
            self.snap(page, f"loading-{route}-loaded")
        bad = {k: v for k, v in widths.items() if abs(v - widths[f"{k.split(':')[0]}: loaded"]) > 0.5}
        listing = ", ".join(f"{k} {v:g}px" for k, v in widths.items())
        if bad:
            raise AssertionError(f"the column jumps while the page loads (desktop): {listing}")
        notes.append(f"desktop column widths: {listing}")
        return "; ".join(notes)

    def _held_real(self, page: Any, path: str, route: str, family_re: str) -> dict[str, float]:
        expect = self.expect
        out: dict[str, float] = {}
        for what, pattern, text in (
            ("session held", re.compile(rf"^{re.escape(self.api)}/me$"), OPENING),
            ("family held", re.compile(rf"^{re.escape(self.api)}{family_re}$"), GATHERING),
        ):
            held: list[Any] = []
            page.route(pattern, self._holder(held))
            self.goto(page, path)
            expect(self.vis(page.get_by_role("status").filter(has_text=text))).to_be_visible()
            page.wait_for_timeout(300)
            out[f"{route}: {what}"] = self.column(page)
            self.snap(page, f"loading-{route}-{what.replace(' ', '-')}")
            if not held:
                raise AssertionError(f"{what}: the page never asked for {pattern.pattern}")
            for r in held:
                r.continue_()
            page.unroute(pattern)
            # Let what the release set off finish before the next phase navigates again.
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(300)
        return out

    @staticmethod
    def _holder(held: list[Any]) -> Callable[[Any], None]:
        """A route handler that keeps GET requests waiting in `held` (one argument: Playwright
        passes the request too to a handler that takes two)."""

        def hold(route: Any) -> None:
            if route.request.method == "GET":
                held.append(route)
            else:
                route.fallback()

        return hold

    def _held_mock(self, ctx: Any, path: str, route: str) -> dict[str, float]:
        expect = self.expect
        if not self.faults_supported:
            raise Skip("the mock has no fault injection on this branch, so it cannot slow a request")
        out: dict[str, float] = {}
        family_path = "/families/{fid}" + ("/tree" if route == "relative" else "")
        for what, fpath, text in (("session held", "/me", OPENING), ("family held", family_path, GATHERING)):
            page = self.extra_page(ctx, f"loading-{route}-{what}")
            page.add_init_script(mock_faults_script([{"method": "GET", "path": fpath, "delayMs": 3000}]))
            self.goto(page, path)
            expect(self.vis(page.get_by_role("status").filter(has_text=text))).to_be_visible()
            out[f"{route}: {what}"] = self.column(page)
            self.snap(page, f"loading-{route}-{what.replace(' ', '-')}")
            page.close()
        self.current = ctx.pages[0]
        self.goto(ctx.pages[0], path)
        return out

    def not_found_card(self, page: Any, name: str) -> None:
        expect = self.expect
        expect(page.get_by_role("heading", level=1, name=NOT_FOUND_TITLE)).to_be_visible()
        self.check_mark(page)
        expect(page.get_by_role("link", name="Go home")).to_have_attribute("href", "/")
        width = self.column(page)
        want = self.loaded_width.get("settings")
        if want and abs(width - want) > 0.5:
            self.layout.append(f"{name}: the not-found card's column is {width:g}px, the page's {want:g}px")
        self.snap(page, name)

    def s_not_found(self) -> str:
        notes = []
        ctx, page = self.new_page("not-found")
        self.sign_in(ctx, page, "admin")
        self.goto(page, self.settings_path(f"no-such-family-{self.tag}"))
        self.not_found_card(page, "not-found-unknown")
        notes.append("unknown family id: 'We couldn’t find that family', 'Go home' -> /")
        self.goto(page, self.relative_path(f"no-such-family-{self.tag}"))
        self.not_found_card(page, "not-found-relative-unknown")
        notes.append("add-a-relative for an unknown family: the same card")
        if self.real:
            # Someone with a family of their own, opening this one's settings.
            octx, opage = self.new_page("outsider")
            self.api_signup(octx, f"Oli Outsider {self.tag}")
            self.api_call(octx, "POST", "/families",
                          {"name": f"Olis {self.tag}", "person": {"name": f"Oli Outsider {self.tag}"}})
            self.goto(opage, self.settings_path())
            self.not_found_card(opage, "not-found-not-a-member")
            notes.append("signed in but not a member: the same card (the page asks the server nothing)")
        return "; ".join(notes)

    def s_member_view(self) -> str:
        expect = self.expect
        ctx, page = self.open_settings("member", "member")
        expect(page.get_by_role("heading", level=1, name=self.family)).to_be_visible()
        for name in ("Make an invite link", "Copy link", "Turn off this link", "Step down as admin"):
            expect(page.get_by_role("button", name=name)).to_have_count(0)
        expect(self.members_list(page).get_by_role("button")).to_have_count(0)
        expect(page.get_by_label("Invite link")).to_have_count(0)
        note = self.vis(page.get_by_text(re.compile(r"Want someone else in the family\? Ask .+ to send them the invite link\.")))
        expect(note).to_be_visible()
        expect(self.row(page, self.member_name)).to_contain_text("(you)")
        expect(self.row(page, self.member_name)).to_contain_text("Member")
        expect(self.row(page, self.admin_name)).to_contain_text("Admin")
        footer = self.vis(page.get_by_text(re.compile(r"Signed in as .*member of this family\.")))
        expect(footer).to_be_visible()
        self.snap(page, "member-view")
        return f"no admin controls; '{note.inner_text()}'; footer '{footer.inner_text()}'"

    def s_admin_view(self) -> str:
        expect = self.expect
        ctx, page = self.open_settings("admin", "admin")
        expect(page.get_by_role("heading", level=1, name=self.family)).to_be_visible()
        expect(page.get_by_role("heading", level=2, name="Invite your family")).to_be_visible()
        rows = self.members_list(page).get_by_role("listitem")
        texts = [t.replace("\n", " ") for t in rows.all_inner_texts()]
        if not texts or "(you)" not in texts[0] or "Admin" not in texts[0]:
            raise AssertionError(f"the admin's own row is not first, marked (you) and Admin: {texts}")
        expect(self.row(page, self.admin_name).get_by_role("button")).to_have_count(0)  # sole admin
        expect(self.row(page, self.member_name).get_by_role(
            "button", name=f"Make {self.member_name} an admin", exact=True)).to_be_visible()
        expect(self.row(page, self.member_name).get_by_role(
            "button", name=f"Remove {self.member_name}", exact=True)).to_be_visible()
        self.snap(page, "admin-view")
        return f"rows: {' | '.join(texts)}"

    def flush_check(self, page: Any, where: str) -> tuple[list[str], list[str], dict[str, int]]:
        """FLUSH_JS at desktop and phone width: (lines, failures, controls measured per width)."""
        lines: list[str] = []
        bad: list[str] = []
        counts: dict[str, int] = {}
        try:
            for view, size in (("desktop", DESKTOP), ("phone", PHONE)):
                page.set_viewport_size(size)
                page.wait_for_timeout(250)
                found = page.evaluate(FLUSH_JS)
                counts[view] = len(found)
                for f in found:
                    line = (f"{where} {view}: '{f['control']}' ({f['tag']}) text left {f['textLeft']}px vs "
                            f"{f['refWhat']} {f['ref']}px (delta {f['delta']}px)")
                    lines.append(line)
                    if abs(float(f["delta"])) > FLUSH_TOLERANCE_PX:
                        bad.append(line)
        finally:
            page.set_viewport_size(DESKTOP)
            page.wait_for_timeout(100)
        return lines, bad, counts

    def s_flush(self) -> str:
        """Finding 5: a flush quiet control whose text starts off the fields' edge (the busy layer)."""
        ctx, page = self.open_settings("admin", "flush")
        lines, bad, counts = self.flush_check(page, "settings")
        for view, n in counts.items():
            if n == 0:
                bad.append(f"settings {view}: no quiet control pulled flush with -ml-3 found (the 'More' links are)")
        self.goto(page, f"/add?family={quote(self.fid, safe='')}")
        self.expect(self.vis(page.get_by_label("Your story"))).to_be_visible()
        add_lines, add_bad, add_counts = self.flush_check(page, "/add")
        lines += add_lines
        bad += add_bad
        print("  " + "\n  ".join(lines), flush=True)
        if bad:
            raise AssertionError(f"text more than {FLUSH_TOLERANCE_PX:g}px off: " + "; ".join(bad))
        return (f"settings: {counts.get('desktop', 0)} at desktop, {counts.get('phone', 0)} at phone; "
                f"/add: {add_counts.get('desktop', 0)} at desktop, {add_counts.get('phone', 0)} at phone; "
                f"all within {FLUSH_TOLERANCE_PX:g}px: " + "; ".join(lines))

    def open_role_confirm(self, page: Any, button_name: str, question: str) -> Any:
        page.get_by_role("button", name=button_name, exact=True).click()
        group = page.get_by_role("group", name=question)
        self.expect(group).to_be_visible()
        self.expect(group).to_be_focused()
        return group

    def s_roles(self) -> str:
        expect = self.expect
        ctx, page = self.open_settings("admin", "roles")
        who = self.member_name
        group = self.open_role_confirm(page, f"Make {who} an admin", f"Make {who} an admin?")
        expect(group).to_contain_text("Admins can invite people, remove members and change who is an admin.")
        self.snap(page, "roles-promote-confirm")
        group.get_by_role("button", name="Make admin", exact=True).click()
        expect(self.row(page, who)).to_contain_text("Admin")
        expect(page.get_by_role("status").filter(has_text=f"{who} is now an admin.")).to_have_count(1)
        got = self.expect_focus(page, rf"button: Make {re.escape(who)} a member", "promoting")
        # Two admins now: the admin may step down on their own row.
        expect(page.get_by_role("button", name="Step down as admin", exact=True)).to_be_visible()
        self.snap(page, "roles-promoted")
        if self.real and self.role_of("member") != "admin":
            raise Seam("api", f"after 'Make admin' the API says {who} is {self.role_of('member')}")

        group = self.open_role_confirm(page, f"Make {who} a member", f"Make {who} a member?")
        self.snap(page, "roles-demote-confirm")
        group.get_by_role("button", name="Make member", exact=True).click()
        expect(self.row(page, who)).to_contain_text("Member")
        expect(page.get_by_role("status").filter(has_text=f"{who} is now a member.")).to_have_count(1)
        got2 = self.expect_focus(page, rf"button: Make {re.escape(who)} an admin", "demoting")
        expect(page.get_by_role("button", name="Step down as admin", exact=True)).to_have_count(0)
        self.snap(page, "roles-demoted")
        if self.real and self.role_of("member") != "member":
            raise Seam("api", f"after 'Make member' the API says {who} is {self.role_of('member')}")
        return f"promoted and demoted {who}; focus after each: '{got}', '{got2}'"

    def s_last_admin(self) -> str:
        expect = self.expect
        ctx, page = self.open_settings("admin", "last-admin")
        mine = self.row(page, self.admin_name)
        expect(mine).to_contain_text("Admin")
        expect(mine.get_by_role("button")).to_have_count(0)
        self.snap(page, "last-admin-no-step-down")
        # A second admin: now stepping down is offered; open it and cancel with Escape.
        who = self.member_name
        group = self.open_role_confirm(page, f"Make {who} an admin", f"Make {who} an admin?")
        group.get_by_role("button", name="Make admin", exact=True).click()
        expect(self.row(page, who)).to_contain_text("Admin")
        group = self.open_role_confirm(page, "Step down as admin", "Step down as admin?")
        expect(group).to_contain_text("You’ll stay in the family")
        self.snap(page, "last-admin-step-down-confirm")
        page.keyboard.press("Escape")
        expect(group).to_have_count(0)
        got = self.expect_focus(page, r"button: Step down as admin", "Escape")
        # Cancel with the button too, then put the member back.
        group = self.open_role_confirm(page, "Step down as admin", "Step down as admin?")
        group.get_by_role("button", name="Cancel").click()
        self.expect_focus(page, r"button: Step down as admin", "Cancel")
        group = self.open_role_confirm(page, f"Make {who} a member", f"Make {who} a member?")
        group.get_by_role("button", name="Make member", exact=True).click()
        expect(self.row(page, who)).to_contain_text("Member")
        expect(page.get_by_role("button", name="Step down as admin", exact=True)).to_have_count(0)
        return f"sole admin: no Step down; with two admins Step down opens a confirm, Escape/Cancel -> '{got}'"

    def s_remove(self) -> str:
        expect = self.expect
        ctx, page = self.open_settings("admin", "remove")
        who = self.people["cal"][1] if self.real else self.member_name
        page.get_by_role("button", name=f"Remove {who}", exact=True).click()
        group = page.get_by_role("group", name=f"Remove {who} from the family? Their place in the tree stays.")
        expect(group).to_be_visible()
        expect(group).to_be_focused()
        self.snap(page, "remove-confirm")
        group.get_by_role("button", name="Cancel").click()
        self.expect_focus(page, rf"button: Remove {re.escape(who)}", "Cancel")
        page.get_by_role("button", name=f"Remove {who}", exact=True).click()
        group.get_by_role("button", name="Confirm").click()
        expect(self.row(page, who)).to_have_count(0)
        expect(page.get_by_role("status").filter(has_text=f"{who} was removed from the family.")).to_have_count(1)
        got = self.expect_focus(page, r"button: Remove |h2: Who", "removing")
        self.snap(page, "remove-done")
        if self.real:
            uid = self.user_ids["cal"]
            if any(m.get("userId") == uid for m in self.family_now().get("members") or []):
                raise Seam("api", f"{who} is still a member after the remove")
        return f"confirm, cancel (focus back), confirm: {who} removed; focus then on '{got}'"

    def s_invite(self) -> str:
        expect = self.expect
        ctx, page = self.open_settings("admin", "invite", clipboard=True)
        notes = []
        link = page.get_by_label("Invite link")
        if link.count() == 0:
            page.get_by_role("button", name="Make an invite link").click()
        expect(link).to_be_visible()
        url = link.input_value()
        if not re.fullmatch(rf"{re.escape(self.web)}/join/[^/]+", url):
            raise AssertionError(f"the invite link is '{url}', not {self.web}/join/<code>")
        expect(self.vis(page.get_by_text(re.compile(r"^(Works for \d+ more days|Expires (today|tomorrow))$")))).to_be_visible()
        page.get_by_role("button", name="Copy link").click()
        expect(page.get_by_role("status").filter(has_text=LINK_COPIED)).to_be_visible()
        copied = page.evaluate("() => navigator.clipboard.readText()")
        if copied != url:
            raise AssertionError(f"the clipboard holds '{copied}', the link is '{url}'")
        self.snap(page, "invite-copied")
        notes.append(f"copied {url}")
        old_code = url.rsplit("/", 1)[1]

        page.get_by_role("button", name="Turn off this link").click()
        group = page.get_by_role("group", name=re.compile(re.escape(TURN_OFF_Q)))
        expect(group).to_be_focused()
        self.snap(page, "invite-turn-off-confirm")
        page.keyboard.press("Escape")
        self.expect_focus(page, r"button: Turn off this link", "Escape")
        page.get_by_role("button", name="Turn off this link").click()
        group.get_by_role("button", name="Confirm").click()
        make = page.get_by_role("button", name="Make an invite link")
        expect(make).to_be_visible()
        expect(link).to_have_count(0)
        self.expect_focus(page, r"button: Make an invite link", "turning the link off")
        self.snap(page, "invite-off")
        notes.append("turned off; focus on 'Make an invite link'")
        if self.real:
            fam = self.family_now()
            live = [i for i in fam.get("invites") or [] if not i.get("revoked")]
            if any(i.get("code") == old_code for i in live):
                raise Seam("api", f"invite {old_code} is not revoked after 'Turn off'")
            # The old link is dead for a visitor.
            vctx, vpage = self.new_page("dead-link")
            self.goto(vpage, f"/join/{old_code}")
            expect(vpage.get_by_role("heading", level=1, name=re.compile("isn’t working|has expired"))).to_be_visible()
            vctx.close()

        make.click()
        expect(link).to_be_visible()
        self.expect_focus(page, r"input: .*?/join/", "making a link")
        new_url = link.input_value()
        if new_url == url:
            raise AssertionError("the new link is the old one")
        page.get_by_role("button", name="Copy link").click()
        expect(page.get_by_role("status").filter(has_text=LINK_COPIED)).to_be_visible()
        self.snap(page, "invite-new")
        notes.append(f"made {new_url}, focus on the link")
        return "; ".join(notes)

    # ----- stale actions (real mode, a second context)

    def need_real(self) -> None:
        if not self.real:
            raise Skip("the mock keeps one session per browser, so another person's change cannot reach an "
                       "open page; real mode covers it with a second context")

    def ensure_member(self, key: str) -> None:
        """Real mode: `key` is a member (rejoining with a fresh invite if they were removed)."""
        if self.role_of(key) is not None:
            return
        code = self.api_call(self.admin_ctx, "POST", f"/families/{self.fid}/invites")["code"]
        jctx = self.browser.new_context()
        try:
            self.api_login(jctx, self.people[key][0])
            me = self.api_call(jctx, "GET", "/me")
            del me
            tree = self.api_call(self.admin_ctx, "GET", f"/families/{self.fid}/tree")
            pid = next((p["id"] for p in tree.get("people", []) if p.get("name") == self.people[key][1]), None)
            self.api_call(jctx, "POST", f"/invites/{code}/join", {"personId": pid} if pid else
                          {"person": {"name": self.people[key][1]}})
        finally:
            jctx.close()

    def s_stale_remove(self) -> str:
        expect = self.expect
        self.need_real()
        who = self.people["dov"][1]
        ctx, page = self.open_settings("admin", "stale-remove")
        expect(self.row(page, who)).to_have_count(1)
        # Another tab removes them first.
        self.api_call(self.admin_ctx, "DELETE", f"/families/{self.fid}/members/{self.user_ids['dov']}")
        page.get_by_role("button", name=f"Remove {who}", exact=True).click()
        page.get_by_role("group", name=re.compile(r"^Remove ")).get_by_role("button", name="Confirm").click()
        message = f"{who} had already left the family."
        # The visible line under the list (the live region says it too, for screen readers).
        expect(self.vis(page.locator("p:not([role=status])").filter(has_text=message))).to_be_visible()
        expect(self.row(page, who)).to_have_count(0)
        self.expect_no_alert(page, "a removal that found them gone")
        got = self.expect_focus(page, r"button: Remove |h2: Who", "a removal that found them gone")
        self.snap(page, "stale-remove-404")
        return f"404 -> '{message}' as a calm notice, the row gone, focus on '{got}'"

    def s_stale_role(self) -> str:
        expect = self.expect
        self.need_real()
        self.ensure_member("dov")
        who = self.people["dov"][1]
        ctx, page = self.open_settings("admin", "stale-role")
        expect(self.row(page, who)).to_have_count(1)
        self.api_call(self.admin_ctx, "DELETE", f"/families/{self.fid}/members/{self.user_ids['dov']}")
        group = self.open_role_confirm(page, f"Make {who} an admin", f"Make {who} an admin?")
        group.get_by_role("button", name="Make admin", exact=True).click()
        message = f"{who} isn’t in the family any more."
        expect(self.alert(page, message)).to_be_visible()
        expect(self.row(page, who)).to_have_count(0)
        got = self.expect_focus(page, r"h2: Who|button: ", "a role change that found them gone")
        self.snap(page, "stale-role-404")
        return f"404 -> '{message}' under the list, the row gone (page fetched the family), focus on '{got}'"

    def s_stale_revoke(self) -> str:
        expect = self.expect
        self.need_real()
        ctx, page = self.open_settings("admin", "stale-revoke")
        link = page.get_by_label("Invite link")
        make = page.get_by_role("button", name="Make an invite link")
        notes = []
        # Another tab turns the link off first. The API answers 204 again for a code the
        # family has, revoked or not (so does the mock): the page just shows it off.
        if link.count() == 0:
            make.click()
        expect(link).to_be_visible()
        for inv in self.family_now().get("invites") or []:
            if not inv.get("revoked"):
                self.api_call(self.admin_ctx, "DELETE", f"/families/{self.fid}/invites/{inv['code']}")
        page.get_by_role("button", name="Turn off this link").click()
        page.get_by_role("group", name=re.compile(re.escape(TURN_OFF_Q))).get_by_role("button", name="Confirm").click()
        expect(make).to_be_visible()
        self.expect_no_alert(page, "turning off a link another tab turned off")
        self.expect_focus(page, r"button: Make an invite link", "turning off a link already off")
        self.snap(page, "stale-revoke-already-off")
        notes.append("already off elsewhere: the API answers 204, the card shows no link and no error, "
                     "focus on 'Make an invite link'")
        # The API's 404 ("Invite not found or expired") is for a code the family never had,
        # which the card never shows, so the "already turned off" notice has no real path here.
        return "; ".join(notes)

    def s_stale_last_admin(self) -> str:
        expect = self.expect
        self.need_real()
        self.set_role(self.admin_ctx, "member", "admin")
        ctx, page = self.open_settings("admin", "stale-409")
        expect(page.get_by_role("button", name="Step down as admin", exact=True)).to_be_visible()
        # The other admin steps down in their own browser first.
        bctx = self.browser.new_context()
        try:
            self.api_login(bctx, self.people["member"][0])
            self.set_role(bctx, "member", "member")
        finally:
            bctx.close()
        group = self.open_role_confirm(page, "Step down as admin", "Step down as admin?")
        group.get_by_role("button", name="Step down", exact=True).click()
        expect(self.alert(page, STEP_DOWN_409)).to_be_visible()
        expect(self.row(page, self.member_name)).to_contain_text("Member")
        expect(page.get_by_role("button", name="Step down as admin", exact=True)).to_have_count(0)
        got = self.expect_focus(page, r"h2: Who|button: ", "a refused step-down")
        self.snap(page, "stale-last-admin-409")
        if self.role_of("admin") != "admin":
            raise Seam("api", "the last admin was demoted")
        return f"409 -> '{STEP_DOWN_409}', the list shows the other admin as a member again, focus on '{got}'"

    def s_stale_not_admin(self) -> str:
        expect = self.expect
        self.need_real()
        self.set_role(self.admin_ctx, "member", "admin")
        ctx, page = self.open_settings("admin", "stale-403")
        who = self.member_name
        expect(page.get_by_role("button", name=f"Make {who} a member", exact=True)).to_be_visible()
        # The other admin makes this one a member, elsewhere.
        bctx = self.browser.new_context()
        try:
            self.api_login(bctx, self.people["member"][0])
            self.set_role(bctx, "admin", "member")
            group = self.open_role_confirm(page, f"Make {who} a member", f"Make {who} a member?")
            group.get_by_role("button", name="Make member", exact=True).click()
            expect(self.vis(page.get_by_role("status").filter(has_text=NO_LONGER_ADMIN))).to_be_visible()
            expect(page.get_by_role("heading", level=2, name="Invite your family")).to_be_visible()  # the note
            expect(page.get_by_label("Invite link")).to_have_count(0)
            expect(self.members_list(page).get_by_role("button")).to_have_count(0)
            expect(self.row(page, who)).to_contain_text("Admin")  # the change did not happen
            # Said once, at the top: no row message repeating it.
            expect(page.get_by_text(NOT_ADMIN_ROLE)).to_have_count(0)
            self.expect_no_alert(page, "a 403 on a role change")
            got = self.expect_focus(page, r"h2: Who", "a 403 on a role change")
            self.snap(page, "stale-not-admin-403")
            # Put things back: the admin is an admin again, the member a member.
            self.set_role(bctx, "admin", "admin")
            self.set_role(self.admin_ctx, "member", "member")
        finally:
            bctx.close()
        return (f"403 -> one line at the top ('{NO_LONGER_ADMIN}…'), no row message repeating it; admin "
                f"controls and the invite card gone; the role unchanged; focus '{got}'")

    # ----- server errors

    def s_err_load(self) -> str:
        expect = self.expect
        notes = []
        for what, mock_path, real_re, network, message in (
            ("family 500", "/families/{fid}", rf"/families/{self.esc_fid()}", False, SERVER_TROUBLE),
            ("tree dropped", "/families/{fid}/tree", rf"/families/{self.esc_fid()}/tree", True, NETWORK_ERROR),
        ):
            ctx, page = self.new_page(f"err-load-{what}")
            self.sign_in(ctx, page, "admin")
            if self.real:
                self.fail_route(page, real_re, "GET", abort=network, times=None)
            else:
                if not self.faults_supported:
                    raise Skip("the mock has no fault injection on this branch")
                fault: dict[str, Any] = {"method": "GET", "path": mock_path}
                fault.update({"network": True} if network else {"status": 500})
                page.add_init_script(mock_faults_script([fault]))
            self.goto(page, self.settings_path())
            expect(self.alert(page, message)).to_be_visible()
            expect(self.alert(page, LOAD_ERROR_TITLE)).to_be_visible()
            expect(page.get_by_role("heading", level=1, name=self.family)).to_be_visible()
            if abs(self.column(page) - self.loaded_width.get("settings", self.column(page))) > 0.5:
                self.layout.append(f"err-load {what}: the column is {self.column(page):g}px")
            self.snap(page, f"err-load-{what.replace(' ', '-')}")
            if self.real:
                page.unroute_all()
            else:
                page.evaluate("() => window.__stitchMock.clearFaults()")
            page.get_by_role("button", name="Try again").click()
            self.wait_loaded(page)
            self.expect_focus(page, r"^div: ", "Try again worked")
            notes.append(f"{what}: '{message}' under '{LOAD_ERROR_TITLE}'; Try again loads, focus on the content")
        return "; ".join(notes)

    def s_err_me(self) -> str:
        expect = self.expect
        ctx, page = self.new_page("err-me")
        self.sign_in(ctx, page, "admin")
        if self.real:
            self.fail_route(page, r"/me", "GET", times=None)
        else:
            if not self.faults_supported:
                raise Skip("the mock has no fault injection on this branch")
            page.add_init_script(mock_faults_script([{"method": "GET", "path": "/me", "status": 500}]))
        self.goto(page, self.settings_path())
        expect(page.get_by_role("heading", level=1, name=GATE_ERROR_TITLE)).to_be_visible()
        expect(self.alert(page, SERVER_TROUBLE)).to_be_visible()
        width = self.column(page)
        want = self.loaded_width.get("settings")
        if want and abs(width - want) > 0.5:
            self.layout.append(f"err-me: the session error sheet's column is {width:g}px, the page's {want:g}px")
        self.snap(page, "err-me-500")
        if self.real:
            page.unroute_all()
        else:
            page.evaluate("() => window.__stitchMock.clearFaults()")
        page.get_by_role("button", name="Try again").click()
        self.wait_loaded(page)
        return f"'{GATE_ERROR_TITLE}' with '{SERVER_TROUBLE}' at {width:g}px; Try again opens the page"

    def s_err_invite(self) -> str:
        expect = self.expect
        ctx, page = self.open_settings("admin", "err-invite", clipboard=True)
        link = page.get_by_label("Invite link")
        if link.count() == 0:
            page.get_by_role("button", name="Make an invite link").click()
        expect(link).to_be_visible()
        url = link.input_value()
        notes = []
        revoke_re = rf"/families/{self.esc_fid()}/invites/[^/?]+"
        for network, message in ((False, SERVER_TROUBLE), (True, NETWORK_ERROR)):
            self.fail(page, "DELETE", "/families/{fid}/invites/{code}", revoke_re, network=network)
            page.get_by_role("button", name="Turn off this link").click()
            page.get_by_role("group", name=re.compile(re.escape(TURN_OFF_Q))).get_by_role("button", name="Confirm").click()
            expect(self.alert(page, message)).to_be_visible()
            expect(self.alert(page, message)).to_contain_text("We couldn’t turn the link off")
            expect(link).to_have_value(url)
            self.expect_focus(page, r"button: Turn off this link", "a failed turn-off")
            self.snap(page, f"err-revoke-{'network' if network else '500'}")
            notes.append(f"turn off {'dropped' if network else '500'}: '{message}', link kept, focus on Turn off")
        page.get_by_role("button", name="Turn off this link").click()
        page.get_by_role("group", name=re.compile(re.escape(TURN_OFF_Q))).get_by_role("button", name="Confirm").click()
        make = page.get_by_role("button", name="Make an invite link")
        expect(make).to_be_visible()
        create_re = rf"/families/{self.esc_fid()}/invites"
        for network, message in ((False, SERVER_TROUBLE), (True, NETWORK_ERROR)):
            self.fail(page, "POST", "/families/{fid}/invites", create_re, network=network)
            make.click()
            expect(self.alert(page, message)).to_be_visible()
            expect(self.alert(page, message)).to_contain_text("We couldn’t make a link")
            self.expect_focus(page, r"button: Make an invite link", "a failed make")
            self.snap(page, f"err-create-{'network' if network else '500'}")
            notes.append(f"make {'dropped' if network else '500'}: '{message}', focus on Make")
        make.click()
        expect(link).to_be_visible()
        self.expect_no_alert(page, "a link made after two failures")
        notes.append("the third try makes a link and clears the message")
        return "; ".join(notes)

    def s_err_role(self) -> str:
        expect = self.expect
        ctx, page = self.open_settings("admin", "err-role")
        who = self.member_name
        notes = []
        role_re = rf"/families/{self.esc_fid()}/members/[^/?]+"
        for network, message in ((False, SERVER_TROUBLE), (True, NETWORK_ERROR)):
            self.fail(page, "PATCH", "/families/{fid}/members/{userId}", role_re, network=network)
            group = self.open_role_confirm(page, f"Make {who} an admin", f"Make {who} an admin?")
            group.get_by_role("button", name="Make admin", exact=True).click()
            row_alert = self.row(page, who).get_by_role("alert").filter(has_text=message)
            expect(row_alert).to_be_visible()
            expect(row_alert).to_contain_text(f"We couldn’t make {who} an admin")
            expect(self.row(page, who)).to_contain_text("Member")
            self.expect_focus(page, rf"button: Make {re.escape(who)} an admin", "a failed role change")
            self.snap(page, f"err-role-{'network' if network else '500'}")
            notes.append(f"{'dropped' if network else '500'}: '{message}' on the row, role kept, focus back")
        return "; ".join(notes)

    def s_err_remove(self) -> str:
        expect = self.expect
        ctx, page = self.open_settings("admin", "err-remove")
        who = self.member_name
        notes = []
        rm_re = rf"/families/{self.esc_fid()}/members/[^/?]+"
        for network, message in ((False, SERVER_TROUBLE), (True, NETWORK_ERROR)):
            self.fail(page, "DELETE", "/families/{fid}/members/{userId}", rm_re, network=network)
            page.get_by_role("button", name=f"Remove {who}", exact=True).click()
            page.get_by_role("group", name=re.compile(r"^Remove ")).get_by_role("button", name="Confirm").click()
            row_alert = self.row(page, who).get_by_role("alert").filter(has_text=message)
            expect(row_alert).to_be_visible()
            expect(row_alert).to_contain_text(f"We couldn’t remove {who}")
            self.expect_focus(page, rf"button: Remove {re.escape(who)}", "a failed removal")
            self.snap(page, f"err-remove-{'network' if network else '500'}")
            notes.append(f"{'dropped' if network else '500'}: '{message}' on the row, still listed, focus back")
        if self.real and self.role_of("member") is None:
            raise Seam("api", f"{who} was removed although the request failed")
        return "; ".join(notes)

    # ----- welcome, switcher, links

    def s_welcome(self) -> str:
        expect = self.expect
        ctx, page = self.new_page("welcome")
        self.sign_in(ctx, page, "admin")
        self.goto(page, "/families/new")
        expect(page.get_by_role("heading", level=1, name="Start your family")).to_be_visible()
        name = f"The Seconds {self.tag}"
        self.vis(page.get_by_label("Family name")).fill(name)
        your = self.vis(page.get_by_label("Your name"))
        if not your.input_value():
            your.fill(self.admin_name)
        page.get_by_role("button", name="Start the family").click()
        expect(page).to_have_url(re.compile(r"/families/[^/]+/settings\?welcome=1$"))
        expect(page.get_by_role("heading", level=1, name=name)).to_be_visible()
        expect(self.vis(page.get_by_text(WELCOME_ADMIN, exact=True))).to_be_visible()
        expect(page.get_by_role("button", name="Make an invite link")).to_be_visible()
        expect(self.vis(page.get_by_text(re.compile("It’s just you so far")))).to_be_visible()
        self.second_fid = re.search(r"/families/([^/]+)/settings", page.url).group(1)
        self.snap(page, "welcome")
        self.welcome_ctx = ctx
        self.contexts.remove(ctx)  # the switcher step goes on from here
        self.welcome_page = page
        return f"'{name}' -> {urlsplit(page.url).path}?welcome=1, lede '{WELCOME_ADMIN}'"

    def s_switcher(self) -> str:
        expect = self.expect
        page = self.welcome_page
        self.contexts.append(self.welcome_ctx)
        self.current = page
        nav = page.get_by_role("navigation", name="Your families")
        expect(nav).to_be_visible()
        links = nav.get_by_role("link")
        expect(links).to_have_count(2)
        current = nav.locator("[aria-current]")
        expect(current).to_have_count(1)
        expect(current).to_contain_text("The Seconds")
        expect(nav.get_by_role("link", name=re.compile(re.escape(self.family)))).to_have_attribute(
            "href", self.settings_path())
        self.snap(page, "switcher")
        nav.get_by_role("link", name=re.compile(re.escape(self.family))).click()
        expect(page).to_have_url(re.compile(re.escape(self.settings_path()) + "$"))
        expect(page.get_by_role("heading", level=1, name=self.family)).to_be_visible()
        self.wait_loaded(page)
        nav = self.vis(page.get_by_role("navigation", name="Your families"))
        expect(nav.locator("[aria-current]")).to_contain_text(self.family)
        self.snap(page, "switcher-switched")
        return f"two families listed, current marked; switched to '{self.family}'"

    def s_links(self) -> str:
        expect = self.expect
        ctx, page = self.open_settings("admin", "links")
        fid = quote(self.fid, safe="")
        back = page.get_by_role("link", name="Family tree")
        expect(back).to_have_attribute("href", "/")
        self.check_mark(page)
        more = page.get_by_role("navigation", name="More")
        expect(more.get_by_role("link", name="Add a memory")).to_have_attribute("href", f"/add?family={fid}")
        expect(more.get_by_role("link", name="Add a relative")).to_have_attribute(
            "href", f"/families/{fid}/people/new")
        expect(more.get_by_role("link", name="Start another family")).to_have_attribute("href", "/families/new")
        expect(page.get_by_role("button", name="Sign out")).to_be_visible()
        self.snap(page, "links-footer")
        back.click()
        expect(page).to_have_url(re.compile(rf"^{re.escape(self.web)}/?$"))
        page.wait_for_load_state("networkidle")
        self.snap(page, "links-home")
        # Sign out from settings goes to the login page.
        self.goto(page, self.settings_path())
        self.wait_loaded(page)
        page.get_by_role("button", name="Sign out").click()
        expect(page).to_have_url(re.compile(r"/login$"))
        return ("'← Family tree' and the Stitch mark -> '/', landed home; footer: Add a memory, Add a relative, "
                "Start another family; Sign out -> /login")

    # ----- add a relative

    def open_relative(self, label: str, path: str | None = None) -> tuple[Any, Any]:
        ctx, page = self.new_page(label)
        self.sign_in(ctx, page, "admin")
        self.goto(page, path or self.relative_path())
        self.expect(page.get_by_role("button", name="Add to the tree")).to_be_visible()
        return ctx, page

    def s_rel_empty(self) -> str:
        expect = self.expect
        ctx, page = self.open_relative("rel-empty")
        expect(page.get_by_role("heading", level=1, name="Add a relative")).to_be_visible()
        expect(page.get_by_role("link", name="Family settings")).to_have_attribute("href", self.settings_path())
        self.check_mark(page)
        expect(self.vis(page.get_by_label("Their name"))).to_have_value("")
        expect(page.get_by_role("group", name=re.compile(r"^Parents"))).to_be_visible()
        self.snap(page, "rel-empty")
        return "the form, its pickers, '← Family settings'"

    def s_rel_validation(self) -> str:
        expect = self.expect
        ctx, page = self.open_relative("rel-validation")
        name = self.vis(page.get_by_label("Their name"))
        born = self.vis(page.get_by_label("Born", exact=True))
        died = self.vis(page.get_by_label("Died", exact=True))
        submit = page.get_by_role("button", name="Add to the tree")
        notes = []
        submit.click()
        expect(self.vis(page.get_by_text(NAME_EMPTY))).to_be_visible()
        expect(name).to_be_focused()
        expect(name).to_have_attribute("aria-invalid", "true")
        self.snap(page, "rel-validation-blank-name")
        notes.append(f"blank: '{NAME_EMPTY}', focus on the name")

        name.fill(f"Ivy Sweep {self.tag}")
        born.fill("3000")
        submit.click()
        expect(self.vis(page.get_by_text(YEAR_FUTURE))).to_be_visible()
        expect(born).to_be_focused()
        born.fill("1600")
        submit.click()
        early = self.vis(page.get_by_text(re.compile(r"1700")))
        expect(early.first).to_be_visible()
        born.fill("1950")
        died.fill("1940")
        submit.click()
        expect(self.vis(page.get_by_text(DIED_BEFORE))).to_be_visible()
        expect(died).to_be_focused()
        self.snap(page, "rel-validation-years")
        notes.append(f"years: future, before 1700, '{DIED_BEFORE}'")

        # A parent born after them (the API's rule, caught before sending).
        died.fill("")
        parents = page.get_by_role("group", name=re.compile(r"^Parents"))
        parent = self.admin_name if self.real else "Maya"
        born.fill("1940" if self.real else "1950")
        self.tick(parents, parent)
        submit.click()
        msg = self.vis(page.get_by_text(re.compile(rf"They can’t be born before their parent {re.escape(parent)}")))
        expect(msg).to_be_visible()
        self.snap(page, "rel-validation-parent-born-after")
        notes.append(f"'{msg.inner_text()}'")
        self.untick(parents, parent)

        # The same person picked as a parent and a spouse: the picker allows one or the other.
        spouses = page.get_by_role("group", name="Married to")
        self.tick(parents, parent)
        box = self.vis(spouses.get_by_label(parent, exact=True))
        if box.count() and box.is_enabled():
            self.vis(spouses.get_by_text(parent, exact=True)).click()
            both = (self.vis(parents.get_by_label(parent, exact=True)).is_checked()
                    and box.is_checked())
            if both:
                submit.click()
                expect(self.vis(page.get_by_text(re.compile("both a parent and a spouse")))).to_be_visible()
                notes.append("parent and spouse at once: the message")
            else:
                notes.append("ticking a parent as a spouse moves them (never both)")
        else:
            notes.append("someone picked as a parent cannot be picked as a spouse")
        self.snap(page, "rel-validation-relationships")

        # The duplicate-name hint.
        existing = self.member_name
        name.fill(existing)
        hint = self.vis(page.get_by_text(re.compile(rf"{re.escape(existing)}.* is already in the tree\.")))
        expect(hint).to_be_visible()
        self.snap(page, "rel-duplicate-hint")
        notes.append(f"hint: '{hint.inner_text()}'")
        return "; ".join(notes)

    def tick(self, scope: Any, name: str) -> None:
        box = self.vis(scope.get_by_label(name, exact=True))
        if box.is_checked():
            return
        text = self.vis(scope.get_by_text(name, exact=True))
        text.scroll_into_view_if_needed()
        text.click()
        self.expect(box).to_be_checked()

    def untick(self, scope: Any, name: str) -> None:
        box = self.vis(scope.get_by_label(name, exact=True))
        if not box.is_checked():
            return
        self.vis(scope.get_by_text(name, exact=True)).click()
        self.expect(box).not_to_be_checked()

    def s_rel_errors(self) -> str:
        expect = self.expect
        ctx, page = self.open_relative("rel-errors")
        name_text = f"Jun Sweep {self.tag}"
        name = self.vis(page.get_by_label("Their name"))
        name.fill(name_text)
        self.vis(page.get_by_label("Born", exact=True)).fill("1931")
        notes = []
        people_re = rf"/families/{self.esc_fid()}/people"
        for network, message in ((False, SERVER_TROUBLE), (True, NETWORK_ERROR)):
            self.fail(page, "POST", "/families/{fid}/people", people_re, network=network)
            page.get_by_role("button", name="Add to the tree").click()
            alert = self.alert(page, message)
            expect(alert).to_be_visible()
            expect(alert).to_be_focused()
            expect(name).to_have_value(name_text)
            expect(page.get_by_role("button", name="Add to the tree")).to_be_enabled()
            self.snap(page, f"rel-error-{'network' if network else '500'}")
            notes.append(f"{'dropped' if network else '500'}: '{message}', focus on it, typed kept")
        if self.real:
            tree = self.api_call(self.admin_ctx, "GET", f"/families/{self.fid}/tree")
            if any(p.get("name") == name_text for p in tree.get("people", [])):
                raise Seam("api", f"{name_text} was saved although the request failed")
        return "; ".join(notes)

    def s_rel_success(self) -> str:
        expect = self.expect
        ctx, page = self.open_relative("rel-success")
        name_text = f"Kit Sweep {self.tag}"
        self.vis(page.get_by_label("Their name")).fill(name_text)
        self.vis(page.get_by_label("Born", exact=True)).fill("1999")
        parents = page.get_by_role("group", name=re.compile(r"^Parents"))
        parent = self.admin_name if self.real else "David"
        self.tick(parents, parent)
        page.get_by_role("button", name="Add to the tree").click()
        heading = page.get_by_role("heading", level=2, name=f"{name_text} is in the tree.")
        expect(heading).to_be_visible()
        expect(heading).to_be_focused()
        add = page.get_by_role("link", name="Add a memory")
        href = add.get_attribute("href") or ""
        m = re.fullmatch(rf"/add\?family={re.escape(quote(self.fid, safe=''))}&added=([^&]+)", href)
        if not m:
            raise AssertionError(f"'Add a memory' leads to '{href}', not /add?family=<fid>&added=<pid>")
        expect(page.get_by_role("link", name="Back to family settings")).to_have_attribute("href", self.settings_path())
        expect(page.get_by_role("button", name="Add another relative")).to_be_visible()
        self.snap(page, "rel-done")
        if self.real:
            tree = self.api_call(self.admin_ctx, "GET", f"/families/{self.fid}/tree")
            kid = next((p for p in tree.get("people", []) if p.get("id") == m.group(1)), None)
            if not kid or kid.get("name") != name_text:
                raise Seam("api", f"the new person {m.group(1)} is not in the tree: {kid}")
        page.get_by_role("button", name="Add another relative").click()
        expect(self.vis(page.get_by_label("Their name"))).to_have_value("")
        return f"done card, heading focused; 'Add a memory' -> {href}; 'Add another relative' empties the form"

    def s_rel_roundtrip(self) -> str:
        expect = self.expect
        ctx, page = self.new_page("rel-roundtrip")
        self.sign_in(ctx, page, "admin")
        fid = quote(self.fid, safe="")
        self.goto(page, f"/add?family={fid}")
        link = self.vis(page.get_by_role("link", name="Add them to the family"))
        expect(link).to_be_visible()
        back = f"/add?family={fid}"
        expect(link).to_have_attribute("href", f"/families/{fid}/people/new?next={quote(back, safe='')}")
        link.click()
        expect(page).to_have_url(re.compile(re.escape(f"/families/{fid}/people/new?next=")))
        expect(page.get_by_role("button", name="Add to the tree")).to_be_visible()
        expect(page.get_by_role("link", name="Back")).to_have_attribute("href", back)
        self.snap(page, "rel-roundtrip-form")
        name_text = f"Lux Sweep {self.tag}"
        self.vis(page.get_by_label("Their name")).fill(name_text)
        page.get_by_role("button", name="Add to the tree").click()
        landed = re.compile(rf"/add\?family={re.escape(fid)}&added=([^&]+)$")
        expect(page).to_have_url(landed)
        arrived = page.url  # the add page may tidy `added` out of the address once it has read it
        who = page.get_by_role("group", name=re.compile("Who was there"))
        box = self.vis(who.get_by_label(name_text, exact=True))
        expect(box).to_be_checked()
        self.snap(page, "rel-roundtrip-back-on-add")
        if self.real:
            pid = landed.search(arrived).group(1)
            tree = self.api_call(self.admin_ctx, "GET", f"/families/{self.fid}/tree")
            if not any(p.get("id") == pid and p.get("name") == name_text for p in tree.get("people", [])):
                raise Seam("api", f"added={pid} is not '{name_text}' in the tree")
        return f"-> {urlsplit(arrived).path}?{urlsplit(arrived).query}; '{name_text}' ticked under 'Who was there?'"


# --------------------------------------------------------------------------- main


def run_mode(mock: bool, args: Any, out: Path, seams: SeamLog) -> None:
    from playwright.sync_api import sync_playwright

    mode = "mock" if mock else "real"
    print(f"\n######## {mode} mode", flush=True)
    try:
        with run_stack(
            mock=mock,
            web_port=args.web_port,
            api_port=None if mock else args.api_port,
            out_dir=out / mode,
        ) as stack, sync_playwright() as pw:
            browser = pw.chromium.launch(headless=not args.headed)
            try:
                SettingsSweep(stack, browser, seams, mode).run()
            finally:
                browser.close()
    except StackError as exc:
        seams.record(f"[{mode}] start the stack", False, first_lines(str(exc), 12), "pages")
    except Exception as exc:  # noqa: BLE001 - anything outside a step is still a seam
        seams.record(f"[{mode}] sweep", False,
                     first_lines(f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}", 12), "pages")


def main(argv: list[str] | None = None) -> int:
    args = sweep_args(DESCRIPTION, argv)
    require_chromium()
    out = sweep_out_dir(args.out, prefix="stitch-sweep-settings-")
    prepare_out(out)
    print(f"Results: {out}", flush=True)
    seams = SeamLog()
    modes = sweep_modes(args.mode)
    for i, mock in enumerate(modes):
        if i and not (wait_port_free(args.web_port) and wait_port_free(args.api_port)):
            seams.record("[both] ports free for the second mode", False,
                         f"port {args.web_port} or {args.api_port} still busy after the first mode", "pages")
            break
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
    return code


if __name__ == "__main__":
    raise SystemExit(main())
