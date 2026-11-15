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
_EVENT_STARTS = {}


@pytest.fixture
def stack(monkeypatch):
    _EVENT_STARTS.clear()
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
        "metadata": {"start_time_unix_secs": _EVENT_STARTS.setdefault((code, conversation), int(time.time()))},
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
    records = list(db.channelMessages.find({}))
    stored = repr(records)
    # Three digits can coincidentally occur inside a SHA256 hex string. Check
    # actual field values, not meaningless short substrings of a digest.
    assert all(value != code for row in records for value in row.values())
    assert all("code" not in row for row in records)
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
            return conversations.accept_call_transcript(db, "agent_test", f"race_{index}", code, "故事", f"hash_{index}", call_started_at=int(time.time()))
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
    raw = b"[" * 2000 + b"0" + b"]" * 2000
    assert client.post(HOOK, content=raw, headers=signed(raw)).status_code == 400


@pytest.mark.parametrize("variant", ["assistant_code", "conflicting", "only_repeat", "redacted", "long", "empty", "wrong_agent", "unfinished", "words"])
def test_ambiguous_untrusted_or_unbounded_transcript_rejected(stack, variant):
    client, db = stack
    code = pair(client)
    payload = event(code)
    data = payload["data"]
    if variant == "assistant_code": data["transcript"][0]["role"] = "agent"
    if variant == "conflicting":
        different = str((int(code[0]) + 1) % 10) + code[1:]
        data["transcript"].append({"role": "user", "message": "STITCH " + different})
    if variant == "only_repeat": data["transcript"][2]["message"] = " ".join(code) + "."
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
        conversations.accept_call_transcript(db, "agent_test", "recover", code, "故事", "same", call_started_at=int(time.time()))
    assert db.channelMessages.count_documents({"kind": "call-draft", "status": "ready"}) == 0
    assert client.post(PAIR, json={}).status_code == 409
    assert conversations.accept_call_transcript(db, "agent_test", "recover", code, "故事", "same", call_started_at=int(time.time())) is False
    assert conversations.accept_call_transcript(db, "agent_test", "recover", code, "故事", "same", call_started_at=int(time.time())) is True
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
        conversations.accept_call_transcript(db, "agent_test", "revoke", code, "故事", "same", call_started_at=int(time.time()))
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
    "STITCH one two three four five six seven eight nine zero one two.",
])
def test_standalone_pairing_accepts_bounded_asr_punctuation(marker):
    payload = event("123456789012")
    payload["data"]["transcript"][0]["message"] = marker
    result = elevenlabs.transcript(json.dumps(payload).encode(), frozenset({"agent_test"}))
    assert result[2] == "123456789012"
    assert "123456789012" not in result[3]


@pytest.mark.parametrize("marker", [
    "My code is STITCH 123456789012.", "STITCH 123456789012 is my code.",
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


def test_new_pairing_is_three_digits_and_expires_in_fifteen_minutes(stack):
    client, db = stack
    code = pair(client)
    assert len(code) == 3 and code.isascii() and code.isdecimal()
    ticket = db.channelMessages.find_one({"kind": "call-pairing"})
    assert ticket["expireAt"] - ticket["createdAt"] == timedelta(minutes=15)
    with pytest.raises(LookupError):
        conversations.accept_call_transcript(db, "agent_test", "late", code.replace("-", ""),
                                           "story", "late_hash", now=ticket["expireAt"])


@pytest.mark.parametrize("marker", [
    "STITCH 123.", "Stitch: one two three.", "STITCH 一二三。", "STITCH 一两三",
])
def test_three_digits_exact_normalization(marker):
    assert elevenlabs.pairing_marker(marker) == "123"


@pytest.mark.parametrize("marker", [
    "STITCH 123-456.", "Stitch: one two three four five six.",
    "STITCH 一二三四五六。", "STITCH 一两三四五六", "STITCH 1 two 三 4 five 六",
])
def test_six_digits_exact_english_chinese_and_identical_repetition(marker):
    payload = event("123456")
    payload["data"]["transcript"].extend([
        {"role": "user", "message": marker},
        {"role": "agent", "message": "STITCH 654321"},
    ])
    result = elevenlabs.transcript(json.dumps(payload).encode(), frozenset({"agent_test"}))
    assert result[2] == "123456"
    assert result[3] == payload["data"]["transcript"][2]["message"]


@pytest.mark.parametrize("marker", [
    "STITCH zero zero one two three four", "STITCH 零〇一二三四",
])
def test_leading_zero_digit_normalization(marker):
    assert elevenlabs.pairing_marker(marker) == "001234"


@pytest.mark.parametrize("marker", [
    "STITCH oh one two three four five", "STITCH １２３４５６", "STITCH twenty three four five six",
    "STITCH 一百二十三四五六", "STITCH onetwothreefourfivesix", "STITCH 12345", "STITCH 1234567",
    "my STITCH one two three four five six", "STITCH one two three four five six please",
])
def test_no_homophones_fullwidth_arithmetic_or_narrative(marker):
    assert elevenlabs.pairing_marker(marker) is None


@pytest.mark.parametrize("repeat,kept", [
    ("My code was one two three four five six.", "My code was [code]."),
    ("验证码是一二三四五六。", "验证码是[code]。"),
    ("Again: 123-456", "Again: [code]"), ("Again: 1 two 三 4 five 六", "Again: [code]"),
    ("Again: １２３４５６", "Again: [code]"), ("Twice: 123456 123456, then ７ apples", "Twice: [code], then ７ apples"),
])
def test_recognized_code_variants_in_prose_are_not_persisted(repeat, kept):
    payload = event("123456", text="We made dumplings.\n" + repeat)
    result = elevenlabs.transcript(json.dumps(payload, ensure_ascii=False).encode(), frozenset({"agent_test"}))
    assert result[3] == "We made dumplings.\n" + kept


@pytest.mark.parametrize("story", [
    "In 1314 the bridge was built.", "她生于一九三一四年。", "Call 555-314-0000, or room ３１４５.",
    "I was three, one of four children.", "The marker [code] is ordinary text.",
])
def test_longer_numbers_containing_the_code_digits_stay_in_the_story(story):
    payload = event("314", text=story)
    result = elevenlabs.transcript(json.dumps(payload, ensure_ascii=False).encode(), frozenset({"agent_test"}))
    assert result[3] == story


def test_turn_holding_only_the_code_is_dropped_and_the_story_kept():
    payload = event("314", text="We made dumplings.")
    payload["data"]["transcript"].extend([{"role": "user", "message": "3 1 4."},
                                          {"role": "user", "message": "三一四"}])
    result = elevenlabs.transcript(json.dumps(payload, ensure_ascii=False).encode(), frozenset({"agent_test"}))
    assert result[3] == "We made dumplings."


def test_one_account_cannot_reserve_codes_without_bound_across_families(stack):
    client, db = stack
    now = datetime.now(timezone.utc).replace(microsecond=0)
    now -= timedelta(seconds=int(now.timestamp()) % 900)  # start of a fifteen-minute window
    families = [("demo_family", "dad"), ("other_family", "other_dad")]
    family = db.families.find_one({"_id": "demo_family"})
    family["_id"] = "other_family"
    db.families.insert_one(family)
    person = db.people.find_one({"_id": "dad"})
    person.update(_id="other_dad", familyId="other_family")
    db.people.insert_one(person)
    for index in range(conversations.CALL_ISSUES_PER_ACTOR):
        family, person = families[index % 2]
        conversations.create_call_pairing(db, family, person, "demo_user_david", now=now + timedelta(seconds=7 * index))
    for family, person in families:
        with pytest.raises(conversations.CallRateLimited) as limited:
            conversations.create_call_pairing(db, family, person, "demo_user_david", now=now + timedelta(seconds=800))
        assert limited.value.retry_after == 900
    assert db.channelMessages.count_documents({"kind": "call-code-reservation"}) == conversations.CALL_ISSUES_PER_ACTOR
    assert conversations.create_call_pairing(db, "demo_family", "mom", "demo_user_ana", now=now + timedelta(seconds=800))
    assert conversations.create_call_pairing(db, "demo_family", "dad", "demo_user_david", now=now + timedelta(seconds=900))


@pytest.mark.parametrize("legacy", ["123456", "123456789012"])
def test_legacy_unexpired_ticket_is_read_only_compatible(stack, legacy):
    client, db = stack
    pair(client)
    db.channelMessages.update_one({"kind": "call-pairing"},
                                  {"$set": {"tokenHash": conversations.call_code_hash(legacy)}})
    assert deliver(client, event(legacy)).status_code == 200
    assert len(client.post(PAIR, json={}).json()["code"]) == 3


def test_global_candidate_limit_spans_call_ids_and_replay_bypasses_it(stack, monkeypatch):
    client, db = stack
    code = pair(client)
    now = datetime.now(timezone.utc)
    attempt = conversations.call_attempt
    def fixed_window(*args, **kwargs):
        kwargs["now"] = now
        return attempt(*args, **kwargs)
    monkeypatch.setattr(conversations, "call_attempt", fixed_window)
    assert deliver(client, event(code)).status_code == 200  # Counts one candidate.
    for index in range(4):
        with pytest.raises(LookupError):
            conversations.accept_call_transcript(db, "agent_test", f"guess_{index}", "000000000000",
                                               "story", "hash", now=now)
    response = deliver(client, event("000000000000", "guess_over_limit"))
    assert response.status_code == 429 and response.headers["retry-after"] == "900"
    assert deliver(client, event(code)).status_code == 200
    draft = client.get(BASE + "/call-drafts").json()["drafts"][0]
    assert client.delete(BASE + "/call-drafts/" + draft["id"]).status_code == 204
    assert deliver(client, event(code)).status_code == 200
    now += timedelta(minutes=15)
    with pytest.raises(LookupError):
        conversations.accept_call_transcript(db, "agent_test", "next_window", "000000000000",
                                           "story", "hash", now=now)


def test_global_candidate_limit_is_atomic_for_concurrent_calls(stack):
    client, db = stack
    now = datetime.now(timezone.utc)
    def guess(index):
        try:
            conversations.accept_call_transcript(db, "agent_test", f"concurrent_guess_{index}",
                                               "000000000000", "story", "hash", now=now)
        except LookupError:
            return "checked"
        except conversations.CallRateLimited:
            return "throttled"
    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(guess, range(20)))
    assert results.count("checked") == 5 and results.count("throttled") == 15


def test_signed_http_repeated_english_and_chinese_codes_bind_one_private_draft(stack):
    client, db = stack
    code = pair(client).replace("-", "")
    english = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"]
    chinese = "零一二三四五六七八九"
    payload = event(code, text="这是一段中文回忆。")
    payload["data"]["transcript"][0]["message"] = "STITCH " + " ".join(english[int(digit)] for digit in code)
    payload["data"]["transcript"].append({"role": "user", "message": "STITCH " + "".join(chinese[int(digit)] for digit in code)})
    assert deliver(client, payload).status_code == 200
    drafts = client.get(BASE + "/call-drafts").json()["drafts"]
    assert len(drafts) == 1 and drafts[0]["text"] == "这是一段中文回忆。"


def test_three_digits_preserve_leading_zeros_and_bound_collisions(stack, monkeypatch):
    client, db = stack
    monkeypatch.setattr(conversations.secrets, "randbelow", lambda _: 7)
    assert pair(client) == "007"
    with pytest.raises(conversations.CallConflict):
        conversations.create_call_pairing(db, "demo_family", "mom", "demo_user_ana")
    assert db.channelMessages.count_documents({"kind": "call-pairing"}) == 1
    assert client.post(PAIR, json={}).status_code == 409  # Never reuse the visible old code.
    assert deliver(client, event("007")).status_code == 200


def test_ten_minute_call_can_finish_before_fifteen_minute_expiry(stack):
    client, db = stack
    code = pair(client)
    ticket = db.channelMessages.find_one({"kind": "call-pairing"})
    assert conversations.accept_call_transcript(db, "agent_test", "ten_minute_call", code,
        "story", "hash", now=ticket["createdAt"] + timedelta(minutes=10),
        call_started_at=int(ticket["createdAt"].timestamp())) is False


@pytest.mark.parametrize("consumed", [False, True])
def test_rotated_or_consumed_code_cannot_move_to_another_family_before_expiry(stack, monkeypatch, consumed):
    client, db = stack
    generated = iter([7, 8] + [7] * 5)
    monkeypatch.setattr(conversations.secrets, "randbelow", lambda _: next(generated))
    assert pair(client) == "007"
    original_expiry = db.channelMessages.find_one({"kind": "call-pairing"})["expireAt"]
    if consumed:
        assert deliver(client, event("007")).status_code == 200
    assert client.post(PAIR, json={}).json()["code"] == "008"
    family = db.families.find_one({"_id": "demo_family"})
    family["_id"] = "other_family"
    db.families.insert_one(family)
    person = db.people.find_one({"_id": "dad"})
    person.update(_id="other_dad", familyId="other_family")
    db.people.insert_one(person)
    with pytest.raises(conversations.CallConflict):
        conversations.create_call_pairing(db, "other_family", "other_dad", "demo_user_david")
    reservation = db.channelMessages.find_one({"_id": "call-code:" + conversations.call_code_hash("007")})
    assert reservation == {"_id": reservation["_id"], "kind": "call-code-reservation",
                           "familyId": "demo_family", "expireAt": original_expiry}
    assert db.channelMessages.count_documents({"kind": "call-pairing", "familyId": "other_family"}) == 0
    assert deliver(client, event("007", "late_old_call")).status_code == 400


@pytest.mark.parametrize("invalid", [None, True, 1.5, "123", "before", "future", "expiry"])
def test_three_digit_start_metadata_is_required_exact_and_within_ticket(stack, invalid):
    client, db = stack
    code = pair(client)
    ticket = db.channelMessages.find_one({"kind": "call-pairing"})
    payload = event(code)
    value = invalid
    if invalid == "before": value = int(ticket["createdAt"].timestamp()) - 1
    if invalid == "future": value = int(time.time()) + 120
    if invalid == "expiry": value = int(ticket["expireAt"].timestamp()) + 1
    payload["data"]["metadata"] = {} if invalid is None else {"start_time_unix_secs": value}
    assert deliver(client, payload).status_code == 400
    assert db.channelMessages.count_documents({"kind": "call-draft"}) == 0
    assert db.channelMessages.find_one({"kind": "call-pairing"})["state"] == "pending"


def test_delayed_old_call_cannot_bind_reused_three_digit_code(stack, monkeypatch):
    client, db = stack
    monkeypatch.setattr(conversations.secrets, "randbelow", lambda _: 7)
    assert pair(client) == "007"
    old_start = int(time.time())
    expired = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.channelMessages.update_many({"kind": {"$in": ["call-pairing", "call-code-reservation"]}},
                                   {"$set": {"expireAt": expired}})
    family = db.families.find_one({"_id": "demo_family"})
    family["_id"] = "other_family"
    db.families.insert_one(family)
    person = db.people.find_one({"_id": "dad"})
    person.update(_id="other_dad", familyId="other_family")
    db.people.insert_one(person)
    issued_at = datetime.now(timezone.utc) + timedelta(seconds=2)
    ticket, code = conversations.create_call_pairing(db, "other_family", "other_dad", "demo_user_david", now=issued_at)
    assert code == "007"
    payload = event("007", "delayed_old_call")
    payload["data"]["metadata"]["start_time_unix_secs"] = old_start
    assert deliver(client, payload).status_code == 400
    assert db.channelMessages.count_documents({"kind": "call-draft"}) == 0
    assert conversations.accept_call_transcript(db, "agent_test", "new_call", code, "new story", "new hash",
        now=issued_at + timedelta(seconds=1), call_started_at=int(issued_at.timestamp())) is False


def test_completed_three_digit_retry_after_expiry_still_deduplicates(stack):
    client, db = stack
    code = pair(client)
    payload = event(code)
    assert deliver(client, payload).status_code == 200
    db.channelMessages.delete_many({"kind": {"$in": ["call-pairing", "call-code-reservation"]}})
    assert deliver(client, payload).status_code == 200
    assert db.channelMessages.count_documents({"kind": "call-draft"}) == 1


def test_fullwidth_copy_of_three_digit_code_cannot_leak_in_story(stack):
    client, db = stack
    code = pair(client)
    fullwidth = code.translate(str.maketrans("0123456789", "０１２３４５６７８９"))
    assert deliver(client, event(code, text="My code was " + fullwidth + ", and we had 12 dumplings")).status_code == 200
    draft = db.channelMessages.find_one({"kind": "call-draft"})
    assert draft["text"] == "My code was [code], and we had 12 dumplings"
    assert code not in draft["text"] and fullwidth not in draft["text"]


@pytest.fixture
def automatic(stack, monkeypatch):
    """The demo host's switch: every signed call is dad's memory in 1984."""
    import agent.runs
    started = []

    def start_run(family_id, user_id, person_id, fields, photo_ids):
        started.append((family_id, user_id, person_id, fields, photo_ids))
        return f"run_{len(started)}"
    monkeypatch.setattr(agent.runs, "start_run", start_run)
    monkeypatch.setenv("STITCH_CHANNELS_CALL_AUTO", "demo_family:dad:1984")
    return (*stack, started)


def codeless(conversation="conv_auto", text="We tipped the canoe anyway."):
    payload = event("000", conversation, text)
    payload["data"]["transcript"].pop(0)
    payload["data"].pop("metadata")
    return payload


def test_automatic_call_starts_one_run_for_the_configured_person_without_a_code(automatic):
    client, db, started = automatic
    assert deliver(client, codeless()).json() == {"received": True}
    assert len(started) == 1
    family_id, user_id, person_id, fields, photo_ids = started[0]
    assert (family_id, user_id, person_id, photo_ids) == ("demo_family", "demo_user_david", "dad", [])
    assert (fields.text, fields.year, fields.channel) == ("We tipped the canoe anyway.", 1984, "voice")
    row = db.channelMessages.find_one({"kind": "call-run"})
    assert row["status"] == "started" and row["runId"] == "run_1" and "text" not in row
    assert db.channelMessages.count_documents({"kind": "call-draft"}) == 0
    # The exact retry is acknowledged and starts nothing; a changed body is refused.
    assert deliver(client, codeless()).status_code == 200
    assert deliver(client, codeless(text="Another story entirely.")).status_code == 409
    assert len(started) == 1


def test_automatic_call_drops_a_code_spoken_out_of_habit(automatic):
    client, db, started = automatic
    payload = event("417", "conv_habit", "We tipped the canoe anyway.")
    assert deliver(client, payload).status_code == 200
    assert started[0][3].text == "We tipped the canoe anyway."


def test_automatic_call_is_still_signed_bounded_and_agent_checked(automatic):
    client, db, started = automatic
    raw = json.dumps(codeless()).encode()
    assert client.post(HOOK, content=raw, headers={"content-type": "application/json"}).status_code == 401
    wrong = codeless("conv_wrong")
    wrong["data"]["agent_id"] = "other"
    assert deliver(client, wrong).status_code == 400
    assert deliver(client, codeless("conv_empty", " ")).status_code == 400
    assert started == []
    for index in range(conversations.CALL_AUTO_PER_WINDOW):
        assert deliver(client, codeless(f"conv_{index}")).status_code in {200, 429}
    assert deliver(client, codeless("conv_over")).status_code == 429
    assert len(started) <= conversations.CALL_AUTO_PER_WINDOW


@pytest.mark.parametrize("setting", ["demo_family:dad", "demo_family:dad:84", "demo family:dad:1984", ":dad:1984"])
def test_malformed_automatic_setting_fails_closed(automatic, monkeypatch, setting):
    client, db, started = automatic
    monkeypatch.setenv("STITCH_CHANNELS_CALL_AUTO", setting)
    assert deliver(client, codeless()).status_code == 503
    assert started == [] and db.channelMessages.count_documents({"kind": "call-run"}) == 0


@pytest.mark.parametrize("target", ["demo_family:nobody:1984", "other:dad:1984", "demo_family:grandpa_luis:1984"])
def test_automatic_call_needs_a_person_linked_to_a_current_member(automatic, monkeypatch, target):
    client, db, started = automatic
    monkeypatch.setenv("STITCH_CHANNELS_CALL_AUTO", target)
    assert deliver(client, codeless()).status_code == 503
    assert started == [] and db.channelMessages.count_documents({"kind": "call-run"}) == 0


def test_failed_start_frees_the_call_for_the_provider_retry(automatic, monkeypatch):
    client, db, started = automatic
    import agent.runs
    working = agent.runs.start_run
    monkeypatch.setattr(agent.runs, "start_run", lambda *args: (_ for _ in ()).throw(RuntimeError("studio down")))
    assert deliver(client, codeless()).status_code == 503
    assert db.channelMessages.count_documents({"kind": "call-run"}) == 0
    monkeypatch.setattr(agent.runs, "start_run", working)
    assert deliver(client, codeless()).status_code == 200
    assert len(started) == 1


def test_latest_call_run_is_only_shown_to_its_own_person(automatic):
    client, db, started = automatic
    login(client)
    assert client.get(BASE + "/call-runs/latest").json() == {"run": None, "automatic": True}
    assert deliver(client, codeless()).status_code == 200
    shown = client.get(BASE + "/call-runs/latest")
    assert shown.headers["cache-control"] == "no-store"
    assert shown.json()["run"]["runId"] == "run_1" and shown.json()["automatic"] is True
    login(client, "ana@demo.stitch")
    assert client.get(BASE + "/call-runs/latest").json()["run"] is None


def test_without_the_setting_a_codeless_call_is_refused_and_nothing_starts(stack, monkeypatch):
    client, db = stack
    import agent.runs
    monkeypatch.setattr(agent.runs, "start_run", lambda *args: pytest.fail("no automatic add without the setting"))
    monkeypatch.delenv("STITCH_CHANNELS_CALL_AUTO", raising=False)
    assert deliver(client, codeless()).status_code == 400
    login(client)
    assert client.get(BASE + "/call-runs/latest").json() == {"run": None, "automatic": False}
