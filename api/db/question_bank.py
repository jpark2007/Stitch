"""Public global neutral question bank; family-scoped notification reservations."""

import hashlib
import json

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

PROMPTS = (
    "What is a small moment from childhood that you still remember?",
    "What did a typical weekend look like when you were young?",
    "What meal reminds you of home?",
    "Who taught you something you still use today?",
    "What was a favorite place to spend time growing up?",
    "What game did you enjoy playing with others?",
    "What do you remember about your first day at a new school?",
    "What is a journey you remember fondly?",
    "What family saying do you remember hearing?",
    "What music takes you back to a particular time?",
    "What was a favorite book or story when you were young?",
    "What did your neighborhood look like?",
    "What is something you made with your own hands?",
    "What do you remember about learning to cook?",
    "What was a celebration you enjoyed?",
    "What ordinary object has a story behind it?",
    "What do you remember about a family photograph?",
    "What was a small kindness someone showed you?",
    "What did you enjoy doing outdoors?",
    "What is a funny misunderstanding you remember?",
    "What did you collect or save when you were young?",
    "What was a memorable visit with a relative or friend?",
    "What do you remember about your first job or volunteer role?",
    "What skill did you enjoy learning?",
    "What tradition did you look forward to?",
    "What season brings back a particular memory?",
    "What did you like about a home you lived in?",
    "What is a time you tried something new?",
    "What advice have you found helpful?",
    "What was a favorite way to spend a quiet afternoon?",
    "What do you remember about a pet or animal you knew?",
    "What place would you like your family to know about?",
    "What is a moment when someone made you laugh?",
    "What did a regular school or work day look like?",
    "What is something you enjoyed teaching someone else?",
    "What is a local custom you remember?",
    "What did you do on a day off with friends or family?",
    "What is a memory connected to a particular smell?",
    "What is a small achievement you felt proud of?",
    "What story would you like a younger relative to hear?",
)


def ensure_question_indexes(db):
    db.questionBank.create_index("questionKey", unique=True)
    db.notifications.create_index([("familyId", 1), ("contactId", 1), ("questionKey", 1)], unique=True)
    db.notifications.create_index([("familyId", 1), ("contactId", 1)], unique=True,
                                  partialFilterExpression={"active": True}, name="one_active_prompt")


def discover_contacts(db, limit=5):
    """Trusted bounded discovery exposes IDs only; content reads below require family."""
    marker = db.channelMessages.find_one({"_id": "prompt-scan"}) or {}
    query = {"status": "active", "verifiedAt": {"$ne": None}, "optInAt": {"$ne": None},
             "spaceKind": "direct"}
    projection = {"_id": 1, "familyId": 1}
    rows = list(db.contacts.find({**query, "_id": {"$gt": marker.get("cursor", "")}}, projection)
                .sort("_id", 1).limit(limit))
    if not rows:
        rows = list(db.contacts.find(query, projection).sort("_id", 1).limit(limit))
    if rows:
        db.channelMessages.update_one({"_id": "prompt-scan"}, {"$set": {
            "kind": "promptScan", "cursor": rows[-1]["_id"]}}, upsert=True)
    return [(r["familyId"], str(r["_id"])) for r in rows]


def notifications(db, family_id, contact_id):
    return list(db.notifications.find({"familyId": family_id, "contactId": contact_id})
                .sort("createdAt", -1).limit(1001))


def next_bank_question(db, asked):
    # Intentional global-data exception: these fixed public strings contain no
    # family content. Personal questions remain in db.memories.open_questions.
    for index, text in enumerate(PROMPTS):
        key = f"bank:{index + 1:02d}"
        db.questionBank.update_one({"questionKey": key}, {"$setOnInsert": {
            "_id": key, "questionKey": key, "text": text}}, upsert=True)
    return db.questionBank.find_one({"questionKey": {"$nin": list(asked)}},
                                   sort=[("questionKey", 1)])


def reserve_notification(db, family_id, contact_id, question, destination, now):
    key = hashlib.sha256(json.dumps([family_id, contact_id, question["questionKey"]]).encode()).hexdigest()
    content = {k: question[k] for k in ("questionKey", "questionId", "text") if k in question}
    record = {"_id": key, "familyId": family_id, "contactId": contact_id, **content,
              "destination": destination, "status": "reserved", "active": True, "createdAt": now}
    # Locking is also enforced by the worker. The partial unique index protects
    # the unanswered-chain invariant if a different producer is later added.
    if db.notifications.find_one({"familyId": family_id, "contactId": contact_id, "active": True}):
        return None
    try:
        db.notifications.insert_one(record)
    except DuplicateKeyError:
        return None
    return record


def update_notification(db, family_id, contact_id, notification_id, expected, fields):
    result = db.notifications.find_one_and_update({"_id": notification_id, "familyId": family_id,
        "contactId": contact_id, "status": expected}, {"$set": fields}, return_document=ReturnDocument.AFTER)
    if result is None:
        raise RuntimeError("Notification reservation changed")
    return result


def answer_prompt(db, family_id, contact_id, receipt_id, now):
    db.notifications.update_one({"familyId": family_id, "contactId": contact_id, "status": "sent", "active": True},
        {"$set": {"status": "answered", "active": False, "answeredAt": now, "answerReceiptId": receipt_id}})
