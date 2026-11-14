"""A character sheet's itemPropId must name a prop of the family or a built-in library prop.

routes/families.py checks it on every route that writes a sheet: POST /families (library
props only, since a new family has none of its own), POST /families/{fid}/people, PATCH
/families/{fid}/people/{id} and POST /invites/{code}/join when it adds a new person.

- Own prop or library prop: accepted, stored trimmed.
- Unknown id, or another family's prop id: 422 ITEM_PROP_UNKNOWN, the same sentence for
  both, so the response never says whether the other family's prop exists. Nothing is
  written (no person, no membership, no family).
- null or absent: accepted. Blank or whitespace-only: treated as absent, stored as null.
- The seeded demo people who wear their hats as props (Luis luis_straw_hat, Sam sam_cap)
  can still be PATCHed with their existing sheets.

Runs on mongomock only.
"""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import auth
import schemas
from db import client as db_client
from db import families as families_db
from db import people as people_db
from db import props as props_db
from db import users as users_db
from routes import families as families_routes
from seed import demo

LIBRARY_PROP = "lantern"


# ------------------------------------------------------------------------------ fixtures


@pytest.fixture
def library(tmp_path, monkeypatch):
    """One built-in library prop, 'lantern', in a temporary library directory."""
    lib = tmp_path / "props"
    lib.mkdir()
    (lib / f"{LIBRARY_PROP}.json").write_text(
        json.dumps({"name": "lantern", "size": "hand", "art": {"body": None}, "color": "#e0b84f", "source": "library"})
    )
    monkeypatch.setattr(props_db, "LIBRARY_DIR", lib)
    props_db.library_props.cache_clear()
    yield lib
    props_db.library_props.cache_clear()


@pytest.fixture
def app(mdb, library):
    db_client.ensure_indexes(mdb)
    db_client.set_db(mdb)
    application = FastAPI()
    application.include_router(families_routes.router)
    yield application
    db_client.set_db(None)


def _client(app: FastAPI, user: schemas.User) -> TestClient:
    tc = TestClient(app)
    tc.cookies.set(auth.COOKIE_NAME, auth.create_session_token(user.id))
    return tc


def _hat(pid: str, name: str) -> schemas.Prop:
    return schemas.Prop(id=pid, name=name, size="hand", art={"body": None}, color="#d9973a")


@pytest.fixture
def world(mdb, app):
    """Ada's family A owns 'ada_hat'; Zoe's family B owns 'zoe_hat'. Ada is A's admin."""
    ada = users_db.create_user(mdb, email="ada@example.com", password_hash=auth.hash_password("pw-ada"), name="Ada")
    zoe = users_db.create_user(mdb, email="zoe@example.com", password_hash=auth.hash_password("pw-zoe"), name="Zoe")
    newbie = users_db.create_user(mdb, email="new@example.com", password_hash=auth.hash_password("pw-new"),
                                  name="Newbie")
    tc_ada, tc_zoe, tc_new = _client(app, ada), _client(app, zoe), _client(app, newbie)

    fam_a = tc_ada.post("/families", json={"name": "A", "person": {"name": "Ada"}})
    fam_b = tc_zoe.post("/families", json={"name": "B", "person": {"name": "Zoe"}})
    assert fam_a.status_code == 201 and fam_b.status_code == 201, (fam_a.text, fam_b.text)
    fid_a, fid_b = fam_a.json()["family"]["id"], fam_b.json()["family"]["id"]
    props_db.insert_prop(mdb, fid_a, _hat("ada_hat", "Ada's hat"), prop_id="ada_hat")
    props_db.insert_prop(mdb, fid_b, _hat("zoe_hat", "Zoe's hat"), prop_id="zoe_hat")
    code = tc_ada.post(f"/families/{fid_a}/invites").json()["code"]
    yield {
        "db": mdb, "ada": tc_ada, "zoe": tc_zoe, "new": tc_new, "newbie": newbie,
        "fid": fid_a, "fid_b": fid_b, "code": code, "ada_pid": fam_a.json()["person"]["id"],
    }
    for tc in (tc_ada, tc_zoe, tc_new):
        tc.close()


def _sheet(item_prop_id: str | None = None, **extra) -> dict:
    sheet = {"color": "#3f6fb5", "item": "a hat", **extra}
    if item_prop_id is not None or "itemPropId" in extra:
        sheet["itemPropId"] = item_prop_id
    return sheet


def _people_count(db, fid: str) -> int:
    return len(people_db.list_people(db, fid))


# ---------------------------------------------------------------------- POST people


@pytest.mark.parametrize("prop_id", ["ada_hat", LIBRARY_PROP, "  ada_hat  ", f" {LIBRARY_PROP}\t"])
def test_add_person_accepts_own_and_library_props(world, prop_id):
    res = world["ada"].post(f"/families/{world['fid']}/people", json={"name": "Rosa", "character": _sheet(prop_id)})
    assert res.status_code == 201, res.text
    assert res.json()["character"]["itemPropId"] == prop_id.strip()
    stored = people_db.get_person(world["db"], world["fid"], res.json()["id"])
    assert stored is not None and stored.character.item_prop_id == prop_id.strip()


@pytest.mark.parametrize("prop_id", ["no_such_prop", "zoe_hat", " zoe_hat "])
def test_add_person_rejects_unknown_and_other_family_props(world, prop_id):
    before = _people_count(world["db"], world["fid"])
    res = world["ada"].post(f"/families/{world['fid']}/people", json={"name": "Rosa", "character": _sheet(prop_id)})
    assert res.status_code == 422, res.text
    assert res.json() == {"detail": families_routes.ITEM_PROP_UNKNOWN}
    assert _people_count(world["db"], world["fid"]) == before


def test_another_familys_prop_reads_exactly_like_a_missing_one(world):
    """No leak: the other family's real prop and a made-up id get byte-identical answers."""
    url = f"/families/{world['fid']}/people"
    real = world["ada"].post(url, json={"name": "Rosa", "character": _sheet("zoe_hat")})
    fake = world["ada"].post(url, json={"name": "Rosa", "character": _sheet("zoe_hat_nope")})
    assert (real.status_code, real.content) == (fake.status_code, fake.content)
    assert "zoe" not in real.text.lower()


@pytest.mark.parametrize("sheet", [_sheet(), {**_sheet(), "itemPropId": None}, _sheet(""), _sheet("   "), None])
def test_add_person_without_an_item_prop_is_fine(world, sheet):
    body = {"name": "Rosa"} if sheet is None else {"name": "Rosa", "character": sheet}
    res = world["ada"].post(f"/families/{world['fid']}/people", json=body)
    assert res.status_code == 201, res.text
    assert res.json()["character"]["itemPropId"] is None


def test_the_check_comes_before_other_family_ids_are_reached(world):
    """A family the caller is not in stays a 404 even with a bad item prop."""
    res = world["ada"].post(f"/families/{world['fid_b']}/people", json={"name": "X", "character": _sheet("nope")})
    assert res.status_code == 404


# ---------------------------------------------------------------------- PATCH people


def test_patch_sets_changes_and_clears_the_item_prop(world):
    url = f"/families/{world['fid']}/people/{world['ada_pid']}"
    tc = world["ada"]
    res = tc.patch(url, json={"character": _sheet("ada_hat")})
    assert res.status_code == 200, res.text
    assert res.json()["character"]["itemPropId"] == "ada_hat"

    res = tc.patch(url, json={"character": _sheet(LIBRARY_PROP)})
    assert res.status_code == 200 and res.json()["character"]["itemPropId"] == LIBRARY_PROP

    # Clearing: a sheet without one, an explicit null, or a blank string.
    for sheet in (_sheet(), {**_sheet(), "itemPropId": None}, _sheet(" ")):
        tc.patch(url, json={"character": _sheet("ada_hat")})
        res = tc.patch(url, json={"character": sheet})
        assert res.status_code == 200, res.text
        assert res.json()["character"]["itemPropId"] is None
        stored = people_db.get_person(world["db"], world["fid"], world["ada_pid"])
        assert stored is not None and stored.character.item_prop_id is None


@pytest.mark.parametrize("prop_id", ["no_such_prop", "zoe_hat"])
def test_patch_rejects_unknown_and_other_family_props_and_keeps_the_sheet(world, prop_id):
    url = f"/families/{world['fid']}/people/{world['ada_pid']}"
    assert world["ada"].patch(url, json={"character": _sheet("ada_hat")}).status_code == 200
    res = world["ada"].patch(url, json={"name": "Ada Renamed", "character": _sheet(prop_id)})
    assert res.status_code == 422
    assert res.json()["detail"] == families_routes.ITEM_PROP_UNKNOWN
    stored = people_db.get_person(world["db"], world["fid"], world["ada_pid"])
    assert stored is not None
    assert stored.name == "Ada" and stored.character.item_prop_id == "ada_hat"


def test_patch_without_a_character_never_checks_the_item_prop(world, mdb):
    """A rename leaves an existing sheet alone, even one written before this check existed."""
    mdb[people_db.COLLECTION].update_one(
        {"_id": world["ada_pid"]}, {"$set": {"character.itemPropId": "long_gone_prop"}}
    )
    res = world["ada"].patch(f"/families/{world['fid']}/people/{world['ada_pid']}", json={"name": "Ada M"})
    assert res.status_code == 200, res.text
    assert res.json()["character"]["itemPropId"] == "long_gone_prop"


# ------------------------------------------------------------------------- join


def test_join_by_adding_yourself_checks_the_item_prop(world):
    tc, code, fid = world["new"], world["code"], world["fid"]
    for bad in ("no_such_prop", "zoe_hat"):
        res = tc.post(f"/invites/{code}/join", json={"person": {"name": "Newbie", "character": _sheet(bad)}})
        assert res.status_code == 422, res.text
        assert res.json()["detail"] == families_routes.ITEM_PROP_UNKNOWN
        # Nothing is left behind: no membership, no person.
        assert families_db.get_member_role(world["db"], fid, world["newbie"].id) is None
        assert people_db.get_person_for_user(world["db"], fid, world["newbie"].id) is None

    res = tc.post(f"/invites/{code}/join", json={"person": {"name": "Newbie", "character": _sheet("ada_hat")}})
    assert res.status_code == 200, res.text
    assert res.json()["person"]["character"]["itemPropId"] == "ada_hat"


@pytest.mark.parametrize("prop_id", [LIBRARY_PROP, None, "  "])
def test_join_by_adding_yourself_accepts_library_props_and_none(world, prop_id):
    sheet = _sheet(prop_id) if prop_id is not None else _sheet()
    res = world["new"].post(f"/invites/{world['code']}/join", json={"person": {"name": "Newbie", "character": sheet}})
    assert res.status_code == 200, res.text
    expected = (prop_id or "").strip() or None
    assert res.json()["person"]["character"]["itemPropId"] == expected


def test_join_by_claim_is_not_affected(world):
    rosa = world["ada"].post(f"/families/{world['fid']}/people", json={"name": "Rosa"}).json()
    res = world["new"].post(f"/invites/{world['code']}/join", json={"personId": rosa["id"]})
    assert res.status_code == 200, res.text


# ------------------------------------------------------------------- create family


def test_create_family_accepts_a_library_prop_and_none(world):
    tc = world["new"]
    res = tc.post("/families", json={"name": "C", "person": {"name": "N", "character": _sheet(LIBRARY_PROP)}})
    assert res.status_code == 201, res.text
    assert res.json()["person"]["character"]["itemPropId"] == LIBRARY_PROP
    res = tc.post("/families", json={"name": "D", "person": {"name": "N", "character": _sheet(" ")}})
    assert res.status_code == 201, res.text
    assert res.json()["person"]["character"]["itemPropId"] is None


@pytest.mark.parametrize("prop_id", ["no_such_prop", "zoe_hat", "ada_hat"])
def test_create_family_rejects_any_family_prop_and_leaves_no_family(world, mdb, prop_id):
    """A new family owns no props yet, so only library props can be worn by its creator."""
    res = world["new"].post("/families", json={"name": "C", "person": {"name": "N", "character": _sheet(prop_id)}})
    assert res.status_code == 422, res.text
    assert res.json()["detail"] == families_routes.ITEM_PROP_UNKNOWN
    assert families_db.list_family_summaries(mdb, world["newbie"].id) == []


# ------------------------------------------------------------------- seeded people


@pytest.fixture
def seeded(mdb, app):
    demo.seed_demo(mdb)
    admin = demo.DEMO_ACCOUNTS[0]
    user = users_db.get_user(mdb, admin.user_id)
    assert user is not None
    tc = _client(app, user)
    yield {"db": mdb, "tc": tc}
    tc.close()


def test_seeded_people_with_item_props_still_patch_cleanly(seeded):
    fid = demo.DEMO_FAMILY_ID
    worn = {
        p.id: p for p in people_db.list_people(seeded["db"], fid) if p.character.item_prop_id is not None
    }
    # Discovered, not hardcoded; Luis's straw hat and Sam's cap are among them.
    assert {"grandpa_luis", "sam"} <= set(worn)
    assert worn["grandpa_luis"].character.item_prop_id == "luis_straw_hat"
    assert worn["sam"].character.item_prop_id == "sam_cap"
    for pid, person in worn.items():
        sheet = person.character.model_dump(mode="json", by_alias=True)
        res = seeded["tc"].patch(f"/families/{fid}/people/{pid}", json={"character": sheet})
        assert res.status_code == 200, (pid, res.text)
        assert res.json()["character"] == sheet
        res = seeded["tc"].patch(f"/families/{fid}/people/{pid}", json={"name": person.name})
        assert res.status_code == 200, (pid, res.text)
        assert res.json()["character"]["itemPropId"] == person.character.item_prop_id


def test_item_prop_ok_is_family_scoped(world):
    db, fid, fid_b = world["db"], world["fid"], world["fid_b"]
    assert families_routes.item_prop_ok(db, fid, "ada_hat")
    assert families_routes.item_prop_ok(db, fid, " ada_hat ")
    assert families_routes.item_prop_ok(db, fid, LIBRARY_PROP)
    assert families_routes.item_prop_ok(db, None, LIBRARY_PROP)
    assert not families_routes.item_prop_ok(db, fid, "zoe_hat")
    assert families_routes.item_prop_ok(db, fid_b, "zoe_hat")
    assert not families_routes.item_prop_ok(db, None, "ada_hat")
    assert not families_routes.item_prop_ok(db, fid, "")
    assert not families_routes.item_prop_ok(db, fid, "   ")
