"""Removing a member or deleting an account stops their Stitch Channels contacts.

families.remove_member stops the member's contacts in that family only (linked by userId or
created for their person); users.delete_users stops an account's contacts in every family.
The stopped shape must match db/contacts.py's own stop helpers, and the Channels lookups must
then refuse the contact. Runs on mongomock (the `mdb` fixture in conftest.py); no network.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from db import contacts as contacts_db
from db import families as fam
from db import users as users_db

T0 = datetime(2026, 11, 1, 12, tzinfo=UTC)
CONTACT_FIELDS = ("status", "stoppedAt", "enrollmentHash", "enrollmentExpiresAt")


def _contact(db, cid, fid, person_id, user_id, handle, status="active", **extra):
    doc = {"_id": cid, "familyId": fid, "personId": person_id, "userId": user_id,
           "channel": "imessage", "handle": handle, "status": status,
           "verifiedAt": T0 if status == "active" else None,
           "optInAt": T0 if status == "active" else None, **extra}
    if status == "pending":
        doc.update(enrollmentHash="h" * 64, enrollmentExpiresAt=datetime.now(UTC) + timedelta(minutes=10),
                   enrollmentAttempts=0, enrollmentManaged=True)
    db.contacts.insert_one(doc)
    return doc


@pytest.fixture
def world(mdb):
    """fam_a: admin u_admin (p_admin), member u2 (p_a), member u3 (p_u3).
    fam_b: admin u_b, member u2 (p_b). u2 has contacts in both families."""
    fam.create_family(mdb, name="A", creator_user_id="u_admin", family_id="fam_a")
    fam.add_member(mdb, "fam_a", "u2")
    fam.add_member(mdb, "fam_a", "u3")
    fam.create_family(mdb, name="B", creator_user_id="u_b", family_id="fam_b")
    fam.add_member(mdb, "fam_b", "u2")
    mdb.people.insert_many([
        {"_id": "p_admin", "familyId": "fam_a", "userId": "u_admin", "name": "Rosa"},
        {"_id": "p_a", "familyId": "fam_a", "userId": "u2", "name": "David"},
        {"_id": "p_u3", "familyId": "fam_a", "userId": "u3", "name": "Ana"},
        {"_id": "p_b", "familyId": "fam_b", "userId": "u2", "name": "David"},
        {"_id": "p_unlinked", "familyId": "fam_a", "userId": None, "name": "Grandpa"},
    ])
    _contact(mdb, "c_a_active", "fam_a", "p_a", "u2", "+15550000001")
    _contact(mdb, "c_a_pending", "fam_a", "p_a", "u2", "dave@example.com", status="pending")
    # Created by an admin for u2's person, with no account linked on the contact.
    _contact(mdb, "c_a_by_admin", "fam_a", "p_a", None, "+15550000002")
    _contact(mdb, "c_a_u3", "fam_a", "p_u3", "u3", "+15550000003")
    _contact(mdb, "c_a_unlinked", "fam_a", "p_unlinked", None, "+15550000004")
    _contact(mdb, "c_b_active", "fam_b", "p_b", "u2", "+15550000005")
    return mdb


def _snapshot(db, *ids):
    return {d["_id"]: d for d in db.contacts.find({"_id": {"$in": list(ids)}})}


def _all(db):
    return list(db.contacts.find().sort("_id", 1))


ENROLLMENT_FIELDS = ("enrollmentHash", "enrollmentExpiresAt", "enrollmentAttempts",
                     "enrollmentInitiator", "enrollmentPersonUserId")


def _assert_stopped(doc):
    assert doc["status"] == "stopped"
    assert isinstance(doc["stoppedAt"], datetime)
    assert doc["verifiedAt"] is None and doc["optInAt"] is None  # consent proof gone
    for field in ENROLLMENT_FIELDS:
        assert field not in doc, field


# ------------------------------------------------------------------------- remove_member


def test_removing_a_member_stops_their_contacts_in_that_family_only(world):
    keep = _snapshot(world, "c_a_u3", "c_a_unlinked", "c_b_active")

    assert fam.remove_member(world, "fam_a", "u2") is True

    after = {d["_id"]: d for d in world.contacts.find()}
    for cid in ("c_a_active", "c_a_pending", "c_a_by_admin"):
        _assert_stopped(after[cid])
    for cid, doc in keep.items():
        assert after[cid] == doc, cid  # other member, unlinked person, u2's other family


def test_a_removed_members_text_is_refused_by_the_channels_lookup(world):
    assert contacts_db.resolve_contact(world, "imessage", "+15550000001")["_id"] == "c_a_active"
    assert contacts_db.worker_contact(world, "fam_a", "c_a_active")["_id"] == "c_a_active"

    fam.remove_member(world, "fam_a", "u2")

    with pytest.raises(PermissionError):
        contacts_db.resolve_contact(world, "imessage", "+15550000001")
    with pytest.raises(PermissionError):
        contacts_db.worker_contact(world, "fam_a", "c_a_active")
    with pytest.raises(PermissionError):
        contacts_db.resolve_contact(world, "imessage", "+15550000002")  # admin-created
    # Their other family still works.
    assert contacts_db.resolve_contact(world, "imessage", "+15550000005")["familyId"] == "fam_b"


def test_a_pending_link_code_cannot_activate_after_removal(mdb):
    fam.create_family(mdb, name="A", creator_user_id="u_admin", family_id="fam_a")
    fam.add_member(mdb, "fam_a", "u2")
    users_db.create_user(mdb, email="u2@example.com", password_hash="x", name="u2", user_id="u2")
    mdb.people.insert_one({"_id": "p_a", "familyId": "fam_a", "userId": "u2", "name": "David"})
    row, code = contacts_db.create_pending_enrollment(
        mdb, "fam_a", "p_a", "u2", "imessage", "+15550000009", {})
    assert row["status"] == "pending"

    fam.remove_member(mdb, "fam_a", "u2")

    _assert_stopped(mdb.contacts.find_one({"_id": row["_id"]}))
    assert contacts_db.redeem_enrollment(mdb, "imessage", "+15550000009", code, "space-1",
                                         space_kind="direct") is False
    assert mdb.contacts.find_one({"_id": row["_id"]})["status"] == "stopped"


def test_last_admin_and_not_a_member_leave_contacts_untouched(world):
    _contact(world, "c_admin", "fam_a", "p_admin", "u_admin", "+15550000010")
    before = _all(world)

    with pytest.raises(fam.LastAdmin):
        fam.remove_member(world, "fam_a", "u_admin")
    assert fam.remove_member(world, "fam_b", "u3") is False  # u3 is not in fam_b
    assert fam.remove_member(world, "missing", "u2") is False

    assert _all(world) == before


def test_an_already_stopped_contact_keeps_its_stopped_at_but_loses_consent(world):
    earlier = datetime(2026, 1, 1, tzinfo=UTC)
    world.contacts.update_one({"_id": "c_a_active"}, {"$set": {"status": "stopped", "stoppedAt": earlier}})
    fam.remove_member(world, "fam_a", "u2")
    doc = world.contacts.find_one({"_id": "c_a_active"})
    assert doc["stoppedAt"] == earlier
    _assert_stopped(doc)


def test_a_rejoined_member_cannot_revive_the_old_contact(world):
    fam.remove_member(world, "fam_a", "u2")
    # Rejoins and reclaims their person, as the join route would.
    assert fam.add_member(world, "fam_a", "u2") is True
    world.people.update_one({"_id": "p_a"}, {"$set": {"userId": "u2"}})

    doc = world.contacts.find_one({"_id": "c_a_active"})
    _assert_stopped(doc)
    assert doc["userId"] == "u2"
    with pytest.raises(PermissionError):
        contacts_db.resolve_contact(world, "imessage", "+15550000001")
    with pytest.raises(PermissionError):
        contacts_db.worker_contact(world, "fam_a", "c_a_active")
    # Reviving it by flipping status alone is still refused: the consent proof is gone.
    world.contacts.update_one({"_id": "c_a_active"}, {"$set": {"status": "active"}})
    with pytest.raises(PermissionError):
        contacts_db.resolve_contact(world, "imessage", "+15550000001")


def test_failed_join_undo_goes_through_the_same_path(world):
    """routes/families._undo_join is the only other remove_member caller; it stops too."""
    from routes.families import _undo_join

    world.people.update_one({"_id": "p_u3"}, {"$set": {"userId": None}})
    _undo_join(world, "fam_a", "u3", was_member=False)
    assert fam.get_member_role(world, "fam_a", "u3") is None
    _assert_stopped(world.contacts.find_one({"_id": "c_a_u3"}))


def test_no_contacts_collection_behaves_as_before(mdb):
    fam.create_family(mdb, name="A", creator_user_id="u_admin", family_id="fam_a")
    fam.add_member(mdb, "fam_a", "u2")
    mdb.people.insert_one({"_id": "p_a", "familyId": "fam_a", "userId": "u2", "name": "D"})
    users_db.create_user(mdb, email="u2@example.com", password_hash="x", name="u2", user_id="u2")

    assert fam.remove_member(mdb, "fam_a", "u2") is True
    assert mdb.people.find_one({"_id": "p_a"})["userId"] is None
    assert users_db.delete_users(mdb, emails=[], user_ids=["u2"]) == 1
    assert "contacts" not in mdb.list_collection_names()


@pytest.mark.parametrize("family_id,user_id", [("", "u2"), ("  ", "u2"), ("fam_a", ""), ("fam_a", " ")])
def test_stop_member_contacts_refuses_blank_ids(world, family_id, user_id):
    before = _all(world)
    with pytest.raises(ValueError):
        fam.stop_member_contacts(world, family_id, user_id)
    assert _all(world) == before


# -------------------------------------------------------------------------- delete_users


def test_deleting_an_account_stops_its_contacts_in_every_family(world):
    users_db.create_user(world, email="dave@example.com", password_hash="x", name="u2", user_id="u2")
    keep = _snapshot(world, "c_a_u3", "c_a_unlinked", "c_a_by_admin")

    assert users_db.delete_users(world, emails=["DAVE@example.com"], user_ids=[]) == 1

    after = {d["_id"]: d for d in world.contacts.find()}
    for cid in ("c_a_active", "c_a_pending", "c_b_active"):
        _assert_stopped(after[cid])
    # Other accounts, and contacts with no userId (created for a person), are unaffected.
    for cid, doc in keep.items():
        assert after[cid] == doc, cid
    with pytest.raises(PermissionError):
        contacts_db.resolve_contact(world, "imessage", "+15550000005")


def test_delete_users_with_nothing_to_match_touches_no_contacts(world):
    before = _all(world)
    assert users_db.delete_users(world, emails=["bad"], user_ids=["", "  "]) == 0
    assert fam.stop_user_contacts(world, []) == 0
    assert fam.stop_user_contacts(world, ["", "  "]) == 0
    assert _all(world) == before


# ------------------------------------------------------------------------ shape contract


def _pair(db, status):
    """Two identical contacts in different families, to stop one each way."""
    for cid, fid in (("ours", "fam_x"), ("theirs", "fam_y")):
        _contact(db, cid, fid, "p", "u_shape", f"{cid}@example.com", status=status)


@pytest.mark.parametrize("status", ["active", "pending"])
def test_stopped_shape_matches_the_channels_stop_contacts(mdb, status):
    _pair(mdb, status)
    assert fam.stop_member_contacts(mdb, "fam_x", "u_shape") == 1
    theirs = mdb.contacts.find_one({"_id": "theirs"})
    contacts_db.stop_contacts(mdb, "imessage", "theirs@example.com", [theirs])
    _assert_same_stop(mdb)


@pytest.mark.parametrize("status", ["active", "pending"])
def test_stopped_shape_matches_the_channels_stop_settings_contact(mdb, status):
    fam.create_family(mdb, name="Y", creator_user_id="u_shape", family_id="fam_y")
    mdb.people.insert_one({"_id": "p", "familyId": "fam_y", "userId": "u_shape", "name": "S"})
    _pair(mdb, status)
    assert fam.stop_member_contacts(mdb, "fam_x", "u_shape") == 1
    contacts_db.stop_settings_contact(mdb, "fam_y", "p", "u_shape")
    _assert_same_stop(mdb)


def _assert_same_stop(db):
    """Ours does everything the Channels stop does, plus clearing the consent proof and the
    rest of the enrollment bookkeeping."""
    ours, theirs = db.contacts.find_one({"_id": "ours"}), db.contacts.find_one({"_id": "theirs"})
    assert theirs["status"] == "stopped"
    extra_cleared = set(ENROLLMENT_FIELDS)
    # Every field Channels keeps, we keep (unless we clear it on purpose), and vice versa.
    assert set(theirs) - extra_cleared == set(ours)
    ignore = {"_id", "familyId", "handle", "stoppedAt", "verifiedAt", "optInAt"}
    assert {k: v for k, v in ours.items() if k not in ignore} == \
        {k: v for k, v in theirs.items() if k not in ignore | extra_cleared}
    for field in CONTACT_FIELDS:  # whatever Channels removes, we remove
        if field not in theirs:
            assert field not in ours, field
    assert ours["verifiedAt"] is None and ours["optInAt"] is None
    assert isinstance(ours["stoppedAt"], datetime) and isinstance(theirs["stoppedAt"], datetime)
    assert abs(ours["stoppedAt"] - theirs["stoppedAt"]) < timedelta(seconds=5)
    _assert_stopped(ours)
