"""Every book and memory response carries everything its stage needs to play each memory.

Driven through `main.create_app()` with STITCH_FAKE_DB=1 (the seeded in-memory demo family),
STITCH_AGENT=fake (seed/fake_runs.py) and STITCH_FAKE_RUN_STEP=0: no MongoDB, no keys, no
network. Runs on mongomock only.

Every id is discovered at run time from the responses (the tree, the threads, each memory);
nothing about the seed's size is hardcoded. For every demo person's book and every memory:

- every prop the scene names (screenplay `props`, a shot's `propStart` and what it rests
  `on`, a start's `holding` and seat `in`, an action's `what`, a `prop:` track and a
  `prop:` parent value, an interaction's prop or `prop:` target, a `prop:` explanation) is
  in `props`;
- every set a shot (screenplay or timeline) or a `set:` track uses is in `sets`;
- every participant, teller, cast member, start person, narrations key (screenplay and
  timeline), default narrator, `puppet:` track or parent and `puppet:` explanation is in
  `people`;
- the signature item prop (`character.itemPropId`) of each of those people is in `props`;
- every media ref (props' art, sets' layer art, tellings' photos, narration audio) is null
  or fetches with 200 from GET /families/{fid}/media/{id};
- nothing in the bundle belongs to another family.

Also: a second telling through the scripted run brings its new teller and their item prop
into the memory and their book; a person with no memories gets an empty 200 book; the
threads list every seeded moment once with its year; and the run routes' demo-path edges
(a second telling by a non-participant, with photos; an answer by someone else; a stream
reattached after done).
"""

from __future__ import annotations

import io
import time
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field

import pytest
from fastapi.testclient import TestClient
from PIL import Image

import agent.runs
import main
import routes.memories
import schemas
from db import client as db_client
from db import media as media_db
from db import props as props_db
from seed import demo
from seed.fake_runs import ScriptedRunManager
from seed.smoke import parse_sse

FID = demo.DEMO_FAMILY_ID
ACCOUNTS = {a.person_id: a for a in demo.DEMO_ACCOUNTS}
ADMIN = demo.DEMO_ACCOUNTS[0]
DEADLINE = 20.0


# ------------------------------------------------------------------------------ fixtures


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """The whole app on the seeded fake db, logged in as the demo admin."""
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
            _login(c, ADMIN.email)
            yield c
    finally:
        agent.runs.configure(None)
        db_client.set_db(None)


def _login(client: TestClient, email: str) -> None:
    res = client.post("/auth/login", json={"email": email, "password": demo.DEMO_PASSWORD})
    assert res.status_code == 200, res.text


def _other_client(api: TestClient, email: str) -> TestClient:
    """A second client on the same app (and fake db), logged in as another demo account."""
    other = TestClient(api.app)
    _login(other, email)
    return other


def _get(client: TestClient, path: str) -> dict:
    res = client.get(path)
    assert res.status_code == 200, f"GET {path} -> {res.status_code}: {res.text}"
    return res.json()


def _png() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (4, 4), (200, 120, 40)).save(out, format="PNG")
    return out.getvalue()


# ------------------------------------------------------------------------------ what a scene names


def _ref_id(value: str, kind: str) -> str | None:
    """'prop:hat.brim' -> 'hat' for kind 'prop'; None when value is not of that kind."""
    prefix = f"{kind}:"
    if not value.startswith(prefix):
        return None
    return value[len(prefix):].split(".", 1)[0] or None


def _before_dot(value: str | None) -> str | None:
    """'<propId>.<socket>' -> '<propId>'."""
    return value.split(".", 1)[0] if value else None


@dataclass
class Needs:
    props: dict[str, str] = field(default_factory=dict)  # id -> where it was named (first)
    sets: dict[str, str] = field(default_factory=dict)
    people: dict[str, str] = field(default_factory=dict)

    def add(self, kind: str, ids: Iterable[str | None], where: str) -> None:
        bucket = getattr(self, kind)
        for i in ids:
            if i:
                bucket.setdefault(i, where)


def scene_needs(memory: schemas.Memory) -> Needs:
    """Every prop, set and person `memory` names anywhere, with where it was named."""
    m = memory.id
    needs = Needs()
    needs.add("people", memory.participant_ids, f"{m} participantIds")
    needs.add("people", (t.author_id for t in memory.tellings), f"{m} tellings")
    needs.add("props", memory.prop_ids, f"{m} propIds")
    needs.add("sets", memory.set_ids, f"{m} setIds")

    sp = memory.screenplay
    if sp is not None:
        needs.add("people", sp.cast, f"{m} screenplay.cast")
        needs.add("props", sp.props, f"{m} screenplay.props")
        needs.add("people", (sp.narrations or {}).keys(), f"{m} screenplay.narrations")
        for shot in sp.shots:
            where = f"{m} shot {shot.id}"
            needs.add("sets", [shot.set], f"{where} set")
            needs.add("people", shot.start.keys(), f"{where} start")
            for start in shot.start.values():
                needs.add("props", [start.holding, _before_dot(start.in_)], f"{where} start holding/in")
            needs.add("props", shot.prop_start.keys(), f"{where} propStart")
            needs.add("props", (_before_dot(ps.on) for ps in shot.prop_start.values()), f"{where} propStart.on")
            for action in shot.actions:
                needs.add("props", [action.what], f"{where} t={action.t} {action.do} what")
                needs.add("people", [action.who], f"{where} t={action.t} {action.do} who")
        for interaction in sp.interactions or []:
            drives = interaction.drives
            needs.add("props", [drives.prop], f"{m} interaction {interaction.id}")
            needs.add("props", (_ref_id(t, "prop") for t in drives.targets or []), f"{m} interaction {interaction.id}")
            needs.add("people", (_ref_id(t, "puppet") for t in drives.targets or []), f"{m} interaction {interaction.id}")
        for explanation in sp.explanations or []:
            needs.add("props", [_ref_id(explanation.target, "prop")], f"{m} explanation")
            needs.add("people", [_ref_id(explanation.target, "puppet")], f"{m} explanation")

    tl = memory.timeline
    if tl is not None:
        needs.add("people", (tl.narrations or {}).keys(), f"{m} timeline.narrations")
        needs.add("people", [tl.default_narrator], f"{m} timeline.defaultNarrator")
        needs.add("sets", (s.set for s in tl.shots), f"{m} timeline.shots")
        for track in tl.tracks:
            where = f"{m} track {track.target} {track.channel}"
            needs.add("props", [_ref_id(track.target, "prop")], where)
            needs.add("people", [_ref_id(track.target, "puppet")], where)
            needs.add("sets", [_ref_id(track.target, "set")], where)
            if track.channel == "parent":
                values = [v for _t, v in track.keys if isinstance(v, str)]
                needs.add("props", (_ref_id(v, "prop") for v in values), f"{where} value")
                needs.add("people", (_ref_id(v, "puppet") for v in values), f"{where} value")
    return needs


def media_refs(memories: list[schemas.Memory], props: list[schemas.Prop], sets: list[schemas.SetDef]) -> dict[str, str]:
    """Every non-null media ref the stage would fetch, with where it was named."""
    refs: dict[str, str] = {}
    for prop in props:
        for key, ref in prop.art.items():
            if ref:
                refs.setdefault(ref, f"prop {prop.id} art.{key}")
    for set_def in sets:
        for n, layer in enumerate(set_def.layers):
            if layer.art:
                refs.setdefault(layer.art, f"set {set_def.id} layer {n}")
    for memory in memories:
        for telling in memory.tellings:
            for photo in telling.photo_ids:
                refs.setdefault(photo, f"{memory.id} telling by {telling.author_id}")
        tl = memory.timeline
        if tl is not None:
            if tl.audio:
                refs.setdefault(tl.audio, f"{memory.id} timeline.audio")
            for author, track in (tl.narrations or {}).items():
                if track.audio:
                    refs.setdefault(track.audio, f"{memory.id} narration audio of {author}")
    return refs


def assert_complete(
    client: TestClient,
    what: str,
    memories: list[schemas.Memory],
    props: list[schemas.Prop],
    sets: list[schemas.SetDef],
    people: list[schemas.Person],
) -> Needs:
    """The bundle holds everything `memories` name, all of it this family's (or the library's)."""
    prop_ids = [p.id for p in props]
    set_ids = [s.id for s in sets]
    person_ids = [p.id for p in people]
    assert len(prop_ids) == len(set(prop_ids)), f"{what}: repeated props {prop_ids}"
    assert len(set_ids) == len(set(set_ids)), f"{what}: repeated sets {set_ids}"
    assert len(person_ids) == len(set(person_ids)), f"{what}: repeated people {person_ids}"
    assert all(p.family_id in (FID, None) for p in props), f"{what}: a prop of another family"
    assert all(s.family_id in (FID, None) for s in sets), f"{what}: a set of another family"
    assert all(p.family_id == FID for p in people), f"{what}: a person of another family"
    assert all(m.family_id == FID for m in memories), f"{what}: a memory of another family"

    needs = Needs()
    for memory in memories:
        found = scene_needs(memory)
        for kind in ("props", "sets", "people"):
            for i, where in getattr(found, kind).items():
                getattr(needs, kind).setdefault(i, where)
    by_person = {p.id: p for p in people}
    for pid in needs.people:
        person = by_person.get(pid)
        if person is not None and person.character.item_prop_id:
            needs.props.setdefault(person.character.item_prop_id, f"itemPropId of {pid}")

    missing = {
        "props": {i: w for i, w in needs.props.items() if i not in prop_ids},
        "sets": {i: w for i, w in needs.sets.items() if i not in set_ids},
        "people": {i: w for i, w in needs.people.items() if i not in person_ids},
    }
    assert missing == {"props": {}, "sets": {}, "people": {}}, f"{what} is missing: {missing}"

    for ref, where in media_refs(memories, props, sets).items():
        bare = media_db.parse_media_ref(ref)
        assert bare, f"{what}: {where} names a malformed media ref {ref!r}"
        res = client.get(f"/families/{FID}/media/{bare}")
        assert res.status_code == 200, f"{what}: {where} names media {ref!r}, which is {res.status_code}"
        assert res.content
    return needs


def check_memory(client: TestClient, memory_id: str) -> schemas.MemoryResponse:
    body = schemas.MemoryResponse.model_validate(_get(client, f"/families/{FID}/memories/{memory_id}"))
    assert body.memory.id == memory_id
    assert body.memory.screenplay is not None and body.memory.timeline is not None, f"{memory_id} has no scene"
    assert_complete(client, f"memory {memory_id}", [body.memory], body.props, body.sets, body.people)
    return body


def check_book(client: TestClient, person_id: str) -> schemas.BookResponse:
    body = schemas.BookResponse.model_validate(_get(client, f"/families/{FID}/people/{person_id}/book"))
    assert body.person.id == person_id
    assert person_id in {p.id for p in body.people}, "the book owner is missing from people"
    years = [m.year for m in body.memories]
    assert years == sorted(years), f"{person_id}'s book is not ordered by year: {years}"
    for memory in body.memories:
        assert person_id in memory.participant_ids, f"{memory.id} is in {person_id}'s book without them"
    assert_complete(client, f"{person_id}'s book", body.memories, body.props, body.sets, body.people)
    owner_item = body.person.character.item_prop_id
    if owner_item:
        assert owner_item in {p.id for p in body.props}, f"{person_id}'s own item prop is missing from their book"
    return body


def _tree_ids(client: TestClient) -> list[str]:
    return [p["id"] for p in _get(client, f"/families/{FID}/tree")["people"]]


def _moment_ids(client: TestClient) -> list[str]:
    return [m["id"] for m in _get(client, f"/families/{FID}/threads")["moments"]]


# ------------------------------------------------------------------------------ the seeded family


def test_every_seeded_memory_carries_its_whole_scene(api: TestClient):
    moments = _moment_ids(api)
    assert moments, "the demo family has no moments"
    named_props: set[str] = set()
    for memory_id in moments:
        body = check_memory(api, memory_id)
        named_props |= set(scene_needs(body.memory).props)
    # The demo's worn hats are named by their wearers, not (only) by screenplays, and still arrive.
    assert named_props, "no seeded scene names a prop"


def test_every_demo_persons_book_carries_every_scene(api: TestClient):
    people = _tree_ids(api)
    assert set(demo.DEMO_PERSON_IDS) <= set(people)
    in_books: set[str] = set()
    for person_id in people:
        book = check_book(api, person_id)
        in_books |= {m.id for m in book.memories}
    assert in_books == set(_moment_ids(api)), "a seeded moment is in nobody's book"


def test_worn_item_props_reach_every_book_and_memory_their_wearer_is_in(api: TestClient):
    tree = [schemas.Person.model_validate(p) for p in _get(api, f"/families/{FID}/tree")["people"]]
    wearers = {p.id: p.character.item_prop_id for p in tree if p.character.item_prop_id}
    assert wearers, "no seeded person wears an item prop"
    for pid, item in wearers.items():
        book = check_book(api, pid)
        assert item in {p.id for p in book.props}
        for memory in book.memories:
            body = check_memory(api, memory.id)
            assert item in {p.id for p in body.props}, f"{pid}'s {item} is missing from {memory.id}"


def test_threads_list_every_seeded_moment_once_with_its_year(api: TestClient):
    threads = schemas.ThreadsResponse.model_validate(_get(api, f"/families/{FID}/threads"))
    ids = [m.id for m in threads.moments]
    assert len(ids) == len(set(ids)), f"a moment is listed twice: {ids}"
    assert set(ids) == set(demo.DEMO_MEMORY_IDS)
    for moment in threads.moments:
        memory = schemas.MemoryResponse.model_validate(_get(api, f"/families/{FID}/memories/{moment.id}")).memory
        assert moment.year == memory.year
        assert moment.title == memory.title
        assert sorted(moment.participant_ids) == sorted(memory.participant_ids)
    years = [m.year for m in threads.moments]
    assert years == sorted(years)
    assert {p.id for p in threads.people} == set(_tree_ids(api))


def test_a_person_with_no_memories_gets_an_empty_book(api: TestClient):
    res = api.post(f"/families/{FID}/people", json={"name": "Baby Ines", "born": 2024, "parentIds": ["sam"]})
    assert res.status_code == 201, res.text
    pid = res.json()["id"]
    body = check_book(api, pid)
    assert body.memories == [] and body.sets == []
    assert [p.id for p in body.people] == [pid]
    assert body.props == []


def test_an_empty_book_still_carries_its_owners_item_prop(api: TestClient):
    item = next(p.id for p in props_db.list_props(db_client.get_db(), FID))
    res = api.post(
        f"/families/{FID}/people",
        json={"name": "Tia Marta", "born": 1965, "character": {"color": "#4f6d7a", "item": "x", "itemPropId": item}},
    )
    assert res.status_code == 201, res.text
    body = check_book(api, res.json()["id"])
    assert body.memories == [] and [p.id for p in body.props] == [item]


def _stored(memory_id: str) -> dict:
    doc = db_client.get_db()["memories"].find_one({"_id": memory_id, "familyId": FID})
    assert doc is not None
    return doc


def test_bundles_follow_the_scene_when_the_stored_id_lists_lag_behind(api: TestClient):
    """propIds/setIds emptied on every stored memory (as a scene saved with stale lists would be):
    the stage still gets every prop and set its screenplay and timeline name."""
    db = db_client.get_db()
    moments = _moment_ids(api)
    for memory_id in moments:
        assert _stored(memory_id).get("propIds") or _stored(memory_id).get("setIds")
        db["memories"].update_one({"_id": memory_id, "familyId": FID}, {"$set": {"propIds": [], "setIds": []}})
    for memory_id in moments:
        body = check_memory(api, memory_id)
        assert body.memory.prop_ids == [] and body.memory.set_ids == []
        assert body.sets, f"{memory_id} lost its sets"
    for person_id in _tree_ids(api):
        check_book(api, person_id)


def test_scene_ids_are_looked_up_in_this_family_only(api: TestClient):
    """A prop or set id that only another family owns, named by a scene, never reaches the bundle."""
    stranger = TestClient(api.app)
    try:
        res = stranger.post("/auth/signup", json={"email": "o@example.test", "password": "other-pw-123", "name": "O"})
        assert res.status_code == 201, res.text
        res = stranger.post("/families", json={"name": "Others", "person": {"name": "O"}})
        assert res.status_code == 201, res.text
        other_fid = res.json()["family"]["id"]
    finally:
        stranger.close()
    db = db_client.get_db()
    lamp = schemas.Prop(id="others_lamp", name="lamp", size="hand", art={"body": None}, color="#e0b84f")
    props_db.insert_prop(db, other_fid, lamp, prop_id=lamp.id)
    seeded_set = props_db.list_sets(db, FID)[0]
    props_db.insert_set(db, other_fid, seeded_set.model_copy(update={"id": "others_porch"}), set_id="others_porch")

    memory_id = _moment_ids(api)[0]
    doc = _stored(memory_id)
    doc["screenplay"]["props"].append(lamp.id)
    db["memories"].update_one(
        {"_id": memory_id, "familyId": FID},
        {"$set": {"screenplay": doc["screenplay"], "propIds": [*doc["propIds"], lamp.id],
                  "setIds": [*doc["setIds"], "others_porch"]}},
    )
    for body in (
        schemas.MemoryResponse.model_validate(_get(api, f"/families/{FID}/memories/{memory_id}")),
        schemas.BookResponse.model_validate(_get(api, f"/families/{FID}/people/{doc['participantIds'][0]}/book")),
    ):
        assert lamp.id not in {p.id for p in body.props}
        assert "others_porch" not in {s.id for s in body.sets}
        assert all(p.family_id in (FID, None) for p in body.props)
        assert all(s.family_id in (FID, None) for s in body.sets)


def test_another_family_cannot_read_the_bundles(api: TestClient):
    stranger = TestClient(api.app)
    res = stranger.post("/auth/signup", json={"email": "s@example.test", "password": "stranger-pw-1", "name": "S"})
    assert res.status_code == 201, res.text
    memory_id = _moment_ids(api)[0]
    assert stranger.get(f"/families/{FID}/memories/{memory_id}").status_code == 404
    assert stranger.get(f"/families/{FID}/people/{ADMIN.person_id}/book").status_code == 404
    stranger.close()


# ------------------------------------------------------------------------------ a second telling


def _stream_to_end(client: TestClient, run_id: str, *, answer: bool = True) -> list[tuple[str, dict]]:
    """Read the run's stream until done or error, answering what it asks unless `answer` is False
    (a replay of a finished run shows its old questions again; those are not to be answered)."""
    events: list[tuple[str, dict]] = []
    deadline = time.monotonic() + DEADLINE
    with client.stream("GET", f"/families/{FID}/runs/{run_id}/stream") as stream:
        assert stream.status_code == 200
        for name, data in parse_sse(stream.iter_lines()):
            assert time.monotonic() < deadline, "the run did not finish in time"
            assert name in schemas.RUN_EVENTS, name
            schemas.RUN_EVENTS[name].model_validate(data)
            events.append((name, data))
            if name in ("done", "error"):
                break
            if not answer:
                continue
            body = None
            if name == "question":
                body = {"kind": "answer", "text": "Just us."}
            elif name == "props_found":
                body = {"kind": "props", "props": [{"id": p["id"], "action": "keep"} for p in data["props"]]}
            elif name == "merge":
                body = {"kind": "merge", "approve": False}
            if body is not None:
                reply = client.post(f"/families/{FID}/runs/{run_id}/answer", json=body)
                assert reply.status_code == 204, reply.text
    return events


def _tell_directly(person_id: str, memory: schemas.Memory, text: str) -> list[tuple[str, object]]:
    """A scripted run for a person with no account, as the studio would run it."""
    manager = agent.runs.get_manager()
    assert isinstance(manager, ScriptedRunManager)
    fields = schemas.SubmitMemoryFields(text=text, year=memory.year, memory_id=memory.id)
    run_id = manager.start_run(FID, ADMIN.user_id, person_id, fields, [])
    status = manager.wait(FID, run_id, timeout=DEADLINE)
    assert status == "done", manager.events(FID, run_id)
    return manager.events(FID, run_id)


def _new_teller_with_item(api: TestClient, avoid: set[str]) -> tuple[str, str]:
    """A new person wearing one of the family's props that `avoid` does not hold."""
    family_props = [p.id for p in props_db.list_props(db_client.get_db(), FID)]
    item = next((p for p in family_props if p not in avoid), None)
    assert item is not None, "every family prop is already in this memory; pick another memory"
    res = api.post(
        f"/families/{FID}/people",
        json={"name": "Cousin Pilar", "born": 1988, "character": {"color": "#7a6ab0", "item": "x", "itemPropId": item}},
    )
    assert res.status_code == 201, res.text
    return res.json()["id"], item


def test_a_second_telling_brings_its_teller_and_their_item_prop(api: TestClient):
    memory_id = _moment_ids(api)[0]
    before = check_memory(api, memory_id)
    teller, item = _new_teller_with_item(api, {p.id for p in before.props})
    assert teller not in {p.id for p in before.people}

    events = _tell_directly(teller, before.memory, "I was there too. I brought my own hat, of course.")
    assert [n for n, _ in events][-2:] == ["scene_ready", "done"]

    after = check_memory(api, memory_id)
    assert teller in {t.author_id for t in after.memory.tellings}
    assert teller in {p.id for p in after.people}
    assert item in {p.id for p in after.props}
    assert after.memory.timeline is not None and teller in (after.memory.timeline.narrations or {})
    assert after.memory.version == before.memory.version + 1

    book = check_book(api, teller)
    assert [m.id for m in book.memories] == [memory_id]
    assert item in {p.id for p in book.props}
    # Everyone else's book that holds the moment now carries the new teller and their prop too.
    for pid in after.memory.participant_ids:
        other = check_book(api, pid)
        assert teller in {p.id for p in other.people} and item in {p.id for p in other.props}


def test_a_second_telling_by_a_non_participant_with_photos_over_http(api: TestClient):
    """Sam tells a moment he was not in, with a photo: he joins it, and the photo plays."""
    sam = ACCOUNTS["sam"]
    sam_client = _other_client(api, sam.email)
    try:
        target = None
        for memory_id in _moment_ids(api):
            memory = check_memory(api, memory_id).memory
            if sam.person_id not in memory.participant_ids:
                target = memory
                break
        if target is None:
            pytest.skip("Sam is in every seeded moment")
        res = sam_client.post(
            f"/families/{FID}/memories",
            data={"text": "I only heard about it later. Everyone tells it differently.", "year": str(target.year),
                  "memoryId": target.id},
            files=[("photos", ("later.png", _png(), "image/png"))],
        )
        assert res.status_code == 202, res.text
        submitted = schemas.SubmitMemoryResponse.model_validate(res.json())
        assert submitted.memory_id == target.id
        events = _stream_to_end(sam_client, submitted.run_id)
        assert events[-1][0] == "done", events

        after = check_memory(sam_client, target.id)
        assert sam.person_id in after.memory.participant_ids
        telling = next(t for t in after.memory.tellings if t.author_id == sam.person_id)
        assert len(telling.photo_ids) == 1  # and check_memory fetched it with 200
        assert target.id in {m.id for m in check_book(sam_client, sam.person_id).memories}
        sam_item = next(p for p in after.people if p.id == sam.person_id).character.item_prop_id
        if sam_item:
            assert sam_item in {p.id for p in after.props}

        # Reattaching after done replays the whole run, ending at done, exactly once.
        again = _stream_to_end(sam_client, submitted.run_id, answer=False)
        assert again == events
        assert [n for n, _ in again].count("done") == 1
    finally:
        sam_client.close()


def test_only_the_runs_owner_can_answer_it(api: TestClient):
    """David starts a new memory; Ana (same family) cannot answer its question, David can."""
    ana = _other_client(api, ACCOUNTS["mom"].email)
    try:
        res = api.post(f"/families/{FID}/memories", data={"text": "We planted a lemon tree.", "year": "2001"})
        assert res.status_code == 202, res.text
        run_id = res.json()["runId"]
        manager = agent.runs.get_manager()
        assert manager.wait(FID, run_id, timeout=DEADLINE) == "waiting"
        pending = manager.events(FID, run_id)[-1][0]
        assert pending == "question"

        res = ana.post(f"/families/{FID}/runs/{run_id}/answer", json={"kind": "answer", "text": "Me too"})
        assert res.status_code == 403
        assert res.json() == {"detail": routes.memories.NOT_RUN_OWNER}
        assert manager.status(FID, run_id) == "waiting"  # untouched

        events = _stream_to_end(api, run_id)
        assert events[-1][0] == "done", events
        memory_id = events[-1][1]["memoryId"]
        # After done the run takes no answers at all, from anyone.
        res = api.post(f"/families/{FID}/runs/{run_id}/answer", json={"kind": "answer", "text": "late"})
        assert res.status_code == 409
        assert res.json() == {"detail": routes.memories.NOT_WAITING}
        # Ana can still watch it (same family) and read the new scene.
        assert _stream_to_end(ana, run_id, answer=False) == events
        check_memory(ana, memory_id)
        check_book(api, ADMIN.person_id)
    finally:
        ana.close()
