"""db/films.py: the films store. Runs on mongomock."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import mongomock
import pytest

import schemas
from db import films as films_db

FAM_A = "fam_a"
FAM_B = "fam_b"
PHOTOS = [f"p{i}" for i in range(8)]
T0 = datetime(2026, 11, 14, 12, 0, tzinfo=UTC)


@pytest.fixture
def mdb():
    client = mongomock.MongoClient(tz_aware=True)
    yield client["stitch_test"]
    client.close()


def _create(db, fid=FAM_A, **over) -> schemas.Film:
    args = {"author_id": "me", "text": "We drove to the lake.", "year": 1998, "photo_ids": PHOTOS, "now": T0}
    args.update(over)
    return films_db.create_film(db, fid, **args)


def _shots() -> list[schemas.FilmShot]:
    return [
        schemas.FilmShot(photo_id="p3", start=0, duration=4.5, beat=0, caption="the dock", focus=(0.2, 0.7),
                         motion="left"),
        schemas.FilmShot(photo_id="p1", start=4.5, duration=5, beat=0),
    ]


def test_create_is_processing_with_an_empty_cut(mdb):
    film = _create(mdb, participant_ids=["a", "b", "a"], photo_ids=PHOTOS + ["p0"])
    assert film.status == "processing" and film.error is None
    assert film.shots == [] and film.beats == [] and film.words == [] and film.audio is None
    assert film.duration == 0
    assert film.title == films_db.placeholder_title(1998)
    assert film.photo_ids == PHOTOS and film.participant_ids == ["a", "b"]  # repeats collapse
    assert film.family_id == FAM_A and film.author_id == "me" and film.created_at == T0
    assert films_db.get_film(mdb, FAM_A, film.id, now=T0) == film
    assert films_db.requested_title(mdb, FAM_A, film.id) is None


def test_a_given_title_is_kept_through_finish(mdb):
    film = _create(mdb, title="  Lake Wren  ")
    assert film.title == "Lake Wren"
    assert films_db.requested_title(mdb, FAM_A, film.id) == "Lake Wren"
    done = films_db.finish_film(mdb, FAM_A, film.id, title="Something else", shots=_shots(), beats=[], words=[],
                                audio=None, duration=9.5, now=T0)
    assert done is not None and done.title == "Lake Wren"


def test_finish_stores_the_cut_and_marks_ready(mdb):
    film = _create(mdb)
    beats = [schemas.FilmBeat(title="Arriving", start=0, narration="We drove to the lake.")]
    words = [schemas.FilmWord(t=0.1, word="We"), schemas.FilmWord(t=0.4, word="drove")]
    done = films_db.finish_film(mdb, FAM_A, film.id, title="The lake", shots=_shots(), beats=beats, words=words,
                                audio="media/abc", duration=9.5, now=T0)
    assert done is not None
    assert done.status == "ready" and done.error is None
    assert done.title == "The lake"
    assert done.shots == _shots() and done.beats == beats and done.words == words
    assert done.shots[0].focus == (0.2, 0.7) and done.shots[0].motion == "left"
    assert done.audio == "media/abc" and done.duration == 9.5
    stored = mdb[films_db.COLLECTION].find_one({"_id": film.id})
    assert stored["shots"][0]["photoId"] == "p3"  # camelCase JSON in storage


def test_fail_marks_failed_with_the_message(mdb):
    film = _create(mdb)
    failed = films_db.fail_film(mdb, FAM_A, film.id, "Too few usable photos", now=T0)
    assert failed is not None and failed.status == "failed" and failed.error == "Too few usable photos"


def test_other_family_ids_read_like_unknown_ones(mdb):
    film = _create(mdb)
    assert films_db.get_film(mdb, FAM_B, film.id) is None
    assert films_db.get_film(mdb, FAM_A, "nope") is None
    assert films_db.fail_film(mdb, FAM_B, film.id, "x") is None
    assert films_db.finish_film(mdb, FAM_B, film.id, title="x", shots=[], beats=[], words=[], audio=None,
                                duration=0) is None
    assert films_db.get_film(mdb, FAM_A, film.id, now=T0).status == "processing"  # untouched
    assert films_db.list_films(mdb, FAM_B) == []


def test_list_is_newest_first_with_covers(mdb):
    old = _create(mdb, now=T0)
    new = _create(mdb, now=T0 + timedelta(minutes=1), photo_ids=["q1", *PHOTOS])
    films_db.finish_film(mdb, FAM_A, old.id, title="Old", shots=_shots(), beats=[], words=[], audio=None,
                         duration=9.5, now=T0)
    _create(mdb, FAM_B)
    listed = films_db.list_films(mdb, FAM_A, now=T0 + timedelta(minutes=2))
    assert [f.id for f in listed] == [new.id, old.id]
    assert listed[0].cover_photo_id == "q1"  # no shots yet: first uploaded photo
    assert listed[1].cover_photo_id == "p3"  # first shot's photo
    assert listed[1].status == "ready" and listed[1].duration == 9.5 and listed[1].title == "Old"
    assert listed[0].author_id == "me" and listed[0].year == 1998


def test_processing_reads_as_failed_once_stale(mdb):
    film = _create(mdb)
    fresh = T0 + films_db.STALE_AFTER - timedelta(seconds=1)
    stale = T0 + films_db.STALE_AFTER + timedelta(seconds=1)
    assert films_db.get_film(mdb, FAM_A, film.id, now=fresh).status == "processing"
    got = films_db.get_film(mdb, FAM_A, film.id, now=stale)
    assert got.status == "failed" and got.error == films_db.STALE_ERROR
    assert films_db.list_films(mdb, FAM_A, now=stale)[0].status == "failed"
    # A finished film is never stale.
    films_db.finish_film(mdb, FAM_A, film.id, title="x", shots=_shots(), beats=[], words=[], audio=None,
                         duration=9.5, now=T0)
    assert films_db.get_film(mdb, FAM_A, film.id, now=stale).status == "ready"


def test_create_with_a_taken_id_is_a_value_error(mdb):
    _create(mdb, film_id="f1")
    with pytest.raises(ValueError):
        _create(mdb, FAM_B, film_id="f1")
