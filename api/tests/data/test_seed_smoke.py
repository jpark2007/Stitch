"""seed/smoke.py: the add-a-memory smoke CLI, driven offline through main.create_app().

STITCH_FAKE_DB=1 gives the seeded in-memory demo family and STITCH_AGENT=fake scripted runs
(seed/fake_runs.py), with STITCH_FAKE_RUN_STEP=0 so a run finishes as soon as it is
answered. FastAPI's TestClient is an httpx.Client, so run_smoke drives it exactly as it
drives a real server. No MongoDB, no keys, no network.
"""

from __future__ import annotations

import dataclasses
import pathlib
from collections.abc import AsyncIterator, Iterator

import pytest
from fastapi.testclient import TestClient

import agent.runs
import main
import routes.memories
import schemas
from db import client as db_client
from seed import demo, smoke
from seed.smoke import SmokeOptions, SmokeResult, parse_sse, run_smoke

WRONG_PASSWORD = "not-the-password-8472"


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("STITCH_FAKE_DB", "1")
    monkeypatch.setenv("STITCH_AGENT", "fake")
    monkeypatch.setenv("STITCH_FAKE_RUN_STEP", "0")
    monkeypatch.delenv("STITCH_CORS_ORIGINS", raising=False)
    monkeypatch.setattr(routes.memories, "HEARTBEAT_SECONDS", 0.05)
    db_client.set_db(None)
    try:
        with TestClient(main.create_app()) as c:
            yield c
    finally:
        agent.runs.configure(None)
        db_client.set_db(None)


def _opts(**overrides: object) -> SmokeOptions:
    values: dict[str, object] = {
        "base_url": "http://testserver",
        "email": "david@demo.stitch",
        "password": demo.DEMO_PASSWORD,
        "family_id": demo.DEMO_FAMILY_ID,
        "text": smoke.DEFAULT_TEXT,
        "year": smoke.DEFAULT_YEAR,
        "question": None,
        "photos": [],
        "answer": smoke.DEFAULT_ANSWER,
        "timeout": 30.0,
    }
    values.update(overrides)
    return SmokeOptions(**values)  # type: ignore[arg-type]


def _run(client: TestClient, opts: SmokeOptions) -> tuple[SmokeResult, list[str]]:
    lines: list[str] = []
    return run_smoke(client, opts, log=lines.append), lines


# ------------------------------------------------------------------------------ full run


def test_full_scripted_run_is_ok(api: TestClient) -> None:
    result, lines = _run(api, _opts())

    assert result.ok, result.message
    assert result.run_id and result.memory_id
    assert result.events[-1] == "done"
    for name in ("plan", "question", "props_found", "scene_ready", "done"):
        assert name in result.events
    assert result.events.index("question") < result.events.index("props_found") < result.events.index("done")
    assert "answered question (answer)" in "\n".join(lines)
    assert "answered props_found (props)" in "\n".join(lines)

    # The answers reached the run: the reply is kept in the telling, the prop was kept.
    res = api.get(f"/families/{demo.DEMO_FAMILY_ID}/memories/{result.memory_id}")
    found = schemas.MemoryResponse.model_validate(res.json())
    assert found.memory.tellings[0].author_id == "dad"
    assert smoke.DEFAULT_ANSWER in found.memory.tellings[0].text
    assert found.memory.year == smoke.DEFAULT_YEAR
    assert found.memory.prop_ids, "an approve-all review keeps the prop"
    assert len(found.memory.tellings[0].photo_ids) == 1, "one generated PNG"

    for line in lines:
        assert demo.DEMO_PASSWORD not in line
        assert "session" not in line.lower()


def test_photos_and_question_from_options(api: TestClient, tmp_path: pathlib.Path) -> None:
    photos = []
    for i in range(2):
        path = tmp_path / f"p{i}.png"
        path.write_bytes(smoke._png())
        photos.append(path)
    result, _ = _run(api, _opts(photos=photos, question="What did you sing?", year=1975))
    assert result.ok, result.message
    memory = api.get(f"/families/{demo.DEMO_FAMILY_ID}/memories/{result.memory_id}").json()["memory"]
    assert len(memory["tellings"][0]["photoIds"]) == 2
    assert memory["tellings"][0]["question"] == "What did you sing?"
    assert memory["year"] == 1975


def test_missing_photo_fails_before_submit(api: TestClient, tmp_path: pathlib.Path) -> None:
    result, _ = _run(api, _opts(photos=[tmp_path / "nope.png"]))
    assert not result.ok
    assert result.message.startswith("photo:")
    assert result.run_id is None


# ------------------------------------------------------------------------------ second telling

SAM = next(a for a in demo.DEMO_ACCOUNTS if a.person_id == "sam")
SAM_MOMENT = "demo_lily_graduation"
SECOND_TEXT = "I saw the whole thing from the third row. She caught her hem and bowed! I still tease her."


def _sams_question() -> str:
    return next(text for pid, text, mid in demo.DEMO_QUESTIONS if pid == SAM.person_id and mid == SAM_MOMENT)


def test_second_telling_smoke_over_a_seeded_moment(api: TestClient) -> None:
    """--memory-id: Sam adds his version of Lily's graduation, where he has an open question."""
    question = _sams_question()
    assert api.post("/auth/login", json={"email": SAM.email, "password": demo.DEMO_PASSWORD}).status_code == 200
    before = schemas.Memory.model_validate(
        api.get(f"/families/{demo.DEMO_FAMILY_ID}/memories/{SAM_MOMENT}").json()["memory"]
    )
    assert SAM.person_id not in {t.author_id for t in before.tellings}

    result, lines = _run(api, _opts(email=SAM.email, memory_id=SAM_MOMENT, text=SECOND_TEXT, question=question))

    assert result.ok, result.message
    assert result.memory_id == SAM_MOMENT
    assert result.events[-2:] == ["scene_ready", "done"]
    assert "question" not in result.events and "props_found" not in result.events
    log = "\n".join(lines)
    assert f"moment {before.title!r} ({before.year}), version {before.version}" in log
    assert f"version {before.version} -> {before.version + 1}" in log
    assert result.message == f"memory {SAM_MOMENT} has sam's version and is in sam's book"

    after = schemas.MemoryResponse.model_validate(
        api.get(f"/families/{demo.DEMO_FAMILY_ID}/memories/{SAM_MOMENT}").json()
    ).memory
    [mine] = [t for t in after.tellings if t.author_id == SAM.person_id]
    assert (mine.text, mine.question, len(mine.photo_ids)) == (SECOND_TEXT, question, 1)
    assert after.version == before.version + 1
    assert after.year == before.year  # the moment's own year was sent, not --year
    assert after.timeline is not None and after.timeline.narrations is not None
    track = after.timeline.narrations[SAM.person_id]
    assert [line.text for line in track.lines] == [
        "I saw the whole thing from the third row.", "She caught her hem and bowed!", "I still tease her."
    ]
    assert after.timeline.default_narrator == before.timeline.default_narrator  # type: ignore[union-attr]
    open_now = api.get(f"/families/{demo.DEMO_FAMILY_ID}/questions").json()["questions"]
    assert not [q for q in open_now if q["memoryId"] == SAM_MOMENT], "Sam's question is answered"


def test_second_telling_of_an_unknown_moment_fails_before_submit(api: TestClient) -> None:
    result, _ = _run(api, _opts(email=SAM.email, memory_id="no_such_moment"))
    assert not result.ok
    assert result.message.startswith("moment: GET memory no_such_moment: HTTP 404")
    assert result.run_id is None and result.events == []


def _scripted_run(monkeypatch: pytest.MonkeyPatch, *events: tuple[str, schemas.Model]) -> None:
    """Replace the run manager's start and stream: no real run happens, these events are sent."""
    manager = agent.runs.get_manager()

    def start_run(*_args: object) -> str:
        return "scripted-run"

    def stream_run(family_id: str, run_id: str) -> AsyncIterator[tuple[str, schemas.Model]]:
        async def gen() -> AsyncIterator[tuple[str, schemas.Model]]:
            for event in events:
                yield event

        return gen()

    monkeypatch.setattr(manager, "start_run", start_run)
    monkeypatch.setattr(manager, "stream_run", stream_run)


def test_second_telling_that_adds_no_track_fails(api: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """A run that says done but never added Sam's telling fails check 6, by name."""
    _scripted_run(monkeypatch, ("done", schemas.DoneEvent(memory_id=SAM_MOMENT)))
    result, _ = _run(api, _opts(email=SAM.email, memory_id=SAM_MOMENT))
    assert not result.ok
    assert result.message == f"memory: {SAM_MOMENT} has no telling by sam"


def test_second_telling_done_on_another_memory_fails(api: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    other = demo.HYMN_MEMORY_ID
    _scripted_run(monkeypatch, ("done", schemas.DoneEvent(memory_id=other)))
    result, _ = _run(api, _opts(email=SAM.email, memory_id=SAM_MOMENT))
    assert not result.ok
    assert result.message == f"stream: done names memory {other}, not the moment {SAM_MOMENT}"


def test_check_second_telling_needs_lines_and_a_new_version() -> None:
    sample = demo.load_sample().memory
    teller = sample.tellings[0].author_id
    with pytest.raises(smoke._Fail, match="still at version"):
        smoke._check_second_telling(sample, sample, teller)
    bumped = sample.model_copy(update={"version": sample.version + 1}, deep=True)
    smoke._check_second_telling(sample, bumped, teller)
    silent = bumped.model_copy(deep=True)
    assert silent.timeline is not None and silent.timeline.narrations is not None
    silent.timeline.narrations[teller].lines = []
    with pytest.raises(smoke._Fail, match="has no lines"):
        smoke._check_second_telling(sample, silent, teller)
    del silent.timeline.narrations[teller]
    with pytest.raises(smoke._Fail, match="no narration track"):
        smoke._check_second_telling(sample, silent, teller)


# ------------------------------------------------------------------------------ failures


def test_wrong_password_fails_without_leaking_it(api: TestClient) -> None:
    result, lines = _run(api, _opts(password=WRONG_PASSWORD))
    assert not result.ok
    assert result.message.startswith("login:")
    assert "david@demo.stitch" in result.message
    assert result.run_id is None and result.events == []
    for text in [result.message, *lines]:
        assert WRONG_PASSWORD not in text
        assert demo.DEMO_PASSWORD not in text


def test_unknown_family_fails(api: TestClient) -> None:
    result, _ = _run(api, _opts(family_id="no_such_family"))
    assert not result.ok
    assert result.message.startswith("membership:")
    assert "no_such_family" in result.message
    assert result.run_id is None


def test_error_event_fails_with_its_message(api: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    manager = agent.runs.get_manager()

    def stream_run(family_id: str, run_id: str) -> AsyncIterator[tuple[str, schemas.Model]]:
        async def events() -> AsyncIterator[tuple[str, schemas.Model]]:
            yield "plan", schemas.PlanEvent(todos=[schemas.PlanItem(text="Read the telling")])
            yield "error", schemas.ErrorEvent(message="The director dropped the script.")

        return events()

    monkeypatch.setattr(manager, "stream_run", stream_run)
    result, lines = _run(api, _opts())
    assert not result.ok
    assert result.run_id
    assert result.events == ["plan", "error"]
    assert "The director dropped the script." in result.message
    assert any("event error: The director dropped the script." in line for line in lines)


def test_answer_rejected_fails_with_detail(api: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    manager = agent.runs.get_manager()

    def answer_run(*_args: object) -> None:
        raise ValueError("That answer does not fit")

    monkeypatch.setattr(manager, "answer_run", answer_run)
    result, _ = _run(api, _opts())
    assert not result.ok
    assert result.message == "answer to question: HTTP 409 That answer does not fit"


def test_timeout_stops_the_stream(api: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    manager = agent.runs.get_manager()
    monkeypatch.setattr(manager, "answer_run", lambda *_args: None)  # never resumes the run
    result, _ = _run(api, _opts(timeout=0.3))
    assert not result.ok
    assert result.message == "timeout: no done event within 0.3s"
    assert "question" in result.events and "done" not in result.events


def test_dropped_stream_is_reopened_and_replay_skipped(api: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    manager = agent.runs.get_manager()
    real_stream = manager.stream_run
    calls = {"n": 0}

    def flaky(family_id: str, run_id: str) -> AsyncIterator[tuple[str, schemas.Model]]:
        calls["n"] += 1
        source = real_stream(family_id, run_id)
        if calls["n"] > 1:
            return source

        async def first_two() -> AsyncIterator[tuple[str, schemas.Model]]:
            count = 0
            async for item in source:
                yield item
                count += 1
                if count == 2:
                    return  # the stream ends early, as if the connection dropped

        return first_two()

    monkeypatch.setattr(manager, "stream_run", flaky)
    result, lines = _run(api, _opts())
    assert result.ok, result.message
    assert calls["n"] == 2
    assert result.events.count("plan") >= 1 and result.events.count("question") == 1
    assert result.events[:2] == ["plan", "subagent_start"]
    assert any("reopening the stream" in line for line in lines)


# ------------------------------------------------------------------------------ parse_sse


def test_parse_sse_skips_ping_comments() -> None:
    lines = [": ping", "", "event: plan", 'data: {"todos": []}', "", ": ping", ""]
    assert list(parse_sse(lines)) == [("plan", {"todos": []})]


def test_parse_sse_joins_multiline_data() -> None:
    lines = ["event: question", 'data: {"text":', 'data: "Who was there?"}', ""]
    assert list(parse_sse(lines)) == [("question", {"text": "Who was there?"})]


def test_parse_sse_skips_frame_without_data() -> None:
    lines = ["event: plan", "", "event: done", 'data: {"memoryId": "m1"}', ""]
    assert list(parse_sse(lines)) == [("done", {"memoryId": "m1"})]


def test_parse_sse_yields_trailing_frame_without_blank_line() -> None:
    lines = ["event: error", 'data: {"message": "boom"}']
    assert list(parse_sse(lines)) == [("error", {"message": "boom"})]


def test_parse_sse_details() -> None:
    # No space after the colon, CRLF endings, id/retry ignored, unnamed frame is "message".
    lines = ["id: 7", "retry: 1000", "event:done\r", 'data:{"memoryId":"m"}\r', "\r", 'data: {"a": 1}', ""]
    assert list(parse_sse(lines)) == [("done", {"memoryId": "m"}), ("message", {"a": 1})]
    with pytest.raises(ValueError):
        list(parse_sse(["event: plan", "data: not json", ""]))


# ------------------------------------------------------------------------------ CLI


def test_main_unreachable_returns_2(capsys: pytest.CaptureFixture[str]) -> None:
    assert smoke.main(["--base-url", "http://127.0.0.1:9", "--timeout", "5"]) == 2
    out = capsys.readouterr()
    assert "Cannot reach http://127.0.0.1:9: is the API running? (see seed/README.md)" in out.err
    assert demo.DEMO_PASSWORD not in out.out + out.err


def test_main_default_options() -> None:
    args = smoke._parser().parse_args([])
    opts = smoke._options(args)
    assert opts.base_url == "http://localhost:8000"
    assert opts.email == "david@demo.stitch"
    assert opts.password == demo.DEMO_PASSWORD
    assert opts.family_id == demo.DEMO_FAMILY_ID
    assert opts.year == 1991
    assert opts.answer == "It was a warm evening and everyone was singing."
    assert opts.timeout == 900.0
    assert opts.photos == [] and opts.question is None
    assert opts.memory_id is None
    assert demo.DEMO_PASSWORD not in smoke._parser().format_help()


def test_main_memory_id_option() -> None:
    opts = smoke._options(smoke._parser().parse_args(["--memory-id", "demo_lily_graduation"]))
    assert opts.memory_id == "demo_lily_graduation"
    assert "--memory-id" in smoke._parser().format_help()
    assert [f.name for f in dataclasses.fields(SmokeOptions)][-1] == "memory_id"


def test_main_ok_and_failed_summary(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    outcomes = iter([
        SmokeResult(ok=True, run_id="r1", memory_id="m1", events=["plan", "done"], message="fine"),
        SmokeResult(ok=False, run_id="r2", memory_id=None, events=["error"], message="run: error event: x"),
    ])
    monkeypatch.setattr(smoke, "run_smoke", lambda _client, _opts, log: next(outcomes))
    assert smoke.main(["--quiet"]) == 0
    assert capsys.readouterr().out.strip() == "smoke OK: run r1, memory m1, 2 events (plan, done)"
    assert smoke.main(["--quiet"]) == 1
    assert capsys.readouterr().out.strip() == "smoke FAILED: run: error event: x (run r2, events: error)"
