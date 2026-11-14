"""agent/production.py and the lazy production default in runs.get_manager.

Never builds anything that talks to a real service: dummy keys only, mongomock for the
family data, and sockets blocked while the services are built.
"""

from __future__ import annotations

import socket
from typing import Any

import mongomock
import pytest

import schemas
from agent import production, runs, studio, wiring
from agent.adapters import AlphaAwareRemover, AnthropicVision, ElevenLabsTTS, OpenAIImageModel
from agent.mongo_repo import MongoRepo
from agent.run_info import RunInfo
from agent.tools.narration import FakeTTS
from db import client as db_client

ENV_VARS = (
    "ANTHROPIC_API_KEY",
    "IMAGE_API_KEY",
    "ELEVENLABS_API_KEY",
    "MONGODB_URI",
    "STITCH_FAKE_DB",
    "STITCH_WEB_URL",
    "STITCH_SUBAGENT_MODEL",
    "STITCH_MODEL",
)

needs_frames = pytest.mark.skipif(not wiring.frames_available(), reason="agent.tools.frames has no make_frame_tools yet")


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> Any:
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    db = mongomock.MongoClient(tz_aware=True)[db_client.MONGODB_DB]
    db_client.set_db(db)
    runs.configure(None)
    yield db
    runs.configure(None)
    db_client.set_db(None)


@pytest.fixture
def no_network(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """Fail any attempt to resolve a host or open a connection."""
    attempts: list[Any] = []

    def refuse(*args: Any, **_kwargs: Any) -> Any:
        attempts.append(args)
        raise AssertionError(f"network call while building services: {args!r}")

    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)
    return attempts


def dummy_keys(monkeypatch: pytest.MonkeyPatch, *, art: bool = True, tts: bool = True) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-dummy")
    monkeypatch.setenv("STITCH_FAKE_DB", "1")
    if art:
        monkeypatch.setenv("IMAGE_API_KEY", "img-dummy")
    if tts:
        monkeypatch.setenv("ELEVENLABS_API_KEY", "el-dummy")


def run_info(family_id: str = "fam_prod") -> RunInfo:
    return RunInfo(
        run_id="run_prod",
        family_id=family_id,
        user_id="user_1",
        author_id="dad",
        fields=schemas.SubmitMemoryFields(text="Dad passed the hymn books down the pew.", year=1988),
    )


# ------------------------------------------------------------------ production_services


def test_production_services_needs_the_anthropic_key() -> None:
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        production.production_services()


def test_blank_anthropic_key_counts_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "  ")
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        production.production_services()


def test_production_services_with_every_key_and_the_fake_db(
    monkeypatch: pytest.MonkeyPatch, clean_env: Any, no_network: list[Any]
) -> None:
    dummy_keys(monkeypatch)
    svc = production.production_services()

    assert svc.labels["model"] == "real"
    assert svc.labels["subagent_model"] == "studio" and svc.subagent_model is None
    assert svc.labels["repo"] == "mongo"
    assert isinstance(svc.repo, MongoRepo) and svc.repo.db is clean_env
    assert svc.labels["persistence"] == "memory"
    assert svc.labels["art"] == "real"
    assert isinstance(svc.art.image_model, OpenAIImageModel)
    assert isinstance(svc.art.remover, AlphaAwareRemover)
    assert svc.labels["tts"] == "real" and isinstance(svc.tts, ElevenLabsTTS)
    assert svc.labels["vision"] == "real" and isinstance(svc.vision, AnthropicVision)
    assert svc.critic is svc.vision
    assert svc.labels["compiler"] in {"FakeCompiler", "screenplay.compiler"}
    if wiring.frames_available():
        assert svc.labels["renderer"] == "diagram"
        assert type(svc.renderer).__name__ == "FakeFrameRenderer"
    else:
        assert svc.labels["renderer"] == "missing" and svc.renderer is None

    graph = wiring.build_run_graph(run_info(), svc)
    assert graph is not None
    assert no_network == []


def test_without_art_and_speech_keys_they_are_placeholder_and_fake(
    monkeypatch: pytest.MonkeyPatch, no_network: list[Any]
) -> None:
    dummy_keys(monkeypatch, art=False, tts=False)
    svc = production.production_services()
    assert svc.labels["art"] == "placeholder"
    assert svc.art.image_model is None
    assert isinstance(svc.art.remover, AlphaAwareRemover)  # cut_out of uploaded photos still works
    assert svc.labels["tts"] == "fake" and isinstance(svc.tts, FakeTTS)
    assert no_network == []


def test_mongodb_uri_uses_mongo_persistence(monkeypatch: pytest.MonkeyPatch) -> None:
    dummy_keys(monkeypatch)
    monkeypatch.delenv("STITCH_FAKE_DB")
    monkeypatch.setenv("MONGODB_URI", "mongodb://db.example:27017/?retryWrites=true")
    calls: list[tuple[str, str]] = []
    sentinel = (object(), object())

    def fake_persistence(uri: str | None = None, db_name: str = "stitch") -> Any:
        calls.append((uri, db_name))
        return sentinel

    monkeypatch.setattr(studio, "mongo_persistence", fake_persistence)
    svc = production.production_services()
    assert svc.labels["persistence"] == "mongo"
    assert (svc.checkpointer, svc.store) == sentinel
    assert calls == [("mongodb://db.example:27017/?retryWrites=true&serverSelectionTimeoutMS=5000", db_client.MONGODB_DB)]


@pytest.mark.parametrize("flag", ["1", "true", "YES"])
def test_fake_db_keeps_persistence_in_memory_even_with_a_uri(monkeypatch: pytest.MonkeyPatch, flag: str) -> None:
    dummy_keys(monkeypatch)
    monkeypatch.setenv("STITCH_FAKE_DB", flag)
    monkeypatch.setenv("MONGODB_URI", "mongodb://db.example:27017")
    monkeypatch.setattr(studio, "mongo_persistence", lambda *a, **k: pytest.fail("mongo persistence under STITCH_FAKE_DB"))
    assert production.production_services().labels["persistence"] == "memory"


def test_subagent_model_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    dummy_keys(monkeypatch)
    monkeypatch.setenv("STITCH_SUBAGENT_MODEL", "claude-sonnet-test")
    asked: list[str | None] = []
    real_make = studio.make_model

    def spy(model: str | None = None) -> Any:
        asked.append(model)
        return real_make(model)

    monkeypatch.setattr(studio, "make_model", spy)
    svc = production.production_services()
    assert asked == [None, "claude-sonnet-test"]
    assert svc.subagent_model is not None and svc.subagent_model is not svc.model
    assert svc.labels["subagent_model"] == "claude-sonnet-test"


@needs_frames
def test_web_url_selects_the_playwright_renderer(monkeypatch: pytest.MonkeyPatch, no_network: list[Any]) -> None:
    dummy_keys(monkeypatch)
    monkeypatch.setenv("STITCH_WEB_URL", "http://localhost:3999/")
    svc = production.production_services()
    assert svc.labels["renderer"] == "playwright"
    assert type(svc.renderer).__name__ == "PlaywrightFrameRenderer"
    assert svc.renderer.base_url == "http://localhost:3999"
    assert no_network == []


def test_describe_is_one_line_in_a_stable_order(monkeypatch: pytest.MonkeyPatch) -> None:
    dummy_keys(monkeypatch, art=False)
    line = production.describe(production.production_services())
    assert "\n" not in line
    assert line.startswith("studio services: model=real subagent_model=studio repo=mongo persistence=memory ")
    assert "art=placeholder tts=real" in line
    assert "sk-ant" not in line and "dummy" not in line


@pytest.mark.parametrize(
    ("uri", "expected"),
    [
        ("mongodb://localhost:27017", "mongodb://localhost:27017/?serverSelectionTimeoutMS=5000"),
        ("mongodb+srv://u:p@cluster.example/stitch?retryWrites=true&w=majority",
         "mongodb+srv://u:p@cluster.example/stitch?retryWrites=true&w=majority&serverSelectionTimeoutMS=5000"),
        ("mongodb://a:1,b:2/?serverselectiontimeoutms=100", "mongodb://a:1,b:2/?serverselectiontimeoutms=100"),
    ],
)
def test_with_server_timeout(uri: str, expected: str) -> None:
    assert production.with_server_timeout(uri) == expected


def test_production_manager_logs_its_services(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    dummy_keys(monkeypatch)
    caplog.set_level("INFO", logger="agent.production")
    manager = production.production_manager(max_workers=1)
    try:
        assert isinstance(manager, runs.RunManager)
        assert any(r.getMessage().startswith("studio services: ") for r in caplog.records)
    finally:
        manager.shutdown()


# ------------------------------------------------------------------ runs.get_manager


def test_get_manager_without_a_key_is_not_configured() -> None:
    with pytest.raises(RuntimeError) as info:
        runs.get_manager()
    message = str(info.value)
    assert message.startswith("agent runs are not configured")
    assert "ANTHROPIC_API_KEY" in message


def test_get_manager_builds_the_production_manager_once(monkeypatch: pytest.MonkeyPatch) -> None:
    built: list[runs.RunManager] = []

    def fake_production_manager(*, max_workers: int = 4) -> runs.RunManager:
        manager = runs.RunManager(repo=MongoRepo(db_client.get_db()), build_graph=lambda run: None, max_workers=1)
        built.append(manager)
        return manager

    monkeypatch.setattr(production, "production_manager", fake_production_manager)
    try:
        first = runs.get_manager()
        assert runs.get_manager() is first
        assert built == [first]

        runs.configure(None)  # resets: the next call builds again
        second = runs.get_manager()
        assert second is not first and built == [first, second]
    finally:
        for manager in built:
            manager.shutdown()


def test_get_manager_does_not_cache_a_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts: list[int] = []
    manager = runs.RunManager(repo=MongoRepo(db_client.get_db()), build_graph=lambda run: None, max_workers=1)

    def flaky(*, max_workers: int = 4) -> runs.RunManager:
        attempts.append(1)
        if len(attempts) == 1:
            raise ValueError("the database is waking up")
        return manager

    monkeypatch.setattr(production, "production_manager", flaky)
    try:
        with pytest.raises(RuntimeError, match=r"^agent runs are not configured: the database is waking up$"):
            runs.get_manager()
        assert runs.get_manager() is manager
        assert len(attempts) == 2
    finally:
        manager.shutdown()


def test_a_configured_manager_wins_over_production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(production, "production_manager", lambda **_: pytest.fail("built production"))
    manager = runs.RunManager(repo=MongoRepo(db_client.get_db()), build_graph=lambda run: None, max_workers=1)
    try:
        runs.configure(manager)
        assert runs.get_manager() is manager
    finally:
        manager.shutdown()
