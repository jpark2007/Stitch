"""db/traditions.py and routes/traditions.py: the Book of Family Traditions. Runs on mongomock."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import auth
import schemas
from db import client as db_client
from db import families as families_db
from db import media as media_db
from db import people as people_db
from db import traditions as traditions_db
from db import users as users_db
from db.client import get_db
from main import ROUTERS
from routes import traditions as traditions_routes
from seed import demo
from seed.demo import DEMO_FAMILY_ID, seed_demo

REPO = Path(__file__).resolve().parents[3]
FAM_A = "fam_a"
FAM_B = "fam_b"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


# --------------------------------------------------------------------- fixtures


@pytest.fixture
def env(mdb):
    """Two families. Alice is in A (with Grandma and a photo), Bob in B (with Bo, a photo
    and a tradition of his own). The client is signed in as Alice."""
    db_client.ensure_indexes(mdb)
    alice = users_db.create_user(mdb, email="alice@x.com", password_hash=auth.hash_password("pw-alice"),
                                 name="Alice")
    bob = users_db.create_user(mdb, email="bob@x.com", password_hash=auth.hash_password("pw-bob"), name="Bob")
    families_db.create_family(mdb, name="Family A", creator_user_id=alice.id, family_id=FAM_A)
    families_db.create_family(mdb, name="Family B", creator_user_id=bob.id, family_id=FAM_B)
    grandma = people_db.create_person(mdb, FAM_A, schemas.NewPerson(name="Grandma"), added_by=alice.id)
    me = people_db.create_person(mdb, FAM_A, schemas.NewPerson(name="Alice"), added_by=alice.id, user_id=alice.id)
    bo = people_db.create_person(mdb, FAM_B, schemas.NewPerson(name="Bo"), added_by=bob.id, user_id=bob.id)
    a_photo = media_db.put_media(mdb, FAM_A, PNG, content_type="image/png", filename="a.png")
    b_photo = media_db.put_media(mdb, FAM_B, PNG, content_type="image/png", filename="b.png")
    b_tradition = traditions_db.create_tradition(
        mdb, FAM_B,
        schemas.CreateTraditionRequest(title="B's secret", category="game",
                                       when=schemas.TraditionWhen(kind="weekly"), participant_ids=[bo.id]),
        created_by=bob.id,
    )

    app = FastAPI()
    app.include_router(traditions_routes.router)
    app.dependency_overrides[get_db] = lambda: mdb
    tc = TestClient(app)
    tc.cookies.set(auth.COOKIE_NAME, auth.create_session_token(alice.id))
    return {"tc": tc, "db": mdb, "alice": alice, "bob": bob, "grandma": grandma, "me": me, "bo": bo,
            "a_photo": a_photo, "b_photo": b_photo, "b_tradition": b_tradition}


def _body(**over) -> dict:
    body = {"title": "Tamales on Christmas Eve", "category": "food",
            "when": {"kind": "yearly", "label": "Christmas Eve", "month": 12}}
    body.update(over)
    return body


def _url(fid: str = FAM_A, tid: str | None = None) -> str:
    return f"/families/{fid}/traditions" + (f"/{tid}" if tid else "")


# --------------------------------------------------------------------- CRUD


def test_crud_happy_path(env):
    tc = env["tc"]
    assert tc.get(_url()).json() == {"traditions": []}

    res = tc.post(_url(), json=_body(
        description="  The whole kitchen turns into an assembly line.  ",
        participantIds=[env["grandma"].id, env["me"].id, env["grandma"].id],
        photoIds=[env["a_photo"]],
        startedYear=1962,
    ))
    assert res.status_code == 201, res.text
    made = schemas.Tradition.model_validate(res.json())
    assert made.family_id == FAM_A
    assert made.title == "Tamales on Christmas Eve"
    assert made.description == "The whole kitchen turns into an assembly line."  # trimmed
    assert made.when == schemas.TraditionWhen(kind="yearly", label="Christmas Eve", month=12)
    assert made.participant_ids == [env["grandma"].id, env["me"].id]  # repeats collapse
    assert made.photo_ids == [env["a_photo"]]
    assert made.started_year == 1962
    assert made.created_by == env["alice"].id
    assert made.created_at == made.updated_at
    assert "_id" not in res.json()

    got = tc.get(_url(tid=made.id))
    assert got.status_code == 200
    assert schemas.Tradition.model_validate(got.json()) == made

    second = tc.post(_url(), json=_body(title="Sunday pancakes", category="food",
                                        when={"kind": "weekly", "label": "every Sunday"}))
    assert second.status_code == 201
    listed = tc.get(_url()).json()["traditions"]
    assert [t["id"] for t in listed] == [made.id, second.json()["id"]]  # oldest first
    assert all(t["familyId"] == FAM_A for t in listed)  # never B's tradition
    schemas.TraditionsResponse.model_validate(tc.get(_url()).json())

    patched = tc.patch(_url(tid=made.id), json={"title": "Christmas Eve tamales", "category": "holiday",
                                                "when": {"kind": "yearly"}, "participantIds": [env["me"].id]})
    assert patched.status_code == 200, patched.text
    p = schemas.Tradition.model_validate(patched.json())
    assert p.title == "Christmas Eve tamales" and p.category == "holiday"
    assert p.when == schemas.TraditionWhen(kind="yearly")  # replaced whole: label and month gone
    assert p.participant_ids == [env["me"].id]
    assert p.description == made.description and p.photo_ids == made.photo_ids  # omitted: unchanged
    assert p.created_at == made.created_at and p.created_by == made.created_by
    assert p.updated_at >= made.updated_at
    assert schemas.Tradition.model_validate(tc.get(_url(tid=made.id)).json()) == p

    assert tc.delete(_url(tid=made.id)).status_code == 204
    assert tc.get(_url(tid=made.id)).status_code == 404
    assert tc.delete(_url(tid=made.id)).status_code == 404
    assert [t["id"] for t in tc.get(_url()).json()["traditions"]] == [second.json()["id"]]
    # Its photo stays: media can be shared with memories.
    assert media_db.media_exists(env["db"], FAM_A, env["a_photo"])


def test_list_order_is_created_at_then_id(mdb):
    when = schemas.TraditionWhen(kind="occasional")
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    t1 = datetime(2026, 1, 2, tzinfo=UTC)
    for tid, now in (("b", t0), ("c", t1), ("a", t0)):
        traditions_db.create_tradition(mdb, FAM_A, schemas.CreateTraditionRequest(title=tid, category="other",
                                                                                   when=when),
                                       created_by="u", tradition_id=tid, now=now)
    assert [t.id for t in traditions_db.list_traditions(mdb, FAM_A)] == ["a", "b", "c"]
    assert traditions_db.list_traditions(mdb, FAM_B) == []


def test_create_with_a_taken_id_is_a_value_error(mdb):
    body = schemas.CreateTraditionRequest(title="x", category="other", when=schemas.TraditionWhen(kind="yearly"))
    traditions_db.create_tradition(mdb, FAM_A, body, created_by="u", tradition_id="t1")
    with pytest.raises(ValueError):
        traditions_db.create_tradition(mdb, FAM_B, body, created_by="u", tradition_id="t1")


def test_minimal_create_has_empty_defaults(env):
    res = env["tc"].post(_url(), json=_body())
    assert res.status_code == 201
    body = res.json()
    assert body["description"] == "" and body["participantIds"] == [] and body["photoIds"] == []
    assert body["startedYear"] is None


# --------------------------------------------------------------------- PATCH semantics


def test_patch_started_year_null_clears_but_omitted_keeps(env):
    tc = env["tc"]
    tid = tc.post(_url(), json=_body(startedYear=1962)).json()["id"]

    kept = tc.patch(_url(tid=tid), json={"title": "Renamed"})
    assert kept.status_code == 200 and kept.json()["startedYear"] == 1962

    changed = tc.patch(_url(tid=tid), json={"startedYear": 1970})
    assert changed.json()["startedYear"] == 1970

    cleared = tc.patch(_url(tid=tid), json={"startedYear": None})
    assert cleared.status_code == 200 and cleared.json()["startedYear"] is None
    assert tc.get(_url(tid=tid)).json()["startedYear"] is None


def test_patch_null_fields_other_than_started_year_are_unchanged(env):
    tc = env["tc"]
    made = tc.post(_url(), json=_body(description="d", participantIds=[env["me"].id],
                                      photoIds=[env["a_photo"]], startedYear=1999)).json()
    res = tc.patch(_url(tid=made["id"]), json={"title": None, "description": None, "category": None,
                                               "when": None, "participantIds": None, "photoIds": None})
    assert res.status_code == 200
    assert res.json() == made  # nothing sent, so updatedAt does not move either


def test_patch_can_empty_lists_and_description(env):
    tc = env["tc"]
    made = tc.post(_url(), json=_body(description="d", participantIds=[env["me"].id],
                                      photoIds=[env["a_photo"]])).json()
    res = tc.patch(_url(tid=made["id"]), json={"description": "", "participantIds": [], "photoIds": []})
    assert res.status_code == 200
    assert res.json()["description"] == "" and res.json()["participantIds"] == [] and res.json()["photoIds"] == []


# --------------------------------------------------------------------- validation


@pytest.mark.parametrize("body", [
    _body(title=""),
    _body(title="   "),
    _body(title="x" * 201),
    _body(description="x" * 5001),
    _body(category="sports"),
    _body(when={"kind": "daily"}),
    _body(when={"kind": "yearly", "month": 0}),
    _body(when={"kind": "yearly", "month": 13}),
    _body(when={"kind": "yearly", "label": "x" * 101}),
    _body(startedYear=1799),
    _body(startedYear=datetime.now(UTC).year + 1),
    _body(participantIds=[f"p{i}" for i in range(101)]),
    _body(photoIds=[f"m{i}" for i in range(11)]),
    {"category": "food", "when": {"kind": "yearly"}},  # no title
    {"title": "x", "when": {"kind": "yearly"}},  # no category
    {"title": "x", "category": "food"},  # no when
])
def test_create_validation_is_422(env, body):
    res = env["tc"].post(_url(), json=body)
    assert res.status_code == 422, res.text
    assert env["tc"].get(_url()).json() == {"traditions": []}


@pytest.mark.parametrize("body", [
    {"title": ""},
    {"title": "x" * 201},
    {"description": "x" * 5001},
    {"category": "sports"},
    {"when": {"kind": "yearly", "month": 13}},
    {"startedYear": 1799},
    {"photoIds": [f"m{i}" for i in range(11)]},
])
def test_patch_validation_is_422(env, body):
    tid = env["tc"].post(_url(), json=_body()).json()["id"]
    assert env["tc"].patch(_url(tid=tid), json=body).status_code == 422


def test_unknown_or_other_family_participant_is_400(env):
    tc = env["tc"]
    for ids in (["nobody"], [env["bo"].id], [env["me"].id, env["bo"].id]):
        res = tc.post(_url(), json=_body(participantIds=ids))
        assert res.status_code == 400
        assert res.json() == {"detail": traditions_routes.UNKNOWN_PARTICIPANT}
    tid = tc.post(_url(), json=_body()).json()["id"]
    res = tc.patch(_url(tid=tid), json={"participantIds": [env["bo"].id]})
    assert res.status_code == 400 and res.json() == {"detail": traditions_routes.UNKNOWN_PARTICIPANT}
    assert tc.get(_url(tid=tid)).json()["participantIds"] == []


def test_unknown_or_other_family_photo_is_400(env):
    tc = env["tc"]
    for ids in (["not-an-id"], ["0" * 24], [env["b_photo"]], [env["a_photo"], env["b_photo"]]):
        res = tc.post(_url(), json=_body(photoIds=ids))
        assert res.status_code == 400
        assert res.json() == {"detail": traditions_routes.UNKNOWN_PHOTO}
    tid = tc.post(_url(), json=_body()).json()["id"]
    res = tc.patch(_url(tid=tid), json={"photoIds": [env["b_photo"]]})
    assert res.status_code == 400 and res.json() == {"detail": traditions_routes.UNKNOWN_PHOTO}
    assert len(tc.get(_url()).json()["traditions"]) == 1


# --------------------------------------------------------------------- access


def test_signed_out_is_401(env):
    tc = env["tc"]
    tid = tc.post(_url(), json=_body()).json()["id"]
    tc.cookies.clear()
    assert tc.get(_url()).status_code == 401
    assert tc.post(_url(), json=_body()).status_code == 401
    assert tc.get(_url(tid=tid)).status_code == 401
    assert tc.patch(_url(tid=tid), json={"title": "x"}).status_code == 401
    assert tc.delete(_url(tid=tid)).status_code == 401


@pytest.mark.parametrize("fid", [FAM_B, "no_such_family"])
def test_non_member_or_unknown_family_is_404(env, fid):
    tc = env["tc"]
    tid = env["b_tradition"].id
    assert tc.get(_url(fid)).status_code == 404
    assert tc.post(_url(fid), json=_body()).status_code == 404
    assert tc.get(_url(fid, tid)).status_code == 404
    assert tc.patch(_url(fid, tid), json={"title": "mine now"}).status_code == 404
    assert tc.delete(_url(fid, tid)).status_code == 404
    assert traditions_db.list_traditions(env["db"], FAM_B) == [env["b_tradition"]]


def test_unknown_tradition_id_is_404(env):
    tc = env["tc"]
    for res in (tc.get(_url(tid="nope")), tc.patch(_url(tid="nope"), json={"title": "x"}),
                tc.delete(_url(tid="nope"))):
        assert res.status_code == 404
        assert res.json() == {"detail": traditions_routes.NOT_FOUND}


def test_another_familys_tradition_id_is_404_through_my_family(env):
    """Alice is a member of A; B's tradition id under /families/fam_a is 404 for every verb,
    the same answer as an unknown id, and B's tradition is left exactly as it was."""
    tc, b = env["tc"], env["b_tradition"]
    unknown = [tc.get(_url(tid="nope")), tc.patch(_url(tid="nope"), json={"title": "x"}),
               tc.delete(_url(tid="nope"))]
    cross = [tc.get(_url(tid=b.id)),
             tc.patch(_url(tid=b.id), json={"title": "stolen", "startedYear": None}),
             tc.delete(_url(tid=b.id))]
    for u, c in zip(unknown, cross, strict=True):
        assert c.status_code == 404
        assert c.json() == u.json()
    assert traditions_db.get_tradition(env["db"], FAM_B, b.id) == b
    assert traditions_db.get_tradition(env["db"], FAM_A, b.id) is None
    # Even an invalid patch body naming B's people answers 404, not 400.
    assert tc.patch(_url(tid=b.id), json={"participantIds": ["nobody"]}).status_code == 404


def test_db_functions_are_family_scoped(env):
    db, b = env["db"], env["b_tradition"]
    assert traditions_db.update_tradition(db, FAM_A, b.id, schemas.UpdateTraditionRequest(title="x")) is None
    assert traditions_db.delete_tradition(db, FAM_A, b.id) is False
    assert traditions_db.get_tradition(db, FAM_B, b.id) == b


def test_router_is_mounted_in_the_app():
    assert traditions_routes.router in ROUTERS


# --------------------------------------------------------------------- purge and seed


def test_purge_family_removes_traditions_only_of_that_family(env):
    db = env["db"]
    env["tc"].post(_url(), json=_body())
    assert db.traditions.count_documents({"familyId": FAM_A}) == 1
    families_db.purge_family(db, FAM_A)
    assert db.traditions.count_documents({"familyId": FAM_A}) == 0
    assert traditions_db.list_traditions(db, FAM_B) == [env["b_tradition"]]


MOCK_PATH = REPO / "web/components/flow/mock/demoTraditions.ts"
# The web mock's person and user ids -> the API demo family's.
MOCK_PEOPLE = {"rosa": "grandma_rosa", "luis": "grandpa_luis", "maya": "lily", "dad": "dad", "mom": "mom",
               "sam": "sam"}
MOCK_USERS = {"user_david": "demo_user_david", "user_ana": "demo_user_ana"}


def _mock_traditions() -> list[dict]:
    """DEMO_TRADITIONS of the web mock, read from its array literal (plain data, one object
    per tradition, strings joined with `+`) and mapped to this family's ids."""
    text = re.sub(r'"\s*\+\s*"', "", MOCK_PATH.read_text())
    body = text[text.index("DEMO_TRADITIONS"):text.index("];")]
    out = []
    for block in body.split("\n  {\n")[1:]:
        def field(name: str, block: str = block) -> str:
            found = re.search(rf"^\s*{name}:\s*(.+?),?$", block, re.MULTILINE | re.DOTALL)
            assert found, f"no {name} in {block[:80]}"
            return found.group(1).strip().rstrip(",")

        when = field("when")
        label = re.search(r'label: "([^"]*)"', when)
        month = re.search(r"month: (\d+)", when)
        out.append({
            "id": json.loads(field("id")),
            "title": json.loads(field("title")),
            "description": json.loads(field("description")),
            "category": json.loads(field("category")),
            "when": schemas.TraditionWhen(kind=re.search(r'kind: "(\w+)"', when).group(1),
                                          label=label.group(1) if label else None,
                                          month=int(month.group(1)) if month else None),
            "participant_ids": [MOCK_PEOPLE[p] for p in json.loads(field("participantIds"))],
            "started_year": json.loads(field("startedYear")),
            "created_by": MOCK_USERS[json.loads(field("createdBy"))],
        })
    return out


def test_seed_has_the_five_demo_traditions_and_is_idempotent(mdb):
    db_client.ensure_indexes(mdb)
    first = seed_demo(mdb)
    traditions = traditions_db.list_traditions(mdb, DEMO_FAMILY_ID)
    assert len(traditions) == 5
    assert [t.id for t in traditions] == list(demo.DEMO_TRADITION_IDS) == first.tradition_ids

    people = {p.id for p in people_db.list_people(mdb, DEMO_FAMILY_ID)}
    for t in traditions:
        assert t.family_id == DEMO_FAMILY_ID
        assert t.participant_ids and set(t.participant_ids) <= people
        assert all(media_db.media_exists(mdb, DEMO_FAMILY_ID, m) for m in t.photo_ids)
        assert t.created_by in demo.DEMO_USER_IDS
    by_id = {t.id: t for t in traditions}
    assert by_id["summer_road_trip"].photo_ids == [demo.DEMO_PHOTO_IDS[1]]
    assert all(not t.photo_ids for t in traditions if t.id != "summer_road_trip")

    before = list(mdb.traditions.find({}, sort=[("_id", 1)]))
    again = seed_demo(mdb)
    assert again.created is False
    assert list(mdb.traditions.find({}, sort=[("_id", 1)])) == before

    seed_demo(mdb, reset=True)
    assert [t.id for t in traditions_db.list_traditions(mdb, DEMO_FAMILY_ID)] == list(demo.DEMO_TRADITION_IDS)
    assert mdb.traditions.count_documents({}) == 5


def test_seeded_traditions_match_the_web_mock(mdb):
    """Same ids, order, text, category, when, started year, people (mapped) and author as
    web/components/flow/mock/demoTraditions.ts."""
    if not MOCK_PATH.exists():
        pytest.skip("web mock not in this checkout")
    db_client.ensure_indexes(mdb)
    seed_demo(mdb)
    mock = _mock_traditions()
    seeded = [
        {"id": t.id, "title": t.title, "description": t.description, "category": t.category, "when": t.when,
         "participant_ids": t.participant_ids, "started_year": t.started_year, "created_by": t.created_by}
        for t in traditions_db.list_traditions(mdb, DEMO_FAMILY_ID)
    ]
    assert seeded == mock


