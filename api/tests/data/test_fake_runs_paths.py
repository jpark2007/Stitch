"""Every path a presenter might click, over HTTP, on the offline demo app.

`main.create_app()` with STITCH_FAKE_DB=1 (the seeded in-memory demo family) and
STITCH_AGENT=fake: the lifespan itself installs `seed.fake_runs.ScriptedRunManager` behind
`agent.runs` (through `install_fake_runs`, exactly as `uvicorn main:app` does), with
STITCH_FAKE_RUN_STEP near zero. Everything goes through the real routes with TestClient:
submit, the run's Server-Sent Events, answers, the memory, books, questions, invites. No
MongoDB, no keys, no network.

Covered, with every SSE frame validated against schemas.RUN_EVENTS:

1. A new memory with photos answering every pause kind: the follow-up question, the merge
   ("Is Grandpa Joe the Joseph in your family tree?") approved and rejected, and the props
   review kept, renamed and dropped. The memory opens with its timeline and is in both
   demo users' books.
2. "Hear their version": every person DEMO_QUESTIONS asks about a seeded moment answers
   that question the way the web app does (their own account; a claimed account through an
   invite for people who have none), one after another on the same moment.
3. Reattach: disconnect while paused and while running, reopen, and a finished run replays.
4. Wrong, late, foreign and racing answers.
5. Two runs at once in one family and in two families.
6. Text-only and very short submits.
7. `python -m seed.smoke`: one PASS or FAIL line per step, for both paths, without network;
   its defaults reach the merge pause, and --merge approve / --props rename|remove are checked.

The merge pause only fires on a capitalised near-miss name after a family title that the
person can carry: "Grandma and Grandpa were there" never asks it.

Memory counts and ids are never hardcoded: they come from seed.demo at test time.
"""

from __future__ import annotations

import contextlib
import io
import json
import threading
import time
from collections import Counter
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

import agent.runs
import main
import routes.memories
import schemas
from db import client as db_client
from db import memories as memories_db
from seed import demo, fake_runs, smoke
from seed.fake_runs import FOLLOWUP_QUESTION, MERGE_QUESTION, ScriptedRunManager, merge_candidate
from seed.smoke import SmokeOptions, parse_sse, run_smoke

# The #152 checks for a second telling: the new track, the others untouched, the compile.
from test_demo_second_tellings import (  # noqa: E402 - tests/data is on sys.path (rootdir import mode)
    _with_fake_audio,
    assert_is_the_compile,
    assert_others_untouched,
    assert_track,
)

FID = demo.DEMO_FAMILY_ID
ACCOUNTS = {a.person_id: a for a in demo.DEMO_ACCOUNTS}
DAVID = next(a for a in demo.DEMO_ACCOUNTS if a.person_id == "dad")
ANA = next(a for a in demo.DEMO_ACCOUNTS if a.person_id == "mom")
PEOPLE = {p.id: p for p in demo.demo_people()}
DEADLINE = 20.0
HEARTBEAT = 0.05
NEEDS_INPUT = {"question", "props_found", "merge"}
TERMINAL = {"done", "error"}
OUTSIDER_PASSWORD = "outsider-plaintext-pw-9"

# memory id -> [(person id, question text)], in DEMO_QUESTIONS order.
MEMORY_ASKEES: dict[str, list[tuple[str, str]]] = {}
for _pid, _text, _mid in demo.DEMO_QUESTIONS:
    if _mid is not None:
        MEMORY_ASKEES.setdefault(_mid, []).append((_pid, _text))

Frame = tuple[str, dict]


# ------------------------------------------------------------------------------ the app


@dataclass
class Api:
    app: FastAPI
    root: TestClient
    clients: list[TestClient] = field(default_factory=list)

    @property
    def manager(self) -> ScriptedRunManager:
        manager = agent.runs.get_manager()
        assert isinstance(manager, ScriptedRunManager), manager
        return manager

    def client(self) -> TestClient:
        """A new browser: the same app and database, its own cookie jar (no second lifespan)."""
        c = TestClient(self.app)
        self.clients.append(c)
        return c

    def login(self, email: str, password: str = demo.DEMO_PASSWORD) -> TestClient:
        c = self.client()
        res = c.post("/auth/login", json={"email": email, "password": password})
        assert res.status_code == 200, res.text
        return c


@contextlib.contextmanager
def _serve(monkeypatch: pytest.MonkeyPatch, step: float) -> Iterator[Api]:
    monkeypatch.setenv("STITCH_FAKE_DB", "1")
    monkeypatch.setenv("STITCH_AGENT", "fake")
    monkeypatch.setenv("STITCH_FAKE_RUN_STEP", str(step))
    monkeypatch.delenv("STITCH_CORS_ORIGINS", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(routes.memories, "HEARTBEAT_SECONDS", HEARTBEAT)
    db_client.set_db(None)
    app = main.create_app()
    try:
        with TestClient(app) as root:
            api = Api(app=app, root=root)
            assert api.manager._step_seconds == step  # the lifespan's own manager, as served
            try:
                yield api
            finally:
                for c in api.clients:
                    c.close()
    finally:
        agent.runs.configure(None)
        db_client.set_db(None)


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch) -> Iterator[Api]:
    with _serve(monkeypatch, 0.0) as served:
        yield served


@pytest.fixture
def slow_api(monkeypatch: pytest.MonkeyPatch) -> Iterator[Api]:
    """Steps 100 ms apart, so a stream can be dropped while the run is still running."""
    with _serve(monkeypatch, 0.1) as served:
        yield served


# ------------------------------------------------------------------------------ helpers


def _png(color: tuple[int, int, int]) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (4, 4), color).save(out, format="PNG")
    return out.getvalue()


def _json(res: httpx.Response, status: int = 200) -> Any:
    assert res.status_code == status, f"{res.request.method} {res.request.url} -> {res.status_code}: {res.text}"
    return res.json() if res.content else None


def submit(
    client: TestClient,
    text: str,
    *,
    fid: str = FID,
    year: int = 1994,
    photos: int = 0,
    participants: Iterable[str] = (),
    memory_id: str | None = None,
    question: str | None = None,
) -> str:
    form: dict[str, Any] = {"text": text, "year": str(year)}
    if participants:
        form["participantIds"] = list(participants)
    if memory_id is not None:
        form["memoryId"] = memory_id
    if question is not None:
        form["question"] = question
    files = [("photos", (f"p{i}.png", _png((40 * i % 255, 120, 200)), "image/png")) for i in range(photos)]
    body = _json(client.post(f"/families/{fid}/memories", data=form, files=files or None), 202)
    submitted = schemas.SubmitMemoryResponse.model_validate(body, by_alias=True, by_name=False)
    assert submitted.memory_id == memory_id
    return submitted.run_id


def validate(name: str, data: dict) -> schemas.Model:
    """The frame is a RUN_EVENTS event whose data validates by its camelCase aliases only."""
    assert name in schemas.RUN_EVENTS, f"unknown event {name!r}"
    return schemas.RUN_EVENTS[name].model_validate(data, by_alias=True, by_name=False)


def _until(lines: Iterator[str], deadline: float, where: str) -> Iterator[str]:
    for line in lines:  # the server pings every HEARTBEAT, so this checks the clock often
        assert time.monotonic() < deadline, f"{where}: stalled"
        yield line


Answerer = Callable[[str, dict], dict | None]


def read(
    client: TestClient,
    run_id: str,
    *,
    fid: str = FID,
    stop: Callable[[list[Frame]], bool] | None = None,
    answer: Answerer | None = None,
    answer_from: int = 0,
) -> list[Frame]:
    """Open the run's stream once, from its first event, and read validated frames.

    Closes the stream (a disconnect) as soon as `stop(frames)` is true; otherwise reads to
    done or error and checks that the server then ends the stream. `answer(name, data)`
    returns the body to POST for a needs-input event (it must get 204), or None. A replay
    after a reconnect resends the pauses answered before it: like the web app, the frames
    before `answer_from` (the ones this client already handled) are not answered again.
    """
    frames: list[Frame] = []
    deadline = time.monotonic() + DEADLINE
    with client.stream("GET", f"/families/{fid}/runs/{run_id}/stream") as res:
        assert res.status_code == 200, res.read()
        assert res.headers["content-type"].startswith("text/event-stream")
        lines = _until(res.iter_lines(), deadline, f"run {run_id}")
        for name, data in parse_sse(lines):
            validate(name, data)
            frames.append((name, data))
            if stop is not None and stop(frames):
                return frames
            if name in TERMINAL:
                break
            body = answer(name, data) if answer is not None and len(frames) > answer_from else None
            if body is not None:
                res_answer = client.post(f"/families/{fid}/runs/{run_id}/answer", json=body)
                assert res_answer.status_code == 204, f"answer to {name}: {res_answer.status_code} {res_answer.text}"
        else:
            pytest.fail(f"run {run_id}: the stream ended without done or error: {names(frames)}")
        rest = [line for line in lines if line and not line.startswith(":")]
        assert rest == [], f"run {run_id}: the stream went on after {frames[-1][0]}: {rest}"
    return frames


def names(frames: list[Frame]) -> list[str]:
    return [n for n, _ in frames]


def until_name(name: str) -> Callable[[list[Frame]], bool]:
    return lambda frames: frames[-1][0] == name


def logged(api: Api, run_id: str, fid: str = FID) -> list[Frame]:
    """The run's own event log, as the stream sends it (camelCase JSON)."""
    return [(n, json.loads(p.model_dump_json(by_alias=True))) for n, p in api.manager.events(fid, run_id)]


def check_run(frames: list[Frame]) -> None:
    """Starts with a plan, ends with exactly one terminal event, asks each pause once."""
    seq = names(frames)
    assert seq and seq[0] == "plan", seq
    assert seq[-1] in TERMINAL and sum(n in TERMINAL for n in seq) == 1, seq
    counts = Counter(n for n in seq if n in NEEDS_INPUT)
    assert all(c == 1 for c in counts.values()), f"a pause was asked twice: {seq}"


@dataclass
class Replies:
    """Answers for a new-memory run, by pause kind, keeping what each pause showed."""

    text: str = "Just the two of us."
    approve: bool = False
    prop_action: str = "keep"
    prop_name: str | None = None
    seen: dict[str, dict] = field(default_factory=dict)

    def __call__(self, name: str, data: dict) -> dict | None:
        if name in NEEDS_INPUT:
            self.seen[name] = data
        if name == "question":
            return {"kind": "answer", "text": self.text}
        if name == "merge":
            return {"kind": "merge", "approve": self.approve}
        if name == "props_found":
            edit: dict[str, Any] = {"id": data["props"][0]["id"], "action": self.prop_action}
            if self.prop_name is not None:
                edit["name"] = self.prop_name
            return {"kind": "props", "props": [edit, *({"id": p["id"], "action": "keep"} for p in data["props"][1:])]}
        return None


def get_memory(client: TestClient, memory_id: str, fid: str = FID) -> schemas.MemoryResponse:
    return schemas.MemoryResponse.model_validate(
        _json(client.get(f"/families/{fid}/memories/{memory_id}")), by_alias=True, by_name=False
    )


def book_ids(client: TestClient, person_id: str, fid: str = FID) -> set[str]:
    book = schemas.BookResponse.model_validate(
        _json(client.get(f"/families/{fid}/people/{person_id}/book")), by_alias=True, by_name=False
    )
    return {m.id for m in book.memories}


def open_questions(client: TestClient, fid: str = FID) -> list[schemas.Question]:
    return schemas.QuestionsResponse.model_validate(
        _json(client.get(f"/families/{fid}/questions")), by_alias=True, by_name=False
    ).questions


def start_own_family(api: Api, email: str, name: str) -> tuple[TestClient, str, str]:
    """A stranger signs up and starts a family of their own: (client, family id, person id)."""
    c = api.client()
    _json(c.post("/auth/signup", json={"email": email, "password": OUTSIDER_PASSWORD, "name": name}), 201)
    created = schemas.CreateFamilyResponse.model_validate(
        _json(c.post("/families", json={"name": f"{name}'s family", "person": {"name": name, "born": 1980}}), 201),
        by_alias=True, by_name=False,
    )
    return c, created.family.id, created.person.id


def claim(api: Api, person_id: str) -> TestClient:
    """Someone with no account yet joins the demo family by claiming their person (invite)."""
    admin = api.login(DAVID.email)
    invite = schemas.InviteResponse.model_validate(_json(admin.post(f"/families/{FID}/invites"), 201))
    c = api.client()
    preview = schemas.InvitePreview.model_validate(_json(c.get(f"/invites/{invite.code}")))
    assert person_id in {p.id for p in preview.unclaimed_people}, f"{person_id} cannot be claimed"
    email = f"{person_id}@example.test"
    _json(c.post("/auth/signup", json={"email": email, "password": OUTSIDER_PASSWORD, "name": person_id}), 201)
    joined = schemas.JoinResponse.model_validate(
        _json(c.post(f"/invites/{invite.code}/join", json={"personId": person_id})), by_alias=True, by_name=False
    )
    assert joined.person.id == person_id and joined.family.id == FID
    return c


def as_person(api: Api, person_id: str) -> TestClient:
    account = ACCOUNTS.get(person_id)
    return api.login(account.email) if account is not None else claim(api, person_id)


def detail(res: httpx.Response) -> str:
    return res.json().get("detail")


# ------------------------------------------------------------------------------ 1. every pause kind

APPROVED_REPLY = "Grandpa Joe was there too, humming in the back row."
REJECTED_REPLY = "Grandpa Lu came by with a pot of tamales."


def _joe() -> schemas.Person:
    """The tree's Joseph, whom "Grandpa Joe" may be (read from the seed, never assumed)."""
    found = merge_candidate(list(PEOPLE.values()), DAVID.person_id, [], APPROVED_REPLY)
    assert found is not None, "the demo tree should have someone 'Grandpa Joe' may be"
    return found[1]


def test_merge_candidate_rules() -> None:
    people = list(PEOPLE.values())
    said, person = merge_candidate(people, "dad", [], "Then Grandpa Joe sang.")  # type: ignore[misc]
    assert said == "Grandpa Joe" and person.name.startswith("Jo")
    # A name that is someone's first name is that person: nothing to ask.
    assert merge_candidate(people, "dad", [], "Grandma Rosa was there too.") is None
    # The teller, or someone already named, is never asked about.
    assert merge_candidate(people, person.id, [], "Grandpa Joe sang.") is None
    assert merge_candidate(people, "dad", [person.id], "Grandpa Joe sang.") is None
    # No family title, or no one alike: nothing.
    assert merge_candidate(people, "dad", [], "Joe sang. Uncle Xavier came.") is None


@pytest.mark.parametrize("text", [
    "Grandma and Grandpa were there.",
    "Grandpa liked jam.",
    "Grandma always laughed at Grandpa's jokes.",
    "We went to Nana's house, then Grandpa drove us home.",
    "My aunt and uncle came, and Cousin is what we called everyone.",
])
def test_merge_candidate_ignores_lowercase_words_after_a_title(text: str) -> None:
    """Only a capitalised word is a name: an ordinary sentence never asks the merge pause."""
    people = list(PEOPLE.values())
    for teller in PEOPLE:
        assert merge_candidate(people, teller, [], text) is None, (teller, text)


def test_merge_candidate_respects_the_title() -> None:
    """A grandparent title needs someone with a child in the tree, and a gendered title never
    maps to someone whose own record says the other gender. Read from the seed, not assumed."""
    people = list(PEOPLE.values())
    parents = {pid for p in people for pid in p.parent_ids}
    for p in people:
        near = p.name[:2] + "zz"  # a near miss for this person's name, nobody's first name
        for title in ("Grandma", "Grandpa", "Nana", "Abuelo"):
            found = merge_candidate(people, "nobody", [], f"{title} {near} waved.")
            if found is None or found[1].id != p.id:
                continue
            assert p.id in parents, f"{title} {near} -> {p.id}, who has no child in the tree"
            hint = fake_runs._gender_hint(p)
            want = "f" if title in ("Grandma", "Nana") else "m"
            assert hint in (None, want), f"{title} {near} -> {p.id}, whose record says {hint}"
    # The reviewer's cases: a grandmother title is never a grandfather, and never a child.
    luis = next(p for p in people if p.name.startswith("Lu"))
    lily = next(p for p in people if p.name.startswith("Lil"))
    assert lily.id not in parents
    assert merge_candidate(people, "dad", [], "Grandma Lu came by.") is None
    assert merge_candidate(people, "dad", [], "Nana Lila hugged us.") is None
    assert merge_candidate(people, "dad", [], "Grandpa Lila waved.") is None
    found = merge_candidate(people, "dad", [], "Grandpa Lu came by.")
    assert found is not None and found[1].id == luis.id


def test_new_memory_answers_every_pause_kind(api: Api) -> None:
    david, ana = api.login(DAVID.email), api.login(ANA.email)
    joe = _joe()
    assert ANA.person_id != joe.id

    # A. Two photos, Ana named; the follow-up answered, the merge approved, the prop kept.
    replies = Replies(text=APPROVED_REPLY, approve=True)
    run_a = submit(david, "We sang on the church steps after the service. The books went everywhere.",
                   photos=2, participants=[ANA.person_id])
    frames = read(david, run_a, answer=replies)
    check_run(frames)
    assert frames == logged(api, run_a), "the stream is the run's own log, in order, nothing extra"
    seq = names(frames)
    assert seq.index("question") < seq.index("merge") < seq.index("props_found") < seq.index("scene_ready")
    assert seq[-2:] == ["scene_ready", "done"]
    assert replies.seen["question"] == {"text": FOLLOWUP_QUESTION}
    assert replies.seen["merge"] == {
        "text": MERGE_QUESTION.format(said="Grandpa Joe", candidate=joe.name),
        "personId": joe.id, "candidateName": joe.name,
    }
    chip = replies.seen["props_found"]["props"][0]
    memory_id = frames[-1][1]["memoryId"]
    assert frames[-2][1] == {"memoryId": memory_id, "version": 1}

    found = get_memory(david, memory_id)
    memory = found.memory
    assert memory.screenplay is not None and memory.timeline is not None and memory.timeline.duration > 0
    assert memory.screenplay.memory_id == memory_id
    [telling] = memory.tellings
    assert telling.author_id == DAVID.person_id and APPROVED_REPLY in telling.text
    assert len(telling.photo_ids) == 2
    for photo in telling.photo_ids:
        res = david.get(f"/families/{FID}/media/{photo}")
        assert res.status_code == 200 and res.headers["content-type"].startswith("image/")
    assert {DAVID.person_id, ANA.person_id, joe.id} <= set(memory.participant_ids)
    assert chip["id"] in memory.prop_ids and chip["id"] in memory.screenplay.props
    assert memory.timeline.narrations is not None and DAVID.person_id in memory.timeline.narrations
    assert_is_the_compile(memory)

    # It opens in both demo users' books (README weekend scope), and in Joseph's.
    assert memory_id in book_ids(david, DAVID.person_id)
    assert memory_id in book_ids(ana, ANA.person_id)
    assert memory_id in book_ids(ana, joe.id)
    # Ana is asked for her version; Joseph, who has died, is not (agent/prompts/linker.md).
    assert any(q.memory_id == memory_id for q in open_questions(ana))
    if joe.died is not None:
        assert not memories_db.open_questions(db_client.get_db(), FID, joe.id)

    # B. Ana tells one; the merge rejected, the prop renamed.
    lu = merge_candidate(list(PEOPLE.values()), ANA.person_id, [], REJECTED_REPLY)
    assert lu is not None
    replies = Replies(text=REJECTED_REPLY, approve=False, prop_action="rename", prop_name="Mom's hymnals")
    run_b = submit(ana, "Every Easter we lined up for the photo and nobody could stand still.", photos=1)
    frames = read(ana, run_b, answer=replies)
    check_run(frames)
    assert {"question", "merge", "props_found"} <= set(names(frames)) and frames[-1][0] == "done"
    assert replies.seen["merge"]["personId"] == lu[1].id
    memory_b = get_memory(ana, frames[-1][1]["memoryId"]).memory
    assert lu[1].id not in memory_b.participant_ids, "a rejected merge links nobody"
    assert REJECTED_REPLY in memory_b.tellings[0].text
    renamed = replies.seen["props_found"]["props"][0]["id"]
    props = schemas.PropsResponse.model_validate(_json(ana.get(f"/families/{FID}/props")), by_alias=True).props
    assert next(p for p in props if p.id == renamed).name == "Mom's hymnals"
    assert renamed in memory_b.prop_ids
    assert memory_b.id in book_ids(ana, ANA.person_id)

    # C. David again; no one to merge, the prop dropped.
    replies = Replies(text="Only the two of us.", prop_action="remove")
    run_c = submit(david, "The night the power went out we read by candlelight.", photos=1)
    frames = read(david, run_c, answer=replies)
    check_run(frames)
    assert "merge" not in names(frames) and frames[-1][0] == "done"
    dropped = replies.seen["props_found"]["props"][0]["id"]
    memory_c = get_memory(david, frames[-1][1]["memoryId"]).memory
    assert dropped not in memory_c.prop_ids
    assert memory_c.screenplay is not None and dropped not in memory_c.screenplay.props
    assert memory_c.timeline is not None
    assert_is_the_compile(memory_c)


def test_approving_a_merge_with_nobody_named_finishes(api: Api) -> None:
    """Regression: approving the merge of someone who has died, with no one else named, used
    to fail the run with KeyError('Ask the others for their version') after the scene was
    staged. Now the run finishes, they are in the moment, and nobody asks them anything."""
    joe = _joe()
    david = api.login(DAVID.email)
    run_id = submit(david, "We sang on the church steps after the service.", photos=1)
    frames = read(david, run_id, answer=Replies(text=APPROVED_REPLY, approve=True))
    check_run(frames)
    assert "merge" in names(frames) and frames[-1][0] == "done", frames[-2:]
    memory = get_memory(david, frames[-1][1]["memoryId"]).memory
    assert joe.id in memory.participant_ids
    plans = [schemas.PlanEvent.model_validate(d) for n, d in frames if n == "plan"]
    asks = [t for t in plans[-1].todos if t.text == fake_runs.ASK_OTHERS]
    assert len(asks) == (0 if joe.died is not None else 1)
    if joe.died is not None:
        assert not memories_db.open_questions(db_client.get_db(), FID, joe.id)


def test_an_ordinary_sentence_after_a_title_asks_no_merge(api: Api) -> None:
    """Over HTTP: "Grandma and Grandpa were there too." in the submit and the reply is not a
    name, so the run never asks "Is Grandma and the Ana in your family tree?"."""
    david = api.login(DAVID.email)
    run_id = submit(david, "Grandpa liked jam. We sang on the church steps.", photos=1)
    frames = read(david, run_id, answer=Replies(text="Grandma and Grandpa were there too.", approve=True))
    check_run(frames)
    assert "merge" not in names(frames) and frames[-1][0] == "done", names(frames)
    memory = get_memory(david, frames[-1][1]["memoryId"]).memory
    # The same people as a run with no family title at all: nobody was merged in.
    control = read(david, submit(david, "We sang on the church steps.", photos=1), answer=Replies(text="Just us."))
    assert "merge" not in names(control) and control[-1][0] == "done"
    assert memory.participant_ids == get_memory(david, control[-1][1]["memoryId"]).memory.participant_ids


def test_linker_skips_people_who_have_died(api: Api) -> None:
    """A named participant who has died is in the moment but is never asked for their version."""
    gone = next(p for p in PEOPLE.values() if p.died is not None)
    living = ANA.person_id
    david = api.login(DAVID.email)
    run_id = submit(david, "We all went to the fair.", participants=[gone.id, living])
    frames = read(david, run_id, answer=Replies())
    assert frames[-1][0] == "done"
    memory_id = frames[-1][1]["memoryId"]
    memory = get_memory(david, memory_id).memory
    assert {gone.id, living} <= set(memory.participant_ids)
    db = db_client.get_db()
    assert not [q for q in memories_db.open_questions(db, FID, gone.id) if q.memory_id == memory_id]
    assert [q for q in memories_db.open_questions(db, FID, living) if q.memory_id == memory_id]


OTHERS = [pid for pid in demo.DEMO_PERSON_IDS if pid != DAVID.person_id]


@pytest.mark.parametrize("person_id", OTHERS, ids=OTHERS)
def test_anyone_in_the_tree_can_be_in_the_scene(api: Api, person_id: str) -> None:
    """Whoever is named first plays the sample's second puppet, at their own walking speed.

    Grandpa Joseph "walks slowly with a cane": at the sample's pace he was still walking when
    the books had to be picked up, the scene did not compile, and the run ended in an error
    with nothing saved (approving "Is Grandpa Joe the Joseph in your family tree?" did that
    too). The director now slows the scene down until everyone makes it.
    """
    david = api.login(DAVID.email)
    run_id = submit(david, "We sang on the church steps. The books went everywhere.", participants=[person_id])
    frames = read(david, run_id, answer=Replies())
    check_run(frames)
    assert "compile_error" not in names(frames) and frames[-1][0] == "done", frames[-1]
    memory = get_memory(david, frames[-1][1]["memoryId"]).memory
    assert memory.screenplay is not None and person_id in memory.screenplay.cast
    assert_is_the_compile(memory)


def test_pace_stretches_start_times_only() -> None:
    sp = demo.load_sample().memory.screenplay
    assert sp is not None
    slow = fake_runs.pace(sp, 2.0)
    assert sp == demo.load_sample().memory.screenplay, "pace is pure"
    for before, after in zip(sp.shots, slow.shots, strict=True):
        assert [a.t * 2 for a in before.actions] == pytest.approx([a.t for a in after.actions])
        assert [a.dur for a in before.actions] == [a.dur for a in after.actions]
        assert [c.t * 2 for c in before.camera] == pytest.approx([c.t for c in after.camera])
        assert [line.t * 2 for line in before.narration] == pytest.approx([line.t for line in after.narration])
    for author, by_shot in (sp.narrations or {}).items():
        for shot_id, lines in by_shot.items():
            got = [line.t for line in (slow.narrations or {})[author][shot_id]]
            assert [line.t * 2 for line in lines] == pytest.approx(got)
    assert fake_runs.pace(sp, 1.0) == sp


# ------------------------------------------------------------------------------ 2. second tellings


def _second_telling_text(person_id: str) -> str:
    return (
        f"I remember it my own way, as {person_id} would. Everyone was talking at once! "
        "Then we all turned around and laughed."
    )


def _no_pauses(name: str, _data: dict) -> None:
    assert name not in NEEDS_INPUT, f"a second telling paused on {name}"


@pytest.mark.parametrize("memory_id", sorted(MEMORY_ASKEES), ids=sorted(MEMORY_ASKEES))
def test_every_askee_tells_their_version_through_their_question(api: Api, memory_id: str) -> None:
    """Each person asked about this moment answers their question as the web app does, in turn."""
    assert memory_id in demo.DEMO_MEMORY_IDS
    for person_id, question_text in MEMORY_ASKEES[memory_id]:
        client = as_person(api, person_id)
        [question] = [q for q in open_questions(client) if q.memory_id == memory_id]
        assert question.text == question_text and question.person_id == person_id
        _with_fake_audio(memory_id)  # as if TTS had run: keep_audio has audio to keep or drop
        before = get_memory(client, memory_id).memory
        assert person_id not in {t.author_id for t in before.tellings}
        text = _second_telling_text(person_id)

        run_id = submit(client, text, year=before.year, memory_id=memory_id, question=question.text)
        frames = read(client, run_id, answer=_no_pauses)
        check_run(frames)
        assert frames == logged(api, run_id)
        assert frames[-2:] == [("scene_ready", {"memoryId": memory_id, "version": before.version + 1}),
                               ("done", {"memoryId": memory_id})]

        after = get_memory(client, memory_id).memory
        assert [t.author_id for t in after.tellings][: len(before.tellings)] == [t.author_id for t in before.tellings]
        [mine] = [t for t in after.tellings if t.author_id == person_id]
        assert (mine.text, mine.question) == (text, question.text)
        assert after.version == before.version + 1
        assert_track(after, person_id, text)  # one line a sentence, word timings in timeline time
        assert after.timeline is not None and after.timeline.narrations is not None
        track = after.timeline.narrations[person_id]
        assert any(line.cue for line in track.lines), f"{person_id}'s lines are not cued to the action"
        assert track.audio is None
        assert_others_untouched(before, after, person_id)
        assert_is_the_compile(after)
        assert memory_id in book_ids(client, person_id)
        assert not [q for q in open_questions(client) if q.memory_id == memory_id], "the question is closed"

    tellers = {t.author_id for t in get_memory(api.login(DAVID.email), memory_id).memory.tellings}
    assert {pid for pid, _ in MEMORY_ASKEES[memory_id]} <= tellers


# ------------------------------------------------------------------------------ 3. reattach


def test_reattach_while_paused_and_after_done(api: Api) -> None:
    david = api.login(DAVID.email)
    run_id = submit(david, "We built a snowman taller than Dad.", photos=1)

    first = read(david, run_id, stop=until_name("question"))
    assert api.manager.status(FID, run_id) == "waiting"
    again = read(david, run_id, stop=until_name("question"))
    assert again == first, "a reopened stream replays the same events, once each"

    answer = {"kind": "answer", "text": "Just us and the dog."}
    assert david.post(f"/families/{FID}/runs/{run_id}/answer", json=answer).status_code == 204
    repeat = david.post(f"/families/{FID}/runs/{run_id}/answer", json=answer)
    assert repeat.status_code == 409, "an answered question is not asked again"

    to_props = read(david, run_id, stop=until_name("props_found"))
    assert to_props[: len(first)] == first
    assert names(to_props).count("question") == 1 and "question" not in names(to_props[len(first):])

    rest = read(david, run_id, answer=Replies(), answer_from=len(to_props) - 1)
    check_run(rest)
    assert rest[: len(to_props)] == to_props and rest == logged(api, run_id)
    assert rest[-1][0] == "done"

    replay = read(api.login(ANA.email), run_id)  # a finished run replays to done for any member
    assert replay == rest


def test_reattach_while_running(slow_api: Api) -> None:
    api = slow_api
    david = api.login(DAVID.email)
    run_id = submit(david, "The summer the creek flooded the garden.")
    early = read(david, run_id, stop=lambda frames: len(frames) == 2)
    assert not NEEDS_INPUT & set(names(early))  # dropped mid-run, before the first pause
    later = read(david, run_id, stop=until_name("question"))
    assert later[:2] == early
    assert later == logged(api, run_id)
    full = read(david, run_id, answer=Replies(), answer_from=len(later) - 1)
    check_run(full)
    assert full[: len(later)] == later and full == logged(api, run_id)


# ------------------------------------------------------------------------------ 4. wrong answers


def _answer(client: TestClient, run_id: str, body: dict, fid: str = FID) -> httpx.Response:
    return client.post(f"/families/{fid}/runs/{run_id}/answer", json=body)


WRONG_KIND = getattr(routes.memories, "ANSWER_WRONG_KIND", None)
"""#154's fixed 409 sentence for a wrong-kind answer, when it has landed."""


def _wrong_kind(res: httpx.Response) -> None:
    assert res.status_code == 409, res.text
    if WRONG_KIND is not None:
        assert detail(res) == WRONG_KIND


def test_wrong_late_and_foreign_answers(api: Api) -> None:
    david, ana = api.login(DAVID.email), api.login(ANA.email)
    run_id = submit(david, "We missed the bus and walked home in the rain.")
    read(david, run_id, stop=until_name("question"))

    _wrong_kind(_answer(david, run_id, {"kind": "props", "props": []}))
    _wrong_kind(_answer(david, run_id, {"kind": "merge", "approve": True}))
    assert _answer(david, run_id, {"kind": "answer", "text": "  "}).status_code == 422
    assert api.manager.status(FID, run_id) == "waiting", "a refused answer leaves the run paused"

    other = _answer(ana, run_id, {"kind": "answer", "text": "Me too!"})
    assert other.status_code == 403 and detail(other) == routes.memories.NOT_RUN_OWNER

    outsider, own_fid, _pid = start_own_family(api, "stranger@example.test", "Stranger")
    for fid in (own_fid, FID):
        res = _answer(outsider, run_id, {"kind": "answer", "text": "hi"}, fid=fid)
        assert res.status_code == 404, (fid, res.text)
        assert outsider.get(f"/families/{fid}/runs/{run_id}/stream").status_code == 404
    assert detail(_answer(outsider, run_id, {"kind": "answer", "text": "hi"}, fid=own_fid)) == \
        routes.memories.RUN_NOT_FOUND
    assert _answer(david, "no-such-run", {"kind": "answer", "text": "hi"}).status_code == 404

    assert _answer(david, run_id, {"kind": "answer", "text": APPROVED_REPLY}).status_code == 204
    read(david, run_id, stop=until_name("merge"))
    _wrong_kind(_answer(david, run_id, {"kind": "answer", "text": "yes"}))
    _wrong_kind(_answer(david, run_id, {"kind": "props", "props": []}))
    assert _answer(david, run_id, {"kind": "merge"}).status_code == 422
    assert _answer(david, run_id, {"kind": "merge", "approve": False}).status_code == 204
    paused = read(david, run_id, stop=until_name("props_found"))
    _wrong_kind(_answer(david, run_id, {"kind": "answer", "text": "yes"}))
    _wrong_kind(_answer(david, run_id, {"kind": "merge", "approve": True}))
    frames = read(david, run_id, answer=Replies(), answer_from=len(paused) - 1)
    check_run(frames)
    assert frames[-1][0] == "done"

    late = _answer(david, run_id, {"kind": "answer", "text": "One more thing."})
    assert late.status_code == 409
    assert detail(late) == routes.memories.NOT_WAITING, "the scripted studio says not waiting like the real one"
    assert run_id not in detail(late)


def _race(clients: list[TestClient], run_id: str, body: dict) -> list[int]:
    barrier = threading.Barrier(len(clients))
    codes: list[int] = []
    lock = threading.Lock()

    def post(c: TestClient) -> None:
        barrier.wait()
        code = _answer(c, run_id, body).status_code
        with lock:
            codes.append(code)

    threads = [threading.Thread(target=post, args=(c,)) for c in clients]
    for t in threads:
        t.start()
    for t in threads:
        t.join(DEADLINE)
    return sorted(codes)


def test_double_answers_race_for_one_pause(api: Api) -> None:
    david = api.login(DAVID.email)
    racers = [david, *(api.login(DAVID.email) for _ in range(3))]  # the same user, four tabs
    for i in range(3):
        run_id = submit(david, f"Race number {i}: we all ran for the last seat.")
        read(david, run_id, stop=until_name("question"))
        codes = _race(racers, run_id, {"kind": "answer", "text": f"Reply {i}."})
        assert codes == [204, 409, 409, 409], codes
        frames = read(david, run_id, stop=until_name("props_found"))
        assert names(frames).count("question") == 1
        codes = _race(racers, run_id, {"kind": "props", "props": []})
        assert codes == [204, 409, 409, 409], codes
        frames = read(david, run_id)
        check_run(frames)
        assert frames[-1][0] == "done"
        [telling] = get_memory(david, frames[-1][1]["memoryId"]).memory.tellings
        assert telling.text.count("Reply ") == 1, "exactly one racing answer was taken"


# ------------------------------------------------------------------------------ 5. concurrent runs


def _drive_together(jobs: list[tuple[Any, ...]]) -> list[list[Frame]]:
    """Read every (client, fid, run, answerer[, answer_from]) stream at once, each on its own thread."""
    results: list[list[Frame] | BaseException | None] = [None] * len(jobs)
    barrier = threading.Barrier(len(jobs))

    def go(i: int) -> None:
        client, fid, run_id, answerer, *answer_from = jobs[i]
        try:
            barrier.wait()
            results[i] = read(client, run_id, fid=fid, answer=answerer, answer_from=sum(answer_from))
        except BaseException as exc:  # noqa: BLE001 - re-raised on the test thread
            results[i] = exc

    threads = [threading.Thread(target=go, args=(i,)) for i in range(len(jobs))]
    for t in threads:
        t.start()
    for t in threads:
        t.join(DEADLINE * 2)
    for r in results:
        if isinstance(r, BaseException):
            raise r
        assert r is not None, "a stream did not finish"
    return results  # type: ignore[return-value]


def test_two_runs_in_one_family_do_not_cross(api: Api) -> None:
    david, ana = api.login(DAVID.email), api.login(ANA.email)
    run_d = submit(david, "Dad's version: the kite went over the fence.", photos=1)
    run_a = submit(ana, "Mom's version: the cake slid off the plate.", photos=2)
    assert run_d != run_a
    out_d, out_a = _drive_together([
        (david, FID, run_d, Replies(text="Dad's reply.")),
        (ana, FID, run_a, Replies(text="Mom's reply.")),
    ])
    for frames, run_id in ((out_d, run_d), (out_a, run_a)):
        check_run(frames)
        assert frames == logged(api, run_id)
        assert frames[-1][0] == "done"
    mem_d, mem_a = out_d[-1][1]["memoryId"], out_a[-1][1]["memoryId"]
    assert mem_d != mem_a
    d, a = get_memory(david, mem_d).memory, get_memory(ana, mem_a).memory
    assert [t.author_id for t in d.tellings] == [DAVID.person_id] and "Dad's reply." in d.tellings[0].text
    assert [t.author_id for t in a.tellings] == [ANA.person_id] and "Mom's reply." in a.tellings[0].text
    assert "Mom's" not in d.tellings[0].text and "Dad's" not in a.tellings[0].text
    assert len(d.tellings[0].photo_ids) == 1 and len(a.tellings[0].photo_ids) == 2


def test_runs_in_two_families_do_not_cross(api: Api) -> None:
    david = api.login(DAVID.email)
    outsider, own_fid, own_pid = start_own_family(api, "neighbor@example.test", "Neighbor")
    run_d = submit(david, "We painted the fence blue.")
    run_o = submit(outsider, "We planted tomatoes by the porch.", fid=own_fid, photos=1)
    out_d, out_o = _drive_together([
        (david, FID, run_d, Replies(text="Dad's reply.")),
        (outsider, own_fid, run_o, Replies(text="Neighbor's reply.")),
    ])
    check_run(out_d)
    check_run(out_o)
    assert out_d == logged(api, run_d) and out_o == logged(api, run_o, own_fid)
    mem_d, mem_o = out_d[-1][1]["memoryId"], out_o[-1][1]["memoryId"]
    own = get_memory(outsider, mem_o, own_fid).memory
    assert own.family_id == own_fid and [t.author_id for t in own.tellings] == [own_pid]
    assert own.timeline is not None and "Neighbor's reply." in own.tellings[0].text
    assert outsider.get(f"/families/{FID}/memories/{mem_d}").status_code == 404
    assert outsider.get(f"/families/{own_fid}/memories/{mem_d}").status_code == 404
    assert david.get(f"/families/{FID}/memories/{mem_o}").status_code == 404
    # Each run's stream is only reachable from its own family.
    assert outsider.get(f"/families/{own_fid}/runs/{run_d}/stream").status_code == 404
    assert david.get(f"/families/{FID}/runs/{run_o}/stream").status_code == 404


# ------------------------------------------------------------------------------ 6. short submits


@pytest.mark.parametrize("text", ["We drove to the coast and ate cold fries.", "Snow.", "Hi", "!?"])
def test_text_only_and_very_short_submits(api: Api, text: str) -> None:
    david = api.login(DAVID.email)
    run_id = submit(david, text)
    frames = read(david, run_id, answer=Replies(text="Me."))
    check_run(frames)
    assert frames[-1][0] == "done", frames[-1]
    memory = get_memory(david, frames[-1][1]["memoryId"]).memory
    [telling] = memory.tellings
    assert telling.photo_ids == [] and telling.text.startswith(text)
    assert memory.title.strip()
    assert memory.timeline is not None and memory.timeline.duration > 0
    track = (memory.timeline.narrations or {}).get(DAVID.person_id)
    assert track is not None and track.lines and all(line.words for line in track.lines)
    assert memory.id in book_ids(david, DAVID.person_id)
    assert_is_the_compile(memory)


# ------------------------------------------------------------------------------ 7. the smoke CLI


def _smoke_opts(**overrides: Any) -> SmokeOptions:
    values: dict[str, Any] = {
        "base_url": "http://testserver",
        "email": DAVID.email,
        "password": demo.DEMO_PASSWORD,
        "family_id": FID,
        "text": smoke.DEFAULT_TEXT,
        "year": smoke.DEFAULT_YEAR,
        "question": None,
        "photos": [],
        "answer": smoke.DEFAULT_ANSWER,
        "timeout": 30.0,
    }
    values.update(overrides)
    return SmokeOptions(**values)


def _steps(lines: list[str]) -> list[tuple[str, str]]:
    """(PASS or FAIL, step) for each step line."""
    out = []
    for line in lines:
        mark, _, rest = line.partition(" ")
        if mark in ("PASS", "FAIL"):
            out.append((mark, rest.split(":", 1)[0]))
    return out


def test_smoke_defaults_reach_the_merge_pause() -> None:
    """With default flags the scripted studio asks the merge pause (the default telling)."""
    found = merge_candidate(list(PEOPLE.values()), DAVID.person_id, [], smoke.DEFAULT_TEXT + " " + smoke.DEFAULT_ANSWER)
    assert found is not None and found[1].id == _joe().id
    assert smoke.DEFAULT_EMAIL == DAVID.email


def test_smoke_new_memory_prints_a_line_per_step(api: Api) -> None:
    lines: list[str] = []
    result = run_smoke(api.client(), _smoke_opts(), log=lines.append)
    assert result.ok, result.message
    steps = _steps(lines)
    assert [s for s in steps if s[0] == "FAIL"] == []
    assert [name for _, name in steps] == [
        "login", "membership", "teller", "submit", "question", "merge", "props_found", "done", "memory", "book"
    ]
    assert result.events.count("merge") == 1
    memory = get_memory(api.login(DAVID.email), result.memory_id).memory  # type: ignore[arg-type]
    assert _joe().id not in memory.participant_ids, "the smoke never merges two people"


def test_smoke_second_telling_prints_a_line_per_step(api: Api) -> None:
    memory_id, (person_id, question) = next(
        (mid, askee) for mid, askees in MEMORY_ASKEES.items() for askee in askees if askee[0] in ACCOUNTS
    )
    lines: list[str] = []
    opts = _smoke_opts(email=ACCOUNTS[person_id].email, memory_id=memory_id, question=question,
                       text=_second_telling_text(person_id))
    result = run_smoke(api.client(), opts, log=lines.append)
    assert result.ok, result.message
    assert [name for _, name in _steps(lines)] == [
        "login", "membership", "teller", "moment", "submit", "done", "memory", "book"
    ]
    assert all(mark == "PASS" for mark, _ in _steps(lines))


def test_smoke_failure_is_one_fail_line(api: Api) -> None:
    lines: list[str] = []
    result = run_smoke(api.client(), _smoke_opts(memory_id="no_such_moment"), log=lines.append)
    assert not result.ok
    assert _steps(lines)[-1] == ("FAIL", "moment")
    assert [mark for mark, _ in _steps(lines)].count("FAIL") == 1


def test_smoke_main_against_the_in_process_app(
    api: Api, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The CLI end to end, its httpx client swapped for the in-process app (no network)."""
    client = api.client()
    monkeypatch.setattr(smoke, "_client", lambda _opts, _timeout: contextlib.nullcontext(client))
    assert smoke.main([]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[-1].startswith("smoke OK: run ")
    assert ("PASS", "merge") in _steps(out) and not [s for s in _steps(out) if s[0] == "FAIL"]
    assert demo.DEMO_PASSWORD not in "\n".join(out)


@pytest.mark.parametrize(("flags", "prop_action"), [
    (["--merge", "approve", "--props", "rename"], "rename"),
    (["--props", "remove"], "remove"),
])
def test_smoke_rehearses_approve_rename_and_remove(
    api: Api, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    flags: list[str], prop_action: str,
) -> None:
    """--merge approve and --props rename|remove: the answers are sent, and checked on the memory."""
    client = api.client()
    monkeypatch.setattr(smoke, "_client", lambda _opts, _timeout: contextlib.nullcontext(client))
    assert smoke.main(flags) == 0
    out = capsys.readouterr().out.splitlines()
    steps = _steps(out)
    assert not [s for s in steps if s[0] == "FAIL"], out
    assert [name for _, name in steps] == [
        "login", "membership", "teller", "submit", "question", "merge", "props_found", "done", "memory",
        "answers", "book",
    ]
    memory_id = out[-1].split("memory ", 1)[1].split(",", 1)[0]
    memory = get_memory(api.login(DAVID.email), memory_id).memory
    joe = _joe()
    assert (joe.id in memory.participant_ids) == ("approve" in flags)
    answers = next(line for line in out if line.startswith("PASS answers:"))
    props = schemas.PropsResponse.model_validate(_json(client.get(f"/families/{FID}/props"))).props
    if prop_action == "remove":
        assert "left out" in answers and memory.prop_ids == []
    else:
        renamed = [p for p in props if p.id in memory.prop_ids]
        assert renamed and renamed[0].name.endswith(" (smoke)"), [p.name for p in renamed]
        assert "renamed to" in answers and "merged into the moment" in answers


def test_smoke_rejects_unknown_answer_choices(api: Api) -> None:
    with pytest.raises(ValueError):
        run_smoke(api.client(), _smoke_opts(), merge="maybe")
    with pytest.raises(ValueError):
        run_smoke(api.client(), _smoke_opts(), props="burn")


def test_two_runs_in_a_new_family_share_one_copy_of_the_scene(api: Api, monkeypatch: pytest.MonkeyPatch) -> None:
    """Two tabs of a brand-new family finish their prop reviews at once: the family gets ONE copy
    of the sample's prop and set, and both memories use it (no duplicate under a random id)."""
    outsider, fid, _pid = start_own_family(api, "twotabs@example.test", "Tabs")
    second = api.client()
    _json(second.post("/auth/login", json={"email": "twotabs@example.test", "password": OUTSIDER_PASSWORD}))
    runs = [submit(outsider, "The day the tent fell on us.", fid=fid),
            submit(second, "The night we lost the car keys.", fid=fid)]
    paused = [read(c, run_id, fid=fid, answer=Replies(), stop=until_name("props_found"))
              for c, run_id in zip((outsider, second), runs, strict=True)]

    # Widen the window between "does the family have its copy?" and "insert it".
    real_get_prop, real_get_set = fake_runs.props_db.get_prop, fake_runs.props_db.get_set

    def slow(get: Callable[..., Any]) -> Callable[..., Any]:
        def wrapped(*args: Any, **kwargs: Any) -> Any:
            found = get(*args, **kwargs)
            if threading.current_thread().name.startswith("scripted-run-"):
                time.sleep(0.15)
            return found
        return wrapped

    monkeypatch.setattr(fake_runs.props_db, "get_prop", slow(real_get_prop))
    monkeypatch.setattr(fake_runs.props_db, "get_set", slow(real_get_set))
    outs = _drive_together([
        (outsider, fid, runs[0], Replies(), len(paused[0]) - 1),
        (second, fid, runs[1], Replies(), len(paused[1]) - 1),
    ])
    memories = [get_memory(outsider, frames[-1][1]["memoryId"], fid).memory for frames in outs]
    assert all(frames[-1][0] == "done" for frames in outs)
    props = schemas.PropsResponse.model_validate(_json(outsider.get(f"/families/{fid}/props"))).props
    sets = schemas.SetsResponse.model_validate(_json(outsider.get(f"/families/{fid}/sets"))).sets
    own_props = [p for p in props if p.family_id == fid]
    own_sets = [s for s in sets if s.family_id == fid]
    assert len(own_props) == 1, [p.id for p in own_props]
    assert len(own_sets) == 1, [s.id for s in own_sets]
    assert memories[0].prop_ids == memories[1].prop_ids == [own_props[0].id]
    assert memories[0].set_ids == memories[1].set_ids == [own_sets[0].id]
