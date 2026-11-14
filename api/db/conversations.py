"""Durable text receipts and replay guards; this slice does not run conversations."""

import hashlib
import json
from datetime import datetime, timezone

from pymongo.errors import DuplicateKeyError
from pymongo import ReturnDocument
from . import contacts, media


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
            "expireAt": datetime.fromtimestamp(timestamp + 900, timezone.utc),
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
        "principal": contact.get("userId") or f"contact:{contact['_id']}",
        "handle": contact["handle"],
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


def worker_candidates(db, *, limit: int = 5) -> list[tuple[str, str]]:
    """Trusted queue discovery returns routing keys only, never story content.

    All subsequent reads/writes require an explicit family. Active runs remain
    candidates even without a new inbound message, so bounded ticks can relay done.
    """
    # This global queue cursor holds only opaque IDs, not family content. Rotate
    # both the bounded scan window and source priority to avoid backlog starvation.
    marker = db.channelMessages.find_one({"_id": "worker-scan"}) or {}
    sources = [("inbox", {"kind": "inbound", "status": "received"}, db.channelMessages),
               ("runs", {"kind": "channelWorker", "pollRun": True, "status": "idle"}, db.conversations)]
    if marker.get("runsFirst"):
        sources.reverse()
    db.channelMessages.update_one({"_id": "worker-scan"}, {"$set": {
        "kind": "workerScan", "runsFirst": not marker.get("runsFirst", False)}}, upsert=True)
    keys = []
    for source, query, collection in sources:
        after = marker.get(source, "")
        rows = list(collection.find({**query, "_id": {"$gt": after}},
                    {"familyId": 1, "contactId": 1}).sort("_id", 1).limit(100))
        if not rows:
            rows = list(collection.find(query, {"familyId": 1, "contactId": 1}).sort("_id", 1).limit(100))
        for row in rows:
            db.channelMessages.update_one({"_id": "worker-scan"}, {"$set": {source: row["_id"]}})
            key = (row["familyId"], row["contactId"])
            worker = db.conversations.find_one({"_id": "worker:" + conversation_key(*key),
                                                "familyId": key[0]}, {"status": 1})
            if worker and worker["status"] != "idle":
                continue
            if key not in keys:
                keys.append(key)
            if len(keys) >= limit:
                return keys
    return keys


def acquire_worker(db, family_id: str, contact_id: str, token: str) -> dict | None:
    key = "worker:" + conversation_key(family_id, contact_id)
    db.conversations.update_one({"_id": key, "familyId": family_id}, {"$setOnInsert": {
        "kind": "channelWorker", "familyId": family_id, "contactId": contact_id,
        "status": "idle", "outbox": [], "pollRun": False,
    }}, upsert=True)
    return db.conversations.find_one_and_update(
        {"_id": key, "familyId": family_id, "status": "idle"},
        {"$set": {"status": "running", "token": token}}, return_document=ReturnDocument.AFTER)


def update_worker(db, family_id: str, contact_id: str, token: str, fields: dict) -> dict:
    row = db.conversations.find_one_and_update(
        {"_id": "worker:" + conversation_key(family_id, contact_id), "familyId": family_id,
         "token": token, "status": "running"}, {"$set": fields}, return_document=ReturnDocument.AFTER)
    if row is None:
        raise RuntimeError("Worker reservation changed")
    return row


def worker_receipts(db, family_id: str, contact_id: str) -> list[dict]:
    return list(db.channelMessages.find({"familyId": family_id, "contactId": contact_id,
        "kind": "inbound", "status": "received"}).sort([("createdAt", 1), ("_id", 1)]).limit(10))


def reserve_worker_receipts(db, family_id: str, contact_id: str, ids: list[str], token: str) -> None:
    result = db.channelMessages.update_many({"familyId": family_id, "contactId": contact_id,
        "_id": {"$in": ids}, "status": "received"}, {"$set": {"status": "processing", "workerToken": token}})
    if result.modified_count != len(ids):
        raise RuntimeError("Receipt reservation changed; reconciliation required")


def complete_worker_receipts(db, family_id: str, contact_id: str, token: str) -> None:
    db.channelMessages.update_many({"familyId": family_id, "contactId": contact_id,
        "status": "processing", "workerToken": token}, {"$set": {"status": "processed"}})


class PhotoConflict(ValueError):
    pass


class PhotoPending(RuntimeError):
    pass


def _photo_contact(db, original: dict, message: dict) -> None:
    current = contacts.resolve_contact(db, message["provider"], message["senderId"])
    if any(current.get(k) != original.get(k) for k in ("_id", "familyId", "personId", "userId")):
        raise PermissionError("Contact identity changed")


def save_photo_inbound(db, contact: dict, message: dict, data: bytes) -> bool:
    """Reserve one writer; retry can recover a completed identical media write.

    An uploading reservation without a readable file is deliberately not stolen:
    GridFS may contain incomplete chunks or an in-flight writer. Reconcile that
    state explicitly instead of racing another upload under the same ObjectId.
    """
    family = contact["familyId"]
    _photo_contact(db, contact, message)
    identity = json.dumps([family, message["provider"], message["messageId"]], separators=(",", ":"))
    key = "inbound:" + hashlib.sha256(identity.encode()).hexdigest()
    content_sha = hashlib.sha256(data).hexdigest()
    digest = hashlib.sha256(json.dumps({**message, "photoSha256": content_sha}, sort_keys=True,
                                       separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    media_id = hashlib.sha256((identity + "\n" + message["attachmentId"]).encode()).hexdigest()[:24]
    record = {"_id": key, "kind": "inbound", "receiptType": "photo", "familyId": family,
              "contactId": str(contact["_id"]), "personId": contact["personId"],
              "principal": contact.get("userId") or f"contact:{contact['_id']}",
              "handle": contact["handle"],
              "provider": message["provider"], "messageId": message["messageId"],
              "spaceId": message["spaceId"], "attachmentId": message["attachmentId"],
              "text": message["caption"], "contentHash": digest, "photoSha256": content_sha,
              "reservedMediaId": media_id, "status": "photo_pending", "uploadState": "reserved",
              "createdAt": datetime.now(timezone.utc)}
    duplicate = False
    try:
        db.channelMessages.insert_one(record)
    except DuplicateKeyError:
        duplicate = True
        previous = db.channelMessages.find_one({"_id": key, "familyId": family})
        if (not previous or previous.get("contentHash") != digest
                or previous.get("receiptType") != "photo" or previous.get("contactId") != str(contact["_id"])):
            raise PhotoConflict("Message content changed") from None

    def complete_file():
        found = media.get_media(db, family, media_id)
        if found is not None and (found.kind != "photo" or found.content_type != message["mimeType"]
                                  or hashlib.sha256(found.data).hexdigest() != content_sha):
            raise PhotoConflict("Stored photo does not match its reservation")
        return found

    if complete_file() is None:
        claimed = db.channelMessages.find_one_and_update(
            {"_id": key, "familyId": family, "contentHash": digest, "uploadState": "reserved"},
            {"$set": {"uploadState": "uploading"}})
        if claimed is None:
            raise PhotoPending("Photo upload is incomplete or in progress")
        _photo_contact(db, contact, message)
        try:
            media.put_media(db, family, data, content_type=message["mimeType"], kind="photo", media_id=media_id)
        except Exception:
            raise PhotoPending("Photo upload outcome is uncertain") from None
        if complete_file() is None:
            raise PhotoPending("Photo upload is incomplete")
    _photo_contact(db, contact, message)
    try:
        result = db.channelMessages.update_one(
            {"_id": key, "familyId": family, "contentHash": digest, "status": "photo_pending"},
            {"$set": {"status": "received", "uploadState": "complete", "photoIds": [media_id]}})
        if result.matched_count != 1:
            # A duplicate must not reset a receipt already consumed by a worker.
            completed = db.channelMessages.find_one({
                "_id": key, "familyId": family, "contentHash": digest,
                "uploadState": "complete", "photoIds": [media_id]})
            if completed is None:
                raise PhotoPending("Photo receipt disappeared")
    except Exception:
        raise PhotoPending("Photo receipt outcome is uncertain") from None
    return duplicate
