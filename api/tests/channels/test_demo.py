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


@pytest.mark.parametrize("web", [False, True])
def test_scripted_worker_uses_real_offline_studio_to_save_book(isolated, web, monkeypatch):
    from tests.channels.test_inbox import sign
    from tests.channels.test_worker import Sender
    sender = Sender()
    monkeypatch.setenv("STITCH_WEB_URL", "https://unrelated-preview.invalid")
    app = channels_demo.create_app(worker=True, sender=sender, web=web, preview_port=3402)
    def tick(client):
        path = "/internal/channels/tick"
        response = client.post(path, content=b"{}", headers=sign(b"{}", path, secret=SECRET))
        assert response.status_code == 200
        assert response.json()["blocked"] == 0
    with TestClient(app) as client:
        existing = [row["_id"] for row in app.state.demo_db.memories.find({}, {"_id": 1})]
        family_id = "demo_family" if web else "terminal-family"
        if web:
            assert client.get("/families/demo_family/people/dad/book").status_code == 401
            login = client.post("/auth/login", json={"email": "david@demo.stitch", "password": "stitch-demo"})
            assert login.status_code == 200
            assert login.json()["families"][0]["id"] == family_id
            assert client.get(f"/families/{family_id}/tree").status_code == 200
            assert client.get(f"/families/{family_id}").status_code == 200
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
        memory = app.state.demo_db.memories.find_one({"familyId": family_id, "_id": {"$nin": existing}})
        assert memory is not None
        assert memory["tellings"][0]["photoIds"]
        assert all(m["provider"] == "terminal" and m["spaceId"] == "terminal" for m in sender.messages)
        if web:
            mid = memory["_id"]
            completed = [m["text"] for m in sender.messages if "/memories/" in m["text"]]
            assert any(f"http://127.0.0.1:3402/memories/{mid}?family=demo_family" in text for text in completed)
            assert all("unrelated-preview" not in text and "localhost:3000" not in text for text in completed)
            memory_path = f"/families/{family_id}/memories/{mid}"
            book_path = f"/families/{family_id}/people/dad/book"
            media_id = memory["tellings"][0]["photoIds"][0]
            media_path = f"/families/{family_id}/media/{media_id}"
            response = client.get(memory_path)
            assert response.status_code == 200
            bundle = response.json()
            assert bundle["memory"]["id"] == mid
            assert bundle["memory"]["timeline"] and bundle["memory"]["screenplay"]
            assert bundle["people"] and bundle["props"] and bundle["sets"]
            assert mid in [m["id"] for m in client.get(book_path).json()["memories"]]
            assert client.get(media_path).content == output.getvalue()
            assert client.post("/auth/logout").status_code == 204
            for path in (memory_path, book_path, media_path):
                assert client.get(path).status_code == 401
            assert client.post("/auth/signup", json={"email": "other@example.test", "password": "test-password", "name": "Other"}).status_code == 201
            for path in (memory_path, book_path, media_path):
                assert client.get(path).status_code == 404


def test_demo_ignores_inherited_worker_mode(isolated, monkeypatch):
    monkeypatch.setenv("STITCH_CHANNELS_WORKER", "1")
    monkeypatch.setenv("CHANNELS_CONVERSATION_MODE", "real")
    with TestClient(channels_demo.create_app()):
        assert os.environ["STITCH_CHANNELS_WORKER"] == "0"
    assert os.environ["STITCH_CHANNELS_WORKER"] == "1"


def test_web_mode_isolates_jwt_transcription_and_cors(isolated, monkeypatch):
    import auth
    monkeypatch.setenv("JWT_SECRET", "inherited-secret-not-for-demo-123456789")
    monkeypatch.setenv("COOKIE_SECURE", "1")
    monkeypatch.setenv("STITCH_CHANNELS_TRANSCRIPTION", "1")
    old = auth.create_session_token("demo_user_david")
    before = dict(os.environ)
    app = channels_demo.create_app(web=True)
    assert dict(os.environ) == before
    with TestClient(app) as client:
        assert os.environ["JWT_SECRET"] != before["JWT_SECRET"]
        assert os.environ["STITCH_CHANNELS_TRANSCRIPTION"] == "0"
        assert os.environ["COOKIE_SECURE"] == "0"
        assert auth.decode_session_token(old) is None
        allowed = client.get("/session", headers={"Origin": "http://127.0.0.1:3002"})
        assert allowed.headers["access-control-allow-origin"] == "http://127.0.0.1:3002"
        assert allowed.headers["access-control-allow-credentials"] == "true"
        assert "access-control-allow-origin" not in client.get("/session", headers={"Origin": "http://localhost:3000"}).headers
        assert client.post("/families/demo_family/memories", json={}).status_code in (404, 405)
        malformed = client.post("/auth/login", json={"email": {}, "password": "never-echo-this"})
        assert malformed.status_code == 422 and "never-echo-this" not in malformed.text
    assert dict(os.environ) == before


def test_web_cli_uses_separate_port_and_validates_port_options(isolated, monkeypatch):
    import uvicorn
    calls = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: calls.append(kwargs))
    assert channels_demo.main(["--web", "--worker"]) == 0
    assert calls[0]["port"] == 8012 and calls[0]["host"] == "127.0.0.1"
    for args in (["--preview-port", "0"], ["--bridge-port", "0"], ["--bridge-port", "65536"]):
        with pytest.raises(SystemExit):
            channels_demo.main(args)


def test_book_contact_fixture_refuses_unseeded_or_wrong_identity(isolated):
    import mongomock
    from db.contacts import seed_book_demo_contact
    with pytest.raises(ValueError, match="disposable"):
        seed_book_demo_contact(object())
    with pytest.raises(ValueError, match="matching"):
        seed_book_demo_contact(mongomock.MongoClient().demo)
    with TestClient(channels_demo.create_app(web=True)) as client:
        db = client.app.state.demo_db
        contact = db.contacts.find_one({"_id": "terminal-contact"})
        assert (contact["familyId"], contact["personId"], contact["userId"]) == ("demo_family", "dad", "demo_user_david")
        with pytest.raises(ValueError, match="already exists"):
            seed_book_demo_contact(db)
        db.people.update_one({"_id": "dad"}, {"$set": {"userId": "other"}})
        with pytest.raises(ValueError, match="matching"):
            seed_book_demo_contact(db)


def test_web_demo_mounts_settings_on_same_db_without_allowing_cloud_activation(isolated):
    from tests.channels.test_inbox import sign
    app = channels_demo.create_app(web=True)
    base = "/families/demo_family/channels"
    with TestClient(app) as client:
        assert client.get(base + "/contacts").status_code == 401
        assert client.post("/auth/login", json={"email": "david@demo.stitch", "password": "stitch-demo"}).status_code == 200
        contact = client.get(base + "/people/dad/contact").json()["contact"]
        assert contact["provider"] == "terminal" and contact["handle"] == "terminal-user"
        assert client.get("/families/unknown/channels/contacts").status_code == 404
        preflight = client.options(base + "/people/dad/contact", headers={
            "Origin": "http://127.0.0.1:3002", "Access-Control-Request-Method": "PATCH",
            "Access-Control-Request-Headers": "content-type"})
        assert preflight.status_code == 200
        assert preflight.headers["access-control-allow-origin"] == "http://127.0.0.1:3002"
        updated = client.patch(base + "/people/dad/contact", json={"timezone": "America/New_York", "cadenceDays": 7,
            "quietHours": {"start": "21:00", "end": "08:00"}})
        assert updated.status_code == 200
        assert app.state.demo_db.contacts.find_one({"_id": "terminal-contact"})["cadenceDays"] == 7
        # Linked relatives enroll their own contact, even when another member is an admin.
        assert client.post("/auth/login", json={"email": "ana@demo.stitch", "password": "stitch-demo"}).status_code == 200
        pending = client.post(base + "/people/mom/enrollment", json={"provider": "imessage", "handle": "+15555550123",
            "timezone": "America/New_York", "cadenceDays": 7, "quietHours": {"start": "21:00", "end": "08:00"}})
        assert pending.status_code == 201 and pending.json()["contact"]["status"] == "pending"
        path = "/internal/channels/enroll"
        raw = json.dumps({"messageId": "synthetic-proof", "provider": "imessage", "senderId": "+15555550123",
            "spaceId": "synthetic-direct", "spaceKind": "direct", "code": pending.json()["code"]}).encode()
        assert client.post(path, content=raw, headers={"content-type": "application/json"}).status_code == 401
        assert client.post(path, content=raw, headers=sign(raw, path, secret=SECRET)).status_code == 403
        assert client.get(base + "/people/mom/contact").json()["contact"]["status"] == "pending"
        assert os.environ["STITCH_CHANNELS_TRANSCRIPTION"] == "0"
        for path in ("/internal/channels/inbound-voice", "/internal/channels/authorize"):
            body = json.dumps({"provider": "imessage", "senderId": "+15555550123"}).encode()
            assert client.post(path, content=body, headers=sign(body, path, secret=SECRET)).status_code == 403
        assert client.post(base + "/people/mom/contact/stop", json={}).json()["contact"]["status"] == "stopped"


def test_default_worker_preview_url_is_isolated_from_inherited_url(isolated, monkeypatch):
    from tests.channels.test_worker import Sender
    monkeypatch.setenv("STITCH_WEB_URL", "https://unrelated-preview.invalid")
    app = channels_demo.create_app(worker=True, web=True, sender=Sender())
    with TestClient(app):
        assert app.state.channels_worker.web_url == "http://127.0.0.1:3002"
    assert os.environ["STITCH_WEB_URL"] == "https://unrelated-preview.invalid"
