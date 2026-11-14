"""Authenticate exact request bytes before JSON parsing or contact lookup."""

import hashlib
import hmac
import os
import re
import time
from uuid import UUID

from fastapi import HTTPException, Request
from starlette.concurrency import run_in_threadpool

from db import client, conversations
from .models import MAX_BODY_BYTES, MAX_PHOTO_BODY_BYTES, WHITESPACE

MAX_SKEW_SECONDS = 300


def verify_signature(method: str, path: str, timestamp: str, nonce: str,
                     signature: str, body: bytes, secret: bytes, now: float) -> None:
    """Raises ValueError for all invalid signatures; does not access storage."""
    if not re.fullmatch(r"[0-9]{1,12}", timestamp):
        raise ValueError("invalid authentication")
    if abs(now - int(timestamp)) > MAX_SKEW_SECONDS:
        raise ValueError("invalid authentication")
    try:
        if str(UUID(nonce)) != nonce:
            raise ValueError("invalid authentication")
    except (ValueError, AttributeError):
        raise ValueError("invalid authentication") from None
    if not re.fullmatch(r"[0-9a-f]{64}", signature):
        raise ValueError("invalid authentication")
    signed = f"{method}\n{path}\n{timestamp}\n{nonce}\n".encode() + body
    expected = hmac.new(secret, signed, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise ValueError("invalid authentication")


async def authenticated_body(request: Request) -> bytes:
    if os.getenv("STITCH_CHANNELS") != "1":
        raise HTTPException(404, "Not found")
    secret_text = os.getenv("CHANNELS_SHARED_SECRET", "")
    # Fail closed instead of silently trimming deployment mistakes: both peers
    # must sign with the exact same nonblank secret, including interior bytes.
    if not secret_text or secret_text != secret_text.strip(WHITESPACE):
        raise HTTPException(503, "Channels is not configured")
    try:
        secret = secret_text.encode("utf-8")
    except UnicodeEncodeError:
        raise HTTPException(503, "Channels is not configured") from None
    if len(secret) < 32:
        raise HTTPException(503, "Channels is not configured")
    # Query strings are not part of this protocol and must not acquire unsigned meaning.
    if request.url.query:
        raise HTTPException(400, "Query parameters are not supported")
    raw = bytearray()
    limit = MAX_PHOTO_BODY_BYTES if request.url.path == "/internal/channels/inbound-photo" else MAX_BODY_BYTES
    async for chunk in request.stream():
        if len(raw) + len(chunk) > limit:
            raise HTTPException(413, "Message is too large")
        raw.extend(chunk)
    body = bytes(raw)
    headers = request.headers
    timestamp = headers.get("x-channels-timestamp", "")
    nonce = headers.get("x-channels-nonce", "")
    signature = headers.get("x-channels-signature", "")
    now = time.time()
    try:
        verify_signature(request.method, request.url.path, timestamp, nonce,
                         signature, body, secret, now)
    except ValueError:
        raise HTTPException(401, "Invalid channel authentication") from None
    # Store replay state before accepting any request. Mongo's unique _id makes
    # simultaneous requests and separate API workers obey the same replay guard.
    consumed = await run_in_threadpool(
        lambda: conversations.consume_nonce(client.get_db(), nonce, int(timestamp))
    )
    if not consumed:
        raise HTTPException(409, "Channel request already received")
    return body
