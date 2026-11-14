"""Recipient proof boundary, mounted separately from settings and inbound routes."""

import re
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import field_validator, ValidationError
from starlette.concurrency import run_in_threadpool

from db import contacts
from db.client import get_db
from .auth import authenticated_body
from .models import bounded_text
from .settings import StrictModel, canonical_handle

router = APIRouter(tags=["channels-enrollment"])


class EnrollmentProof(StrictModel):
    messageId: str
    provider: Literal["imessage"]
    senderId: str
    spaceId: str
    code: str

    @field_validator("messageId", "spaceId")
    @classmethod
    def identifier(cls, value):
        return bounded_text(value, 256)

    _sender = field_validator("senderId")(canonical_handle)

    @field_validator("code")
    @classmethod
    def token(cls, value):
        if not re.fullmatch(r"[A-Za-z0-9_-]{43}", value):
            raise ValueError("Invalid proof")
        return value


@router.post("/internal/channels/enroll")
async def enroll(raw: bytes = Depends(authenticated_body), db=Depends(get_db)):
    try:
        proof = EnrollmentProof.model_validate_json(raw)
    except ValidationError:
        raise HTTPException(400, "Enrollment proof unavailable") from None
    accepted = await run_in_threadpool(contacts.redeem_enrollment, db, proof.provider,
                                       proof.senderId, proof.code, proof.spaceId)
    if not accepted:
        raise HTTPException(400, "Enrollment proof unavailable")
    return {"status": "active"}
