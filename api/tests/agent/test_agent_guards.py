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
    "STITCH_MAX_MODEL_CALLS": "max_model_calls",
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
        max_images_per_run=24, max_graph_steps=1000, model_timeout_s=120.0, model_max_retries=2, run_timeout_s=1200.0,
        max_model_calls=600,
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
        "STITCH_MAX_MODEL_CALLS": "100",
        "UNRELATED": "x",
    })
    assert limits == RunLimits(
        max_images_per_run=0, max_graph_steps=50, model_timeout_s=45.5, model_max_retries=0, run_timeout_s=600.0,
        max_model_calls=100,
    )
    assert isinstance(limits.max_images_per_run, int) and isinstance(limits.run_timeout_s, float)
    assert not caplog.records


BAD_VALUES = [
    ("STITCH_MAX_IMAGES_PER_RUN", ["", "   ", "lots", "2.5", "-1"]),
    ("STITCH_MAX_GRAPH_STEPS", ["", "many", "100.0", "49", "0", "-5"]),
    ("STITCH_MODEL_TIMEOUT_S", ["", "soon", "0", "-1", "nan", "inf"]),
    ("STITCH_MODEL_MAX_RETRIES", ["", "twice", "1.5", "-1"]),
    ("STITCH_RUN_TIMEOUT_S", ["", "forever", "0", "-30", "inf"]),
    ("STITCH_MAX_MODEL_CALLS", ["", "   ", "plenty", "250.0", "99", "0", "-1"]),
]


@pytest.mark.parametrize(("name", "value"), [(n, v) for n, vs in BAD_VALUES for v in vs])
def test_a_bad_value_warns_naming_the_variable_and_keeps_the_default(
    caplog: pytest.LogCaptureFixture, name: str, value: str
) -> None:
    caplog.set_level(logging.WARNING, logger="agent.guards")
    # Every other variable is set to a valid, non-default value: those still apply.
    valid = {"STITCH_MAX_IMAGES_PER_RUN": "3", "STITCH_MAX_GRAPH_STEPS": "60", "STITCH_MODEL_TIMEOUT_S": "9",
             "STITCH_MODEL_MAX_RETRIES": "4", "STITCH_RUN_TIMEOUT_S": "90", "STITCH_MAX_MODEL_CALLS": "150"}
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


@pytest.mark.parametrize(("raw", "expected"), [("100", 100), (" 250 ", 250), ("600", 600), ("100000", 100000)])
def test_max_model_calls_accepts_a_whole_number_at_or_above_the_floor(
    caplog: pytest.LogCaptureFixture, raw: str, expected: int
) -> None:
    caplog.set_level(logging.WARNING, logger="agent.guards")
    assert guards.MIN_MODEL_CALLS == 100
    assert RunLimits.from_env({"STITCH_MAX_MODEL_CALLS": raw}).max_model_calls == expected
    assert not caplog.records


@pytest.mark.parametrize(("raw", "rule"), [("", "a whole number >= 100"), ("lots", "a whole number >= 100"),
                                           ("99", "a whole number >= 100"), ("12.5", "a whole number >= 100")])
def test_max_model_calls_blank_junk_or_below_the_floor_warns_once_and_keeps_600(
    caplog: pytest.LogCaptureFixture, raw: str, rule: str
) -> None:
    caplog.set_level(logging.WARNING, logger="agent.guards")
    for _ in range(2):
        assert RunLimits.from_env({"STITCH_MAX_MODEL_CALLS": raw}).max_model_calls == 600
    warned = warnings_about(caplog, "STITCH_MAX_MODEL_CALLS")
    assert len(warned) == 1 and rule in warned[0] and "600" in warned[0]


def test_max_model_calls_unset_is_the_default_without_a_warning(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING, logger="agent.guards")
    assert RunLimits.from_env({}).max_model_calls == 600
    assert not caplog.records


def test_the_documented_meaning_of_the_step_limit_and_the_per_run_caps() -> None:
    """STITCH_MAX_GRAPH_STEPS bounds one studio graph stream, not a run and not a sub-agent;
    the per-run caps are STITCH_MAX_MODEL_CALLS and STITCH_RUN_TIMEOUT_S (test_agent_run_budget.py
    pins the behaviour, this pins what the docs say about it)."""
    doc = " ".join((guards.__doc__ or "").split())
    assert "steps per studio graph invocation" in doc
    assert "NOT a per-run cap" in doc and "fresh step budget, on every resume" in doc
    assert "deepagents and langchain bind their own recursion limit (9999) on every sub-agent graph" in doc
    assert "The per-run caps are STITCH_MAX_MODEL_CALLS (cost) and STITCH_RUN_TIMEOUT_S (time)" in doc


# ------------------------------------------------------------------ env_value


@pytest.mark.parametrize(
    ("env", "expected"),
    [({}, None), ({"X": ""}, None), ({"X": " "}, None), ({"X": " \t\n "}, None), ({"X": "m1"}, "m1"),
     ({"X": "  m1 \n"}, "m1")],
)
def test_env_value_is_stripped_or_none(env: dict[str, str], expected: str | None) -> None:
    assert guards.env_value(env, "X") == expected


def test_env_value_reads_os_environ_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STITCH_TEST_ENV_VALUE", "  padded  ")
    assert guards.env_value(None, "STITCH_TEST_ENV_VALUE") == "padded"
    monkeypatch.setenv("STITCH_TEST_ENV_VALUE", "   ")
    assert guards.env_value(None, "STITCH_TEST_ENV_VALUE") is None


# ------------------------------------------------------------------ RunUsage


def test_run_usage_counts_model_calls_per_run_and_refuses_past_the_cap() -> None:
    usage = guards.RunUsage()
    assert [usage.take_model_call("a", 3) for _ in range(3)] == [1, 2, 3]
    with pytest.raises(guards.RunBudgetExceeded) as raised:
        usage.take_model_call("a", 3)
    assert raised.value.limit == 3 and raised.value.run_id == "a"
    assert "limit of 3 model calls" in str(raised.value)
    assert usage.model_calls("a") == 3  # the refused call is not counted
    assert usage.take_model_call("b", 3) == 1  # another run has its own count
    assert usage.take_model_call("a", None) == 4  # no cap: counted, never refused
    assert usage.counts("a") == (4, 0) and usage.counts("unknown") == (0, 0)


def test_run_usage_seed_never_lowers_a_count_and_forget_drops_the_run() -> None:
    usage = guards.RunUsage()
    usage.seed("a", model_calls=7, images_used=2)
    assert usage.counts("a") == (7, 2)
    usage.seed("a", model_calls=3, images_used=5)
    assert usage.counts("a") == (7, 5)
    assert usage.take_image("a", 6) == (True, False)
    assert usage.take_image("a", 6) == (False, True)  # the first refusal is flagged once
    assert usage.take_image("a", 6) == (False, False)
    usage.forget("a")
    assert usage.counts("a") == (0, 0)


def test_run_usage_keeps_the_most_recent_runs_only() -> None:
    usage = guards.RunUsage(keep=2)
    for run in ("a", "b", "c"):
        usage.take_model_call(run, None)
    assert usage.counts("a") == (0, 0) and usage.counts("c") == (1, 0)


def test_run_usage_is_thread_safe() -> None:
    import threading

    usage = guards.RunUsage()
    refused: list[int] = []

    def work() -> None:
        for _ in range(100):
            try:
                usage.take_model_call("r", 250)
            except guards.RunBudgetExceeded:
                refused.append(1)

    threads = [threading.Thread(target=work) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert usage.model_calls("r") == 250 and len(refused) == 800 - 250


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
