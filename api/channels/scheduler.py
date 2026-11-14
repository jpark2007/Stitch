"""Default-off manual prompt planning; no timer, model call, or outbound side effect."""

from datetime import datetime, timezone
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from db import conversations, question_bank


def allowed_now(contact, now):
    try:
        zone = ZoneInfo(contact["timezone"])
        cadence = contact["cadenceDays"]
        quiet = contact["quietHours"]
        if type(cadence) is not int or not 1 <= cadence <= 30:
            return False
        start, end = quiet["start"], quiet["end"]
        if not all(isinstance(x, str) and re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", x) for x in (start, end)) or start == end:
            return False
        if now.tzinfo is None:
            return False
        clock = now.astimezone(zone).strftime("%H:%M")
        quiet_now = start <= clock < end if start < end else clock >= start or clock < end
        return not quiet_now
    except (KeyError, TypeError, ValueError, ZoneInfoNotFoundError):
        return False


def trusted_destination(contact, row, principal):
    destination = row.get("destination", {})
    return (contact.get("status") == "active" and isinstance(contact.get("verifiedAt"), datetime)
        and isinstance(contact.get("optInAt"), datetime)
        and contact.get("spaceKind") == "direct" and bool(contact.get("spaceId"))
        and destination.get("spaceId") == contact["spaceId"]
        and destination.get("handle") == contact.get("handle")
        and destination.get("provider") == contact.get("channel")
        and destination.get("personId") == contact.get("personId")
        and destination.get("principal") == principal
        and (contact.get("channel") != "imessage"
             or destination.get("contactProof") == conversations.contact_proof(contact)))


def cadence_due(contact, row, history, now):
    """Re-evaluate current preferences at planning AND immediately before send."""
    if not allowed_now(contact, now):
        return False
    timestamps = [x["sentAt"] for x in history if x.get("sentAt")] + [contact.get("optInAt")]
    if row.get("lastInboundAt"):
        timestamps.append(row["lastInboundAt"])
    if any(not isinstance(value, datetime) for value in timestamps):
        return False
    last = max(value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc) for value in timestamps)
    zone = ZoneInfo(contact["timezone"])
    return (now.astimezone(zone).date() - last.astimezone(zone).date()).days >= contact["cadenceDays"]


def plan(db, service, contact, row, now):
    c = service.context
    if not allowed_now(contact, now) or not trusted_destination(contact, row, c.principal):
        return None
    if conversations.worker_receipts(db, c.family_id, c.contact_id):
        return None
    if any(item["status"] not in {"sent", "cancelled"} for item in row.get("outbox", [])):
        return None
    state = service._state()
    history = question_bank.notifications(db, c.family_id, c.contact_id)
    if len(history) >= 1000 or any(item.get("active") for item in history) or state.get("modelCalls", 0) >= 32:
        return None
    if not cadence_due(contact, row, history, now):
        return None
    if state["status"] != "draft":
        if not service.can_start_new_story(row):
            return None
    elif any(state["draft"].get(k) for k in ("text", "year", "photoIds", "questionId")):
        return None
    asked = {item["questionKey"] for item in history}
    for question in service.get_open_questions():
        if "open:" + question["id"] not in asked:
            return {"questionKey": "open:" + question["id"], "questionId": question["id"], "text": question["text"]}
    return question_bank.next_bank_question(db, asked)
