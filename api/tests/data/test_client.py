"""db/client.py: connection helpers and indexes."""

from __future__ import annotations

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

    # Plain MongoDB has no search indexes; Atlas may. Either way, no exception.
    created = client.ensure_search_indexes(real_db)
    assert set(created) <= set(client.VECTOR_INDEXES.values())
