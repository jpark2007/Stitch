"""routes/auth.py: signup, login, logout and GET /me, mounted on a bare app over mongomock."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import auth
from db import client as db_client
from db import families as families_db
from routes import auth as auth_routes

PASSWORD = "correct horse battery"
FORBIDDEN = ("passwordHash", "password_hash", "$argon2")


@pytest.fixture
def tc(mdb):
    db_client.ensure_indexes(mdb)
    db_client.set_db(mdb)
    app = FastAPI()
    app.include_router(auth_routes.router)
    try:
        with TestClient(app) as client:
            yield client
    finally:
        db_client.set_db(None)


def _signup(tc: TestClient, email: str = "david@example.com", name: str = "David", password: str = PASSWORD):
    return tc.post("/auth/signup", json={"email": email, "password": password, "name": name})


def _set_cookie_header(resp) -> str:
    headers = [v for k, v in resp.headers.multi_items() if k.lower() == "set-cookie"]
    assert len(headers) == 1, headers
    return headers[0]


def _assert_no_secrets(resp) -> None:
    body = resp.text
    for needle in FORBIDDEN:
        assert needle not in body, f"{needle!r} leaked in {resp.request.method} {resp.request.url.path}"


# ---------------------------------------------------------------- router shape


def test_router_has_exactly_the_four_routes():
    routes = {(m, r.path) for r in auth_routes.router.routes for m in r.methods}
    assert routes == {
        ("POST", "/auth/signup"),
        ("POST", "/auth/login"),
        ("POST", "/auth/logout"),
        ("GET", "/me"),
    }


# ---------------------------------------------------------------- signup


def test_signup_sets_cookie_and_returns_me(tc):
    resp = _signup(tc)
    assert resp.status_code == 201
    body = resp.json()
    assert set(body) == {"user", "families"}
    assert body["families"] == []
    user = body["user"]
    assert set(user) == {"id", "email", "name", "createdAt"}  # camelCase, no password field
    assert user["email"] == "david@example.com"
    assert user["name"] == "David"

    cookie = _set_cookie_header(resp)
    assert cookie.startswith(f"{auth.COOKIE_NAME}=")
    lowered = cookie.lower()
    assert "httponly" in lowered
    assert "samesite=lax" in lowered
    assert "path=/" in lowered
    assert auth.decode_session_token(tc.cookies.get(auth.COOKIE_NAME)) == user["id"]

    me = tc.get("/me")
    assert me.status_code == 200
    assert me.json() == {"user": user, "families": []}


def test_signup_normalizes_email_and_strips_name(tc):
    resp = _signup(tc, email="  David@Example.com ", name="  David  ")
    assert resp.status_code == 201
    assert resp.json()["user"]["email"] == "david@example.com"
    assert resp.json()["user"]["name"] == "David"

    tc.cookies.clear()
    login = tc.post("/auth/login", json={"email": "david@example.com", "password": PASSWORD})
    assert login.status_code == 200
    assert login.json()["user"]["id"] == resp.json()["user"]["id"]


def test_signup_duplicate_email_is_409(tc):
    assert _signup(tc).status_code == 201
    tc.cookies.clear()
    dup = _signup(tc, email="DAVID@example.com", name="Other David")
    assert dup.status_code == 409
    assert dup.json() == {"detail": "An account with this email already exists"}
    assert "set-cookie" not in dup.headers


@pytest.mark.parametrize(
    "payload, detail",
    [
        ({"email": "a@example.com", "password": "short", "name": "Ana"}, None),
        ({"email": "a@example.com", "password": PASSWORD, "name": "   "}, "Enter your name"),
        ({"email": "a@example.com", "password": PASSWORD, "name": ""}, "Enter your name"),
        ({"email": "not-an-email", "password": PASSWORD, "name": "Ana"}, "Enter a valid email"),
        ({"email": "   ", "password": PASSWORD, "name": "Ana"}, "Enter a valid email"),
        ({"email": "a@example.com", "name": "Ana"}, None),
        ({"email": "a@example.com", "password": PASSWORD, "name": "Ana", "role": "admin"}, None),
    ],
    ids=["short-password", "blank-name", "empty-name", "no-at", "blank-email", "missing-password", "extra-field"],
)
def test_signup_validation_is_422(tc, mdb, payload, detail):
    resp = tc.post("/auth/signup", json=payload)
    assert resp.status_code == 422
    if detail is not None:
        assert resp.json() == {"detail": detail}
    assert "set-cookie" not in resp.headers
    assert mdb.users.count_documents({}) == 0
    _assert_no_secrets(resp)


def test_signup_stores_argon2_hash_not_password(tc, mdb):
    _signup(tc)
    doc = mdb.users.find_one({"email": "david@example.com"})
    assert doc["passwordHash"].startswith("$argon2id$")
    assert PASSWORD not in doc["passwordHash"]


# ---------------------------------------------------------------- login


def test_login_sets_cookie_and_returns_families(tc):
    user_id = _signup(tc).json()["user"]["id"]
    fam = families_db.create_family(db_client.get_db(), name="The Maos", creator_user_id=user_id)
    tc.cookies.clear()

    resp = tc.post("/auth/login", json={"email": " David@Example.COM", "password": PASSWORD})
    assert resp.status_code == 200
    body = resp.json()
    assert body["user"]["id"] == user_id
    assert body["families"] == [{"id": fam.id, "name": "The Maos", "role": "admin", "personId": None}]

    cookie = _set_cookie_header(resp)
    assert cookie.startswith(f"{auth.COOKIE_NAME}=") and "httponly" in cookie.lower()
    assert tc.get("/me").json() == body


def test_wrong_password_and_unknown_email_are_identical_401(tc):
    _signup(tc)
    tc.cookies.clear()
    wrong = tc.post("/auth/login", json={"email": "david@example.com", "password": "not the password"})
    unknown = tc.post("/auth/login", json={"email": "nobody@example.com", "password": PASSWORD})
    malformed = tc.post("/auth/login", json={"email": "nobody", "password": PASSWORD})
    for resp in (wrong, unknown, malformed):
        assert resp.status_code == 401
        assert resp.json() == {"detail": "Invalid email or password"}
        assert "set-cookie" not in resp.headers
    assert wrong.content == unknown.content == malformed.content
    assert tc.get("/me").status_code == 401


def test_unknown_email_still_runs_argon2(tc, monkeypatch):
    calls: list[str] = []
    real = auth.verify_password

    def spy(password_hash: str, pw: str) -> bool:
        calls.append(password_hash)
        return real(password_hash, pw)

    monkeypatch.setattr(auth, "verify_password", spy)
    tc.post("/auth/login", json={"email": "nobody@example.com", "password": PASSWORD})
    tc.post("/auth/login", json={"email": "malformed", "password": PASSWORD})
    assert len(calls) == 2
    assert calls[0].startswith("$argon2id$")
    assert calls[0] == calls[1]  # computed once, reused


# ---------------------------------------------------------------- logout and /me


def test_logout_clears_cookie_then_me_is_401(tc):
    _signup(tc)
    assert tc.get("/me").status_code == 200

    resp = tc.post("/auth/logout")
    assert resp.status_code == 204
    assert resp.content == b""
    cookie = _set_cookie_header(resp)
    assert cookie.startswith(f'{auth.COOKIE_NAME}=""') or cookie.startswith(f"{auth.COOKIE_NAME}=;")
    assert "max-age=0" in cookie.lower()
    assert auth.COOKIE_NAME not in tc.cookies

    assert tc.get("/me").status_code == 401


def test_logout_without_session_is_204(tc):
    resp = tc.post("/auth/logout")
    assert resp.status_code == 204
    assert "max-age=0" in _set_cookie_header(resp).lower()


def test_me_without_cookie_is_401(tc):
    resp = tc.get("/me")
    assert resp.status_code == 401
    assert resp.json() == {"detail": "Not signed in"}


def test_me_with_tampered_cookie_is_401(tc):
    _signup(tc)
    token = tc.cookies.get(auth.COOKIE_NAME)
    head, payload, sig = token.split(".")
    tampered = f"{head}.{payload}.{sig[:-2]}{'AA' if not sig.endswith('AA') else 'BB'}"
    tc.cookies.clear()
    tc.cookies.set(auth.COOKIE_NAME, tampered)
    assert tc.get("/me").status_code == 401
    tc.cookies.set(auth.COOKIE_NAME, "garbage")
    assert tc.get("/me").status_code == 401


def test_me_for_deleted_user_is_401(tc, mdb):
    user_id = _signup(tc).json()["user"]["id"]
    mdb.users.delete_one({"_id": user_id})
    assert tc.get("/me").status_code == 401


# ---------------------------------------------------------------- no secrets, ever


def test_no_response_contains_password_hash(tc):
    responses = [
        _signup(tc),
        _signup(tc),  # 409
        tc.post("/auth/signup", json={"email": "x@example.com", "password": "short", "name": "X"}),
        tc.get("/me"),
    ]
    user_id = responses[0].json()["user"]["id"]
    families_db.create_family(db_client.get_db(), name="The Maos", creator_user_id=user_id)
    responses += [
        tc.get("/me"),
        tc.post("/auth/logout"),
        tc.get("/me"),  # 401
        tc.post("/auth/login", json={"email": "david@example.com", "password": PASSWORD}),
        tc.post("/auth/login", json={"email": "david@example.com", "password": "wrong password"}),
        tc.post("/auth/login", json={"email": "nobody@example.com", "password": PASSWORD}),
        tc.post("/auth/login", json={"email": "david@example.com"}),  # 422
    ]
    assert {r.status_code for r in responses} == {200, 201, 204, 401, 409, 422}
    for resp in responses:
        _assert_no_secrets(resp)
