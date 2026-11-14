"""The offline demo in one command: the API on an in-memory database with the demo family.

From api/:

    uv sync && uv run python -m seed.serve              # http://localhost:8000
    uv run python -m seed.serve --port 8001 --reload    # another port, reload on code changes

It sets STITCH_FAKE_DB=1 (so api/main.py seeds the demo family into mongomock and runs
"add a memory" on scripted runs), gives sessions a fixed dev-only JWT_SECRET when none is
configured (so a browser stays signed in across --reload: the demo users are re-created
with the same ids), checks the port is free, prints the demo accounts, and starts uvicorn.

No MongoDB, Docker or API keys are needed, and nothing goes over the network except the
local listening socket. STITCH_AGENT is left alone, so main.py's default applies (scripted
runs whenever STITCH_FAKE_DB is on) and an explicit value still wins. See seed/README.md.
"""

from __future__ import annotations

import argparse
import errno
import importlib
import os
import socket
import sys
from collections.abc import Mapping
from pathlib import Path

import uvicorn
from dotenv import dotenv_values as read_dotenv

API_DIR = Path(__file__).resolve().parents[1]
ENV_FILES: tuple[Path, ...] = (API_DIR / ".env", API_DIR.parent / ".env")
"""The files api/main.py loads, in the same order (the first one to set a name wins)."""

DEV_JWT_SECRET = "stitch-local-demo-only-not-a-secret-000"
"""Signs demo sessions when no JWT_SECRET is configured. Public on purpose: local demo only."""

WEB_URL = "http://localhost:3000"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000
_TRUTHY = frozenset({"1", "true", "yes"})
_LOOPBACK_OR_ANY = frozenset({"127.0.0.1", "localhost", "0.0.0.0", "::", "::1"})


def _blank(value: str | None) -> bool:
    return not (value or "").strip()


def demo_env(environ: Mapping[str, str], *, dotenv_values: Mapping[str, str | None] | None = None) -> dict[str, str]:
    """The variables to set for the demo (pure: reads `environ` and `dotenv_values`, sets nothing).

    STITCH_FAKE_DB is always "1". JWT_SECRET is DEV_JWT_SECRET only when no secret is
    configured: not in `environ` and not in `dotenv_values` (a blank value counts as
    absent, because auth.py treats it as unset). STITCH_AGENT and everything else are
    never returned, so main.py's defaults and the caller's explicit values apply.
    """
    env: dict[str, str] = {"STITCH_FAKE_DB": "1"}
    if "JWT_SECRET" in environ:
        # Set, even to blank, it hides the .env files' value from main.py's load_dotenv.
        configured = not _blank(environ["JWT_SECRET"])
    else:
        configured = not _blank((dotenv_values or {}).get("JWT_SECRET"))
    if not configured:
        env["JWT_SECRET"] = DEV_JWT_SECRET
    return env


def _bind_error(host: str, port: int) -> OSError | None:
    """The error binding host:port the way uvicorn does (SO_REUSEADDR), or None if it binds."""
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    try:
        with socket.socket(family, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((host, port))
    except OSError as exc:
        return exc
    return None


def port_free(host: str, port: int) -> bool:
    """True when a server could listen on host:port now (it binds and releases a socket)."""
    return _bind_error(host, port) is None


def _display_host(host: str) -> str:
    if host in _LOOPBACK_OR_ANY:
        return "localhost"
    return f"[{host}]" if ":" in host else host


def banner(host: str, port: int) -> str:
    """The lines printed before the server starts: URLs, the demo accounts, how to stop."""
    from seed.demo import DEMO_ACCOUNTS, DEMO_FAMILY_NAME, DEMO_PASSWORD

    api_url = f"http://{_display_host(host)}:{port}"
    width = max(len(a.email) for a in DEMO_ACCOUNTS)
    lines = [
        "",
        f"Stitch offline demo: in-memory database with the demo family ({DEMO_FAMILY_NAME}), scripted runs",
        f"  API      {api_url}   (health: {api_url}/health)",
        f"  Web app  {WEB_URL}   (cd web && npm install && npm run dev)",
        "           open it at localhost, not 127.0.0.1: the session cookie is SameSite=Lax,",
        "           and the browser treats the two as different sites",
    ]
    if port != DEFAULT_PORT:
        lines.append(f"           start the web app with NEXT_PUBLIC_API_URL={api_url} (it calls port {DEFAULT_PORT})")
    lines.append(f"  Sign in  (password {DEMO_PASSWORD} for every account)")
    for account in DEMO_ACCOUNTS:
        lines.append(f"           {account.email.ljust(width)}  {account.name} ({account.role})")
    lines += [
        "  No MongoDB, Docker or API keys needed.",
        "  Ctrl+C to stop; data resets on every restart.",
        "",
    ]
    return "\n".join(lines)


def _dotenv_enabled(environ: Mapping[str, str]) -> bool:
    return (environ.get("STITCH_NO_DOTENV") or "").strip().lower() not in _TRUTHY


def read_env_files(paths: tuple[Path, ...] = ENV_FILES) -> dict[str, str | None]:
    """The .env files' values, merged as main.py loads them (first file wins). Exports nothing."""
    values: dict[str, str | None] = {}
    for path in paths:
        if path.is_file():
            for key, value in read_dotenv(path).items():
                values.setdefault(key, value)
    return values


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m seed.serve",
        description="Run the API offline on an in-memory database seeded with the demo family "
        "(STITCH_FAKE_DB=1, scripted runs; no MongoDB, Docker or keys). See seed/README.md.",
    )
    parser.add_argument("--host", default=DEFAULT_HOST, help="interface to listen on (default %(default)s)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="port to listen on (default %(default)s)")
    parser.add_argument("--reload", action="store_true", help="restart on code changes (data resets each time)")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Start the demo API. 1 when the port cannot be used; 0 after the server stops."""
    args = _parser().parse_args(argv)

    dotenv = read_env_files() if _dotenv_enabled(os.environ) else {}
    os.environ.update(demo_env(os.environ, dotenv_values=dotenv))

    error = _bind_error(args.host, args.port)
    if error is not None:
        if error.errno == errno.EADDRINUSE:
            print(f"port {args.port} is in use: is the API already running? pass --port", file=sys.stderr)
        else:
            print(f"cannot listen on {args.host}:{args.port}: {error.strerror or error}", file=sys.stderr)
        return 1

    # Import main first, as uvicorn would: it loads the .env files before anything reads the
    # environment (seed.demo, used by the banner, imports db.client, which reads it on import).
    if str(API_DIR) not in sys.path:
        sys.path.insert(0, str(API_DIR))
    importlib.import_module("main")

    print(banner(args.host, args.port), flush=True)
    uvicorn.run(
        "main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        reload_dirs=[str(API_DIR)] if args.reload else None,
        app_dir=str(API_DIR),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
