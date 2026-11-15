"""OpenAIImageModel: the production ``art.ImageModel``, for an OpenAI-compatible images API.

The image provider is an assumption, not a decision: this speaks the OpenAI Images API
shape (``POST /images/generations`` and ``POST /images/edits``, base64 results in
``data[0].b64_json``), which several providers and gateways also serve. The base URL and
model are configurable so another compatible provider can be dropped in without code
changes:

- ``IMAGE_API_KEY``: bearer token (required unless a ``transport`` is injected).
- ``IMAGE_API_URL``: base URL, default ``https://api.openai.com/v1``.
- ``IMAGE_MODEL``: model id, default ``gpt-image-1``.

``generate(prompt, reference_images=(), size=(w, h))``:

- Asks for the supported size (1024x1024, 1536x1024 or 1024x1536) whose aspect ratio is
  closest to ``w / h``.
- With no references it calls ``/images/generations`` (JSON); with references it calls
  ``/images/edits`` (multipart, one ``image[]`` file per reference).
- Decodes the first result and fits it to exactly ``(w, h)`` with Pillow, keeping any alpha,
  and returns PNG bytes. The art is never scaled unevenly: when its aspect ratio is within 1%
  of ``w / h`` it is resized; otherwise it is scaled evenly until it covers ``(w, h)`` and then
  cropped to it, anchored at the bottom edge and centered horizontally (set layers stand on
  the ground line at the bottom, so a wide strip such as a 2048x464 middle layer keeps the
  bottom band of a 1536x1024 canvas and loses the top). The fitting lives in
  ``agent.adapters.fitting`` and is shared with ``HiggsfieldImageModel``.

``canvas_size(size)`` returns the size actually requested from the API for ``size``
(``closest_size``), so callers can tell the model which band of the canvas survives the crop
(``agent.tools.art.ArtService.make_layer`` does this).

Bounded, so a hung or failing provider cannot stall a run (the run itself is capped by
``STITCH_RUN_TIMEOUT_S``):

- Every request has a timeout: ``timeout`` seconds (default ``DEFAULT_TIMEOUT_S``, 120) to
  write the request and between bytes of the response, and at most ``CONNECT_TIMEOUT_S``
  (10) to connect.
- A request that fails fast and is safe to repeat is retried at most ``max_retries`` times
  (default ``DEFAULT_MAX_RETRIES``, 1): a connection that could not be made, or HTTP 408,
  429, 500, 502, 503 or 504. Each retry waits ``RETRY_BACKOFF_S`` doubling per attempt, or the
  response's numeric ``Retry-After``, never more than ``MAX_BACKOFF_S``. A read timeout is not
  retried: the provider may still be drawing (and billing) that image, and waiting again would
  double the worst case. So one ``generate`` takes at most about
  ``(1 + max_retries) * (CONNECT_TIMEOUT_S + timeout) + max_retries * MAX_BACKOFF_S`` seconds.

Errors raise ``RuntimeError`` with the HTTP status; the API key is never put in a message.
Tests inject an ``httpx.MockTransport`` (and a no-op ``sleep``).
"""

from __future__ import annotations

import base64
import io
import math
import os
import time
from collections.abc import Callable, Sequence
from typing import Any

import httpx
from PIL import Image

from agent.adapters.fitting import ASPECT_TOLERANCE, fit_to_size
from agent.adapters.fitting import has_alpha as _has_alpha
from agent.adapters.fitting import to_png as _to_png
from agent.guards import env_value

API_KEY_ENV = "IMAGE_API_KEY"
URL_ENV = "IMAGE_API_URL"
MODEL_ENV = "IMAGE_MODEL"
DEFAULT_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-image-1"

SUPPORTED_SIZES: tuple[tuple[int, int], ...] = ((1024, 1024), (1536, 1024), (1024, 1536))

DEFAULT_TIMEOUT_S = 120.0
"""Seconds to write a request and between bytes of the response (image models are slow)."""
CONNECT_TIMEOUT_S = 10.0
"""Seconds to open a connection."""
DEFAULT_MAX_RETRIES = 1
"""Retries of a request that failed fast (no connection, HTTP 408/429/5xx); never of a read timeout."""
RETRY_BACKOFF_S = 0.5
"""Wait before the first retry; doubles per attempt."""
MAX_BACKOFF_S = 8.0
"""Longest wait before one retry, whatever Retry-After asks."""
RETRY_STATUSES = frozenset({408, 429, 500, 502, 503, 504})

_REFERENCE_TYPES = {"PNG": ("image/png", "png"), "JPEG": ("image/jpeg", "jpg"), "WEBP": ("image/webp", "webp")}
_ERROR_DETAIL_CHARS = 200


def closest_size(size: tuple[int, int]) -> tuple[int, int]:
    """The supported size whose aspect ratio is closest (in log space) to ``size``."""
    w, h = size
    if w <= 0 or h <= 0:
        raise ValueError(f"size must be positive, got {size!r}")
    target = math.log(w / h)
    return min(SUPPORTED_SIZES, key=lambda s: abs(math.log(s[0] / s[1]) - target))


def _reference_file(index: int, data: bytes) -> tuple[str, bytes, str]:
    """(filename, bytes, content type) for one reference image; unusual formats become PNG."""
    try:
        with Image.open(io.BytesIO(data)) as src:
            fmt = (src.format or "").upper()
            if fmt in _REFERENCE_TYPES:
                content_type, ext = _REFERENCE_TYPES[fmt]
                return f"reference_{index}.{ext}", data, content_type
            src.load()
            im = src.convert("RGBA" if _has_alpha(src) else "RGB")
    except Exception as exc:
        raise ValueError(f"reference image {index} is not a readable image") from exc
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return f"reference_{index}.png", buf.getvalue(), "image/png"


def retryable_error(exc: httpx.HTTPError) -> bool:
    """A transport failure worth one more try: the connection was never made (so nothing ran)."""
    return isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout))


def backoff_s(attempt: int, response: httpx.Response | None) -> float:
    """Seconds to wait before retry ``attempt + 1``: a numeric Retry-After, else exponential; capped."""
    wait = RETRY_BACKOFF_S * (2**attempt)
    if response is not None:
        raw = response.headers.get("retry-after", "").strip()
        try:
            asked = float(raw)
        except ValueError:
            asked = None
        if asked is not None and math.isfinite(asked) and asked >= 0:
            wait = asked
    return min(MAX_BACKOFF_S, wait)


def _given(value: str | None) -> str | None:
    """An explicit constructor argument, stripped; None when it is None or blank."""
    return (value.strip() or None) if value is not None else None


class OpenAIImageModel:
    """``art.ImageModel`` over an OpenAI-compatible images API (provider is configurable)."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        transport: httpx.BaseTransport | None = None,
        timeout: float = DEFAULT_TIMEOUT_S,
        max_retries: int = DEFAULT_MAX_RETRIES,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not (isinstance(timeout, (int, float)) and math.isfinite(timeout) and timeout > 0):
            raise ValueError(f"timeout must be a number of seconds > 0, got {timeout!r}")
        if not isinstance(max_retries, int) or max_retries < 0:
            raise ValueError(f"max_retries must be a whole number >= 0, got {max_retries!r}")
        key = api_key or os.environ.get(API_KEY_ENV)
        if not key and transport is None:
            raise RuntimeError(f"{API_KEY_ENV} is not set; OpenAIImageModel needs it (or an injected transport)")
        self._key = key or ""
        # A blank or whitespace IMAGE_API_URL / IMAGE_MODEL means the default; a padded one is used stripped.
        self.base_url = (_given(base_url) or env_value(None, URL_ENV) or DEFAULT_URL).rstrip("/")
        self.model = _given(model) or env_value(None, MODEL_ENV) or DEFAULT_MODEL
        self._transport = transport
        self.max_retries = max_retries
        self._sleep = sleep
        self.timeout = httpx.Timeout(float(timeout), connect=min(CONNECT_TIMEOUT_S, float(timeout)))
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        self._client = httpx.Client(base_url=self.base_url, headers=headers, timeout=self.timeout, transport=transport)

    def __repr__(self) -> str:  # never show the key
        return f"OpenAIImageModel(base_url={self.base_url!r}, model={self.model!r})"

    def close(self) -> None:
        self._client.close()

    def canvas_size(self, size: tuple[int, int]) -> tuple[int, int]:
        """The size the API is asked for when ``generate`` is called with ``size``."""
        return closest_size((int(size[0]), int(size[1])))

    def generate(
        self,
        prompt: str,
        *,
        reference_images: Sequence[bytes] = (),
        size: tuple[int, int] = (1024, 1024),
    ) -> bytes:
        out_size = (int(size[0]), int(size[1]))
        api_w, api_h = self.canvas_size(out_size)
        api_size = f"{api_w}x{api_h}"
        if reference_images:
            path = "/images/edits"
            files = [("image[]", _reference_file(i, data)) for i, data in enumerate(reference_images)]
            form = {"model": self.model, "prompt": prompt, "size": api_size, "n": "1"}
            response = self._post(path, data=form, files=files)
        else:
            path = "/images/generations"
            body = {"model": self.model, "prompt": prompt, "size": api_size, "n": 1}
            response = self._post(path, json=body)
        return _to_png(self._first_image(path, response), out_size)

    # ------------------------------------------------------------------ internals

    def _scrub(self, text: str) -> str:
        return text.replace(self._key, "[redacted]") if self._key else text

    def _post(self, path: str, **kwargs: Any) -> httpx.Response:
        """POST with the bounded retry policy of the module docstring."""
        attempt = 0
        while True:
            try:
                response = self._client.post(path, **kwargs)
            except httpx.HTTPError as exc:
                if attempt < self.max_retries and retryable_error(exc):
                    self._sleep(backoff_s(attempt, None))
                    attempt += 1
                    continue
                raise RuntimeError(self._scrub(f"image API {path} request failed: {type(exc).__name__}")) from None
            if response.status_code in RETRY_STATUSES and attempt < self.max_retries:
                self._sleep(backoff_s(attempt, response))
                attempt += 1
                continue
            if response.status_code >= 400:
                raise RuntimeError(
                    self._scrub(f"image API {path} returned HTTP {response.status_code}{self._detail(response)}")
                )
            return response

    def _detail(self, response: httpx.Response) -> str:
        try:
            err = response.json().get("error")
            message = err.get("message") if isinstance(err, dict) else err
        except Exception:  # noqa: BLE001 - the body is not JSON or not the usual shape
            return ""
        if not isinstance(message, str) or not message.strip():
            return ""
        return f": {self._scrub(message.strip())[:_ERROR_DETAIL_CHARS]}"

    def _first_image(self, path: str, response: httpx.Response) -> bytes:
        try:
            item = response.json()["data"][0]
        except Exception:  # noqa: BLE001
            raise RuntimeError(f"image API {path} returned no image data") from None
        b64 = item.get("b64_json") if isinstance(item, dict) else None
        if isinstance(b64, str) and b64:
            try:
                return base64.b64decode(b64, validate=True)
            except Exception:  # noqa: BLE001
                raise RuntimeError(f"image API {path} returned invalid base64 image data") from None
        url = item.get("url") if isinstance(item, dict) else None
        if isinstance(url, str) and url.startswith(("https://", "http://")):
            # Some compatible providers return a short-lived URL instead of base64. It is a
            # different host, so it is fetched without our Authorization header.
            try:
                with httpx.Client(transport=self._transport, timeout=self.timeout) as plain:
                    fetched = plain.get(url)
            except httpx.HTTPError:
                fetched = None
            if fetched is not None and fetched.status_code < 400:
                return fetched.content
            raise RuntimeError(f"image API {path} returned an image URL that could not be fetched")
        raise RuntimeError(f"image API {path} returned no image data")


__all__ = [
    "ASPECT_TOLERANCE",
    "CONNECT_TIMEOUT_S",
    "DEFAULT_MAX_RETRIES",
    "DEFAULT_MODEL",
    "DEFAULT_TIMEOUT_S",
    "DEFAULT_URL",
    "MAX_BACKOFF_S",
    "RETRY_BACKOFF_S",
    "RETRY_STATUSES",
    "SUPPORTED_SIZES",
    "OpenAIImageModel",
    "backoff_s",
    "closest_size",
    "fit_to_size",
    "retryable_error",
]
