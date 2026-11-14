"""Opt-in internal transport router. Mounting is left to the API owner."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from db import client, contacts, conversations
from .auth import authenticated_body
from .models import AuthorizeContact, InboundMessage, InboundPhoto, Tick, WHITESPACE

router = APIRouter(prefix="/internal/channels", tags=["channels"])


def _receive(message: InboundMessage) -> dict:
    db = client.get_db()
    data = message.model_dump()
    try:
        if message.text.strip(WHITESPACE).upper() in {"STOP", "UNSUBSCRIBE"}:
            rows = contacts.matching_contacts(db, message.provider, message.senderId)
            if not rows:
                raise LookupError("Unknown contact")
            contacts.stop_contacts(db, message.provider, message.senderId, rows)
            duplicates = []
            for row in rows:
                try:
                    duplicates.append(conversations.save_inbound(db, row, data, stopped=True))
                except Exception:
                    # Consent revocation must not depend on receipt identity or
                    # availability after the contact update has succeeded.
                    duplicates.append(False)
            return {"accepted": True, "duplicate": all(duplicates), "stopped": True}
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
async def tick(body: bytes = Depends(authenticated_body)) -> dict:
    try:
        Tick.model_validate_json(body)
    except ValidationError:
        raise HTTPException(422, "Invalid tick") from None
    return {"processed": 0, "scheduler": "inactive"}


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
