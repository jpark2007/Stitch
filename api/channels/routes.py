"""Opt-in internal transport router. Mounting is left to the API owner."""

import os

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from db import client, contacts, conversations
from .auth import authenticated_body
from .consent import receive_stop
from .models import AuthorizeContact, InboundMessage, InboundPhoto, InboundVoice, Tick

router = APIRouter(prefix="/internal/channels", tags=["channels"])


def _receive(message: InboundMessage) -> dict:
    db = client.get_db()
    data = message.model_dump()
    try:
        stopped = receive_stop(db, data, message.text)
        if stopped is not None:
            return stopped
        contact = contacts.resolve_contact(db, message.provider, message.senderId)
        return {"accepted": True, "duplicate": conversations.save_inbound(db, contact, data)}
    except LookupError:
        raise HTTPException(404, "Unknown contact") from None
    except PermissionError:
        raise HTTPException(403, "Contact is not opted in") from None
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None


@router.post("/inbound", status_code=202)
async def inbound(body: bytes = Depends(authenticated_body)) -> dict:
    try:
        message = InboundMessage.model_validate_json(body)
    except ValidationError:
        # Do not echo private message input in default validation-error details.
        raise HTTPException(422, "Invalid channel message") from None
    return await run_in_threadpool(_receive, message)


@router.post("/tick")
async def tick(request: Request, body: bytes = Depends(authenticated_body)) -> dict:
    try:
        Tick.model_validate_json(body)
    except ValidationError:
        raise HTTPException(422, "Invalid tick") from None
    if os.getenv("STITCH_CHANNELS_WORKER") != "1":
        return {"processed": 0, "scheduler": "inactive"}
    def process():
        from .worker import configured_worker
        worker = getattr(request.app.state, "channels_worker", None)
        if worker is None:
            worker = configured_worker(client.get_db())
        return worker.tick()
    try:
        return await run_in_threadpool(process)
    except ValueError:
        raise HTTPException(503, "Channels worker is not configured") from None


def _authorize(message: AuthorizeContact) -> dict:
    try:
        contacts.resolve_contact(client.get_db(), message.provider, message.senderId)
    except LookupError:
        raise HTTPException(404, "Unknown contact") from None
    except PermissionError:
        raise HTTPException(403, "Contact is not opted in") from None
    except ValueError:
        raise HTTPException(409, "Family selection required") from None
    return {"allowed": True}


@router.post("/authorize")
async def authorize(body: bytes = Depends(authenticated_body)) -> dict:
    try:
        message = AuthorizeContact.model_validate_json(body)
    except ValidationError:
        raise HTTPException(422, "Invalid contact authorization") from None
    return await run_in_threadpool(_authorize, message)


def _receive_photo(message: InboundPhoto) -> dict:
    from .photos import PhotoDecoderUnavailable, receive_photo
    # Separate conflict errors from invalid image bytes without exposing content.
    try:
        return receive_photo(client.get_db(), message)
    except LookupError:
        raise HTTPException(404, "Unknown contact") from None
    except PermissionError:
        raise HTTPException(403, "Contact is not opted in") from None
    except PhotoDecoderUnavailable:
        raise HTTPException(415, "HEIC/HEIF decoding is unavailable") from None
    except conversations.PhotoConflict:
        raise HTTPException(409, "Message ID was already used for different content") from None
    except conversations.PhotoPending:
        raise HTTPException(503, "Photo storage needs retry or operator reconciliation",
                            headers={"Retry-After": "1"}) from None
    except ValueError as exc:
        if str(exc) == "Family selection required":
            raise HTTPException(409, "Family selection required") from None
        raise HTTPException(422, "Invalid photo") from None


@router.post("/inbound-photo", status_code=202)
async def inbound_photo(body: bytes = Depends(authenticated_body)) -> dict:
    try:
        message = InboundPhoto.model_validate_json(body)
    except ValidationError:
        raise HTTPException(422, "Invalid photo message") from None
    return await run_in_threadpool(_receive_photo, message)


def _receive_voice(message: InboundVoice) -> dict:
    from .voice import receive_voice
    try:
        return receive_voice(client.get_db(), message)
    except LookupError:
        raise HTTPException(404, "Unknown contact") from None
    except PermissionError:
        raise HTTPException(403, "Contact is not opted in") from None
    except conversations.PhotoConflict:
        raise HTTPException(409, "Message ID was already used for different content") from None
    except conversations.PhotoPending:
        raise HTTPException(503, "Audio storage requires retry or reconciliation") from None
    except ValueError as exc:
        if str(exc) == "Family selection required":
            raise HTTPException(409, "Family selection required") from None
        raise HTTPException(422, "Invalid voice note") from None


@router.post("/inbound-voice", status_code=202)
async def inbound_voice(request: Request, body: bytes = Depends(authenticated_body)) -> dict:
    from .transcription import TranscriptionConfig, TranscriptionError
    if getattr(request.app.state, "channels_transcriber", None) is None:
        try:
            TranscriptionConfig.from_env()  # Validation only; no provider call.
        except TranscriptionError:
            raise HTTPException(503, "Voice transcription is not configured") from None
    try:
        message = InboundVoice.model_validate_json(body)
    except ValidationError:
        raise HTTPException(422, "Invalid voice message") from None
    return await run_in_threadpool(_receive_voice, message)
