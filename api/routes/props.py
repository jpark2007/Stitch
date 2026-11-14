"""Saved props and sets, prop review edits, and photo uploads (ARCHITECTURE.md section 6).

Every route depends on `auth.family_member`, so another family's fid is a 404, and every
db call is scoped to that fid, so another family's prop or photo id behaves like an
unknown one. Only `db/` talks to MongoDB; this module never imports `agent/`.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from pymongo.database import Database
from starlette.concurrency import run_in_threadpool

import auth
import schemas
from db import media as media_db
from db import props as props_db
from db.client import get_db

router = APIRouter(tags=["props"])

# What the upload accepts: the photo formats phones and browsers produce. Kept equal to
# routes.memories.ALLOWED_PHOTO_TYPES (a test asserts it) without importing that module,
# so a photo that works on a memory also works as a prop's art.
ALLOWED_IMAGE_TYPES: frozenset[str] = frozenset(
    {"image/jpeg", "image/png", "image/webp", "image/gif", "image/heic", "image/heif"}
)


@router.get("/families/{fid}/props", response_model=schemas.PropsResponse)
def list_props(
    access: auth.FamilyAccess = Depends(auth.family_member),
    db: Database = Depends(get_db),
) -> schemas.PropsResponse:
    """The family's saved props (library props are not listed), ordered by name."""
    return schemas.PropsResponse(props=props_db.list_props(db, access.family_id))


@router.get("/families/{fid}/sets", response_model=schemas.SetsResponse)
def list_sets(
    access: auth.FamilyAccess = Depends(auth.family_member),
    db: Database = Depends(get_db),
) -> schemas.SetsResponse:
    """The family's saved sets, ordered by name."""
    return schemas.SetsResponse(sets=props_db.list_sets(db, access.family_id))


def _not_found() -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")


@router.patch("/families/{fid}/props/{prop_id}", response_model=schemas.Prop)
def patch_prop(
    prop_id: str,
    patch: schemas.PropPatch,
    access: auth.FamilyAccess = Depends(auth.family_member),
    db: Database = Depends(get_db),
) -> schemas.Prop:
    """Rename a saved prop and/or replace its art with an uploaded photo.

    422: neither field set, or a blank name. 404: a library prop, an unknown id, or another
    family's prop. 400 "Unknown photo": photo_id is not one of this family's media ids.
    The stored art uses the 'media/<id>' form whichever form the client sent.
    """
    fid = access.family_id
    if patch.name is None and patch.photo_id is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Nothing to change"
        )
    name: str | None = None
    if patch.name is not None:
        name = patch.name.strip()
        if not name:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Name must not be blank"
            )

    # Only the family's own saved props can be edited. get_prop also returns library
    # props (family_id None), which are shared and read-only, so they are a 404 too.
    current = props_db.get_prop(db, fid, prop_id)
    if current is None or current.family_id != fid:
        raise _not_found()

    photo_ref: str | None = None
    if patch.photo_id is not None:
        bare = media_db.parse_media_ref(patch.photo_id)
        if bare is None or not media_db.media_exists(db, fid, bare):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unknown photo")
        photo_ref = media_db.media_ref(bare)

    updated = props_db.update_prop(
        db, fid, prop_id, schemas.PropPatch(name=name, photo_id=photo_ref)
    )
    if updated is None:  # deleted between the check and the update
        raise _not_found()
    return updated


def _size_label(n: int) -> str:
    mb = 1024 * 1024
    return f"{n // mb} MB" if n >= mb and n % mb == 0 else f"{n} bytes"


@router.post(
    "/families/{fid}/media",
    status_code=status.HTTP_201_CREATED,
    response_model=schemas.MediaUploadResponse,
)
async def upload_media(
    file: UploadFile = File(...),
    access: auth.FamilyAccess = Depends(auth.family_member),
    db: Database = Depends(get_db),
) -> schemas.MediaUploadResponse:
    """Upload a photo for this family. Returns {"id": <bare media id>}.

    415 for anything but a JPEG, PNG, WebP, GIF or HEIC/HEIF image; 413 over db.media.MAX_BYTES;
    400 for an empty file. The bytes land in GridFS tagged with the family id.
    """
    content_type = (file.content_type or "").split(";", 1)[0].strip().lower()
    if content_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Upload a JPEG, PNG, WebP, GIF or HEIC/HEIF image",
        )
    limit = media_db.MAX_BYTES  # read at call time so tests can lower it
    try:
        data = await file.read(limit + 1)
    finally:
        await file.close()
    if len(data) > limit:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"Photos must be {_size_label(limit)} or smaller",
        )
    if not data:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="The file is empty")

    media_id = await run_in_threadpool(
        media_db.put_media,
        db,
        access.family_id,
        data,
        content_type=content_type,
        filename=file.filename,
        kind="photo",
    )
    return schemas.MediaUploadResponse(id=media_id)
