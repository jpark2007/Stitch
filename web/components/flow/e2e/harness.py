"""A small browser harness for the flow pages: start the stack, watch pages, log seams.

Run with the API's uv environment (it has Playwright), from api/:

    cd api && uv run python ../web/components/flow/e2e/demo_path.py

What it gives a sweep or demo script:

- run_stack(mock=...): starts the offline API (python -m seed.serve, in-memory database,
  scripted agent runs) and `next dev`, waits until both answer, and always stops both
  process groups on the way out, failures included. Mock mode (NEXT_PUBLIC_API_MOCK=1)
  starts only the web app.
- PageWatch: collects console errors, uncaught page errors, failed requests and 5xx
  responses per page, each prefixed with a label and the URL.
- shot(): numbered full-page screenshots under <out_dir>/shots/.
- SeamLog: one line per step (ok or not, who owns the fix), written to seams.json and
  printed as an aligned table.

Nothing here touches the network beyond localhost, and nothing reads API keys: the API
gets an allowlisted environment plus api_env(), which never forwards credentials.
See README.md next to this file.
"""

from __future__ import annotations

import json
import os
import re
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT: Path = Path(__file__).resolve().parents[4]
"""The repository root (this file is web/components/flow/e2e/harness.py)."""

API_DIR: Path = REPO_ROOT / "api"
WEB_DIR: Path = REPO_ROOT / "web"

BASE_ENV_KEYS: tuple[str, ...] = ("PATH", "HOME", "LANG", "TMPDIR")
"""The only variables of the caller's environment the servers inherit."""

API_HEALTH_DEADLINE = 60.0
WEB_READY_DEADLINE = 180.0
STOP_GRACE = 10.0

OWNERS: tuple[str, ...] = ("pages", "ethan", "api")
"""Who fixes a seam: the flow pages, Ethan's pages (tree, book, people, memories), or the API."""

SKIP_LINE = "SKIP: Playwright Chromium is not installed (cd api && uv run playwright install chromium)"


# --------------------------------------------------------------------------- ports and env


def free_port() -> int:
    """A TCP port on localhost that nothing listens on right now (the OS picks it)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def port_in_use(port: int) -> bool:
    """True when something accepts connections on localhost:port (IPv4 or IPv6)."""
    for family, host in ((socket.AF_INET, "127.0.0.1"), (socket.AF_INET6, "::1")):
        try:
            with socket.socket(family, socket.SOCK_STREAM) as sock:
                sock.settimeout(0.5)
                if sock.connect_ex((host, port)) == 0:
                    return True
        except OSError:
            continue
    return False


def api_env(api_port: int, web_origin: str, run_step: float) -> dict[str, str]:
    """The extra environment for `python -m seed.serve --port <api_port>`. Pure.

    In-memory database, scripted agent runs `run_step` seconds apart, CORS for the web
    app's origin and a cookie that works over plain http. No credentials of any kind:
    the caller merges this over an allowlisted environment (BASE_ENV_KEYS), and
    STITCH_NO_DOTENV keeps the API from reading a developer's .env files.
    """
    del api_port  # The port goes on the command line; kept in the signature for callers.
    return {
        "STITCH_FAKE_DB": "1",
        "STITCH_AGENT": "fake",
        "STITCH_FAKE_RUN_STEP": str(run_step),
        "STITCH_CORS_ORIGINS": web_origin,
        "COOKIE_SECURE": "0",
        "STITCH_NO_DOTENV": "1",
    }


def web_env(*, mock: bool, api_url: str | None) -> dict[str, str]:
    """The extra environment for `next dev`. Pure.

    Mock mode: NEXT_PUBLIC_API_MOCK=1 (the in-browser mock backend). Otherwise the API's
    URL, with mock mode explicitly off.
    """
    env = {"NEXT_TELEMETRY_DISABLED": "1"}
    if mock:
        env["NEXT_PUBLIC_API_MOCK"] = "1"
    else:
        if not api_url:
            raise ValueError("web_env needs api_url when mock is False")
        env["NEXT_PUBLIC_API_URL"] = api_url
        env["NEXT_PUBLIC_API_MOCK"] = ""
    return env


def base_env(environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """The allowlisted part of `environ` (default os.environ) the servers start from."""
    source = os.environ if environ is None else environ
    return {k: source[k] for k in BASE_ENV_KEYS if k in source}


# --------------------------------------------------------------------------- chromium


def chromium_available() -> bool:
    """True when Playwright and its Chromium build are installed (no browser is launched)."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False
    try:
        with sync_playwright() as p:
            path = p.chromium.executable_path
    except Exception:  # noqa: BLE001 - any failure here means "not usable"
        return False
    return bool(path) and Path(path).exists()


def require_chromium() -> None:
    """Exit 0 with a SKIP line when Chromium is missing, so a teammate's run never fails on it."""
    if not chromium_available():
        print(SKIP_LINE, flush=True)
        raise SystemExit(0)


# --------------------------------------------------------------------------- the stack


@dataclass(frozen=True)
class Stack:
    web_url: str
    api_url: str | None
    mock: bool
    out_dir: Path


class StackError(RuntimeError):
    """The stack could not start (busy port, a server that exited or never answered)."""


def _get(url: str, timeout: float = 5.0) -> tuple[int, bytes]:
    req = urllib.request.Request(url, headers={"User-Agent": "stitch-e2e"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:  # noqa: S310 - localhost only
            return res.status, res.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read() if exc.fp else b""


def _tail(path: Path, lines: int = 25) -> str:
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return "(no log)"
    return "\n".join(text.splitlines()[-lines:])


def _wait_until(
    check: Callable[[], bool], *, deadline: float, proc: subprocess.Popen[bytes], what: str, log: Path
) -> None:
    end = time.monotonic() + deadline
    while time.monotonic() < end:
        if proc.poll() is not None:
            raise StackError(f"{what} exited with code {proc.returncode} before it was ready.\n{log}:\n{_tail(log)}")
        try:
            if check():
                return
        except (OSError, urllib.error.URLError, ValueError):
            pass
        time.sleep(0.5)
    raise StackError(f"{what} was not ready after {deadline:.0f} s.\n{log}:\n{_tail(log)}")


def _start(cmd: list[str], *, cwd: Path, env: dict[str, str], log: Path) -> subprocess.Popen[bytes]:
    handle = log.open("ab")
    try:
        return subprocess.Popen(  # noqa: S603 - fixed commands
            cmd,
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,  # its own process group, so stop() reaches every child
        )
    finally:
        handle.close()


def _ps() -> list[tuple[int, int, str]]:
    """(pid, pgid, command) for every process, or [] when ps is unavailable."""
    try:
        out = subprocess.run(  # noqa: S603, S607 - fixed command
            ["ps", "-axo", "pid=,pgid=,command="], capture_output=True, text=True, timeout=10, check=False
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    rows = []
    for line in out.splitlines():
        parts = line.split(None, 2)
        if len(parts) == 3 and parts[0].isdigit() and parts[1].isdigit():
            rows.append((int(parts[0]), int(parts[1]), parts[2]))
    return rows


def _detached_flushes(group_pids: set[int]) -> list[int]:
    """next's telemetry flush children of these pids.

    On shutdown `next dev` spawns `node .../detached-flush.js dev <dir> _events_<pid>_<hex>.json`
    detached (its own session, outside the group), even with telemetry off. The events
    file names the server's pid, which ties each one to this stack and no other.
    """
    found = []
    for pid, _pgid, cmd in _ps():
        if "detached-flush" not in cmd:
            continue
        m = re.search(r"_events_(\d+)_", cmd)
        if m and int(m.group(1)) in group_pids:
            found.append(pid)
    return found


def _signal_pids(pids: list[int], sig: int) -> None:
    for pid in pids:
        try:
            os.kill(pid, sig)
        except (ProcessLookupError, PermissionError):
            pass


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def stop_process_group(proc: subprocess.Popen[bytes], grace: float = STOP_GRACE) -> None:
    """SIGTERM the process group, then SIGKILL it if it is still there after `grace` s.

    Then stop next's detached telemetry flush, if the group spawned one on the way out
    (it gets a few seconds to finish by itself first), so no stray process outlives the stack.
    """
    try:
        pgid = os.getpgid(proc.pid)
    except ProcessLookupError:
        pgid = None
    group_pids = {pid for pid, g, _ in _ps() if g == pgid} if pgid is not None else set()
    group_pids.add(proc.pid)
    _stop_group(proc, pgid, grace)
    flushes = _detached_flushes(group_pids)
    if flushes:
        end = time.monotonic() + 5.0
        while time.monotonic() < end and any(_alive(p) for p in flushes):
            time.sleep(0.2)
        _signal_pids([p for p in flushes if _alive(p)], signal.SIGTERM)
        time.sleep(0.5)
        _signal_pids([p for p in flushes if _alive(p)], signal.SIGKILL)


def _stop_group(proc: subprocess.Popen[bytes], pgid: int | None, grace: float) -> None:
    if pgid is not None:
        try:
            os.killpg(pgid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    try:
        proc.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        pass
    if pgid is not None:
        # The leader may be gone while children (next's workers) linger: kill the group.
        try:
            os.killpg(pgid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        proc.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        pass


@contextmanager
def run_stack(
    *,
    mock: bool,
    web_port: int | None = None,
    api_port: int | None = None,
    run_step: float = 0.1,
    out_dir: Path | None = None,
) -> Iterator[Stack]:
    """Start the API (real mode only) and `next dev`; stop both on exit, failure included.

    Real mode runs `python -m seed.serve --port <api_port>` from api/ with api_env() and
    waits for GET /health == {"status": "ok"} (60 s). Then `npx next dev --port <web_port>`
    from web/ with web_env(), until GET /login answers 200 (180 s; the first compile is
    slow). Logs go to out_dir/api.log and out_dir/web.log. Both URLs use localhost, never
    127.0.0.1: the session cookie is SameSite=Lax.
    """
    out = Path(out_dir) if out_dir is not None else Path(tempfile.mkdtemp(prefix="stitch-e2e-"))
    out.mkdir(parents=True, exist_ok=True)
    web_port = web_port if web_port is not None else free_port()
    api_port = None if mock else (api_port if api_port is not None else free_port())

    for name, port in (("web", web_port), ("API", api_port)):
        if port is not None and port_in_use(port):
            raise StackError(f"port {port} for the {name} server is busy: stop whatever listens there or pick another")

    web_url = f"http://localhost:{web_port}"
    api_url = None if mock else f"http://localhost:{api_port}"
    procs: list[subprocess.Popen[bytes]] = []
    try:
        if not mock:
            log = out / "api.log"
            env = {**base_env(), **api_env(api_port, web_url, run_step), "PYTHONUNBUFFERED": "1"}
            proc = _start([sys.executable, "-m", "seed.serve", "--port", str(api_port)], cwd=API_DIR, env=env, log=log)
            procs.append(proc)

            def api_ok() -> bool:
                status, body = _get(f"{api_url}/health", timeout=2.0)
                return status == 200 and json.loads(body or b"null") == {"status": "ok"}

            _wait_until(api_ok, deadline=API_HEALTH_DEADLINE, proc=proc, what="the API (seed.serve)", log=log)

        log = out / "web.log"
        env = {**base_env(), **web_env(mock=mock, api_url=api_url)}
        proc = _start(["npx", "next", "dev", "--port", str(web_port)], cwd=WEB_DIR, env=env, log=log)
        procs.append(proc)
        _wait_until(
            lambda: _get(f"{web_url}/login", timeout=60.0)[0] == 200,
            deadline=WEB_READY_DEADLINE,
            proc=proc,
            what="the web app (next dev)",
            log=log,
        )
        yield Stack(web_url=web_url, api_url=api_url, mock=mock, out_dir=out)
    finally:
        for proc in reversed(procs):
            stop_process_group(proc)


# --------------------------------------------------------------------------- page watch

# Chromium logs every 4xx/5xx fetch as a console error. A 401 from GET /me is how a page
# learns nobody is signed in (the join page asks on purpose), so it is not a problem.
_EXPECTED_CONSOLE: tuple[tuple[re.Pattern[str], re.Pattern[str]], ...] = (
    (re.compile(r"status of 401"), re.compile(r"/me(\?|$)")),
)


@dataclass
class PageWatch:
    """Collects what went wrong on the pages it is attached to, newest last.

    `problems` holds one line per problem, "[label] kind at <page URL>: text". The URL is
    always the page's own URL when the problem happened (never a script's or a fetched
    resource's), so a caller can tell whose page it was; a console error's source location
    is kept in the text as "(source: ...)". `events` holds the same problems as dicts
    (label, kind, pageUrl, source, text), index for index.
    """

    problems: list[str] = field(default_factory=list)
    events: list[dict[str, str]] = field(default_factory=list)

    def attach(self, page: Any, label: str) -> None:
        # Main-frame navigations so far, and the count when each request started: an
        # ERR_ABORTED is only expected when a navigation came in between.
        navs = [0]
        started: dict[Any, int] = {}

        def add(kind: str, text: str, source: str = "") -> None:
            page_url = page.url
            text = text.strip()
            shown = f"{text} (source: {source})" if source and source != page_url else text
            self.problems.append(f"[{label}] {kind} at {page_url}: {shown}")
            self.events.append({"label": label, "kind": kind, "pageUrl": page_url, "source": source, "text": text})

        def is_main(frame: Any) -> bool:
            try:
                return frame == page.main_frame
            except Exception:  # noqa: BLE001 - a closed page has no frames
                return True

        def on_framenavigated(frame: Any) -> None:
            if is_main(frame):  # client-side route changes (pushState) count too
                navs[0] += 1

        def on_request(req: Any) -> None:
            try:
                if req.is_navigation_request() and is_main(req.frame):
                    navs[0] += 1  # a document navigation starts: it will cancel what is in flight
            except Exception:  # noqa: BLE001
                pass
            started[req] = navs[0]

        def on_requestfinished(req: Any) -> None:
            started.pop(req, None)

        def on_console(msg: Any) -> None:
            if msg.type != "error":
                return
            loc = getattr(msg, "location", None) or {}
            src = loc.get("url", "") if isinstance(loc, dict) else ""
            if any(t.search(msg.text) and u.search(src) for t, u in _EXPECTED_CONSOLE):
                return
            add("console.error", msg.text, src)

        def on_pageerror(err: Any) -> None:
            add("pageerror", str(err))

        def body_discarded(req: Any) -> bool:
            """A 1xx-3xx answer (not an event stream) arrived and the page dropped its body.

            lib/api.ts's requestVoid() cancels the body of a response it does not need
            (POST /auth/signup, the run answer...), which Chromium reports as ERR_ABORTED.
            The request itself went through. An event stream cut off midway still counts.
            """
            try:
                res = getattr(req, "existing_response", None)
                if res is None or res.status >= 400:
                    return False
                ctype = (res.headers or {}).get("content-type", "")
            except Exception:  # noqa: BLE001
                return False
            return "text/event-stream" not in ctype

        def on_requestfailed(req: Any) -> None:
            failure = req.failure or ""
            at_start = started.pop(req, navs[0])
            if "ERR_ABORTED" in failure:
                try:
                    navigation = bool(req.is_navigation_request())
                except Exception:  # noqa: BLE001
                    navigation = False
                # Cancelled by a navigation (the request itself was one, or the page moved on
                # while it was in flight), or a finished answer whose body the page did not
                # want: expected. Any other abort while the page stays put is not.
                if navigation or navs[0] != at_start or body_discarded(req):
                    return
            add("requestfailed", f"{req.method} {req.url} {failure}")

        def on_response(res: Any) -> None:
            if res.status >= 500:
                add("http", f"{res.status} {res.request.method} {res.url}")

        page.on("framenavigated", on_framenavigated)
        page.on("request", on_request)
        page.on("requestfinished", on_requestfinished)
        page.on("console", on_console)
        page.on("pageerror", on_pageerror)
        page.on("requestfailed", on_requestfailed)
        page.on("response", on_response)

    def since(self, mark: int) -> list[str]:
        """Problems collected after `mark` (a previous len(problems))."""
        return self.problems[mark:]

    def events_since(self, mark: int) -> list[dict[str, str]]:
        """The structured events after `mark` (same indexes as problems)."""
        return self.events[mark:]


_SHOT_COUNTERS: dict[Path, int] = {}
_SHOTS_TAKEN: dict[Path, list[Path]] = {}
_SHOT_NUMBER = re.compile(r"^(\d+)-")


def shot(page: Any, stack: Stack, name: str) -> Path:
    """A full-page PNG at out_dir/shots/<NN>-<name>.png, numbered in call order per out_dir.

    In a reused out dir the numbers carry on after the highest one already there, so a
    new shot never overwrites or shares a number with an old one; shots_taken() lists only
    this process's shots.
    """
    shots = stack.out_dir / "shots"
    shots.mkdir(parents=True, exist_ok=True)
    if stack.out_dir not in _SHOT_COUNTERS:
        existing = [int(m.group(1)) for p in shots.glob("*.png") if (m := _SHOT_NUMBER.match(p.name))]
        _SHOT_COUNTERS[stack.out_dir] = max(existing, default=0)
    n = _SHOT_COUNTERS[stack.out_dir] + 1
    _SHOT_COUNTERS[stack.out_dir] = n
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-") or "shot"
    path = shots / f"{n:02d}-{safe}.png"
    page.screenshot(path=str(path), full_page=True)
    _SHOTS_TAKEN.setdefault(stack.out_dir, []).append(path)
    return path


def shots_taken(out_dir: Path) -> list[Path]:
    """The screenshots shot() wrote to out_dir in this process, in order."""
    return list(_SHOTS_TAKEN.get(Path(out_dir), []))


# --------------------------------------------------------------------------- seams


class SeamLog:
    """One entry per step: what ran, whether it worked, and whose fix it is."""

    def __init__(self) -> None:
        self.entries: list[dict[str, Any]] = []

    def record(self, step: str, ok: bool, detail: str = "", owner: str = "pages") -> None:
        if owner not in OWNERS:
            raise ValueError(f"owner must be one of {', '.join(OWNERS)}, not {owner!r}")
        self.entries.append({"step": step, "ok": bool(ok), "owner": owner, "detail": detail})
        mark = "ok  " if ok else "FAIL"
        first = detail.splitlines()[0] if detail else ""
        print(f"  {mark} [{owner}] {step}" + (f": {first}" if first else ""), flush=True)

    def failed(self, owner: str | None = None) -> list[dict[str, Any]]:
        if owner is not None and owner not in OWNERS:
            raise ValueError(f"owner must be one of {', '.join(OWNERS)}, not {owner!r}")
        return [e for e in self.entries if not e["ok"] and (owner is None or e["owner"] == owner)]

    def to_json(self) -> dict[str, Any]:
        by_owner = {o: len(self.failed(o)) for o in OWNERS}
        return {
            "seams": [dict(e) for e in self.entries],
            "counts": {
                "total": len(self.entries),
                "ok": sum(1 for e in self.entries if e["ok"]),
                "failed": len(self.failed()),
                "failedByOwner": by_owner,
            },
        }

    def table(self) -> str:
        rows = [("#", "result", "owner", "step", "detail")]
        for i, e in enumerate(self.entries, 1):
            detail = (e["detail"].splitlines() or [""])[0]
            if len(detail) > 110:
                detail = detail[:107] + "..."
            rows.append((str(i), "ok" if e["ok"] else "FAIL", e["owner"], e["step"], detail))
        widths = [max(len(r[c]) for r in rows) for c in range(4)]
        lines = []
        for r in rows:
            lines.append("  ".join(r[c].ljust(widths[c]) for c in range(4)) + "  " + r[4])
            if r is rows[0]:
                lines.append("  ".join("-" * w for w in widths) + "  " + "-" * 6)
        return "\n".join(line.rstrip() for line in lines)

    def write(self, out_dir: Path) -> Path:
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        path = out / "seams.json"
        path.write_text(json.dumps(self.to_json(), indent=2) + "\n")
        print(self.table(), flush=True)
        return path
