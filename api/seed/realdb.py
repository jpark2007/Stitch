"""Shared helpers for the CLIs that talk to a real MongoDB (seed.preflight, seed.demo).

db.client reads MONGODB_URI when it is imported, before a CLI has had a chance to load a
.env file, so these CLIs never use client.MONGODB_URI or client.get_client(). They call
load_env() first, then build their own short-timeout client from the environment with
connect(). Nothing here prints; describe_failure() and redacted() give the text a CLI may
show, and neither ever contains the URI's credentials or query string.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

from pymongo import MongoClient
from pymongo.database import Database
from pymongo.errors import (
    ConfigurationError,
    ConnectionFailure,
    OperationFailure,
    ServerSelectionTimeoutError,
)

from agent.preflight import ENV_FILES, load_env_files, mongo_uri_problem, redact_mongo_uri, scrub

DEFAULT_MONGODB_URI = "mongodb://localhost:27017"  # db.client's default when MONGODB_URI is unset
DEFAULT_MONGODB_DB = "stitch"
DEFAULT_TIMEOUT_MS = 5000
AUTH_FAILED_CODES = frozenset({18, 8000})  # AuthenticationFailed; Atlas's "bad auth"
ACCESS_LIST_HINT = (
    "on Atlas, add this machine's IP address to the cluster's IP access list "
    "(Atlas > Security > Network Access)"
)
_TRUTHY = frozenset({"1", "true", "yes"})
_DETAIL_CHARS = 240


def dotenv_enabled(environ: Mapping[str, str] | None = None, *, under_pytest: bool | None = None) -> bool:
    """api/main.py's rule: never under pytest (tests must not read a developer's keys), and
    never with STITCH_NO_DOTENV=1 (or true/yes)."""
    env = os.environ if environ is None else environ
    if ("pytest" in sys.modules) if under_pytest is None else under_pytest:
        return False
    return (env.get("STITCH_NO_DOTENV") or "").strip().lower() not in _TRUTHY


def load_env(paths: Sequence[Path] = ENV_FILES, *, enabled: bool | None = None) -> list[Path]:
    """Load api/.env, then the repo-root ../.env, into os.environ. Returns the files loaded.

    Same order and rules as api/main.py: a variable that is already set is never
    overridden (the shell wins, then api/.env, then ../.env), and nothing is loaded under
    pytest or with STITCH_NO_DOTENV=1. `enabled` overrides that check (tests).
    """
    if not (dotenv_enabled() if enabled is None else enabled):
        return []
    loaded = load_env_files(list(paths))
    sync_db_client()
    return loaded


def sync_db_client(environ: Mapping[str, str] | None = None) -> list[str]:
    """Re-apply the environment db.client read when it was imported, before load_env ran.

    db.client reads EMBEDDING_DIM at import time; a CLI imports it before loading .env, so
    without this an EMBEDDING_DIM set only in a .env file would give vector indexes of the
    default 1024 dimensions while the API (which loads .env first) queries with another.
    Returns what changed, as 'NAME old -> new'. An unusable value is left alone.
    """
    from db import client

    env = os.environ if environ is None else environ
    changed: list[str] = []
    raw = (env.get("EMBEDDING_DIM") or "").strip()
    try:
        dim = int(raw) if raw else None
    except ValueError:
        dim = None
    if dim is not None and dim > 0 and dim != client.VECTOR_DIM:
        changed.append(f"EMBEDDING_DIM {client.VECTOR_DIM} -> {dim}")
        client.VECTOR_DIM = dim
    return changed


def uri_from_env(environ: Mapping[str, str] | None = None) -> str | None:
    """MONGODB_URI, stripped, or None when it is unset or blank."""
    env = os.environ if environ is None else environ
    return (env.get("MONGODB_URI") or "").strip() or None


def db_name_from_env(environ: Mapping[str, str] | None = None) -> str:
    """MONGODB_DB, stripped, or 'stitch'."""
    env = os.environ if environ is None else environ
    return (env.get("MONGODB_DB") or "").strip() or DEFAULT_MONGODB_DB


def redacted(uri: str) -> str:
    """Scheme and hosts only: never the credentials, database path or query string."""
    return redact_mongo_uri(uri)


def connect(
    uri: str,
    db_name: str,
    *,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
    appname: str = "stitch-preflight",
) -> tuple[MongoClient, Database]:
    """A tz-aware client with bounded timeouts, and the database. Pings, so an unreachable
    server or refused credentials raise here (and the client is closed), not on first use."""
    timeout_ms = max(1, int(timeout_ms))
    client: MongoClient = MongoClient(
        uri,
        tz_aware=True,
        serverSelectionTimeoutMS=timeout_ms,
        connectTimeoutMS=timeout_ms,
        socketTimeoutMS=max(4 * timeout_ms, 20_000),
        appname=appname,
    )
    try:
        client.admin.command("ping")
    except BaseException:
        client.close()
        raise
    return client, client[db_name]


def _cut(text: str, limit: int = _DETAIL_CHARS) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 3] + "..."


def _is_dns_failure(exc: BaseException) -> bool:
    text = str(exc).lower()
    return any(s in text for s in (
        "dns", "srv record", "_mongodb._tcp", "resolution lifetime", "nxdomain",
        "nodename nor servname", "name or service not known", "getaddrinfo",
    ))


def _uri_parse_error(exc: BaseException, problem: str | None) -> bool:
    """pymongo rejects a malformed URI with ConfigurationError (InvalidURI), or a bare
    ValueError for some (an unescaped '/' in the password reads as a bad port)."""
    if isinstance(exc, ConfigurationError):
        return True
    if not isinstance(exc, ValueError):
        return False
    text = str(exc).lower()
    return problem is not None or any(s in text for s in ("uri", "port", "escaped", "rfc 3986"))


def describe_failure(exc: BaseException, secrets: Sequence[str], *, uri: str | None = None) -> str:
    """One readable sentence for a failed MongoDB connection or command, always scrubbed.

    Covers an unreachable server (ServerSelectionTimeoutError, with the Atlas IP access
    list hint), refused credentials (OperationFailure code 18 or 8000), a malformed URI
    (ConfigurationError or pymongo's ValueError: an unescaped character in the password is
    named without quoting it) and a failed DNS SRV lookup for a mongodb+srv:// URI. `uri`
    is the URI that was used (default: MONGODB_URI), only ever inspected, never shown.
    """
    name = type(exc).__name__
    detail = _cut(scrub(f"{name}: {exc}", secrets))
    problem = mongo_uri_problem((os.environ.get("MONGODB_URI") or "") if uri is None else uri)
    if isinstance(exc, ConfigurationError) and _is_dns_failure(exc):
        sentence = (
            "Could not look up the cluster's DNS SRV record for the mongodb+srv:// URI: check the "
            f"cluster host name in MONGODB_URI and this machine's network or DNS ({detail})."
        )
    elif _uri_parse_error(exc, problem):
        # pymongo's parser can quote a piece of the password: with a known problem, never show its text.
        sentence = f"MONGODB_URI is not a valid MongoDB connection string: {problem or detail}."
    elif isinstance(exc, OperationFailure) and getattr(exc, "code", None) in AUTH_FAILED_CODES:
        sentence = (
            "MongoDB refused the credentials (authentication failed): check the database user and "
            "password in MONGODB_URI (Atlas > Security > Database Access), and percent-encode any "
            "special character in the password."
        )
    elif isinstance(exc, ServerSelectionTimeoutError):
        if _is_dns_failure(exc):
            sentence = (
                "Could not resolve the MongoDB host: check the host name in MONGODB_URI and this "
                f"machine's network or DNS ({detail})."
            )
        else:
            sentence = (
                "Could not reach MongoDB before the timeout: the server is down or unreachable, or "
                f"{ACCESS_LIST_HINT} ({detail})."
            )
    elif isinstance(exc, OperationFailure):
        sentence = f"MongoDB refused the operation (code {getattr(exc, 'code', None)}): {detail}."
    elif isinstance(exc, ConnectionFailure):
        sentence = f"The connection to MongoDB failed: {detail}."
    else:
        sentence = f"Unexpected error talking to MongoDB: {detail}."
    return scrub(sentence, secrets)


__all__ = [
    "ACCESS_LIST_HINT",
    "DEFAULT_MONGODB_DB",
    "DEFAULT_MONGODB_URI",
    "DEFAULT_TIMEOUT_MS",
    "connect",
    "db_name_from_env",
    "describe_failure",
    "dotenv_enabled",
    "load_env",
    "redacted",
    "uri_from_env",
]
