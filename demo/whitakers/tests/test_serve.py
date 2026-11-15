"""The launcher: its environment, --check and its exit codes."""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from demo.whitakers.serve import environment
from demo.whitakers.tests.conftest import FIXTURE_DIR, REPO_DIR, edit_json

SECRET = "whitakers-test-channels-secret-0123456789abcdef"


def args(**kw) -> argparse.Namespace:
    base = {"data_dir": None, "replay_dir": None, "channels": False}
    base.update(kw)
    return argparse.Namespace(**base)


def test_the_environment_points_replay_at_the_data_folder(tmp_path):
    env = environment(args(data_dir=str(FIXTURE_DIR)), {})
    assert env["STITCH_AGENT"] == "fake" and env["STITCH_CHANNELS"] == "0"
    assert env["STITCH_WHITAKERS_DIR"] == str(FIXTURE_DIR.resolve())
    assert env["STITCH_REPLAY_DIR"] == str(FIXTURE_DIR.resolve() / "replays")
    # An explicit replay folder wins; so does STITCH_REPLAY_DIR already set.
    assert environment(args(data_dir=str(FIXTURE_DIR), replay_dir=str(tmp_path)), {})["STITCH_REPLAY_DIR"] == str(
        tmp_path.resolve()
    )
    assert "STITCH_REPLAY_DIR" not in environment(args(data_dir=str(FIXTURE_DIR)), {"STITCH_REPLAY_DIR": "/x"})
    # No --data-dir: STITCH_WHITAKERS_DIR.
    assert environment(args(), {"STITCH_WHITAKERS_DIR": str(tmp_path)})["STITCH_WHITAKERS_DIR"] == str(
        tmp_path.resolve()
    )


def test_channels_needs_the_shared_secret_and_mounts_as_the_channels_demo(monkeypatch):
    monkeypatch.delenv("CHANNELS_SHARED_SECRET", raising=False)
    with pytest.raises(ValueError, match="CHANNELS_SHARED_SECRET"):
        environment(args(channels=True), {})
    monkeypatch.setenv("CHANNELS_SHARED_SECRET", SECRET)
    env = environment(args(channels=True), {})
    assert (env["STITCH_CHANNELS"], env["STITCH_CHANNELS_WORKER"], env["STITCH_CHANNELS_TRANSCRIPTION"]) == (
        "1", "1", "0"
    )
    assert environment(args(channels=True), {"STITCH_CHANNELS_TRANSCRIPTION": "1"})["STITCH_CHANNELS_TRANSCRIPTION"] == "1"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def launch(*argv: str, **env: str) -> subprocess.CompletedProcess:
    environ = {k: v for k, v in os.environ.items()
               if k not in ("STITCH_REPLAY_DIR", "STITCH_WHITAKERS_DIR", "CHANNELS_SHARED_SECRET")}
    environ.update({"STITCH_NO_DOTENV": "1", **env})
    return subprocess.run(
        [sys.executable, "-m", "demo.whitakers.serve", "--port", str(_free_port()), *argv],
        cwd=REPO_DIR, env=environ, capture_output=True, text=True, timeout=120,
    )


def test_check_compiles_the_family_and_its_stories_and_names_no_data():
    done = launch("--check", "--data-dir", str(FIXTURE_DIR))
    assert done.returncode == 0, done.stderr
    assert "The Testers (testers): 4 people, 2 moments, 2 accounts, 2 photos, 2 art files" in done.stdout
    assert "porch_books" in done.stdout and "every prepared story compile" in done.stdout
    output = done.stdout + done.stderr
    for secret in ("tester-pass", "cleo@testers.example", "library books when", "Cleo Tester"):
        assert secret not in output


def test_check_exits_2_naming_family_errors_and_story_file_errors(data_copy: Path):
    edit_json(data_copy / "moments" / "books_1999" / "screenplay.json",
              lambda sp: sp["shots"][0]["start"]["cleo"].update(at="nowhere_mark"))
    (data_copy / "replays" / "broken_json").mkdir()
    (data_copy / "replays" / "broken_json" / "story.json").write_text("{")
    done = launch("--check", "--data-dir", str(data_copy))
    assert done.returncode == 2, done.stdout + done.stderr
    assert "moments/books_1999: the screenplay does not compile" in done.stderr and "nowhere_mark" in done.stderr
    assert "replays: prepared story 'broken_json'" in done.stderr
    assert "replays: not compiled until the family loads" in done.stderr


def test_check_exits_2_for_a_story_that_does_not_compile(data_copy: Path):
    edit_json(data_copy / "replays" / "porch_books" / "story.json",
              lambda s: s.update(screenplayFile="screenplay.json"))
    sp = json.loads((data_copy / "moments" / "books_1999" / "screenplay.json").read_text())
    sp["shots"][0]["start"]["dev"]["at"] = "missing_mark"
    (data_copy / "replays" / "porch_books" / "screenplay.json").write_text(json.dumps(sp))
    done = launch("--check", "--data-dir", str(data_copy))
    assert done.returncode == 2, done.stdout + done.stderr
    assert "replays/porch_books (family testers)" in done.stderr and "missing_mark" in done.stderr


def test_a_missing_data_folder_exits_2(tmp_path):
    done = launch("--check", "--data-dir", str(tmp_path / "nope"))
    assert done.returncode == 2
    assert "the data directory does not exist" in done.stderr


def test_channels_without_a_secret_exits_2():
    done = launch("--check", "--channels", "--data-dir", str(FIXTURE_DIR))
    assert done.returncode == 2
    assert "CHANNELS_SHARED_SECRET" in done.stderr


def test_check_with_channels_validates_contacts(data_copy: Path):
    edit_json(data_copy / "family.json", lambda f: f.update(contacts=[
        {"personId": "dev", "imessage": "+15555550100", "timezone": "Not/AZone"},
    ]))
    done = launch("--check", "--channels", "--data-dir", str(data_copy), CHANNELS_SHARED_SECRET=SECRET)
    assert done.returncode == 2, done.stdout + done.stderr
    assert "contacts[0] (dev): not a valid enrollment (timezone)" in done.stderr
    assert "+15555550100" not in done.stderr


def test_the_launcher_refuses_a_port_in_use():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen()
        port = sock.getsockname()[1]
        environ = {k: v for k, v in os.environ.items() if k not in ("STITCH_REPLAY_DIR", "STITCH_WHITAKERS_DIR")}
        environ["STITCH_NO_DOTENV"] = "1"
        done = subprocess.run(
            [sys.executable, "-m", "demo.whitakers.serve", "--port", str(port), "--data-dir", str(FIXTURE_DIR)],
            cwd=REPO_DIR, env=environ, capture_output=True, text=True, timeout=120,
        )
    assert done.returncode == 1
    assert "is the API already running" in done.stderr
