"""Opt-in real browser/API/studio rehearsal. No installs, live providers or HTTP mocks."""

from __future__ import annotations

import base64
from contextlib import contextmanager
import hashlib
import hmac
import io
import json
import os
from pathlib import Path
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import time
from urllib.parse import quote
from uuid import uuid4

import pytest


pytestmark = pytest.mark.skipif(
    os.environ.get("STITCH_CHANNELS_BROWSER_TEST") != "1",
    reason="Opt in with STITCH_CHANNELS_BROWSER_TEST=1; see channels/TESTING.md for browser prerequisites",
)

ROOT = Path(__file__).resolve().parents[3]
API = ROOT / "api"
WEB = ROOT / "web"

# This private child harness composes the public demo factory, without adding a
# production debug route or importing main.py. Only synthetic terminal replies
# are captured; no bridge, provider, model, or transcription client is called.
HARNESS = """
import json, os
from pathlib import Path
import uvicorn
from seed.channels_demo import create_app

class CaptureSender:
    def send(self, message):
        if message['provider'] != 'terminal' or message['spaceId'] != 'terminal':
            raise RuntimeError('Browser rehearsal accepts only its terminal fixture')
        with Path(os.environ['CHANNELS_TEST_CAPTURE']).open('a', encoding='utf-8') as f:
            f.write(json.dumps(message) + '\\n')
            f.flush()

app = create_app(web=True, worker=True, sender=CaptureSender(),
                 preview_port=int(os.environ['CHANNELS_TEST_WEB_PORT']))
uvicorn.run(app, fd=int(os.environ['CHANNELS_TEST_API_FD']), access_log=False,
            log_level='warning')
"""


def _prerequisites():
    if os.name != "posix":
        pytest.fail("This opt-in process-isolation test currently requires macOS or Linux.")
    node, npm = shutil.which("node"), shutil.which("npm")
    if not node or not npm:
        pytest.fail("Install Node.js 20.9+ and npm before enabling the Channels browser test.")
    version = subprocess.run([node, "--version"], capture_output=True, text=True, timeout=5, check=True).stdout.strip().lstrip("v")
    parts = tuple(int(p) for p in version.split(".")[:2])
    if parts < (20, 9):
        pytest.fail("The existing Next.js frontend requires Node.js 20.9+.")
    next_bin = WEB / "node_modules/next/dist/bin/next"
    if not next_bin.is_file():
        pytest.fail(f"Frontend dependencies are missing. Run `cd {WEB} && npm ci` separately; pytest never installs them.")
    if (WEB / ".next/dev/lock").exists():
        pytest.fail("A Next dev lock exists in this checkout. Use a separate worktree; do not stop another agent's server.")
    for name in (".env", ".env.local", ".env.development", ".env.development.local"):
        if (WEB / name).exists():
            pytest.fail("Use an isolated frontend checkout without dotenv files for this synthetic browser test.")
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        pytest.fail("Playwright is missing. Run `uv sync --locked` and `uv run playwright install chromium` before opting in.")
    return node, next_bin, sync_playwright


def _environment() -> dict[str, str]:
    # An allowlist rather than deleting known key names: future provider keys,
    # proxy credentials, NODE_OPTIONS and dotenv settings must not leak through.
    env = {key: os.environ[key] for key in ("PATH", "SYSTEMROOT", "WINDIR", "TMPDIR") if key in os.environ}
    env.update(LANG="en_US.UTF-8", LC_ALL="en_US.UTF-8",
               STITCH_NO_DOTENV="1", STITCH_FAKE_DB="1", STITCH_AGENT="fake",
               STITCH_CHANNELS="1", STITCH_CHANNELS_WORKER="1", STITCH_CHANNELS_TRANSCRIPTION="0",
               CHANNELS_CONVERSATION_MODE="scripted", STITCH_CHANNELS_SCHEDULER="0",
               NEXT_TELEMETRY_DISABLED="1", NEXT_PUBLIC_API_MOCK="0", NO_COLOR="1")
    return env


def _reserve_port():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    return sock


def _stop_owned(process):
    if process is None:
        return
    # Every child was started in its own new session. Kill only that owned
    # process group, including Next's child, never a port or executable pattern.
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=5)


@contextmanager
def _servers(tmp_path, node, next_bin):
    import httpx

    tmp_path.chmod(0o700)
    harness = tmp_path / "serve_demo.py"
    harness.write_text(HARNESS, encoding="utf-8")
    capture = tmp_path / "terminal_replies.jsonl"
    capture.touch(mode=0o600)
    shared_secret = secrets.token_hex(32)
    # API FD stays reserved across spawn. Next cannot inherit a listening FD;
    # retry its brief port-release race, checking our own child and ready log.
    for attempt in range(3):
        api_socket, web_socket = _reserve_port(), _reserve_port()
        api_port, web_port = api_socket.getsockname()[1], web_socket.getsockname()[1]
        api_url, web_url = f"http://127.0.0.1:{api_port}", f"http://127.0.0.1:{web_port}"
        env = _environment()
        env.update(CHANNELS_SHARED_SECRET=shared_secret, PYTHONPATH=str(API),
                   CHANNELS_TEST_CAPTURE=str(capture), CHANNELS_TEST_API_FD=str(api_socket.fileno()),
                   CHANNELS_TEST_WEB_PORT=str(web_port), NEXT_PUBLIC_API_URL=api_url)
        api_log, web_log = tmp_path / f"api-{attempt}.log", tmp_path / f"web-{attempt}.log"
        api_process = web_process = None
        retry = False
        with api_log.open("wb") as api_output, web_log.open("wb") as web_output:
            try:
                api_process = subprocess.Popen([sys.executable, str(harness)], cwd=API, env=env,
                    pass_fds=(api_socket.fileno(),), start_new_session=True, stdout=api_output, stderr=subprocess.STDOUT)
                api_socket.close()
                web_socket.close()
                # Do not pass the internal HMAC secret or Python fixture details
                # to the frontend process, even though the secret is synthetic.
                web_env = _environment()
                web_env["NEXT_PUBLIC_API_URL"] = api_url
                web_process = subprocess.Popen([node, str(next_bin), "dev", "--hostname", "127.0.0.1", "--port", str(web_port)],
                    cwd=WEB, env=web_env, start_new_session=True, stdout=web_output, stderr=subprocess.STDOUT)
                deadline = time.monotonic() + 90
                with httpx.Client(timeout=2, trust_env=False) as probe:
                    while time.monotonic() < deadline:
                        if api_process.poll() is not None:
                            pytest.fail(f"Owned demo API exited during startup. Inspect private log {api_log}.")
                        if web_process.poll() is not None:
                            retry = "EADDRINUSE" in web_log.read_text(errors="replace")
                            if retry:
                                break
                            pytest.fail(f"Owned Next server exited. Inspect private log {web_log}; dependencies are never installed by this test.")
                        try:
                            api_ready = probe.get(api_url + "/openapi.json").status_code == 200
                        except httpx.HTTPError:
                            api_ready = False
                        # A foreign listener winning the race is not readiness.
                        if api_ready and "Ready in" in web_log.read_text(errors="replace"):
                            break
                        time.sleep(0.1)
                    else:
                        pytest.fail(f"Local servers did not become ready within 90s. Inspect {api_log} and {web_log}.")
                if not retry:
                    yield api_url, web_url, shared_secret, capture
                    return
            finally:
                api_socket.close()
                web_socket.close()
                _stop_owned(web_process)
                _stop_owned(api_process)
        if not retry:
            break
    pytest.fail("Three unused-port attempts raced with another listener; no existing server was stopped.")


def _signed(client, secret, path, payload):
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()
    timestamp, nonce = str(int(time.time())), str(uuid4())
    signature = hmac.new(secret.encode(), f"POST\n{path}\n{timestamp}\n{nonce}\n".encode() + raw, hashlib.sha256).hexdigest()
    return client.post(path, content=raw, headers={"Content-Type": "application/json",
        "X-Channels-Timestamp": timestamp, "X-Channels-Nonce": nonce, "X-Channels-Signature": signature})


def test_terminal_photo_story_book_and_pending_consent_in_real_browser(tmp_path):
    """Real HTTP and Chromium; the existing ScriptedRunManager is explicitly fake."""
    import httpx
    from PIL import Image

    node, next_bin, sync_playwright = _prerequisites()
    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(headless=True)
        except Exception:
            pytest.fail("Chromium could not launch. Run `uv run playwright install chromium` (and required OS browser libraries) before opting in.")
        try:
            with _servers(tmp_path, node, next_bin) as (api_url, web_url, secret, capture):
                context = browser.new_context(viewport={"width": 1440, "height": 1000})
                page = context.new_page()
                page.set_default_timeout(30_000)
                errors, console_errors = [], []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)

                # Exercise the actual login form and session cookie, not a fake
                # /me response or a cookie inserted directly by the test.
                target = "/settings/notifications?family=demo_family"
                page.goto(web_url + "/login?next=" + quote(target, safe=""), wait_until="networkidle", timeout=90_000)
                page.get_by_label("Email", exact=True).fill("david@demo.stitch")
                page.get_by_label("Password", exact=True).fill("stitch-demo")
                page.get_by_role("button", name="Log in", exact=True).click()
                page.get_by_role("heading", name="Stories by message", exact=True).wait_for()
                assert context.request.get(api_url + "/me").status == 200

                def replies():
                    return [json.loads(line) for line in capture.read_text().splitlines() if line]

                with httpx.Client(base_url=api_url, timeout=30, trust_env=False) as transport:
                    def tick():
                        response = _signed(transport, secret, "/internal/channels/tick", {})
                        assert response.status_code == 200
                        assert response.json()["blocked"] == 0

                    def text(value):
                        response = _signed(transport, secret, "/internal/channels/inbound", {
                            "messageId": uuid4().hex, "provider": "terminal", "spaceId": "terminal",
                            "senderId": "terminal-user", "text": value})
                        assert response.status_code == 202
                        tick()

                    def await_reply(fragment):
                        deadline = time.monotonic() + 15
                        while time.monotonic() < deadline:
                            if any(fragment in row["text"] for row in replies()):
                                return
                            tick()
                            time.sleep(0.05)
                        pytest.fail(f"Scripted studio did not emit expected rehearsal reply: {fragment}")

                    png = io.BytesIO()
                    Image.new("RGB", (12, 12), "#b54a31").save(png, format="PNG")
                    photo = _signed(transport, secret, "/internal/channels/inbound-photo", {
                        "messageId": uuid4().hex, "provider": "terminal", "spaceId": "terminal", "senderId": "terminal-user",
                        "attachmentId": "synthetic-photo", "mimeType": "image/png", "dataBase64": base64.b64encode(png.getvalue()).decode()})
                    assert photo.status_code == 202
                    tick()
                    story = "We dropped the hymn books on the church steps during our browser rehearsal."
                    for words in (story, "YEAR 1998", "SUBMIT"):
                        text(words)
                    await_reply("Who else was there")
                    text("I was alone.")
                    await_reply("Reply KEEP")
                    text("KEEP")
                    await_reply("/memories/")

                    book_path = "/families/demo_family/people/dad/book"
                    response = context.request.get(api_url + book_path)
                    assert response.status == 200
                    matches = [m for m in response.json()["memories"] if any(story in t["text"] for t in m["tellings"])]
                    assert len(matches) == 1
                    memory = matches[0]
                    assert memory["timeline"] and memory["screenplay"]
                    photo_ids = memory["tellings"][0]["photoIds"]
                    assert len(photo_ids) == 1
                    media = context.request.get(api_url + "/families/demo_family/media/" + photo_ids[0])
                    assert media.status == 200 and media.body() == png.getvalue()
                    expected = web_url + "/memories/" + memory["id"] + "?family=demo_family"
                    assert any(expected in row["text"] for row in replies())

                    page.goto(expected + "&state=open&t=2", wait_until="networkidle", timeout=60_000)
                    page.get_by_text("opening the book…", exact=True).wait_for(state="hidden", timeout=45_000)
                    page.get_by_label("A handmade storybook on a wooden table", exact=True).wait_for(state="visible")
                    assert page.locator("canvas").count() >= 1
                    # Book3D keeps its closed hint in the DOM at opacity zero.
                    # Its spread navigation becomes interactive only when open.
                    page.wait_for_function("""() => {
                        const nav = document.querySelector('nav[aria-label="Spreads"]');
                        return nav && getComputedStyle(nav).pointerEvents === 'auto'
                            && Number(getComputedStyle(nav).opacity) > 0.99;
                    }""", timeout=15_000)
                    page.wait_for_timeout(1000)  # Allow the deterministic open spread's final paint.
                    page.screenshot(path=str(tmp_path / "book.png"), full_page=True)
                    assert not errors and not console_errors, "Browser reported an error while opening the saved book"

                    page.goto(web_url + target, wait_until="networkidle")
                    page.get_by_label("Whose stories?", exact=True).select_option("grandma_rosa")
                    page.get_by_label("Phone number or iMessage email", exact=True).fill("+15555550177")
                    page.get_by_role("button", name="Create opt-in code", exact=True).click()
                    page.get_by_text("Waiting for opt-in", exact=True).wait_for()
                    code = page.locator("code").inner_text().removeprefix("LINK ")
                    # Valid HMAC is insufficient to turn this offline terminal
                    # demo into a cloud enrollment service.
                    proof = _signed(transport, secret, "/internal/channels/enroll", {
                        "messageId": uuid4().hex, "provider": "imessage", "senderId": "+15555550177",
                        "spaceId": "synthetic-dm", "spaceKind": "direct", "code": code})
                    assert proof.status_code == 403
                    contact_path = "/families/demo_family/channels/people/grandma_rosa/contact"
                    assert context.request.get(api_url + contact_path).json()["contact"]["status"] == "pending"
                    page.get_by_label("Days between prompts", exact=True).fill("11")
                    page.get_by_role("button", name="Save preferences", exact=True).click()
                    page.get_by_text("Preferences saved.", exact=True).wait_for()
                    assert context.request.get(api_url + contact_path).json()["contact"]["cadenceDays"] == 11
                    page.screenshot(path=str(tmp_path / "pending-settings.png"), full_page=True, mask=[page.locator("code")])
                    page.get_by_role("button", name="Stop messages", exact=True).click()
                    page.get_by_text("Stopped", exact=True).wait_for()
                    assert context.request.get(api_url + contact_path).json()["contact"]["status"] == "stopped"
                    page.set_viewport_size({"width": 390, "height": 844})
                    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
                    page.screenshot(path=str(tmp_path / "stopped-settings-mobile.png"), full_page=True)
                    assert context.request.get(api_url + "/families/not-this-family/channels/contacts").status == 404
                    anonymous = playwright.request.new_context()
                    try:
                        assert anonymous.get(api_url + book_path).status == 401
                        assert anonymous.get(api_url + contact_path).status == 401
                    finally:
                        anonymous.dispose()
                    assert not errors and not console_errors, "Browser reported an error during Channels settings"
        finally:
            browser.close()
