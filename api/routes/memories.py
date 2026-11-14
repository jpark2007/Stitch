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
  `MemoryNotFound(LookupError)` when `memory_id` is missing or has no scene yet. The
  route checks `memory_id` itself first (404 "Memory not found" for an unknown or
  other-family memory, before start_run), so a LookupError for a memory it found means
  the moment has no scene: 409 NO_SCENE. A LookupError with no `memory_id` stays 404.
  Anything else, including the plain RuntimeError `get_manager()` raises when no run
  manager is configured, is 503 START_FAILED. Stored photos are deleted on every failure.
- `stream_run(family_id, run_id) -> AsyncIterator[(event name, model)]` is called and
  iterated on the event loop. `RunNotFound(LookupError)` (unknown or other-family run)
  or PermissionError at the call or on the first item is 404; any other exception at the
  call is 503 STUDIO_UNAVAILABLE. The real one raises RunNotFound on its first step,
  at once, but yields nothing until the run's first event, so the route waits at most
  FIRST_EVENT_WAIT for that item and then opens the stream anyway (headers, heartbeats).
  Each item becomes `event: <name>` with `data: <model JSON by alias>`, `name` one of
  `schemas.RUN_EVENTS`.
- `answer_run(family_id, run_id, user_id, answer: schemas.AnswerRequest) -> None` may
  block (up to a few seconds on a threading.Condition while a question settles), so a
  sync one runs in the threadpool; a coroutine function is awaited. `RunNotFound` is 404,
  `NotRunOwner(PermissionError)` 403, `RunNotWaiting(RuntimeError)` 409 NOT_WAITING, a
  ValueError (the answer does not fit the pending question) 409, and anything else,
  including the not-configured RuntimeError, 503 STUDIO_UNAVAILABLE.

Error details are always fixed sentences from this module. An exception's own text (run
ids, tool names, other families' ids) goes to the log, never to the client: a ValueError
from start_run is 422 SUBMIT_REFUSED, and one from answer_run is 409 with the matching
sentence from `_ANSWER_CONFLICTS` (else ANSWER_CONFLICT).

Size limits (route level; schemas.py is unbounded): text MAX_TEXT_CHARS, question
MAX_QUESTION_CHARS, title MAX_TITLE_CHARS, at most MAX_PARTICIPANTS participantIds, and
an answer's text MAX_ANSWER_CHARS with at most MAX_PROP_EDITS prop edits. Over a limit is
422 with a sentence naming the limit, before anything is stored or the agent is reached.

STITCH_AGENT=off: main.py stores the resolved agent mode on `app.state.agent_mode`
(AGENT_OFF_STATE). When it is "off", submit answers 503 START_FAILED and stream and
answer 503 STUDIO_UNAVAILABLE, after the membership check (so 401 and 404 come first)
and before `agent.runs` is imported or any photo is stored, so a key in the environment
can never start a lazy studio build. Without that attribute (a bare router, as in the
route tests) the routes behave as described above.
"""

from __future__ import annotations

import asyncio
import importlib
import inspect
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime
from typing import Annotated, Any

import anyio
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile, status
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
# Route-level size limits (schemas.py stays unbounded). Generous for real memories, but a
# pasted novel never reaches the compiler, narration or embeddings. Over a limit is 422.
MAX_TEXT_CHARS = 20_000
MAX_QUESTION_CHARS = 1_000
MAX_TITLE_CHARS = 200
MAX_PARTICIPANTS = 100
MAX_ANSWER_CHARS = MAX_TEXT_CHARS
MAX_PROP_EDITS = 100
ALLOWED_PHOTO_TYPES: frozenset[str] = frozenset(
    {"image/jpeg", "image/png", "image/webp", "image/gif", "image/heic", "image/heif"}
)
# Seconds without an event before the stream sends a `: ping` comment. Monkeypatched in tests.
HEARTBEAT_SECONDS: float = 15.0
# Seconds the stream route waits for a run's first event before it sends the headers anyway.
FIRST_EVENT_WAIT: float = 0.5

NO_PERSON = "Add or claim your person in this family first"
NO_SCENE = "This moment has no scene yet, so another telling cannot be added to it"
START_FAILED = "Could not start the studio"
STUDIO_UNAVAILABLE = "The studio is unavailable"
RUN_NOT_FOUND = "Run not found"
NOT_RUN_OWNER = "Only the person who started this run can answer"
NOT_WAITING = "This run is not waiting for an answer"
ANSWER_CONFLICT = "Run cannot take that answer"
ANSWER_WRONG_KIND = "That answer does not match what this run is waiting for"
SUBMIT_REFUSED = "This memory could not be started with those details"
STREAM_INVALID = "Internal stream error"
STREAM_FAILED = "Something went wrong while making this memory"

SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
    "Connection": "keep-alive",
}


AGENT_OFF_STATE = "agent_mode"
"""The `app.state` attribute main.py sets to the resolved STITCH_AGENT mode ("off", "fake" or "real")."""


def _agent_off(request: Request) -> bool:
    """True when the app was started with STITCH_AGENT=off. False when the attribute is absent."""
    return getattr(request.app.state, AGENT_OFF_STATE, None) == "off"


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


def _check_length(value: str | None, limit: int, what: str) -> None:
    """422 when value is longer than limit characters."""
    if value is not None and len(value) > limit:
        raise _unprocessable(f"{what} must be at most {limit:,} characters")


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
    request: Request,
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
    if _agent_off(request):
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=START_FAILED)
    fid = access.family_id

    # 1. Fields.
    if not text.strip():
        raise _unprocessable("Write the memory before submitting it")
    _check_length(text, MAX_TEXT_CHARS, "The memory")
    _check_length(question, MAX_QUESTION_CHARS, "The question")
    _check_length(title, MAX_TITLE_CHARS, "The title")
    if len(participant_ids) > MAX_PARTICIPANTS:
        raise _unprocessable(f"A memory can have at most {MAX_PARTICIPANTS} people in it")
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
            if fields.memory_id is not None:
                # Step 2 found this memory in the family, so the studio refused the moment
                # itself: it can only add a telling to a memory that has a scene.
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=NO_SCENE) from None
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Memory not found") from None
        if isinstance(exc, ValueError):
            # The message can name ids and agent internals: it goes to the log only.
            log.info("start_run for family %s refused the submit: %s", fid, exc)
            raise _unprocessable(SUBMIT_REFUSED) from None
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


async def _next_item(agen: Any) -> Any:
    """The agent generator's next item, or _END when it is exhausted."""
    try:
        return await anext(agen)
    except StopAsyncIteration:
        return _END


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


async def sse_events(agen: Any, first: Any = _END, pending: asyncio.Future | None = None) -> AsyncIterator[str]:
    """SSE text for a run: `first` (already peeked), then the rest of `agen`.

    `pending`, if given, is a `_next_item(agen)` step already in flight (the route started
    it and stopped waiting); its item comes next. Ends after a `done` or `error` event; a
    heartbeat comment is sent whenever no event arrives for HEARTBEAT_SECONDS; the agent's
    generator is always closed on the way out, including when the client disconnects.
    """
    try:
        if first is not _END:
            text, final = _frame(first)
            yield text
            if final:
                return
        while True:
            if pending is None:
                pending = asyncio.ensure_future(_next_item(agen))
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
    """A StreamingResponse that always closes its body iterator, even when a send fails.

    `on_close` runs after that. It closes the agent's generator (and any step in flight)
    when the body iterator was never started, since an unstarted async generator's
    `finally` never runs.
    """

    def __init__(self, content: Any, *, on_close: Callable[[], Awaitable[None]] | None = None, **kwargs: Any) -> None:
        super().__init__(content, **kwargs)
        self._on_close = on_close

    async def stream_response(self, send: Any) -> None:
        try:
            await super().stream_response(send)
        finally:
            with anyio.CancelScope(shield=True):
                aclose = getattr(self.body_iterator, "aclose", None)
                if aclose is not None:
                    await aclose()
                if self._on_close is not None:
                    await self._on_close()


def _event_stream(agen: Any, first: Any = _END, pending: asyncio.Future | None = None) -> _SSEResponse:
    return _SSEResponse(
        sse_events(agen, first, pending),
        on_close=lambda: _close(agen, pending),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


@router.get(
    "/families/{fid}/runs/{run_id}/stream",
    response_class=StreamingResponse,
    responses={
        200: {"content": {"text/event-stream": {}}, "description": "Run events (see schemas.RUN_EVENTS)"},
        404: {"model": schemas.ErrorResponse},
        503: {"model": schemas.ErrorResponse},
    },
)
async def stream_run_events(
    request: Request,
    run_id: str,
    access: FamilyAccess = Depends(family_member),
) -> StreamingResponse:
    """Server-sent events for one run: `event: <name>` and `data: <camelCase JSON>`."""
    if _agent_off(request):
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=STUDIO_UNAVAILABLE)
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

    # Peek at the first item: the real stream_run raises RunNotFound there, at once, and
    # that must be a 404 rather than a 200 stream. A run that has said nothing yet (the
    # studio is still on its first model call) does not hold the headers back: after
    # FIRST_EVENT_WAIT the stream opens with that step still in flight, and heartbeats run.
    first_step = asyncio.ensure_future(_next_item(agen))
    try:
        done, _ = await asyncio.wait({first_step}, timeout=FIRST_EVENT_WAIT)
    except BaseException:  # the request was cancelled while waiting
        await _close(agen, first_step)
        raise
    if not done:
        return _event_stream(agen, pending=first_step)

    try:
        first = first_step.result()
    except (LookupError, PermissionError):
        await _close(agen, None)
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=RUN_NOT_FOUND) from None
    except Exception:
        log.exception("Run stream %s failed before its first event", run_id)
        await _close(agen, None)
        return _SSEResponse(
            iter([_error_frame(STREAM_FAILED)]), media_type="text/event-stream", headers=SSE_HEADERS
        )
    if first is _END:
        await _close(agen, None)
        return _SSEResponse(iter(()), media_type="text/event-stream", headers=SSE_HEADERS)

    return _event_stream(agen, first)


# ------------------------------------------------------------------------------ answer


def _check_answer(body: schemas.AnswerRequest) -> None:
    if body.kind == "answer" and (body.text is None or not body.text.strip()):
        raise _unprocessable("An answer needs text")
    if body.kind == "props" and body.props is None:
        raise _unprocessable("A props review needs the props list")
    if body.kind == "merge" and body.approve is None:
        raise _unprocessable("A merge answer needs approve")
    _check_length(body.text, MAX_ANSWER_CHARS, "The answer")
    if body.props is not None:
        if len(body.props) > MAX_PROP_EDITS:
            raise _unprocessable(f"A props review can list at most {MAX_PROP_EDITS} props")
        for edit in body.props:
            _check_length(edit.name, MAX_TITLE_CHARS, "A prop name")


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
    request: Request,
    run_id: str,
    body: schemas.AnswerRequest,
    access: FamilyAccess = Depends(family_member),
) -> Response:
    """Answer a follow-up, confirm or edit the props, or approve a merge. Resumes the run."""
    if _agent_off(request):
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=STUDIO_UNAVAILABLE)
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


# (lowercase fragment of an agent ValueError message, the fixed detail sent instead).
# The agents phrase these cases in agent/runs.py, agent/tools/ask.py and seed/fake_runs.py.
_ANSWER_CONFLICTS: tuple[tuple[str, str], ...] = (
    ("not waiting", NOT_WAITING),
    ("waiting for kind", ANSWER_WRONG_KIND),
    ("does not match the pending", ANSWER_WRONG_KIND),
    ("needs text", "An answer needs text"),
    ("props list", "A props review needs the props list"),
    ("needs approve", "A merge answer needs approve"),
)


def _answer_conflict_detail(message: str) -> str:
    """A fixed, user-facing 409 detail for an answer_run ValueError. Never echoes the message."""
    lowered = message.lower()
    for fragment, detail in _ANSWER_CONFLICTS:
        if fragment in lowered:
            return detail
    return ANSWER_CONFLICT


def _answer_error(exc: Exception, run_id: str, not_waiting: type[BaseException] | None) -> HTTPException:
    """The HTTP error for an exception from answer_run (see the module docstring)."""
    if not_waiting is not None and isinstance(exc, not_waiting):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=NOT_WAITING)
    if isinstance(exc, LookupError):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=RUN_NOT_FOUND)
    if isinstance(exc, PermissionError):
        return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=NOT_RUN_OWNER)
    if isinstance(exc, ValueError):
        # The message can carry run ids, tool names and parser internals: log it, and send
        # the client a fixed sentence for the cases it can act on.
        log.info("answer_run for run %s refused the answer: %s", run_id, exc)
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=_answer_conflict_detail(str(exc)))
    log.exception("Could not answer run %s", run_id, exc_info=exc)
    return HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=STUDIO_UNAVAILABLE)
