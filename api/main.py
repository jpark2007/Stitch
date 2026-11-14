"""FastAPI app entry point: routers, CORS, startup and agent runs (ARCHITECTURE.md sections 6 and 7).

Run it from api/:

    uv run uvicorn main:app --env-file ../.env            # real MongoDB (MONGODB_URI)
    STITCH_FAKE_DB=1 uv run uvicorn main:app --reload     # in-memory, seeded demo family, scripted runs

Environment:

- STITCH_CORS_ORIGINS: comma-separated browser origins allowed to call the API with cookies.
  Defaults to http://localhost:3000 and http://127.0.0.1:3000. Read when create_app() runs.
- STITCH_FAKE_DB=1: use an in-memory mongomock database seeded with the demo family
  (see seed/README.md). Read when the app starts.
- STITCH_AGENT=off|fake|real: who runs "add a memory" (POST /families/{fid}/memories and
  its stream and answer routes). Read when the app starts.
  - off: nothing is configured and agent/ is never imported; the run routes answer 503.
  - fake: scripted runs from seed/fake_runs.py (no model calls, no keys, no network).
  - real: `agent.runs.configure_from_env(db)` builds the model studio. If it is missing
    or fails, startup logs a warning and falls back to scripted runs.
  Unset, empty or unknown (unknown logs a warning): fake when STITCH_FAKE_DB is on or
  ANTHROPIC_API_KEY is unset or blank, otherwise real.
- STITCH_FAKE_RUN_STEP: seconds between the steps of a scripted run (float >= 0, default 0.6).

No agent step can make startup fail: on any error it logs, and the run routes answer 503.
Validation errors (422) never echo the request input back, so a rejected signup or login
does not send its password back in the response.

This module never imports `agent/` at import time. Memory submission reaches the agent
lazily inside routes/memories.py, and startup imports agent.runs only when STITCH_AGENT is
fake or real, so read routes and STITCH_AGENT=off work without it.
"""

from __future__ import annotations

import importlib
import logging
import math
import os
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import APIRouter, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pymongo.errors import ConnectionFailure
from starlette.concurrency import run_in_threadpool

import routes.ask
import routes.auth
import routes.families
import routes.memories
import routes.props
import routes.tree
from db import client

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

_TRUTHY = frozenset({"1", "true", "yes"})


def cors_origins() -> list[str]:
    """Allowed browser origins: STITCH_CORS_ORIGINS (comma-separated) or the defaults.

    Never "*": the session cookie needs credentialed requests, which a wildcard forbids.
    """
    raw = os.environ.get("STITCH_CORS_ORIGINS") or ""
    origins: list[str] = []
    for part in raw.split(","):
        origin = part.strip()
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


def _install_agent_fake() -> bool:
    """Scripted runs (seed/fake_runs.py) behind agent.runs. True when a manager was installed."""
    from seed.fake_runs import install_fake_runs

    step = fake_run_step_seconds()
    if not install_fake_runs(step_seconds=step):
        log.warning("STITCH_AGENT=fake: scripted runs not installed; adding a memory answers 503")
        return False
    log.info("STITCH_AGENT=fake: scripted agent runs, no model calls (step %ss)", step)
    return True


def _has_manager(runs: Any) -> bool:
    try:
        runs.get_manager()
    except Exception:  # noqa: BLE001 - not configured, or no get_manager at all
        return False
    return True


def _configure_real_agent() -> bool:
    """The model studio via agent.runs.configure_from_env(db), else scripted runs.

    True when a manager was installed (real or scripted).
    """
    fallback = "falling back to scripted runs (no model calls)"
    try:
        runs = importlib.import_module("agent.runs")
    except Exception as exc:  # noqa: BLE001 - any import failure means no real runs
        log.warning("STITCH_AGENT=real: agent.runs cannot be imported (%r); %s", exc, fallback)
        return _install_agent_fake()
    configure_from_env = getattr(runs, "configure_from_env", None)
    if not callable(configure_from_env):
        log.warning("STITCH_AGENT=real: agent.runs has no configure_from_env() yet; %s", fallback)
        return _install_agent_fake()
    try:
        configure_from_env(client.get_db())
    except Exception as exc:  # noqa: BLE001 - a bad key or adapter must not stop the API
        log.warning("STITCH_AGENT=real: agent.runs.configure_from_env() failed (%r); %s", exc, fallback)
        return _install_agent_fake()
    if not _has_manager(runs):
        log.warning("STITCH_AGENT=real: configure_from_env() did not configure a run manager; %s", fallback)
        return _install_agent_fake()
    log.info("STITCH_AGENT=real: agent runs use the model studio")
    return True


def _shutdown_agent() -> None:
    """Stop the run manager this process configured and clear it. Never raises, never imports."""
    runs = sys.modules.get("agent.runs")
    if runs is None:
        return
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
        log.info("STITCH_AGENT=off: agent runs are off; adding a memory answers 503")
        return False
    step = _install_agent_fake if mode == "fake" else _configure_real_agent
    try:
        return await run_in_threadpool(step)
    except Exception as exc:  # noqa: BLE001 - no agent step may stop the API from starting
        log.warning("STITCH_AGENT=%s: agent runs not configured (%r); adding a memory answers 503", mode, exc)
        return False


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    installed_fake = False
    if fake_db_enabled():
        await run_in_threadpool(_install_fake_db)
        installed_fake = True
    else:
        await run_in_threadpool(_ensure_indexes)
    configured_agent = await _start_agent(agent_mode())
    try:
        yield
    finally:
        if configured_agent:
            await run_in_threadpool(_shutdown_agent)
        if installed_fake:
            client.set_db(None)


# ----------------------------------------------------------------------------- errors


async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:  # noqa: ARG001
    """422 in FastAPI's shape, minus `input`, `ctx` and `url`, so no request value is echoed back.

    FastAPI's default handler returns each error's `input`, which on /auth/signup and
    /auth/login is the plaintext password.
    """
    detail = [{"type": e["type"], "loc": list(e["loc"]), "msg": e["msg"]} for e in exc.errors()]
    return JSONResponse(status_code=422, content={"detail": detail})


# ----------------------------------------------------------------------------- app


def create_app() -> FastAPI:
    """Build the app. Reads STITCH_CORS_ORIGINS now; STITCH_FAKE_DB and STITCH_AGENT when it starts."""
    app = FastAPI(title="Stitch API", lifespan=lifespan)
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
