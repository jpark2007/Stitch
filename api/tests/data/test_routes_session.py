"""GET /session: the always-200 session probe (routes/auth.py), on the real app over mongomock.

Signed out (no cookie, an empty, malformed, tampered, expired, wrong-secret or jti-less
token, a revoked session, a deleted user) it is 200 {"user": null, "families": []}, with no
Set-Cookie. Signed in it is byte-for-byte GET /me. A cookie that does not decode never
touches the database; a valid one whose session or user cannot be read is a 503, never
"signed out". Every answer is Cache-Control: no-store.

The app's lifespan is never entered (TestClient without `with`); the db is set with
`db.client.set_db(mdb)`. Expired tokens are built relative to now, never a fixed date.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from fastapi.testclient import TestClient
from pymongo.errors import PyMongoError, ServerSelectionTimeoutError

import auth
import main
import schemas
from db import client as db_client
from db import families as families_db
from db import sessions as sessions_db
from db import users as users_db
from routes import auth as auth_routes

PASSWORD = "correct horse battery"
SIGNED_OUT = {"user": None, "families": []}


@pytest.fixture
def app(mdb):
    db_client.ensure_indexes(mdb)
    db_client.set_db(mdb)
    try:
        yield main.create_app()
    finally:
        db_client.set_db(None)


def _client(app) -> TestClient:
    """A client with an empty cookie jar; cookies are sent explicitly per request."""
    return TestClient(app)


def _signup(app, email: str = "ana@example.com", name: str = "Ana") -> tuple[str, dict]:
    """A new account: (its session cookie value, the signup body)."""
    tc = _client(app)
    res = tc.post("/auth/signup", json={"email": email, "password": PASSWORD, "name": name})
    assert res.status_code == 201, res.text
    return tc.cookies.get(auth.COOKIE_NAME), res.json()


def _get(app, path: str, cookie: str | None):
    """GET path with exactly this cookie value (None sends no Cookie header at all)."""
    headers = {} if cookie is None else {"Cookie": f"{auth.COOKIE_NAME}={cookie}"}
    return _client(app).get(path, headers=headers)


def _set_cookies(res) -> list[str]:
    return [v for k, v in res.headers.multi_items() if k.lower() == "set-cookie"]


def _assert_signed_out(res) -> None:
    assert res.status_code == 200, res.text
    assert res.json() == SIGNED_OUT
    assert _set_cookies(res) == []
    assert res.headers["cache-control"] == "no-store"
    assert schemas.SessionResponse.model_validate(res.json()) == schemas.SessionResponse()


# ------------------------------------------------------------------------------ bad tokens


def _expired(user_id: str) -> str:
    return auth.create_session_token(user_id, now=datetime.now(UTC) - timedelta(days=auth.SESSION_DAYS + 1))


def _claims(user_id: str) -> dict:
    now = datetime.now(UTC)
    return {
        "sub": user_id,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(days=1)).timestamp()),
        "jti": "jti-for-a-test",
    }


def _wrong_secret(user_id: str) -> str:
    return jwt.encode(_claims(user_id), "not-the-server-secret-0123456789abcdef", algorithm=auth.JWT_ALGORITHM)


def _no_jti(user_id: str) -> str:
    claims = _claims(user_id)
    del claims["jti"]
    return jwt.encode(claims, os.environ["JWT_SECRET"], algorithm=auth.JWT_ALGORITHM)  # set by conftest


def _tampered(user_id: str) -> str:
    """A valid token for user_id with its payload swapped for another user's: bad signature."""
    header, _, signature = auth.create_session_token(user_id).split(".")
    _, other_payload, _ = auth.create_session_token("someone-else").split(".")
    return ".".join([header, other_payload, signature])


# Tokens that never decode: no database is needed to call them signed out.
UNDECODABLE = {
    "empty": lambda uid: "",
    "malformed": lambda uid: "not-a-jwt",
    "tampered": _tampered,
    "expired": _expired,
    "wrong-secret": _wrong_secret,
    "no-jti": _no_jti,
}


def test_no_cookie_is_signed_out(app):
    _assert_signed_out(_get(app, "/session", None))


@pytest.mark.parametrize("kind", sorted(UNDECODABLE))
def test_an_undecodable_cookie_is_signed_out(app, kind):
    _, body = _signup(app)
    token = UNDECODABLE[kind](body["user"]["id"])
    assert auth.decode_session_claims(token) is None  # the case really is a bad token

    _assert_signed_out(_get(app, "/session", token))


def test_a_revoked_session_is_signed_out(app):
    cookie, _ = _signup(app)
    assert _get(app, "/session", cookie).json()["user"] is not None

    res = _client(app).post("/auth/logout", headers={"Cookie": f"{auth.COOKIE_NAME}={cookie}"})
    assert res.status_code == 204

    _assert_signed_out(_get(app, "/session", cookie))
    assert _get(app, "/me", cookie).status_code == 401  # the two agree on "signed out"


def test_a_deleted_user_is_signed_out(app, mdb):
    cookie, body = _signup(app)
    mdb.users.delete_one({"_id": body["user"]["id"]})

    _assert_signed_out(_get(app, "/session", cookie))


def test_a_valid_token_for_a_user_that_never_existed_is_signed_out(app):
    _assert_signed_out(_get(app, "/session", auth.create_session_token("no-such-user")))


def test_session_never_touches_the_session_or_cookie_of_a_signed_out_caller(app, mdb):
    """Not even an invalid cookie is cleared, and nothing is revoked or written."""
    cookie, _ = _signup(app)
    before = {name: list(mdb[name].find()) for name in mdb.list_collection_names()}

    for token in (None, "", "not-a-jwt", _expired("u1"), cookie):
        res = _get(app, "/session", token)
        assert res.status_code == 200 and _set_cookies(res) == []

    assert {name: list(mdb[name].find()) for name in mdb.list_collection_names()} == before


# ------------------------------------------------------------------------------ signed in


def _create_family(app, cookie: str, name: str) -> str:
    res = _client(app).post(
        "/families",
        json={"name": name, "person": {"name": "Ana"}},
        headers={"Cookie": f"{auth.COOKIE_NAME}={cookie}"},
    )
    assert res.status_code == 201, res.text
    return res.json()["family"]["id"]


def _assert_same_as_me(app, cookie: str):
    session = _get(app, "/session", cookie)
    me = _get(app, "/me", cookie)
    assert session.status_code == 200 and me.status_code == 200, (session.text, me.text)
    assert session.content == me.content  # byte-for-byte
    assert _set_cookies(session) == []
    assert session.headers["cache-control"] == "no-store"
    parsed = schemas.SessionResponse.model_validate(session.json())
    assert parsed.user is not None
    return session.json()


def test_signed_in_admin_of_two_families_matches_me(app):
    cookie, body = _signup(app)
    fids = {_create_family(app, cookie, "Park"), _create_family(app, cookie, "Lee")}

    got = _assert_same_as_me(app, cookie)

    assert got["user"] == body["user"]
    assert {f["id"] for f in got["families"]} == fids
    assert {f["role"] for f in got["families"]} == {"admin"}


def test_signed_in_with_no_families_matches_me(app):
    cookie, body = _signup(app)

    got = _assert_same_as_me(app, cookie)

    assert got == {"user": body["user"], "families": []}


def test_a_session_survives_another_users_logout(app):
    ana, _ = _signup(app)
    ben, _ = _signup(app, "ben@example.com", "Ben")
    _client(app).post("/auth/logout", headers={"Cookie": f"{auth.COOKIE_NAME}={ben}"})

    assert _get(app, "/session", ana).json()["user"]["email"] == "ana@example.com"
    _assert_signed_out(_get(app, "/session", ben))


def test_me_is_no_store_too(app):
    cookie, _ = _signup(app)
    assert _get(app, "/me", cookie).headers["cache-control"] == "no-store"


# ------------------------------------------------------------------------------ database down


def _boom(*_args, **_kwargs):
    raise ServerSelectionTimeoutError("mongodb is down")


@pytest.fixture
def db_down(app, monkeypatch):
    """Every way of reaching the database raises, and each attempt is counted."""
    attempts: list[str] = []

    def opener():
        attempts.append("get_db")
        _boom()

    app.dependency_overrides[db_client.get_db] = opener
    monkeypatch.setattr(db_client, "get_client", lambda: (attempts.append("get_client"), _boom())[1])
    return attempts


@pytest.mark.parametrize("kind", ["none", *sorted(UNDECODABLE)])
def test_a_cookie_that_does_not_decode_never_touches_the_database(app, db_down, kind):
    token = None if kind == "none" else UNDECODABLE[kind]("u1")

    _assert_signed_out(_get(app, "/session", token))

    assert db_down == []


def test_a_valid_cookie_with_the_database_unreachable_is_503(app, db_down, caplog):
    token = auth.create_session_token("u1")

    with caplog.at_level(logging.ERROR, logger=auth_routes.log.name):
        res = _get(app, "/session", token)

    assert db_down == ["get_db"]
    _assert_unavailable(res, token)
    assert token not in caplog.text


def _assert_unavailable(res, token: str) -> None:
    assert res.status_code == 503, res.text
    assert res.json() == {"detail": "Could not check your session right now"}
    assert token not in res.text
    assert auth.decode_session_claims(token)["jti"] not in res.text
    assert token not in str(res.headers)
    assert _set_cookies(res) == []
    assert res.headers["cache-control"] == "no-store"


@pytest.mark.parametrize(
    ("module", "name"),
    [(users_db, "get_user"), (sessions_db, "is_revoked"), (families_db, "list_family_summaries")],
    ids=["user-lookup", "revocation-check", "family-list"],
)
def test_a_database_error_after_a_valid_cookie_is_503_not_signed_out(app, monkeypatch, caplog, module, name):
    cookie, _ = _signup(app)

    def broken(*_args, **_kwargs):
        raise PyMongoError("connection reset")

    monkeypatch.setattr(module, name, broken)
    with caplog.at_level(logging.ERROR, logger=auth_routes.log.name):
        res = _get(app, "/session", cookie)

    _assert_unavailable(res, cookie)
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR and r.name == auth_routes.log.name]
    assert len(errors) == 1 and errors[0].exc_info is not None  # logged with its traceback
    assert cookie not in caplog.text


def test_session_user_or_none_never_raises_for_a_bad_token(mdb):
    for token in (None, "", "not-a-jwt", _expired("u1"), _wrong_secret("u1"), _no_jti("u1")):
        assert auth.session_user_or_none(token, mdb) is None


def test_session_user_or_none_lets_a_database_error_through(mdb, monkeypatch):
    user = users_db.create_user(mdb, email="ana@example.com", password_hash="x", name="Ana")
    token = auth.create_session_token(user.id)
    assert auth.session_user_or_none(token, mdb) == user

    monkeypatch.setattr(users_db, "get_user", _boom)
    with pytest.raises(PyMongoError):
        auth.session_user_or_none(token, mdb)
