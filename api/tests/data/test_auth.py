"""auth.py: argon2 passwords, session JWTs, the session cookie, and the access dependencies."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from fastapi import Depends, FastAPI, Response
from fastapi.testclient import TestClient

import auth
import schemas
from db import client as db_client
from db import families as families_db
from db import users as users_db
from db.client import get_db

# ---------------------------------------------------------------- passwords


def test_hash_round_trip():
    hashed = auth.hash_password("correct horse battery staple")
    assert hashed.startswith("$argon2id$")
    assert "correct horse" not in hashed
    assert auth.verify_password(hashed, "correct horse battery staple") is True
    assert auth.hash_password("correct horse battery staple") != hashed  # salted


def test_wrong_password_and_bad_hash():
    hashed = auth.hash_password("pw-1")
    assert auth.verify_password(hashed, "pw-2") is False
    assert auth.verify_password(hashed, "") is False
    assert auth.verify_password("not-a-hash", "pw-1") is False
    assert auth.verify_password("", "pw-1") is False
    assert auth.verify_password(None, "pw-1") is False  # type: ignore[arg-type]
    assert auth.verify_password(hashed, None) is False  # type: ignore[arg-type]


# ---------------------------------------------------------------- tokens


def test_token_round_trip():
    token = auth.create_session_token("user-1")
    assert auth.decode_session_token(token) == "user-1"
    claims = jwt.decode(token, options={"verify_signature": False})
    assert set(claims) == {"sub", "iat", "exp"}
    assert claims["exp"] - claims["iat"] == auth.SESSION_DAYS * 86400


def test_token_expiry_via_now():
    now = datetime.now(UTC)
    fresh = auth.create_session_token("user-1", now=now - timedelta(days=auth.SESSION_DAYS - 1))
    stale = auth.create_session_token("user-1", now=now - timedelta(days=auth.SESSION_DAYS, minutes=1))
    assert auth.decode_session_token(fresh) == "user-1"
    assert auth.decode_session_token(stale) is None


def test_tampered_token():
    token = auth.create_session_token("user-1")
    header, payload, signature = token.split(".")
    forged_payload = jwt.utils.base64url_encode(
        jwt.utils.base64url_decode(payload).replace(b"user-1", b"user-2")
    ).decode()
    assert auth.decode_session_token(f"{header}.{forged_payload}.{signature}") is None
    flipped = signature[:-2] + ("AA" if signature[-2:] != "AA" else "BB")
    assert auth.decode_session_token(f"{header}.{payload}.{flipped}") is None


def test_token_signed_with_wrong_secret():
    now = int(datetime.now(UTC).timestamp())
    claims = {"sub": "user-1", "iat": now, "exp": now + 3600}
    other = jwt.encode(claims, "some-other-secret-0123456789abcdef0123", algorithm="HS256")
    assert auth.decode_session_token(other) is None


def test_unsigned_and_malformed_tokens():
    now = int(datetime.now(UTC).timestamp())
    unsigned = jwt.encode({"sub": "user-1", "iat": now, "exp": now + 3600}, None, algorithm="none")
    assert auth.decode_session_token(unsigned) is None
    for bad in ["", "abc", "a.b.c", "....", None]:
        assert auth.decode_session_token(bad) is None  # type: ignore[arg-type]


def test_token_missing_claims():
    secret = auth._secret()
    now = int(datetime.now(UTC).timestamp())
    no_exp = jwt.encode({"sub": "user-1", "iat": now}, secret, algorithm="HS256")
    no_sub = jwt.encode({"iat": now, "exp": now + 3600}, secret, algorithm="HS256")
    empty_sub = jwt.encode({"sub": "", "iat": now, "exp": now + 3600}, secret, algorithm="HS256")
    assert auth.decode_session_token(no_exp) is None
    assert auth.decode_session_token(no_sub) is None
    assert auth.decode_session_token(empty_sub) is None


def test_fallback_secret_when_unset(monkeypatch, caplog):
    monkeypatch.delenv("JWT_SECRET", raising=False)
    monkeypatch.setattr(auth, "_fallback_secret", None)
    with caplog.at_level(logging.WARNING, logger="auth"):
        token = auth.create_session_token("user-1")
        assert auth.decode_session_token(token) == "user-1"
    assert "JWT_SECRET" in caplog.text
    first = auth._fallback_secret
    assert first and len(first) >= 32
    auth.create_session_token("user-2")
    assert auth._fallback_secret == first  # one per process


def test_token_from_one_secret_fails_under_another(monkeypatch):
    token = auth.create_session_token("user-1")
    monkeypatch.setenv("JWT_SECRET", "rotated-secret-0123456789abcdef0123456789")
    assert auth.decode_session_token(token) is None


# ---------------------------------------------------------------- dependencies on a tiny app

ROLES: dict[tuple[str, str], str] = {}


def _fake_get_member_role(db, family_id, user_id):
    assert family_id and user_id
    return ROLES.get((family_id, user_id))


def _build_app(mdb) -> FastAPI:
    app = FastAPI()

    @app.post("/session/{uid}")
    def start(uid: str, response: Response) -> dict[str, bool]:
        auth.set_session_cookie(response, uid)
        return {"ok": True}

    @app.post("/logout")
    def logout(response: Response) -> dict[str, bool]:
        auth.clear_session_cookie(response)
        return {"ok": True}

    @app.get("/me", response_model=schemas.User)
    def me(user: schemas.User = Depends(auth.current_user)) -> schemas.User:
        return user

    @app.get("/maybe")
    def maybe(user: schemas.User | None = Depends(auth.optional_user)) -> dict[str, str | None]:
        return {"id": user.id if user else None}

    @app.get("/families/{fid}/tree")
    def tree(access: auth.FamilyAccess = Depends(auth.family_member)) -> dict[str, object]:
        return {"familyId": access.family_id, "role": access.role, "isAdmin": access.is_admin}

    @app.post("/families/{fid}/invites")
    def invite(access: auth.FamilyAccess = Depends(auth.family_admin)) -> dict[str, str]:
        return {"familyId": access.family_id}

    app.dependency_overrides[get_db] = lambda: mdb
    return app


@pytest.fixture
def env(mdb, monkeypatch):
    db_client.ensure_indexes(mdb)
    ROLES.clear()
    monkeypatch.setattr(families_db, "get_member_role", _fake_get_member_role, raising=False)
    alice = users_db.create_user(mdb, email="alice@x.com", password_hash=auth.hash_password("pw"), name="Alice")
    bob = users_db.create_user(mdb, email="bob@x.com", password_hash=auth.hash_password("pw"), name="Bob")
    ROLES[("famA", alice.id)] = "admin"
    ROLES[("famA", bob.id)] = "member"
    tc = TestClient(_build_app(mdb))
    return tc, alice, bob


def _signed_in(tc: TestClient, user_id: str) -> TestClient:
    tc.cookies.set(auth.COOKIE_NAME, auth.create_session_token(user_id))
    return tc


def test_set_session_cookie_attributes(env):
    tc, alice, _ = env
    resp = tc.post(f"/session/{alice.id}")
    assert resp.status_code == 200
    header = resp.headers["set-cookie"]
    parts = [p.strip() for p in header.split(";")]
    lowered = [p.lower() for p in parts]
    assert parts[0].startswith(f"{auth.COOKIE_NAME}=")
    assert "httponly" in lowered
    assert "samesite=lax" in lowered
    assert "path=/" in lowered
    assert f"max-age={auth.SESSION_DAYS * 86400}" in lowered
    assert "secure" not in lowered
    token = parts[0].split("=", 1)[1]
    assert auth.decode_session_token(token) == alice.id
    # The client keeps the cookie and the next request is signed in.
    assert tc.get("/me").json()["id"] == alice.id


def test_secure_cookie_when_configured(env, monkeypatch):
    tc, alice, _ = env
    monkeypatch.setenv("COOKIE_SECURE", "1")
    header = tc.post(f"/session/{alice.id}").headers["set-cookie"].lower()
    assert "; secure" in header


def test_clear_session_cookie(env):
    tc, alice, _ = env
    header = tc.post("/logout").headers["set-cookie"].lower()
    assert header.startswith(f"{auth.COOKIE_NAME}=")
    assert "max-age=0" in header
    assert "path=/" in header


def test_current_user_without_cookie(env):
    tc, _, _ = env
    resp = tc.get("/me")
    assert resp.status_code == 401
    assert resp.json() == {"detail": "Not signed in"}


def test_current_user_with_bad_cookie(env):
    tc, _, _ = env
    tc.cookies.set(auth.COOKIE_NAME, "garbage")
    resp = tc.get("/me")
    assert resp.status_code == 401
    assert resp.json() == {"detail": "Not signed in"}


def test_current_user_with_expired_cookie(env):
    tc, alice, _ = env
    old = datetime.now(UTC) - timedelta(days=auth.SESSION_DAYS + 1)
    tc.cookies.set(auth.COOKIE_NAME, auth.create_session_token(alice.id, now=old))
    assert tc.get("/me").status_code == 401


def test_current_user_deleted_user(env, mdb):
    tc, alice, _ = env
    _signed_in(tc, alice.id)
    assert tc.get("/me").status_code == 200
    mdb.users.delete_one({"_id": alice.id})
    resp = tc.get("/me")
    assert resp.status_code == 401
    assert resp.json() == {"detail": "Not signed in"}


def test_current_user_valid_cookie_has_no_password_hash(env):
    tc, alice, _ = env
    resp = _signed_in(tc, alice.id).get("/me")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == alice.id
    assert body["email"] == "alice@x.com"
    assert "passwordHash" not in body
    assert "argon2" not in resp.text


def test_optional_user(env):
    tc, alice, _ = env
    assert tc.get("/maybe").json() == {"id": None}
    tc.cookies.set(auth.COOKIE_NAME, "garbage")
    assert tc.get("/maybe").json() == {"id": None}
    _signed_in(tc, alice.id)
    assert tc.get("/maybe").json() == {"id": alice.id}


def test_family_member_success(env):
    tc, alice, bob = env
    resp = _signed_in(tc, alice.id).get("/families/famA/tree")
    assert resp.status_code == 200
    assert resp.json() == {"familyId": "famA", "role": "admin", "isAdmin": True}
    resp = _signed_in(tc, bob.id).get("/families/famA/tree")
    assert resp.json() == {"familyId": "famA", "role": "member", "isAdmin": False}


def test_family_member_non_member_gets_404_not_403(env):
    tc, alice, _ = env
    _signed_in(tc, alice.id)
    for path in ["/families/famB/tree", "/families/does-not-exist/tree"]:
        resp = tc.get(path)
        assert resp.status_code == 404
        assert resp.json() == {"detail": "Not found"}
    # Admin-only routes of another family are 404 too, never 403.
    assert tc.post("/families/famB/invites").status_code == 404


def test_family_member_requires_sign_in(env):
    tc, _, _ = env
    assert tc.get("/families/famA/tree").status_code == 401


def test_family_admin(env):
    tc, alice, bob = env
    assert _signed_in(tc, alice.id).post("/families/famA/invites").json() == {"familyId": "famA"}
    resp = _signed_in(tc, bob.id).post("/families/famA/invites")
    assert resp.status_code == 403


def test_family_member_calls_get_member_role_at_request_time(env, monkeypatch, mdb):
    """auth must look the function up on the module per request, so db.families can be swapped in."""
    tc, alice, _ = env
    seen = []

    def spy(db, family_id, user_id):
        seen.append((db, family_id, user_id))
        return "member"

    monkeypatch.setattr(families_db, "get_member_role", spy, raising=False)
    assert _signed_in(tc, alice.id).get("/families/famZ/tree").status_code == 200
    assert seen == [(mdb, "famZ", alice.id)]


def test_family_access_is_frozen():
    user = schemas.User(id="u", email="u@x.com", name="U")
    access = auth.FamilyAccess(user=user, family_id="f", role="member")
    assert access.is_admin is False
    with pytest.raises(AttributeError):
        access.role = "admin"  # type: ignore[misc]
