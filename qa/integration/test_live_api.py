"""Exercise startup, multipart and SSE over TCP instead of TestClient."""

import json
from pathlib import Path
import subprocess
import sys
import time

import httpx


def test_child_environment_disables_dotenv_outside_pytest(api_process_environment, tmp_path):
    """Run current main's import guard against synthetic files, never real keys."""
    root = Path(__file__).resolve().parents[2]
    isolated_api = tmp_path / "api"
    isolated_api.mkdir()
    main_copy = isolated_api / "main.py"
    main_copy.write_text((root / "api/main.py").read_text())
    for directory in (tmp_path, isolated_api):
        (directory / ".env").write_text("STITCH_QA_SYNTHETIC_DOTENV=present\n")
    env = api_process_environment.copy()
    env["PYTHONPATH"] = str(root / "api")
    probe = """
import os, runpy, sys
import dotenv
assert 'pytest' not in sys.modules
def forbidden_load(*args, **kwargs):
    raise RuntimeError('synthetic dotenv loader invoked')
dotenv.load_dotenv = forbidden_load
state = runpy.run_path(sys.argv[1], run_name='qa_import_probe')
assert state['LOADED_ENV_FILES'] == []
assert 'STITCH_QA_SYNTHETIC_DOTENV' not in os.environ
"""
    command = [sys.executable, "-c", probe, str(main_copy)]
    result = subprocess.run(command, cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    # Positive control: without the child opt-out, current main attempts loading.
    env.pop("STITCH_NO_DOTENV", None)
    control = subprocess.run(command, cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30)
    assert control.returncode != 0
    assert "synthetic dotenv loader invoked" in control.stderr


def test_live_memory_stream_persists_to_both_books(live_api):
    with httpx.Client(base_url=live_api.url, timeout=10, trust_env=False) as client:
        login = client.post("/auth/login", json={"email": "sam@demo.stitch", "password": "stitch-demo"})
        assert login.status_code == 200, login.text
        assert login.json()["user"]["email"] == "sam@demo.stitch"
        story = "We sang on the church steps while the rain fell."
        submission = client.post("/families/demo_family/memories", files={
            "text": (None, story), "year": (None, "1998"), "participantIds": (None, "lily"),
        })
        assert submission.status_code == 202, submission.text
        run = f"/families/demo_family/runs/{submission.json()['runId']}"
        names = []
        memory_id = None
        deadline = time.monotonic() + 30
        with client.stream("GET", run + "/stream") as stream:
            assert stream.status_code == 200
            assert stream.headers["content-type"].startswith("text/event-stream")
            name, data = "", []
            for line in stream.iter_lines():
                assert time.monotonic() < deadline, "SSE exceeded 30s"
                if line.startswith("event:"):
                    name = line[6:].strip()
                elif line.startswith("data:"):
                    data.append(line[5:].strip())
                elif not line and data:
                    payload = json.loads("\n".join(data))
                    names.append(name)
                    assert len(names) < 100, "Unexpected unbounded event stream"
                    assert name != "error", payload
                    answer = None
                    if name == "question":
                        assert payload["text"]
                        answer = {"kind": "answer", "text": "Lily held the umbrella."}
                    elif name == "props_found":
                        assert payload["props"]
                        answer = {"kind": "props", "props": [
                            {"id": prop["id"], "action": "keep"} for prop in payload["props"]
                        ]}
                    elif name == "done":
                        memory_id = payload["memoryId"]
                        break
                    if answer:
                        response = client.post(run + "/answer", json=answer)
                        assert response.status_code == 204, response.text
                    name, data = "", []
        assert memory_id, f"Stream ended without done: {names}"
        assert [name for name in names if name in {"question", "props_found", "scene_ready", "done"}] == [
            "question", "props_found", "scene_ready", "done",
        ]
        for person in ("sam", "lily"):
            response = client.get(f"/families/demo_family/people/{person}/book")
            assert response.status_code == 200, response.text
            memory = next(m for m in response.json()["memories"] if m["id"] == memory_id)
            assert memory["year"] == 1998
            assert {"sam", "lily"} <= set(memory["participantIds"])
            assert memory["screenplay"] and memory["timeline"]
            assert story in memory["tellings"][0]["text"]
            assert "Lily held the umbrella." in memory["tellings"][0]["text"]
