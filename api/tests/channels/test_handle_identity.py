"""Signed enrollment and intake share provider-specific trusted identity lookup."""

import json

import pytest

from channels.routes import router
from db import contacts
from tests.channels.test_inbox import sign
from tests.channels.test_settings import stack, login, create, proof, SECRET
from tests.channels.test_photos import envelope as photo_envelope
from tests.channels.test_voice import envelope as voice_envelope, Transcriber


def post(client, route, body):
    path = "/internal/channels/" + route
    raw = json.dumps(body).encode()
    return client.post(path, content=raw, headers=sign(raw, path, secret=SECRET))


@pytest.fixture
def enrolled(stack):
    client, db = stack
    client.app.include_router(router)
    client.app.state.channels_transcriber = Transcriber()
    login(client)
    response = create(client, handle="Nate@Example.COM")
    assert response.status_code == 201
    assert response.json()["contact"]["handle"] == "nate@example.com"
    assert proof(client, response.json()["code"], senderId="NATE@Example.com").status_code == 200
    return client, db


@pytest.mark.parametrize("sender", ["Nate@Example.COM", "nate@example.com", "NATE@EXAMPLE.COM"])
def test_signed_email_intake_and_stop_share_enrollment_identity(enrolled, sender):
    client, db = enrolled
    common = {"provider": "imessage", "senderId": sender, "spaceId": "verified-room"}
    assert post(client, "authorize", {k: common[k] for k in ("provider", "senderId")}).status_code == 200
    text = {**common, "messageId": "text", "text": "Grandma taught me to bake."}
    photo = photo_envelope(**common)
    voice = voice_envelope(**common)
    for path, body in [("inbound", text), ("inbound-photo", photo), ("inbound-voice", voice)]:
        assert post(client, path, body).json() == {"accepted": True, "duplicate": False}
        assert post(client, path, body).json() == {"accepted": True, "duplicate": True}
        # Normalize only identity lookup: signed raw content still defines collisions.
        alternate = "NATE@example.com" if sender != "NATE@example.com" else "nate@example.com"
        assert post(client, path, {**body, "senderId": alternate}).status_code == 409
    receipts = list(db.channelMessages.find({"kind": "inbound"}))
    assert len(receipts) == 3
    assert all(row["handle"] == "nate@example.com" for row in receipts)
    assert all(row["contactProof"]["handle"] == "nate@example.com" for row in receipts)
    assert post(client, "inbound", {**common, "messageId": "stop", "text": "STOP"}).json()["stopped"]
    assert db.contacts.find_one({})["status"] == "stopped"
    assert post(client, "authorize", {"provider": "imessage", "senderId": sender}).status_code == 403
    assert post(client, "inbound", {**text, "messageId": "later"}).status_code == 403


def test_ambiguous_email_blocks_intake_but_stop_revokes_all_families(enrolled):
    client, db = enrolled
    original = db.contacts.find_one({})
    db.people.insert_one({"_id": "other-person", "familyId": "other-family"})
    db.contacts.insert_one({**original, "_id": "other-contact", "familyId": "other-family", "personId": "other-person"})
    assert post(client, "authorize", {"provider": "imessage", "senderId": "NATE@Example.COM"}).status_code == 409
    response = post(client, "inbound", {"provider": "imessage", "senderId": "NATE@Example.COM",
        "spaceId": "verified-room", "messageId": "stop-all", "text": "UNSUBSCRIBE"})
    assert response.status_code == 202
    assert db.contacts.count_documents({"status": "stopped"}) == 2


def test_terminal_email_shaped_identifiers_remain_case_sensitive(enrolled):
    client, db = enrolled
    original = db.contacts.find_one({})
    for index, handle in enumerate(("Nate@Example.COM", "nate@example.com")):
        db.contacts.insert_one({**original, "_id": f"terminal-{index}", "familyId": f"terminal-family-{index}",
                               "channel": "terminal", "handle": handle})
    matches = contacts.matching_contacts(db, "terminal", "Nate@Example.COM")
    assert [row["_id"] for row in matches] == ["terminal-0"]
    contacts.stop_contacts(db, "terminal", "Nate@Example.COM", matches)
    assert db.contacts.find_one({"_id": "terminal-0"})["status"] == "stopped"
    assert db.contacts.find_one({"_id": "terminal-1"})["status"] == "active"
    assert db.contacts.find_one({"_id": original["_id"]})["status"] == "active"
    assert contacts.matching_contacts(db, "terminal", "NATE@EXAMPLE.COM") == []


@pytest.mark.parametrize("handle", [" nate@example.com", "nate@example.com ", "nаte@example.com", "other@example.com"])
def test_lookup_does_not_trim_or_unicode_fold_into_enrolled_identity(enrolled, handle):
    assert contacts.matching_contacts(enrolled[1], "imessage", handle) == []
