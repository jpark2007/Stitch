"""Account routes: sign up, log in, log out, current user (ARCHITECTURE.md sections 4 and 6).

- POST /auth/signup, POST /auth/login, POST /auth/logout
- GET /me: the signed-in user and their families; 401 when signed out.
- GET /session: the same data, but always 200 with `user` null when signed out (or the
  cookie is invalid), so a signed-out page view does not log a 401. It reads the database
  only when the cookie is a valid token, never sets or clears a cookie, and answers 503
  (not "signed out") when the database cannot be read. Both are `Cache-Control: no-store`.

Responses are built only from schemas.User, which has no password field. The stored
password hash is read through users_db.get_login() and never leaves login().

Signup caps the trimmed display name at MAX_DISPLAY_NAME_CHARS and the normalized email
at MAX_EMAIL_CHARS (422 with a sentence naming the limit). Login has no caps of its own:
any email it cannot find, long or not, is the same 401 after the same argon2 work.
"""

from __future__ import annotations

import logging
import secrets
import threading
from collections.abc import Callable

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response, status
from pymongo.database import Database
from pymongo.errors import PyMongoError

import auth
import schemas
from db import families as families_db
from db import sessions as sessions_db
from db import users as users_db
from db.client import get_db

log = logging.getLogger(__name__)

router = APIRouter(tags=["auth"])

INVALID_LOGIN = "Invalid email or password"
SESSION_UNAVAILABLE = "Could not check your session right now"
NO_STORE = {"Cache-Control": "no-store"}  # the answer depends on the cookie
HTTP_422 = 422  # starlette renamed the constant; the number is the contract

# Signup limits, checked after trimming (schemas.SignupRequest itself is unbounded).
# 254 is the longest address SMTP can deliver to (RFC 5321 path limit minus the brackets).
MAX_DISPLAY_NAME_CHARS = 100
MAX_EMAIL_CHARS = 254

_dummy_hash: str | None = None
_dummy_lock = threading.Lock()


def _dummy_password_hash() -> str:
    """An argon2 hash of a random value, computed once on first use.

    Login verifies against it when the email is unknown, so a miss costs the same
    argon2 work as a wrong password and response timing does not reveal accounts.
    """
    global _dummy_hash
    if _dummy_hash is None:
        with _dummy_lock:
            if _dummy_hash is None:
                _dummy_hash = auth.hash_password(secrets.token_urlsafe(32))
    return _dummy_hash


def warm_login_hash() -> None:
    """Compute the dummy hash ahead of the first login (main.py calls this at startup).

    Otherwise the first login with an unknown email in a process does two argon2 runs
    (hash, then verify) where a wrong password does one, which tells the two apart.
    """
    _dummy_password_hash()


def _valid_email_shape(email: str) -> bool:
    """A normalized email with something on both sides of its last '@' and no whitespace.

    users_db.normalize_email only asks for an '@', so "@", "ana@" and "a b@x.com" got
    through as accounts nobody could ever be emailed at.
    """
    local, _, domain = email.rpartition("@")
    return bool(local) and bool(domain) and not any(ch.isspace() for ch in email)


def _me(db: Database, user: schemas.User) -> schemas.MeResponse:
    return schemas.MeResponse(user=user, families=families_db.list_family_summaries(db, user.id))


@router.post(
    "/auth/signup",
    status_code=status.HTTP_201_CREATED,
    response_model=schemas.MeResponse,
)
def signup(
    body: schemas.SignupRequest,
    request: Request,
    response: Response,
    db: Database = Depends(get_db),
) -> schemas.MeResponse:
    """Create an account and start a session. A new account belongs to no families yet."""
    try:
        email = users_db.normalize_email(body.email)
    except ValueError:
        raise HTTPException(HTTP_422, detail="Enter a valid email") from None
    if len(email) > MAX_EMAIL_CHARS:
        raise HTTPException(HTTP_422, detail=f"Email must be at most {MAX_EMAIL_CHARS} characters")
    if not _valid_email_shape(email):
        raise HTTPException(HTTP_422, detail="Enter a valid email")
    name = body.name.strip()
    if not name:
        raise HTTPException(HTTP_422, detail="Enter your name")
    if len(name) > MAX_DISPLAY_NAME_CHARS:
        raise HTTPException(HTTP_422, detail=f"Your name must be at most {MAX_DISPLAY_NAME_CHARS} characters")
    try:
        user = users_db.create_user(
            db,
            email=email,
            password_hash=auth.hash_password(body.password),
            name=name,
        )
    except users_db.EmailTaken:
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail="An account with this email already exists"
        ) from None
    auth.set_session_cookie(response, user.id, request)
    return schemas.MeResponse(user=user, families=[])


@router.post("/auth/login", response_model=schemas.MeResponse)
def login(
    body: schemas.LoginRequest,
    request: Request,
    response: Response,
    db: Database = Depends(get_db),
) -> schemas.MeResponse:
    """Start a session. Unknown email, malformed email and wrong password all get the same 401."""
    found = users_db.get_login(db, body.email)  # None for a malformed or unknown email
    if found is None:
        auth.verify_password(_dummy_password_hash(), body.password)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail=INVALID_LOGIN)
    user, password_hash = found
    if not auth.verify_password(password_hash, body.password):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail=INVALID_LOGIN)
    auth.set_session_cookie(response, user.id, request)
    return _me(db, user)


@router.post(
    "/auth/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
def logout(
    request: Request,
    session: str | None = Cookie(default=None, alias=auth.COOKIE_NAME),
    db: Database = Depends(get_db),
) -> Response:
    """End the session. Needs no session, and always clears the cookie.

    A valid session cookie is also revoked server-side, so a copy of it stops working.
    Other sessions of the same user are untouched. An absent or invalid cookie revokes
    nothing and is not an error.
    """
    claims = auth.decode_session_claims(session) if session else None
    if claims is not None:
        try:
            sessions_db.revoke_session(db, claims["jti"], auth.session_expiry(claims))
        except PyMongoError:
            # Still clear the cookie; the browser's copy is gone even if the record is not.
            log.exception("Could not record the revoked session")
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    auth.clear_session_cookie(response, request)
    return response


@router.get("/me", response_model=schemas.MeResponse)
def me(
    response: Response,
    user: schemas.User = Depends(auth.current_user),
    db: Database = Depends(get_db),
) -> schemas.MeResponse:
    """The signed-in user and the families they belong to. 401 when signed out."""
    response.headers.update(NO_STORE)
    return _me(db, user)


def _db_opener(request: Request) -> Callable[[], Database]:
    """get_db, not yet called (honouring app.dependency_overrides like Depends(get_db) does).

    GET /session calls it only once the cookie decodes, so a signed-out page view never
    touches the database and still works while MongoDB is down.
    """
    return request.app.dependency_overrides.get(get_db, get_db)


@router.get("/session", response_model=schemas.SessionResponse)
def session_probe(
    response: Response,
    session: str | None = Cookie(default=None, alias=auth.COOKIE_NAME),
    open_db: Callable[[], Database] = Depends(_db_opener),
) -> schemas.SessionResponse:
    """Who is signed in. Always 200: user null and no families when nobody is.

    Signed in, the body is exactly GET /me's. No cookie, an empty or invalid one, a revoked
    session or a deleted user are all "signed out". The cookie is never set or cleared here:
    clearing an invalid one could race a login in another tab and wipe its fresh cookie.

    A valid token whose session or user cannot be read (PyMongoError) is a 503, not
    "signed out", which the web app would act on by sending the user to the login page.
    """
    response.headers.update(NO_STORE)
    if auth.decode_session_claims(session) is None:  # no database needed to say "signed out"
        return schemas.SessionResponse()
    try:
        db = open_db()
        user = auth.session_user_or_none(session, db)
        if user is None:
            return schemas.SessionResponse()
        me = _me(db, user)
    except PyMongoError:
        log.exception("Could not read the session or its user")  # never logs the token
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, detail=SESSION_UNAVAILABLE, headers=NO_STORE
        ) from None
    return schemas.SessionResponse(user=me.user, families=me.families)
