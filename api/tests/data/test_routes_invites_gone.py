"""410 Gone for an invite code that exists but can no longer be used; 404 only for an unknown one.

GET /invites/{code} and POST /invites/{code}/join, through the real app (main.create_app,
all middleware) over mongomock:

- a fresh code previews (200) and joins as before;
- an expired code (expiresAt <= now) is 410 INVITE_EXPIRED, a revoked one 410
  INVITE_REVOKED, and revoked wins when both apply;
- an unknown or empty-ish code is 404 INVITE_NOT_FOUND;
- a 410 join writes nothing, even when its body is also invalid;
- a 410 never names the family the code belonged to;
- signed out, a join is 401 whatever the code's state (auth runs first); the public
  preview is where a signed-out visitor learns the link is stale.

Expired codes are made relative to the real clock (create_invite with a `now` in the
past), never with a hardcoded date.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

import auth
import main
import schemas
from db import client as db_client
from db import families as families_db
from db import people as people_db
from db import users as users_db
from routes import families as families_routes

EXPIRED = {"detail": "This invite has expired"}
REVOKED = {"detail": "This invite was revoked"}
NOT_FOUND = {"detail": "Invite not found"}


# ------------------------------------------------------------------------------ fixtures


@pytest.fixture
def world(mdb, monkeypatch):
    """The real app over mongomock. Ada is admin of "The Maos" (Ada, Rosa unclaimed); Ben
    has an account and no family."""
    monkeypatch.delenv("STITCH_CORS_ORIGINS", raising=False)
    db_client.ensure_indexes(mdb)
    db_client.set_db(mdb)
    made = {
        key: users_db.create_user(mdb, email=f"{key}@example.com", password_hash="x", name=name)
        for key, name in (("ada", "Ada"), ("ben", "Ben"))
    }
    family = families_db.create_family(mdb, name="The Maos", creator_user_id=made["ada"].id)
    people_db.create_person(mdb, family.id, schemas.NewPerson(name="Ada"), added_by=made["ada"].id, user_id=made["ada"].id)
    rosa = people_db.create_person(mdb, family.id, schemas.NewPerson(name="Rosa"), added_by=made["ada"].id)
    app = main.create_app()
    clients: list[TestClient] = []

    def client(user: schemas.User | None = None) -> TestClient:
        tc = TestClient(app)
        if user is not None:
            tc.cookies.set(auth.COOKIE_NAME, auth.create_session_token(user.id))
        clients.append(tc)
        return tc

    w = type("World", (), {})()
    w.db, w.client, w.users, w.fid, w.rosa = mdb, client, made, family.id, rosa
    w.ada, w.ben, w.anon = client(made["ada"]), client(made["ben"]), client(None)
    yield w
    for tc in clients:
        tc.close()
    db_client.set_db(None)


def _fresh(w) -> str:
    res = w.ada.post(f"/families/{w.fid}/invites")
    assert res.status_code == 201, res.text
    return res.json()["code"]


def _expired(w) -> str:
    """A code whose 14 days ran out a day ago, by the real clock."""
    long_ago = datetime.now(UTC) - timedelta(days=families_db.INVITE_DAYS + 1)
    invite = families_db.create_invite(w.db, w.fid, w.users["ada"].id, now=long_ago)
    assert invite is not None
    return invite.code


def _revoked(w) -> str:
    code = _fresh(w)
    assert w.ada.delete(f"/families/{w.fid}/invites/{code}").status_code == 204
    return code


def _revoked_and_expired(w) -> str:
    code = _expired(w)
    assert w.ada.delete(f"/families/{w.fid}/invites/{code}").status_code == 204  # revoking an expired code still works
    return code


def _snapshot(w) -> dict:
    """Everything a join could write: the family document and every person."""
    return {
        "family": w.db.families.find_one({"_id": w.fid}),
        "people": sorted(w.db.people.find({}), key=lambda p: p["_id"]),
    }


def _bodies(w) -> list[dict]:
    """Join bodies: claim, add yourself, and three that would fail their own validation."""
    return [
        {"personId": w.rosa.id},
        {"person": {"name": "Ben"}},
        {},  # neither personId nor person: 422 for a usable code
        {"personId": w.rosa.id, "person": {"name": "Ben"}},  # both: 422 for a usable code
        {"person": {"name": "   "}},  # blank name: 400 for a usable code
        {"personId": "nobody"},  # unknown person: 404 for a usable code
    ]


STALE = [(_expired, EXPIRED), (_revoked, REVOKED), (_revoked_and_expired, REVOKED)]
STALE_IDS = ["expired", "revoked", "revoked-and-expired"]


# ------------------------------------------------------------------------------- fresh


def test_fresh_code_previews_and_joins(world):
    code = _fresh(world)
    preview = world.anon.get(f"/invites/{code}")
    assert preview.status_code == 200
    assert preview.json()["familyName"] == "The Maos"
    res = world.ben.post(f"/invites/{code}/join", json={"personId": world.rosa.id})
    assert res.status_code == 200, res.text
    assert families_db.get_member_role(world.db, world.fid, world.users["ben"].id) == "member"
    assert people_db.get_person(world.db, world.fid, world.rosa.id).user_id == world.users["ben"].id


def test_fresh_code_joins_by_adding_yourself(world):
    code = _fresh(world)
    res = world.ben.post(f"/invites/{code}/join", json={"person": {"name": "Ben"}})
    assert res.status_code == 200, res.text
    assert res.json()["person"]["name"] == "Ben"


def test_fresh_code_still_answers_body_errors_as_before(world):
    """A usable code: the body checks answer exactly as they did before 410 existed."""
    code = _fresh(world)
    expected = [422, 422, 400, 404]
    for body, status in zip(_bodies(world)[2:], expected, strict=True):
        assert world.ben.post(f"/invites/{code}/join", json=body).status_code == status, body
    assert families_db.get_member_role(world.db, world.fid, world.users["ben"].id) is None


# ------------------------------------------------------------------------------- stale


@pytest.mark.parametrize(("make", "detail"), STALE, ids=STALE_IDS)
def test_stale_code_preview_is_410(world, make, detail):
    code = make(world)
    res = world.anon.get(f"/invites/{code}")
    assert res.status_code == 410
    assert res.json() == detail
    # Signed in, the same answer.
    assert world.ben.get(f"/invites/{code}").json() == detail


@pytest.mark.parametrize(("make", "detail"), STALE, ids=STALE_IDS)
def test_stale_code_join_is_410_and_writes_nothing(world, make, detail):
    code = make(world)
    before = _snapshot(world)
    for body in _bodies(world):
        res = world.ben.post(f"/invites/{code}/join", json=body)
        assert res.status_code == 410, (body, res.text)
        assert res.json() == detail, body
    assert _snapshot(world) == before
    assert families_db.get_member_role(world.db, world.fid, world.users["ben"].id) is None
    assert people_db.get_person_for_user(world.db, world.fid, world.users["ben"].id) is None
    assert world.db.people.count_documents({"name": "Ben"}) == 0


@pytest.mark.parametrize(("make", "detail"), STALE, ids=STALE_IDS)
def test_stale_code_is_410_even_for_a_member(world, make, detail):
    """Ada already belongs: a stale code is still 410 for her, not a silent re-join."""
    code = make(world)
    res = world.ada.post(f"/invites/{code}/join", json={"personId": world.rosa.id})
    assert (res.status_code, res.json()) == (410, detail)
    assert people_db.get_person(world.db, world.fid, world.rosa.id).user_id is None


@pytest.mark.parametrize(("make", "detail"), STALE, ids=STALE_IDS)
def test_a_410_does_not_name_the_family(world, make, detail):
    code = make(world)
    for res in (world.anon.get(f"/invites/{code}"), world.ben.post(f"/invites/{code}/join", json={})):
        assert res.status_code == 410
        assert res.json() == detail  # the detail and nothing else
        assert "Maos" not in res.text and world.fid not in res.text


def test_the_expiry_boundary_is_expired(world):
    """expiresAt exactly at the request's clock reading answers 410 expired.

    The stored expiresAt is moved into the past by a whisker so the request's own
    datetime.now() is at or after it; the exact equality is unit-tested in
    test_families_db.py with now=.
    """
    code = _fresh(world)
    just_now = datetime.now(UTC)
    world.db.families.update_one(
        {"_id": world.fid, "invites.code": code}, {"$set": {"invites.$.expiresAt": just_now}}
    )
    res = world.anon.get(f"/invites/{code}")
    assert (res.status_code, res.json()) == (410, EXPIRED)


def test_expiry_from_a_patched_expires_at(world):
    """A code made fresh and then aged in the database is expired, not unknown."""
    code = _fresh(world)
    world.db.families.update_one(
        {"_id": world.fid, "invites.code": code},
        {"$set": {"invites.$.expiresAt": datetime.now(UTC) - timedelta(minutes=1)}},
    )
    assert world.ben.post(f"/invites/{code}/join", json={"person": {"name": "Ben"}}).json() == EXPIRED
    world.db.families.update_one(
        {"_id": world.fid, "invites.code": code},
        {"$set": {"invites.$.expiresAt": datetime.now(UTC) + timedelta(days=1)}},
    )
    assert world.ben.get(f"/invites/{code}").status_code == 200


def test_revoking_one_code_leaves_another_usable(world):
    gone, fresh = _revoked(world), _fresh(world)
    assert world.ben.get(f"/invites/{gone}").json() == REVOKED
    assert world.ben.post(f"/invites/{fresh}/join", json={"personId": world.rosa.id}).status_code == 200
    assert world.ben.post(f"/invites/{gone}/join", json={"personId": world.rosa.id}).json() == REVOKED


# ------------------------------------------------------------------------------ unknown


@pytest.mark.parametrize("code", ["nope", "%20", "x" * 200, "aaaaaaaaaaaaaaaa"])
def test_unknown_code_is_404(world, code):
    _fresh(world)  # a real code exists; these are not it
    preview = world.anon.get(f"/invites/{code}")
    assert (preview.status_code, preview.json()) == (404, NOT_FOUND)
    join = world.ben.post(f"/invites/{code}/join", json={"person": {"name": "Ben"}})
    assert (join.status_code, join.json()) == (404, NOT_FOUND)
    assert families_db.get_member_role(world.db, world.fid, world.users["ben"].id) is None


def test_unknown_code_is_404_even_with_an_invalid_body(world):
    """The code is checked before the body's personId / person, like a stale one."""
    res = world.ben.post("/invites/nope/join", json={})
    assert (res.status_code, res.json()) == (404, NOT_FOUND)


def test_an_empty_code_never_reaches_the_invite_routes(world):
    """`/invites/` and `/invites//join` match no route: a plain 404 or 405, never a 410."""
    assert world.anon.get("/invites/").status_code in (404, 405)
    assert world.ben.post("/invites//join", json={"person": {"name": "Ben"}}).status_code in (404, 405)


def test_the_detail_constants(world):
    assert families_routes.INVITE_EXPIRED == EXPIRED["detail"]
    assert families_routes.INVITE_REVOKED == REVOKED["detail"]
    assert families_routes.INVITE_NOT_FOUND == NOT_FOUND["detail"]


# ----------------------------------------------------------------------- ordering rules


@pytest.mark.parametrize("make", [_expired, _revoked, _fresh, lambda w: "nope"], ids=["expired", "revoked", "fresh", "unknown"])
def test_signed_out_join_is_always_401(world, make):
    """Auth runs first (current_user is a dependency), so a signed-out join is 401 whatever
    the code's state, and says nothing about the code. The public preview is what tells a
    signed-out visitor the link is stale."""
    code = make(world)
    before = _snapshot(world)
    res = world.anon.post(f"/invites/{code}/join", json={"person": {"name": "Ben"}})
    assert res.status_code == 401
    assert "invite" not in res.json()["detail"].lower()
    assert _snapshot(world) == before


def test_an_unparseable_body_is_422_before_the_code_is_checked(world):
    """FastAPI validates the body against JoinRequest before the route runs, so a body
    that is not a JoinRequest at all (an unknown field, a wrong type) is 422 even on a
    stale code. Only the route's own checks (exactly one of personId / person, the
    person's name and sheet) come after the 410."""
    code = _expired(world)
    for body in ({"personId": world.rosa.id, "extra": 1}, {"person": "Ben"}):
        assert world.ben.post(f"/invites/{code}/join", json=body).status_code == 422, body
    assert families_db.get_member_role(world.db, world.fid, world.users["ben"].id) is None


def test_admin_revoke_route_is_unchanged(world):
    """DELETE /families/{fid}/invites/{code}: 204 for any code of this family, revoked or
    expired included, and 404 INVITE_NOT_FOUND for one that is not."""
    for code in (_fresh(world), _revoked(world), _expired(world)):
        assert world.ada.delete(f"/families/{world.fid}/invites/{code}").status_code == 204
    res = world.ada.delete(f"/families/{world.fid}/invites/nope")
    assert (res.status_code, res.json()) == (404, NOT_FOUND)
