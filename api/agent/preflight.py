"""Preflight for a real studio run: which services are real, which are fallbacks, and why.

    cd api
    uv run python -m agent.preflight            # env report and format checks, no network
    uv run python -m agent.preflight --live     # plus one minimal call per configured service
    uv run python -m agent.preflight --json     # the same report as JSON (a list of services)

It loads the same .env files the API loads (api/.env, then the repo-root .env, never
overriding a variable already set, skipped with STITCH_NO_DOTENV=1), then prints one block
per service: the env vars it reads, whether each is set, the mode the production studio
(agent/production.py) will run it in, and, for a fallback, what the family will see. A last
block lists the run limits (agent.guards.RunLimits).

Secrets come first. No key, password, URI credentials, URI query string or token is ever
written: a key shows as 'set (N chars), format ok' or 'set (N chars)' plus its problem, a URL
shows as scheme and host only, and every provider error, every line of output (text or JSON)
and every log record written while preflight runs goes through one `scrub()`, which removes
each secret value found in the environment, raw, decoded and URL-encoded, before anything is
cut to DETAIL_CHARS.

--live makes one minimal call per configured service, and skips the ones running as fallbacks:

- Anthropic: models.retrieve for each distinct model id (studio, sub-agents, vision) and one
  messages.create with max_tokens=1 on the studio model (so billing and credit are checked).
- Image API, by the provider agent.production picks (IMAGE_PROVIDER, agent.adapters.image_provider):
  Higgsfield: POST {HIGGSFIELD_API_URL}/estimate/{HIGGSFIELD_IMAGE_MODEL} with a minimal body (a
  price estimate: no image is generated); OpenAI-compatible: GET {IMAGE_API_URL}/models/{IMAGE_MODEL}
  (no generation, no cost).
- ElevenLabs: GET /v1/voices/{id} for the default voice and each mapped voice.
- MongoDB: a ping with serverSelectionTimeoutMS=5000 (the family database, unless STITCH_FAKE_DB).
- Render page (STITCH_WEB_URL set): agent.tools.frames.probe_render_page. 'contract' passes;
  'legacy' (the page lacks window.__STITCH_RENDER__/__STITCH_READY__) and 'no_playwright'
  warn, since the checker then sees diagrams; 'unreachable' fails.

Exit codes: 0 when everything required is set, no value has a format error and no live check
failed; 1 otherwise (warnings never fail); 2 for bad arguments.

Every live call goes through a seam on `LiveSeams` (an httpx transport, an Anthropic client
factory, a MongoDB client factory, a Playwright page factory), so the tests run offline.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import os
import re
import sys
import traceback
from collections.abc import Callable, Iterable, Iterator, Mapping, MutableMapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TextIO
from urllib.parse import quote, quote_plus, unquote, unquote_plus

import httpx
from dotenv import load_dotenv

from agent import guards
from agent.adapters import higgsfield as hf_adapter
from agent.adapters import image_provider
from agent.adapters import images as images_adapter
from agent.adapters import tts as tts_adapter
from agent.adapters import vision as vision_adapter
from agent.guards import RunLimits

API_DIR = Path(__file__).resolve().parents[1]
ENV_FILES: tuple[Path, ...] = (API_DIR / ".env", API_DIR.parent / ".env")
"""The files api/main.py loads, in the same order (the first one to set a name wins)."""

_TRUTHY = frozenset({"1", "true", "yes"})
AGENT_MODES: tuple[str, ...] = ("off", "fake", "real")
"""STITCH_AGENT values, as api/main.py reads them."""

DEFAULT_MONGODB_URI = "mongodb://localhost:27017"
"""db.client's database when MONGODB_URI is unset."""
DEFAULT_MONGODB_DB = "stitch"

LIVE_TIMEOUT_S = 10.0
MONGO_TIMEOUT_MS = 5000
RENDER_PROBE_TIMEOUT_MS = 30000
"""A cold Next.js dev server compiles the render route on its first request."""
DETAIL_CHARS = 300
REDACTED = "[redacted]"
_MIN_SCRUB = 4
"""Shorter strings are not scrubbed: replacing them would mangle the report."""
_MIN_QUERY_VALUE = 12
"""A single query value is scrubbed only at token length ('true' and '1' are not secrets)."""
_QUOTES = ("'", '"', "`")

ANTHROPIC_KEY = vision_adapter.API_KEY_ENV
STUDIO_MODEL_ENV = "STITCH_MODEL"
SUBAGENT_MODEL_ENV = "STITCH_SUBAGENT_MODEL"
WEB_URL_ENV = "STITCH_WEB_URL"

SECRET_ENVS: tuple[str, ...] = (
    vision_adapter.API_KEY_ENV,
    images_adapter.API_KEY_ENV,
    hf_adapter.API_KEY_ENV,
    hf_adapter.API_SECRET_ENV,
    tts_adapter.API_KEY_ENV,
    "MONGODB_URI",
    "MONGODB_URI_TEST",
    "JWT_SECRET",
    "VOYAGE_API_KEY",
)
"""Never printed, and scrubbed whole from everything preflight writes."""

_SECRET_NAME = re.compile(r"KEY|SECRET|TOKEN|PASSWORD|PASSWD|CREDENTIAL", re.IGNORECASE)
"""Any other variable named like a secret is scrubbed too (when its value is 8+ characters)."""

LIMIT_ENVS: tuple[tuple[str, str], ...] = (
    (guards.ENV_MAX_MODEL_CALLS, "max_model_calls"),
    (guards.ENV_MAX_IMAGES_PER_RUN, "max_images_per_run"),
    (guards.ENV_MAX_GRAPH_STEPS, "max_graph_steps"),
    (guards.ENV_MODEL_TIMEOUT_S, "model_timeout_s"),
    (guards.ENV_MODEL_MAX_RETRIES, "model_max_retries"),
    (guards.ENV_RUN_TIMEOUT_S, "run_timeout_s"),
)
"""Each STITCH_* run limit and its RunLimits field."""

ENV_NAMES: tuple[str, ...] = (
    "STITCH_AGENT",
    ANTHROPIC_KEY,
    STUDIO_MODEL_ENV,
    SUBAGENT_MODEL_ENV,
    vision_adapter.MODEL_ENV,
    "MONGODB_URI",
    "MONGODB_DB",
    "STITCH_FAKE_DB",
    image_provider.PROVIDER_ENV,
    hf_adapter.API_KEY_ENV,
    hf_adapter.API_SECRET_ENV,
    hf_adapter.URL_ENV,
    hf_adapter.MODEL_ENV,
    hf_adapter.STYLE_ENV,
    hf_adapter.RESULT_HOSTS_ENV,
    images_adapter.API_KEY_ENV,
    images_adapter.URL_ENV,
    images_adapter.MODEL_ENV,
    tts_adapter.API_KEY_ENV,
    tts_adapter.MODEL_ENV,
    tts_adapter.VOICES_ENV,
    tts_adapter.DEFAULT_VOICE_ENV,
    WEB_URL_ENV,
    *(name for name, _ in LIMIT_ENVS),
)
"""Every variable the report reads, in report order."""

FALLBACKS: dict[str, str] = {
    "agent": (
        "scripted runs: adding a memory plays a canned run (seed/fake_runs.py) with no model "
        "calls, so the family's own story is not staged"
    ),
    "persistence": (
        "runs, their checkpoints and the studio's /memories/ notes live in memory and are lost when the API restarts"
    ),
    "art": (
        "placeholder art: props and set layers are flat shapes in a placeholder color instead "
        "of painted art (cut-outs of uploaded photos still work)"
    ),
    "tts": (
        "silent narration: no voice plays, but the words still highlight on estimated timings "
        "(0.3 s a word) and the run goes on"
    ),
    "renderer": (
        "diagram frames: the checker critiques labelled diagrams of the stage, not screenshots, "
        "so it cannot see art or layout problems (the family sees no difference)"
    ),
    "compiler": "FakeCompiler: timelines come from the stand-in compiler, not screenplay.compiler",
}

RENDER_STATES: dict[str, tuple[str, str]] = {
    "contract": ("pass", "the render page meets the contract: the checker sees screenshots of the real stage"),
    "legacy": (
        "warn",
        "the render page lacks window.__STITCH_RENDER__/__STITCH_READY__: the checker will see diagrams",
    ),
    "unreachable": (
        "fail",
        "the render page did not load or never became ready: the checker will see diagrams",
    ),
    "no_playwright": (
        "warn",
        "Playwright or its Chromium is not installed (uv run playwright install chromium): diagrams will be used",
    ),
}
"""probe_render_page's answer -> (live status, what it means)."""


# ------------------------------------------------------------------------------ env files


def dotenv_enabled(environ: Mapping[str, str] | None = None) -> bool:
    """False under pytest or with STITCH_NO_DOTENV=1 (api/main.py's rule)."""
    env = os.environ if environ is None else environ
    if "pytest" in sys.modules:
        return False
    return (env.get("STITCH_NO_DOTENV") or "").strip().lower() not in _TRUTHY


def load_env_files(paths: Sequence[Path] = ENV_FILES) -> list[Path]:
    """Load each existing file into os.environ, in order, never overriding a set variable."""
    loaded: list[Path] = []
    for path in paths:
        if path.is_file():
            load_dotenv(path, override=False)
            loaded.append(path)
    return loaded


# ---------------------------------------------------------------------------- secrecy


def _split_userinfo(rest: str) -> tuple[str, str]:
    """(userinfo, everything after it) of a scheme-free URI, taking the LAST '@' in the whole string.

    Splitting off the path or query first would miss credentials whose password holds an
    unescaped '/' or '?', and show them as the host; taking the last '@' never leaks them.
    """
    userinfo, at, after = rest.rpartition("@")
    return (userinfo, after) if at else ("", rest)


def _mongo_host_part(rest: str) -> str:
    """pymongo's host part of a scheme-free URI: cut at the first '?', then at the first '/'."""
    return rest.partition("?")[0].partition("/")[0]


def _options_well_formed(opts: str) -> bool:
    """Whether pymongo would read `opts` as key=value pairs ('&' or ';' separated, not mixed)."""
    if "&" in opts and ";" in opts:
        return False
    return all(chunk.count("=") == 1 and "@" not in chunk.partition("=")[0] for chunk in re.split(r"[&;]", opts))


def _stray_at(rest: str) -> bool:
    """An '@' after the host part that cannot be part of a database name or option value.

    pymongo cuts the host part at the first '?' and then the first '/', so a password holding an
    unescaped '/' or '?' (and an '@') leaves an '@' in the database path, or in an options string
    that is not key=value pairs. pymongo then rejects the URI or reads part of the password as
    the host.
    """
    host_plus_db, _, opts = rest.partition("?")
    return "@" in host_plus_db.partition("/")[2] or ("@" in opts and not _options_well_formed(opts))


def _mongo_userinfo(rest: str) -> tuple[str, str]:
    """(credentials, what follows them) of a scheme-free MongoDB URI, never splitting a password.

    Well-formed credentials end at the last '@' of the host part (an '@' inside an option value
    is not theirs). Otherwise everything up to the last '@' anywhere counts as credentials.
    """
    host_part = _mongo_host_part(rest)
    if "@" in host_part and not _stray_at(rest):
        userinfo = host_part.rpartition("@")[0]
        return userinfo, rest[len(userinfo) + 1 :]
    return _split_userinfo(rest)


def _uri_secrets(value: str, *, mongo: bool) -> list[str]:
    """The credentials of a URI (whole, each half, each piece between reserved characters),
    its whole query string and each token-length query value."""
    rest = value.strip().partition("://")[2]
    creds, after = _mongo_userinfo(rest) if mongo else _split_userinfo(rest)
    found: list[str] = []
    if creds:
        # A parser error can quote one piece of a password with an unescaped reserved character
        # (pymongo's 'Bad database name ...'), so every piece is a secret too.
        found += [creds, *creds.split(":", 1), *re.split(r"[:/?@#&;=]", creds)]
    query = re.split(r"[?#]", after, maxsplit=1)[1:]
    if query:
        values = [v for chunk in re.split(r"[&;#]", query[0]) for v in chunk.split("=", 1)[1:]]
        found += [query[0], *(v for v in values if len(v) >= _MIN_QUERY_VALUE)]
    return found


def secret_values(env: Mapping[str, str]) -> list[str]:
    """Every secret string that must never be written, in every form it could be echoed in.

    The values of SECRET_ENVS and of variables named like secrets, the credentials and query
    string of every URI in the environment, and of each: the stripped and unquoted value, its
    URL-decoded, URL-encoded and JSON-escaped forms. Longest first. Never raises.
    """
    bases: list[str] = []
    for name, value in env.items():
        if not value:
            continue
        if name in SECRET_ENVS or (_SECRET_NAME.search(name) and len(value.strip()) >= 8):
            bases.append(value)
        if "://" in value:
            bases += _uri_secrets(value, mongo=value.strip().lower().startswith("mongodb"))
    plain: set[str] = set()
    for base in bases:
        for variant in (base, base.strip(), base.strip().strip("".join(_QUOTES)).strip()):
            plain.update(_forms(variant, (lambda v: v, unquote, unquote_plus)))
    encoders: tuple[Callable[[str], str], ...] = (
        lambda v: v,
        quote,
        lambda v: quote(v, safe=""),
        quote_plus,
        # How a JSON error body echoes it: non-ASCII as \\uXXXX, or raw with '"' and '\\' escaped.
        lambda v: json.dumps(v)[1:-1],
        lambda v: json.dumps(v, ensure_ascii=False)[1:-1],
    )
    found = {form for s in plain for form in _forms(s, encoders)}
    return sorted((s for s in found if len(s) >= _MIN_SCRUB), key=len, reverse=True)


def _forms(value: str, encoders: Iterable[Callable[[str], str]]) -> set[str]:
    """Each encoding of `value` that succeeds (a lone surrogate from a bad byte in the
    environment cannot be UTF-8 encoded: that one form is skipped, never a crash)."""
    found: set[str] = set()
    for encode in encoders:
        with contextlib.suppress(ValueError):  # UnicodeError is a ValueError
            found.add(encode(value))
    return found


def scrub(text: str, secrets: Iterable[str]) -> str:
    """`text` with every secret (any letter case) replaced by [redacted]: the one scrub preflight uses."""
    ordered = sorted({s for s in secrets if s}, key=len, reverse=True)
    if not ordered or not text:
        return text
    return re.sub("|".join(re.escape(s) for s in ordered), REDACTED, text, flags=re.IGNORECASE)


def _cut(text: str, limit: int = DETAIL_CHARS) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 3] + "..."


def _detail(text: str, secrets: Sequence[str]) -> str:
    """Provider text for the report: scrubbed first, then cut (a cut must never split a secret)."""
    return _cut(scrub(text, secrets))


def _scrub_tree(value: Any, secrets: Sequence[str]) -> Any:
    if isinstance(value, str):
        return scrub(value, secrets)
    if isinstance(value, list):
        return [_scrub_tree(v, secrets) for v in value]
    if isinstance(value, dict):
        return {k: _scrub_tree(v, secrets) for k, v in value.items()}
    return value


class _ScrubLogs(logging.Filter):
    """Rewrites each log record (message, arguments, traceback) through scrub()."""

    def __init__(self, secrets: Sequence[str]) -> None:
        super().__init__()
        self.secrets = secrets

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 - a malformed record still must not leak its arguments
            message = str(record.msg)
        if record.exc_info:
            message += "\n" + "".join(traceback.format_exception(*record.exc_info)).rstrip()
            record.exc_info = None
        record.exc_text = None
        record.msg, record.args = scrub(message, self.secrets), ()
        if record.stack_info:
            record.stack_info = scrub(record.stack_info, self.secrets)
        return True


@contextlib.contextmanager
def scrubbed_logs(secrets: Sequence[str]) -> Iterator[None]:
    """While inside, every log handler in the process (and logging.lastResort) writes scrubbed records."""
    handlers: set[logging.Handler] = set(logging.getLogger().handlers)
    for logger in list(logging.Logger.manager.loggerDict.values()):
        if isinstance(logger, logging.Logger):
            handlers.update(logger.handlers)
    if logging.lastResort is not None:
        handlers.add(logging.lastResort)
    scrubber = _ScrubLogs(secrets)
    for handler in handlers:
        handler.addFilter(scrubber)
    try:
        yield
    finally:
        for handler in handlers:
            handler.removeFilter(scrubber)


def redact_mongo_uri(uri: str) -> str:
    """Scheme and hosts only: no credentials, database path or options."""
    scheme, sep, rest = uri.strip().partition("://")
    if not sep:
        return "<not a URI>"
    userinfo, after = _mongo_userinfo(rest)
    hosts = re.split(r"[/?#]", after, maxsplit=1)[0]
    return f"{scheme}://" + ("<credentials redacted>@" if userinfo else "") + hosts


def mongo_uri_problem(uri: str) -> str | None:
    """What pymongo's URI parser would reject (or misread) in the credentials, without parsing the URI.

    An '@' after the host part (in the database path, or in options that are not key=value
    pairs) means a password with an unescaped reserved character; in the credentials, a second
    ':' or '@', or a '%' that is not an escape, is rejected (pymongo's parse_userinfo).
    """
    rest = uri.strip().partition("://")[2]
    host_part = _mongo_host_part(rest)
    # Worded without "user"/"password" as whole words, so the scrub of a short username cannot mangle it.
    fix = "percent-encode the credentials (urllib.parse.quote_plus)"
    if _stray_at(rest) or ("@" not in host_part and "@" in rest):
        return f"an '@' comes after a '/' or '?', so the credentials hold an unescaped '/', '?' or '@': {fix}"
    if "@" not in host_part:
        return None
    userinfo = host_part.rpartition("@")[0]
    if "@" in userinfo or userinfo.count(":") > 1 or re.search(r"%(?![0-9A-Fa-f]{2})", userinfo):
        return f"the credentials hold an unescaped ':', '@' or '%': {fix}"
    return None


def redact_url(url: str) -> str:
    """An http(s) URL without any user:password@ part, query string or fragment."""
    scheme, sep, rest = url.strip().partition("://")
    if not sep:
        return "<not a URL>"
    userinfo, after = _split_userinfo(rest)
    base = re.split(r"[?#]", after, maxsplit=1)[0]
    shown = f"{scheme}://" + ("<credentials redacted>@" if userinfo else "") + base
    return shown + ("?<query redacted>" if base != after else "")


# ------------------------------------------------------------------------------- report


@dataclass
class VarReport:
    name: str
    set: bool
    shown: str
    """What the report prints for the value; never a secret."""
    problem: str | None = None
    severity: str | None = None
    """'error', 'warning' or None."""

    def as_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "set": self.set,
            "value": self.shown,
            "problem": self.problem,
            "severity": self.severity,
        }


@dataclass
class LiveResult:
    status: str
    """'pass', 'warn', 'fail', 'skipped' or 'not_run' (without --live)."""
    detail: str = ""


@dataclass
class ServiceReport:
    service: str
    mode: str
    vars: list[VarReport] = field(default_factory=list)
    summary: str = ""
    fallback: str | None = None
    """What the family experiences when this service runs as a fallback."""
    notes: list[str] = field(default_factory=list)
    problems: list[tuple[str, str]] = field(default_factory=list)
    """Service-level (severity, text), beyond the per-variable ones."""
    live: LiveResult = field(default_factory=lambda: LiveResult("not_run"))

    def _issues(self, severity: str) -> list[str]:
        out = [f"{v.name}: {v.problem}" for v in self.vars if v.severity == severity and v.problem]
        return out + [text for sev, text in self.problems if sev == severity]

    def errors(self) -> list[str]:
        return self._issues("error")

    def warnings(self) -> list[str]:
        return self._issues("warning")

    def as_json(self) -> dict[str, Any]:
        return {
            "service": self.service,
            "mode": self.mode,
            "summary": self.summary,
            "vars": [v.as_json() for v in self.vars],
            "fallback": self.fallback,
            "notes": list(self.notes),
            "problems": [{"severity": sev, "text": text} for sev, text in self.problems],
            "live": {"status": self.live.status, "detail": self.live.detail},
        }


@dataclass
class LiveSeams:
    """Injectable seams for every outside call (tests pass fakes; defaults are the real thing)."""

    http_transport: httpx.BaseTransport | None = None
    anthropic_factory: Callable[[str, float], Any] | None = None
    """(api_key, timeout_s) -> an object with .models.retrieve and .messages.create."""
    mongo_factory: Callable[[str, int], Any] | None = None
    """(uri, server_selection_timeout_ms) -> a MongoClient-like object (.admin.command, .close)."""
    page_factory: Any = None
    """A frames.PageFactory for probe_render_page; None launches headless Chromium."""
    timeout_s: float = LIVE_TIMEOUT_S
    render_timeout_ms: int = RENDER_PROBE_TIMEOUT_MS


# ------------------------------------------------------------------------------ helpers


def _stripped(env: Mapping[str, str], name: str) -> str | None:
    """agent.production._env (guards.env_value): the value stripped, or None when unset or blank."""
    return guards.env_value(env, name)


def fake_db(env: Mapping[str, str]) -> bool:
    return (env.get("STITCH_FAKE_DB") or "").strip().lower() in _TRUTHY


def agent_mode(env: Mapping[str, str]) -> tuple[str, bool]:
    """(mode, known) as api/main.py's agent_mode: known is False for an unknown STITCH_AGENT."""
    raw = (env.get("STITCH_AGENT") or "").strip().lower()
    if raw in AGENT_MODES:
        return raw, True
    has_key = bool((env.get(ANTHROPIC_KEY) or "").strip())
    default = "fake" if fake_db(env) or not has_key else "real"
    return default, not raw


def shape_problem(value: str) -> str | None:
    """Quotes, surrounding whitespace or a newline: the mistakes copy-pasting a key makes."""
    if "\n" in value or "\r" in value:
        return "contains a newline (paste the value on one line)"
    if value != value.strip():
        return "has leading or trailing whitespace"
    if value[:1] in _QUOTES or value[-1:] in _QUOTES:
        return "is wrapped in quotes (remove them; in .env write NAME=value)"
    if any(ch.isspace() for ch in value):
        return "contains whitespace"
    return None


def _url_problem(value: str) -> str | None:
    scheme, sep, rest = value.strip().partition("://")
    host = rest.split("/", 1)[0].rpartition("@")[2] if sep else ""
    if scheme.lower() not in ("http", "https") or not host:
        return "must be an http:// or https:// URL"
    return None


def _unset(value: str | None, default: str | None = None) -> str:
    shown = "unset" + (f" (default: {default})" if default is not None else "")
    return shown if value is None else "blank, treated as " + shown


def _plain_var(env: Mapping[str, str], name: str, default: str | None) -> VarReport:
    """A non-secret variable: its value is shown (unless it is malformed)."""
    value = env.get(name)
    if value is None or not value.strip():
        return VarReport(name, False, _unset(value, default))
    problem = shape_problem(value)
    if problem:
        return VarReport(name, True, f"set ({len(value)} chars)", problem, "error")
    return VarReport(name, True, _cut(value, 120))


def _model_var(env: Mapping[str, str], name: str, default: str) -> VarReport:
    """A model id (or other setting production reads with guards.env_value): blank means the
    default and a padded value is used stripped, so the value shown is the one in use."""
    raw = env.get(name)
    value = guards.env_value(env, name)
    if value is None:
        return VarReport(name, False, _unset(raw, default))
    if problem := shape_problem(value):
        return VarReport(name, True, f"set ({len(raw or '')} chars)", problem, "error")
    var = VarReport(name, True, _cut(value, 120))
    if raw != value:
        var.problem = "has leading or trailing whitespace; the studio uses it stripped"
        var.severity = "warning"
    return var


def _secret_var(
    env: Mapping[str, str],
    name: str,
    *,
    missing: str | None = None,
    prefix_hint: Callable[[str], str | None] | None = None,
) -> VarReport:
    """A key: only its length and format are ever shown. `missing` makes it required."""
    value = env.get(name)
    if value is None or not value.strip():
        return VarReport(name, False, _unset(value), missing, "error" if missing else None)
    size = f"set ({len(value)} chars)"
    if problem := shape_problem(value):
        return VarReport(name, True, size, problem, "error")
    if hint := (prefix_hint(value) if prefix_hint else None):
        return VarReport(name, True, size, hint, "warning")
    return VarReport(name, True, f"{size}, format ok")


def _url_var(env: Mapping[str, str], name: str, default: str | None = None) -> VarReport:
    value = env.get(name)
    if value is None or not value.strip():
        return VarReport(name, False, _unset(value, default))
    if problem := shape_problem(value) or _url_problem(value):
        return VarReport(name, True, f"set ({len(value)} chars)", problem, "error")
    return VarReport(name, True, redact_url(value))


# --------------------------------------------------------------------------- services


def _studio_model(env: Mapping[str, str]) -> str:
    """The studio's model id as studio.make_model picks it."""
    from agent.studio import DEFAULT_MODEL

    return guards.env_value(env, STUDIO_MODEL_ENV) or DEFAULT_MODEL


def _vision_model(env: Mapping[str, str]) -> str:
    """The vision model id as AnthropicVision picks it."""
    from agent.studio import DEFAULT_MODEL

    return guards.env_value(env, vision_adapter.MODEL_ENV) or DEFAULT_MODEL


def _image_model(env: Mapping[str, str]) -> str:
    """The image model id as OpenAIImageModel picks it."""
    return guards.env_value(env, images_adapter.MODEL_ENV) or images_adapter.DEFAULT_MODEL


def _image_url(env: Mapping[str, str]) -> str:
    """The image API base URL as OpenAIImageModel picks it."""
    return (guards.env_value(env, images_adapter.URL_ENV) or images_adapter.DEFAULT_URL).rstrip("/")


def art_label(env: Mapping[str, str]) -> str:
    """agent.production's art label: 'higgsfield', 'openai' or 'placeholder' (same rule)."""
    choice = image_provider.choose_image_provider(env)
    if choice.provider == "higgsfield" and hf_adapter.config_problem(env):
        return "placeholder"
    return choice.provider


def _compiler_label() -> str:
    from agent.tools.compile import load_compiler
    from agent.wiring import compiler_label

    return compiler_label(load_compiler())


def modes(env: Mapping[str, str] | None = None, *, compiler: str | None = None) -> dict[str, str]:
    """The production studio's label for each service, as agent.production would pick it.

    The keys are those of `agent.production.production_services().labels`. model, vision and
    critic are 'missing' when ANTHROPIC_API_KEY is unset (production refuses to build).
    `compiler` skips loading the compiler when the caller already knows its label.
    """
    env = os.environ if env is None else env
    has_key = _stripped(env, ANTHROPIC_KEY) is not None
    return {
        "model": "real" if has_key else "missing",
        "subagent_model": _stripped(env, SUBAGENT_MODEL_ENV) or "studio",
        "repo": "mongo",
        "persistence": "mongo" if _stripped(env, "MONGODB_URI") and not fake_db(env) else "memory",
        "art": art_label(env),
        "tts": "real" if _stripped(env, tts_adapter.API_KEY_ENV) else "fake",
        "vision": "real" if has_key else "missing",
        "critic": "real" if has_key else "missing",
        "renderer": "playwright" if _stripped(env, WEB_URL_ENV) else "diagram",
        "compiler": compiler if compiler is not None else _compiler_label(),
        "limits": RunLimits.from_env(env).label(),
    }


def _agent_report(env: Mapping[str, str]) -> ServiceReport:
    mode, known = agent_mode(env)
    raw = env.get("STITCH_AGENT")
    has_key = _stripped(env, ANTHROPIC_KEY) is not None
    if raw is None or not raw.strip():
        why = "STITCH_FAKE_DB is on" if fake_db(env) else f"ANTHROPIC_API_KEY is {'set' if has_key else 'not set'}"
        var = VarReport("STITCH_AGENT", False, f"unset (default: {mode}, because {why})")
    elif not known:
        problem = f"not one of {', '.join(AGENT_MODES)}; the API uses {mode!r}"
        var = VarReport("STITCH_AGENT", True, f"set ({len(raw)} chars)", problem, "error")
    else:
        var = VarReport("STITCH_AGENT", True, raw.strip().lower())
    report = ServiceReport("agent", mode, [var])
    if mode == "real" and not has_key:
        report.mode = "fake"
        report.summary = "STITCH_AGENT=real, but without ANTHROPIC_API_KEY startup falls back to scripted runs"
        report.fallback = FALLBACKS["agent"]
        report.problems.append(("error", "STITCH_AGENT=real cannot build the studio without ANTHROPIC_API_KEY"))
    elif mode == "real":
        report.summary = "adding a memory runs the model studio (agent.production)"
        report.notes.append(
            "if the studio cannot be built (see any errors below), startup logs a warning and falls back to scripted runs"
        )
    elif mode == "fake":
        report.summary = "adding a memory plays scripted runs (no model calls)"
        report.fallback = FALLBACKS["agent"]
    else:
        report.summary = "no agent: adding a memory answers 503"
        report.problems.append(("warning", "STITCH_AGENT=off: the add-a-memory routes answer 503"))
    return report


def _model_report(env: Mapping[str, str], labels: Mapping[str, str]) -> ServiceReport:
    from agent.studio import DEFAULT_MODEL

    key = _secret_var(
        env,
        ANTHROPIC_KEY,
        missing="missing (required, no fallback): without it there is no studio, and STITCH_AGENT=real "
        "falls back to scripted runs",
        prefix_hint=lambda v: None if v.startswith("sk-ant-") else "Anthropic API keys start with sk-ant-",
    )
    report = ServiceReport(
        "model",
        labels["model"],
        [
            key,
            _model_var(env, STUDIO_MODEL_ENV, DEFAULT_MODEL),
            _model_var(env, SUBAGENT_MODEL_ENV, "the studio model"),
        ],
    )
    if labels["model"] == "real":
        sub = labels["subagent_model"]
        report.summary = f"studio model {_studio_model(env)}; sub-agents use " + (
            "the studio model" if sub == "studio" else sub
        )
    else:
        report.summary = "no model: the studio cannot be built (ANTHROPIC_API_KEY has no fallback)"
    return report


def _vision_report(env: Mapping[str, str], labels: Mapping[str, str]) -> ServiceReport:
    from agent.studio import DEFAULT_MODEL

    key_set = _stripped(env, ANTHROPIC_KEY) is not None
    key = VarReport(ANTHROPIC_KEY, key_set, "see model" if key_set else "unset (see model)")
    report = ServiceReport("vision", labels["vision"], [key, _model_var(env, vision_adapter.MODEL_ENV, DEFAULT_MODEL)])
    report.summary = (
        f"Claude vision ({_vision_model(env)}) describes photos and critiques frames"
        if key_set
        else "no vision without ANTHROPIC_API_KEY"
    )
    if not _stripped(env, vision_adapter.MODEL_ENV) and _stripped(env, STUDIO_MODEL_ENV):
        report.notes.append(
            f"vision does not follow {STUDIO_MODEL_ENV}; set {vision_adapter.MODEL_ENV} to change its model"
        )
    return report


def _persistence_report(env: Mapping[str, str], labels: Mapping[str, str]) -> ServiceReport:
    uri = env.get("MONGODB_URI")
    fake = fake_db(env)
    if uri is None or not uri.strip():
        uri_var = VarReport("MONGODB_URI", False, _unset(uri))
    else:
        size = f"set ({len(uri)} chars)"
        if problem := shape_problem(uri):
            uri_var = VarReport("MONGODB_URI", True, size, problem, "error")
        elif not uri.startswith(("mongodb://", "mongodb+srv://")):
            uri_var = VarReport("MONGODB_URI", True, size, "must start with mongodb:// or mongodb+srv://", "error")
        else:
            uri_var = VarReport("MONGODB_URI", True, f"{size}: {redact_mongo_uri(uri)}")
            if cred_problem := mongo_uri_problem(uri):
                uri_var.problem, uri_var.severity = cred_problem, "error"
    fake_raw = (env.get("STITCH_FAKE_DB") or "").strip()
    fake_var = VarReport(
        "STITCH_FAKE_DB", bool(fake_raw), f"{fake_raw} ({'on' if fake else 'off'})" if fake_raw else "unset (off)"
    )
    report = ServiceReport(
        "persistence", labels["persistence"], [uri_var, _plain_var(env, "MONGODB_DB", DEFAULT_MONGODB_DB), fake_var]
    )
    if fake:
        report.summary = "STITCH_FAKE_DB: family data and runs are in an in-memory mongomock database"
        report.fallback = FALLBACKS["persistence"] + "; the family data is in memory too (demo family seeded)"
        if uri_var.set:
            report.notes.append("MONGODB_URI is ignored while STITCH_FAKE_DB is on")
    elif labels["persistence"] == "mongo":
        report.summary = "family data, run records, checkpoints and /memories/ on MongoDB; runs survive a restart"
        report.fallback = (
            f"{FALLBACKS['persistence']}, when MongoDB is unreachable or refuses the credentials as the API "
            "starts (the studio still builds, logs an error and the services line says persistence=memory); "
            "check the ping with --live"
        )
    else:
        report.summary = f"family data on {DEFAULT_MONGODB_URI} (db.client's default); runs in memory"
        report.fallback = FALLBACKS["persistence"]
        unset = (
            f"MONGODB_URI is unset and STITCH_FAKE_DB is off: the API uses {DEFAULT_MONGODB_URI}; "
            "set MONGODB_URI, or STITCH_FAKE_DB=1 for a dry run"
        )
        report.problems.append(("warning", unset))
    return report


def _provider_var(env: Mapping[str, str], choice: image_provider.ImageProviderChoice) -> VarReport:
    name = image_provider.PROVIDER_ENV
    raw = env.get(name)
    if raw is None or not raw.strip():
        return VarReport(name, False, _unset(raw, f"{choice.provider}, because {choice.reason}"))
    var = VarReport(name, True, _cut(raw.strip(), 40))
    if choice.warning:
        var.problem, var.severity = choice.warning, "warning"
    return var


def _hf_vars(env: Mapping[str, str], selected: bool) -> list[VarReport]:
    """The Higgsfield variables: key and secret as length and format only, the rest shown."""
    severity = "error" if selected else "warning"
    key = _secret_var(env, hf_adapter.API_KEY_ENV)
    secret = _secret_var(env, hf_adapter.API_SECRET_ENV)
    key_value = _stripped(env, hf_adapter.API_KEY_ENV)
    if key_value and not key.problem:
        if hf_adapter.credentials(env) is None:
            key.problem = (
                f"no secret: set {hf_adapter.API_SECRET_ENV}, or write the key as id:secret "
                "(both from https://console.higgsfield.ai)"
            )
            key.severity = severity
            key.shown = key.shown.replace(", format ok", "")
        elif ":" in key_value and secret.set:
            key.problem = (
                f"looks like id:secret but {hf_adapter.API_SECRET_ENV} is set too; "
                "the whole value is used as the key ID"
            )
            key.severity = "warning"
            key.shown = key.shown.replace(", format ok", "")
    model = _model_var(env, hf_adapter.MODEL_ENV, hf_adapter.DEFAULT_MODEL)
    model_id = hf_adapter.configured_model(env)
    if model.set and not model.problem and model_id not in hf_adapter.MODELS:
        model.problem = f"not a supported endpoint; use one of: {', '.join(hf_adapter.MODELS)}"
        model.severity = severity
    style_raw = env.get(hf_adapter.STYLE_ENV)
    if style_raw is None or not style_raw.strip():
        style = VarReport(hf_adapter.STYLE_ENV, False, _unset(style_raw, repr(hf_adapter.DEFAULT_STYLE_SUFFIX)))
    else:
        suffix = hf_adapter.configured_style_suffix(env)
        style = VarReport(hf_adapter.STYLE_ENV, True, repr(_cut(suffix, 120)) if suffix else "none (no suffix)")
    hosts_raw = env.get(hf_adapter.RESULT_HOSTS_ENV)
    hosts = ", ".join(hf_adapter.configured_result_hosts(env))
    hosts_var = VarReport(
        hf_adapter.RESULT_HOSTS_ENV,
        bool(hosts_raw and hosts_raw.strip()),
        f"allowed result hosts: {hosts} (and their subdomains)",
    )
    return [key, secret, _url_var(env, hf_adapter.URL_ENV, hf_adapter.DEFAULT_URL), model, style, hosts_var]


def _openai_vars(env: Mapping[str, str]) -> list[VarReport]:
    effective_url = _image_url(env)
    openai_default = effective_url == images_adapter.DEFAULT_URL.rstrip("/")
    key = _secret_var(
        env,
        images_adapter.API_KEY_ENV,
        prefix_hint=(lambda v: None if v.startswith("sk-") else "OpenAI API keys start with sk-")
        if openai_default
        else None,
    )
    return [
        key,
        _url_var(env, images_adapter.URL_ENV, images_adapter.DEFAULT_URL),
        _model_var(env, images_adapter.MODEL_ENV, images_adapter.DEFAULT_MODEL),
    ]


def _art_report(env: Mapping[str, str], labels: Mapping[str, str]) -> ServiceReport:
    choice = image_provider.choose_image_provider(env)
    hf_selected = choice.provider == "higgsfield"
    report = ServiceReport(
        "art",
        labels["art"],
        [_provider_var(env, choice), *_hf_vars(env, hf_selected), *_openai_vars(env)],
    )
    budget = RunLimits.from_env(env).max_images_per_run
    budget_fallback = (
        f"{FALLBACKS['art']}, for one prop or layer when its generation fails, and for the rest of a run once "
        f"it has used its image budget ({guards.ENV_MAX_IMAGES_PER_RUN}={budget})"
    )
    if labels["art"] == "higgsfield":
        model = hf_adapter.configured_model(env)
        url = redact_url(hf_adapter.configured_base_url(env))
        report.summary = (
            f"generated prop and set art on Higgsfield ({model} at {url}), at most {budget} image(s) a run"
        )
        report.fallback = budget_fallback
        report.notes.append(
            f"{model} takes no reference image: references are ignored and art is drawn from the prompt alone"
        )
        report.notes.append(
            "the docs do not name the host results are served from: a result on a host outside "
            f"{hf_adapter.RESULT_HOSTS_ENV}'s list is refused (the error names the host to add)"
        )
    elif labels["art"] == "openai":
        model = _image_model(env)
        report.summary = (
            f"generated prop and set art ({model} at {redact_url(_image_url(env))}), at most {budget} image(s) a run"
        )
        report.fallback = budget_fallback
    elif hf_selected:
        # The key or model variable above carries the error.
        problem = hf_adapter.config_problem(env)
        report.summary = f"Higgsfield is selected ({choice.reason}) but {problem}: placeholder art"
        report.fallback = FALLBACKS["art"]
    else:
        report.summary = f"placeholder art ({choice.reason})"
        report.fallback = FALLBACKS["art"]
    if labels["art"] != "placeholder" and budget == 0:
        report.problems.append(("warning", f"{guards.ENV_MAX_IMAGES_PER_RUN}=0: every run gets placeholder art"))
    return report


def _voices_var(env: Mapping[str, str], severity: str) -> VarReport:
    from agent.tools.narration import PLACEHOLDER_VOICES

    raw = env.get(tts_adapter.VOICES_ENV)
    if raw is None or not raw.strip():
        return VarReport(tts_adapter.VOICES_ENV, False, "unset (every voice uses the default voice)")
    size = f"set ({len(raw)} chars)"
    if "\n" in raw or "\r" in raw:
        return VarReport(tts_adapter.VOICES_ENV, True, size, "contains a newline", severity)
    if raw[:1] in _QUOTES or raw.strip()[-1:] in _QUOTES:
        return VarReport(tts_adapter.VOICES_ENV, True, size, "is wrapped in quotes (remove them)", severity)
    try:
        mapping = tts_adapter.parse_voices(raw)
        for voice_id in mapping.values():
            tts_adapter._check_voice_id(voice_id)  # the adapter's own rule
    except ValueError as exc:
        return VarReport(tts_adapter.VOICES_ENV, True, size, str(exc), severity)
    var = VarReport(tts_adapter.VOICES_ENV, True, f"{len(mapping)} voice(s) mapped: {', '.join(sorted(mapping))}")
    if unknown := sorted(set(mapping) - set(PLACEHOLDER_VOICES)):
        var.problem = (
            f"maps {', '.join(unknown)}, which the studio never asks for (it uses {', '.join(PLACEHOLDER_VOICES)})"
        )
        var.severity = "warning"
    return var


def _default_voice_var(env: Mapping[str, str], severity: str) -> VarReport:
    name = tts_adapter.DEFAULT_VOICE_ENV
    raw = env.get(name)
    if not raw:
        return VarReport(name, False, f"unset (default: {tts_adapter.DEFAULT_VOICE_ID}, Rachel)")
    size = f"set ({len(raw)} chars)"
    if problem := shape_problem(raw):
        return VarReport(name, True, size, problem, severity)
    if not tts_adapter._VOICE_ID.match(raw):  # the adapter's own rule
        return VarReport(
            name, True, size, "not a valid ElevenLabs voice id (letters, digits, _ and -, at most 64)", severity
        )
    return VarReport(name, True, raw)


def _tts_report(env: Mapping[str, str], labels: Mapping[str, str]) -> ServiceReport:
    real = labels["tts"] == "real"
    # The voices only matter (and only break the studio build) when the key is set.
    severity = "error" if real else "warning"
    key = _secret_var(
        env,
        tts_adapter.API_KEY_ENV,
        prefix_hint=lambda v: (
            None if v.startswith("sk_") or re.fullmatch(r"[0-9a-f]{32}", v) else "ElevenLabs API keys start with sk_"
        ),
    )
    voices, default_voice = _voices_var(env, severity), _default_voice_var(env, severity)
    report = ServiceReport(
        "tts",
        labels["tts"],
        [key, _model_var(env, tts_adapter.MODEL_ENV, tts_adapter.DEFAULT_MODEL), voices, default_voice],
    )
    if real:
        report.summary = "ElevenLabs narration with word timings"
        report.fallback = (
            f"{FALLBACKS['tts']}, for one teller's voice pass when an ElevenLabs call fails in the middle "
            "of a run and its one retry fails too (synthesize_narration warns and carries on)"
        )
        if "error" in (voices.severity, default_voice.severity):
            report.notes.append("ElevenLabsTTS() rejects these voices, so the studio cannot be built")
    else:
        report.summary = "ELEVENLABS_API_KEY is unset: silent narration"
        report.fallback = FALLBACKS["tts"]
    return report


def _renderer_report(env: Mapping[str, str], labels: Mapping[str, str]) -> ServiceReport:
    report = ServiceReport("renderer", labels["renderer"], [_url_var(env, WEB_URL_ENV)])
    if labels["renderer"] == "playwright":
        report.summary = "the checker screenshots the web /render/[memoryId] page in headless Chromium"
        report.fallback = (
            f"{FALLBACKS['renderer']}, per render, when the page is unreachable, Playwright or its Chromium "
            "is missing, or the page lacks the render contract (window.__STITCH_RENDER__, ?t=, "
            "window.__STITCH_READY__)"
        )
        report.notes.append("--live probes the render page for the contract")
    else:
        report.summary = "STITCH_WEB_URL is unset: Pillow diagrams of the stage"
        report.fallback = FALLBACKS["renderer"]
        report.notes.append(
            "for screenshots, run the web app and set STITCH_WEB_URL to it (e.g. http://localhost:3000), "
            "then check the render page with --live"
        )
    return report


def _compiler_report(labels: Mapping[str, str]) -> ServiceReport:
    report = ServiceReport("compiler", labels["compiler"])
    if labels["compiler"] == "FakeCompiler":
        report.summary = "screenplay.compiler has no compile(): the stand-in compiler"
        report.fallback = FALLBACKS["compiler"]
        report.problems.append(("warning", "running on FakeCompiler, not screenplay.compiler"))
    else:
        report.summary = "the real screenplay compiler (local, no keys)"
    return report


def _parsed_like(raw: str, default: Any) -> Any:
    """`raw` parsed as the field's type (int or float), or None."""
    try:
        return type(default)(raw.strip())
    except ValueError:
        return None


def _limits_report(env: Mapping[str, str], labels: Mapping[str, str]) -> ServiceReport:
    """Each STITCH_* limit: set or not, the value in force, and a warning for an invalid value.

    RunLimits decides validity; an invalid value is one it replaced with the default.
    """
    limits, defaults = RunLimits.from_env(env), RunLimits()
    report = ServiceReport("limits", labels["limits"])
    for name, attr in LIMIT_ENVS:
        raw, value, default = env.get(name), getattr(limits, attr), getattr(defaults, attr)
        if raw is None:
            report.vars.append(VarReport(name, False, f"unset (default: {default:g})"))
            continue
        var = VarReport(name, True, f"{value:g}")
        if _parsed_like(raw, default) != value:
            var.shown = f"set ({len(raw)} chars), using the default {default:g}"
            var.problem = f"{_cut(raw, 40)!r} is not valid; RunLimits keeps the default {default:g}"
            var.severity = "warning"
        report.vars.append(var)
    report.summary = (
        f"at most {limits.max_model_calls} model calls and {limits.max_images_per_run} image(s) a run; "
        f"{limits.run_timeout_s:g} s of running time per run; {limits.max_graph_steps} steps per studio graph "
        f"stream; {limits.model_timeout_s:g} s per Claude request with {limits.model_max_retries} retries"
    )
    report.notes.append(
        f"{guards.ENV_MAX_MODEL_CALLS} counts the studio's and every sub-agent's model calls together, across "
        "every answer and a restart; a run past it ends with an error event and the feed offers Tell it again"
    )
    report.notes.append(
        f"{guards.ENV_MAX_GRAPH_STEPS} bounds steps per studio graph stream only: each answer resumes with a fresh "
        f"step budget and sub-agents keep deepagents' own limit; the per-run caps are {guards.ENV_MAX_MODEL_CALLS} "
        f"and {guards.ENV_RUN_TIMEOUT_S}"
    )
    report.notes.append(
        f"{guards.ENV_RUN_TIMEOUT_S} counts only while a run is running, never while it waits on the teller; "
        "a run past it ends with an error event and the feed offers Tell it again"
    )
    return report


def build_report(
    env: Mapping[str, str] | None = None, *, seams: LiveSeams | None = None, live: bool = False
) -> list[ServiceReport]:
    """One ServiceReport per service and one for the limits; with live=True, each one's live check."""
    env = os.environ if env is None else env
    labels = modes(env)
    reports = [
        _agent_report(env),
        _model_report(env, labels),
        _vision_report(env, labels),
        _persistence_report(env, labels),
        _art_report(env, labels),
        _tts_report(env, labels),
        _renderer_report(env, labels),
        _compiler_report(labels),
        _limits_report(env, labels),
    ]
    if live:
        run_live(reports, env, seams or LiveSeams())
    return reports


# ------------------------------------------------------------------------------- live


class _HTTPFail(Exception):
    """A non-200 answer; its message is the provider's status and body."""


def _error_text(exc: BaseException, secrets: Sequence[str]) -> str:
    """A failed call as the report shows it: the provider's own error, scrubbed, then cut."""
    name = type(exc).__name__
    if isinstance(exc, _HTTPFail):
        text = str(exc)
    elif isinstance(exc, (httpx.TimeoutException, TimeoutError)) or "Timeout" in name:
        text = f"timed out ({name}: {exc})"
    else:
        text = f"{name}: {exc}"
    return _detail(text, secrets)


def _default_anthropic(api_key: str, timeout_s: float) -> Any:
    import anthropic

    return anthropic.Anthropic(api_key=api_key, timeout=timeout_s, max_retries=0)


def _default_mongo(uri: str, timeout_ms: int) -> Any:
    from pymongo import MongoClient

    return MongoClient(
        uri, serverSelectionTimeoutMS=timeout_ms, connectTimeoutMS=timeout_ms, appname="stitch-preflight"
    )


def _combine(parts: list[tuple[bool, str]]) -> LiveResult:
    return LiveResult("pass" if all(ok for ok, _ in parts) else "fail", "; ".join(text for _, text in parts))


def _live_anthropic(by_name: dict[str, ServiceReport], env: Mapping[str, str], seams: LiveSeams, guarded: Any) -> None:
    key = _stripped(env, ANTHROPIC_KEY)
    if key is None:
        by_name["model"].live = by_name["vision"].live = LiveResult("skipped", "ANTHROPIC_API_KEY is not set")
        return
    made, client = guarded(lambda: (seams.anthropic_factory or _default_anthropic)(key, seams.timeout_s))
    if not made:
        by_name["model"].live = by_name["vision"].live = LiveResult("fail", client)
        return
    studio_model, sub_model, vision_model = _studio_model(env), _stripped(env, SUBAGENT_MODEL_ENV), _vision_model(env)
    retrieved: dict[str, tuple[bool, str]] = {}

    def retrieve(model_id: str) -> tuple[bool, str]:
        if model_id not in retrieved:
            ok, text = guarded(lambda: (client.models.retrieve(model_id), "ok")[1])
            retrieved[model_id] = (ok, f"models.retrieve {model_id}: {text}")
        return retrieved[model_id]

    def ping() -> str:
        response = client.messages.create(
            model=studio_model, max_tokens=1, messages=[{"role": "user", "content": "ping"}]
        )
        stop = getattr(response, "stop_reason", None)
        return f"ok (stop_reason {stop})" if stop else "ok"

    def messages_check() -> tuple[bool, str]:
        ok, text = guarded(ping)
        return ok, f"messages.create {studio_model} max_tokens=1: {text}"

    steps: list[Callable[[], tuple[bool, str]]] = [lambda: retrieve(studio_model)]
    if sub_model and sub_model != studio_model:
        steps.append(lambda: retrieve(sub_model))
    steps.append(messages_check)
    parts: list[tuple[bool, str]] = []
    for step in steps:
        parts.append(step())
        if not parts[-1][0]:
            break  # one failure says it (a bad key fails every call the same way)
    by_name["model"].live = _combine(parts)
    by_name["vision"].live = _combine([retrieve(vision_model)])


def art_label_reason(env: Mapping[str, str]) -> str:
    """Why art is placeholder, in a short clause."""
    choice = image_provider.choose_image_provider(env)
    if choice.provider == "higgsfield":
        return hf_adapter.config_problem(env) or choice.reason
    return choice.reason


def _live_higgsfield(http: httpx.Client, env: Mapping[str, str], guarded: Any) -> LiveResult:
    """POST /estimate/{model}: proves the key and the model with no generation (the price doc's
    estimate endpoint answers {"usd": ...} for the same body a generation would send)."""
    creds = hf_adapter.credentials(env)
    model = hf_adapter.configured_model(env)
    if creds is None or model not in hf_adapter.MODELS:
        return LiveResult("skipped", "fix the Higgsfield errors above first")
    base = hf_adapter.configured_base_url(env)

    def estimate() -> str:
        response = http.post(
            f"{base}/estimate/{model}", json=hf_adapter.estimate_body(model), headers=hf_adapter.authorization(*creds)
        )
        if response.status_code != 200:
            try:
                body = response.text
            except Exception:  # noqa: BLE001 - a body that cannot be read is just not shown
                body = ""
            raise _HTTPFail(f"HTTP {response.status_code}: {body}")
        try:
            usd = response.json().get("usd")
        except Exception:  # noqa: BLE001
            usd = None
        price = f", about ${_cut(str(usd), 20)} per image" if usd is not None else ""
        return f"200{price} (an estimate: no image generated)"

    ok, text = guarded(estimate)
    return _combine([(ok, f"POST /estimate/{model}: {text}")])


def _live_http(by_name: dict[str, ServiceReport], env: Mapping[str, str], seams: LiveSeams, guarded: Any) -> None:
    def get(http: httpx.Client, url: str, headers: dict[str, str]) -> httpx.Response:
        response = http.get(url, headers=headers)
        if response.status_code != 200:
            try:
                body = response.text
            except Exception:  # noqa: BLE001 - a body that cannot be read is just not shown
                body = ""
            raise _HTTPFail(f"HTTP {response.status_code}: {body}")
        return response

    with httpx.Client(transport=seams.http_transport, timeout=seams.timeout_s, follow_redirects=True) as http:
        art_key = _stripped(env, images_adapter.API_KEY_ENV)
        art_mode = by_name["art"].mode
        if art_mode == "higgsfield":
            by_name["art"].live = _live_higgsfield(http, env, guarded)
        elif art_mode != "openai" or art_key is None:
            by_name["art"].live = LiveResult("skipped", f"placeholder art ({art_label_reason(env)})")
        else:
            base = _image_url(env)
            model = _image_model(env)
            ok, text = guarded(
                lambda: (get(http, f"{base}/models/{model}", {"Authorization": f"Bearer {art_key}"}), "200")[1]
            )
            by_name["art"].live = _combine([(ok, f"GET /models/{model}: {text}")])

        tts_key = _stripped(env, tts_adapter.API_KEY_ENV)
        if tts_key is None:
            by_name["tts"].live = LiveResult("skipped", "silent narration (ELEVENLABS_API_KEY is not set)")
        else:
            ids = [env.get(tts_adapter.DEFAULT_VOICE_ENV) or tts_adapter.DEFAULT_VOICE_ID]
            with contextlib.suppress(ValueError):  # already reported as a format error
                ids += list(tts_adapter.parse_voices(env.get(tts_adapter.VOICES_ENV)).values())

            def voice(voice_id: str) -> str:
                response = get(http, f"{tts_adapter.BASE_URL}/v1/voices/{voice_id}", {"xi-api-key": tts_key})
                try:
                    name = response.json().get("name")
                except Exception:  # noqa: BLE001
                    name = None
                return f"200 ({_cut(str(name), 40)})" if name else "200"

            parts = []
            for voice_id in dict.fromkeys(ids):
                ok, text = guarded(lambda voice_id=voice_id: voice(voice_id))
                parts.append((ok, f"GET /v1/voices/{voice_id}: {text}"))
            by_name["tts"].live = _combine(parts)


def _live_render(report: ServiceReport, env: Mapping[str, str], seams: LiveSeams, guarded: Any) -> None:
    web = _stripped(env, WEB_URL_ENV)
    if web is None:
        report.live = LiveResult("skipped", "diagram frames (STITCH_WEB_URL is not set)")
        return
    if any(v.severity == "error" for v in report.vars):
        report.live = LiveResult("skipped", "fix the STITCH_WEB_URL format error first")
        return
    from agent.tools.frames import probe_render_page

    ok, state = guarded(
        lambda: probe_render_page(web.rstrip("/"), timeout_ms=seams.render_timeout_ms, page_factory=seams.page_factory)
    )
    if not ok:
        report.live = LiveResult("fail", f"render page probe: {state}")
        return
    status, meaning = RENDER_STATES.get(state, ("fail", f"unknown probe answer {state!r}"))
    report.live = LiveResult(status, f"render page {redact_url(web.rstrip('/'))}: {state}: {meaning}")


def _live_mongo(report: ServiceReport, env: Mapping[str, str], seams: LiveSeams, guarded: Any) -> None:
    if fake_db(env):
        report.live = LiveResult("skipped", "in-memory database (STITCH_FAKE_DB is on)")
        return
    if any(v.name == "MONGODB_URI" and v.severity == "error" for v in report.vars):
        # pymongo's error for a malformed URI can quote part of the password: never ping with it.
        report.live = LiveResult("skipped", "fix the MONGODB_URI format error first")
        return
    uri = _stripped(env, "MONGODB_URI")
    target = uri or DEFAULT_MONGODB_URI
    label = redact_mongo_uri(target) + ("" if uri else " (db.client's default)")

    def ping() -> str:
        client = (seams.mongo_factory or _default_mongo)(target, MONGO_TIMEOUT_MS)
        try:
            client.admin.command("ping")
        finally:
            close = getattr(client, "close", None)
            if callable(close):
                close()
        return "ok"

    ok, text = guarded(ping)
    report.live = _combine([(ok, f"ping {label}: {text}")])


def run_live(reports: list[ServiceReport], env: Mapping[str, str], seams: LiveSeams) -> None:
    """Fill each report's `live` with one minimal call per configured service."""
    secrets = secret_values(env)
    by_name = {r.service: r for r in reports}

    def guarded(fn: Callable[[], Any]) -> tuple[bool, Any]:
        try:
            return True, fn()
        except Exception as exc:  # noqa: BLE001 - every provider failure is a reported fail
            return False, _error_text(exc, secrets)

    with scrubbed_logs(secrets):
        _live_anthropic(by_name, env, seams, guarded)
        _live_http(by_name, env, seams, guarded)
        _live_render(by_name["renderer"], env, seams, guarded)
        _live_mongo(by_name["persistence"], env, seams, guarded)
    by_name["agent"].live = LiveResult("skipped", "no call (see model)")
    by_name["compiler"].live = LiveResult("skipped", "local, no call")
    by_name["limits"].live = LiveResult("skipped", "local, no call")


# ----------------------------------------------------------------------------- output


def exit_code(reports: Sequence[ServiceReport]) -> int:
    failed = any(r.errors() for r in reports) or any(r.live.status == "fail" for r in reports)
    return 1 if failed else 0


def expected_log_line(reports: Sequence[ServiceReport], env: Mapping[str, str]) -> str | None:
    """The 'studio services: ...' line agent.production will log, or None when it cannot build."""
    by_name = {r.service: r for r in reports}
    if by_name["model"].mode != "real":
        return None
    from agent.production import LABEL_ORDER

    labels = modes(env, compiler=by_name["compiler"].mode)
    ordered = [k for k in LABEL_ORDER if k in labels] + sorted(k for k in labels if k not in LABEL_ORDER)
    return "studio services: " + " ".join(f"{k}={labels[k]}" for k in ordered)


def render_text(reports: Sequence[ServiceReport], *, env: Mapping[str, str], env_files: str, live: bool) -> str:
    lines = ["Stitch studio preflight" + (" (--live)" if live else ""), env_files, ""]
    width = max(len(name) for name in ENV_NAMES) + 2
    for r in reports:
        lines.append(f"[{r.service}]  mode: {r.mode}")
        if r.summary:
            lines.append(f"  {r.summary}")
        for v in r.vars:
            lines.append(f"  {v.name:<{width}}{v.shown}")
            if v.problem:
                lines.append(f"  {'':<{width}}{'ERROR' if v.severity == 'error' else 'warning'}: {v.problem}")
        lines += [f"  {'ERROR' if sev == 'error' else 'warning'}: {text}" for sev, text in r.problems]
        lines += [f"  note: {note}" for note in r.notes]
        if r.fallback:
            lines.append(f"  fallback: {r.fallback}")
        if r.live.status != "not_run":
            lines.append(f"  live: {r.live.status}" + (f": {r.live.detail}" if r.live.detail else ""))
        lines.append("")
    if log_line := expected_log_line(reports, env):
        lines += ["The API will log, when the studio is built:", f"  {log_line}", ""]
    errors = [f"{r.service}: {e}" for r in reports for e in r.errors()]
    errors += [f"{r.service}: live check failed" for r in reports if r.live.status == "fail"]
    warnings = [f"{r.service}: {w}" for r in reports for w in r.warnings()]
    warnings += [f"{r.service}: live check warned" for r in reports if r.live.status == "warn"]
    code = exit_code(reports)
    lines.append(f"result: {len(errors)} error(s), {len(warnings)} warning(s); exit {code}")
    lines += [f"  ERROR {e}" for e in errors]
    if not live and code == 0:
        lines.append("  next: uv run python -m agent.preflight --live")
    return "\n".join(lines) + "\n"


def describe_env_files(loaded: Sequence[Path] | None, enabled: bool) -> str:
    if not enabled:
        return "env files: not loaded (STITCH_NO_DOTENV=1 or under pytest); only the shell environment is used"
    if loaded:
        return "env files: loaded " + ", ".join(str(p) for p in loaded) + " (variables already set in the shell win)"
    return "env files: none found (looked for " + ", ".join(str(p) for p in ENV_FILES) + ")"


def main(
    argv: Sequence[str] | None = None,
    *,
    environ: MutableMapping[str, str] | None = None,
    out: TextIO | None = None,
    seams: LiveSeams | None = None,
) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m agent.preflight",
        description="Check the studio's environment before a real run: keys, modes, fallbacks and limits.",
    )
    parser.add_argument("--live", action="store_true", help="make one minimal call per configured service")
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:  # bad arguments exit 2, --help exits 0
        return int(exc.code) if isinstance(exc.code, int) else 2
    out = out or sys.stdout
    env = os.environ if environ is None else environ

    enabled = dotenv_enabled(env)
    loaded = load_env_files(ENV_FILES) if enabled and env is os.environ else []
    secrets = secret_values(env)
    with scrubbed_logs(secrets):
        try:
            reports = build_report(env, seams=seams, live=args.live)
            if args.json:
                text = json.dumps(_scrub_tree([r.as_json() for r in reports], secrets), indent=2) + "\n"
            else:
                text = render_text(reports, env=env, env_files=describe_env_files(loaded, enabled), live=args.live)
            code = exit_code(reports)
        except Exception as exc:  # noqa: BLE001 - a crash must not print a secret in its traceback
            text, code = f"preflight failed: {_detail(f'{type(exc).__name__}: {exc}', secrets)}\n", 1
    out.write(scrub(text, secrets))
    out.flush()
    return code


__all__ = [
    "DETAIL_CHARS",
    "ENV_FILES",
    "ENV_NAMES",
    "LIMIT_ENVS",
    "RENDER_STATES",
    "LiveResult",
    "LiveSeams",
    "ServiceReport",
    "VarReport",
    "agent_mode",
    "build_report",
    "exit_code",
    "expected_log_line",
    "load_env_files",
    "main",
    "modes",
    "mongo_uri_problem",
    "redact_mongo_uri",
    "redact_url",
    "run_live",
    "scrub",
    "scrubbed_logs",
    "secret_values",
    "shape_problem",
]


if __name__ == "__main__":
    sys.exit(main())
