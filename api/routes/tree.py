"""Read routes: tree, books, memories, threads, media (ARCHITECTURE.md section 6, 'Family content').

Every route depends on `auth.family_member` (path parameter `fid`), so a non-member gets
404 before any data is read, and every db call is scoped to that family: another
family's person, memory or media id reads exactly like an id that does not exist.

Read routes never import `agent/` (ARCHITECTURE.md section 7).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pymongo.database import Database

import schemas
from auth import FamilyAccess, family_member
from db import families as families_db
from db import media as media_db
from db import memories as memories_db
from db import people as people_db
from db import props as props_db
from db.client import get_db

router = APIRouter(tags=["tree"])

NOT_FOUND = "Not found"
CACHE_CONTROL = "private, max-age=86400"
# Media is served from the API origin, so a stored file must never run as a page there.
# Images, audio and video still render when opened directly.
MEDIA_CSP = "default-src 'none'; img-src 'self'; media-src 'self'; style-src 'unsafe-inline'; sandbox"


def _not_found() -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NOT_FOUND)


def _dedupe(ids: Iterable[str | None]) -> list[str]:
    """Drop empties and repeats, keeping first-seen order."""
    return list(dict.fromkeys(i for i in ids if i))


def _scene_bundle(
    db: Database,
    family_id: str,
    memories: list[schemas.Memory],
    *,
    extra_person_ids: Sequence[str] = (),
) -> tuple[list[schemas.Prop], list[schemas.SetDef], list[schemas.Person]]:
    """Everything the stage needs to play these memories: their props, sets and people.

    People are each memory's participants and tellers (then `extra_person_ids`, e.g. the
    book owner). Props are each memory's `propIds`, then any signature item prop worn by
    those people (`character.itemPropId`) so their puppets can be drawn. Ids are deduped in
    first-seen order; unknown or other-family ids are skipped by the db layer. Props
    include built-in library props.
    """
    person_ids = _dedupe(
        pid
        for memory in memories
        for pid in [*memory.participant_ids, *(t.author_id for t in memory.tellings)]
    )
    person_ids = _dedupe([*person_ids, *extra_person_ids])
    people = people_db.get_people(db, family_id, person_ids)

    prop_ids = _dedupe(pid for memory in memories for pid in memory.prop_ids)
    prop_ids = _dedupe([*prop_ids, *(p.character.item_prop_id for p in people)])
    set_ids = _dedupe(sid for memory in memories for sid in memory.set_ids)

    props = props_db.get_props(db, family_id, prop_ids)
    sets = props_db.get_sets(db, family_id, set_ids)
    return props, sets, people


# ------------------------------------------------------------------------- tree / threads


@router.get("/families/{fid}/tree", response_model=schemas.TreeResponse)
def get_tree(
    access: FamilyAccess = Depends(family_member),
    db: Database = Depends(get_db),
) -> schemas.TreeResponse:
    """Everyone in the family, ordered by generation, birth year, then name."""
    family = families_db.get_family(db, access.family_id)
    if family is None:
        raise _not_found()
    return schemas.TreeResponse(
        family_id=family.id,
        family_name=family.name,
        people=people_db.get_tree(db, access.family_id),
    )


@router.get("/families/{fid}/threads", response_model=schemas.ThreadsResponse)
def get_threads(
    access: FamilyAccess = Depends(family_member),
    db: Database = Depends(get_db),
) -> schemas.ThreadsResponse:
    """The threads screen: every person (with their thread color) and every moment by year."""
    return schemas.ThreadsResponse(
        people=people_db.person_summaries(db, access.family_id),
        moments=memories_db.moment_summaries(db, access.family_id),
    )


# ------------------------------------------------------------------------- books / memories


@router.get("/families/{fid}/people/{person_id}/book", response_model=schemas.BookResponse)
def get_book(
    person_id: str,
    access: FamilyAccess = Depends(family_member),
    db: Database = Depends(get_db),
) -> schemas.BookResponse:
    """A person's book: their memories ordered by year, with the props, sets and people to play them."""
    person = people_db.get_person(db, access.family_id, person_id)
    if person is None:
        raise _not_found()
    memories = memories_db.get_book(db, access.family_id, person.id)
    props, sets, people = _scene_bundle(db, access.family_id, memories, extra_person_ids=[person.id])
    return schemas.BookResponse(person=person, memories=memories, props=props, sets=sets, people=people)


@router.get("/families/{fid}/memories/{memory_id}", response_model=schemas.MemoryResponse)
def get_memory(
    memory_id: str,
    access: FamilyAccess = Depends(family_member),
    db: Database = Depends(get_db),
) -> schemas.MemoryResponse:
    """One memory with all its tellings, plus the props, sets and people to play it."""
    memory = memories_db.get_memory(db, access.family_id, memory_id)
    if memory is None:
        raise _not_found()
    props, sets, people = _scene_bundle(db, access.family_id, [memory])
    return schemas.MemoryResponse(memory=memory, props=props, sets=sets, people=people)


# ------------------------------------------------------------------------------ media

_RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")


class _Unsatisfiable(Exception):
    pass


def _parse_range(header: str | None, length: int) -> tuple[int, int] | None:
    """The inclusive (start, end) of a single byte range, or None to send the whole file.

    Malformed headers, other units and multiple ranges are ignored (RFC 9110 lets a server
    ignore Range). Raises _Unsatisfiable when the range is well formed but outside the file.
    """
    if not header:
        return None
    match = _RANGE_RE.match(header.strip().replace(" ", ""))
    if match is None:
        return None
    first, last = match.groups()
    if first:
        start = int(first)
        end = int(last) if last else length - 1
        if last and end < start:
            return None  # invalid range-spec: ignore the header
        if start >= length:
            raise _Unsatisfiable
        return start, min(end, length - 1)
    if not last:
        return None  # "bytes=-"
    suffix = int(last)
    if suffix == 0 or length == 0:
        raise _Unsatisfiable
    return max(0, length - suffix), length - 1


def _etag_matches(header: str | None, etag: str) -> bool:
    """If-None-Match comparison (weak), including `*` and comma-separated lists."""
    if not header:
        return False
    for candidate in header.split(","):
        candidate = candidate.strip()
        if candidate == "*" or candidate.removeprefix("W/") == etag:
            return True
    return False


def _content_disposition(filename: str | None) -> str:
    """`inline`, with an ASCII-safe quoted filename and an RFC 5987 UTF-8 one when needed."""
    if not filename:
        return "inline"
    name = "".join(ch for ch in filename if ch >= " " and ch != "\x7f").strip()
    name = name.replace("/", "_").replace("\\", "_")
    if not name:
        return "inline"
    ascii_name = "".join(ch if ch.isascii() and ch != '"' else "_" for ch in name)
    value = f'inline; filename="{ascii_name}"'
    if ascii_name != name:
        value += f"; filename*=UTF-8''{quote(name, safe='')}"
    return value


def _requested_media_id(request: Request, raw_id: str) -> str | None:
    """The bare id from the path. A 'media/<id>' form is accepted only when sent as media%2F<id>."""
    if "/" in raw_id:
        raw_path = request.scope.get("raw_path")
        if isinstance(raw_path, bytes) and b"media%2f" not in raw_path.lower():
            return None
    return media_db.parse_media_ref(raw_id)


@router.get(
    "/families/{fid}/media/{media_id:path}",
    response_class=Response,
    responses={
        200: {"description": "The file", "content": {"application/octet-stream": {}}},
        206: {"description": "A single byte range of the file"},
        304: {"description": "Not modified (If-None-Match)"},
        404: {"model": schemas.ErrorResponse},
        416: {"description": "Range not satisfiable"},
    },
)
def get_media(
    media_id: str,
    request: Request,
    access: FamilyAccess = Depends(family_member),
    db: Database = Depends(get_db),
) -> Response:
    """A photo, cutout or narration audio file from GridFS. Supports one byte range and ETags."""
    bare_id = _requested_media_id(request, media_id)
    file = media_db.get_media(db, access.family_id, bare_id) if bare_id else None
    if file is None:
        raise _not_found()

    etag = f'"{file.id}"'
    data = file.data
    length = len(data)
    headers = {
        "Cache-Control": CACHE_CONTROL,
        "ETag": etag,
        "X-Content-Type-Options": "nosniff",
        "Accept-Ranges": "bytes",
        "Content-Disposition": _content_disposition(file.filename),
        "Content-Security-Policy": MEDIA_CSP,
    }

    if _etag_matches(request.headers.get("if-none-match"), etag):
        not_modified = {k: headers[k] for k in ("Cache-Control", "ETag")}
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=not_modified)

    range_header = request.headers.get("range")
    if_range = request.headers.get("if-range")
    if if_range is not None and if_range.strip() != etag:
        range_header = None  # the client's copy is stale: send the whole file

    try:
        byte_range = _parse_range(range_header, length)
    except _Unsatisfiable:
        return Response(
            status_code=status.HTTP_416_RANGE_NOT_SATISFIABLE,
            headers={**headers, "Content-Range": f"bytes */{length}", "Content-Length": "0"},
        )

    if byte_range is None:
        return Response(
            content=data,
            media_type=file.content_type,
            headers={**headers, "Content-Length": str(length)},
        )

    start, end = byte_range
    chunk = data[start : end + 1]
    return Response(
        content=chunk,
        status_code=status.HTTP_206_PARTIAL_CONTENT,
        media_type=file.content_type,
        headers={
            **headers,
            "Content-Range": f"bytes {start}-{end}/{length}",
            "Content-Length": str(len(chunk)),
        },
    )
