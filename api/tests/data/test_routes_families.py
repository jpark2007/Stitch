"""routes/families.py mounted alone on a FastAPI app over mongomock.

Covers the weekend path (create, invite, preview, claim, add yourself), the join edge
cases, people and member routes, and that another family's ids are always a 404.
Every response passes through a hook that fails the test if it mentions passwordHash.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

import auth
import schemas
from db import client as db_client
from db import families as families_db
from db import people as people_db
from db import users as users_db
from routes import families as families_routes

# ------------------------------------------------------------------------------ fixtures


def _no_password_hash(response: httpx.Response) -> None:
    response.read()
    assert "passwordHash" not in response.text
    assert "password_hash" not in response.text


@pytest.fixture
def app(mdb):
    db_client.ensure_indexes(mdb)
    db_client.set_db(mdb)
    application = FastAPI()
    application.include_router(families_routes.router)
    yield application
    db_client.set_db(None)


@pytest.fixture
def users(mdb):
    """Four accounts. Ada, Ben and Cy share a family in the tests; Zoe has her own."""
    made = {}
    for key, name in (("a", "Ada"), ("b", "Ben"), ("c", "Cy"), ("z", "Zoe")):
        made[key] = users_db.create_user(
            mdb, email=f"{key}@example.com", password_hash=auth.hash_password("pw-" + key), name=name
        )
    return made


@pytest.fixture
def client(app):
    """client(user) -> a TestClient signed in as that user; client(None) -> signed out."""
    made: list[TestClient] = []

    def make(user: schemas.User | None) -> TestClient:
        tc = TestClient(app)
        tc.event_hooks = {"request": [], "response": [_no_password_hash]}
        if user is not None:
            tc.cookies.set(auth.COOKIE_NAME, auth.create_session_token(user.id))
        made.append(tc)
        return tc

    yield make
    for tc in made:
        tc.close()


def _create_family(tc: TestClient, name: str, person_name: str, **person) -> dict:
    res = tc.post("/families", json={"name": name, "person": {"name": person_name, **person}})
    assert res.status_code == 201, res.text
    return res.json()


def _invite(tc: TestClient, fid: str) -> str:
    res = tc.post(f"/families/{fid}/invites")
    assert res.status_code == 201, res.text
    return res.json()["code"]


def _add_person(tc: TestClient, fid: str, name: str, **fields) -> dict:
    res = tc.post(f"/families/{fid}/people", json={"name": name, **fields})
    assert res.status_code == 201, res.text
    return res.json()


@pytest.fixture
def family_a(users, client):
    """Ada's family: Ada (admin, linked) plus Rosa and Luis, unclaimed, married."""
    ada = client(users["a"])
    created = _create_family(ada, "The Maos", "Ada", born=1990)
    fid = created["family"]["id"]
    rosa = _add_person(ada, fid, "Rosa", born=1940)
    luis = _add_person(ada, fid, "Luis", born=1938, spouseIds=[rosa["id"]])
    return {"fid": fid, "ada": created["person"], "rosa": rosa, "luis": luis, "client": ada}


@pytest.fixture
def family_b(users, client):
    """Zoe's family, which nobody else belongs to."""
    zoe = client(users["z"])
    created = _create_family(zoe, "The Zhangs", "Zoe")
    fid = created["family"]["id"]
    other = _add_person(zoe, fid, "Wen", born=1950)
    code = _invite(zoe, fid)
    return {"fid": fid, "zoe": created["person"], "wen": other, "code": code, "client": zoe}


# -------------------------------------------------------------------------- router shape


def test_router_exports_exactly_the_family_routes():
    routes = {
        (method, route.path): (route.status_code, route.response_model)
        for route in families_routes.router.routes
        if isinstance(route, APIRoute)
        for method in route.methods
    }
    assert routes == {
        ("POST", "/families"): (201, schemas.CreateFamilyResponse),
        ("GET", "/families/{fid}"): (None, schemas.Family),
        ("POST", "/families/{fid}/invites"): (201, schemas.InviteResponse),
        ("DELETE", "/families/{fid}/invites/{code}"): (204, None),
        ("GET", "/invites/{code}"): (None, schemas.InvitePreview),
        ("POST", "/invites/{code}/join"): (None, schemas.JoinResponse),
        ("POST", "/families/{fid}/people"): (201, schemas.Person),
        ("PATCH", "/families/{fid}/people/{person_id}"): (None, schemas.Person),
        ("DELETE", "/families/{fid}/members/{user_id}"): (204, None),
    }
    assert families_routes.router.tags == ["families"]


# ---------------------------------------------------------------------- the weekend path


def test_weekend_path(users, client, mdb):
    ada, ben, cy, guest = client(users["a"]), client(users["b"]), client(users["c"]), client(None)

    # Ada starts a family: she is its admin and its first person.
    created = _create_family(ada, "The Maos", "Ada", born=1962)
    fid = created["family"]["id"]
    assert created["family"] == {"id": fid, "name": "The Maos", "role": "admin", "personId": created["person"]["id"]}
    first = created["person"]
    assert first["userId"] == users["a"].id
    assert first["familyId"] == fid
    assert first["addedBy"] == users["a"].id
    assert first["parentIds"] == [] and first["spouseIds"] == []
    assert first["character"]["color"]
    assert families_db.get_member_role(mdb, fid, users["a"].id) == "admin"

    # Ada adds relatives without accounts.
    rosa = _add_person(ada, fid, "Rosa", born=1940)
    luis = _add_person(ada, fid, "Luis", born=1938, spouseIds=[rosa["id"]])
    assert rosa["userId"] is None and rosa["addedBy"] == users["a"].id
    assert rosa["character"]["color"] != first["character"]["color"]

    # Ada makes an invite link.
    res = ada.post(f"/families/{fid}/invites")
    assert res.status_code == 201
    invite = res.json()
    code = invite["code"]
    assert invite["path"] == f"/join/{code}"
    expires = datetime.fromisoformat(invite["expiresAt"])
    assert timedelta(days=13, hours=23) < expires - datetime.now(UTC) <= timedelta(days=14)

    # Ben previews it without signing in and sees who is left to claim.
    res = guest.get(f"/invites/{code}")
    assert res.status_code == 200
    preview = res.json()
    assert preview["familyId"] == fid and preview["familyName"] == "The Maos"
    assert {p["name"] for p in preview["unclaimedPeople"]} == {"Rosa", "Luis"}
    assert all(set(p) <= {"id", "name", "born", "color"} for p in preview["unclaimedPeople"])

    # Joining needs a session.
    assert guest.post(f"/invites/{code}/join", json={"personId": rosa["id"]}).status_code == 401

    # Ben signs in and claims Rosa.
    res = ben.post(f"/invites/{code}/join", json={"personId": rosa["id"]})
    assert res.status_code == 200, res.text
    joined = res.json()
    assert joined["family"] == {"id": fid, "name": "The Maos", "role": "member", "personId": rosa["id"]}
    assert joined["person"]["id"] == rosa["id"] and joined["person"]["userId"] == users["b"].id
    assert [p["name"] for p in guest.get(f"/invites/{code}").json()["unclaimedPeople"]] == ["Luis"]

    # Cy adds themself as the child of Rosa and Luis.
    res = cy.post(
        f"/invites/{code}/join",
        json={"person": {"name": "Cy", "born": 1968, "parentIds": [rosa["id"], luis["id"]]}},
    )
    assert res.status_code == 200, res.text
    joined = res.json()
    cy_person = joined["person"]
    assert joined["family"] == {"id": fid, "name": "The Maos", "role": "member", "personId": cy_person["id"]}
    assert cy_person["parentIds"] == [rosa["id"], luis["id"]]
    assert cy_person["userId"] == users["c"].id and cy_person["addedBy"] == users["c"].id
    assert families_db.get_member_role(mdb, fid, users["c"].id) == "member"

    # The admin sees members and invites; plain members see members only.
    res = ada.get(f"/families/{fid}")
    assert res.status_code == 200
    family = res.json()
    assert {m["userId"]: m["role"] for m in family["members"]} == {
        users["a"].id: "admin",
        users["b"].id: "member",
        users["c"].id: "member",
    }
    assert [i["code"] for i in family["invites"]] == [code]
    for member in (ben, cy):
        res = member.get(f"/families/{fid}")
        assert res.status_code == 200
        assert res.json()["invites"] == []
        assert len(res.json()["members"]) == 3
        assert code not in res.text


# ------------------------------------------------------------------------ create family


def test_create_family_requires_sign_in(client, mdb):
    res = client(None).post("/families", json={"name": "X", "person": {"name": "Me"}})
    assert res.status_code == 401
    assert mdb.families.count_documents({}) == 0


@pytest.mark.parametrize("person", [{"parentIds": ["p1"]}, {"spouseIds": ["p2"]}])
def test_create_family_with_relations_is_400_and_writes_nothing(users, client, mdb, person):
    res = client(users["a"]).post("/families", json={"name": "The Maos", "person": {"name": "Ada", **person}})
    assert res.status_code == 400
    assert mdb.families.count_documents({}) == 0
    assert mdb.people.count_documents({}) == 0


@pytest.mark.parametrize(
    "body",
    [{"name": "   ", "person": {"name": "Ada"}}, {"name": "The Maos", "person": {"name": "  "}}],
)
def test_create_family_blank_names_are_400(users, client, mdb, body):
    assert client(users["a"]).post("/families", json=body).status_code == 400
    assert mdb.families.count_documents({}) == 0


def test_create_family_trims_names_and_keeps_character(users, client):
    res = client(users["a"]).post(
        "/families",
        json={"name": "  The Maos ", "person": {"name": " Ada ", "character": {"color": "#123456", "item": "red scarf"}}},
    )
    assert res.status_code == 201
    body = res.json()
    assert body["family"]["name"] == "The Maos"
    assert body["person"]["name"] == "Ada"
    assert body["person"]["character"]["color"] == "#123456"


def test_create_family_unknown_field_is_422(users, client):
    res = client(users["a"]).post("/families", json={"name": "X", "person": {"name": "Me"}, "extra": 1})
    assert res.status_code == 422


# ------------------------------------------------------------------------------ invites


def test_non_admin_cannot_create_or_revoke_invites(family_a, users, client):
    fid = family_a["fid"]
    code = _invite(family_a["client"], fid)
    ben = client(users["b"])
    assert ben.post(f"/invites/{code}/join", json={"personId": family_a["rosa"]["id"]}).status_code == 200

    res = ben.post(f"/families/{fid}/invites")
    assert res.status_code == 403
    res = ben.delete(f"/families/{fid}/invites/{code}")
    assert res.status_code == 403
    assert client(None).get(f"/invites/{code}").status_code == 200  # still active


def test_revoke_invite(family_a, users, client):
    fid, ada = family_a["fid"], family_a["client"]
    code = _invite(ada, fid)
    res = ada.delete(f"/families/{fid}/invites/{code}")
    assert res.status_code == 204
    assert res.content == b""
    assert ada.delete(f"/families/{fid}/invites/unknown-code").status_code == 404

    preview = client(None).get(f"/invites/{code}")
    assert preview.status_code == 404
    assert preview.json() == {"detail": "Invite not found or expired"}
    join = client(users["b"]).post(f"/invites/{code}/join", json={"personId": family_a["rosa"]["id"]})
    assert join.status_code == 404


def test_expired_invite_is_404_on_preview_and_join(family_a, users, client, mdb):
    fid = family_a["fid"]
    old = datetime.now(UTC) - timedelta(days=15)
    expired = families_db.create_invite(mdb, fid, users["a"].id, now=old)
    assert expired is not None
    assert client(None).get(f"/invites/{expired.code}").status_code == 404
    res = client(users["b"]).post(f"/invites/{expired.code}/join", json={"person": {"name": "Ben"}})
    assert res.status_code == 404
    assert families_db.get_member_role(mdb, fid, users["b"].id) is None
    assert people_db.get_person_for_user(mdb, fid, users["b"].id) is None


def test_unknown_invite_is_404(users, client):
    assert client(None).get("/invites/nope").status_code == 404
    assert client(users["b"]).post("/invites/nope/join", json={"person": {"name": "Ben"}}).status_code == 404


def test_admin_sees_revoked_invites_too(family_a):
    fid, ada = family_a["fid"], family_a["client"]
    code = _invite(ada, fid)
    ada.delete(f"/families/{fid}/invites/{code}")
    invites = ada.get(f"/families/{fid}").json()["invites"]
    assert [(i["code"], i["revoked"]) for i in invites] == [(code, True)]


# --------------------------------------------------------------------------------- join


@pytest.mark.parametrize("shape", ["neither", "both null", "both"])
def test_join_needs_exactly_one_of_person_id_or_person(family_a, users, client, mdb, shape):
    code = _invite(family_a["client"], family_a["fid"])
    body = {
        "neither": {},
        "both null": {"personId": None, "person": None},
        "both": {"personId": family_a["rosa"]["id"], "person": {"name": "Ben"}},
    }[shape]
    res = client(users["b"]).post(f"/invites/{code}/join", json=body)
    assert res.status_code == 422
    assert families_db.get_member_role(mdb, family_a["fid"], users["b"].id) is None
    assert people_db.get_person(mdb, family_a["fid"], family_a["rosa"]["id"]).user_id is None


def test_claiming_someone_elses_person_is_409_and_leaves_no_membership(family_a, users, client, mdb):
    fid = family_a["fid"]
    code = _invite(family_a["client"], fid)
    assert client(users["b"]).post(f"/invites/{code}/join", json={"personId": family_a["rosa"]["id"]}).status_code == 200

    res = client(users["c"]).post(f"/invites/{code}/join", json={"personId": family_a["rosa"]["id"]})
    assert res.status_code == 409
    assert families_db.get_member_role(mdb, fid, users["c"].id) is None
    assert people_db.get_person(mdb, fid, family_a["rosa"]["id"]).user_id == users["b"].id

    # The admin's own person is claimed too.
    res = client(users["c"]).post(f"/invites/{code}/join", json={"personId": family_a["ada"]["id"]})
    assert res.status_code == 409
    assert families_db.get_member_role(mdb, fid, users["c"].id) is None


def test_claim_rollback_when_claim_races(family_a, users, client, mdb, monkeypatch):
    """The person is free at lookup but taken by the time claim_person runs."""
    fid = family_a["fid"]
    code = _invite(family_a["client"], fid)

    def taken(*_args, **_kwargs):
        raise people_db.AlreadyClaimed("this person is already claimed")

    monkeypatch.setattr(people_db, "claim_person", taken)
    res = client(users["c"]).post(f"/invites/{code}/join", json={"personId": family_a["rosa"]["id"]})
    assert res.status_code == 409
    assert families_db.get_member_role(mdb, fid, users["c"].id) is None


def test_claim_unknown_or_other_family_person_is_404(family_a, family_b, users, client, mdb):
    code = _invite(family_a["client"], family_a["fid"])
    cy = client(users["c"])
    assert cy.post(f"/invites/{code}/join", json={"personId": "nobody"}).status_code == 404
    assert cy.post(f"/invites/{code}/join", json={"personId": family_b["wen"]["id"]}).status_code == 404
    assert families_db.get_member_role(mdb, family_a["fid"], users["c"].id) is None
    assert people_db.get_person(mdb, family_b["fid"], family_b["wen"]["id"]).user_id is None


def test_rejoining_by_claim_is_idempotent(family_a, users, client, mdb):
    fid = family_a["fid"]
    code = _invite(family_a["client"], fid)
    ben = client(users["b"])
    first = ben.post(f"/invites/{code}/join", json={"personId": family_a["rosa"]["id"]})
    again = ben.post(f"/invites/{code}/join", json={"personId": family_a["rosa"]["id"]})
    assert first.status_code == again.status_code == 200
    assert first.json() == again.json()
    members = families_db.get_family(mdb, fid).members
    assert [m.user_id for m in members].count(users["b"].id) == 1


def test_member_with_a_person_cannot_claim_another(family_a, users, client, mdb):
    fid = family_a["fid"]
    code = _invite(family_a["client"], fid)
    ben = client(users["b"])
    assert ben.post(f"/invites/{code}/join", json={"personId": family_a["rosa"]["id"]}).status_code == 200

    res = ben.post(f"/invites/{code}/join", json={"personId": family_a["luis"]["id"]})
    assert res.status_code == 409
    # Ben was already a member, so the failed request does not remove him.
    assert families_db.get_member_role(mdb, fid, users["b"].id) == "member"
    assert people_db.get_person(mdb, fid, family_a["luis"]["id"]).user_id is None
    assert people_db.get_person_for_user(mdb, fid, users["b"].id).id == family_a["rosa"]["id"]


def test_admin_rejoining_keeps_admin_role(family_a, client):
    fid, ada = family_a["fid"], family_a["client"]
    code = _invite(ada, fid)
    res = ada.post(f"/invites/{code}/join", json={"personId": family_a["ada"]["id"]})
    assert res.status_code == 200
    assert res.json()["family"]["role"] == "admin"
    assert res.json()["family"]["personId"] == family_a["ada"]["id"]


def test_member_without_a_person_can_claim_later(family_a, users, client, mdb):
    """Someone whose person was unclaimed (e.g. removed then re-added) can still claim."""
    fid = family_a["fid"]
    families_db.add_member(mdb, fid, users["b"].id)
    code = _invite(family_a["client"], fid)
    res = client(users["b"]).post(f"/invites/{code}/join", json={"personId": family_a["luis"]["id"]})
    assert res.status_code == 200
    assert res.json()["family"]["personId"] == family_a["luis"]["id"]


def test_join_by_adding_with_bad_relations_is_400_and_rolls_back(family_a, family_b, users, client, mdb):
    fid = family_a["fid"]
    code = _invite(family_a["client"], fid)
    cy = client(users["c"])
    for person in (
        {"name": "Cy", "parentIds": ["nobody"]},
        {"name": "Cy", "parentIds": [family_b["wen"]["id"]]},
        {"name": "Cy", "spouseIds": [family_b["zoe"]["id"]]},
    ):
        res = cy.post(f"/invites/{code}/join", json={"person": person})
        assert res.status_code == 400, person
        assert families_db.get_member_role(mdb, fid, users["c"].id) is None
    assert mdb.people.count_documents({"familyId": fid, "name": "Cy"}) == 0


def test_join_by_adding_blank_name_is_400(family_a, users, client, mdb):
    code = _invite(family_a["client"], family_a["fid"])
    res = client(users["c"]).post(f"/invites/{code}/join", json={"person": {"name": " "}})
    assert res.status_code == 400
    assert families_db.get_member_role(mdb, family_a["fid"], users["c"].id) is None


def test_join_by_adding_when_already_linked_is_409(family_a, users, client, mdb):
    fid = family_a["fid"]
    code = _invite(family_a["client"], fid)
    ben = client(users["b"])
    assert ben.post(f"/invites/{code}/join", json={"person": {"name": "Ben"}}).status_code == 200
    res = ben.post(f"/invites/{code}/join", json={"person": {"name": "Ben again"}})
    assert res.status_code == 409
    assert families_db.get_member_role(mdb, fid, users["b"].id) == "member"
    assert mdb.people.count_documents({"familyId": fid, "userId": users["b"].id}) == 1


def test_join_by_adding_spouse_links_both_ways(family_a, users, client, mdb):
    fid = family_a["fid"]
    code = _invite(family_a["client"], fid)
    res = client(users["b"]).post(
        f"/invites/{code}/join", json={"person": {"name": "Ben", "spouseIds": [family_a["ada"]["id"]]}}
    )
    assert res.status_code == 200
    ben_id = res.json()["person"]["id"]
    assert ben_id in people_db.get_person(mdb, fid, family_a["ada"]["id"]).spouse_ids


def test_joining_a_second_family(family_a, family_b, users, client, mdb):
    """One user, several families: Ada joins Zoe's family too and keeps her own."""
    ada = family_a["client"]
    res = ada.post(f"/invites/{family_b['code']}/join", json={"personId": family_b["wen"]["id"]})
    assert res.status_code == 200
    assert res.json()["family"] == {
        "id": family_b["fid"],
        "name": "The Zhangs",
        "role": "member",
        "personId": family_b["wen"]["id"],
    }
    assert families_db.get_member_role(mdb, family_a["fid"], users["a"].id) == "admin"


# ------------------------------------------------------------------------------- people


def test_add_person_validates_relations(family_a, family_b):
    fid, ada = family_a["fid"], family_a["client"]
    res = ada.post(f"/families/{fid}/people", json={"name": "Kid", "parentIds": [family_b["zoe"]["id"]]})
    assert res.status_code == 400
    assert "unknown person" in res.json()["detail"]
    res = ada.post(f"/families/{fid}/people", json={"name": "Kid", "parentIds": ["a", "b", "c"]})
    assert res.status_code == 400
    assert ada.post(f"/families/{fid}/people", json={"name": ""}).status_code == 400
    assert ada.post(f"/families/{fid}/people", json={"born": 1990}).status_code == 422


def test_any_member_can_add_people_without_accounts(family_a, users, client):
    fid = family_a["fid"]
    code = _invite(family_a["client"], fid)
    ben = client(users["b"])
    ben.post(f"/invites/{code}/join", json={"personId": family_a["rosa"]["id"]})
    person = _add_person(ben, fid, "Great-grandma Mei", born=1910, died=1999)
    assert person["userId"] is None
    assert person["addedBy"] == users["b"].id
    assert person["familyId"] == fid
    assert person["died"] == 1999


def test_patch_person(family_a):
    fid, ada = family_a["fid"], family_a["client"]
    rosa_id = family_a["rosa"]["id"]
    res = ada.patch(
        f"/families/{fid}/people/{rosa_id}",
        json={"name": "Rosa Mao", "died": 2019, "character": {"color": "#aa3366", "item": "straw hat", "build": "short"}},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["name"] == "Rosa Mao" and body["died"] == 2019 and body["born"] == 1940
    assert body["character"]["build"] == "short"
    assert body["spouseIds"] == [family_a["luis"]["id"]]

    # Make Ada Rosa's parent; then making Rosa Ada's parent would be a cycle.
    res = ada.patch(f"/families/{fid}/people/{rosa_id}", json={"parentIds": [family_a["ada"]["id"]]})
    assert res.status_code == 200
    res = ada.patch(f"/families/{fid}/people/{family_a['ada']['id']}", json={"parentIds": [rosa_id]})
    assert res.status_code == 400
    assert ada.patch(f"/families/{fid}/people/{rosa_id}", json={"name": "  "}).status_code == 400
    assert ada.patch(f"/families/{fid}/people/nobody", json={"name": "X"}).status_code == 404


def test_patch_person_in_another_family_is_404(family_a, family_b, mdb):
    fid, ada = family_a["fid"], family_a["client"]
    res = ada.patch(f"/families/{fid}/people/{family_b['wen']['id']}", json={"name": "Hacked"})
    assert res.status_code == 404
    assert people_db.get_person(mdb, family_b["fid"], family_b["wen"]["id"]).name == "Wen"


def test_patch_with_relation_from_another_family_is_400(family_a, family_b):
    fid, ada = family_a["fid"], family_a["client"]
    res = ada.patch(f"/families/{fid}/people/{family_a['rosa']['id']}", json={"spouseIds": [family_b["zoe"]["id"]]})
    assert res.status_code == 400


# ------------------------------------------------------------------------------ members


def test_removing_a_member_unclaims_their_person(family_a, users, client, mdb):
    fid, ada = family_a["fid"], family_a["client"]
    code = _invite(ada, fid)
    ben = client(users["b"])
    assert ben.post(f"/invites/{code}/join", json={"personId": family_a["rosa"]["id"]}).status_code == 200

    res = ada.delete(f"/families/{fid}/members/{users['b'].id}")
    assert res.status_code == 204
    assert res.content == b""
    assert families_db.get_member_role(mdb, fid, users["b"].id) is None
    rosa = people_db.get_person(mdb, fid, family_a["rosa"]["id"])
    assert rosa is not None and rosa.user_id is None
    assert ben.get(f"/families/{fid}").status_code == 404
    assert ada.delete(f"/families/{fid}/members/{users['b'].id}").status_code == 404
    # Rosa is claimable again.
    assert "Rosa" in [p["name"] for p in client(None).get(f"/invites/{code}").json()["unclaimedPeople"]]


def test_removing_the_last_admin_is_409(family_a, users, mdb):
    fid, ada = family_a["fid"], family_a["client"]
    res = ada.delete(f"/families/{fid}/members/{users['a'].id}")
    assert res.status_code == 409
    assert res.json() == {"detail": "A family needs at least one admin"}
    assert families_db.get_member_role(mdb, fid, users["a"].id) == "admin"


def test_an_admin_can_leave_when_another_admin_remains(family_a, users, mdb):
    fid, ada = family_a["fid"], family_a["client"]
    families_db.add_member(mdb, fid, users["b"].id, "admin")
    assert ada.delete(f"/families/{fid}/members/{users['a'].id}").status_code == 204
    assert families_db.get_member_role(mdb, fid, users["a"].id) is None


def test_non_admin_cannot_remove_members(family_a, users, client):
    fid = family_a["fid"]
    code = _invite(family_a["client"], fid)
    ben = client(users["b"])
    ben.post(f"/invites/{code}/join", json={"personId": family_a["rosa"]["id"]})
    assert ben.delete(f"/families/{fid}/members/{users['a'].id}").status_code == 403
    assert ben.delete(f"/families/{fid}/members/{users['b'].id}").status_code == 403


# ------------------------------------------------------------------------- access rules


def test_signed_out_requests_are_401(family_a, client):
    guest, fid = client(None), family_a["fid"]
    rosa = family_a["rosa"]["id"]
    for method, url, body in (
        ("GET", f"/families/{fid}", None),
        ("POST", f"/families/{fid}/invites", None),
        ("DELETE", f"/families/{fid}/invites/x", None),
        ("POST", f"/families/{fid}/people", {"name": "X"}),
        ("PATCH", f"/families/{fid}/people/{rosa}", {"name": "X"}),
        ("DELETE", f"/families/{fid}/members/x", None),
        ("POST", "/invites/x/join", {"person": {"name": "X"}}),
    ):
        assert guest.request(method, url, json=body).status_code == 401, (method, url)


def test_another_familys_ids_are_404_on_every_family_route(family_a, family_b, users, mdb):
    """Ada is admin of family A. Family B must look exactly like a family that does not exist."""
    ada = family_a["client"]
    fid_b = family_b["fid"]
    wen = family_b["wen"]["id"]
    zoe_user = users["z"].id

    checks = [
        ("GET", f"/families/{fid_b}", None),
        ("POST", f"/families/{fid_b}/invites", None),
        ("DELETE", f"/families/{fid_b}/invites/{family_b['code']}", None),
        ("POST", f"/families/{fid_b}/people", {"name": "Intruder"}),
        ("PATCH", f"/families/{fid_b}/people/{wen}", {"name": "Intruder"}),
        ("DELETE", f"/families/{fid_b}/members/{zoe_user}", None),
        ("PATCH", f"/families/{family_a['fid']}/people/{wen}", {"name": "Intruder"}),
        # Admin-only routes on A with B's ids:
        ("DELETE", f"/families/{family_a['fid']}/invites/{family_b['code']}", None),
        ("DELETE", f"/families/{family_a['fid']}/members/{zoe_user}", None),
    ]
    missing = [
        ("GET", "/families/does-not-exist", None),
        ("POST", "/families/does-not-exist/invites", None),
        ("PATCH", f"/families/{family_a['fid']}/people/does-not-exist", {"name": "X"}),
    ]
    nonexistent_bodies = {ada.request(m, u, json=b).json()["detail"] for m, u, b in missing}
    for method, url, body in checks:
        res = ada.request(method, url, json=body)
        assert res.status_code == 404, (method, url, res.status_code, res.text)
        assert res.json()["detail"] in nonexistent_bodies | {"Person not found", "Member not found", families_routes.INVITE_NOT_FOUND}

    # Nothing in family B changed.
    fam_b = families_db.get_family(mdb, fid_b)
    assert [m.user_id for m in fam_b.members] == [zoe_user]
    assert [(i.code, i.revoked) for i in fam_b.invites] == [(family_b["code"], False)]
    assert people_db.get_person(mdb, fid_b, wen).name == "Wen"
    assert mdb.people.count_documents({"familyId": fid_b}) == 2


def test_no_response_contains_password_hash(family_a, users, client, mdb):
    """Belt and braces on top of the hook: the users collection does store hashes."""
    assert mdb.users.find_one({"_id": users["a"].id})["passwordHash"].startswith("$argon2id$")
    fid, ada = family_a["fid"], family_a["client"]
    code = _invite(ada, fid)
    bodies = [
        ada.get(f"/families/{fid}").text,
        client(None).get(f"/invites/{code}").text,
        client(users["b"]).post(f"/invites/{code}/join", json={"personId": family_a["rosa"]["id"]}).text,
        client(users["c"]).post(f"/invites/{code}/join", json={"person": {"name": "Cy"}}).text,
    ]
    for body in bodies:
        assert "passwordHash" not in body and "argon2" not in body
