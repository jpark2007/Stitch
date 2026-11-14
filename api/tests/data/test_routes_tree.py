"""routes/tree.py: tree, book, memory, threads and media reads, scoped to the caller's family."""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import auth
import schemas
from db import families as families_db
from db import media as media_db
from db import memories as memories_db
from db import people as people_db
from db import props as props_db
from db import users as users_db
from db.client import get_db
from routes import tree as tree_routes

API_DIR = Path(__file__).resolve().parents[2]
REPO = API_DIR.parent
SAMPLE = json.loads((REPO / "web" / "public" / "samples" / "hymn_books.memory.json").read_text())

FAM_A = "famA"
FAM_B = "famB"
PNG = b"\x89PNG\r\n\x1a\n" + bytes(range(32))


# ------------------------------------------------------------------------------- fixtures


def _sample_prop() -> schemas.Prop:
    return schemas.Prop.model_validate(SAMPLE["props"][0])


def _sample_set() -> schemas.SetDef:
    return schemas.SetDef.model_validate(SAMPLE["sets"][0])


@pytest.fixture
def library(tmp_path, monkeypatch):
    """One built-in library prop, 'lantern', in a temporary library directory."""
    lib = tmp_path / "props"
    lib.mkdir()
    (lib / "lantern.json").write_text(
        json.dumps({"name": "lantern", "size": "hand", "art": {"body": None}, "color": "#e0b84f", "source": "library"})
    )
    monkeypatch.setattr(props_db, "LIBRARY_DIR", lib)
    props_db.library_props.cache_clear()
    yield lib
    props_db.library_props.cache_clear()


@dataclass
class World:
    client: TestClient
    alice: schemas.User  # admin of family A
    bea: schemas.User  # member of family A
    carl: schemas.User  # admin of family B
    stranger: schemas.User  # in no family
    david: schemas.Person
    ana: schemas.Person
    mia: schemas.Person
    m_steps: schemas.Memory  # 1988
    m_kitchen: schemas.Memory  # 1975
    cane_id: str
    hymn_id: str
    set_id: str
    media_id: str
    b_person: schemas.Person
    b_memory: schemas.Memory
    b_media_id: str


def _user(mdb, email: str, name: str) -> schemas.User:
    return users_db.create_user(mdb, email=email, password_hash=auth.hash_password("pw-12345678"), name=name)


@pytest.fixture
def world(mdb, library) -> World:
    alice = _user(mdb, "alice@x.com", "Alice")
    bea = _user(mdb, "bea@x.com", "Bea")
    carl = _user(mdb, "carl@x.com", "Carl")
    stranger = _user(mdb, "stranger@x.com", "Stranger")

    families_db.create_family(mdb, name="The Parks", creator_user_id=alice.id, family_id=FAM_A)
    families_db.add_member(mdb, FAM_A, bea.id, "member")
    families_db.create_family(mdb, name="The Others", creator_user_id=carl.id, family_id=FAM_B)

    # Family A's saved prop and set (from the hymn-book sample), plus a prop worn as a signature item.
    hymn = props_db.insert_prop(mdb, FAM_A, _sample_prop(), prop_id="hymn_books", embedding=[0.1, 0.2])
    steps = props_db.insert_set(mdb, FAM_A, _sample_set(), set_id="church_steps_1988")
    cane = props_db.insert_prop(
        mdb,
        FAM_A,
        schemas.Prop(id="x", name="walking cane", size="hand", art={"body": None}),
        prop_id="cane",
    )

    # Two generations: David and Ana (married), their daughter Mia.
    david = people_db.create_person(
        mdb,
        FAM_A,
        schemas.NewPerson(
            name="David",
            born=1940,
            character=schemas.CharacterSheet(color="#3f6fb5", item="walking cane", item_prop_id=cane.id),
        ),
        added_by=alice.id,
        user_id=alice.id,
    )
    ana = people_db.create_person(
        mdb, FAM_A, schemas.NewPerson(name="Ana", born=1942, spouse_ids=[david.id]), added_by=alice.id
    )
    mia = people_db.create_person(
        mdb,
        FAM_A,
        schemas.NewPerson(name="Mia", born=1970, parent_ids=[david.id, ana.id]),
        added_by=alice.id,
        user_id=bea.id,
    )

    # 1988 is inserted first so the book has to sort by year. Ana tells it but is not in it.
    m_steps = memories_db.create_memory(
        mdb,
        FAM_A,
        title="Hymn books on the church steps",
        year=1988,
        participant_ids=[david.id, mia.id],
        telling=schemas.Telling(author_id=david.id, text="She dropped every hymn book on the steps."),
        prop_ids=[hymn.id, "no_such_prop"],
        set_ids=[steps.id],
        memory_id="m_steps",
    )
    m_steps = memories_db.add_telling(
        mdb, FAM_A, m_steps.id, schemas.Telling(author_id=ana.id, text="I remember it differently.")
    )
    memories_db.set_embedding(mdb, FAM_A, m_steps.id, [0.3, 0.4])
    m_kitchen = memories_db.create_memory(
        mdb,
        FAM_A,
        title="The kitchen lantern",
        year=1975,
        participant_ids=[mia.id, ana.id],
        telling=schemas.Telling(author_id=mia.id, text="Mom lit the lantern when the power went out."),
        prop_ids=["lantern", hymn.id],
        set_ids=[steps.id, "no_such_set"],
        memory_id="m_kitchen",
    )

    media_id = media_db.put_media(
        mdb, FAM_A, PNG, content_type="image/png", filename='Ana "steps" — 1988.png', kind="photo"
    )

    # Family B, with ids family A must never reach.
    b_person = people_db.create_person(mdb, FAM_B, schemas.NewPerson(name="Bo", born=1950), added_by=carl.id)
    b_memory = memories_db.create_memory(
        mdb,
        FAM_B,
        title="Family B's secret",
        year=1960,
        participant_ids=[b_person.id],
        telling=schemas.Telling(author_id=b_person.id, text="private"),
        memory_id="m_b",
    )
    b_media_id = media_db.put_media(mdb, FAM_B, b"secret-bytes", content_type="image/jpeg")

    app = FastAPI()
    app.include_router(tree_routes.router)
    app.dependency_overrides[get_db] = lambda: mdb
    client = TestClient(app)
    return World(
        client=client,
        alice=alice,
        bea=bea,
        carl=carl,
        stranger=stranger,
        david=david,
        ana=ana,
        mia=mia,
        m_steps=m_steps,
        m_kitchen=m_kitchen,
        cane_id=cane.id,
        hymn_id=hymn.id,
        set_id=steps.id,
        media_id=media_id,
        b_person=b_person,
        b_memory=b_memory,
        b_media_id=b_media_id,
    )


def _as(world: World, user: schemas.User) -> TestClient:
    world.client.cookies.set(auth.COOKIE_NAME, auth.create_session_token(user.id))
    return world.client


def _walk_keys(value) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, dict):
        for k, v in value.items():
            keys.add(k)
            keys |= _walk_keys(v)
    elif isinstance(value, list):
        for v in value:
            keys |= _walk_keys(v)
    return keys


def _assert_clean_camel(body) -> None:
    keys = _walk_keys(body)
    assert not {k for k in keys if "_" in k}, f"snake_case keys leaked: {sorted(k for k in keys if '_' in k)}"
    assert "passwordHash" not in keys
    assert "embedding" not in keys


# ------------------------------------------------------------------------------- router shape


def test_router_exports_the_five_reads():
    routes = {(route.path, tuple(sorted(route.methods))): route for route in tree_routes.router.routes}
    expected = {
        "/families/{fid}/tree": schemas.TreeResponse,
        "/families/{fid}/people/{person_id}/book": schemas.BookResponse,
        "/families/{fid}/memories/{memory_id}": schemas.MemoryResponse,
        "/families/{fid}/threads": schemas.ThreadsResponse,
    }
    for path, model in expected.items():
        assert (path, ("GET",)) in routes, path
        assert routes[(path, ("GET",))].response_model is model
    assert ("/families/{fid}/media/{media_id:path}", ("GET",)) in routes
    for route in tree_routes.router.routes:
        deps = [d.call for d in route.dependant.dependencies]
        assert auth.family_member in deps, route.path
    assert tree_routes.router.tags == ["tree"]


# ------------------------------------------------------------------------------- tree


def test_tree_order_and_family_name(world):
    resp = _as(world, world.bea).get(f"/families/{FAM_A}/tree")
    assert resp.status_code == 200
    body = resp.json()
    assert body["familyId"] == FAM_A
    assert body["familyName"] == "The Parks"
    assert [p["name"] for p in body["people"]] == ["David", "Ana", "Mia"]
    mia = body["people"][2]
    assert mia["parentIds"] == [world.david.id, world.ana.id]
    assert body["people"][0]["spouseIds"] == [world.ana.id]
    assert body["people"][0]["character"]["itemPropId"] == world.cane_id
    _assert_clean_camel(body)


# ------------------------------------------------------------------------------- book


def test_book_orders_by_year_and_bundles_everything_once(world):
    resp = _as(world, world.alice).get(f"/families/{FAM_A}/people/{world.mia.id}/book")
    assert resp.status_code == 200
    body = resp.json()
    assert body["person"]["id"] == world.mia.id
    assert [m["id"] for m in body["memories"]] == ["m_kitchen", "m_steps"]
    assert [m["year"] for m in body["memories"]] == [1975, 1988]

    # Props: memory props in first-seen order (library included, the missing id skipped),
    # then the signature item worn by David.
    assert [p["id"] for p in body["props"]] == ["lantern", "hymn_books", "cane"]
    lantern = body["props"][0]
    assert lantern["familyId"] is None and lantern["source"] == "library"
    assert body["props"][1]["familyId"] == FAM_A

    # Sets: deduped across memories, the missing id skipped.
    assert [s["id"] for s in body["sets"]] == ["church_steps_1988"]
    assert body["sets"][0]["marks"]["steps_foot"] == [0.5, 0.0]

    # People: participants and tellers of both memories, once each, first-seen order.
    assert [p["id"] for p in body["people"]] == [world.mia.id, world.ana.id, world.david.id]
    _assert_clean_camel_except_marks(body)


def _assert_clean_camel_except_marks(body) -> None:
    """Set mark names are data ids (e.g. steps_foot), not field names; check everything else."""
    stripped = json.loads(json.dumps(body))
    for set_def in stripped.get("sets", []):
        set_def["marks"] = {}
    _assert_clean_camel(stripped)


def test_book_for_person_without_memories_still_has_their_props(world, mdb):
    resp = _as(world, world.alice).get(f"/families/{FAM_A}/people/{world.david.id}/book")
    assert resp.status_code == 200
    body = resp.json()
    assert [m["id"] for m in body["memories"]] == ["m_steps"]

    lonely = people_db.create_person(mdb, FAM_A, schemas.NewPerson(name="Zed"), added_by=world.alice.id)
    resp = world.client.get(f"/families/{FAM_A}/people/{lonely.id}/book")
    assert resp.status_code == 200
    body = resp.json()
    assert body["memories"] == [] and body["props"] == [] and body["sets"] == []
    assert [p["id"] for p in body["people"]] == [lonely.id]


def test_book_unknown_person_404(world):
    resp = _as(world, world.alice).get(f"/families/{FAM_A}/people/nobody/book")
    assert resp.status_code == 404
    assert resp.json() == {"detail": "Not found"}


# ------------------------------------------------------------------------------- memory


def test_memory_returns_tellings_and_every_teller(world):
    resp = _as(world, world.bea).get(f"/families/{FAM_A}/memories/m_steps")
    assert resp.status_code == 200
    body = resp.json()
    memory = body["memory"]
    assert memory["id"] == "m_steps" and memory["familyId"] == FAM_A
    assert memory["era"] is not None
    assert [t["authorId"] for t in memory["tellings"]] == [world.david.id, world.ana.id]
    assert memory["tellings"][1]["text"] == "I remember it differently."
    # Ana told it without being in it; she is still in the people bundle.
    assert [p["id"] for p in body["people"]] == [world.david.id, world.mia.id, world.ana.id]
    assert [p["id"] for p in body["props"]] == ["hymn_books", "cane"]
    assert [s["id"] for s in body["sets"]] == ["church_steps_1988"]
    _assert_clean_camel_except_marks(body)


def test_memory_unknown_404(world):
    resp = _as(world, world.alice).get(f"/families/{FAM_A}/memories/nope")
    assert resp.status_code == 404


def test_scene_bundle_dedupes_in_first_seen_order(world, mdb):
    memories = [world.m_steps, world.m_kitchen, world.m_steps]
    props, sets, people = tree_routes._scene_bundle(mdb, FAM_A, memories, extra_person_ids=[world.mia.id])
    assert [p.id for p in props] == ["hymn_books", "lantern", "cane"]
    assert [s.id for s in sets] == ["church_steps_1988"]
    assert [p.id for p in people] == [world.david.id, world.mia.id, world.ana.id]
    assert tree_routes._scene_bundle(mdb, FAM_A, []) == ([], [], [])


# ------------------------------------------------------------------------------- threads


def test_threads_people_colors_and_moments_by_year(world):
    resp = _as(world, world.bea).get(f"/families/{FAM_A}/threads")
    assert resp.status_code == 200
    body = resp.json()
    people = body["people"]
    assert [p["name"] for p in people] == ["David", "Ana", "Mia"]
    assert set(people[0]) == {"id", "name", "born", "color"}
    assert people[0]["color"] == "#3f6fb5"
    assert all(isinstance(p["color"], str) and p["color"].startswith("#") for p in people)
    assert len({p["color"] for p in people}) == 3

    moments = body["moments"]
    assert [m["id"] for m in moments] == ["m_kitchen", "m_steps"]
    assert [m["year"] for m in moments] == [1975, 1988]
    assert moments[1]["participantIds"] == [world.david.id, world.mia.id]
    assert moments[1]["tellingAuthorIds"] == [world.david.id, world.ana.id]
    assert "tellings" not in moments[0] and "screenplay" not in moments[0]
    _assert_clean_camel(body)


# ------------------------------------------------------------------------------- media


def test_media_full_file(world):
    resp = _as(world, world.bea).get(f"/families/{FAM_A}/media/{world.media_id}")
    assert resp.status_code == 200
    assert resp.content == PNG
    h = resp.headers
    assert h["content-type"] == "image/png"
    assert h["content-length"] == str(len(PNG))
    assert h["cache-control"] == "private, max-age=86400"
    assert h["etag"] == f'"{world.media_id}"'
    assert h["x-content-type-options"] == "nosniff"
    assert h["accept-ranges"] == "bytes"
    cd = h["content-disposition"]
    assert cd.startswith('inline; filename="Ana _steps_ _ 1988.png"')
    assert "filename*=UTF-8''Ana%20%22steps%22%20%E2%80%94%201988.png" in cd


def test_media_range_prefix(world):
    resp = _as(world, world.bea).get(
        f"/families/{FAM_A}/media/{world.media_id}", headers={"Range": "bytes=0-3"}
    )
    assert resp.status_code == 206
    assert resp.content == PNG[:4]
    assert resp.headers["content-length"] == "4"
    assert resp.headers["content-range"] == f"bytes 0-3/{len(PNG)}"
    assert resp.headers["content-type"] == "image/png"


def test_media_range_suffix_and_open_end(world):
    client = _as(world, world.bea)
    url = f"/families/{FAM_A}/media/{world.media_id}"
    n = len(PNG)

    resp = client.get(url, headers={"Range": "bytes=-2"})
    assert resp.status_code == 206
    assert resp.content == PNG[-2:]
    assert resp.headers["content-range"] == f"bytes {n - 2}-{n - 1}/{n}"

    resp = client.get(url, headers={"Range": "bytes=10-"})
    assert resp.status_code == 206
    assert resp.content == PNG[10:]
    assert resp.headers["content-range"] == f"bytes 10-{n - 1}/{n}"

    resp = client.get(url, headers={"Range": "bytes=-1000"})  # longer than the file: all of it
    assert resp.status_code == 206 and resp.content == PNG

    resp = client.get(url, headers={"Range": "bytes=5-99999"})  # end clamps to the file
    assert resp.status_code == 206 and resp.content == PNG[5:]
    assert resp.headers["content-range"] == f"bytes 5-{n - 1}/{n}"


def test_media_unsatisfiable_range(world):
    client = _as(world, world.bea)
    url = f"/families/{FAM_A}/media/{world.media_id}"
    for header in (f"bytes={len(PNG)}-", f"bytes={len(PNG) + 10}-{len(PNG) + 20}", "bytes=-0"):
        resp = client.get(url, headers={"Range": header})
        assert resp.status_code == 416, header
        assert resp.headers["content-range"] == f"bytes */{len(PNG)}"
        assert resp.content == b""


@pytest.mark.parametrize("header", ["bytes=0-1,4-5", "bytes=abc", "items=0-3", "bytes=5-2", "bytes=-", "0-3"])
def test_media_malformed_or_multi_range_sends_full(world, header):
    resp = _as(world, world.bea).get(f"/families/{FAM_A}/media/{world.media_id}", headers={"Range": header})
    assert resp.status_code == 200
    assert resp.content == PNG


def test_media_if_range_mismatch_sends_full(world):
    client = _as(world, world.bea)
    url = f"/families/{FAM_A}/media/{world.media_id}"
    resp = client.get(url, headers={"Range": "bytes=0-3", "If-Range": '"stale"'})
    assert resp.status_code == 200 and resp.content == PNG
    resp = client.get(url, headers={"Range": "bytes=0-3", "If-Range": f'"{world.media_id}"'})
    assert resp.status_code == 206 and resp.content == PNG[:4]


def test_media_if_none_match_304(world):
    client = _as(world, world.bea)
    url = f"/families/{FAM_A}/media/{world.media_id}"
    etag = client.get(url).headers["etag"]
    for header in (etag, f"W/{etag}", f'"other", {etag}', "*"):
        resp = client.get(url, headers={"If-None-Match": header})
        assert resp.status_code == 304, header
        assert resp.content == b""
        assert resp.headers["etag"] == etag
    resp = client.get(url, headers={"If-None-Match": '"something-else"'})
    assert resp.status_code == 200 and resp.content == PNG


def test_media_encoded_ref_prefix_is_tolerated(world):
    client = _as(world, world.bea)
    resp = client.get(f"/families/{FAM_A}/media/media%2F{world.media_id}")
    assert resp.status_code == 200 and resp.content == PNG
    # The literal, unencoded prefix is a different path and is not served.
    assert client.get(f"/families/{FAM_A}/media/media/{world.media_id}").status_code == 404


@pytest.mark.parametrize("bad", ["garbage", "0" * 24, "media%2Fgarbage", "a/b", "%20"])
def test_media_garbage_id_404(world, bad):
    resp = _as(world, world.bea).get(f"/families/{FAM_A}/media/{bad}")
    assert resp.status_code == 404
    assert resp.json() == {"detail": "Not found"}


def test_media_without_filename_is_plain_inline(world, mdb):
    mid = media_db.put_media(mdb, FAM_A, b"ID3audio", content_type="audio/mpeg", kind="audio")
    resp = _as(world, world.bea).get(f"/families/{FAM_A}/media/{mid}")
    assert resp.status_code == 200
    assert resp.headers["content-disposition"] == "inline"
    assert resp.headers["content-type"] == "audio/mpeg"


def test_content_disposition_is_header_safe():
    value = tree_routes._content_disposition('evil"\r\nSet-Cookie: x=1\\..png')
    assert "\r" not in value and "\n" not in value
    assert value.startswith('inline; filename="evil_Set-Cookie: x=1_..png"')
    assert tree_routes._content_disposition("") == "inline"
    assert tree_routes._content_disposition("\x00\x01") == "inline"
    assert tree_routes._content_disposition("plain.png") == 'inline; filename="plain.png"'


# ------------------------------------------------------------------------------- access


def _family_routes(fid: str, person_id: str, memory_id: str, media_id: str) -> list[str]:
    return [
        f"/families/{fid}/tree",
        f"/families/{fid}/people/{person_id}/book",
        f"/families/{fid}/memories/{memory_id}",
        f"/families/{fid}/threads",
        f"/families/{fid}/media/{media_id}",
    ]


def test_member_of_a_gets_404_for_family_b_on_every_route(world):
    client = _as(world, world.alice)
    for url in _family_routes(FAM_B, world.b_person.id, world.b_memory.id, world.b_media_id):
        resp = client.get(url)
        assert resp.status_code == 404, url
        assert resp.json() == {"detail": "Not found"}, url
    # Family B's own admin can read the same urls, so the 404 is purely the family check.
    client = _as(world, world.carl)
    for url in _family_routes(FAM_B, world.b_person.id, world.b_memory.id, world.b_media_id):
        assert client.get(url).status_code == 200, url


def test_family_b_ids_under_family_a_are_404(world):
    client = _as(world, world.alice)
    for url in (
        f"/families/{FAM_A}/people/{world.b_person.id}/book",
        f"/families/{FAM_A}/memories/{world.b_memory.id}",
        f"/families/{FAM_A}/media/{world.b_media_id}",
        f"/families/{FAM_A}/media/media%2F{world.b_media_id}",
    ):
        resp = client.get(url)
        assert resp.status_code == 404, url
        assert resp.json() == {"detail": "Not found"}, url
        assert b"secret" not in resp.content and b"private" not in resp.content


def test_unknown_family_and_non_member_are_404(world):
    client = _as(world, world.stranger)
    for url in _family_routes(FAM_A, world.mia.id, world.m_steps.id, world.media_id):
        assert client.get(url).status_code == 404, url
    for url in _family_routes("no-such-family", "p", "m", world.media_id):
        assert client.get(url).status_code == 404, url


def test_signed_out_gets_401_on_every_route(world):
    world.client.cookies.clear()
    for url in _family_routes(FAM_A, world.mia.id, world.m_steps.id, world.media_id):
        resp = world.client.get(url)
        assert resp.status_code == 401, url
        assert resp.json() == {"detail": "Not signed in"}
    world.client.cookies.set(auth.COOKIE_NAME, "not-a-token")
    assert world.client.get(f"/families/{FAM_A}/tree").status_code == 401


def test_no_response_contains_password_hash(world):
    client = _as(world, world.alice)
    for url in _family_routes(FAM_A, world.mia.id, world.m_steps.id, world.media_id)[:4]:
        resp = client.get(url)
        assert resp.status_code == 200
        assert "passwordHash" not in resp.text and "$argon2" not in resp.text


# ------------------------------------------------------------------------------- imports


def test_importing_routes_tree_never_imports_agent():
    code = (
        "import sys\n"
        "import routes.tree\n"
        "leaked = sorted(m for m in sys.modules if m == 'agent' or m.startswith('agent.'))\n"
        "assert not leaked, leaked\n"
        "print('ok')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=API_DIR, capture_output=True, text=True, timeout=120
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"
