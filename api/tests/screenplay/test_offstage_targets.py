"""Verbs that walk their actor up to a prop (pick_up, sit_on, get_in) report a prop standing past
the stage edge as not_on_stage, naming the prop, before any walk is planned
(verb_helpers.require_prop_inside_stage / offstage_prop_reason).

Without the check the walk-up took the actor off the stage and the error blamed the actor
("dad is offstage", whose hint is an enter for someone already on stage), or the actor sat down
offstage without a word. A prop exactly on the edge (|x| = 1) is on stage.
"""

from __future__ import annotations

from typing import Any

import pytest

from screenplay.compiler import compile as compile_screenplay
from screenplay.errors import hint_for
from screenplay.schema import CompileResult, Screenplay
from screenplay.state import StateError, World
from screenplay.verb_helpers import fmt_x, offstage_prop_reason, require_prop_inside_stage
from verb_cases import CAST, PROPS, base_scene

EDGE = {"edge": [1.0, 0.0], "past_edge": [1.0005, 0.0], "left_edge": [0.0, 0.0]}  # x = 1, 1.001, -1


def _sp(*actions: dict, set: str = "yard", start: dict, prop_start: dict, marks: dict | None = None) -> Screenplay:
    shot: dict[str, Any] = {"id": "s1", "set": set, "start": start, "propStart": prop_start, "camera": [],
                            "actions": list(actions)}
    if marks:
        shot["marks"] = marks
    return Screenplay.model_validate({"memoryId": "offstage", "cast": CAST, "props": PROPS, "shots": [shot]})


def _compile(sp: Screenplay) -> CompileResult:
    sc = base_scene()
    return compile_screenplay(sp, sets=sc.sets, props=sc.props, people=sc.people)


def _errors(res: CompileResult) -> list[tuple[str, str]]:
    return [(e.code, e.message) for e in res.errors]


def _a(t: float, do: str, **kw: Any) -> dict:
    return {"t": t, "do": do, **kw}


# ------------------------------------------------------------------ the acceptance cases

def test_get_in_a_sedan_driven_offstage_names_the_sedan():
    res = _compile(_sp(
        _a(1, "get_in", who="dad", what="sedan"),
        _a(4, "drive_to", who="dad", what="sedan", to="offstage_right"),
        _a(9, "get_in", who="mom", what="sedan"),
        set="street", start={"dad": {"at": "garage"}, "mom": {"at": "corner"}}, prop_start={"sedan": {"at": "garage"}},
    ))
    assert _errors(res) == [(
        "not_on_stage",
        'shot s1, t=9: mom cannot get_in "sedan"; sedan is offstage at x=1.3; drive_to a mark on stage first',
    )]
    # the reason names the fix; the hint waits for the directing skill's row
    # (errors.PENDING_SKILL_HINTS)
    assert hint_for(res.errors[0]) is None
    assert hint_for(res.errors[0], pending=True) == "add a drive_to of sedan to a mark on stage before this get_in"


def test_sit_on_a_chair_offstage_names_the_chair():
    res = _compile(_sp(_a(1, "sit_on", who="dad", what="chair"),
                       start={"dad": {"at": "curb"}}, prop_start={"chair": {"at": "offstage_right"}}))
    assert _errors(res) == [(
        "not_on_stage",
        'shot s1, t=1: dad cannot sit_on "chair"; chair is offstage at x=1.3; place it on stage with propStart',
    )]
    assert hint_for(res.errors[0]) == "place chair on stage with propStart in shot s1"


def test_pick_up_of_books_offstage_names_the_books_not_dad():
    res = _compile(_sp(_a(1, "pick_up", who="dad", what="hymn_books"),
                       start={"dad": {"at": "curb"}}, prop_start={"hymn_books": {"at": "offstage_right"}}))
    assert _errors(res) == [(
        "not_on_stage",
        'shot s1, t=1: dad cannot pick_up "hymn_books"; hymn_books is offstage at x=1.3; '
        "place it on stage with propStart",
    )]
    hint = hint_for(res.errors[0])
    assert hint == "place hymn_books on stage with propStart in shot s1"
    assert not hint.startswith("add an enter for dad")


@pytest.mark.parametrize("side", ["offstage_left", "offstage_right"])
def test_both_sides(side):
    res = _compile(_sp(_a(1, "pick_up", who="mom", what="cap"),
                       start={"mom": {"at": "bench_spot"}}, prop_start={"cap": {"at": side}}))
    x = "-1.3" if side == "offstage_left" else "1.3"
    assert _errors(res) == [(
        "not_on_stage",
        f'shot s1, t=1: mom cannot pick_up "cap"; cap is offstage at x={x}; place it on stage with propStart',
    )]


# ---------------------------------------------------------------------------- the edge

@pytest.mark.parametrize("mark", ["edge", "left_edge"])
def test_a_prop_exactly_on_the_edge_can_be_picked_up(mark):
    start = {"dad": {"at": "curb" if mark == "edge" else "door"}}
    res = _compile(_sp(_a(1, "pick_up", who="dad", what="hymn_books"),
                       start=start, prop_start={"hymn_books": {"at": mark}}, marks=EDGE))
    assert res.ok, _errors(res)


def test_a_chair_exactly_on_the_edge_can_be_sat_on():
    res = _compile(_sp(_a(1, "sit_on", who="dad", what="chair"),
                       start={"dad": {"at": "curb"}}, prop_start={"chair": {"at": "edge"}}, marks=EDGE))
    assert res.ok, _errors(res)


def test_a_sedan_exactly_on_the_edge_can_be_got_into():
    res = _compile(_sp(_a(1, "get_in", who="dad", what="sedan"),
                       start={"dad": {"at": "curb"}}, prop_start={"sedan": {"at": "edge"}}, marks=EDGE))
    assert res.ok, _errors(res)


def test_just_past_the_edge_is_offstage_and_shown_to_three_decimals():
    res = _compile(_sp(_a(1, "pick_up", who="dad", what="hymn_books"),
                       start={"dad": {"at": "curb"}}, prop_start={"hymn_books": {"at": "past_edge"}}, marks=EDGE))
    assert _errors(res) == [(
        "not_on_stage",
        'shot s1, t=1: dad cannot pick_up "hymn_books"; hymn_books is offstage at x=1.001; '
        "place it on stage with propStart",
    )]


# ------------------------------------------------------------- what the check leaves alone

def test_a_held_prop_is_unaffected():
    """mom stands offstage holding the books: the pick_up fails because she holds them."""
    res = _compile(_sp(_a(1, "pick_up", who="dad", what="hymn_books"),
                       start={"dad": {"at": "curb"}, "mom": {"at": "offstage_right", "holding": "hymn_books"}},
                       prop_start={}))
    assert _errors(res) == [("already_held", 'shot s1, t=1: dad cannot pick_up "hymn_books"; mom is holding it')]


def test_drive_to_still_brings_a_sedan_on_from_offstage():
    res = _compile(_sp(_a(1, "drive_to", who="dad", what="sedan", to="corner"), set="street",
                       start={"dad": {"at": "offstage_left", "in": "sedan.driver"}},
                       prop_start={"sedan": {"at": "offstage_left"}}))
    assert res.ok, _errors(res)


def test_getting_in_out_of_sight_on_the_sedans_side_is_allowed():
    """dad waits offstage right by the parked sedan, gets in there and drives on: no walk
    crosses the stage edge."""
    res = _compile(_sp(_a(1, "get_in", who="dad", what="sedan"), _a(5, "drive_to", who="dad", what="sedan", to="curb"),
                       start={"dad": {"at": "offstage_right"}}, prop_start={"sedan": {"at": "offstage_right"}}))
    assert res.ok, _errors(res)


def test_getting_in_from_the_other_side_of_the_stage_is_not():
    res = _compile(_sp(_a(1, "get_in", who="dad", what="sedan"),
                       start={"dad": {"at": "offstage_left"}}, prop_start={"sedan": {"at": "offstage_right"}}))
    assert _errors(res) == [(
        "not_on_stage",
        'shot s1, t=1: dad cannot get_in "sedan"; sedan is offstage at x=1.3; drive_to a mark on stage first',
    )]


def test_a_prop_riding_on_a_sedan_offstage_names_the_sedan():
    res = _compile(_sp(_a(1, "pick_up", who="dad", what="suitcase"), start={"dad": {"at": "curb"}},
                       prop_start={"sedan": {"at": "offstage_right"}, "suitcase": {"on": "sedan.passenger"}}))
    assert _errors(res) == [(
        "not_on_stage",
        'shot s1, t=1: dad cannot pick_up "suitcase"; suitcase is on sedan, offstage at x=1.3; '
        "drive_to a mark on stage first",
    )]
    assert hint_for(res.errors[0]) is None  # errors.PENDING_SKILL_HINTS
    assert hint_for(res.errors[0], pending=True) == "add a drive_to of sedan to a mark on stage before this pick_up"


def test_books_on_a_table_offstage_name_the_table():
    res = _compile(_sp(_a(1, "pick_up", who="dad", what="hymn_books"), start={"dad": {"at": "curb"}},
                       prop_start={"table": {"at": "offstage_right"}, "hymn_books": {"on": "table.top"}}))
    assert _errors(res) == [(
        "not_on_stage",
        'shot s1, t=1: dad cannot pick_up "hymn_books"; hymn_books is on table, offstage at x=1.3; '
        "place table on stage with propStart",
    )]
    assert hint_for(res.errors[0]) == "place table on stage with propStart in shot s1"


def test_what_counts_is_where_the_stack_meets_the_ground():
    """A chair on a pickup's bed may overhang the edge while the pickup stands on stage: it is
    on stage, as a seat overhanging the edge is (verbs_body.seated_world_pos)."""
    w = _world_with_pickup()
    w._to_ground("pickup", (-0.6, 0.0, 0.5))
    w._attach_prop("chair", "pickup", "bed")
    assert w.props["chair"].world_pos[0] < -1.0  # type: ignore[index]
    assert offstage_prop_reason(w, "chair") is None
    assert require_prop_inside_stage(w, "chair") == w.props["chair"].world_pos
    w._to_ground("pickup", (1.2, 0.0, 0.5))
    w._refresh_world("pickup")
    assert offstage_prop_reason(w, "chair") == "chair is on pickup, offstage at x=1.2; drive_to a mark on stage first"


def _world_with_pickup() -> World:
    sc = base_scene()
    pickup = sc.props["sedan"].model_copy(update={"id": "pickup", "aspect": 2.4,
                                                  "sockets": {"driver": [0.7, 0.45], "bed": [0.25, 0.35]}})
    return World(sc.people.values(), [*sc.props.values(), pickup])


def test_an_offstage_actor_is_still_the_offstage_error():
    """The actor's own offstage error comes first: the fix is their enter."""
    res = _compile(_sp(_a(1, "pick_up", who="dad", what="hymn_books"),
                       start={"dad": {"at": "offstage_left"}}, prop_start={"hymn_books": {"at": "offstage_right"}}))
    assert [e.code for e in res.errors] == ["offstage"]
    assert hint_for(res.errors[0]) == "add an enter for dad before this pick_up"


# --------------------------------------------------------------------------- the helpers

@pytest.mark.parametrize("x,text", [(1.3, "1.3"), (-1.25, "-1.25"), (1.0006, "1.001"), (1.0, "1"),
                                    (-0.0001, "0"), (2.123456, "2.123")])
def test_fmt_x(x, text):
    assert fmt_x(x) == text


def _world() -> World:
    sc = base_scene()
    return World(sc.people.values(), sc.props.values())


def test_require_prop_inside_stage_returns_the_anchor_or_names_the_prop():
    w = _world()
    w._to_ground("chair", (1.0, 0.0, 0.5))
    assert require_prop_inside_stage(w, "chair") == (1.0, 0.0, 0.5)
    w._to_ground("chair", (-1.3, 0.0, 0.5))
    with pytest.raises(StateError) as e:
        require_prop_inside_stage(w, "chair")
    assert (e.value.code, e.value.reason) == ("not_on_stage", "chair is offstage at x=-1.3; place it on stage with propStart")
    with pytest.raises(StateError) as e:
        require_prop_inside_stage(w, "cap")  # never placed
    assert (e.value.code, e.value.reason) == ("not_on_stage", "cap is not on stage")


def test_offstage_prop_reason_by_kind():
    w = _world()
    w._to_ground("sedan", (1.5, 0.0, 0.5))
    assert offstage_prop_reason(w, "sedan") == "sedan is offstage at x=1.5; drive_to a mark on stage first"
    w._to_ground("lamp", (-1.2, 0.0, 0.5))
    assert offstage_prop_reason(w, "lamp") == "lamp is offstage at x=-1.2; place it on stage with propStart"
    w._to_ground("lamp", (-1.0, 0.0, 0.5))
    assert offstage_prop_reason(w, "lamp") is None
    assert offstage_prop_reason(w, "cap") is None  # never placed: require_prop_on_stage says so
    w._attach_puppet("cap", "dad", "hand_r")
    assert offstage_prop_reason(w, "cap") is None  # held: where its holder stands decides
