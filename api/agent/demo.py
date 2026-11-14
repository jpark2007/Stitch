"""One add-a-memory run of the studio from the terminal, through the same seam the routes use.

    cd api
    uv run python -m agent.demo --fake --auto              # offline: fake brain, fake services
    uv run python -m agent.demo --fake --auto --out /tmp/memory.json
    uv run python -m agent.demo --fake --auto --weekend    # offline: the whole weekend demo path
    ANTHROPIC_API_KEY=... uv run python -m agent.demo       # real studio (Claude), answers on stdin
    ANTHROPIC_API_KEY=... STITCH_FAKE_DB=1 uv run python -m agent.demo --auto

It starts one run with `RunManager.start_run`, follows `RunManager.stream_run` on asyncio
and prints one line per run event. When the studio pauses (question, props_found, merge)
the answer comes from the terminal, or from `auto_answer` with --auto, and goes back
through `RunManager.answer_run` as the same user who started the run. It stops on done
or error. With --out, the saved memory is written as `schemas.MemoryResponse` JSON
(memory, props, sets, people, read back from the repo), the shape the web stage plays.

After a run ends with done, one `book:` line per person in the memory says whether it is
in that person's book (`AgentRepo.find_memories(person_id=...)`, which is
`db.memories.get_book`, the /book route's query, on the real repo).

The weekend path (README, 'Scope for the weekend')
--------------------------------------------------
--weekend is the stage demo in one command: the teller (mom) tells the story with a photo
(--photo, or a generated sample photo), the memory lands in both her book and the
participant's (dad), and then dad tells his version of the same memory as a second
telling (`--then dad`, with `--then-story`). Each step prints its events and the book
lines, and the last line checks the saved timeline against the contract. The pieces work
alone too: --sample-photo attaches the generated photo, --then PERSON adds the second
telling after any first run.

Modes
-----
--fake      `wiring.fake_services` with `agent.fake_studio.fake_studio_model()` as the
            brain, over an InMemoryRepo seeded from the hymn_books sample
            (`agent.repo.load_sample_family`). No keys, no network.
(default)   `agent.production.production_services()`: needs ANTHROPIC_API_KEY, and reads
            the env vars listed in agent/production.py (STITCH_MODEL,
            STITCH_SUBAGENT_MODEL, MONGODB_URI / MONGODB_DB, STITCH_FAKE_DB,
            IMAGE_API_KEY, ELEVENLABS_API_KEY, STITCH_WEB_URL). The family is seeded
            with `seed.demo.seed_demo` unless --no-seed; with STITCH_FAKE_DB=1 the
            database is in-memory mongomock, seeded the same way. The line printed
            first names which services are real and which are placeholders.

Exit codes: 0 when every run ends with done (and, after a done run, the memory is in the
book of every person in it), 1 on error, a timeout or a missing book entry, 2 on missing
configuration or arguments the run cannot start with.

Usage notes: the real (keyed) mode has not been exercised yet; it was written against
agent.production and seed.demo on a machine with no API keys. The --fake mode and the
missing-key exit are covered by tests/agent/test_agent_demo.py.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import mimetypes
import os
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, TextIO

from pydantic import BaseModel, ValidationError

import schemas

if TYPE_CHECKING:
    from agent.repo import AgentRepo
    from agent.runs import RunManager

NEEDS_INPUT_EVENTS = frozenset({"question", "props_found", "merge"})
TERMINAL_EVENTS = frozenset({"done", "error"})

FAKE_FAMILY_ID = "fam_demo"
DEFAULT_PERSON = "mom"
DEFAULT_PARTICIPANTS = "dad"
DEFAULT_YEAR = 1988
DEFAULT_STORY = (
    "It was a Sunday in 1988 and I was carrying far too many hymn books down the church steps. "
    "They slipped and went everywhere, right in front of him. "
    "He picked up every single one and handed them back so carefully, and I could not even look at him."
)
AUTO_ANSWER_TEXT = "It was a bright spring morning, and the whole congregation was watching."
DEFAULT_THEN_STORY = (
    "I remember those hymn books going everywhere. I picked them up one at a time, slowly, "
    "so I could stay next to her a little longer."
)
"""The second telling's text (--then-story): the participant's version of the same moment."""
BOOK_LIMIT = 1000
"""How many of a person's memories the book check reads."""
FALLBACK_USER_ID = "demo_cli"
"""User id for a real-mode run told as a person without a demo account."""

MISSING_KEY_MESSAGE = (
    "agent.demo: ANTHROPIC_API_KEY is not set, so the real studio cannot run. The real mode "
    "reads ANTHROPIC_API_KEY (required: the studio model, STITCH_MODEL to pick it, and Claude "
    "vision), STITCH_SUBAGENT_MODEL (optional sub-agent model), MONGODB_URI and MONGODB_DB "
    "(family data and checkpoints; STITCH_FAKE_DB=1 uses an in-memory database instead), "
    "IMAGE_API_KEY (real prop and set art, else placeholders), ELEVENLABS_API_KEY (real "
    "voices, else silent audio with real timings) and STITCH_WEB_URL (render frames from the "
    "web /render page, else diagrams). Set ANTHROPIC_API_KEY and retry, or run offline with "
    "--fake --auto."
)

Answerer = Callable[[str, BaseModel], schemas.AnswerRequest]


@dataclass
class DemoResult:
    run_id: str
    status: str
    """'done', 'error' or 'timeout'."""
    memory_id: str | None
    events: list[str] = field(default_factory=list)
    """Event names in the order they streamed."""


# --------------------------------------------------------------------------- formatting

_PLAN_MARKS = {"pending": "[ ]", "in_progress": "[>]", "completed": "[x]"}


def _one_line(text: str | None, limit: int = 160) -> str:
    flat = " ".join((text or "").split())
    return flat if len(flat) <= limit else flat[: limit - 3].rstrip() + "..."


def format_event(name: str, payload: BaseModel) -> str:
    """One readable line for a run event, e.g. 'props_found: hymn_books (Hymn books, carried)'."""
    match payload:
        case schemas.PlanEvent(todos=todos):
            steps = " | ".join(f"{_PLAN_MARKS.get(t.status, '[ ]')} {_one_line(t.text, 60)}" for t in todos)
            return f"plan: {steps or '(empty)'}"
        case schemas.SubagentStartEvent(name=who, task=task):
            return f"subagent_start: {who}" + (f": {_one_line(task)}" if task else "")
        case schemas.SubagentEndEvent(name=who, summary=summary):
            return f"subagent_end: {who}" + (f": {_one_line(summary)}" if summary else "")
        case schemas.QuestionEvent(text=text):
            return f"question: {_one_line(text, 400)}"
        case schemas.PropsFoundEvent(props=chips):
            listed = "; ".join(_chip(c) for c in chips)
            return f"props_found: {listed or '(none)'}"
        case schemas.MergeEvent(text=text, person_id=person_id, candidate_name=candidate):
            return f"merge: {_one_line(text, 300)} (candidate {candidate}, id {person_id})"
        case schemas.CompileErrorEvent(errors=errors, round=round_):
            codes = ", ".join(e.code for e in errors) or "(none)"
            first = f"; first: {_one_line(errors[0].message, 120)}" if errors else ""
            return f"compile_error: round {round_}, {len(errors)} error(s): {codes}{first}"
        case schemas.FramesEvent(frames=frames, critique=critique):
            times = ", ".join(f"{f.t:g}s" for f in frames)
            line = f"frames: {len(frames)} frame(s)" + (f" at {times}" if times else "")
            return line + (f"; critique: {_one_line(critique, 120)}" if critique else "")
        case schemas.SceneReadyEvent(memory_id=memory_id, version=version):
            return f"scene_ready: memory {memory_id} v{version}"
        case schemas.DoneEvent(memory_id=memory_id):
            return f"done: memory {memory_id}"
        case schemas.ErrorEvent(message=message):
            return f"error: {_one_line(message, 400)}"
    return f"{name}: {payload.model_dump_json()}"


def _chip(chip: schemas.PropChip) -> str:
    extra = [chip.size]
    if chip.reused:
        extra.append("reused")
    if chip.art is None:
        extra.append("placeholder art")
    return f"{chip.id} ({chip.name}, {', '.join(extra)})"


def _describe_answer(answer: schemas.AnswerRequest) -> str:
    if answer.kind == "answer":
        return f"answer: {_one_line(answer.text, 200)}"
    if answer.kind == "merge":
        return "merge: " + ("approved" if answer.approve else "rejected")
    edits = answer.props or []
    return "props: " + (", ".join(f"{e.action} {e.id}" + (f"={e.name}" if e.name else "") for e in edits) or "keep all")


# --------------------------------------------------------------------------- answers


def auto_answer(name: str, payload: BaseModel) -> schemas.AnswerRequest:
    """A fixed answer for every needs-input event: a short reply, keep every prop, approve the merge."""
    if name == "question":
        return schemas.AnswerRequest(kind="answer", text=AUTO_ANSWER_TEXT)
    if name == "props_found":
        assert isinstance(payload, schemas.PropsFoundEvent)
        return schemas.AnswerRequest(
            kind="props", props=[schemas.PropEdit(id=chip.id, action="keep") for chip in payload.props]
        )
    if name == "merge":
        return schemas.AnswerRequest(kind="merge", approve=True)
    raise ValueError(f"{name!r} does not need an answer")


def stdin_answer(
    name: str,
    payload: BaseModel,
    *,
    read: Callable[[str], str] = input,
    out: TextIO | None = None,
) -> schemas.AnswerRequest:
    """Ask on the terminal. `read` and `out` are for tests."""
    out = out if out is not None else sys.stdout
    if name == "question":
        while True:
            text = read("  your answer> ").strip()
            if text:
                return schemas.AnswerRequest(kind="answer", text=text)
            print("  (an answer needs some text)", file=out)
    if name == "props_found":
        assert isinstance(payload, schemas.PropsFoundEvent)
        print("  Props: " + ", ".join(c.id for c in payload.props), file=out)
        print("  Enter keeps them all. Or edit, separated by ';': 'remove <id>' or 'rename <id>=<new name>'.", file=out)
        while True:
            try:
                return schemas.AnswerRequest(kind="props", props=parse_prop_edits(read("  props> "), payload.props))
            except ValueError as exc:
                print(f"  ({exc})", file=out)
    if name == "merge":
        while True:
            said = read("  same person? [y/n]> ").strip().lower()
            if said in {"y", "yes", "n", "no"}:
                return schemas.AnswerRequest(kind="merge", approve=said.startswith("y"))
            print("  (type y or n)", file=out)
    raise ValueError(f"{name!r} does not need an answer")


def parse_prop_edits(line: str, chips: Iterable[schemas.PropChip]) -> list[schemas.PropEdit]:
    """'remove a; rename b=New name' -> edits for every chip (the rest are kept)."""
    ids = [c.id for c in chips]
    changes: dict[str, schemas.PropEdit] = {}
    for part in (p.strip() for p in line.split(";")):
        if not part:
            continue
        verb, _, rest = part.partition(" ")
        rest = rest.strip()
        if verb == "remove" and rest in ids:
            changes[rest] = schemas.PropEdit(id=rest, action="remove")
        elif verb == "rename" and "=" in rest:
            prop_id, _, new_name = (s.strip() for s in rest.partition("="))
            if prop_id not in ids or not new_name:
                raise ValueError(f"cannot rename {prop_id!r}: use one of {', '.join(ids)} and a new name")
            changes[prop_id] = schemas.PropEdit(id=prop_id, action="rename", name=new_name)
        else:
            raise ValueError(f"did not understand {part!r}")
    return [changes.get(i) or schemas.PropEdit(id=i, action="keep") for i in ids]


# --------------------------------------------------------------------------- the run


def run_demo(
    manager: RunManager,
    *,
    family_id: str,
    user_id: str,
    person_id: str,
    fields: schemas.SubmitMemoryFields,
    photo_ids: list[str],
    answer: Answerer,
    out: TextIO,
    timeout: float = 600.0,
) -> DemoResult:
    """Start one run, print its events to `out`, answer it when it pauses, until done or error.

    Answers go through `manager.answer_run` with `user_id`, the user who started the run.
    If the run has not ended after `timeout` seconds the result's status is 'timeout'.
    """
    run_id = manager.start_run(family_id, user_id, person_id, fields, photo_ids)
    print(f"run {run_id} started for {person_id} in {family_id}", file=out, flush=True)
    result = DemoResult(run_id=run_id, status="running", memory_id=None)

    async def follow() -> None:
        async for name, payload in manager.stream_run(family_id, run_id):
            result.events.append(name)
            print(format_event(name, payload), file=out, flush=True)
            if name in NEEDS_INPUT_EVENTS:
                await _answer(manager, family_id, run_id, user_id, name, payload, answer, out)
            elif isinstance(payload, schemas.SceneReadyEvent):
                result.memory_id = payload.memory_id
            elif isinstance(payload, schemas.DoneEvent):
                result.status, result.memory_id = "done", payload.memory_id
                return
            elif isinstance(payload, schemas.ErrorEvent):
                result.status = "error"
                return
        result.status = "error"  # the stream closed without a terminal event

    async def bounded() -> None:
        try:
            await asyncio.wait_for(follow(), timeout)
        except TimeoutError:
            result.status = "timeout"
            print(f"timeout: the run did not end within {timeout:g}s", file=out, flush=True)

    asyncio.run(bounded())
    return result


ANSWER_ATTEMPTS = 3


async def _answer(
    manager: RunManager,
    family_id: str,
    run_id: str,
    user_id: str,
    name: str,
    payload: BaseModel,
    answer: Answerer,
    out: TextIO,
) -> None:
    """Ask `answer` (on a thread: it may block on stdin) and send it; re-ask if the run rejects it."""
    for attempt in range(1, ANSWER_ATTEMPTS + 1):
        reply = await asyncio.to_thread(answer, name, payload)
        print(f"  -> {_describe_answer(reply)}", file=out, flush=True)
        try:
            manager.answer_run(family_id, run_id, user_id, reply)
            return
        except ValueError as exc:
            print(f"  (the run did not take that answer: {exc})", file=out, flush=True)
            if attempt == ANSWER_ATTEMPTS:
                raise


# --------------------------------------------------------------------------- output


def memory_response(repo: AgentRepo, family_id: str, memory_id: str) -> schemas.MemoryResponse | None:
    """The saved memory with every prop, set and person its scene uses, read back from `repo`."""
    memory = repo.get_memory(family_id, memory_id)
    if memory is None:
        return None
    screenplay = memory.screenplay
    person_ids = [
        *memory.participant_ids,
        *(t.author_id for t in memory.tellings),
        *(screenplay.cast if screenplay else []),
    ]
    people = [p for p in (repo.get_person(family_id, pid) for pid in _dedupe(person_ids)) if p is not None]
    prop_ids = [
        *memory.prop_ids,
        *(screenplay.props if screenplay else []),
        *(p.character.item_prop_id for p in people if p.character.item_prop_id),
    ]
    set_ids = [*memory.set_ids, *(shot.set for shot in (screenplay.shots if screenplay else []))]
    props = [p for p in (repo.get_prop(family_id, pid) for pid in _dedupe(prop_ids)) if p is not None]
    sets = [s for s in (repo.get_set(family_id, sid) for sid in _dedupe(set_ids)) if s is not None]
    return schemas.MemoryResponse(memory=memory, props=props, sets=sets, people=people)


def _dedupe(ids: Iterable[str | None]) -> list[str]:
    return list(dict.fromkeys(i for i in ids if i))


def books_with(repo: AgentRepo, family_id: str, memory_id: str) -> dict[str, bool]:
    """For each person in the memory (its participants and tellers): is it in their book?

    A book is `find_memories(person_id=...)`; on MongoRepo that is `db.memories.get_book`,
    the query behind GET /families/{fid}/people/{pid}/book.
    """
    memory = repo.get_memory(family_id, memory_id)
    if memory is None:
        return {}
    people = _dedupe([*(t.author_id for t in memory.tellings), *memory.participant_ids])
    return {
        pid: any(m.id == memory_id for m in repo.find_memories(family_id, person_id=pid, limit=BOOK_LIMIT))
        for pid in people
    }


def print_books(repo: AgentRepo, family_id: str, memory_id: str, out: TextIO) -> bool:
    """One 'book:' line per person in the memory; True when it is in every one of their books."""
    books = books_with(repo, family_id, memory_id)
    for pid, there in books.items():
        print(f"book: {pid} {'has' if there else 'is MISSING'} {memory_id}", file=out, flush=True)
    if not books:
        print(f"book: memory {memory_id} is not in the repo", file=out, flush=True)
    return bool(books) and all(books.values())


def describe_timeline(memory: schemas.Memory) -> str:
    """The saved timeline checked against the contract (schemas.Timeline), in one line.

    Raises pydantic.ValidationError when the saved timeline does not validate.
    """
    if memory.timeline is None or memory.screenplay is None:
        return "timeline: none saved"
    timeline = schemas.Timeline.model_validate(memory.timeline.model_dump(mode="json", by_alias=True))
    voices = ", ".join(sorted(timeline.narrations or {})) or "none"
    tellers = ", ".join(t.author_id for t in memory.tellings)
    return (
        f"timeline: valid, {len(timeline.shots)} shot(s), {timeline.duration:g}s, "
        f"{len(timeline.tracks)} track(s), narrations: {voices}; memory {memory.id} v{memory.version}, "
        f"tellings by {tellers}"
    )


def sample_photo_png() -> bytes:
    """A small generated photo (PNG) for a demo with no photo of its own: sky, steps, two figures."""
    import io

    from PIL import Image, ImageDraw

    image = Image.new("RGB", (160, 120), (150, 190, 225))
    draw = ImageDraw.Draw(image)
    for i, top in enumerate(range(70, 120, 10)):
        shade = 170 - 12 * i
        draw.rectangle((10 + 6 * i, top, 150 - 6 * i, top + 10), fill=(shade, shade, shade - 10))
    draw.rectangle((52, 40, 64, 72), fill=(120, 40, 60))
    draw.rectangle((96, 36, 108, 72), fill=(40, 60, 110))
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


# --------------------------------------------------------------------------- CLI


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m agent.demo",
        description="Run one add-a-memory flow through the studio and print its run events.",
        epilog="Exit codes: 0 done, 1 error or timeout, 2 missing configuration.",
    )
    parser.add_argument("--fake", action="store_true", help="offline: fake brain and fake services, no keys")
    parser.add_argument(
        "--family", default=None, help="family id (default: the seeded demo family, or 'fam_demo' with --fake)"
    )
    parser.add_argument("--as", dest="person", default=DEFAULT_PERSON, help="teller's person id (default: mom)")
    parser.add_argument("--story", default=DEFAULT_STORY, help="the telling (default: the hymn-books story)")
    parser.add_argument("--title", default=None, help="optional title for the memory")
    parser.add_argument(
        "--participants", default=DEFAULT_PARTICIPANTS, help="comma-separated person ids (default: dad)"
    )
    parser.add_argument("--year", type=int, default=DEFAULT_YEAR, help=f"year of the memory (default: {DEFAULT_YEAR})")
    parser.add_argument(
        "--photo", action="append", default=[], metavar="PATH", help="a photo to attach (repeatable)"
    )
    parser.add_argument(
        "--sample-photo", action="store_true", help="attach a small generated photo (for a demo with none at hand)"
    )
    parser.add_argument("--memory-id", default=None, help="add a second telling to this existing memory")
    parser.add_argument(
        "--then", default=None, metavar="PERSON",
        help="after the first run is done, PERSON tells their version of the same memory (a second telling)",
    )
    parser.add_argument("--then-story", default=DEFAULT_THEN_STORY, help="the second telling's text (with --then)")
    parser.add_argument(
        "--weekend", action="store_true",
        help="the weekend demo path: a telling with a photo (--sample-photo unless --photo), "
        "then the first participant's second telling (--then), both books checked",
    )
    parser.add_argument("--auto", action="store_true", help="answer questions automatically instead of on stdin")
    parser.add_argument("--out", default=None, metavar="PATH", help="write the saved memory as MemoryResponse JSON")
    parser.add_argument("--no-seed", action="store_true", help="real mode: do not seed the demo family")
    parser.add_argument("--timeout", type=float, default=600.0, help="seconds to wait for the run (default: 600)")
    parser.add_argument("--verbose", action="store_true", help="log the studio's INFO messages")
    return parser


@dataclass
class _Setup:
    manager: RunManager
    repo: AgentRepo
    family_id: str
    user_for: Callable[[str], str]
    """The user id a person tells (and answers) as."""


def _fake_user(person_id: str) -> str:
    return f"user_{person_id}"


def _fake_setup(args: argparse.Namespace, out: TextIO) -> _Setup:
    from agent import fake_studio, wiring
    from agent.repo import InMemoryRepo, load_sample_family

    family_id = args.family or FAKE_FAMILY_ID
    repo = InMemoryRepo()
    load_sample_family(repo, family_id)
    services = wiring.fake_services(model=fake_studio.fake_studio_model(), repo=repo)
    print("mode: fake (no keys, no network)", file=out)
    print(_describe_labels(services.labels), file=out, flush=True)
    return _Setup(wiring.make_manager(services), repo, family_id, _fake_user)


def _describe_labels(labels: dict[str, str]) -> str:
    return "studio services: " + " ".join(f"{k}={v}" for k, v in labels.items())


class _ConfigError(Exception):
    """Missing or unusable configuration: main prints it and returns 2."""


def _real_setup(args: argparse.Namespace, out: TextIO) -> _Setup:
    # Only reached with ANTHROPIC_API_KEY set; the network clients are imported from here on.
    from agent import production, wiring
    from db import client as db_client
    from seed import demo as seed_demo

    fake_db = (os.environ.get("STITCH_FAKE_DB") or "").strip().lower() in {"1", "true", "yes"}
    if fake_db:
        from seed.fake import install_fake_db

        install_fake_db(seed=not args.no_seed)
    try:
        services = production.production_services()
    except RuntimeError as exc:
        raise _ConfigError(str(exc)) from exc
    print("mode: real", file=out)
    print(production.describe(services), file=out, flush=True)

    if not fake_db and not args.no_seed:
        try:
            seeded = seed_demo.seed_demo(db_client.get_db())
        except Exception as exc:  # noqa: BLE001 - an unreachable database is a configuration problem
            raise _ConfigError(
                f"could not seed the demo family ({type(exc).__name__}: {exc}); set MONGODB_URI to a "
                "reachable MongoDB, use STITCH_FAKE_DB=1, or pass --no-seed"
            ) from exc
        print(f"demo family {seeded.family_id!r} " + ("seeded" if seeded.created else "already there"), file=out)

    accounts = {a.person_id: a.user_id for a in seed_demo.DEMO_ACCOUNTS}
    family_id = args.family or seed_demo.DEMO_FAMILY_ID
    return _Setup(
        wiring.make_manager(services), services.repo, family_id, lambda pid: accounts.get(pid, FALLBACK_USER_ID)
    )


def _check_run_inputs(setup: _Setup, person_id: str, fields: schemas.SubmitMemoryFields) -> None:
    """What start_run would refuse (ValueError / MemoryNotFound), as a configuration error up front."""
    if setup.repo.get_person(setup.family_id, person_id) is None:
        raise _ConfigError(f"--as {person_id!r} is not a person in family {setup.family_id!r}")
    if fields.memory_id is not None:
        memory = setup.repo.get_memory(setup.family_id, fields.memory_id)
        if memory is None or memory.screenplay is None or memory.timeline is None:
            raise _ConfigError(
                f"--memory-id {fields.memory_id!r} is not a memory with a scene in family {setup.family_id!r}"
            )


def _second_teller(args: argparse.Namespace, participants: list[str]) -> str | None:
    """Who tells the second telling: --then, or with --weekend the first participant who is not the teller."""
    if args.then:
        return args.then.strip() or None
    if args.weekend:
        return next((p for p in participants if p != args.person), None)
    return None


def _check_second_teller(setup: _Setup, teller: str, then: str) -> None:
    if then == teller:
        raise _ConfigError(f"--then {then!r} is the first teller; a second telling needs another person")
    if setup.repo.get_person(setup.family_id, then) is None:
        raise _ConfigError(f"--then {then!r} is not a person in family {setup.family_id!r}")


def _store_photos(repo: AgentRepo, family_id: str, paths: list[str]) -> list[str]:
    ids: list[str] = []
    for raw in paths:
        path = Path(raw)
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise _ConfigError(f"cannot read photo {raw!r}: {exc}") from exc
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        ids.append(repo.put_media(family_id, data, content_type, kind="photo"))
    return ids


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    out = sys.stdout
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    if not args.fake and not (os.environ.get("ANTHROPIC_API_KEY") or "").strip():
        print(MISSING_KEY_MESSAGE, file=sys.stderr)
        return 2

    setup: _Setup | None = None
    results: list[DemoResult] = []
    in_books = True
    answer = auto_answer if args.auto else stdin_answer
    try:
        setup = _fake_setup(args, out) if args.fake else _real_setup(args, out)
        participants = [p.strip() for p in args.participants.split(",") if p.strip()]
        fields = schemas.SubmitMemoryFields(
            text=args.story,
            year=args.year,
            title=args.title,
            memory_id=args.memory_id,
            participant_ids=participants,
        )
        _check_run_inputs(setup, args.person, fields)
        then = _second_teller(args, participants)
        if then is not None:
            _check_second_teller(setup, args.person, then)
        photo_ids = _store_photos(setup.repo, setup.family_id, args.photo)
        if args.sample_photo or (args.weekend and not photo_ids):
            photo_ids.append(setup.repo.put_media(setup.family_id, sample_photo_png(), "image/png", kind="photo"))
            print(f"photo: generated sample photo {photo_ids[-1]}", file=out, flush=True)

        def tell(person_id: str, step_fields: schemas.SubmitMemoryFields, step_photos: list[str]) -> bool:
            """One run, its result line and, once done, its book lines. True when it is done."""
            nonlocal in_books
            assert setup is not None
            result = run_demo(
                setup.manager,
                family_id=setup.family_id,
                user_id=setup.user_for(person_id),
                person_id=person_id,
                fields=step_fields,
                photo_ids=step_photos,
                answer=answer,
                out=out,
                timeout=args.timeout,
            )
            results.append(result)
            print(f"result: {result.status}, memory {result.memory_id or '-'}, {len(result.events)} event(s)",
                  file=out, flush=True)
            if result.status != "done" or result.memory_id is None:
                return False
            in_books = print_books(setup.repo, setup.family_id, result.memory_id, out) and in_books
            return True

        if tell(args.person, fields, photo_ids) and then is not None:
            memory_id = results[-1].memory_id
            print(f"second telling: {then} tells their version of memory {memory_id}", file=out, flush=True)
            tell(then, schemas.SubmitMemoryFields(
                text=args.then_story, year=args.year, memory_id=memory_id, participant_ids=[args.person]
            ), [])
    except _ConfigError as exc:
        print(f"agent.demo: {exc}", file=sys.stderr)
        return 2
    except (EOFError, KeyboardInterrupt):
        print("\nagent.demo: stopped before the run ended", file=sys.stderr)
        return 1
    finally:
        if setup is not None:
            setup.manager.shutdown(wait=False)

    last = results[-1]
    if last.status != "done":
        return 1
    saved = setup.repo.get_memory(setup.family_id, last.memory_id or "")
    if saved is not None:
        try:
            print(describe_timeline(saved), file=out, flush=True)
        except ValidationError as exc:
            print(f"agent.demo: the saved timeline does not validate: {exc}", file=sys.stderr)
            return 1
    if args.out:
        response = memory_response(setup.repo, setup.family_id, last.memory_id or "")
        if response is None:
            print(f"agent.demo: memory {last.memory_id!r} is not in the repo; nothing written", file=sys.stderr)
            return 1
        Path(args.out).write_text(response.model_dump_json(indent=2) + "\n")
        print(f"wrote {args.out} (MemoryResponse, version {response.memory.version})", file=out)
    if not in_books:
        print("agent.demo: the memory is missing from someone's book", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
