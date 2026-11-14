"""db/families.py: families, memberships, invites."""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta

from pathlib import Path

import pytest
from bson import ObjectId

import schemas
from db import families as fam
from db import media as media_db
from db import memories as memories_db
from db import people as people_db
from db import props as props_db
from db import traditions as traditions_db
from db import users as users_db

mongomock = pytest.importorskip("mongomock")

T0 = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)


@pytest.fixture
def mdb():
    return mongomock.MongoClient(tz_aware=True)["stitch_test"]


@pytest.fixture
def real_db():
    uri = os.environ.get("MONGODB_URI_TEST")
    if not uri:
        pytest.skip("MONGODB_URI_TEST not set")
    from pymongo import MongoClient

    client = MongoClient(uri, tz_aware=True, serverSelectionTimeoutMS=5000)
    name = f"stitch_test_{uuid.uuid4().hex[:12]}"
    db = client[name]
    db.families.create_index("members.userId")
    db.families.create_index("invites.code", unique=True, sparse=True)
    try:
        yield db
    finally:
        client.drop_database(name)
        client.close()


def _family_with(mdb, *members: tuple[str, str], fid: str = "fam_a"):
    """A family whose first member is the admin creator, plus extra (user, role) members."""
    fam.create_family(mdb, name="The Maos", creator_user_id="u_admin", family_id=fid, now=T0)
    for i, (uid, role) in enumerate(members, start=1):
        assert fam.add_member(mdb, fid, uid, role, now=T0 + timedelta(minutes=i))
    return fid


# ----------------------------------------------------------------------------- families

def test_create_and_get(mdb):
    created = fam.create_family(mdb, name="  The Maos ", creator_user_id="u1", now=T0)
    assert isinstance(created, schemas.Family)
    assert created.name == "The Maos"
    assert created.id and isinstance(created.id, str)
    assert [(m.user_id, m.role) for m in created.members] == [("u1", "admin")]
    assert created.members[0].joined_at == T0
    assert created.invites == []

    got = fam.get_family(mdb, created.id)
    assert got == created
    assert mdb.families.find_one({"_id": created.id})["members"][0]["userId"] == "u1"
    assert fam.get_member_role(mdb, created.id, "u1") == "admin"


def test_create_with_explicit_id_and_default_now(mdb):
    before = datetime.now(UTC)
    f = fam.create_family(mdb, name="Rivera", creator_user_id="u1", family_id="fam_x")
    assert f.id == "fam_x"
    assert f.members[0].joined_at >= before - timedelta(seconds=1)


@pytest.mark.parametrize("name", ["", "   ", "\n"])
def test_create_blank_name_raises(mdb, name):
    with pytest.raises(ValueError):
        fam.create_family(mdb, name=name, creator_user_id="u1")
    assert mdb.families.count_documents({}) == 0


def test_get_unknown_family(mdb):
    assert fam.get_family(mdb, "nope") is None


# -------------------------------------------------------------------------- memberships

def test_get_member_role(mdb):
    fid = _family_with(mdb, ("u_m", "member"))
    assert fam.get_member_role(mdb, fid, "u_admin") == "admin"
    assert fam.get_member_role(mdb, fid, "u_m") == "member"
    assert fam.get_member_role(mdb, fid, "u_stranger") is None
    assert fam.get_member_role(mdb, "unknown_family", "u_admin") is None


def test_get_member_role_other_family(mdb):
    _family_with(mdb, fid="fam_a")
    fam.create_family(mdb, name="B", creator_user_id="u_b", family_id="fam_b")
    assert fam.get_member_role(mdb, "fam_b", "u_admin") is None
    assert fam.get_member_role(mdb, "fam_a", "u_b") is None


def test_list_family_summaries(mdb):
    fam.create_family(mdb, name="Late", creator_user_id="other", family_id="fam_late", now=T0)
    fam.create_family(mdb, name="Early", creator_user_id="u1", family_id="fam_early", now=T0)
    fam.create_family(mdb, name="Not mine", creator_user_id="other", family_id="fam_no", now=T0)
    # u1 joins fam_late after creating fam_early, so fam_early comes first.
    fam.add_member(mdb, "fam_late", "u1", now=T0 + timedelta(days=1))
    mdb.people.insert_many([
        {"_id": "p_early", "familyId": "fam_early", "userId": "u1", "name": "David"},
        {"_id": "p_late_other", "familyId": "fam_late", "userId": "other", "name": "Ana"},
        {"_id": "p_no", "familyId": "fam_no", "userId": "u1", "name": "Stray"},
    ])

    out = fam.list_family_summaries(mdb, "u1")
    assert out == [
        schemas.FamilySummary(id="fam_early", name="Early", role="admin", person_id="p_early"),
        schemas.FamilySummary(id="fam_late", name="Late", role="member", person_id=None),
    ]
    assert fam.list_family_summaries(mdb, "nobody") == []


def test_add_member_idempotent(mdb):
    fid = _family_with(mdb)
    assert fam.add_member(mdb, fid, "u2", now=T0 + timedelta(hours=1))
    assert fam.add_member(mdb, fid, "u2", now=T0 + timedelta(hours=2))
    doc = mdb.families.find_one({"_id": fid})
    rows = [m for m in doc["members"] if m["userId"] == "u2"]
    assert len(rows) == 1
    assert rows[0]["role"] == "member"
    assert rows[0]["joinedAt"] == T0 + timedelta(hours=1)


def test_add_member_never_downgrades(mdb):
    fid = _family_with(mdb)
    assert fam.add_member(mdb, fid, "u_admin", "member")
    assert fam.get_member_role(mdb, fid, "u_admin") == "admin"
    assert len(mdb.families.find_one({"_id": fid})["members"]) == 1


def test_add_member_admin_promotes_existing_member(mdb):
    fid = _family_with(mdb, ("u2", "member"))
    assert fam.add_member(mdb, fid, "u2", "admin")
    assert fam.get_member_role(mdb, fid, "u2") == "admin"


def test_add_member_unknown_family_and_bad_role(mdb):
    assert fam.add_member(mdb, "missing", "u1") is False
    assert fam.add_member(mdb, "missing", "u1", "admin") is False
    assert mdb.families.count_documents({}) == 0
    fid = _family_with(mdb)
    with pytest.raises(ValueError):
        fam.add_member(mdb, fid, "u2", "owner")  # type: ignore[arg-type]


def test_set_member_role(mdb):
    fid = _family_with(mdb, ("u2", "member"))
    assert fam.set_member_role(mdb, fid, "u2", "admin")
    assert fam.get_member_role(mdb, fid, "u2") == "admin"
    # Two admins now, so either can step down.
    assert fam.set_member_role(mdb, fid, "u_admin", "member")
    assert fam.get_member_role(mdb, fid, "u_admin") == "member"
    assert fam.set_member_role(mdb, fid, "u_admin", "member")  # already member: no-op
    with pytest.raises(fam.LastAdmin):
        fam.set_member_role(mdb, fid, "u2", "member")
    assert fam.get_member_role(mdb, fid, "u2") == "admin"


def test_set_member_role_preserves_other_fields(mdb):
    fid = _family_with(mdb, ("u2", "admin"))
    fam.set_member_role(mdb, fid, "u2", "member")
    members = {m["userId"]: m for m in mdb.families.find_one({"_id": fid})["members"]}
    assert members["u2"]["joinedAt"] == T0 + timedelta(minutes=1)
    assert members["u_admin"]["role"] == "admin"


def test_set_member_role_not_member_or_other_family(mdb):
    fid = _family_with(mdb)
    fam.create_family(mdb, name="B", creator_user_id="u_b", family_id="fam_b")
    assert fam.set_member_role(mdb, fid, "u_b", "admin") is False
    assert fam.set_member_role(mdb, fid, "u_b", "member") is False
    assert fam.set_member_role(mdb, "missing", "u_admin", "member") is False
    with pytest.raises(ValueError):
        fam.set_member_role(mdb, fid, "u_admin", "root")  # type: ignore[arg-type]


def test_remove_member_unclaims_person_in_that_family_only(mdb):
    fid = _family_with(mdb, ("u2", "member"))
    fam.create_family(mdb, name="B", creator_user_id="u_b", family_id="fam_b")
    fam.add_member(mdb, "fam_b", "u2")
    mdb.people.insert_many([
        {"_id": "p_a", "familyId": fid, "userId": "u2", "name": "David"},
        {"_id": "p_b", "familyId": "fam_b", "userId": "u2", "name": "David"},
        {"_id": "p_admin", "familyId": fid, "userId": "u_admin", "name": "Rosa"},
    ])

    assert fam.remove_member(mdb, fid, "u2") is True
    assert fam.get_member_role(mdb, fid, "u2") is None
    assert fam.get_member_role(mdb, "fam_b", "u2") == "member"
    people = {p["_id"]: p for p in mdb.people.find()}
    assert people["p_a"]["userId"] is None
    assert people["p_b"]["userId"] == "u2"
    assert people["p_admin"]["userId"] == "u_admin"
    assert [s.id for s in fam.list_family_summaries(mdb, "u2")] == ["fam_b"]

    assert fam.remove_member(mdb, fid, "u2") is False  # no longer a member
    assert fam.remove_member(mdb, "missing", "u2") is False


def test_remove_member_last_admin(mdb):
    fid = _family_with(mdb, ("u2", "member"))
    mdb.people.insert_one({"_id": "p_admin", "familyId": fid, "userId": "u_admin", "name": "R"})
    with pytest.raises(fam.LastAdmin):
        fam.remove_member(mdb, fid, "u_admin")
    assert fam.get_member_role(mdb, fid, "u_admin") == "admin"
    assert mdb.people.find_one({"_id": "p_admin"})["userId"] == "u_admin"

    # With a second admin, the first may leave.
    fam.set_member_role(mdb, fid, "u2", "admin")
    assert fam.remove_member(mdb, fid, "u_admin") is True
    with pytest.raises(fam.LastAdmin):
        fam.remove_member(mdb, fid, "u2")


def test_remove_member_wrong_family_does_not_touch_people(mdb):
    _family_with(mdb, ("u2", "member"), fid="fam_a")
    fam.create_family(mdb, name="B", creator_user_id="u_b", family_id="fam_b")
    mdb.people.insert_one({"_id": "p_a", "familyId": "fam_a", "userId": "u2", "name": "D"})
    assert fam.remove_member(mdb, "fam_b", "u2") is False
    assert mdb.people.find_one({"_id": "p_a"})["userId"] == "u2"



# ------------------------------------------------------------------------------ invites

def test_invite_expiry_day_13_vs_15(mdb):
    fid = _family_with(mdb)
    inv = fam.create_invite(mdb, fid, "u_admin", now=T0)
    assert isinstance(inv, schemas.Invite)
    assert inv.expires_at == T0 + timedelta(days=fam.INVITE_DAYS)
    assert inv.created_by == "u_admin" and inv.revoked is False

    found = fam.find_active_invite(mdb, inv.code, now=T0 + timedelta(days=13))
    assert found is not None
    family, invite = found
    assert family.id == fid and family.name == "The Maos"
    assert invite == inv
    assert fam.find_active_invite(mdb, inv.code, now=T0 + timedelta(days=15)) is None
    # The boundary itself counts as expired (expiresAt <= now).
    assert fam.find_active_invite(mdb, inv.code, now=T0 + timedelta(days=14)) is None


def test_invite_custom_days(mdb):
    fid = _family_with(mdb)
    inv = fam.create_invite(mdb, fid, "u_admin", now=T0, days=1)
    assert inv.expires_at == T0 + timedelta(days=1)
    with pytest.raises(ValueError):
        fam.create_invite(mdb, fid, "u_admin", now=T0, days=0)


def test_revoke_invite(mdb):
    fid = _family_with(mdb)
    fam.create_family(mdb, name="B", creator_user_id="u_b", family_id="fam_b")
    keep = fam.create_invite(mdb, fid, "u_admin", now=T0)
    inv = fam.create_invite(mdb, fid, "u_admin", now=T0)

    assert fam.revoke_invite(mdb, "fam_b", inv.code) is False  # wrong family
    assert fam.find_active_invite(mdb, inv.code, now=T0) is not None

    assert fam.revoke_invite(mdb, fid, inv.code) is True
    assert fam.find_active_invite(mdb, inv.code, now=T0) is None
    assert fam.find_active_invite(mdb, keep.code, now=T0) is not None  # only that code
    assert fam.revoke_invite(mdb, fid, "no-such-code") is False
    assert fam.revoke_invite(mdb, "missing", inv.code) is False


def test_find_active_invite_unknown(mdb):
    _family_with(mdb)
    assert fam.find_active_invite(mdb, "unknown", now=T0) is None
    assert fam.find_active_invite(mdb, "", now=T0) is None


def test_find_invite_states(mdb):
    fid = _family_with(mdb)
    inv = fam.create_invite(mdb, fid, "u_admin", now=T0)
    expires = T0 + timedelta(days=fam.INVITE_DAYS)

    found = fam.find_invite(mdb, inv.code, now=expires - timedelta(seconds=1))
    assert found is not None
    family, invite, state = found
    assert (family.id, family.name, invite, state) == (fid, "The Maos", inv, "active")
    # The exact boundary is expired, the same rule as find_active_invite (expiresAt <= now).
    assert fam.find_invite(mdb, inv.code, now=expires)[2] == "expired"
    assert fam.find_active_invite(mdb, inv.code, now=expires) is None
    assert fam.find_invite(mdb, inv.code, now=expires + timedelta(days=1))[2] == "expired"

    assert fam.revoke_invite(mdb, fid, inv.code)
    revoked = fam.find_invite(mdb, inv.code, now=T0)
    assert revoked[2] == "revoked" and revoked[1].revoked is True
    # Revoked and expired: the admin's revocation is the answer.
    assert fam.find_invite(mdb, inv.code, now=expires + timedelta(days=1))[2] == "revoked"


def test_find_invite_unknown_or_bad_code_is_none(mdb):
    fid = _family_with(mdb)
    fam.create_invite(mdb, fid, "u_admin", now=T0)
    for code in ("unknown", "", None, 123, ["x"]):
        assert fam.find_invite(mdb, code, now=T0) is None  # type: ignore[arg-type]
        assert fam.find_active_invite(mdb, code, now=T0) is None  # type: ignore[arg-type]


def test_find_invite_picks_the_right_invite_of_a_family(mdb):
    fid = _family_with(mdb)
    old = fam.create_invite(mdb, fid, "u_admin", now=T0)
    fresh = fam.create_invite(mdb, fid, "u_admin", now=T0 + timedelta(days=10))
    gone = fam.create_invite(mdb, fid, "u_admin", now=T0 + timedelta(days=10))
    fam.revoke_invite(mdb, fid, gone.code)
    now = T0 + timedelta(days=15)
    assert {c: fam.find_invite(mdb, c, now=now)[2] for c in (old.code, fresh.code, gone.code)} == {
        old.code: "expired",
        fresh.code: "active",
        gone.code: "revoked",
    }
    family, invite = fam.find_active_invite(mdb, fresh.code, now=now)
    assert family.id == fid and invite == fresh


def test_find_invite_without_a_revoked_field_is_active(mdb):
    """A hand-written or older invite document with no `revoked` key is not revoked."""
    fid = _family_with(mdb)
    mdb.families.update_one(
        {"_id": fid},
        {"$push": {"invites": {"code": "legacy-code-0123", "createdBy": "u_admin", "expiresAt": T0 + timedelta(days=1)}}},
    )
    assert fam.find_invite(mdb, "legacy-code-0123", now=T0)[2] == "active"
    assert fam.find_invite(mdb, "legacy-code-0123", now=T0 + timedelta(days=1))[2] == "expired"


def test_find_invite_naive_expires_at_and_naive_now():
    db = mongomock.MongoClient()["stitch_naive"]  # not tz_aware: returns naive datetimes
    fam.create_family(db, name="N", creator_user_id="u1", family_id="fam_n", now=T0)
    inv = fam.create_invite(db, "fam_n", "u1", now=T0)
    stored = db.families.find_one({"_id": "fam_n"})["invites"][0]["expiresAt"]
    assert stored.tzinfo is None  # the naive read is real here
    expires = T0 + timedelta(days=fam.INVITE_DAYS)
    assert fam.find_invite(db, inv.code, now=expires - timedelta(seconds=1))[2] == "active"
    assert fam.find_invite(db, inv.code, now=expires)[2] == "expired"
    assert fam.find_invite(db, inv.code, now=expires.replace(tzinfo=None))[2] == "expired"  # naive now
    _, invite, _ = fam.find_invite(db, inv.code, now=T0)
    assert invite.expires_at.tzinfo is not None
    fam.revoke_invite(db, "fam_n", inv.code)
    assert fam.find_invite(db, inv.code, now=expires)[2] == "revoked"


def test_find_invite_default_now_is_the_real_clock(mdb):
    fid = _family_with(mdb)
    fresh = fam.create_invite(mdb, fid, "u_admin")
    stale = fam.create_invite(mdb, fid, "u_admin", now=datetime.now(UTC) - timedelta(days=fam.INVITE_DAYS + 1))
    assert fam.find_invite(mdb, fresh.code)[2] == "active"
    assert fam.find_invite(mdb, stale.code)[2] == "expired"


def test_list_invites(mdb):
    fid = _family_with(mdb)
    fresh = fam.create_invite(mdb, fid, "u_admin", now=T0 + timedelta(days=10))
    expired = fam.create_invite(mdb, fid, "u_admin", now=T0)
    revoked = fam.create_invite(mdb, fid, "u_admin", now=T0 + timedelta(days=10))
    fam.revoke_invite(mdb, fid, revoked.code)
    now = T0 + timedelta(days=15)

    assert [i.code for i in fam.list_invites(mdb, fid, now=now)] == [fresh.code]
    all_codes = [i.code for i in fam.list_invites(mdb, fid, active_only=False, now=now)]
    assert all_codes == [fresh.code, expired.code, revoked.code]
    assert fam.list_invites(mdb, "missing", now=now) == []
    assert fam.get_family(mdb, fid).invites[2].revoked is True


def test_create_invite_unknown_family(mdb):
    assert fam.create_invite(mdb, "missing", "u1", now=T0) is None


def test_invite_codes_random_long_unique(mdb):
    fid = _family_with(mdb)
    codes = [fam.create_invite(mdb, fid, "u_admin", now=T0).code for _ in range(200)]
    assert len(set(codes)) == 200
    for code in codes:
        assert len(code) >= 16
        assert all(c.isalnum() or c in "-_" for c in code)


def test_invite_code_collision_retries(mdb, monkeypatch):
    fid = _family_with(mdb)
    first = fam.create_invite(mdb, fid, "u_admin", now=T0)
    draws = iter([first.code, first.code, "fresh-code-0123456"])
    monkeypatch.setattr(fam.secrets, "token_urlsafe", lambda n: next(draws))
    second = fam.create_invite(mdb, fid, "u_admin", now=T0)
    assert second.code == "fresh-code-0123456"
    assert len(fam.list_invites(mdb, fid, active_only=False)) == 2


def test_naive_datetimes_are_treated_as_utc():
    db = mongomock.MongoClient()["stitch_naive"]  # not tz_aware: returns naive datetimes
    fam.create_family(db, name="N", creator_user_id="u1", family_id="fam_n", now=T0)
    inv = fam.create_invite(db, "fam_n", "u1", now=T0)
    assert fam.find_active_invite(db, inv.code, now=T0 + timedelta(days=13)) is not None
    assert fam.find_active_invite(db, inv.code, now=T0 + timedelta(days=15)) is None
    assert fam.list_family_summaries(db, "u1")[0].id == "fam_n"


# ---------------------------------------------------------------------------- real Mongo

def test_real_db_find_invite_states(real_db):
    """Skips without MONGODB_URI_TEST: the three states and the boundary on a real server."""
    f = fam.create_family(real_db, name="Real", creator_user_id="u_admin", now=T0)
    inv = fam.create_invite(real_db, f.id, "u_admin", now=T0)
    expires = T0 + timedelta(days=fam.INVITE_DAYS)
    assert fam.find_invite(real_db, inv.code, now=expires - timedelta(seconds=1))[2] == "active"
    assert fam.find_invite(real_db, inv.code, now=expires)[2] == "expired"
    assert fam.revoke_invite(real_db, f.id, inv.code)
    assert fam.find_invite(real_db, inv.code, now=expires)[2] == "revoked"
    assert fam.find_invite(real_db, "no-such-code", now=T0) is None


def test_real_db_invite_flow(real_db):
    f = fam.create_family(real_db, name="Real", creator_user_id="u_admin", now=T0)
    inv = fam.create_invite(real_db, f.id, "u_admin", now=T0)
    family, invite = fam.find_active_invite(real_db, inv.code, now=T0 + timedelta(days=13))
    assert family.id == f.id and invite.code == inv.code
    assert fam.find_active_invite(real_db, inv.code, now=T0 + timedelta(days=15)) is None

    assert fam.add_member(real_db, f.id, "u2", now=T0 + timedelta(days=1))
    assert fam.add_member(real_db, f.id, "u2")
    assert fam.get_member_role(real_db, f.id, "u2") == "member"
    assert [s.id for s in fam.list_family_summaries(real_db, "u2")] == [f.id]

    assert fam.revoke_invite(real_db, "other_family", inv.code) is False
    assert fam.revoke_invite(real_db, f.id, inv.code) is True
    assert fam.find_active_invite(real_db, inv.code, now=T0) is None

    with pytest.raises(fam.LastAdmin):
        fam.set_member_role(real_db, f.id, "u_admin", "member")
    assert fam.set_member_role(real_db, f.id, "u2", "admin")
    assert fam.set_member_role(real_db, f.id, "u_admin", "member")
    assert fam.remove_member(real_db, f.id, "u_admin")
    with pytest.raises(fam.LastAdmin):
        fam.remove_member(real_db, f.id, "u2")


# ------------------------------------------------------------------------ purge, delete users

SAMPLE = schemas.MemoryResponse.model_validate_json(
    (Path(__file__).resolve().parents[3] / "web/public/samples/hymn_books.memory.json").read_text()
)
PURGED = ("people", "memories", "questions", "props", "sets", "traditions")


def _fill_family(db, fid: str, admin: str) -> list[str]:
    """A family with one of everything it can own. Returns its media ids."""
    fam.create_family(db, name=f"Family {fid}", creator_user_id=admin, family_id=fid, now=T0)
    person = people_db.create_person(db, fid, schemas.NewPerson(name="Grandma"), added_by=admin)
    memory = memories_db.create_memory(db, fid, title="Hymn books", year=1988, participant_ids=[person.id])
    memories_db.create_question(db, fid, person_id=person.id, text="What did you sing?", memory_id=memory.id)
    props_db.insert_prop(db, fid, SAMPLE.props[0], prop_id=f"{fid}_prop")
    props_db.insert_set(db, fid, SAMPLE.sets[0], set_id=f"{fid}_set")
    traditions_db.create_tradition(
        db, fid,
        schemas.CreateTraditionRequest(title="Hymns on Sunday", category="music",
                                       when=schemas.TraditionWhen(kind="weekly"), participant_ids=[person.id]),
        created_by=admin,
    )
    return [
        media_db.put_media(db, fid, b"photo " + fid.encode(), content_type="image/png"),
        media_db.put_media(db, fid, b"audio " + fid.encode(), content_type="audio/mpeg", kind="audio"),
    ]


def _family_docs(db, fid: str) -> dict[str, list]:
    return {
        "families": list(db.families.find({"_id": fid})),
        **{c: list(db[c].find({"familyId": fid}).sort("_id", 1)) for c in PURGED},
        "media": list(db[media_db.INLINE].find({"metadata.familyId": fid}).sort("_id", 1)),
        "files": list(db[media_db.FILES].find({"metadata.familyId": fid}).sort("_id", 1)),
    }


def _gridfs_file(db, fid: str) -> ObjectId:
    """A file as a real GridFS bucket stores it (mongomock uses the inline store instead)."""
    oid = ObjectId()
    db[media_db.FILES].insert_one({"_id": oid, "length": 3, "chunkSize": 261120, "filename": "x.png",
                                   "metadata": {"familyId": fid, "kind": "photo", "contentType": "image/png"}})
    db[media_db.CHUNKS].insert_one({"_id": ObjectId(), "files_id": oid, "n": 0, "data": b"abc"})
    return oid


def test_purge_family_removes_only_that_family(mdb):
    a_media = _fill_family(mdb, "fam_a", "u_a")
    b_media = _fill_family(mdb, "fam_b", "u_b")
    a_file, b_file = _gridfs_file(mdb, "fam_a"), _gridfs_file(mdb, "fam_b")
    assert all(_family_docs(mdb, "fam_a").values())
    b_before = _family_docs(mdb, "fam_b")
    b_chunks = list(mdb[media_db.CHUNKS].find({"files_id": b_file}))
    users_before = list(mdb.users.find())

    assert fam.purge_family(mdb, "fam_a") is None

    assert _family_docs(mdb, "fam_a") == {k: [] for k in b_before}
    assert mdb[media_db.CHUNKS].count_documents({"files_id": a_file}) == 0
    assert all(media_db.get_media(mdb, "fam_a", mid) is None for mid in a_media)
    assert fam.get_family(mdb, "fam_a") is None

    assert _family_docs(mdb, "fam_b") == b_before
    assert list(mdb[media_db.CHUNKS].find({"files_id": b_file})) == b_chunks
    assert [media_db.get_media(mdb, "fam_b", mid).data for mid in b_media] == [b"photo fam_b", b"audio fam_b"]
    assert list(mdb.users.find()) == users_before  # accounts are not family data

    fam.purge_family(mdb, "fam_a")  # purging again is a no-op
    assert _family_docs(mdb, "fam_b") == b_before


@pytest.mark.parametrize("blank", ["", "   ", None, 0])
def test_purge_family_refuses_a_blank_id(mdb, blank):
    _fill_family(mdb, "fam_a", "u_a")
    before = _family_docs(mdb, "fam_a")
    with pytest.raises(ValueError):
        fam.purge_family(mdb, blank)
    with pytest.raises(ValueError):
        media_db.delete_family_media(mdb, blank)
    assert _family_docs(mdb, "fam_a") == before


def test_delete_family_media_counts_both_stores(mdb):
    _fill_family(mdb, "fam_a", "u_a")
    _gridfs_file(mdb, "fam_a")
    keep = media_db.put_media(mdb, "fam_b", b"keep", content_type="image/png")
    assert media_db.delete_family_media(mdb, "fam_a") == 3
    assert media_db.delete_family_media(mdb, "fam_a") == 0
    assert mdb[media_db.CHUNKS].count_documents({}) == 0
    assert media_db.get_media(mdb, "fam_b", keep).data == b"keep"


def _users(db) -> None:
    for uid, email in (("u1", "david@demo.stitch"), ("u2", "ana@demo.stitch"), ("u3", "olivia@example.com")):
        users_db.create_user(db, email=email, password_hash="x", name=uid, user_id=uid)


def _user_ids(db) -> list[str]:
    return sorted(d["_id"] for d in db.users.find({}, {"_id": 1}))


def test_delete_users_with_empty_lists_deletes_nothing(mdb):
    _users(mdb)
    assert users_db.delete_users(mdb, emails=[], user_ids=[]) == 0
    assert users_db.delete_users(mdb, emails=["not-an-email", "  "], user_ids=["", "   "]) == 0
    assert _user_ids(mdb) == ["u1", "u2", "u3"]


def test_delete_users_by_normalized_email_or_id(mdb):
    _users(mdb)
    assert users_db.delete_users(mdb, emails=["  DAVID@Demo.Stitch "], user_ids=[]) == 1
    assert _user_ids(mdb) == ["u2", "u3"]
    assert users_db.delete_users(mdb, emails=[], user_ids=["u2", "nobody"]) == 1
    assert _user_ids(mdb) == ["u3"]


def test_delete_users_matches_email_or_id_once(mdb):
    _users(mdb)
    # u1 by email and by id, u2 by id only; a bad email in the list is ignored.
    deleted = users_db.delete_users(mdb, emails=["david@demo.stitch", "bad"], user_ids=["u1", "u2"])
    assert deleted == 2
    assert _user_ids(mdb) == ["u3"]


def test_real_db_purge_family_and_media(real_db):
    a_media = _fill_family(real_db, "fam_a", "u_a")
    b_media = _fill_family(real_db, "fam_b", "u_b")
    assert real_db[media_db.FILES].count_documents({"metadata.familyId": "fam_a"}) == 2
    fam.purge_family(real_db, "fam_a")
    assert {k: v for k, v in _family_docs(real_db, "fam_a").items() if v} == {}
    assert real_db[media_db.CHUNKS].count_documents({}) == 2  # fam_b's two one-chunk files
    assert all(media_db.get_media(real_db, "fam_a", mid) is None for mid in a_media)
    assert [media_db.get_media(real_db, "fam_b", mid).data for mid in b_media] == [b"photo fam_b", b"audio fam_b"]
