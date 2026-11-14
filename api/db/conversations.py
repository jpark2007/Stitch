"""Durable text receipts and replay guards; this slice does not run conversations."""

import hashlib
import json
from datetime import datetime, timezone

from pymongo.errors import DuplicateKeyError


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
