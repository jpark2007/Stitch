"""STITCH_ART_LAYERS: "off" generates art for props only; set layers are placeholder layers.

Parsing (agent.guards.RunLimits), ArtService.make_layer, the set tool's note, how
agent.production builds the art service and the preflight [art] block. The whole-run cases
(props drawn, layers placeholder, the run completes) are in test_agent_fallbacks.py.
No keys, no network: the image model is the deterministic fake.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator

import mongomock
import pytest

import schemas
from agent import guards, preflight, production, runs
from agent.guards import RunLimits
from agent.repo import InMemoryRepo, load_sample_family
from agent.run_info import RunInfo
from agent.tools.art import LAYERS_OFF_NOTE, ArtService, FakeBackgroundRemover, FakeImageModel
from agent.tools.props import make_prop_tools
from agent.tools.sets import make_set_tools
from db import client as db_client

FAMILY = "fam_layers_switch"


@pytest.fixture(autouse=True)
def fresh_warnings() -> Iterator[None]:
    guards._warned.clear()
    yield
    guards._warned.clear()


def warnings_about(caplog: pytest.LogCaptureFixture, name: str) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelname == "WARNING" and name in r.getMessage()]


# ------------------------------------------------------------------------------ parsing


def test_unset_is_on() -> None:
    assert RunLimits().art_layers is True
    assert RunLimits.from_env({}).art_layers is True


@pytest.mark.parametrize(("raw", "expected"), [("on", True), ("ON", True), (" off ", False), ("Off", False)])
def test_on_and_off_in_any_case(raw: str, expected: bool, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING, logger="agent.guards")
    assert RunLimits.from_env({"STITCH_ART_LAYERS": raw}).art_layers is expected
    assert warnings_about(caplog, "STITCH_ART_LAYERS") == []


@pytest.mark.parametrize("raw", ["", "  ", "no", "false", "0", "props-only"])
def test_any_other_value_is_on_with_one_warning(raw: str, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING, logger="agent.guards")
    assert RunLimits.from_env({"STITCH_ART_LAYERS": raw}).art_layers is True
    assert RunLimits.from_env({"STITCH_ART_LAYERS": raw}).art_layers is True
    (message,) = warnings_about(caplog, "STITCH_ART_LAYERS")
    assert "'on' or 'off'" in message and "using the default on" in message


def test_the_switch_leaves_the_other_limits_and_the_label_alone() -> None:
    off = RunLimits.from_env({"STITCH_ART_LAYERS": "off"})
    assert off.label() == RunLimits().label()
    assert off.max_images_per_run == RunLimits().max_images_per_run


# --------------------------------------------------------------------------- ArtService


def test_make_layer_off_never_calls_the_model_nor_counts(caplog: pytest.LogCaptureFixture) -> None:
    model = FakeImageModel()
    art = ArtService(model, FakeBackgroundRemover(), max_images_per_run=2, layers_enabled=False)
    caplog.set_level(logging.INFO, logger="agent.tools.art")
    for layer in ("back", "middle", "front", "back"):
        assert art.make_layer("a hill", layer=layer, size=(64, 32), run_id="r1") is None  # type: ignore[arg-type]
    assert art.make_layer("a hill", layer="back", size=(64, 32), run_id="r2") is None
    assert model.calls == []
    assert art.images_used("r1") == 0 and art.images_left("r1") == 2
    # Props, parts and states still draw, and take the whole budget.
    assert art.make_cutout("a basket", size=(64, 64), run_id="r1") is not None
    assert art.make_cutout("a basket lid", size=(64, 64), run_id="r1") is not None
    assert len(model.calls) == 2 and art.images_left("r1") == 0
    records = [r for r in caplog.records if r.name == "agent.tools.art"]
    assert [r.levelname for r in records] == ["INFO", "INFO"]  # one line per run, no warning
    assert "r1" in records[0].getMessage() and "r2" in records[1].getMessage()


def test_make_layer_off_still_validates_the_layer_name() -> None:
    art = ArtService(FakeImageModel(), FakeBackgroundRemover(), layers_enabled=False)
    with pytest.raises(ValueError):
        art.make_layer("x", layer="side")  # type: ignore[arg-type]


def test_cut_out_of_a_photo_is_unaffected() -> None:
    import io

    from PIL import Image, ImageDraw

    im = Image.new("RGB", (64, 64), (255, 255, 255))
    ImageDraw.Draw(im).rectangle((16, 16, 48, 48), fill=(120, 40, 40))
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    art = ArtService(FakeImageModel(), FakeBackgroundRemover(), layers_enabled=False)
    assert art.cut_out(buf.getvalue()) is not None


def test_on_is_the_default_and_draws_layers() -> None:
    model = FakeImageModel()
    art = ArtService(model, FakeBackgroundRemover(), max_images_per_run=24)
    assert art.layers_enabled is True
    assert art.make_layer("a hill", layer="back", size=(64, 32), run_id="r") is not None
    assert len(model.calls) == 1 and art.images_used("r") == 1


# ---------------------------------------------------------------------------- the tools


def _run() -> RunInfo:
    return RunInfo(
        run_id="run_switch",
        family_id=FAMILY,
        user_id="user_1",
        author_id="dad",
        fields=schemas.SubmitMemoryFields(text="We ate on the lake dock.", year=1994),
    )


def test_generate_set_layer_off_saves_placeholders_and_says_it_is_final() -> None:
    repo = InMemoryRepo()
    load_sample_family(repo, FAMILY)
    model = FakeImageModel()
    art = ArtService(model, FakeBackgroundRemover(), max_images_per_run=24, layers_enabled=False)
    _, generate_set_layer = make_set_tools(repo, _run(), art)
    results = [
        json.loads(generate_set_layer.invoke({"place": "lake dock", "layer": layer, "description": "a wooden dock"}))
        for layer in ("back", "middle", "front")
    ]
    assert model.calls == []
    assert all(r["art"] == "placeholder" and r["note"] == LAYERS_OFF_NOTE for r in results)
    assert "final" in LAYERS_OFF_NOTE and "Do not call generate_set_layer for this layer again" in LAYERS_OFF_NOTE
    saved = repo.get_set(FAMILY, results[-1]["setId"])
    assert saved is not None and sorted(la.depth for la in saved.layers) == [0, 1, 2]
    assert all(la.art is None and la.color for la in saved.layers)


def test_generate_prop_is_unaffected_by_the_switch() -> None:
    repo = InMemoryRepo()
    load_sample_family(repo, FAMILY)
    model = FakeImageModel()
    art = ArtService(model, FakeBackgroundRemover(), max_images_per_run=24, layers_enabled=False)
    tools = {t.name: t for t in make_prop_tools(repo, _run(), art)}
    result = json.loads(
        tools["generate_prop"].invoke({"name": "picnic basket", "description": "a wicker basket", "size": "carried"})
    )
    assert result["art"] == "generated" and len(model.calls) == 1


# ---------------------------------------------------------------------- production


@pytest.fixture
def mock_db() -> Iterator[None]:
    db_client.set_db(mongomock.MongoClient(tz_aware=True)[db_client.MONGODB_DB])
    runs.configure(None)
    yield
    runs.configure(None)
    db_client.set_db(None)


def test_production_passes_the_switch_to_the_art_service(monkeypatch: pytest.MonkeyPatch, mock_db: None) -> None:
    for name in (
        "MONGODB_URI", "IMAGE_PROVIDER", "HIGGSFIELD_API_KEY", "CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_API_TOKEN",
        "STITCH_MAX_IMAGES_PER_RUN",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-dummy")
    monkeypatch.setenv("STITCH_FAKE_DB", "1")
    monkeypatch.setenv("IMAGE_API_KEY", "img-dummy")
    monkeypatch.delenv("STITCH_ART_LAYERS", raising=False)
    assert production.production_services().art.layers_enabled is True
    monkeypatch.setenv("STITCH_ART_LAYERS", "off")
    svc = production.production_services()
    assert svc.art.layers_enabled is False
    assert svc.art.max_images_per_run == 24  # the budget is untouched
    assert svc.labels["limits"] == RunLimits().label()
    monkeypatch.setenv("STITCH_ART_LAYERS", "sometimes")
    assert production.production_services().art.layers_enabled is True


# ----------------------------------------------------------------------- preflight


def _art(env: dict[str, str]) -> preflight.ServiceReport:
    return next(r for r in preflight.build_report(env) if r.service == "art")


def test_preflight_shows_the_switch() -> None:
    assert guards.ENV_ART_LAYERS in preflight.ENV_NAMES
    unset = next(v for v in _art({}).vars if v.name == "STITCH_ART_LAYERS")
    assert unset.set is False and "default: on" in unset.shown and unset.problem is None

    off = _art({"STITCH_ART_LAYERS": "off", "IMAGE_API_KEY": "sk-dummy-key"})
    var = next(v for v in off.vars if v.name == "STITCH_ART_LAYERS")
    assert var.set is True and var.shown.startswith("off") and var.problem is None
    assert any("STITCH_ART_LAYERS=off" in note and "placeholder" in note for note in off.notes)

    bad = next(v for v in _art({"STITCH_ART_LAYERS": "maybe"}).vars if v.name == "STITCH_ART_LAYERS")
    assert bad.severity == "warning" and "'on' or 'off'" in (bad.problem or "")
    assert not any("STITCH_ART_LAYERS=off" in note for note in _art({"STITCH_ART_LAYERS": "maybe"}).notes)
