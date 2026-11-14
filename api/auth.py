"""Password hashing (argon2), session JWTs, and the current-user / family-member dependencies.

ARCHITECTURE.md section 4 (Access control):
- `current_user` decodes the session cookie (401 if absent, invalid, revoked by logout,
  or the user is gone).
- `session_user_or_none` is the same check without the 401, for GET /session.
- `family_member(fid)` checks membership. A non-member gets 404, never 403, so another
  family's ids are indistinguishable from ids that do not exist.
- `family_admin` additionally requires the admin role (403 for a plain member, who
  already knows the family exists).
"""

from __future__ import annotations

import logging
import os
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import jwt
from argon2 import PasswordHasher
from argon2.exceptions import Argon2Error, InvalidHashError
from fastapi import Cookie, Depends, HTTPException, Request, Response, status
from pymongo.database import Database

import schemas
from db import families as families_db
from db import sessions as sessions_db
from db import users as users_db
from db.client import get_db

log = logging.getLogger(__name__)

COOKIE_NAME = "stitch_session"
SESSION_DAYS = 30
JWT_ALGORITHM = "HS256"

_hasher = PasswordHasher()  # argon2id with the library's recommended parameters
_fallback_secret: str | None = None


# ---------------------------------------------------------------- passwords

def hash_password(pw: str) -> str:
    """An argon2id hash of pw (`$argon2id$...`)."""
    if not isinstance(pw, str):
        raise ValueError("password must be a string")
    return _hasher.hash(pw)


def verify_password(password_hash: str, pw: str) -> bool:
    """True only if pw matches. False on a mismatch or an invalid hash; never raises."""
    if not isinstance(password_hash, str) or not isinstance(pw, str):
        return False
    try:
        return _hasher.verify(password_hash, pw)
    except (Argon2Error, InvalidHashError, ValueError, TypeError):
        return False


# ---------------------------------------------------------------- session tokens

def _secret() -> str:
    """JWT_SECRET from the environment, else one random secret for this process."""
    configured = os.environ.get("JWT_SECRET")
    if configured:
        return configured
    global _fallback_secret
    if _fallback_secret is None:
        _fallback_secret = secrets.token_urlsafe(32)
        log.warning("JWT_SECRET is not set; using a random per-process secret. Sessions end on restart.")
    return _fallback_secret


def create_session_token(user_id: str, *, now: datetime | None = None) -> str:
    """A signed HS256 JWT with sub (user id), iat, exp (SESSION_DAYS later) and a random jti.

    The jti names this one session, so logout can revoke it without ending the user's
    other sessions (db/sessions.py).
    """
    if not isinstance(user_id, str) or not user_id:
        raise ValueError("user_id is required")
    issued = now or datetime.now(UTC)
    claims = {
        "sub": user_id,
        "iat": int(issued.timestamp()),
        "exp": int((issued + timedelta(days=SESSION_DAYS)).timestamp()),
        "jti": secrets.token_urlsafe(16),
    }
    return jwt.encode(claims, _secret(), algorithm=JWT_ALGORITHM)


def decode_session_claims(token: str) -> dict | None:
    """The verified claims of a valid token. None if it is expired, tampered, or malformed.

    A valid token has a non-empty string sub and jti, and int iat and exp. This only
    checks the signature and claims; revocation is checked against the database by
    the dependencies below.
    """
    if not isinstance(token, str) or not token:
        return None
    try:
        claims = jwt.decode(
            token,
            _secret(),
            algorithms=[JWT_ALGORITHM],
            options={"require": ["sub", "iat", "exp", "jti"]},
        )
    except jwt.PyJWTError:
        return None
    sub, jti, exp = claims.get("sub"), claims.get("jti"), claims.get("exp")
    if not (isinstance(sub, str) and sub and isinstance(jti, str) and jti):
        return None
    if not isinstance(exp, int) or isinstance(exp, bool):
        return None
    return claims


def decode_session_token(token: str) -> str | None:
    """The user id in a valid token. None if it is expired, tampered, or malformed."""
    claims = decode_session_claims(token)
    return claims["sub"] if claims else None


def session_expiry(claims: dict) -> datetime:
    """The exp claim of verified claims as an aware UTC datetime."""
    return datetime.fromtimestamp(claims["exp"], UTC)


# ---------------------------------------------------------------- cookies

_COOKIE_SECURE_ON = frozenset({"1", "true", "yes"})
_COOKIE_SECURE_OFF = frozenset({"0", "false", "no"})


def _cookie_secure(request: Request | None = None) -> bool:
    """Whether the session cookie gets the Secure flag.

    COOKIE_SECURE=1/true/yes forces it on and 0/false/no forces it off (any case).
    Unset or anything else: on exactly when the request came in over https, so a
    deployment behind TLS is Secure without extra configuration, while plain
    http://localhost development still works (Safari drops Secure cookies over http).
    Behind a proxy that terminates TLS, uvicorn sees https only when it trusts the
    proxy's X-Forwarded-Proto (--proxy-headers, --forwarded-allow-ips); otherwise set
    COOKIE_SECURE=1.
    """
    raw = (os.environ.get("COOKIE_SECURE") or "").strip().lower()
    if raw in _COOKIE_SECURE_ON:
        return True
    if raw in _COOKIE_SECURE_OFF:
        return False
    return request is not None and request.url.scheme == "https"


def set_session_cookie(response: Response, user_id: str, request: Request | None = None) -> None:
    """Start a session: httpOnly, SameSite=Lax, whole site, SESSION_DAYS long.

    Pass the request so an https request gets a Secure cookie (see _cookie_secure).
    """
    response.set_cookie(
        key=COOKIE_NAME,
        value=create_session_token(user_id),
        max_age=SESSION_DAYS * 86400,
        path="/",
        httponly=True,
        samesite="lax",
        secure=_cookie_secure(request),
    )


def clear_session_cookie(response: Response, request: Request | None = None) -> None:
    """End a session (expire the cookie with the same attributes it was set with)."""
    response.delete_cookie(
        key=COOKIE_NAME,
        path="/",
        httponly=True,
        samesite="lax",
        secure=_cookie_secure(request),
    )


# ---------------------------------------------------------------- dependencies

def _user_from_cookie(session: str | None, db: Database) -> schemas.User | None:
    claims = decode_session_claims(session) if session else None
    if claims is None:
        return None
    if sessions_db.is_revoked(db, claims["jti"]):  # logged out
        return None
    return users_db.get_user(db, claims["sub"])


def session_user_or_none(session: str | None, db: Database) -> schemas.User | None:
    """The user behind a session cookie value, or None when nobody is signed in by it.

    None for a missing, empty, malformed, tampered, expired or wrong-secret token, one
    without a jti, a revoked (logged-out) session, or a user who no longer exists. It never
    raises for a bad token. It does raise PyMongoError when the database cannot be read,
    so a caller can tell "signed out" apart from "could not check" (GET /session answers
    503 for the latter rather than reporting the user as signed out).

    The database is read only when the token itself is valid.
    """
    return _user_from_cookie(session, db)


def current_user(
    session: str | None = Cookie(default=None, alias=COOKIE_NAME),
    db: Database = Depends(get_db),
) -> schemas.User:
    """The signed-in user. 401 "Not signed in" if there is no valid session."""
    user = _user_from_cookie(session, db)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not signed in")
    return user


def optional_user(
    session: str | None = Cookie(default=None, alias=COOKIE_NAME),
    db: Database = Depends(get_db),
) -> schemas.User | None:
    """The signed-in user, or None."""
    return _user_from_cookie(session, db)


@dataclass(frozen=True)
class FamilyAccess:
    user: schemas.User
    family_id: str
    role: schemas.Role

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


def family_member(
    fid: str,
    user: schemas.User = Depends(current_user),
    db: Database = Depends(get_db),
) -> FamilyAccess:
    """The caller's access to family `fid` (path parameter). 404 if they are not a member."""
    role = families_db.get_member_role(db, fid, user.id)
    if role is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return FamilyAccess(user=user, family_id=fid, role=role)


def family_admin(access: FamilyAccess = Depends(family_member)) -> FamilyAccess:
    """Like family_member, but 403 unless the caller is the family's admin."""
    if not access.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admins only")
    return access
