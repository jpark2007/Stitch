"""seed.serve: the one-command offline demo (`uv run python -m seed.serve`).

Unit tests for its pieces, then one integration test that starts it as a real process and
talks to it over real HTTP on localhost: health, every demo account, the tree, each
account's book, and a scripted add-a-memory run (seed.smoke), then a clean Ctrl+C.
No network beyond localhost, no keys, no MongoDB.
"""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import httpx
import jwt
import pytest

from auth import COOKIE_NAME
from seed import demo, serve
from seed.smoke import SmokeOptions, run_smoke

STARTUP_LIMIT = 60.0
SHUTDOWN_LIMIT = 10.0
KEY_VARS = ("ANTHROPIC_API_KEY", "VOYAGE_API_KEY", "IMAGE_API_KEY", "ELEVENLABS_API_KEY")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def listener() -> Iterator[tuple[str, int]]:
    """A socket listening on a localhost port, closed afterwards."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        yield "127.0.0.1", sock.getsockname()[1]
    finally:
        sock.close()


@pytest.fixture
def isolated_environ(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """os.environ swapped for a copy (restored afterwards), with the .env files off."""
    env = dict(os.environ)
    env["STITCH_NO_DOTENV"] = "1"
    for name in ("JWT_SECRET", "STITCH_FAKE_DB", "STITCH_AGENT"):
        env.pop(name, None)
    monkeypatch.setattr(os, "environ", env)
    return env


# ------------------------------------------------------------------------------ demo_env


def test_demo_env_always_sets_fake_db() -> None:
    assert serve.demo_env({})["STITCH_FAKE_DB"] == "1"
    assert serve.demo_env({"STITCH_FAKE_DB": "0"})["STITCH_FAKE_DB"] == "1"


def test_demo_env_defaults_jwt_secret_when_absent_everywhere() -> None:
    assert serve.demo_env({}) == {"STITCH_FAKE_DB": "1", "JWT_SECRET": serve.DEV_JWT_SECRET}
    assert serve.demo_env({}, dotenv_values={}) == {"STITCH_FAKE_DB": "1", "JWT_SECRET": serve.DEV_JWT_SECRET}
    assert len(serve.DEV_JWT_SECRET) >= 32


def test_demo_env_keeps_a_configured_jwt_secret() -> None:
    assert "JWT_SECRET" not in serve.demo_env({"JWT_SECRET": "from-the-shell"})
    assert "JWT_SECRET" not in serve.demo_env({}, dotenv_values={"JWT_SECRET": "from-a-dotenv-file"})
    assert "JWT_SECRET" not in serve.demo_env({"JWT_SECRET": "shell"}, dotenv_values={"JWT_SECRET": "file"})


def test_demo_env_treats_a_blank_jwt_secret_as_absent() -> None:
    # auth.py treats a blank JWT_SECRET as unset (a random per-process secret).
    assert serve.demo_env({"JWT_SECRET": "  "})["JWT_SECRET"] == serve.DEV_JWT_SECRET
    assert serve.demo_env({}, dotenv_values={"JWT_SECRET": ""})["JWT_SECRET"] == serve.DEV_JWT_SECRET
    assert serve.demo_env({}, dotenv_values={"JWT_SECRET": None})["JWT_SECRET"] == serve.DEV_JWT_SECRET


def test_demo_env_never_touches_stitch_agent_or_anything_else() -> None:
    environ = {"STITCH_AGENT": "real", "ANTHROPIC_API_KEY": "k", "PATH": "/bin"}
    out = serve.demo_env(environ, dotenv_values={"STITCH_AGENT": "off"})
    assert set(out) == {"STITCH_FAKE_DB", "JWT_SECRET"}
    assert set(serve.demo_env({})) == {"STITCH_FAKE_DB", "JWT_SECRET"}
    assert environ == {"STITCH_AGENT": "real", "ANTHROPIC_API_KEY": "k", "PATH": "/bin"}  # pure


def test_read_env_files_first_file_wins(tmp_path: Path) -> None:
    first, second, missing = tmp_path / "a.env", tmp_path / "b.env", tmp_path / "none.env"
    first.write_text("JWT_SECRET=from-first\n")
    second.write_text("JWT_SECRET=from-second\nOTHER=x\n")
    assert serve.read_env_files((missing, first, second)) == {"JWT_SECRET": "from-first", "OTHER": "x"}


# ------------------------------------------------------------------------------ port_free / banner


def test_port_free_true_for_an_unused_port() -> None:
    assert serve.port_free("127.0.0.1", _free_port()) is True


def test_port_free_false_while_a_socket_listens(listener: tuple[str, int]) -> None:
    host, port = listener
    assert serve.port_free(host, port) is False


def test_banner_lists_every_account_and_how_to_stop() -> None:
    text = serve.banner("127.0.0.1", 8000)
    assert "http://localhost:8000" in text
    assert "http://localhost:3000" in text
    assert "SameSite=Lax" in text and "127.0.0.1" in text
    assert demo.DEMO_PASSWORD in text
    for account in demo.DEMO_ACCOUNTS:
        assert account.email in text
    assert "Ctrl+C to stop; data resets on every restart" in text
    assert "NEXT_PUBLIC_API_URL" not in text  # the web app's default already points at 8000
    assert "NEXT_PUBLIC_API_URL=http://localhost:8123" in serve.banner("127.0.0.1", 8123)


# ------------------------------------------------------------------------------ main


def test_main_busy_port_exits_1_without_starting(
    listener: tuple[str, int],
    isolated_environ: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def never(*args: object, **kwargs: object) -> None:
        raise AssertionError("uvicorn.run must not be called when the port is busy")

    monkeypatch.setattr(serve.uvicorn, "run", never)
    host, port = listener
    assert serve.main(["--host", host, "--port", str(port)]) == 1
    err = capsys.readouterr().err
    assert f"port {port} is in use: is the API already running? pass --port" in err


def test_main_sets_the_demo_env_and_runs_uvicorn(
    isolated_environ: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[tuple[tuple, dict]] = []
    monkeypatch.setattr(serve.uvicorn, "run", lambda *a, **kw: calls.append((a, kw)))
    port = _free_port()
    assert serve.main(["--port", str(port)]) == 0
    assert calls == [(
        ("main:app",),
        {"host": "127.0.0.1", "port": port, "reload": False, "reload_dirs": None, "app_dir": str(serve.API_DIR)},
    )]
    assert isolated_environ["STITCH_FAKE_DB"] == "1"
    assert isolated_environ["JWT_SECRET"] == serve.DEV_JWT_SECRET
    assert "STITCH_AGENT" not in isolated_environ
    assert "Ctrl+C to stop" in capsys.readouterr().out


def test_main_reload_watches_the_api_dir(isolated_environ: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict] = []
    monkeypatch.setattr(serve.uvicorn, "run", lambda *a, **kw: calls.append(kw))
    isolated_environ["JWT_SECRET"] = "a-secret-the-developer-chose-0123456789"
    assert serve.main(["--port", str(_free_port()), "--reload"]) == 0
    assert calls[0]["reload"] is True and calls[0]["reload_dirs"] == [str(serve.API_DIR)]
    assert isolated_environ["JWT_SECRET"] == "a-secret-the-developer-chose-0123456789"


# ------------------------------------------------------------------------------ integration


class Server:
    def __init__(self, proc: subprocess.Popen, port: int, log_path: Path, startup: float) -> None:
        self.proc = proc
        self.port = port
        self.log_path = log_path
        self.startup = startup

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def log(self) -> str:
        try:
            return self.log_path.read_text(errors="replace")
        except OSError:
            return ""


def _child_env() -> dict[str, str]:
    env = dict(os.environ)
    for name in (*KEY_VARS, "JWT_SECRET", "STITCH_FAKE_DB", "STITCH_AGENT", "MONGODB_URI"):
        env.pop(name, None)
    env.update({"STITCH_NO_DOTENV": "1", "STITCH_FAKE_RUN_STEP": "0", "PYTHONUNBUFFERED": "1"})
    return env


def _stop(proc: subprocess.Popen) -> int | None:
    """SIGINT (Ctrl+C), then kill. The exit code after SIGINT, or None if it had to be killed."""
    if proc.poll() is not None:
        return proc.returncode
    proc.send_signal(signal.SIGINT)
    try:
        return proc.wait(timeout=SHUTDOWN_LIMIT)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=SHUTDOWN_LIMIT)
        return None


@contextmanager
def _serving(log_path: Path) -> Iterator[Server]:
    """Run `python -m seed.serve` on a free port until /health answers; always stop it after."""
    try:
        port = _free_port()
    except OSError as exc:
        pytest.skip(f"cannot bind a localhost socket here: {exc}")
    started = time.monotonic()
    with log_path.open("wb") as out:
        proc = subprocess.Popen(
            [sys.executable, "-m", "seed.serve", "--port", str(port)],
            cwd=serve.API_DIR, env=_child_env(), stdout=out, stderr=subprocess.STDOUT,
        )
    srv = Server(proc, port, log_path, 0.0)
    try:
        deadline = started + STARTUP_LIMIT
        while True:
            if proc.poll() is not None:
                pytest.fail(f"seed.serve exited with {proc.returncode} before /health answered:\n{srv.log()}")
            try:
                if httpx.get(f"{srv.base_url}/health", timeout=2.0).status_code == 200:
                    break
            except httpx.TransportError:
                pass
            if time.monotonic() > deadline:
                pytest.fail(f"/health did not answer 200 within {STARTUP_LIMIT:g}s:\n{srv.log()}")
            time.sleep(0.2)
        srv.startup = time.monotonic() - started
        yield srv
    finally:
        _stop(proc)


@pytest.fixture
def server(tmp_path: Path) -> Iterator[Server]:
    with _serving(tmp_path / "serve.log") as srv:
        yield srv


def _books_from_seed() -> dict[str, set[str]]:
    """Each person's seeded memories (a book lists the memories whose participants include them)."""
    books: dict[str, set[str]] = {pid: set() for pid in demo.DEMO_PERSON_IDS}
    for moment in demo.MOMENTS:
        for pid in moment.participant_ids:
            books[pid].add(moment.id)
    return books


def test_one_command_demo_over_real_http(server: Server) -> None:
    assert server.startup < STARTUP_LIMIT
    log = server.log()
    assert "Ctrl+C to stop; data resets on every restart" in log
    assert "STITCH_FAKE_DB: in-memory database with demo family 'demo_family'" in log
    assert "STITCH_AGENT=fake: scripted agent runs" in log

    expected_books = _books_from_seed()
    for account in demo.DEMO_ACCOUNTS:
        with httpx.Client(base_url=server.base_url, timeout=30.0) as http:
            res = http.post("/auth/login", json={"email": account.email, "password": demo.DEMO_PASSWORD})
            assert res.status_code == 200, (account.email, res.text)
            # No JWT_SECRET configured: the fixed dev secret signs it, so sessions survive --reload.
            claims = jwt.decode(res.cookies[COOKIE_NAME], serve.DEV_JWT_SECRET, algorithms=["HS256"])
            assert claims["sub"] == account.user_id

            me = http.get("/me")
            assert me.status_code == 200
            families = {f["id"]: f for f in me.json()["families"]}
            assert families[demo.DEMO_FAMILY_ID]["personId"] == account.person_id
            assert families[demo.DEMO_FAMILY_ID]["role"] == account.role

            tree = http.get(f"/families/{demo.DEMO_FAMILY_ID}/tree")
            assert tree.status_code == 200
            assert len(tree.json()["people"]) == len(demo.DEMO_PERSON_IDS)

            book = http.get(f"/families/{demo.DEMO_FAMILY_ID}/people/{account.person_id}/book")
            assert book.status_code == 200
            memories = book.json()["memories"]
            assert {m["id"] for m in memories} == expected_books[account.person_id]
            assert len(memories) == len(expected_books[account.person_id])
            assert all(m["timeline"] is not None for m in memories), account.email

    lines: list[str] = []
    opts = SmokeOptions(
        base_url=server.base_url,
        email=demo.DEMO_ACCOUNTS[0].email,
        password=demo.DEMO_PASSWORD,
        family_id=demo.DEMO_FAMILY_ID,
        text="We drove to the lake with the windows down and sang the whole way.",
        year=1991,
        question=None,
        photos=[],
        answer="Everyone was singing.",
        timeout=60.0,
    )
    with httpx.Client(base_url=server.base_url, timeout=30.0) as http:
        result = run_smoke(http, opts, log=lines.append)
    assert result.ok, (result.message, lines, server.log())

    started = time.monotonic()
    code = _stop(server.proc)
    assert code is not None, f"seed.serve ignored Ctrl+C for {SHUTDOWN_LIMIT:g}s and was killed"
    assert time.monotonic() - started < SHUTDOWN_LIMIT
    assert "Application shutdown complete" in server.log()


def test_a_demo_session_survives_a_restart(tmp_path: Path) -> None:
    """The fixed dev JWT_SECRET and stable demo user ids keep a browser signed in across restarts."""
    account = demo.DEMO_ACCOUNTS[2]
    with _serving(tmp_path / "first.log") as first:
        res = httpx.post(f"{first.base_url}/auth/login",
                         json={"email": account.email, "password": demo.DEMO_PASSWORD}, timeout=30.0)
        assert res.status_code == 200
        cookie = res.cookies[COOKIE_NAME]
    with _serving(tmp_path / "second.log") as second:
        me = httpx.get(f"{second.base_url}/me", cookies={COOKIE_NAME: cookie}, timeout=30.0)
        assert me.status_code == 200, me.text
        assert me.json()["user"]["id"] == account.user_id
