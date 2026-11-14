"""Durable text receipts and replay guards; this slice does not run conversations."""

import hashlib
import json
from datetime import datetime, timezone

from pymongo.errors import DuplicateKeyError
from pymongo import ReturnDocument


def ensure_message_indexes(db) -> None:
    # Only authentication records have expireAt; story receipts never expire here.
    db.channelMessages.create_index("expireAt", expireAfterSeconds=0,
                                    name="channel_auth_expiry")
    db.channelMessages.create_index([("familyId", 1), ("contactId", 1), ("createdAt", 1)],
                                    name="channel_inbox_contact")


def consume_nonce(db, nonce: str, timestamp: int) -> bool:
    try:
        db.channelMessages.insert_one({
            "_id": "auth:" + nonce,
            "kind": "auth",
            # Keep until beyond the latest instant this timestamp could pass auth.
            "expireAt": datetime.fromtimestamp(timestamp + 301, timezone.utc),
        })
    except DuplicateKeyError:
        return False
    return True


def save_inbound(db, contact: dict, message: dict, *, stopped: bool = False) -> bool:
    """Return duplicate status. A reused ID with different content is rejected.

    The unique Mongo _id serializes concurrent deliveries without a find/insert race.
    Receipt writes are durable according to the configured Mongo write concern.
    """
    family_id = contact["familyId"]
    identity = json.dumps([family_id, message["provider"], message["messageId"]], separators=(",", ":"))
    key = "inbound:" + hashlib.sha256(identity.encode()).hexdigest()
    digest = hashlib.sha256(json.dumps(message, sort_keys=True, separators=(",", ":"),
                                       ensure_ascii=False).encode()).hexdigest()
    record = {
        "_id": key, "kind": "inbound", "familyId": family_id,
        "contactId": str(contact["_id"]), "personId": contact["personId"],
        "provider": message["provider"], "messageId": message["messageId"],
        "spaceId": message["spaceId"], "text": message["text"],
        "contentHash": digest, "status": "stop_requested" if stopped else "received",
        "createdAt": datetime.now(timezone.utc),
    }
    try:
        db.channelMessages.insert_one(record)
        return False
    except DuplicateKeyError:
        previous = db.channelMessages.find_one({"_id": key, "familyId": family_id})
        if previous is None or previous.get("contentHash") != digest or previous.get("contactId") != str(contact["_id"]):
            raise ValueError("Message ID was already used for different content") from None
        return True


def active_contact(db, family_id: str, contact_id: str) -> dict:
    """Resolve an already selected contact, never a model-provided family."""
    if not family_id or not contact_id:
        raise PermissionError("An active verified contact is required")
    contact = db.contacts.find_one({"_id": contact_id, "familyId": family_id})
    if (not contact or contact.get("status") != "active" or not contact.get("verifiedAt")
            or not contact.get("optInAt") or not db.people.find_one(
                {"_id": contact.get("personId"), "familyId": family_id}, {"_id": 1})):
        raise PermissionError("An active verified contact is required")
    return contact


def conversation_key(family_id: str, contact_id: str) -> str:
    if not family_id or not contact_id:
        raise ValueError("Family and contact are required")
    return hashlib.sha256(json.dumps([family_id, contact_id]).encode()).hexdigest()


def load_conversation(db, family_id: str, contact_id: str, person_id: str, principal: str) -> dict:
    key = conversation_key(family_id, contact_id)
    db.conversations.update_one({"_id": key, "familyId": family_id}, {"$setOnInsert": {
        "familyId": family_id, "contactId": contact_id, "personId": person_id,
        "principal": principal, "draft": {"text": ""}, "revision": 0,
        "status": "draft", "cursor": 0, "modelCalls": 0,
    }}, upsert=True)
    row = db.conversations.find_one({"_id": key, "familyId": family_id})
    if row["personId"] != person_id or row["principal"] != principal:
        raise PermissionError("Contact identity changed; operator reconciliation required")
    return row


def update_conversation(db, family_id: str, contact_id: str, revision: int, fields: dict,
                        *, status: str | None = None) -> dict:
    query = {"_id": conversation_key(family_id, contact_id), "familyId": family_id,
             "revision": revision}
    if status is not None:
        query["status"] = status
    row = db.conversations.find_one_and_update(
        query, {"$set": fields, "$inc": {"revision": 1}}, return_document=ReturnDocument.AFTER)
    if row is None:
        raise RuntimeError("Conversation changed; reload before retrying")
    return row


def take_conversation_model_call(db, family_id: str, contact_id: str, limit: int) -> None:
    row = db.conversations.find_one_and_update(
        {"_id": conversation_key(family_id, contact_id), "familyId": family_id,
         "modelCalls": {"$lt": limit}}, {"$inc": {"modelCalls": 1}})
    if row is None:
        raise RuntimeError("Conversation model-call limit reached")
