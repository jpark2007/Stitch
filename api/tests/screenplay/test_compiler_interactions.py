"""compile() fills a scrub's missing from/to with its own shot's bounds, in timeline time.

The stage sampler reads a missing ``from`` as 0 and a missing ``to`` as the timeline's
duration, so a tab in shot s2 that left one out would scrub across s1 as well. The compiler
fills both ends in: ``from`` defaults to the shot's ``action_start`` (its t=0) and ``to`` to the
shot's ``end``.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

import schemas
from screenplay.compiler import compile as compile_screenplay
from screenplay.schema import CompileResult, Screenplay

SAMPLES = Path(__file__).resolve().parents[3] / "web" / "public" / "samples"


def _load(name: str) -> dict:
    return json.loads((SAMPLES / name).read_text())


MEMORY = _load("hymn_books.memory.json")
SETS = {s["id"]: schemas.SetDef.model_validate(s) for s in MEMORY["sets"]}
PROPS = {p["id"]: schemas.Prop.model_validate(p) for p in MEMORY["props"]}
PEOPLE = {p["id"]: schemas.Person.model_validate(p) for p in MEMORY["people"]}


def run(data: dict) -> CompileResult:
    return compile_screenplay(Screenplay.model_validate(data), sets=SETS, props=PROPS, people=PEOPLE)


def two_shots(*tabs: dict) -> dict:
    """The hymn screenplay with its one shot copied as s2, plus the given tabs."""
    data = _load("hymn_books.screenplay.json")
    s2 = copy.deepcopy(data["shots"][0])
    s2["id"] = "s2"
    data["shots"].append(s2)
    data["interactions"].extend(tabs)
    return data


def tab(id_: str, shot: str | None, **bounds: float) -> dict:
    drives: dict = {"type": "scrub", "targets": ["prop:hymn_books"]}
    drives.update(bounds)
    out = {"id": id_, "kind": "pull_tab", "label": "Pull", "at": [0.5, 0.5], "drives": drives}
    if shot is not None:
        out["shot"] = shot
    return out


def compiled(data: dict):
    res = run(data)
    assert res.ok, [e.message for e in res.errors]
    assert res.timeline is not None and res.timeline.interactions is not None
    shots = {s.id: s for s in res.timeline.shots}
    tabs = {i.id: i for i in res.timeline.interactions}
    return shots, tabs


def test_missing_from_is_the_shots_action_start():
    shots, tabs = compiled(two_shots(tab("t2", "s2", to=1.0)))
    s2 = shots["s2"]
    got = tabs["t2"].drives
    assert got.from_ is not None and got.to is not None
    assert got.from_ == pytest.approx(s2.action_start)
    assert got.to == pytest.approx(s2.action_start + 1.0)
    # The shot timing these numbers come from (s1 ends at 10.65; s2's action starts RISE later).
    assert (s2.action_start, got.from_, got.to) == pytest.approx((11.85, 11.85, 12.85))
    assert got.from_ > shots["s1"].end  # it no longer reaches back into s1


def test_neither_bound_is_the_whole_shot():
    shots, tabs = compiled(two_shots(tab("t3", "s2")))
    s2 = shots["s2"]
    got = tabs["t3"].drives
    assert (got.from_, got.to) == pytest.approx((s2.action_start, s2.end))
    assert got.from_ >= shots["s1"].end


def test_missing_to_is_the_shots_end():
    shots, tabs = compiled(two_shots(tab("t4", "s2", **{"from": 2.0})))
    s2 = shots["s2"]
    assert (tabs["t4"].drives.from_, tabs["t4"].drives.to) == pytest.approx((s2.action_start + 2.0, s2.end))


def test_no_shot_means_the_first_shot():
    shots, tabs = compiled(two_shots(tab("t5", None)))
    s1 = shots["s1"]
    assert (tabs["t5"].drives.from_, tabs["t5"].drives.to) == pytest.approx((s1.action_start, s1.end))
    assert tabs["t5"].shot is None


def test_written_bounds_are_unchanged():
    # The sample's own tab (shot time 3.25..3.55 in s1) converts exactly as before.
    _, tabs = compiled(two_shots(tab("t2", "s2", to=1.0)))
    golden = _load("hymn_books.timeline.json")["interactions"][0]
    drop = tabs["drop_books"].model_dump(by_alias=True, exclude_none=True)
    assert drop == golden
    assert (tabs["drop_books"].drives.from_, tabs["drop_books"].drives.to) == (4.45, 4.75)


def test_from_past_the_shots_end_is_a_precise_error():
    data = two_shots(tab("late", "s2", **{"from": 30.0}))
    res = run(data)
    assert not res.ok and res.timeline is None
    bad = [e for e in res.errors if e.code == "interaction_bad_range"]
    assert len(bad) == 1 and bad[0].shot == "s2"
    assert bad[0].message.endswith(
        'interaction "late" would scrub from shot t=30.00 back to t=9.45, where shot "s2" ends; '
        'give it a "to" after its "from"'
    )


def test_deterministic():
    data = two_shots(tab("t2", "s2", to=1.0), tab("t3", "s2"))
    assert run(data).model_dump_json() == run(copy.deepcopy(data)).model_dump_json()
