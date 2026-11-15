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
DIGIT_WORDS = dict(zip(("zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"), "0123456789"))
DIGIT_WORDS.update(dict(zip("零〇一二两三四五六七八九", "001223456789")))
DIGIT_TOKEN = r"(?:[0-9零〇一二两三四五六七八九]|(?<![A-Za-z])(?:zero|one|two|three|four|five|six|seven|eight|nine)(?![A-Za-z]))"
DIGIT_SEQUENCE = re.compile(DIGIT_TOKEN + r"(?:[ \t,，、-]*" + DIGIT_TOKEN + r")*", re.IGNORECASE)
DIGIT_PART = re.compile(DIGIT_TOKEN, re.IGNORECASE)
PRIVACY_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")
CODE_MARKER = "[code]"
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


def auto_target() -> tuple[str, str, int] | None:
    """The demo host's one teller: STITCH_CHANNELS_CALL_AUTO=<familyId>:<personId>:<year>.

    Unset (the default) keeps spoken codes and private drafts. Set, every signed
    call from an allowed agent becomes that person's memory in that year with no
    code and no review, so anyone who reaches the number adds to their book. It
    is for a supervised demo only.
    """
    raw = os.getenv("STITCH_CHANNELS_CALL_AUTO")
    if not raw:
        return None
    match = re.fullmatch(r"([A-Za-z0-9_-]{1,64}):([A-Za-z0-9_-]{1,64}):([0-9]{4})", raw)
    if not match:
        raise HTTPException(503, "Call interviews are not configured")
    return match[1], match[2], int(match[3])


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


def normalized_digits(sequence: str) -> str:
    return "".join(DIGIT_WORDS.get(match[0].lower(), match[0]) for match in DIGIT_PART.finditer(sequence))


def pairing_marker(message: str) -> str | None:
    """Exact standalone digit tokens only; never infer a number from narrative."""
    marker = message.strip()
    if len(marker) > 96:
        return None
    marker = marker.strip(PAIRING_EDGE_PUNCTUATION).strip()
    match = re.fullmatch(r"STITCH[ \t:：,，\"'“”‘’]{1,8}(.+)", marker, re.IGNORECASE)
    if match and DIGIT_SEQUENCE.fullmatch(match[1]):
        digits = normalized_digits(match[1])
        if len(digits) in {3, 6, 12}:  # Read compatibility for unexpired legacy tickets.
            return digits
    return None


def without_code(message: str, code: str) -> str | None:
    """Drop a repeated code from the draft without losing the story around it.

    Best effort: the code is already consumed and the draft is private to its
    owner, so a code spoken as part of a longer run of numbers is left alone.

    A standalone repeat of the code (any recognized spelling, once or several
    times over) is replaced by a marker. A longer number that merely contains
    the same digits, such as the year 1314 beside code 314, is narrative and
    stays. A turn that held nothing but the code is dropped.
    """
    def replace(match):
        digits = normalized_digits(match[0])
        times, rest = divmod(len(digits), len(code))
        return CODE_MARKER if times and not rest and digits == code * times else message[match.start():match.end()]
    # Fullwidth digits fold one-for-one, so the spans line up with the original.
    kept = DIGIT_SEQUENCE.sub(replace, message.translate(PRIVACY_DIGITS))
    if CODE_MARKER not in kept:
        return message
    return kept if kept.replace(CODE_MARKER, "").strip(PAIRING_EDGE_PUNCTUATION + " \t\r\n") else None


def transcript(raw: bytes, allowed_agents: frozenset[str], *,
               require_code: bool = True) -> tuple[str, str, str | None, str, int | None]:
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
            marker = pairing_marker(message)
            if marker is not None:
                pairing.append(marker)
            else:
                users.append(message)
        if not require_code:
            # Automatic mode: a code spoken out of habit is dropped, never needed.
            text = "\n".join(users)
            if not text.strip() or len(text) > MAX_TEXT:
                raise ValueError()
            return agent, conversation_id, None, text, None
        if not pairing or len(set(pairing)) != 1:
            raise ValueError()
        code = pairing[0]
        text = "\n".join(kept for kept in (without_code(message, code) for message in users) if kept is not None)
        if not text.strip() or len(text) > MAX_TEXT:
            raise ValueError()
        call_started_at = None
        if len(code) == 3:
            metadata = data.get("metadata")
            if not isinstance(metadata, dict) or type(metadata.get("start_time_unix_secs")) is not int:
                raise ValueError()
            call_started_at = metadata["start_time_unix_secs"]
        return agent, conversation_id, code, text, call_started_at
    except (ValueError, KeyError, TypeError, UnicodeError, RecursionError):
        raise HTTPException(400, "Call transcript or pairing unavailable") from None


def start_automatic_add(db, agent: str, conversation_id: str, target: tuple[str, str, int],
                        text: str, content_hash: str) -> None:
    """Start the studio run for one call exactly once; a provider retry starts nothing."""
    family_id, person_id, year = target
    row = conversations.begin_auto_call(db, agent, conversation_id, family_id, person_id, content_hash)
    if row is None:
        return
    try:
        import schemas
        from agent.runs import start_run
        run_id = start_run(family_id, row["actorId"], person_id,
                           schemas.SubmitMemoryFields(text=text, year=year, channel="voice"), [])
        if not isinstance(run_id, str) or not run_id:
            raise RuntimeError("No run id")
    except Exception:
        # Free the call so the provider's retry can start it; log no transcript.
        conversations.abandon_auto_call(db, row["_id"], family_id)
        raise HTTPException(503, "Call interviews are not configured") from None
    conversations.finish_auto_call(db, row["_id"], family_id, run_id)


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
    target = auto_target()
    agent, conversation_id, code, text, call_started_at = transcript(
        body, config.agents, require_code=target is None)
    if target is not None:
        if getattr(request.app.state, "agent_mode", None) == "off":
            raise HTTPException(503, "Call interviews are not configured")
        try:
            await run_in_threadpool(start_automatic_add, get_db(), agent, conversation_id,
                                    target, text, hashlib.sha256(body).hexdigest())
        except LookupError:
            raise HTTPException(503, "Call interviews are not configured") from None
        except conversations.CallConflict:
            raise HTTPException(409, "Call binding unavailable") from None
        except conversations.CallRateLimited as exc:
            raise HTTPException(429, "Try again shortly", headers={"Retry-After": str(exc.retry_after)}) from None
        return {"received": True}
    try:
        def accept():
            return conversations.accept_call_transcript(get_db(), agent, conversation_id,
                code, text, hashlib.sha256(body).hexdigest(), call_started_at=call_started_at)
        await run_in_threadpool(accept)
    except LookupError:
        raise HTTPException(400, "Call pairing unavailable") from None
    except conversations.CallConflict:
        raise HTTPException(409, "Call binding unavailable") from None
    except conversations.CallRateLimited as exc:
        raise HTTPException(429, "Try again shortly", headers={"Retry-After": str(exc.retry_after)}) from None
    return {"received": True}
