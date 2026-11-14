"""HiggsfieldImageModel: ``art.ImageModel`` over the Higgsfield API (https://higgsfield.ai).

Built against the official docs (read 2026-11-14):

- Overview, base URL, auth:   https://docs.higgsfield.ai/docs/index.md,
                              https://docs.higgsfield.ai/docs/authentication.md,
                              https://docs.higgsfield.ai/docs/help/faq.md
- Request lifecycle, polling: https://docs.higgsfield.ai/docs/concepts/requests.md,
                              https://docs.higgsfield.ai/docs/concepts/polling.md,
                              https://docs.higgsfield.ai/docs/api-reference/requests/get-request-status.md
- Errors, limits, retries:    https://docs.higgsfield.ai/docs/concepts/errors.md,
                              https://docs.higgsfield.ai/docs/concepts/rate-limits.md,
                              https://docs.higgsfield.ai/docs/concepts/idempotency.md,
                              https://docs.higgsfield.ai/docs/api-reference/headers.md
- Price check without a generation: https://docs.higgsfield.ai/docs/concepts/billing-and-retention.md
  (``POST {base}/estimate/{endpoint}`` with the generation's body answers ``{"usd": "..."}``)
- Models: https://docs.higgsfield.ai/docs/models/recraft-v4-1/text-to-image.md,
          https://docs.higgsfield.ai/docs/models/soul-standard/generate.md,
          https://docs.higgsfield.ai/docs/models/soul-2/generate.md

What the docs say, and what this adapter does with it:

- Base URL ``https://api.higgsfield.ai``. Every call sends ``Authorization: Key {key_id}:{secret}``
  (a key ID and a secret from https://console.higgsfield.ai; not a Bearer token).
- A generation is ``POST {base}/{endpoint}`` with a JSON body; the endpoint is the model id (for
  example ``recraft/v4.1/text-to-image``). It answers at once with ``{"status": "queued",
  "request_id", "status_url", "cancel_url"}``. ``GET {base}/requests/{request_id}/status`` answers
  ``{"status", "request_id", "images": [{"url"}], "error"}``; ``queued`` and ``in_progress`` are not
  terminal, ``completed``, ``failed``, ``nsfw`` and ``canceled`` are. ``failed`` and ``nsfw`` are
  not charged; a ``queued`` request can be cancelled (``POST {base}/requests/{id}/cancel``, refunded).
- Errors are FastAPI's ``{"detail": ...}`` (a string, a list of validation errors, or an object
  with ``code`` / ``message``). 429 (queue full) carries ``Retry-After``; 5xx is retryable.
- An ``Idempotency-Key`` header makes a repeated submission return the original request instead
  of a second (billed) one.

``generate(prompt, reference_images=(), size=(w, h))``:

1. Picks the model's documented aspect ratio closest to ``w / h`` (log space) and builds the body
   from ``MODELS`` (Recraft: ``resolution=1k``, ``output_format=png``; SOUL: ``resolution=1080p``,
   ``batch_size=1``). The prompt gets ``HIGGSFIELD_STYLE_SUFFIX`` appended (``DEFAULT_STYLE_SUFFIX``
   unless set; ``none`` turns it off).
2. Submits once with a fresh ``Idempotency-Key``. A submission that failed fast (no connection,
   HTTP 408/429/5xx) is repeated at most once, with the same key, after ``images.backoff_s``
   (Retry-After honoured, capped at 8 s). Once a ``request_id`` exists nothing is ever submitted
   again: that request is polled.
3. Polls ``{base}/requests/{id}/status`` (built from the base URL and the id, never from a URL in
   the response) from ``POLL_START_S`` (2 s) growing x1.5 to ``POLL_MAX_S`` (10 s), as the polling
   doc recommends. A 408/429/5xx or dropped status call is retried up to ``MAX_POLL_ERRORS`` times
   in a row; any other 4xx (401, 404) stops at once. The whole ``generate`` is bounded by
   ``deadline_s`` (120 s by default, the existing adapter's bound; connect timeout 10 s, 30 s per
   API call): past it, a request still ``queued`` is cancelled (best effort, so it is refunded) and
   ``RuntimeError`` names the request id and last status.
4. Downloads ``images[0].url`` only over https from a host in the allowlist (``higgsfield.ai`` and
   its subdomains, plus ``HIGGSFIELD_RESULT_HOSTS``), without the Authorization header, following
   at most ``MAX_REDIRECTS`` redirects and only to allowed https hosts, refusing a non-``image/*``
   content type and anything over ``MAX_DOWNLOAD_BYTES`` (25 MB). This is an SSRF boundary.
5. Decodes with Pillow and fits to exactly ``(w, h)`` with ``fitting.to_png``, the same rule as
   ``OpenAIImageModel``.

``reference_images``: none of the supported text-to-image endpoints takes a reference image as
an image-to-image input (Recraft V4.1 declares none; SOUL's ``image_reference_url`` only guides
its prompt enhancement and needs a public URL), so references are ignored, the prop is drawn
from the prompt alone, and one warning per process says so. A prop never fails over references.

Not verified from the official docs (each is configurable or fails loudly):

- The host the result images are served from: the docs only show ``cdn.example.com``. The default
  allowlist is ``higgsfield.ai`` and its subdomains; a result on another host raises
  ``RuntimeError`` naming that host, and adding it to ``HIGGSFIELD_RESULT_HOSTS`` allows it.
- The pixel size behind each aspect ratio (Recraft "1k", SOUL "1080p"). ``canvas_size`` returns a
  nominal size with the exact ratio (long side 1024); only the ratio matters to callers, and the
  result is fitted to the requested size whatever its pixels.
- Prices: ask the estimate endpoint (``preflight --live`` does) or the console.

Env:

- ``HIGGSFIELD_API_KEY``: the API key ID, or ``id:secret`` in one value (as the SDK's ``HF_KEY``).
- ``HIGGSFIELD_API_SECRET``: the API key secret (needed unless the key holds ``id:secret``).
- ``HIGGSFIELD_API_URL``: base URL, default ``https://api.higgsfield.ai``.
- ``HIGGSFIELD_IMAGE_MODEL``: endpoint id, default ``recraft/v4.1/text-to-image``; one of ``MODELS``.
- ``HIGGSFIELD_STYLE_SUFFIX``: text appended to every prompt; ``none`` for no suffix.
- ``HIGGSFIELD_RESULT_HOSTS``: extra comma-separated result hosts (each also allows its subdomains).

Errors raise ``RuntimeError`` with the HTTP status and a short provider message; the key and the
secret are never in a message or a log. Tests inject an ``httpx.MockTransport``, a no-op
``sleep`` and a fake ``clock``.
"""

from __future__ import annotations

import logging
import math
import os
import re
import threading
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx

from agent.adapters.fitting import to_png
from agent.adapters.images import CONNECT_TIMEOUT_S, RETRY_STATUSES, backoff_s, retryable_error
from agent.guards import env_value

log = logging.getLogger(__name__)

API_KEY_ENV = "HIGGSFIELD_API_KEY"
API_SECRET_ENV = "HIGGSFIELD_API_SECRET"
URL_ENV = "HIGGSFIELD_API_URL"
MODEL_ENV = "HIGGSFIELD_IMAGE_MODEL"
STYLE_ENV = "HIGGSFIELD_STYLE_SUFFIX"
RESULT_HOSTS_ENV = "HIGGSFIELD_RESULT_HOSTS"

DEFAULT_URL = "https://api.higgsfield.ai"
DEFAULT_MODEL = "recraft/v4.1/text-to-image"
DEFAULT_STYLE_SUFFIX = "Flat 2D cut-paper illustration, not a photograph and not a 3D render."
"""Appended to every prompt so the model stays out of photoreal; ``none`` turns it off."""
STYLE_OFF = "none"
DEFAULT_RESULT_HOSTS: tuple[str, ...] = ("higgsfield.ai",)

DEFAULT_DEADLINE_S = 120.0
"""Seconds for one whole ``generate``: submit, every poll and the download."""
REQUEST_TIMEOUT_S = 30.0
"""Seconds to write one API request and between bytes of its response."""
DEFAULT_MAX_RETRIES = 1
"""Repeats of a submission that failed fast (no connection, HTTP 408/429/5xx), same idempotency key."""
POLL_START_S = 2.0
POLL_GROWTH = 1.5
POLL_MAX_S = 10.0
MAX_POLL_ERRORS = 3
"""Status calls in a row that may fail (408/429/5xx or a dropped connection) before giving up."""
MAX_DOWNLOAD_BYTES = 25 * 1024 * 1024
MAX_REDIRECTS = 3
NOMINAL_LONG_SIDE = 1024
ESTIMATE_PROMPT = "a paper cup"
"""The prompt of preflight's price estimate (no image is generated)."""

TERMINAL_FAILURES = frozenset({"failed", "nsfw", "canceled", "cancelled"})
_ERROR_DETAIL_CHARS = 200
_REQUEST_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")


@dataclass(frozen=True)
class ModelSpec:
    """One documented text-to-image endpoint: its aspect ratios and fixed body fields."""

    aspect_ratios: tuple[str, ...]
    params: Mapping[str, Any] = field(default_factory=dict)
    max_prompt_chars: int | None = None
    doc: str = ""


SOUL_RATIOS = ("9:16", "16:9", "4:3", "3:4", "1:1", "2:3", "3:2")
RECRAFT_RATIOS = ("1:1", "2:1", "1:2", "3:2", "2:3", "4:3", "3:4", "5:4", "4:5", "6:10", "14:10", "10:14", "16:9", "9:16")

MODELS: dict[str, ModelSpec] = {
    "recraft/v4.1/text-to-image": ModelSpec(
        aspect_ratios=RECRAFT_RATIOS,
        params={"resolution": "1k", "output_format": "png"},
        max_prompt_chars=10000,
        doc="https://docs.higgsfield.ai/docs/models/recraft-v4-1/text-to-image.md",
    ),
    "higgsfield-ai/soul/standard": ModelSpec(
        aspect_ratios=SOUL_RATIOS,
        params={"resolution": "1080p", "batch_size": 1},
        doc="https://docs.higgsfield.ai/docs/models/soul-standard/generate.md",
    ),
    "higgsfield-ai/soul/v2/standard": ModelSpec(
        aspect_ratios=SOUL_RATIOS,
        params={"resolution": "1080p", "batch_size": 1},
        doc="https://docs.higgsfield.ai/docs/models/soul-2/generate.md",
    ),
}
"""The endpoints whose request body this adapter knows. Others are refused, not guessed."""


# ------------------------------------------------------------------------------ config


def normalize_model(model: str) -> str:
    return model.strip().strip("/")


def configured_model(env: Mapping[str, str] | None = None) -> str:
    """The endpoint id in use: ``HIGGSFIELD_IMAGE_MODEL`` or ``DEFAULT_MODEL``."""
    return normalize_model(env_value(env, MODEL_ENV) or DEFAULT_MODEL)


def configured_base_url(env: Mapping[str, str] | None = None) -> str:
    return (env_value(env, URL_ENV) or DEFAULT_URL).rstrip("/")


def configured_style_suffix(env: Mapping[str, str] | None = None) -> str:
    """The prompt suffix in use ('' when ``HIGGSFIELD_STYLE_SUFFIX=none``)."""
    raw = env_value(env, STYLE_ENV)
    if raw is None:
        return DEFAULT_STYLE_SUFFIX
    return "" if raw.lower() == STYLE_OFF else raw


def configured_result_hosts(env: Mapping[str, str] | None = None) -> tuple[str, ...]:
    """Allowed result hosts: the defaults plus ``HIGGSFIELD_RESULT_HOSTS`` (lower-case, no '*.')."""
    extra = env_value(env, RESULT_HOSTS_ENV) or ""
    hosts = list(DEFAULT_RESULT_HOSTS)
    for item in extra.split(","):
        host = item.strip().lower().removeprefix("*.").strip(".")
        if host and host not in hosts:
            hosts.append(host)
    return tuple(hosts)


def credentials(env: Mapping[str, str] | None = None) -> tuple[str, str] | None:
    """(key id, secret) from the env, or None when they are incomplete."""
    key = env_value(env, API_KEY_ENV)
    if not key:
        return None
    secret = env_value(env, API_SECRET_ENV)
    if secret:
        return key, secret
    key_id, sep, combined_secret = key.partition(":")
    if sep and key_id and combined_secret:
        return key_id, combined_secret
    return None


def config_problem(env: Mapping[str, str] | None = None) -> str | None:
    """Why ``HiggsfieldImageModel()`` cannot be built from ``env`` (None when it can)."""
    if not env_value(env, API_KEY_ENV):
        return f"{API_KEY_ENV} is not set"
    if credentials(env) is None:
        return f"{API_SECRET_ENV} is not set (or put id:secret in {API_KEY_ENV})"
    model = configured_model(env)
    if model not in MODELS:
        return f"{MODEL_ENV}={model!r} is not a supported endpoint; use one of: {', '.join(MODELS)}"
    return None


def authorization(key_id: str, secret: str) -> dict[str, str]:
    """The documented auth header."""
    return {"Authorization": f"Key {key_id}:{secret}"}


def estimate_body(model: str) -> dict[str, Any]:
    """The smallest documented body for ``model``, for the free ``/estimate/{endpoint}`` call."""
    return {"prompt": ESTIMATE_PROMPT, "aspect_ratio": "1:1", **MODELS[model].params}


def _ratio(text: str) -> float:
    a, _, b = text.partition(":")
    return int(a) / int(b)


def closest_aspect(size: tuple[int, int], ratios: Sequence[str]) -> str:
    """The ratio ('W:H') closest to ``size`` in log space."""
    w, h = size
    if w <= 0 or h <= 0:
        raise ValueError(f"size must be positive, got {size!r}")
    target = math.log(w / h)
    return min(ratios, key=lambda r: abs(math.log(_ratio(r)) - target))


def nominal_size(ratio: str) -> tuple[int, int]:
    """A size with exactly ``ratio``, long side ``NOMINAL_LONG_SIDE`` (the real pixels are unverified)."""
    r = _ratio(ratio)
    if r >= 1:
        return NOMINAL_LONG_SIDE, max(1, round(NOMINAL_LONG_SIDE / r))
    return max(1, round(NOMINAL_LONG_SIDE * r)), NOMINAL_LONG_SIDE


def host_allowed(host: str, allowed: Sequence[str]) -> bool:
    """``host`` is one of ``allowed`` or a subdomain of one."""
    host = host.lower().rstrip(".")
    return any(host == a or host.endswith("." + a) for a in allowed)


_reference_warned = False
_reference_lock = threading.Lock()


def _warn_references_ignored(model: str) -> None:
    global _reference_warned  # noqa: PLW0603
    with _reference_lock:
        if _reference_warned:
            return
        _reference_warned = True
    log.warning(
        "Higgsfield %s takes no reference image: reference images are ignored and art is drawn "
        "from the prompt alone (warned once per process)",
        model,
    )


# ------------------------------------------------------------------------------- model


class HiggsfieldImageModel:
    """``art.ImageModel`` and ``art.CanvasSizer`` over the Higgsfield API."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        api_secret: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        style_suffix: str | None = None,
        result_hosts: Sequence[str] | None = None,
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
        if api_key is not None:
            env[API_KEY_ENV] = api_key
        if api_secret is not None:
            env[API_SECRET_ENV] = api_secret
        creds = credentials(env)
        if creds is None:
            if not env_value(env, API_KEY_ENV):
                raise RuntimeError(f"{API_KEY_ENV} is not set; HiggsfieldImageModel needs it")
            raise RuntimeError(
                f"{API_SECRET_ENV} is not set; HiggsfieldImageModel needs the key ID and its secret "
                f"(or id:secret in {API_KEY_ENV})"
            )
        self._key_id, self._secret = creds
        self.base_url = ((base_url or "").strip() or configured_base_url(env)).rstrip("/")
        self.model = normalize_model(model) if model and model.strip() else configured_model(env)
        if self.model not in MODELS:
            raise RuntimeError(
                f"{MODEL_ENV}={self.model!r} is not a supported endpoint; use one of: {', '.join(MODELS)}"
            )
        self.spec = MODELS[self.model]
        if style_suffix is None:
            self.style_suffix = configured_style_suffix(env)
        else:
            self.style_suffix = "" if style_suffix.strip().lower() == STYLE_OFF else style_suffix.strip()
        self.result_hosts = (
            configured_result_hosts(env) if result_hosts is None else tuple(h.lower() for h in result_hosts)
        )
        self.deadline_s = float(deadline_s)
        self.max_retries = max_retries
        self._sleep = sleep
        self._clock = clock
        self._transport = transport
        self.timeout = httpx.Timeout(REQUEST_TIMEOUT_S, connect=CONNECT_TIMEOUT_S)
        self._client = httpx.Client(
            base_url=self.base_url,
            headers=authorization(self._key_id, self._secret),
            timeout=self.timeout,
            transport=transport,
        )

    def __repr__(self) -> str:  # never show the key
        return f"HiggsfieldImageModel(base_url={self.base_url!r}, model={self.model!r})"

    def close(self) -> None:
        self._client.close()

    def aspect_ratio(self, size: tuple[int, int]) -> str:
        """The documented aspect ratio sent for ``size``."""
        return closest_aspect((int(size[0]), int(size[1])), self.spec.aspect_ratios)

    def canvas_size(self, size: tuple[int, int]) -> tuple[int, int]:
        """A nominal size with the aspect ratio the API is asked for (only the ratio is exact)."""
        return nominal_size(self.aspect_ratio(size))

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
        if reference_images:
            _warn_references_ignored(self.model)
        text = self.full_prompt(prompt)
        limit = self.spec.max_prompt_chars
        if limit is not None and len(text) > limit:
            raise RuntimeError(f"Higgsfield {self.model}: prompt is {len(text)} chars, the model takes at most {limit}")
        body = {"prompt": text, "aspect_ratio": self.aspect_ratio(out_size), **self.spec.params}
        request_id = self._submit(body)
        url = self._wait(request_id, deadline)
        return to_png(self._download(url, deadline), out_size)

    # ------------------------------------------------------------------ internals

    def _scrub(self, text: str) -> str:
        for secret in (f"{self._key_id}:{self._secret}", self._secret, self._key_id):
            if secret:
                text = text.replace(secret, "[redacted]")
        return text

    def _detail(self, response: httpx.Response) -> str:
        """The provider's short message from a FastAPI ``{"detail": ...}`` body, scrubbed and cut."""
        try:
            detail = response.json().get("detail")
        except Exception:  # noqa: BLE001 - not JSON or not the FastAPI shape
            return ""
        if isinstance(detail, dict):
            detail = detail.get("message") or detail.get("code")
        elif isinstance(detail, list) and detail:
            first = detail[0]
            detail = first.get("msg") if isinstance(first, dict) else first
        if not isinstance(detail, str) or not detail.strip():
            return ""
        return f": {self._scrub(detail.strip())[:_ERROR_DETAIL_CHARS]}"

    def _fail(self, what: str, response: httpx.Response) -> RuntimeError:
        return RuntimeError(
            self._scrub(f"Higgsfield {what} returned HTTP {response.status_code}{self._detail(response)}")
        )

    def _submit(self, body: dict[str, Any]) -> str:
        """POST the generation (one repeat when it failed fast, same idempotency key); its request id."""
        path = f"/{self.model}"
        headers = {"Idempotency-Key": str(uuid.uuid4())}
        attempt = 0
        while True:
            try:
                response = self._client.post(path, json=body, headers=headers)
            except httpx.HTTPError as exc:
                if attempt < self.max_retries and retryable_error(exc):
                    self._sleep(backoff_s(attempt, None))
                    attempt += 1
                    continue
                raise RuntimeError(
                    self._scrub(f"Higgsfield {self.model} submit failed: {type(exc).__name__}")
                ) from None
            if response.status_code in RETRY_STATUSES and attempt < self.max_retries:
                self._sleep(backoff_s(attempt, response))
                attempt += 1
                continue
            if response.status_code >= 400:
                raise self._fail(f"{self.model} submit", response)
            break
        try:
            request_id = response.json()["request_id"]
        except Exception:  # noqa: BLE001
            raise RuntimeError(f"Higgsfield {self.model} submit returned no request_id") from None
        if not isinstance(request_id, str) or not _REQUEST_ID.match(request_id):
            raise RuntimeError(f"Higgsfield {self.model} submit returned an invalid request_id")
        return request_id

    def _wait(self, request_id: str, deadline: float) -> str:
        """Poll until the request is terminal; the first image URL of a completed request."""
        path = f"/requests/{request_id}/status"
        delay = POLL_START_S
        errors = 0
        status = "queued"
        while True:
            response: httpx.Response | None
            try:
                response = self._client.get(path)
            except httpx.HTTPError:
                response = None
            if response is not None and response.status_code < 400:
                errors = 0
                try:
                    data = response.json()
                    status = str(data.get("status") or "")
                except Exception:  # noqa: BLE001
                    raise RuntimeError(f"Higgsfield request {request_id}: status answer is not JSON") from None
                if status == "completed":
                    return self._first_url(request_id, data)
                if status in TERMINAL_FAILURES:
                    error = data.get("error")
                    why = (
                        f": {self._scrub(error.strip())[:_ERROR_DETAIL_CHARS]}"
                        if isinstance(error, str) and error.strip()
                        else ""
                    )
                    raise RuntimeError(f"Higgsfield request {request_id} ended {status}{why}")
            elif response is not None and response.status_code not in RETRY_STATUSES and response.status_code < 500:
                raise self._fail(f"status of request {request_id}", response)
            else:
                errors += 1
                if errors > MAX_POLL_ERRORS:
                    what = f"HTTP {response.status_code}" if response is not None else "no connection"
                    raise RuntimeError(f"Higgsfield request {request_id}: status check kept failing ({what})")
            if self._clock() + delay > deadline:
                self._cancel_if_queued(request_id, status)
                raise RuntimeError(
                    f"Higgsfield request {request_id} did not finish within {self.deadline_s:g} s "
                    f"(last status {status or 'unknown'})"
                )
            self._sleep(delay)
            delay = min(POLL_MAX_S, delay * POLL_GROWTH)

    def _cancel_if_queued(self, request_id: str, status: str) -> None:
        """Best effort: a request still queued is cancelled, so it is refunded and never drawn."""
        if status != "queued":
            return
        try:
            self._client.post(f"/requests/{request_id}/cancel")
        except httpx.HTTPError:
            pass

    def _first_url(self, request_id: str, data: Any) -> str:
        try:
            url = data["images"][0]["url"]
        except Exception:  # noqa: BLE001
            url = None
        if not isinstance(url, str) or not url:
            raise RuntimeError(f"Higgsfield request {request_id} completed with no image")
        return url

    def _check_url(self, url: str) -> None:
        """Refuse anything but https on an allowed host (the SSRF boundary)."""
        try:
            parts = urlsplit(url)
            host = parts.hostname or ""
            has_credentials = bool(parts.username or parts.password)
        except ValueError:
            raise RuntimeError("Higgsfield result URL refused: not a valid URL") from None
        if parts.scheme != "https" or not host or has_credentials:
            raise RuntimeError("Higgsfield result URL refused: only https URLs without credentials are fetched")
        if not host_allowed(host, self.result_hosts):
            raise RuntimeError(
                f"Higgsfield result URL refused: host {host!r} is not an allowed result host "
                f"(add it to {RESULT_HOSTS_ENV} if it is Higgsfield's)"
            )

    def _download(self, url: str, deadline: float) -> bytes:
        """GET the result: allowed hosts only (redirects too), image/* only, at most MAX_DOWNLOAD_BYTES."""
        remaining = max(1.0, deadline - self._clock())
        timeout = httpx.Timeout(min(2 * REQUEST_TIMEOUT_S, remaining), connect=CONNECT_TIMEOUT_S)
        too_big = f"Higgsfield result download refused: larger than {MAX_DOWNLOAD_BYTES // (1024 * 1024)} MB"
        # A separate client: the result host never sees the Authorization header.
        with httpx.Client(transport=self._transport, timeout=timeout, follow_redirects=False) as plain:
            for _ in range(MAX_REDIRECTS + 1):
                self._check_url(url)
                try:
                    with plain.stream("GET", url) as response:
                        if response.is_redirect:
                            url = urljoin(url, response.headers.get("location", ""))
                            continue
                        if response.status_code >= 400:
                            raise RuntimeError(f"Higgsfield result download returned HTTP {response.status_code}")
                        ctype = response.headers.get("content-type", "").split(";")[0].strip().lower()
                        if not ctype.startswith("image/"):
                            raise RuntimeError(
                                f"Higgsfield result download refused: content type {ctype or 'missing'!r} is not an image"
                            )
                        declared = response.headers.get("content-length", "").strip()
                        if declared.isdigit() and int(declared) > MAX_DOWNLOAD_BYTES:
                            raise RuntimeError(too_big)
                        chunks: list[bytes] = []
                        total = 0
                        for chunk in response.iter_bytes():
                            total += len(chunk)
                            if total > MAX_DOWNLOAD_BYTES:
                                raise RuntimeError(too_big)
                            chunks.append(chunk)
                        return b"".join(chunks)
                except httpx.HTTPError as exc:
                    raise RuntimeError(f"Higgsfield result download failed: {type(exc).__name__}") from None
        raise RuntimeError(f"Higgsfield result download refused: more than {MAX_REDIRECTS} redirects")


__all__ = [
    "API_KEY_ENV",
    "API_SECRET_ENV",
    "DEFAULT_DEADLINE_S",
    "DEFAULT_MODEL",
    "DEFAULT_RESULT_HOSTS",
    "DEFAULT_STYLE_SUFFIX",
    "DEFAULT_URL",
    "MAX_DOWNLOAD_BYTES",
    "MODELS",
    "MODEL_ENV",
    "RESULT_HOSTS_ENV",
    "STYLE_ENV",
    "URL_ENV",
    "HiggsfieldImageModel",
    "ModelSpec",
    "authorization",
    "closest_aspect",
    "config_problem",
    "configured_base_url",
    "configured_model",
    "configured_result_hosts",
    "configured_style_suffix",
    "credentials",
    "estimate_body",
    "host_allowed",
    "nominal_size",
]
