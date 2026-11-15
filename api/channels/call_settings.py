"""Own-person pairing and review endpoints; no automatic memory submission."""

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict

from auth import FamilyAccess, family_member
from db.client import get_db
from db import conversations
from .elevenlabs import auto_target, configuration


def enabled(response: Response):
    configuration()
    response.headers["Cache-Control"] = "no-store"


router = APIRouter(dependencies=[Depends(enabled)])


class Empty(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


def operation(function, *args):
    try:
        return function(*args)
    except LookupError:
        raise HTTPException(404, "Not found") from None
    except conversations.CallConflict:
        raise HTTPException(409, "Call pairing unavailable") from None
    except conversations.CallRateLimited as exc:
        raise HTTPException(429, "Try again shortly", headers={"Retry-After": str(exc.retry_after)}) from None


def public_draft(row):
    return {"id": row["_id"], "personId": row["personId"], "createdAt": row["createdAt"], "text": row["text"]}


@router.post("/people/{person_id}/call-pairing", status_code=201)
def create_pairing(person_id: str, body: Empty, access: FamilyAccess = Depends(family_member), db=Depends(get_db)):
    row, code = operation(conversations.create_call_pairing, db, access.family_id, person_id, access.user.id)
    return {"code": code, "expiresAt": row["expireAt"], "personId": person_id,
        "phoneNumber": configuration().phone_number,
        "instruction": "Read STITCH followed by these three digits as one turn at the start of your call, one digit at a time in English or Chinese. Start your call soon and finish before this code expires in fifteen minutes. Your words become a private draft for your review; nothing is submitted automatically."}


@router.get("/call-runs/latest")
def latest_run(access: FamilyAccess = Depends(family_member), db=Depends(get_db)):
    """The run the newest automatic call started for the caller's own person, or null."""
    row = conversations.latest_call_run(db, access.family_id, access.user.id)
    return {"run": {"runId": row["runId"], "createdAt": row["createdAt"]} if row else None,
            "automatic": auto_target() is not None}


@router.get("/call-drafts")
def list_drafts(access: FamilyAccess = Depends(family_member), db=Depends(get_db)):
    return {"drafts": [public_draft(row) for row in operation(conversations.list_call_drafts, db, access.family_id, access.user.id)]}


@router.get("/call-drafts/{draft_id}")
def get_draft(draft_id: str, access: FamilyAccess = Depends(family_member), db=Depends(get_db)):
    return {"draft": public_draft(operation(conversations.get_call_draft, db, access.family_id, access.user.id, draft_id))}


@router.delete("/call-drafts/{draft_id}", status_code=204)
def delete_draft(draft_id: str, access: FamilyAccess = Depends(family_member), db=Depends(get_db)):
    operation(conversations.remove_call_draft, db, access.family_id, access.user.id, draft_id)
    return Response(status_code=204, headers={"Cache-Control": "no-store"})
