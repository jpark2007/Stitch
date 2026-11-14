"""Run the disposable terminal receipt demo (no stories, model calls, or phone messages).

Use a password manager to generate a random shared secret of at least 32 bytes.
Enter the same secret at the silent prompt in each terminal; never put it in a
command argument, tracked file, or chat. This command does not load any .env file.

Terminal 1, from api/:

    read -s CHANNELS_SHARED_SECRET; export CHANNELS_SHARED_SECRET
    uv sync --locked
    uv run python -m seed.channels_demo --port 8011

Terminal 2, from channels/ on the companion bridge branch:

    read -s CHANNELS_SHARED_SECRET; export CHANNELS_SHARED_SECRET
    export CHANNELS_API_URL=http://127.0.0.1:8011
    npm ci
    npm start

The terminal bridge must send provider=terminal, senderId=terminal-user and
spaceId=terminal. Only the sender is used to resolve the trusted demo contact.
Type a story to receive a transport receipt; STOP disables that contact until
this disposable server is restarted. No story appears in the book yet. The demo
always binds 127.0.0.1; there is no public-host, reload, or production mode.
"""

from __future__ import annotations

import argparse
import json
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Request

DEFAULT_PORT = 8011
HOST = "127.0.0.1"


def _secret() -> str:
    value = os.getenv("CHANNELS_SHARED_SECRET", "")
    if len(value.encode("utf-8")) < 32:
        raise ValueError("Set CHANNELS_SHARED_SECRET to a random secret of at least 32 UTF-8 bytes")
    return value


def create_app() -> FastAPI:
    """Construct a standalone app. Database and environment changes occur only in lifespan.

    Refuse to borrow an existing database override: this entrypoint is for its own
    process, not for mounting inside another running API. No main.py import occurs.
    """
    from channels import ensure_channel_indexes
    from channels.auth import authenticated_body
    from channels.routes import router
    from db import client as db_client

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        import mongomock

        _secret()
        if getattr(db_client, "_db_override", None) is not None:
            raise RuntimeError("Run the Channels demo in its own process; another database override is active")
        mongo = mongomock.MongoClient(tz_aware=True)
        db = mongo["stitch_channels_terminal_demo"]
        previous_flag = os.environ.get("STITCH_CHANNELS")
        try:
            ensure_channel_indexes(db)
            now = datetime.now(timezone.utc)
            db.families.insert_one({"_id": "terminal-family", "name": "Terminal demo"})
            db.people.insert_one({"_id": "terminal-person", "familyId": "terminal-family", "name": "Demo teller"})
            db.contacts.insert_one({
                "_id": "terminal-contact", "familyId": "terminal-family", "personId": "terminal-person",
                "handle": "terminal-user", "channel": "terminal", "status": "active",
                "verifiedAt": now, "optInAt": now,
            })
            db_client.set_db(db)
            os.environ["STITCH_CHANNELS"] = "1"
            app.state.demo_db = db
            yield
        finally:
            # Never replace an override installed by someone else while running.
            if getattr(db_client, "_db_override", None) is db:
                db_client.set_db(None)
            if os.environ.get("STITCH_CHANNELS") == "1":
                if previous_flag is None:
                    os.environ.pop("STITCH_CHANNELS", None)
                else:
                    os.environ["STITCH_CHANNELS"] = previous_flag
            mongo.drop_database(db.name)
            mongo.close()

    async def terminal_body(request: Request) -> bytes:
        body = await authenticated_body(request)
        if request.url.path.endswith("/inbound"):
            try:
                value = json.loads(body)
            except (ValueError, UnicodeDecodeError):
                return body  # The router returns its normal sanitized validation error.
            if isinstance(value, dict) and value.get("provider") != "terminal":
                raise HTTPException(403, "This demo accepts terminal messages only")
        return body

    app = FastAPI(title="Stitch terminal receipt demo", lifespan=lifespan)
    app.dependency_overrides[authenticated_body] = terminal_body
    app.include_router(router)
    return app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    try:
        _secret()
    except ValueError as exc:
        parser.error(str(exc))
    import uvicorn

    print(f"Terminal receipt demo: http://{HOST}:{args.port}", flush=True)
    print("Disposable local data only. Receipts are not stories; no texts or calls are sent.", flush=True)
    uvicorn.run(create_app(), host=HOST, port=args.port, access_log=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
