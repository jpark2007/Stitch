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
- Decodes the first result and resizes it to exactly ``(w, h)`` with Pillow, keeping any
  alpha, and returns PNG bytes. The resize is a plain resize, so when ``w / h`` is far from
  the requested size's aspect (for example a 4:1 set layer) the art is stretched.

Errors raise ``RuntimeError`` with the HTTP status; the API key is never put in a message.
Tests inject an ``httpx.MockTransport``.
"""

from __future__ import annotations

import base64
import io
import math
import os
from collections.abc import Sequence
from typing import Any

import httpx
from PIL import Image

API_KEY_ENV = "IMAGE_API_KEY"
URL_ENV = "IMAGE_API_URL"
MODEL_ENV = "IMAGE_MODEL"
DEFAULT_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-image-1"

SUPPORTED_SIZES: tuple[tuple[int, int], ...] = ((1024, 1024), (1536, 1024), (1024, 1536))

_REFERENCE_TYPES = {"PNG": ("image/png", "png"), "JPEG": ("image/jpeg", "jpg"), "WEBP": ("image/webp", "webp")}
_ERROR_DETAIL_CHARS = 200


def closest_size(size: tuple[int, int]) -> tuple[int, int]:
    """The supported size whose aspect ratio is closest (in log space) to ``size``."""
    w, h = size
    if w <= 0 or h <= 0:
        raise ValueError(f"size must be positive, got {size!r}")
    target = math.log(w / h)
    return min(SUPPORTED_SIZES, key=lambda s: abs(math.log(s[0] / s[1]) - target))


def _has_alpha(im: Image.Image) -> bool:
    return im.mode in ("RGBA", "LA", "PA") or (im.mode == "P" and "transparency" in im.info)


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


def _to_png(data: bytes, size: tuple[int, int]) -> bytes:
    with Image.open(io.BytesIO(data)) as src:
        src.load()
        im = src.convert("RGBA" if _has_alpha(src) else "RGB")
    if im.size != size:
        im = im.resize(size, Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


class OpenAIImageModel:
    """``art.ImageModel`` over an OpenAI-compatible images API (provider is configurable)."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 120,
    ) -> None:
        key = api_key or os.environ.get(API_KEY_ENV)
        if not key and transport is None:
            raise RuntimeError(f"{API_KEY_ENV} is not set; OpenAIImageModel needs it (or an injected transport)")
        self._key = key or ""
        self.base_url = (base_url or os.environ.get(URL_ENV) or DEFAULT_URL).rstrip("/")
        self.model = model or os.environ.get(MODEL_ENV) or DEFAULT_MODEL
        self._transport = transport
        self._timeout = timeout
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        self._client = httpx.Client(base_url=self.base_url, headers=headers, timeout=timeout, transport=transport)

    def __repr__(self) -> str:  # never show the key
        return f"OpenAIImageModel(base_url={self.base_url!r}, model={self.model!r})"

    def close(self) -> None:
        self._client.close()

    def generate(
        self,
        prompt: str,
        *,
        reference_images: Sequence[bytes] = (),
        size: tuple[int, int] = (1024, 1024),
    ) -> bytes:
        out_size = (int(size[0]), int(size[1]))
        api_w, api_h = closest_size(out_size)
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
        try:
            response = self._client.post(path, **kwargs)
        except httpx.HTTPError as exc:
            raise RuntimeError(self._scrub(f"image API {path} request failed: {type(exc).__name__}")) from None
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
                with httpx.Client(transport=self._transport, timeout=self._timeout) as plain:
                    fetched = plain.get(url)
            except httpx.HTTPError:
                fetched = None
            if fetched is not None and fetched.status_code < 400:
                return fetched.content
            raise RuntimeError(f"image API {path} returned an image URL that could not be fetched")
        raise RuntimeError(f"image API {path} returned no image data")


__all__ = ["DEFAULT_MODEL", "DEFAULT_URL", "SUPPORTED_SIZES", "OpenAIImageModel", "closest_size"]
