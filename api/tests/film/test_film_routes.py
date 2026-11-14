"""routes/films.py: create, list and read films, and the background build.

Runs on mongomock. The pipeline is never called: film.pipeline (and film.services where it
matters) are replaced in sys.modules by stubs, so these tests do not depend on it.
"""

from __future__ import annotations

import sys
import types
from datetime import UTC, datetime, timedelta

import mongomock
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import auth
import main
import schemas
from db import families as families_db
from db import films as films_db
from db import media as media_db
from db import people as people_db
from db import users as users_db
from db.client import get_db
from film.types import FilmCut, FilmError, Narration, Services
from routes import films as films_routes
from routes import memories as memories_routes

FAM_A = "fam_a"
FAM_B = "fam_b"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
TEST_JWT_SECRET = "stitch-test-secret-0123456789abcdef0123456789"


# --------------------------------------------------------------------- stubs


class _Describer:
    def describe(self, photo: bytes) -> dict:  # pragma: no cover - the stub pipeline never calls it
        return {}


class _Narrator:
    def speak(self, lines: list[str]) -> Narration:  # pragma: no cover
        return Narration(audio=None, content_type=None, line_starts=[], words=[], duration=0)


SERVICES = Services(describer=_Describer(), editor=None, narrator=_Narrator())


class Pipeline:
    """A stand-in for film.pipeline: records each call and answers with `result`."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.result: FilmCut | Exception = self.cut()

    @staticmethod
    def cut(audio: bytes | None = b"ID3 narration") -> FilmCut:
        return FilmCut(
            title="The summer at the lake",
            shots=[],  # filled per call from the photos it was given
            beats=[schemas.FilmBeat(title="Arriving", start=0, narration="We drove to the lake.")],
            words=[schemas.FilmWord(t=0.2, word="We"), schemas.FilmWord(t=0.5, word="drove")],
            audio=audio,
            audio_content_type="audio/mpeg" if audio else None,
            duration=8.0,
        )

    def make_film(self, photos, story, services):
        self.calls.append((photos, story, services))
        if isinstance(self.result, Exception):
            raise self.result
        shots = [schemas.FilmShot(photo_id=p.id, start=i * 4.0, duration=4.0, beat=0, caption=f"shot {i}")
                 for i, p in enumerate(photos[1:3])]
        return FilmCut(**{**self.result.__dict__, "shots": shots})


@pytest.fixture
def pipeline(monkeypatch) -> Pipeline:
    stub = Pipeline()
    module = types.ModuleType("film.pipeline")
    module.make_film = stub.make_film
    monkeypatch.setitem(sys.modules, "film.pipeline", module)
    return stub


# --------------------------------------------------------------------- fixtures


@pytest.fixture
def env(monkeypatch, pipeline):
    """Two families. Alice is in A (with Grandma, 9 photos and a narration file), Bob in B
    (with a photo). The client is signed in as Alice; film services are the stub."""
    monkeypatch.setenv("JWT_SECRET", TEST_JWT_SECRET)
    monkeypatch.delenv("COOKIE_SECURE", raising=False)
    client = mongomock.MongoClient(tz_aware=True)
    mdb = client["stitch_test"]
    alice = users_db.create_user(mdb, email="alice@x.com", password_hash="x", name="Alice")
    bob = users_db.create_user(mdb, email="bob@x.com", password_hash="x", name="Bob")
    families_db.create_family(mdb, name="Family A", creator_user_id=alice.id, family_id=FAM_A)
    families_db.create_family(mdb, name="Family B", creator_user_id=bob.id, family_id=FAM_B)
    grandma = people_db.create_person(mdb, FAM_A, schemas.NewPerson(name="Grandma"), added_by=alice.id)
    me = people_db.create_person(mdb, FAM_A, schemas.NewPerson(name="Alice"), added_by=alice.id, user_id=alice.id)
    bo = people_db.create_person(mdb, FAM_B, schemas.NewPerson(name="Bo"), added_by=bob.id, user_id=bob.id)
    photos = [media_db.put_media(mdb, FAM_A, PNG + bytes([i]), content_type="image/png", filename=f"{i}.png")
              for i in range(9)]
    audio = media_db.put_media(mdb, FAM_A, b"ID3", content_type="audio/mpeg", kind="audio")
    b_photo = media_db.put_media(mdb, FAM_B, PNG, content_type="image/png")

    app = FastAPI()
    app.add_exception_handler(main.RequestValidationError, main.validation_error_handler)
    app.include_router(films_routes.router)
    app.dependency_overrides[get_db] = lambda: mdb
    app.state.film_services = SERVICES
    tc = TestClient(app)
    tc.cookies.set(auth.COOKIE_NAME, auth.create_session_token(alice.id))
    bob_tc = TestClient(app)
    bob_tc.cookies.set(auth.COOKIE_NAME, auth.create_session_token(bob.id))
    yield types.SimpleNamespace(tc=tc, bob_tc=bob_tc, app=app, db=mdb, alice=alice, bob=bob,
                                grandma=grandma, me=me, bo=bo, photos=photos, audio=audio, b_photo=b_photo,
                                pipeline=pipeline)
    client.close()


def _url(fid: str = FAM_A, film_id: str | None = None) -> str:
    return f"/families/{fid}/films" + (f"/{film_id}" if film_id else "")


def _body(env, **over) -> dict:
    body = {"text": "We drove to the lake. Dad burned the pancakes.", "year": 1998, "photoIds": env.photos[:8]}
    body.update(over)
    return body


def _films(db) -> int:
    return db[films_db.COLLECTION].count_documents({})


# --------------------------------------------------------------------- happy path


def test_create_returns_processing_then_builds_to_ready(env):
    res = env.tc.post(_url(), json=_body(env, participantIds=[env.grandma.id, env.me.id]))
    assert res.status_code == 202, res.text
    made = schemas.Film.model_validate(res.json())
    assert made.status == "processing" and made.shots == [] and made.audio is None and made.duration == 0
    assert made.author_id == env.me.id and made.family_id == FAM_A
    assert made.photo_ids == env.photos[:8] and made.participant_ids == [env.grandma.id, env.me.id]

    # TestClient runs background tasks before returning, so the build is done.
    photos, story, services = env.pipeline.calls[0]
    assert [p.id for p in photos] == env.photos[:8]
    assert photos[0].data == PNG + bytes([0]) and photos[0].content_type == "image/png"
    assert photos[0].filename == "0.png"
    assert story.text == made.text and story.year == 1998 and story.title is None
    assert story.people == ["Grandma", "Alice"]
    assert services is SERVICES

    got = env.tc.get(_url(film_id=made.id))
    assert got.status_code == 200
    film = schemas.Film.model_validate(got.json())
    assert film.status == "ready" and film.error is None
    assert film.title == "The summer at the lake"
    assert [s.photo_id for s in film.shots] == env.photos[1:3]
    assert film.beats[0].title == "Arriving" and [w.word for w in film.words] == ["We", "drove"]
    assert film.duration == 8.0
    audio_id = media_db.parse_media_ref(film.audio)
    stored = media_db.get_media(env.db, FAM_A, audio_id)
    assert film.audio.startswith("media/") and stored.kind == "audio"
    assert stored.data == b"ID3 narration" and stored.content_type == "audio/mpeg"


def test_silent_cut_has_no_audio_and_a_given_title_wins(env):
    env.pipeline.result = Pipeline.cut(audio=None)
    made = env.tc.post(_url(), json=_body(env, title="  Lake Wren  ")).json()
    assert made["title"] == "Lake Wren"
    assert env.pipeline.calls[0][1].title == "Lake Wren"
    film = env.tc.get(_url(film_id=made["id"])).json()
    assert film["status"] == "ready" and film["audio"] is None and film["title"] == "Lake Wren"


def test_photo_refs_and_repeats_collapse(env):
    ids = [f"media/{env.photos[0]}", *env.photos[:8]]
    res = env.tc.post(_url(), json=_body(env, photoIds=ids))
    assert res.status_code == 202, res.text
    assert res.json()["photoIds"] == env.photos[:8]


# --------------------------------------------------------------------- build failures


def test_film_error_fails_with_its_message(env):
    env.pipeline.result = FilmError("These photos are too blurry to make a film from")
    made = env.tc.post(_url(), json=_body(env)).json()
    film = env.tc.get(_url(film_id=made["id"])).json()
    assert film["status"] == "failed"
    assert film["error"] == "These photos are too blurry to make a film from"


def test_a_crash_fails_with_the_generic_message(env, caplog):
    env.pipeline.result = RuntimeError("secret internals at /Users/x/key=sk-ant")
    made = env.tc.post(_url(), json=_body(env)).json()
    film = env.tc.get(_url(film_id=made["id"])).json()
    assert film["status"] == "failed" and film["error"] == films_routes.BUILD_FAILED
    assert "sk-ant" not in film["error"]


def test_a_services_failure_fails_the_film(env):
    def broken() -> Services:
        raise RuntimeError("no adapters")

    env.app.state.film_services = broken
    made = env.tc.post(_url(), json=_body(env)).json()
    assert env.tc.get(_url(film_id=made["id"])).json()["status"] == "failed"
    assert env.pipeline.calls == []


def test_default_services_use_the_agent_mode(env, monkeypatch):
    seen: list[str] = []
    module = types.ModuleType("film.services")
    module.default_services = lambda mode: seen.append(mode) or SERVICES
    monkeypatch.setitem(sys.modules, "film.services", module)
    env.app.state.film_services = None
    setattr(env.app.state, memories_routes.AGENT_OFF_STATE, "off")  # what main.py's startup stores
    env.tc.post(_url(), json=_body(env))
    delattr(env.app.state, memories_routes.AGENT_OFF_STATE)  # no startup ran: "fake"
    env.tc.post(_url(), json=_body(env))
    assert seen == ["off", "fake"]
    assert env.pipeline.calls[0][2] is SERVICES


def test_stale_processing_reads_as_failed(env):
    old = datetime.now(UTC) - films_db.STALE_AFTER - timedelta(minutes=1)
    film = films_db.create_film(env.db, FAM_A, author_id=env.me.id, text="x", year=1998,
                                photo_ids=env.photos[:8], now=old)
    got = env.tc.get(_url(film_id=film.id)).json()
    assert got["status"] == "failed" and got["error"] == films_db.STALE_ERROR
    assert env.tc.get(_url()).json()["films"][0]["status"] == "failed"


# --------------------------------------------------------------------- validation


@pytest.mark.parametrize("over", [
    {"photoIds": "seven"},
    {"text": ""},
    {"text": "   "},
    {"text": "x" * (memories_routes.MAX_TEXT_CHARS + 1)},
    {"year": 1799},
    {"year": datetime.now(UTC).year + 1},
    {"title": "x" * 201},
])
def test_bad_fields_are_422(env, over):
    body = _body(env, **over)
    if over.get("photoIds") == "seven":
        body["photoIds"] = env.photos[:7]
    res = env.tc.post(_url(), json=body)
    assert res.status_code == 422, res.text
    assert _films(env.db) == 0 and env.pipeline.calls == []


def test_too_few_or_too_many_photos_is_422(env):
    assert env.tc.post(_url(), json=_body(env, photoIds=env.photos[:7])).status_code == 422
    too_many = [str(i) for i in range(schemas.MAX_FILM_PHOTOS + 1)]
    assert env.tc.post(_url(), json=_body(env, photoIds=too_many)).status_code == 422
    # Eight ids, but only seven different photos.
    res = env.tc.post(_url(), json=_body(env, photoIds=[*env.photos[:7], env.photos[0]]))
    assert res.status_code == 422 and res.json() == {"detail": films_routes.TOO_FEW_PHOTOS}
    assert _films(env.db) == 0


@pytest.mark.parametrize("bad", ["audio", "b_photo", "bogus", "unknown"])
def test_a_non_photo_or_foreign_media_id_is_400(env, bad):
    wrong = {"audio": env.audio, "b_photo": env.b_photo, "bogus": "not-an-id", "unknown": "0" * 24}[bad]
    res = env.tc.post(_url(), json=_body(env, photoIds=[*env.photos[:7], wrong]))
    assert res.status_code == 400
    assert res.json() == {"detail": films_routes.UNKNOWN_PHOTO}
    assert _films(env.db) == 0


def test_a_participant_outside_the_family_is_400(env):
    for ids in (["nobody"], [env.bo.id], [env.me.id, env.bo.id]):
        res = env.tc.post(_url(), json=_body(env, participantIds=ids))
        assert res.status_code == 400 and res.json() == {"detail": films_routes.UNKNOWN_PARTICIPANT}
    assert _films(env.db) == 0


def test_a_caller_without_a_person_is_409(env):
    env.db[people_db.COLLECTION].update_one({"_id": env.me.id}, {"$set": {"userId": None}})
    res = env.tc.post(_url(), json=_body(env))
    assert res.status_code == 409 and res.json() == {"detail": memories_routes.NO_PERSON}
    assert _films(env.db) == 0


# --------------------------------------------------------------------- reads, isolation, auth


def test_list_is_newest_first_with_covers(env):
    first = env.tc.post(_url(), json=_body(env)).json()
    env.pipeline.result = FilmError("nope")
    second = env.tc.post(_url(), json=_body(env, photoIds=env.photos[1:9])).json()

    res = env.tc.get(_url())
    assert res.status_code == 200
    films = schemas.FilmsResponse.model_validate(res.json()).films
    assert [f.id for f in films] == [second["id"], first["id"]]
    assert films[0].status == "failed" and films[0].cover_photo_id == env.photos[1]  # first uploaded
    assert films[1].status == "ready" and films[1].cover_photo_id == env.photos[1]  # first shot
    assert films[1].duration == 8.0 and films[1].author_id == env.me.id
    assert env.bob_tc.get(_url(FAM_B)).json() == {"films": []}


def test_another_familys_film_is_404(env):
    film = env.tc.post(_url(), json=_body(env)).json()
    # Bob, in his own family, asking for A's film id.
    res = env.bob_tc.get(_url(FAM_B, film["id"]))
    assert res.status_code == 404 and res.json() == {"detail": films_routes.NOT_FOUND}
    assert env.tc.get(_url(film_id="nope")).status_code == 404
    # Bob is not in family A at all.
    for res in (env.bob_tc.get(_url(FAM_A, film["id"])), env.bob_tc.get(_url(FAM_A)),
                env.bob_tc.post(_url(FAM_A), json=_body(env))):
        assert res.status_code == 404
    assert _films(env.db) == 1


def test_signed_out_is_401(env):
    anon = TestClient(env.app)
    for res in (anon.get(_url()), anon.get(_url(film_id="x")), anon.post(_url(), json=_body(env))):
        assert res.status_code == 401
    assert _films(env.db) == 0


def test_router_is_mounted_in_main():
    assert films_routes.router in main.ROUTERS
    paths = {(m, r.path) for r in films_routes.router.routes for m in r.methods}
    assert paths == {("POST", "/families/{fid}/films"), ("GET", "/families/{fid}/films"),
                     ("GET", "/families/{fid}/films/{film_id}")}
