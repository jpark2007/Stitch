"""Replay across families: a story replays in whichever family has its cast, and is checked there."""

from __future__ import annotations

import pytest

from demo.replay import manager as replay_manager
from demo.replay.manager import ReplayRunManager, cast_fits, story_family
from demo.replay.tests.conftest import FAM_B, FID, fields
from demo.replay.tests.test_manager import solo_story


@pytest.fixture
def families(monkeypatch):
    monkeypatch.setattr(replay_manager, "REPLAY_FAMILIES", [FID])
    return replay_manager.REPLAY_FAMILIES


def test_cast_fits_only_the_family_with_every_cast_member(db, example):
    solo = solo_story(example[0])
    assert cast_fits(db, FID, example[0]) and not cast_fits(db, FAM_B, example[0])
    assert cast_fits(db, FAM_B, solo) and not cast_fits(db, FID, solo)


def test_a_story_is_checked_against_the_first_added_family_with_its_cast(db, example, families):
    solo = solo_story(example[0])
    assert story_family(db, example[0]) == FID
    assert story_family(db, solo) == FID  # not added yet: the demo family names the missing cast
    replay_manager.add_replay_family(FAM_B)
    replay_manager.add_replay_family(FAM_B)
    assert families == [FID, FAM_B]
    assert story_family(db, solo) == FAM_B
    assert story_family(db, example[0]) == FID


def test_a_story_with_a_family_id_is_checked_against_that_family(db, example):
    import dataclasses

    pinned = dataclasses.replace(example[0], family_id=FAM_B)
    assert story_family(db, pinned) == FAM_B


def test_each_family_matches_only_the_stories_it_has_the_cast_for(db, example):
    # Both stories match the same text; each family gets its own.
    solo = solo_story(example[0])
    mgr = ReplayRunManager(stories=[example[0], solo], db_getter=lambda: db, step_seconds=0)
    try:
        assert mgr.match(FID, fields(), db=db).story.name == example[0].name
        assert mgr.match(FAM_B, fields(), db=db).story.name == "solo"
        assert mgr.match(FAM_B, fields(memory_id="m1"), db=db) is None
    finally:
        mgr.shutdown()
