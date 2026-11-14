"""The production run manager: real services chosen from the environment.

`agent.runs.get_manager()` calls `production_manager()` the first time a run route needs
the agent, so the API needs no startup hook. Each service is picked from env vars:

    ANTHROPIC_API_KEY      required: the studio model (studio.make_model, STITCH_MODEL)
                           and Claude vision for describe_photo and inspect_frames; unset:
                           RuntimeError (no model, no studio)
    STITCH_SUBAGENT_MODEL  optional model id for the eight sub-agents
    MONGODB_URI            checkpoints, /memories/ and the run records on MongoDB
                           (studio.mongo_persistence), so runs survive a restart; unset, or
                           STITCH_FAKE_DB truthy: in memory, and runs are lost on restart
    IMAGE_API_KEY          real prop and set art; unset: placeholder art (null art + color)
    ELEVENLABS_API_KEY     real narration voices; unset: FakeTTS (silent audio, real timings)
    STITCH_WEB_URL         render_frames screenshots the web /render/[memoryId] page with
                           Playwright; unset: the Pillow diagram renderer (FakeFrameRenderer),
                           whose labelled diagrams the critic is told are not the real stage
                           (a warning is logged)

Fallbacks: a missing or failing service degrades a run, never fails it or stops the API
(tests/agent/test_agent_fallbacks.py drives each case end to end):

    MongoDB fails          MONGODB_URI is set but studio.mongo_persistence raises (no server,
                           bad credentials, bad URI): an error names the exception type only
                           (never the URI or password), the in-memory warning is logged once,
                           and persistence is InMemorySaver/InMemoryStore (label "memory")
    image API fails        the prop or set layer gets placeholder art (null art + color);
                           STITCH_MAX_IMAGES_PER_RUN=0 does the same without calling the model
    cutout fails           placeholder art for props and the middle and front layers (the back
                           layer is never cut out); cut_out answers "use a placeholder"
    speech API fails       the voice pass retries once, then narrates silently with estimated
                           word timings and a "warning:" line (agent.tools.narration)
    photo vision fails     describe_photo answers "could not describe photo ..."; the run goes on
    frame critic fails     inspect_frames answers "could not inspect frames ..."; the checker
                           passes without a critique
    render page fails      PlaywrightFrameRenderer draws labelled diagrams instead

Every real adapter has a bounded request timeout and retry count (agent/adapters), so a hung
provider cannot hold a run past STITCH_RUN_TIMEOUT_S.

Time and cost limits (agent.guards.RunLimits; a bad value warns and keeps the default):

    STITCH_MAX_IMAGES_PER_RUN  image generations per run (24); 0: placeholder art only
    STITCH_MAX_GRAPH_STEPS     graph steps per run (1000, at least 50; studio.thread_config)
    STITCH_MODEL_TIMEOUT_S     seconds per Claude request (120; studio.make_model)
    STITCH_MODEL_MAX_RETRIES   retries of a failed Claude request (2; studio.make_model)
    STITCH_RUN_TIMEOUT_S       seconds per run (1200; enforced by the run manager)

The "limits" label shows the limits in force, e.g. "images=24 steps=1000 model_timeout=120s
model_retries=2".

The family data goes through `MongoRepo(db.client.get_db())`, so it follows whatever
database the API uses, including the STITCH_FAKE_DB mongomock override.

Building makes no network call to the model, image, speech or vision services (their
clients connect on first use). The one exception is MongoDB persistence when
MONGODB_URI is set: langgraph's MongoDBSaver and MongoDBStore check their indexes when
they are created, so that step talks to MongoDB once, bounded by a short server
selection timeout.
"""

from __future__ import annotations

import logging
import os
import threading
from urllib.parse import parse_qsl, urlsplit, urlunsplit

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.base import BaseStore
from langgraph.store.memory import InMemoryStore
from langgraph.types import Checkpointer

from agent import studio
from agent.adapters import AlphaAwareRemover, AnthropicVision, ElevenLabsTTS, OpenAIImageModel
from agent.guards import RunLimits
from agent.mongo_repo import MongoRepo
from agent.runs import RunManager
from agent.tools.art import ArtService
from agent.tools.compile import load_compiler
from agent.tools.frames import FakeFrameRenderer, FrameRenderer, PlaywrightFrameRenderer
from agent.tools.narration import FakeTTS
from agent.wiring import Services, compiler_label, make_manager
from db import client as db_client

logger = logging.getLogger(__name__)

_TRUTHY = frozenset({"1", "true", "yes"})

SERVER_SELECTION_TIMEOUT_MS = 5000
"""How long building the MongoDB checkpointer may wait for an unreachable server."""

LABEL_ORDER: tuple[str, ...] = (
    "model",
    "subagent_model",
    "repo",
    "persistence",
    "art",
    "tts",
    "vision",
    "critic",
    "renderer",
    "compiler",
    "limits",
)


_memory_warning_lock = threading.Lock()
_memory_warned = False


def _warn_memory_persistence(reason: str) -> None:
    """Warn, once per process, that in-memory persistence loses runs on a restart."""
    global _memory_warned  # noqa: PLW0603
    with _memory_warning_lock:
        if _memory_warned:
            return
        _memory_warned = True
    logger.warning(
        "agent persistence is in memory (%s): runs, their checkpoints and /memories/ notes "
        "do not survive an API restart; set MONGODB_URI to keep them",
        reason,
    )


def _env(name: str) -> str | None:
    value = os.environ.get(name)
    return value.strip() if value and value.strip() else None


def _fake_db() -> bool:
    return (os.environ.get("STITCH_FAKE_DB") or "").strip().lower() in _TRUTHY


def with_server_timeout(uri: str, timeout_ms: int = SERVER_SELECTION_TIMEOUT_MS) -> str:
    """`uri` with serverSelectionTimeoutMS set, unless it already sets one."""
    parts = urlsplit(uri)
    if any(key.lower() == "serverselectiontimeoutms" for key, _ in parse_qsl(parts.query, keep_blank_values=True)):
        return uri
    option = f"serverSelectionTimeoutMS={int(timeout_ms)}"
    # Append without re-encoding the existing options; pymongo needs a "/" before "?".
    query = f"{parts.query}&{option}" if parts.query else option
    return urlunsplit(parts._replace(path=parts.path or "/", query=query))


def _persistence() -> tuple[Checkpointer, BaseStore, str]:
    """(checkpointer, store, persistence label): MongoDB when MONGODB_URI is set and reachable.

    A MongoDB that cannot be reached or refuses the credentials does not stop the API: one
    error is logged naming only the cause's exception type (never the URI, which carries the
    password, nor the driver's message, which can echo hosts and user names), and runs fall
    back to in memory with the once-per-process warning that they do not survive a restart.
    """
    uri = _env("MONGODB_URI")
    if not uri or _fake_db():
        _warn_memory_persistence("STITCH_FAKE_DB is set" if _fake_db() else "MONGODB_URI is not set")
        return InMemorySaver(), InMemoryStore(), "memory"
    try:
        checkpointer, store = studio.mongo_persistence(with_server_timeout(uri), db_name=db_client.MONGODB_DB)
    except Exception as exc:  # noqa: BLE001 - unreachable, auth failure, bad URI: degrade, never stop the API
        cause = type(exc).__name__
        logger.error(
            "agent persistence: MongoDB at MONGODB_URI could not be used (%s); falling back to in-memory "
            "persistence. Check that the server is reachable from the API and the credentials are right.",
            cause,
        )
        _warn_memory_persistence(f"MongoDB at MONGODB_URI failed with {cause}")
        return InMemorySaver(), InMemoryStore(), "memory"
    return checkpointer, store, "mongo"


def production_services() -> Services:
    """The real services, chosen from os.environ (see the module docstring).

    Raises RuntimeError when ANTHROPIC_API_KEY is not set: without a model there is no studio.
    """
    if not _env("ANTHROPIC_API_KEY"):
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set; the studio needs a Claude model to run "
            "(set it in the API's environment and retry)"
        )
    labels: dict[str, str] = {}
    limits = RunLimits.from_env()

    model = studio.make_model()
    labels["model"] = "real"
    subagent_model = None
    subagent_id = _env("STITCH_SUBAGENT_MODEL")
    if subagent_id:
        subagent_model = studio.make_model(subagent_id)
        labels["subagent_model"] = subagent_id
    else:
        labels["subagent_model"] = "studio"

    repo = MongoRepo(db_client.get_db())
    labels["repo"] = "mongo"

    checkpointer, store, labels["persistence"] = _persistence()

    remover = AlphaAwareRemover()
    if _env("IMAGE_API_KEY"):
        art = ArtService(OpenAIImageModel(), remover, max_images_per_run=limits.max_images_per_run)
        labels["art"] = "real"
    else:
        art = ArtService(None, remover)  # placeholder art; cut_out of uploaded photos still works
        labels["art"] = "placeholder"

    if _env("ELEVENLABS_API_KEY"):
        tts = ElevenLabsTTS()
        labels["tts"] = "real"
    else:
        # Silent audio with real word timings: families hear silence, not a test hum.
        tts = FakeTTS(tone=False)
        labels["tts"] = "fake"

    vision = AnthropicVision()
    labels["vision"] = "real"
    labels["critic"] = "real"

    web_url = _env("STITCH_WEB_URL")
    renderer: FrameRenderer
    if web_url:
        renderer = PlaywrightFrameRenderer(web_url)
        labels["renderer"] = "playwright"
    else:
        # No web app to screenshot: the checker sees Pillow diagrams of the stage, and the
        # renderer's critic_note tells the vision critic so.
        renderer = FakeFrameRenderer()
        labels["renderer"] = "diagram"
        logger.warning(
            "STITCH_WEB_URL is not set: the checker sees labelled diagrams of the stage, not "
            "the real stage; set STITCH_WEB_URL to the running web app (e.g. "
            "http://localhost:3000) for real screenshots"
        )

    compiler = load_compiler()
    labels["compiler"] = compiler_label(compiler)
    labels["limits"] = limits.label()

    return Services(
        repo=repo,
        model=model,
        checkpointer=checkpointer,
        store=store,
        art=art,
        tts=tts,
        vision=vision,
        critic=vision,
        renderer=renderer,
        compiler=compiler,
        subagent_model=subagent_model,
        labels=labels,
    )


def describe(services: Services) -> str:
    """One log line naming each service choice, e.g. 'studio services: model=real ... renderer=diagram'."""
    labels = dict(services.labels)
    ordered = [key for key in LABEL_ORDER if key in labels]
    ordered += sorted(key for key in labels if key not in LABEL_ORDER)
    return "studio services: " + " ".join(f"{key}={labels[key]}" for key in ordered)


def production_manager(*, max_workers: int = 4) -> RunManager:
    """A RunManager over `production_services()`; logs which services it chose."""
    services = production_services()
    logger.info(describe(services))
    return make_manager(services, max_workers=max_workers)


__all__ = ["describe", "production_manager", "production_services", "with_server_timeout"]
