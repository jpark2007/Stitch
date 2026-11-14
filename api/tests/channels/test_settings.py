"""Real cookie auth and signed proof, with disposable data and no external traffic."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
import json

import mongomock
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from channels.settings import router as settings_router
from channels.enrollment import router as enrollment_router
from channels import ensure_channel_indexes
from db import client as db_client, contacts
from routes.auth import router as auth_router
from seed.demo import seed_demo
from tests.channels.test_inbox import sign

SECRET = "settings-test-secret-" * 3
BASE = "/families/demo_family/channels"
PERSON = BASE + "/people/dad"
BODY = {"provider": "imessage", "handle": "+15555550123", "timezone": "America/New_York",
        "cadenceDays": 7, "quietHours": {"start": "21:00", "end": "08:00"}}


@pytest.fixture
def stack(monkeypatch):
    db = mongomock.MongoClient(tz_aware=True).settings
    db_client.ensure_indexes(db)
    ensure_channel_indexes(db)
    seed_demo(db)
    monkeypatch.setattr(db_client, "_db_override", db)
    monkeypatch.setenv("JWT_SECRET", "settings-cookie-test-secret-" * 2)
    monkeypatch.setenv("COOKIE_SECURE", "0")
    monkeypatch.setenv("STITCH_CHANNELS", "1")
    monkeypatch.setenv("CHANNELS_SHARED_SECRET", SECRET)
    app = FastAPI()
    app.include_router(auth_router)
    app.include_router(settings_router)
    app.include_router(enrollment_router)
    with TestClient(app) as client:
        yield client, db


def login(client, email="david@demo.stitch"):
    assert client.post("/auth/login", json={"email": email, "password": "stitch-demo"}).status_code == 200


def create(client, person=PERSON, **changes):
    return client.post(person + "/enrollment", json={**BODY, **changes})


def proof(client, code, **changes):
    data = {"messageId": "proof-message", "provider": "imessage", "senderId": BODY["handle"],
            "spaceId": "verified-room", "spaceKind": "direct", "code": code, **changes}
    raw = json.dumps(data).encode()
    path = "/internal/channels/enroll"
    return client.post(path, content=raw, headers=sign(raw, path, secret=SECRET))


def test_pending_proof_preferences_stop_and_sanitized_reads(stack):
    client, db = stack
    assert client.get(PERSON + "/contact").status_code == 401
    login(client)
    assert client.get(PERSON + "/contact").json() == {"contact": None}
    response = create(client)
    assert response.status_code == 201
    assert response.headers["cache-control"] == "no-store"
    created = response.json()
    assert created["contact"]["status"] == "pending" and len(created["code"]) == 43
    assert "opt in" in created["instruction"] and "sends nothing" in created["instruction"]
    stored = db.contacts.find_one({})
    assert stored["enrollmentHash"] != created["code"] and created["code"] not in repr(stored)
    with pytest.raises(PermissionError):
        contacts.resolve_contact(db, "imessage", BODY["handle"])
    assert create(client).status_code == 409
    listed = client.get(BASE + "/contacts").json()
    assert set(listed["contacts"][0]) == {"personId", "provider", "handle", "status", "timezone", "cadenceDays", "quietHours"}
    assert "enrollmentHash" not in repr(listed) and created["code"] not in repr(listed)
    assert proof(client, created["code"]).json() == {"status": "active"}
    active = contacts.resolve_contact(db, "imessage", BODY["handle"])
    assert active["spaceId"] == "verified-room" and active["userId"] == "demo_user_david"
    assert active["spaceKind"] == "direct"
    assert "enrollmentHash" not in active
    assert proof(client, created["code"]).status_code == 400
    assert create(client).status_code == 409
    update = client.patch(PERSON + "/contact", json={"timezone": "Europe/London", "cadenceDays": 30})
    assert update.status_code == 200 and update.json()["contact"]["cadenceDays"] == 30
    assert client.post(PERSON + "/contact/stop", json={}).json()["contact"]["status"] == "stopped"
    with pytest.raises(PermissionError):
        contacts.resolve_contact(db, "imessage", BODY["handle"])
    renewed = create(client)
    assert renewed.status_code == 201 and renewed.json()["contact"]["status"] == "pending"
    assert db.contacts.find_one({})["spaceKind"] is None
    assert proof(client, created["code"]).status_code == 400
    assert proof(client, renewed.json()["code"]).status_code == 200


def test_member_can_only_manage_own_contact_and_crossfamily_is_404(stack):
    client, db = stack
    login(client, "ana@demo.stitch")
    for method, path, data in [("get", PERSON + "/contact", None), ("post", PERSON + "/enrollment", BODY),
                               ("patch", PERSON + "/contact", {"cadenceDays": 2}), ("post", PERSON + "/contact/stop", {})]:
        assert client.request(method, path, json=data).status_code == 403
        assert client.request(method, path.replace("demo_family", "other-family"), json=data).status_code == 404
    mom = BASE + "/people/mom"
    assert create(client, mom).status_code == 201
    assert len(client.get(BASE + "/contacts").json()["contacts"]) == 1
    login(client, "sam@demo.stitch")
    assert client.get(BASE + "/contacts").json() == {"contacts": []}
    login(client)
    assert len(client.get(BASE + "/contacts").json()["contacts"]) == 1
    assert client.get(BASE + "/people/missing/contact").status_code == 404


@pytest.mark.parametrize("change", [
    {"provider": "terminal"}, {"handle": "555-1234"}, {"handle": "a..b@example.com"},
    {"handle": "a@example"}, {"handle": " a@example.com"}, {"timezone": "../secret"},
    {"timezone": "Mars/Base"}, {"cadenceDays": 0}, {"cadenceDays": 31}, {"cadenceDays": True},
    {"cadenceDays": "7"}, {"quietHours": {"start": "24:00", "end": "08:00"}},
    {"quietHours": {"start": "21:00", "end": "21:00"}}, {"status": "active"}, {"userId": "spoof"},
])
def test_pending_input_is_strict_and_bounded(stack, change):
    client, db = stack
    login(client)
    assert create(client, **change).status_code == 422
    assert db.contacts.count_documents({}) == 0


@pytest.mark.parametrize("change", [{}, {"timezone": None}, {"cadenceDays": 0}, {"cadenceDays": False},
    {"timezone": "not/a/zone"}, {"status": "active"}, {"handle": "+15555550124"},
    {"quietHours": {"start": "00:00", "end": "24:00"}}])
def test_patch_cannot_activate_rebind_or_set_invalid_preferences(stack, change):
    client, _ = stack
    login(client)
    assert create(client).status_code == 201
    assert client.patch(PERSON + "/contact", json=change).status_code == 422


def test_exact_handle_binding_expiry_attempt_limit_and_reissue(stack):
    client, db = stack
    login(client)
    code = create(client).json()["code"]
    assert proof(client, code, senderId="+15555550124").status_code == 400
    assert proof(client, code, provider="terminal").status_code == 400
    for _ in range(5):
        assert proof(client, "A" * 43).status_code == 400
    assert proof(client, code).status_code == 400
    db.contacts.update_one({}, {"$set": {"enrollmentExpiresAt": datetime.now(timezone.utc) - timedelta(seconds=1)}})
    assert proof(client, code).status_code == 400
    replacement = create(client)
    assert replacement.status_code == 201
    fresh = replacement.json()["code"]
    assert fresh != code
    assert proof(client, code).status_code == 400
    assert proof(client, fresh).status_code == 200


@pytest.mark.parametrize("change", ["stop", "provider-stop", "revoke", "remap", "delete-person", "delete-user"])
def test_proof_rechecks_current_consent_initiator_and_identity(stack, change):
    client, db = stack
    login(client)
    code = create(client).json()["code"]
    if change == "stop":
        assert client.post(PERSON + "/contact/stop", json={}).status_code == 200
    elif change == "provider-stop":
        contacts.stop_contacts(db, "imessage", BODY["handle"], contacts.matching_contacts(db, "imessage", BODY["handle"]))
    elif change == "revoke":
        db.families.update_one({"_id": "demo_family"}, {"$pull": {"members": {"userId": "demo_user_david"}}})
    elif change == "remap":
        db.people.update_one({"_id": "dad"}, {"$set": {"userId": "remapped-user"}})
    elif change == "delete-person":
        db.people.delete_one({"_id": "dad"})
    else:
        db.users.delete_one({"_id": "demo_user_david"})
    assert proof(client, code).status_code == 400
    assert db.contacts.find_one({})["status"] != "active"


def test_admin_can_prepare_relative_but_recipient_must_prove_handle(stack):
    client, db = stack
    login(client)
    # Use an unlinked seeded relative's actual id, never a caller-supplied user mapping.
    relative_id = db.people.find_one({"familyId": "demo_family", "userId": None})["_id"]
    response = create(client, BASE + f"/people/{relative_id}")
    assert response.status_code == 201
    assert db.contacts.find_one({})["status"] == "pending"
    db.families.update_one({"_id": "demo_family", "members.userId": "demo_user_david"}, {"$set": {"members.0.role": "member"}})
    assert proof(client, response.json()["code"]).status_code == 400


def test_concurrent_redemption_consumes_once(stack):
    client, db = stack
    login(client)
    code = create(client).json()["code"]
    def redeem(_):
        return contacts.redeem_enrollment(db, "imessage", BODY["handle"], code, "space", space_kind="direct")
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(redeem, range(8))) == 1


def test_email_normalization_and_legacy_crossfamily_conflict_are_generic(stack):
    client, db = stack
    login(client)
    response = create(client, handle="Nate@Example.COM")
    assert response.json()["contact"]["handle"] == "nate@example.com"
    assert proof(client, response.json()["code"], senderId="NATE@EXAMPLE.COM").status_code == 200
    db.contacts.insert_one({"_id": "foreign", "familyId": "private-family", "personId": "private-person",
                            "channel": "imessage", "handle": "+15555550124", "status": "active"})
    response = create(client, BASE + "/people/mom", handle="+15555550124")
    assert response.status_code == 409 and "private" not in response.text


def test_proof_hmac_queries_unknown_fields_and_nonce_replay(stack):
    client, _ = stack
    login(client)
    code = create(client).json()["code"]
    assert client.post("/internal/channels/enroll", json={}).status_code == 401
    assert proof(client, code, familyId="spoof").status_code == 400
    path = "/internal/channels/enroll"
    raw = json.dumps({"messageId": "m", "provider": "imessage", "senderId": BODY["handle"], "spaceId": "s", "spaceKind": "direct", "code": code}).encode()
    headers = sign(raw, path, secret=SECRET)
    assert client.post(path + "?family=spoof", content=raw, headers=headers).status_code == 400
    assert client.post(path, content=raw, headers=headers).status_code == 200
    assert client.post(path, content=raw, headers=headers).status_code == 409


def test_create_rate_limit_and_single_managed_contact_per_person(stack):
    client, db = stack
    login(client)
    assert create(client).status_code == 201
    for _ in range(4):
        assert create(client).status_code == 409
    assert db.channelEnrollmentLimits.find_one({})["count"] == 5
    assert create(client, BASE + "/people/mom", handle="+15555550124").status_code == 409
    assert db.contacts.count_documents({}) == 1


def test_settings_feature_gate(stack, monkeypatch):
    client, _ = stack
    login(client)
    monkeypatch.setenv("STITCH_CHANNELS", "0")
    assert client.get(BASE + "/contacts").status_code == 404


@pytest.mark.parametrize("kind", [None, "group", "dm", "unknown", True])
def test_signed_enrollment_requires_exact_direct_space_proof(stack, kind):
    client, db = stack
    login(client)
    code = create(client).json()["code"]
    assert proof(client, code, spaceKind=kind).status_code == 400
    assert db.contacts.find_one({})["status"] == "pending"
    assert contacts.redeem_enrollment(db, "imessage", BODY["handle"], code, "room", space_kind=kind) is False
    assert proof(client, code).status_code == 200


def test_unsigned_or_missing_direct_proof_cannot_activate_contact(stack):
    client, db = stack
    login(client)
    code = create(client).json()["code"]
    data = {"messageId": "m", "provider": "imessage", "senderId": BODY["handle"], "spaceId": "s", "code": code}
    raw = json.dumps(data).encode()
    path = "/internal/channels/enroll"
    assert client.post(path, content=raw, headers=sign(raw, path, secret=SECRET)).status_code == 400
    assert client.post(path, json={**data, "spaceKind": "direct"}).status_code == 401
    assert client.patch(PERSON + "/contact", json={"spaceKind": "direct"}).status_code == 422
    assert db.contacts.find_one({})["status"] == "pending"


@pytest.mark.parametrize("same_handle", [False, True])
def test_concurrent_creation_cannot_duplicate_person_or_share_handle_across_families(stack, same_handle):
    _, db = stack
    family = db.families.find_one({"_id": "demo_family"})
    family["_id"] = "other-family"
    db.families.insert_one(family)
    person = db.people.find_one({"_id": "dad"})
    person.update(_id="other-dad", familyId="other-family")
    db.people.insert_one(person)
    def start(n):
        family_id = "other-family" if same_handle and n else "demo_family"
        person_id = "other-dad" if same_handle and n else "dad"
        handle = BODY["handle"] if same_handle else f"+1555555012{n}"
        try:
            contacts.create_pending_enrollment(db, family_id, person_id, "demo_user_david", "imessage", handle,
                {key: BODY[key] for key in ("timezone", "cadenceDays", "quietHours")})
            return True
        except contacts.ContactConflict:
            return False
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sum(pool.map(start, range(2))) == 1
    assert db.contacts.count_documents({}) == 1


def test_stop_between_proof_check_and_atomic_activation_wins(stack, monkeypatch):
    client, db = stack
    login(client)
    code = create(client).json()["code"]
    original = contacts.settings_person
    def stop_during_authorization(*args):
        person = original(*args)
        rows = contacts.matching_contacts(db, "imessage", BODY["handle"])
        contacts.stop_contacts(db, "imessage", BODY["handle"], rows)
        return person
    monkeypatch.setattr(contacts, "settings_person", stop_during_authorization)
    assert proof(client, code).status_code == 400
    assert db.contacts.find_one({})["status"] == "stopped"


def test_stopped_handle_cannot_be_rebound_and_requires_fresh_proof(stack):
    client, db = stack
    login(client)
    old = create(client).json()["code"]
    assert client.post(PERSON + "/contact/stop", json={}).status_code == 200
    assert create(client, handle="+15555550124").status_code == 409
    assert create(client, BASE + "/people/mom").status_code == 409
    fresh = create(client)
    assert fresh.status_code == 201
    assert db.contacts.find_one({})["status"] == "pending"
    assert proof(client, old).status_code == 400
    assert proof(client, fresh.json()["code"]).status_code == 200
