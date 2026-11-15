"""Seeding the loaded family beside the Maos, through db/ only."""

from __future__ import annotations

import auth
from db import families as families_db
from db import media as media_db
from db import memories as memories_db
from db import people as people_db
from db import props as props_db
from db import users as users_db
from seed.demo import DEMO_FAMILY_ID

from demo.whitakers.data import load_family
from demo.whitakers.seed import family_is_complete, seed_family
from demo.whitakers.tests.conftest import CLEO, FID, FIXTURE_DIR, PASSWORD


def test_the_family_is_seeded_complete_beside_the_maos(mdb):
    data = load_family(FIXTURE_DIR)
    assert seed_family(mdb, data) is True
    assert family_is_complete(mdb, data)

    family = families_db.get_family(mdb, FID)
    assert family.name == "The Testers"
    assert {m.user_id: m.role for m in family.members} == {"testers_user_cleo": "admin", "testers_user_dev": "member"}
    user, password_hash = users_db.get_login(mdb, CLEO)
    assert user.id == "testers_user_cleo" and auth.verify_password(password_hash, PASSWORD)

    people = {p.id: p for p in people_db.list_people(mdb, FID)}
    assert set(people) == {"ada", "ben", "cleo", "dev"}
    assert people["cleo"].user_id == "testers_user_cleo" and people["dev"].user_id == "testers_user_dev"
    assert people["ada"].spouse_ids == ["ben"] and people["ben"].spouse_ids == ["ada"]

    books = memories_db.get_memory(mdb, FID, "books_1999")
    assert books.title == "The library books" and books.timeline is not None
    assert [t.author_id for t in books.tellings] == ["dev", "cleo"]
    photo_ids = books.tellings[1].photo_ids
    assert len(photo_ids) == 2 and all(media_db.get_media(mdb, FID, mid) is not None for mid in photo_ids)
    prop = props_db.get_prop(mdb, FID, "tester_books")
    art_id = prop.art["body"].removeprefix("media/")
    assert media_db.get_media(mdb, FID, art_id) is not None
    assert memories_db.get_memory(mdb, FID, "tamales_1975").timeline is not None

    # The Maos are untouched.
    assert families_db.get_family(mdb, DEMO_FAMILY_ID) is not None
    assert len(people_db.list_people(mdb, DEMO_FAMILY_ID)) == 8


def test_seeding_twice_is_a_no_op(mdb):
    data = load_family(FIXTURE_DIR)
    assert seed_family(mdb, data) is True
    assert seed_family(mdb, data) is False
    assert len(people_db.list_people(mdb, FID)) == 4


def test_an_incomplete_seed_is_redone(mdb):
    data = load_family(FIXTURE_DIR)
    seed_family(mdb, data)
    users_db.delete_users(mdb, emails=[CLEO], user_ids=["testers_user_cleo"])
    assert not family_is_complete(mdb, data)
    assert seed_family(mdb, data) is True
    assert family_is_complete(mdb, data)
