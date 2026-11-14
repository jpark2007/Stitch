"""update_person: a parents patch must not marry a descendant to their own ancestor.

A parents patch on A changes the ancestors of A and of everyone below A, so the
spouse-in-lineage rule has to look at the whole branch, not only at A's own spouses.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import auth
import schemas
from db import client as db_client
from db import people as P
from db import users as users_db
from routes import families as families_routes

FAM = "fam_lineage"


def add(db, name, parents=(), spouses=(), *, family=FAM):
    return P.create_person(
        db,
        family,
        schemas.NewPerson(name=name, parent_ids=list(parents), spouse_ids=list(spouses)),
        added_by="u_admin",
    )


def stored(db, person_id):
    return db[P.COLLECTION].find_one({"_id": person_id})


def reparent(db, person_id, parents):
    return P.update_person(db, FAM, person_id, schemas.PersonPatch(parent_ids=list(parents)))


# ------------------------------------------------------------------------------ rejections


def test_child_married_to_new_parent_of_their_parent_is_rejected(mdb):
    a = add(mdb, "A")
    z = add(mdb, "Z", parents=[a.id])
    w = add(mdb, "W", spouses=[z.id])
    assert stored(mdb, z.id)["spouseIds"] == [w.id]

    with pytest.raises(P.InvalidRelation, match="married to their own ancestor"):
        reparent(mdb, a.id, [w.id])
    assert stored(mdb, a.id)["parentIds"] == []


def test_one_generation_deeper_is_rejected(mdb):
    a = add(mdb, "A")
    z = add(mdb, "Z", parents=[a.id])
    y = add(mdb, "Y", parents=[z.id])
    w = add(mdb, "W", spouses=[y.id])

    with pytest.raises(P.InvalidRelation, match="married to their own ancestor"):
        reparent(mdb, a.id, [w.id])
    assert stored(mdb, a.id)["parentIds"] == []


def test_new_grandparent_married_to_descendant_is_rejected(mdb):
    """The bad spouse can sit above the new parent, not only be the new parent."""
    a = add(mdb, "A")
    z = add(mdb, "Z", parents=[a.id])
    g = add(mdb, "G", spouses=[z.id])
    w = add(mdb, "W", parents=[g.id])

    with pytest.raises(P.InvalidRelation, match="married to their own ancestor"):
        reparent(mdb, a.id, [w.id])
    assert stored(mdb, a.id)["parentIds"] == []


def test_one_sided_legacy_spouse_link_on_the_ancestor_is_rejected(mdb):
    a = add(mdb, "A")
    z = add(mdb, "Z", parents=[a.id])
    w = add(mdb, "W")
    # Legacy data: only W lists Z; Z has no back link.
    mdb[P.COLLECTION].update_one({"_id": w.id}, {"$set": {"spouseIds": [z.id]}})
    assert stored(mdb, z.id)["spouseIds"] == []

    with pytest.raises(P.InvalidRelation, match="married to their own ancestor"):
        reparent(mdb, a.id, [w.id])
    assert stored(mdb, a.id)["parentIds"] == []


def test_one_sided_legacy_spouse_link_on_the_descendant_is_rejected(mdb):
    a = add(mdb, "A")
    z = add(mdb, "Z", parents=[a.id])
    w = add(mdb, "W")
    mdb[P.COLLECTION].update_one({"_id": z.id}, {"$set": {"spouseIds": [w.id]}})

    with pytest.raises(P.InvalidRelation, match="married to their own ancestor"):
        reparent(mdb, a.id, [w.id])
    assert stored(mdb, a.id)["parentIds"] == []


def test_one_sided_legacy_link_to_the_patched_person_is_rejected(mdb):
    """W lists A as a spouse but A does not list W; making W A's parent is still wrong."""
    a = add(mdb, "A")
    w = add(mdb, "W")
    mdb[P.COLLECTION].update_one({"_id": w.id}, {"$set": {"spouseIds": [a.id]}})

    with pytest.raises(P.InvalidRelation, match="married to their own ancestor"):
        reparent(mdb, a.id, [w.id])
    assert stored(mdb, a.id)["parentIds"] == []


def test_one_sided_legacy_link_is_rejected_even_when_the_patch_clears_spouses(mdb):
    """A never listed W, so clearing A's spouses unlinks nothing: W still lists A."""
    a = add(mdb, "A")
    w = add(mdb, "W")
    mdb[P.COLLECTION].update_one({"_id": w.id}, {"$set": {"spouseIds": [a.id]}})

    with pytest.raises(P.InvalidRelation, match="married to their own ancestor"):
        P.update_person(mdb, FAM, a.id, schemas.PersonPatch(parent_ids=[w.id], spouse_ids=[]))
    assert stored(mdb, a.id)["parentIds"] == []
    assert stored(mdb, w.id)["spouseIds"] == [a.id]


def test_rejection_names_the_two_people(mdb):
    a = add(mdb, "Ada")
    z = add(mdb, "Zoe", parents=[a.id])
    add(mdb, "Walt", spouses=[z.id])
    walt = mdb[P.COLLECTION].find_one({"name": "Walt"})["_id"]

    with pytest.raises(P.InvalidRelation, match=r"\(Walt and Zoe\)"):
        reparent(mdb, a.id, [walt])


# ------------------------------------------------------------------------- still accepted


def test_unrelated_new_parent_is_accepted(mdb):
    a = add(mdb, "A")
    z = add(mdb, "Z", parents=[a.id])
    s = add(mdb, "S", spouses=[z.id])  # Z's spouse, not becoming anyone's ancestor
    w = add(mdb, "W")

    person = reparent(mdb, a.id, [w.id])
    assert person is not None and person.parent_ids == [w.id]
    assert stored(mdb, s.id)["spouseIds"] == [z.id]


def test_new_parents_who_are_married_to_each_other_are_accepted(mdb):
    a = add(mdb, "A")
    add(mdb, "Z", parents=[a.id])
    mom = add(mdb, "Mom")
    dad = add(mdb, "Dad", spouses=[mom.id])

    person = reparent(mdb, a.id, [mom.id, dad.id])
    assert person is not None and person.parent_ids == [mom.id, dad.id]


def test_adding_a_parent_married_to_the_existing_parent_is_accepted(mdb):
    a = add(mdb, "A")
    add(mdb, "Z", parents=[a.id])
    mom = add(mdb, "Mom")
    reparent(mdb, a.id, [mom.id])
    dad = add(mdb, "Dad", spouses=[mom.id])

    person = reparent(mdb, a.id, [mom.id, dad.id])
    assert person is not None and person.parent_ids == [mom.id, dad.id]


def test_keeping_the_same_parents_is_accepted(mdb):
    mom = add(mdb, "Mom")
    a = add(mdb, "A", parents=[mom.id])
    add(mdb, "Z", parents=[a.id])

    person = reparent(mdb, a.id, [mom.id])
    assert person is not None and person.parent_ids == [mom.id]


def test_spouse_corrected_to_parent_in_one_patch_is_accepted(mdb):
    """'I picked W as my spouse by mistake, W is my parent': the same patch unlinks W."""
    a = add(mdb, "A")
    w = add(mdb, "W", spouses=[a.id])
    assert stored(mdb, a.id)["spouseIds"] == [w.id]

    person = P.update_person(mdb, FAM, a.id, schemas.PersonPatch(parent_ids=[w.id], spouse_ids=[]))
    assert person is not None and person.parent_ids == [w.id] and person.spouse_ids == []
    assert stored(mdb, w.id)["spouseIds"] == []


def test_spouse_corrected_to_parent_with_a_child_below_is_accepted(mdb):
    a = add(mdb, "A")
    z = add(mdb, "Z", parents=[a.id])
    w = add(mdb, "W", spouses=[a.id])

    person = P.update_person(mdb, FAM, a.id, schemas.PersonPatch(parent_ids=[w.id], spouse_ids=[]))
    assert person is not None and person.parent_ids == [w.id]
    assert stored(mdb, w.id)["spouseIds"] == []
    assert stored(mdb, z.id)["parentIds"] == [a.id]


def test_spouse_corrected_to_parent_keeps_other_spouses_checked(mdb):
    """Dropping W unlinks W only; a descendant still married to W's side is rejected."""
    a = add(mdb, "A")
    z = add(mdb, "Z", parents=[a.id])
    w = add(mdb, "W", spouses=[a.id])
    mdb[P.COLLECTION].update_one({"_id": w.id}, {"$addToSet": {"spouseIds": z.id}})
    mdb[P.COLLECTION].update_one({"_id": z.id}, {"$addToSet": {"spouseIds": w.id}})

    with pytest.raises(P.InvalidRelation, match="married to their own ancestor"):
        P.update_person(mdb, FAM, a.id, schemas.PersonPatch(parent_ids=[w.id], spouse_ids=[]))
    assert stored(mdb, a.id)["parentIds"] == []


def test_spouse_only_patch_is_unchanged(mdb):
    a = add(mdb, "A")
    b = add(mdb, "B")
    person = P.update_person(mdb, FAM, a.id, schemas.PersonPatch(spouse_ids=[b.id]))
    assert person is not None and person.spouse_ids == [b.id]


def test_direct_case_still_says_parent_and_spouse(mdb):
    a = add(mdb, "A")
    w = add(mdb, "W", spouses=[a.id])
    with pytest.raises(P.InvalidRelation, match="a person cannot be both a parent and a spouse"):
        reparent(mdb, a.id, [w.id])
    assert stored(mdb, a.id)["parentIds"] == []


def test_wide_family_stays_fast_enough(mdb):
    """A few hundred people below A: one read, and the walk finishes promptly."""
    a = add(mdb, "A")
    docs = []
    frontier = [a.id]
    for gen in range(4):
        nxt = []
        for parent in frontier:
            for k in range(6):
                pid = f"p{gen}_{parent}_{k}"
                docs.append(
                    {"_id": pid, "familyId": FAM, "name": pid, "parentIds": [parent], "spouseIds": []}
                )
                nxt.append(pid)
        frontier = nxt[:40]
    mdb[P.COLLECTION].insert_many(docs)
    w = add(mdb, "W")

    person = reparent(mdb, a.id, [w.id])
    assert person is not None and person.parent_ids == [w.id]


# ---------------------------------------------------------------------------------- route


@pytest.fixture
def api(mdb):
    db_client.ensure_indexes(mdb)
    db_client.set_db(mdb)
    app = FastAPI()
    app.include_router(families_routes.router)
    user = users_db.create_user(
        mdb, email="ada@example.com", password_hash=auth.hash_password("pw-ada"), name="Ada"
    )
    tc = TestClient(app)
    tc.cookies.set(auth.COOKIE_NAME, auth.create_session_token(user.id))
    res = tc.post("/families", json={"name": "The Lines", "person": {"name": "Ada"}})
    assert res.status_code == 201, res.text
    yield tc, res.json()["family"]["id"]
    tc.close()
    db_client.set_db(None)


def _post_person(tc, fid, name, **fields):
    res = tc.post(f"/families/{fid}/people", json={"name": name, **fields})
    assert res.status_code == 201, res.text
    return res.json()["id"]


def test_route_rejects_with_the_same_status_as_other_invalid_relations(api, mdb):
    tc, fid = api
    a = _post_person(tc, fid, "A")
    z = _post_person(tc, fid, "Z", parentIds=[a])
    w = _post_person(tc, fid, "W", spouseIds=[z])

    # The direct case, already an InvalidRelation before this fix.
    v = _post_person(tc, fid, "V", spouseIds=[a])
    direct = tc.patch(f"/families/{fid}/people/{a}", json={"parentIds": [v]})
    assert direct.status_code == 400, direct.text

    res = tc.patch(f"/families/{fid}/people/{a}", json={"parentIds": [w]})
    assert res.status_code == direct.status_code, res.text
    assert "married to their own ancestor" in res.text
    assert stored(mdb, a)["parentIds"] == []


def test_route_accepts_spouse_corrected_to_parent_in_one_patch(api, mdb):
    """The join screen sends parentIds and spouseIds together in one PATCH."""
    tc, fid = api
    a = _post_person(tc, fid, "A")
    w = _post_person(tc, fid, "W", spouseIds=[a])

    res = tc.patch(f"/families/{fid}/people/{a}", json={"parentIds": [w], "spouseIds": []})
    assert res.status_code == 200, res.text
    assert res.json()["parentIds"] == [w]
    assert stored(mdb, w)["spouseIds"] == []
