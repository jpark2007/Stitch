"""The whole film flow over HTTP, offline: the real media upload route, the real films
routes and the real pipeline with its default (fake) services. Nothing is stubbed but the
database (mongomock). This is the seam the browser run covers: photos uploaded one at a time
through POST /families/{fid}/media, then POST /families/{fid}/films, the background build,
and GET until "ready".
"""

from __future__ import annotations

import io
from datetime import timedelta

import mongomock
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import ImageEnhance

import auth
import main
import schemas
from db import families as families_db
from db import people as people_db
from db import users as users_db
from db.client import get_db
from film.timing import MAX_SHOT
from film_checks import assert_well_cut
from film_images import STORY, T0, blurred, encode, scene
from routes import films as films_routes
from routes import props as props_routes
from routes.memories import AGENT_OFF_STATE

FAM_A = "fam_a"
FAM_B = "fam_b"
TEST_JWT_SECRET = "stitch-test-secret-0123456789abcdef0123456789"


def _photos() -> list[tuple[str, bytes]]:
    """12 JPEGs as a person would pick them: 10 distinct scenes (portrait and landscape, three
    with capture times), a blurred burst twin of the third and a re-encoded, slightly
    brighter copy of the seventh with no EXIF. Upload order is the list order."""
    out: list[tuple[str, bytes]] = []
    for i in range(10):
        size = (480, 640) if i % 3 == 1 else (640, 480)
        taken = T0 + timedelta(minutes=25 * i) if i < 3 else None
        out.append((f"{i:02d}.jpg", encode(scene(100 + i, size=size), taken=taken)))
        if i == 2:
            burst = blurred(scene(100 + i, size=size))
            out.append(("02-burst.jpg", encode(burst, taken=taken + timedelta(seconds=2))))
        if i == 6:
            copy = ImageEnhance.Brightness(scene(100 + i, size=size)).enhance(1.06)
            out.append(("06-copy.jpg", encode(copy)))
    return out


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", TEST_JWT_SECRET)
    monkeypatch.delenv("COOKIE_SECURE", raising=False)
    client = mongomock.MongoClient(tz_aware=True)
    mdb = client["stitch_test"]
    alice = users_db.create_user(mdb, email="alice@x.com", password_hash="x", name="Alice")
    bob = users_db.create_user(mdb, email="bob@x.com", password_hash="x", name="Bob")
    families_db.create_family(mdb, name="Family A", creator_user_id=alice.id, family_id=FAM_A)
    families_db.create_family(mdb, name="Family B", creator_user_id=bob.id, family_id=FAM_B)
    people_db.create_person(mdb, FAM_A, schemas.NewPerson(name="Alice"), added_by=alice.id, user_id=alice.id)
    people_db.create_person(mdb, FAM_B, schemas.NewPerson(name="Bob"), added_by=bob.id, user_id=bob.id)

    app = FastAPI()
    app.add_exception_handler(main.RequestValidationError, main.validation_error_handler)
    app.include_router(props_routes.router)
    app.include_router(films_routes.router)
    app.dependency_overrides[get_db] = lambda: mdb
    # As main.py leaves it offline; no app.state.film_services, so default_services("fake").
    setattr(app.state, AGENT_OFF_STATE, "fake")
    tc = TestClient(app)
    tc.cookies.set(auth.COOKIE_NAME, auth.create_session_token(alice.id))
    bob_tc = TestClient(app)
    bob_tc.cookies.set(auth.COOKIE_NAME, auth.create_session_token(bob.id))
    yield tc, bob_tc
    client.close()


def test_uploaded_photos_become_a_ready_offline_film(env):
    tc, bob_tc = env
    picked = _photos()
    ids: dict[str, str] = {}
    for name, data in picked:
        r = tc.post(f"/families/{FAM_A}/media", files={"file": (name, io.BytesIO(data), "image/jpeg")})
        assert r.status_code == 201, r.text
        ids[name] = r.json()["id"]
    names = {v: k for k, v in ids.items()}

    r = tc.post(f"/families/{FAM_A}/films", json={"text": STORY, "year": 1998, "photoIds": list(ids.values())})
    assert r.status_code == 202, r.text
    assert r.json()["status"] == "processing"
    film_id = r.json()["id"]

    # TestClient runs the background build before returning, so the first read is final.
    r = tc.get(f"/families/{FAM_A}/films/{film_id}")
    assert r.status_code == 200
    film = schemas.Film.model_validate(r.json())
    assert film.status == "ready", film.error
    assert film.audio is None  # offline: silent, with estimated word timings
    assert film.words and film.title.strip()
    sources = {ids[name]: data for name, data in picked}
    assert_well_cut(film.shots, film.beats, film.words, film.duration, set(ids.values()), sources)

    shown = [names[s.photo_id] for s in film.shots]
    # The story asks for about 10 shots, and there are exactly 10 different scenes: every one
    # is in, and of each near-duplicate pair only one (the sharp frame of the burst).
    assert len(shown) == 10
    assert "02.jpg" in shown and "02-burst.jpg" not in shown
    assert ("06.jpg" in shown) != ("06-copy.jpg" in shown)
    assert {f"{i:02d}.jpg" for i in range(10) if i != 6} <= set(shown)
    # Without capture times on every photo the film keeps the order they were picked in.
    order = [n for n, _ in picked]
    assert shown == sorted(shown, key=order.index)
    assert len(shown) == len(set(shown))
    assert all(s.duration <= MAX_SHOT for s in film.shots)

    listed = tc.get(f"/families/{FAM_A}/films").json()["films"]
    assert [f["id"] for f in listed] == [film_id]

    # Another family: neither under its own id nor under this family's is the film there.
    assert bob_tc.get(f"/families/{FAM_B}/films/{film_id}").status_code == 404
    assert bob_tc.get(f"/families/{FAM_A}/films/{film_id}").status_code == 404
    assert bob_tc.get(f"/families/{FAM_B}/films").json()["films"] == []
