"""Offline tests for harness.py and demo_path.py's pure parts: no browser, no servers, no network.

    cd api && uv run pytest ../web/components/flow/e2e/test_harness.py -q
"""

from __future__ import annotations

import json
import socket
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


def test_page_watch_collects_and_filters() -> None:
    class Msg:
        def __init__(self, type_: str, text: str, url: str) -> None:
            self.type, self.text, self.location = type_, text, {"url": url}

    class Req:
        def __init__(self, url: str, failure: str | None, method: str = "GET") -> None:
            self.url, self.failure, self.method = url, failure, method

    class Res:
        def __init__(self, status: int, url: str) -> None:
            self.status, self.url, self.request = status, url, Req(url, None)

    class FakePage:
        url = "http://localhost:3201/add"

        def __init__(self) -> None:
            self.handlers: dict[str, object] = {}

        def on(self, event: str, fn: object) -> None:
            self.handlers[event] = fn

    page = FakePage()
    watch = harness.PageWatch()
    watch.attach(page, "A")
    h = page.handlers
    h["console"](Msg("error", "boom", "http://localhost:3201/x.js"))  # type: ignore[operator]
    h["console"](Msg("warning", "meh", "http://localhost:3201/x.js"))  # type: ignore[operator]
    h["console"](  # type: ignore[operator]
        Msg("error", "Failed to load resource: the server responded with a status of 401 (Unauthorized)",
            "http://localhost:8201/me")
    )
    h["pageerror"](RuntimeError("bad"))  # type: ignore[operator]
    h["requestfailed"](Req("http://localhost:8201/families/f/runs/r/stream", "net::ERR_ABORTED"))  # type: ignore[operator]
    h["requestfailed"](Req("http://localhost:8201/me", "net::ERR_CONNECTION_REFUSED"))  # type: ignore[operator]
    h["response"](Res(500, "http://localhost:8201/families/f/memories"))  # type: ignore[operator]
    h["response"](Res(404, "http://localhost:8201/families/f/memories/x"))  # type: ignore[operator]

    assert watch.problems == [
        "[A] console.error at http://localhost:3201/x.js: boom",
        "[A] pageerror at http://localhost:3201/add: bad",
        "[A] requestfailed at http://localhost:3201/add: GET http://localhost:8201/me net::ERR_CONNECTION_REFUSED",
        "[A] http at http://localhost:3201/add: 500 GET http://localhost:8201/families/f/memories",
    ]
    assert watch.since(3) == watch.problems[3:]


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
