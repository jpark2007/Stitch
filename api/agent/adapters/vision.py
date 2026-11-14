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

Bounded like the studio model, so a hung vision call cannot stall a run: the SDK client is
built with ``timeout`` seconds per request and ``max_retries`` retries (the SDK retries
connection errors, timeouts and HTTP 408/409/429/5xx with backoff). Both default to the run
limits for Claude requests, ``agent.guards.RunLimits.model_timeout_s`` and
``model_max_retries`` (env ``STITCH_MODEL_TIMEOUT_S``, default 120, and
``STITCH_MODEL_MAX_RETRIES``, default 2). An injected ``client`` keeps its own settings.
"""

from __future__ import annotations

import base64
import io
import math
import os
from collections.abc import Sequence
from typing import Any

from PIL import Image, ImageOps

from agent.guards import RunLimits

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
    ) -> None:
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
        if client is None:
            if not os.environ.get(API_KEY_ENV):
                raise RuntimeError(f"{API_KEY_ENV} is not set; AnthropicVision needs it (or an injected client)")
            import anthropic

            client = anthropic.Anthropic(timeout=self.timeout, max_retries=self.max_retries)
        self.client = client

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
        response = self.client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            messages=[{"role": "user", "content": content}],
        )
        return _text_of(response)


__all__ = ["MAX_EDGE", "AnthropicVision", "prepare_image"]
