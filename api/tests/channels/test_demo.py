"""Standalone terminal server: disposable data, no inherited service configuration."""

import hashlib
import hmac
import importlib
import json
import os
import time
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from db import client as db_client
from seed import channels_demo

SECRET = "terminal-demo-test-secret-" * 3


def post(client, text="A local story", **changes):
    body = json.dumps({"messageId": str(uuid4()), "provider": "terminal", "spaceId": "terminal",
                       "senderId": "terminal-user", "text": text, **changes}).encode()
    timestamp, nonce = str(int(time.time())), str(uuid4())
    path = "/internal/channels/inbound"
    signed = f"POST\n{path}\n{timestamp}\n{nonce}\n".encode() + body
    return client.post(path, content=body, headers={
        "X-Channels-Timestamp": timestamp, "X-Channels-Nonce": nonce,
        "X-Channels-Signature": hmac.new(SECRET.encode(), signed, hashlib.sha256).hexdigest(),
        "content-type": "application/json",
    })


@pytest.fixture
def isolated(monkeypatch):
    monkeypatch.setenv("CHANNELS_SHARED_SECRET", SECRET)
    monkeypatch.setattr(db_client, "_db_override", None)
    monkeypatch.setenv("MONGODB_URI", "mongodb://must-not-be-contacted.invalid")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "must-not-be-used")
    monkeypatch.setenv("STITCH_AGENT", "real")
    monkeypatch.setenv("STITCH_CHANNELS", "0")
    monkeypatch.setattr(db_client, "get_client", lambda: pytest.fail("real MongoDB client requested"))


def test_import_and_factory_do_not_change_environment_or_start_db(isolated):
    before = dict(os.environ)
    importlib.reload(channels_demo)
    channels_demo.create_app()
    assert dict(os.environ) == before
    assert db_client._db_override is None


def test_receipt_stop_disposal_and_restart(isolated):
    app = channels_demo.create_app()
    with TestClient(app) as client:
        assert os.environ["STITCH_CHANNELS"] == "1"
        assert post(client).json() == {"accepted": True, "duplicate": False}
        assert post(client, "STOP").json()["stopped"] is True
        assert post(client).status_code == 403
        db = app.state.demo_db
        assert db.contacts.count_documents({}) == 1
        assert db.memories.count_documents({}) == 0
    assert db_client._db_override is None
    assert os.environ["STITCH_CHANNELS"] == "0"
    assert db.contacts.count_documents({}) == 0
    with TestClient(channels_demo.create_app()) as client:
        assert post(client).status_code == 202


def test_cloud_messages_cannot_enter_even_with_valid_signature(isolated):
    with TestClient(channels_demo.create_app()) as client:
        assert post(client, provider="imessage").status_code == 403
        assert post(client, senderId="+15555555555").status_code == 404


def test_auth_and_disabled_gate_still_apply(isolated, monkeypatch):
    with TestClient(channels_demo.create_app()) as client:
        assert client.post("/internal/channels/inbound", json={}).status_code == 401
        monkeypatch.setenv("STITCH_CHANNELS", "0")
        assert post(client).status_code == 404


def test_does_not_overwrite_another_database(isolated, monkeypatch):
    sentinel = object()
    monkeypatch.setattr(db_client, "_db_override", sentinel)
    with pytest.raises(RuntimeError, match="own process"):
        with TestClient(channels_demo.create_app()):
            pass
    assert db_client._db_override is sentinel


def test_shutdown_leaves_new_override_alone(isolated):
    sentinel = object()
    with TestClient(channels_demo.create_app()):
        db_client.set_db(sentinel)
    assert db_client._db_override is sentinel


@pytest.mark.parametrize("secret", ["", "short"])
def test_cli_requires_secret_without_echoing_value(isolated, monkeypatch, capsys, secret):
    monkeypatch.setenv("CHANNELS_SHARED_SECRET", secret)
    with pytest.raises(SystemExit) as error:
        channels_demo.main([])
    assert error.value.code == 2
    assert "at least 32" in capsys.readouterr().err
    assert db_client._db_override is None


@pytest.mark.parametrize("args", [["--port", "0"], ["--port", "65536"], ["--host", "0.0.0.0"]])
def test_cli_rejects_invalid_port_or_public_host(isolated, args):
    with pytest.raises(SystemExit) as error:
        channels_demo.main(args)
    assert error.value.code == 2


def test_cli_binds_only_loopback_and_never_logs_secret(isolated, monkeypatch, capsys):
    import uvicorn

    calls = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: calls.append(kwargs))
    assert channels_demo.main(["--port", "8011"]) == 0
    assert calls == [{"host": "127.0.0.1", "port": 8011, "access_log": False}]
    output = capsys.readouterr().out
    assert "http://127.0.0.1:8011" in output
    assert "Receipts are not stories" in output
    assert SECRET not in output


def test_scripted_worker_uses_real_offline_studio_to_save_book(isolated):
    from tests.channels.test_inbox import sign
    from tests.channels.test_worker import Sender
    sender = Sender()
    app = channels_demo.create_app(worker=True, sender=sender)
    def tick(client):
        path = "/internal/channels/tick"
        response = client.post(path, content=b"{}", headers=sign(b"{}", path, secret=SECRET))
        assert response.status_code == 200
        assert response.json()["blocked"] == 0
    with TestClient(app) as client:
        import base64
        import io
        from PIL import Image
        output = io.BytesIO()
        Image.new("RGB", (2, 2), "red").save(output, format="PNG")
        path = "/internal/channels/inbound-photo"
        body = json.dumps({"messageId": "demo-photo", "provider": "terminal", "spaceId": "terminal",
            "senderId": "terminal-user", "attachmentId": "photo", "mimeType": "image/png",
            "dataBase64": base64.b64encode(output.getvalue()).decode()}).encode()
        assert client.post(path, content=body, headers=sign(body, path, secret=SECRET)).status_code == 202
        tick(client)
        for text in ("We dropped the hymn books on the church steps.", "YEAR 1998", "SUBMIT"):
            assert post(client, text).status_code == 202
            tick(client)
        assert "Who else was there" in sender.messages[-1]["text"]
        assert post(client, "I was alone.").status_code == 202
        tick(client)
        assert "Reply KEEP" in sender.messages[-1]["text"]
        assert post(client, "KEEP").status_code == 202
        tick(client)
        for _ in range(5):
            if any("/memories/" in m["text"] for m in sender.messages):
                break
            tick(client)
        assert any("/memories/" in m["text"] for m in sender.messages)
        memory = app.state.demo_db.memories.find_one({"familyId": "terminal-family"})
        assert memory is not None
        assert memory["tellings"][0]["photoIds"]
        assert all(m["provider"] == "terminal" and m["spaceId"] == "terminal" for m in sender.messages)


def test_demo_ignores_inherited_worker_mode(isolated, monkeypatch):
    monkeypatch.setenv("STITCH_CHANNELS_WORKER", "1")
    monkeypatch.setenv("CHANNELS_CONVERSATION_MODE", "real")
    with TestClient(channels_demo.create_app()):
        assert os.environ["STITCH_CHANNELS_WORKER"] == "0"
    assert os.environ["STITCH_CHANNELS_WORKER"] == "1"
