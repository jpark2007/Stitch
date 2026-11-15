"""Pre-live contact authority checks; real local auth, fake DB, no provider calls."""

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier
import pytest

from agent.conversation.service import ConversationService
from db import contacts, conversations, families, users
from channels.routes import router
from tests.channels.test_inbox import sign
from tests.channels.test_settings import stack, login, create, proof, BASE, BODY, SECRET


def test_admin_cannot_enroll_another_linked_member_but_self_and_unlinked_are_allowed(stack):
    client, db = stack
    login(client)
    for person in ("mom", "sam"):
        assert create(client, BASE + f"/people/{person}").status_code == 403
    assert db.contacts.count_documents({}) == 0
    assert create(client).status_code == 201
    unlinked = db.people.find_one({"familyId": "demo_family", "userId": None})["_id"]
    response = create(client, BASE + f"/people/{unlinked}", handle="+15555550124")
    assert response.status_code == 201
    assert proof(client, response.json()["code"], senderId="+15555550124").status_code == 200
    row = contacts.resolve_contact(db, "imessage", "+15555550124")
    service = ConversationService.for_contact(db, "demo_family", row["_id"])
    assert service.context.principal == f"contact:{row['_id']}"


@pytest.mark.parametrize("revocation", ["membership", "mapping", "account", "unlink"])
def test_active_intake_worker_and_existing_service_reject_revoked_linked_principal(stack, revocation):
    client, db = stack
    client.app.include_router(router)
    login(client, "sam@demo.stitch")
    response = create(client, BASE + "/people/sam")
    assert response.status_code == 201
    assert proof(client, response.json()["code"]).status_code == 200
    row = contacts.resolve_contact(db, "imessage", BODY["handle"])
    service = ConversationService.for_contact(db, "demo_family", row["_id"])
    assert service.context.principal == "demo_user_sam"
    if revocation == "membership":
        # Deliberately leave person mapping unchanged: lookup checks cannot rely
        # on a lifecycle hook or the later unlink finishing successfully.
        db.families.update_one({"_id": "demo_family"}, {"$pull": {"members": {"userId": "demo_user_sam"}}})
    elif revocation == "account":
        users.delete_users(db, emails=[], user_ids=["demo_user_sam"])
    else:
        if revocation == "mapping":
            db.people.update_one({"_id": "mom"}, {"$set": {"userId": None}})
        db.people.update_one({"_id": "sam", "familyId": "demo_family"}, {"$set": {"userId": None if revocation == "unlink" else "demo_user_ana"}})
    for operation in (
        lambda: contacts.resolve_contact(db, "imessage", BODY["handle"]),
        lambda: contacts.worker_contact(db, "demo_family", row["_id"]),
        lambda: conversations.active_contact(db, "demo_family", row["_id"]),
        service._state,
    ):
        with pytest.raises(PermissionError):
            operation()
    path = "/internal/channels/authorize"
    raw = json.dumps({"provider": "imessage", "senderId": BODY["handle"]}).encode()
    assert client.post(path, content=raw, headers=sign(raw, path, secret=SECRET)).status_code == 403


def test_remove_member_public_lifecycle_immediately_revokes_contact_access(stack):
    client, db = stack
    login(client, "sam@demo.stitch")
    response = create(client, BASE + "/people/sam")
    assert proof(client, response.json()["code"]).status_code == 200
    assert families.remove_member(db, "demo_family", "demo_user_sam")
    with pytest.raises(PermissionError):
        contacts.resolve_contact(db, "imessage", BODY["handle"])


def test_unlinked_contact_cannot_become_the_newly_linked_users_principal(stack):
    client, db = stack
    login(client)
    relative = db.people.find_one({"familyId": "demo_family", "userId": None})["_id"]
    response = create(client, BASE + f"/people/{relative}")
    assert proof(client, response.json()["code"]).status_code == 200
    row = contacts.resolve_contact(db, "imessage", BODY["handle"])
    db.people.update_one({"_id": "sam"}, {"$set": {"userId": None}})
    db.people.update_one({"_id": relative}, {"$set": {"userId": "demo_user_sam"}})
    with pytest.raises(PermissionError):
        conversations.active_contact(db, "demo_family", row["_id"])


def test_legacy_admin_created_linked_enrollment_cannot_be_redeemed(stack):
    client, db = stack
    login(client, "sam@demo.stitch")
    response = create(client, BASE + "/people/sam")
    db.contacts.update_one({}, {"$set": {"enrollmentInitiator": "demo_user_david"}})
    assert proof(client, response.json()["code"]).status_code == 400
    assert db.contacts.find_one({})["status"] == "pending"


def add_family(db, name):
    family = db.families.find_one({"_id": "demo_family"})
    db.families.insert_one({**family, "_id": name})
    person = db.people.find_one({"_id": "dad"})
    db.people.insert_one({**person, "_id": name + "-person", "familyId": name})
    return f"/families/{name}/channels/people/{name}-person"


@pytest.mark.parametrize("stale_state", ["expired", "stopped"])
def test_stale_handle_is_atomically_reclaimed_in_another_family_with_fresh_proof(stack, stale_state):
    client, db = stack
    login(client)
    original = create(client).json()
    slot = db.contacts.find_one({})
    if stale_state == "expired":
        db.contacts.update_one({}, {"$set": {"enrollmentExpiresAt": datetime.now(timezone.utc) - timedelta(seconds=1)}})
    else:
        assert proof(client, original["code"]).status_code == 200
        contacts.stop_contacts(db, "imessage", BODY["handle"], contacts.matching_contacts(db, "imessage", BODY["handle"]))
    destination = add_family(db, "other-family")
    response = create(client, destination)
    assert response.status_code == 201
    pending = db.contacts.find_one({})
    assert pending["_id"] == slot["_id"] and pending["familyId"] == "other-family"
    assert pending["status"] == "pending"
    assert pending["optInAt"] is None and pending["verifiedAt"] is None
    assert pending["spaceId"] is None and pending["spaceKind"] is None
    assert "stoppedAt" not in pending
    with pytest.raises(PermissionError):
        conversations.active_contact(db, "demo_family", slot["_id"])
    assert proof(client, original["code"]).status_code == 400
    assert proof(client, response.json()["code"]).status_code == 200
    assert contacts.resolve_contact(db, "imessage", BODY["handle"])["familyId"] == "other-family"
    assert db.contacts.count_documents({}) == 1


def test_active_and_unexpired_other_family_conflicts_have_same_generic_response(stack):
    client, db = stack
    login(client)
    original = create(client).json()
    destination = add_family(db, "private-family")
    pending = create(client, destination)
    assert proof(client, original["code"]).status_code == 200
    active = create(client, destination)
    assert pending.status_code == active.status_code == 409
    assert pending.json() == active.json() == {"detail": "Contact enrollment unavailable"}
    assert db.channelEnrollmentLimits.find_one({})["count"] == 3
    assert db.contacts.find_one({})["familyId"] == "demo_family"


def test_concurrent_stale_slot_reclaim_has_only_one_winner(stack, monkeypatch):
    client, db = stack
    login(client)
    create(client)
    db.contacts.update_one({}, {"$set": {"status": "stopped", "stoppedAt": datetime.now(timezone.utc)}})
    for name in ("family-b", "family-c"):
        add_family(db, name)
    original = contacts.matching_contacts
    barrier = Barrier(2)
    def same_snapshot(*args):
        rows = original(*args)
        barrier.wait(timeout=5)
        return rows
    monkeypatch.setattr(contacts, "matching_contacts", same_snapshot)
    def reclaim(name):
        try:
            contacts.create_pending_enrollment(db, name, name + "-person", "demo_user_david", "imessage", BODY["handle"],
                {key: BODY[key] for key in ("timezone", "cadenceDays", "quietHours")})
            return True
        except contacts.ContactConflict:
            return False
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sum(pool.map(reclaim, ("family-b", "family-c"))) == 1
    assert db.contacts.count_documents({}) == 1
    assert db.contacts.find_one({})["status"] == "pending"


def test_stale_reclaimer_cannot_overwrite_changed_consent_proof(stack, monkeypatch):
    client, db = stack
    login(client)
    create(client)
    db.contacts.update_one({}, {"$set": {"status": "stopped", "stoppedAt": datetime.now(timezone.utc)}})
    destination = add_family(db, "other-family")
    original = db.contacts.find_one_and_replace
    def new_proof_wins(query, replacement, **kwargs):
        db.contacts.update_one({}, {"$set": {"status": "active", "verifiedAt": datetime.now(timezone.utc),
                                            "optInAt": datetime.now(timezone.utc), "spaceId": "fresh-space"}})
        return original(query, replacement, **kwargs)
    monkeypatch.setattr(db.contacts, "find_one_and_replace", new_proof_wins)
    assert create(client, destination).status_code == 409
    assert db.contacts.find_one({})["familyId"] == "demo_family"
    assert db.contacts.find_one({})["spaceId"] == "fresh-space"


def test_same_family_reassignment_keeps_previous_persons_draft_inaccessible(stack):
    client, db = stack
    login(client)
    relatives = list(db.people.find({"familyId": "demo_family", "userId": None}))[:2]
    first = create(client, BASE + f"/people/{relatives[0]['_id']}").json()
    assert proof(client, first["code"]).status_code == 200
    contact = db.contacts.find_one({})
    service = ConversationService.for_contact(db, "demo_family", contact["_id"])
    service.save_story_draft("The first person's private story.")
    contacts.stop_contacts(db, "imessage", BODY["handle"], [contact])
    second = create(client, BASE + f"/people/{relatives[1]['_id']}")
    assert second.status_code == 201
    assert proof(client, second.json()["code"]).status_code == 200
    with pytest.raises(PermissionError):
        ConversationService.for_contact(db, "demo_family", contact["_id"])
    stored = db.conversations.find_one({"contactId": contact["_id"]})
    assert stored["personId"] == relatives[0]["_id"]
    assert stored["draft"]["text"] == "The first person's private story."
