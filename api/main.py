"""FastAPI app entry point: routers, CORS and startup (ARCHITECTURE.md sections 6 and 7).

Run it from api/:

    uv run uvicorn main:app --env-file ../.env            # real MongoDB (MONGODB_URI)
    STITCH_FAKE_DB=1 uv run uvicorn main:app --reload     # in-memory, seeded demo family

Environment:

- STITCH_CORS_ORIGINS: comma-separated browser origins allowed to call the API with cookies.
  Defaults to http://localhost:3000 and http://127.0.0.1:3000. Read when create_app() runs.
- STITCH_FAKE_DB=1: use an in-memory mongomock database seeded with the demo family
  (see seed/README.md). Read when the app starts.

This module never imports `agent/`. Memory submission reaches the agent lazily inside
routes/memories.py, so read routes and startup work without it.
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware
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


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    installed_fake = False
    if fake_db_enabled():
        await run_in_threadpool(_install_fake_db)
        installed_fake = True
    else:
        await run_in_threadpool(_ensure_indexes)
    try:
        yield
    finally:
        if installed_fake:
            client.set_db(None)


def create_app() -> FastAPI:
    """Build the app. Reads STITCH_CORS_ORIGINS now and STITCH_FAKE_DB when it starts."""
    app = FastAPI(title="Stitch API", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins(),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    for router in ROUTERS:
        app.include_router(router)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
