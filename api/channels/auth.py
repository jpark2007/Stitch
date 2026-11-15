"""Authenticate exact request bytes before JSON parsing or contact lookup."""

import asyncio
import io
import threading
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
BODY_READ_SECONDS = 30
SMALL_BODY_READ_SECONDS = 2
MAX_ACTIVE_BODIES = 4
# Per API process (not fleet-wide). A threading gate works across TestClient
# event loops as well as production, and rejects instead of queueing large bodies.
_body_slots = threading.BoundedSemaphore(MAX_ACTIVE_BODIES)


def verify_signature(method: str, path: str, timestamp: str, nonce: str,
                     signature: str, body: bytes, secret: bytes, now: float) -> None:
    """Raises ValueError for all invalid signatures; does not access storage."""
    _validate_headers(timestamp, nonce, signature, now)
    digest = hmac.new(secret, _prefix(method, path, timestamp, nonce), hashlib.sha256)
    digest.update(body)
    if not hmac.compare_digest(digest.hexdigest(), signature):
        raise ValueError("invalid authentication")


def _prefix(method: str, path: str, timestamp: str, nonce: str) -> bytes:
    return f"{method}\n{path}\n{timestamp}\n{nonce}\n".encode()


def _validate_headers(timestamp: str, nonce: str, signature: str, now: float) -> None:
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


async def _buffer_operation(function, *args):
    # Cancellation must not close a buffer that a worker is still using.
    task = asyncio.create_task(run_in_threadpool(function, *args))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        await task
        raise


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
    is_media = request.url.path in {
        "/internal/channels/inbound-photo", "/internal/channels/inbound-voice"}
    limit = MAX_PHOTO_BODY_BYTES if is_media else MAX_BODY_BYTES
    headers = request.headers
    timestamp = headers.get("x-channels-timestamp", "")
    nonce = headers.get("x-channels-nonce", "")
    signature = headers.get("x-channels-signature", "")
    try:
        for name in ("x-channels-timestamp", "x-channels-nonce", "x-channels-signature"):
            if len(headers.getlist(name)) != 1:
                raise ValueError("invalid authentication")
        _validate_headers(timestamp, nonce, signature, time.time())
    except ValueError:
        raise HTTPException(401, "Invalid channel authentication") from None
    lengths = headers.getlist("content-length")
    expected_length = None
    if lengths:
        if len(lengths) != 1 or not re.fullmatch(r"[0-9]{1,12}", lengths[0]):
            raise HTTPException(400, "Invalid Content-Length")
        expected_length = int(lengths[0])
        if expected_length > limit:
            raise HTTPException(413, "Message is too large")
    if is_media and not _body_slots.acquire(blocking=False):
        raise HTTPException(503, "Channel request capacity exceeded", headers={"Retry-After": "1"})
    try:
        digest = hmac.new(secret, _prefix(request.method, request.url.path, timestamp, nonce), hashlib.sha256)
        # BytesIO.getvalue avoids constructing prefix+body or a second full-body
        # bytearray copy. Chunk hashing/buffering runs off the event loop.
        with io.BytesIO() as raw:
            size = 0
            def append(chunk: bytes) -> None:
                digest.update(chunk)
                raw.write(chunk)
            try:
                async with asyncio.timeout(BODY_READ_SECONDS if is_media else SMALL_BODY_READ_SECONDS):
                    async for chunk in request.stream():
                        size += len(chunk)
                        if size > limit:
                            raise HTTPException(413, "Message is too large")
                        if expected_length is not None and size > expected_length:
                            raise HTTPException(400, "Content-Length does not match body")
                        await _buffer_operation(append, chunk)
            except TimeoutError:
                raise HTTPException(408, "Channel request body timed out") from None
            if expected_length is not None and size != expected_length:
                raise HTTPException(400, "Content-Length does not match body")
            if abs(time.time() - int(timestamp)) > MAX_SKEW_SECONDS:
                raise HTTPException(401, "Invalid channel authentication")
            if not hmac.compare_digest(digest.hexdigest(), signature):
                raise HTTPException(401, "Invalid channel authentication")
            body = await _buffer_operation(raw.getvalue)
        # Store replay state before accepting any request. Mongo's unique _id makes
        # simultaneous requests and separate API workers obey the same replay guard.
        consumed = await run_in_threadpool(
            lambda: conversations.consume_nonce(client.get_db(), nonce, int(timestamp))
        )
        if not consumed:
            raise HTTPException(409, "Channel request already received")
        return body
    finally:
        if is_media:
            _body_slots.release()
