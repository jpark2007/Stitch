"""Durable text receipts and replay guards; this slice does not run conversations."""

import hashlib
import json
import secrets
from datetime import datetime, timezone, timedelta

from pymongo.errors import DuplicateKeyError
from pymongo import ReturnDocument
from . import contacts, media


def contact_proof(contact: dict) -> dict | None:
    """Snapshot the immutable observed iMessage consent generation; terminal is unchanged."""
    if contact.get("channel") != "imessage":
        return None
    return {key: contact.get(key) for key in
            ("handle", "channel", "spaceId", "spaceKind", "verifiedAt", "optInAt")}


class CallConflict(ValueError):
    pass


class CallRateLimited(ValueError):
    pass


def call_attempt(db, scope: str, identity: str, *, family_id=None, limit=10, now=None):
    """Atomic minute windows, TTL cleanup; no transcript, token or caller retained."""
    now = now or datetime.now(timezone.utc)
    window = int(now.timestamp()) // 60
    key = "call-rate:" + hashlib.sha256(f"{scope}:{identity}:{window}".encode()).hexdigest()
    try:
        row = db.channelMessages.find_one_and_update(
            {"_id": key, "count": {"$lt": limit}},
            {"$inc": {"count": 1}, "$setOnInsert": {"kind": "call-rate", "familyId": family_id,
                "expireAt": now + timedelta(minutes=2)}}, upsert=True, return_document=ReturnDocument.AFTER)
    except DuplicateKeyError:
        raise CallRateLimited("Try again shortly") from None
    return row


def call_person_proof(db, family_id: str, person_id: str, actor_id: str) -> dict:
    """Own linked person only; a membership generation prevents leave/rejoin revival."""
    family = db.families.find_one({"_id": family_id})
    person = db.people.find_one({"_id": person_id, "familyId": family_id, "userId": actor_id})
    member = next((m for m in (family or {}).get("members", []) if m.get("userId") == actor_id), None)
    if (not person or not member or not isinstance(member.get("joinedAt"), datetime)
            or db.users.find_one({"_id": actor_id}, {"_id": 1}) is None):
        raise LookupError("Call person unavailable")
    return {"actorId": actor_id, "personId": person_id, "membershipJoinedAt": member["joinedAt"]}


def call_code_hash(code: str) -> str:
    return hashlib.sha256(("stitch-call-pairing:" + code).encode("ascii")).hexdigest()


def create_call_pairing(db, family_id: str, person_id: str, actor_id: str, *, now=None) -> tuple[dict, str]:
    proof = call_person_proof(db, family_id, person_id, actor_id)
    now = now or datetime.now(timezone.utc)
    key = "call-pairing:" + conversation_key(family_id, person_id)
    call_attempt(db, "issue", family_id, family_id=family_id, now=now)
    for _ in range(5):
        code = f"{secrets.randbelow(10**12):012d}"
        fields = {"kind": "call-pairing", "familyId": family_id, **proof, "tokenHash": call_code_hash(code),
                  "state": "pending", "createdAt": now, "expireAt": now + timedelta(hours=1)}
        try:
            row = db.channelMessages.find_one_and_update(
                {"_id": key, "familyId": family_id, "state": {"$ne": "claiming"}},
                {"$set": fields, "$unset": {"conversationKey": ""}}, upsert=True,
                return_document=ReturnDocument.AFTER)
            # Membership changes after rotation cannot authorize ingestion.
            if call_person_proof(db, family_id, person_id, actor_id) != proof:
                raise LookupError("Call person changed")
            return row, code
        except DuplicateKeyError:
            existing = db.channelMessages.find_one({"_id": key, "familyId": family_id})
            if existing and existing.get("state") == "claiming":
                raise CallConflict("A call is being attached; retry after it completes") from None
    raise CallConflict("Pairing unavailable")


def _call_proof_current(db, row):
    proof = call_person_proof(db, row["familyId"], row["personId"], row["actorId"])
    if any(row.get(key) != value for key, value in proof.items()):
        raise LookupError("Call authority changed")


def accept_call_transcript(db, agent_id: str, conversation_id: str, code: str,
                           text: str, content_hash: str, *, now=None) -> bool:
    """Provider discovery uses only the secret token; no input family/person is trusted.

    A unique call reservation precedes ticket consumption. Binding is resumable only
    for the exact original content; no transcript is published before proof settles.
    """
    now = now or datetime.now(timezone.utc)
    key = "call:" + hashlib.sha256(json.dumps([agent_id, conversation_id], separators=(",", ":")).encode()).hexdigest()
    token_hash = call_code_hash(code)
    row = db.channelMessages.find_one({"_id": key, "kind": "call-draft"})
    if row:
        if row.get("contentHash") != content_hash or row.get("proofHash") != token_hash:
            raise CallConflict("Call identity conflict")
        _call_proof_current(db, row)
        if row["status"] in {"ready", "deleted"}:
            db.channelMessages.update_one({"kind": "call-pairing", "familyId": row["familyId"],
                "tokenHash": token_hash, "conversationKey": key, "state": "claiming"}, {"$set": {"state": "consumed"}})
            return True
        if row["status"] != "binding":
            raise CallConflict("Call binding unavailable")
    else:
        call_attempt(db, "bind", key, limit=5, now=now)
        pairing = db.channelMessages.find_one({"kind": "call-pairing", "tokenHash": token_hash,
            "state": "pending", "expireAt": {"$gt": now}})
        if pairing is None:
            raise LookupError("Call pairing unavailable")
        _call_proof_current(db, pairing)
        call_attempt(db, "family-bind", pairing["familyId"], family_id=pairing["familyId"], limit=20, now=now)
        row = {"_id": key, "kind": "call-draft", "status": "binding", "familyId": pairing["familyId"],
            **{name: pairing[name] for name in ("actorId", "personId", "membershipJoinedAt")},
            "proofHash": token_hash, "contentHash": content_hash, "createdAt": now}
        try:
            db.channelMessages.insert_one(row)
        except DuplicateKeyError:
            return accept_call_transcript(db, agent_id, conversation_id, code, text, content_hash, now=now)
    family_id = row["familyId"]
    claimed = db.channelMessages.find_one_and_update({"kind": "call-pairing", "familyId": family_id,
        "tokenHash": token_hash, "state": "pending", "expireAt": {"$gt": now},
        "actorId": row["actorId"], "personId": row["personId"], "membershipJoinedAt": row["membershipJoinedAt"]},
        {"$set": {"state": "claiming", "conversationKey": key}}, return_document=ReturnDocument.AFTER)
    if claimed is None:
        claimed = db.channelMessages.find_one({"kind": "call-pairing", "familyId": family_id,
            "tokenHash": token_hash, "state": {"$in": ["claiming", "consumed"]}, "conversationKey": key})
    if claimed is None:
        db.channelMessages.update_one({"_id": key, "familyId": family_id, "status": "binding"},
                                      {"$set": {"status": "rejected"}})
        raise CallConflict("Call pairing is no longer available")
    _call_proof_current(db, row)
    db.channelMessages.update_one({"_id": key, "familyId": family_id, "status": "binding"},
                                  {"$set": {"status": "ready", "text": text}})
    db.channelMessages.update_one({"kind": "call-pairing", "familyId": family_id,
        "tokenHash": token_hash, "conversationKey": key}, {"$set": {"state": "consumed"}})
    # Recheck immediately after persistence; stale membership never discloses text.
    try:
        _call_proof_current(db, row)
    except LookupError:
        db.channelMessages.update_one({"_id": key, "familyId": family_id},
                                      {"$set": {"status": "revoked"}, "$unset": {"text": ""}})
        raise
    return False


def list_call_drafts(db, family_id: str, actor_id: str) -> list[dict]:
    rows = list(db.channelMessages.find({"kind": "call-draft", "familyId": family_id,
        "actorId": actor_id, "status": "ready"}).sort([("createdAt", -1), ("_id", 1)]).limit(50))
    visible = []
    for row in rows:
        try:
            _call_proof_current(db, row)
        except LookupError:
            continue
        visible.append(row)
    return visible


def get_call_draft(db, family_id: str, actor_id: str, draft_id: str) -> dict:
    row = db.channelMessages.find_one({"_id": draft_id, "kind": "call-draft", "familyId": family_id,
                                       "actorId": actor_id, "status": "ready"})
    if row is None:
        raise LookupError("Call draft unavailable")
    _call_proof_current(db, row)
    return row


def remove_call_draft(db, family_id: str, actor_id: str, draft_id: str) -> None:
    row = get_call_draft(db, family_id, actor_id, draft_id)
    result = db.channelMessages.update_one({"_id": row["_id"], "familyId": family_id, "status": "ready"},
        {"$set": {"status": "deleted"}, "$unset": {"text": ""}})
    if result.modified_count != 1:
        raise LookupError("Call draft unavailable")


def ensure_message_indexes(db) -> None:
    # Authentication, pairing and short-window counters expire; story receipts do not.
    db.channelMessages.create_index("expireAt", expireAfterSeconds=0,
                                    name="channel_auth_expiry")
    db.channelMessages.create_index([("familyId", 1), ("contactId", 1), ("createdAt", 1)],
                                    name="channel_inbox_contact")
    # Only pairing tickets carry tokenHash. Draft receipts use proofHash so the
    # sparse unique index also works beside existing records on repeated startup.
    db.channelMessages.create_index("tokenHash", unique=True, sparse=True,
                                    name="channel_call_pairing_hash")


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
    if contact.get("channel") == "imessage":
        record["contactProof"] = contact_proof(contact)
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
    if contact is None:
        raise PermissionError("An active authorized contact is required")
    try:
        contacts.validate_contact_principal(db, contact)
    except (LookupError, ValueError, PermissionError):
        raise PermissionError("An active authorized contact is required") from None
    return contact


def conversation_key(family_id: str, contact_id: str) -> str:
    if not family_id or not contact_id:
        raise ValueError("Family and contact are required")
    return hashlib.sha256(json.dumps([family_id, contact_id]).encode()).hexdigest()


def has_pending_question(db, family_id: str, contact_id: str) -> bool:
    """Read the selected contact's question state without creating a draft."""
    row = db.conversations.find_one(
        {"_id": conversation_key(family_id, contact_id), "familyId": family_id,
         "contactId": contact_id}, {"pending": 1})
    return bool(row and row.get("pending"))


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
    """Record cumulative telemetry; the callback enforces its invocation limit.

    Keep the limit argument for existing callback instrumentation. No lifetime or
    daily cap is enforced by this counter.
    """
    row = db.conversations.find_one_and_update(
        {"_id": conversation_key(family_id, contact_id), "familyId": family_id,
         "contactId": contact_id}, {"$inc": {"modelCalls": 1}})
    if row is None:
        raise RuntimeError("Conversation missing for model-call telemetry")


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


def locked_worker(db, family_id: str, contact_id: str, token: str) -> dict:
    row = db.conversations.find_one({"_id": "worker:" + conversation_key(family_id, contact_id),
        "familyId": family_id, "contactId": contact_id, "token": token, "status": "running"})
    if row is None:
        raise RuntimeError("Worker reservation is required")
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


def resume_preflight_worker(db, family_id: str, contact_id: str) -> None:
    """Operator-only recovery of a proved pre-effect failure; never a generic reset.

    Stop worker ticks before invoking. A failed recovery remains held; old generic
    reconciliation states have no proof and cannot use this path.
    """
    active_contact(db, family_id, contact_id)
    key = "worker:" + conversation_key(family_id, contact_id)
    query = {"_id": key, "familyId": family_id, "contactId": contact_id,
             "status": "blocked", "reason": "preflight_only", "preflightOnly": True}
    worker = db.conversations.find_one(query)
    if worker is None:
        raise RuntimeError("No proven preflight-only failure to resume")
    state = db.conversations.find_one({"_id": conversation_key(family_id, contact_id), "familyId": family_id})
    if (not state or state["status"] != "draft" or state.get("runId") or state.get("pending")
            or state.get("relayBuffer") or state.get("answerStatus") == "sending"
            or worker.get("pollRun") or any(x["status"] not in {"sent", "cancelled"} for x in worker.get("outbox", []))):
        raise RuntimeError("Uncertain work cannot be resumed by preflight recovery")
    receipts = list(db.channelMessages.find({"familyId": family_id, "contactId": contact_id,
        "status": "processing"}))
    if any(r.get("workerToken") != worker.get("token") or r.get("receiptType") == "voice"
           or r.get("transcriptionStatus") for r in receipts):
        raise RuntimeError("Receipt effects require reconciliation")
    if db.conversations.update_one({**query, "token": worker["token"]}, {"$set": {"status": "recovering"}}).modified_count != 1:
        raise RuntimeError("Worker recovery changed")
    db.channelMessages.update_many({"familyId": family_id, "contactId": contact_id,
        "status": "processing", "workerToken": worker["token"]},
        {"$set": {"status": "received"}, "$unset": {"workerToken": ""}})
    db.conversations.update_one({"_id": key, "familyId": family_id, "status": "recovering", "token": worker["token"]},
        {"$set": {"status": "idle", "preflightOnly": False}, "$unset": {"reason": ""}})


class PhotoConflict(ValueError):
    pass


class PhotoPending(RuntimeError):
    pass


def _attachment_contact(db, original: dict, message: dict) -> None:
    current = contacts.resolve_contact(db, message["provider"], message["senderId"])
    if any(current.get(k) != original.get(k) for k in ("_id", "familyId", "personId", "userId")):
        raise PermissionError("Contact identity changed")


def save_photo_inbound(db, contact: dict, message: dict, data: bytes) -> bool:
    return _save_attachment_inbound(db, contact, message, data, receipt_type="photo", kind="photo")


def save_voice_inbound(db, contact: dict, message: dict, data: bytes) -> bool:
    return _save_attachment_inbound(db, contact, message, data, receipt_type="voice", kind="audio")


def _save_attachment_inbound(db, contact: dict, message: dict, data: bytes,
                             *, receipt_type: str, kind: str) -> bool:
    """Reserve one writer; retry can recover a completed identical media write.

    An uploading reservation without a readable file is deliberately not stolen:
    GridFS may contain incomplete chunks or an in-flight writer. Reconcile that
    state explicitly instead of racing another upload under the same ObjectId.
    """
    family = contact["familyId"]
    _attachment_contact(db, contact, message)
    identity = json.dumps([family, message["provider"], message["messageId"]], separators=(",", ":"))
    key = "inbound:" + hashlib.sha256(identity.encode()).hexdigest()
    content_sha = hashlib.sha256(data).hexdigest()
    hash_field = "photoSha256" if receipt_type == "photo" else "audioSha256"
    pending_status = receipt_type + "_pending"
    digest = hashlib.sha256(json.dumps({**message, hash_field: content_sha}, sort_keys=True,
                                       separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    media_id = hashlib.sha256((identity + "\n" + message["attachmentId"]).encode()).hexdigest()[:24]
    media_fields = {"photoIds": [media_id]} if receipt_type == "photo" else {"audioId": media_id}
    record = {"_id": key, "kind": "inbound", "receiptType": receipt_type, "familyId": family,
              "contactId": str(contact["_id"]), "personId": contact["personId"],
              "principal": contact.get("userId") or f"contact:{contact['_id']}",
              "handle": contact["handle"],
              "provider": message["provider"], "messageId": message["messageId"],
              "spaceId": message["spaceId"], "attachmentId": message["attachmentId"],
              "text": message.get("caption", ""), "contentHash": digest, hash_field: content_sha,
              "reservedMediaId": media_id, "status": pending_status, "uploadState": "reserved",
              "createdAt": datetime.now(timezone.utc)}
    if receipt_type == "voice":
        record["transcriptionStatus"] = "pending"
        record["audioMimeType"] = message["mimeType"]
    if contact.get("channel") == "imessage":
        record["contactProof"] = contact_proof(contact)
    duplicate = False
    try:
        db.channelMessages.insert_one(record)
    except DuplicateKeyError:
        duplicate = True
        previous = db.channelMessages.find_one({"_id": key, "familyId": family})
        if (not previous or previous.get("contentHash") != digest
                or previous.get("receiptType") != receipt_type or previous.get("contactId") != str(contact["_id"])):
            raise PhotoConflict("Message content changed") from None

    def complete_file():
        found = media.get_media(db, family, media_id)
        if found is not None and (found.kind != kind or found.content_type != message["mimeType"]
                                  or hashlib.sha256(found.data).hexdigest() != content_sha):
            raise PhotoConflict("Stored media does not match its reservation")
        return found

    if complete_file() is None:
        claimed = db.channelMessages.find_one_and_update(
            {"_id": key, "familyId": family, "contentHash": digest, "uploadState": "reserved"},
            {"$set": {"uploadState": "uploading"}})
        if claimed is None:
            raise PhotoPending("Media upload is incomplete or in progress")
        _attachment_contact(db, contact, message)
        try:
            media.put_media(db, family, data, content_type=message["mimeType"], kind=kind, media_id=media_id)
        except Exception:
            raise PhotoPending("Media upload outcome is uncertain") from None
        if complete_file() is None:
            raise PhotoPending("Media upload is incomplete")
    _attachment_contact(db, contact, message)
    try:
        result = db.channelMessages.update_one(
            {"_id": key, "familyId": family, "contentHash": digest, "status": pending_status},
            {"$set": {"status": "received", "uploadState": "complete", **media_fields}})
        if result.matched_count != 1:
            # A duplicate must not reset a receipt already consumed by a worker.
            completed = db.channelMessages.find_one({
                "_id": key, "familyId": family, "contentHash": digest,
                "uploadState": "complete", **media_fields})
            if completed is None:
                raise PhotoPending("Photo receipt disappeared")
    except Exception:
        raise PhotoPending("Photo receipt outcome is uncertain") from None
    return duplicate


def reserve_transcription(db, family_id: str, contact_id: str, receipt_id: str) -> dict:
    query = {"_id": receipt_id, "familyId": family_id, "contactId": contact_id,
             "receiptType": "voice", "status": "processing", "uploadState": "complete"}
    row = db.channelMessages.find_one_and_update(
        {**query, "transcriptionStatus": "pending"}, {"$set": {"transcriptionStatus": "transcribing"}},
        return_document=ReturnDocument.AFTER)
    if row is not None:
        return row
    row = db.channelMessages.find_one({**query, "transcriptionStatus": "complete"})
    if row is None:
        raise RuntimeError("Transcription is reserved or uncertain; reconcile before retry")
    return row


def complete_transcription(db, family_id: str, contact_id: str, receipt_id: str, text: str) -> None:
    result = db.channelMessages.update_one({"_id": receipt_id, "familyId": family_id,
        "contactId": contact_id, "receiptType": "voice", "status": "processing",
        "transcriptionStatus": "transcribing"}, {"$set": {"transcriptionStatus": "complete", "transcript": text}})
    if result.matched_count != 1:
        raise RuntimeError("Transcription reservation changed")


def uncertain_transcription(db, family_id: str, contact_id: str, receipt_id: str) -> None:
    db.channelMessages.update_one({"_id": receipt_id, "familyId": family_id,
        "contactId": contact_id, "receiptType": "voice", "transcriptionStatus": "transcribing"},
        {"$set": {"transcriptionStatus": "uncertain"}})
