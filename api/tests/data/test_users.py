"""db/users.py: accounts, email normalization, and keeping passwordHash inside the db layer."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

import schemas
from db import client, users


@pytest.fixture
def udb(mdb):
    client.ensure_indexes(mdb)
    return mdb


def test_normalize_email():
    assert users.normalize_email("  David@Example.COM ") == "david@example.com"
    for bad in ["", "   ", "no-at-sign"]:
        with pytest.raises(ValueError):
            users.normalize_email(bad)


def test_create_and_get(udb):
    now = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
    user = users.create_user(udb, email=" David@Example.com", password_hash="$argon2id$x", name=" David ", now=now)
    assert isinstance(user, schemas.User)
    assert user.email == "david@example.com"
    assert user.name == "David"
    assert user.created_at == now
    assert isinstance(user.id, str) and len(user.id) == 24

    stored = udb.users.find_one({"_id": user.id})
    assert set(stored) == {"_id", "email", "passwordHash", "name", "createdAt"}
    assert stored["passwordHash"] == "$argon2id$x"

    assert users.get_user(udb, user.id) == user
    assert users.get_user_by_email(udb, "DAVID@example.com ") == user
    assert users.get_user(udb, "missing") is None
    assert users.get_user_by_email(udb, "nobody@example.com") is None
    assert users.get_user_by_email(udb, "not an email") is None


def test_created_at_is_utc_aware(udb):
    user = users.create_user(udb, email="a@x.com", password_hash="h", name="A")
    assert user.created_at.tzinfo is not None
    assert users.get_user(udb, user.id).created_at.tzinfo is not None


def test_explicit_user_id(udb):
    user = users.create_user(udb, email="a@x.com", password_hash="h", name="A", user_id="demo_david")
    assert user.id == "demo_david"
    assert users.get_user(udb, "demo_david").email == "a@x.com"


def test_email_taken(udb):
    users.create_user(udb, email="a@x.com", password_hash="h", name="A")
    with pytest.raises(users.EmailTaken):
        users.create_user(udb, email=" A@X.com ", password_hash="h2", name="Other")
    assert udb.users.count_documents({}) == 1


def test_email_taken_from_duplicate_key(udb, monkeypatch):
    """The unique index still wins if the pre-check loses a race."""
    users.create_user(udb, email="a@x.com", password_hash="h", name="A")
    real_find_one = udb.users.find_one
    coll = udb.users

    class RacyUsers:
        def __getattr__(self, name):
            return getattr(coll, name)

        def find_one(self, *args, **kwargs):  # the pre-check sees nothing
            return None

    class RacyDb:
        def __getitem__(self, name):
            return RacyUsers() if name == "users" else udb[name]

    with pytest.raises(users.EmailTaken):
        users.create_user(RacyDb(), email="a@x.com", password_hash="h", name="B")
    assert real_find_one({"email": "a@x.com"})["name"] == "A"


def test_create_user_validates(udb):
    with pytest.raises(ValueError):
        users.create_user(udb, email="bad", password_hash="h", name="A")
    with pytest.raises(ValueError):
        users.create_user(udb, email="a@x.com", password_hash="h", name="  ")
    with pytest.raises(ValueError):
        users.create_user(udb, email="a@x.com", password_hash="", name="A")


def test_get_login_returns_hash(udb):
    created = users.create_user(udb, email="a@x.com", password_hash="$argon2id$hash", name="A")
    login = users.get_login(udb, " A@x.com")
    assert login is not None
    user, password_hash = login
    assert user == created
    assert password_hash == "$argon2id$hash"
    assert users.get_login(udb, "nobody@x.com") is None
    assert users.get_login(udb, "") is None


def test_get_users_in_input_order(udb):
    a = users.create_user(udb, email="a@x.com", password_hash="h", name="A")
    b = users.create_user(udb, email="b@x.com", password_hash="h", name="B")
    c = users.create_user(udb, email="c@x.com", password_hash="h", name="C")
    assert users.get_users(udb, [c.id, "missing", a.id, b.id, a.id]) == [c, a, b]
    assert users.get_users(udb, []) == []


def test_no_user_json_contains_password_hash(udb):
    created = users.create_user(udb, email="a@x.com", password_hash="$argon2id$secret", name="A")
    returned = [
        created,
        users.get_user(udb, created.id),
        users.get_user_by_email(udb, "a@x.com"),
        *users.get_users(udb, [created.id]),
        users.get_login(udb, "a@x.com")[0],
    ]
    for user in returned:
        dumped = user.model_dump(mode="json")
        assert "passwordHash" not in dumped and "password_hash" not in dumped
        text = user.model_dump_json()
        assert "passwordHash" not in text and "$argon2id$secret" not in text
        assert set(json.loads(text)) <= {"id", "email", "name", "createdAt"}
