"""No response from any route carries a secret (ARCHITECTURE.md sections 4 and 7).

Drives EVERY route of the full app once on its happy path, as a real family would use it
(sign up, start a family, invite, join by adding and by claiming, add and edit people,
upload, browse, submit a memory, stream it, answer it, remove a member, log out), and
checks every response body and header for a password hash or a plaintext password.

A coverage guard compares the routes exercised with the routes the app serves, so a new
route fails here until it is driven too. Runs on mongomock with a fake agent seam.
"""

from __future__ import annotations

import importlib
import json
import pkgutil
import re
import sys
import types
import uuid
from typing import Any

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.routing import APIRoute, iter_route_contexts
from fastapi.testclient import TestClient

import routes
import schemas
from db import client as db_client
from db import memories as memories_db
from db import props as props_db
from routes import memories as memories_routes

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32

PASSWORDS = {
    "alice": "Alice-plaintext-pw-1!",
    "bob": "Bob-plaintext-pw-2!",
    "carol": "Carol-plaintext-pw-3!",
}
SHORT_PASSWORD = "Zq9!x"  # under the 8-character minimum, and in no error message
FORBIDDEN = ("passwordhash", "password_hash", "$argon2")


# ------------------------------------------------------------------------------ the app


def _full_app() -> FastAPI:
    """Every router in the `routes` package, included with no prefix.

    Built without main.py on purpose (main.py is wired in parallel); wave 3 switches this
    to `main.app`.
    """
    app = FastAPI()
    for info in sorted(pkgutil.iter_modules(routes.__path__), key=lambda m: m.name):
        module = importlib.import_module(f"routes.{info.name}")
        router = getattr(module, "router", None)
        if isinstance(router, APIRouter):
            app.include_router(router)
    return app


def _app_routes(app: FastAPI) -> set[tuple[str, str]]:
    """Every (METHOD, path template) the app serves through an APIRoute (docs routes excluded)."""
    found: set[tuple[str, str]] = set()
    for ctx in iter_route_contexts(app.routes):
        route = ctx.original_route
        if not isinstance(route, APIRoute):
            continue
        path = ctx.path or route.path
        for method in route.methods or ():
            if method != "HEAD":
                found.add((method, path))
    return found


def _coverage_problems(app: FastAPI, exercised: set[tuple[str, str]]) -> list[str]:
    served = _app_routes(app)
    return [f"route never exercised: {r}" for r in sorted(served - exercised)] + [
        f"exercised a route the app does not serve: {r}" for r in sorted(exercised - served)
    ]


_PARAM = re.compile(r"\{(\w+)(?::\w+)?\}")


def _fill(template: str, params: dict[str, str]) -> str:
    """'/families/{fid}/media/{media_id:path}' with the params substituted."""
    return _PARAM.sub(lambda m: params[m.group(1)], template)


# ------------------------------------------------------------------------------ secrets check


def _secrets_in(res, passwords: dict[str, str] = PASSWORDS) -> list[str]:
    """Every forbidden string found in the response body or headers (case-insensitive)."""
    blob = res.content.decode("utf-8", errors="replace")
    blob += "\n" + "\n".join(f"{k}: {v}" for k, v in res.headers.multi_items())
    blob = blob.lower()
    needles = [*FORBIDDEN, *(pw.lower() for pw in passwords.values())]
    return [needle for needle in needles if needle in blob]


# ------------------------------------------------------------------------------ fake agent seam


class FakeRuns:
    """Stand-ins for agent.runs.start_run / stream_run / answer_run, with family-bound runs."""

    def __init__(self) -> None:
        self.runs: dict[str, tuple[str, str]] = {}  # run_id -> (family_id, user_id)
        self.answers: list[tuple] = []

    def start_run(self, family_id, user_id, person_id, fields, photo_ids) -> str:
        run_id = f"run-{uuid.uuid4().hex[:12]}"
        self.runs[run_id] = (family_id, user_id)
        return run_id

    def _owner(self, family_id: str, run_id: str) -> tuple[str, str]:
        owner = self.runs.get(run_id)
        if owner is None or owner[0] != family_id:
            raise LookupError(run_id)
        return owner

    def stream_run(self, family_id, run_id):
        self._owner(family_id, run_id)
        return self._events(run_id)

    async def _events(self, run_id: str):
        yield "plan", schemas.PlanEvent(todos=[schemas.PlanItem(text="Read the telling")])
        yield "done", schemas.DoneEvent(memory_id=f"memory-of-{run_id}")

    def answer_run(self, family_id, run_id, user_id, answer) -> None:
        _, owner_user = self._owner(family_id, run_id)
        if owner_user != user_id:
            raise PermissionError(run_id)
        self.answers.append((family_id, run_id, user_id, answer))

    def module(self) -> types.ModuleType:
        mod = types.ModuleType("agent.runs")
        mod.start_run = self.start_run
        mod.stream_run = self.stream_run
        mod.answer_run = self.answer_run
        return mod


# ------------------------------------------------------------------------------ fixtures


class Driver:
    """Sends requests by route template, checks each response for secrets, records coverage."""

    def __init__(self, app: FastAPI) -> None:
        self.app = app
        self.exercised: set[tuple[str, str]] = set()
        self.clients: list[TestClient] = []

    def client(self) -> TestClient:
        tc = TestClient(self.app)
        self.clients.append(tc)
        return tc

    def call(
        self,
        tc: TestClient,
        method: str,
        template: str,
        expect: int,
        params: dict[str, str] | None = None,
        **kwargs: Any,
    ):
        url = _fill(template, params or {})
        res = tc.request(method, url, **kwargs)
        assert res.status_code == expect, f"{method} {url} -> {res.status_code}: {res.text}"
        leaked = _secrets_in(res)
        assert leaked == [], f"{method} {template} leaked {leaked}: {res.text[:500]}"
        self.exercised.add((method, template))
        return res


@pytest.fixture
def env(mdb, monkeypatch):
    fake = FakeRuns()
    monkeypatch.setitem(sys.modules, "agent.runs", fake.module())
    monkeypatch.setattr(memories_routes, "HEARTBEAT_SECONDS", 5.0)
    db_client.ensure_indexes(mdb)
    db_client.set_db(mdb)
    driver = Driver(_full_app())
    try:
        yield types.SimpleNamespace(db=mdb, fake=fake, driver=driver)
        for tc in driver.clients:
            tc.close()
    finally:
        db_client.set_db(None)


# ------------------------------------------------------------------------------ the drive


def _signup(d: Driver, tc: TestClient, who: str) -> dict:
    res = d.call(
        tc,
        "POST",
        "/auth/signup",
        201,
        json={"email": f"{who}@example.com", "password": PASSWORDS[who], "name": who.title()},
    )
    return res.json()


def test_every_route_on_its_happy_path_leaks_no_secret(env):
    d: Driver = env.driver
    db = env.db
    served = _app_routes(d.app)

    alice, bob, carol, anon = d.client(), d.client(), d.client(), d.client()

    if ("GET", "/health") in served:  # served once the app is main.app
        d.call(anon, "GET", "/health", 200)

    # Accounts.
    alice_user = _signup(d, alice, "alice")["user"]
    d.call(alice, "POST", "/auth/login", 200, json={"email": "alice@example.com", "password": PASSWORDS["alice"]})
    assert d.call(alice, "GET", "/me", 200).json()["user"]["id"] == alice_user["id"]
    stored = db.users.find_one({"_id": alice_user["id"]})
    assert stored["passwordHash"].startswith("$argon2"), "the check below needs a real hash in the db"

    # A family, an invite, and the admin's view of the family (with invites).
    created = d.call(alice, "POST", "/families", 201, json={"name": "Park", "person": {"name": "Alice"}}).json()
    fid = created["family"]["id"]
    alice_pid = created["person"]["id"]
    p = {"fid": fid}
    code = d.call(alice, "POST", "/families/{fid}/invites", 201, params=p).json()["code"]
    family = d.call(alice, "GET", "/families/{fid}", 200, params=p).json()
    assert [i["code"] for i in family["invites"]] == [code]

    # People without accounts, then one edited.
    grandma = d.call(
        alice, "POST", "/families/{fid}/people", 201, params=p, json={"name": "Grandma", "born": 1930}
    ).json()
    uncle = d.call(
        alice, "POST", "/families/{fid}/people", 201, params=p, json={"name": "Uncle", "parentIds": [grandma["id"]]}
    ).json()
    d.call(
        alice,
        "PATCH",
        "/families/{fid}/people/{person_id}",
        200,
        params={**p, "person_id": grandma["id"]},
        json={"died": 2010, "character": {"color": "#7a2f4a", "item": "green hymn book"}},
    )

    # Joining: preview, add yourself (Bob), claim an existing person (Carol).
    preview = d.call(anon, "GET", "/invites/{code}", 200, params={"code": code}).json()
    assert {u["id"] for u in preview["unclaimedPeople"]} >= {grandma["id"], uncle["id"]}
    bob_user = _signup(d, bob, "bob")["user"]
    d.call(
        bob,
        "POST",
        "/invites/{code}/join",
        200,
        params={"code": code},
        json={"person": {"name": "Bob", "parentIds": [alice_pid]}},
    )
    _signup(d, carol, "carol")
    d.call(carol, "POST", "/invites/{code}/join", 200, params={"code": code}, json={"personId": uncle["id"]})

    # Content made by the agent in real life, stored directly here: a saved prop and set, a
    # memory with a telling that uses them, and an open question for Alice.
    prop = props_db.insert_prop(
        db,
        fid,
        schemas.Prop(id="x", name="hymn books", size="hand", art={"body": None}, color="#2f4a7a"),
        prop_id="hymn_books_park",
    )
    set_def = props_db.insert_set(
        db,
        fid,
        schemas.SetDef(id="x", name="church steps", layers=[schemas.SetLayer(art=None, depth=0, height=1.0)]),
        set_id="church_steps_park",
    )
    memory = memories_db.create_memory(
        db,
        fid,
        title="Hymn books",
        year=1962,
        participant_ids=[grandma["id"], alice_pid],
        telling=schemas.Telling(author_id=alice_pid, text="We sang from the green hymn books every Sunday."),
        prop_ids=[prop.id],
        set_ids=[set_def.id],
    )
    memories_db.create_question(db, fid, person_id=alice_pid, text="What did Sundays sound like?", memory_id=memory.id)

    # Media.
    media_id = d.call(
        alice, "POST", "/families/{fid}/media", 201, params=p, files={"file": ("books.png", PNG, "image/png")}
    ).json()["id"]
    got = d.call(alice, "GET", "/families/{fid}/media/{media_id:path}", 200, params={**p, "media_id": media_id})
    assert got.content == PNG

    # Reading.
    d.call(alice, "GET", "/families/{fid}/tree", 200, params=p)
    d.call(alice, "GET", "/families/{fid}/threads", 200, params=p)
    book = d.call(alice, "GET", "/families/{fid}/people/{person_id}/book", 200, params={**p, "person_id": alice_pid})
    assert [m["id"] for m in book.json()["memories"]] == [memory.id]
    d.call(alice, "GET", "/families/{fid}/memories/{memory_id}", 200, params={**p, "memory_id": memory.id})
    assert [x["id"] for x in d.call(alice, "GET", "/families/{fid}/props", 200, params=p).json()["props"]] == [prop.id]
    assert [x["id"] for x in d.call(alice, "GET", "/families/{fid}/sets", 200, params=p).json()["sets"]] == [set_def.id]
    d.call(
        alice,
        "PATCH",
        "/families/{fid}/props/{prop_id}",
        200,
        params={**p, "prop_id": prop.id},
        json={"name": "green hymn books", "photoId": media_id},
    )
    assert len(d.call(alice, "GET", "/families/{fid}/questions", 200, params=p).json()["questions"]) == 1
    d.call(alice, "POST", "/families/{fid}/ask", 200, params=p, json={"query": "hymn books"})

    # A memory run: submit with a photo, stream it, answer it.
    run_id = d.call(
        alice,
        "POST",
        "/families/{fid}/memories",
        202,
        params=p,
        data={"text": "Grandma dropped the hymn books on the steps.", "year": "1988"},
        files=[("photos", ("steps.png", PNG + b"steps", "image/png"))],
    ).json()["runId"]
    stream = d.call(alice, "GET", "/families/{fid}/runs/{run_id}/stream", 200, params={**p, "run_id": run_id})
    assert "event: plan" in stream.text and "event: done" in stream.text
    d.call(
        alice,
        "POST",
        "/families/{fid}/runs/{run_id}/answer",
        204,
        params={**p, "run_id": run_id},
        json={"kind": "answer", "text": "Sunday mornings in 1988"},
    )
    assert len(env.fake.answers) == 1

    # Admin housekeeping, then sign out.
    d.call(alice, "DELETE", "/families/{fid}/invites/{code}", 204, params={**p, "code": code})
    d.call(alice, "DELETE", "/families/{fid}/members/{user_id}", 204, params={**p, "user_id": bob_user["id"]})
    d.call(alice, "POST", "/auth/logout", 204)

    assert _coverage_problems(d.app, d.exercised) == []


# ------------------------------------------------------------------------------ coverage guard


def test_coverage_guard_flags_a_route_that_is_not_driven():
    app = _full_app()
    extra = APIRouter()

    @extra.get("/families/{fid}/new-thing")
    def new_thing(fid: str) -> dict[str, str]:  # pragma: no cover - never called
        return {}

    app.include_router(extra)
    exercised = _app_routes(_full_app())
    assert _coverage_problems(app, exercised) == ["route never exercised: ('GET', '/families/{fid}/new-thing')"]
    assert _coverage_problems(FastAPI(), {("GET", "/me")}) == [
        "exercised a route the app does not serve: ('GET', '/me')"
    ]


def test_fill_handles_path_convertors():
    assert _fill("/families/{fid}/media/{media_id:path}", {"fid": "f1", "media_id": "m1"}) == "/families/f1/media/m1"


def test_secrets_check_catches_each_forbidden_string():
    class Res:
        def __init__(self, body: str, headers: dict[str, str] | None = None) -> None:
            self.content = body.encode()
            self.headers = types.SimpleNamespace(multi_items=lambda: list((headers or {}).items()))

    assert _secrets_in(Res('{"passwordHash": "x"}')) == ["passwordhash"]
    assert _secrets_in(Res('{"password_hash": "x"}')) == ["password_hash"]
    assert _secrets_in(Res('{"h": "$ARGON2id$v=19$..."}')) == ["$argon2"]
    assert _secrets_in(Res(json.dumps({"input": PASSWORDS["bob"].upper()}))) == [PASSWORDS["bob"].lower()]
    assert _secrets_in(Res("{}", {"X-Debug": PASSWORDS["carol"]})) == [PASSWORDS["carol"].lower()]
    assert _secrets_in(Res('{"ok": true}')) == []


# ------------------------------------------------------------------------------ error paths

# FastAPI's default 422 handler echoes the offending request input back, so a malformed
# signup or login body returns the submitted plaintext password. Routers cannot install
# exception handlers; the fix is a RequestValidationError handler in api/main.py that drops
# or redacts `input`. These cases XPASS (and so fail, being strict) once _full_app() is
# switched to main.app with that handler in place; remove the marker then.
ECHOES_PASSWORD = pytest.mark.xfail(
    strict=True,
    reason="Leak: FastAPI's default 422 body echoes the request input, including the plaintext password. "
    "Needs a redacting RequestValidationError handler in api/main.py.",
)


@pytest.mark.parametrize(
    ("body", "path"),
    [
        pytest.param(
            {"email": "dup@example.com", "password": PASSWORDS["alice"], "name": "Dup"},
            "/auth/signup",
            id="signup-taken",
        ),
        pytest.param(
            {"email": "dup@example.com", "password": PASSWORDS["bob"]}, "/auth/login", id="login-wrong-password"
        ),
        pytest.param({"email": "nobody@example.com", "password": PASSWORDS["bob"]}, "/auth/login", id="login-unknown"),
        pytest.param(
            {"email": "x@example.com", "password": PASSWORDS["bob"], "name": " "},
            "/auth/signup",
            id="signup-blank-name",
        ),
        pytest.param(
            {"email": "x@example.com", "password": SHORT_PASSWORD, "name": "X"},
            "/auth/signup",
            id="signup-short-password",
            marks=ECHOES_PASSWORD,
        ),
        pytest.param({"password": PASSWORDS["bob"]}, "/auth/login", id="login-missing-email", marks=ECHOES_PASSWORD),
        pytest.param(
            {"email": "x@example.com", "password": PASSWORDS["bob"]},
            "/auth/signup",
            id="signup-missing-name",
            marks=ECHOES_PASSWORD,
        ),
    ],
)
def test_auth_error_responses_leak_no_secret(env, body, path):
    d: Driver = env.driver
    tc = d.client()
    first = tc.post("/auth/signup", json={"email": "dup@example.com", "password": PASSWORDS["alice"], "name": "Dup"})
    assert first.status_code == 201, first.text
    passwords = {**PASSWORDS, "short": SHORT_PASSWORD}

    res = tc.post(path, json=body)

    assert 400 <= res.status_code < 500, res.text
    assert _secrets_in(res, passwords) == [], res.text
