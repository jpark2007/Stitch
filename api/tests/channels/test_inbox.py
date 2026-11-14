"""Internal boundary tests: no model, provider, database server, or credentials."""

import hashlib
import hmac
import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from uuid import uuid4

import mongomock
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from channels import ensure_channel_indexes
from channels.routes import router
from db import client as db_client
from db import conversations

SECRET = "test-channel-secret-" * 3
PATH = "/internal/channels/inbound"


def envelope(**changes):
    return {"messageId": "message-1", "provider": "terminal", "spaceId": "room-1",
            "senderId": "alice", "text": "My grandmother taught me to bake.", **changes}


def sign(body, path=PATH, timestamp=None, nonce=None, secret=SECRET):
    timestamp = str(int(time.time()) if timestamp is None else timestamp)
    nonce = str(uuid4()) if nonce is None else nonce
    signed = f"POST\n{path}\n{timestamp}\n{nonce}\n".encode() + body
    return {"content-type": "application/json", "X-Channels-Timestamp": timestamp,
            "X-Channels-Nonce": nonce,
            "X-Channels-Signature": hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()}


def send(client, data=None, path=PATH, **sign_args):
    body = json.dumps(envelope() if data is None else data, ensure_ascii=False).encode(errors="backslashreplace")
    return client.post(path, content=body, headers=sign(body, path, **sign_args))


def add_contact(db, family="family-a", handle="alice", person="person-a", **changes):
    now = datetime.now(timezone.utc)
    doc = {"_id": f"{family}:{handle}", "familyId": family, "personId": person,
           "channel": "terminal", "handle": handle, "status": "active",
           "verifiedAt": now, "optInAt": now, **changes}
    db.contacts.insert_one(doc)
    if db.people.find_one({"_id": person}) is None:
        db.people.insert_one({"_id": person, "familyId": family})
    return doc


@pytest.fixture
def stack(monkeypatch):
    db = mongomock.MongoClient(tz_aware=True).stitch
    ensure_channel_indexes(db)
    monkeypatch.setenv("STITCH_CHANNELS", "1")
    monkeypatch.setenv("CHANNELS_SHARED_SECRET", SECRET)
    monkeypatch.setattr(db_client, "get_db", lambda: db)
    add_contact(db)
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        yield client, db


def test_receipt_is_durable_across_apps_and_scoped(stack):
    client, db = stack
    assert send(client).json() == {"accepted": True, "duplicate": False}
    other = FastAPI()
    other.include_router(router)
    with TestClient(other) as restarted:
        assert send(restarted).json() == {"accepted": True, "duplicate": True}
    receipt = db.channelMessages.find_one({"kind": "inbound"})
    assert receipt["familyId"] == "family-a"
    assert receipt["personId"] == "person-a"
    assert receipt["status"] == "received"
    assert db.channelMessages.count_documents({"kind": "inbound"}) == 1
    assert db.memories.count_documents({}) == 0  # receipt is not a generated story


@pytest.mark.parametrize("change", [{"text": "different"}, {"spaceId": "other"}])
def test_message_id_cannot_be_reused_for_different_content(stack, change):
    client, db = stack
    assert send(client).status_code == 202
    assert send(client, envelope(**change)).status_code == 409
    assert db.channelMessages.count_documents({"kind": "inbound"}) == 1


def test_concurrent_duplicate_insert_is_atomic(stack):
    _, db = stack
    contact = db.contacts.find_one({"handle": "alice"})
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: conversations.save_inbound(db, contact, envelope()), range(16)))
    assert results.count(False) == 1
    assert results.count(True) == 15


def test_concurrent_nonce_is_atomic(stack):
    _, db = stack
    nonce = str(uuid4())
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: conversations.consume_nonce(db, nonce, int(time.time())), range(16)))
    assert results.count(True) == 1


@pytest.mark.parametrize("flag", [None, "0", "true"])
def test_disabled_even_when_mounted(stack, monkeypatch, flag):
    client, db = stack
    if flag is None:
        monkeypatch.delenv("STITCH_CHANNELS")
    else:
        monkeypatch.setenv("STITCH_CHANNELS", flag)
    assert send(client).status_code == 404
    assert db.channelMessages.count_documents({}) == 0


@pytest.mark.parametrize("secret", [
    "", "short", "x" * 31, "é" * 15 + "x", " " * 32, "\t" * 40,
    "\n" * 40, "\u2003" * 32, " " + SECRET, SECRET + " ",
    "\t" + SECRET, SECRET + "\n", SECRET + "\r\n", "\u00a0" + SECRET,
    "\ufeff" + SECRET, SECRET + "\ufeff", "\u0085" + SECRET, SECRET + "\u001c",
])
def test_invalid_configured_secret_fails_closed_before_storage(stack, monkeypatch, secret):
    client, db = stack
    monkeypatch.setenv("CHANNELS_SHARED_SECRET", secret)
    monkeypatch.setattr(db_client, "get_db", lambda: pytest.fail("invalid config reached storage"))
    response = send(client, secret=secret)
    assert response.status_code == 503
    assert response.json() == {"detail": "Channels is not configured"}
    assert db.channelMessages.count_documents({}) == 0


@pytest.mark.parametrize("secret", ["x" * 32, "é" * 16, "x" * 16 + " " + "y" * 16])
def test_valid_configured_secret_uses_exact_utf8_bytes(stack, monkeypatch, secret):
    client, db = stack
    monkeypatch.setenv("CHANNELS_SHARED_SECRET", secret)
    assert send(client, secret=secret).status_code == 202
    assert db.channelMessages.count_documents({"kind": "inbound"}) == 1


@pytest.mark.parametrize("offset", [-301, 301])
def test_stale_or_future_signature(stack, offset):
    client, db = stack
    assert send(client, timestamp=int(time.time()) + offset).status_code == 401
    assert db.channelMessages.count_documents({}) == 0


def test_signature_tampering_path_and_replay(stack):
    client, db = stack
    body = json.dumps(envelope()).encode()
    headers = sign(body)
    assert client.post(PATH, content=body + b" ", headers=headers).status_code == 401
    assert client.post("/internal/channels/tick", content=body, headers=headers).status_code == 401
    assert client.post(PATH, content=body, headers=headers).status_code == 202
    assert client.post(PATH, content=body, headers=headers).status_code == 409
    assert db.channelMessages.count_documents({"kind": "inbound"}) == 1


@pytest.mark.parametrize("nonce", ["abc", "A0000000-0000-4000-8000-000000000000"])
def test_nonce_requires_canonical_uuid(stack, nonce):
    assert send(stack[0], nonce=nonce).status_code == 401


@pytest.mark.parametrize("timestamp", ["-1", "1.0", "00000000000000", "nan"])
def test_timestamp_requires_integer_epoch(stack, timestamp):
    assert send(stack[0], timestamp=timestamp).status_code == 401


def test_missing_auth_never_accepts_session_cookie(stack):
    client, db = stack
    assert client.post(PATH, json=envelope(), headers={"cookie": "session=anything"}).status_code == 401
    assert db.channelMessages.count_documents({}) == 0


def test_nonce_replay_survives_router_restart(stack):
    client, _ = stack
    body = json.dumps(envelope()).encode()
    headers = sign(body)
    assert client.post(PATH, content=body, headers=headers).status_code == 202
    other = FastAPI()
    other.include_router(router)
    with TestClient(other) as restarted:
        assert restarted.post(PATH, content=body, headers=headers).status_code == 409


@pytest.mark.parametrize("change", [
    {"familyId": "family-b"}, {"personId": "someone-else"}, {"text": " "},
    {"text": 12}, {"text": "é" * 4001}, {"senderId": "é" * 129},
    {"messageId": ""}, {"provider": "unknown"},
    {"text": "\ufeff"}, {"text": "\u001c"}, {"text": "\ud800"}, {"senderId": "\ufeff"},
])
def test_schema_limits_and_no_identity_injection(stack, change):
    client, db = stack
    response = send(client, envelope(**change))
    assert response.status_code == 422
    assert response.json() == {"detail": "Invalid channel message"}
    assert db.channelMessages.count_documents({"kind": "inbound"}) == 0


def test_raw_body_limit(stack):
    client, db = stack
    body = b" " * 16385
    assert client.post(PATH, content=body, headers=sign(body)).status_code == 413
    assert db.channelMessages.count_documents({}) == 0


def test_unknown_and_ambiguous_handles_do_not_leak_family_data(stack):
    client, db = stack
    assert send(client, envelope(senderId="unknown")).status_code == 404
    add_contact(db, family="secret-family", person="person-b")
    response = send(client)
    assert response.status_code == 409
    assert response.json() == {"detail": "Family selection required"}
    assert db.channelMessages.count_documents({"kind": "inbound"}) == 0


@pytest.mark.parametrize("changes", [{"status": "stopped"}, {"status": "pending_optin"},
                                     {"verifiedAt": None}, {"optInAt": None}])
def test_consent_is_required(stack, changes):
    client, db = stack
    db.contacts.update_one({"handle": "alice"}, {"$set": changes})
    assert send(client).status_code == 403
    assert db.channelMessages.count_documents({"kind": "inbound"}) == 0


def test_invalid_cross_family_person_mapping_is_rejected(stack):
    client, db = stack
    db.people.update_one({"_id": "person-a"}, {"$set": {"familyId": "other"}})
    assert send(client).status_code == 404
    assert db.channelMessages.count_documents({"kind": "inbound"}) == 0


@pytest.mark.parametrize("text", ["STOP", " unsubscribe "])
def test_stop_applies_to_all_mapped_families_only(stack, text):
    client, db = stack
    add_contact(db, family="family-b", person="person-b")
    add_contact(db, family="family-c", handle="bob", person="person-c")
    response = send(client, envelope(text=text))
    assert response.status_code == 202
    assert response.json() == {"accepted": True, "duplicate": False, "stopped": True}
    assert {r["status"] for r in db.contacts.find({"handle": "alice"})} == {"stopped"}
    assert db.contacts.find_one({"handle": "bob"})["status"] == "active"
    assert send(client, envelope(text=text)).json()["duplicate"] is True


def test_other_family_same_message_id_stays_separate(stack):
    client, db = stack
    add_contact(db, family="family-b", handle="bob", person="person-b")
    assert send(client).status_code == 202
    assert send(client, envelope(senderId="bob")).status_code == 202
    assert set(db.channelMessages.distinct("familyId", {"kind": "inbound"})) == {"family-a", "family-b"}


def test_colliding_stop_does_not_change_consent(stack):
    client, db = stack
    assert send(client).status_code == 202
    assert send(client, envelope(text="STOP")).status_code == 409
    assert db.contacts.find_one({"handle": "alice"})["status"] == "active"


def test_stop_does_not_silently_truncate_multiple_families(stack):
    client, db = stack
    for i in range(105):
        add_contact(db, family=f"family-{i}", person=f"person-{i}")
    assert send(client, envelope(text="STOP")).status_code == 202
    assert db.contacts.count_documents({"handle": "alice", "status": "active"}) == 0


def test_tick_is_explicitly_inactive_and_authenticated(stack):
    client, db = stack
    assert send(client, {}, path="/internal/channels/tick").json() == {"processed": 0, "scheduler": "inactive"}
    assert send(client, {"force": True}, path="/internal/channels/tick").status_code == 422
    assert client.post("/internal/channels/tick", json={}).status_code == 401
    assert db.channelMessages.count_documents({"kind": "inbound"}) == 0


def test_unsigned_query_is_rejected(stack):
    client, _ = stack
    body = json.dumps(envelope()).encode()
    assert client.post(PATH + "?family=other", content=body, headers=sign(body)).status_code == 400


def test_nonce_expiry_does_not_expire_inbox(stack):
    client, db = stack
    assert send(client).status_code == 202
    assert "expireAt" not in db.channelMessages.find_one({"kind": "inbound"})
    indexes = db.channelMessages.index_information()
    assert indexes["channel_auth_expiry"]["expireAfterSeconds"] == 0
    ensure_channel_indexes(db)  # safe repeated startup
