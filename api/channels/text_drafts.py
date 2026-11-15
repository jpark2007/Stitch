"""Private text drafts are a view of durable, identity-bound inbound receipts.

No second write or studio run is needed: accepting a text already stores its exact
words atomically in channelMessages, whose existing family purge covers drafts.
"""

from datetime import datetime
import re

from db import contacts, conversations
from .consent import is_stop_command


def ordinary_text(text):
    if not isinstance(text, str) or not text.strip() or is_stop_command(text):
        return False
    command = text.strip().upper()
    return (command not in {"HELP", "CALL", "NEW", "SUBMIT", "KEEP", "YES", "NO"}
            and not re.fullmatch(r"YEAR(?:\s+.*)?", command)
            and not re.fullmatch(r"LINK(?:\s+.*)?", command))


def save_received_text(db, contact, message):
    """The receipt itself is the draft: duplicate IDs cannot create another copy."""
    return conversations.save_inbound(db, contact, message)


def list_text_drafts(db, family_id, person_id, actor_id):
    person = contacts.settings_person(db, family_id, person_id, actor_id)
    if person.get("userId") != actor_id:
        raise PermissionError("Only your own text drafts are available")
    contact = contacts.get_settings_contact(db, family_id, person_id, actor_id)
    if contact is None:
        return []
    current = contacts.worker_contact(db, family_id, str(contact["_id"]))
    proof = conversations.contact_proof(current)
    if (not proof or proof.get("spaceKind") != "direct" or not proof.get("spaceId")
            or not isinstance(proof.get("verifiedAt"), datetime)
            or not isinstance(proof.get("optInAt"), datetime)):
        raise PermissionError("Verified direct-message contact required")
    query = {"kind": "inbound", "familyId": family_id, "contactId": str(current["_id"]),
             "personId": person_id, "principal": actor_id, "provider": "imessage",
             "handle": current["handle"], "spaceId": current["spaceId"], "contactProof": proof,
             "receiptType": {"$exists": False}, "status": {"$in": ["received", "processing", "processed"]}}
    rows = db.channelMessages.find(query, {"text": 1, "createdAt": 1}).sort(
        [("createdAt", -1), ("_id", -1)]).limit(100)
    drafts = [{"id": str(row["_id"]), "text": row["text"], "createdAt": row["createdAt"]}
              for row in rows if ordinary_text(row.get("text")) and isinstance(row.get("createdAt"), datetime)][:50]
    # A removal, remap or fresh consent while reading cannot expose the prior proof.
    latest = contacts.worker_contact(db, family_id, str(current["_id"]))
    latest_person = contacts.settings_person(db, family_id, person_id, actor_id)
    if (latest_person.get("userId") != actor_id or latest.get("userId") != actor_id
            or conversations.contact_proof(latest) != proof):
        raise PermissionError("Contact identity changed")
    return drafts
