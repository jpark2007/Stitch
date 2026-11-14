"""Full-app family isolation: another family's ids are a 404 on every family-scoped route.

ARCHITECTURE.md section 4 "Access control" (every family-scoped route depends on
`family_member`; another family's id is a 404, never a 403) and section 7 "Rules that keep
it clean" (a family-scoped route without that check is a bug).

The app under test is the real one, `main.create_app()`, so a router wired into main.py
is covered the moment it exists. Its lifespan is never entered (TestClient without `with`):
the db is set with `db.client.set_db(mdb)`, so no fake-db install or index setup runs.

The coverage guard forces every route the app serves (APIRoutes and plain Starlette routes
alike) to be classified: family-scoped routes (path contains `{fid}`) must have a request
builder in CASES, and every other route must be on the UNSCOPED_REQUIRED allowlist or be
one of FastAPI's documentation routes (DOC_ROUTES).

Runs on mongomock. The agent seam (`agent.runs`) is a fake module in sys.modules.
"""

from __future__ import annotations

import re
import sys
import types
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.routing import iter_route_contexts
from fastapi.testclient import TestClient

import auth
import main
import schemas
from db import client as db_client
from db import families as families_db
from db import media as media_db
from db import memories as memories_db
from db import people as people_db
from db import props as props_db
from db import users as users_db
from routes import memories as memories_routes

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


# ------------------------------------------------------------------------------ the app


def _full_app() -> FastAPI:
    """The real app, exactly as uvicorn serves it. Its lifespan is never entered here."""
    return main.create_app()


def _app_routes(app: FastAPI) -> set[tuple[str, str]]:
    """Every (METHOD, path template) the app serves, APIRoute or not (docs routes included).

    A route without methods (a websocket or a mount) is listed as ("ANY", path), so it still
    has to be classified.
    """
    found: set[tuple[str, str]] = set()
    for ctx in iter_route_contexts(app.routes):
        route = ctx.original_route
        path = ctx.path or getattr(route, "path", "")
        methods = getattr(route, "methods", None) or {"ANY"}
        for method in methods:
            if method != "HEAD":
                found.add((method, path))
    return found


# Routes that are allowed to have no `{fid}`: the health check, the account routes,
# starting a family, and the invite routes (the code itself is the secret). Anything else
# needs a family check. The app must serve every one of these.
UNSCOPED_REQUIRED: frozenset[tuple[str, str]] = frozenset(
    {
        ("GET", "/health"),
        ("POST", "/auth/signup"),
        ("POST", "/auth/login"),
        ("POST", "/auth/logout"),
        ("GET", "/me"),
        ("POST", "/families"),
        ("GET", "/invites/{code}"),
        ("POST", "/invites/{code}/join"),
    }
)
# FastAPI's own documentation routes: no family data, allowed but not required (they go
# away if the app is built with docs_url=None and friends).
DOC_ROUTES: frozenset[tuple[str, str]] = frozenset(
    {
        ("GET", "/openapi.json"),
        ("GET", "/docs"),
        ("GET", "/docs/oauth2-redirect"),
        ("GET", "/redoc"),
    }
)


def _guard_problems(app: FastAPI, cases: Iterable[tuple[str, str]]) -> list[str]:
    """Why the app's routes and the classification disagree. [] when every route is classified."""
    found = _app_routes(app)
    scoped = {r for r in found if "{fid}" in r[1]}
    unscoped = found - scoped
    classified = set(cases)
    problems: list[str] = []
    problems += [f"family-scoped route missing from CASES: {r}" for r in sorted(scoped - classified)]
    problems += [f"CASES entry the app does not serve: {r}" for r in sorted(classified - scoped)]
    problems += [
        f"route without {{fid}} is not on the allowlist: {r}" for r in sorted(unscoped - UNSCOPED_REQUIRED - DOC_ROUTES)
    ]
    problems += [f"allowlisted route the app does not serve: {r}" for r in sorted(UNSCOPED_REQUIRED - unscoped)]
    return problems


# ------------------------------------------------------------------------------ fake agent seam


class FakeRuns:
    """Stand-ins for agent.runs.start_run / stream_run / answer_run, with family-bound runs."""

    def __init__(self) -> None:
        self.runs: dict[str, tuple[str, str]] = {}  # run_id -> (family_id, user_id)
        self.calls: list[tuple] = []

    def start_run(self, family_id, user_id, person_id, fields, photo_ids) -> str:
        run_id = f"run-{uuid.uuid4().hex[:12]}"
        self.runs[run_id] = (family_id, user_id)
        self.calls.append(("start", family_id, user_id, person_id, run_id))
        return run_id

    def _owner(self, family_id: str, run_id: str) -> tuple[str, str]:
        owner = self.runs.get(run_id)
        if owner is None or owner[0] != family_id:
            raise LookupError(run_id)
        return owner

    def stream_run(self, family_id, run_id):
        self.calls.append(("stream", family_id, run_id))
        self._owner(family_id, run_id)
        return self._events(run_id)

    async def _events(self, run_id: str):
        yield "plan", schemas.PlanEvent(todos=[schemas.PlanItem(text="Read the telling")])
        yield "done", schemas.DoneEvent(memory_id=f"memory-of-{run_id}")

    def answer_run(self, family_id, run_id, user_id, answer) -> None:
        self.calls.append(("answer", family_id, run_id, user_id))
        _, owner_user = self._owner(family_id, run_id)
        if owner_user != user_id:
            raise PermissionError(run_id)

    def module(self) -> types.ModuleType:
        mod = types.ModuleType("agent.runs")
        mod.start_run = self.start_run
        mod.stream_run = self.stream_run
        mod.answer_run = self.answer_run
        return mod


# ------------------------------------------------------------------------------ the world


@dataclass
class FamilyWorld:
    fid: str
    admin: schemas.User
    member: schemas.User
    admin_person: schemas.Person
    member_person: schemas.Person
    elder: schemas.Person  # no account
    memory_id: str
    prop_id: str
    set_id: str
    media_id: str
    invite_code: str
    question_id: str
    run_id: str


def _build_family(db, fake: FakeRuns, tag: str) -> FamilyWorld:
    admin = users_db.create_user(db, email=f"admin-{tag}@example.com", password_hash="unused", name=f"Admin {tag}")
    member = users_db.create_user(db, email=f"member-{tag}@example.com", password_hash="unused", name=f"Member {tag}")
    family = families_db.create_family(db, name=f"Family {tag}", creator_user_id=admin.id)
    fid = family.id
    families_db.add_member(db, fid, member.id, "member")

    admin_p = people_db.create_person(
        db, fid, schemas.NewPerson(name=f"Admin {tag}"), added_by=admin.id, user_id=admin.id
    )
    member_p = people_db.create_person(
        db, fid, schemas.NewPerson(name=f"Member {tag}", parent_ids=[admin_p.id]), added_by=admin.id, user_id=member.id
    )
    elder = people_db.create_person(db, fid, schemas.NewPerson(name=f"Grandma {tag}", born=1930), added_by=admin.id)

    prop = props_db.insert_prop(
        db,
        fid,
        schemas.Prop(id="x", name=f"hymn books {tag}", size="hand", art={"body": None}, color="#2f4a7a"),
        prop_id=f"prop_{tag}",
    )
    set_def = props_db.insert_set(
        db,
        fid,
        schemas.SetDef(
            id="x", name=f"church steps {tag}", layers=[schemas.SetLayer(art=None, depth=0, height=1.0, color="#ccc")]
        ),
        set_id=f"set_{tag}",
    )
    media_id = media_db.put_media(db, fid, PNG + tag.encode(), content_type="image/png", filename=f"{tag}.png")
    memory = memories_db.create_memory(
        db,
        fid,
        title=f"Hymn books {tag}",
        year=1962,
        participant_ids=[elder.id, admin_p.id],
        telling=schemas.Telling(author_id=admin_p.id, text=f"We sang from the green hymn books ({tag})."),
        prop_ids=[prop.id],
        set_ids=[set_def.id],
    )
    invite = families_db.create_invite(db, fid, admin.id)
    assert invite is not None
    question = memories_db.create_question(
        db, fid, person_id=member_p.id, text=f"What did Sundays sound like? ({tag})", memory_id=memory.id
    )
    run_id = fake.start_run(fid, admin.id, admin_p.id, schemas.SubmitMemoryFields(text="hi", year=1962), [])
    return FamilyWorld(
        fid=fid,
        admin=admin,
        member=member,
        admin_person=admin_p,
        member_person=member_p,
        elder=elder,
        memory_id=memory.id,
        prop_id=prop.id,
        set_id=set_def.id,
        media_id=media_id,
        invite_code=invite.code,
        question_id=question.id,
        run_id=run_id,
    )


class Env(types.SimpleNamespace):
    pass


@pytest.fixture
def env(mdb, monkeypatch) -> Env:
    fake = FakeRuns()
    monkeypatch.setitem(sys.modules, "agent.runs", fake.module())
    monkeypatch.setattr(memories_routes, "HEARTBEAT_SECONDS", 5.0)
    db_client.ensure_indexes(mdb)
    db_client.set_db(mdb)
    try:
        a = _build_family(mdb, fake, "a")
        b = _build_family(mdb, fake, "b")
        fake.calls.clear()
        app = _full_app()
        clients: list[TestClient] = []

        def client_for(user: schemas.User | None, token: str | None = None) -> TestClient:
            tc = TestClient(app)
            if user is not None:
                tc.cookies.set(auth.COOKIE_NAME, auth.create_session_token(user.id))
            elif token is not None:
                tc.cookies.set(auth.COOKIE_NAME, token)
            clients.append(tc)
            return tc

        yield Env(db=mdb, fake=fake, app=app, a=a, b=b, client_for=client_for)
        for tc in clients:
            tc.close()
    finally:
        db_client.set_db(None)


# ------------------------------------------------------------------------------ snapshots


def _sorted(docs: Iterable[dict]) -> list[dict]:
    return sorted(docs, key=lambda d: str(d["_id"]))


def _db_snapshot(db) -> dict[str, list[dict]]:
    """Every document in every collection."""
    return {name: _sorted(db[name].find()) for name in sorted(db.list_collection_names())}


def _family_snapshot(db, fid: str) -> dict[str, Any]:
    """One family's data: family doc, members, invites, people, memories, props, sets, media, questions."""
    family = db.families.find_one({"_id": fid})
    return {
        "family": family,
        "members": (family or {}).get("members"),
        "invites": (family or {}).get("invites"),
        "people": _sorted(db.people.find({"familyId": fid})),
        "memories": _sorted(db.memories.find({"familyId": fid})),
        "props": _sorted(db[props_db.PROPS].find({"familyId": fid})),
        "sets": _sorted(db[props_db.SETS].find({"familyId": fid})),
        "media": sorted(str(d["_id"]) for d in db[media_db.INLINE].find({"metadata.familyId": fid}, {"_id": 1})),
        "questions": _sorted(db.questions.find({"familyId": fid})),
    }


def _state(env: Env) -> dict[str, Any]:
    return {
        "a": _family_snapshot(env.db, env.a.fid),
        "b": _family_snapshot(env.db, env.b.fid),
        "db": _db_snapshot(env.db),
    }


def _assert_unchanged(env: Env, before: dict[str, Any]) -> None:
    after = _state(env)
    assert after["b"] == before["b"], "family B's data changed"
    assert after["a"] == before["a"], "family A's data changed"
    assert after["db"] == before["db"], "the database changed"


# ------------------------------------------------------------------------------ request builders

Builder = Callable[[str, FamilyWorld], dict[str, Any]]


def _photo_files() -> list[tuple[str, tuple[str, bytes, str]]]:
    return [("photos", ("photo.png", PNG + b"upload", "image/png"))]


# (METHOD, route path) -> request kwargs from (fid, that family's ids). Every body is valid,
# so a 404 can only come from the family check, not from request validation.
CASES: dict[tuple[str, str], Builder] = {
    ("GET", "/families/{fid}"): lambda f, w: {"url": f"/families/{f}"},
    ("POST", "/families/{fid}/invites"): lambda f, w: {"url": f"/families/{f}/invites"},
    ("DELETE", "/families/{fid}/invites/{code}"): lambda f, w: {"url": f"/families/{f}/invites/{w.invite_code}"},
    ("POST", "/families/{fid}/people"): lambda f, w: {"url": f"/families/{f}/people", "json": {"name": "Mallory"}},
    ("PATCH", "/families/{fid}/people/{person_id}"): lambda f, w: {
        "url": f"/families/{f}/people/{w.elder.id}",
        "json": {"name": "Renamed by an outsider"},
    },
    ("DELETE", "/families/{fid}/members/{user_id}"): lambda f, w: {"url": f"/families/{f}/members/{w.member.id}"},
    ("GET", "/families/{fid}/tree"): lambda f, w: {"url": f"/families/{f}/tree"},
    ("GET", "/families/{fid}/threads"): lambda f, w: {"url": f"/families/{f}/threads"},
    ("GET", "/families/{fid}/people/{person_id}/book"): lambda f, w: {"url": f"/families/{f}/people/{w.elder.id}/book"},
    ("GET", "/families/{fid}/memories/{memory_id}"): lambda f, w: {"url": f"/families/{f}/memories/{w.memory_id}"},
    ("GET", "/families/{fid}/media/{media_id:path}"): lambda f, w: {"url": f"/families/{f}/media/{w.media_id}"},
    ("GET", "/families/{fid}/props"): lambda f, w: {"url": f"/families/{f}/props"},
    ("GET", "/families/{fid}/sets"): lambda f, w: {"url": f"/families/{f}/sets"},
    ("PATCH", "/families/{fid}/props/{prop_id}"): lambda f, w: {
        "url": f"/families/{f}/props/{w.prop_id}",
        "json": {"name": "stolen books"},
    },
    ("POST", "/families/{fid}/media"): lambda f, w: {
        "url": f"/families/{f}/media",
        "files": {"file": ("photo.png", PNG + b"upload", "image/png")},
    },
    ("POST", "/families/{fid}/memories"): lambda f, w: {
        "url": f"/families/{f}/memories",
        "data": {"text": "An outsider's story", "year": "1990"},
        "files": _photo_files(),
    },
    ("GET", "/families/{fid}/runs/{run_id}/stream"): lambda f, w: {"url": f"/families/{f}/runs/{w.run_id}/stream"},
    ("POST", "/families/{fid}/runs/{run_id}/answer"): lambda f, w: {
        "url": f"/families/{f}/runs/{w.run_id}/answer",
        "json": {"kind": "answer", "text": "yes"},
    },
    ("GET", "/families/{fid}/questions"): lambda f, w: {"url": f"/families/{f}/questions"},
    ("POST", "/families/{fid}/ask"): lambda f, w: {"url": f"/families/{f}/ask", "json": {"query": "hymn books"}},
}


def _send(tc: TestClient, method: str, kwargs: dict[str, Any]):
    return tc.request(method, **kwargs)


def _assert_404(res) -> None:
    assert res.status_code == 404, res.text
    body = res.json()
    assert isinstance(body.get("detail"), str) and body["detail"], body


# ------------------------------------------------------------------------------ 1. coverage guard


def test_every_route_is_classified():
    assert _guard_problems(_full_app(), CASES) == []


def test_the_app_under_test_is_main_with_health_and_docs():
    """The guard sees the real app: /health and FastAPI's doc routes included."""
    served = _app_routes(_full_app())
    assert ("GET", "/health") in served
    assert DOC_ROUTES <= served
    assert {r for r in served if r[1].startswith("/families/{fid}")} == set(CASES)


def test_health_and_doc_routes_are_not_family_data(env):
    tc = env.client_for(env.a.admin)
    assert tc.get("/health").json() == {"status": "ok"}
    for _, path in sorted(DOC_ROUTES):
        res = tc.get(path)
        assert res.status_code == 200, (path, res.text[:200])
        assert env.b.fid not in res.text and env.a.fid not in res.text


def test_guard_flags_an_unclassified_family_route_and_an_unlisted_open_route():
    app = _full_app()
    extra = APIRouter()

    @extra.get("/families/{fid}/secrets")
    def secrets(fid: str) -> dict[str, str]:  # pragma: no cover - never called
        return {}

    @extra.post("/public/thing")
    def public_thing() -> dict[str, str]:  # pragma: no cover - never called
        return {}

    app.include_router(extra)

    async def raw(request):  # pragma: no cover - never called
        return None

    app.add_route("/families/{fid}/raw", raw, methods=["GET"])  # a plain Starlette route
    assert _guard_problems(app, CASES) == [
        "family-scoped route missing from CASES: ('GET', '/families/{fid}/raw')",
        "family-scoped route missing from CASES: ('GET', '/families/{fid}/secrets')",
        "route without {fid} is not on the allowlist: ('POST', '/public/thing')",
    ]


def test_guard_flags_a_stale_case_and_a_missing_allowlisted_route():
    app = FastAPI()  # serves nothing, so every case and allowlist entry is stale
    problems = _guard_problems(app, {("GET", "/families/{fid}/tree")})
    assert "CASES entry the app does not serve: ('GET', '/families/{fid}/tree')" in problems
    assert "allowlisted route the app does not serve: ('GET', '/me')" in problems
    # Dropping a real route from CASES is caught too.
    partial = {k: v for k, v in CASES.items() if k != ("GET", "/families/{fid}/tree")}
    assert _guard_problems(_full_app(), partial) == [
        "family-scoped route missing from CASES: ('GET', '/families/{fid}/tree')"
    ]


def test_guard_counts_a_family_route_under_another_param_name_as_unlisted():
    app = FastAPI()
    extra = APIRouter()

    @extra.get("/families/{family_id}/leak")
    def leak(family_id: str) -> dict[str, str]:  # pragma: no cover - never called
        return {}

    app.include_router(extra)
    assert "route without {fid} is not on the allowlist: ('GET', '/families/{family_id}/leak')" in _guard_problems(
        app, ()
    )


def test_world_is_not_empty(env):
    """The snapshots below are only meaningful if family B really has data of every kind."""
    snap = _family_snapshot(env.db, env.b.fid)
    assert len(snap["members"]) == 2
    assert len(snap["invites"]) == 1
    assert len(snap["people"]) == 3
    assert len(snap["memories"]) == 1 and snap["memories"][0]["tellings"]
    assert [p["_id"] for p in snap["props"]] == [env.b.prop_id]
    assert [s["_id"] for s in snap["sets"]] == [env.b.set_id]
    assert snap["media"] == [env.b.media_id]
    assert len(snap["questions"]) == 1
    assert env.fake.runs[env.b.run_id] == (env.b.fid, env.b.admin.id)


# ------------------------------------------------------------------------------ 2. another family's fid


@pytest.mark.parametrize("role", ["admin", "member"])
@pytest.mark.parametrize("case", sorted(CASES), ids=lambda c: f"{c[0]} {c[1]}")
def test_family_b_fid_is_404_for_family_a(env, case, role):
    method, _ = case
    caller = env.a.admin if role == "admin" else env.a.member
    tc = env.client_for(caller)
    before = _state(env)

    res = _send(tc, method, CASES[case](env.b.fid, env.b))

    _assert_404(res)
    _assert_unchanged(env, before)
    assert env.fake.calls == [], "the agent seam was reached for another family"


# ------------------------------------------------------------------------------ 3. another family's object id


@dataclass(frozen=True)
class SecondId:
    name: str
    method: str
    path: str
    url: Callable[[FamilyWorld, FamilyWorld], str]  # (fid's family, object's family) -> url
    extra: Callable[[], dict[str, Any]] = dict
    ok: int = 200  # status for the same request with the caller's own object


SECOND_ID_CASES: list[SecondId] = [
    SecondId(
        "patch person",
        "PATCH",
        "/families/{fid}/people/{person_id}",
        lambda f, o: f"/families/{f.fid}/people/{o.elder.id}",
        lambda: {"json": {"name": "Renamed"}},
    ),
    SecondId(
        "book",
        "GET",
        "/families/{fid}/people/{person_id}/book",
        lambda f, o: f"/families/{f.fid}/people/{o.elder.id}/book",
    ),
    SecondId(
        "memory",
        "GET",
        "/families/{fid}/memories/{memory_id}",
        lambda f, o: f"/families/{f.fid}/memories/{o.memory_id}",
    ),
    SecondId(
        "media bare id",
        "GET",
        "/families/{fid}/media/{media_id:path}",
        lambda f, o: f"/families/{f.fid}/media/{o.media_id}",
    ),
    SecondId(
        "media ref form",
        "GET",
        "/families/{fid}/media/{media_id:path}",
        lambda f, o: f"/families/{f.fid}/media/media%2F{o.media_id}",
    ),
    SecondId(
        "patch prop",
        "PATCH",
        "/families/{fid}/props/{prop_id}",
        lambda f, o: f"/families/{f.fid}/props/{o.prop_id}",
        lambda: {"json": {"name": "renamed"}},
    ),
    SecondId(
        "revoke invite",
        "DELETE",
        "/families/{fid}/invites/{code}",
        lambda f, o: f"/families/{f.fid}/invites/{o.invite_code}",
        ok=204,
    ),
    SecondId(
        "remove member",
        "DELETE",
        "/families/{fid}/members/{user_id}",
        lambda f, o: f"/families/{f.fid}/members/{o.member.id}",
        ok=204,
    ),
    SecondId(
        "run stream",
        "GET",
        "/families/{fid}/runs/{run_id}/stream",
        lambda f, o: f"/families/{f.fid}/runs/{o.run_id}/stream",
    ),
    SecondId(
        "run answer",
        "POST",
        "/families/{fid}/runs/{run_id}/answer",
        lambda f, o: f"/families/{f.fid}/runs/{o.run_id}/answer",
        lambda: {"json": {"kind": "answer", "text": "yes"}},
        ok=204,
    ),
]


def test_second_id_cases_cover_every_route_with_a_second_id():
    """Every family-scoped route the app serves with a second path id has a SECOND_ID case."""
    served = _app_routes(_full_app())
    with_second_id = {r for r in served if "{fid}" in r[1] and len(re.findall(r"\{", r[1])) >= 2}
    assert with_second_id, "expected routes with a second id"
    assert {(c.method, c.path) for c in SECOND_ID_CASES} == with_second_id


@pytest.mark.parametrize("case", SECOND_ID_CASES, ids=lambda c: c.name)
def test_family_b_object_under_family_a_fid_is_404(env, case):
    tc = env.client_for(env.a.admin)  # admin, so admin-only routes reach their lookup
    before = _state(env)

    res = tc.request(case.method, case.url(env.a, env.b), **case.extra())

    _assert_404(res)
    _assert_unchanged(env, before)
    # The seam, when reached, was only ever asked about family A.
    assert all(call[1] == env.a.fid for call in env.fake.calls), env.fake.calls

    # Control: the same request for A's own object is not a 404, so the 404 above came
    # from the family scoping and not from a route that always fails.
    own = tc.request(case.method, case.url(env.a, env.a), **case.extra())
    assert own.status_code == case.ok, own.text


# ------------------------------------------------------------------------------ 4. cross-family references in bodies


@dataclass(frozen=True)
class CrossRef:
    name: str
    method: str
    url: Callable[[Env], str]
    body: Callable[[Env], dict[str, Any]]
    status: int


CROSS_REF_CASES: list[CrossRef] = [
    CrossRef(
        "memory: memoryId of B's memory",
        "POST",
        lambda e: f"/families/{e.a.fid}/memories",
        lambda e: {
            "data": {"text": "Second telling", "year": "1962", "memoryId": e.b.memory_id},
            "files": _photo_files(),
        },
        404,
    ),
    CrossRef(
        "memory: participantIds of B's people",
        "POST",
        lambda e: f"/families/{e.a.fid}/memories",
        lambda e: {
            "data": {"text": "With them", "year": "1962", "participantIds": [e.b.elder.id, e.b.admin_person.id]},
            "files": _photo_files(),
        },
        400,
    ),
    CrossRef(
        "memory: participantIds mixing A's and B's people",
        "POST",
        lambda e: f"/families/{e.a.fid}/memories",
        lambda e: {"data": {"text": "With them", "year": "1962", "participantIds": [e.a.elder.id, e.b.elder.id]}},
        400,
    ),
    CrossRef(
        "add person: parent in B",
        "POST",
        lambda e: f"/families/{e.a.fid}/people",
        lambda e: {"json": {"name": "Kid", "parentIds": [e.b.elder.id]}},
        400,
    ),
    CrossRef(
        "add person: spouse in B",
        "POST",
        lambda e: f"/families/{e.a.fid}/people",
        lambda e: {"json": {"name": "Partner", "spouseIds": [e.b.elder.id]}},
        400,
    ),
    CrossRef(
        "patch person: parent in B",
        "PATCH",
        lambda e: f"/families/{e.a.fid}/people/{e.a.elder.id}",
        lambda e: {"json": {"parentIds": [e.b.elder.id]}},
        400,
    ),
    CrossRef(
        "patch person: spouse in B",
        "PATCH",
        lambda e: f"/families/{e.a.fid}/people/{e.a.elder.id}",
        lambda e: {"json": {"spouseIds": [e.b.elder.id]}},
        400,
    ),
    CrossRef(
        "patch prop: photoId of B's media",
        "PATCH",
        lambda e: f"/families/{e.a.fid}/props/{e.a.prop_id}",
        lambda e: {"json": {"photoId": e.b.media_id}},
        400,
    ),
    CrossRef(
        "patch prop: photoId of B's media, ref form",
        "PATCH",
        lambda e: f"/families/{e.a.fid}/props/{e.a.prop_id}",
        lambda e: {"json": {"photoId": f"media/{e.b.media_id}"}},
        400,
    ),
]


@pytest.mark.parametrize("case", CROSS_REF_CASES, ids=lambda c: c.name)
def test_cross_family_reference_in_body_does_not_succeed(env, case):
    tc = env.client_for(env.a.admin)
    before = _state(env)

    res = tc.request(case.method, case.url(env), **case.body(env))

    assert not 200 <= res.status_code < 300, res.text
    assert res.status_code == case.status, res.text
    assert isinstance(res.json().get("detail"), str)
    _assert_unchanged(env, before)
    assert env.fake.calls == [], "the agent seam was reached with another family's ids"


# ------------------------------------------------------------------------------ 5. signed out


@pytest.mark.parametrize("session", ["none", "unknown-user", "garbage"])
@pytest.mark.parametrize("case", sorted(CASES), ids=lambda c: f"{c[0]} {c[1]}")
def test_signed_out_is_401(env, case, session):
    method, _ = case
    if session == "none":
        tc = env.client_for(None)
    elif session == "unknown-user":
        tc = env.client_for(None, token=auth.create_session_token("no-such-user"))
    else:
        tc = env.client_for(None, token="not-a-jwt")
    before = _state(env)

    res = _send(tc, method, CASES[case](env.a.fid, env.a))

    assert res.status_code == 401, res.text
    assert isinstance(res.json().get("detail"), str)
    _assert_unchanged(env, before)
    assert env.fake.calls == []
