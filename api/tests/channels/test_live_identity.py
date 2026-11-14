"""Pre-live contact authority checks; real local auth, fake DB, no provider calls."""

import json
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
