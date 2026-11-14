"""FastAPI app entry point: routers, CORS, startup and agent runs (ARCHITECTURE.md sections 6 and 7).

Run it from api/:

    uv run uvicorn main:app --reload                      # real MongoDB (MONGODB_URI from api/.env)
    STITCH_FAKE_DB=1 uv run uvicorn main:app --reload     # in-memory, seeded demo family, scripted runs

.env files: importing this module loads api/.env, then the repo-root .env, before anything
else reads the environment (db.client reads EMBEDDING_DIM and MONGODB_URI on import).
Neither overrides a variable that is already set, so the real environment wins, and so
does `uvicorn --env-file` (applied before the import). Startup logs which files were
loaded, by path only. Skipped under pytest and with STITCH_NO_DOTENV=1, so tests never
read a developer's keys.

Environment:

- VOYAGE_API_KEY (and VOYAGE_MODEL, default voyage-3.5): on a real database, startup installs
  db.embeddings.embedder_from_env() as db.search's embedder, so "ask the family" and prop
  and set matching use Atlas Vector Search and saved props and sets get a vector. Unset or
  blank, or with STITCH_FAKE_DB on, there is no embedder and both use keyword search.
  Startup logs whether embeddings are on (never the key). EMBEDDING_DIM (default 1024) is
  the vector length and must match the Atlas vector indexes.
- STITCH_NO_DOTENV=1: do not load the .env files.
- STITCH_CORS_ORIGINS: comma-separated browser origins allowed to call the API with cookies.
  Defaults to http://localhost:3000 and http://127.0.0.1:3000. Read when create_app() runs.
- STITCH_FAKE_DB=1: use an in-memory mongomock database seeded with the demo family
  (see seed/README.md). Read when the app starts.
- STITCH_AGENT=off|fake|real: who runs "add a memory" (POST /families/{fid}/memories and
  its stream and answer routes). Read when the app starts; the resolved mode is kept on
  `app.state.agent_mode` (see routes.memories.AGENT_OFF_STATE).
  - off: no agent at all, even with ANTHROPIC_API_KEY set. agent/ is never imported or
    built, and the run routes answer 503.
  - fake: scripted runs from seed/fake_runs.py (no model calls, no keys, no network).
  - real: `agent.runs.get_manager()`, which builds `agent.production.production_manager()`
    from ANTHROPIC_API_KEY (required), MONGODB_URI, IMAGE_API_KEY, ELEVENLABS_API_KEY and
    STITCH_WEB_URL. A manager configured before startup is kept instead. If the build
    fails (no key, for example), startup logs a warning with the reason and falls back to
    scripted runs.
  Unset, empty or unknown (unknown logs a warning): fake when STITCH_FAKE_DB is on or
  ANTHROPIC_API_KEY is unset or blank, otherwise real.
- STITCH_FAKE_RUN_STEP: seconds between the steps of a scripted run (float >= 0, default 0.6).

No agent step can make startup fail: on any error it logs, and the run routes answer 503.
Validation errors (422) never echo the request input back, so a rejected signup or login
does not send its password back in the response.

Request bodies are capped before any route reads them (BodySizeLimitMiddleware): a POST,
PUT or PATCH body over MAX_MULTIPART_BODY_BYTES (multipart/form-data) or
MAX_JSON_BODY_BYTES (anything else) is a 413 {"detail": "That request is too large"},
by Content-Length up front or by counting a chunked body as it streams in. It sits inside
CORSMiddleware, so the 413 carries the CORS headers and the web app can show it. GET
requests, the run event streams among them, pass straight through.

This module never imports `agent/` at import time. Memory submission reaches the agent
lazily inside routes/memories.py, and startup imports agent.runs only when STITCH_AGENT is
fake or real, so read routes and STITCH_AGENT=off work without it. Under off the run
routes refuse before they reach that import.
"""

from __future__ import annotations

import importlib
import logging
import math
import os
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

_TRUTHY = frozenset({"1", "true", "yes"})

API_DIR = Path(__file__).resolve().parent
# api/.env first, then the repo-root .env. Neither overrides a variable already set.
ENV_FILES: tuple[Path, ...] = (API_DIR / ".env", API_DIR.parent / ".env")


def load_env_files(paths: list[Path]) -> list[Path]:
    """Load each existing file into os.environ, in order, never overriding a set variable.

    So the real environment (and uvicorn's --env-file, applied before this module is
    imported) always wins, then the first file that sets a name. Missing files are
    skipped. Returns the files that were loaded.
    """
    loaded: list[Path] = []
    for path in paths:
        if path.is_file():
            load_dotenv(path, override=False)
            loaded.append(path)
    return loaded


def dotenv_enabled() -> bool:
    """False under pytest (tests never read a developer's keys) or with STITCH_NO_DOTENV=1."""
    if "pytest" in sys.modules:
        return False
    return (os.environ.get("STITCH_NO_DOTENV") or "").strip().lower() not in _TRUTHY


# Before anything below reads the environment: db.client reads EMBEDDING_DIM and
# MONGODB_URI when imported, and create_app() reads STITCH_CORS_ORIGINS.
LOADED_ENV_FILES: list[Path] = load_env_files(list(ENV_FILES)) if dotenv_enabled() else []

from fastapi import APIRouter, FastAPI, Request  # noqa: E402
from fastapi.exceptions import RequestValidationError  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402
from pymongo.errors import ConnectionFailure  # noqa: E402
from starlette.concurrency import run_in_threadpool  # noqa: E402
from starlette.datastructures import Headers  # noqa: E402
from starlette.types import ASGIApp, Message, Receive, Scope, Send  # noqa: E402

import routes.ask  # noqa: E402
import routes.auth  # noqa: E402
import routes.families  # noqa: E402
import routes.memories  # noqa: E402
import routes.props  # noqa: E402
import routes.tree  # noqa: E402
import asking  # noqa: E402
from db import client, search  # noqa: E402
from db.embeddings import embedder_from_env  # noqa: E402
from db.media import MAX_BYTES  # noqa: E402
from routes.memories import MAX_PHOTOS  # noqa: E402

# A child of uvicorn's error logger, so startup lines show in the uvicorn console.
log = logging.getLogger("uvicorn.error").getChild("stitch")

ROUTERS: tuple[APIRouter, ...] = (
    routes.auth.router,
    routes.families.router,
    routes.tree.router,
    routes.props.router,
    routes.ask.router,
    routes.memories.router,
)

DEFAULT_CORS_ORIGINS: tuple[str, ...] = ("http://localhost:3000", "http://127.0.0.1:3000")

AGENT_MODES: tuple[str, ...] = ("off", "fake", "real")
DEFAULT_FAKE_RUN_STEP = 0.6

# Request body caps (BodySizeLimitMiddleware). A multipart body may carry MAX_PHOTOS photos
# of up to db.media.MAX_BYTES each, plus room for the form fields and part headers.
MAX_JSON_BODY_BYTES = 1_000_000
MAX_MULTIPART_BODY_BYTES = MAX_PHOTOS * MAX_BYTES + 1_000_000
BODY_TOO_LARGE = "That request is too large"
BODY_METHODS = frozenset({"POST", "PUT", "PATCH"})


def cors_origins() -> list[str]:
    """Allowed browser origins: STITCH_CORS_ORIGINS (comma-separated) or the defaults.

    Never "*": the session cookie needs credentialed requests, which a wildcard forbids.
    A trailing slash is dropped ("https://stitch.app/" -> "https://stitch.app"): a
    browser's Origin header never has one, and CORS compares origins exactly.
    """
    raw = os.environ.get("STITCH_CORS_ORIGINS") or ""
    origins: list[str] = []
    for part in raw.split(","):
        origin = part.strip().rstrip("/")
        if not origin:
            continue
        if origin == "*":
            log.warning("STITCH_CORS_ORIGINS: ignoring '*' (not allowed with credentials)")
            continue
        if origin not in origins:
            origins.append(origin)
    return origins or list(DEFAULT_CORS_ORIGINS)


def fake_db_enabled() -> bool:
    """True when STITCH_FAKE_DB is 1, true or yes (any case)."""
    return (os.environ.get("STITCH_FAKE_DB") or "").strip().lower() in _TRUTHY


def agent_mode() -> str:
    """STITCH_AGENT (off, fake or real, any case), else the default.

    The default is "fake" when STITCH_FAKE_DB is on or ANTHROPIC_API_KEY is unset or blank,
    otherwise "real". An unknown value logs a warning and gives the default.
    """
    raw = (os.environ.get("STITCH_AGENT") or "").strip().lower()
    if raw in AGENT_MODES:
        return raw
    has_key = bool((os.environ.get("ANTHROPIC_API_KEY") or "").strip())
    default = "fake" if fake_db_enabled() or not has_key else "real"
    if raw:
        log.warning("STITCH_AGENT=%r is not one of %s; using %r", raw, ", ".join(AGENT_MODES), default)
    return default


def fake_run_step_seconds() -> float:
    """STITCH_FAKE_RUN_STEP as seconds (a finite float >= 0), else 0.6 (a bad value logs a warning)."""
    raw = (os.environ.get("STITCH_FAKE_RUN_STEP") or "").strip()
    if not raw:
        return DEFAULT_FAKE_RUN_STEP
    try:
        value = float(raw)
    except ValueError:
        value = math.nan
    if not math.isfinite(value) or value < 0:
        log.warning(
            "STITCH_FAKE_RUN_STEP=%r is not a number of seconds >= 0; using %s", raw, DEFAULT_FAKE_RUN_STEP
        )
        return DEFAULT_FAKE_RUN_STEP
    return value


# ----------------------------------------------------------------------------- startup steps


def _install_fake_db() -> None:
    from seed.demo import DEMO_ACCOUNTS, DEMO_FAMILY_ID, DEMO_PASSWORD
    from seed.fake import install_fake_db

    install_fake_db()
    emails = ", ".join(account.email for account in DEMO_ACCOUNTS)
    log.info(
        "STITCH_FAKE_DB: in-memory database with demo family %r; sign in as %s, password %r",
        DEMO_FAMILY_ID,
        emails,
        DEMO_PASSWORD,
    )


def _ensure_indexes() -> None:
    """Best-effort index creation. Never raises: the app must start without MongoDB."""
    db = client.get_db()
    try:
        client.ensure_indexes(db)
    except ConnectionFailure as exc:
        # Unreachable: each search-index call would wait out its own server-selection timeout.
        log.warning("MongoDB unreachable; indexes not created, the API starts anyway: %r", exc)
        return
    except Exception as exc:  # noqa: BLE001 - startup must never fail on indexes
        log.warning("Indexes not created: %r", exc)
    try:
        client.ensure_search_indexes(db)
    except Exception as exc:  # noqa: BLE001 - vector indexes are Atlas-only and optional
        log.warning("Vector search indexes not created: %r", exc)


def _log_env_files() -> None:
    """One line naming the .env files loaded at import (paths only, never values)."""
    if LOADED_ENV_FILES:
        log.info("Environment loaded from %s (variables already set win)",
                 ", ".join(str(p) for p in LOADED_ENV_FILES))
    elif dotenv_enabled():
        log.info("No .env file found (looked for %s)", ", ".join(str(p) for p in ENV_FILES))


def _configure_embeddings() -> None:
    """Install the Voyage embedder from the environment, or none. One log line, never the key."""
    try:
        embedder = embedder_from_env()
    except Exception as exc:  # noqa: BLE001 - search falls back to keywords
        log.warning("Vector search embeddings off: the embedder could not be built (%s)", type(exc).__name__)
        embedder = None
    search.set_embedder(embedder)
    if embedder is None:
        log.info("Vector search embeddings off (VOYAGE_API_KEY not set): ask and prop matching use keywords")
        return
    log.info(
        "Vector search embeddings on: Voyage AI %s, %s dimensions",
        getattr(embedder, "model", "?"),
        getattr(embedder, "dimensions", client.VECTOR_DIM),
    )


def _configure_answerer() -> None:
    """Install the ask model step (asking.answerer_from_env), or none. One log line, never the key.

    ANTHROPIC_API_KEY set and STITCH_ASK_MODEL not "off": /ask lets a small model pick the
    memory among the search's candidates. Installed with STITCH_FAKE_DB too (the demo runs on it).
    """
    try:
        answerer = asking.answerer_from_env()
    except Exception as exc:  # noqa: BLE001 - /ask falls back to the search answer
        log.warning("Ask model step off: it could not be built (%s)", type(exc).__name__)
        answerer = None
    asking.set_answerer(answerer)
    if answerer is None:
        log.info("Ask model step off (no ANTHROPIC_API_KEY, or STITCH_ASK_MODEL=off): /ask answers from search")
    else:
        log.info("Ask model step on: %s", getattr(answerer, "model", "?"))


def _install_agent_fake() -> bool:
    """Scripted runs (seed/fake_runs.py) behind agent.runs. True when a manager was installed."""
    from seed.fake_runs import install_fake_runs

    step = fake_run_step_seconds()
    if not install_fake_runs(step_seconds=step):
        log.warning("STITCH_AGENT=fake: scripted runs not installed; adding a memory answers 503")
        return False
    log.info("STITCH_AGENT=fake: scripted agent runs, no model calls (step %ss)", step)
    return True


def _configured_manager(runs: Any) -> Any:
    """The run manager already installed in agent.runs, or None.

    This reads agent.runs' private `_manager` slot on purpose: `get_manager()` would start
    a lazy production build when nothing is configured, and this check must not.
    """
    return getattr(runs, "_manager", None)


def _configure_real_agent() -> bool:
    """The model studio via agent.runs.get_manager(), else scripted runs. Runs in the threadpool.

    True when this process installed a manager (real or scripted), so shutdown stops it.
    A manager configured before startup is kept and left to whoever configured it.
    """
    fallback = "falling back to scripted runs (no model calls)"
    try:
        runs = importlib.import_module("agent.runs")
    except Exception as exc:  # noqa: BLE001 - any import failure means no real runs
        log.warning("STITCH_AGENT=real: agent.runs cannot be imported (%s); %s", exc, fallback)
        return _install_agent_fake()
    if _configured_manager(runs) is not None:
        log.info("STITCH_AGENT=real: keeping the agent run manager configured before startup")
        return False
    try:
        # Builds agent.production.production_manager() once and configures it.
        runs.get_manager()
    except Exception as exc:  # noqa: BLE001 - a missing key or a bad adapter must not stop the API
        log.warning("STITCH_AGENT=real: the model studio could not be built (%s); %s", exc, fallback)
        return _install_agent_fake()
    log.info("STITCH_AGENT=real: agent runs use the model studio")
    return True


def _shutdown_agent() -> None:
    """Stop the run manager this process configured and clear it. Never raises, never imports."""
    runs = sys.modules.get("agent.runs")
    if runs is None or _configured_manager(runs) is None:
        return  # nothing to stop; get_manager() here would build a fresh studio just to stop it
    try:
        runs.get_manager().shutdown()
    except Exception as exc:  # noqa: BLE001 - shutdown must finish
        log.warning("Agent run manager did not shut down cleanly: %r", exc)
    try:
        runs.configure(None)
    except Exception as exc:  # noqa: BLE001 - shutdown must finish
        log.warning("Agent run manager not cleared: %r", exc)


async def _start_agent(mode: str) -> bool:
    """Configure agent runs for `mode`. True when a manager was installed. Never raises."""
    if mode == "off":
        log.info("STITCH_AGENT=off: no agent runs (agent/ is not loaded); adding a memory answers 503")
        return False
    step = _install_agent_fake if mode == "fake" else _configure_real_agent
    try:
        return await run_in_threadpool(step)
    except Exception as exc:  # noqa: BLE001 - no agent step may stop the API from starting
        log.warning("STITCH_AGENT=%s: agent runs not configured (%r); adding a memory answers 503", mode, exc)
        return False


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    _log_env_files()
    installed_fake = False
    # Read on purpose (db.search has no getter): shutdown puts back whatever was there.
    previous_embedder = getattr(search, "_embedder", None)
    previous_answerer = asking.get_answerer()
    _configure_answerer()
    if fake_db_enabled():
        await run_in_threadpool(_install_fake_db)
        installed_fake = True
        # mongomock has no $vectorSearch, and the fake mode makes no network calls.
        if (os.environ.get("VOYAGE_API_KEY") or "").strip():
            log.info("Vector search embeddings off: STITCH_FAKE_DB ignores VOYAGE_API_KEY")
    else:
        await run_in_threadpool(_ensure_indexes)
        _configure_embeddings()
    # The dummy login hash now, so the first login with an unknown email is not the one
    # request that pays for computing it (a timing tell that the account does not exist).
    try:
        await run_in_threadpool(routes.auth.warm_login_hash)
    except Exception as exc:  # noqa: BLE001 - login computes it lazily anyway
        log.warning("Login timing hash not precomputed (%s)", type(exc).__name__)
    mode = agent_mode()
    # The run routes refuse with 503 when this is "off" (routes.memories._agent_off).
    setattr(application.state, routes.memories.AGENT_OFF_STATE, mode)
    configured_agent = await _start_agent(mode)
    try:
        yield
    finally:
        asking.set_answerer(previous_answerer)
        if configured_agent:
            await run_in_threadpool(_shutdown_agent)
        if installed_fake:
            client.set_db(None)
        else:
            search.set_embedder(previous_embedder)


# ----------------------------------------------------------------------------- errors


async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:  # noqa: ARG001
    """422 in FastAPI's shape, minus `input`, `ctx` and `url`, so no request value is echoed back.

    FastAPI's default handler returns each error's `input`, which on /auth/signup and
    /auth/login is the plaintext password.
    """
    detail = [{"type": e["type"], "loc": list(e["loc"]), "msg": e["msg"]} for e in exc.errors()]
    return JSONResponse(status_code=422, content={"detail": detail})


# ----------------------------------------------------------------------------- body size


class _BodyTooLarge(Exception):
    """Raised from the wrapped `receive` once a streamed body passes its limit."""


async def _send_too_large(scope: Scope, receive: Receive, send: Send) -> None:
    await JSONResponse({"detail": BODY_TOO_LARGE}, status_code=413)(scope, receive, send)


class BodySizeLimitMiddleware:
    """Pure ASGI: 413 for a POST, PUT or PATCH body over its limit, never buffering it.

    multipart/form-data gets `multipart_limit`, any other content type `json_limit`. A
    Content-Length over the limit is refused before the app runs. Every body is also
    counted as it streams through `receive` (a chunked body has no Content-Length): once
    the count passes the limit, `receive` raises, whatever the app sends after that is
    dropped (FastAPI turns a failed body read into a 400), and the 413 goes out instead.
    Other methods and non-HTTP scopes are passed through untouched, so the SSE run
    streams never go through a wrapper.
    """

    def __init__(self, app: ASGIApp, *, json_limit: int, multipart_limit: int) -> None:
        self.app = app
        self.json_limit = json_limit
        self.multipart_limit = multipart_limit

    def limit_for(self, headers: Headers) -> int:
        content_type = (headers.get("content-type") or "").strip().lower()
        return self.multipart_limit if content_type.startswith("multipart/form-data") else self.json_limit

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("method") not in BODY_METHODS:
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        limit = self.limit_for(headers)
        declared = (headers.get("content-length") or "").strip()
        # A malformed Content-Length is left to the server; the streamed count still applies.
        if declared.isdigit() and int(declared) > limit:
            await _send_too_large(scope, receive, send)
            return

        received = 0
        too_large = False
        started = False

        async def limited_receive() -> Message:
            nonlocal received, too_large
            if too_large:
                raise _BodyTooLarge
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body") or b"")
                if received > limit:
                    too_large = True
                    raise _BodyTooLarge
            return message

        async def guarded_send(message: Message) -> None:
            nonlocal started
            if too_large:
                return  # the app's answer to a cut-off body; the 413 below replaces it
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, guarded_send)
        except Exception:
            if not too_large:
                raise
        if too_large and not started:
            await _send_too_large(scope, receive, send)


# ----------------------------------------------------------------------------- app


def create_app() -> FastAPI:
    """Build the app. Reads STITCH_CORS_ORIGINS now; STITCH_FAKE_DB and STITCH_AGENT when it starts.

    Middleware runs outermost first: CORSMiddleware, then BodySizeLimitMiddleware, so a
    413 for an oversized body still carries the CORS headers. (Starlette's add_middleware
    puts each new middleware outside the ones added before it.)
    """
    app = FastAPI(title="Stitch API", lifespan=lifespan)
    app.add_middleware(
        BodySizeLimitMiddleware,
        json_limit=MAX_JSON_BODY_BYTES,
        multipart_limit=MAX_MULTIPART_BODY_BYTES,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins(),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    for router in ROUTERS:
        app.include_router(router)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
