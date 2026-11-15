"""Call invitations and explicit, recipient-only test sends through the trusted bridge."""

from datetime import datetime, timezone
import hashlib
import os
import re
from uuid import uuid4

from db import contacts, conversations


def call_number():
    value = os.getenv("STITCH_CHANNELS_CALL_NUMBER", "")
    return value if re.fullmatch(r"\+[1-9][0-9]{7,14}", value) else None


def prompt_text(question):
    number = call_number()
    if not number:
        return f"{question}\n\nReply STOP to stop messages."
    return f"{question}\n\n{call_invitation()} Or reply here. Reply STOP to stop messages."


def call_invitation():
    number = call_number()
    if not number:
        return None
    target = os.getenv("STITCH_CHANNELS_CALL_AUTO", "").split(":")
    if len(target) == 3 and all(target):
        book = "Rick's demo book" if target[1] == "rick" else "the configured demo storyteller's book"
        return f"Call {number} to tell your story. No code is needed; this demo adds calls to {book}."
    return (f"Call {number} to tell your story. First get your private call code "
            "from Call interviews in Stitch settings.")


class TestPromptError(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message


def send_test_prompt(db, family_id, person_id, actor_id, request_id, sender):
    """Bypass cadence/quiet hours for an explicit self-test, never consent or proof.

    Test outbox entries live in the existing family-scoped worker document. They
    are never evicted or replayed automatically. The shared worker reservation
    serializes tests with normal processing; at 1000 tests operator review is
    required. Provider acknowledgement is not recipient delivery.
    """
    def eligible():
        person = contacts.settings_person(db, family_id, person_id, actor_id)
        if person.get("userId") != actor_id:
            raise PermissionError("Only your own contact can receive a test")
        selected = contacts.get_settings_contact(db, family_id, person_id, actor_id)
        if selected is None:
            raise LookupError("Contact not found")
        contact = contacts.worker_contact(db, family_id, str(selected["_id"]))
        if (contact.get("channel") != "imessage" or contact.get("spaceKind") != "direct"
                or not contact.get("spaceId")
                or not isinstance(contact.get("verifiedAt"), datetime)
                or not isinstance(contact.get("optInAt"), datetime)):
            raise PermissionError("Verify and opt in from your direct iMessage conversation first")
        return contact

    identity = eligible()
    contact_id = str(identity["_id"])
    proof = conversations.contact_proof(identity)

    def check():
        current = eligible()
        if (str(current["_id"]) != contact_id or current.get("userId") != actor_id
                or conversations.contact_proof(current) != proof):
            raise PermissionError("Contact proof changed")

    def prior(row):
        for entry in (row or {}).get("testPromptOutbox", []):
            if entry["requestId"] == request_id:
                status = entry["status"]
                return {"status": "sent" if status == "sent" else
                        "uncertain" if status == "uncertain" or row["status"] == "blocked" else "pending",
                        "duplicate": True}
        return None

    query = {"kind": "channelWorker", "familyId": family_id, "contactId": contact_id}
    previous = prior(db.conversations.find_one(query))
    if previous:
        return previous
    token = uuid4().hex
    row = conversations.acquire_worker(db, family_id, contact_id, token)
    if row is None:
        previous = prior(db.conversations.find_one(query))
        if previous:
            return previous
        raise TestPromptError(409, "Contact worker is busy or needs reconciliation")
    reserved = False
    entries = row.get("testPromptOutbox", [])
    try:
        previous = prior(row)
        if previous:
            conversations.update_worker(db, family_id, contact_id, token, {"status": "idle"})
            return previous
        if (len(entries) >= 1000 or any(x["status"] not in {"sent", "cancelled"} for x in row.get("outbox", []))
                or any(x["status"] != "sent" for x in entries)):
            raise TestPromptError(409, "Previous messages or test history need reconciliation")
        check()
        message_id = "test-" + hashlib.sha256(f"{family_id}:{contact_id}:{request_id}".encode()).hexdigest()
        entry = {"requestId": request_id, "status": "pending", "createdAt": datetime.now(timezone.utc),
                 "contactProof": proof, "message": {"messageId": message_id, "provider": "imessage",
                 "spaceId": identity["spaceId"], "text": prompt_text(
                     "Stitch test prompt: What is a favorite memory of time spent with your family?")}}
        entries.append(entry)
        # From this persistence attempt onward, a failure is uncertain and held.
        reserved = True
        conversations.update_worker(db, family_id, contact_id, token, {"testPromptOutbox": entries})
        check()
        entry["status"] = "sending"
        conversations.update_worker(db, family_id, contact_id, token, {"testPromptOutbox": entries})
        check()
        sender.send(entry["message"])
        entry["status"] = "sent"
        entry["sentAt"] = datetime.now(timezone.utc)
        conversations.update_worker(db, family_id, contact_id, token,
                                    {"testPromptOutbox": entries, "status": "idle"})
        return {"status": "sent", "duplicate": False}
    except Exception:
        if reserved:
            entry["status"] = "uncertain"
            try:
                conversations.update_worker(db, family_id, contact_id, token,
                    {"testPromptOutbox": entries, "status": "blocked", "reason": "test_prompt_uncertain"})
            except Exception:
                pass
            return {"status": "uncertain", "duplicate": False}
        conversations.update_worker(db, family_id, contact_id, token, {"status": "idle"})
        raise
