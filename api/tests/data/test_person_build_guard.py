"""PATCH person: a build, worn-item or walking-speed (notes) change that would break a saved scene is a 409.

Driven through `main.create_app()` with STITCH_FAKE_DB=1 (the seeded in-memory demo family),
STITCH_AGENT=fake and STITCH_FAKE_RUN_STEP=0, logged in as a demo member. No MongoDB, no
keys, no network.

The seeded demo family has two scenes a smaller build cannot play: dad 'tall' -> 'small'
can no longer hand the hymn books back up the church steps (sample_hymn_books), and
grandma_carmen 'short' -> 'small' breaks Sunday tamales. Both are out_of_reach in the real
compiler, and every later telling of those moments would fail the same way.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

import agent.runs
import main
import routes.families
import routes.memories
import schemas
from db import client as db_client
from screenplay import compiler as screenplay_compiler
from screenplay.character import speed_scale
from screenplay.schema import Screenplay
from seed import demo
from seed.fake_runs import ScriptedRunManager, compile_scene
from seed.smoke import parse_sse

FID = demo.DEMO_FAMILY_ID
ANA = next(a for a in demo.DEMO_ACCOUNTS if a.person_id == "mom")
HYMN_BOOKS = "sample_hymn_books"
TAMALES = "demo_sunday_tamales"
DEADLINE = 20.0


# ------------------------------------------------------------------------------ fixtures


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("STITCH_FAKE_DB", "1")
    monkeypatch.setenv("STITCH_AGENT", "fake")
    monkeypatch.setenv("STITCH_FAKE_RUN_STEP", "0")
    monkeypatch.delenv("STITCH_CORS_ORIGINS", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(routes.memories, "HEARTBEAT_SECONDS", 0.05)
    db_client.set_db(None)
    try:
        with TestClient(main.create_app()) as c:
            res = c.post("/auth/login", json={"email": ANA.email, "password": demo.DEMO_PASSWORD})
            assert res.status_code == 200, res.text
            yield c
    finally:
        agent.runs.configure(None)
        db_client.set_db(None)


@pytest.fixture
def compile_calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Counts the guard's compiler calls (the real compiler still runs)."""
    calls: list[str] = []
    real = screenplay_compiler.compile

    def counting(screenplay: Screenplay, **kwargs: Any):
        calls.append(screenplay.memory_id)
        return real(screenplay, **kwargs)

    monkeypatch.setattr(screenplay_compiler, "compile", counting)
    return calls


# ------------------------------------------------------------------------------ helpers


def _person(client: TestClient, person_id: str) -> dict:
    res = client.get(f"/families/{FID}/tree")
    assert res.status_code == 200, res.text
    found = [p for p in res.json()["people"] if p["id"] == person_id]
    assert len(found) == 1, person_id
    return found[0]


def _patch_sheet(client: TestClient, person_id: str, **changes: Any):
    """PATCH this person's character: the current sheet with `changes` applied (camelCase keys)."""
    sheet = {**_person(client, person_id)["character"], **changes}
    return client.patch(f"/families/{FID}/people/{person_id}", json={"character": sheet})


def _title(memory_id: str) -> str:
    doc = db_client.get_db()["memories"].find_one({"_id": memory_id, "familyId": FID}, {"title": 1})
    assert doc is not None, memory_id
    return doc["title"]


def _stored_memory(memory_id: str) -> dict:
    doc = db_client.get_db()["memories"].find_one({"_id": memory_id, "familyId": FID})
    assert doc is not None, memory_id
    return doc


def _answer_for(name: str, data: dict) -> dict | None:
    if name == "question":
        return {"kind": "answer", "text": "Just us."}
    if name == "props_found":
        return {"kind": "props", "props": [{"id": p["id"], "action": "keep"} for p in data["props"]]}
    if name == "merge":
        return {"kind": "merge", "approve": False}
    return None


def _retell(client: TestClient, memory_id: str, text: str) -> list[tuple[str, dict]]:
    """Submit a telling of an existing moment as the logged-in account; stream it to the end."""
    year = client.get(f"/families/{FID}/memories/{memory_id}").json()["memory"]["year"]
    res = client.post(f"/families/{FID}/memories", data={"text": text, "year": str(year), "memoryId": memory_id})
    assert res.status_code == 202, res.text
    submitted = schemas.SubmitMemoryResponse.model_validate(res.json())
    events: list[tuple[str, dict]] = []
    deadline = time.monotonic() + DEADLINE
    with client.stream("GET", f"/families/{FID}/runs/{submitted.run_id}/stream") as stream:
        assert stream.status_code == 200
        for name, data in parse_sse(stream.iter_lines()):
            assert time.monotonic() < deadline, "the run did not finish in time"
            events.append((name, data))
            if name in ("done", "error"):
                break
            body = _answer_for(name, data)
            if body is not None:
                reply = client.post(f"/families/{FID}/runs/{submitted.run_id}/answer", json=body)
                assert reply.status_code == 204, reply.text
    return events


# ------------------------------------------------------------------------------ the demo data


def test_the_seeded_demo_family_has_the_cases_this_file_tests(api: TestClient):
    people = {p["id"]: p for p in api.get(f"/families/{FID}/tree").json()["people"]}
    assert people["dad"]["character"]["build"] == "tall"
    assert people["grandma_carmen"]["character"]["build"] == "short"
    assert people["sam"]["character"]["itemPropId"] == "sam_cap"
    assert _title(HYMN_BOOKS) == "How I met your mother"
    assert isinstance(agent.runs.get_manager(), ScriptedRunManager)


# ------------------------------------------------------------------------------ rejected


def test_dad_cannot_shrink_out_of_the_hymn_book_scene(api: TestClient):
    before = _stored_memory(HYMN_BOOKS)
    res = _patch_sheet(api, "dad", build="small")
    assert res.status_code == 409, res.text
    detail = res.json()["detail"]
    assert "How I met your mother" in detail
    assert detail == routes.families.BUILD_BREAKS_SCENE.format(title="How I met your mother")
    for leak in ("out_of_reach", "arm_r", "reach is", "shot s1"):
        assert leak not in detail
    assert _person(api, "dad")["character"]["build"] == "tall"
    after = _stored_memory(HYMN_BOOKS)
    assert after["version"] == before["version"]
    assert after["screenplay"] == before["screenplay"]


def test_grandma_carmen_cannot_shrink_out_of_sunday_tamales(api: TestClient):
    res = _patch_sheet(api, "grandma_carmen", build="small")
    assert res.status_code == 409, res.text
    assert f'"{_title(TAMALES)}"' in res.json()["detail"]
    assert _person(api, "grandma_carmen")["character"]["build"] == "short"


def test_a_rejected_patch_writes_nothing_else_either(api: TestClient):
    """The whole patch is refused, not just the build: name and color stay as they were."""
    before = _person(api, "dad")
    res = api.patch(
        f"/families/{FID}/people/dad",
        json={"name": "Big Dave", "character": {**before["character"], "build": "small", "color": "#123456"}},
    )
    assert res.status_code == 409, res.text
    assert _person(api, "dad") == before


def test_a_rejected_patch_leaves_the_scene_retellable(api: TestClient):
    """Regression: the bug only showed on the next telling, which failed out_of_reach."""
    assert _patch_sheet(api, "dad", build="small").status_code == 409
    events = _retell(api, HYMN_BOOKS, "I remember the books going everywhere. He was so careful with them.")
    names = [name for name, _ in events]
    assert names[-1] == "done", events[-3:]
    assert "compile_error" not in names
    assert compile_scene(db_client.get_db(), FID, Screenplay.model_validate(_stored_memory(HYMN_BOOKS)["screenplay"])).ok


# ------------------------------------------------------------------------------ allowed


def test_same_build_is_a_no_op(api: TestClient):
    before = _person(api, "dad")
    res = _patch_sheet(api, "dad", build="tall")
    assert res.status_code == 200, res.text
    assert res.json() == before
    assert _person(api, "dad") == before


@pytest.mark.parametrize("item_prop_id", [None, "luis_straw_hat"])
def test_sam_can_change_the_worn_item(api: TestClient, item_prop_id: str | None):
    res = _patch_sheet(api, "sam", itemPropId=item_prop_id)
    assert res.status_code == 200, res.text
    assert res.json()["character"]["itemPropId"] == item_prop_id
    assert _person(api, "sam")["character"]["itemPropId"] == item_prop_id


def test_a_color_only_patch_compiles_nothing(api: TestClient, compile_calls: list[str]):
    res = _patch_sheet(api, "dad", color="#2a4d8f")
    assert res.status_code == 200, res.text
    assert res.json()["character"]["color"] == "#2a4d8f"
    assert res.json()["character"]["build"] == "tall"
    assert compile_calls == []


def test_name_and_years_patches_compile_nothing(api: TestClient, compile_calls: list[str]):
    res = api.patch(f"/families/{FID}/people/grandma_carmen", json={"name": "Carmen", "born": 1941})
    assert res.status_code == 200, res.text
    sheet = _person(api, "grandma_carmen")["character"]
    res = _patch_sheet(api, "grandma_carmen", item="wooden spoon", notes="hums while she cooks")
    assert res.status_code == 200, res.text
    assert res.json()["character"]["build"] == sheet["build"] == "short"
    assert compile_calls == []


def test_an_item_change_compiles_every_saved_scene_of_that_person(api: TestClient, compile_calls: list[str]):
    """Each scene twice (today, then with the new sheet); a scene without them is never compiled."""
    res = _patch_sheet(api, "sam", itemPropId="luis_straw_hat")
    assert res.status_code == 200, res.text
    with_sam = [
        d["_id"] for d in db_client.get_db()["memories"].find({"familyId": FID, "screenplay.cast": "sam"}, {"_id": 1})
    ]
    assert len(with_sam) >= 2
    assert sorted(compile_calls) == sorted(with_sam * 2)
    assert HYMN_BOOKS not in compile_calls and TAMALES not in compile_calls


def test_an_item_that_breaks_a_scene_names_the_item(api: TestClient, monkeypatch: pytest.MonkeyPatch):
    """Same build, new worn item, and the scene stops compiling: the sentence is about the item."""
    real = screenplay_compiler.compile

    def item_sensitive(screenplay: Screenplay, *, people: dict, **kwargs: Any):
        result = real(screenplay, people=people, **kwargs)
        sam = people.get("sam")
        if sam is not None and sam.character.item_prop_id == "luis_straw_hat":
            return result.model_copy(update={"ok": False, "timeline": None})
        return result

    monkeypatch.setattr(screenplay_compiler, "compile", item_sensitive)
    res = _patch_sheet(api, "sam", itemPropId="luis_straw_hat")
    assert res.status_code == 409, res.text
    detail = res.json()["detail"]
    assert "worn item" in detail and "build" not in detail
    assert detail.startswith(routes.families.ITEM_BREAKS_SCENE.split("{")[0])
    assert _person(api, "sam")["character"]["itemPropId"] == "sam_cap"


def test_a_person_in_no_saved_scene_may_take_any_build(api: TestClient, compile_calls: list[str]):
    res = api.post(
        f"/families/{FID}/people",
        json={"name": "Cousin Teo", "born": 1995, "character": {"color": "#aa5500", "build": "average"}},
    )
    assert res.status_code == 201, res.text
    teo = res.json()["id"]
    for build in ("small", "tall", "short", "average"):
        res = _patch_sheet(api, teo, build=build)
        assert res.status_code == 200, res.text
        assert res.json()["character"]["build"] == build
    assert compile_calls == []


def test_a_scene_already_broken_does_not_block_the_change(api: TestClient):
    """Move mom's mark up the steps so even tall dad cannot hand the books back: the stored
    scene fails out_of_reach today, so it must not stop dad from becoming small."""
    db = db_client.get_db()
    stored = _stored_memory(HYMN_BOOKS)["screenplay"]
    stored["shots"][0]["marks"]["steps_top"] = [0.6, 0.5]
    db["memories"].update_one({"_id": HYMN_BOOKS, "familyId": FID}, {"$set": {"screenplay": stored}})
    broken = compile_scene(db, FID, Screenplay.model_validate(stored))
    assert not broken.ok
    assert "out_of_reach" in {e.code for e in broken.errors}

    res = _patch_sheet(api, "dad", build="small")
    assert res.status_code == 200, res.text
    assert _person(api, "dad")["character"]["build"] == "small"


def test_a_scene_that_cannot_be_loaded_does_not_block_the_change(api: TestClient):
    """A stored screenplay that no longer validates is skipped, not a 500 and not a 409."""
    db = db_client.get_db()
    db["memories"].update_one({"_id": HYMN_BOOKS, "familyId": FID}, {"$set": {"screenplay.shots": "not a list"}})
    res = _patch_sheet(api, "dad", build="small")
    assert res.status_code == 200, res.text


def test_a_compiler_crash_on_the_current_scene_does_not_block(api: TestClient, monkeypatch: pytest.MonkeyPatch):
    def boom(*_args: Any, **_kwargs: Any):
        raise RuntimeError("compiler bug")

    monkeypatch.setattr(screenplay_compiler, "compile", boom)
    res = _patch_sheet(api, "dad", build="small")
    assert res.status_code == 200, res.text


def test_unknown_person_is_still_a_404(api: TestClient):
    res = api.patch(
        f"/families/{FID}/people/nobody", json={"character": {"color": "#112233", "build": "small"}}
    )
    assert res.status_code == 404, res.text


# ------------------------------------------------------------------------------ notes that change walking speed
#
# The compiler reads character.notes through screenplay.character.speed_scale: "walks slowly
# with a cane" makes a puppet walk at 0.6x, so a walk that fit its slot no longer does and the
# next action finds the person still walking. Dad (no notes, 1.0x) with a cane breaks the green
# sedan road trip in the real compiler.

GREEN_SEDAN = "demo_green_sedan_trip"
CANE = "walks slowly with a cane"


def _scenes_with(person_id: str) -> list[str]:
    return [
        d["_id"]
        for d in db_client.get_db()["memories"].find({"familyId": FID, "screenplay.cast": person_id}, {"_id": 1})
    ]


def test_dad_cannot_take_a_cane_in_a_scene_he_has_to_walk_through(api: TestClient):
    """Regression: a notes-only edit to a slower walk used to skip the guard entirely."""
    before = _person(api, "dad")
    assert speed_scale(before["character"].get("notes")) == 1.0
    assert speed_scale(CANE) < 1.0
    sedan_before = _stored_memory(GREEN_SEDAN)

    res = _patch_sheet(api, "dad", notes=CANE)
    assert res.status_code == 409, res.text
    body = res.json()
    assert set(body) == {"detail"}
    detail = body["detail"]
    titles = {_title(m) for m in _scenes_with("dad")}
    named = [t for t in titles if f'"{t}"' in detail]
    assert named == [_title(GREEN_SEDAN)], detail
    assert detail == routes.families.NOTES_BREAKS_SCENE.format(name=before["name"], title=named[0])
    assert "build" not in detail and "worn item" not in detail
    for leak in ("busy", "still walking", "speed", "0.6", "shot s"):
        assert leak not in detail
    assert _person(api, "dad") == before
    assert _person(api, "dad")["character"].get("notes") == before["character"].get("notes")
    after = _stored_memory(GREEN_SEDAN)
    assert after["version"] == sedan_before["version"]
    assert after["screenplay"] == sedan_before["screenplay"]


def test_after_a_rejected_cane_every_scene_with_dad_still_compiles_and_retells(api: TestClient):
    """The original failure: every later telling of the broken moment failed with
    'This telling could not be added to the scene'. After the 409 nothing changed, so the
    saved scenes still compile with the stored people and a second telling goes through."""
    assert _patch_sheet(api, "dad", notes=CANE).status_code == 409
    db = db_client.get_db()
    for memory_id in _scenes_with("dad"):
        result = compile_scene(db, FID, Screenplay.model_validate(_stored_memory(memory_id)["screenplay"]))
        assert result.ok, (memory_id, [e.code for e in result.errors])

    events = _retell(api, HYMN_BOOKS, "Dad told it slower, like he always does. He never dropped a single book.")
    names = [name for name, _ in events]
    assert names[-1] == "done", events[-3:]
    assert "compile_error" not in names
    for name, data in events:
        if name == "error":
            raise AssertionError(data)
        assert "could not be added" not in str(data)


@pytest.mark.parametrize(
    ("person_id", "notes"),
    [
        ("dad", "loves gardening"),  # None -> no speed words, still 1.0x
        ("lily", "always a step ahead, quick to laugh"),  # brisk -> quick, still 1.25x
        ("grandpa_joseph", "never without his cane at the market"),  # slowly + cane -> cane, still 0.6x
        ("sam", None),  # bounces on his toes -> cleared, still 1.0x
    ],
)
def test_notes_that_keep_the_walking_speed_compile_nothing(
    api: TestClient, compile_calls: list[str], person_id: str, notes: str | None
):
    before = _person(api, person_id)
    assert speed_scale(notes) == speed_scale(before["character"].get("notes"))
    res = _patch_sheet(api, person_id, notes=notes)
    assert res.status_code == 200, res.text
    assert res.json()["character"].get("notes") == notes
    assert _person(api, person_id)["character"].get("notes") == notes
    assert compile_calls == []


def test_a_faster_walk_runs_the_guard_on_every_scene_of_that_person(api: TestClient, compile_calls: list[str]):
    """Faster is a speed change too: the guard compiles each scene with dad twice (today, then
    with the new sheet) and lets the compiler decide. It accepts this one."""
    assert speed_scale("walks briskly") > 1.0
    with_dad = _scenes_with("dad")
    assert len(with_dad) >= 2
    res = _patch_sheet(api, "dad", notes="walks briskly")
    assert res.status_code == 200, res.text
    assert res.json()["character"]["notes"] == "walks briskly"
    assert sorted(compile_calls) == sorted(with_dad * 2)
    assert TAMALES not in compile_calls  # a scene without dad is never compiled


def test_a_faster_walk_that_breaks_a_scene_is_refused_by_the_guard(api: TestClient, monkeypatch: pytest.MonkeyPatch):
    """The guard, not a speed rule, decides: when the compiler rejects the faster dad, so does the patch."""
    real = screenplay_compiler.compile

    def speed_sensitive(screenplay: Screenplay, *, people: dict, **kwargs: Any):
        result = real(screenplay, people=people, **kwargs)
        dad = people.get("dad")
        if screenplay.memory_id == HYMN_BOOKS and dad is not None and speed_scale(dad.character.notes) > 1.0:
            return result.model_copy(update={"ok": False, "timeline": None})
        return result

    monkeypatch.setattr(screenplay_compiler, "compile", speed_sensitive)
    before = _person(api, "dad")
    res = _patch_sheet(api, "dad", notes="walks briskly")
    assert res.status_code == 409, res.text
    assert res.json()["detail"] == routes.families.NOTES_BREAKS_SCENE.format(
        name=before["name"], title=_title(HYMN_BOOKS)
    )
    assert _person(api, "dad") == before


def test_a_build_change_wins_the_message_over_a_notes_change(api: TestClient):
    """Both changed and the scene breaks: the sentence names the build, the first cause."""
    before = _person(api, "dad")
    res = _patch_sheet(api, "dad", build="small", notes=CANE)
    assert res.status_code == 409, res.text
    detail = res.json()["detail"]
    assert detail.startswith(routes.families.BUILD_BREAKS_SCENE.split("{")[0]), detail
    assert _person(api, "dad") == before


def test_an_item_change_wins_the_message_over_a_notes_change(api: TestClient, monkeypatch: pytest.MonkeyPatch):
    real = screenplay_compiler.compile

    def item_sensitive(screenplay: Screenplay, *, people: dict, **kwargs: Any):
        result = real(screenplay, people=people, **kwargs)
        sam = people.get("sam")
        if sam is not None and sam.character.item_prop_id == "luis_straw_hat":
            return result.model_copy(update={"ok": False, "timeline": None})
        return result

    monkeypatch.setattr(screenplay_compiler, "compile", item_sensitive)
    res = _patch_sheet(api, "sam", itemPropId="luis_straw_hat", notes="walks slowly")
    assert res.status_code == 409, res.text
    assert res.json()["detail"].startswith(routes.families.ITEM_BREAKS_SCENE.split("{")[0])
    assert _person(api, "sam")["character"]["itemPropId"] == "sam_cap"


def test_the_notes_message_is_plain_language():
    msg = routes.families.NOTES_BREAKS_SCENE
    assert "{title}" in msg and "{name}" in msg
    text = msg.format(name="Dave", title="The road trip in the green sedan")
    assert text.endswith("Keep the current notes.")
    assert '"The road trip in the green sedan"' in text and "Dave" in text


# ------------------------------------------------------------------------------ docs


def test_409_is_documented_with_error_response(api: TestClient):
    op = api.get("/openapi.json").json()["paths"]["/families/{fid}/people/{person_id}"]["patch"]
    assert op["responses"]["409"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ErrorResponse"
    }
    assert op["responses"]["200"]["content"]["application/json"]["schema"] == {"$ref": "#/components/schemas/Person"}
    assert "notes" in op["responses"]["409"]["description"]
