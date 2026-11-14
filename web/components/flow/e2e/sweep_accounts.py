"""Browser sweep of the account pages (/login, /signup, /families/new) and the flow form kit.

From api/ (it uses the API's uv environment, which has Playwright):

    cd api && uv run python ../web/components/flow/e2e/sweep_accounts.py \
        [--mode mock|real|both] [--headed] [--out DIR] [--api-port N] [--web-port N]

For each mode (mock: the in-browser mock backend; real: the offline API, seed.serve) and
each viewport (harness.VIEWPORTS), it walks every state of the three pages: the empty
submit, each validation error, a wrong password (401), a taken email (409), server
errors (500, 502, 503) and a dropped connection, the pending state (button disabled, a
double click sends one request), success, and ?next= handling. On the same pages it
checks the form kit: every input has a label, a failed submit moves focus to the first
problem, invalid fields carry aria-invalid and aria-describedby pointing at their
message, Enter submits, the "(optional)" mark reads with a space, and tab order follows
the visual order. It also types into the server-rendered form before React hydrates it
(the text must survive).

Every check is a seam in seams.json with a screenshot under shots/. Exit code 1 when a
'pages' or 'api' seam failed, else 0. Without Playwright's Chromium it prints the SKIP
line and exits 0. In mock mode, server errors and the pending state use the mock
backend's fault injection (window.__stitchMock.inject, on main since #194); a mock
without it fails those checks rather than skipping them.
"""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True  # keep __pycache__ out of web/

import re  # noqa: E402
import traceback  # noqa: E402
import uuid  # noqa: E402
from collections.abc import Callable  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Any  # noqa: E402
from urllib.parse import urlsplit  # noqa: E402

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

DESCRIPTION = "Sweep the account pages (/login, /signup, /families/new) and the flow form kit in Chromium."

TIMEOUT_MS = 45_000  # next dev compiles a route on its first visit
EXPECT_MS = 30_000
PASSWORD = "stitch-sweep-pass"
WRONG_PASSWORD = "not-the-password-1"

# The seeded accounts that already have a family. Real: api/seed (password stitch-demo).
# Mock: components/flow/mock/demoSeed.ts (DEMO_PASSWORD, read from the file below).
REAL_SEED_EMAIL = "david@demo.stitch"
REAL_SEED_PASSWORD = "stitch-demo"
MOCK_SEED_EMAIL = "david@example.com"

# The kit's validation messages (components/flow/validation.ts, accountForms.ts).
EMAIL_EMPTY = "Enter your email."
EMAIL_BAD = "That doesn't look like an email address."
LOGIN_PASSWORD_EMPTY = "Enter your password."
PASSWORD_EMPTY = "Enter a password."
NAME_EMPTY = "Enter your name."
FAMILY_EMPTY = "Give your family a name."
YEAR_FUTURE = "That year hasn't happened yet."
YEAR_EARLY = "Pick a year from 1850 on."
OPTIONAL_LABEL = "The year you were born (optional)"

# The routes Ethan owns; a problem on one of them is his to look at.
ETHAN_PATHS = re.compile(r"^/(people|memories|threads|render)(/|$)|^/$")

SENTINEL_STATUS = 418
SENTINEL_DETAIL = "sweep sentinel: a second request was sent"

# ----------------------------------------------------------------------------- source facts


def _source(path: Path) -> str:
    try:
        return path.read_text()
    except OSError:
        return ""


def password_min() -> int:
    """PASSWORD_MIN from validation.ts (8 if it cannot be read)."""
    m = re.search(r"export const PASSWORD_MIN = (\d+);", _source(WEB_DIR / "components/flow/validation.ts"))
    return int(m.group(1)) if m else 8


def mock_seed_password() -> str:
    """DEMO_PASSWORD from the mock demo seed ("stitch-demo" if it cannot be read)."""
    m = re.search(r'export const DEMO_PASSWORD = "([^"]+)"', _source(WEB_DIR / "components/flow/mock/demoSeed.ts"))
    return m.group(1) if m else "stitch-demo"


def api_messages() -> dict[int, str]:
    """What lib/api.ts shows for a status with no detail (0 is the dropped connection)."""
    src = _source(WEB_DIR / "lib/api.ts")
    out: dict[int, str] = {}
    block = re.search(r"const STATUS_MESSAGES[^{]*\{(.*?)\n\};", src, re.S)
    if block:
        out.update({int(k): v for k, v in re.findall(r'(\d{3}): "([^"]*)"', block.group(1))})
    net = re.search(r'NETWORK_ERROR_MESSAGE =\s*"([^"]*)"', src)
    if net:
        out[0] = net.group(1)
    return out


# ----------------------------------------------------------------------------- small helpers


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
    return "ethan" if ETHAN_PATHS.search(path) else "pages"


def problem_owner(event: dict[str, str]) -> str:
    if event.get("kind") == "http":
        return "api"
    return owner_for_url(event.get("pageUrl", ""))


def slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").lower()[:60] or "step"


def lab(page: Any, text: str) -> Any:
    """The visible control labelled exactly `text` (a hidden, cached page keeps its DOM)."""
    return page.get_by_label(text, exact=True).filter(visible=True)


def visible_text(page: Any, text: str) -> Any:
    return page.get_by_text(text, exact=True).filter(visible=True)


def form_alert(page: Any, text: str | re.Pattern[str] | None = None) -> Any:
    """The form's error box (role=alert), not Next's route announcer."""
    alerts = page.locator("form [role=alert]").filter(visible=True)
    return alerts.filter(has_text=text) if text is not None else alerts


FOCUS_ON_FIRST_PROBLEM_JS = """() => {
  const a = document.activeElement;
  const form = a && a.closest('form');
  if (!form) return false;
  const first = form.querySelector('[aria-invalid="true"]') || form.querySelector('[data-form-error]');
  return first === a;
}"""

FOCUSED_JS = """() => {
  const a = document.activeElement;
  if (!a || a === document.body) return '(nothing)';
  const l = a.labels && a.labels[0];
  if (l) return 'field "' + l.textContent.trim() + '"';
  if (a.hasAttribute('data-form-error')) return 'the form error';
  return a.tagName.toLowerCase() + ' "' + (a.textContent || '').trim().replace(/\\s+/g, ' ').slice(0, 40) + '"';
}"""

TAB_STOP_JS = """() => {
  const a = document.activeElement;
  if (!a || a === document.body) return null;
  const r = a.getBoundingClientRect();
  const l = a.labels && a.labels[0];
  const raw = l ? l.textContent : (a.getAttribute('aria-label') || a.innerText || a.textContent || '');
  const name = raw.trim().replace(/\\s+/g, ' ').slice(0, 40);
  return {
    name, inMain: !!a.closest('main'),
    top: Math.round(r.top + window.scrollY), left: Math.round(r.left + window.scrollX),
    key: a.tagName + '|' + (a.id || name) + '|' + Math.round(r.top + window.scrollY) + '|' + Math.round(r.left),
  };
}"""

DESCRIBED_BY_TEXTS_JS = """el => (el.getAttribute('aria-describedby') || '').split(/\\s+/).filter(Boolean)
  .map(id => { const t = document.getElementById(id); return t ? t.textContent.trim() : null; })"""

ARM_MOCK_FAULT_JS = """fault => {
  const m = window.__stitchMock;
  if (!m || typeof m.inject !== 'function') return false;
  m.inject(fault);
  return true;
}"""

CLEAR_MOCK_FAULTS_JS = """() => { const m = window.__stitchMock; if (m && m.clearFaults) m.clearFaults(); }"""

# A sentinel fault still armed means the request it waited for never came.
SENTINEL_UNUSED_JS = """async path => {
  const m = window.__stitchMock;
  if (!m) return null;
  const r = await m.fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
  m.clearFaults();
  return r.status;
}"""

# Every frame from now on, note any visible password field with text in it (by its page path).
WATCH_PASSWORDS_JS = """() => {
  window.__sweepPasswordsSeen = [];
  const look = () => {
    for (const el of document.querySelectorAll('input[type=password]')) {
      if (el.value && el.offsetParent !== null) window.__sweepPasswordsSeen.push(location.pathname);
    }
    requestAnimationFrame(look);
  };
  requestAnimationFrame(look);
}"""

MOCK_FAMILIES_NAMED_JS ="""async name => {
  const r = await window.__stitchMock.fetch('/me');
  const me = await r.json();
  return (me.families || []).filter(f => f.name === name).length;
}"""


# ----------------------------------------------------------------------------- one person


class Person:
    """One browser context at one window size, with its page, problem watch and request log."""

    def __init__(self, browser: Any, label: str, viewport: tuple[int, int], init_script: str | None = None) -> None:
        width, height = viewport
        self.label = label
        self.ctx = browser.new_context(viewport={"width": width, "height": height})
        self.ctx.set_default_timeout(TIMEOUT_MS)
        if init_script:
            self.ctx.add_init_script(init_script)
        self.page = self.ctx.new_page()
        self.watch = PageWatch()
        self.watch.attach(self.page, label)
        self.requests: list[tuple[str, str]] = []
        self.page.on("request", lambda r: self.requests.append((r.method, r.url)))

    def posts_to(self, url: str) -> int:
        return sum(1 for method, u in self.requests if method == "POST" and u.split("?")[0] == url)

    def close(self) -> None:
        try:
            self.ctx.close()
        except Exception:  # noqa: BLE001
            pass


# ----------------------------------------------------------------------------- the sweep


class AccountSweep:
    def __init__(
        self,
        stack: Stack,
        browser: Any,
        seams: SeamLog,
        view: str,
    ) -> None:
        from playwright.sync_api import expect

        self.expect = expect
        self.stack = stack
        self.browser = browser
        self.seams = seams
        self.mock = stack.mock
        self.mode = "mock" if stack.mock else "real"
        self.view = view
        self.viewport = VIEWPORTS[view]
        self.tag = f"{self.mode}/{view}"
        self.web = stack.web_url
        self.api = stack.api_url or ""
        self.messages = api_messages()
        self.pw_min = password_min()
        self.seed_email = MOCK_SEED_EMAIL if self.mock else REAL_SEED_EMAIL
        self.seed_password = mock_seed_password() if self.mock else REAL_SEED_PASSWORD
        self.people: list[Person] = []
        self.run_tag = uuid.uuid4().hex[:6]
        self.counter = 0

    # ------------------------------------------------------------------ runner

    def person(self, label: str, init_script: str | None = None) -> Person:
        p = Person(self.browser, f"{self.tag} {label}", self.viewport, init_script)
        self.people.append(p)
        return p

    def new_email(self, what: str) -> str:
        self.counter += 1
        return f"sweep-{what}-{self.mode}-{self.view}-{self.run_tag}-{self.counter}@example.com"

    def check(
        self,
        name: str,
        who: Person,
        fn: Callable[[], str | None],
        *,
        owner: str = "pages",
        allow: Callable[[str], bool] | None = None,
    ) -> bool:
        full = f"[{self.tag}] {name}"
        mark = len(who.watch.problems)
        result_owner = owner
        try:
            detail = fn() or ""
            ok = True
            # Every state must fit the window: at 390 px nothing may scroll sideways.
            over = who.page.evaluate("() => document.documentElement.scrollWidth - window.innerWidth")
            if isinstance(over, (int, float)) and over > 1:
                ok, detail = False, f"the page scrolls sideways by {over}px at {self.viewport[0]}px wide"
        except Seam as exc:
            ok, result_owner, detail = False, exc.owner, self.failure_detail(exc, who)
        except Exception as exc:  # noqa: BLE001 - every failure becomes a recorded seam
            ok, detail = False, self.failure_detail(exc, who)
        try:
            shot(who.page, self.stack, f"{self.mode}-{self.view}-{slug(name)}{'' if ok else '-FAILED'}")
        except Exception as exc:  # noqa: BLE001
            print(f"  (screenshot failed: {first_lines(str(exc), 1)})", flush=True)
        self.seams.record(full, ok, detail, result_owner)
        self.record_problems(full, who, mark, allow)
        return ok

    def record_problems(self, name: str, who: Person, mark: int, allow: Callable[[str], bool] | None) -> None:
        groups: dict[str, list[str]] = {}
        for problem, event in zip(who.watch.since(mark), who.watch.events_since(mark), strict=True):
            if expected_noise(problem) or (allow is not None and allow(problem)):
                continue
            groups.setdefault(problem_owner(event), []).append(problem)
        for owner, problems in groups.items():
            self.seams.record(f"{name}: browser problems", False, "\n".join(problems), owner)

    def failure_detail(self, exc: BaseException, who: Person) -> str:
        parts = [first_lines(f"{type(exc).__name__}: {exc}"), f"at {who.page.url}"]
        try:
            texts = form_alert(who.page).all_inner_texts()
            alerts = [t.strip() for t in texts if t.strip()]
        except Exception:  # noqa: BLE001
            alerts = []
        if alerts:
            parts.append("form shows: " + " | ".join(alerts))
        return "\n".join(parts)

    def run(self) -> None:
        print(f"\n=== {self.tag}", flush=True)
        try:
            self.login_checks()
            self.login_success_checks()
            self.signup_and_family_checks()
            self.next_checks()
        finally:
            for p in self.people:
                p.close()

    # ------------------------------------------------------------------ shared checks

    def goto(self, who: Person, path: str, wait: str = "load") -> None:
        who.page.goto(f"{self.web}{path}", wait_until=wait)

    def submit_button(self, page: Any, name: str) -> Any:
        return page.get_by_role("button", name=name, exact=True)

    def path_of(self, page: Any) -> str:
        parts = urlsplit(page.url)
        return parts.path + (f"?{parts.query}" if parts.query else "")

    def expect_field_error(self, page: Any, label: str, message: str) -> None:
        field = lab(page, label)
        self.expect(field).to_have_attribute("aria-invalid", "true")
        texts = field.evaluate(DESCRIBED_BY_TEXTS_JS)
        if message not in texts:
            raise AssertionError(f'"{label}" is aria-describedby {texts}, which does not include "{message}"')
        self.expect(visible_text(page, message)).to_be_visible()

    def expect_valid(self, page: Any, label: str) -> None:
        self.expect(lab(page, label)).not_to_have_attribute("aria-invalid", "true")

    def expect_focus_on_first_problem(self, page: Any, expected: str) -> str:
        try:
            page.wait_for_function(FOCUS_ON_FIRST_PROBLEM_JS, timeout=5_000)
        except Exception as exc:  # noqa: BLE001
            raise AssertionError(
                f"focus is on {page.evaluate(FOCUSED_JS)}, not the first invalid field or the form error"
            ) from exc
        focused = page.evaluate(FOCUSED_JS)
        if focused != expected:
            raise AssertionError(f"focus is on {focused}, expected {expected}")
        return focused

    def render_check(self, who: Person, path: str, heading: str, labels: list[str], submit: str) -> str:
        page = who.page
        self.goto(who, path)
        self.expect(page.get_by_role("heading", level=1, name=heading)).to_be_visible()
        for text in labels:
            control = lab(page, text)
            self.expect(control).to_have_count(1)
            tag = control.evaluate("el => el.tagName.toLowerCase()")
            if tag not in ("input", "textarea", "select"):
                raise AssertionError(f'"{text}" labels a <{tag}>, not a form control')
        # Enabled once hydrated (the account forms keep it disabled until then).
        self.expect(self.submit_button(page, submit)).to_be_enabled()
        unlabelled = page.evaluate(
            """() => [...document.querySelectorAll('main input, main textarea, main select')]
                .filter(el => el.type !== 'hidden' && el.offsetParent !== null && !(el.labels && el.labels.length)
                              && !el.getAttribute('aria-label') && !el.getAttribute('aria-labelledby'))
                .map(el => el.name || el.id)"""
        )
        if unlabelled:
            raise AssertionError(f"inputs with no label: {unlabelled}")
        return f"{len(labels)} labelled fields: {', '.join(labels)}"

    def tab_order_check(self, who: Person, expected: list[str]) -> str:
        """Tab from the top of a freshly loaded page; the stops in <main> must run top to bottom."""
        page = who.page
        stops: list[dict[str, Any]] = []
        seen: set[str] = set()
        for _ in range(30):
            page.keyboard.press("Tab")
            stop = page.evaluate(TAB_STOP_JS)
            if stop is None:
                continue
            if stop["key"] in seen:
                break
            seen.add(stop["key"])
            if not stop["inMain"]:
                if stops:
                    break  # past the page's content (the dev tools button comes last)
                continue
            stops.append(stop)
        names = [s["name"] for s in stops]
        order = " -> ".join(names)
        for a, b in zip(stops, stops[1:]):
            same_row = abs(a["top"] - b["top"]) <= 8
            if (same_row and b["left"] < a["left"]) or (not same_row and b["top"] < a["top"]):
                raise AssertionError(f'"{b["name"]}" comes after "{a["name"]}" in tab order but sits above or left of it: {order}')
        at = [names.index(n) if n in names else -1 for n in expected]
        if -1 in at:
            missing = [n for n, i in zip(expected, at) if i == -1]
            raise AssertionError(f"not reachable by Tab: {missing}; order: {order}")
        if at != sorted(at):
            raise AssertionError(f"fields out of order: {order}")
        return order

    # ------------------------------------------------------------------ faults

    def arm_real(self, who: Person, path: str, *, status: int | None = None, abort: bool = False,
                 hold: list[Any] | None = None) -> Callable[[], None]:
        """Fail (or hold) the next POST to the API's `path` on this page; returns the disarm."""
        url = f"{self.api}{path}"
        left = [1]
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

        who.page.route(url, handler)
        return lambda: who.page.unroute(url, handler)

    def arm_mock(self, who: Person, faults: list[dict[str, Any]]) -> None:
        """Arm mock faults on this page: inject into a live backend, else arm it before it starts."""
        for fault in faults:
            if not who.page.evaluate(ARM_MOCK_FAULT_JS, fault):
                who.page.evaluate(mock_faults_script([fault]))

    def fault_allow(self, path: str) -> Callable[[str], bool]:
        """The browser lines an injected failure on the API's `path` causes by design.

        Real mode only: the mock backend never touches the network, so its faults leave none.
        """
        target = re.compile(re.escape(f"{self.api}{path}") + r"(?![\w/])")

        def allow(problem: str) -> bool:
            if self.mock or not target.search(problem):
                return False
            return (
                " http at " in problem
                or " requestfailed at " in problem
                or ("console.error at " in problem and "Failed to load resource" in problem)
            )

        return allow

    def server_error_checks(
        self,
        who: Person,
        page_name: str,
        api_path: str,
        submit: str,
        fill: Callable[[], None],
        kept: tuple[str, str],
    ) -> None:
        page = who.page
        for status in (500, 502, 503, 0):
            what = f"HTTP {status}" if status else "a dropped connection"

            def run(status: int = status) -> str:
                expected = self.messages.get(status)
                if not expected:
                    raise AssertionError(f"lib/api.ts has no message for status {status}")
                start = page.url
                fill()
                disarm: Callable[[], None] | None = None
                if self.mock:
                    fault: dict[str, Any] = {"method": "POST", "path": api_path, "times": 1}
                    fault.update({"network": True} if status == 0 else {"status": status})
                    self.arm_mock(who, [fault])
                else:
                    disarm = self.arm_real(who, api_path, status=status or None, abort=status == 0)
                try:
                    self.submit_button(page, submit).click()
                    self.expect(form_alert(page, expected)).to_be_visible()
                    focused = self.expect_focus_on_first_problem(page, "the form error")
                    self.expect(self.submit_button(page, submit)).to_be_enabled()
                    label, value = kept
                    self.expect(lab(page, label)).to_have_value(value)
                    if page.url != start:
                        raise AssertionError(f"left the page: {start} -> {page.url}")
                finally:
                    if disarm:
                        disarm()
                    if self.mock:
                        page.evaluate(CLEAR_MOCK_FAULTS_JS)
                return f'"{expected}"; focus on {focused}; {label} kept'

            self.check(f"{page_name}: {what} shows a calm message, nothing lost", who, run,
                       allow=self.fault_allow(api_path))

    def pending_check(
        self,
        who: Person,
        api_path: str,
        submit: str,
        busy_label: str,
        fields: list[str],
        after: Callable[[], str],
    ) -> str:
        """Hold the request, double-click submit, check the busy state and that one request went."""
        page = who.page
        url = f"{self.api}{api_path}"
        held: list[Any] = []
        disarm: Callable[[], None] | None = None
        before = who.posts_to(url)
        if self.mock:
            self.arm_mock(who, [
                {"method": "POST", "path": api_path, "delayMs": 2500, "times": 1},
                {"method": "POST", "path": api_path, "status": SENTINEL_STATUS, "detail": SENTINEL_DETAIL, "times": 1},
            ])
        else:
            disarm = self.arm_real(who, api_path, hold=held)
        try:
            self.submit_button(page, submit).dblclick()
            busy = page.get_by_role("button", name=busy_label, exact=True)
            self.expect(busy).to_be_disabled()
            self.expect(busy).to_have_attribute("aria-busy", "true")
            for label in fields:
                self.expect(lab(page, label)).to_be_disabled()
            page.wait_for_timeout(400)  # room for a second request, if one were coming
            shot(page, self.stack, f"{self.mode}-{self.view}-{slug(api_path)}-pending")
            if not self.mock:
                sent = who.posts_to(url) - before
                if sent != 1:
                    raise AssertionError(f"a double click sent {sent} POST {api_path} requests, not 1")
                for route in held:
                    route.fallback()
                held.clear()
            outcome = after()
            if self.mock:
                status = page.evaluate(SENTINEL_UNUSED_JS, api_path)
                if status != SENTINEL_STATUS:
                    raise AssertionError(
                        f"a double click sent a second POST {api_path} (the sentinel fault was used; probe got {status})"
                    )
        finally:
            for route in held:
                try:
                    route.fallback()
                except Exception:  # noqa: BLE001
                    pass
            if disarm:
                disarm()
        return f"busy \"{busy_label}\", fields disabled, one request; then {outcome}"

    # ------------------------------------------------------------------ /login

    def login_checks(self) -> None:
        who = self.person("login")
        page = who.page
        email, password = lab(page, "Email"), lab(page, "Password")
        log_in = self.submit_button(page, "Log in")
        login_url = f"{self.api}/auth/login"

        self.check("login: renders, every input labelled", who,
                   lambda: self.render_check(who, "/login", "Welcome back", ["Email", "Password"], "Log in"))
        self.check("login: tab order follows the visual order", who,
                   lambda: self.tab_order_check(who, ["Email", "Password", "Log in", "Create an account"]))

        def empty() -> str:
            log_in.click()
            self.expect_field_error(page, "Email", EMAIL_EMPTY)
            self.expect_field_error(page, "Password", LOGIN_PASSWORD_EMPTY)
            return "both fields flagged; focus on " + self.expect_focus_on_first_problem(page, 'field "Email"')

        self.check("login: empty submit flags every field, focus on the first", who, empty)

        def bad_email() -> str:
            email.fill("not-an-email")
            password.fill("whatever-1")
            log_in.click()
            self.expect_field_error(page, "Email", EMAIL_BAD)
            self.expect_valid(page, "Password")
            return "focus on " + self.expect_focus_on_first_problem(page, 'field "Email"')

        self.check("login: a bad email is flagged on its field", who, bad_email)

        def wrong_password() -> str:
            before = who.posts_to(login_url)
            email.fill(self.seed_email)
            password.fill(WRONG_PASSWORD)
            password.press("Enter")
            alert = form_alert(page)
            self.expect(alert).to_be_visible()
            text = alert.inner_text().strip()
            if text in self.messages.values():
                raise AssertionError(f"401 shows a generic message: {text!r}")
            focused = self.expect_focus_on_first_problem(page, "the form error")
            self.expect(email).to_have_value(self.seed_email)
            if not self.mock and who.posts_to(login_url) - before != 1:
                raise AssertionError(f"Enter sent {who.posts_to(login_url) - before} requests, not 1")
            return f'Enter submitted; "{text}"; focus on {focused}; email kept'

        self.check("login: wrong password (401) via Enter", who, wrong_password)

        if self.mock:

            def demo_note() -> str:
                page.get_by_role("button", name="Use the demo account").click()
                self.expect(email).to_have_value(self.seed_email)
                self.expect(password).to_have_value(self.seed_password)
                self.expect(log_in).to_be_focused()
                email.fill(self.seed_email)
                password.fill(WRONG_PASSWORD)  # do not sign in: later checks need this page signed out
                return "the demo note fills both fields and focuses Log in"

            self.check("login: the demo note fills the demo account", who, demo_note)

        def fill_login() -> None:
            email.fill(self.seed_email)
            password.fill(WRONG_PASSWORD)

        self.server_error_checks(who, "login", "/auth/login", "Log in", fill_login, ("Email", self.seed_email))

        # Pending: real mode holds a wrong-password request (stays signed out); mock mode
        # delays a right one in its own context and proves one request with a sentinel.
        if self.mock:
            pend = self.person("login-pending")
            self.goto(pend, "/login")
        else:
            pend = who

        def pending() -> str:
            p = pend.page
            self.expect(self.submit_button(p, "Log in")).to_be_enabled()
            lab(p, "Email").fill(self.seed_email)
            lab(p, "Password").fill(self.seed_password if self.mock else WRONG_PASSWORD)

            def after() -> str:
                if self.mock:
                    self.expect(p).to_have_url(f"{self.web}/")
                    return "home"
                self.expect(form_alert(p)).to_be_visible()
                self.expect(self.submit_button(p, "Log in")).to_be_enabled()
                return "401 shown, Log in enabled again"

            return self.pending_check(pend, "/auth/login", "Log in", "Logging in…", ["Email", "Password"], after)

        self.check("login: pending state, a double click sends one request", pend, pending)

    def login_success_checks(self) -> None:
        # Typed before hydration: next dev renders the form on the server; fill it the
        # moment the document arrives, before React has taken it over.
        who = self.person("login-early")
        page = who.page

        def early() -> str:
            self.goto(who, "/login", wait="commit")
            lab(page, "Email").fill(self.seed_email)
            lab(page, "Password").fill(self.seed_password)
            self.expect(self.submit_button(page, "Log in")).to_be_enabled()  # hydrated
            page.wait_for_load_state("load")
            page.wait_for_timeout(500)  # any re-render after hydration has happened by now
            self.expect(lab(page, "Email")).to_have_value(self.seed_email)
            self.expect(lab(page, "Password")).to_have_value(self.seed_password)
            return "email and password typed before hydration are still there"

        self.check("login: input typed before hydration is kept", who, early)

        def home() -> str:
            self.submit_button(page, "Log in").click()
            self.expect(page).to_have_url(f"{self.web}/")
            return f"{self.seed_email} -> {self.path_of(page)}"

        self.check("login: a seeded user goes home", who, home)

        # The same after a sign-out that left the signed-in hint behind (the join page's
        # "Not you? Sign out" does): the form hides while /me is asked, then comes back.
        hinted = self.person(
            "login-hint", init_script="try { localStorage.setItem('stitch.signedIn', '1'); } catch {}"
        )

        def early_with_hint() -> str:
            p = hinted.page
            self.goto(hinted, "/login", wait="commit")
            lab(p, "Email").fill(self.seed_email)
            lab(p, "Password").fill(self.seed_password)
            self.expect(self.submit_button(p, "Log in")).to_be_enabled()
            p.wait_for_load_state("load")
            p.wait_for_timeout(1_000)  # the /me check answers 401 and the form comes back
            self.expect(lab(p, "Email")).to_have_value(self.seed_email)
            self.expect(lab(p, "Password")).to_have_value(self.seed_password)
            self.submit_button(p, "Log in").click()
            self.expect(p).to_have_url(f"{self.web}/")
            return "typed values survive the signed-in check; logged in and home"

        self.check("login: input typed before hydration survives a stale signed-in hint", hinted, early_with_hint)

        # Enter pressed the moment the document arrives: before hydration that would be
        # the browser's own GET submit, with the password in the URL, unless Log in is
        # disabled until the form is interactive.
        eager = self.person("login-eager-enter")

        def eager_enter() -> str:
            p = eager.page
            self.goto(eager, "/login", wait="commit")
            lab(p, "Email").fill(self.seed_email)
            lab(p, "Password").fill(WRONG_PASSWORD)
            lab(p, "Password").press("Enter")
            p.wait_for_load_state("load")
            self.expect(self.submit_button(p, "Log in")).to_be_enabled()
            p.wait_for_timeout(500)
            if "password" in urlsplit(p.url).query.lower():
                raise AssertionError(f"the form was submitted natively, password in the URL: {p.url}")
            self.expect(p).to_have_url(f"{self.web}/login")
            return f"no native submit; still at {self.path_of(p)}, nothing in the URL"

        self.check("login: Enter before hydration never puts the password in the URL", eager, eager_enter)

    # ------------------------------------------------------------------ /signup and /families/new

    def signup_and_family_checks(self) -> None:
        who = self.person("signup")
        page = who.page
        name, email, password = lab(page, "Your name"), lab(page, "Email"), lab(page, "Password")
        create = self.submit_button(page, "Create account")
        person_name = f"Sweep Person {self.run_tag}"

        self.check("signup: renders, every input labelled", who,
                   lambda: self.render_check(who, "/signup", "Make your account", ["Your name", "Email", "Password"],
                                             "Create account"))
        self.check("signup: tab order follows the visual order", who,
                   lambda: self.tab_order_check(who, ["Your name", "Email", "Password", "Create account", "Log in"]))

        def empty() -> str:
            create.click()
            self.expect_field_error(page, "Your name", NAME_EMPTY)
            self.expect_field_error(page, "Email", EMAIL_EMPTY)
            self.expect_field_error(page, "Password", PASSWORD_EMPTY)
            return "three fields flagged; focus on " + self.expect_focus_on_first_problem(page, 'field "Your name"')

        self.check("signup: empty submit flags every field, focus on the first", who, empty)

        def missing_name() -> str:
            name.fill("   ")
            email.fill(self.new_email("x"))
            password.fill(PASSWORD)
            create.click()
            self.expect_field_error(page, "Your name", NAME_EMPTY)
            self.expect_valid(page, "Email")
            self.expect_valid(page, "Password")
            return "a blank name is flagged alone; focus on " + self.expect_focus_on_first_problem(
                page, 'field "Your name"'
            )

        self.check("signup: a missing name is flagged", who, missing_name)

        def bad_email_short_password() -> str:
            name.fill(person_name)
            email.fill("nope@")
            password.fill("x" * (self.pw_min - 1))
            create.click()
            self.expect_field_error(page, "Email", EMAIL_BAD)
            short = f"That’s too short. Use at least {self.pw_min} characters."
            self.expect_field_error(page, "Password", short)
            self.expect_valid(page, "Your name")
            # The hint ("At least 8 characters.") stays described too, below the error.
            texts = password.evaluate(DESCRIBED_BY_TEXTS_JS)
            return f"password described by {texts}; focus on " + self.expect_focus_on_first_problem(
                page, 'field "Email"'
            )

        self.check(f"signup: a bad email and a password under {self.pw_min} are flagged", who,
                   bad_email_short_password)

        def taken_email() -> str:
            name.fill(person_name)
            email.fill(self.seed_email)
            password.fill(PASSWORD)
            password.press("Enter")
            alert = form_alert(page)
            self.expect(alert).to_be_visible()
            text = alert.inner_text().strip()
            if "already" not in text.lower():
                raise AssertionError(f"409 shows {text!r}, which does not say the email is taken")
            focused = self.expect_focus_on_first_problem(page, "the form error")
            self.expect(email).to_have_value(self.seed_email)
            self.expect(page).to_have_url(re.compile(r"/signup$"))
            return f'Enter submitted; "{text}"; focus on {focused}'

        self.check("signup: a taken email (409) via Enter", who, taken_email)

        def fill_signup() -> None:
            name.fill(person_name)
            email.fill(self.seed_email)  # if a fault did not fire, a 409, never a new account
            password.fill(PASSWORD)

        self.server_error_checks(who, "signup", "/auth/signup", "Create account", fill_signup,
                                 ("Email", self.seed_email))

        new_email = self.new_email("signup")

        def pending_then_success() -> str:
            name.fill(person_name)
            email.fill(new_email)
            password.fill(PASSWORD)

            def after() -> str:
                self.expect(page).to_have_url(re.compile(r"/families/new$"))
                self.expect(page.get_by_role("heading", level=1, name="Start your family")).to_be_visible()
                return f"signed up -> {self.path_of(page)}"

            return self.pending_check(who, "/auth/signup", "Create account", "Creating your account…",
                                      ["Your name", "Email", "Password"], after)

        signed_up = self.check("signup: pending state, one request, then on to /families/new", who,
                               pending_then_success)
        if not signed_up:
            self.seams.record(f"[{self.tag}] families/new: checks", False,
                              "not run: needs a signed-up person with no family (signup failed)", "pages")
            return
        self.family_checks(who, person_name)

    def family_checks(self, who: Person, person_name: str) -> None:
        page = who.page
        family_name = lab(page, "Family name")
        your_name = lab(page, "Your name")
        born = lab(page, OPTIONAL_LABEL)
        start = self.submit_button(page, "Start the family")
        family = f"The Sweeps {self.run_tag} {self.view}"

        def render() -> str:
            detail = self.render_check(who, "/families/new", "Start your family",
                                       ["Family name", "Your name", OPTIONAL_LABEL], "Start the family")
            self.expect(your_name).to_have_value(person_name)
            return f"{detail}; your name prefilled"

        self.check("families/new: renders, every input labelled, your name filled in", who, render)
        self.check("families/new: tab order follows the visual order", who,
                   lambda: self.tab_order_check(who, ["Family name", "Your name", OPTIONAL_LABEL, "Start the family"]))

        def optional_mark() -> str:
            self.expect(born).to_have_accessible_name(OPTIONAL_LABEL)
            label_text = page.locator("label").filter(has_text="The year you were born").filter(visible=True)
            text = label_text.text_content() or ""
            if "born (optional)" not in re.sub(r"\s+", " ", text):
                raise AssertionError(f"the label reads {text!r}")
            return f'accessible name "{OPTIONAL_LABEL}"'

        self.check("families/new: the optional mark reads with a space", who, optional_mark)

        def empty() -> str:
            your_name.fill("")
            start.click()
            self.expect_field_error(page, "Family name", FAMILY_EMPTY)
            self.expect_field_error(page, "Your name", NAME_EMPTY)
            self.expect_valid(page, OPTIONAL_LABEL)
            return "family and your name flagged, the year is optional; focus on " + \
                self.expect_focus_on_first_problem(page, 'field "Family name"')

        self.check("families/new: empty submit flags the required fields, focus on the first", who, empty)

        def empty_family() -> str:
            your_name.fill(person_name)
            family_name.fill("  ")
            start.click()
            self.expect_field_error(page, "Family name", FAMILY_EMPTY)
            self.expect_valid(page, "Your name")
            return "focus on " + self.expect_focus_on_first_problem(page, 'field "Family name"')

        self.check("families/new: an empty family name is flagged", who, empty_family)

        def bad_years() -> str:
            family_name.fill(family)
            born.fill("3000")
            born.press("Enter")
            self.expect_field_error(page, OPTIONAL_LABEL, YEAR_FUTURE)
            focus = self.expect_focus_on_first_problem(page, f'field "{OPTIONAL_LABEL}"')
            born.fill("1700")
            start.click()
            self.expect_field_error(page, OPTIONAL_LABEL, YEAR_EARLY)
            shot(page, self.stack, f"{self.mode}-{self.view}-families-new-year-too-early")
            born.fill("")
            return f"Enter submitted; 3000 and 1700 flagged; focus on {focus}"

        self.check("families/new: a future or too-early year is flagged (Enter submits)", who, bad_years)

        def fill_family() -> None:
            family_name.fill(family)
            your_name.fill(person_name)
            born.fill("1990")

        self.server_error_checks(who, "families/new", "/families", "Start the family", fill_family,
                                 ("Family name", family))

        def pending_then_success() -> str:
            fill_family()

            def after() -> str:
                # NewFamilyScreen goes to the new family's settings with ?welcome=1 (the
                # invite step of the demo path), not home.
                self.expect(page).to_have_url(re.compile(r"/families/[^/?]+/settings\?welcome=1$"))
                self.expect(page.get_by_role("heading", level=1, name=family)).to_be_visible()
                if self.mock:
                    n = page.evaluate(MOCK_FAMILIES_NAMED_JS, family)
                    if n != 1:
                        raise AssertionError(f"{n} families named {family!r} exist after one double click")
                return f"family made -> {self.path_of(page)}"

            return self.pending_check(who, "/families", "Start the family", "Starting your family…",
                                      ["Family name", "Your name", OPTIONAL_LABEL], after)

        self.check("families/new: pending state, one request, then on to the family's settings", who,
                   pending_then_success)

    # ------------------------------------------------------------------ ?next=

    def next_checks(self) -> None:
        gate = self.person("next-gate")

        def gate_round_trip() -> str:
            p = gate.page
            self.goto(gate, "/families/new")
            self.expect(p).to_have_url(re.compile(r"/login\?next=%2Ffamilies%2Fnew$"))
            lab(p, "Email").fill(self.seed_email)
            lab(p, "Password").fill(self.seed_password)
            self.submit_button(p, "Log in").click()
            self.expect(p).to_have_url(f"{self.web}/families/new")
            self.expect(p.get_by_role("heading", level=1, name="Start your family")).to_be_visible()
            self.expect(p.get_by_role("link", name="Back to your family")).to_be_visible()
            return "signed out /families/new -> /login?next=%2Ffamilies%2Fnew -> log in -> /families/new"

        self.check("login: ?next= is honoured (signed-out /families/new and back)", gate, gate_round_trip)

        unsafe = self.person("next-unsafe")

        def unsafe_next() -> str:
            p = unsafe.page
            self.goto(unsafe, "/login?next=%2F%2Fevil.example%2Fx")
            lab(p, "Email").fill(self.seed_email)
            lab(p, "Password").fill(self.seed_password)
            self.submit_button(p, "Log in").click()
            self.expect(p).to_have_url(f"{self.web}/")
            href = p.evaluate("() => location.href")
            return f"next=//evil.example/x ignored -> {href}"

        self.check("login: an unsafe ?next= is ignored", unsafe, unsafe_next)

        # "/.//evil.example/x" starts with one slash, but URL parsing drops the "." segment and
        # leaves the path "//evil.example/x", which history reads as another host.
        dotted = self.person("next-dot-segment")

        def dot_segment_next() -> str:
            p = dotted.page
            self.goto(dotted, "/login?next=%2F.%2F%2Fevil.example%2Fx")
            lab(p, "Email").fill(self.seed_email)
            lab(p, "Password").fill(self.seed_password)
            self.submit_button(p, "Log in").click()
            self.expect(p).to_have_url(f"{self.web}/")
            href = p.evaluate("() => location.href")
            return f"next=/.//evil.example/x ignored -> {href}"

        self.check("login: a ?next= whose dot segment collapses to //host is ignored", dotted, dot_segment_next)

        def signup_next(who: Person, query: str, expect_path: re.Pattern[str]) -> str:
            p = who.page
            self.goto(who, f"/signup{query}")
            lab(p, "Your name").fill(f"Sweep Next {self.run_tag}")
            lab(p, "Email").fill(self.new_email("next"))
            lab(p, "Password").fill(PASSWORD)
            self.submit_button(p, "Create account").click()
            self.expect(p).to_have_url(expect_path)
            return f"/signup{query} -> {self.path_of(p)}"

        honoured = self.person("signup-next")
        self.check("signup: ?next= is honoured", honoured,
                   lambda: signup_next(honoured, "?next=%2Fthreads", re.compile(r"localhost:\d+/threads$")))
        ignored = self.person("signup-unsafe")
        self.check("signup: an unsafe ?next= is ignored", ignored,
                   lambda: signup_next(ignored, "?next=https%3A%2F%2Fevil.example%2F",
                                       re.compile(r"localhost:\d+/families/new$")))

        early = self.person("signup-early")

        def early_signup() -> str:
            p = early.page
            mail = self.new_email("early")
            self.goto(early, "/signup", wait="commit")
            lab(p, "Your name").fill("Early Typist")
            lab(p, "Email").fill(mail)
            lab(p, "Password").fill(PASSWORD)
            self.expect(self.submit_button(p, "Create account")).to_be_enabled()
            p.wait_for_load_state("load")
            p.wait_for_timeout(500)
            for label, value in (("Your name", "Early Typist"), ("Email", mail), ("Password", PASSWORD)):
                self.expect(lab(p, label)).to_have_value(value)
            self.submit_button(p, "Create account").click()
            self.expect(p).to_have_url(re.compile(r"/families/new$"))
            return "name, email and password typed before hydration kept; signed up"

        self.check("signup: input typed before hydration is kept", early, early_signup)

        # Back after signing up: /login -> "Create an account" -> sign up -> /families/new, then
        # the browser's Back. Sign up replaces /signup in history, so Back is /login, which
        # sees a signed-in visitor and sends them on to /families/new again. No filled-in
        # form (above all, no password) may show on the way.
        back = self.person("signup-back")

        def back_after_signup() -> str:
            p = back.page
            self.goto(back, "/login")
            p.get_by_role("link", name="Create an account").click()
            self.expect(p).to_have_url(re.compile(r"/signup$"))
            lab(p, "Your name").fill(f"Sweep Back {self.run_tag}")
            lab(p, "Email").fill(self.new_email("back"))
            lab(p, "Password").fill(PASSWORD)
            self.submit_button(p, "Create account").click()
            self.expect(p).to_have_url(re.compile(r"/families/new$"))
            self.expect(p.get_by_role("heading", level=1, name="Start your family")).to_be_visible()
            p.evaluate(WATCH_PASSWORDS_JS)
            p.go_back()
            self.expect(p).to_have_url(re.compile(r"/families/new$"))
            self.expect(p.get_by_role("heading", level=1, name="Start your family")).to_be_visible()
            p.wait_for_timeout(300)
            shown = p.evaluate("() => window.__sweepPasswordsSeen || []")
            if shown:
                raise AssertionError(f"a password field showed with text in it on the way back: {shown}")
            if p.get_by_role("button", name="Create account").count() > 0:
                raise AssertionError("the sign up form is showing again after Back")
            return "Back -> /login (signed in) -> /families/new; no filled form or password shown"

        self.check("signup: Back after signing up never reopens a filled form", back, back_after_signup)


# ----------------------------------------------------------------------------- main


def probe_mock_faults(stack: Stack, browser: Any, seams: SeamLog) -> None:
    """Record whether the mock backend can inject faults (window.__stitchMock.inject, PR #194).

    Mock-mode server errors and pending states depend on it. It has been on main since #194,
    so its absence is a failure (a regression in components/flow/mock), not a skip.
    """
    who = Person(browser, "mock probe", VIEWPORTS["desktop"])
    try:
        # /families/new asks /me at once (SessionGate), which creates the mock backend.
        who.page.goto(f"{stack.web_url}/families/new")
        who.page.wait_for_url(re.compile(r"/login"), timeout=TIMEOUT_MS)
        ok = bool(who.page.evaluate("() => !!(window.__stitchMock && typeof window.__stitchMock.inject === 'function')"))
    except Exception as exc:  # noqa: BLE001
        seams.record("[mock] probe the mock backend for fault injection", False, first_lines(str(exc)), "pages")
        return
    finally:
        who.close()
    seams.record(
        "[mock] mock backend fault injection",
        ok,
        "available: server errors and pending states are checked in mock mode"
        if ok
        else "window.__stitchMock.inject is missing: the mock-mode server error and pending checks below "
        "cannot inject their faults and fail",
    )


def summarize(seams: SeamLog, out: Path) -> int:
    code = finish(seams, out)
    shots = shots_taken(out / "mock") + shots_taken(out / "real")
    counts = seams.to_json()["counts"]
    print(f"\nout dir:     {out}")
    print(f"seams:       {out / 'seams.json'}")
    for mode in ("mock", "real"):
        taken = shots_taken(out / mode)
        if taken:
            print(f"screenshots: {len(taken)} in {out / mode / 'shots'}")
    print(f"logs:        {out}/<mode>/web.log, {out}/real/api.log")
    skipped = sum(1 for e in seams.entries if e["ok"] and e["detail"].startswith("SKIPPED"))
    print(
        f"\n{counts['total']} checks: {counts['ok']} ok ({skipped} skipped), {counts['failed']} failed "
        f"(pages {counts['failedByOwner']['pages']}, api {counts['failedByOwner']['api']}, "
        f"ethan {counts['failedByOwner']['ethan']}); {len(shots)} screenshots"
    )
    return code


def main(argv: list[str] | None = None) -> int:
    args = sweep_args(DESCRIPTION, argv)
    require_chromium()
    from playwright.sync_api import expect, sync_playwright

    expect.set_options(timeout=EXPECT_MS)
    out = sweep_out_dir(args.out, prefix="stitch-sweep-accounts-")
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
                        if mock:
                            probe_mock_faults(stack, browser, seams)
                        for view in VIEWPORTS:
                            AccountSweep(stack, browser, seams, view).run()
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
