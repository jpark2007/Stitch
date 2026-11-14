"""The weekend demo path (README "Scope for the weekend"), end to end over HTTP.

Everything goes through `main.create_app()` with STITCH_FAKE_DB=1 (the lifespan installs the
seeded mongomock database) and `seed.fake_runs.ScriptedRunManager` behind `agent.runs`, so
the whole path runs offline: no MongoDB, no model keys, no network.

- Two new people: signup, start a family, add a relative, invite, preview, join (by adding
  a new person, and by claiming the relative), /me, tree, submit a memory with two photos,
  the run's SSE stream through question -> answer -> props_found -> answer -> done, the
  scene in both users' books, the memory, threads and the uploaded photos.
- The stage demo: the seeded demo family's hymn-book memory in David's and Ana's books.
- A second telling of a moment ends in done with no question and no prop review.

Every response body is checked for `passwordHash` and the plaintext passwords used here.
"""

from __future__ import annotations

import io
import json
import time
from collections.abc import Iterator
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
from db import media as media_db
from seed import demo
from seed.fake_runs import FOLLOWUP_QUESTION, ScriptedRunManager

ALICE_PASSWORD = "alice-plaintext-pw-1"
BOB_PASSWORD = "bob-plaintext-pw-22"
STREAM_DEADLINE_SECONDS = 20.0
MAX_EVENTS = 200
HEARTBEAT_SECONDS = 0.05  # the server pings this often, so a stalled stream still hits the deadline
FORBIDDEN = ("passwordHash", "password_hash", ALICE_PASSWORD, BOB_PASSWORD, demo.DEMO_PASSWORD)


# ------------------------------------------------------------------------------ response guard


def _assert_clean(text: str, where: str) -> None:
    for secret in FORBIDDEN:
        assert secret not in text, f"{where} leaked {secret!r}"


@dataclass
class Guard:
    """Counts what it checked, so the no-secrets assertions cannot pass vacuously."""

    checked: int = 0
    stream_frames: int = 0
    seen: list[str] = field(default_factory=list)

    def hook(self, response: httpx.Response) -> None:
        """httpx response hook: read and scan every non-streaming body."""
        if response.headers.get("content-type", "").startswith("text/event-stream"):
            return  # scanned frame by frame in read_events()
        response.read()
        _assert_clean(response.content.decode("utf-8", "replace"), f"{response.request.method} {response.request.url}")
        _assert_clean(str(response.headers), f"headers of {response.request.url}")
        self.checked += 1
        self.seen.append(f"{response.request.method} {response.request.url.path}")


def _guarded(client: TestClient, guard: Guard) -> TestClient:
    client.event_hooks = {"request": [], "response": [guard.hook]}
    return client


# ------------------------------------------------------------------------------ fixtures


@dataclass
class Demo:
    app: FastAPI
    manager: ScriptedRunManager
    guard: Guard
    alice: TestClient
    bob: TestClient


@pytest.fixture
def demo_app(monkeypatch: pytest.MonkeyPatch) -> Iterator[Demo]:
    monkeypatch.setenv("STITCH_FAKE_DB", "1")
    # fake, not off: off makes every run route answer 503. The lifespan's own scripted
    # manager is swapped for this test's below (step 0, and the test holds a handle on it).
    monkeypatch.setenv("STITCH_AGENT", "fake")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("STITCH_CORS_ORIGINS", raising=False)
    monkeypatch.setattr(routes.memories, "HEARTBEAT_SECONDS", HEARTBEAT_SECONDS)
    db_client.set_db(None)

    app = main.create_app()
    guard = Guard()
    manager: ScriptedRunManager | None = None
    try:
        # The first client runs the lifespan (installs the seeded mongomock db). The second
        # one shares the app and its db but keeps its own cookie jar.
        with _guarded(TestClient(app), guard) as alice:
            assert db_client.get_db().name, "the lifespan should have installed the fake db"
            auto = agent.runs._manager  # the lifespan's scripted manager (STITCH_AGENT=fake)
            assert isinstance(auto, ScriptedRunManager), auto
            auto.shutdown()
            manager = ScriptedRunManager(step_seconds=0.0)
            agent.runs.configure(manager)
            bob = _guarded(TestClient(app), guard)
            try:
                yield Demo(app=app, manager=manager, guard=guard, alice=alice, bob=bob)
            finally:
                manager.shutdown()
                agent.runs.configure(None)
                bob.close()
    finally:
        agent.runs.configure(None)
        db_client.set_db(None)


# ------------------------------------------------------------------------------ helpers


def _png(color: tuple[int, int, int]) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (4, 4), color).save(out, format="PNG")
    return out.getvalue()


def _ok(res: httpx.Response, status: int = 200) -> Any:
    assert res.status_code == status, f"{res.request.method} {res.request.url} -> {res.status_code}: {res.text}"
    return res.json() if res.content else None


def read_events(
    client: TestClient,
    fid: str,
    run_id: str,
    until: str | None = None,
    *,
    guard: Guard | None = None,
) -> list[tuple[str, dict]]:
    """Open the run's SSE stream and read `(event, data)` frames from the first event.

    Stops (and closes the stream) after the first `until` event, or reads to the end of the
    stream when `until` is None. `: ping` comments are skipped. Fails after MAX_EVENTS
    events or STREAM_DEADLINE_SECONDS; the server's frequent pings (HEARTBEAT_SECONDS) make
    sure the deadline is checked even while no event arrives.
    """
    events: list[tuple[str, dict]] = []
    started = time.monotonic()
    with client.stream("GET", f"/families/{fid}/runs/{run_id}/stream") as res:
        assert res.status_code == 200, res.read()
        assert res.headers["content-type"].startswith("text/event-stream")
        name: str | None = None
        data: list[str] = []
        for line in res.iter_lines():
            elapsed = time.monotonic() - started
            assert elapsed < STREAM_DEADLINE_SECONDS, f"stream stalled; events so far: {[e for e, _ in events]}"
            if guard is not None:
                _assert_clean(line, f"SSE line of run {run_id}")
            if line == "":
                if name is not None or data:
                    assert name is not None and data, f"incomplete SSE frame: event={name!r} data={data!r}"
                    events.append((name, json.loads("\n".join(data))))
                    if guard is not None:
                        guard.stream_frames += 1
                    assert len(events) <= MAX_EVENTS, "too many events"
                    if name == until:
                        return events
                name, data = None, []
                continue
            if line.startswith(":"):
                continue  # heartbeat comment
            key, _, value = line.partition(":")
            value = value[1:] if value.startswith(" ") else value
            if key == "event":
                name = value
            elif key == "data":
                data.append(value)
            else:
                pytest.fail(f"unexpected SSE field {key!r} in {line!r}")
    assert until is None, f"stream ended before {until!r}; got {[e for e, _ in events]}"
    return events


def validate_events(events: list[tuple[str, dict]]) -> list[schemas.Model]:
    """Every name is a RUN_EVENTS key and every data frame validates (camelCase aliases only)."""
    models = []
    for name, data in events:
        assert name in schemas.RUN_EVENTS, f"unknown event {name!r}"
        models.append(schemas.RUN_EVENTS[name].model_validate(data, by_alias=True, by_name=False))
    return models


def _names(events: list[tuple[str, dict]]) -> list[str]:
    return [name for name, _ in events]


def _answer(client: TestClient, fid: str, run_id: str, body: dict) -> httpx.Response:
    return client.post(f"/families/{fid}/runs/{run_id}/answer", json=body)


def _media_fetchable(client: TestClient, fid: str, media_id: str) -> httpx.Response:
    res = client.get(f"/families/{fid}/media/{media_id}")
    assert res.status_code == 200, f"media {media_id}: {res.status_code} {res.text}"
    assert res.headers["content-type"].startswith("image/"), res.headers["content-type"]
    assert res.content
    return res


# ------------------------------------------------------------------------------ the family


@dataclass
class Family:
    fid: str
    name: str
    alice_pid: str
    mother_pid: str
    bob_pid: str


def signup(client: TestClient, email: str, password: str, name: str) -> dict:
    body = _ok(client.post("/auth/signup", json={"email": email, "password": password, "name": name}), 201)
    me = schemas.MeResponse.model_validate(body, by_alias=True, by_name=False)
    assert me.user.email == email and me.families == []
    return body


def start_family(d: Demo, join: str) -> Family:
    """Alice starts a family and adds her mother; Bob joins through an invite.

    join="add": Bob adds himself as Alice's spouse. join="claim": Bob claims the mother.
    """
    alice, bob = d.alice, d.bob
    signup(alice, "alice@example.test", ALICE_PASSWORD, "Alice Park")

    created = _ok(alice.post("/families", json={"name": "The Parks", "person": {"name": "Alice", "born": 1985}}), 201)
    created_model = schemas.CreateFamilyResponse.model_validate(created, by_alias=True, by_name=False)
    fid = created_model.family.id
    alice_pid = created_model.person.id
    assert created_model.family.role == "admin" and created_model.family.person_id == alice_pid

    # Her mother, who will never have an account; then Alice's parent link to her.
    mother = schemas.Person.model_validate(
        _ok(alice.post(f"/families/{fid}/people", json={"name": "Grace", "born": 1958}), 201),
        by_alias=True, by_name=False,
    )
    assert mother.user_id is None
    patched = schemas.Person.model_validate(
        _ok(alice.patch(f"/families/{fid}/people/{alice_pid}", json={"parentIds": [mother.id]})),
        by_alias=True, by_name=False,
    )
    assert patched.parent_ids == [mother.id]

    invite = schemas.InviteResponse.model_validate(
        _ok(alice.post(f"/families/{fid}/invites"), 201), by_alias=True, by_name=False
    )
    assert invite.path == f"/join/{invite.code}"

    # Bob previews the invite before he has an account (the code is the secret).
    preview = schemas.InvitePreview.model_validate(
        _ok(bob.get(f"/invites/{invite.code}")), by_alias=True, by_name=False
    )
    assert preview.family_id == fid and preview.family_name == "The Parks"
    unclaimed = {p.id for p in preview.unclaimed_people}
    assert mother.id in unclaimed and alice_pid not in unclaimed

    signup(bob, "bob@example.test", BOB_PASSWORD, "Bob Park")
    if join == "add":
        body = {"person": {"name": "Bob", "born": 1984, "spouseIds": [alice_pid]}}
    else:
        body = {"personId": mother.id}
    joined = schemas.JoinResponse.model_validate(
        _ok(bob.post(f"/invites/{invite.code}/join", json=body)), by_alias=True, by_name=False
    )
    bob_pid = joined.person.id
    assert joined.family.id == fid and joined.family.role == "member" and joined.family.person_id == bob_pid
    if join == "add":
        assert bob_pid not in (alice_pid, mother.id) and joined.person.spouse_ids == [alice_pid]
    else:
        assert bob_pid == mother.id
        after = schemas.InvitePreview.model_validate(_ok(bob.get(f"/invites/{invite.code}")), by_alias=True)
        assert mother.id not in {p.id for p in after.unclaimed_people}
    assert joined.person.user_id is not None

    return Family(fid=fid, name="The Parks", alice_pid=alice_pid, mother_pid=mother.id, bob_pid=bob_pid)


def check_me_and_tree(d: Demo, fam: Family, join: str) -> None:
    for client, pid, role in ((d.alice, fam.alice_pid, "admin"), (d.bob, fam.bob_pid, "member")):
        me = schemas.MeResponse.model_validate(_ok(client.get("/me")), by_alias=True, by_name=False)
        mine = [f for f in me.families if f.id == fam.fid]
        assert len(mine) == 1, me.families
        assert mine[0].name == fam.name and mine[0].role == role and mine[0].person_id == pid

        tree = schemas.TreeResponse.model_validate(
            _ok(client.get(f"/families/{fam.fid}/tree")), by_alias=True, by_name=False
        )
        ids = {p.id for p in tree.people}
        assert tree.family_id == fam.fid and {fam.alice_pid, fam.bob_pid, fam.mother_pid} <= ids
        assert len(ids) == (3 if join == "add" else 2)
        by_id = {p.id: p for p in tree.people}
        assert by_id[fam.alice_pid].user_id is not None and by_id[fam.bob_pid].user_id is not None


@dataclass
class Told:
    run_id: str
    memory_id: str
    photo_ids: list[str]
    events: list[tuple[str, dict]]


def tell_new_memory(d: Demo, fam: Family, teller: TestClient, other: TestClient, other_pid: str) -> Told:
    """`teller` submits a new memory with two photos and walks the run to done over SSE."""
    fid = fam.fid
    res = teller.post(
        f"/families/{fid}/memories",
        data={
            "text": "We sang from the green hymn books on the church steps. Then the wind took the pages.",
            "year": "1988",
            "question": "What do you remember about Sundays?",
            "participantIds": [other_pid],
        },
        files=[
            ("photos", ("steps.png", _png((40, 120, 80)), "image/png")),
            ("photos", ("hymnal.png", _png((200, 180, 90)), "image/png")),
        ],
    )
    submitted = schemas.SubmitMemoryResponse.model_validate(_ok(res, 202), by_alias=True, by_name=False)
    assert res.json() == {"runId": submitted.run_id, "memoryId": None}
    run_id = submitted.run_id

    # 1. Up to the follow-up question, then close the stream.
    first = read_events(teller, fid, run_id, until="question", guard=d.guard)
    assert first[0][0] == "plan"
    assert first[-1] == ("question", {"text": FOLLOWUP_QUESTION})

    # Only the person who started the run may answer it, even while it is waiting.
    assert _answer(other, fid, run_id, {"kind": "answer", "text": "Me too!"}).status_code == 403
    assert _answer(teller, fid, run_id, {"kind": "answer", "text": "Grandma Rosa was there too."}).status_code == 204

    # 2. Reopening replays from the first event, then continues to the prop review.
    second = read_events(teller, fid, run_id, until="props_found", guard=d.guard)
    assert second[: len(first)] == first, "the stream must replay from the first event"
    assert _names(second).count("question") == 1
    props_found = schemas.PropsFoundEvent.model_validate(second[-1][1], by_alias=True, by_name=False)
    assert props_found.props, "the prop review should show at least one prop"

    # The run is waiting for a props review: a plain answer does not fit it.
    wrong = _answer(teller, fid, run_id, {"kind": "answer", "text": "again"})
    assert wrong.status_code == 409, wrong.text
    assert _answer(teller, fid, run_id, {"kind": "props", "props": []}).status_code == 204

    # 3. Reopen and read to the end of the stream.
    events = read_events(teller, fid, run_id, guard=d.guard)
    assert events[: len(second)] == second
    validate_events(events)
    names = _names(events)
    terminal = [n for n in names if n in ("done", "error")]
    assert terminal == ["done"], names
    assert names[-1] == "done", "nothing may follow the terminal event"
    assert names.count("question") == 1 and names.count("props_found") == 1
    assert "scene_ready" in names
    memory_id = schemas.DoneEvent.model_validate(events[-1][1], by_alias=True, by_name=False).memory_id

    # Not waiting any more: 409, for the starter and the right kind.
    late = _answer(teller, fid, run_id, {"kind": "answer", "text": "One more thing."})
    assert late.status_code == 409, late.text
    late_props = _answer(teller, fid, run_id, {"kind": "props", "props": []})
    assert late_props.status_code == 409, late_props.text

    memory = schemas.MemoryResponse.model_validate(
        _ok(teller.get(f"/families/{fid}/memories/{memory_id}")), by_alias=True, by_name=False
    )
    photo_ids = memory.memory.tellings[0].photo_ids
    return Told(run_id=run_id, memory_id=memory_id, photo_ids=list(photo_ids), events=events)


def _book(client: TestClient, fid: str, pid: str) -> schemas.BookResponse:
    return schemas.BookResponse.model_validate(
        _ok(client.get(f"/families/{fid}/people/{pid}/book")), by_alias=True, by_name=False
    )


# ------------------------------------------------------------------------------ test 1


@pytest.mark.parametrize("join", ["add", "claim"])
def test_two_new_people_make_a_memory(demo_app: Demo, join: str):
    d = demo_app
    fam = start_family(d, join)
    check_me_and_tree(d, fam, join)

    told = tell_new_memory(d, fam, teller=d.bob, other=d.alice, other_pid=fam.alice_pid)
    fid, memory_id = fam.fid, told.memory_id
    assert d.manager.status(fid, told.run_id) == "done"

    # The scene is in both users' books, with its screenplay and timeline.
    for client, pid in ((d.bob, fam.bob_pid), (d.alice, fam.alice_pid)):
        book = _book(client, fid, pid)
        assert book.person.id == pid
        found = [m for m in book.memories if m.id == memory_id]
        assert len(found) == 1, f"memory {memory_id} missing from {pid}'s book"
        memory = found[0]
        assert memory.screenplay is not None and memory.timeline is not None
        assert memory.timeline.tracks, "the timeline should have tracks"
        assert {fam.bob_pid, fam.alice_pid} <= set(memory.participant_ids)
        assert set(memory.prop_ids) <= {p.id for p in book.props}
        assert set(memory.set_ids) <= {s.id for s in book.sets}

    # The memory itself: one telling by Bob, with the follow-up answer kept, the props and set.
    res = d.alice.get(f"/families/{fid}/memories/{memory_id}")
    memory = schemas.MemoryResponse.model_validate(_ok(res), by_alias=True, by_name=False)
    assert memory.memory.id == memory_id and memory.memory.family_id == fid
    assert memory.memory.year == 1988
    assert memory.props and memory.sets
    assert {p.id for p in memory.props} >= set(memory.memory.prop_ids)
    assert all(p.family_id in (fid, None) for p in memory.props)
    assert all(s.family_id in (fid, None) for s in memory.sets)
    assert {fam.bob_pid, fam.alice_pid} <= {p.id for p in memory.people}
    assert [t.author_id for t in memory.memory.tellings] == [fam.bob_pid]
    assert "Grandma Rosa was there too." in memory.memory.tellings[0].text
    assert memory.memory.screenplay is not None and memory.memory.screenplay.memory_id == memory_id

    # Threads list it, with Bob's person among the tellers.
    threads = schemas.ThreadsResponse.model_validate(
        _ok(d.bob.get(f"/families/{fid}/threads")), by_alias=True, by_name=False
    )
    moments = {m.id: m for m in threads.moments}
    assert memory_id in moments
    assert moments[memory_id].telling_author_ids == [fam.bob_pid]
    assert {fam.alice_pid, fam.bob_pid} <= {p.id for p in threads.people}

    # Both uploaded photos stream back as images, to either member.
    assert len(told.photo_ids) == 2 and len(set(told.photo_ids)) == 2
    for photo_id in told.photo_ids:
        for client in (d.bob, d.alice):
            media = _media_fetchable(client, fid, photo_id)
            assert media.headers["content-type"] == "image/png"
            assert media.content.startswith(b"\x89PNG")

    # Another family's member cannot read any of it: the demo family's David gets 404s.
    david = d.bob  # reuse Bob's client after logging out
    assert david.post("/auth/logout").status_code == 204
    _ok(david.post("/auth/login", json={"email": "david@demo.stitch", "password": demo.DEMO_PASSWORD}))
    for path in (
        f"/families/{fid}/tree",
        f"/families/{fid}/memories/{memory_id}",
        f"/families/{fid}/people/{fam.bob_pid}/book",
        f"/families/{fid}/threads",
        f"/families/{fid}/media/{told.photo_ids[0]}",
        f"/families/{fid}/runs/{told.run_id}/stream",
    ):
        assert david.get(path).status_code == 404, path

    # The guard really scanned the traffic.
    assert d.guard.checked >= 25 and d.guard.stream_frames >= len(told.events)


# ------------------------------------------------------------------------------ test 2


def _media_refs(value: Any) -> set[str]:
    """Every string in a JSON value that parses as a media reference ("media/<id>" or a bare id)."""
    found: set[str] = set()
    if isinstance(value, dict):
        for v in value.values():
            found |= _media_refs(v)
    elif isinstance(value, list):
        for v in value:
            found |= _media_refs(v)
    elif isinstance(value, str):
        media_id = media_db.parse_media_ref(value)
        if media_id is not None:
            found.add(media_id)
    return found


def test_stage_demo_family_has_the_hymn_book_scene(demo_app: Demo):
    d = demo_app
    fid = demo.DEMO_FAMILY_ID
    db = db_client.get_db()
    accounts = {a.email: a for a in demo.DEMO_ACCOUNTS}
    david, ana = accounts["david@demo.stitch"], accounts["ana@demo.stitch"]

    for client, account in ((d.alice, david), (d.bob, ana)):
        me = schemas.MeResponse.model_validate(
            _ok(client.post("/auth/login", json={"email": account.email, "password": demo.DEMO_PASSWORD})),
            by_alias=True, by_name=False,
        )
        assert me.user.id == account.user_id
        family = next(f for f in me.families if f.id == fid)
        assert family.name == demo.DEMO_FAMILY_NAME and family.person_id == account.person_id

        tree = schemas.TreeResponse.model_validate(_ok(client.get(f"/families/{fid}/tree")), by_alias=True)
        assert {p.id for p in tree.people} == set(demo.DEMO_PERSON_IDS)

        book = _book(client, fid, account.person_id)
        hymn = [m for m in book.memories if m.id == demo.HYMN_MEMORY_ID]
        assert len(hymn) == 1, f"{account.email}'s book is missing the hymn-book memory"
        memory = hymn[0]
        assert memory.screenplay is not None and memory.timeline is not None and memory.timeline.tracks
        assert set(memory.prop_ids) <= {p.id for p in book.props}
        assert set(memory.set_ids) <= {s.id for s in book.sets}
        assert {"dad", "mom"} <= {p.id for p in book.people}

        full = _ok(client.get(f"/families/{fid}/memories/{demo.HYMN_MEMORY_ID}"))
        response = schemas.MemoryResponse.model_validate(full, by_alias=True, by_name=False)
        assert response.props and response.sets and response.memory.timeline is not None
        assert len(response.memory.tellings) == 2

        # Every media reference in the memory, its timeline, props and sets that the seed
        # stored streams back. (The sample's art is all placeholders today, so this also
        # fetches the seeded photos from the rest of the book, where David appears.)
        stored = {m for m in _media_refs(full) if media_db.media_exists(db, fid, m)}
        for media_id in sorted(stored):
            _media_fetchable(client, fid, media_id)
        book_photos = {pid for m in book.memories for t in m.tellings for pid in t.photo_ids}
        for media_id in sorted(book_photos):
            assert media_db.media_exists(db, fid, media_id), media_id
            _media_fetchable(client, fid, media_id)
        if account is david:
            assert len(book_photos) == 2, "David's book should hold the two seeded road-trip photos"

    assert d.guard.checked > 0


# ------------------------------------------------------------------------------ test 3


def test_second_telling_adds_to_the_moment(demo_app: Demo):
    d = demo_app
    fam = start_family(d, "add")
    # Alice tells the moment first (a person's own second telling replaces their first).
    told = tell_new_memory(d, fam, teller=d.alice, other=d.bob, other_pid=fam.bob_pid)
    fid, memory_id = fam.fid, told.memory_id

    res = d.bob.post(
        f"/families/{fid}/memories",
        data={
            "text": "I remember the wind more than the hymns. Alice chased a page down the steps.",
            "year": "1988",
            "memoryId": memory_id,
            "participantIds": [fam.alice_pid],
        },
        files=[("photos", ("page.png", _png((90, 90, 200)), "image/png"))],
    )
    submitted = schemas.SubmitMemoryResponse.model_validate(_ok(res, 202), by_alias=True, by_name=False)
    assert submitted.memory_id == memory_id

    events = read_events(d.bob, fid, submitted.run_id, guard=d.guard)
    validate_events(events)
    names = _names(events)
    assert [n for n in names if n in ("done", "error")] == ["done"] and names[-1] == "done", names
    assert "question" not in names and "props_found" not in names
    assert events[-1][1] == {"memoryId": memory_id}

    # Not waiting at any point: answering is a 409.
    assert _answer(d.bob, fid, submitted.run_id, {"kind": "answer", "text": "hello"}).status_code == 409

    memory = schemas.MemoryResponse.model_validate(
        _ok(d.alice.get(f"/families/{fid}/memories/{memory_id}")), by_alias=True, by_name=False
    )
    tellings = memory.memory.tellings
    assert [t.author_id for t in tellings] == [fam.alice_pid, fam.bob_pid]
    assert tellings[1].text.startswith("I remember the wind")
    assert len(tellings[1].photo_ids) == 1
    _media_fetchable(d.alice, fid, tellings[1].photo_ids[0])
    assert memory.memory.screenplay is not None and memory.memory.timeline is not None

    threads = schemas.ThreadsResponse.model_validate(_ok(d.alice.get(f"/families/{fid}/threads")), by_alias=True)
    moment = next(m for m in threads.moments if m.id == memory_id)
    assert moment.telling_author_ids == [fam.alice_pid, fam.bob_pid]

    # Bob's book shows the same memory, now with both tellings.
    book = _book(d.bob, fid, fam.bob_pid)
    in_book = next(m for m in book.memories if m.id == memory_id)
    assert len(in_book.tellings) == 2
