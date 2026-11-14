"""Smoke-test "add a memory" end to end over HTTP, against a running API.

This is the manual check before the stage demo, and the way to smoke-test the real model
studio with real keys. It talks to the API exactly like the web app does (cookie session,
multipart submit, the run's Server-Sent Events, answers), so it needs a server running.

Start the API first, in one terminal (from api/):

    # Offline: in-memory database with the demo family, scripted runs (no keys, no MongoDB).
    uv run python -m seed.serve
    # (the same thing by hand: STITCH_FAKE_DB=1 uv run uvicorn main:app --port 8000)

    # Real model studio: MongoDB and ANTHROPIC_API_KEY (and the other model keys) in the
    # repo-root .env. Seed the demo family once, then start the server with that file.
    uv run --env-file ../.env python -m seed.demo
    uv run uvicorn main:app --env-file ../.env --port 8000

Then, in a second terminal (from api/):

    uv run python -m seed.smoke --base-url http://localhost:8000

    # A second telling ("hear Sam's version"): Sam adds his telling to Lily's graduation.
    uv run python -m seed.smoke --email sam@demo.stitch --memory-id demo_lily_graduation

    # Rehearse every pause kind of a new memory, one run each, on the scripted studio. The
    # default telling calls someone "Grandpa Joe", so every run also asks the merge pause
    # "Is Grandpa Joe the Joseph in your family tree?" (any capitalised near-miss name after
    # a family title does, e.g. --answer "Grandpa Joe was there too.").
    uv run python -m seed.smoke                                   # merge rejected, prop kept
    uv run python -m seed.smoke --merge approve --props rename    # merged, prop renamed
    uv run python -m seed.smoke --props remove                    # prop left out

Every check prints one line as it finishes, "PASS <step>: <what was seen>", and the first
one that fails prints "FAIL <step>: <why>" and ends the smoke. Between them, each run event
is logged on an indented "  event ..." line. Then one summary line ("smoke OK: ..." or
"smoke FAILED: ..."); --quiet prints only that summary.

Options (all optional):

    --base-url URL      the API (default http://localhost:8000)
    --email E           the account to sign in as (default david@demo.stitch)
    --password P        its password (default: the demo password, seed.demo.DEMO_PASSWORD)
    --family FID        the family to add the memory to (default demo_family)
    --text T            the telling (default: a short road-trip memory that mentions
                        "Grandpa Joe", so the scripted studio asks its merge pause)
    --year N            the year of the moment (default 1991; with --memory-id the
                        moment's own year is sent instead)
    --question Q        the question the telling answers (default none)
    --photo PATH ...    photos to attach (default: one small generated PNG)
    --answer TEXT       the reply to the studio's follow-up question
    --merge CHOICE      the answer to a merge pause: reject (default) or approve
    --props CHOICE      the props review: keep (default) every prop, or rename or remove
                        the first one offered (the others are kept)
    --memory-id M       add a second telling to the existing moment M instead of making a
                        new memory (the submit sends memoryId; see checks 3b, 6 and 7)
    --timeout SECONDS   give up after this long (default 900; a real run takes minutes)
    --quiet             print only the final summary line

The exit code is 0 when every check passed, 1 when one failed (the summary line names it),
and 2 when the API cannot be reached at all.

What each check means, in order (the first one that fails is the summary's message):

1. login        POST /auth/login with the email and password answers 200.
2. membership   GET /me lists the family, so the account belongs to it.
3. teller       the account has a person in that family; it tells the memory.
3b. moment      (--memory-id only) GET /families/{fid}/memories/{M} answers 200: the
                moment exists in the family. Its version and year are noted.
4. submit       POST /families/{fid}/memories (multipart: text, year, question, photos,
                and memoryId with --memory-id) answers 202 with a run id: the studio
                accepted the run.
5. stream       GET /families/{fid}/runs/{runId}/stream sends events named as in
                schemas.RUN_EVENTS whose data validates against those models, until
                `done` or `error`. Each event is logged as it arrives.
                - `question`: answered with {"kind": "answer", "text": --answer}.
                - `props_found`: answered with every prop kept ({"kind": "props",
                  "props": [{"id": ..., "action": "keep"}, ...]}); with --props rename
                  or remove the first prop gets {"action": "rename", "name": "<its
                  name> (smoke)"} or {"action": "remove"} instead.
                - `merge`: answered with {"kind": "merge", "approve": false}, so by
                  default a smoke run never merges two people; --merge approve sends
                  true.
                Every answer must get 204, and prints its own PASS line (step `question`,
                `props_found` or `merge`). The stream's last check is step `done`. An `error` event, or no `done` before --timeout,
                fails the smoke. If the connection drops, the stream is reopened (it
                replays the run from its first event) and the events already seen are
                skipped. With --memory-id, `done.memoryId` must be M.
6. memory       GET /families/{fid}/memories/{memoryId} has a screenplay and a timeline:
                the scene was staged and saved. With --memory-id, also: the memory has a
                telling by the teller, `timeline.narrations[teller]` has at least one
                line (their version can be heard), and the version went up.
                (Steps 2, 3 and 4 print as `membership`, `teller` and `submit`; 3b as
                `moment`.)
6b. answers     (only when --merge approve was sent to a merge pause, or --props rename
                or remove to a props review) the memory shows it: the merged person is
                among its participants, a renamed prop has its new name in
                GET /families/{fid}/props, a removed prop is not among its props.
7. book         GET /families/{fid}/people/{tellerId}/book lists the memory: it opens in
                the teller's book (a second telling makes the teller a participant).

Each run adds one memory to the family (with --memory-id: one telling to that moment,
replacing the account's own earlier telling of it, if any). On a real MongoDB, `uv run --env-file ../.env
python -m seed.demo --reset` rebuilds the demo family without them.

Passwords and session cookies are never printed or logged. See also seed/README.md.

From code: `run_smoke(client, SmokeOptions(...), merge="reject", props="keep") -> SmokeResult`,
with an httpx.Client (or FastAPI's TestClient) whose base_url is the API.
"""

from __future__ import annotations

import argparse
import io
import json
import mimetypes
import pathlib
import sys
import time
from collections.abc import Callable, Iterable, Iterator
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from typing import Any

import httpx
from pydantic import BaseModel, ValidationError

import schemas
from seed import demo

DEFAULT_BASE_URL = "http://localhost:8000"
DEFAULT_EMAIL = "david@demo.stitch"
DEFAULT_TEXT = (
    "Every summer we drove to the lake with the windows down. "
    "Grandpa Joe sang along to the radio the whole way and we all joined in on the chorus."
)
"""The default telling. "Grandpa Joe" is a near miss for the demo tree's Joseph, so on the
scripted studio the default run asks the merge pause too."""
DEFAULT_YEAR = 1991
DEFAULT_ANSWER = "It was a warm evening and everyone was singing."
DEFAULT_TIMEOUT = 900.0
MERGE_CHOICES = ("reject", "approve")
PROPS_CHOICES = ("keep", "rename", "remove")
RENAMED = "{name} (smoke)"
"""The new name --props rename gives the first prop offered."""

MAX_RECONNECTS = 3
"""How many times a dropped stream is reopened before the smoke gives up."""
READ_TIMEOUT_CAP = 60.0
"""Longest wait for any byte on a request. The stream sends a heartbeat every 15 seconds."""
CONNECT_TIMEOUT_CAP = 10.0
DETAIL_LIMIT = 200

REACH_MESSAGE = "Cannot reach {url}: is the API running? (see seed/README.md)"


@dataclass
class SmokeOptions:
    base_url: str
    email: str
    password: str
    family_id: str
    text: str
    year: int
    question: str | None
    photos: list[pathlib.Path]
    answer: str
    timeout: float
    memory_id: str | None = None
    """Add a second telling to this existing moment instead of making a new memory."""


@dataclass
class SmokeResult:
    ok: bool
    run_id: str | None
    memory_id: str | None
    events: list[str]
    message: str


# ------------------------------------------------------------------------------ SSE


def _frame(name: str | None, data: list[str]) -> tuple[str, dict] | None:
    if not data:
        return None
    text = "\n".join(data)
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"event {name or 'message'!r} has data that is not JSON: {text[:DETAIL_LIMIT]!r}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"event {name or 'message'!r} has data that is not a JSON object")  # noqa: TRY004
    return name or "message", value


def parse_sse(lines: Iterable[str]) -> Iterator[tuple[str, dict]]:
    """(event name, JSON data) for each Server-Sent Events frame in `lines`.

    `lines` are text lines without their line endings (httpx's `iter_lines()`). A blank
    line ends a frame. Comment lines (starting with ':', like the server's `: ping`) and
    the `id` and `retry` fields are ignored; several `data:` lines are joined with
    newlines; a frame with no data is skipped; a last frame with no blank line after it is
    still yielded. A frame with no `event:` line is named "message", as in the SSE spec.
    Raises ValueError when a frame's data is not a JSON object.
    """
    name: str | None = None
    data: list[str] = []
    for raw in lines:
        line = raw.rstrip("\r\n")
        if line == "":
            frame = _frame(name, data)
            if frame is not None:
                yield frame
            name, data = None, []
            continue
        if line.startswith(":"):
            continue
        key, colon, value = line.partition(":")
        if colon and value.startswith(" "):
            value = value[1:]
        if key == "event":
            name = value
        elif key == "data":
            data.append(value)
    frame = _frame(name, data)
    if frame is not None:
        yield frame


# ------------------------------------------------------------------------------ helpers


class _Fail(Exception):
    """A failed check: its message is the smoke's message."""


class _Timeout(Exception):
    """--timeout passed while the stream was open."""


class _Dropped(Exception):
    """The stream ended or broke before `done` or `error`."""


def _redactor(secret: str) -> Callable[[str], str]:
    def redact(text: str) -> str:
        return text.replace(secret, "***") if secret else text

    return redact


def _detail(res: httpx.Response) -> str:
    """The error detail of a response, short: FastAPI's `detail`, else the body."""
    try:
        res.read()
        body = res.json()
    except Exception:  # noqa: BLE001 - not JSON, or not readable
        body = None
    if isinstance(body, dict) and "detail" in body:
        detail = body["detail"]
        if isinstance(detail, list):
            parts = []
            for item in detail:
                if isinstance(item, dict):
                    loc = ".".join(str(p) for p in item.get("loc", []))
                    parts.append(f"{loc}: {item.get('msg', '')}".strip(": "))
                else:
                    parts.append(str(item))
            text = "; ".join(parts)
        else:
            text = str(detail)
    else:
        try:
            text = res.text
        except Exception:  # noqa: BLE001
            text = ""
    text = " ".join(text.split())
    if len(text) > DETAIL_LIMIT:
        text = text[: DETAIL_LIMIT - 1] + "…"
    return f"HTTP {res.status_code}" + (f" {text}" if text else "")


def _parse(model: type[BaseModel], res: httpx.Response, what: str) -> Any:
    try:
        return model.model_validate(res.json())
    except (ValueError, ValidationError) as exc:
        first = exc.errors()[0] if isinstance(exc, ValidationError) and exc.errors() else None
        where = f" ({'.'.join(str(p) for p in first['loc'])}: {first['msg']})" if first else ""
        raise _Fail(f"{what}: the response does not match schemas.{model.__name__}{where}") from None


def _png() -> bytes:
    from PIL import Image

    out = io.BytesIO()
    Image.new("RGB", (64, 48), (214, 170, 92)).save(out, format="PNG")
    return out.getvalue()


def _photo_files(paths: list[pathlib.Path]) -> list[tuple[str, tuple[str, bytes, str]]]:
    if not paths:
        return [("photos", ("smoke.png", _png(), "image/png"))]
    files = []
    for path in paths:
        try:
            data = pathlib.Path(path).read_bytes()
        except OSError as exc:
            raise _Fail(f"photo: cannot read {path} ({exc.strerror or exc})") from None
        ctype = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        if ctype == "image/jpg":
            ctype = "image/jpeg"
        files.append(("photos", (pathlib.Path(path).name, data, ctype)))
    return files


def _describe(name: str, model: BaseModel) -> str:
    """A short detail for the log line of one event."""
    if isinstance(model, schemas.QuestionEvent):
        return f": {model.text}"
    if isinstance(model, schemas.ErrorEvent):
        return f": {model.message}"
    if isinstance(model, schemas.PropsFoundEvent):
        return ": " + (", ".join(p.name for p in model.props) or "no props")
    if isinstance(model, schemas.MergeEvent):
        return f": {model.text}"
    if isinstance(model, schemas.SubagentStartEvent | schemas.SubagentEndEvent):
        return f" ({model.name})"
    if isinstance(model, schemas.CompileErrorEvent):
        return f" (round {model.round}, {len(model.errors)} errors)"
    if isinstance(model, schemas.DoneEvent | schemas.SceneReadyEvent):
        return f" ({model.memory_id})"
    return ""


def _answer_note(answer: schemas.AnswerRequest) -> str:
    if answer.kind == "merge":
        return ", not merged" if not answer.approve else ", merged"
    if answer.kind == "props":
        edits = answer.props or []
        kept = sum(e.action == "keep" for e in edits)
        notes = [f"{kept} kept"] + [
            f"{e.id} renamed to {e.name!r}" if e.action == "rename" else f"{e.id} removed"
            for e in edits if e.action != "keep"
        ]
        return ", " + ", ".join(notes)
    return ""


@dataclass
class _Run:
    """What the stream has shown so far, and what the answers asked for (checked in 6b)."""

    events: list[str] = field(default_factory=list)
    memory_id: str | None = None
    error: str | None = None
    merged: schemas.MergeEvent | None = None  # a merge pause answered with approve
    prop_edit: schemas.PropEdit | None = None  # a rename or remove sent in the props review


def _answer_for(
    name: str, model: BaseModel, opts: SmokeOptions, run: _Run, *, merge: str = "reject", props: str = "keep"
) -> schemas.AnswerRequest | None:
    """The answer a needs-input event gets, or None for every other event.

    Notes on `run` what an approved merge or a rename or remove asked for.
    """
    if name == "question":
        return schemas.AnswerRequest(kind="answer", text=opts.answer)
    if name == "props_found":
        assert isinstance(model, schemas.PropsFoundEvent)
        edits = [schemas.PropEdit(id=p.id, action="keep") for p in model.props]
        if edits and props == "rename":
            edits[0] = schemas.PropEdit(id=model.props[0].id, action="rename",
                                        name=RENAMED.format(name=model.props[0].name))
        elif edits and props == "remove":
            edits[0] = schemas.PropEdit(id=model.props[0].id, action="remove")
        run.prop_edit = edits[0] if edits and edits[0].action != "keep" else None
        return schemas.AnswerRequest(kind="props", props=edits)
    if name == "merge":
        assert isinstance(model, schemas.MergeEvent)
        approve = merge == "approve"
        run.merged = model if approve else None
        return schemas.AnswerRequest(kind="merge", approve=approve)
    return None


def _deadline_lines(lines: Iterable[str], deadline: float) -> Iterator[str]:
    for line in lines:
        if time.monotonic() > deadline:
            raise _Timeout
        yield line


def _follow(
    client: httpx.Client,
    opts: SmokeOptions,
    run_id: str,
    run: _Run,
    deadline: float,
    log: Callable[[str], None],
    *,
    merge: str = "reject",
    props: str = "keep",
) -> None:
    """Read the run's stream once, from its first event; skip the events already seen.

    Returns after `done` or `error`. Raises _Dropped when the stream ends or breaks first,
    _Timeout when the deadline passes, _Fail on anything else that fails the smoke.
    """
    fid = opts.family_id
    seen = len(run.events)
    index = 0
    try:
        with client.stream("GET", f"/families/{fid}/runs/{run_id}/stream") as res:
            if res.status_code != 200:
                raise _Fail(f"stream: {_detail(res)}")
            ctype = res.headers.get("content-type", "")
            if not ctype.startswith("text/event-stream"):
                raise _Fail(f"stream: expected text/event-stream, got {ctype or 'no content type'}")
            for name, data in parse_sse(_deadline_lines(res.iter_lines(), deadline)):
                index += 1
                if index <= seen:
                    continue  # replayed after a reconnect
                expected = schemas.RUN_EVENTS.get(name)
                if expected is None:
                    raise _Fail(f"stream: unknown event {name!r} (not in schemas.RUN_EVENTS)")
                try:
                    model = expected.model_validate(data)
                except ValidationError as exc:
                    first = exc.errors()[0]
                    loc = ".".join(str(p) for p in first["loc"])
                    raise _Fail(f"stream: {name} event does not match schemas ({loc}: {first['msg']})") from None
                run.events.append(name)
                log(f"  event {name}{_describe(name, model)}")

                if isinstance(model, schemas.ErrorEvent):
                    run.error = model.message
                    return
                if isinstance(model, schemas.DoneEvent):
                    run.memory_id = model.memory_id
                    return
                answer = _answer_for(name, model, opts, run, merge=merge, props=props)
                if answer is not None:
                    reply = client.post(
                        f"/families/{fid}/runs/{run_id}/answer",
                        json=answer.model_dump(mode="json", by_alias=True, exclude_none=True),
                    )
                    if reply.status_code != 204:
                        raise _Fail(f"answer to {name}: {_detail(reply)}")
                    log(f"PASS {name}: answered {name} ({answer.kind}){_answer_note(answer)}")
    except ValueError as exc:  # parse_sse: data that is not a JSON object
        raise _Fail(f"stream: {exc}") from None
    except (httpx.RemoteProtocolError, httpx.ReadError) as exc:
        raise _Dropped(f"the stream broke ({type(exc).__name__})") from None
    raise _Dropped("the stream ended before done or error")


# ------------------------------------------------------------------------------ the smoke


def _check_second_telling(before: schemas.Memory, after: schemas.Memory, teller: str) -> None:
    """Check 6 for --memory-id: the teller's telling and narration track are there, version up."""
    mid = after.id
    if not any(t.author_id == teller for t in after.tellings):
        raise _Fail(f"memory: {mid} has no telling by {teller}")
    track = (after.timeline.narrations or {}).get(teller) if after.timeline is not None else None
    if track is None:
        raise _Fail(f"memory: {mid} has no narration track for {teller} (timeline.narrations[{teller!r}])")
    if not track.lines:
        raise _Fail(f"memory: {teller}'s narration track on {mid} has no lines")
    if after.version <= before.version:
        raise _Fail(f"memory: {mid} is still at version {after.version} (was {before.version})")


def _check_answers(client: httpx.Client, fid: str, memory: schemas.Memory, run: _Run) -> str | None:
    """Check 6b: the memory shows an approved merge and a renamed or removed prop.

    Returns the PASS detail, or None when nothing was asked for (no step line then).
    """
    seen = []
    if run.merged is not None:
        if run.merged.person_id not in memory.participant_ids:
            raise _Fail(f"answers: {run.merged.person_id} was merged but is not in memory {memory.id}")
        seen.append(f"{run.merged.candidate_name} ({run.merged.person_id}) merged into the moment")
    edit = run.prop_edit
    if edit is not None and edit.action == "remove":
        if edit.id in memory.prop_ids:
            raise _Fail(f"answers: prop {edit.id} was removed but memory {memory.id} still has it")
        seen.append(f"prop {edit.id} left out")
    elif edit is not None:
        res = client.get(f"/families/{fid}/props")
        if res.status_code != 200:
            raise _Fail(f"answers: GET props: {_detail(res)}")
        found = next((p for p in _parse(schemas.PropsResponse, res, "answers").props if p.id == edit.id), None)
        if found is None or found.name != edit.name:
            now = "missing" if found is None else repr(found.name)
            raise _Fail(f"answers: prop {edit.id} was renamed to {edit.name!r} but is {now}")
        seen.append(f"prop {edit.id} renamed to {edit.name!r}")
    return "; ".join(seen) or None


def run_smoke(
    client: httpx.Client,
    opts: SmokeOptions,
    *,
    log: Callable[[str], None] = print,
    merge: str = "reject",
    props: str = "keep",
) -> SmokeResult:
    """Drive add-a-memory end to end through `client` (base_url set; paths are relative).

    ok is True only when every check in the module docstring passed; otherwise `message`
    names the first one that failed. httpx.ConnectError (the API is not running) is not
    caught: `main` turns it into exit code 2. The password never appears in `message` or
    in anything passed to `log`. `merge` ("reject" or "approve") answers a merge pause and
    `props` ("keep", "rename" or "remove") the props review, as --merge and --props do.
    """
    if merge not in MERGE_CHOICES:
        raise ValueError(f"merge must be one of {MERGE_CHOICES}, not {merge!r}")
    if props not in PROPS_CHOICES:
        raise ValueError(f"props must be one of {PROPS_CHOICES}, not {props!r}")
    redact = _redactor(opts.password)

    def say(text: str) -> None:
        log(redact(text))

    run = _Run()
    run_id: str | None = None
    started = time.monotonic()
    deadline = started + opts.timeout
    fid = opts.family_id

    def result(ok: bool, message: str) -> SmokeResult:
        return SmokeResult(ok=ok, run_id=run_id, memory_id=run.memory_id, events=list(run.events),
                           message=redact(message))

    def failed(message: str) -> SmokeResult:
        say(f"FAIL {message}")  # the step that failed: "<step>: <why>"
        return result(False, message)

    try:
        # 1. Sign in.
        res = client.post("/auth/login", json={"email": opts.email, "password": opts.password})
        if res.status_code != 200:
            raise _Fail(f"login: could not sign in as {opts.email}: {_detail(res)}")
        say(f"PASS login: signed in as {opts.email}")

        # 2. Membership, and 3. the teller.
        res = client.get("/me")
        if res.status_code != 200:
            raise _Fail(f"me: {_detail(res)}")
        me: schemas.MeResponse = _parse(schemas.MeResponse, res, "me")
        family = next((f for f in me.families if f.id == fid), None)
        if family is None:
            raise _Fail(f"membership: {opts.email} is not a member of family {fid!r}")
        say(f"PASS membership: {opts.email} is in family {family.name} ({fid})")
        if not family.person_id:
            raise _Fail(f"teller: {opts.email} has no person in family {fid!r}")
        teller = family.person_id
        say(f"PASS teller: {teller} tells it")

        # 3b. A second telling: the moment exists; note its version and year.
        moment: schemas.Memory | None = None
        if opts.memory_id is not None:
            res = client.get(f"/families/{fid}/memories/{opts.memory_id}")
            if res.status_code != 200:
                raise _Fail(f"moment: GET memory {opts.memory_id}: {_detail(res)}")
            moment = _parse(schemas.MemoryResponse, res, "moment").memory
            say(f"PASS moment: moment {moment.title!r} ({moment.year}), version {moment.version}, "
                f"{len(moment.tellings)} telling(s)")

        # 4. Submit.
        files = _photo_files(opts.photos)
        form = {"text": opts.text, "year": str(moment.year if moment is not None else opts.year)}
        if opts.question:
            form["question"] = opts.question
        if moment is not None:
            form["memoryId"] = moment.id
        res = client.post(f"/families/{fid}/memories", data=form, files=files)
        if res.status_code != 202:
            raise _Fail(f"submit: {_detail(res)}")
        submitted: schemas.SubmitMemoryResponse = _parse(schemas.SubmitMemoryResponse, res, "submit")
        run_id = submitted.run_id
        say(f"PASS submit: run {run_id} started with {len(files)} photo(s)")

        # 5. Stream, answering what the run asks.
        drops = 0
        while True:
            try:
                _follow(client, opts, run_id, run, deadline, say, merge=merge, props=props)
                break
            except _Dropped as exc:
                drops += 1
                if drops > MAX_RECONNECTS or time.monotonic() > deadline:
                    raise _Fail(f"stream: {exc}; gave up after {drops - 1} reconnects") from None
                say(f"  {exc}; reopening the stream")
        if run.error is not None:
            raise _Fail(f"run: error event: {run.error}")
        memory_id = run.memory_id
        assert memory_id is not None
        if moment is not None and memory_id != moment.id:
            raise _Fail(f"stream: done names memory {memory_id}, not the moment {moment.id}")
        say(f"PASS done: memory {memory_id} in {time.monotonic() - started:.1f}s, "
            f"{len(run.events)} events, every one valid")

        # 6. The memory has its scene.
        res = client.get(f"/families/{fid}/memories/{memory_id}")
        if res.status_code != 200:
            raise _Fail(f"memory: GET memory {memory_id}: {_detail(res)}")
        found: schemas.MemoryResponse = _parse(schemas.MemoryResponse, res, "memory")
        if found.memory.screenplay is None:
            raise _Fail(f"memory: {memory_id} has no screenplay")
        if found.memory.timeline is None:
            raise _Fail(f"memory: {memory_id} has no timeline")
        staged = f"{found.memory.title!r} ({found.memory.year}) has its screenplay and timeline"
        if moment is not None:
            _check_second_telling(moment, found.memory, teller)
            lines = len(found.memory.timeline.narrations[teller].lines)  # type: ignore[index]
            staged += (f"; {teller}'s telling is on it, narrated in {lines} line(s); "
                       f"version {moment.version} -> {found.memory.version}")
        say(f"PASS memory: {staged}")

        # 6b. What the answers asked for is on the memory.
        answered = _check_answers(client, fid, found.memory, run)
        if answered is not None:
            say(f"PASS answers: {answered}")

        # 7. It opens in the teller's book.
        res = client.get(f"/families/{fid}/people/{teller}/book")
        if res.status_code != 200:
            raise _Fail(f"book: GET {teller}'s book: {_detail(res)}")
        book: schemas.BookResponse = _parse(schemas.BookResponse, res, "book")
        if not any(m.id == memory_id for m in book.memories):
            raise _Fail(f"book: {teller}'s book does not list memory {memory_id}")
        say(f"PASS book: {teller}'s book lists it ({len(book.memories)} memories)")
    except _Fail as exc:
        return failed(str(exc))
    except _Timeout:
        return failed(f"timeout: no done event within {opts.timeout:g}s")
    except httpx.ConnectError:
        raise
    except httpx.TimeoutException as exc:
        return failed(f"timeout: the API did not answer in time ({type(exc).__name__})")
    except httpx.HTTPError as exc:
        return failed(f"http: {type(exc).__name__}: {exc}")

    if opts.memory_id is not None:
        return result(True, f"memory {run.memory_id} has {teller}'s version and is in {teller}'s book")
    return result(True, f"memory {run.memory_id} is staged and in {teller}'s book")


# ------------------------------------------------------------------------------ CLI


def _positive(value: str) -> float:
    try:
        seconds = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{value!r} is not a number of seconds") from None
    if not seconds > 0:
        raise argparse.ArgumentTypeError("the timeout must be more than 0 seconds")
    return seconds


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m seed.smoke",
        description="Smoke-test add-a-memory end to end over HTTP against a running API "
        "(start it first; see seed/smoke.py and seed/README.md).",
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="the API (default %(default)s)")
    parser.add_argument("--email", default=DEFAULT_EMAIL, help="account to sign in as (default %(default)s)")
    parser.add_argument("--password", default=None, help="its password (default: the demo password)")
    parser.add_argument("--family", default=demo.DEMO_FAMILY_ID, help="family id (default %(default)s)")
    parser.add_argument("--text", default=DEFAULT_TEXT, help="the telling (default: a short road-trip memory)")
    parser.add_argument("--year", type=int, default=DEFAULT_YEAR, help="year of the moment (default %(default)s)")
    parser.add_argument("--question", default=None, help="the question the telling answers (default none)")
    parser.add_argument(
        "--photo", dest="photos", nargs="+", action="extend", type=pathlib.Path, default=[], metavar="PATH",
        help="photos to attach (default: one small generated PNG)",
    )
    parser.add_argument("--answer", default=DEFAULT_ANSWER, help="reply to the follow-up question")
    parser.add_argument(
        "--merge", choices=MERGE_CHOICES, default="reject",
        help="answer a merge pause: reject (default) or approve",
    )
    parser.add_argument(
        "--props", choices=PROPS_CHOICES, default="keep",
        help="the props review: keep every prop (default), or rename or remove the first one",
    )
    parser.add_argument(
        "--memory-id", default=None, metavar="M",
        help="add a second telling to the existing moment M instead of a new memory",
    )
    parser.add_argument(
        "--timeout", type=_positive, default=DEFAULT_TIMEOUT, help="seconds before giving up (default %(default)g)"
    )
    parser.add_argument("--quiet", action="store_true", help="print only the summary line")
    return parser


def _options(args: argparse.Namespace) -> SmokeOptions:
    return SmokeOptions(
        base_url=args.base_url.rstrip("/"),
        email=args.email,
        password=args.password if args.password is not None else demo.DEMO_PASSWORD,
        family_id=args.family,
        text=args.text,
        year=args.year,
        question=args.question,
        photos=list(args.photos),
        answer=args.answer,
        timeout=args.timeout,
        memory_id=args.memory_id or None,
    )


def _client(opts: SmokeOptions, timeout: httpx.Timeout) -> AbstractContextManager[httpx.Client]:
    """The HTTP client `main` drives the smoke with (tests swap in the in-process app)."""
    return httpx.Client(base_url=opts.base_url, timeout=timeout)


def main(argv: list[str] | None = None) -> int:
    """Run the smoke from the command line. 0 when ok, 1 when a check failed, 2 when unreachable."""
    args = _parser().parse_args(argv)
    opts = _options(args)
    log: Callable[[str], None] = (lambda _text: None) if args.quiet else print
    timeout = httpx.Timeout(
        min(opts.timeout, READ_TIMEOUT_CAP), connect=min(opts.timeout, CONNECT_TIMEOUT_CAP)
    )
    # Only the answer choices that differ from run_smoke's defaults are passed on.
    choices = {k: v for k, v in (("merge", args.merge), ("props", args.props)) if v != _parser().get_default(k)}
    log(f"smoke: {opts.base_url}, family {opts.family_id}, as {opts.email}")
    try:
        with _client(opts, timeout) as client:
            result = run_smoke(client, opts, log=log, **choices)
    except httpx.ConnectError:
        print(REACH_MESSAGE.format(url=opts.base_url), file=sys.stderr)
        return 2

    names = ", ".join(result.events) or "no events"
    if result.ok:
        print(f"smoke OK: run {result.run_id}, memory {result.memory_id}, {len(result.events)} events ({names})")
        return 0
    print(f"smoke FAILED: {result.message} (run {result.run_id or '-'}, events: {names})")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
