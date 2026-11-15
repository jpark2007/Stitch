"""The launcher's app through the real routes: both families, replay for either, and Channels."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import main
from seed import fake, fake_runs
from seed.demo import DEMO_FAMILY_ID, DEMO_PASSWORD

from demo.replay import manager as replay_manager
from demo.replay.tests.conftest import EXAMPLE_DIR, EXAMPLE_TEXT
from demo.whitakers import wiring
from demo.whitakers.tests.conftest import CLEO, DEV, FID, FIXTURE_DIR, PASSWORD, PORCH_TEXT, edit_json

JWT = "stitch-whitakers-test-secret-0123456789abcdef"
SECRET = "whitakers-test-channels-secret-0123456789abcdef"


@pytest.fixture
def both_replays(tmp_path) -> Path:
    """A replay folder with the Maos' example story and the Testers' porch story."""
    folder = tmp_path / "replays"
    folder.mkdir()
    (folder / "sunday_tamales").symlink_to(EXAMPLE_DIR / "sunday_tamales", target_is_directory=True)
    (folder / "porch_books").symlink_to(FIXTURE_DIR / "replays" / "porch_books", target_is_directory=True)
    return folder


@pytest.fixture
def app_env(monkeypatch, both_replays):
    monkeypatch.setenv("STITCH_FAKE_DB", "1")
    monkeypatch.setenv("STITCH_WHITAKERS_DIR", str(FIXTURE_DIR))
    monkeypatch.setenv("STITCH_REPLAY_DIR", str(both_replays))
    monkeypatch.setenv("STITCH_FAKE_RUN_STEP", "0")
    monkeypatch.setenv("JWT_SECRET", JWT)
    monkeypatch.setenv("STITCH_CHANNELS", "0")
    monkeypatch.delenv("COOKIE_SECURE", raising=False)
    monkeypatch.delenv("STITCH_AGENT", raising=False)
    # The wiring rebinds these names; monkeypatch puts the originals back afterwards.
    monkeypatch.setattr(fake_runs, "install_fake_runs", fake_runs.install_fake_runs)
    monkeypatch.setattr(fake, "install_fake_db", fake.install_fake_db)
    monkeypatch.setattr(replay_manager, "REPLAY_FAMILIES", [DEMO_FAMILY_ID])


def build_app():
    wiring.seed_on_fake_db()
    replay_manager.use_replay_runs()
    app = main.create_app()
    replay_manager.wrap_lifespan(app, main.fake_run_step_seconds)
    wiring.wrap_channels(app)
    return app


def installed() -> Any:
    return replay_manager._configured_manager()


def tell(c: TestClient, fid: str, text: str, *, email: str, password: str) -> dict:
    """Submit a telling and answer every pause through the routes; the saved memory."""
    assert c.post("/auth/login", json={"email": email, "password": password}).status_code == 200
    res = c.post(f"/families/{fid}/memories", data={"text": text, "year": "2001"})
    assert res.status_code == 202, res.text
    run_id = res.json()["runId"]
    mgr = installed()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if mgr.wait(fid, run_id, timeout=5) in ("done", "error"):
            break
        name, payload = mgr.events(fid, run_id)[-1]
        if name == "question":
            body: dict[str, Any] = {"kind": "answer", "text": "Ada was at the window."}
        elif name == "merge":
            body = {"kind": "merge", "approve": True}
        else:
            body = {"kind": "props", "props": [{"id": p.id, "action": "keep"} for p in payload.props]}
        assert c.post(f"/families/{fid}/runs/{run_id}/answer", json=body).status_code == 204
    name, done = mgr.events(fid, run_id)[-1]
    assert name == "done", done
    memory = c.get(f"/families/{fid}/memories/{done.memory_id}")
    assert memory.status_code == 200, memory.text
    return memory.json()["memory"]


def test_a_fixture_account_signs_in_and_sees_its_family(app_env):
    with TestClient(build_app()) as c:
        assert c.post("/auth/login", json={"email": DEV, "password": PASSWORD}).status_code == 200
        tree = c.get(f"/families/{FID}/tree")
        assert tree.status_code == 200, tree.text
        assert {p["id"] for p in tree.json()["people"]} == {"ada", "ben", "cleo", "dev"}
        book = c.get(f"/families/{FID}/people/cleo/book")
        assert book.status_code == 200
        assert {m["id"] for m in book.json()["memories"]} == {"tamales_1975", "books_1999"}
        memory = c.get(f"/families/{FID}/memories/books_1999").json()["memory"]
        assert memory["timeline"] is not None
        photo = memory["tellings"][1]["photoIds"][0]
        assert c.get(f"/families/{FID}/media/{photo}").status_code == 200
        assert c.get(f"/families/{DEMO_FAMILY_ID}/tree").status_code == 404  # not a Mao
        assert c.get(f"/families/{FID}/threads").status_code == 200


def test_replay_ends_on_the_prepared_scene_in_either_family(app_env):
    with TestClient(build_app()) as c:
        mgr = installed()
        assert isinstance(mgr, replay_manager.ReplayRunManager)
        assert [s.name for s in mgr.stories] == ["porch_books", "sunday_tamales"]

        porch = tell(c, FID, PORCH_TEXT, email=CLEO, password=PASSWORD)
        assert porch["title"] == "The porch steps" and porch["year"] == 2001
        assert porch["setIds"] == ["tester_steps"] and "tester_books" in porch["propIds"]
        assert porch["timeline"]["defaultNarrator"] == "cleo"

        tamales = tell(c, DEMO_FAMILY_ID, EXAMPLE_TEXT, email="david@demo.stitch", password=DEMO_PASSWORD)
        assert tamales["setIds"] == ["carmen_kitchen_1970"]

        # Each family's story is only the other's scripted scene.
        crossed = tell(c, DEMO_FAMILY_ID, PORCH_TEXT, email="david@demo.stitch", password=DEMO_PASSWORD)
        assert "hymn_books" in crossed["propIds"]
        crossed = tell(c, FID, EXAMPLE_TEXT, email=CLEO, password=PASSWORD)
        assert f"hymn_books_{FID}" in crossed["propIds"]  # the scripted scene's copy, in this family


@pytest.fixture
def channels_env(app_env, monkeypatch, data_copy):
    edit_json(data_copy / "family.json", lambda f: f.update(contacts=[
        {"personId": "dev", "imessage": "dev-phone@testers.example"},
        {"personId": "ada", "imessage": "+15555550100"},
    ]))
    monkeypatch.setenv("STITCH_WHITAKERS_DIR", str(data_copy))
    monkeypatch.setenv("STITCH_CHANNELS", "1")
    monkeypatch.setenv("STITCH_CHANNELS_WORKER", "1")
    monkeypatch.setenv("STITCH_CHANNELS_TRANSCRIPTION", "0")
    monkeypatch.setenv("CHANNELS_SHARED_SECRET", SECRET)


def test_channels_runs_on_the_replay_manager_and_enrolls_contacts_pending(channels_env, capsys):
    from channels.worker import ChannelWorker

    from agent.conversation.demo import ScriptedConversation
    from db import client

    with TestClient(build_app()) as c:
        worker = c.app.state.channels_worker
        assert isinstance(worker, ChannelWorker)
        assert worker.runs is installed() and isinstance(worker.runs, replay_manager.ReplayRunManager)
        assert worker.agent_factory is ScriptedConversation
        rows = {r["personId"]: r for r in client.get_db().contacts.find({"familyId": FID})}
        assert set(rows) == {"dev", "ada"}
        for row in rows.values():
            assert row["status"] == "pending" and row["optInAt"] is None and row["verifiedAt"] is None
        # A linked person enrolls as its own user; an unlinked one as the admin.
        assert rows["dev"]["enrollmentInitiator"] == "testers_user_dev"
        assert rows["ada"]["enrollmentInitiator"] == "testers_user_cleo"
    out = capsys.readouterr().out
    assert "LINK " in out and "dev" in out
    assert "dev-phone@testers.example" not in out and "+15555550100" not in out


def test_without_channels_there_is_no_worker(app_env):
    with TestClient(build_app()) as c:
        assert getattr(c.app.state, "channels_worker", None) is None
