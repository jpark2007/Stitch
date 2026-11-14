"""Revoked session ids (ARCHITECTURE.md section 6: logout ends the session).

Sessions are stateless JWTs, so logging out records the token's `jti` here and
auth rejects any session whose jti is listed. Each record keeps `expiresAt` (the
token's own expiry); a TTL index on it (db/client.py) drops the record once the
token could no longer be used anyway.

Session ids are not family data, so these functions take no family_id.
"""

from __future__ import annotations

from datetime import UTC, datetime

from pymongo.database import Database

COLLECTION = "revoked_sessions"


def revoke_session(db: Database, jti: str, expires_at: datetime) -> None:
    """Mark session `jti` as ended until `expires_at`. Revoking twice is harmless."""
    if not isinstance(jti, str) or not jti:
        raise ValueError("jti is required")
    if not isinstance(expires_at, datetime):
        raise ValueError("expires_at must be a datetime")
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    db[COLLECTION].update_one(
        {"_id": jti},
        {"$set": {"expiresAt": expires_at}},
        upsert=True,
    )


def is_revoked(db: Database, jti: str) -> bool:
    """True if session `jti` was revoked."""
    if not isinstance(jti, str) or not jti:
        return False
    return db[COLLECTION].find_one({"_id": jti}, {"_id": 1}) is not None
