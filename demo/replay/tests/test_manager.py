"""ReplayRunManager: matching tellings end on the prepared scene; everything else is the scripted run."""

from __future__ import annotations

import inspect
import sys
import types
from typing import Any

import pytest

import schemas
from db import memories as memories_db
from db import props as props_db
from screenplay.narration import default_author
from screenplay.schema import CompileError, CompileResult
from seed import fake_runs
from seed.fake_runs import ScriptedRunManager, compile_scene

from demo.replay import manager as replay_manager
from demo.replay.manager import SEAMS, ReplayRunManager, install_replay_runs, narrated_by
from demo.replay.tests.conftest import (
    ANA,
    B_TELLER,
    B_USER,
    DAVID,
    EXAMPLE_TEXT,
    FAM_B,
    FID,
    OTHER_TEXT,
    drive,
    fields,
    names,
    payloads,
)

HYMN = "hymn_books"


@pytest.fixture
def replay(db, example):
    mgr = ReplayRunManager(stories=example, db_getter=lambda: db, step_seconds=0)
    yield mgr
    mgr.shutdown()


@pytest.fixture
def scripted(db):
    mgr = ScriptedRunManager(db_getter=lambda: db, step_seconds=0)
    yield mgr
    mgr.shutdown()


def run(mgr, *, family_id=FID, user_id=DAVID, person_id="dad", photo_ids=(), **kw) -> tuple[list, schemas.Memory]:
    run_id = mgr.start_run(family_id, user_id, person_id, fields(**kw), list(photo_ids))
    events = drive(mgr, family_id, run_id, user_id)
    assert events[-1][0] == "done", events[-1]
    memory = memories_db.get_memory(mgr._db_getter(), family_id, events[-1][1].memory_id)
    assert memory is not None
    return events, memory


def assert_well_formed(events) -> None:
    for name, payload in events:
        assert isinstance(payload, schemas.RUN_EVENTS[name]), name
    assert [n for n in names(events) if n in ("done", "error")] == ["done"]


# ------------------------------------------------------------------ not matching: scripted


def test_a_non_matching_telling_gets_the_scripted_hymn_book_scene(replay, scripted):
    events, memory = run(replay, text=OTHER_TEXT)
    assert_well_formed(events)
    [found] = payloads(events, "props_found")
    assert [c.id for c in found.props] == [HYMN]
    assert HYMN in memory.prop_ids and memory.set_ids == ["church_steps_1988"]
    assert HYMN in memory.screenplay.props
    assert memory.title == fake_runs.short_title(OTHER_TEXT) and memory.year == 1999

    expected, _ = run(scripted, text=OTHER_TEXT)
    assert names(events) == names(expected)
    same = [p for n, p in events if n not in ("scene_ready", "done")]
    assert same == [p for n, p in expected if n not in ("scene_ready", "done")]


def test_without_stories_every_telling_is_scripted(db):
    mgr = ReplayRunManager(db_getter=lambda: db, step_seconds=0)
    try:
        _, memory = run(mgr)
        assert HYMN in memory.prop_ids
    finally:
        mgr.shutdown()


def test_a_family_without_the_story_cast_gets_the_scripted_scene(replay):
    events, memory = run(replay, family_id=FAM_B, user_id=B_USER, person_id=B_TELLER)
    assert [c.name for c in payloads(events, "props_found")[0].props] == ["hymn books"]
    assert memory.set_ids == [f"church_steps_1988_{FAM_B}"]


# ------------------------------------------------------------------ matching: prepared scene


def test_a_matching_telling_streams_the_scripted_sequence(replay, scripted):
    events, _ = run(replay)
    assert_well_formed(events)
    expected, _ = run(scripted, text=OTHER_TEXT)
    assert names(events) == names(expected)
    assert names(events)[:4] == ["plan", "subagent_start", "subagent_end", "question"]
    assert {"props_found", "scene_ready", "done"} <= set(names(events))

    [question] = payloads(events, "question")
    assert question.text == "Who else was in the kitchen?"
    [found] = payloads(events, "props_found")
    assert [(c.id, c.name, c.reused) for c in found.props] == [
        ("tamale_pot", "tamale pot", True), ("lumpy_tamale", "a lumpy tamale", True),
    ]
    starts = {p.name: p.task for p in payloads(events, "subagent_start")}
    assert starts["director"] == "Stage the scene: Carmen's kitchen"
    ends = {p.name: p.summary for p in payloads(events, "subagent_end")}
    assert ends["prop_master"] == "Reused the tamale pot. Reused a lumpy tamale."
    assert "compiled without errors" in ends["director"]
    assert "hymn" not in repr(events).lower()


def test_the_saved_memory_plays_the_compiled_prepared_screenplay(replay, example, db):
    [story] = example
    events, memory = run(replay, photo_ids=["photo_a", "photo_b"])
    [ready] = payloads(events, "scene_ready")
    assert ready.memory_id == memory.id

    expected_sp = narrated_by(story.screenplay, "dad")
    expected = compile_scene(db, FID, expected_sp)
    assert expected.ok
    assert memory.timeline.model_dump() == expected.timeline.model_dump()
    assert memory.screenplay.model_dump(exclude={"memory_id"}) == expected_sp.model_dump(exclude={"memory_id"})
    assert memory.screenplay.memory_id == memory.id

    assert HYMN not in memory.prop_ids and HYMN not in memory.screenplay.props
    assert memory.set_ids == ["carmen_kitchen_1970"]
    assert set(memory.prop_ids) == set(story.screenplay.props)
    assert memory.title == "Tamale Sundays" and memory.year == 1970
    assert set(memory.participant_ids) == {"dad", "mom", "grandma_carmen", "grandpa_luis"}
    # The authored storybook text and explanations are kept, the narration is the teller's.
    assert [s.page_text for s in memory.screenplay.shots] == [s.page_text for s in story.screenplay.shots]
    assert memory.screenplay.explanations == story.screenplay.explanations
    assert default_author(memory.screenplay) == "dad" and memory.timeline.default_narrator == "dad"
    assert [line.text for s in memory.screenplay.shots for line in s.narration] == [
        line.text for s in story.screenplay.shots for line in s.narration
    ]
    [telling] = memory.tellings
    assert telling.author_id == "dad"
    assert telling.text == f"{EXAMPLE_TEXT}\n\nGrandma Carmen and Papa."
    assert telling.photo_ids == ["photo_a", "photo_b"]


def test_the_teller_who_wrote_the_narration_keeps_it(replay):
    _, memory = run(replay, user_id=ANA, person_id="mom")
    assert default_author(memory.screenplay) == "mom"
    assert set(memory.participant_ids) == {"mom", "grandma_carmen", "grandpa_luis"}


def test_a_close_telling_still_replays(replay):
    _, memory = run(replay, text=EXAMPLE_TEXT.replace("husks", "husk").lower())
    assert memory.set_ids == ["carmen_kitchen_1970"]


def test_review_edits_apply_to_the_prepared_props(replay, db):
    def review(payload):
        return schemas.AnswerRequest(kind="props", props=[
            schemas.PropEdit(id="tamale_pot", action="rename", name="soup pot"),
            schemas.PropEdit(id="lumpy_tamale", action="keep"),
        ])

    run_id = replay.start_run(FID, DAVID, "dad", fields(), [])
    events = drive(replay, FID, run_id, DAVID, review=review)
    assert events[-1][0] == "done"
    ends = {p.name: p.summary for p in payloads(events, "subagent_end")}
    assert ends["prop_master"].startswith("Reused the soup pot.")
    assert props_db.get_prop(db, FID, "tamale_pot").name == "soup pot"


def remove(prop_id: str):
    def review(payload):
        return schemas.AnswerRequest(kind="props", props=[schemas.PropEdit(id=prop_id, action="remove")])

    return review


def test_a_prop_the_scene_needs_stays_when_removed_and_the_feed_says_so(replay, db):
    run_id = replay.start_run(FID, DAVID, "dad", fields(), [])
    events = drive(replay, FID, run_id, DAVID, review=remove("tamale_pot"))
    assert events[-1][0] == "done"
    ends = {p.name: p.summary for p in payloads(events, "subagent_end")}
    assert ends["prop_master"] == "Kept the tamale pot: the scene needs it. Reused a lumpy tamale."
    memory = memories_db.get_memory(db, FID, events[-1][1].memory_id)
    assert "tamale_pot" in memory.prop_ids and "tamale_pot" in memory.screenplay.props
    assert compile_scene(db, FID, memory.screenplay).ok


def test_staging_keeps_a_removed_prop_when_the_scene_fails_without_it(replay, db, monkeypatch):
    # The dry compile after the review is the first guard; staging is the second.
    monkeypatch.setattr(ReplayRunManager, "_compiles_without", lambda self, run, replay, removed: True)
    run_id = replay.start_run(FID, DAVID, "dad", fields(), [])
    events = drive(replay, FID, run_id, DAVID, review=remove("lumpy_tamale"))
    assert events[-1][0] == "done", events[-1]
    memory = memories_db.get_memory(db, FID, events[-1][1].memory_id)
    assert memory.set_ids == ["carmen_kitchen_1970"] and "lumpy_tamale" in memory.prop_ids


def solo_story(base):
    """One shot of Bea (family B) carrying the pot past a tamale on the table, on the example's set."""
    import dataclasses

    from screenplay.schema import Screenplay

    sp = Screenplay.model_validate({
        "memoryId": "solo",
        "cast": [B_TELLER],
        "props": ["tamale_pot", "lumpy_tamale"],
        "shots": [{
            "id": "s1", "set": "carmen_kitchen_1970",
            "start": {B_TELLER: {"at": "by_stove", "holding": "tamale_pot", "facing": "right"}},
            "propStart": {"lumpy_tamale": {"at": "table_right"}},
            "camera": [{"t": 0, "move": "wide"}],
            "actions": [{"t": 0.5, "who": B_TELLER, "do": "walk_to", "to": "table_right", "id": "go"}],
            "narration": [{"t": 0, "text": "I carried the pot to the table.", "cue": "go", "cueWord": "carried"}],
            "pageText": "The pot.",
        }],
    })
    return dataclasses.replace(base, name="solo", screenplay=sp)


def test_a_removed_prop_leaves_the_prepared_scene(db, example):
    mgr = ReplayRunManager(stories=[solo_story(example[0])], db_getter=lambda: db, step_seconds=0)
    try:
        run_id = mgr.start_run(FAM_B, B_USER, B_TELLER, fields(), [])
        events = drive(mgr, FAM_B, run_id, B_USER, review=remove(f"lumpy_tamale_{FAM_B}"))
    finally:
        mgr.shutdown()
    assert events[-1][0] == "done", events[-1]
    ends = {p.name: p.summary for p in payloads(events, "subagent_end")}
    assert ends["prop_master"] == "Saved the tamale pot. Left out a lumpy tamale."
    memory = memories_db.get_memory(db, FAM_B, events[-1][1].memory_id)
    assert memory.set_ids == [f"carmen_kitchen_1970_{FAM_B}"]
    assert memory.prop_ids == [f"tamale_pot_{FAM_B}"] and memory.screenplay.props == [f"tamale_pot_{FAM_B}"]
    assert props_db.get_prop(db, FAM_B, f"lumpy_tamale_{FAM_B}") is None
    assert compile_scene(db, FAM_B, memory.screenplay).ok


def test_a_prepared_scene_that_fails_at_run_time_falls_back_to_the_scripted_one(replay, monkeypatch):
    real = replay_manager.compile_scene
    calls = []

    def failing(db, family_id, screenplay):
        if "tamale_pot" in screenplay.props:
            calls.append(screenplay)
            return CompileResult(ok=False, errors=[CompileError(code="busy", message="broken on purpose")])
        return real(db, family_id, screenplay)

    monkeypatch.setattr(replay_manager, "compile_scene", failing)
    events, memory = run(replay)
    assert calls
    assert HYMN in memory.prop_ids and memory.set_ids == ["church_steps_1988"]
    assert "compile_error" not in names(events)


def test_a_story_with_other_props_and_sets_creates_them_in_the_family(db, example):
    # Family B has Bea only, and none of the example's sets or props.
    mgr = ReplayRunManager(stories=[solo_story(example[0])], db_getter=lambda: db, step_seconds=0)
    try:
        events, memory = run(mgr, family_id=FAM_B, user_id=B_USER, person_id=B_TELLER)
    finally:
        mgr.shutdown()
    [found] = payloads(events, "props_found")
    assert [(c.id, c.reused) for c in found.props] == [
        (f"tamale_pot_{FAM_B}", False), (f"lumpy_tamale_{FAM_B}", False),
    ]
    assert memory.set_ids == [f"carmen_kitchen_1970_{FAM_B}"]
    assert f"tamale_pot_{FAM_B}" in memory.prop_ids
    assert props_db.get_prop(db, FAM_B, f"tamale_pot_{FAM_B}").family_id == FAM_B
    assert memory.screenplay.shots[0].set == f"carmen_kitchen_1970_{FAM_B}"


# ------------------------------------------------------------------ second tellings


def test_a_second_telling_of_a_replayed_memory_is_the_scripted_second_telling(replay, db):
    _, memory = run(replay)
    second = replay.start_run(FID, ANA, "mom", fields(
        text="I remember the steam on the windows. Papa always ate mine first.", memory_id=memory.id,
    ), [])
    events = drive(replay, FID, second, ANA)
    assert events[-1][0] == "done", events[-1]
    assert "props_found" not in names(events) and "question" not in names(events)
    again = memories_db.get_memory(db, FID, memory.id)
    assert again.version == memory.version + 1
    assert {t.author_id for t in again.tellings} == {"dad", "mom"}
    assert "mom" in (again.screenplay.narrations or {})
    assert again.timeline.default_narrator == "dad"
    assert again.set_ids == ["carmen_kitchen_1970"]


def test_a_second_telling_with_the_prepared_text_is_not_replayed(replay, db):
    _, memory = run(replay)
    second = replay.start_run(FID, ANA, "mom", fields(memory_id=memory.id), [])
    events = drive(replay, FID, second, ANA)
    assert events[-1][0] == "done"
    assert memories_db.get_memory(db, FID, memory.id).version == memory.version + 1


# ------------------------------------------------------------------ installing, seams


def test_install_configures_a_replay_manager(monkeypatch, example, db):
    stub = types.ModuleType("agent.runs")
    seen: list[Any] = []
    stub.configure = seen.append  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "agent.runs", stub)
    assert install_replay_runs(step_seconds=0.25, stories=example, db_getter=lambda: db) is True
    [mgr] = seen
    assert isinstance(mgr, ReplayRunManager) and mgr.stories == tuple(example)
    assert mgr._step_seconds == 0.25
    mgr.shutdown()


def test_install_loads_and_checks_the_configured_directory(monkeypatch, db, tmp_path):
    from demo.replay.tests.conftest import EXAMPLE_DIR

    stub = types.ModuleType("agent.runs")
    seen: list[Any] = []
    stub.configure = seen.append  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "agent.runs", stub)
    monkeypatch.setenv("STITCH_REPLAY_DIR", str(EXAMPLE_DIR))
    assert install_replay_runs(db_getter=lambda: db) is True
    assert [s.name for s in seen[0].stories] == ["sunday_tamales"]
    seen[0].shutdown()

    monkeypatch.setenv("STITCH_REPLAY_DIR", str(tmp_path / "missing"))
    with pytest.raises(FileNotFoundError):
        install_replay_runs(db_getter=lambda: db)


def test_install_returns_false_without_agent_runs(monkeypatch):
    monkeypatch.setitem(sys.modules, "agent.runs", None)
    assert install_replay_runs(stories=[]) is False


def test_use_replay_runs_points_main_at_the_replay_installer(monkeypatch):
    monkeypatch.setattr(fake_runs, "install_fake_runs", fake_runs.install_fake_runs)
    monkeypatch.setenv("STITCH_AGENT", "real")
    replay_manager.use_replay_runs()
    from seed.fake_runs import install_fake_runs

    assert install_fake_runs is install_replay_runs
    import os

    assert os.environ["STITCH_AGENT"] == "fake"


@pytest.mark.parametrize("name", sorted(SEAMS))
def test_the_private_seams_of_the_scripted_manager_still_exist(name):
    method = getattr(ScriptedRunManager, name, None)
    assert method is not None, f"ScriptedRunManager.{name} is gone: update demo/replay/manager.py"
    params = [p for p in inspect.signature(method).parameters if p != "self"]
    assert params == list(SEAMS[name]), f"ScriptedRunManager.{name}{params} changed"


def test_the_staged_dataclass_has_the_fields_replay_fills():
    import dataclasses

    assert [f.name for f in dataclasses.fields(fake_runs._Staged)] == [
        "screenplay", "result", "participants", "prop_ids", "set_ids",
    ]
