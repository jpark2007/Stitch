"""Exercise startup, multipart and SSE over TCP instead of TestClient."""

import json
import time

import httpx


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
