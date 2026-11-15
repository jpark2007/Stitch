"""Signed post-call review drafts. No provider requests, inbox commands or studio work."""

import asyncio
from dataclasses import dataclass
import hashlib
import hmac
import json
import os
import re
import time

from fastapi import APIRouter, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from db import conversations
from db.client import get_db

MAX_BODY = 256 * 1024
READ_TIMEOUT = 2.0
MAX_TEXT = 20_000
PAIRING_EDGE_PUNCTUATION = ".,!?;:…\"'“”‘’()[]{}<>，。！？：；、（）【】"
router = APIRouter()


@dataclass(frozen=True)
class Configuration:
    secret: bytes
    agents: frozenset[str]
    phone_number: str | None


def configuration() -> Configuration:
    if os.getenv("STITCH_CHANNELS") != "1" or os.getenv("STITCH_CHANNELS_ELEVENLABS") != "1":
        raise HTTPException(404, "Not found")
    secret = os.getenv("ELEVENLABS_WEBHOOK_SECRET", "")
    agents = frozenset(x.strip() for x in os.getenv("ELEVENLABS_AGENT_IDS", "").split(",") if x.strip())
    number = os.getenv("STITCH_CHANNELS_CALL_NUMBER") or None
    if (len(secret.encode()) < 32 or secret != secret.strip() or not 1 <= len(agents) <= 20
            or any(not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", agent) for agent in agents)
            or (number is not None and not re.fullmatch(r"\+[1-9][0-9]{7,14}", number))):
        raise HTTPException(503, "Call interviews are not configured")
    return Configuration(secret.encode(), agents, number)


def signature_fields(header: str | None, *, now=None) -> tuple[str, str]:
    if not isinstance(header, str) or len(header) > 256:
        raise HTTPException(401, "Invalid webhook authentication")
    match = re.fullmatch(r"t=([0-9]{1,12}),\s*v0=([a-f0-9]{64})", header)
    if not match or abs((time.time() if now is None else now) - int(match[1])) > 1800:
        raise HTTPException(401, "Invalid webhook authentication")
    return match[1], match[2]


def verify_signature(raw: bytes, header: str | None, secret: bytes, *, now=None) -> None:
    timestamp, supplied = signature_fields(header, now=now)
    expected = hmac.new(secret, timestamp.encode() + b"." + raw, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, supplied):
        raise HTTPException(401, "Invalid webhook authentication")


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def transcript(raw: bytes, allowed_agents: frozenset[str]) -> tuple[str, str, str, str]:
    try:
        event = json.loads(raw.decode("utf-8"), object_pairs_hook=_object,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Invalid JSON")))
        if not isinstance(event, dict) or event.get("type") != "post_call_transcription":
            raise ValueError()
        data = event["data"]
        agent, conversation_id = data["agent_id"], data["conversation_id"]
        if (agent not in allowed_agents or not isinstance(conversation_id, str)
                or not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", conversation_id) or data.get("status") != "done"):
            raise ValueError()
        turns = data["transcript"]
        if not isinstance(turns, list) or not 1 <= len(turns) <= 1000:
            raise ValueError()
        users, pairing = [], []
        for turn in turns:
            if not isinstance(turn, dict):
                raise ValueError()
            if turn.get("role") != "user":
                continue
            message = turn.get("message")
            if not isinstance(message, str):
                raise ValueError()  # Redacted/null user content cannot prove pairing.
            message.encode("utf-8")
            # ASR commonly adds a comma after STITCH and a final full stop. Only
            # bounded punctuation at marker boundaries is tolerated, never words
            # extracted from a longer sentence or number-word guessing.
            marker = message.strip()
            marker = marker.strip(PAIRING_EDGE_PUNCTUATION).strip() if len(marker) <= 96 else ""
            match = re.fullmatch(r"STITCH[ \t:：,，\"'“”‘’]{1,8}([0-9][0-9 \t-]{10,34}[0-9])",
                                 marker, re.IGNORECASE)
            if match:
                digits = re.sub(r"[ \t-]", "", match[1])
                if len(digits) != 12:
                    raise ValueError()
                pairing.append(digits)
            else:
                users.append(message)
        if len(pairing) != 1:
            raise ValueError()
        code = pairing[0]
        # Never retain the numeric secret if the caller repeats it in a sentence.
        if any(code in re.sub(r"\D", "", message) for message in users):
            raise ValueError()
        text = "\n".join(users)
        if not text.strip() or len(text) > MAX_TEXT:
            raise ValueError()
        return agent, conversation_id, code, text
    except (ValueError, KeyError, TypeError, UnicodeError):
        raise HTTPException(400, "Call transcript or pairing unavailable") from None


@router.post("/elevenlabs/post-call")
async def post_call(request: Request):
    config = configuration()
    if request.url.query or request.headers.get("content-type", "").split(";")[0].strip() != "application/json":
        raise HTTPException(400, "Invalid webhook request")
    if len(request.headers.getlist("elevenlabs-signature")) != 1:
        raise HTTPException(401, "Invalid webhook authentication")
    header = request.headers.get("elevenlabs-signature")
    signature_fields(header)
    length = request.headers.get("content-length")
    if length is not None and (len(length) > 8 or not length.isascii() or not length.isdecimal() or int(length) > MAX_BODY):
        raise HTTPException(413, "Webhook too large")
    raw = bytearray()
    try:
        async with asyncio.timeout(READ_TIMEOUT):
            async for chunk in request.stream():
                if len(raw) + len(chunk) > MAX_BODY:
                    raise HTTPException(413, "Webhook too large")
                raw.extend(chunk)
    except TimeoutError:
        raise HTTPException(408, "Webhook read timed out") from None
    body = bytes(raw)
    verify_signature(body, header, config.secret)
    agent, conversation_id, code, text = transcript(body, config.agents)
    try:
        def accept():
            return conversations.accept_call_transcript(get_db(), agent, conversation_id,
                                                       code, text, hashlib.sha256(body).hexdigest())
        await run_in_threadpool(accept)
    except LookupError:
        raise HTTPException(400, "Call pairing unavailable") from None
    except conversations.CallConflict:
        raise HTTPException(409, "Call binding unavailable") from None
    except conversations.CallRateLimited:
        raise HTTPException(429, "Try again shortly", headers={"Retry-After": "60"}) from None
    return {"received": True}
