"""Swapping a prop for a photo keeps its rig, so every stored scene still recompiles.

ARCHITECTURE.md section 6, PATCH /families/{fid}/props/{id} ("replace its art with a
photo"). A memory saved with a prop can drive one of its parts with a pull tab (the sedan's
front wheel, the bike's back wheel, the tamale pot's lid) or switch its states (the bike
bell, the umbrella, the ring box). db.props.photo_rig keeps the parts, each part's art and
every state name, so after the swap:

(a) every seeded memory that uses a swapped family prop recompiles with zero errors, the
    way seed/fake_runs.compile_scene does, and keeps every part track and state it had,
    also with a second telling's narration added (as the scripted run writes it);
(b) over HTTP on the offline demo app (STITCH_FAKE_DB=1, scripted runs): upload a photo,
    PATCH a prop with a pull tab on a part, add a second telling; the run ends in `done`
    and GET memory returns props whose parts and states cover the timeline's tracks;
(c) photo_rig itself, unit by unit.

Props, memories and questions are discovered from the seeded data at run time: no counts
and no ids are hardcoded except the two moments the HTTP test names on purpose.
Runs on mongomock only; no MongoDB, no keys, no network.
"""

from __future__ import annotations

import contextlib
import io
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import mongomock
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
from db import people as people_db
from db import props as props_db
from screenplay.schema import Timeline
from seed import demo, fake_runs

# Public helpers of the offline-demo path tests (tests/data is on sys.path, rootdir import mode).
from test_fake_runs_paths import (  # noqa: E402
    Api,
    Replies,
    as_person,
    check_run,
    get_memory,
    names,
    open_questions,
    read,
    submit,
)

FID = demo.DEMO_FAMILY_ID
PHOTO_SIZE = (640, 400)  # deliberately not square: the swap also changes the prop's aspect
_PART_CHANNEL = re.compile(r"^part\.(?P<part>.+)\.(?P<channel>rot|offset)$")


# ------------------------------------------------------------------------------ helpers


def _png(size: tuple[int, int] = PHOTO_SIZE, color: tuple[int, int, int] = (94, 140, 74)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, color).save(out, format="PNG")
    return out.getvalue()


@dataclass
class Rig:
    """What a timeline needs from its props' rigs: part tracks and state names, per prop."""

    parts: set[tuple[str, str]] = field(default_factory=set)  # (prop id, part name)
    part_tracks: set[tuple[str, str]] = field(default_factory=set)  # (target, channel)
    states: set[tuple[str, str]] = field(default_factory=set)  # (prop id, state name)
    interactions: set[str] = field(default_factory=set)


def rig_of(timeline: Timeline) -> Rig:
    """Every part and state a timeline refers to: its tracks and its pull tabs."""
    rig = Rig()
    for track in timeline.tracks:
        if not track.target.startswith("prop:"):
            continue
        prop_id = track.target.removeprefix("prop:")
        match = _PART_CHANNEL.match(track.channel)
        if match:
            rig.parts.add((prop_id, match["part"]))
            rig.part_tracks.add((track.target, track.channel))
        elif track.channel == "state":
            rig.states.update((prop_id, value) for _, value in track.keys if isinstance(value, str))
    for tab in timeline.interactions or []:
        rig.interactions.add(tab.id)
        drive = tab.drives
        if drive.type == "prop_part" and drive.prop and drive.part:
            rig.parts.add((drive.prop, drive.part))
        elif drive.type == "prop_state" and drive.prop:
            rig.states.update((drive.prop, s) for s in drive.states or [])
    return rig


def rig_problems(rig: Rig, props: dict[str, schemas.Prop]) -> list[str]:
    """Parts and states the timeline uses that the current prop definitions lack."""
    problems = []
    for prop_id, part in sorted(rig.parts):
        prop = props.get(prop_id)
        if prop is None or part not in prop.parts:
            problems.append(f"{prop_id} has no part {part!r}")
    for prop_id, state in sorted(rig.states):
        prop = props.get(prop_id)
        if prop is None or state not in prop.states:
            problems.append(f"{prop_id} has no state {state!r}")
    return problems


def _seeded() -> Any:
    db = mongomock.MongoClient(tz_aware=True)["stitch_photo_swap"]
    db_client.ensure_indexes(db)
    demo.seed_demo(db)
    return db


def _memories(db) -> list[schemas.Memory]:
    """Every moment in the demo family that has a scene, discovered from the database."""
    ids = [m.id for m in memories_db.moment_summaries(db, FID)]
    found = memories_db.get_memories(db, FID, ids)
    return [m for m in found if m.screenplay is not None and m.timeline is not None]


def _second_teller(db, memory: schemas.Memory) -> str | None:
    """A person in the family who has not told this moment, to add a telling for."""
    told = {t.author_id for t in memory.tellings}
    untold = [p.id for p in people_db.list_people(db, FID) if p.id not in told]
    in_scene = [pid for pid in untold if pid in memory.screenplay.cast]
    return (in_scene or untold or [None])[0]


# ------------------------------------------------------------- (a) sweep the seeded demo


def test_every_seeded_scene_recompiles_after_each_of_its_props_is_swapped_for_a_photo() -> None:
    db = _seeded()
    memories = _memories(db)
    assert memories, "the demo seeds no scenes"
    originals = {m.id: rig_of(m.timeline) for m in memories}
    users: dict[str, list[schemas.Memory]] = {}
    for memory in memories:
        for prop_id in dict.fromkeys(memory.screenplay.props):
            users.setdefault(prop_id, []).append(memory)
    family_props = {pid: p for pid in users if (p := props_db.get_prop(db, FID, pid)) is not None
                    and p.family_id == FID}
    assert family_props, "no seeded memory uses a family prop"
    # The sweep must exercise the rigs this fix is about, wherever the seed puts them.
    assert any(p.parts for p in family_props.values()), "no seeded prop has a moving part"
    assert any(p.states for p in family_props.values()), "no seeded prop has states"

    failures: list[str] = []
    for prop_id, before in family_props.items():
        photo = media_db.put_media(db, FID, _png(), content_type="image/png", filename=f"{prop_id}.png")
        after = props_db.update_prop(db, FID, prop_id, schemas.PropPatch(photo_id=media_db.media_ref(photo)))
        assert after is not None, prop_id
        assert after.source == "photo" and after.art["body"] == media_db.media_ref(photo)
        assert after.parts == before.parts, prop_id
        assert set(after.states) == set(before.states), prop_id
        assert after.aspect == pytest.approx(PHOTO_SIZE[0] / PHOTO_SIZE[1], abs=1e-4)
        for part in after.parts.values():
            if part.art != "body":
                assert after.art[part.art] == before.art.get(part.art), (prop_id, part.art)
        assert all("body" not in override for override in after.states.values()), prop_id

        for memory in users[prop_id]:
            result = fake_runs.compile_scene(db, FID, memory.screenplay)
            where = f"{memory.id} after swapping {prop_id}"
            if not result.ok:
                failures.append(f"{where}: {[e.message for e in result.errors]}")
                continue
            got = rig_of(result.timeline)
            want = originals[memory.id]
            if not want.part_tracks <= got.part_tracks:
                failures.append(f"{where}: lost part tracks {sorted(want.part_tracks - got.part_tracks)}")
            if not want.states <= got.states:
                failures.append(f"{where}: lost states {sorted(want.states - got.states)}")
            if want.interactions != got.interactions:
                failures.append(f"{where}: pull tabs {sorted(want.interactions)} -> {sorted(got.interactions)}")
            props = {p.id: p for p in props_db.get_props(db, FID, memory.prop_ids)}
            failures.extend(f"{where}: {problem}" for problem in rig_problems(got, props))

            # A second telling over the same action, as the scripted run writes it.
            teller = _second_teller(db, memory)
            if teller is not None:
                told = fake_runs.telling_narration(memory.screenplay, teller,
                                                   "We all piled in. The wheels went round and round.")
                again = fake_runs.compile_scene(db, FID, told)
                if not again.ok:
                    failures.append(f"{where}, told by {teller}: {[e.message for e in again.errors]}")
                elif teller not in (again.timeline.narrations or {}):
                    failures.append(f"{where}, told by {teller}: no narration track")
    assert failures == []


def test_a_seeded_scene_recompiles_with_every_prop_swapped_at_once() -> None:
    db = _seeded()
    memories = _memories(db)
    for memory in memories:
        for prop in props_db.get_props(db, FID, memory.prop_ids):
            if prop.family_id == FID and prop.source != "photo":
                photo = media_db.put_media(db, FID, _png(), content_type="image/png")
                assert props_db.update_prop(db, FID, prop.id, schemas.PropPatch(photo_id=photo)) is not None
    for memory in memories:
        result = fake_runs.compile_scene(db, FID, memory.screenplay)
        assert result.ok, (memory.id, [e.message for e in result.errors])
        want, got = rig_of(memory.timeline), rig_of(result.timeline)
        assert want.part_tracks <= got.part_tracks and want.states <= got.states, memory.id
        assert rig_problems(got, {p.id: p for p in props_db.get_props(db, FID, memory.prop_ids)}) == []


# ------------------------------------------------------------------- (b) over HTTP


@contextlib.contextmanager
def _offline_app(monkeypatch: pytest.MonkeyPatch) -> Iterator[Api]:
    """The served offline demo, as tests/data/test_fake_runs_paths.py runs it."""
    monkeypatch.setenv("STITCH_FAKE_DB", "1")
    monkeypatch.setenv("STITCH_AGENT", "fake")
    monkeypatch.setenv("STITCH_FAKE_RUN_STEP", "0")
    monkeypatch.delenv("STITCH_CORS_ORIGINS", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(routes.memories, "HEARTBEAT_SECONDS", 0.05)
    db_client.set_db(None)
    app = main.create_app()
    try:
        with TestClient(app) as root:
            served = Api(app=app, root=root)
            try:
                yield served
            finally:
                for c in served.clients:
                    c.close()
    finally:
        agent.runs.configure(None)
        db_client.set_db(None)


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch) -> Iterator[Api]:
    with _offline_app(monkeypatch) as served:
        yield served


def _swap_over_http(client: TestClient, prop_id: str) -> schemas.Prop:
    """Upload a photo of the real thing and make it the prop's art, as the web app does."""
    up = client.post(f"/families/{FID}/media", files={"file": ("real.png", _png(), "image/png")})
    assert up.status_code == 201, up.text
    media_id = schemas.MediaUploadResponse.model_validate(up.json()).id
    res = client.patch(f"/families/{FID}/props/{prop_id}", json={"photoId": media_id})
    assert res.status_code == 200, res.text
    prop = schemas.Prop.model_validate(res.json())
    assert prop.source == "photo" and prop.art["body"] == media_db.media_ref(media_id)
    return prop


def _tell(api: Api, client: TestClient, memory_id: str, text: str, question: str | None = None
          ) -> tuple[schemas.Memory, schemas.MemoryResponse]:
    before = get_memory(client, memory_id).memory
    run_id = submit(client, text, year=before.year, memory_id=memory_id, question=question)
    frames = read(client, run_id, answer=Replies())
    check_run(frames)
    assert frames[-1] == ("done", {"memoryId": memory_id}), names(frames)
    return before, get_memory(client, memory_id)


def _assert_props_cover_the_timeline(res: schemas.MemoryResponse, prop_id: str, teller: str) -> None:
    memory = res.memory
    assert memory.timeline is not None
    props = {p.id: p for p in res.props}
    assert props[prop_id].source == "photo"
    rig = rig_of(memory.timeline)
    assert any(pid == prop_id for pid, _ in rig.parts | rig.states), f"the scene no longer moves {prop_id}"
    assert rig_problems(rig, props) == []
    assert teller in (memory.timeline.narrations or {}), "the new teller has no narration track"
    assert teller in {t.author_id for t in memory.tellings}


@pytest.mark.parametrize(("prop_id", "memory_id"), [
    ("green_sedan", "demo_green_sedan_trip"),  # a pull tab turns the front wheel
    ("red_bike", "demo_sams_first_bike"),  # a pull tab turns the back wheel; the bell rings
])
def test_a_second_telling_after_a_photo_swap_finishes(api: Api, prop_id: str, memory_id: str) -> None:
    ana = next(a for a in demo.DEMO_ACCOUNTS if a.person_id == "mom")
    client = api.login(ana.email)
    stored = get_memory(client, memory_id)
    assert prop_id in {p.id for p in stored.props}
    assert any(tab.drives.type == "prop_part" and tab.drives.prop == prop_id
               for tab in stored.memory.timeline.interactions or []), f"{memory_id} has no pull tab on {prop_id}"
    swapped = _swap_over_http(client, prop_id)
    old = next(p for p in stored.props if p.id == prop_id)
    assert swapped.parts == old.parts and set(swapped.states) == set(old.states)

    before, after = _tell(api, client, memory_id, "I sat in the middle and sang the whole way. "
                                                  "Nobody asked me to stop, so I did not.")
    assert ana.person_id not in {t.author_id for t in before.tellings}
    assert after.memory.version == before.version + 1
    _assert_props_cover_the_timeline(after, prop_id, ana.person_id)


def _question_on_a_moving_part(api: Api) -> tuple[str, str, str, str] | None:
    """(person id, question text, memory id, prop id) of a demo question about a moment whose
    scene has a pull tab on a family prop's part, from DEMO_QUESTIONS and the stored scenes."""
    reader = api.login(demo.DEMO_ACCOUNTS[0].email)
    for person_id, text, memory_id in demo.DEMO_QUESTIONS:
        if memory_id is None:
            continue
        res = get_memory(reader, memory_id)
        family = {p.id for p in res.props if p.family_id == FID}
        for tab in (res.memory.timeline.interactions or []) if res.memory.timeline else []:
            if tab.drives.type == "prop_part" and tab.drives.prop in family:
                return person_id, text, memory_id, tab.drives.prop
    return None


def test_answering_a_demo_question_after_a_photo_swap_finishes(api: Api) -> None:
    found = _question_on_a_moving_part(api)
    if found is None:
        pytest.skip("no demo question is about a moment with a pull tab on a part")
    person_id, text, memory_id, prop_id = found
    client = as_person(api, person_id)
    [question] = [q for q in open_questions(client) if q.memory_id == memory_id and q.text == text]
    _swap_over_http(client, prop_id)

    _, after = _tell(api, client, memory_id, "The lid rattled all morning. We lifted it to peek.",
                     question=question.text)
    _assert_props_cover_the_timeline(after, prop_id, person_id)
    assert not [q for q in open_questions(client) if q.id == question.id], "the question is closed"


# ------------------------------------------------------------------- (c) photo_rig


def _prop(**fields: Any) -> schemas.Prop:
    return schemas.Prop.model_validate({"id": "thing", "name": "thing", "size": "hand",
                                        "art": {"body": "media/old"}, **fields})


def test_photo_rig_with_no_parts_and_no_states_is_the_photo_alone() -> None:
    assert props_db.photo_rig(_prop(), "media/p1") == {
        "art": {"body": "media/p1"}, "parts": {}, "states": {}, "source": "photo"}


def test_photo_rig_keeps_part_overrides_and_drops_the_body() -> None:
    prop = _prop(
        art={"body": "media/b", "lid": "media/l", "steam": "media/s"},
        parts={"lid": {"art": "lid", "at": [0.5, 0.1]}},
        states={"open": {"body": "media/b_open", "lid": "media/l_open", "steam": "media/s2"},
                "shut": {"body": "media/b_shut"}, "plain": {}},
    )
    rig = props_db.photo_rig(prop, "media/p2")
    assert rig["art"] == {"body": "media/p2", "lid": "media/l"}  # 'steam' is no part's art
    assert rig["parts"] == {"lid": {"art": "lid", "at": [0.5, 0.1], "spin": False}}
    assert rig["states"] == {"open": {"lid": "media/l_open"}, "shut": {}, "plain": {}}
    assert rig["source"] == "photo"


def test_photo_rig_gives_a_part_with_no_old_art_the_placeholder() -> None:
    prop = _prop(art={"body": None},
                 parts={"wheel_front": {"art": "wheel", "at": [0.8, 0.9], "spin": True},
                        "wheel_back": {"art": "wheel", "at": [0.2, 0.9], "spin": True}})
    rig = props_db.photo_rig(prop, "media/p3")
    assert rig["art"] == {"body": "media/p3", "wheel": None}
    assert set(rig["parts"]) == {"wheel_front", "wheel_back"}
    # The result is a valid stored rig.
    schemas.Prop.model_validate({**prop.model_dump(by_alias=True), **rig})


def test_photo_rig_keeps_the_photo_for_a_part_drawn_with_the_body() -> None:
    prop = _prop(parts={"whole": {"art": "body", "at": [0.5, 0.5], "spin": True}},
                 states={"lit": {"body": "media/lit"}})
    rig = props_db.photo_rig(prop, "media/p4")
    assert rig["art"] == {"body": "media/p4"}
    assert rig["states"] == {"lit": {}}


def test_photo_rig_is_pure() -> None:
    prop = _prop(art={"body": "media/b", "lid": "media/l"}, parts={"lid": {"art": "lid", "at": [0.5, 0.1]}},
                 states={"open": {"body": "media/x", "lid": "media/y"}})
    snapshot = prop.model_copy(deep=True)
    rig = props_db.photo_rig(prop, "media/p5")
    rig["states"]["open"]["lid"] = "changed"
    rig["art"]["lid"] = "changed"
    assert prop == snapshot
