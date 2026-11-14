"""Submit a memory, stream the agent run, answer follow-ups (ARCHITECTURE.md section 6).

Routes:
- POST /families/{fid}/memories               multipart submit; starts an agent run (202)
- GET  /families/{fid}/runs/{run_id}/stream   server-sent events for that run
- POST /families/{fid}/runs/{run_id}/answer   answer a follow-up, review props, approve a merge (204)

Every route depends on `auth.family_member`, so a caller who is not in family `fid` gets
404 before the agent is touched.

The agent is reached ONLY through `agent.runs`, imported lazily inside each handler so
that importing this module, and serving read routes, never imports the agent package:

- `start_run(family_id, user_id, person_id, fields: schemas.SubmitMemoryFields,
  photo_ids: list[str]) -> str` returns the run id at once (the run continues on the
  agent's own worker), so it is called on the event loop (and awaited if it returns an
  awaitable). It raises ValueError when the person is not in the family (422) and
  `MemoryNotFound(LookupError)` when `memory_id` is missing or has no scene yet (404).
  Anything else, including the plain RuntimeError `get_manager()` raises when no run
  manager is configured, is 503 START_FAILED. Stored photos are deleted on every failure.
- `stream_run(family_id, run_id) -> AsyncIterator[(event name, model)]` is called and
  iterated on the event loop. `RunNotFound(LookupError)` (unknown or other-family run)
  or PermissionError at the call or on the first item is 404; any other exception at the
  call is 503 STUDIO_UNAVAILABLE. Each item becomes `event: <name>` with
  `data: <model JSON by alias>`, `name` one of `schemas.RUN_EVENTS`.
- `answer_run(family_id, run_id, user_id, answer: schemas.AnswerRequest) -> None` may
  block (up to a few seconds on a threading.Condition while a question settles), so a
  sync one runs in the threadpool; a coroutine function is awaited. `RunNotFound` is 404,
  `NotRunOwner(PermissionError)` 403, `RunNotWaiting(RuntimeError)` 409 NOT_WAITING, a
  ValueError (the answer does not fit the pending question) 409, and anything else,
  including the not-configured RuntimeError, 503 STUDIO_UNAVAILABLE.
"""

from __future__ import annotations

import asyncio
import importlib
import inspect
import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Annotated, Any

import anyio
from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from pydantic import ValidationError
from pymongo.database import Database

import schemas
from auth import FamilyAccess, family_member
from db import media as media_db
from db import memories as memories_db
from db import people as people_db
from db.client import get_db

log = logging.getLogger(__name__)

router = APIRouter(tags=["memories"])

MIN_YEAR = 1800
MAX_PHOTOS = 10
ALLOWED_PHOTO_TYPES: frozenset[str] = frozenset(
    {"image/jpeg", "image/png", "image/webp", "image/gif", "image/heic", "image/heif"}
)
# Seconds without an event before the stream sends a `: ping` comment. Monkeypatched in tests.
HEARTBEAT_SECONDS: float = 15.0

NO_PERSON = "Add or claim your person in this family first"
START_FAILED = "Could not start the studio"
STUDIO_UNAVAILABLE = "The studio is unavailable"
RUN_NOT_FOUND = "Run not found"
NOT_RUN_OWNER = "Only the person who started this run can answer"
NOT_WAITING = "This run is not waiting for an answer"
ANSWER_CONFLICT = "Run cannot take that answer"
STREAM_INVALID = "Internal stream error"
STREAM_FAILED = "Something went wrong while making this memory"

SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
    "Connection": "keep-alive",
}


async def _maybe_await(value: Any) -> Any:
    """Await value if the seam returned an awaitable (sync and async seams both work)."""
    if inspect.isawaitable(value):
        return await value
    return value


def _blank_to_none(value: str | None) -> str | None:
    if value is None:
        return None
    return value if value.strip() else None


def _dedupe(ids: list[str]) -> list[str]:
    seen: dict[str, None] = {}
    for item in ids:
        if item and item.strip():
            seen.setdefault(item.strip(), None)
    return list(seen)


def _unprocessable(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=detail)


def _photo_type(upload: UploadFile) -> str:
    return (upload.content_type or "").split(";", 1)[0].strip().lower()


def _photo_uploads(parts: list[UploadFile | str]) -> list[UploadFile]:
    """The real files among the `photos` parts.

    A browser submits an empty file input as a nameless, zero-byte part, which Starlette
    hands over as "" (or a nameless empty UploadFile); those are skipped. Any other text
    value under `photos` is a client error.
    """
    uploads: list[UploadFile] = []
    for part in parts:
        if isinstance(part, str):
            if part:
                raise _unprocessable("Photos must be uploaded as files")
            continue
        if not part.filename and part.size == 0:
            continue
        uploads.append(part)
    return uploads


# ------------------------------------------------------------------------------ submit


@router.post(
    "/families/{fid}/memories",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=schemas.SubmitMemoryResponse,
    responses={
        400: {"model": schemas.ErrorResponse},
        404: {"model": schemas.ErrorResponse},
        409: {"model": schemas.ErrorResponse},
        413: {"model": schemas.ErrorResponse},
        415: {"model": schemas.ErrorResponse},
        503: {"model": schemas.ErrorResponse},
    },
)
async def submit_memory(
    text: Annotated[str, Form()],
    year: Annotated[int, Form()],
    question: Annotated[str | None, Form()] = None,
    title: Annotated[str | None, Form()] = None,
    memory_id: Annotated[str | None, Form(alias="memoryId")] = None,
    participant_ids: Annotated[list[str], Form(alias="participantIds")] = [],  # noqa: B006 - FastAPI copies defaults
    photos: Annotated[list[UploadFile | str], File()] = [],  # noqa: B006
    access: FamilyAccess = Depends(family_member),
    db: Database = Depends(get_db),
) -> schemas.SubmitMemoryResponse:
    """Submit a telling (question, answer, year, photos). The author is the caller's person."""
    fid = access.family_id

    # 1. Fields.
    if not text.strip():
        raise _unprocessable("Write the memory before submitting it")
    max_year = datetime.now(UTC).year + 1
    if not MIN_YEAR <= year <= max_year:
        raise _unprocessable(f"Year must be between {MIN_YEAR} and {max_year}")
    try:
        fields = schemas.SubmitMemoryFields(
            text=text,
            year=year,
            question=_blank_to_none(question),
            title=_blank_to_none(title),
            memory_id=_blank_to_none(memory_id),
            participant_ids=_dedupe(participant_ids),
        )
    except ValidationError as exc:
        raise _unprocessable("Invalid memory fields") from exc

    # 2. The author, the moment being added to, and the participants all belong to this family.
    person = await run_in_threadpool(people_db.get_person_for_user, db, fid, access.user.id)
    if person is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=NO_PERSON)
    if fields.memory_id is not None:
        memory = await run_in_threadpool(memories_db.get_memory, db, fid, fields.memory_id)
        if memory is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Memory not found")
    if fields.participant_ids:
        found = await run_in_threadpool(people_db.get_people, db, fid, fields.participant_ids)
        if {p.id for p in found} != set(fields.participant_ids):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Every participant must be a person in this family",
            )

    # 3. Photos: validate every one before storing any.
    uploads = _photo_uploads(photos)
    if len(uploads) > MAX_PHOTOS:
        raise _unprocessable(f"Attach at most {MAX_PHOTOS} photos")
    for upload in uploads:
        if _photo_type(upload) not in ALLOWED_PHOTO_TYPES:
            raise HTTPException(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                detail="Photos must be JPEG, PNG, WebP, GIF or HEIC images",
            )
    payloads: list[tuple[UploadFile, bytes]] = []
    for upload in uploads:
        if upload.size is not None and upload.size > media_db.MAX_BYTES:
            raise HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail="A photo is too large")
        data = await upload.read(media_db.MAX_BYTES + 1)
        if len(data) > media_db.MAX_BYTES:
            raise HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail="A photo is too large")
        if not data:
            raise _unprocessable("A photo is empty")
        payloads.append((upload, data))

    # 4. Reach the agent seam (lazily), then store the photos and start the run.
    try:
        from agent.runs import start_run
    except ImportError:
        log.exception("agent.runs is not importable")
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=START_FAILED) from None

    photo_ids: list[str] = []
    try:
        for upload, data in payloads:
            photo_ids.append(
                await run_in_threadpool(
                    media_db.put_media,
                    db,
                    fid,
                    data,
                    content_type=_photo_type(upload),
                    filename=upload.filename,
                    kind="photo",
                )
            )
        run_id = await _maybe_await(start_run(fid, access.user.id, person.id, fields, list(photo_ids)))
        if not isinstance(run_id, str) or not run_id:
            raise RuntimeError(f"start_run returned {run_id!r}, expected a run id")
    except Exception as exc:
        await _delete_media(db, fid, photo_ids)
        if isinstance(exc, LookupError):
            log.info("start_run for family %s: %s", fid, exc)
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Memory not found") from None
        if isinstance(exc, ValueError):
            log.info("start_run for family %s refused the submit: %s", fid, exc)
            raise _unprocessable(str(exc) or "Invalid memory") from None
        log.exception("Could not start a run for family %s", fid)
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=START_FAILED) from None

    return schemas.SubmitMemoryResponse(run_id=run_id, memory_id=fields.memory_id)


async def _delete_media(db: Database, fid: str, media_ids: list[str]) -> None:
    for media_id in media_ids:
        try:
            await run_in_threadpool(media_db.delete_media, db, fid, media_id)
        except Exception:  # noqa: BLE001 - cleanup is best effort
            log.exception("Could not delete media %s after a failed submit", media_id)


# ------------------------------------------------------------------------------ stream


_END = object()  # sentinel: the agent's generator is exhausted


def _sse(name: str, model: schemas.Model) -> str:
    return f"event: {name}\ndata: {model.model_dump_json(by_alias=True)}\n\n"


def _error_frame(message: str) -> str:
    return _sse("error", schemas.ErrorEvent(message=message))


def _frame(item: Any) -> tuple[str, bool]:
    """(SSE text, is_final) for one item from the agent. Invalid items become an error event."""
    try:
        name, model = item
    except (TypeError, ValueError):
        log.error("Run stream produced a non-pair item: %r", item)
        return _error_frame(STREAM_INVALID), True
    expected = schemas.RUN_EVENTS.get(name) if isinstance(name, str) else None
    if expected is None or not isinstance(model, expected):
        log.error("Run stream produced an invalid event %r with %s", name, type(model).__name__)
        return _error_frame(STREAM_INVALID), True
    try:
        text = _sse(name, model)
    except Exception:  # noqa: BLE001 - a model that cannot serialize ends the stream cleanly
        log.exception("Run stream event %r could not be serialized", name)
        return _error_frame(STREAM_INVALID), True
    return text, name in ("done", "error")


async def _close(agen: Any, pending: asyncio.Task | None) -> None:
    """Stop the agent's generator: cancel an in-flight step, then aclose. Never raises."""
    with anyio.CancelScope(shield=True):
        if pending is not None and not pending.done():
            pending.cancel()
            try:
                await pending
            except BaseException:  # noqa: BLE001 - the step's own outcome no longer matters
                pass
        aclose = getattr(agen, "aclose", None)
        if aclose is not None:
            try:
                await aclose()
            except Exception:  # noqa: BLE001
                log.exception("Run stream generator failed to close")


async def sse_events(agen: Any, first: Any = _END) -> AsyncIterator[str]:
    """SSE text for a run: `first` (already peeked), then the rest of `agen`.

    Ends after a `done` or `error` event; a heartbeat comment is sent whenever no event
    arrives for HEARTBEAT_SECONDS; the agent's generator is always closed on the way out,
    including when the client disconnects.
    """

    async def step() -> Any:
        try:
            return await anext(agen)
        except StopAsyncIteration:
            return _END

    pending: asyncio.Task | None = None
    try:
        if first is not _END:
            text, final = _frame(first)
            yield text
            if final:
                return
        while True:
            pending = asyncio.ensure_future(step())
            while True:
                done, _ = await asyncio.wait({pending}, timeout=HEARTBEAT_SECONDS)
                if done:
                    break
                yield ": ping\n\n"
            task, pending = pending, None
            try:
                item = task.result()
            except Exception:  # noqa: BLE001 - details go to the log only
                log.exception("Run stream failed")
                yield _error_frame(STREAM_FAILED)
                return
            if item is _END:
                return
            text, final = _frame(item)
            yield text
            if final:
                return
    finally:
        await _close(agen, pending)


class _SSEResponse(StreamingResponse):
    """A StreamingResponse that always closes its body iterator, even when a send fails."""

    async def stream_response(self, send: Any) -> None:
        try:
            await super().stream_response(send)
        finally:
            aclose = getattr(self.body_iterator, "aclose", None)
            if aclose is not None:
                with anyio.CancelScope(shield=True):
                    await aclose()


@router.get(
    "/families/{fid}/runs/{run_id}/stream",
    response_class=StreamingResponse,
    responses={
        200: {"content": {"text/event-stream": {}}, "description": "Run events (see schemas.RUN_EVENTS)"},
        404: {"model": schemas.ErrorResponse},
        503: {"model": schemas.ErrorResponse},
    },
)
async def stream_run_events(run_id: str, access: FamilyAccess = Depends(family_member)) -> StreamingResponse:
    """Server-sent events for one run: `event: <name>` and `data: <camelCase JSON>`."""
    try:
        from agent.runs import stream_run
    except ImportError:
        log.exception("agent.runs is not importable")
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=STUDIO_UNAVAILABLE) from None

    try:
        source = await _maybe_await(stream_run(access.family_id, run_id))
        agen = source if hasattr(source, "__anext__") else aiter(source)
    except (LookupError, PermissionError):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=RUN_NOT_FOUND) from None
    except Exception:
        log.exception("Could not open the stream for run %s", run_id)
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=STUDIO_UNAVAILABLE) from None

    try:
        first = await anext(agen)
    except StopAsyncIteration:
        await _close(agen, None)
        return _SSEResponse(iter(()), media_type="text/event-stream", headers=SSE_HEADERS)
    except (LookupError, PermissionError):
        await _close(agen, None)
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=RUN_NOT_FOUND) from None
    except Exception:
        log.exception("Run stream %s failed before its first event", run_id)
        await _close(agen, None)
        return _SSEResponse(
            iter([_error_frame(STREAM_FAILED)]), media_type="text/event-stream", headers=SSE_HEADERS
        )

    return _SSEResponse(sse_events(agen, first), media_type="text/event-stream", headers=SSE_HEADERS)


# ------------------------------------------------------------------------------ answer


def _check_answer(body: schemas.AnswerRequest) -> None:
    if body.kind == "answer" and (body.text is None or not body.text.strip()):
        raise _unprocessable("An answer needs text")
    if body.kind == "props" and body.props is None:
        raise _unprocessable("A props review needs the props list")
    if body.kind == "merge" and body.approve is None:
        raise _unprocessable("A merge answer needs approve")


@router.post(
    "/families/{fid}/runs/{run_id}/answer",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    responses={
        403: {"model": schemas.ErrorResponse},
        404: {"model": schemas.ErrorResponse},
        409: {"model": schemas.ErrorResponse},
        503: {"model": schemas.ErrorResponse},
    },
)
async def answer_run_route(
    run_id: str,
    body: schemas.AnswerRequest,
    access: FamilyAccess = Depends(family_member),
) -> Response:
    """Answer a follow-up, confirm or edit the props, or approve a merge. Resumes the run."""
    _check_answer(body)
    try:
        from agent.runs import answer_run

        runs_mod = importlib.import_module("agent.runs")
    except ImportError:
        log.exception("agent.runs is not importable")
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=STUDIO_UNAVAILABLE) from None
    not_waiting = _exception_class(getattr(runs_mod, "RunNotWaiting", None))

    args = (access.family_id, run_id, access.user.id, body)
    try:
        if inspect.iscoroutinefunction(answer_run):
            await answer_run(*args)
        else:
            # The real answer_run can block for seconds on a threading.Condition.
            await _maybe_await(await run_in_threadpool(answer_run, *args))
    except Exception as exc:
        raise _answer_error(exc, run_id, not_waiting) from None
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _exception_class(value: Any) -> type[BaseException] | None:
    return value if isinstance(value, type) and issubclass(value, BaseException) else None


def _answer_error(exc: Exception, run_id: str, not_waiting: type[BaseException] | None) -> HTTPException:
    """The HTTP error for an exception from answer_run (see the module docstring)."""
    if not_waiting is not None and isinstance(exc, not_waiting):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=NOT_WAITING)
    if isinstance(exc, LookupError):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=RUN_NOT_FOUND)
    if isinstance(exc, PermissionError):
        return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=NOT_RUN_OWNER)
    if isinstance(exc, ValueError):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc) or ANSWER_CONFLICT)
    log.exception("Could not answer run %s", run_id, exc_info=exc)
    return HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=STUDIO_UNAVAILABLE)
