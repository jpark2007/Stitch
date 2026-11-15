"""Cookie-authenticated preferences. Creating a contact never sends or opts in."""

import os
import re
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from auth import FamilyAccess, family_member
from db.client import get_db
from db import contacts


def enabled(response: Response):
    response.headers["Cache-Control"] = "no-store"
    if os.getenv("STITCH_CHANNELS") != "1":
        raise HTTPException(404, "Not found")


router = APIRouter(prefix="/families/{fid}/channels", tags=["channels-settings"], dependencies=[Depends(enabled)])
from .call_settings import router as call_settings_router
router.include_router(call_settings_router)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class QuietHours(StrictModel):
    start: str
    end: str

    @field_validator("start", "end")
    @classmethod
    def clock(cls, value):
        if not re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", value):
            raise ValueError("Use HH:MM")
        return value

    @model_validator(mode="after")
    def interval(self):
        if self.start == self.end:
            raise ValueError("Quiet hours must have distinct start and end")
        return self


def timezone_name(value: str) -> str:
    if len(value) > 100:
        raise ValueError("Invalid timezone")
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError("Use an IANA timezone") from None
    return value


def canonical_handle(value: str) -> str:
    if re.fullmatch(r"\+[1-9][0-9]{7,14}", value):
        return value
    if len(value) <= 254 and value.isascii() and re.fullmatch(
            r"[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+(?:\.[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+)*@"
            r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+", value):
        local, domain = value.rsplit("@", 1)
        if len(local) <= 64:
            return f"{local.lower()}@{domain.lower()}"
    raise ValueError("Use an E.164 phone number or email address")


class EnrollmentRequest(StrictModel):
    provider: Literal["imessage"]
    handle: str
    timezone: str
    cadenceDays: int = Field(ge=1, le=30)
    quietHours: QuietHours

    _timezone = field_validator("timezone")(timezone_name)
    _handle = field_validator("handle")(canonical_handle)


class PreferencesPatch(StrictModel):
    timezone: str | None = None
    cadenceDays: int | None = Field(default=None, ge=1, le=30)
    quietHours: QuietHours | None = None

    @model_validator(mode="after")
    def explicit_fields(self):
        if not self.model_fields_set or any(getattr(self, key) is None for key in self.model_fields_set):
            raise ValueError("Supply at least one non-null preference")
        if self.timezone is not None:
            timezone_name(self.timezone)
        return self


class Empty(StrictModel):
    pass


class TestPromptRequest(StrictModel):
    requestId: str

    @field_validator("requestId")
    @classmethod
    def request_id(cls, value):
        if len(value) != 36 or str(UUID(value)) != value:
            raise ValueError("Use a canonical UUID")
        return value


def public_contact(row: dict | None) -> dict | None:
    if row is None:
        return None
    return {key: row.get(key) for key in ("personId", "handle", "status", "timezone", "cadenceDays", "quietHours")} | {"provider": row.get("channel")}


def operation(function, *args):
    try:
        return function(*args)
    except LookupError:
        raise HTTPException(404, "Not found") from None
    except PermissionError:
        raise HTTPException(403, "Own contact or family admin required") from None
    except contacts.ContactConflict:
        raise HTTPException(409, "Contact enrollment unavailable") from None


@router.get("/contacts")
def list_contacts(access: FamilyAccess = Depends(family_member), db=Depends(get_db)):
    rows = operation(contacts.list_settings_contacts, db, access.family_id, access.user.id)
    return {"contacts": [public_contact(row) for row in rows]}


@router.get("/people/{person_id}/contact")
def get_contact(person_id: str, access: FamilyAccess = Depends(family_member), db=Depends(get_db)):
    return {"contact": public_contact(operation(contacts.get_settings_contact, db, access.family_id, person_id, access.user.id))}


@router.post("/people/{person_id}/enrollment", status_code=201)
def create_enrollment(person_id: str, body: EnrollmentRequest, access: FamilyAccess = Depends(family_member), db=Depends(get_db)):
    preferences = body.model_dump(exclude={"provider", "handle"})
    row, code = operation(contacts.create_pending_enrollment, db, access.family_id, person_id,
                          access.user.id, body.provider, body.handle, preferences)
    return {"contact": public_contact(row), "code": code, "expiresAt": row["enrollmentExpiresAt"],
            "instruction": "To verify this handle and opt in to family story messages, the recipient must send LINK " + code + ". Sending it means consent. Reply STOP anytime. Creating this request sends nothing."}


@router.patch("/people/{person_id}/contact")
def update_preferences(person_id: str, body: PreferencesPatch, access: FamilyAccess = Depends(family_member), db=Depends(get_db)):
    row = operation(contacts.update_contact_preferences, db, access.family_id, person_id, access.user.id, body.model_dump(exclude_unset=True))
    return {"contact": public_contact(row)}


@router.post("/people/{person_id}/contact/stop")
def stop_contact(person_id: str, body: Empty, access: FamilyAccess = Depends(family_member), db=Depends(get_db)):
    row = operation(contacts.stop_settings_contact, db, access.family_id, person_id, access.user.id)
    return {"contact": public_contact(row)}


@router.post("/people/{person_id}/contact/test-prompt")
def test_prompt(person_id: str, body: TestPromptRequest,
                access: FamilyAccess = Depends(family_member), db=Depends(get_db)):
    from .outbound import BridgeSender
    from .prompts import TestPromptError, send_test_prompt
    try:
        sender = BridgeSender()
    except ValueError:
        raise HTTPException(503, "Messaging bridge is not configured") from None
    try:
        return operation(send_test_prompt, db, access.family_id, person_id,
                         access.user.id, body.requestId, sender)
    except TestPromptError as exc:
        raise HTTPException(exc.status, exc.message) from None
