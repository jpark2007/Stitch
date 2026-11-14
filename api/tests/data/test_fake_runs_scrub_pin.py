"""Pull-tab scrub windows stay on their action when the scripted narrator pins the action.

`_lines_over(..., pin=True)` (the default narrator's retelling in `telling_narration`, and
`narrate_as_teller` for a new memory) writes every action at the time the old narration cued
it. A scrub interaction's from/to are written in the shot's pre-cue time, and the compiler
maps them through `narration.retime_point` from the actions' written times to their cued ones;
once the actions are pinned, the written times ARE the cued ones, so the window has to be
rewritten too or the tab drifts (the bug-hunt evidence: demo_sams_first_bike ride_tab
8.6-10.6 became 9.0-11.0).

mongomock + seed_demo; no MongoDB, no keys, no network.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

import schemas
from db import client as db_client
from db import memories as memories_db
from screenplay import narration as narration_timing
from screenplay.schema import Interaction, InteractionDrive, Screenplay
from seed import demo, fake_runs
from seed.fake_runs import ScriptedRunManager, narrate_as_teller, pace, telling_narration

FID = demo.DEMO_FAMILY_ID
ADMIN = demo.DEMO_ACCOUNTS[0]
DEADLINE = 20.0
TOLERANCE = 0.01


def _scrub_memories() -> list[str]:
    return [
        mid for mid in demo.DEMO_MEMORY_IDS
        if any(i.drives.type == "scrub" for i in demo.load_screenplay(mid).interactions or [])
    ]


SCRUB_MEMORIES = _scrub_memories()


@pytest.fixture
def db(mdb):
    db_client.ensure_indexes(mdb)
    demo.seed_demo(mdb)
    db_client.set_db(mdb)
    try:
        yield mdb
    finally:
        db_client.set_db(None)


@pytest.fixture
def manager(db):
    mgr = ScriptedRunManager(db_getter=lambda: db, step_seconds=0)
    yield mgr
    mgr.shutdown()


def _scrubs(memory: schemas.Memory) -> dict[str, tuple[float | None, float | None]]:
    assert memory.timeline is not None
    return {
        i.id: (i.drives.from_, i.drives.to)
        for i in memory.timeline.interactions or []
        if i.drives.type == "scrub"
    }


def _action_times(memory: schemas.Memory) -> dict[str, list[float]]:
    """Each shot's actions in timeline time, as the compiler placed them (start + retimed t)."""
    sp, tl = memory.screenplay, memory.timeline
    assert sp is not None and tl is not None
    starts = {s.id: s.action_start for s in tl.shots}
    default = narration_timing.default_author(sp)
    return {
        shot.id: [
            round(starts[shot.id] + t, 3)
            for t in narration_timing.retime_actions(shot, starts[shot.id], author=default)
        ]
        for shot in sp.shots
    }


def test_some_demo_memories_have_scrubs():
    assert SCRUB_MEMORIES, "no seeded demo moment has a scrub tab; this file tests nothing"


@pytest.mark.parametrize("memory_id", SCRUB_MEMORIES)
def test_the_default_narrators_unchanged_retelling_keeps_every_scrub_window(db, manager, memory_id):
    before = memories_db.get_memory(db, FID, memory_id)
    assert before is not None and before.timeline is not None
    narrator = before.timeline.default_narrator
    [telling] = [t for t in before.tellings if t.author_id == narrator]
    fields = schemas.SubmitMemoryFields(text=telling.text, year=before.year, memory_id=memory_id)
    run_id = manager.start_run(FID, ADMIN.user_id, narrator, fields, [])

    async def go() -> list[tuple[str, Any]]:
        return [e async for e in manager.stream_run(FID, run_id)]

    events = asyncio.run(asyncio.wait_for(go(), DEADLINE))
    assert events[-1][0] == "done", events[-1]
    after = memories_db.get_memory(db, FID, memory_id)
    assert after is not None and after.version == before.version + 1

    # The pinned action did not move (shared action), and an unchanged retelling is no longer
    # than the old one, so no shot moved: every tab scrubs exactly the window it did before.
    assert [s.action_start for s in after.timeline.shots] == pytest.approx(
        [s.action_start for s in before.timeline.shots], abs=TOLERANCE
    )
    old, new = _scrubs(before), _scrubs(after)
    assert set(new) == set(old)
    for tab, was in old.items():
        assert new[tab] == pytest.approx(was, abs=TOLERANCE), f"{memory_id} {tab}: {was} -> {new[tab]}"
    old_times, new_times = _action_times(before), _action_times(after)
    assert set(new_times) == set(old_times)
    for shot_id, times in old_times.items():
        assert new_times[shot_id] == pytest.approx(times, abs=TOLERANCE), f"{memory_id} {shot_id}: the action moved"


@pytest.mark.parametrize("memory_id", SCRUB_MEMORIES)
def test_telling_narration_and_compile_keep_the_scrub_windows(db, memory_id):
    """The same check without the run: telling_narration + compile_scene directly."""
    before = memories_db.get_memory(db, FID, memory_id)
    assert before is not None and before.screenplay is not None and before.timeline is not None
    narrator = before.timeline.default_narrator
    [telling] = [t for t in before.tellings if t.author_id == narrator]
    redone = telling_narration(before.screenplay, narrator, telling.text)
    result = fake_runs.compile_scene(db, FID, redone)
    if not result.ok:  # the run falls back to uncued lines the same way
        redone = telling_narration(before.screenplay, narrator, telling.text, cued=False)
        result = fake_runs.compile_scene(db, FID, redone)
    assert result.ok and result.timeline is not None, [e.message for e in result.errors]
    old, new = _scrubs(before), _scrubs(memories_db.get_memory(db, FID, memory_id).model_copy(
        update={"timeline": result.timeline}))
    assert set(new) == set(old)
    for tab, was in old.items():
        assert new[tab] == pytest.approx(was, abs=TOLERANCE), f"{memory_id} {tab}: {was} -> {new[tab]}"


# ------------------------------------------------------------------------------ unit


def _scene_with_scrubs() -> Screenplay:
    """One demo shot whose default narration cues an action later than written, plus scrubs:
    one on that shot, one with no shot (first shot), one with an open end, one on another shot,
    and a non-scrub interaction."""
    base = demo.load_screenplay("demo_sams_first_bike")
    sp = base.model_copy(deep=True)
    first, second = sp.shots[0].id, sp.shots[1].id
    sp.interactions = [
        Interaction(id="on_first", kind="pull_tab", label="a", shot=first, at=[0.5, 0.5],
                    drives=InteractionDrive(type="scrub", from_=7.8, to=9.8)),
        Interaction(id="no_shot", kind="pull_tab", label="b", at=[0.5, 0.5],
                    drives=InteractionDrive(type="scrub", from_=7.9, to=9.0)),
        Interaction(id="open_end", kind="pull_tab", label="c", shot=first, at=[0.5, 0.5],
                    drives=InteractionDrive(type="scrub", from_=8.0)),
        Interaction(id="on_second", kind="pull_tab", label="d", shot=second, at=[0.5, 0.5],
                    drives=InteractionDrive(type="scrub", from_=0.5, to=1.5)),
        Interaction(id="toggle", kind="slider", label="e", shot=first, at=[0.5, 0.5],
                    drives=InteractionDrive(type="prop_state", prop=sp.props[0] if sp.props else "x",
                                            states=["a", "b"])),
    ]
    return sp


def _expected(sp: Screenplay) -> dict[str, tuple[float | None, float | None]]:
    """What retime_point makes of each scrub, from the written actions to the cued ones."""
    default = narration_timing.default_author(sp)
    timing = {}
    for shot in sp.shots:
        original = [a.t for a in shot.actions]
        try:
            retimed = narration_timing.retime_actions(shot, 0.0, author=default)
        except narration_timing.NarrationError:
            retimed = list(original)
        timing[shot.id] = (original, retimed)
    out = {}
    for it in sp.interactions or []:
        if it.drives.type != "scrub":
            continue
        original, retimed = timing[it.shot or sp.shots[0].id]
        out[it.id] = tuple(
            None if v is None else narration_timing.retime_point(v, original, retimed)
            for v in (it.drives.from_, it.drives.to)
        )
    return out


def test_pinning_moves_scrubs_through_retime_point_and_keeps_an_open_end():
    sp = _scene_with_scrubs()
    expected = _expected(sp)
    default = narration_timing.default_author(sp)
    # The demo's cues really move this shot's actions, or the test would prove nothing.
    shot = sp.shots[0]
    assert narration_timing.retime_actions(shot, 0.0, author=default) != [a.t for a in shot.actions]
    assert expected["on_first"] != (7.8, 9.8)

    for result in (
        narrate_as_teller(sp, default, "We rode all afternoon. Then we went home for dinner."),
        telling_narration(sp, default, "We rode all afternoon. Then we went home for dinner."),
    ):
        got = {it.id: (it.drives.from_, it.drives.to) for it in result.interactions or [] if it.drives.type == "scrub"}
        assert got == {k: pytest.approx(v) if None not in v else v for k, v in expected.items()}
        assert got["open_end"][1] is None
        toggle = next(it for it in result.interactions or [] if it.id == "toggle")
        assert toggle == next(it for it in sp.interactions or [] if it.id == "toggle")
    # Pure: the input is untouched.
    assert sp == _scene_with_scrubs()


def test_another_tellers_track_never_moves_the_scrubs():
    sp = _scene_with_scrubs()
    other = next(c for c in sp.cast if c != narration_timing.default_author(sp))
    result = telling_narration(sp, other, "I watched from the porch. He did not fall once.")
    assert result.interactions == sp.interactions
    assert [a.t for s in result.shots for a in s.actions] == [a.t for s in sp.shots for a in s.actions]


def test_pace_stretches_the_scrub_windows_with_the_actions():
    sp = _scene_with_scrubs()
    slow = pace(sp, 2.0)
    before = {it.id: it.drives for it in sp.interactions or []}
    for it in slow.interactions or []:
        if it.drives.type != "scrub":
            assert it.drives == before[it.id].model_copy()
            continue
        for was, now in ((before[it.id].from_, it.drives.from_), (before[it.id].to, it.drives.to)):
            assert now == (None if was is None else pytest.approx(was * 2.0))
