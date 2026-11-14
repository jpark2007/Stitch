"""One bounded, signed loopback bridge request. No retry or destination from a model."""

import hashlib
import asyncio
import hmac
import json
import os
import time
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from .models import WHITESPACE

SEND_TIMEOUT_SECONDS = 5


class BridgeSender:
    def __init__(self, *, base_url: str | None = None, secret: str | None = None, transport=None):
        base_url = base_url or os.getenv("CHANNELS_BRIDGE_URL", "http://127.0.0.1:3011")
        parsed = urlsplit(base_url)
        if (parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "::1"}
                or parsed.username or parsed.password or parsed.path not in {"", "/"}
                or parsed.query or parsed.fragment):
            raise ValueError("Bridge URL must be a loopback HTTP origin")
        self.url = base_url.rstrip("/") + "/send"
        self.secret = secret if secret is not None else os.getenv("CHANNELS_SHARED_SECRET", "")
        if len(self.secret.encode()) < 32 or self.secret != self.secret.strip(WHITESPACE):
            raise ValueError("Invalid channel shared secret")
        self.transport = transport

    def send(self, message: dict) -> None:
        if set(message) != {"messageId", "provider", "spaceId", "text"}:
            raise ValueError("Invalid outbound fields")
        if message["provider"] not in {"terminal", "imessage"}:
            raise ValueError("Invalid outbound provider")
        for key, limit in (("messageId", 256), ("spaceId", 256), ("text", 8000)):
            if not isinstance(message[key], str) or not message[key].strip() or len(message[key].encode()) > limit:
                raise ValueError("Invalid outbound data")
        raw = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode()
        if len(raw) > 16384:
            raise ValueError("Outbound body is too large")
        timestamp, nonce = str(int(time.time())), str(uuid4())
        signature = hmac.new(self.secret.encode(),
            f"POST\n/send\n{timestamp}\n{nonce}\n".encode() + raw, hashlib.sha256).hexdigest()
        async def deliver():
            async with asyncio.timeout(SEND_TIMEOUT_SECONDS), httpx.AsyncClient(timeout=5, follow_redirects=False,
                    trust_env=False, transport=self.transport) as client:
                async with client.stream("POST", self.url, content=raw, headers={
                "content-type": "application/json", "accept-encoding": "identity", "X-Channels-Timestamp": timestamp,
                "X-Channels-Nonce": nonce, "X-Channels-Signature": signature,
                }) as response:
                    body = bytearray()
                    if response.headers.get("content-encoding", "identity") != "identity":
                        raise RuntimeError("Unsupported bridge acknowledgement encoding")
                    async for chunk in response.aiter_bytes(chunk_size=1024):
                        if len(body) + len(chunk) > 1024:
                            raise RuntimeError("Bridge acknowledgement too large")
                        body.extend(chunk)
                    if response.status_code != 200 or json.loads(body) != {"status": "sent"}:
                        raise RuntimeError("Bridge delivery outcome uncertain")
        asyncio.run(deliver())
