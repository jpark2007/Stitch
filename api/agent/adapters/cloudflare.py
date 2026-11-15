"""CloudflareImageModel: ``art.ImageModel`` over Cloudflare Workers AI text-to-image models.

Built against the official docs (read 2026-11-14):

- REST call, auth, response envelope: https://developers.cloudflare.com/workers-ai/get-started/rest-api/
  (``POST {base}/accounts/{account_id}/ai/run/{model}``, ``Authorization: Bearer {token}``,
  JSON answers ``{"result": {...}, "success", "errors", "messages"}``)
- Errors: https://developers.cloudflare.com/workers-ai/platform/errors/ (``3036``, HTTP 429:
  "You have used up your daily free allocation of 10,000 neurons"; ``3040``, HTTP 429: "Capacity
  temporarily exceeded, please try again"; ``3007``/``3008``, HTTP 408)
- Prices and the free allowance: https://developers.cloudflare.com/workers-ai/platform/pricing/
  (10,000 Neurons a day free on every plan, reset at 00:00 UTC; $0.011 per 1,000 Neurons after)
- FLUX.2 [klein] 4B: https://developers.cloudflare.com/workers-ai/models/flux-2-klein-4b/ and
  https://developers.cloudflare.com/changelog/post/2026-01-15-flux-2-klein-4b-workers-ai/
- FLUX.2 [klein] 9B: https://developers.cloudflare.com/changelog/post/2026-01-28-flux-2-klein-9b-workers-ai/
- Leonardo Phoenix 1.0: https://developers.cloudflare.com/workers-ai/models/phoenix-1.0/ (schema-input.json,
  schema-output.json)
- Model search (preflight --live): https://developers.cloudflare.com/api/resources/ai/subresources/models/methods/list/

What the docs say, and what this adapter does with it:

- The FLUX.2 models take ``multipart/form-data`` "even if you just have a prompt": fields
  ``prompt``, ``width`` and ``height`` (256-1920, defaults 1024x768), ``guidance``, ``seed`` and up
  to four reference images ``input_image_0`` .. ``input_image_3`` ("must be smaller than 512x512");
  steps are fixed at 4. They answer JSON with ``image`` as a base64 string.
- Phoenix 1.0 takes a JSON body (``prompt``, ``width``/``height`` up to 2048, ``num_steps``,
  ``guidance``, ``negative_prompt``) and answers the raw JPEG bytes (``contentType: image/jpeg``).
- FLUX.1 [schnell] is not offered: its schema caps the prompt at 2048 characters, and the
  studio's art prompt (the locked style prompt plus the subject) is longer than that.

``generate(prompt, reference_images=(), size=(w, h))``:

1. Asks for the canvas ``request_size(size)``: the aspect of ``(w, h)`` with the long side
   ``TARGET_LONG_SIDE`` (1024), the short side at least the model's minimum (256), both at most
   the model's maximum and multiples of 16. The prompt gets ``CLOUDFLARE_STYLE_SUFFIX`` appended
   (``higgsfield.DEFAULT_STYLE_SUFFIX`` unless set; ``none`` turns it off).
2. FLUX.2: up to four reference images go along as ``input_image_N`` PNGs scaled to fit inside
   ``REFERENCE_MAX_SIDE`` (511) pixels; an unreadable or oversized reference is skipped with a
   warning (a prop never fails over a reference). Phoenix takes no reference: they are ignored.
3. One POST. A connection that could not be made, HTTP 408, a 429 that is not the daily
   allowance, or a 5xx is retried at most ``max_retries`` times (default 2), waiting
   ``images.backoff_s`` (Retry-After honoured, capped at 8 s), and only when the wait fits the
   deadline. A read timeout is never retried. Redirects are never followed (the token goes only
   to the configured API host): a 3xx is an error.
4. The answer is read with ``Accept-Encoding: identity`` in chunks, at most ``MAX_RESPONSE_BYTES``
   (25 MB) after decoding (a gzip answer is inflated with a bounded output; any other content
   encoding is refused), checking the deadline between chunks. An ``image/*`` answer is the image;
   a JSON answer must carry ``result.image`` as base64.
5. Decodes with Pillow (refusing more than ``fitting.MAX_PIXELS`` pixels, a decompression bomb)
   and fits to exactly ``(w, h)`` with ``fitting.to_png``, the same rule as the other adapters.

The daily free allowance being used up (error ``3036``, HTTP 429) raises
``CloudflareAllowanceExhausted`` at once, with no retry: ``ArtService`` then draws placeholder
art for that image, and every later image until the allowance resets at 00:00 UTC.

The whole ``generate`` is bounded by ``deadline_s`` (120 s by default). Before every request the
time left is computed; each request's timeout is the smaller of its own limit
(``REQUEST_TIMEOUT_S`` 90 s, connect ``CONNECT_TIMEOUT_S`` 10 s) and the time left.

``CLOUDFLARE_API_URL`` must be https (the token is sent on every call), except for ``localhost``,
``127.0.0.1`` or ``::1``. ``CLOUDFLARE_ACCOUNT_ID`` must be letters and digits only (it is put in
the URL path). Otherwise the model refuses to construct.

Not verified from the official docs (each is configurable or fails loudly):

- A prompt length limit for the FLUX.2 and Phoenix models: their schemas state none.
- Whether a FLUX.2 width or height must be a multiple of 16: this adapter always sends one.
- How a partial 512x512 tile is billed (rounded up, or by area): the README gives both.

Env:

- ``CLOUDFLARE_ACCOUNT_ID``: the account ID (dashboard, Workers AI, "Use REST API").
- ``CLOUDFLARE_API_TOKEN``: an API token with Workers AI Read and Edit.
- ``CLOUDFLARE_API_URL``: base URL, default ``https://api.cloudflare.com/client/v4``.
- ``CLOUDFLARE_IMAGE_MODEL``: model id, default ``@cf/black-forest-labs/flux-2-klein-4b``; one of ``MODELS``.
- ``CLOUDFLARE_STYLE_SUFFIX``: text appended to every prompt; ``none`` for no suffix.

Errors raise ``RuntimeError`` with the HTTP status and a short provider message; the token and
the account ID are never in a message or a log. Tests inject an ``httpx.MockTransport``, a no-op
``sleep`` and a fake ``clock``.
"""

from __future__ import annotations

import base64
import binascii
import io
import json
import logging
import math
import os
import re
import threading
import time
import zlib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import httpx
from PIL import Image

from agent.adapters.fitting import MAX_PIXELS, has_alpha, to_png
from agent.adapters.higgsfield import DEFAULT_STYLE_SUFFIX as _HF_STYLE_SUFFIX
from agent.adapters.images import CONNECT_TIMEOUT_S, RETRY_STATUSES, backoff_s, retryable_error
from agent.guards import env_value

log = logging.getLogger(__name__)

ACCOUNT_ENV = "CLOUDFLARE_ACCOUNT_ID"
TOKEN_ENV = "CLOUDFLARE_API_TOKEN"
URL_ENV = "CLOUDFLARE_API_URL"
MODEL_ENV = "CLOUDFLARE_IMAGE_MODEL"
STYLE_ENV = "CLOUDFLARE_STYLE_SUFFIX"

DEFAULT_URL = "https://api.cloudflare.com/client/v4"
DEFAULT_MODEL = "@cf/black-forest-labs/flux-2-klein-4b"
DEFAULT_STYLE_SUFFIX = _HF_STYLE_SUFFIX
"""The same flat cut-paper hint Higgsfield prompts get; ``none`` turns it off."""
STYLE_OFF = "none"

DEFAULT_DEADLINE_S = 120.0
"""Seconds for one whole ``generate``: every attempt and every wait between them."""
REQUEST_TIMEOUT_S = 90.0
"""Seconds to write one request and between bytes of its answer (the answer is the image)."""
DEFAULT_MAX_RETRIES = 2
"""Repeats of a request that failed fast (no connection, HTTP 408, 429 capacity, 5xx)."""
MAX_RESPONSE_BYTES = 25 * 1024 * 1024
MAX_ERROR_BYTES = 64 * 1024
"""An error answer is read only this far (it is a short JSON message)."""
TARGET_LONG_SIDE = 1024
SIZE_STEP = 16
REFERENCE_MAX_SIDE = 511
"""The docs: reference images "must be smaller than 512x512"."""
MAX_REFERENCES = 4
LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
"""Hosts CLOUDFLARE_API_URL may use plain http with (a local stub; the token never leaves the machine)."""

ALLOWANCE_CODE = 3036
"""Workers AI error code: the daily free allocation of 10,000 Neurons is used up (HTTP 429)."""
_ALLOWANCE_TEXT = "daily free allocation"
_ACCOUNT_ID = re.compile(r"^[A-Za-z0-9]{1,64}$")
_ACCOUNT_ID_USUAL = re.compile(r"^[0-9a-f]{32}$")
_ERROR_DETAIL_CHARS = 200


class CloudflareAllowanceExhausted(RuntimeError):
    """The account's free daily Neurons are used up; no image until 00:00 UTC (or Workers Paid)."""


@dataclass(frozen=True)
class ModelSpec:
    """One documented text-to-image model: how its request is sent and its size range."""

    multipart: bool
    """True: multipart/form-data fields (FLUX.2). False: a JSON body."""
    min_side: int
    max_side: int
    references: bool
    """Takes ``input_image_0`` .. ``input_image_3``."""
    params: Mapping[str, Any] = field(default_factory=dict)
    answer: str = "base64 JSON"
    """'base64 JSON' (``result.image``) or 'raw image' (the body is the image)."""
    doc: str = ""


MODELS: dict[str, ModelSpec] = {
    "@cf/black-forest-labs/flux-2-klein-4b": ModelSpec(
        multipart=True,
        min_side=256,
        max_side=1920,
        references=True,
        doc="https://developers.cloudflare.com/changelog/post/2026-01-15-flux-2-klein-4b-workers-ai/",
    ),
    "@cf/black-forest-labs/flux-2-klein-9b": ModelSpec(
        multipart=True,
        min_side=256,
        max_side=1920,
        references=True,
        doc="https://developers.cloudflare.com/changelog/post/2026-01-28-flux-2-klein-9b-workers-ai/",
    ),
    "@cf/leonardo/phoenix-1.0": ModelSpec(
        multipart=False,
        min_side=256,
        max_side=2048,
        references=False,
        answer="raw image",
        doc="https://developers.cloudflare.com/workers-ai/models/phoenix-1.0/",
    ),
}
"""The models whose request this adapter knows. Others are refused, not guessed."""


# ------------------------------------------------------------------------------ config


def configured_model(env: Mapping[str, str] | None = None) -> str:
    """The model id in use: ``CLOUDFLARE_IMAGE_MODEL`` or ``DEFAULT_MODEL``."""
    return (env_value(env, MODEL_ENV) or DEFAULT_MODEL).strip("/")


def configured_base_url(env: Mapping[str, str] | None = None) -> str:
    return (env_value(env, URL_ENV) or DEFAULT_URL).rstrip("/")


def configured_style_suffix(env: Mapping[str, str] | None = None) -> str:
    """The prompt suffix in use ('' when ``CLOUDFLARE_STYLE_SUFFIX=none``)."""
    raw = env_value(env, STYLE_ENV)
    if raw is None:
        return DEFAULT_STYLE_SUFFIX
    return "" if raw.lower() == STYLE_OFF else raw


def url_problem(url: str) -> str | None:
    """Why ``url`` cannot be the API base URL: not https (unless the host is local), or not a URL."""
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        has_credentials = bool(parts.username or parts.password)
    except ValueError:
        return "is not a valid URL"
    if not host or parts.scheme not in ("https", "http"):
        return "must be an https:// URL"
    if has_credentials:
        return "must not carry user:password@ (the token is sent as a header)"
    if parts.query or parts.fragment:
        return "must not have a query string or fragment"
    if parts.scheme == "http" and host not in LOCAL_HOSTS:
        return "must be https (the API token is sent on every call); plain http only for localhost"
    return None


def account_problem(account: str) -> str | None:
    """Why ``account`` cannot be an account ID (it goes in the URL path); None when it can."""
    if not _ACCOUNT_ID.match(account):
        return "must be letters and digits only (the 32-character Account ID from the dashboard)"
    return None


def account_looks_unusual(account: str) -> bool:
    """Cloudflare account IDs are 32 lowercase hex characters; anything else is worth a warning."""
    return not _ACCOUNT_ID_USUAL.match(account)


def token_problem(token: str) -> str | None:
    if not token.isascii() or not token.isprintable() or any(ch.isspace() for ch in token):
        return "contains a non-ASCII, whitespace or control character (re-copy it from the dashboard)"
    return None


def credentials(env: Mapping[str, str] | None = None) -> tuple[str, str] | None:
    """(account id, token) from the env, or None when either is missing."""
    account = env_value(env, ACCOUNT_ENV)
    token = env_value(env, TOKEN_ENV)
    if not account or not token:
        return None
    return account, token


def config_problem(env: Mapping[str, str] | None = None) -> str | None:
    """Why ``CloudflareImageModel()`` cannot be built from ``env`` (None when it can)."""
    account = env_value(env, ACCOUNT_ENV)
    token = env_value(env, TOKEN_ENV)
    if not account:
        return f"{ACCOUNT_ENV} is not set"
    if not token:
        return f"{TOKEN_ENV} is not set"
    if problem := account_problem(account):
        return f"{ACCOUNT_ENV} {problem}"
    if problem := token_problem(token):
        return f"{TOKEN_ENV} {problem}"
    model = configured_model(env)
    if model not in MODELS:
        return f"{MODEL_ENV}={model!r} is not a supported model; use one of: {', '.join(MODELS)}"
    if problem := url_problem(configured_base_url(env)):
        return f"{URL_ENV} {problem}"
    return None


def authorization(token: str) -> dict[str, str]:
    """The documented auth header."""
    return {"Authorization": f"Bearer {token}"}


def run_path(account: str, model: str) -> str:
    return f"/accounts/{account}/ai/run/{model}"


def search_path(account: str) -> str:
    """The model search endpoint (preflight --live): proves the token and account, draws nothing."""
    return f"/accounts/{account}/ai/models/search"


def _round_step(value: float) -> int:
    return max(SIZE_STEP, int(round(value / SIZE_STEP)) * SIZE_STEP)


def request_size(size: tuple[int, int], spec: ModelSpec) -> tuple[int, int]:
    """The canvas asked for ``size``: its aspect, long side TARGET_LONG_SIDE, within the model's
    range and a multiple of SIZE_STEP on both sides (only the aspect matters to callers)."""
    w, h = int(size[0]), int(size[1])
    if w <= 0 or h <= 0:
        raise ValueError(f"size must be positive, got {size!r}")
    ratio = w / h
    lo, hi = spec.min_side, spec.max_side
    if ratio >= 1:
        long_side, short_side = float(TARGET_LONG_SIDE), TARGET_LONG_SIDE / ratio
    else:
        long_side, short_side = float(TARGET_LONG_SIDE), TARGET_LONG_SIDE * ratio
    if short_side < lo:  # a wide strip: grow both sides to keep the aspect, up to the maximum
        long_side = min(float(hi), lo * max(ratio, 1 / ratio))
        short_side = float(lo)
    # Every model's bounds are multiples of SIZE_STEP, so clamping keeps both sides on the step.
    long_px = min(hi, max(lo, _round_step(long_side)))
    short_px = min(hi, max(lo, _round_step(short_side)))
    return (long_px, short_px) if ratio >= 1 else (short_px, long_px)


class _ScrubHTTPLogs(logging.Filter):
    """httpx logs every request's URL at INFO ("HTTP Request: POST https://.../accounts/{id}/..."),
    and the account ID is in that path: this rewrites those records without any registered secret."""

    def __init__(self) -> None:
        super().__init__()
        self.secrets: set[str] = set()
        self.lock = threading.Lock()

    def add(self, *secrets: str) -> None:
        with self.lock:
            self.secrets.update(s for s in secrets if len(s) >= 4)  # shorter ones would mangle every line

    def filter(self, record: logging.LogRecord) -> bool:
        with self.lock:
            secrets = sorted(self.secrets, key=len, reverse=True)
        if not secrets:
            return True
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 - a malformed record still must not leak its arguments
            message = str(record.msg)
        pattern = "|".join(re.escape(s) for s in secrets)
        scrubbed = re.sub(pattern, "[redacted]", message, flags=re.IGNORECASE)
        if scrubbed != message:
            record.msg, record.args = scrubbed, ()
        return True


_http_log_scrubber = _ScrubHTTPLogs()
for _name in ("httpx", "httpcore"):
    logging.getLogger(_name).addFilter(_http_log_scrubber)


_reference_warned = False
_reference_lock = threading.Lock()


def _warn_references_once(text: str) -> None:
    global _reference_warned  # noqa: PLW0603
    with _reference_lock:
        if _reference_warned:
            return
        _reference_warned = True
    log.warning("Cloudflare: %s (warned once per process)", text)


def reference_png(data: bytes) -> bytes:
    """``data`` as a PNG that fits inside REFERENCE_MAX_SIDE pixels; ValueError when unreadable."""
    try:
        with Image.open(io.BytesIO(data)) as src:
            if src.width * src.height > MAX_PIXELS:
                raise ValueError(f"reference is {src.width}x{src.height}, more than {MAX_PIXELS} pixels")
            src.load()
            im = src.convert("RGBA" if has_alpha(src) else "RGB")
    except ValueError:
        raise
    except Exception as exc:  # noqa: BLE001 - any decode failure means the reference is unusable
        raise ValueError("reference is not a readable image") from exc
    im.thumbnail((REFERENCE_MAX_SIDE, REFERENCE_MAX_SIDE), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


# ------------------------------------------------------------------------------- model


class CloudflareImageModel:
    """``art.ImageModel`` and ``art.CanvasSizer`` over Cloudflare Workers AI."""

    def __init__(
        self,
        *,
        account_id: str | None = None,
        api_token: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        style_suffix: str | None = None,
        transport: httpx.BaseTransport | None = None,
        deadline_s: float = DEFAULT_DEADLINE_S,
        max_retries: int = DEFAULT_MAX_RETRIES,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not (isinstance(deadline_s, (int, float)) and math.isfinite(deadline_s) and deadline_s > 0):
            raise ValueError(f"deadline_s must be a number of seconds > 0, got {deadline_s!r}")
        if not isinstance(max_retries, int) or max_retries < 0:
            raise ValueError(f"max_retries must be a whole number >= 0, got {max_retries!r}")
        env: dict[str, str] = dict(os.environ)
        if account_id is not None:
            env[ACCOUNT_ENV] = account_id
        if api_token is not None:
            env[TOKEN_ENV] = api_token
        account = env_value(env, ACCOUNT_ENV)
        token = env_value(env, TOKEN_ENV)
        if not account:
            raise RuntimeError(f"{ACCOUNT_ENV} is not set; CloudflareImageModel needs it")
        if not token:
            raise RuntimeError(f"{TOKEN_ENV} is not set; CloudflareImageModel needs it")
        if problem := account_problem(account):
            raise RuntimeError(f"{ACCOUNT_ENV} {problem}")
        if problem := token_problem(token):
            raise RuntimeError(f"{TOKEN_ENV} {problem}")
        self._account = account
        self._token = token
        _http_log_scrubber.add(account, token)
        self.base_url = ((base_url or "").strip() or configured_base_url(env)).rstrip("/")
        if problem := url_problem(self.base_url):
            raise RuntimeError(f"{URL_ENV} {problem}")
        self.model = model.strip().strip("/") if model and model.strip() else configured_model(env)
        if self.model not in MODELS:
            raise RuntimeError(
                f"{MODEL_ENV}={self.model!r} is not a supported model; use one of: {', '.join(MODELS)}"
            )
        self.spec = MODELS[self.model]
        if style_suffix is None:
            self.style_suffix = configured_style_suffix(env)
        else:
            self.style_suffix = "" if style_suffix.strip().lower() == STYLE_OFF else style_suffix.strip()
        self.deadline_s = float(deadline_s)
        self.max_retries = max_retries
        self._sleep = sleep
        self._clock = clock
        self._client = httpx.Client(
            base_url=self.base_url,
            headers={**authorization(token), "Accept-Encoding": "identity"},
            timeout=httpx.Timeout(REQUEST_TIMEOUT_S, connect=CONNECT_TIMEOUT_S),
            transport=transport,
            follow_redirects=False,
        )

    def __repr__(self) -> str:  # never show the token or the account
        return f"CloudflareImageModel(base_url={self.base_url!r}, model={self.model!r})"

    def close(self) -> None:
        self._client.close()

    def canvas_size(self, size: tuple[int, int]) -> tuple[int, int]:
        """The width and height sent for ``size`` (the model draws exactly that canvas)."""
        return request_size(size, self.spec)

    def full_prompt(self, prompt: str) -> str:
        """``prompt`` with the style suffix appended (when there is one)."""
        return f"{prompt}\n\n{self.style_suffix}" if self.style_suffix else prompt

    def generate(
        self,
        prompt: str,
        *,
        reference_images: Sequence[bytes] = (),
        size: tuple[int, int] = (1024, 1024),
    ) -> bytes:
        out_size = (int(size[0]), int(size[1]))
        deadline = self._clock() + self.deadline_s
        width, height = self.canvas_size(out_size)
        text = self.full_prompt(prompt)
        if self.spec.multipart:
            fields: list[tuple[str, Any]] = [
                ("prompt", (None, text)),
                ("width", (None, str(width))),
                ("height", (None, str(height))),
            ]
            fields += [
                (f"input_image_{i}", (f"reference_{i}.png", data, "image/png"))
                for i, data in enumerate(self._references(reference_images))
            ]
            request: dict[str, Any] = {"files": fields}
        else:
            if reference_images:
                _warn_references_once(f"{self.model} takes no reference image: references are ignored")
            request = {"json": {"prompt": text, "width": width, "height": height, **self.spec.params}}
        data = self._run(request, deadline)
        try:
            return to_png(data, out_size)
        except ValueError as exc:  # more than MAX_PIXELS: a decompression bomb
            raise RuntimeError(f"Cloudflare {self.model} answer refused: {exc}") from None
        except Exception:  # noqa: BLE001 - not an image Pillow can read
            raise RuntimeError(f"Cloudflare {self.model} answer is not a readable image") from None

    # ------------------------------------------------------------------ internals

    def _references(self, reference_images: Sequence[bytes]) -> list[bytes]:
        """At most MAX_REFERENCES usable references, each under 512x512; bad ones are skipped."""
        out: list[bytes] = []
        if len(reference_images) > MAX_REFERENCES:
            _warn_references_once(f"{self.model} takes at most {MAX_REFERENCES} reference images; the rest are ignored")
        for index, data in enumerate(reference_images[:MAX_REFERENCES]):
            try:
                out.append(reference_png(data))
            except ValueError as exc:
                log.warning("Cloudflare: reference image %d skipped: %s", index, exc)
        return out

    def _left(self, deadline: float) -> float:
        return deadline - self._clock()

    def _timeout(self, left: float) -> httpx.Timeout:
        """A request timeout that never runs past the time ``left``."""
        return httpx.Timeout(min(REQUEST_TIMEOUT_S, left), connect=min(CONNECT_TIMEOUT_S, left))

    def _scrub(self, text: str) -> str:
        for secret in sorted({self._token, self._account}, key=len, reverse=True):
            if secret:
                text = re.sub(re.escape(secret), "[redacted]", text, flags=re.IGNORECASE)
        return text

    def _errors(self, body: bytes) -> list[dict[str, Any]]:
        try:
            errors = json.loads(body).get("errors")
        except Exception:  # noqa: BLE001 - not JSON or not the usual envelope
            return []
        return [e for e in errors if isinstance(e, dict)] if isinstance(errors, list) else []

    def _detail(self, body: bytes) -> str:
        """The provider's first error message (and code), scrubbed and cut."""
        for error in self._errors(body):
            message = error.get("message")
            code = error.get("code")
            if isinstance(message, str) and message.strip():
                prefix = f"{code} " if isinstance(code, int) else ""
                return f": {prefix}{self._scrub(message.strip())[:_ERROR_DETAIL_CHARS]}"
        return ""

    def _allowance_exhausted(self, status: int, body: bytes) -> bool:
        if status != 429:
            return False
        for error in self._errors(body):
            message = error.get("message")
            if error.get("code") == ALLOWANCE_CODE or (isinstance(message, str) and _ALLOWANCE_TEXT in message.lower()):
                return True
        return False

    def _fail(self, status: int, body: bytes) -> RuntimeError:
        text = f"Cloudflare {self.model} returned HTTP {status}{self._detail(body)}"
        if self._allowance_exhausted(status, body):
            return CloudflareAllowanceExhausted(
                self._scrub(
                    f"{text} (the free daily allowance is used up: placeholder art until it resets at 00:00 UTC)"
                )
            )
        return RuntimeError(self._scrub(text))

    def _out_of_time(self, what: str) -> RuntimeError:
        return RuntimeError(f"Cloudflare {self.model}: no time left for {what} within {self.deadline_s:g} s")

    def _read(self, response: httpx.Response, deadline: float, limit: int) -> bytes:
        """The answer's body, decoded, at most ``limit`` bytes, all of it within ``deadline``."""
        too_big = RuntimeError(f"Cloudflare {self.model} answer refused: larger than {limit // 1024} KB")
        encoding = response.headers.get("content-encoding", "").strip().lower()
        if encoding not in ("", "identity", "gzip"):
            raise RuntimeError(f"Cloudflare {self.model} answer refused: content encoding {encoding!r}")
        declared = response.headers.get("content-length", "").strip()
        if encoding in ("", "identity") and declared.isdigit() and int(declared) > limit:
            raise too_big
        inflate = zlib.decompressobj(16 + zlib.MAX_WBITS) if encoding == "gzip" else None
        chunks: list[bytes] = []
        total = 0
        try:
            for raw in response.iter_raw():
                if self._left(deadline) <= 0:
                    raise RuntimeError(f"Cloudflare {self.model} answer did not finish within {self.deadline_s:g} s")
                # Inflating is bounded: never more than one byte past the limit per chunk.
                data = inflate.decompress(raw, limit + 1 - total) if inflate is not None else raw
                total += len(data)
                if total > limit or (inflate is not None and inflate.unconsumed_tail):
                    raise too_big
                chunks.append(data)
            if inflate is not None:
                tail = inflate.flush()
                total += len(tail)
                if total > limit:
                    raise too_big
                chunks.append(tail)
        except zlib.error:
            raise RuntimeError(f"Cloudflare {self.model} answer refused: bad gzip data") from None
        return b"".join(chunks)

    def _run(self, request: dict[str, Any], deadline: float) -> bytes:
        """POST the generation with the bounded retry policy; the image bytes it answered."""
        path = run_path(self._account, self.model)
        attempt = 0
        while True:
            left = self._left(deadline)
            if left <= 0:
                raise self._out_of_time("the request" if attempt == 0 else "a retry")
            wait: float | None = None
            try:
                with self._client.stream("POST", path, timeout=self._timeout(left), **request) as response:
                    status = response.status_code
                    if status >= 300:
                        body = self._read(response, deadline, MAX_ERROR_BYTES) if status >= 400 else b""
                        if 300 <= status < 400:
                            raise RuntimeError(
                                f"Cloudflare {self.model} answered a redirect (HTTP {status}); redirects are not followed"
                            )
                        retryable = status in RETRY_STATUSES and not self._allowance_exhausted(status, body)
                        if not retryable or attempt >= self.max_retries:
                            raise self._fail(status, body)
                        wait = backoff_s(attempt, response)
                        if self._left(deadline) <= wait:
                            raise self._fail(status, body)
                    else:
                        ctype = response.headers.get("content-type", "").split(";")[0].strip().lower()
                        body = self._read(response, deadline, MAX_RESPONSE_BYTES)
                        return self._image(ctype, body)
            except httpx.HTTPError as exc:
                if attempt < self.max_retries and retryable_error(exc):
                    wait = backoff_s(attempt, None)
                    if self._left(deadline) <= wait:
                        raise self._out_of_time("a retry") from None
                else:
                    raise RuntimeError(
                        self._scrub(f"Cloudflare {self.model} request failed: {type(exc).__name__}")
                    ) from None
            assert wait is not None  # every path that reaches here set it
            self._sleep(wait)
            attempt += 1

    def _image(self, ctype: str, body: bytes) -> bytes:
        """The image bytes of a successful answer: a raw image, or JSON with ``result.image`` base64."""
        if ctype.startswith("image/"):
            return body
        try:
            data = json.loads(body)
        except Exception:  # noqa: BLE001
            raise RuntimeError(
                f"Cloudflare {self.model} answer refused: content type {ctype or 'missing'!r} is neither an image nor JSON"
            ) from None
        if not isinstance(data, dict):
            raise RuntimeError(f"Cloudflare {self.model} answered JSON with no image")
        if data.get("success") is False:
            raise RuntimeError(self._scrub(f"Cloudflare {self.model} answered success=false{self._detail(body)}"))
        result = data.get("result")
        image = result.get("image") if isinstance(result, dict) else data.get("image")
        if not isinstance(image, str) or not image.strip():
            raise RuntimeError(f"Cloudflare {self.model} answered JSON with no image")
        text = image.strip()
        if text.startswith("data:") and "," in text:
            text = text.split(",", 1)[1]
        try:
            return base64.b64decode(text, validate=True)
        except (binascii.Error, ValueError):
            raise RuntimeError(f"Cloudflare {self.model} answered an image that is not valid base64") from None


__all__ = [
    "ACCOUNT_ENV",
    "ALLOWANCE_CODE",
    "DEFAULT_DEADLINE_S",
    "DEFAULT_MODEL",
    "DEFAULT_STYLE_SUFFIX",
    "DEFAULT_URL",
    "MAX_RESPONSE_BYTES",
    "MODELS",
    "MODEL_ENV",
    "STYLE_ENV",
    "TOKEN_ENV",
    "URL_ENV",
    "CloudflareAllowanceExhausted",
    "CloudflareImageModel",
    "ModelSpec",
    "account_looks_unusual",
    "account_problem",
    "authorization",
    "config_problem",
    "configured_base_url",
    "configured_model",
    "configured_style_suffix",
    "credentials",
    "reference_png",
    "request_size",
    "run_path",
    "search_path",
    "token_problem",
]
