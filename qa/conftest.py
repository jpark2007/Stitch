"""Disposable real HTTP servers; no application imports or injected dependencies."""

from contextlib import contextmanager
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import threading
import time

import httpx
import pytest
from playwright.sync_api import sync_playwright


class _Page(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"<!doctype html><title>Stitch browser boundary test</title>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


@contextmanager
def _origin():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Page)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@dataclass(frozen=True)
class LiveAPI:
    url: str
    allowed_origin: str
    denied_origin: str


@pytest.fixture(scope="session")
def live_api(tmp_path_factory):
    """Reserve the API socket until uvicorn inherits it (POSIX/macOS/Linux)."""
    root = Path(__file__).resolve().parents[1]
    scratch = tmp_path_factory.mktemp("stitch-live")
    # Allowlist rather than inherit database, model, proxy or tracing credentials.
    env = {key: os.environ[key] for key in ("PATH", "SYSTEMROOT", "LANG") if key in os.environ}
    env.update(
        HOME=str(scratch), TMPDIR=str(scratch), PYTHONDONTWRITEBYTECODE="1",
        STITCH_FAKE_DB="1", STITCH_AGENT="fake", STITCH_FAKE_RUN_STEP="0",
        JWT_SECRET=secrets.token_urlsafe(32), COOKIE_SECURE="0",
    )
    with _origin() as allowed, _origin() as denied, socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        url = f"http://127.0.0.1:{listener.getsockname()[1]}"
        env["STITCH_CORS_ORIGINS"] = allowed
        log_path = scratch / "uvicorn.log"
        with log_path.open("w+") as log:
            process = subprocess.Popen(
                [sys.executable, "-m", "uvicorn", "main:app", "--fd", str(listener.fileno())],
                cwd=root / "api", env=env, pass_fds=(listener.fileno(),),
                stdout=log, stderr=subprocess.STDOUT,
            )
            listener.close()
            try:
                deadline = time.monotonic() + 30
                with httpx.Client(base_url=url, timeout=0.5, trust_env=False) as client:
                    while process.poll() is None and time.monotonic() < deadline:
                        try:
                            response = client.get("/health")
                            if response.status_code == 200 and response.json() == {"status": "ok"}:
                                break
                        except httpx.TransportError:
                            pass
                        time.sleep(0.05)
                    else:
                        pytest.fail(f"API failed to start within 30s:\n{log_path.read_text()}")
                yield LiveAPI(url, allowed, denied)
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)


@pytest.fixture(scope="session")
def browser():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            yield browser
        finally:
            browser.close()


@pytest.fixture
def browser_context(browser):
    context = browser.new_context()
    context.set_default_timeout(10_000)
    try:
        yield context
    finally:
        context.close()
