"""Private signed fixtures only: no phone, speech model, or external requests."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import time

import mongomock
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

from channels import ensure_channel_indexes, elevenlabs
from channels.settings import router as settings_router
from channels.routes import router as channels_router
from db import client as db_client, conversations
from routes.auth import router as auth_router
from seed.demo import seed_demo

SECRET = "synthetic-elevenlabs-test-secret-only-1234"
BASE = "/families/demo_family/channels"
PAIR = BASE + "/people/dad/call-pairing"
HOOK = "/internal/channels/elevenlabs/post-call"


@pytest.fixture
def stack(monkeypatch):
    db = mongomock.MongoClient(tz_aware=True).calls
    db_client.ensure_indexes(db)
    ensure_channel_indexes(db)
    seed_demo(db)
    monkeypatch.setattr(db_client, "_db_override", db)
    for key, value in {"JWT_SECRET": "synthetic-cookie-secret-" * 3, "COOKIE_SECURE": "0",
        "STITCH_CHANNELS": "1", "STITCH_CHANNELS_ELEVENLABS": "1",
        "ELEVENLABS_WEBHOOK_SECRET": SECRET, "ELEVENLABS_AGENT_IDS": "agent_test"}.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("STITCH_CHANNELS_CALL_NUMBER", raising=False)
    app = FastAPI()
    for router in (auth_router, settings_router, channels_router):
        app.include_router(router)
    with TestClient(app) as client:
        yield client, db


def login(client, email="david@demo.stitch"):
    assert client.post("/auth/login", json={"email": email, "password": "stitch-demo"}).status_code == 200


def pair(client):
    login(client)
    response = client.post(PAIR, json={})
    assert response.status_code == 201, response.text
    assert response.headers["cache-control"] == "no-store"
    return response.json()["code"]


def event(code, conversation="conv_test", text="我小时候和奶奶包饺子。\nSUBMIT STOP KEEP"):
    return {"type": "post_call_transcription", "data": {"agent_id": "agent_test",
        "conversation_id": conversation, "status": "done", "family_id": "attacker-family",
        "transcript": [{"role": "user", "message": "STITCH " + code},
                       {"role": "agent", "message": "SUBMIT assistant secret"},
                       {"role": "user", "message": text}]}}


def signed(raw, timestamp=None):
    stamp = str(int(time.time()) if timestamp is None else timestamp)
    digest = hmac.new(SECRET.encode(), stamp.encode() + b"." + raw, hashlib.sha256).hexdigest()
    return {"content-type": "application/json", "elevenlabs-signature": f"t={stamp},v0={digest}"}


def deliver(client, payload, timestamp=None):
    raw = json.dumps(payload, ensure_ascii=False).encode()
    return client.post(HOOK, content=raw, headers=signed(raw, timestamp))


def test_cookie_pair_signed_chinese_review_delete_and_replay(stack):
    client, db = stack
    assert client.post(PAIR, json={}).status_code == 401
    code = pair(client)
    payload = event(code)
    assert deliver(client, payload).status_code == 200
    drafts = client.get(BASE + "/call-drafts").json()["drafts"]
    assert len(drafts) == 1
    draft = drafts[0]
    assert draft["text"] == payload["data"]["transcript"][2]["message"]
    assert set(draft) == {"id", "personId", "createdAt", "text"}
    stored = repr(list(db.channelMessages.find({})))
    assert code not in stored and code.replace("-", "") not in stored
    assert "assistant secret" not in stored and "attacker-family" not in stored
    assert db.channelMessages.count_documents({"kind": "inbound"}) == 0
    assert db.contacts.count_documents({}) == 0
    assert deliver(client, payload).status_code == 200
    changed = event(code, text="changed")
    assert deliver(client, changed).status_code == 409
    path = BASE + "/call-drafts/" + draft["id"]
    assert client.get(path).json()["draft"] == draft
    assert client.delete(path).status_code == 204
    assert client.delete(path).status_code == 404
    assert deliver(client, payload).status_code == 200
    assert client.get(path).status_code == 404
    assert client.get(BASE + "/call-drafts").json() == {"drafts": []}
    assert "text" not in db.channelMessages.find_one({"kind": "call-draft"})


def test_other_person_other_family_and_admin_cannot_impersonate(stack):
    client, db = stack
    code = pair(client)
    assert client.post(PAIR.replace("dad", "mom"), json={}).status_code == 404
    assert client.post(PAIR.replace("demo_family", "other"), json={}).status_code == 404
    assert deliver(client, event(code)).status_code == 200
    draft = client.get(BASE + "/call-drafts").json()["drafts"][0]
    login(client, "ana@demo.stitch")
    assert client.get(BASE + "/call-drafts").json() == {"drafts": []}
    assert client.get(BASE + "/call-drafts/" + draft["id"]).status_code == 404
    assert client.delete(BASE + "/call-drafts/" + draft["id"]).status_code == 404
    assert client.post(PAIR, json={}).status_code == 404


@pytest.mark.parametrize("mutation", ["member", "person", "user", "rejoined"])
def test_current_identity_rechecked_before_ingest_and_read(stack, mutation):
    client, db = stack
    code = pair(client)
    assert deliver(client, event(code)).status_code == 200
    if mutation == "member":
        db.families.update_one({"_id": "demo_family"}, {"$pull": {"members": {"userId": "demo_user_david"}}})
    elif mutation == "person":
        db.people.update_one({"_id": "dad"}, {"$unset": {"userId": ""}})
    elif mutation == "user":
        db.users.delete_one({"_id": "demo_user_david"})
    else:
        family = db.families.find_one({"_id": "demo_family"})
        for member in family["members"]:
            if member["userId"] == "demo_user_david":
                member["joinedAt"] += timedelta(seconds=2)
        db.families.replace_one({"_id": "demo_family"}, family)
    assert deliver(client, event(code)).status_code == 400
    assert conversations.list_call_drafts(db, "demo_family", "demo_user_david") == []


def test_rotation_expiration_single_use_and_expired_replay(stack):
    client, db = stack
    old = pair(client)
    code = client.post(PAIR, json={}).json()["code"]
    assert deliver(client, event(old)).status_code == 400
    assert deliver(client, event(code)).status_code == 200
    assert deliver(client, event(code, "second_call")).status_code == 400
    db.channelMessages.delete_many({"kind": "call-pairing"})  # TTL after successful binding.
    assert deliver(client, event(code)).status_code == 200
    fresh = client.post(PAIR, json={}).json()["code"]
    db.channelMessages.update_one({"kind": "call-pairing"}, {"$set": {"expireAt": datetime.now(timezone.utc) - timedelta(seconds=1)}})
    assert deliver(client, event(fresh, "third_call")).status_code == 400


def test_atomic_two_calls_one_token_and_exact_replay(stack):
    client, db = stack
    code = pair(client).replace("-", "")
    def accept(index):
        try:
            return conversations.accept_call_transcript(db, "agent_test", f"race_{index}", code, "故事", f"hash_{index}")
        except (LookupError, conversations.CallConflict):
            return "denied"
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(accept, [0, 1]))
    assert outcomes.count(False) == 1
    assert db.channelMessages.count_documents({"kind": "call-draft", "status": "ready"}) == 1
    winner = outcomes.index(False)
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert list(pool.map(accept, [winner] * 4)) == [True] * 4


@pytest.mark.parametrize("delta", [-1801, 1801])
def test_old_and_future_signatures_rejected(stack, delta):
    client, db = stack
    assert deliver(client, event("123456789012"), int(time.time()) + delta).status_code == 401
    assert db.channelMessages.count_documents({}) == 0


def test_signature_before_json_strict_header_and_body_bound(stack):
    client, db = stack
    raw = b"not json"
    headers = signed(raw)
    assert client.post(HOOK, content=raw + b"tampered", headers=headers).status_code == 401
    assert client.post(HOOK, content=raw, headers=headers).status_code == 400
    headers["elevenlabs-signature"] += ",t=1"
    assert client.post(HOOK, content=raw, headers=headers).status_code == 401
    raw = b"x" * (elevenlabs.MAX_BODY + 1)
    assert client.post(HOOK, content=raw, headers=signed(raw)).status_code == 413


@pytest.mark.parametrize("variant", ["assistant_code", "double", "repeat", "redacted", "long", "empty", "wrong_agent", "unfinished", "words"])
def test_ambiguous_untrusted_or_unbounded_transcript_rejected(stack, variant):
    client, db = stack
    code = pair(client)
    payload = event(code)
    data = payload["data"]
    if variant == "assistant_code": data["transcript"][0]["role"] = "agent"
    if variant == "double": data["transcript"].append(data["transcript"][0])
    if variant == "repeat": data["transcript"][2]["message"] = "my code was " + code
    if variant == "redacted": data["transcript"][0]["message"] = None
    if variant == "long": data["transcript"][2]["message"] = "中" * 20001
    if variant == "empty": data["transcript"][2]["message"] = " "
    if variant == "wrong_agent": data["agent_id"] = "other"
    if variant == "unfinished": data["status"] = "processing"
    if variant == "words": data["transcript"][0]["message"] = "STITCH one two three four"
    assert deliver(client, payload).status_code == 400
    assert db.channelMessages.count_documents({"kind": "call-draft"}) == 0


def test_flags_config_phone_and_burst_limits(stack, monkeypatch):
    client, db = stack
    login(client)
    monkeypatch.setenv("STITCH_CHANNELS_ELEVENLABS", "0")
    assert client.post(PAIR, json={}).status_code == 404
    monkeypatch.setenv("STITCH_CHANNELS_ELEVENLABS", "1")
    monkeypatch.setenv("STITCH_CHANNELS_CALL_NUMBER", "not-a-number")
    assert client.post(PAIR, json={}).status_code == 503
    monkeypatch.setenv("STITCH_CHANNELS_CALL_NUMBER", "+15555550123")
    first = client.post(PAIR, json={})
    assert first.json()["phoneNumber"] == "+15555550123"
    for _ in range(9): assert client.post(PAIR, json={}).status_code == 201
    assert client.post(PAIR, json={}).status_code == 429
    for _ in range(5): assert deliver(client, event("000000000000")).status_code == 400
    assert deliver(client, event("000000000000")).status_code == 429
    future = datetime.now(timezone.utc) + timedelta(minutes=2)
    assert conversations.create_call_pairing(db, "demo_family", "dad", "demo_user_david", now=future)


def test_chunked_read_deadline(stack, monkeypatch):
    monkeypatch.setattr(elevenlabs, "READ_TIMEOUT", 0.01)
    async def slow():
        await asyncio.sleep(0.1)
        return {"type": "http.request", "body": b"", "more_body": False}
    headers = [(k.encode(), v.encode()) for k, v in signed(b"").items()]
    request = Request({"type": "http", "method": "POST", "path": HOOK, "query_string": b"", "headers": headers}, slow)
    with pytest.raises(HTTPException) as error:
        asyncio.run(elevenlabs.post_call(request))
    assert error.value.status_code == 408


def test_binding_recovers_after_storage_failure_without_double_attach(stack, monkeypatch):
    client, db = stack
    code = pair(client).replace("-", "")
    original = db.channelMessages.update_one
    failed = False
    def fail_once(query, update, *args, **kwargs):
        nonlocal failed
        if update.get("$set", {}).get("status") == "ready" and not failed:
            failed = True
            raise RuntimeError("synthetic storage interruption")
        return original(query, update, *args, **kwargs)
    monkeypatch.setattr(db.channelMessages, "update_one", fail_once)
    with pytest.raises(RuntimeError):
        conversations.accept_call_transcript(db, "agent_test", "recover", code, "故事", "same")
    assert db.channelMessages.count_documents({"kind": "call-draft", "status": "ready"}) == 0
    assert client.post(PAIR, json={}).status_code == 409
    assert conversations.accept_call_transcript(db, "agent_test", "recover", code, "故事", "same") is False
    assert conversations.accept_call_transcript(db, "agent_test", "recover", code, "故事", "same") is True
    assert db.channelMessages.count_documents({"kind": "call-draft", "status": "ready"}) == 1


def test_membership_change_during_binding_revokes_before_read(stack, monkeypatch):
    client, db = stack
    code = pair(client).replace("-", "")
    original = db.channelMessages.update_one
    def detach(query, update, *args, **kwargs):
        result = original(query, update, *args, **kwargs)
        if update.get("$set", {}).get("status") == "ready":
            db.people.update_one({"_id": "dad"}, {"$unset": {"userId": ""}})
        return result
    monkeypatch.setattr(db.channelMessages, "update_one", detach)
    with pytest.raises(LookupError):
        conversations.accept_call_transcript(db, "agent_test", "revoke", code, "故事", "same")
    row = db.channelMessages.find_one({"kind": "call-draft"})
    assert row["status"] == "revoked" and "text" not in row


def test_atomic_family_burst_capacity(stack):
    client, db = stack
    now = datetime.now(timezone.utc)
    def attempt(_):
        try:
            conversations.call_attempt(db, "test", "demo_family", family_id="demo_family", limit=3, now=now)
            return True
        except conversations.CallRateLimited:
            return False
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(attempt, range(20))) == 3
    assert db.channelMessages.find_one({"kind": "call-rate"})["count"] == 3


@pytest.mark.parametrize("marker", [
    "STITCH 1234-5678-9012.", "Stitch, 1234 5678 9012!", "STITCH:123456789012",
    "“STITCH 123456789012。”", "STITCH：1234-5678-9012。", '“STITCH” 123456789012.',
])
def test_standalone_pairing_accepts_bounded_asr_punctuation(marker):
    payload = event("123456789012")
    payload["data"]["transcript"][0]["message"] = marker
    result = elevenlabs.transcript(json.dumps(payload).encode(), frozenset({"agent_test"}))
    assert result[2] == "123456789012"
    assert "123456789012" not in result[3]


@pytest.mark.parametrize("marker", [
    "My code is STITCH 123456789012.", "STITCH 123456789012 is my code.",
    "STITCH one two three four five six seven eight nine zero one two.",
    "STITCH 12345678901.", "STITCH 1234567890123.", "!" * 97 + "STITCH 123456789012",
    "STITCH123456789012.", "STITCH 123456789012. STITCH 123456789012.",
])
def test_pairing_punctuation_does_not_extract_narrative_or_guess(marker):
    payload = event("123456789012")
    payload["data"]["transcript"][0]["message"] = marker
    with pytest.raises(HTTPException):
        elevenlabs.transcript(json.dumps(payload).encode(), frozenset({"agent_test"}))


def test_pairing_unique_sparse_index_restarts_with_existing_receipts(stack):
    client, db = stack
    code = pair(client)
    assert deliver(client, event(code)).status_code == 200
    db.channelMessages.insert_one({"kind": "inbound", "familyId": "demo_family"})
    ensure_channel_indexes(db)
    index = db.channelMessages.index_information()["channel_call_pairing_hash"]
    assert index["unique"] and index["sparse"]
    pairing = db.channelMessages.find_one({"kind": "call-pairing"})
    draft = db.channelMessages.find_one({"kind": "call-draft"})
    assert draft["proofHash"] == pairing["tokenHash"] and "tokenHash" not in draft
    from pymongo.errors import DuplicateKeyError
    with pytest.raises(DuplicateKeyError):
        db.channelMessages.insert_one({"kind": "call-pairing", "tokenHash": pairing["tokenHash"]})
