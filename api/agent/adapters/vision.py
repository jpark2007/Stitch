"""AnthropicVision: the production ``photos.VisionModel``, backed by Claude.

``describe(images, prompt)`` sends one user message with one base64 image block per image
followed by the prompt as a text block, and returns the model's text.

Images are prepared with Pillow before they are sent:

- The media type is sniffed from the bytes (never trusted from a filename or upload header).
  JPEG, PNG, GIF and WebP are sent as they are; anything else Pillow reads (BMP, TIFF, ...)
  is re-encoded as PNG.
- An image whose long edge is over ``MAX_EDGE`` (1568 px, the size above which Claude
  downscales anyway) is downscaled and re-encoded: PNG when it has transparency or was a
  PNG, otherwise JPEG. The EXIF orientation is applied first, so a sideways phone photo is
  described upright.
- An image still too large for the API's per-image limit is re-encoded as JPEG.

Env: ``ANTHROPIC_API_KEY`` (read by the SDK) and ``STITCH_VISION_MODEL`` (default
``agent.studio.DEFAULT_MODEL``). Tests inject a fake ``client`` with a ``messages.create``.

Bounded, so a hung or overloaded vision provider cannot stall a run. ``timeout`` (seconds per
request) and ``max_retries`` default to the run limits for Claude requests,
``agent.guards.RunLimits.model_timeout_s`` and ``model_max_retries`` (env
``STITCH_MODEL_TIMEOUT_S``, default 120, and ``STITCH_MODEL_MAX_RETRIES``, default 2).

The retries are made here, not by the SDK. The pinned SDK (anthropic 2.8.0,
``anthropic._base_client.BaseClient._calculate_retry_timeout``) obeys any positive
``retry-after-ms`` / ``Retry-After`` with no cap (only ``time.sleep``'s platform limit, about
49 days), so one ``Retry-After: 3600`` from an overloaded API would hold a run for an hour. So
the SDK client is built with ``max_retries=0`` and ``describe`` retries itself:

- Retried, at most ``max_retries`` times: a connection that failed
  (``anthropic.APIConnectionError``), and HTTP 408, 409, 429 and 5xx (529 overloaded
  included), the statuses the SDK retries. An ``x-should-retry: true|false`` header wins, as
  in the SDK.
- Not retried: a request that timed out (``anthropic.APITimeoutError``; the provider is hung,
  and waiting again would double the worst case), any other HTTP error (400, 401, 403, 404,
  413, ...), and any non-SDK exception.
- Each retry waits ``RETRY_BACKOFF_S`` doubling per attempt, or the response's numeric
  ``retry-after-ms`` / ``Retry-After``, never more than ``MAX_BACKOFF_S`` (8 s, the same cap
  as ``agent.adapters.images``).

So one ``describe`` takes at most ``worst_case_s(timeout, max_retries)`` =
``(1 + max_retries) * timeout + max_retries * MAX_BACKOFF_S`` seconds (376 s with the
defaults), pinned by tests through the SDK over a fake transport. An injected ``client``
keeps its own SDK settings, but its errors go through the same bounded retry loop.
"""

from __future__ import annotations

import base64
import io
import logging
import math
import os
import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any

import anthropic
from PIL import Image, ImageOps

from agent.adapters.images import MAX_BACKOFF_S, RETRY_BACKOFF_S
from agent.guards import RunLimits

log = logging.getLogger(__name__)

MAX_EDGE = 1568
"""Long edge (px) above which an image is downscaled before it is sent."""

MAX_IMAGE_BYTES = 3_750_000
"""Raw bytes per image that stay under the API's 5 MB base64 limit."""

_SENDABLE = {"JPEG": "image/jpeg", "PNG": "image/png", "GIF": "image/gif", "WEBP": "image/webp"}

API_KEY_ENV = "ANTHROPIC_API_KEY"
MODEL_ENV = "STITCH_VISION_MODEL"


def _has_alpha(im: Image.Image) -> bool:
    return im.mode in ("RGBA", "LA", "PA") or (im.mode == "P" and "transparency" in im.info)


def _encode(im: Image.Image, *, png: bool) -> tuple[bytes, str]:
    buf = io.BytesIO()
    if png:
        if im.mode not in ("RGB", "RGBA", "L", "LA"):
            im = im.convert("RGBA" if _has_alpha(im) else "RGB")
        im.save(buf, format="PNG", optimize=True)
        return buf.getvalue(), "image/png"
    if im.mode != "RGB":
        im = im.convert("RGB")
    im.save(buf, format="JPEG", quality=88)
    return buf.getvalue(), "image/jpeg"


def prepare_image(data: bytes) -> tuple[bytes, str]:
    """(bytes to send, media type) for one image. Raises ValueError if Pillow cannot read it."""
    try:
        with Image.open(io.BytesIO(data)) as src:
            fmt = (src.format or "").upper()
            orientation = src.getexif().get(0x0112, 1)
            src.load()
            im = src.copy()
    except Exception as exc:
        raise ValueError(f"not a readable image ({len(data)} bytes)") from exc

    too_big = max(im.size) > MAX_EDGE
    rotated = orientation not in (None, 1)
    if fmt in _SENDABLE and not too_big and not rotated and len(data) <= MAX_IMAGE_BYTES:
        return data, _SENDABLE[fmt]

    if rotated:
        im = ImageOps.exif_transpose(im)
    if max(im.size) > MAX_EDGE:
        scale = MAX_EDGE / max(im.size)
        new_size = (max(1, round(im.width * scale)), max(1, round(im.height * scale)))
        if im.mode == "P":
            im = im.convert("RGBA" if _has_alpha(im) else "RGB")
        im = im.resize(new_size, Image.Resampling.LANCZOS)
    as_png = _has_alpha(im) or fmt == "PNG" or fmt not in _SENDABLE
    out, media_type = _encode(im, png=as_png)
    if len(out) > MAX_IMAGE_BYTES:
        out, media_type = _encode(im, png=False)
    return out, media_type


def _text_of(response: Any) -> str:
    """Join the text blocks of a Messages API response (SDK object or plain dict)."""
    content = response.get("content") if isinstance(response, dict) else getattr(response, "content", None)
    parts: list[str] = []
    for block in content or []:
        kind = block.get("type") if isinstance(block, dict) else getattr(block, "type", None)
        if kind != "text":
            continue
        text = block.get("text") if isinstance(block, dict) else getattr(block, "text", None)
        if text:
            parts.append(text)
    return "\n".join(parts).strip()


RETRY_STATUSES = frozenset({408, 409, 429})
"""Statuses retried besides every 5xx: the ones the SDK itself retries."""


def worst_case_s(timeout: float, max_retries: int) -> float:
    """The longest one ``describe`` can take: every attempt times out, every wait is capped."""
    return (1 + max_retries) * float(timeout) + max_retries * MAX_BACKOFF_S


def _header(headers: Mapping[str, str] | None, name: str) -> str | None:
    if headers is None:
        return None
    try:
        value = headers.get(name)
    except Exception:  # noqa: BLE001 - an odd fake response; treat as no header
        return None
    return value.strip() if isinstance(value, str) else None


def _asked_wait_s(headers: Mapping[str, str] | None) -> float | None:
    """Seconds the provider asked to wait (``retry-after-ms``, then a numeric ``Retry-After``)."""
    for name, scale in (("retry-after-ms", 1000.0), ("retry-after", 1.0)):
        raw = _header(headers, name)
        if raw is None:
            continue
        try:
            asked = float(raw) / scale
        except ValueError:
            continue  # e.g. an HTTP date: the exponential backoff applies
        if math.isfinite(asked) and asked >= 0:
            return asked
    return None


def retry_wait_s(attempt: int, headers: Mapping[str, str] | None = None) -> float:
    """Seconds to wait before retry ``attempt + 1``: what the provider asked, else exponential; capped."""
    asked = _asked_wait_s(headers)
    # min(attempt, 16): past the cap anyway, and 2**attempt never overflows a float.
    wait = RETRY_BACKOFF_S * (2 ** min(attempt, 16)) if asked is None else asked
    return min(MAX_BACKOFF_S, wait)


def retryable(exc: BaseException) -> tuple[bool, Mapping[str, str] | None]:
    """(retry this failure?, the response headers behind it) under the module's retry rules."""
    if isinstance(exc, anthropic.APITimeoutError):
        return False, None
    if isinstance(exc, anthropic.APIConnectionError):
        return True, None
    if isinstance(exc, anthropic.APIStatusError):
        response = getattr(exc, "response", None)
        headers = getattr(response, "headers", None)
        should = (_header(headers, "x-should-retry") or "").lower()
        if should in ("true", "false"):
            return should == "true", headers
        status = exc.status_code
        return status in RETRY_STATUSES or status >= 500, headers
    return False, None


class AnthropicVision:
    """``photos.VisionModel`` over the Anthropic Messages API."""

    def __init__(
        self,
        *,
        model: str | None = None,
        client: Any | None = None,
        max_tokens: int = 1500,
        timeout: float | None = None,
        max_retries: int | None = None,
        sleep: Callable[[float], None] = time.sleep,
        http_client: Any | None = None,
    ) -> None:
        """``http_client`` (an ``httpx2.Client``, what the SDK speaks) is for tests that drive the
        real SDK over a fake transport; ``sleep`` lets them record the retry waits."""
        if model is None:
            model = os.environ.get(MODEL_ENV)
        if not model:
            from agent.studio import DEFAULT_MODEL

            model = DEFAULT_MODEL
        self.model = model
        self.max_tokens = max_tokens
        if timeout is None or max_retries is None:
            limits = RunLimits.from_env()
            timeout = limits.model_timeout_s if timeout is None else timeout
            max_retries = limits.model_max_retries if max_retries is None else max_retries
        if not (isinstance(timeout, (int, float)) and math.isfinite(timeout) and timeout > 0):
            raise ValueError(f"timeout must be a number of seconds > 0, got {timeout!r}")
        if not isinstance(max_retries, int) or max_retries < 0:
            raise ValueError(f"max_retries must be a whole number >= 0, got {max_retries!r}")
        self.timeout = float(timeout)
        self.max_retries = max_retries
        self._sleep = sleep
        if client is None:
            if not os.environ.get(API_KEY_ENV):
                raise RuntimeError(f"{API_KEY_ENV} is not set; AnthropicVision needs it (or an injected client)")
            # max_retries=0: the SDK would obey any Retry-After uncapped; describe retries instead.
            extra: dict[str, Any] = {"http_client": http_client} if http_client is not None else {}
            client = anthropic.Anthropic(timeout=self.timeout, max_retries=0, **extra)
        self.client = client

    @property
    def worst_case_s(self) -> float:
        """The longest one ``describe`` call can take (see the module docstring)."""
        return worst_case_s(self.timeout, self.max_retries)

    def _create(self, **request: Any) -> Any:
        """``client.messages.create`` under the bounded retry rules of the module docstring."""
        attempt = 0
        while True:
            try:
                return self.client.messages.create(**request)
            except Exception as exc:
                retry, headers = retryable(exc)
                if not retry or attempt >= self.max_retries:
                    raise
                wait = retry_wait_s(attempt, headers)
                log.info("vision request failed (%s); retry %d in %.2fs", type(exc).__name__, attempt + 1, wait)
                self._sleep(wait)
                attempt += 1

    def describe(self, images: Sequence[bytes], prompt: str) -> str:
        if not images:
            raise ValueError("describe needs at least one image")
        content: list[dict[str, Any]] = []
        for data in images:
            payload, media_type = prepare_image(data)
            content.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": media_type,
                        "data": base64.b64encode(payload).decode("ascii"),
                    },
                }
            )
        content.append({"type": "text", "text": prompt})
        response = self._create(
            model=self.model,
            max_tokens=self.max_tokens,
            messages=[{"role": "user", "content": content}],
        )
        return _text_of(response)


__all__ = [
    "MAX_EDGE",
    "RETRY_STATUSES",
    "AnthropicVision",
    "prepare_image",
    "retry_wait_s",
    "retryable",
    "worst_case_s",
]
