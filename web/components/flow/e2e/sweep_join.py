"""Browser sweep of /join/[code], in mock mode and against the offline API.

From api/ (it uses the API's uv environment, which has Playwright):

    cd api && uv run python ../web/components/flow/e2e/sweep_join.py [--mode mock|real|both]
        [--headed] [--out DIR] [--api-port N] [--web-port N]

It walks every state of the join page: the loading sheet, the invitation signed out, sign up
and sign in from the invite (both come back to /join/<code>), already a member, claiming an
unclaimed person, a person someone else claimed first (409, then the page looks again),
adding yourself with relationships (picking, the parent limit, none picked), validation
errors, server errors (500 and a dropped connection), a dead link (unknown, revoked, expired;
signed in and signed out) and the welcome, whose links must lead to / and /add?family=<fid>.
Two neighbours too: a member's "Not you? Sign out" on the invite lands back on it signed out,
and /families/new's "Were you invited?" field opens a pasted invite link and refuses garbage.

Real mode starts `python -m seed.serve` (in-memory database) and makes its invites and
people through the API as the demo admin, david@demo.stitch. Mock mode
(NEXT_PUBLIC_API_MOCK=1) uses the mock's open invite (DEMO_INVITE_CODE, "riveras-demo") and
its accounts (david@example.com / ana@example.com, password stitch-demo). Server errors are
made with page.route in real mode, and with the mock's fault injection in mock mode
(window.__stitchMock.inject / window.__stitchMockFaults, on main since #194; a mock without it
fails those steps). The one recorded skip is a real limit: in mock mode a claim made in another
browser cannot reach an open page (each page load has its own mock state), so the 409 race is
real mode only.

After "Not you? Sign out", the sign-out step follows "I already have one" to /login: the
signed-in hint must be gone, so the form shows at once and the page never asks GET /me.

Every check is one seam in seams.json with a screenshot of the state at 1280x900 and at
390x844. Exit 1 when a 'pages' or 'api' seam failed, else 0. Without Chromium it prints the
SKIP line and exits 0.
"""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True  # keep __pycache__ out of web/

import argparse  # noqa: E402
import json  # noqa: E402
import re  # noqa: E402
import shutil  # noqa: E402
import tempfile  # noqa: E402
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
    PageWatch,
    SeamLog,
    Stack,
    StackError,
    require_chromium,
    run_stack,
    shot,
    shots_taken,
    wait_port_free,
)

DESKTOP = {"width": 1280, "height": 900}
MOBILE = {"width": 390, "height": 844}
TIMEOUT_MS = 30_000
NAV_TIMEOUT_MS = 120_000  # the first visit to a route waits for `next dev` to compile it
PASSWORD = "stitch-e2e-password"

# Real mode: the offline API's demo admin (api/seed/README.md).
REAL_ADMIN = ("david@demo.stitch", "stitch-demo")
# Mock mode: web/components/flow/mock/demoSeed.ts.
MOCK_INVITE_CODE = "riveras-demo"
MOCK_ADMIN = ("david@example.com", "stitch-demo")
MOCK_STORAGE_KEY = "stitch.mock.v1"

SERVER_TROUBLE = "Something went wrong on our side. Please try again in a moment."
NETWORK_ERROR = "We can't reach Stitch right now. Check your connection and try again."
OPENING = "Opening your invitation"

# Each choice card's border colour, by the first line of its text.
CARD_BORDERS_JS = """els => els.map(e => e.closest('label')).filter(Boolean)
  .map(l => [l.innerText.trim().split('\\n')[0], getComputedStyle(l).borderTopColor])"""

# components/flow/session.ts: the "probably signed in" hint the log in page reads.
SIGNED_IN_KEY = "stitch.signedIn"
SIGNED_IN_HINT_JS = f"""() => {{
  try {{ return localStorage.getItem({json.dumps(SIGNED_IN_KEY)}); }} catch {{ return 'unreadable'; }}
}}"""

# Flow pages own these routes; anything else on the web app is Ethan's (/, people, memories...).
FLOW_PATHS = re.compile(r"^/(join|login|signup|add|families)(/|$)")

# Chromium logs each failed fetch as a console error. These lines are expected when the
# step meant to get that answer (a 404 for a dead link, a 409 for a taken person).
FAILED_RESOURCE_STATUS = re.compile(r"Failed to load resource: the server responded with a status of (\d{3})")
FAILED_RESOURCE_NET = re.compile(r"Failed to load resource: net::ERR_FAILED")


# --------------------------------------------------------------------------- pure helpers


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Browser sweep of the join-by-invite page.")
    p.add_argument("--mode", choices=("mock", "real", "both"), default="both")
    p.add_argument("--headed", action="store_true", help="show the browser")
    p.add_argument("--out", type=Path, default=None, help="results folder (default: a new temp folder)")
    p.add_argument("--api-port", type=int, default=None)
    p.add_argument("--web-port", type=int, default=None)
    return p.parse_args(argv)


def modes_for(choice: str) -> list[str]:
    return ["mock", "real"] if choice == "both" else [choice]


def exit_code(seams: SeamLog) -> int:
    """1 when the flow pages or the API failed a check; Ethan's seams are reported only."""
    return 1 if seams.failed("pages") or seams.failed("api") else 0


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
"""Label suffix of a page opened with JavaScript off (its script requests are blocked as 'csp')."""


def is_expected(event: dict[str, str], allow: Allow) -> bool:
    """True when a PageWatch event is what the step set out to cause."""
    kind, text = event.get("kind", ""), event.get("text", "")
    if kind == "requestfailed" and event.get("label", "").endswith(NO_JS_LABEL) and text.endswith(" csp"):
        return True  # a script tag the browser did not fetch because scripts are off
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
    return False


def first_lines(text: str, n: int = 6) -> str:
    lines = [ln for ln in str(text).splitlines() if ln.strip()]
    out = "\n".join(lines[:n])
    return out if len(lines) <= n else out + "\n..."


def cors_headers(origin: str) -> dict[str, str]:
    """What a faked API answer needs for the browser to hand it to the page."""
    return {
        "Access-Control-Allow-Origin": origin,
        "Access-Control-Allow-Credentials": "true",
        "Content-Type": "application/json",
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


class Seam(Exception):
    """A failure whose fix belongs to someone other than the step's owner."""

    def __init__(self, owner: str, message: str) -> None:
        super().__init__(message)
        self.owner = owner


class Skip(Exception):
    """The check cannot run in this mode; recorded as an ok seam with the reason."""


# --------------------------------------------------------------------------- the sweep


class JoinSweep:
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
        self.tag = uuid.uuid4().hex[:6]

        # Filled in by setup.
        self.code = MOCK_INVITE_CODE
        self.fid = ""
        self.family = ""
        self.revoked_code = ""
        self.claimable = ""  # a name to claim
        self.contested = ""  # a name someone else claims first (real mode)
        self.contested_id = ""
        self.faults_supported = False
        self.admin_ctx: Any = None
        self.admin_page: Any = None
        # Pages handed from one step to the next.
        self.joiner: Any = None  # signed up from the invite; validation, then the claim
        self.joiner_name = ""
        self.last_email = ""  # the account signed_in_joiner() made last
        self.claimed = ""
        self.signin_page: Any = None
        self.member_page: Any = None  # signed in, already in the family
        self.rel_page: Any = None
        self.err_rel_page: Any = None
        self.mock_dead: tuple[str, str] | None = None  # mock: (revoked, expired) codes
        self.current: Any = None  # the page a failing step screenshots

    # ------------------------------------------------------------------ plumbing

    def new_page(self, label: str, *, js: bool = True, init_script: str | None = None) -> tuple[Any, Any]:
        ctx = self.browser.new_context(viewport=DESKTOP, java_script_enabled=js)
        ctx.set_default_timeout(TIMEOUT_MS)
        ctx.set_default_navigation_timeout(NAV_TIMEOUT_MS)
        if init_script:
            ctx.add_init_script(init_script)
        page = ctx.new_page()
        self.watch.attach(page, f"{self.mode}:{label}")
        self.contexts.append(ctx)
        self.current = page
        return ctx, page

    def use(self, page: Any) -> Any:
        """A page handed over from an earlier step; it is the one a failure screenshots."""
        if page is not None:
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
        self.browser_problems(label, mark, allow)

    def failure_shots(self, key: str, ctx_mark: int) -> None:
        """Screenshot the pages this step opened, and the page it took over from an earlier one."""
        pages = [p for ctx in self.contexts[ctx_mark:] for p in ctx.pages]
        if self.current is not None and self.current not in pages:
            pages.append(self.current)
        for page in pages:
            try:
                shot(page, self.stack, f"{key}-failed")
            except Exception:  # noqa: BLE001
                pass

    def browser_problems(self, label: str, mark: int, allow: Allow) -> None:
        groups: dict[str, list[str]] = {}
        for problem, event in zip(self.watch.since(mark), self.watch.events_since(mark), strict=True):
            if is_expected(event, allow):
                continue
            owner = "api" if event.get("kind") == "http" else owner_for_url(event.get("pageUrl", ""))
            groups.setdefault(owner, []).append(problem)
        for owner, problems in groups.items():
            self.seams.record(f"{label}: browser problems", False, "\n".join(problems), owner)

    def snap(self, page: Any, name: str) -> None:
        """The state at 1280x900 and at 390x844; a page that scrolls sideways on a phone fails."""
        page.wait_for_timeout(150)  # let focus moves and transitions settle
        shot(page, self.stack, f"{name}-desktop")
        page.set_viewport_size(MOBILE)
        try:
            page.wait_for_timeout(200)
            over = page.evaluate("() => document.documentElement.scrollWidth - window.innerWidth")
            shot(page, self.stack, f"{name}-mobile")
        finally:
            page.set_viewport_size(DESKTOP)
        if isinstance(over, (int, float)) and over > 1:
            self.layout.append(f"{name}: the page scrolls sideways by {over}px at 390px wide")

    def goto(self, page: Any, path: str) -> None:
        page.goto(f"{self.web}{path}")

    @staticmethod
    def vis(locator: Any) -> Any:
        """Visible matches only: with cacheComponents a page navigated away from stays mounted, hidden."""
        return locator.filter(visible=True)

    def check_mark(self, page: Any) -> None:
        """The shared flow frame: the "Stitch" mark top left, linking home."""
        mark = page.get_by_role("navigation", name="Stitch").get_by_role("link", name="Stitch", exact=True)
        self.expect(mark).to_be_visible()
        self.expect(mark).to_have_attribute("href", "/")

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

    def api_signup(self, ctx: Any, name: str) -> str:
        email = f"{re.sub(r'[^a-z]+', '-', name.lower()).strip('-')}-{uuid.uuid4().hex[:6]}@example.com"
        self.api_call(ctx, "POST", "/auth/signup", {"email": email, "password": PASSWORD, "name": name})
        return email

    def person_in_family(self, ctx: Any) -> str:
        me = self.api_call(ctx, "GET", "/me")
        for fam in me.get("families", []):
            if fam.get("id") == self.fid:
                return fam.get("personId") or ""
        raise Seam("api", f"GET /me lists no family {self.fid}: {me.get('families')}")

    # ------------------------------------------------------------------ UI helpers

    def ui_signup(self, page: Any, name: str) -> str:
        email = f"{re.sub(r'[^a-z]+', '-', name.lower()).strip('-')}-{uuid.uuid4().hex[:6]}@example.com"
        self.expect(page.get_by_role("heading", level=1, name="Make your account")).to_be_visible()
        self.vis(page.get_by_label("Your name")).fill(name)
        self.vis(page.get_by_label("Email")).fill(email)
        self.vis(page.get_by_label("Password", exact=True)).fill(PASSWORD)
        page.get_by_role("button", name="Create account").click()
        return email

    def ui_login(self, page: Any, email: str, password: str) -> None:
        self.expect(page.get_by_role("heading", level=1, name="Welcome back")).to_be_visible()
        self.vis(page.get_by_label("Email")).fill(email)
        self.vis(page.get_by_label("Password", exact=True)).fill(password)
        page.get_by_role("button", name="Log in", exact=True).click()

    def join_url_re(self) -> re.Pattern[str]:
        return re.compile(rf"^{re.escape(self.web)}/join/{re.escape(quote(self.code, safe=''))}$")

    def signed_in_joiner(self, label: str, name: str) -> tuple[Any, Any]:
        """A new account, signed in, on the invite's "which one is you?" form."""
        ctx, page = self.new_page(label)
        if self.real:
            self.last_email = self.api_signup(ctx, name)
            self.goto(page, f"/join/{self.code}")
        else:
            self.goto(page, f"/signup?next={quote(f'/join/{self.code}', safe='')}")
            self.last_email = self.ui_signup(page, name)
            self.expect(page).to_have_url(self.join_url_re())
        self.expect(page.get_by_role("button", name=re.compile(r"^Join"))).to_be_visible()
        return ctx, page

    def choose_group(self, page: Any) -> Any:
        return page.get_by_role("group", name=re.compile("Which one is you"))

    def add_self_radio(self, page: Any) -> Any:
        return page.get_by_role("radio", name=re.compile("not on the tree yet"))

    def pick_add_self(self, page: Any) -> None:
        radio = self.add_self_radio(page)
        if radio.count() > 0:
            radio.check()
            self.expect(radio).to_be_checked()

    def claim_radios(self, page: Any) -> list[str]:
        """The accessible names of the people on offer, the add-yourself choice left out."""
        names = self.choose_group(page).get_by_role("radio").evaluate_all(
            "els => els.map(e => (e.closest('label')?.innerText || '').trim())"
        )
        return [n for n in names if "not on the tree yet" not in n]

    def tick(self, scope: Any, name: str) -> None:
        """Tick a RelationshipPicker chip: an sr-only checkbox under its label text."""
        box = self.vis(scope.get_by_label(name, exact=True))
        if box.is_checked():
            return
        text = self.vis(scope.get_by_text(name, exact=True))
        text.scroll_into_view_if_needed()
        text.click()
        self.expect(box).to_be_checked()

    def chip_names(self, group: Any) -> list[str]:
        return group.get_by_role("checkbox").evaluate_all(
            "els => els.map(e => (e.closest('label')?.innerText || '').trim())"
        )

    def check_welcome(self, page: Any, who: str) -> str:
        """The welcome after joining: its two links, by href."""
        expect = self.expect
        expect(page.get_by_role("heading", level=1, name=re.compile(r"^Welcome to"))).to_be_visible()
        expect(self.vis(page.get_by_text(who, exact=True))).to_be_visible()
        self.check_mark(page)
        add = page.get_by_role("link", name="Add your first memory")
        home = page.get_by_role("link", name="Open the family tree")
        expect(add).to_have_attribute("href", f"/add?family={quote(self.fid, safe='')}")
        expect(home).to_have_attribute("href", "/")
        return f"welcome links: Add your first memory -> {add.get_attribute('href')}, Open the family tree -> /"

    def alert_texts(self, page: Any) -> list[str]:
        texts = page.get_by_role("alert").evaluate_all(
            "els => els.filter(e => { const h = e.getRootNode().host;"
            " return !(h && h.localName === 'next-route-announcer'); }).map(e => e.innerText)"
        )
        return [t.strip() for t in texts if t and t.strip()]

    def expect_alert(self, page: Any, text: str) -> None:
        self.expect(self.vis(page.get_by_role("alert").filter(has_text=text))).to_be_visible()

    # ------------------------------------------------------------------ fault injection

    def fail_route(self, page: Any, path_re: str, method: str, *, status: int | None = None,
                   body: dict[str, Any] | None = None, abort: bool = False, times: int | None = None) -> None:
        """Real mode: answer `method path_re` on the API with `status` (or drop it)."""
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
                route.fulfill(status=status or 500, headers=cors_headers(self.web),
                              body=json.dumps(body if body is not None else {"detail": "Internal Server Error"}))

        page.route(pattern, handler)

    def mock_faults_script(self, faults: list[dict[str, Any]]) -> str:
        return f"window.__stitchMockFaults = {json.dumps(faults)};"

    def need_mock_faults(self) -> None:
        if not self.faults_supported:
            raise AssertionError(
                "the mock backend has no window.__stitchMock.inject (on main since #194), so this step "
                "cannot fail a request in mock mode"
            )

    # ------------------------------------------------------------------ the walk

    def run(self) -> None:
        try:
            self.step("warmup", "compile the routes the sweep visits", self.s_warmup)
            self.step("setup", "set up the invite", self.s_setup, owner="api" if self.real else "pages")
            need = ("setup",)
            self.step("loading", "loading: the sheet while the invite loads", self.s_loading, needs=need)
            self.step("preview", "signed out: the invitation", self.s_preview, needs=need)
            self.step("signup", "sign up from the invite comes back to /join", self.s_signup_from_invite, needs=need)
            self.step("validation", "validation: nothing picked, no name, a future year",
                      self.s_validation, needs=("signup",))
            self.step("claim", "claim an unclaimed person, then the welcome", self.s_claim, needs=("signup",))
            self.step("welcome-add", "welcome: 'Add your first memory' opens /add?family=<fid>",
                      self.s_welcome_add, needs=("claim",))
            self.step("signin", "sign in from the invite comes back to /join", self.s_signin_from_invite,
                      allow=Allow(statuses=(401,)), needs=need)
            self.step("signout", "'Not you? Sign out' comes back to the invitation", self.s_sign_out,
                      allow=Allow(statuses=(401,)), needs=("signin",))
            self.step("member", "already a member", self.s_already_member, needs=need)
            self.step("member-signout", "already a member: 'Not you? Sign out' comes back to the invitation",
                      self.s_member_sign_out, allow=Allow(statuses=(401,)), needs=need)
            self.step("paste-invite", "start a family: a pasted invite link opens it; garbage is refused",
                      self.s_paste_invite, allow=Allow(statuses=(401,)), needs=need)
            self.step("taken", "a person someone else just claimed (409, look again)", self.s_taken,
                      allow=Allow(statuses=(409,)), needs=need)
            self.step("rel", "add yourself: picking relationships, the parent limit", self.s_add_self_relationships,
                      needs=need)
            self.step("welcome-home", "welcome: 'Open the family tree' opens /", self.s_welcome_home,
                      needs=("rel",))
            self.step("rel-none", "add yourself: saving with no relatives picked", self.s_add_self_none, needs=need)
            gone = Allow(statuses=(404, 410))
            self.step("gone-out", "dead link signed out: the card and 'Log in'", self.s_gone_signed_out,
                      allow=gone)
            self.step("gone-member", "dead link signed in: 'Open your family tree'", self.s_gone_member,
                      allow=gone, needs=("member",))
            self.step("gone-new", "dead link signed in, no family yet: 'Start your own family'",
                      self.s_gone_no_family, allow=gone)
            self.step("gone-revoked", "dead link: a revoked invite", self.s_gone_revoked, allow=gone,
                      needs=("member",))
            self.step("gone-expired", "dead link: an expired invite", self.s_gone_expired, allow=gone,
                      needs=("member",))
            fault = Allow(faults=True)
            self.step("err-preview-500", "server error: the invite answers 500, then Try again",
                      self.s_err_preview_500, allow=fault, needs=need)
            self.step("err-preview-net", "server error: the invite's connection drops",
                      self.s_err_preview_abort, allow=fault, needs=need)
            self.step("err-me-500", "server error: GET /me answers 500", self.s_err_me_500, allow=fault,
                      needs=need)
            self.step("err-join", "server error: the join answers 500, then drops, then goes through",
                      self.s_err_join, allow=fault, needs=need)
            self.step("err-rel", "server error: saving relationships answers 500, then Skip",
                      self.s_err_relationships, allow=fault, needs=need)
        finally:
            self.close_all()

    # ------------------------------------------------------------------ steps

    def s_warmup(self) -> str:
        """Visit each route once so `next dev` compiles it before the timed checks."""
        ctx, page = self.new_page("warmup")
        mark = len(self.watch.problems)
        took = []
        for path in ("/join/warmup-code", "/signup", "/login", "/add", "/families/new", "/"):
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
        ctx.close()
        self.contexts.remove(ctx)
        return ", ".join(took)

    def s_setup(self) -> str:
        if not self.real:
            ctx, page = self.new_page("setup")
            self.goto(page, f"/join/{self.code}")
            self.expect(page.get_by_role("heading", level=1, name=re.compile("invited to"))).to_be_visible()
            self.faults_supported = bool(
                page.evaluate("() => typeof window.__stitchMock?.inject === 'function'")
            )
            ctx.close()
            self.contexts.remove(ctx)
            return (f"mock invite {self.code}; fault injection "
                    f"{'available' if self.faults_supported else 'MISSING (the fault steps will fail)'}")
        ctx, page = self.new_page("admin")
        self.api_call(ctx, "POST", "/auth/login", {"email": REAL_ADMIN[0], "password": REAL_ADMIN[1]})
        me = self.api_call(ctx, "GET", "/me")
        admin_of = [f for f in me.get("families", []) if f.get("role") == "admin"]
        if not admin_of:
            raise Seam("api", f"{REAL_ADMIN[0]} is admin of no family: {me.get('families')}")
        self.fid, self.family = admin_of[0]["id"], admin_of[0]["name"]
        self.code = self.api_call(ctx, "POST", f"/families/{self.fid}/invites")["code"]
        self.revoked_code = self.api_call(ctx, "POST", f"/families/{self.fid}/invites")["code"]
        self.api_call(ctx, "DELETE", f"/families/{self.fid}/invites/{self.revoked_code}")
        self.claimable = f"Cora Vale {self.tag}"
        self.contested = f"Theo Vale {self.tag}"
        self.api_call(ctx, "POST", f"/families/{self.fid}/people", {"name": self.claimable, "born": 1991})
        self.contested_id = self.api_call(
            ctx, "POST", f"/families/{self.fid}/people", {"name": self.contested, "born": 1993}
        )["id"]
        self.admin_ctx, self.admin_page = ctx, page
        return (f"family {self.fid} '{self.family}', invite {self.code}, revoked {self.revoked_code}, "
                f"unclaimed '{self.claimable}' and '{self.contested}'")

    def s_loading(self) -> str:
        notes = []
        # What arrives before any script runs: the static shell is the loading sheet.
        ctx, page = self.new_page("no-js", js=False)
        self.goto(page, f"/join/{self.code}")
        self.expect(page.get_by_role("status").filter(has_text=OPENING)).to_be_visible()
        self.check_mark(page)
        self.snap(page, "loading-static-shell")
        notes.append("static shell (JS off) shows 'Opening your invitation…' in the Stitch frame")

        if self.real:
            # The client-side wait: hold GET /invites/{code} until the sheet is on screen.
            ctx, page = self.new_page("loading")
            held: list[Any] = []
            pattern = re.compile(rf"^{re.escape(self.api)}/invites/{re.escape(self.code)}$")

            def hold(route: Any) -> None:
                if route.request.method == "GET":
                    held.append(route)
                else:
                    route.fallback()

            page.route(pattern, hold)
            self.goto(page, f"/join/{self.code}")
            self.expect(page.get_by_role("status").filter(has_text=OPENING)).to_be_visible()
            page.wait_for_timeout(500)
            self.expect(page.get_by_role("status").filter(has_text=OPENING)).to_be_visible()
            self.snap(page, "loading-held")
            if not held:
                raise AssertionError("the page never asked for the invite")
            # Let the held requests through first; unrouting with routes still pending
            # hands them back to Playwright, which then refuses a later continue.
            for route in held:
                route.continue_()
            page.unroute(pattern)
            self.expect(page.get_by_role("heading", level=1, name=re.compile("invited to"))).to_be_visible()
            notes.append("with the invite held, the sheet stays until it answers, then the invitation shows")
        else:
            self.need_mock_faults()
            ctx, page = self.new_page(
                "loading",
                init_script=self.mock_faults_script(
                    # Every try (React runs the load effect twice in development).
                    [{"method": "GET", "path": "/invites/{code}", "delayMs": 4000}]
                ),
            )
            self.goto(page, f"/join/{self.code}")
            self.expect(page.get_by_role("status").filter(has_text=OPENING)).to_be_visible()
            self.snap(page, "loading-delayed")
            self.expect(page.get_by_role("heading", level=1, name=re.compile("invited to"))).to_be_visible()
            notes.append("with a 4 s mock delay, the sheet shows, then the invitation")
        return "; ".join(notes)

    def s_preview(self) -> str:
        expect = self.expect
        ctx, page = self.new_page("visitor")
        self.goto(page, f"/join/{self.code}")
        expect(page.get_by_role("heading", level=1, name=re.compile("invited to"))).to_be_visible()
        self.check_mark(page)
        back = quote(f"/join/{self.code}", safe="")
        create = page.get_by_role("link", name="Create an account")
        have = page.get_by_role("link", name="I already have one")
        expect(create).to_have_attribute("href", f"/signup?next={back}")
        expect(have).to_have_attribute("href", f"/login?next={back}")
        waiting = page.get_by_role("list", name=re.compile("waiting for their owner"))
        names = waiting.get_by_role("listitem").all_inner_texts() if waiting.count() else []
        if self.real and not any(self.claimable in n for n in names):
            raise AssertionError(f"'{self.claimable}' is not among the people waiting: {names}")
        self.snap(page, "preview-signed-out")
        return f"heading '{page.get_by_role('heading', level=1).inner_text()}'; waiting: {', '.join(names) or 'none'}"

    def s_signup_from_invite(self) -> str:
        expect = self.expect
        ctx, page = self.new_page("signup")
        self.goto(page, f"/join/{self.code}")
        page.get_by_role("link", name="Create an account").click()
        expect(page).to_have_url(re.compile(r"/signup\?next=%2Fjoin%2F"))
        expect(self.vis(page.get_by_text("Make an account to join your family."))).to_be_visible()
        self.snap(page, "signup-from-invite")
        name = f"Jo Signup {self.tag}"
        self.ui_signup(page, name)
        expect(page).to_have_url(self.join_url_re())
        expect(self.choose_group(page)).to_be_visible()
        expect(self.vis(page.get_by_text(re.compile(r"Signed in as")))).to_be_visible()
        self.check_mark(page)
        self.snap(page, "choose")
        if not self.real:
            self.fid = self.mock_family_id(page)
        # The optional field's accessible name has a real space before "(optional)".
        self.pick_add_self(page)
        expect(self.vis(page.get_by_label("Year you were born (optional)", exact=True))).to_be_visible()
        expect(self.vis(page.get_by_label("Your name"))).to_have_value(name)
        self.snap(page, "choose-add-yourself")
        self.joiner = page
        self.joiner_name = name
        return f"signed up as '{name}', back at {page.url}; 'Year you were born (optional)' labelled"

    def mock_family_id(self, page: Any) -> str:
        fid = page.evaluate(
            """([key, code]) => {
                const s = JSON.parse(localStorage.getItem(key) || 'null');
                const f = s && s.families.find(f => f.invites.some(i => i.code === code));
                return f ? f.id : '';
            }""",
            [MOCK_STORAGE_KEY, self.code],
        )
        if not fid:
            raise AssertionError(f"no family with invite {self.code} in the mock's saved state")
        return fid

    def s_validation(self) -> str:
        expect = self.expect
        page = self.use(self.joiner)
        # Back to "pick someone", with nobody picked.
        group = self.choose_group(page)
        if group.count() == 0:
            raise Skip("nobody is left to claim, so there is no choice to leave empty")
        page.reload()
        expect(self.choose_group(page)).to_be_visible()
        radios = self.choose_group(page).get_by_role("radio")
        borders = radios.evaluate_all(CARD_BORDERS_JS)
        page.get_by_role("button", name="Join the family").click()
        expect(self.vis(page.get_by_text("Pick which one is you."))).to_be_visible()
        focused = page.evaluate("() => document.activeElement?.type || ''")
        if focused != "radio":
            raise AssertionError(f"focus went to a {focused or 'non-input'}, not the choices")
        # The error marks the group once (a rule down its left edge); no card changes colour.
        page.wait_for_timeout(250)  # past the border transition
        changed = [(a[0], a[1], b[1]) for a, b in zip(borders, radios.evaluate_all(CARD_BORDERS_JS), strict=True)
                   if a != b]
        if changed:
            raise AssertionError(f"choice cards changed border with the error (card, before, after): {changed}")
        self.snap(page, "validation-nothing-picked")

        self.pick_add_self(page)
        name = self.vis(page.get_by_label("Your name"))
        name.fill("")
        self.vis(page.get_by_label("Year you were born (optional)", exact=True)).fill("3000")
        page.get_by_role("button", name="Join the family").click()
        expect(self.vis(page.get_by_text("Enter your name."))).to_be_visible()
        expect(self.vis(page.get_by_text("That year hasn't happened yet."))).to_be_visible()
        expect(name).to_be_focused()
        expect(name).to_have_attribute("aria-invalid", "true")
        year = self.vis(page.get_by_label("Year you were born (optional)", exact=True))
        expect(year).to_have_attribute("aria-invalid", "true")
        self.snap(page, "validation-add-yourself")
        return "errors: 'Pick which one is you.'; 'Enter your name.'; 'That year hasn't happened yet.'"

    def s_claim(self) -> str:
        expect = self.expect
        page = self.use(self.joiner)
        page.reload()
        expect(self.choose_group(page)).to_be_visible()
        offered = self.claim_radios(page)
        if not offered:
            raise AssertionError("nobody to claim on the form")
        # A choice reads "Rosa born 1938" (or the same over two lines); the name is before "born".
        target = self.claimable if self.real else re.sub(r"\s+born\s+\d+$", "", offered[0].splitlines()[0]).strip()
        radio = page.get_by_role("radio", name=re.compile(re.escape(target)))
        radio.check()
        expect(radio).to_be_checked()
        button = page.get_by_role("button", name=f"Join as {target}")
        expect(button).to_be_visible()
        self.snap(page, "claim-picked")
        button.click()
        detail = self.check_welcome(page, target)
        self.snap(page, "welcome-after-claim")
        if self.real:
            pid = self.person_in_family(page.context)
            tree = self.api_call(page.context, "GET", f"/families/{self.fid}/tree")
            mine = next((p for p in tree.get("people", []) if p.get("id") == pid), None)
            if not mine or mine.get("name") != target:
                raise Seam("api", f"after claiming '{target}' the account's person is {mine}")
        self.claimed = target
        return f"claimed '{target}'; {detail}"

    def s_welcome_add(self) -> str:
        page = self.use(self.joiner)
        page.get_by_role("link", name="Add your first memory").click()
        self.expect(page).to_have_url(re.compile(rf"/add\?family={re.escape(quote(self.fid, safe=''))}$"))
        self.expect(page.get_by_role("heading", level=1, name="Add a memory")).to_be_visible()
        self.snap(page, "welcome-add-landed")
        return f"-> {page.url}"

    def s_signin_from_invite(self) -> str:
        expect = self.expect
        # An account that exists but is not in the family yet, signed out.
        if self.real:
            maker, _ = self.new_page("maker")
            email = self.api_signup(maker, f"Sid Signin {self.tag}")
            ctx, page = self.new_page("signin")
            self.goto(page, f"/join/{self.code}")
        else:
            # The mock's accounts live in this browser: make one, then sign out from the invite.
            ctx, page = self.signed_in_joiner("signin", f"Sid Signin {self.tag}")
            email = self.last_email
            expect(self.vis(page.get_by_text(re.compile(rf"Signed in as .*{re.escape(email)}")))).to_be_visible()
            page.get_by_role("button", name="Not you? Sign out").click()
        page.get_by_role("link", name="I already have one").click()
        expect(page).to_have_url(re.compile(r"/login\?next=%2Fjoin%2F"))
        expect(self.vis(page.get_by_text("Log in to join your family."))).to_be_visible()
        self.snap(page, "signin-from-invite")
        self.ui_login(page, email, PASSWORD)
        expect(page).to_have_url(self.join_url_re())
        expect(self.choose_group(page)).to_be_visible()
        self.snap(page, "signin-back-at-invite")
        self.signin_page = page
        return f"logged in as {email}, back at {page.url} (which one is you?)"

    def s_sign_out(self) -> str:
        expect = self.expect
        page = self.use(self.signin_page)
        page.get_by_role("button", name="Not you? Sign out").click()
        expect(page.get_by_role("link", name="Create an account")).to_be_visible()
        expect(page).to_have_url(self.join_url_re())
        self.snap(page, "signed-out-again")
        # The signed-in hint must go with the session: with it left behind, /login would ask
        # GET /me and hold its form back behind a blank sheet until the 401 came.
        hint = page.evaluate(SIGNED_IN_HINT_JS)
        if hint is not None:
            raise AssertionError(f"localStorage {SIGNED_IN_KEY!r} is still {hint!r} after signing out")
        me_url = f"{self.api}/me"
        asked: list[str] = []

        def on_request(req: Any) -> None:
            if req.method == "GET" and req.url.split("?")[0] == me_url:
                asked.append(req.url)

        page.on("request", on_request)
        try:
            started = time.monotonic()
            page.get_by_role("link", name="I already have one").click()
            expect(page).to_have_url(re.compile(r"/login\?next=%2Fjoin%2F"))
            expect(self.vis(page.get_by_label("Email"))).to_be_visible()
            expect(page.get_by_role("button", name="Log in", exact=True)).to_be_enabled()
            took = time.monotonic() - started
            page.wait_for_timeout(1_000)  # room for a late /me, if one were coming
        finally:
            page.remove_listener("request", on_request)
        if asked:
            raise AssertionError(f"/login asked GET /me {len(asked)} time(s) after signing out")
        self.snap(page, "signed-out-login")
        how = "no GET /me" if self.real else "no signed-in check (the mock answers in-page)"
        return (f"signed out, back at the invite; hint cleared; 'I already have one' -> /login form in "
                f"{took:.1f}s, {how}")

    def s_already_member(self) -> str:
        expect = self.expect
        email, password = REAL_ADMIN if self.real else MOCK_ADMIN
        ctx, page = self.new_page("member")
        self.goto(page, f"/login?next={quote(f'/join/{self.code}', safe='')}")
        self.ui_login(page, email, password)
        expect(page).to_have_url(self.join_url_re())
        expect(page.get_by_role("heading", level=1, name=re.compile("already in"))).to_be_visible()
        self.check_mark(page)
        home = page.get_by_role("link", name="Open the family tree")
        settings = page.get_by_role("link", name="Family settings")
        expect(home).to_have_attribute("href", "/")
        expect(settings).to_have_attribute("href", re.compile(r"^/families/[^/]+/settings$"))
        if not self.fid:
            m = re.match(r"^/families/([^/]+)/settings$", settings.get_attribute("href") or "")
            self.fid = m.group(1) if m else ""
        self.snap(page, "already-member")
        self.member_page = page
        return f"'{page.get_by_role('heading', level=1).inner_text()}'; settings -> {settings.get_attribute('href')}"

    def s_member_sign_out(self) -> str:
        """A member opens the invite on a shared laptop; the next person signs them out here."""
        expect = self.expect
        email, password = REAL_ADMIN if self.real else MOCK_ADMIN
        ctx, page = self.new_page("member-signout")
        self.goto(page, f"/login?next={quote(f'/join/{self.code}', safe='')}")
        self.ui_login(page, email, password)
        expect(page).to_have_url(self.join_url_re())
        expect(page.get_by_role("heading", level=1, name=re.compile("already in"))).to_be_visible()
        expect(self.vis(page.get_by_text(re.compile(rf"Signed in as .*{re.escape(email)}")))).to_be_visible()
        button = page.get_by_role("button", name="Not you? Sign out")
        expect(button).to_be_visible()
        self.snap(page, "already-member-sign-out")
        button.click()
        expect(page).to_have_url(self.join_url_re())
        expect(page.get_by_role("heading", level=1, name=re.compile("invited to"))).to_be_visible()
        expect(page.get_by_role("link", name="Create an account")).to_be_visible()
        expect(page.get_by_role("link", name="I already have one")).to_be_visible()
        hint = page.evaluate(SIGNED_IN_HINT_JS)
        if hint is not None:
            raise AssertionError(f"localStorage {SIGNED_IN_KEY!r} is still {hint!r} after signing out")
        self.snap(page, "already-member-signed-out")
        return f"signed in as {email}: 'Not you? Sign out' -> {page.url}, the signed-out invitation"

    def s_paste_invite(self) -> str:
        """/families/new: 'Were you invited?' opens a pasted link here; anything else is refused."""
        expect = self.expect
        error = "That doesn't look like an invite link."
        ctx, page = self.new_page("paste-invite")
        name = f"Pia Paste {self.tag}"
        if self.real:
            email = self.api_signup(ctx, name)
            self.goto(page, "/families/new")
        else:
            self.goto(page, "/signup")
            email = self.ui_signup(page, name)
        expect(page).to_have_url(re.compile(r"/families/new$"))
        expect(page.get_by_role("heading", level=1, name="Start your family")).to_be_visible()
        expect(page.get_by_role("heading", level=2, name="Were you invited?")).to_be_visible()
        expect(self.vis(page.get_by_text(re.compile(rf"Signed in as .*{re.escape(email)}")))).to_be_visible()
        expect(page.get_by_role("button", name="Not you? Sign out")).to_be_visible()
        field = self.vis(page.get_by_label("Paste your invite link"))
        open_button = page.get_by_role("button", name="Open", exact=True)
        self.snap(page, "new-family-invited")

        # Empty, then garbage, then a link to some other page: each one is refused.
        refused = []
        for bad in ("", "this is not an invite", f"{self.web}/families/new"):
            field.fill(bad)
            open_button.click()
            expect(self.vis(page.get_by_text(error, exact=True))).to_be_visible()
            expect(field).to_have_attribute("aria-invalid", "true")
            expect(field).to_be_focused()
            expect(page).to_have_url(re.compile(r"/families/new$"))
            refused.append(repr(bad))
        self.snap(page, "new-family-invite-refused")
        # Typing again clears the error.
        field.fill("h")
        expect(self.vis(page.get_by_text(error, exact=True))).to_have_count(0)

        # A full link from another origin, with a trailing slash, query and hash: only its code is used.
        link = f"https://stitch.example/join/{quote(self.code, safe='')}/?from=text#top"
        field.fill(f"  {link}  ")
        open_button.click()
        expect(page).to_have_url(self.join_url_re())
        expect(page.get_by_role("heading", level=1, name=re.compile("invited to"))).to_be_visible()
        expect(page.get_by_role("button", name=re.compile(r"^Join"))).to_be_visible()
        self.snap(page, "new-family-invite-opened")

        # And "Not you? Sign out" on /families/new goes to /login, signed out.
        self.goto(page, "/families/new")
        expect(page.get_by_role("heading", level=1, name="Start your family")).to_be_visible()
        page.get_by_role("button", name="Not you? Sign out").click()
        expect(page).to_have_url(re.compile(r"/login$"))
        expect(self.vis(page.get_by_label("Email"))).to_be_visible()
        hint = page.evaluate(SIGNED_IN_HINT_JS)
        if hint is not None:
            raise AssertionError(f"localStorage {SIGNED_IN_KEY!r} is still {hint!r} after signing out")
        return (f"refused {', '.join(refused)} with '{error}'; '{link}' opened /join/{self.code}; "
                "'Not you? Sign out' -> /login")

    def s_taken(self) -> str:
        expect = self.expect
        if not self.real:
            raise Skip("the mock keeps its state per page load, so a claim made elsewhere cannot reach "
                       "an open page; real mode covers it")
        ctx, page = self.signed_in_joiner("slow", f"Sky Slow {self.tag}")
        radio = page.get_by_role("radio", name=re.compile(re.escape(self.contested)))
        radio.check()
        # Someone else claims the same person first.
        rival, _ = self.new_page("rival")
        self.api_signup(rival, f"Rae Quick {self.tag}")
        self.api_call(rival, "POST", f"/invites/{self.code}/join", {"personId": self.contested_id})
        page.get_by_role("button", name=f"Join as {self.contested}").click()
        message = f"{self.contested} was just claimed by someone else. Pick another name, or add yourself."
        expect(self.vis(page.get_by_text(message))).to_be_visible()
        expect(page.get_by_role("radio", name=re.compile(re.escape(self.contested)))).to_have_count(0)
        self.snap(page, "taken")
        # Recover: add yourself instead.
        self.pick_add_self(page)
        page.get_by_role("button", name="Join the family").click()
        expect(page.get_by_role("heading", name=re.compile("Where do you fit"))).to_be_visible()
        page.get_by_role("button", name="Skip for now").click()
        self.check_welcome(page, f"Sky Slow {self.tag}")
        return f"'{message}'; the name left the list; joined as someone new instead"

    def s_add_self_relationships(self) -> str:
        expect = self.expect
        name = f"Nia Newcomb {self.tag}"
        ctx, page = self.signed_in_joiner("rel", name)
        self.pick_add_self(page)
        expect(self.vis(page.get_by_label("Your name"))).to_have_value(name)
        self.vis(page.get_by_label("Year you were born (optional)", exact=True)).fill("1995")
        page.get_by_role("button", name="Join the family").click()
        expect(page.get_by_role("heading", name=f"Where do you fit, {name.split()[0]}?")).to_be_visible()
        self.snap(page, "relationships")
        parents = page.get_by_role("group", name=re.compile(r"^Parents"))
        spouses = page.get_by_role("group", name="Married to")
        people = self.chip_names(parents)
        if len(people) < 4:
            raise AssertionError(f"need four people to pick from, the picker offers {people}")
        p1, p2, p3, s1 = people[0], people[1], people[2], people[3]
        self.tick(parents, p1)
        self.tick(parents, p2)
        # A third parent is refused with a note, and nothing changes.
        self.vis(parents.get_by_text(p3, exact=True)).click()
        expect(self.vis(page.get_by_role("status").filter(has_text="up to 2 parents"))).to_be_visible()
        expect(self.vis(parents.get_by_label(p3, exact=True))).not_to_be_checked()
        self.tick(spouses, s1)
        summary = f"Child of {p1} and {p2}, married to {s1}"
        expect(self.vis(page.get_by_text(summary, exact=True))).to_be_visible()
        self.snap(page, "relationships-picked")
        page.get_by_role("button", name="Save and continue").click()
        self.check_welcome(page, name)
        self.snap(page, "welcome-after-add-yourself")
        if self.real:
            pid = self.person_in_family(ctx)
            tree = self.api_call(ctx, "GET", f"/families/{self.fid}/tree")
            by_id = {p["id"]: p for p in tree.get("people", [])}
            me = by_id.get(pid) or {}
            got_parents = sorted(by_id.get(i, {}).get("name", i) for i in me.get("parentIds") or [])
            got_spouses = sorted(by_id.get(i, {}).get("name", i) for i in me.get("spouseIds") or [])
            if got_parents != sorted([p1, p2]) or got_spouses != [s1]:
                raise Seam("api", f"saved parents {got_parents}, spouses {got_spouses}; "
                                  f"picked {[p1, p2]} and {[s1]}")
        self.rel_page = page
        return f"'{summary}'; third parent ({p3}) refused with a note; saved"

    def s_welcome_home(self) -> str:
        page = self.use(self.rel_page)
        page.get_by_role("link", name="Open the family tree").click()
        self.expect(page).to_have_url(re.compile(rf"^{re.escape(self.web)}/?$"))
        page.wait_for_load_state("networkidle")
        self.snap(page, "welcome-home-landed")
        return f"-> {page.url}"

    def s_add_self_none(self) -> str:
        expect = self.expect
        name = f"Ode Nobody {self.tag}"
        ctx, page = self.signed_in_joiner("none", name)
        self.pick_add_self(page)
        page.get_by_role("button", name="Join the family").click()
        expect(page.get_by_role("heading", name=re.compile("Where do you fit"))).to_be_visible()
        expect(self.vis(page.get_by_text("No relatives picked yet", exact=True))).to_be_visible()
        page.get_by_role("button", name="Save and continue").click()
        self.check_welcome(page, name)
        self.snap(page, "welcome-none-picked")
        return "saved with nobody picked; welcome shown"

    # ----- dead links

    def gone_card(self, page: Any, title: str, way_out: str, href: str, name: str) -> None:
        expect = self.expect
        expect(page.get_by_role("heading", level=1, name=title)).to_be_visible()
        self.check_mark(page)
        link = page.get_by_role("link", name=way_out)
        expect(link).to_have_attribute("href", href)
        self.snap(page, name)

    def s_gone_signed_out(self) -> str:
        ctx, page = self.new_page("gone-out")
        self.goto(page, f"/join/no-such-invite-{self.tag}")
        self.gone_card(page, "This invite link isn’t working", "Log in", "/login", "gone-unknown-signed-out")
        page.get_by_role("link", name="Log in").click()
        self.expect(page).to_have_url(re.compile(r"/login$"))
        return "unknown code: 'This invite link isn’t working', 'Log in' -> /login"

    def s_gone_member(self) -> str:
        page = self.use(self.member_page)
        self.goto(page, f"/join/no-such-invite-{self.tag}")
        self.gone_card(page, "This invite link isn’t working", "Open your family tree", "/", "gone-unknown-member")
        return "unknown code, signed in with a family: 'Open your family tree' -> /"

    def s_gone_no_family(self) -> str:
        ctx, page = self.new_page("gone-new")
        if self.real:
            self.api_signup(ctx, f"Una Nofam {self.tag}")
        else:
            self.goto(page, "/signup")
            self.ui_signup(page, f"Una Nofam {self.tag}")
            self.expect(page).to_have_url(re.compile(r"/families/new$"))
        self.goto(page, f"/join/no-such-invite-{self.tag}")
        self.gone_card(page, "This invite link isn’t working", "Start your own family", "/families/new",
                       "gone-unknown-no-family")
        return "unknown code, signed in with no family: 'Start your own family' -> /families/new"

    def mock_add_dead_invites(self, page: Any) -> tuple[str, str]:
        """Mock mode: add a revoked and an expired invite to the saved mock state, then reload."""
        revoked, expired = f"sweep-revoked-{self.tag}", f"sweep-expired-{self.tag}"
        result = page.evaluate(
            """([key, code, revoked, expired]) => {
                const raw = localStorage.getItem(key);
                if (!raw) return 'no saved mock state';
                const s = JSON.parse(raw);
                const f = (s.families || []).find(f => (f.invites || []).some(i => i.code === code));
                if (!f) return 'no family with the demo invite';
                const by = f.members[0].userId, day = 864e5;
                f.invites.push({ code: revoked, createdBy: by, expiresAt: new Date(Date.now() + day).toISOString(), revoked: true });
                f.invites.push({ code: expired, createdBy: by, expiresAt: new Date(Date.now() - day).toISOString(), revoked: false });
                localStorage.setItem(key, JSON.stringify(s));
                return 'ok';
            }""",
            [MOCK_STORAGE_KEY, self.code, revoked, expired],
        )
        if result != "ok":
            raise AssertionError(f"could not add dead invites to the mock state: {result}")
        return revoked, expired

    def s_gone_revoked(self) -> str:
        page = self.use(self.member_page)
        if self.real:
            code = self.revoked_code
            note = "the API answers 404 'Invite not found or expired' for a revoked code, so the card names every cause"
        else:
            self.mock_dead = self.mock_add_dead_invites(page)
            code = self.mock_dead[0]
            note = "the mock answers 404 for a revoked code"
        self.goto(page, f"/join/{code}")
        self.gone_card(page, "This invite link isn’t working", "Open your family tree", "/", "gone-revoked")
        if not self.real:
            # The live invite still works: only the revoked one is dead.
            self.goto(page, f"/join/{self.code}")
            self.expect(page.get_by_role("heading", level=1, name=re.compile("already in"))).to_be_visible()
        return note

    def s_gone_expired(self) -> str:
        page = self.use(self.member_page)
        if self.real:
            # The offline API cannot age an invite, and it answers 404 for an expired one
            # anyway; a 410 checks the page's "expired" copy.
            ctx, page = self.new_page("gone-expired")
            self.fail_route(page, f"/invites/{re.escape(self.code)}", "GET", status=410,
                            body={"detail": "This invite has expired"})
            self.goto(page, f"/join/{self.code}")
            self.gone_card(page, "This invite link has expired", "Log in", "/login", "gone-expired-410")
            return ("410 (faked) -> 'This invite link has expired'; the API itself answers 404 for an "
                    "expired code (known), which shows the 'isn't working' card")
        code = (self.mock_dead or self.mock_add_dead_invites(page))[1]
        self.goto(page, f"/join/{code}")
        self.gone_card(page, "This invite link isn’t working", "Open your family tree", "/", "gone-expired")
        return "the mock answers 404 for an expired code: the 'isn't working' card"

    # ----- server errors

    def load_failure(self, label: str, path: str, *, abort: bool, message: str, name: str) -> str:
        """Open the invite with GET `path` failing on every try, check the retry sheet, then heal it
        and press Try again. Every try fails (no `times`): in development React runs the page's
        load effect twice, and the first answer is thrown away."""
        expect = self.expect
        if self.real:
            ctx, page = self.new_page(label)
            path_re = re.escape(path)
            if abort:
                self.fail_route(page, path_re, "GET", abort=True)
            else:
                self.fail_route(page, path_re, "GET", status=500)
        else:
            self.need_mock_faults()
            mock_path = "/invites/{code}" if path.startswith("/invites/") else path
            fault: dict[str, Any] = {"method": "GET", "path": mock_path}
            fault.update({"network": True} if abort else {"status": 500})
            ctx, page = self.new_page(label, init_script=self.mock_faults_script([fault]))
        self.goto(page, f"/join/{self.code}")
        expect(page.get_by_role("heading", level=1, name="We couldn’t open this invitation")).to_be_visible()
        self.expect_alert(page, message)
        self.check_mark(page)
        self.snap(page, name)
        if self.real:
            page.unroute_all()
        else:
            page.evaluate("() => window.__stitchMock.clearFaults()")
        page.get_by_role("button", name="Try again").click()
        expect(page.get_by_role("heading", level=1, name=re.compile("invited to"))).to_be_visible()
        return f"'{message}'; once it answers, Try again shows the invitation"

    def s_err_preview_500(self) -> str:
        return self.load_failure("err-preview", f"/invites/{self.code}", abort=False,
                                 message=SERVER_TROUBLE, name="error-preview-500")

    def s_err_preview_abort(self) -> str:
        return self.load_failure("err-net", f"/invites/{self.code}", abort=True,
                                 message=NETWORK_ERROR, name="error-preview-network")

    def s_err_me_500(self) -> str:
        return self.load_failure("err-me", "/me", abort=False, message=SERVER_TROUBLE, name="error-me-500")

    def s_err_join(self) -> str:
        expect = self.expect
        name = f"Eli Erring {self.tag}"
        if not self.real:
            self.need_mock_faults()
        ctx, page = self.signed_in_joiner("err-join", name)
        if self.real:
            # The newest route runs first and falls back to the older one once used up:
            # the first try gets the 500, the second the dropped connection.
            self.fail_route(page, f"/invites/{re.escape(self.code)}/join", "POST", abort=True, times=1)
            self.fail_route(page, f"/invites/{re.escape(self.code)}/join", "POST", status=500, times=1)
        else:
            # One fault at a time on the live backend: the 500 now, the dropped connection next.
            page.evaluate(
                """() => {
                    window.__stitchMock.inject({ method: 'POST', path: '/invites/{code}/join', status: 500, times: 1 });
                }"""
            )
        self.pick_add_self(page)
        page.get_by_role("button", name="Join the family").click()
        self.expect_alert(page, SERVER_TROUBLE)
        expect(self.vis(page.get_by_label("Your name"))).to_have_value(name)
        self.snap(page, "error-join-500")
        if self.real:
            page.get_by_role("button", name="Join the family").click()
            self.expect_alert(page, NETWORK_ERROR)
            self.snap(page, "error-join-network")
        else:
            page.evaluate(
                """() => {
                    window.__stitchMock.inject({ method: 'POST', path: '/invites/{code}/join', network: true, times: 1 });
                }"""
            )
            page.get_by_role("button", name="Join the family").click()
            self.expect_alert(page, NETWORK_ERROR)
            self.snap(page, "error-join-network")
        page.get_by_role("button", name="Join the family").click()
        expect(page.get_by_role("heading", name=re.compile("Where do you fit"))).to_be_visible()
        self.err_rel_page = page
        return f"500: '{SERVER_TROUBLE}', dropped: '{NETWORK_ERROR}'; name kept; the third try joins"

    def s_err_relationships(self) -> str:
        expect = self.expect
        if not self.real:
            self.need_mock_faults()
        page = self.use(self.err_rel_page)
        if page is None:
            ctx, page = self.signed_in_joiner("err-rel", f"Ren Retry {self.tag}")
            self.pick_add_self(page)
            page.get_by_role("button", name="Join the family").click()
            expect(page.get_by_role("heading", name=re.compile("Where do you fit"))).to_be_visible()
        if self.real:
            self.fail_route(page, f"/families/{re.escape(quote(self.fid, safe=''))}/people/[^/?]+", "PATCH",
                            status=500)
        else:
            page.evaluate(
                """() => {
                    window.__stitchMock.inject({ method: 'PATCH', path: '/families/{fid}/people/{pid}', status: 500, times: 1 });
                }"""
            )
        parents = page.get_by_role("group", name=re.compile(r"^Parents"))
        self.tick(parents, self.chip_names(parents)[0])
        page.get_by_role("button", name="Save and continue").click()
        self.expect_alert(page, SERVER_TROUBLE)
        expect(self.vis(parents.get_by_role("checkbox").first)).to_be_checked()
        self.snap(page, "error-relationships-500")
        page.get_by_role("button", name="Skip for now").click()
        expect(page.get_by_role("heading", level=1, name=re.compile(r"^Welcome to"))).to_be_visible()
        return f"'{SERVER_TROUBLE}'; the pick is kept; Skip for now reaches the welcome"


# --------------------------------------------------------------------------- main


def run_mode(mode: str, args: argparse.Namespace, out: Path, seams: SeamLog) -> None:
    from playwright.sync_api import sync_playwright

    print(f"\n######## {mode} mode", flush=True)
    api_port = None if mode == "mock" else args.api_port
    # --mode both runs the second stack on the first one's ports: wait for them to free up.
    if not (wait_port_free(args.web_port) and wait_port_free(api_port)):
        seams.record(f"[{mode}] start the stack", False,
                     f"port {args.web_port} or {api_port} is still busy after the previous stack", "pages")
        return
    try:
        with run_stack(
            mock=mode == "mock",
            web_port=args.web_port,
            api_port=api_port,
            out_dir=out / mode,
        ) as stack, sync_playwright() as pw:
            browser = pw.chromium.launch(headless=not args.headed)
            try:
                JoinSweep(stack, browser, seams, mode).run()
            finally:
                browser.close()
    except StackError as exc:
        seams.record(f"[{mode}] start the stack", False, first_lines(str(exc), 12), "pages")
    except Exception as exc:  # noqa: BLE001 - anything outside a step is still a seam
        seams.record(f"[{mode}] sweep", False, first_lines(f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}", 12),
                     "pages")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    require_chromium()
    out = args.out.resolve() if args.out else Path(tempfile.mkdtemp(prefix="stitch-sweep-join-"))
    prepare_out(out)
    print(f"Results: {out}", flush=True)
    seams = SeamLog()
    for mode in modes_for(args.mode):
        run_mode(mode, args, out, seams)
    print()
    path = seams.write(out)
    counts = seams.to_json()["counts"]
    print(f"\nseams: {path}")
    print(f"counts: {counts['total']} checks, {counts['ok']} ok, {counts['failed']} failed "
          f"(by owner: {counts['failedByOwner']})")
    for mode in modes_for(args.mode):
        taken = shots_taken(out / mode)
        print(f"screenshots ({mode}): {out / mode / 'shots'} ({len(taken)} taken)")
    return exit_code(seams)


if __name__ == "__main__":
    raise SystemExit(main())
