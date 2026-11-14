"""Photos, cutouts and narration audio in GridFS (ARCHITECTURE.md sections 4 and 5).

Every file carries `metadata.familyId`, and every read goes through the family check:
an unknown id, a malformed id and another family's id all look the same (None/False).
Media is never served by a public URL; routes call `get_media` after `family_member`.

Ids are bare ObjectId strings. Contracts reference media as 'media/<id>' (for example
`Prop.art` or a set layer's `art`); `get_media`, `media_exists` and `delete_media` accept
both forms.

Backends:
- Real pymongo databases use `gridfs.GridFSBucket(db, bucket_name='media')`, so files land
  in `media.files` / `media.chunks` with metadata `{familyId, kind, contentType}`.
- mongomock databases (tests) use a private inline store, the `media.inline` collection,
  with the same metadata shape and the same public behaviour. mongomock's GridFS
  integration is broken with pymongo 4.20, so the real bucket cannot be used there.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, get_args

import gridfs
from bson import ObjectId
from bson.errors import InvalidId
from gridfs.errors import NoFile
from pymongo.database import Database

MediaKind = Literal["photo", "cutout", "audio", "frame", "other"]
MEDIA_KINDS: frozenset[str] = frozenset(get_args(MediaKind))
MAX_BYTES = 20 * 1024 * 1024

BUCKET = "media"
FILES = f"{BUCKET}.files"
INLINE = f"{BUCKET}.inline"
REF_PREFIX = "media/"


@dataclass(frozen=True)
class MediaFile:
    id: str
    family_id: str
    content_type: str
    filename: str | None
    kind: str
    length: int
    data: bytes


# ------------------------------------------------------------------ references

def media_ref(media_id: str) -> str:
    """'abc' -> 'media/abc'. An existing 'media/<id>' is returned unchanged."""
    media_id = str(media_id)
    return media_id if media_id.startswith(REF_PREFIX) else f"{REF_PREFIX}{media_id}"


def parse_media_ref(ref: str) -> str | None:
    """'media/<id>' or a bare '<id>' -> '<id>'. None unless <id> is a valid ObjectId."""
    if not isinstance(ref, str):
        return None
    media_id = ref.strip().removeprefix(REF_PREFIX)
    return media_id if ObjectId.is_valid(media_id) else None


def _oid(media_id: str) -> ObjectId | None:
    parsed = parse_media_ref(media_id)
    if parsed is None:
        return None
    try:
        return ObjectId(parsed)
    except (InvalidId, TypeError):
        return None


def _is_mock(db: Database) -> bool:
    return type(db).__module__.startswith("mongomock")


def _bucket(db: Database) -> gridfs.GridFSBucket:
    return gridfs.GridFSBucket(db, bucket_name=BUCKET)


def _metadata(family_id: str, kind: str, content_type: str) -> dict:
    return {"familyId": family_id, "kind": kind, "contentType": content_type}


def _file_doc(db: Database, family_id: str, oid: ObjectId) -> dict | None:
    """The file document for this id, only if it belongs to family_id."""
    collection = INLINE if _is_mock(db) else FILES
    return db[collection].find_one({"_id": oid, "metadata.familyId": family_id})


def _to_media_file(doc: dict, data: bytes) -> MediaFile:
    meta = doc.get("metadata") or {}
    return MediaFile(
        id=str(doc["_id"]),
        family_id=meta.get("familyId", ""),
        content_type=meta.get("contentType") or "application/octet-stream",
        filename=doc.get("filename") or None,
        kind=meta.get("kind") or "other",
        length=int(doc.get("length", len(data))),
        data=data,
    )


# ------------------------------------------------------------------ public API

def put_media(
    db: Database,
    family_id: str,
    data: bytes,
    *,
    content_type: str,
    filename: str | None = None,
    kind: MediaKind = "photo",
) -> str:
    """Store bytes for a family and return the new bare media id.

    Raises ValueError when data is empty or larger than MAX_BYTES, when family_id or
    content_type is empty, or when kind is not a MediaKind. TypeError when data is not bytes.
    """
    if not family_id:
        raise ValueError("family_id is required")
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise TypeError("data must be bytes")
    data = bytes(data)
    if not data:
        raise ValueError("media is empty")
    if len(data) > MAX_BYTES:
        raise ValueError(f"media is larger than {MAX_BYTES} bytes")
    if not content_type:
        raise ValueError("content_type is required")
    if kind not in MEDIA_KINDS:
        raise ValueError(f"unknown media kind: {kind!r}")

    metadata = _metadata(family_id, kind, content_type)
    if _is_mock(db):
        oid = ObjectId()
        db[INLINE].insert_one(
            {
                "_id": oid,
                "filename": filename,
                "length": len(data),
                "uploadDate": datetime.now(UTC),
                "metadata": metadata,
                "data": data,
            }
        )
        return str(oid)

    oid = _bucket(db).upload_from_stream(filename or "", data, metadata=metadata)
    return str(oid)


def get_media(db: Database, family_id: str, media_id: str) -> MediaFile | None:
    """The file with its bytes, or None for a bad, unknown or other-family id."""
    oid = _oid(media_id)
    if oid is None:
        return None
    doc = _file_doc(db, family_id, oid)
    if doc is None:
        return None
    if _is_mock(db):
        return _to_media_file(doc, bytes(doc.get("data") or b""))
    try:
        with _bucket(db).open_download_stream(oid) as stream:
            data = stream.read()
    except NoFile:
        return None
    return _to_media_file(doc, data)


def media_exists(db: Database, family_id: str, media_id: str) -> bool:
    oid = _oid(media_id)
    return oid is not None and _file_doc(db, family_id, oid) is not None


def delete_media(db: Database, family_id: str, media_id: str) -> bool:
    """Delete a family's file. False when it does not exist or belongs to another family."""
    oid = _oid(media_id)
    if oid is None or _file_doc(db, family_id, oid) is None:
        return False
    if _is_mock(db):
        result = db[INLINE].delete_one({"_id": oid, "metadata.familyId": family_id})
        return result.deleted_count == 1
    try:
        _bucket(db).delete(oid)
    except NoFile:
        return False
    return True
