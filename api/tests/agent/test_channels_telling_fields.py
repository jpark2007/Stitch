"""Telling.audioId and Telling.channel (Stitch Channels contract): SubmitMemoryFields carries
them through agent.runs.start_run to the telling the studio saves, for a first and a second
telling; an audio id must be this family's own recording; a web submission (neither field)
saves exactly the document it did before.

Drives the whole studio (agent.wiring over the fake studio model, the real compiler) on
MongoRepo over mongomock, and reads the raw MongoDB documents. No network, no keys.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator

import mongomock
import pytest

import schemas
from agent import wiring
from agent.fake_studio import FakeStory, fake_studio_model
from agent.mongo_repo import MongoRepo
from agent.repo import load_sample_family
from db import media as db_media

FAMILY = "fam_channels"
OTHER = "fam_other"
WAIT = 25.0
MOM_TEXT = (
    "We carried the picnic basket down to the lake dock. "
    "I nearly dropped it at the end of the boards. "
    "He took it from me and laughed."
)
DAD_TEXT = "She was sure the basket would go in the lake. I took it before it did, and we both laughed."
# The keys a web telling's document has today (question only when one was asked).
WEB_TELLING_KEYS = {"authorId", "text", "photoIds", "createdAt"}


@pytest.fixture
def studio() -> Iterator[tuple[MongoRepo, object]]:
    db = mongomock.MongoClient(tz_aware=True)["channels_fields"]
    repo = MongoRepo(db)
    load_sample_family(repo, FAMILY)  # person ids are global in MongoDB: OTHER only holds media
    story = FakeStory(place="lake dock", prop="picnic basket", prop_size="carried", compile_failures=0)
    manager = wiring.make_manager(wiring.fake_services(model=fake_studio_model(story), repo=repo))
    try:
        yield repo, manager
    finally:
        manager.shutdown(wait=True)


def _drive(manager, user_id: str, author_id: str, fields: schemas.SubmitMemoryFields) -> str:
    """Run to the end, keeping every prop at each review pause; return the saved memory id."""
    run_id = manager.start_run(FAMILY, user_id, author_id, fields, [])
    for _ in range(5):
        status = manager.wait(FAMILY, run_id, WAIT)
        assert status != "running", [n for n, _ in manager.events(FAMILY, run_id)]
        if status != "waiting":
            break
        found = [p for n, p in manager.events(FAMILY, run_id) if n == "props_found"]
        assert found
        edits = [schemas.PropEdit(id=c.id, action="keep") for c in found[-1].props]
        manager.answer_run(FAMILY, run_id, user_id, schemas.AnswerRequest(kind="props", props=edits))

    async def replay() -> list:
        return [e async for e in manager.stream_run(FAMILY, run_id)]

    events = asyncio.run(asyncio.wait_for(replay(), WAIT))
    names = [n for n, _ in events]
    assert names[-1] == "done" and "error" not in names, [p for n, p in events if n == "error"]
    return events[-1][1].memory_id


def _tellings(repo: MongoRepo, memory_id: str) -> list[dict]:
    doc = repo.db.memories.find_one({"_id": memory_id, "familyId": FAMILY})
    assert doc is not None
    return doc["tellings"]


def test_audio_id_and_channel_are_saved_on_a_first_and_a_second_telling(studio):
    repo, manager = studio
    mom_audio = repo.put_media(FAMILY, b"mom voice note", "audio/mp4", kind="audio")
    dad_audio = repo.put_media(FAMILY, b"dad voice note", "audio/x-caf", kind="audio")

    memory_id = _drive(manager, "user_mom", "mom", schemas.SubmitMemoryFields(
        text=MOM_TEXT, year=1994, participant_ids=["dad"], audio_id=mom_audio, channel="voice"))
    first = _tellings(repo, memory_id)
    assert [(t["authorId"], t.get("audioId"), t.get("channel")) for t in first] == [("mom", mom_audio, "voice")]

    second = _drive(manager, "user_dad", "dad", schemas.SubmitMemoryFields(
        text=DAD_TEXT, year=1994, memory_id=memory_id, audio_id=dad_audio, channel="voice"))
    assert second == memory_id
    both = _tellings(repo, memory_id)
    assert [(t["authorId"], t.get("audioId"), t.get("channel")) for t in both] == [
        ("mom", mom_audio, "voice"), ("dad", dad_audio, "voice")]

    # And through the API contract, as the web reads it.
    memory = repo.get_memory(FAMILY, memory_id)
    assert memory is not None
    dumped = memory.model_dump(mode="json", by_alias=True, exclude_none=True)["tellings"]
    assert [(t.get("audioId"), t.get("channel")) for t in dumped] == [(mom_audio, "voice"), (dad_audio, "voice")]


def test_a_text_telling_saves_its_channel_without_an_audio_key(studio):
    repo, manager = studio
    memory_id = _drive(manager, "user_mom", "mom", schemas.SubmitMemoryFields(
        text=MOM_TEXT, year=1994, participant_ids=["dad"], channel="text"))
    [telling] = _tellings(repo, memory_id)
    assert telling["channel"] == "text" and "audioId" not in telling


def test_a_web_submission_saves_the_same_telling_document_as_before(studio):
    repo, manager = studio
    memory_id = _drive(manager, "user_mom", "mom",
                       schemas.SubmitMemoryFields(text=MOM_TEXT, year=1994, participant_ids=["dad"]))
    second = _drive(manager, "user_dad", "dad",
                    schemas.SubmitMemoryFields(text=DAD_TEXT, year=1994, memory_id=memory_id))
    assert second == memory_id
    tellings = _tellings(repo, memory_id)
    assert [t["authorId"] for t in tellings] == ["mom", "dad"]
    for telling in tellings:
        assert set(telling) == WEB_TELLING_KEYS, telling  # no audioId, no channel, not even null
    # The run's own record of the form round-trips too (restore after a restart).
    assert schemas.SubmitMemoryFields(text="x", year=1).model_dump(by_alias=True, exclude_none=True) == {
        "text": "x", "year": 1, "participantIds": []}


@pytest.mark.parametrize("which", ["other_family", "unknown", "photo", "malformed"])
def test_an_audio_id_that_is_not_this_familys_recording_is_refused(studio, which):
    repo, manager = studio
    audio = {
        "other_family": lambda: db_media.put_media(repo.db, OTHER, b"their voice note", content_type="audio/mp4",
                                                   kind="audio"),
        "unknown": lambda: "media/" + "0" * 24,
        "photo": lambda: repo.put_media(FAMILY, b"\x89PNG\r\n\x1a\n" + b"\x00" * 16, "image/png", kind="photo"),
        "malformed": lambda: "not-a-media-id",
    }[which]()
    before = repo.db.memories.count_documents({})
    with pytest.raises(ValueError, match="^Audio is not available in this family$"):
        manager.start_run(FAMILY, "user_mom", "mom",
                          schemas.SubmitMemoryFields(text=MOM_TEXT, year=1994, audio_id=audio, channel="voice"), [])
    assert repo.db.memories.count_documents({}) == before


def test_channel_is_one_of_the_three_values():
    with pytest.raises(ValueError):
        schemas.SubmitMemoryFields(text="x", year=1, channel="email")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        schemas.Telling(author_id="a", text="x", channel="sms")  # type: ignore[arg-type]
    assert schemas.Telling.model_validate({"authorId": "a", "text": "x", "audioId": "m1", "channel": "voice"}).audio_id == "m1"
