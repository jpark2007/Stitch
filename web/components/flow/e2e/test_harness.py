"""Offline tests for harness.py and demo_path.py's pure parts: no browser, no servers, no network.

    cd api && uv run pytest ../web/components/flow/e2e/test_harness.py -q
"""

from __future__ import annotations

import json
import re
import shutil
import socket
import subprocess
import sys
from pathlib import Path

import pytest

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

import harness  # noqa: E402
from harness import SKIP_LINE, SeamLog, api_env, base_env, free_port, require_chromium, web_env  # noqa: E402

CREDENTIAL_MARKERS = ("ANTHROPIC", "VOYAGE", "MONGODB", "IMAGE", "ELEVENLABS", "JWT", "SECRET", "KEY", "TOKEN")


def test_repo_root_is_the_repository() -> None:
    assert (harness.REPO_ROOT / "api" / "seed" / "serve.py").is_file()
    assert (harness.REPO_ROOT / "web" / "package.json").is_file()
    assert harness.API_DIR == harness.REPO_ROOT / "api"
    assert harness.WEB_DIR == harness.REPO_ROOT / "web"


def test_free_port_is_bindable() -> None:
    port = free_port()
    assert 1024 <= port <= 65535
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", port))  # nothing holds it


def test_port_in_use_sees_a_listener() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        port = sock.getsockname()[1]
        assert harness.port_in_use(port)
    assert not harness.port_in_use(free_port())


def test_api_env_is_offline_and_allows_the_web_origin() -> None:
    env = api_env(8201, "http://localhost:3201", 0.1)
    assert env["STITCH_FAKE_DB"] == "1"
    assert env["STITCH_AGENT"] == "fake"
    assert env["STITCH_FAKE_RUN_STEP"] == "0.1"
    assert env["STITCH_CORS_ORIGINS"] == "http://localhost:3201"
    assert env["COOKIE_SECURE"] == "0"
    assert env["STITCH_NO_DOTENV"] == "1"
    assert api_env(8201, "http://localhost:3201", 0)["STITCH_FAKE_RUN_STEP"] == "0"
    assert all(isinstance(v, str) for v in env.values())


def test_api_env_never_forwards_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("ANTHROPIC_API_KEY", "VOYAGE_API_KEY", "MONGODB_URI", "IMAGE_API_KEY", "ELEVENLABS_API_KEY"):
        monkeypatch.setenv(name, "secret-value")
    for env in (api_env(8201, "http://localhost:3201", 0.1), {**base_env(), **api_env(8201, "http://x", 0.1)}):
        for name in env:
            assert not any(marker in name for marker in CREDENTIAL_MARKERS), name
        assert "secret-value" not in env.values()


def test_base_env_is_an_allowlist() -> None:
    source = {
        "PATH": "/bin",
        "HOME": "/home/x",
        "LANG": "en_US.UTF-8",
        "TMPDIR": "/tmp",
        "ANTHROPIC_API_KEY": "k",
        "MONGODB_URI": "mongodb://x",
        "NEXT_PUBLIC_API_MOCK": "1",
    }
    assert base_env(source) == {"PATH": "/bin", "HOME": "/home/x", "LANG": "en_US.UTF-8", "TMPDIR": "/tmp"}
    assert base_env({"PATH": "/bin"}) == {"PATH": "/bin"}


def test_web_env_mock_mode() -> None:
    env = web_env(mock=True, api_url=None)
    assert env == {"NEXT_PUBLIC_API_MOCK": "1", "NEXT_TELEMETRY_DISABLED": "1"}


def test_web_env_real_mode() -> None:
    env = web_env(mock=False, api_url="http://localhost:8201")
    assert env == {
        "NEXT_PUBLIC_API_URL": "http://localhost:8201",
        "NEXT_PUBLIC_API_MOCK": "",
        "NEXT_TELEMETRY_DISABLED": "1",
    }
    with pytest.raises(ValueError):
        web_env(mock=False, api_url=None)


def test_seam_log_record_and_failed() -> None:
    log = SeamLog()
    log.record("1 sign up", True, "ok at /families/new")
    log.record("2 family", False, "AssertionError: x\nmore", owner="pages")
    log.record("7 books", False, "not in B's book", owner="api")
    log.record("10a scene", False, "REQUEST for Ethan: ...", owner="ethan")
    log.record("10b home", True, owner="ethan")

    assert [e["step"] for e in log.failed()] == ["2 family", "7 books", "10a scene"]
    assert [e["step"] for e in log.failed("pages")] == ["2 family"]
    assert [e["step"] for e in log.failed("api")] == ["7 books"]
    assert [e["step"] for e in log.failed("ethan")] == ["10a scene"]
    assert log.entries[0] == {"step": "1 sign up", "ok": True, "owner": "pages", "detail": "ok at /families/new"}
    assert log.entries[4]["detail"] == ""

    with pytest.raises(ValueError):
        log.record("x", True, owner="someone")
    with pytest.raises(ValueError):
        log.failed("someone")


def test_seam_log_write(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    log = SeamLog()
    log.record("1 sign up", True, "fine")
    log.record("2 family", False, "line one\nline two", owner="pages")
    log.record("10a scene", False, "REQUEST for Ethan", owner="ethan")
    capsys.readouterr()

    path = log.write(tmp_path / "out")
    assert path == tmp_path / "out" / "seams.json"
    data = json.loads(path.read_text())
    assert data["seams"] == [
        {"step": "1 sign up", "ok": True, "owner": "pages", "detail": "fine"},
        {"step": "2 family", "ok": False, "owner": "pages", "detail": "line one\nline two"},
        {"step": "10a scene", "ok": False, "owner": "ethan", "detail": "REQUEST for Ethan"},
    ]
    assert data["counts"] == {
        "total": 3,
        "ok": 1,
        "failed": 2,
        "failedByOwner": {"pages": 1, "ethan": 1, "api": 0},
    }

    table = capsys.readouterr().out.splitlines()
    assert table[0].split() == ["#", "result", "owner", "step", "detail"]
    rows = table[2:]
    assert len(rows) == 3
    # Aligned: every row's owner column starts at the same offset as the header's.
    col = table[0].index("owner")
    assert all(r[col : col + 5] in ("pages", "ethan") for r in rows)
    assert "line one" in rows[1] and "line two" not in rows[1]


def test_require_chromium_skips_cleanly(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(harness, "chromium_available", lambda: False)
    with pytest.raises(SystemExit) as exc:
        require_chromium()
    assert exc.value.code == 0
    assert capsys.readouterr().out.strip() == SKIP_LINE
    assert SKIP_LINE == "SKIP: Playwright Chromium is not installed (cd api && uv run playwright install chromium)"


def test_require_chromium_passes_when_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(harness, "chromium_available", lambda: True)
    require_chromium()


def test_demo_path_skips_without_chromium(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    import demo_path

    monkeypatch.setattr(harness, "chromium_available", lambda: False)

    def no_stack(**_: object) -> None:
        raise AssertionError("run_stack must not start without Chromium")

    monkeypatch.setattr(demo_path, "run_stack", no_stack)
    with pytest.raises(SystemExit) as exc:
        demo_path.main([])
    assert exc.value.code == 0
    assert SKIP_LINE in capsys.readouterr().out


def test_shot_numbers_in_call_order(tmp_path: Path) -> None:
    class FakePage:
        def __init__(self) -> None:
            self.calls: list[tuple[str, bool]] = []

        def screenshot(self, *, path: str, full_page: bool) -> None:
            self.calls.append((path, full_page))
            Path(path).write_bytes(b"png")

    stack = harness.Stack(web_url="http://localhost:1", api_url=None, mock=True, out_dir=tmp_path)
    page = FakePage()
    first = harness.shot(page, stack, "sign up")
    second = harness.shot(page, stack, "family/settings")
    assert first == tmp_path / "shots" / "01-sign-up.png"
    assert second == tmp_path / "shots" / "02-family-settings.png"
    assert all(full for _, full in page.calls)
    assert harness.shots_taken(tmp_path) == [first, second]


def test_shot_in_a_reused_out_dir_never_mixes_runs(tmp_path: Path) -> None:
    class FakePage:
        def screenshot(self, *, path: str, full_page: bool) -> None:
            Path(path).write_bytes(b"png")

    (tmp_path / "shots").mkdir()
    (tmp_path / "shots" / "01-old.png").write_bytes(b"old")
    (tmp_path / "shots" / "07-old-failed.png").write_bytes(b"old")
    stack = harness.Stack(web_url="http://localhost:1", api_url=None, mock=True, out_dir=tmp_path)
    new = harness.shot(FakePage(), stack, "new")
    assert new == tmp_path / "shots" / "08-new.png"
    assert (tmp_path / "shots" / "01-old.png").read_bytes() == b"old"
    assert harness.shots_taken(tmp_path) == [new]


def test_archive_previous_run_moves_old_outputs(tmp_path: Path) -> None:
    import demo_path

    assert demo_path.archive_previous_run(tmp_path) is None
    (tmp_path / "shots").mkdir()
    (tmp_path / "shots" / "01-old.png").write_bytes(b"old")
    (tmp_path / "seams.json").write_text("{}")
    (tmp_path / "api.log").write_text("log")
    (tmp_path / "notes.txt").write_text("mine")
    dest = demo_path.archive_previous_run(tmp_path)
    assert dest is not None and dest.parent == tmp_path and dest.name.startswith("previous-")
    assert (dest / "shots" / "01-old.png").read_bytes() == b"old"
    assert (dest / "seams.json").is_file() and (dest / "api.log").is_file()
    assert not (tmp_path / "shots").exists() and not (tmp_path / "seams.json").exists()
    assert (tmp_path / "notes.txt").read_text() == "mine"  # not ours: left alone


def test_demo_path_reports_any_startup_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A startup error that is not a StackError (npx missing) still ends in seams.json and exit 1."""
    import demo_path

    monkeypatch.setattr(demo_path, "require_chromium", lambda: None)

    def broken_stack(**_: object) -> None:
        raise FileNotFoundError(2, "No such file or directory", "npx")

    monkeypatch.setattr(demo_path, "run_stack", broken_stack)
    assert demo_path.main(["--out", str(tmp_path)]) == 1
    data = json.loads((tmp_path / "seams.json").read_text())
    assert data["counts"]["failedByOwner"]["pages"] == 1
    seam = data["seams"][0]
    assert seam["step"] == "start the stack (seed.serve + next dev)"
    assert seam["detail"].startswith("FileNotFoundError:") and "npx" in seam["detail"]
    assert "FAIL" in capsys.readouterr().out


class _Msg:
    def __init__(self, type_: str, text: str, url: str) -> None:
        self.type, self.text, self.location = type_, text, {"url": url}


class _Frame:
    pass


class _Req:
    def __init__(self, url: str, failure: str | None = None, method: str = "GET", *, nav: bool = False,
                 frame: object = None) -> None:
        self.url, self.failure, self.method, self.nav, self.frame = url, failure, method, nav, frame

    def is_navigation_request(self) -> bool:
        return self.nav


class _Res:
    def __init__(self, status: int, url: str) -> None:
        self.status, self.url, self.request = status, url, _Req(url)


class _WatchedPage:
    def __init__(self, url: str) -> None:
        self.url = url
        self.main_frame = _Frame()
        self.handlers: dict[str, object] = {}

    def on(self, event: str, fn: object) -> None:
        self.handlers[event] = fn

    def emit(self, event: str, arg: object) -> None:
        self.handlers[event](arg)  # type: ignore[operator]


def test_page_watch_collects_and_filters() -> None:
    page = _WatchedPage("http://localhost:3201/add")
    watch = harness.PageWatch()
    watch.attach(page, "A")
    page.emit("console", _Msg("error", "boom", "http://localhost:3201/_next/static/chunks/x.js"))
    page.emit("console", _Msg("warning", "meh", "http://localhost:3201/x.js"))
    page.emit(
        "console",
        _Msg("error", "Failed to load resource: the server responded with a status of 401 (Unauthorized)",
             "http://localhost:8201/me"),
    )
    page.emit("pageerror", RuntimeError("bad"))
    page.emit("requestfailed", _Req("http://localhost:8201/me", "net::ERR_CONNECTION_REFUSED"))
    page.emit("response", _Res(500, "http://localhost:8201/families/f/memories"))
    page.emit("response", _Res(404, "http://localhost:8201/families/f/memories/x"))

    assert watch.problems == [
        "[A] console.error at http://localhost:3201/add: boom (source: http://localhost:3201/_next/static/chunks/x.js)",
        "[A] pageerror at http://localhost:3201/add: bad",
        "[A] requestfailed at http://localhost:3201/add: GET http://localhost:8201/me net::ERR_CONNECTION_REFUSED",
        "[A] http at http://localhost:3201/add: 500 GET http://localhost:8201/families/f/memories",
    ]
    assert watch.since(3) == watch.problems[3:]
    assert len(watch.events) == len(watch.problems)
    assert watch.events[0] == {
        "label": "A",
        "kind": "console.error",
        "pageUrl": "http://localhost:3201/add",
        "source": "http://localhost:3201/_next/static/chunks/x.js",
        "text": "boom",
    }
    assert [e["kind"] for e in watch.events_since(2)] == ["requestfailed", "http"]


def test_page_watch_names_the_page_not_the_script() -> None:
    """A console error on Ethan's page is filed under that page's URL, whatever script logged it."""
    import demo_path

    page = _WatchedPage("http://localhost:3201/")
    watch = harness.PageWatch()
    watch.attach(page, "A")
    page.emit("console", _Msg("error", "Warning: Each child in a list should have a unique key",
                              "http://localhost:3201/_next/static/chunks/0aq.js"))
    page.emit("console", _Msg("error", "Failed to load resource: the server responded with a status of 404",
                              "http://localhost:8201/families/f/tree"))
    page.emit("response", _Res(502, "http://localhost:8201/families/f/tree"))
    assert [e["pageUrl"] for e in watch.events] == ["http://localhost:3201/"] * 3
    assert [demo_path.problem_owner(e) for e in watch.events] == ["ethan", "ethan", "api"]

    page.url = "http://localhost:3201/add?family=f"
    page.emit("console", _Msg("error", "boom", "http://localhost:3201/_next/static/chunks/0aq.js"))
    assert demo_path.problem_owner(watch.events[-1]) == "pages"


def test_page_watch_aborts_only_ignored_after_a_navigation() -> None:
    page = _WatchedPage("http://localhost:3201/add?run=r")
    watch = harness.PageWatch()
    watch.attach(page, "A")
    aborted = "net::ERR_ABORTED"

    # A navigation request that was itself cancelled: expected.
    nav = _Req("http://localhost:3201/add", aborted, nav=True, frame=page.main_frame)
    page.emit("request", nav)
    page.emit("requestfailed", nav)

    # An event stream cancelled because the page navigated (goto) while it was open: expected.
    stream = _Req("http://localhost:8201/families/f/runs/r/events", aborted)
    page.emit("request", stream)
    page.emit("request", _Req("http://localhost:3201/", nav=True, frame=page.main_frame))
    page.emit("requestfailed", stream)

    # A fetch cancelled by a client-side route change (pushState): expected.
    fetch = _Req("http://localhost:8201/families/f/questions", aborted)
    page.emit("request", fetch)
    page.emit("framenavigated", page.main_frame)
    page.emit("requestfailed", fetch)

    # A child frame navigating does not excuse anything.
    wrong = _Req("http://localhost:8201/families/f/runs/r/events", aborted)
    page.emit("request", wrong)
    page.emit("framenavigated", _Frame())
    page.emit("requestfailed", wrong)
    assert watch.problems == [
        f"[A] requestfailed at {page.url}: GET http://localhost:8201/families/f/runs/r/events {aborted}"
    ]

    # Finished requests are forgotten; a stream the page aborts on its own is reported.
    done = _Req("http://localhost:8201/me")
    page.emit("request", done)
    page.emit("requestfinished", done)
    again = _Req("http://localhost:8201/families/f/runs/r/events", aborted)
    page.emit("request", again)
    page.emit("requestfailed", again)
    assert len(watch.problems) == 2

    class Answer:
        def __init__(self, status: int, ctype: str) -> None:
            self.status, self.headers = status, {"content-type": ctype}

    def answered(url: str, status: int, ctype: str, method: str = "POST") -> _Req:
        req = _Req(url, aborted, method)
        req.existing_response = Answer(status, ctype)  # type: ignore[attr-defined]
        return req

    # requestVoid() drops the body of a 201: the request went through, so not a problem.
    signup = answered("http://localhost:8201/auth/signup", 201, "application/json")
    # A stream that had started and was cut off, and a 404 whose body was dropped: problems.
    stream_cut = answered("http://localhost:8201/families/f/runs/r/events", 200, "text/event-stream", "GET")
    missing = answered("http://localhost:8201/families/f/invites/x", 404, "application/json", "DELETE")
    for req in (signup, stream_cut, missing):
        page.emit("request", req)
        page.emit("requestfailed", req)
    assert [p.split(": ", 1)[1] for p in watch.problems[2:]] == [
        f"GET http://localhost:8201/families/f/runs/r/events {aborted}",
        f"DELETE http://localhost:8201/families/f/invites/x {aborted}",
    ]


def test_detached_flushes_match_only_this_groups_pids(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [
        (500, 500, "node /w/web/node_modules/next/dist/telemetry/detached-flush.js dev /w/web _events_101_ab12.json"),
        (501, 501, "node /x/web/node_modules/next/dist/telemetry/detached-flush.js dev /x/web _events_999_cd34.json"),
        (502, 101, "node next dev --port 3201"),
    ]
    monkeypatch.setattr(harness, "_ps", lambda: rows)
    assert harness._detached_flushes({100, 101}) == [500]
    assert harness._detached_flushes({7}) == []


def test_tiny_png_is_a_png() -> None:
    import demo_path

    data = demo_path.tiny_png()
    assert data.startswith(b"\x89PNG\r\n\x1a\n")
    assert b"IHDR" in data and data.endswith(b"IEND\xaeB`\x82")


def test_owner_for_url() -> None:
    import demo_path

    assert demo_path.owner_for_url("http://localhost:3201/") == "ethan"
    assert demo_path.owner_for_url("http://localhost:3201/memories/abc") == "ethan"
    assert demo_path.owner_for_url("http://localhost:3201/people/p1") == "ethan"
    assert demo_path.owner_for_url("http://localhost:3201/add?family=f") == "pages"
    assert demo_path.owner_for_url("http://localhost:3201/join/code") == "pages"
    assert demo_path.problem_owner({"kind": "http", "pageUrl": "http://localhost:3201/add"}) == "api"
    assert demo_path.problem_owner({"kind": "console.error", "pageUrl": "http://localhost:3201/threads"}) == "ethan"
    assert demo_path.problem_owner({"kind": "requestfailed", "pageUrl": "http://localhost:3201/join/c"}) == "pages"


# --------------------------------------------------------------------------- sweep helpers


def test_viewports() -> None:
    assert harness.VIEWPORTS == {"desktop": (1280, 900), "phone": (390, 844)}
    assert list(harness.VIEWPORTS) == ["desktop", "phone"]  # the order sweeps walk them


def test_sweep_args_defaults() -> None:
    args = harness.sweep_args("a sweep", [])
    assert args.mode == "both"
    assert args.headed is False
    assert args.out is None
    assert args.api_port is None
    assert args.web_port is None


def test_sweep_args_every_flag(tmp_path: Path) -> None:
    args = harness.sweep_args(
        "a sweep",
        ["--mode", "real", "--headed", "--out", str(tmp_path), "--api-port", "8215", "--web-port", "3215"],
    )
    assert args.mode == "real"
    assert args.headed is True
    assert args.out == tmp_path
    assert (args.api_port, args.web_port) == (8215, 3215)
    assert harness.sweep_args("a sweep", ["--mode", "mock"]).mode == "mock"


def test_sweep_args_rejects_unknown_modes_and_ports(capsys: pytest.CaptureFixture[str]) -> None:
    for argv in (["--mode", "fake"], ["--web-port", "abc"], ["--keep-going"]):
        with pytest.raises(SystemExit) as exc:
            harness.sweep_args("a sweep", argv)
        assert exc.value.code == 2
    assert "invalid choice" in capsys.readouterr().err


def test_sweep_args_help_names_the_description(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        harness.sweep_args("Sweep the join page.", ["--help"])
    out = capsys.readouterr().out
    assert "Sweep the join page." in out
    for flag in ("--mode", "--headed", "--out", "--api-port", "--web-port"):
        assert flag in out


def test_sweep_modes() -> None:
    assert harness.sweep_modes("mock") == [True]
    assert harness.sweep_modes("real") == [False]
    assert harness.sweep_modes("both") == [True, False]
    with pytest.raises(ValueError):
        harness.sweep_modes("fake")


def test_sweep_out_dir(tmp_path: Path) -> None:
    given = tmp_path / "a" / "b"
    assert harness.sweep_out_dir(given) == given.resolve()
    assert given.is_dir()
    temp = harness.sweep_out_dir(None, prefix="stitch-test-sweep-")
    try:
        assert temp.is_dir() and temp.name.startswith("stitch-test-sweep-")
        assert harness.REPO_ROOT not in temp.parents  # never inside the repo by default
    finally:
        temp.rmdir()


def test_wait_port_free() -> None:
    assert harness.wait_port_free(None)
    assert harness.wait_port_free(free_port(), timeout=1.0)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen(64)  # room for every probe: nothing accepts them
        assert not harness.wait_port_free(sock.getsockname()[1], timeout=0.3)


PAGE = "http://localhost:3215/login"


def test_expected_noise_4xx_console_lines() -> None:
    for status in (401, 404, 409, 422):
        line = (
            f"[A] console.error at {PAGE}: Failed to load resource: the server responded with a status of "
            f"{status} (Conflict) (source: http://localhost:8215/auth/signup)"
        )
        assert harness.expected_noise(line), line
    # 5xx is not noise; neither is any other console error.
    assert not harness.expected_noise(
        f"[A] console.error at {PAGE}: Failed to load resource: the server responded with a status of 500 "
        "(Internal Server Error) (source: http://localhost:8215/auth/login)"
    )
    assert not harness.expected_noise(f"[A] console.error at {PAGE}: Warning: Each child in a list needs a key")
    assert not harness.expected_noise(f"[A] console.error at {PAGE}: status of 404 in a message of our own")


def test_expected_noise_aborted_rsc_prefetch() -> None:
    assert harness.expected_noise(
        f"[A] requestfailed at {PAGE}: GET http://localhost:3215/signup?_rsc=1x2y3 net::ERR_ABORTED"
    )
    assert harness.expected_noise(
        f"[A] requestfailed at {PAGE}: GET http://localhost:3215/families/new?next=%2F&_rsc=ab net::ERR_ABORTED"
    )
    # Not a prefetch, or not an abort: a real problem.
    assert not harness.expected_noise(
        f"[A] requestfailed at {PAGE}: GET http://localhost:8215/families/f/runs/r/events net::ERR_ABORTED"
    )
    assert not harness.expected_noise(
        f"[A] requestfailed at {PAGE}: GET http://localhost:3215/signup?_rsc=1x net::ERR_CONNECTION_REFUSED"
    )
    assert not harness.expected_noise(f"[A] pageerror at {PAGE}: _rsc= net::ERR_ABORTED")


def test_app_problems_drops_noise_after_the_mark() -> None:
    page = _WatchedPage(PAGE)
    watch = harness.PageWatch()
    watch.attach(page, "A")
    page.emit("console", _Msg("error", "early boom", "http://localhost:3215/x.js"))
    mark = len(watch.problems)
    page.emit("console", _Msg("error", "Failed to load resource: the server responded with a status of 409 (Conflict)",
                              "http://localhost:8215/auth/signup"))
    page.emit("request", prefetch := _Req("http://localhost:3215/signup?_rsc=abc", "net::ERR_ABORTED"))
    page.emit("requestfailed", prefetch)
    page.emit("pageerror", RuntimeError("late boom"))
    assert len(watch.problems) == 4
    assert harness.app_problems(watch, mark) == [f"[A] pageerror at {PAGE}: late boom"]
    assert harness.app_problems(watch) == [
        f"[A] console.error at {PAGE}: early boom (source: http://localhost:3215/x.js)",
        f"[A] pageerror at {PAGE}: late boom",
    ]


def test_finish_exit_codes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    seams = SeamLog()
    seams.record("fine", True)
    assert harness.finish(seams, tmp_path) == 0
    assert json.loads((tmp_path / "seams.json").read_text())["counts"]["ok"] == 1

    seams.record("Ethan's page", False, "REQUEST for Ethan: ...", "ethan")
    assert harness.finish(seams, tmp_path) == 0, "Ethan's seams are reported, not failed"

    for owner in ("pages", "api"):
        failing = SeamLog()
        failing.record("fine", True)
        failing.record("broken", False, "boom", owner)
        assert harness.finish(failing, tmp_path / owner) == 1
        written = json.loads((tmp_path / owner / "seams.json").read_text())
        assert written["counts"]["failedByOwner"][owner] == 1
    capsys.readouterr()


def test_mock_faults_script_shape() -> None:
    faults = [
        {"method": "POST", "path": "/auth/login", "status": 503, "times": 2},
        {"path": "/families/{fid}/invites", "network": True},
        {"method": "POST", "path": "/families", "delayMs": 1500, "times": 1},
        {"path": "/auth/signup", "status": 409, "detail": "Taken   “quoted” </script>"},
    ]
    script = harness.mock_faults_script(faults)
    assert "window" in script and "__stitchMockFaults" in script
    assert " " not in script  # escaped, so it cannot end a JS line early
    payload = re.search(r"armed\.concat\((.*)\);", script)
    assert payload is not None
    assert json.loads(payload.group(1)) == faults
    assert harness.mock_faults_script([]).count("concat([])") == 1


@pytest.mark.parametrize(
    "fault",
    [
        {"status": 500},  # no path
        {"path": "auth/login", "status": 500},  # not absolute
        {"path": "/x", "status": 99},
        {"path": "/x", "status": True},
        {"path": "/x", "status": 500, "times": 0},
        {"path": "/x", "status": 500, "times": 1.5},
        {"path": "/x", "delayMs": -1},
        {"path": "/x", "network": "yes"},
        {"path": "/x", "status": 500, "detail": 5},
        {"path": "/x", "status": 500, "method": ""},
        {"path": "/x"},  # does nothing
        {"path": "/x", "status": 500, "retry": True},  # unknown key
        "not a dict",
    ],
)
def test_mock_faults_script_rejects_bad_faults(fault: object) -> None:
    with pytest.raises(ValueError):
        harness.mock_faults_script([fault])  # type: ignore[list-item]


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_mock_faults_script_runs_and_appends(tmp_path: Path) -> None:
    """The script really sets window.__stitchMockFaults, keeping faults armed before it."""
    first = harness.mock_faults_script([{"path": "/a", "status": 500, "times": 2}])
    second = harness.mock_faults_script([{"method": "POST", "path": "/b", "network": True}])
    probe = tmp_path / "probe.js"
    probe.write_text(
        "globalThis.window = {};\n" + first + second + "console.log(JSON.stringify(window.__stitchMockFaults));\n"
    )
    out = subprocess.run(["node", str(probe)], capture_output=True, text=True, timeout=30, check=True).stdout  # noqa: S603, S607
    assert json.loads(out) == [
        {"path": "/a", "status": 500, "times": 2},
        {"method": "POST", "path": "/b", "network": True},
    ]


# --------------------------------------------------------------------------- sweep_accounts.py


def test_sweep_accounts_skips_without_chromium(monkeypatch: pytest.MonkeyPatch,
                                               capsys: pytest.CaptureFixture[str]) -> None:
    import sweep_accounts

    monkeypatch.setattr(harness, "chromium_available", lambda: False)

    def no_stack(**_: object) -> None:
        raise AssertionError("run_stack must not start without Chromium")

    monkeypatch.setattr(sweep_accounts, "run_stack", no_stack)
    with pytest.raises(SystemExit) as exc:
        sweep_accounts.main(["--mode", "both"])
    assert exc.value.code == 0
    assert capsys.readouterr().out.strip() == SKIP_LINE


def test_sweep_accounts_reads_the_app_s_own_messages() -> None:
    import sweep_accounts

    messages = sweep_accounts.api_messages()
    for status in (0, 500, 502, 503):
        assert messages.get(status), f"lib/api.ts has no message for {status}"
    assert "reach Stitch" in messages[0]
    assert sweep_accounts.password_min() >= 8
    assert sweep_accounts.mock_seed_password()


def test_sweep_accounts_owners() -> None:
    import sweep_accounts

    assert sweep_accounts.owner_for_url("http://localhost:3215/") == "ethan"
    assert sweep_accounts.owner_for_url("http://localhost:3215/threads") == "ethan"
    assert sweep_accounts.owner_for_url("http://localhost:3215/login?next=%2F") == "pages"
    assert sweep_accounts.owner_for_url("http://localhost:3215/families/new") == "pages"
    assert sweep_accounts.problem_owner({"kind": "http", "pageUrl": "http://localhost:3215/login"}) == "api"
    assert sweep_accounts.problem_owner({"kind": "pageerror", "pageUrl": "http://localhost:3215/"}) == "ethan"
