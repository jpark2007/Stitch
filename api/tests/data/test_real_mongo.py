"""Real-MongoDB smoke tests: the code paths mongomock never exercises.

Every test here takes the `real_db` fixture from conftest.py, which skips unless
MONGODB_URI_TEST is set and drops its throwaway database afterwards. With no MongoDB the
whole file reports as skipped. Nothing here connects at import time.

Run against a disposable server:

    MONGODB_URI_TEST=mongodb://localhost:27017 uv run pytest tests/data/test_real_mongo.py -v

Covered:
- db.client.ensure_indexes twice, with the unique indexes present on the server;
- db.client.ensure_search_indexes on a non-Atlas server (no raise, no hang);
- seed.demo.seed_demo idempotency and reset scoping;
- db.media on real GridFS (media.files / media.chunks), including family scoping;
- db.search keyword fallback when $vectorSearch fails with OperationFailure;
- routes.auth / routes.tree / routes.families on a bare app: login, /me, tree, book,
  media 200 and 206, and a cross-family media 404.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Iterator

import pytest
from bson import ObjectId
from fastapi import FastAPI
from fastapi.testclient import TestClient

import auth
import schemas
from db import client as db_client
from db import families as families_db
from db import media as media_db
from db import people as people_db
from db import search as search_db
from db import users as users_db
from routes import auth as auth_routes
from routes import families as families_routes
from routes import tree as tree_routes
from seed import demo

OTHER_FAMILY = "real_other_family"
HANG_TIMEOUT_S = 30.0


def _is_atlas() -> bool:
    return ".mongodb.net" in (os.environ.get("MONGODB_URI_TEST") or "")


def _counts(db) -> dict[str, int]:
    return {name: db[name].count_documents({}) for name in sorted(db.list_collection_names())}


def _walk_keys(value) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, dict):
        for k, v in value.items():
            keys.add(k)
            keys |= _walk_keys(v)
    elif isinstance(value, list):
        for v in value:
            keys |= _walk_keys(v)
    return keys


def _no_secrets(body) -> None:
    keys = _walk_keys(body)
    assert "passwordHash" not in keys
    assert "password_hash" not in keys
    assert "embedding" not in keys


def _other_family(db) -> tuple[schemas.User, str]:
    """A non-demo family with one person and one media file. Returns (its admin, its media id)."""
    owner = users_db.create_user(
        db, email="owner@other.test", password_hash=auth.hash_password("other-pass-123"), name="Owner"
    )
    families_db.create_family(db, name="The Others", creator_user_id=owner.id, family_id=OTHER_FAMILY)
    people_db.create_person(
        db, OTHER_FAMILY, schemas.NewPerson(name="Bo", born=1950), added_by=owner.id, person_id="bo"
    )
    media_id = media_db.put_media(
        db, OTHER_FAMILY, b"other-family-secret-bytes", content_type="image/jpeg", filename="secret.jpg"
    )
    return owner, media_id


# ------------------------------------------------------------------------------ 1. indexes


def test_ensure_indexes_is_idempotent_and_creates_unique_indexes(real_db):
    db_client.ensure_indexes(real_db)
    db_client.ensure_indexes(real_db)  # the second run must be a no-op, not a conflict

    for collection, specs in db_client.INDEXES.items():
        info = real_db[collection].index_information()
        for name, keys, options in specs:
            assert name in info, f"{collection}.{name} missing"
            assert [(k, int(v)) for k, v in info[name]["key"]] == [(k, int(v)) for k, v in keys]
            assert bool(info[name].get("unique")) == bool(options.get("unique")), name
            if "partialFilterExpression" in options:
                assert info[name]["partialFilterExpression"] == options["partialFilterExpression"]

    users = real_db["users"].index_information()
    assert users["users_email_unique"]["unique"] is True
    families = real_db["families"].index_information()
    assert families["families_invites_code_unique"]["unique"] is True
    assert families["families_invites_code_unique"]["partialFilterExpression"] == {
        "invites.code": {"$exists": True}
    }


def test_unique_indexes_are_enforced_by_the_server(real_db):
    db_client.ensure_indexes(real_db)
    users_db.create_user(real_db, email="dup@x.test", password_hash="h", name="A")
    with pytest.raises(users_db.EmailTaken):
        users_db.create_user(real_db, email="dup@x.test", password_hash="h", name="B")

    # Two families with no invites at all must not collide on a null invite code.
    families_db.create_family(real_db, name="One", creator_user_id="u1", family_id="f1")
    families_db.create_family(real_db, name="Two", creator_user_id="u2", family_id="f2")
    assert real_db["families"].count_documents({}) == 2


# ------------------------------------------------------------------------- 2. search indexes


def test_ensure_search_indexes_neither_raises_nor_hangs(real_db):
    db_client.ensure_indexes(real_db)  # collections exist, as on a real startup
    outcome: dict[str, object] = {}

    def run() -> None:
        try:
            outcome["first"] = db_client.ensure_search_indexes(real_db)
            outcome["second"] = db_client.ensure_search_indexes(real_db)
        except BaseException as exc:  # noqa: BLE001 - reported by the assertion below
            outcome["error"] = exc

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(HANG_TIMEOUT_S)
    assert not worker.is_alive(), f"ensure_search_indexes did not return within {HANG_TIMEOUT_S}s"
    assert "error" not in outcome, f"ensure_search_indexes raised: {outcome.get('error')!r}"

    names = set(db_client.VECTOR_INDEXES.values())
    assert isinstance(outcome["first"], list) and set(outcome["first"]) <= names
    assert isinstance(outcome["second"], list) and set(outcome["second"]) <= names
    if not _is_atlas():
        assert outcome["first"] == [] and outcome["second"] == []


# ------------------------------------------------------------------------------- 3. seed


def test_seed_demo_is_idempotent(real_db):
    db_client.ensure_indexes(real_db)

    first = demo.seed_demo(real_db)
    assert first.created is True
    assert first.family_id == demo.DEMO_FAMILY_ID
    after_first = _counts(real_db)
    assert after_first["users"] == len(demo.DEMO_ACCOUNTS)
    assert after_first["families"] == 1
    assert after_first["people"] == len(demo.DEMO_PERSON_IDS)
    assert after_first["memories"] == len(demo.DEMO_MEMORY_IDS)
    # Real GridFS, not the mongomock inline store.
    assert after_first["media.files"] == 2
    assert after_first["media.chunks"] >= 2
    assert media_db.INLINE not in after_first

    second = demo.seed_demo(real_db)
    assert second.created is False
    assert _counts(real_db) == after_first


def test_seed_demo_reset_rebuilds_and_leaves_other_families_alone(real_db):
    db_client.ensure_indexes(real_db)
    owner, other_media = _other_family(real_db)
    other_before = {
        "families": real_db["families"].find_one({"_id": OTHER_FAMILY}),
        "people": list(real_db["people"].find({"familyId": OTHER_FAMILY}).sort("_id")),
        "media": list(real_db[media_db.FILES].find({"metadata.familyId": OTHER_FAMILY}).sort("_id")),
        "user": real_db["users"].find_one({"_id": owner.id}),
    }

    assert demo.seed_demo(real_db).created is True
    seeded = _counts(real_db)
    demo_media_before = {
        str(d["_id"]) for d in real_db[media_db.FILES].find({"metadata.familyId": demo.DEMO_FAMILY_ID})
    }

    # Simulate a stage-demo edit, then reset: the edit is gone and the counts are back.
    real_db["memories"].update_one({"_id": demo.HYMN_MEMORY_ID}, {"$set": {"title": "edited"}})
    rebuilt = demo.seed_demo(real_db, reset=True)
    assert rebuilt.created is True
    assert _counts(real_db) == seeded
    assert real_db["memories"].find_one({"_id": demo.HYMN_MEMORY_ID})["title"] != "edited"

    # The old demo photos (files and chunks) were replaced, not duplicated.
    demo_media_after = {
        str(d["_id"]) for d in real_db[media_db.FILES].find({"metadata.familyId": demo.DEMO_FAMILY_ID})
    }
    assert len(demo_media_after) == 2 and not (demo_media_after & demo_media_before)
    stale = [ObjectId(i) for i in demo_media_before]
    assert real_db[f"{media_db.BUCKET}.chunks"].count_documents({"files_id": {"$in": stale}}) == 0

    # The non-demo family is untouched, byte for byte.
    assert real_db["families"].find_one({"_id": OTHER_FAMILY}) == other_before["families"]
    assert list(real_db["people"].find({"familyId": OTHER_FAMILY}).sort("_id")) == other_before["people"]
    assert (
        list(real_db[media_db.FILES].find({"metadata.familyId": OTHER_FAMILY}).sort("_id"))
        == other_before["media"]
    )
    assert real_db["users"].find_one({"_id": owner.id}) == other_before["user"]
    got = media_db.get_media(real_db, OTHER_FAMILY, other_media)
    assert got is not None and got.data == b"other-family-secret-bytes"


# ------------------------------------------------------------------------------ 4. media


def test_media_round_trip_on_real_gridfs(real_db):
    family, other = "fam_media_a", "fam_media_b"
    # Larger than one GridFS chunk (255 KiB), so the download has to stitch chunks together.
    data = bytes(range(256)) * 2400  # 614,400 bytes
    media_id = media_db.put_media(
        real_db, family, data, content_type="image/png", filename="big.png", kind="photo"
    )

    files = real_db[media_db.FILES].find_one({"_id": media_db._oid(media_id)})
    assert files is not None
    assert files["metadata"] == {"familyId": family, "kind": "photo", "contentType": "image/png"}
    assert real_db[f"{media_db.BUCKET}.chunks"].count_documents({"files_id": files["_id"]}) >= 3
    assert media_db.INLINE not in real_db.list_collection_names()

    got = media_db.get_media(real_db, family, media_id)
    assert got is not None
    assert got.data == data
    assert got.length == len(data)
    assert (got.id, got.family_id, got.content_type, got.filename, got.kind) == (
        media_id, family, "image/png", "big.png", "photo",
    )
    # The 'media/<id>' reference form reads the same file.
    assert media_db.get_media(real_db, family, media_db.media_ref(media_id)).data == data
    assert media_db.media_exists(real_db, family, media_id) is True

    # Another family sees nothing, and cannot delete it.
    assert media_db.get_media(real_db, other, media_id) is None
    assert media_db.media_exists(real_db, other, media_id) is False
    assert media_db.delete_media(real_db, other, media_id) is False
    assert media_db.get_media(real_db, family, media_id) is not None

    assert media_db.delete_media(real_db, family, media_id) is True
    assert media_db.get_media(real_db, family, media_id) is None
    assert media_db.media_exists(real_db, family, media_id) is False
    assert real_db[media_db.FILES].count_documents({"_id": files["_id"]}) == 0
    assert real_db[f"{media_db.BUCKET}.chunks"].count_documents({"files_id": files["_id"]}) == 0
    assert media_db.delete_media(real_db, family, media_id) is False


# ----------------------------------------------------------------------------- 5. search


def test_ask_quotes_falls_back_to_keywords_on_plain_mongodb(real_db, monkeypatch):
    db_client.ensure_indexes(real_db)
    demo.seed_demo(real_db)

    vector_results: list[object] = []
    keyword_calls: list[str] = []
    real_vector, real_keyword = search_db._vector_search, search_db._keyword_search

    def spy_vector(*args, **kwargs):
        result = real_vector(*args, **kwargs)
        vector_results.append(result)
        return result

    def spy_keyword(db, family_id, query, limit):
        keyword_calls.append(family_id)
        return real_keyword(db, family_id, query, limit)

    monkeypatch.setattr(search_db, "_vector_search", spy_vector)
    monkeypatch.setattr(search_db, "_keyword_search", spy_keyword)

    # A query vector forces the $vectorSearch attempt; plain MongoDB rejects the stage.
    quotes = search_db.ask_quotes(
        real_db, demo.DEMO_FAMILY_ID, "Who dropped the hymn books?", vector=[0.1] * db_client.VECTOR_DIM
    )

    assert len(vector_results) == 1
    if _is_atlas():
        assert not vector_results[0]  # no index in the throwaway db: nothing found
    else:
        assert vector_results[0] is None  # OperationFailure was caught
    assert keyword_calls == [demo.DEMO_FAMILY_ID]

    assert quotes, "keyword fallback found nothing"
    assert {q.memory_id for q in quotes} == {demo.HYMN_MEMORY_ID}
    assert {q.author_id for q in quotes} == {"dad", "mom"}
    assert all("hymn book" in q.text.lower() for q in quotes)
    assert all(q.year == 1988 for q in quotes)

    # Scoped to the family: the same question asked of another family finds nothing.
    assert search_db.ask_quotes(real_db, "no_such_family", "hymn books", vector=[0.1] * 4) == []


# ----------------------------------------------------------------------------- 6. routes


@pytest.fixture
def api(real_db) -> Iterator[tuple[TestClient, object, str]]:
    """A bare app with the auth, tree and families routers, pointed at the seeded real db."""
    db_client.ensure_indexes(real_db)
    demo.seed_demo(real_db)
    _owner, other_media = _other_family(real_db)

    app = FastAPI()
    app.include_router(auth_routes.router)
    app.include_router(tree_routes.router)
    app.include_router(families_routes.router)
    db_client.set_db(real_db)
    try:
        with TestClient(app) as client:
            yield client, real_db, other_media
    finally:
        db_client.set_db(None)


def _demo_photo_ids(db) -> list[str]:
    docs = db[media_db.FILES].find({"metadata.familyId": demo.DEMO_FAMILY_ID}).sort("filename")
    return [str(d["_id"]) for d in docs]


def test_login_me_tree_and_book_through_the_routers(api):
    client, _db, _other_media = api

    resp = client.post("/auth/login", json={"email": "david@demo.stitch", "password": demo.DEMO_PASSWORD})
    assert resp.status_code == 200, resp.text
    assert auth.COOKIE_NAME in resp.cookies
    body = resp.json()
    _no_secrets(body)
    assert body["user"]["email"] == "david@demo.stitch"
    assert [(f["id"], f["role"], f["personId"]) for f in body["families"]] == [
        (demo.DEMO_FAMILY_ID, "admin", "dad")
    ]

    me = client.get("/me")
    assert me.status_code == 200
    _no_secrets(me.json())
    assert me.json()["user"]["id"] == "demo_user_david"

    tree = client.get(f"/families/{demo.DEMO_FAMILY_ID}/tree")
    assert tree.status_code == 200
    tree_body = tree.json()
    _no_secrets(tree_body)
    assert tree_body["familyId"] == demo.DEMO_FAMILY_ID
    assert tree_body["familyName"] == demo.DEMO_FAMILY_NAME
    assert sorted(p["id"] for p in tree_body["people"]) == sorted(demo.DEMO_PERSON_IDS)

    book = client.get(f"/families/{demo.DEMO_FAMILY_ID}/people/dad/book")
    assert book.status_code == 200
    book_body = book.json()
    _no_secrets(book_body)
    assert book_body["person"]["id"] == "dad"
    years = [m["year"] for m in book_body["memories"]]
    assert years == sorted(years)
    hymn = next(m for m in book_body["memories"] if m["id"] == demo.HYMN_MEMORY_ID)
    assert [t["authorId"] for t in hymn["tellings"]] == ["dad", "mom"]
    assert hymn["screenplay"] is not None
    assert "hymn_books" in [p["id"] for p in book_body["props"]]
    assert "church_steps_1988" in [s["id"] for s in book_body["sets"]]

    # A wrong password is a 401, and logging out ends the session.
    bad = TestClient(client.app).post(
        "/auth/login", json={"email": "david@demo.stitch", "password": "wrong-password"}
    )
    assert bad.status_code == 401
    assert client.post("/auth/logout").status_code == 204
    client.cookies.clear()
    assert client.get("/me").status_code == 401


def test_media_full_and_range_reads_through_the_router(api):
    client, db, _other_media = api
    resp = client.post("/auth/login", json={"email": "ana@demo.stitch", "password": demo.DEMO_PASSWORD})
    assert resp.status_code == 200

    photo_ids = _demo_photo_ids(db)
    assert len(photo_ids) == 2
    # The seeded road-trip telling links exactly these photos.
    trip = db["memories"].find_one({"_id": "demo_green_sedan_trip"})
    assert sorted(trip["tellings"][0]["photoIds"]) == sorted(photo_ids)

    photo_id = photo_ids[0]
    stored = media_db.get_media(db, demo.DEMO_FAMILY_ID, photo_id)
    assert stored is not None

    full = client.get(f"/families/{demo.DEMO_FAMILY_ID}/media/{photo_id}")
    assert full.status_code == 200
    assert full.headers["content-type"] == "image/png"
    assert full.headers["accept-ranges"] == "bytes"
    assert int(full.headers["content-length"]) == stored.length
    assert full.content == stored.data
    assert full.content.startswith(b"\x89PNG\r\n\x1a\n")

    part = client.get(f"/families/{demo.DEMO_FAMILY_ID}/media/{photo_id}", headers={"Range": "bytes=0-99"})
    assert part.status_code == 206
    assert part.headers["content-range"] == f"bytes 0-99/{stored.length}"
    assert part.content == stored.data[:100]

    tail = client.get(f"/families/{demo.DEMO_FAMILY_ID}/media/{photo_id}", headers={"Range": "bytes=-16"})
    assert tail.status_code == 206
    assert tail.content == stored.data[-16:]


def test_other_family_media_is_404_through_the_router(api):
    client, _db, other_media = api
    resp = client.post("/auth/login", json={"email": "david@demo.stitch", "password": demo.DEMO_PASSWORD})
    assert resp.status_code == 200

    # Another family's id under David's own family reads like an id that does not exist ...
    leak = client.get(f"/families/{demo.DEMO_FAMILY_ID}/media/{other_media}")
    assert leak.status_code == 404
    assert leak.json() == {"detail": "Not found"}
    assert b"other-family-secret-bytes" not in leak.content

    # ... and the other family's own path is closed to a non-member.
    direct = client.get(f"/families/{OTHER_FAMILY}/media/{other_media}")
    assert direct.status_code == 404
    assert client.get(f"/families/{OTHER_FAMILY}/tree").status_code == 404
    assert client.get(f"/families/{OTHER_FAMILY}").status_code == 404
