"""A new scripted memory tells THIS family's story on the page, not the sample's (seed/fake_runs.py).

The scripted director stages the hymn-book sample for every new memory. Its storybook text
("One Sunday in 1988, a stack of hymn books changed everything.") and its explanations ("Dad
was 26...", "Mom sang in the choir...") used to survive the recast, so a stranger's family got
another family's story with their own people's ids on it. `recast_prose` now writes each
shot's page text from the telling and one explanation per cast member and prop (DESIGN.md:
a short storybook text per shot, a "why this matters" per person and prop).

Covered: a fresh family made over HTTP (two signups, one family, an invite join as the
teller's spouse, a prop rename in the review) and the demo family (Ana tells "Lily was born
in a snowstorm..." with Lily and David named), plus `recast_prose` itself. STITCH_FAKE_DB app
or mongomock; no MongoDB, no keys, no network.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

import agent.runs
import main
import routes.memories
import schemas
from db import client as db_client
from db import memories as memories_db
from db import people as people_db
from db import props as props_db
from screenplay.schema import Explanation, Screenplay
from seed import demo
from seed.fake_runs import (
    EXPLANATION_CHARS,
    PAGE_TEXT_WORDS,
    ScriptedRunManager,
    recast_prose,
    short_title,
    split_sentences,
)
from seed.smoke import parse_sse

FID = demo.DEMO_FAMILY_ID
ANA = next(a for a in demo.DEMO_ACCOUNTS if a.person_id == "mom")
DEADLINE = 20.0
PASSWORD = "prose-test-password-9"
SAMPLE_PROSE = ("Dad was 26", "choir", "1988", "One Sunday")
FOLLOWUP_REPLY = "Just the two of us, and the neighbors' dog."

FRESH_TEXT = (
    "We planted a lemon tree behind our first little house on a windy afternoon in early spring. "
    "Theo dug the hole and I held the tree straight. It rained right after, so we sat on the porch "
    "and watched the puddles grow."
)
LILY_TEXT = (
    "Lily was born in a snowstorm, the worst one the town had seen in twenty years. "
    "David drove us to the hospital at ten miles an hour. She was tiny and loud and perfect."
)


# ------------------------------------------------------------------------------ checks


def _all_prose(memory: schemas.Memory) -> list[str]:
    sp, tl = memory.screenplay, memory.timeline
    assert sp is not None and tl is not None
    texts = [s.page_text or "" for s in sp.shots] + [s.page_text or "" for s in tl.shots]
    texts += [e.text for e in sp.explanations or []] + [e.text for e in tl.explanations or []]
    return texts


def assert_tells_this_story(memory: schemas.Memory, *, teller: schemas.Person, people: dict[str, schemas.Person],
                            prop_names: dict[str, str]) -> None:
    sp, tl = memory.screenplay, memory.timeline
    assert sp is not None and tl is not None
    [telling] = [t for t in memory.tellings if t.author_id == teller.id]
    title = memory.title

    # Every shot, in the screenplay and the timeline, has page text from the telling (or the title).
    for shot in [*sp.shots, *tl.shots]:
        page = shot.page_text
        assert page and page.strip(), f"shot {shot.id} has no page text"
        words = page.removesuffix("...").split()
        assert len(words) <= PAGE_TEXT_WORDS, page
        drawn = " ".join(words)
        assert drawn in " ".join(telling.text.split()) or page == title, f"{page!r} is not from the telling"
    assert [s.page_text for s in tl.shots] == [s.page_text for s in sp.shots]

    # None of the sample's prose survives, unless this telling says it too.
    for text in _all_prose(memory):
        for phrase in SAMPLE_PROSE:
            if phrase not in telling.text:
                assert phrase not in text, f"the sample's {phrase!r} is still in {text!r}"

    # Exactly one explanation per cast puppet and per prop, in the screenplay and the timeline.
    for explanations in (sp.explanations or [], tl.explanations or []):
        targets = [e.target for e in explanations]
        assert sorted(targets) == sorted([f"puppet:{c}" for c in sp.cast] + [f"prop:{p}" for p in sp.props])
        assert len(set(targets)) == len(targets)
        assert all(0 < len(e.text) < EXPLANATION_CHARS for e in explanations)
    by_target = {e.target: e.text for e in sp.explanations or []}
    assert teller.name in by_target[f"puppet:{teller.id}"]
    assert "telling this story" in by_target[f"puppet:{teller.id}"]
    for pid in sp.cast:
        if pid != teller.id:
            assert people[pid].name in by_target[f"puppet:{pid}"], by_target[f"puppet:{pid}"]
    for prop_id in sp.props:
        assert f"The {prop_names[prop_id]} " in by_target[f"prop:{prop_id}"], by_target[f"prop:{prop_id}"]


# ------------------------------------------------------------------------------ a fresh family, over HTTP


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    monkeypatch.setenv("STITCH_FAKE_DB", "1")
    monkeypatch.setenv("STITCH_AGENT", "fake")
    monkeypatch.setenv("STITCH_FAKE_RUN_STEP", "0")
    monkeypatch.delenv("STITCH_CORS_ORIGINS", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(routes.memories, "HEARTBEAT_SECONDS", 0.05)
    db_client.set_db(None)
    application = main.create_app()
    try:
        with TestClient(application):
            assert isinstance(agent.runs.get_manager(), ScriptedRunManager)
            yield application
    finally:
        agent.runs.configure(None)
        db_client.set_db(None)


def _json(res: httpx.Response, status: int = 200) -> Any:
    assert res.status_code == status, f"{res.request.method} {res.request.url} -> {res.status_code}: {res.text}"
    return res.json() if res.content else None


def _submit_and_answer(c: TestClient, fid: str, data: dict, review) -> list[tuple[str, dict]]:
    submitted = schemas.SubmitMemoryResponse.model_validate(_json(c.post(f"/families/{fid}/memories", data=data), 202))
    events: list[tuple[str, dict]] = []
    deadline = time.monotonic() + DEADLINE
    with c.stream("GET", f"/families/{fid}/runs/{submitted.run_id}/stream") as stream:
        assert stream.status_code == 200
        for name, payload in parse_sse(stream.iter_lines()):
            assert time.monotonic() < deadline, "the run did not finish in time"
            schemas.RUN_EVENTS[name].model_validate(payload)
            events.append((name, payload))
            if name in ("done", "error"):
                break
            body = None
            if name == "question":
                body = {"kind": "answer", "text": FOLLOWUP_REPLY}
            elif name == "props_found":
                body = review([p["id"] for p in payload["props"]])
            elif name == "merge":
                body = {"kind": "merge", "approve": False}
            if body is not None:
                _json(c.post(f"/families/{fid}/runs/{submitted.run_id}/answer", json=body), 204)
    return events


def test_a_fresh_family_gets_its_own_page_text_and_explanations(app):
    maya, theo = TestClient(app), TestClient(app)
    try:
        _json(maya.post("/auth/signup", json={"email": "maya@example.test", "password": PASSWORD, "name": "Maya"}), 201)
        created = schemas.CreateFamilyResponse.model_validate(
            _json(maya.post("/families", json={"name": "The Reyes", "person": {"name": "Maya Reyes", "born": 1980}}),
                  201),
            by_alias=True, by_name=False,
        )
        fid, maya_id = created.family.id, created.person.id
        invite = schemas.InviteResponse.model_validate(_json(maya.post(f"/families/{fid}/invites"), 201))
        _json(theo.post("/auth/signup", json={"email": "theo@example.test", "password": PASSWORD, "name": "Theo"}), 201)
        joined = schemas.JoinResponse.model_validate(
            _json(theo.post(f"/invites/{invite.code}/join",
                            json={"person": {"name": "Theo Reyes", "born": 1979, "spouseIds": [maya_id]}})),
            by_alias=True, by_name=False,
        )
        theo_id = joined.person.id

        events = _submit_and_answer(
            maya, fid,
            {"text": FRESH_TEXT, "year": "2004", "participantIds": [theo_id]},
            lambda ids: {"kind": "props", "props": [{"id": ids[0], "action": "rename", "name": "lemon seedlings"}]},
        )
        assert events[-1][0] == "done", events[-1]
        memory_id = events[-1][1]["memoryId"]
    finally:
        maya.close()
        theo.close()

    db = db_client.get_db()
    memory = memories_db.get_memory(db, fid, memory_id)
    assert memory is not None and memory.screenplay is not None and memory.timeline is not None
    people = {p.id: p for p in people_db.list_people(db, fid)}
    assert sorted(memory.screenplay.cast) == sorted([maya_id, theo_id])
    [prop_id] = memory.screenplay.props
    assert props_db.get_prop(db, fid, prop_id).name == "lemon seedlings"
    assert_tells_this_story(memory, teller=people[maya_id], people=people, prop_names={prop_id: "lemon seedlings"})

    by_target = {e.target: e.text for e in memory.screenplay.explanations or []}
    assert by_target[f"puppet:{maya_id}"] == "Maya Reyes is telling this story, from 2004."
    assert by_target[f"puppet:{theo_id}"] == "Maya's spouse Theo Reyes was there."
    assert by_target[f"prop:{prop_id}"] == "The lemon seedlings from Maya's story."
    pages = [s.page_text for s in memory.screenplay.shots]
    assert pages[0] == "We planted a lemon tree behind our first little house on a windy afternoon in early..."


# ------------------------------------------------------------------------------ the demo family


@pytest.fixture
def db(mdb):
    db_client.ensure_indexes(mdb)
    demo.seed_demo(mdb)
    db_client.set_db(mdb)
    try:
        yield mdb
    finally:
        db_client.set_db(None)


@pytest.fixture
def manager(db):
    mgr = ScriptedRunManager(db_getter=lambda: db, step_seconds=0)
    yield mgr
    mgr.shutdown()


def _run_demo(mgr: ScriptedRunManager, review) -> list[tuple[str, Any]]:
    fields = schemas.SubmitMemoryFields(text=LILY_TEXT, year=1993, participant_ids=["lily", "dad"])
    run_id = mgr.start_run(FID, ANA.user_id, "mom", fields, [])
    while True:
        status = mgr.wait(FID, run_id, timeout=DEADLINE)
        if status in ("done", "error"):
            return mgr.events(FID, run_id)
        assert status == "waiting"
        name, payload = mgr.events(FID, run_id)[-1]
        if name == "question":
            answer = schemas.AnswerRequest(kind="answer", text=FOLLOWUP_REPLY)
        elif name == "props_found":
            answer = review([p.id for p in payload.props])
        else:
            answer = schemas.AnswerRequest(kind="merge", approve=False)
        mgr.answer_run(FID, run_id, ANA.user_id, answer)


@pytest.mark.parametrize("rename", [None, "baby blanket"])
def test_the_demo_family_new_memory_tells_lilys_story(db, manager, rename):
    def review(ids: list[str]) -> schemas.AnswerRequest:
        action = "keep" if rename is None else "rename"
        return schemas.AnswerRequest(kind="props", props=[schemas.PropEdit(id=ids[0], action=action, name=rename)])

    events = _run_demo(manager, review)
    assert events[-1][0] == "done", events[-1]
    memory = memories_db.get_memory(db, FID, events[-1][1].memory_id)
    assert memory is not None and memory.screenplay is not None
    people = {p.id: p for p in people_db.list_people(db, FID)}
    sp = memory.screenplay
    assert set(sp.cast) == {"mom", "lily"}
    prop_names = {pid: props_db.get_prop(db, FID, pid).name for pid in sp.props}
    if rename is not None:
        assert set(prop_names.values()) == {rename}
    assert_tells_this_story(memory, teller=people["mom"], people=people, prop_names=prop_names)
    by_target = {e.target: e.text for e in sp.explanations or []}
    assert by_target["puppet:mom"] == f"{people['mom'].name} is telling this story, from 1993."
    assert by_target["puppet:lily"] == f"{people['mom'].name.split()[0]}'s child {people['lily'].name} was there."
    assert sp.shots[0].page_text == split_sentences(LILY_TEXT)[0]
    # The page text follows the narration: each shot opens on the sentence its narration opens on.
    for shot in sp.shots:
        if shot.narration:
            assert shot.page_text == " ".join(shot.narration[0].text.split()[:PAGE_TEXT_WORDS]) + (
                "..." if len(shot.narration[0].text.split()) > PAGE_TEXT_WORDS else ""
            )


# ------------------------------------------------------------------------------ recast_prose


def _person(pid: str, name: str, **kw: Any) -> schemas.Person:
    return schemas.Person(id=pid, family_id="f", name=name, character=demo.demo_people()[0].character, **kw)


def _scene(shots: int = 3, cast: tuple[str, ...] = ("me", "you"), props: tuple[str, ...] = ("books",)) -> Screenplay:
    sample = demo.load_sample().memory.screenplay
    assert sample is not None
    base = sample.shots[0]
    return Screenplay(
        memory_id="m",
        cast=list(cast),
        props=list(props),
        shots=[base.model_copy(update={"id": f"s{i + 1}"}, deep=True) for i in range(shots)],
        explanations=[Explanation(target="puppet:dad", text="Dad was 26 and had only come to church.")],
    )


def test_recast_prose_spreads_trims_and_falls_back_to_the_title():
    me = _person("me", "June Park")
    long = " ".join(f"word{i}" for i in range(30)) + "."
    out = recast_prose(
        _scene(shots=3), teller=me, people={"me": me}, props={}, text=f"{long} Short one!", title="Our day", year=None,
    )
    pages = [s.page_text for s in out.shots]
    assert pages[0] == " ".join(f"word{i}" for i in range(PAGE_TEXT_WORDS)) + "..."
    assert pages[1] == "Short one!"
    assert pages[2] == "Our day"  # two sentences, three shots
    # More sentences than shots: each shot opens on the first sentence of its slice.
    many = "One. Two. Three. Four. Five. Six."
    out = recast_prose(_scene(shots=2), teller=me, people={"me": me}, props={}, text=many, title="t", year=None)
    assert [s.page_text for s in out.shots] == ["One.", "Four."]
    # Nothing to say at all: the title, and never an empty page.
    out = recast_prose(_scene(shots=2), teller=me, people={"me": me}, props={}, text="?!", title="  ", year=None)
    assert all(s.page_text for s in out.shots)


def test_recast_prose_rebuilds_explanations_from_the_tree():
    me = _person("me", "June Park", parent_ids=["mum"])
    people = {
        "me": me,
        "wife": _person("wife", "Ada", spouse_ids=["me"]),
        "mum": _person("mum", "Grace"),
        "kid": _person("kid", "Bo", parent_ids=["me"]),
        "pal": _person("pal", "Ravi"),
    }
    props = {"books": schemas.Prop.model_validate({**demo.load_sample().props[0].model_dump(by_alias=True),
                                                    "name": "red kite", "familyId": "f"})}
    out = recast_prose(
        _scene(cast=("me", "wife", "mum", "kid", "pal")), teller=me, people=people, props=props,
        text="We flew a kite.", title="Kite day", year=2001,
    )
    assert [(e.target, e.text) for e in out.explanations or []] == [
        ("puppet:me", "June Park is telling this story, from 2001."),
        ("puppet:wife", "June's spouse Ada was there."),
        ("puppet:mum", "June's parent Grace was there."),
        ("puppet:kid", "June's child Bo was there."),
        ("puppet:pal", "Ravi was there with June."),
        ("prop:books", "The red kite from June's story."),
    ]
    # A prop not in the scene and the sample's own explanations are gone; long names stay short.
    huge = _person("you", "X" * 150 + " " + "Y" * 150)
    out = recast_prose(_scene(cast=("me", "you"), props=()), teller=me, people={"me": me, "you": huge},
                       props=props, text="Hi.", title="t", year=None)
    assert [e.target for e in out.explanations or []] == ["puppet:me", "puppet:you"]
    assert out.explanations[0].text == "June Park is telling this story."
    assert all(len(e.text) < EXPLANATION_CHARS for e in out.explanations)


def test_recast_prose_is_pure():
    me = _person("me", "June")
    sp = _scene()
    before = sp.model_copy(deep=True)
    recast_prose(sp, teller=me, people={"me": me}, props={}, text="A day.", title=short_title("A day."), year=1999)
    assert sp == before
