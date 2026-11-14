"""agent/demo.py: the one-command studio run, offline.

The real (keyed) mode is never run here: only its missing-key exit, with sockets blocked.
"""

from __future__ import annotations

import io
import os
import socket
import subprocess
import sys
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from pydantic import BaseModel

import schemas
from agent import demo
from screenplay.schema import CompileError

REAL_ENV_FILES = demo.ENV_FILES  # before the autouse fixture points it at tmp

API_DIR = Path(__file__).resolve().parents[2]

REPRESENTATIVE: dict[str, BaseModel] = {
    "plan": schemas.PlanEvent(
        todos=[
            schemas.PlanItem(text="Check the story", status="completed"),
            schemas.PlanItem(text="Storyboard the shots", status="in_progress"),
            schemas.PlanItem(text="Stage the scene"),
        ]
    ),
    "subagent_start": schemas.SubagentStartEvent(name="director", task="Stage the scene\nand compile it"),
    "subagent_end": schemas.SubagentEndEvent(name="director", summary="compiled: yes"),
    "question": schemas.QuestionEvent(text="What were you carrying?"),
    "props_found": schemas.PropsFoundEvent(
        props=[
            schemas.PropChip(id="hymn_books", name="Hymn books", size="hand", reused=True, art="media/1"),
            schemas.PropChip(id="bench", name="Church bench", size="furniture"),
        ]
    ),
    "merge": schemas.MergeEvent(text="Is Uncle Joe the Joseph in your tree?", person_id="grandpa_joseph",
                                candidate_name="Joseph"),
    "compile_error": schemas.CompileErrorEvent(
        errors=[
            CompileError(code="unknown_mark", message='shot s1, t=0.5: unknown mark "nowhere"'),
            CompileError(code="not_holding", message="dad is not holding hymn_books"),
        ],
        round=2,
    ),
    "frames": schemas.FramesEvent(
        frames=[schemas.FrameShot(t=1.0, media_id="media/7"), schemas.FrameShot(t=2.5, media_id="media/8")],
        critique="verdict: pass",
    ),
    "scene_ready": schemas.SceneReadyEvent(memory_id="memory_9", version=3),
    "done": schemas.DoneEvent(memory_id="memory_9"),
    "error": schemas.ErrorEvent(message="the studio finished without saving a scene"),
}


# --------------------------------------------------------------------------- format_event


@pytest.fixture(autouse=True)
def _no_real_env_files(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """main() loads api/.env in real mode: point it at an empty tmp dir so no test reads real keys."""
    monkeypatch.setattr(demo, "ENV_FILES", (tmp_path / "none" / ".env",))


def test_representative_payloads_cover_every_run_event() -> None:
    assert set(REPRESENTATIVE) == set(schemas.RUN_EVENTS)
    for name, payload in REPRESENTATIVE.items():
        assert isinstance(payload, schemas.RUN_EVENTS[name])


@pytest.mark.parametrize("name", sorted(schemas.RUN_EVENTS))
def test_format_event_is_one_line_starting_with_the_name(name: str) -> None:
    line = demo.format_event(name, REPRESENTATIVE[name])
    assert line.startswith(f"{name}: ")
    assert "\n" not in line


def test_format_event_shows_what_matters() -> None:
    fmt = {name: demo.format_event(name, payload) for name, payload in REPRESENTATIVE.items()}
    assert "[x] Check the story" in fmt["plan"] and "[>] Storyboard the shots" in fmt["plan"]
    assert "[ ] Stage the scene" in fmt["plan"]
    assert fmt["subagent_start"] == "subagent_start: director: Stage the scene and compile it"
    assert fmt["subagent_end"] == "subagent_end: director: compiled: yes"
    assert "What were you carrying?" in fmt["question"]
    assert "hymn_books (Hymn books, hand, reused)" in fmt["props_found"]
    assert "bench (Church bench, furniture, placeholder art)" in fmt["props_found"]
    assert "Joseph" in fmt["merge"] and "grandpa_joseph" in fmt["merge"]
    assert "round 2" in fmt["compile_error"] and "unknown_mark, not_holding" in fmt["compile_error"]
    assert "2 frame(s)" in fmt["frames"] and "1s, 2.5s" in fmt["frames"]
    assert fmt["scene_ready"] == "scene_ready: memory memory_9 v3"
    assert fmt["done"] == "done: memory memory_9"
    assert "without saving a scene" in fmt["error"]


def test_format_event_clips_long_text() -> None:
    line = demo.format_event("subagent_start", schemas.SubagentStartEvent(name="x", task="word " * 200))
    assert len(line) < 220 and line.endswith("...")


# --------------------------------------------------------------------------- answers


def test_auto_answer_question() -> None:
    reply = demo.auto_answer("question", REPRESENTATIVE["question"])
    assert reply.kind == "answer" and reply.text and reply.text.strip()
    schemas.AnswerRequest.model_validate(reply.model_dump())


def test_auto_answer_props_keeps_every_chip() -> None:
    reply = demo.auto_answer("props_found", REPRESENTATIVE["props_found"])
    assert reply.kind == "props"
    assert [(e.id, e.action) for e in reply.props or []] == [("hymn_books", "keep"), ("bench", "keep")]
    schemas.AnswerRequest.model_validate(reply.model_dump())


def test_auto_answer_merge() -> None:
    reply = demo.auto_answer("merge", REPRESENTATIVE["merge"])
    assert reply.kind == "merge" and reply.approve is True
    schemas.AnswerRequest.model_validate(reply.model_dump())


def test_auto_answer_refuses_events_that_need_no_answer() -> None:
    with pytest.raises(ValueError):
        demo.auto_answer("done", REPRESENTATIVE["done"])


def test_auto_answers_fit_the_pending_tools() -> None:
    """The run accepts them: answer_to_resume maps each to a decision for its gated tool."""
    from agent.tools import ask

    def pending(name: str, args: dict) -> dict:
        return {"action_requests": [{"name": name, "args": args}], "review_configs": []}

    cases = {
        "question": (ask.ASK_TELLER, {"question": "What were you carrying?"}),
        "props_found": (ask.CONFIRM_PROPS, {"props": []}),
        "merge": (ask.MERGE_PEOPLE, {"name": "Uncle Joe", "person_id": "grandpa_joseph", "candidate_name": "Joseph",
                                     "question": "Same?"}),
    }
    for event, (tool, args) in cases.items():
        resume = ask.answer_to_resume(pending(tool, args), demo.auto_answer(event, REPRESENTATIVE[event]))
        assert resume["decisions"][0]["type"] in {"respond", "edit"}


def _reader(lines: list[str]):
    queue = list(lines)
    return lambda _prompt: queue.pop(0)


def test_stdin_answer_question_reasks_on_empty() -> None:
    out = io.StringIO()
    reply = demo.stdin_answer("question", REPRESENTATIVE["question"], read=_reader(["", "  sunny  "]), out=out)
    assert reply == schemas.AnswerRequest(kind="answer", text="sunny")
    assert "needs some text" in out.getvalue()


def test_stdin_answer_props_edits_and_keeps_the_rest() -> None:
    out = io.StringIO()
    reply = demo.stdin_answer(
        "props_found",
        REPRESENTATIVE["props_found"],
        read=_reader(["frobnicate", "rename hymn_books=Hymnals; remove bench"]),
        out=out,
    )
    assert reply.kind == "props"
    assert [(e.id, e.action, e.name) for e in reply.props or []] == [
        ("hymn_books", "rename", "Hymnals"),
        ("bench", "remove", None),
    ]
    assert "did not understand" in out.getvalue()


def test_stdin_answer_props_enter_keeps_all_and_merge_yes_no() -> None:
    keep = demo.stdin_answer("props_found", REPRESENTATIVE["props_found"], read=_reader([""]), out=io.StringIO())
    assert [e.action for e in keep.props or []] == ["keep", "keep"]
    no = demo.stdin_answer("merge", REPRESENTATIVE["merge"], read=_reader(["maybe", "n"]), out=io.StringIO())
    assert no == schemas.AnswerRequest(kind="merge", approve=False)


# --------------------------------------------------------------------------- run_demo


class StubManager:
    """start_run / stream_run / answer_run, yielding question -> props_found -> scene_ready -> done."""

    def __init__(self) -> None:
        self.started: list[tuple] = []
        self.answers: list[tuple[str, str, str, schemas.AnswerRequest]] = []

    def start_run(self, family_id, user_id, person_id, fields, photo_ids) -> str:
        self.started.append((family_id, user_id, person_id, fields, list(photo_ids)))
        return "run_1"

    async def stream_run(self, family_id: str, run_id: str) -> AsyncIterator[tuple[str, BaseModel]]:
        yield "plan", REPRESENTATIVE["plan"]
        yield "question", REPRESENTATIVE["question"]
        assert len(self.answers) == 1, "the question was answered before the stream went on"
        yield "props_found", REPRESENTATIVE["props_found"]
        assert len(self.answers) == 2
        yield "scene_ready", schemas.SceneReadyEvent(memory_id="memory_42", version=1)
        yield "done", schemas.DoneEvent(memory_id="memory_42")
        yield "error", schemas.ErrorEvent(message="never read: the demo stops at done")

    def answer_run(self, family_id: str, run_id: str, user_id: str, answer: schemas.AnswerRequest) -> None:
        self.answers.append((family_id, run_id, user_id, answer))


def test_run_demo_answers_as_the_starting_user_and_returns_done() -> None:
    manager = StubManager()
    fields = schemas.SubmitMemoryFields(text="hymn books", year=1988, participant_ids=["dad"])
    out = io.StringIO()
    asked: list[str] = []

    def answer(name: str, payload: BaseModel) -> schemas.AnswerRequest:
        asked.append(name)
        return demo.auto_answer(name, payload)

    result = demo.run_demo(
        manager,  # type: ignore[arg-type]
        family_id="fam_x",
        user_id="user_ana",
        person_id="mom",
        fields=fields,
        photo_ids=["media/1"],
        answer=answer,
        out=out,
        timeout=5,
    )

    assert manager.started == [("fam_x", "user_ana", "mom", fields, ["media/1"])]
    assert asked == ["question", "props_found"]
    assert [(f, r, u, a.kind) for f, r, u, a in manager.answers] == [
        ("fam_x", "run_1", "user_ana", "answer"),
        ("fam_x", "run_1", "user_ana", "props"),
    ]
    assert result == demo.DemoResult(
        run_id="run_1",
        status="done",
        memory_id="memory_42",
        events=["plan", "question", "props_found", "scene_ready", "done"],
    )
    text = out.getvalue()
    assert "question: What were you carrying?" in text
    assert "scene_ready: memory memory_42 v1" in text
    assert "done: memory memory_42" in text


def test_run_demo_reports_error_and_timeout() -> None:
    class Failing(StubManager):
        async def stream_run(self, family_id, run_id):
            yield "error", schemas.ErrorEvent(message="boom")

    class Stuck(StubManager):
        async def stream_run(self, family_id, run_id):
            import asyncio

            yield "plan", REPRESENTATIVE["plan"]
            await asyncio.sleep(30)

    fields = schemas.SubmitMemoryFields(text="t", year=1990)
    common = {"family_id": "f", "user_id": "u", "person_id": "p", "fields": fields, "photo_ids": [],
              "answer": demo.auto_answer}
    failed = demo.run_demo(Failing(), out=io.StringIO(), **common)  # type: ignore[arg-type]
    assert (failed.status, failed.memory_id, failed.events) == ("error", None, ["error"])
    stuck = demo.run_demo(Stuck(), out=io.StringIO(), timeout=0.2, **common)  # type: ignore[arg-type]
    assert (stuck.status, stuck.events) == ("timeout", ["plan"])


# --------------------------------------------------------------------------- CLI


def test_main_fake_auto_writes_a_memory_response(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    pytest.importorskip("agent.fake_studio")
    out_path = tmp_path / "memory.json"

    assert demo.main(["--fake", "--auto", "--out", str(out_path)]) == 0

    response = schemas.MemoryResponse.model_validate_json(out_path.read_text())
    memory = response.memory
    assert memory.family_id == demo.FAKE_FAMILY_ID
    assert memory.screenplay is not None and memory.timeline is not None
    assert [t.author_id for t in memory.tellings] == ["mom"]
    assert {"mom", "dad"} <= {p.id for p in response.people}
    assert {s.id for s in response.sets} >= {shot.set for shot in memory.screenplay.shots}
    assert set(memory.screenplay.props) <= {p.id for p in response.props}
    printed = capsys.readouterr().out
    assert "studio services:" in printed
    assert "props_found:" in printed and f"done: memory {memory.id}" in printed


def test_main_fake_rejects_unknown_teller(capsys: pytest.CaptureFixture[str]) -> None:
    pytest.importorskip("agent.fake_studio")
    assert demo.main(["--fake", "--auto", "--as", "nobody"]) == 2
    assert "'nobody' is not a person" in capsys.readouterr().err


@pytest.fixture
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_args, **_kwargs):
        raise AssertionError("the demo tried to open a network connection")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


def test_main_without_key_exits_2_with_a_clear_message(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], no_network: None
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert demo.main([]) == 2
    err = capsys.readouterr().err
    for name in ("ANTHROPIC_API_KEY", "MONGODB_URI", "STITCH_FAKE_DB", "IMAGE_API_KEY", "ELEVENLABS_API_KEY",
                 "STITCH_WEB_URL", "--fake"):
        assert name in err
    assert "--weekend" not in err


def test_weekend_without_fake_or_key_exits_2_and_names_the_offline_command(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], no_network: None
) -> None:
    """`python -m agent.demo --weekend` is the real studio: with no key it says how to run it offline."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert demo.main(["--weekend"]) == 2
    err = capsys.readouterr().err
    assert "ANTHROPIC_API_KEY is not set" in err
    assert "uv run python -m agent.demo --fake --auto --weekend" in err


def test_missing_key_exit_imports_no_network_clients() -> None:
    """In a fresh interpreter: main([]) returns 2 before agent.production or any client loads."""
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    env["STITCH_NO_DOTENV"] = "1"  # never read the developer's real api/.env
    code = (
        "import sys\n"
        "from agent.demo import main\n"
        "rc = main([])\n"
        "loaded = [m for m in ('agent.production', 'agent.adapters', 'anthropic', 'langchain_anthropic', 'httpx',"
        " 'pymongo', 'db.client') if m in sys.modules]\n"
        "print(rc, loaded)\n"
    )
    done = subprocess.run(
        [sys.executable, "-c", code], cwd=API_DIR, env=env, capture_output=True, text=True, timeout=120, check=False
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "2 []"
    assert "ANTHROPIC_API_KEY is not set" in done.stderr


# --------------------------------------------------------------------------- the weekend demo path


@pytest.fixture
def captured_setup(monkeypatch: pytest.MonkeyPatch) -> list[demo._Setup]:
    """Every setup main builds in fake mode, so a test can read the repo the run wrote to."""
    made: list[demo._Setup] = []
    build = demo._fake_setup

    def capture(args, out):  # noqa: ANN001, ANN202
        setup = build(args, out)
        made.append(setup)
        return setup

    monkeypatch.setattr(demo, "_fake_setup", capture)
    return made


def test_main_fake_weekend_path_is_in_both_books_with_a_valid_timeline(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], captured_setup: list[demo._Setup]
) -> None:
    """README 'Scope for the weekend': mom tells with a photo, the scene is in mom's and dad's
    books, dad adds his telling, and the saved timeline validates and recompiles on the real compiler."""
    from screenplay import compiler

    out_path = tmp_path / "memory.json"
    assert demo.main(["--fake", "--auto", "--weekend", "--out", str(out_path)]) == 0
    (setup,) = captured_setup
    family = setup.family_id
    printed = capsys.readouterr().out

    response = schemas.MemoryResponse.model_validate_json(out_path.read_text())
    memory = response.memory
    assert memory.family_id == family == demo.FAKE_FAMILY_ID
    # One telling with a photo, then a second telling of the same memory.
    assert [t.author_id for t in memory.tellings] == ["mom", "dad"]
    assert memory.tellings[0].text == demo.DEFAULT_STORY and memory.tellings[1].text == demo.DEFAULT_THEN_STORY
    (photo,) = memory.tellings[0].photo_ids
    assert setup.repo.get_media(family, photo) is not None
    assert memory.version == 2
    assert [m.id for m in setup.repo.find_memories(family, limit=1000) if m.id == memory.id] == [memory.id]

    # In both people's books.
    for person in ("mom", "dad"):
        assert memory.id in [m.id for m in setup.repo.find_memories(family, person_id=person, limit=1000)], person
    assert demo.books_with(setup.repo, family, memory.id) == {"mom": True, "dad": True}
    assert printed.count(f"book: mom has {memory.id}") == 2 and printed.count(f"book: dad has {memory.id}") == 2

    # The saved timeline validates against the contract, with both voices over one scene.
    assert memory.screenplay is not None and memory.timeline is not None
    timeline = schemas.Timeline.model_validate(memory.timeline.model_dump(mode="json", by_alias=True))
    assert timeline.duration > 0 and timeline.shots and timeline.tracks
    assert all(shot.page_text and shot.page_text.strip() for shot in timeline.shots)
    assert set(timeline.narrations or {}) == {"mom", "dad"} and timeline.default_narrator == "mom"
    assert timeline.explanations and timeline.interactions

    # The run used the real compiler, and the saved screenplay compiles on it again.
    assert "compiler=screenplay.compiler" in printed
    result = compiler.compile(
        memory.screenplay,
        sets={s.id: s for s in response.sets},
        props={p.id: p for p in response.props},
        people={p.id: p for p in response.people},
    )
    assert result.ok and not result.errors, result.errors
    assert result.timeline is not None and [s.id for s in result.timeline.shots] == [s.id for s in timeline.shots]

    assert "photo: generated sample photo" in printed
    assert f"second telling: dad tells their version of memory {memory.id}" in printed
    assert printed.count(f"done: memory {memory.id}") == 2
    assert f"timeline: valid, {len(timeline.shots)} shot(s)" in printed and "narrations: dad, mom" in printed


def test_main_then_needs_another_person_in_the_family(capsys: pytest.CaptureFixture[str]) -> None:
    assert demo.main(["--fake", "--auto", "--then", "mom"]) == 2
    assert "is the first teller" in capsys.readouterr().err
    assert demo.main(["--fake", "--auto", "--then", "nobody"]) == 2
    assert "'nobody' is not a person" in capsys.readouterr().err


def test_main_fails_when_the_memory_is_missing_from_a_book(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(demo, "books_with", lambda _repo, _family, _memory: {"mom": True, "dad": False})
    assert demo.main(["--fake", "--auto"]) == 1
    captured = capsys.readouterr()
    assert "book: dad is MISSING" in captured.out and "book: mom has" in captured.out
    assert "missing from someone's book" in captured.err


def test_main_fails_when_the_saved_timeline_does_not_validate(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(demo, "describe_timeline", lambda _memory: schemas.Timeline.model_validate({}))
    assert demo.main(["--fake", "--auto"]) == 1
    assert "the saved timeline does not validate" in capsys.readouterr().err


def test_sample_photo_is_a_png() -> None:
    from PIL import Image

    data = demo.sample_photo_png()
    with Image.open(io.BytesIO(data)) as image:
        assert image.format == "PNG" and image.size == (160, 120)


# --------------------------------------------------------------------------- env files


def _unset(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    """Unset `name` so monkeypatch restores it afterwards, even if load_env sets it (delenv of
    an absent variable records nothing, and the value would leak into later tests)."""
    monkeypatch.setenv(name, "")
    monkeypatch.delenv(name)


def test_main_real_mode_loads_the_key_from_an_env_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, no_network: None
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("ANTHROPIC_API_KEY=from-file\n")
    monkeypatch.setattr(demo, "ENV_FILES", (env_file, tmp_path / "missing.env"))
    _unset(monkeypatch, "ANTHROPIC_API_KEY")
    _unset(monkeypatch, "STITCH_NO_DOTENV")
    seen: list[str | None] = []

    def stop(args: object, out: object) -> None:
        seen.append(os.environ.get("ANTHROPIC_API_KEY"))
        raise SystemExit(7)

    monkeypatch.setattr(demo, "_real_setup", stop)
    with pytest.raises(SystemExit):
        demo.main(["--auto"])
    assert seen == ["from-file"]


def test_load_env_keeps_a_variable_already_set(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    first, second = tmp_path / "a.env", tmp_path / "b.env"
    first.write_text("ANTHROPIC_API_KEY=from-file\nSTITCH_DEMO_TEST_X=api\n")
    second.write_text("STITCH_DEMO_TEST_X=root\nSTITCH_DEMO_TEST_Y=root\n")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "from-shell")
    _unset(monkeypatch, "STITCH_DEMO_TEST_X")
    _unset(monkeypatch, "STITCH_DEMO_TEST_Y")
    _unset(monkeypatch, "STITCH_NO_DOTENV")
    assert demo.load_env([first, second, tmp_path / "missing.env"]) == [first, second]
    assert os.environ["ANTHROPIC_API_KEY"] == "from-shell"
    assert os.environ["STITCH_DEMO_TEST_X"] == "api"
    assert os.environ["STITCH_DEMO_TEST_Y"] == "root"


@pytest.mark.parametrize("value", ["1", "true", "YES"])
def test_stitch_no_dotenv_disables_loading(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, value: str) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("STITCH_DEMO_TEST_Z=loaded\n")
    _unset(monkeypatch, "STITCH_DEMO_TEST_Z")
    monkeypatch.setenv("STITCH_NO_DOTENV", value)
    assert demo.load_env([env_file]) == []
    assert "STITCH_DEMO_TEST_Z" not in os.environ


def test_main_no_env_file_and_no_key_still_exits_2(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str], no_network: None
) -> None:
    monkeypatch.setattr(demo, "ENV_FILES", (tmp_path / ".env", tmp_path / "root.env"))
    _unset(monkeypatch, "ANTHROPIC_API_KEY")
    _unset(monkeypatch, "STITCH_NO_DOTENV")
    assert demo.main([]) == 2
    assert "ANTHROPIC_API_KEY is not set" in capsys.readouterr().err


def test_fake_mode_loads_no_env_file(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[bool] = []
    monkeypatch.setattr(demo, "load_env", lambda *a, **k: called.append(True) or [])
    _unset(monkeypatch, "ANTHROPIC_API_KEY")
    assert demo.main(["--fake", "--auto"]) == 0
    assert called == []


def test_env_files_match_preflight() -> None:
    from agent import preflight

    assert REAL_ENV_FILES == preflight.ENV_FILES
