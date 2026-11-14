"""db/media.py: family-scoped media on the mongomock fallback and on real GridFS."""

from __future__ import annotations

import os
import uuid

import pytest
from bson import ObjectId

from db import media
from db.media import (
    MAX_BYTES,
    MediaFile,
    delete_media,
    get_media,
    media_exists,
    media_ref,
    parse_media_ref,
    put_media,
)

FAMILY_A = "family_a"
FAMILY_B = "family_b"
PNG = b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 4


@pytest.fixture
def mdb():
    mongomock = pytest.importorskip("mongomock")
    return mongomock.MongoClient(tz_aware=True)["stitch_test"]


@pytest.fixture
def real_db():
    uri = os.environ.get("MONGODB_URI_TEST")
    if not uri:
        pytest.skip("MONGODB_URI_TEST is not set")
    from pymongo import MongoClient

    client = MongoClient(uri, serverSelectionTimeoutMS=3000, tz_aware=True)
    name = f"stitch_test_media_{uuid.uuid4().hex[:8]}"
    try:
        client.admin.command("ping")
    except Exception as exc:  # noqa: BLE001
        client.close()
        pytest.skip(f"MongoDB at MONGODB_URI_TEST is unreachable: {exc}")
    try:
        yield client[name]
    finally:
        client.drop_database(name)
        client.close()


# ------------------------------------------------------------------ refs

def test_media_ref_round_trip():
    oid = str(ObjectId())
    assert media_ref(oid) == f"media/{oid}"
    assert media_ref(f"media/{oid}") == f"media/{oid}"
    assert parse_media_ref(f"media/{oid}") == oid
    assert parse_media_ref(oid) == oid


@pytest.mark.parametrize("bad", ["", "media/", "media/a1", "not-an-id", "media/zz" * 6, "photos/" + "a" * 24])
def test_parse_media_ref_rejects_invalid(bad):
    assert parse_media_ref(bad) is None


# ------------------------------------------------------------------ mongomock fallback

def test_put_get_round_trip(mdb):
    media_id = put_media(mdb, FAMILY_A, PNG, content_type="image/png", filename="steps.png", kind="cutout")
    assert isinstance(media_id, str) and ObjectId.is_valid(media_id)

    got = get_media(mdb, FAMILY_A, media_id)
    assert isinstance(got, MediaFile)
    assert got == MediaFile(
        id=media_id,
        family_id=FAMILY_A,
        content_type="image/png",
        filename="steps.png",
        kind="cutout",
        length=len(PNG),
        data=PNG,
    )


def test_defaults_to_photo_without_filename(mdb):
    media_id = put_media(mdb, FAMILY_A, b"jpeg-bytes", content_type="image/jpeg")
    got = get_media(mdb, FAMILY_A, media_id)
    assert got is not None
    assert got.kind == "photo"
    assert got.filename is None


def test_fallback_stores_familyid_metadata(mdb):
    media_id = put_media(mdb, FAMILY_A, b"abc", content_type="audio/mpeg", kind="audio")
    doc = mdb["media.inline"].find_one({"_id": ObjectId(media_id)})
    assert doc["metadata"] == {"familyId": FAMILY_A, "kind": "audio", "contentType": "audio/mpeg"}


def test_media_ref_form_is_accepted(mdb):
    media_id = put_media(mdb, FAMILY_A, b"abc", content_type="image/png")
    ref = media_ref(media_id)
    got = get_media(mdb, FAMILY_A, ref)
    assert got is not None and got.id == media_id and got.data == b"abc"
    assert media_exists(mdb, FAMILY_A, ref)
    assert delete_media(mdb, FAMILY_A, ref)
    assert get_media(mdb, FAMILY_A, media_id) is None


@pytest.mark.parametrize("bad", ["", "nope", "media/a1", "123", "media/" + "g" * 24])
def test_invalid_id_returns_none(mdb, bad):
    assert get_media(mdb, FAMILY_A, bad) is None
    assert media_exists(mdb, FAMILY_A, bad) is False
    assert delete_media(mdb, FAMILY_A, bad) is False


def test_unknown_id_returns_none(mdb):
    unknown = str(ObjectId())
    assert get_media(mdb, FAMILY_A, unknown) is None
    assert media_exists(mdb, FAMILY_A, unknown) is False
    assert delete_media(mdb, FAMILY_A, unknown) is False


def test_other_family_cannot_read_or_delete(mdb):
    b_id = put_media(mdb, FAMILY_B, b"secret", content_type="image/png")
    assert get_media(mdb, FAMILY_A, b_id) is None
    assert get_media(mdb, FAMILY_A, media_ref(b_id)) is None
    assert media_exists(mdb, FAMILY_A, b_id) is False
    assert delete_media(mdb, FAMILY_A, b_id) is False
    # Still there for its own family.
    assert get_media(mdb, FAMILY_B, b_id).data == b"secret"


def test_delete(mdb):
    media_id = put_media(mdb, FAMILY_A, b"abc", content_type="image/png")
    assert media_exists(mdb, FAMILY_A, media_id)
    assert delete_media(mdb, FAMILY_A, media_id) is True
    assert media_exists(mdb, FAMILY_A, media_id) is False
    assert get_media(mdb, FAMILY_A, media_id) is None
    assert delete_media(mdb, FAMILY_A, media_id) is False


def test_size_limit(mdb):
    with pytest.raises(ValueError):
        put_media(mdb, FAMILY_A, b"", content_type="image/png")
    with pytest.raises(ValueError):
        put_media(mdb, FAMILY_A, b"x" * (MAX_BYTES + 1), content_type="image/png")
    exactly = put_media(mdb, FAMILY_A, b"x" * MAX_BYTES, content_type="image/png")
    assert get_media(mdb, FAMILY_A, exactly).length == MAX_BYTES


def test_rejects_bad_arguments(mdb):
    with pytest.raises(ValueError):
        put_media(mdb, FAMILY_A, b"x", content_type="image/png", kind="poster")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        put_media(mdb, "", b"x", content_type="image/png")
    with pytest.raises(ValueError):
        put_media(mdb, FAMILY_A, b"x", content_type="")
    with pytest.raises(TypeError):
        put_media(mdb, FAMILY_A, "not bytes", content_type="text/plain")  # type: ignore[arg-type]


def test_mongomock_is_detected(mdb):
    assert media._is_mock(mdb) is True


# ------------------------------------------------------------------ real GridFS

def test_real_gridfs_round_trip_and_family_scope(real_db):
    assert media._is_mock(real_db) is False
    media_id = put_media(real_db, FAMILY_A, PNG, content_type="image/png", filename="a.png", kind="photo")

    files_doc = real_db["media.files"].find_one({"_id": ObjectId(media_id)})
    assert files_doc["metadata"] == {"familyId": FAMILY_A, "kind": "photo", "contentType": "image/png"}
    assert real_db["media.chunks"].count_documents({"files_id": ObjectId(media_id)}) >= 1

    got = get_media(real_db, FAMILY_A, media_ref(media_id))
    assert got == MediaFile(
        id=media_id, family_id=FAMILY_A, content_type="image/png", filename="a.png",
        kind="photo", length=len(PNG), data=PNG,
    )
    assert get_media(real_db, FAMILY_B, media_id) is None
    assert media_exists(real_db, FAMILY_B, media_id) is False
    assert delete_media(real_db, FAMILY_B, media_id) is False
    assert get_media(real_db, FAMILY_A, "media/a1") is None

    assert delete_media(real_db, FAMILY_A, media_id) is True
    assert get_media(real_db, FAMILY_A, media_id) is None
    assert real_db["media.chunks"].count_documents({"files_id": ObjectId(media_id)}) == 0
