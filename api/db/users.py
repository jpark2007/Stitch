"""User accounts (ARCHITECTURE.md section 5, `users`).

Users are not family data, so these functions take no family_id. The stored
`passwordHash` never leaves this module except through get_login().
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from bson import ObjectId
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError

import schemas
from db.families import stop_user_contacts

COLLECTION = "users"


class EmailTaken(Exception):
    """An account with this email already exists."""


def normalize_email(email: str) -> str:
    """Strip and lowercase. ValueError if empty or missing an '@'."""
    if not isinstance(email, str):
        raise ValueError("email must be a string")
    normalized = email.strip().lower()
    if not normalized or "@" not in normalized:
        raise ValueError("invalid email")
    return normalized


def _to_user(doc: dict[str, Any]) -> schemas.User:
    return schemas.User(
        id=doc["_id"],
        email=doc["email"],
        name=doc["name"],
        created_at=doc.get("createdAt"),
    )


def _utc_ms(value: datetime | None) -> datetime:
    """Aware UTC, truncated to the millisecond BSON stores, so what we return equals what we read back."""
    value = value or datetime.now(UTC)
    value = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    return value.replace(microsecond=value.microsecond // 1000 * 1000)


def create_user(
    db: Database,
    *,
    email: str,
    password_hash: str,
    name: str,
    user_id: str | None = None,
    now: datetime | None = None,
) -> schemas.User:
    """Store a new user. Raises EmailTaken if the email is already registered.

    ValueError when an explicit `user_id` already belongs to an account with another
    email (the id clashed, not the email, so EmailTaken would be the wrong answer).
    """
    email = normalize_email(email)
    name = name.strip() if isinstance(name, str) else ""
    if not name:
        raise ValueError("name is required")
    if not password_hash:
        raise ValueError("password_hash is required")
    if db[COLLECTION].find_one({"email": email}, {"_id": 1}) is not None:
        raise EmailTaken(email)
    doc = {
        "_id": user_id or str(ObjectId()),
        "email": email,
        "passwordHash": password_hash,
        "name": name,
        "createdAt": _utc_ms(now),
    }
    try:
        db[COLLECTION].insert_one(doc)
    except DuplicateKeyError as exc:
        # Lost a race with another signup for the same email, or the explicit user_id is
        # in use. Never read exc.details (mongomock leaves it None); look at the data.
        if user_id and db[COLLECTION].count_documents(
            {"_id": user_id, "email": {"$ne": email}}, limit=1
        ):
            raise ValueError(f"user id already exists: {user_id}") from exc
        raise EmailTaken(email) from exc
    return _to_user(doc)


def get_user(db: Database, user_id: str) -> schemas.User | None:
    if not isinstance(user_id, str) or not user_id:
        return None
    doc = db[COLLECTION].find_one({"_id": user_id}, {"passwordHash": 0})
    return _to_user(doc) if doc else None


def get_user_by_email(db: Database, email: str) -> schemas.User | None:
    try:
        email = normalize_email(email)
    except ValueError:
        return None
    doc = db[COLLECTION].find_one({"email": email}, {"passwordHash": 0})
    return _to_user(doc) if doc else None


def get_login(db: Database, email: str) -> tuple[schemas.User, str] | None:
    """The user and their stored password hash, for login only. None if no such account."""
    try:
        email = normalize_email(email)
    except ValueError:
        return None
    doc = db[COLLECTION].find_one({"email": email})
    if not doc or not doc.get("passwordHash"):
        return None
    return _to_user(doc), doc["passwordHash"]


def get_users(db: Database, user_ids: list[str]) -> list[schemas.User]:
    """The users that exist among user_ids, in input order (missing skipped, repeats dropped)."""
    ids = list(dict.fromkeys(uid for uid in user_ids if isinstance(uid, str)))
    if not ids:
        return []
    found = {
        doc["_id"]: _to_user(doc)
        for doc in db[COLLECTION].find({"_id": {"$in": ids}}, {"passwordHash": 0})
    }
    return [found[uid] for uid in ids if uid in found]


def delete_users(db: Database, *, emails: list[str], user_ids: list[str]) -> int:
    """Delete the accounts whose email (normalized as stored) or id is listed. Returns the count.

    Every Stitch Channels contact linked to a deleted account or a listed id (contacts.userId)
    is stopped, in every family, so texts from that phone are no longer accepted (see
    families.stop_user_contacts). A contact created for a person that was not linked to the
    account (no userId on the contact) is unaffected: it belongs to that person in that
    family, not to the account.

    Malformed emails and blank ids are ignored. With nothing left to match, nothing is
    deleted and 0 is returned, so an empty filter can never wipe the collection.
    """
    normalized: list[str] = []
    for email in emails:
        try:
            normalized.append(normalize_email(email))
        except ValueError:
            continue
    ids = [uid for uid in user_ids if isinstance(uid, str) and uid.strip()]
    clauses: list[dict[str, Any]] = []
    if normalized:
        clauses.append({"email": {"$in": list(dict.fromkeys(normalized))}})
    if ids:
        clauses.append({"_id": {"$in": list(dict.fromkeys(ids))}})
    if not clauses:
        return 0
    found = [doc["_id"] for doc in db[COLLECTION].find({"$or": clauses}, {"_id": 1})]
    deleted = db[COLLECTION].delete_many({"$or": clauses}).deleted_count
    # Texts from a deleted account's phone must stop reaching any family.
    stop_user_contacts(db, list(dict.fromkeys([*found, *ids])))
    return deleted
