"""The production run manager: real services chosen from the environment.

`agent.runs.get_manager()` calls `production_manager()` the first time a run route needs
the agent, so the API needs no startup hook. Each service is picked from env vars:

    ANTHROPIC_API_KEY      required: the studio model (studio.make_model, STITCH_MODEL)
                           and Claude vision for describe_photo and inspect_frames
    STITCH_SUBAGENT_MODEL  optional model id for the eight sub-agents
    MONGODB_URI            checkpoints and /memories/ on MongoDB (studio.mongo_persistence);
                           unset, or STITCH_FAKE_DB truthy: in-memory, lost on restart
    IMAGE_API_KEY          real prop and set art; unset: placeholder art (null art + color)
    ELEVENLABS_API_KEY     real narration voices; unset: FakeTTS (silent audio, real timings)
    STITCH_WEB_URL         render_frames screenshots the web /render/[memoryId] page with
                           Playwright; unset: the Pillow diagram renderer

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
from urllib.parse import parse_qsl, urlsplit, urlunsplit

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore

from agent import studio
from agent.adapters import AlphaAwareRemover, AnthropicVision, ElevenLabsTTS, OpenAIImageModel
from agent.mongo_repo import MongoRepo
from agent.runs import RunManager
from agent.tools.art import ArtService
from agent.tools.compile import load_compiler
from agent.tools.narration import FakeTTS
from agent.wiring import Services, compiler_label, frames_attr, make_manager
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

    uri = _env("MONGODB_URI")
    if uri and not _fake_db():
        checkpointer, store = studio.mongo_persistence(with_server_timeout(uri), db_name=db_client.MONGODB_DB)
        labels["persistence"] = "mongo"
    else:
        logger.warning(
            "agent persistence is in memory (%s): paused runs and /memories/ notes are lost on restart",
            "STITCH_FAKE_DB is set" if _fake_db() else "MONGODB_URI is not set",
        )
        checkpointer, store = InMemorySaver(), InMemoryStore()
        labels["persistence"] = "memory"

    remover = AlphaAwareRemover()
    if _env("IMAGE_API_KEY"):
        art = ArtService(OpenAIImageModel(), remover)
        labels["art"] = "real"
    else:
        art = ArtService(None, remover)  # placeholder art; cut_out of uploaded photos still works
        labels["art"] = "placeholder"

    if _env("ELEVENLABS_API_KEY"):
        tts = ElevenLabsTTS()
        labels["tts"] = "real"
    else:
        tts = FakeTTS()
        labels["tts"] = "fake"

    vision = AnthropicVision()
    labels["vision"] = "real"
    labels["critic"] = "real"

    web_url = _env("STITCH_WEB_URL")
    playwright_cls = frames_attr("PlaywrightFrameRenderer")
    diagram_cls = frames_attr("FakeFrameRenderer")
    if web_url and playwright_cls is not None:
        renderer = playwright_cls(web_url)
        labels["renderer"] = "playwright"
    elif diagram_cls is not None:
        # The web /render page is not ready yet, so the checker sees Pillow diagrams.
        renderer = diagram_cls()
        labels["renderer"] = "diagram"
    else:
        renderer = None
        labels["renderer"] = "missing"

    compiler = load_compiler()
    labels["compiler"] = compiler_label(compiler)

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
