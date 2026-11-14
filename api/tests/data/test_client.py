"""db/client.py: connection helpers and indexes."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pymongo.errors import DuplicateKeyError, OperationFailure

from db import client

EXPECTED = {
    "users": {"users_email_unique": [("email", 1)]},
    "families": {
        "families_members_userId": [("members.userId", 1)],
        "families_invites_code_unique": [("invites.code", 1)],
    },
    "people": {
        "people_familyId_userId": [("familyId", 1), ("userId", 1)],
        "people_userId_familyId_unique": [("userId", 1), ("familyId", 1)],
        "people_familyId_parentIds": [("familyId", 1), ("parentIds", 1)],
    },
    "memories": {
        "memories_familyId_participantIds_year": [("familyId", 1), ("participantIds", 1), ("year", 1)],
    },
    "questions": {
        "questions_familyId_personId_status": [("familyId", 1), ("personId", 1), ("status", 1)],
    },
    "props": {"props_familyId_name": [("familyId", 1), ("name", 1)]},
    "sets": {"sets_familyId_name": [("familyId", 1), ("name", 1)]},
    "revoked_sessions": {"revoked_sessions_expiresAt_ttl": [("expiresAt", 1)]},
}


def _keys(info: dict) -> list[tuple[str, int]]:
    return [(k, int(v)) for k, v in info["key"]]


def _assert_all_indexes(db) -> None:
    for collection, indexes in EXPECTED.items():
        info = db[collection].index_information()
        for name, keys in indexes.items():
            assert name in info, f"{collection}.{name} missing"
            assert _keys(info[name]) == keys
    assert db.users.index_information()["users_email_unique"].get("unique") is True
    invites = db.families.index_information()["families_invites_code_unique"]
    assert invites.get("unique") is True
    assert invites.get("partialFilterExpression") == {"invites.code": {"$exists": True}}
    people = db.people.index_information()
    assert not people["people_familyId_userId"].get("unique")
    one_per_user = people["people_userId_familyId_unique"]
    assert one_per_user.get("unique") is True
    assert one_per_user.get("partialFilterExpression") == {"userId": {"$type": "string"}}
    ttl = db.revoked_sessions.index_information()["revoked_sessions_expiresAt_ttl"]
    assert ttl.get("expireAfterSeconds") == 0
    assert not ttl.get("unique")


def test_constants():
    assert client.VECTOR_INDEXES == {
        "memories": "memories_vector",
        "props": "props_vector",
        "sets": "sets_vector",
    }
    assert isinstance(client.VECTOR_DIM, int) and client.VECTOR_DIM > 0


def test_ensure_indexes_creates_everything_and_is_idempotent(mdb):
    client.ensure_indexes(mdb)
    _assert_all_indexes(mdb)
    before = {c: mdb[c].index_information() for c in EXPECTED}
    client.ensure_indexes(mdb)
    client.ensure_indexes(mdb)
    assert {c: mdb[c].index_information() for c in EXPECTED} == before


def test_ensure_indexes_idempotent_with_data(mdb):
    """Re-running on a populated database must not trip over existing documents."""
    client.ensure_indexes(mdb)
    mdb.users.insert_many([{"_id": "u1", "email": "a@x.com"}, {"_id": "u2", "email": "b@x.com"}])
    mdb.families.insert_many([
        {"_id": "f1", "name": "A", "members": [], "invites": []},
        {"_id": "f2", "name": "B", "members": [], "invites": []},
        {"_id": "f3", "name": "C", "members": [], "invites": [{"code": "abc"}]},
    ])
    client.ensure_indexes(mdb)
    _assert_all_indexes(mdb)


def test_ensure_indexes_surfaces_a_conflicting_spec(mdb):
    """An old index with our name but different options is not silently kept."""
    mdb.users.create_index([("email", 1)], name="users_email_unique")  # not unique
    with pytest.raises(OperationFailure):
        client.ensure_indexes(mdb)


def test_revoked_sessions_ttl_index_is_idempotent(mdb):
    client.ensure_indexes(mdb)
    first = mdb.revoked_sessions.index_information()
    assert first["revoked_sessions_expiresAt_ttl"]["expireAfterSeconds"] == 0
    mdb.revoked_sessions.insert_one({"_id": "j1", "expiresAt": datetime.now(UTC) + timedelta(days=1)})
    client.ensure_indexes(mdb)
    assert mdb.revoked_sessions.index_information() == first
    assert mdb.revoked_sessions.count_documents({}) == 1


def test_ensure_indexes_surfaces_a_ttl_mismatch(mdb):
    """An old index with our name but no TTL is not mistaken for the TTL index."""
    mdb.revoked_sessions.create_index([("expiresAt", 1)], name="revoked_sessions_expiresAt_ttl")
    with pytest.raises(OperationFailure):
        client.ensure_indexes(mdb)


def test_unique_email_enforced(mdb):
    client.ensure_indexes(mdb)
    mdb.users.insert_one({"_id": "u1", "email": "a@x.com"})
    with pytest.raises(DuplicateKeyError):
        mdb.users.insert_one({"_id": "u2", "email": "a@x.com"})


def test_families_without_invites_coexist(mdb):
    client.ensure_indexes(mdb)
    mdb.families.insert_one({"_id": "f1", "name": "A", "members": [], "invites": []})
    mdb.families.insert_one({"_id": "f2", "name": "B", "members": [], "invites": []})
    mdb.families.insert_one({"_id": "f3", "name": "C", "members": []})
    mdb.families.insert_one({"_id": "f4", "name": "D", "members": []})
    assert mdb.families.count_documents({}) == 4


def test_people_unique_index_is_seen_as_present_on_rerun(mdb):
    """_same_spec must match it (partialFilterExpression included), so a rerun is a no-op."""
    client.ensure_indexes(mdb)
    name, keys, options = next(s for s in client.INDEXES["people"] if s[0] == "people_userId_familyId_unique")
    assert client._same_spec(mdb.people.index_information()[name], keys, options)
    client.ensure_indexes(mdb)


def test_people_unique_index_added_beside_the_old_non_unique_one(mdb):
    """A database from before this index (old index plus linked people) upgrades cleanly."""
    mdb.people.create_index([("familyId", 1), ("userId", 1)], name="people_familyId_userId")
    mdb.people.insert_many([
        {"_id": "p1", "familyId": "f1", "userId": "u1"},
        {"_id": "p2", "familyId": "f1", "userId": None},
        {"_id": "p3", "familyId": "f1", "userId": None},
        {"_id": "p4", "familyId": "f2", "userId": "u1"},
    ])
    client.ensure_indexes(mdb)
    client.ensure_indexes(mdb)
    _assert_all_indexes(mdb)


@pytest.mark.parametrize("hook", ["missing", "broken"])
def test_mongomock_index_hook_change_degrades_with_a_warning(caplog, hook):
    """If a mongomock upgrade drops or changes its internal index store, startup survives."""
    from types import SimpleNamespace

    def broken(*_a, **_kw):
        raise TypeError("changed signature")

    store = None if hook == "missing" else SimpleNamespace(create_index=broken)
    name, keys, options = next(s for s in client.INDEXES["people"] if s[0] == "people_userId_familyId_unique")
    with caplog.at_level("WARNING", logger="db.client"):
        client._register_mongomock_index(
            SimpleNamespace(_store=store), name, keys, options["partialFilterExpression"]
        )
    assert f"index {name} skipped" in caplog.text


def test_people_unique_index_refuses_real_duplicates(mdb):
    """The mongomock creation workaround never hides a user already linked twice."""
    mdb.people.insert_many([
        {"_id": "p1", "familyId": "f1", "userId": "u1"},
        {"_id": "p2", "familyId": "f1", "userId": "u1"},
    ])
    with pytest.raises(DuplicateKeyError):
        client.ensure_indexes(mdb)
    assert "people_userId_familyId_unique" not in mdb.people.index_information()


def test_one_person_per_user_per_family_enforced(mdb):
    client.ensure_indexes(mdb)
    mdb.people.insert_one({"_id": "p1", "familyId": "f1", "userId": "u1"})
    with pytest.raises(DuplicateKeyError):
        mdb.people.insert_one({"_id": "p2", "familyId": "f1", "userId": "u1"})
    # Unclaimed people (null or absent userId) never collide; other families are separate.
    mdb.people.insert_many([
        {"_id": "p3", "familyId": "f1", "userId": None},
        {"_id": "p4", "familyId": "f1", "userId": None},
        {"_id": "p5", "familyId": "f1"},
        {"_id": "p6", "familyId": "f2", "userId": "u1"},
    ])
    # Claiming a second person by update is rejected too.
    with pytest.raises(DuplicateKeyError):
        mdb.people.update_one({"_id": "p3"}, {"$set": {"userId": "u1"}})
    assert mdb.people.count_documents({"familyId": "f1", "userId": "u1"}) == 1


def test_ensure_search_indexes_is_best_effort_on_mongomock(mdb):
    assert client.ensure_search_indexes(mdb) == []


def test_ping(mdb):
    assert client.ping(mdb) is True


def test_ping_false_when_command_fails():
    class Broken:
        def command(self, *_args, **_kwargs):
            raise RuntimeError("down")

    assert client.ping(Broken()) is False


def test_set_db_overrides_get_db(mdb):
    try:
        client.set_db(mdb)
        assert client.get_db() is mdb
    finally:
        client.set_db(None)
    assert client.get_db() is not mdb


def test_get_client_is_cached_and_lazy(monkeypatch):
    """No server here: building the client must not connect or raise."""
    monkeypatch.setattr(client, "_client", None)
    first = client.get_client()
    try:
        assert client.get_client() is first
        assert first.codec_options.tz_aware is True
        assert first.options.server_selection_timeout == 5.0
        assert first.options.pool_options.metadata["application"] == {"name": "stitch"}
        assert client.get_db().name == client.MONGODB_DB
    finally:
        first.close()


# ---------------------------------------------------------------- real MongoDB (opt-in)

def test_real_ensure_indexes(real_db):
    client.ensure_indexes(real_db)
    client.ensure_indexes(real_db)
    _assert_all_indexes(real_db)

    real_db.families.insert_one({"_id": "f1", "name": "A", "members": [], "invites": []})
    real_db.families.insert_one({"_id": "f2", "name": "B", "members": []})
    real_db.families.insert_one({"_id": "f3", "name": "C", "invites": [{"code": "k1"}]})
    with pytest.raises(DuplicateKeyError):
        real_db.families.insert_one({"_id": "f4", "name": "D", "invites": [{"code": "k1"}]})

    real_db.users.insert_one({"_id": "u1", "email": "a@x.com"})
    with pytest.raises(DuplicateKeyError):
        real_db.users.insert_one({"_id": "u2", "email": "a@x.com"})

    real_db.people.insert_many([
        {"_id": "p1", "familyId": "f1", "userId": "u1"},
        {"_id": "p2", "familyId": "f1", "userId": None},
        {"_id": "p3", "familyId": "f1", "userId": None},
        {"_id": "p4", "familyId": "f2", "userId": "u1"},
    ])
    with pytest.raises(DuplicateKeyError):
        real_db.people.insert_one({"_id": "p5", "familyId": "f1", "userId": "u1"})
    with pytest.raises(DuplicateKeyError):
        real_db.people.update_one({"_id": "p2"}, {"$set": {"userId": "u1"}})

    # Plain MongoDB has no search indexes; Atlas may. Either way, no exception.
    created = client.ensure_search_indexes(real_db)
    assert set(created) <= set(client.VECTOR_INDEXES.values())
