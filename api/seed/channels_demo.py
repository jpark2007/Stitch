"""Run the disposable terminal demo (optional offline studio, no live model/provider calls).

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
this disposable server is restarted. By default no story is generated. Add --worker
to enable the explicit scripted studio, then send story words, YEAR 1998 and SUBMIT,
ticking after each input. See channels/README.md for the signed tick command.
Saved demo memories are in this disposable database, not another web app's database. The demo
always binds 127.0.0.1; there is no public-host, reload, or production mode.
"""

from __future__ import annotations

import argparse
import json
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request

DEFAULT_PORT = 8011
HOST = "127.0.0.1"


def _secret() -> str:
    from channels.models import WHITESPACE
    value = os.getenv("CHANNELS_SHARED_SECRET", "")
    if len(value.encode("utf-8")) < 32 or value != value.strip(WHITESPACE):
        raise ValueError("Set CHANNELS_SHARED_SECRET to a random secret of at least 32 UTF-8 bytes")
    return value


def create_app(*, worker: bool = False, sender=None) -> FastAPI:
    """Construct a standalone app. Database and environment changes occur only in lifespan.

    Refuse to borrow an existing database override: this entrypoint is for its own
    process, not for mounting inside another running API. No main.py import occurs.
    """
    from channels import ensure_channel_indexes
    from channels.auth import authenticated_body
    from channels.routes import router
    from db import client as db_client
    from db.contacts import seed_terminal_contact

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        import mongomock

        _secret()
        if getattr(db_client, "_db_override", None) is not None:
            raise RuntimeError("Run the Channels demo in its own process; another database override is active")
        mongo = mongomock.MongoClient(tz_aware=True)
        db = mongo["stitch_channels_terminal_demo"]
        previous_flag = os.environ.get("STITCH_CHANNELS")
        previous_worker = os.environ.get("STITCH_CHANNELS_WORKER")
        manager = None
        try:
            ensure_channel_indexes(db)
            seed_terminal_contact(db, "terminal-family")
            db_client.set_db(db)
            os.environ["STITCH_CHANNELS"] = "1"
            os.environ["STITCH_CHANNELS_WORKER"] = "1" if worker else "0"
            if worker:
                from channels.worker import ChannelWorker
                from channels.outbound import BridgeSender
                from agent.conversation.demo import ScriptedConversation
                from seed.fake_runs import ScriptedRunManager
                manager = ScriptedRunManager(db_getter=lambda: db, step_seconds=0)
                app.state.channels_worker = ChannelWorker(db, sender=sender or BridgeSender(),
                    agent_factory=ScriptedConversation, runs=manager)
            app.state.demo_db = db
            yield
        finally:
            if manager is not None:
                manager.shutdown()
            if os.environ.get("STITCH_CHANNELS_WORKER") == ("1" if worker else "0"):
                if previous_worker is None:
                    os.environ.pop("STITCH_CHANNELS_WORKER", None)
                else:
                    os.environ["STITCH_CHANNELS_WORKER"] = previous_worker
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
        if request.url.path.rsplit("/", 1)[-1] in {"inbound", "inbound-photo", "authorize"}:
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
    parser.add_argument("--worker", action="store_true", help="Enable the explicit offline scripted story worker")
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    try:
        _secret()
    except ValueError as exc:
        parser.error(str(exc))
    import uvicorn

    print(f"Terminal receipt demo: http://{HOST}:{args.port}", flush=True)
    if args.worker:
        print("Disposable local scripted stories; replies go only to the terminal bridge.", flush=True)
        print("Scripted worker enabled: send story, YEAR 1998, SUBMIT; tick after each message.", flush=True)
    else:
        print("Disposable local data only. Receipts are not stories; no texts or calls are sent.", flush=True)
    uvicorn.run(create_app(worker=args.worker), host=HOST, port=args.port, access_log=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
