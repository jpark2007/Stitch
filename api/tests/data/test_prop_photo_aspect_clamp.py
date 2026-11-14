"""A photo swap clamps the prop's aspect, so every memory saved with the prop recompiles.

PATCH /families/{fid}/props/{id} with a photoId (routes/props.patch_prop, db/props.update_prop)
promises that every memory already saved with the prop still recompiles, a second telling
included. A prop's aspect sizes its drawn body, so a 3:1 panorama of the demo's green sedan
pushed the trip's blocking off the set ('dad cannot sit_on "fruit_crate"; dad is offstage').
update_prop now clamps the photo's ratio into [MIN_PHOTO_ASPECT, MAX_PHOTO_ASPECT].

(a) over HTTP on the seeded demo: a 300x100 photo stores MAX_PHOTO_ASPECT, a 100x300 photo
    MIN_PHOTO_ASPECT, an in-range 400x300 photo its own ratio;
(b) regression: after the 300x100 swap on green_sedan, a scripted second telling by Lily on
    demo_green_sedan_trip ends `done` with her narration track;
(c) sweep: every demo-family prop a seeded memory uses, at each bound, recompiles every memory
    that uses it, plain and with a second telling's narration added: zero compile errors.

Props and memories are discovered from the seeded data; only the regression names its ids.
Runs on mongomock only; no MongoDB, no keys, no network.
"""

from __future__ import annotations

import io
from typing import Any

import mongomock
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

import auth
import schemas
from db import client as db_client
from db import media as media_db
from db import memories as memories_db
from db import people as people_db
from db import props as props_db
from db.client import get_db
from routes import props as props_routes
from seed import demo, fake_runs

FID = demo.DEMO_FAMILY_ID
ADMIN = demo.DEMO_ACCOUNTS[0]
SEDAN = "green_sedan"
TRIP = "demo_green_sedan_trip"
RUN_TIMEOUT = 30.0
SECOND_TELLING = "We all piled in. The wheels went round and round."


def _png(width: int, height: int) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (width, height), (94, 140, 74)).save(out, format="PNG")
    return out.getvalue()


def _png_at(aspect: float, height: int = 100) -> bytes:
    return _png(round(aspect * height), height)


def _seeded() -> Any:
    db = mongomock.MongoClient(tz_aware=True)["stitch_aspect_clamp"]
    db_client.ensure_indexes(db)
    demo.seed_demo(db)
    return db


@pytest.fixture
def db() -> Any:
    return _seeded()


@pytest.fixture
def client(db: Any) -> TestClient:
    """The props routes on the seeded demo, logged in as the demo admin."""
    app = FastAPI()
    app.include_router(props_routes.router)
    app.dependency_overrides[get_db] = lambda: db
    tc = TestClient(app)
    tc.cookies.set(auth.COOKIE_NAME, auth.create_session_token(ADMIN.user_id))
    return tc


def _swap_over_http(client: TestClient, prop_id: str, data: bytes) -> schemas.Prop:
    up = client.post(f"/families/{FID}/media", files={"file": ("photo.png", data, "image/png")})
    assert up.status_code == 201, up.text
    media_id = schemas.MediaUploadResponse.model_validate(up.json()).id
    res = client.patch(f"/families/{FID}/props/{prop_id}", json={"photoId": media_id})
    assert res.status_code == 200, res.text
    prop = schemas.Prop.model_validate(res.json())
    assert prop.source == "photo" and prop.art["body"] == media_db.media_ref(media_id)
    return prop


def _swap(db: Any, prop_id: str, data: bytes) -> schemas.Prop:
    photo = media_db.put_media(db, FID, data, content_type="image/png", filename=f"{prop_id}.png")
    prop = props_db.update_prop(db, FID, prop_id, schemas.PropPatch(photo_id=media_db.media_ref(photo)))
    assert prop is not None, prop_id
    return prop


# --------------------------------------------------------------------- the bounds


def test_the_bounds_are_ordered_around_square() -> None:
    assert 0 < props_db.MIN_PHOTO_ASPECT < 1 < props_db.MAX_PHOTO_ASPECT


@pytest.mark.parametrize(("aspect", "want"), [
    (3.0, props_db.MAX_PHOTO_ASPECT),
    (100.0, props_db.MAX_PHOTO_ASPECT),
    (0.3333, props_db.MIN_PHOTO_ASPECT),
    (0.01, props_db.MIN_PHOTO_ASPECT),
    (1.3333, 1.3333),
    (props_db.MIN_PHOTO_ASPECT, props_db.MIN_PHOTO_ASPECT),
    (props_db.MAX_PHOTO_ASPECT, props_db.MAX_PHOTO_ASPECT),
])
def test_clamp_photo_aspect(aspect: float, want: float) -> None:
    assert props_db.clamp_photo_aspect(aspect) == want


def test_the_docstrings_promise_the_clamp() -> None:
    for doc in (props_db.update_prop.__doc__, props_routes.patch_prop.__doc__):
        assert doc is not None
        assert "MIN_PHOTO_ASPECT" in doc and "MAX_PHOTO_ASPECT" in doc
        assert "nearest allowed aspect" in doc


# ------------------------------------------------------------------- (a) over HTTP


def test_a_wide_photo_stores_the_max_aspect(client: TestClient, db: Any) -> None:
    prop = _swap_over_http(client, SEDAN, _png(300, 100))
    assert prop.aspect == props_db.MAX_PHOTO_ASPECT
    stored = props_db.get_prop(db, FID, SEDAN)
    assert stored is not None and stored.aspect == props_db.MAX_PHOTO_ASPECT


def test_a_tall_photo_stores_the_min_aspect(client: TestClient, db: Any) -> None:
    prop = _swap_over_http(client, SEDAN, _png(100, 300))
    assert prop.aspect == props_db.MIN_PHOTO_ASPECT
    stored = props_db.get_prop(db, FID, SEDAN)
    assert stored is not None and stored.aspect == props_db.MIN_PHOTO_ASPECT


def test_an_in_range_photo_stores_its_own_ratio(client: TestClient, db: Any) -> None:
    prop = _swap_over_http(client, SEDAN, _png(400, 300))
    assert prop.aspect == pytest.approx(400 / 300, abs=0.01)
    stored = props_db.get_prop(db, FID, SEDAN)
    assert stored is not None and stored.aspect == pytest.approx(400 / 300, abs=0.01)


def test_an_unreadable_photo_keeps_the_old_aspect(db: Any) -> None:
    before = props_db.get_prop(db, FID, SEDAN)
    assert before is not None
    after = _swap(db, SEDAN, b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
    assert after.aspect == before.aspect


# ---------------------------------------------------------------- (b) regression


def _tell_scripted(db: Any, memory_id: str, teller: str, text: str) -> tuple[str, list[tuple[str, Any]]]:
    """A second telling through the scripted studio, as the offline demo runs it."""
    memory = memories_db.get_memory(db, FID, memory_id)
    assert memory is not None
    manager = fake_runs.ScriptedRunManager(db_getter=lambda: db, step_seconds=0)
    try:
        fields = schemas.SubmitMemoryFields(text=text, year=memory.year, memory_id=memory_id)
        run_id = manager.start_run(FID, ADMIN.user_id, teller, fields, [])
        status = manager.wait(FID, run_id, timeout=RUN_TIMEOUT)
        return status, manager.events(FID, run_id)
    finally:
        manager.shutdown()


def test_a_second_telling_after_a_panorama_swap_on_the_sedan_finishes(client: TestClient, db: Any) -> None:
    trip = memories_db.get_memory(db, FID, TRIP)
    assert trip is not None and SEDAN in trip.prop_ids
    assert "lily" not in {t.author_id for t in trip.tellings}
    assert _swap_over_http(client, SEDAN, _png(300, 100)).aspect == props_db.MAX_PHOTO_ASPECT

    status, events = _tell_scripted(db, TRIP, "lily", SECOND_TELLING)
    names = [name for name, _ in events]
    errors = [payload.errors for name, payload in events if name == "compile_error"]
    assert status == "done", (names, errors)
    assert names[-1] == "done"
    after = memories_db.get_memory(db, FID, TRIP)
    assert after is not None and after.timeline is not None
    assert after.version == trip.version + 1
    assert "lily" in (after.timeline.narrations or {})
    assert "lily" in {t.author_id for t in after.tellings}


# ------------------------------------------------------------------- (c) sweep


def _second_teller(db: Any, memory: schemas.Memory) -> str | None:
    """Someone in the family who has not told this moment, preferring someone in the scene."""
    told = {t.author_id for t in memory.tellings}
    untold = [p.id for p in people_db.list_people(db, FID) if p.id not in told]
    in_scene = [pid for pid in untold if pid in memory.screenplay.cast]
    return (in_scene or untold or [None])[0]


@pytest.mark.parametrize("bound", ["MIN_PHOTO_ASPECT", "MAX_PHOTO_ASPECT"])
def test_every_demo_prop_at_each_bound_recompiles_every_memory_that_uses_it(bound: str) -> None:
    aspect = getattr(props_db, bound)
    db = _seeded()
    ids = [m.id for m in memories_db.moment_summaries(db, FID)]
    memories = [m for m in memories_db.get_memories(db, FID, ids) if m.screenplay is not None]
    assert memories, "the demo seeds no scenes"
    users: dict[str, list[schemas.Memory]] = {}
    for memory in memories:
        for prop_id in dict.fromkeys(memory.prop_ids):
            users.setdefault(prop_id, []).append(memory)
    family = sorted(pid for pid in users
                    if (p := props_db.get_prop(db, FID, pid)) is not None and p.family_id == FID)
    assert SEDAN in family, "the sweep must cover the prop the bug was found on"

    failures: list[str] = []
    for prop_id in family:
        before = props_db.get_prop(db, FID, prop_id)
        assert before is not None
        swapped = _swap(db, prop_id, _png_at(aspect))
        assert swapped.aspect == pytest.approx(aspect, abs=1e-4), prop_id
        for memory in users[prop_id]:
            where = f"{memory.id} with {prop_id} at {aspect}"
            result = fake_runs.compile_scene(db, FID, memory.screenplay)
            if not result.ok:
                failures.append(f"{where}: {[e.message for e in result.errors]}")
            teller = _second_teller(db, memory)
            if teller is None:
                continue
            told = fake_runs.telling_narration(memory.screenplay, teller, SECOND_TELLING)
            again = fake_runs.compile_scene(db, FID, told)
            if not again.ok:
                failures.append(f"{where}, told by {teller}: {[e.message for e in again.errors]}")
            elif teller not in (again.timeline.narrations or {}):
                failures.append(f"{where}, told by {teller}: no narration track")
        # Put the prop's aspect back so each prop is measured on its own.
        db[props_db.PROPS].update_one({"familyId": FID, "id": prop_id}, {"$set": {"aspect": before.aspect}})
    assert failures == []
