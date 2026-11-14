"""Families, memberships, and invite codes (ARCHITECTURE.md section 4 steps 2-4, section 5).

Stored shape of a `families` document:

    {"_id": "...", "name": "The Maos",
     "members": [{"userId": "...", "role": "admin", "joinedAt": datetime}],
     "invites": [{"code": "k3J9...", "createdBy": "...", "expiresAt": datetime, "revoked": False}]}

Every function except `find_invite` and `find_active_invite` is keyed by family id, so an
id from another family simply does not match and returns None/False. Invite previews
happen before membership, which is why those two are keyed by the code alone.
"""

from __future__ import annotations

import secrets
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from bson import ObjectId
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError

import schemas
from db import media as media_db

INVITE_DAYS = 14
_ROLES = ("admin", "member")
_CODE_ATTEMPTS = 8
_CAS_ATTEMPTS = 8
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)

# Why an invite code that exists can or cannot be used. "revoked" wins over "expired" when
# both apply: the admin's explicit action is the more useful explanation.
InviteState = Literal["active", "expired", "revoked"]


class LastAdmin(Exception):
    """Raised instead of leaving a family with no admin."""


# ------------------------------------------------------------------------------ helpers

def _now(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(UTC)
    return _aware(now)


def _aware(dt: datetime) -> datetime:
    """Treat naive datetimes (a non tz_aware client) as UTC."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def _check_role(role: str) -> None:
    if role not in _ROLES:
        raise ValueError(f"role must be one of {_ROLES}, got {role!r}")


def _invite(doc: dict[str, Any]) -> schemas.Invite:
    return schemas.Invite(
        code=doc["code"],
        created_by=doc["createdBy"],
        expires_at=_aware(doc["expiresAt"]),
        revoked=bool(doc.get("revoked", False)),
    )


def _member(doc: dict[str, Any]) -> schemas.Member:
    joined = doc.get("joinedAt")
    return schemas.Member(
        user_id=doc["userId"],
        role=doc["role"],
        joined_at=_aware(joined) if joined is not None else None,
    )


def _family(doc: dict[str, Any]) -> schemas.Family:
    return schemas.Family(
        id=doc["_id"],
        name=doc["name"],
        members=[_member(m) for m in doc.get("members", [])],
        invites=[_invite(i) for i in doc.get("invites", [])],
    )


def _invite_state(invite: dict[str, Any], now: datetime) -> InviteState:
    """The stored invite's state at `now`. Expired means expiresAt <= now."""
    if invite.get("revoked", False):
        return "revoked"
    if _aware(invite["expiresAt"]) <= now:
        return "expired"
    return "active"


def _is_active(invite: dict[str, Any], now: datetime) -> bool:
    return _invite_state(invite, now) == "active"


def _other_admin(user_id: str) -> dict[str, Any]:
    """Query fragment: some member other than user_id is an admin."""
    return {"members": {"$elemMatch": {"userId": {"$ne": user_id}, "role": "admin"}}}


# ----------------------------------------------------------------------------- families

def create_family(
    db: Database,
    *,
    name: str,
    creator_user_id: str,
    family_id: str | None = None,
    now: datetime | None = None,
    members: Sequence[schemas.Member] = (),
) -> schemas.Family:
    """Create a family; the creator becomes its admin.

    `members` are written in the same insert, after the creator, so a family never exists
    without them (a seed that is interrupted cannot leave a half-joined family). A member
    without `joined_at` joins at `now`. ValueError on a blank name, on an explicit
    `family_id` that is already taken, on a bad role, and on a member listed twice (or the
    creator listed again).
    """
    if not isinstance(name, str) or not name.strip():
        raise ValueError("family name must not be blank")
    if not creator_user_id:
        raise ValueError("creator_user_id is required")
    created = _now(now)
    member_docs = [{"userId": creator_user_id, "role": "admin", "joinedAt": created}]
    for member in members:
        _check_role(member.role)
        if not member.user_id:
            raise ValueError("member user_id is required")
        if any(m["userId"] == member.user_id for m in member_docs):
            raise ValueError(f"member listed twice: {member.user_id}")
        joined = _aware(member.joined_at) if member.joined_at is not None else created
        member_docs.append({"userId": member.user_id, "role": member.role, "joinedAt": joined})
    doc = {
        "_id": family_id or str(ObjectId()),
        "name": name.strip(),
        "members": member_docs,
        "invites": [],
    }
    try:
        db.families.insert_one(doc)
    except DuplicateKeyError as exc:
        raise ValueError(f"family id already exists: {doc['_id']}") from exc
    return _family(doc)


def get_family(db: Database, family_id: str) -> schemas.Family | None:
    doc = db.families.find_one({"_id": family_id})
    return _family(doc) if doc else None


# Every collection whose documents carry a `familyId` and belong to exactly one family.
FAMILY_SCOPED = ("people", "memories", "questions", "props", "sets", "traditions")


def purge_family(db: Database, family_id: str) -> None:
    """Delete a family and everything it owns: its document, its people, memories,
    questions, props, sets and traditions, and its media. Nothing of any other family is touched.

    Accounts are not family data and stay (see `users.delete_users`). ValueError on a
    blank family_id, so a bad argument can never widen a filter.
    """
    if not isinstance(family_id, str) or not family_id.strip():
        raise ValueError("family_id is required")
    db.families.delete_one({"_id": family_id})
    for collection in FAMILY_SCOPED:
        db[collection].delete_many({"familyId": family_id})
    media_db.delete_family_media(db, family_id)


# -------------------------------------------------------------------------- memberships

def get_member_role(db: Database, family_id: str, user_id: str) -> schemas.Role | None:
    """The user's role in the family, or None when the family is missing or they are not a member."""
    doc = db.families.find_one(
        {"_id": family_id, "members.userId": user_id},
        {"members": {"$elemMatch": {"userId": user_id}}},
    )
    if not doc or not doc.get("members"):
        return None
    return doc["members"][0]["role"]


def list_family_summaries(db: Database, user_id: str) -> list[schemas.FamilySummary]:
    """Every family the user belongs to, ordered by when they joined it."""
    rows: list[tuple[datetime, schemas.FamilySummary]] = []
    for doc in db.families.find({"members.userId": user_id}, {"name": 1, "members": 1}):
        member = next((m for m in doc.get("members", []) if m["userId"] == user_id), None)
        if member is None:
            continue
        person = db.people.find_one({"familyId": doc["_id"], "userId": user_id}, {"_id": 1})
        summary = schemas.FamilySummary(
            id=doc["_id"],
            name=doc["name"],
            role=member["role"],
            person_id=person["_id"] if person else None,
        )
        joined = member.get("joinedAt")
        rows.append((_aware(joined) if joined is not None else _EPOCH, summary))
    rows.sort(key=lambda row: row[0])
    return [summary for _, summary in rows]


def add_member(
    db: Database,
    family_id: str,
    user_id: str,
    role: schemas.Role = "member",
    *,
    now: datetime | None = None,
) -> bool:
    """Add the user to the family. Idempotent, and never downgrades an existing role.

    Re-adding an existing member as "admin" promotes them; re-adding as "member" is a no-op.
    Returns False if the family does not exist.
    """
    _check_role(role)
    if not user_id:
        raise ValueError("user_id is required")
    pushed = db.families.update_one(
        {"_id": family_id, "members.userId": {"$ne": user_id}},
        {"$push": {"members": {"userId": user_id, "role": role, "joinedAt": _now(now)}}},
    )
    if pushed.matched_count:
        return True
    # Either the family is missing or the user is already a member.
    if role == "admin":
        promoted = db.families.update_one(
            {"_id": family_id, "members.userId": user_id},
            {"$set": {"members.$.role": "admin"}},
        )
        return promoted.matched_count == 1
    return db.families.count_documents({"_id": family_id, "members.userId": user_id}, limit=1) == 1


def set_member_role(db: Database, family_id: str, user_id: str, role: schemas.Role) -> bool:
    """Change a member's role. False if not a member. LastAdmin when demoting the only admin."""
    _check_role(role)
    if role == "admin":
        result = db.families.update_one(
            {"_id": family_id, "members.userId": user_id},
            {"$set": {"members.$.role": "admin"}},
        )
        return result.matched_count == 1

    # Demotion must keep another admin. The positional operator is undefined with two
    # conditions on `members`, and mongomock lacks arrayFilters, so compare-and-swap the
    # members array: the write only lands if nobody changed the array since we read it.
    for _ in range(_CAS_ATTEMPTS):
        doc = db.families.find_one({"_id": family_id, "members.userId": user_id}, {"members": 1})
        if doc is None:
            return False
        members = doc["members"]
        target = next(m for m in members if m["userId"] == user_id)
        if target["role"] == "member":
            return True
        if not any(m["role"] == "admin" and m["userId"] != user_id for m in members):
            raise LastAdmin(f"{user_id} is the only admin of {family_id}")
        updated = [{**m, "role": "member"} if m["userId"] == user_id else m for m in members]
        result = db.families.update_one(
            {"_id": family_id, "members": members}, {"$set": {"members": updated}}
        )
        if result.matched_count == 1:
            return True
    raise RuntimeError(f"could not update members of {family_id}: concurrent writes")


def remove_member(db: Database, family_id: str, user_id: str) -> bool:
    """Remove the user and unclaim their person in this family only.

    False if the user is not a member. LastAdmin when removing the only admin.
    """
    result = db.families.update_one(
        {
            "_id": family_id,
            "members.userId": user_id,
            "$or": [
                {"members": {"$elemMatch": {"userId": user_id, "role": {"$ne": "admin"}}}},
                _other_admin(user_id),
            ],
        },
        {"$pull": {"members": {"userId": user_id}}},
    )
    if result.matched_count == 0:
        if db.families.count_documents({"_id": family_id, "members.userId": user_id}, limit=1):
            raise LastAdmin(f"{user_id} is the only admin of {family_id}")
        return False
    db.people.update_many({"familyId": family_id, "userId": user_id}, {"$set": {"userId": None}})
    return True


# ------------------------------------------------------------------------------ invites

def create_invite(
    db: Database,
    family_id: str,
    created_by: str,
    *,
    now: datetime | None = None,
    days: int = INVITE_DAYS,
) -> schemas.Invite | None:
    """A fresh random invite code valid for `days` days. None if the family does not exist."""
    if days <= 0:
        raise ValueError("days must be positive")
    expires_at = _now(now) + timedelta(days=days)
    for _ in range(_CODE_ATTEMPTS):
        code = secrets.token_urlsafe(12)
        if db.families.count_documents({"invites.code": code}, limit=1):
            continue  # collision with an existing code anywhere; draw again
        invite = {"code": code, "createdBy": created_by, "expiresAt": expires_at, "revoked": False}
        try:
            result = db.families.update_one({"_id": family_id}, {"$push": {"invites": invite}})
        except DuplicateKeyError:
            continue  # lost a race against the unique index on invites.code
        if result.matched_count == 0:
            return None
        return _invite(invite)
    raise RuntimeError("could not generate a unique invite code")


def revoke_invite(db: Database, family_id: str, code: str) -> bool:
    """Revoke a code. False if the code does not belong to that family."""
    result = db.families.update_one(
        {"_id": family_id, "invites.code": code},
        {"$set": {"invites.$.revoked": True}},
    )
    return result.matched_count == 1


def list_invites(
    db: Database,
    family_id: str,
    *,
    active_only: bool = True,
    now: datetime | None = None,
) -> list[schemas.Invite]:
    doc = db.families.find_one({"_id": family_id}, {"invites": 1})
    if not doc:
        return []
    current = _now(now)
    return [
        _invite(i) for i in doc.get("invites", []) if not active_only or _is_active(i, current)
    ]


def find_invite(
    db: Database, code: str, *, now: datetime | None = None
) -> tuple[schemas.Family, schemas.Invite, InviteState] | None:
    """Look up an invite by code alone, whatever its state (previews happen before membership).

    None if the code is not a non-empty string or is unknown. Otherwise the family, the
    invite and its state at `now`: "revoked" if an admin revoked it (even when it has also
    expired), else "expired" if expiresAt <= now, else "active".
    """
    if not isinstance(code, str) or not code:
        return None
    doc = db.families.find_one({"invites.code": code})
    if not doc:
        return None
    invite = next((i for i in doc.get("invites", []) if i["code"] == code), None)
    if invite is None:
        return None
    return _family(doc), _invite(invite), _invite_state(invite, _now(now))


def find_active_invite(
    db: Database, code: str, *, now: datetime | None = None
) -> tuple[schemas.Family, schemas.Invite] | None:
    """Look up a usable invite by code alone (previews happen before membership).

    None if the code is unknown, revoked, or expired (expiresAt <= now).
    """
    found = find_invite(db, code, now=now)
    if found is None or found[2] != "active":
        return None
    return found[0], found[1]
