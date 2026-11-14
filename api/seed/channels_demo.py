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
import secrets
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request

DEFAULT_PORT = 8011
WEB_PORT = 8012
HOST = "127.0.0.1"


def _secret() -> str:
    from channels.models import WHITESPACE
    value = os.getenv("CHANNELS_SHARED_SECRET", "")
    if len(value.encode("utf-8")) < 32 or value != value.strip(WHITESPACE):
        raise ValueError("Set CHANNELS_SHARED_SECRET to a random secret of at least 32 UTF-8 bytes")
    return value


def create_app(*, worker: bool = False, sender=None, web: bool = False, preview_port: int = 3002,
               bridge_port: int | None = None) -> FastAPI:
    """Construct a standalone app. Database and environment changes occur only in lifespan.

    Refuse to borrow an existing database override: this entrypoint is for its own
    process, not for mounting inside another running API. No main.py import occurs.
    """
    from channels import ensure_channel_indexes
    from channels.auth import authenticated_body
    from channels.routes import router
    from db import client as db_client
    from db.contacts import seed_terminal_contact

    if any(isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535
           for port in (preview_port, bridge_port if bridge_port is not None else 3012)):
        raise ValueError("Demo ports must be between 1 and 65535")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        import mongomock

        _secret()
        if getattr(db_client, "_db_override", None) is not None:
            raise RuntimeError("Run the Channels demo in its own process; another database override is active")
        mongo = mongomock.MongoClient(tz_aware=True)
        db = mongo["stitch_channels_terminal_demo"]
        owned_env = {"STITCH_CHANNELS": "1", "STITCH_CHANNELS_WORKER": "1" if worker else "0",
                     "STITCH_CHANNELS_TRANSCRIPTION": "0"}
        if web:
            owned_env.update(JWT_SECRET=secrets.token_urlsafe(32), COOKIE_SECURE="0")
        previous_env = {key: os.environ.get(key) for key in owned_env}
        manager = None
        try:
            ensure_channel_indexes(db)
            if web:
                from seed.demo import seed_demo
                from db.contacts import seed_book_demo_contact
                db_client.ensure_indexes(db)
                seed_demo(db)
                seed_book_demo_contact(db)
            else:
                seed_terminal_contact(db, "terminal-family")
            db_client.set_db(db)
            os.environ.update(owned_env)
            if worker:
                from channels.worker import ChannelWorker
                from channels.outbound import BridgeSender
                from agent.conversation.demo import ScriptedConversation
                from seed.fake_runs import ScriptedRunManager
                manager = ScriptedRunManager(db_getter=lambda: db, step_seconds=0)
                port = bridge_port if bridge_port is not None else (3012 if web else 3011)
                app.state.channels_worker = ChannelWorker(db, sender=sender or BridgeSender(base_url=f"http://127.0.0.1:{port}"),
                    agent_factory=ScriptedConversation, runs=manager,
                    **({"web_url": f"http://{HOST}:{preview_port}"} if web else {}))
            app.state.demo_db = db
            yield
        finally:
            if manager is not None:
                manager.shutdown()
            for key, value in owned_env.items():
                if os.environ.get(key) == value:
                    previous = previous_env[key]
                    if previous is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = previous
            # Never replace an override installed by someone else while running.
            if getattr(db_client, "_db_override", None) is db:
                db_client.set_db(None)
            mongo.drop_database(db.name)
            mongo.close()

    async def terminal_body(request: Request) -> bytes:
        body = await authenticated_body(request)
        if request.url.path.rsplit("/", 1)[-1] in {"inbound", "inbound-photo", "inbound-voice", "authorize", "enroll"}:
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
    if web:
        from fastapi.middleware.cors import CORSMiddleware
        from fastapi.exceptions import RequestValidationError
        from fastapi.responses import JSONResponse
        from routes import auth, families, tree
        from channels.settings import router as settings_router
        from channels.enrollment import router as enrollment_router

        app.add_middleware(CORSMiddleware,
            allow_origins=[f"http://127.0.0.1:{preview_port}", f"http://localhost:{preview_port}"],
            allow_credentials=True, allow_methods=["GET", "POST", "PATCH"], allow_headers=["Content-Type"])
        app.include_router(auth.router)
        app.include_router(settings_router)
        app.include_router(enrollment_router)
        # Existing protected read routes, including book/memory/media bundles.
        # Studio REST writes are deliberately absent; only the explicit worker runs.
        for existing in (families.router, tree.router):
            app.router.routes.extend(route for route in existing.routes if getattr(route, "methods", set()) == {"GET"})

        @app.exception_handler(RequestValidationError)
        async def validation_error(_request, exc):
            return JSONResponse(status_code=422, content={"detail": [
                {"type": e["type"], "loc": list(e["loc"]), "msg": e["msg"]}
                for e in exc.errors()]})
    return app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int)
    parser.add_argument("--web", action="store_true", help="Serve protected demo book reads from the same disposable database")
    parser.add_argument("--preview-port", type=int, default=3002)
    parser.add_argument("--bridge-port", type=int)
    parser.add_argument("--worker", action="store_true", help="Enable the explicit offline scripted story worker")
    args = parser.parse_args(argv)
    args.port = args.port if args.port is not None else (WEB_PORT if args.web else DEFAULT_PORT)
    if any(not 1 <= port <= 65535 for port in (args.port, args.preview_port, args.bridge_port or 3012)) or args.bridge_port == 0:
        parser.error("Ports must be between 1 and 65535")
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
    if args.web:
        print("Protected demo book API enabled; see channels/DEMO.md for separate preview instructions.", flush=True)
    uvicorn.run(create_app(worker=args.worker, web=args.web, preview_port=args.preview_port,
                          bridge_port=args.bridge_port), host=HOST, port=args.port, access_log=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
