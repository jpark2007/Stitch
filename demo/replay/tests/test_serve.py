"""The replay app and launcher: api/main.py installs replay runs, the routes replay, bad stories stop it."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from typing import Any

import pytest
from fastapi.testclient import TestClient

import main
from seed import fake_runs
from seed.demo import DEMO_PASSWORD

from demo.replay import manager as replay_manager
from demo.replay.fixtures import load_stories
from demo.replay.serve import replay_lines
from demo.replay.tests.conftest import EXAMPLE_DIR, EXAMPLE_TEXT, FID, OTHER_TEXT, REPO_DIR

JWT = "stitch-replay-test-secret-0123456789abcdef0123"


@pytest.fixture
def replay_env(monkeypatch):
    monkeypatch.setenv("STITCH_FAKE_DB", "1")
    monkeypatch.setenv("STITCH_REPLAY_DIR", str(EXAMPLE_DIR))
    monkeypatch.setenv("STITCH_FAKE_RUN_STEP", "0")
    monkeypatch.setenv("JWT_SECRET", JWT)
    monkeypatch.delenv("COOKIE_SECURE", raising=False)
    monkeypatch.delenv("STITCH_AGENT", raising=False)
    # use_replay_runs rebinds this name; monkeypatch puts the scripted installer back afterwards.
    monkeypatch.setattr(fake_runs, "install_fake_runs", fake_runs.install_fake_runs)


def replay_app(*, patch: bool = True):
    if patch:
        replay_manager.use_replay_runs()
    app = main.create_app()
    replay_manager.wrap_lifespan(app, main.fake_run_step_seconds)
    return app


def installed() -> Any:
    return replay_manager._configured_manager()


def tell(c: TestClient, text: str, *, email: str = "david@demo.stitch", year: int = 1999) -> dict:
    """Submit a telling through the routes and answer every pause through the routes."""
    assert c.post("/auth/login", json={"email": email, "password": DEMO_PASSWORD}).status_code == 200
    res = c.post(f"/families/{FID}/memories", data={"text": text, "year": str(year)})
    assert res.status_code == 202, res.text
    run_id = res.json()["runId"]
    mgr = installed()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        status = mgr.wait(FID, run_id, timeout=5)
        if status in ("done", "error"):
            break
        name, payload = mgr.events(FID, run_id)[-1]
        if name == "question":
            body = {"kind": "answer", "text": "Papa was there."}
        elif name == "merge":
            body = {"kind": "merge", "approve": True}
        else:
            body = {"kind": "props", "props": [{"id": p.id, "action": "keep"} for p in payload.props]}
        answered = c.post(f"/families/{FID}/runs/{run_id}/answer", json=body)
        assert answered.status_code == 204, answered.text
    name, done = mgr.events(FID, run_id)[-1]
    assert name == "done", done
    memory = c.get(f"/families/{FID}/memories/{done.memory_id}")
    assert memory.status_code == 200, memory.text
    return memory.json()["memory"]


def test_the_app_installs_replay_runs_and_the_routes_replay_the_prepared_scene(replay_env):
    with TestClient(replay_app()) as c:
        assert isinstance(installed(), replay_manager.ReplayRunManager)
        assert [s.name for s in installed().stories] == ["sunday_tamales"]
        memory = tell(c, EXAMPLE_TEXT)
        assert memory["title"] == "Tamale Sundays" and memory["year"] == 1970
        assert memory["setIds"] == ["carmen_kitchen_1970"]
        assert "hymn_books" not in memory["propIds"]
        assert memory["timeline"]["defaultNarrator"] == "dad"

        other = tell(c, OTHER_TEXT)
        assert "hymn_books" in other["propIds"] and other["setIds"] == ["church_steps_1988"]
    assert installed() is None


def test_startup_puts_replay_runs_in_place_even_when_the_installer_was_not_rebound(replay_env, monkeypatch):
    monkeypatch.setenv("STITCH_AGENT", "fake")
    with TestClient(replay_app(patch=False)) as c:
        assert isinstance(installed(), replay_manager.ReplayRunManager)
        assert tell(c, EXAMPLE_TEXT)["setIds"] == ["carmen_kitchen_1970"]


def test_the_banner_names_the_stories_never_their_text():
    stories = load_stories(EXAMPLE_DIR)
    lines = "\n".join(replay_lines(stories, EXAMPLE_DIR))
    assert "1 prepared story from" in lines and "sunday_tamales" in lines
    assert "tamales," not in lines and "masa" not in lines


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _launch(*args: str, **env: str) -> subprocess.CompletedProcess:
    environ = {k: v for k, v in os.environ.items() if k != "STITCH_REPLAY_DIR"}
    environ.update({"STITCH_NO_DOTENV": "1", **env})
    return subprocess.run(
        [sys.executable, "-m", "demo.replay.serve", "--port", str(_free_port()), *args],
        cwd=REPO_DIR, env=environ, capture_output=True, text=True, timeout=60,
    )


def test_the_launcher_refuses_a_story_that_does_not_compile(tmp_path):
    import json

    from demo.replay.tests.test_fixtures import TAMALES, write_story

    sp = json.loads(TAMALES.read_text())
    sp["shots"][0]["actions"][1]["to"] = "nowhere_mark"
    write_story(tmp_path, "broken_kitchen", screenplay=sp)
    done = _launch("--replay-dir", str(tmp_path))
    assert done.returncode == 2, done.stderr
    assert "prepared story 'broken_kitchen'" in done.stderr and "nowhere_mark" in done.stderr
    assert "Application startup" not in done.stderr


def test_check_compiles_the_stories_and_exits():
    done = _launch("--check", "--replay-dir", str(EXAMPLE_DIR))
    assert done.returncode == 0, done.stderr
    assert "sunday_tamales" in done.stdout and "1 prepared story compiles" in done.stdout
    assert "masa" not in done.stdout + done.stderr


def test_the_launcher_refuses_a_missing_replay_dir(tmp_path):
    done = _launch(STITCH_REPLAY_DIR=str(tmp_path / "nope"))
    assert done.returncode == 2
    assert "STITCH_REPLAY_DIR is not a directory" in done.stderr


def test_the_launcher_refuses_a_port_in_use():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen()
        port = sock.getsockname()[1]
        environ = {**os.environ, "STITCH_NO_DOTENV": "1"}
        done = subprocess.run(
            [sys.executable, "-m", "demo.replay.serve", "--port", str(port)],
            cwd=REPO_DIR, env=environ, capture_output=True, text=True, timeout=60,
        )
    assert done.returncode == 1
    assert f"cannot listen on 127.0.0.1:{port}" in done.stderr
