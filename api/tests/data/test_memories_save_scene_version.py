"""db/memories.save_scene(expected_version=...): the optimistic-concurrency guard (mongomock).

With None the save is unconditional, exactly as before (agent code never passes it). With an
int the write lands only while the stored version still equals it; otherwise VersionConflict
is raised and nothing is written. A missing memory is still None.
"""

from __future__ import annotations

import pytest

from db import client as db_client
from db import memories as memories_db
from seed import demo

FID = demo.DEMO_FAMILY_ID


@pytest.fixture
def db(mdb):
    db_client.ensure_indexes(mdb)
    demo.seed_demo(mdb)
    return mdb


def _scene(db, memory_id: str):
    memory = memories_db.get_memory(db, FID, memory_id)
    assert memory is not None and memory.screenplay is not None and memory.timeline is not None
    return memory


def _blank_memory(db) -> str:
    """A new moment in the demo family with no scene yet (version 1)."""
    memory = memories_db.create_memory(db, FID, title="No scene yet", year=1990, participant_ids=["mom"])
    assert memory.version == 1 and memory.timeline is None
    return memory.id


def _save(db, memory_id: str, source, **kw):
    return memories_db.save_scene(
        db, FID, memory_id,
        screenplay=source.screenplay, timeline=source.timeline,
        prop_ids=source.prop_ids, set_ids=source.set_ids, **kw,
    )


def _stored(db, memory_id: str) -> dict:
    doc = db["memories"].find_one({"_id": memory_id})
    assert doc is not None
    return {k: doc.get(k) for k in ("screenplay", "timeline", "version", "propIds", "setIds", "title")}


def test_without_expected_version_first_save_keeps_v1_and_later_saves_increment(db):
    source = _scene(db, demo.HYMN_MEMORY_ID)
    mid = _blank_memory(db)
    first = _save(db, mid, source)
    assert first is not None and first.version == 1 and first.timeline == source.timeline
    second = _save(db, mid, source)
    assert second is not None and second.version == 2
    third = _save(db, mid, source, expected_version=None)
    assert third is not None and third.version == 3


def test_a_matching_expected_version_saves_and_increments(db):
    memory = _scene(db, demo.HYMN_MEMORY_ID)
    saved = _save(db, memory.id, memory, expected_version=memory.version)
    assert saved is not None and saved.version == memory.version + 1
    again = _save(db, memory.id, memory, expected_version=saved.version)
    assert again is not None and again.version == memory.version + 2


def test_a_matching_expected_version_on_the_first_save_keeps_v1(db):
    source = _scene(db, demo.HYMN_MEMORY_ID)
    mid = _blank_memory(db)
    saved = _save(db, mid, source, expected_version=1)
    assert saved is not None and saved.version == 1 and saved.screenplay == source.screenplay


def test_a_stale_expected_version_raises_and_writes_nothing(db):
    memory = _scene(db, demo.HYMN_MEMORY_ID)
    other = _scene(db, demo.DEMO_MEMORY_IDS[-1] if demo.DEMO_MEMORY_IDS[-1] != memory.id else demo.DEMO_MEMORY_IDS[0])
    assert other.screenplay != memory.screenplay
    before = _stored(db, memory.id)
    for stale in (memory.version - 1, memory.version + 1, 0):
        with pytest.raises(memories_db.VersionConflict):
            memories_db.save_scene(
                db, FID, memory.id,
                screenplay=other.screenplay, timeline=other.timeline,
                prop_ids=other.prop_ids, set_ids=other.set_ids, title="Overwritten",
                expected_version=stale,
            )
        assert _stored(db, memory.id) == before


def test_a_stale_expected_version_on_the_first_save_raises_and_writes_nothing(db):
    source = _scene(db, demo.HYMN_MEMORY_ID)
    mid = _blank_memory(db)
    before = _stored(db, mid)
    with pytest.raises(memories_db.VersionConflict):
        _save(db, mid, source, expected_version=2)
    assert _stored(db, mid) == before
    assert before["timeline"] is None and before["version"] == 1


def test_a_missing_memory_is_none(db):
    source = _scene(db, demo.HYMN_MEMORY_ID)
    assert _save(db, "no_such_memory", source) is None
    assert _save(db, "no_such_memory", source, expected_version=1) is None
    # Another family's memory reads as missing too, never as a conflict.
    assert memories_db.save_scene(
        db, "another_family", source.id,
        screenplay=source.screenplay, timeline=source.timeline,
        prop_ids=[], set_ids=[], expected_version=source.version,
    ) is None
    assert _stored(db, source.id)["version"] == source.version


def test_version_conflict_is_an_exception_of_the_module():
    assert issubclass(memories_db.VersionConflict, Exception)
