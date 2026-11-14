"""'Hear Mom's version' offline: second tellings over the seeded demo moments, end to end.

Driven through `main.create_app()` with STITCH_FAKE_DB=1 (the seeded in-memory demo family),
STITCH_AGENT=fake (seed/fake_runs.py) and STITCH_FAKE_RUN_STEP=0. No MongoDB, no keys, no
network.

For every memory-linked entry of seed.demo.DEMO_QUESTIONS (read from seed.demo, never
hardcoded here), the asked person adds their telling to that moment: over HTTP for a demo
account holder (log in, submit with memoryId and the question, stream to done), otherwise
through the installed ScriptedRunManager. Then the stored memory must carry a real narration
track for them (one line per sentence, word timings in timeline time, no overlaps, cues on
their actions), the default narrator and every other teller untouched (audio included), the
version up by one, a timeline that is exactly the compiler's output, and the question closed.

The edge cases (long tellings, retelling, the default narrator's retelling on a multi-shot
scene, text with no spoken word, photos) run the ScriptedRunManager on the seeded demo
family in mongomock directly.
"""

from __future__ import annotations

import asyncio
import io
import itertools
import time
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image

import agent.runs
import main
import routes.memories
import schemas
from db import client as db_client
from db import media as media_db
from db import memories as memories_db
from screenplay import narration as narration_timing
from screenplay.schema import NarrationLine, NarrationTrack, Screenplay, Shot, Timeline
from seed import demo, fake_runs
from seed.fake_runs import ScriptedRunManager, compile_scene, split_sentences
from seed.smoke import parse_sse

FID = demo.DEMO_FAMILY_ID
ACCOUNTS = {a.person_id: a for a in demo.DEMO_ACCOUNTS}
ADMIN = demo.DEMO_ACCOUNTS[0]
MEMORY_QUESTIONS = [(pid, text, mid) for pid, text, mid in demo.DEMO_QUESTIONS if mid is not None]
DEADLINE = 20.0
EPS = 1e-6


def _telling_text(person_id: str) -> str:
    """A four-sentence telling with a quote and a paragraph break (split_sentences cases)."""
    return (
        f"I remember it my own way, as {person_id} would. Everyone was talking at once! "
        'Somebody said "look at that." and we all turned around.\n\n'
        "It is still one of my favorite days."
    )


# ------------------------------------------------------------------------------ fixtures


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("STITCH_FAKE_DB", "1")
    monkeypatch.setenv("STITCH_AGENT", "fake")
    monkeypatch.setenv("STITCH_FAKE_RUN_STEP", "0")
    monkeypatch.delenv("STITCH_CORS_ORIGINS", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(routes.memories, "HEARTBEAT_SECONDS", 0.05)
    db_client.set_db(None)
    try:
        with TestClient(main.create_app()) as c:
            assert isinstance(agent.runs.get_manager(), ScriptedRunManager)
            yield c
    finally:
        agent.runs.configure(None)
        db_client.set_db(None)


def _login(client: TestClient, email: str) -> None:
    res = client.post("/auth/login", json={"email": email, "password": demo.DEMO_PASSWORD})
    assert res.status_code == 200, res.text


def _get_memory(client: TestClient, memory_id: str) -> schemas.Memory:
    res = client.get(f"/families/{FID}/memories/{memory_id}")
    assert res.status_code == 200, res.text
    return schemas.MemoryResponse.model_validate(res.json()).memory


def _answer_for(name: str, data: dict) -> dict | None:
    if name == "question":
        return {"kind": "answer", "text": "Just us."}
    if name == "props_found":
        return {"kind": "props", "props": [{"id": p["id"], "action": "keep"} for p in data["props"]]}
    if name == "merge":
        return {"kind": "merge", "approve": False}
    return None


def _tell_over_http(client: TestClient, memory: schemas.Memory, text: str, question: str) -> list[tuple[str, dict]]:
    """Submit a telling for the logged-in account and stream its run to the end."""
    res = client.post(
        f"/families/{FID}/memories",
        data={"text": text, "year": str(memory.year), "question": question, "memoryId": memory.id},
    )
    assert res.status_code == 202, res.text
    submitted = schemas.SubmitMemoryResponse.model_validate(res.json())
    assert submitted.memory_id == memory.id
    events: list[tuple[str, dict]] = []
    deadline = time.monotonic() + DEADLINE
    with client.stream("GET", f"/families/{FID}/runs/{submitted.run_id}/stream") as stream:
        assert stream.status_code == 200
        for name, data in parse_sse(stream.iter_lines()):
            assert time.monotonic() < deadline, "the run did not finish in time"
            assert name in schemas.RUN_EVENTS, name
            schemas.RUN_EVENTS[name].model_validate(data)
            events.append((name, data))
            if name in ("done", "error"):
                break
            body = _answer_for(name, data)
            if body is not None:
                reply = client.post(f"/families/{FID}/runs/{submitted.run_id}/answer", json=body)
                assert reply.status_code == 204, reply.text
    return events


def _tell_directly(person_id: str, memory: schemas.Memory, text: str, question: str) -> list[tuple[str, Any]]:
    """Start a scripted run for someone with no account, answer whatever it asks, return its events."""
    manager = agent.runs.get_manager()
    assert isinstance(manager, ScriptedRunManager)
    fields = schemas.SubmitMemoryFields(text=text, year=memory.year, question=question, memory_id=memory.id)
    run_id = manager.start_run(FID, ADMIN.user_id, person_id, fields, [])
    deadline = time.monotonic() + DEADLINE
    while True:
        status = manager.wait(FID, run_id, timeout=DEADLINE)
        assert time.monotonic() < deadline, "the run did not finish in time"
        if status in ("done", "error"):
            break
        assert status == "waiting"
        name, payload = manager.events(FID, run_id)[-1]
        body = _answer_for(name, payload.model_dump(mode="json", by_alias=True))
        assert body is not None, name
        manager.answer_run(FID, run_id, ADMIN.user_id, schemas.AnswerRequest.model_validate(body))
    return manager.events(FID, run_id)


def _with_fake_audio(memory_id: str) -> schemas.Memory:
    """Give every narration track of the stored timeline an audio ref, as if TTS had run.

    Written straight to the stored document (no version bump): it stands in for the real
    pipeline's narration audio, so keep_audio has something to keep.
    """
    db = db_client.get_db()
    memory = memories_db.get_memory(db, FID, memory_id)
    assert memory is not None and memory.timeline is not None
    tl = memory.timeline.model_copy(deep=True)
    for author, track in (tl.narrations or {}).items():
        track.audio = f"media/audio_{author}"
    tl.audio = f"media/audio_{tl.default_narrator}"
    db["memories"].update_one(
        {"_id": memory_id, "familyId": FID},
        {"$set": {"timeline": tl.model_dump(mode="json", by_alias=True, exclude_none=True)}},
    )
    stored = memories_db.get_memory(db, FID, memory_id)
    assert stored is not None
    return stored


# ------------------------------------------------------------------------------ checks


def _without_audio(tl: Timeline) -> Timeline:
    out = tl.model_copy(deep=True)
    out.audio = None
    for track in (out.narrations or {}).values():
        track.audio = None
    return out


def _lines_by_shot(sp: Screenplay, author: str) -> list[tuple[Shot, list[NarrationLine]]]:
    """`author`'s screenplay lines, shot by shot, in shot order."""
    default = narration_timing.default_author(sp)
    if author == default:
        return [(shot, list(shot.narration)) for shot in sp.shots]
    by_shot = (sp.narrations or {}).get(author, {})
    return [(shot, list(by_shot.get(shot.id, []))) for shot in sp.shots]


def _placed(memory: schemas.Memory, author: str) -> list[tuple[Shot, float, NarrationLine]]:
    """(screenplay shot, its timeline action_start, timeline line) for each of `author`'s lines.

    The timeline's track lists every line in shot order, so the screenplay's lines per shot
    say which shot each timeline line belongs to.
    """
    sp, tl = memory.screenplay, memory.timeline
    assert sp is not None and tl is not None and tl.narrations is not None
    track = tl.narrations[author]
    starts = {s.id: s.action_start for s in tl.shots}
    out: list[tuple[Shot, float, NarrationLine]] = []
    lines = iter(track.lines)
    for shot, written in _lines_by_shot(sp, author):
        for _ in written:
            out.append((shot, starts[shot.id], next(lines)))
    assert next(lines, None) is None, f"{author}'s track has lines no shot accounts for"
    return out


def _relative(memory: schemas.Memory, author: str) -> list[tuple]:
    """`author`'s lines with every time relative to its shot's action start (shared-action view)."""
    return [
        (
            shot.id, line.text, line.source, line.cue, line.cue_word,
            round(line.t - start, 6), [(w.word, round(w.t - start, 6)) for w in line.words or []],
        )
        for shot, start, line in _placed(memory, author)
    ]


def assert_well_timed(memory: schemas.Memory, author: str) -> None:
    """Every line and word sits inside the timeline, in order, and no line overlaps the one before;
    a cued line's cue word lands where the compiler put the cued action (CONTRACTS cue rules)."""
    sp, tl = memory.screenplay, memory.timeline
    assert sp is not None and tl is not None
    default = narration_timing.default_author(sp)
    said_by = -1.0
    for shot, start, line in _placed(memory, author):
        words = line.words or []
        times = [line.t, *(w.t for w in words)]
        assert all(b >= a - EPS for a, b in itertools.pairwise(times)), f"{line.text!r} runs backwards: {times}"
        assert all(-EPS <= t <= tl.duration + EPS for t in times), f"{line.text!r} is outside [0, {tl.duration}]"
        assert line.t >= said_by - EPS, f"{line.text!r} starts at {line.t} before the line before is said ({said_by})"
        if words:
            said_by = words[-1].t + narration_timing.WORD_SECONDS
        if line.cue is not None:
            idx = narration_timing.cue_action_index(shot, line.cue)
            retimed = narration_timing.retime_actions(shot, start, author=default)
            target = (line.cue_word or words[-1].word).casefold()
            word_t = next(w.t for w in words if w.word.casefold() == target)
            assert word_t == pytest.approx(start + retimed[idx], abs=EPS), (
                f"{line.text!r}: cue word {target!r} at {word_t}, action {line.cue!r} at {start + retimed[idx]}"
            )
    shot_end = {s.id: s.end for s in tl.shots}
    for shot, _start, line in _placed(memory, author):
        if line.words:
            assert line.words[-1].t + narration_timing.WORD_SECONDS <= shot_end[shot.id] + EPS, (
                f"{line.text!r} is still being said after its shot ends"
            )


def _absolute(track: NarrationTrack) -> list[tuple]:
    return [(line.text, line.t, [(w.word, w.t) for w in line.words or []]) for line in track.lines]


def assert_others_untouched(before: schemas.Memory, after: schemas.Memory, teller: str) -> None:
    """The default narrator, their captions and every other teller's track are kept, in shot
    time. Each one's audio (one clip from t=0) is kept while their lines are said at the same
    timeline times, and dropped (None, to be synthesized again) when a longer shot moved them."""
    tb, ta = before.timeline, after.timeline
    assert tb is not None and ta is not None and tb.narrations is not None and ta.narrations is not None
    assert ta.default_narrator == tb.default_narrator
    assert [c.text for c in ta.captions] == [c.text for c in tb.captions]
    assert ta.captions == ta.narrations[ta.default_narrator].captions
    for author, old in tb.narrations.items():
        if author == teller:
            continue
        assert author in ta.narrations, f"{author}'s track is gone"
        assert _relative(after, author) == _relative(before, author), f"{author}'s lines changed"
        new = ta.narrations[author]
        if _absolute(new) == _absolute(old):
            assert new.audio == old.audio, f"{author}'s audio was dropped, though their lines did not move"
        else:
            assert new.audio is None, f"{author}'s lines moved, but their old audio was kept"
        if author == ta.default_narrator:
            assert ta.audio == new.audio
    assert after.screenplay is not None and before.screenplay is not None
    assert after.screenplay.shots == before.screenplay.shots, "the shared action changed"
    for author, by_shot in (before.screenplay.narrations or {}).items():
        if author != teller:
            assert after.screenplay.narrations is not None and after.screenplay.narrations[author] == by_shot


def assert_is_the_compile(memory: schemas.Memory) -> None:
    """The stored screenplay recompiles, with zero errors, to the stored timeline (audio aside)."""
    assert memory.screenplay is not None and memory.timeline is not None
    result = compile_scene(db_client.get_db(), FID, memory.screenplay)
    assert result.errors == [], [e.message for e in result.errors]
    assert result.timeline is not None
    assert _without_audio(result.timeline) == _without_audio(memory.timeline)


def assert_track(memory: schemas.Memory, teller: str, text: str) -> None:
    tl = memory.timeline
    assert tl is not None and tl.narrations is not None
    assert teller in tl.narrations, f"no narration track for {teller}"
    track = tl.narrations[teller]
    assert track.author_id == teller
    assert [line.text for line in track.lines] == split_sentences(text)
    assert [c.text for c in track.captions] == [line.text for line in track.lines]
    assert all(line.words for line in track.lines)
    assert [line.source for line in track.lines] == [
        f"telling:{teller}:s{n}" for n in range(1, len(track.lines) + 1)
    ]
    assert_well_timed(memory, teller)


# ------------------------------------------------------------------------------ end to end


@pytest.mark.parametrize(
    ("person_id", "question", "memory_id"),
    MEMORY_QUESTIONS,
    ids=[f"{pid}-{mid}" for pid, _q, mid in MEMORY_QUESTIONS],
)
def test_every_asked_person_adds_their_version(
    api: TestClient, person_id: str, question: str, memory_id: str
) -> None:
    account = ACCOUNTS.get(person_id)
    _login(api, account.email if account else ADMIN.email)
    if account is not None:
        open_before = api.get(f"/families/{FID}/questions").json()["questions"]
        assert any(q["memoryId"] == memory_id and q["text"] == question for q in open_before)
    _with_fake_audio(memory_id)
    before = _get_memory(api, memory_id)
    assert person_id not in {t.author_id for t in before.tellings}, "the question asks someone who told it"
    text = _telling_text(person_id)

    if account is not None:
        events = _tell_over_http(api, before, text, question)
        assert events[-1] == ("done", {"memoryId": memory_id}), events[-1]
    else:
        raw = _tell_directly(person_id, before, text, question)
        assert raw[-1][0] == "done", raw[-1]
        assert raw[-1][1].memory_id == memory_id
        events = [(n, p.model_dump(mode="json", by_alias=True)) for n, p in raw]
    seq = [name for name, _ in events]
    assert "compile_error" not in seq and "error" not in seq
    assert seq[-2] == "scene_ready" and events[-2][1]["version"] == before.version + 1

    after = _get_memory(api, memory_id)
    mine = [t for t in after.tellings if t.author_id == person_id]
    assert len(mine) == 1 and mine[0].text == text and mine[0].question == question
    assert [t.author_id for t in after.tellings][: len(before.tellings)] == [t.author_id for t in before.tellings]
    assert person_id in after.participant_ids
    assert after.version == before.version + 1
    assert_track(after, person_id, text)
    assert after.timeline is not None and after.timeline.narrations[person_id].audio is None  # new words, no audio yet
    assert_others_untouched(before, after, person_id)
    assert_is_the_compile(after)

    if account is not None:
        _login(api, account.email)
        open_after = api.get(f"/families/{FID}/questions").json()["questions"]
        assert not [q for q in open_after if q["memoryId"] == memory_id], open_after
    stored = db_client.get_db()["questions"].find_one({"familyId": FID, "personId": person_id, "text": question})
    assert stored is not None and stored["status"] == "answered"


def test_every_memory_linked_question_is_covered() -> None:
    """The demo asks someone about at least one moment, and each one points at a seeded moment."""
    assert MEMORY_QUESTIONS
    assert {mid for *_x, mid in MEMORY_QUESTIONS} <= set(demo.DEMO_MEMORY_IDS)


# ------------------------------------------------------------------------------ edge cases

STEPLESS = 0.0


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
    mgr = ScriptedRunManager(db_getter=lambda: db, step_seconds=STEPLESS)
    yield mgr
    mgr.shutdown()


def _run(mgr: ScriptedRunManager, db, person_id: str, memory_id: str, text: str,
         photo_ids: list[str] | None = None) -> tuple[list[tuple[str, Any]], schemas.Memory]:
    memory = memories_db.get_memory(db, FID, memory_id)
    assert memory is not None
    fields = schemas.SubmitMemoryFields(text=text, year=memory.year, memory_id=memory_id)
    run_id = mgr.start_run(FID, ADMIN.user_id, person_id, fields, list(photo_ids or []))

    async def go() -> list[tuple[str, Any]]:
        return [e async for e in mgr.stream_run(FID, run_id)]

    events = asyncio.run(asyncio.wait_for(go(), DEADLINE))
    after = memories_db.get_memory(db, FID, memory_id)
    assert after is not None
    return events, after


def _one_shot_moments() -> list[str]:
    return [mid for mid in demo.DEMO_MEMORY_IDS if len(demo.load_screenplay(mid).shots) == 1]


def _newcomer(db, memory_id: str) -> str:
    """Someone in the demo tree who has not told this moment."""
    memory = memories_db.get_memory(db, FID, memory_id)
    assert memory is not None
    told = {t.author_id for t in memory.tellings}
    return next(pid for pid in demo.DEMO_PERSON_IDS if pid not in told)


LONG_TEXT = " ".join(
    f"This is sentence number {n} of a very long telling, and it goes on for a while." for n in range(1, 14)
)


@pytest.mark.parametrize("memory_id", _one_shot_moments())
def test_a_long_telling_over_a_one_shot_scene_extends_the_shot(db, manager, memory_id):
    """13 sentences over one shot: the lines follow each other without overlapping, and the shot
    (so the timeline) grows until the last word has been said. The action does not move."""
    assert len(split_sentences(LONG_TEXT)) >= 12
    teller = _newcomer(db, memory_id)
    before = _with_fake_audio(memory_id)
    events, after = _run(manager, db, teller, memory_id, LONG_TEXT)
    assert events[-1][0] == "done", events[-1]
    assert_track(after, teller, LONG_TEXT)
    assert after.timeline.duration > before.timeline.duration
    last = after.timeline.narrations[teller].lines[-1].words[-1].t
    assert last + narration_timing.WORD_SECONDS <= after.timeline.duration
    assert after.timeline.tracks == before.timeline.tracks  # the shared action stays put
    assert_others_untouched(before, after, teller)
    # Only the end of the one shot moved, so every other teller's clip still fits: kept.
    for author, track in before.timeline.narrations.items():
        assert after.timeline.narrations[author].audio == track.audio
    assert after.timeline.audio == before.timeline.audio
    assert_is_the_compile(after)


def _multi_shot_moments() -> list[str]:
    return [mid for mid in demo.DEMO_MEMORY_IDS if len(demo.load_screenplay(mid).shots) > 1]


@pytest.mark.parametrize("memory_id", _multi_shot_moments())
def test_a_long_telling_over_a_multi_shot_scene(db, manager, memory_id):
    """13 sentences spread over the shots: no overlaps, each shot grows to fit its lines, and the
    later shots start later. Every other teller keeps their lines in shot time; anyone whose
    lines moved in timeline time loses the old clip (it would play out of step)."""
    teller = _newcomer(db, memory_id)
    before = _with_fake_audio(memory_id)
    events, after = _run(manager, db, teller, memory_id, LONG_TEXT)
    assert events[-1][0] == "done", events[-1]
    assert_track(after, teller, LONG_TEXT)
    shots = after.screenplay.shots
    mine = after.screenplay.narrations[teller]
    assert all(mine.get(shot.id) for shot in shots), "every shot carries some of the telling"
    assert_others_untouched(before, after, teller)
    first_end = (before.timeline.shots[0].end, after.timeline.shots[0].end)
    if first_end[1] > first_end[0]:
        later = {s.id for s in shots[1:]}
        for author in before.timeline.narrations:
            if author == teller:
                continue
            in_later = any(shot.id in later and lines for shot, lines in _lines_by_shot(after.screenplay, author))
            if in_later:
                assert after.timeline.narrations[author].audio is None, author
    assert_is_the_compile(after)


def test_the_same_teller_telling_twice_replaces_their_track(db, manager):
    memory_id = "demo_green_sedan_trip"
    teller = _newcomer(db, memory_id)
    first = "The car was green. It smelled like peaches."
    second = "I remember the flat tire. Joseph swore at it. Then he laughed. We ate on a crate."
    _events, once = _run(manager, db, teller, memory_id, first)
    events, twice = _run(manager, db, teller, memory_id, second)
    assert events[-1][0] == "done", events[-1]
    assert twice.version == once.version + 1
    mine = [t for t in twice.tellings if t.author_id == teller]
    assert len(mine) == 1 and mine[0].text == second
    assert len(twice.tellings) == len(once.tellings)
    assert_track(twice, teller, second)
    assert_others_untouched(once, twice, teller)
    assert_is_the_compile(twice)


def test_the_default_narrators_retelling_of_a_multi_shot_scene_keeps_the_action(db, manager):
    memory_id = "demo_green_sedan_trip"
    before = memories_db.get_memory(db, FID, memory_id)
    assert before is not None and len(before.screenplay.shots) > 1
    default = before.timeline.default_narrator
    assert default == before.tellings[0].author_id
    text = "We drove to the coast. The tire went flat! Dad fixed it in his good trousers. We ate peaches."
    events, after = _run(manager, db, default, memory_id, text)
    assert events[-1][0] == "done", events[-1]
    sp, tl = after.screenplay, after.timeline
    assert [line.text for shot in sp.shots for line in shot.narration] == split_sentences(text)
    assert all(line.source.startswith(f"telling:{default}:") for shot in sp.shots for line in shot.narration)
    assert tl.default_narrator == default
    assert [c.text for c in tl.captions] == split_sentences(text) == [c.text for c in tl.narrations[default].captions]
    assert default not in (sp.narrations or {})
    # The action is pinned where the old narration put it, shot by shot.
    old_times = {s.id: narration_timing.retime_actions(s, 0.0, author=default) for s in before.screenplay.shots}
    new_times = {s.id: narration_timing.retime_actions(s, 0.0, author=default) for s in sp.shots}
    assert new_times.keys() == old_times.keys()
    for sid, times in old_times.items():
        assert new_times[sid] == pytest.approx(times, abs=EPS), sid
    assert_well_timed(after, default)
    # Every other teller keeps their words and their audio.
    for author, track in before.timeline.narrations.items():
        if author != default:
            assert [line.text for line in tl.narrations[author].lines] == [line.text for line in track.lines]
            assert _relative(after, author) == _relative(before, author)
    assert_is_the_compile(after)


@pytest.mark.parametrize("text", ["...", "❤️ \U0001f389", "?!"])
@pytest.mark.parametrize("who", ["newcomer", "default"])
def test_a_telling_with_no_spoken_word_keeps_one_silent_line(db, manager, text, who):
    """No speakable word: the telling is saved with ONE uncued line of the raw text, so the
    teller always has a track (never a telling with a silently missing narration)."""
    memory_id = "demo_sams_first_bike"
    before = memories_db.get_memory(db, FID, memory_id)
    teller = _newcomer(db, memory_id) if who == "newcomer" else before.timeline.default_narrator
    events, after = _run(manager, db, teller, memory_id, text)
    assert events[-1][0] == "done", events[-1]
    assert [t.text for t in after.tellings if t.author_id == teller] == [text]
    track = after.timeline.narrations[teller]
    assert [line.text for line in track.lines] == [text.strip()]
    [line] = track.lines
    assert line.cue is None and line.words == [] and line.source == f"telling:{teller}:s1"
    assert after.timeline.default_narrator == before.timeline.default_narrator
    assert_is_the_compile(after)


def test_a_blank_telling_errors_and_writes_nothing(db, manager):
    """Whitespace only never reaches a run through the route (422); the script refuses it too."""
    memory_id = "demo_sams_first_bike"
    before = memories_db.get_memory(db, FID, memory_id)
    events, after = _run(manager, db, _newcomer(db, memory_id), memory_id, "   \n ")
    assert events[-1][0] == "error"
    assert events[-1][1].message == fake_runs.TELLING_FAILED_MESSAGE
    assert after == before


def _png() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (8, 8), (200, 120, 60)).save(out, format="PNG")
    return out.getvalue()


def test_photos_on_a_second_telling_are_kept_on_the_telling(db, manager):
    memory_id = "demo_green_sedan_trip"  # its first telling carries the two seeded photos
    before = memories_db.get_memory(db, FID, memory_id)
    assert before is not None and any(t.photo_ids for t in before.tellings)
    teller = _newcomer(db, memory_id)
    photos = [media_db.put_media(db, FID, _png(), content_type="image/png", kind="photo") for _ in range(2)]
    events, after = _run(manager, db, teller, memory_id, "We all wore hats. Rosa's was the biggest.", photos)
    assert events[-1][0] == "done", events[-1]
    [mine] = [t for t in after.tellings if t.author_id == teller]
    assert mine.photo_ids == photos
    # Everyone else's photos stay on their own tellings.
    assert {t.author_id: t.photo_ids for t in after.tellings if t.author_id != teller} == {
        t.author_id: t.photo_ids for t in before.tellings
    }
    assert_is_the_compile(after)


def test_photos_over_http_reach_the_telling(api: TestClient):
    """The route stores the uploaded photos and the scripted run puts them on the telling."""
    pid, _question, memory_id = next((p, q, m) for p, q, m in MEMORY_QUESTIONS if p in ACCOUNTS)
    _login(api, ACCOUNTS[pid].email)
    memory = _get_memory(api, memory_id)
    res = api.post(
        f"/families/{FID}/memories",
        data={"text": "Here is my picture of it.", "year": str(memory.year), "memoryId": memory_id},
        files=[("photos", ("a.png", _png(), "image/png")), ("photos", ("b.png", _png(), "image/png"))],
    )
    assert res.status_code == 202, res.text
    run_id = res.json()["runId"]
    with api.stream("GET", f"/families/{FID}/runs/{run_id}/stream") as stream:
        names = [name for name, _ in parse_sse(stream.iter_lines())]
    assert names[-1] == "done", names
    after = _get_memory(api, memory_id)
    [mine] = [t for t in after.tellings if t.author_id == pid]
    assert len(mine.photo_ids) == 2
    for photo_id in mine.photo_ids:
        assert api.get(f"/families/{FID}/media/{photo_id}").status_code == 200
