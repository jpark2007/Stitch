"""Trusted contacts, preferences and recipient-proven pending enrollment."""

from datetime import datetime, timezone, timedelta
import hashlib
import hmac
import secrets
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError


def ensure_contact_indexes(db) -> None:
    db.contacts.create_index([("familyId", 1), ("handle", 1)], unique=True,
                             name="channel_contact_family_handle")
    db.contacts.create_index([("channel", 1), ("handle", 1)],
                             name="channel_contact_lookup")
    db.channelEnrollmentLimits.create_index("expiresAt", expireAfterSeconds=0,
                                             name="channel_enrollment_limit_expiry")
    db.contacts.create_index([("familyId", 1), ("personId", 1)], unique=True,
        partialFilterExpression={"enrollmentManaged": True}, name="channel_enrolled_person_unique")


def matching_contacts(db, provider: str, handle: str) -> list[dict]:
    """Identity discovery is deliberately cross-family; no family data is returned to callers.

    This is the one trusted-handle lookup needed before a family can be selected.
    It cannot authorize an action: resolve_contact also checks the person's family.
    """
    return list(db.contacts.find({"channel": provider, "handle": handle}))


def resolve_contact(db, provider: str, handle: str) -> dict:
    rows = matching_contacts(db, provider, handle)
    if not rows:
        raise LookupError("Unknown contact")
    if len(rows) != 1:
        raise ValueError("Family selection required")
    contact = rows[0]
    family_id, person_id = contact.get("familyId"), contact.get("personId")
    if not family_id or not person_id or not db.people.find_one(
        {"familyId": family_id, "_id": person_id}, {"_id": 1}
    ):
        raise LookupError("Unknown contact")
    if contact.get("status") != "active" or not contact.get("verifiedAt") or not contact.get("optInAt"):
        raise PermissionError("Contact is not opted in")
    return contact


def stop_contacts(db, provider: str, handle: str, rows: list[dict]) -> None:
    """STOP applies to every mapped family; it never needs family disambiguation."""
    for row in rows:
        db.contacts.update_one(
            {"_id": row["_id"], "familyId": row["familyId"], "channel": provider, "handle": handle},
            {"$set": {"status": "stopped", "stoppedAt": datetime.now(timezone.utc)},
             "$unset": {"enrollmentHash": "", "enrollmentExpiresAt": ""}},
        )


def worker_contact(db, family_id: str, contact_id: str) -> dict:
    """Check both the selected family and current handle ambiguity before sending."""
    original = db.contacts.find_one({"familyId": family_id, "_id": contact_id})
    if original is None:
        raise LookupError("Unknown contact")
    resolved = resolve_contact(db, original["channel"], original["handle"])
    if resolved["familyId"] != family_id or str(resolved["_id"]) != contact_id:
        raise PermissionError("Contact mapping changed")
    return resolved


def seed_terminal_contact(db, family_id: str) -> None:
    """Disposable Channels demo fixture, called only with its isolated database."""
    import schemas
    from .people import create_person
    now = datetime.now(timezone.utc)
    db.families.insert_one({"_id": family_id, "name": "Terminal demo"})
    create_person(db, family_id, schemas.NewPerson(name="Demo teller", born=1980),
                  added_by=None, person_id="terminal-person")
    db.contacts.insert_one({"_id": "terminal-contact", "familyId": family_id,
        "personId": "terminal-person", "handle": "terminal-user", "channel": "terminal",
        "status": "active", "verifiedAt": now, "optInAt": now})


def seed_book_demo_contact(db) -> None:
    """Bind terminal-user to the existing demo teller, only in disposable mongomock.

    This fixture is not enrollment: it refuses real databases, missing demo
    membership and mismatched people, and never updates an existing contact.
    """
    import mongomock
    from seed.demo import DEMO_ACCOUNTS, DEMO_FAMILY_ID
    from .families import get_member_role
    from .people import get_person
    from .users import get_user

    if not isinstance(db, mongomock.database.Database):
        raise ValueError("Book demo contacts require a disposable database")
    account = DEMO_ACCOUNTS[0]
    person = get_person(db, DEMO_FAMILY_ID, account.person_id)
    if (person is None or person.user_id != account.user_id
            or get_member_role(db, DEMO_FAMILY_ID, account.user_id) is None
            or get_user(db, account.user_id) is None):
        raise ValueError("Seed the matching demo family and account first")
    if matching_contacts(db, "terminal", "terminal-user"):
        raise ValueError("Terminal demo contact already exists")
    now = datetime.now(timezone.utc)
    db.contacts.insert_one({"_id": "terminal-contact", "familyId": DEMO_FAMILY_ID,
        "personId": account.person_id, "userId": account.user_id,
        "handle": "terminal-user", "channel": "terminal", "status": "active",
        "verifiedAt": now, "optInAt": now})


class ContactConflict(ValueError):
    """Generic conflict; never disclose another family's contact identity."""


def settings_person(db, family_id: str, person_id: str, actor_id: str) -> dict:
    """Authorize every settings operation against current membership and person."""
    from .families import get_member_role
    role = get_member_role(db, family_id, actor_id)
    person = db.people.find_one({"familyId": family_id, "_id": person_id})
    if role is None or person is None:
        raise LookupError("Not found")
    if role != "admin" and person.get("userId") != actor_id:
        raise PermissionError("Own contact or family admin required")
    return person


def list_settings_contacts(db, family_id: str, actor_id: str) -> list[dict]:
    from .families import get_member_role
    role = get_member_role(db, family_id, actor_id)
    if role is None:
        raise LookupError("Not found")
    people = list(db.people.find({"familyId": family_id, **({} if role == "admin" else {"userId": actor_id})}, {"_id": 1}))
    return list(db.contacts.find({"familyId": family_id, "personId": {"$in": [p["_id"] for p in people]}}))


def get_settings_contact(db, family_id: str, person_id: str, actor_id: str) -> dict | None:
    settings_person(db, family_id, person_id, actor_id)
    rows = list(db.contacts.find({"familyId": family_id, "personId": person_id}))
    if len(rows) > 1:
        raise ContactConflict("Contact needs reconciliation")
    return rows[0] if rows else None


def _enrollment_rate(db, actor_id: str, now: datetime) -> None:
    bucket = int(now.timestamp()) // 3600
    key = hashlib.sha256(f"{actor_id}:{bucket}".encode()).hexdigest()
    try:
        row = db.channelEnrollmentLimits.find_one_and_update(
            {"_id": key, "count": {"$lt": 5}},
            {"$inc": {"count": 1}, "$setOnInsert": {"expiresAt": now + timedelta(hours=2)}},
            upsert=True, return_document=ReturnDocument.AFTER)
    except DuplicateKeyError:
        raise ContactConflict("Enrollment unavailable") from None
    if row is None:
        raise ContactConflict("Enrollment unavailable")


def create_pending_enrollment(db, family_id: str, person_id: str, actor_id: str,
                              provider: str, handle: str, preferences: dict,
                              *, now: datetime | None = None) -> tuple[dict, str]:
    now = now or datetime.now(timezone.utc)
    person = settings_person(db, family_id, person_id, actor_id)
    _enrollment_rate(db, actor_id, now)
    existing = get_settings_contact(db, family_id, person_id, actor_id)
    matches = matching_contacts(db, provider, handle)
    if matches and (len(matches) != 1 or existing is None or matches[0]["_id"] != existing["_id"]):
        raise ContactConflict("Enrollment unavailable")
    if existing is not None:
        if (existing.get("status") not in {"pending", "stopped"} or existing.get("channel") != provider
                or existing.get("handle") != handle or existing.get("userId") != person.get("userId")
                or (existing.get("status") == "pending" and existing.get("enrollmentExpiresAt", now) > now)):
            raise ContactConflict("Enrollment unavailable")
    code = secrets.token_urlsafe(32)
    fields = {"familyId": family_id, "personId": person_id, "userId": person.get("userId"),
        "channel": provider, "handle": handle, "status": "pending", "enrollmentManaged": True, **preferences,
        "enrollmentHash": hashlib.sha256(code.encode()).hexdigest(),
        "enrollmentExpiresAt": now + timedelta(minutes=10), "enrollmentAttempts": 0,
        "enrollmentInitiator": actor_id, "enrollmentPersonUserId": person.get("userId"),
        "verifiedAt": None, "optInAt": None, "spaceId": None}
    if existing is None:
        # A deterministic new-contact key serializes creation for the same handle
        # across API workers/families without changing legacy contact indexes.
        key = "enrollment:" + hashlib.sha256(f"{provider}\n{handle}".encode()).hexdigest()
        try:
            db.contacts.insert_one({"_id": key, **fields})
        except DuplicateKeyError:
            raise ContactConflict("Enrollment unavailable") from None
        return {"_id": key, **fields}, code
    state = ({"enrollmentHash": existing.get("enrollmentHash"), "enrollmentExpiresAt": {"$lte": now}}
             if existing["status"] == "pending" else {"stoppedAt": existing.get("stoppedAt")})
    row = db.contacts.find_one_and_update(
        {"_id": existing["_id"], "familyId": family_id, "personId": person_id, "status": existing["status"],
         "channel": provider, "handle": handle, "userId": person.get("userId"), **state},
        {"$set": fields}, return_document=ReturnDocument.AFTER)
    if row is None:
        raise ContactConflict("Enrollment unavailable")
    return row, code


def update_contact_preferences(db, family_id: str, person_id: str, actor_id: str, changes: dict) -> dict:
    existing = get_settings_contact(db, family_id, person_id, actor_id)
    if existing is None:
        raise LookupError("Not found")
    row = db.contacts.find_one_and_update(
        {"_id": existing["_id"], "familyId": family_id, "personId": person_id},
        {"$set": changes}, return_document=ReturnDocument.AFTER)
    if row is None:
        raise LookupError("Not found")
    return row


def stop_settings_contact(db, family_id: str, person_id: str, actor_id: str) -> dict:
    existing = get_settings_contact(db, family_id, person_id, actor_id)
    if existing is None:
        raise LookupError("Not found")
    row = db.contacts.find_one_and_update(
        {"_id": existing["_id"], "familyId": family_id, "personId": person_id},
        {"$set": {"status": "stopped", "stoppedAt": datetime.now(timezone.utc)},
         "$unset": {"enrollmentHash": "", "enrollmentExpiresAt": ""}},
        return_document=ReturnDocument.AFTER)
    if row is None:
        raise LookupError("Not found")
    return row


def redeem_enrollment(db, provider: str, handle: str, code: str, space_id: str,
                      *, now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    rows = matching_contacts(db, provider, handle)
    if len(rows) != 1:
        return False
    row = rows[0]
    if row.get("status") != "pending" or row.get("enrollmentExpiresAt", now) <= now:
        return False
    query = {"_id": row["_id"], "familyId": row["familyId"], "personId": row["personId"],
        "channel": provider, "handle": handle, "status": "pending",
        "userId": row.get("userId"), "enrollmentInitiator": row.get("enrollmentInitiator"),
        "enrollmentPersonUserId": row.get("enrollmentPersonUserId"),
        "enrollmentHash": row.get("enrollmentHash"), "enrollmentExpiresAt": {"$gt": now},
        "enrollmentAttempts": {"$lt": 5}}
    expected = row.get("enrollmentHash", "")
    if not hmac.compare_digest(expected, hashlib.sha256(code.encode()).hexdigest()):
        db.contacts.update_one(query, {"$inc": {"enrollmentAttempts": 1}})
        return False
    from .users import get_user
    try:
        actor = row["enrollmentInitiator"]
        person = settings_person(db, row["familyId"], row["personId"], actor)
        if get_user(db, actor) is None or person.get("userId") != row.get("enrollmentPersonUserId"):
            return False
        if row.get("userId") != person.get("userId"):
            return False
    except (KeyError, LookupError, PermissionError):
        return False
    # Consume proof and activate in one compare-and-set. STOP/reissue/concurrent
    # redemption all change this predicate; none can revive a stopped contact.
    active = db.contacts.find_one_and_update(query,
        {"$set": {"status": "active", "verifiedAt": now, "optInAt": now, "spaceId": space_id},
         "$unset": {"enrollmentHash": "", "enrollmentExpiresAt": "", "enrollmentInitiator": "",
                    "enrollmentPersonUserId": "", "enrollmentAttempts": ""}},
        return_document=ReturnDocument.AFTER)
    return active is not None
