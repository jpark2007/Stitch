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

import io
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, get_args

import gridfs
from bson import ObjectId
from bson.errors import InvalidId
from gridfs.errors import CorruptGridFile, NoFile
from PIL import Image
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError

MediaKind = Literal["photo", "cutout", "audio", "frame", "other"]
MEDIA_KINDS: frozenset[str] = frozenset(get_args(MediaKind))
MAX_BYTES = 20 * 1024 * 1024

BUCKET = "media"
FILES = f"{BUCKET}.files"
CHUNKS = f"{BUCKET}.chunks"
INLINE = f"{BUCKET}.inline"
REF_PREFIX = "media/"

# EXIF Orientation tag, and the values (5-8) whose upright image swaps width and height.
_EXIF_ORIENTATION = 0x0112
_ROTATED_ORIENTATIONS = frozenset({5, 6, 7, 8})


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
    media_id: str | None = None,
) -> str:
    """Store bytes for a family and return the new bare media id.

    `media_id` (a bare ObjectId string or 'media/<id>') stores the file under that id
    instead of a fresh one, so a seed can write the same ids on every run; ValueError when
    it is not a valid ObjectId or a file with that id already exists (in any family).

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

    fixed: ObjectId | None = None
    if media_id is not None:
        fixed = _oid(media_id)
        if fixed is None:
            raise ValueError(f"media_id is not a valid id: {media_id!r}")
        collection = INLINE if _is_mock(db) else FILES
        if db[collection].count_documents({"_id": fixed}, limit=1):
            raise ValueError(f"media id already exists: {fixed}")

    metadata = _metadata(family_id, kind, content_type)
    if _is_mock(db):
        oid = fixed or ObjectId()
        try:
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
        except DuplicateKeyError as exc:
            raise ValueError(f"media id already exists: {oid}") from exc
        return str(oid)

    if fixed is not None:
        _bucket(db).upload_from_stream_with_id(fixed, filename or "", data, metadata=metadata)
        return str(fixed)
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
    except (NoFile, CorruptGridFile):
        # NoFile: deleted between the two reads. CorruptGridFile: the file document is
        # there but chunks are missing or the wrong size (an interrupted upload or
        # delete). Either way there is no file to serve, not a server error.
        return None
    return _to_media_file(doc, data)


def image_aspect(db: Database, family_id: str, media_id: str) -> float | None:
    """Displayed width / height of a family's image, rounded to 4 decimals, or None.

    Accepts a bare id or 'media/<id>'. EXIF orientation is applied, so a phone portrait
    photo stored as landscape pixels with orientation 6 gives its upright ratio. None,
    never an exception, when the media is missing or another family's, the bytes are not
    a decodable image, or a dimension is zero.
    """
    found = get_media(db, family_id, media_id)
    if found is None:
        return None
    try:
        with Image.open(io.BytesIO(found.data)) as img:
            width, height = img.size
            try:
                orientation = int(img.getexif().get(_EXIF_ORIENTATION) or 1)
            except Exception:  # noqa: BLE001 - a broken EXIF block means "upright"
                orientation = 1
    except Exception:  # noqa: BLE001 - any undecodable upload means "unknown aspect"
        return None
    if orientation in _ROTATED_ORIENTATIONS:
        width, height = height, width
    if width <= 0 or height <= 0:
        return None
    return round(width / height, 4)


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


def delete_family_media(db: Database, family_id: str) -> int:
    """Delete every file of one family, in both backends. Returns how many files went.

    Covers the inline store (mongomock) and the GridFS bucket (files and their chunks),
    each filtered by `metadata.familyId`, so a database written by either backend is
    cleaned. ValueError on a blank family_id, which would otherwise match nothing useful.

    File documents go before their chunks, as GridFSBucket.delete does: if this stops
    halfway, what is left is unreachable chunks (garbage), never a file document that
    still lists as existing but cannot be read.
    """
    if not isinstance(family_id, str) or not family_id.strip():
        raise ValueError("family_id is required")
    owned = {"metadata.familyId": family_id}
    deleted = db[INLINE].delete_many(owned).deleted_count
    file_ids = [doc["_id"] for doc in db[FILES].find(owned, {"_id": 1})]
    if file_ids:
        deleted += db[FILES].delete_many({"_id": {"$in": file_ids}, **owned}).deleted_count
        db[CHUNKS].delete_many({"files_id": {"$in": file_ids}})
    return deleted
