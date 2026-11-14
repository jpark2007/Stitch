"""agent.guards.RunLimits (env parsing) and the image budget across one run's props and sets."""

from __future__ import annotations

import dataclasses
import json
import logging
from collections.abc import Iterator
from typing import Any

import pytest

import schemas
from agent import guards
from agent.guards import RunLimits
from agent.repo import InMemoryRepo, load_sample_family
from agent.run_info import RunInfo
from agent.tools.art import ArtService, FakeBackgroundRemover, FakeImageModel, placeholder_color
from agent.tools.props import make_prop_tools
from agent.tools.sets import make_set_tools

DEFAULTS = RunLimits()
ENV = {
    "STITCH_MAX_IMAGES_PER_RUN": "max_images_per_run",
    "STITCH_MAX_GRAPH_STEPS": "max_graph_steps",
    "STITCH_MODEL_TIMEOUT_S": "model_timeout_s",
    "STITCH_MODEL_MAX_RETRIES": "model_max_retries",
    "STITCH_RUN_TIMEOUT_S": "run_timeout_s",
}


@pytest.fixture(autouse=True)
def fresh_warnings() -> Iterator[None]:
    guards._warned.clear()
    yield
    guards._warned.clear()


def warnings_about(caplog: pytest.LogCaptureFixture, name: str) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelname == "WARNING" and name in r.getMessage()]


# ------------------------------------------------------------------ RunLimits.from_env


def test_defaults() -> None:
    assert DEFAULTS == RunLimits(
        max_images_per_run=24, max_graph_steps=1000, model_timeout_s=120.0, model_max_retries=2, run_timeout_s=1200.0
    )
    assert RunLimits.from_env({}) == DEFAULTS
    with pytest.raises(dataclasses.FrozenInstanceError):
        DEFAULTS.max_images_per_run = 3  # type: ignore[misc]


def test_reads_os_environ_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ENV:
        monkeypatch.delenv(name, raising=False)
    assert RunLimits.from_env() == DEFAULTS
    monkeypatch.setenv("STITCH_MAX_IMAGES_PER_RUN", "5")
    assert RunLimits.from_env().max_images_per_run == 5


def test_valid_overrides(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING, logger="agent.guards")
    limits = RunLimits.from_env({
        "STITCH_MAX_IMAGES_PER_RUN": "0",
        "STITCH_MAX_GRAPH_STEPS": " 50 ",
        "STITCH_MODEL_TIMEOUT_S": "45.5",
        "STITCH_MODEL_MAX_RETRIES": "0",
        "STITCH_RUN_TIMEOUT_S": "600",
        "UNRELATED": "x",
    })
    assert limits == RunLimits(
        max_images_per_run=0, max_graph_steps=50, model_timeout_s=45.5, model_max_retries=0, run_timeout_s=600.0
    )
    assert isinstance(limits.max_images_per_run, int) and isinstance(limits.run_timeout_s, float)
    assert not caplog.records


BAD_VALUES = [
    ("STITCH_MAX_IMAGES_PER_RUN", ["", "   ", "lots", "2.5", "-1"]),
    ("STITCH_MAX_GRAPH_STEPS", ["", "many", "100.0", "49", "0", "-5"]),
    ("STITCH_MODEL_TIMEOUT_S", ["", "soon", "0", "-1", "nan", "inf"]),
    ("STITCH_MODEL_MAX_RETRIES", ["", "twice", "1.5", "-1"]),
    ("STITCH_RUN_TIMEOUT_S", ["", "forever", "0", "-30", "inf"]),
]


@pytest.mark.parametrize(("name", "value"), [(n, v) for n, vs in BAD_VALUES for v in vs])
def test_a_bad_value_warns_naming_the_variable_and_keeps_the_default(
    caplog: pytest.LogCaptureFixture, name: str, value: str
) -> None:
    caplog.set_level(logging.WARNING, logger="agent.guards")
    # Every other variable is set to a valid, non-default value: those still apply.
    valid = {"STITCH_MAX_IMAGES_PER_RUN": "3", "STITCH_MAX_GRAPH_STEPS": "60", "STITCH_MODEL_TIMEOUT_S": "9",
             "STITCH_MODEL_MAX_RETRIES": "4", "STITCH_RUN_TIMEOUT_S": "90"}
    limits = RunLimits.from_env({**valid, name: value})
    field = ENV[name]
    assert getattr(limits, field) == getattr(DEFAULTS, field)
    for other, other_field in ENV.items():
        if other != name:
            assert getattr(limits, other_field) == type(getattr(DEFAULTS, other_field))(valid[other])
    warned = warnings_about(caplog, name)
    assert len(warned) == 1
    assert len(caplog.records) == 1  # only the bad variable is warned about
    assert repr(value) in warned[0] and str(getattr(DEFAULTS, field)) in warned[0]


def test_the_same_bad_value_warns_once_per_process(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING, logger="agent.guards")
    for _ in range(3):
        assert RunLimits.from_env({"STITCH_MAX_GRAPH_STEPS": "ten"}).max_graph_steps == 1000
    assert len(warnings_about(caplog, "STITCH_MAX_GRAPH_STEPS")) == 1
    RunLimits.from_env({"STITCH_MAX_GRAPH_STEPS": "eleven"})  # a different bad value warns again
    assert len(warnings_about(caplog, "STITCH_MAX_GRAPH_STEPS")) == 2


def test_label() -> None:
    assert DEFAULTS.label() == "images=24 steps=1000 model_timeout=120s model_retries=2 run_timeout=1200s"
    assert RunLimits(run_timeout_s=90.5).label().endswith(" model_retries=2 run_timeout=90.5s")
    assert RunLimits(max_images_per_run=0, model_timeout_s=30.5).label().startswith("images=0 steps=1000 model_timeout=30.5s")


# ------------------------------------------------------------------ the image budget over one run

FAM = "fam_guard"


def make_run(run_id: str) -> RunInfo:
    return RunInfo(
        run_id=run_id,
        family_id=FAM,
        user_id="user_1",
        author_id="dad",
        fields=schemas.SubmitMemoryFields(text="We drove to the lake in the old station wagon.", year=1979),
    )


def run_tools(repo: InMemoryRepo, run: RunInfo, art: ArtService) -> dict[str, Any]:
    return {t.name: t for t in [*make_prop_tools(repo, run, art), *make_set_tools(repo, run, art)]}


def test_a_run_making_props_and_set_layers_stops_drawing_at_the_cap() -> None:
    repo = InMemoryRepo()
    load_sample_family(repo, FAM)
    model = FakeImageModel()  # records every generate call
    art = ArtService(model, FakeBackgroundRemover(), max_images_per_run=2)
    tools = run_tools(repo, make_run("run_a"), art)

    first = json.loads(tools["generate_prop"].invoke({"name": "picnic basket", "description": "wicker", "size": "hand"}))
    second = json.loads(tools["generate_set_layer"].invoke({"place": "the lake", "layer": "back", "description": "a lake"}))
    third = json.loads(tools["generate_prop"].invoke({"name": "station wagon", "description": "wood panels",
                                                      "size": "vehicle"}))
    assert len(model.calls) == 2
    assert first["art"] == "generated" and "note" not in first
    assert second["art"] == "generated" and "note" not in second
    assert third["art"] == "placeholder"
    assert third["note"] == "image budget for this run is used up (2 images); station wagon uses placeholder art"
    saved = repo.get_prop(FAM, third["id"])
    assert saved is not None and saved.art["body"] is None and saved.color == placeholder_color("station wagon")
    assert art.images_left("run_a") == 0

    # A set layer after the cap is a placeholder too, and the call still succeeds.
    fourth = json.loads(tools["generate_set_layer"].invoke({"place": "the lake", "layer": "front", "description": "reeds"}))
    assert fourth["art"] == "placeholder"
    assert fourth["note"] == "image budget for this run is used up (2 images); the front layer of the lake uses placeholder art"
    assert len(model.calls) == 2

    # Another run of the same family has its own budget.
    other = run_tools(repo, make_run("run_b"), art)
    fresh = json.loads(other["generate_prop"].invoke({"name": "fishing rod", "description": "bamboo", "size": "hand"}))
    assert fresh["art"] == "generated" and "note" not in fresh
    assert len(model.calls) == 3
    assert art.images_left("run_b") == 1
