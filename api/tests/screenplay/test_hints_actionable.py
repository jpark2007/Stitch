"""Every hint this round added or changed is actionable: the failing screenplay gives the exact
code, message and errors.hint_for text, and the screenplay with that hint applied compiles.

Each case is (the broken screenplay, its one error, its hint, the screenplay with the hint
applied). The director agent reads the hint beside the message, so a hint whose fix does not
compile is worse than none.

A case whose hint is None is an errors.PENDING_SKILL_HINTS situation: its fix is a verb the
directing skill's row for the code does not name yet, so hint_for gives no hint and the reason
says the fix itself ("...; use stand_up first"). ``pending`` is the hint it gives with
``pending=True``; the fixed screenplay applies that one, so it is checked to compile already.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from screenplay import body as B
from screenplay.compiler import compile as compile_screenplay
from screenplay.errors import hint_for
from screenplay.schema import CompileResult, Screenplay
from verb_cases import CAST, PROPS, base_scene


def _a(t: float, do: str, **kw: Any) -> dict:
    return {"t": t, "do": do, **kw}


def _sp(*actions: dict, set: str = "yard", start: dict | None = None, prop_start: dict | None = None,
        marks: dict | None = None, camera: list | None = None, narration: list | None = None,
        cast: list[str] | None = None, props: list[str] | None = None) -> Screenplay:
    shot: dict[str, Any] = {"id": "s1", "set": set, "start": start or {}, "propStart": prop_start or {},
                            "camera": camera or [], "actions": list(actions)}
    if marks:
        shot["marks"] = marks
    if narration:
        shot["narration"] = narration
    return Screenplay.model_validate({"memoryId": "hints", "cast": cast or list(CAST), "props": props or list(PROPS),
                                      "shots": [shot]})


def _compile(sp: Screenplay) -> CompileResult:
    sc = base_scene()
    return compile_screenplay(sp, sets=sc.sets, props=sc.props, people=sc.people)


@dataclass(frozen=True)
class HintCase:
    name: str
    broken: Screenplay
    code: str
    message: str
    hint: str | None
    fixed: Screenplay
    pending: str | None = None


TABLE = {"table": {"at": "table_spot"}}
CHAIR = {"chair": {"at": "bench_spot"}}
SEATED_MOM = {"mom": {"at": "bench_spot", "in": "chair.seat"}}
GARAGE = {"sedan": {"at": "garage"}}

CASES: list[HintCase] = [
    HintCase(
        "seated_pick_up",
        _sp(_a(1, "pick_up", who="mom", what="cap"), start=SEATED_MOM, prop_start={**CHAIR, "cap": {"at": "bench_spot"}}),
        "out_of_reach",
        'shot s1, t=1: mom cannot pick_up "cap"; mom\'s arm_r cannot reach: target is 0.23 from the shoulder, '
        "reach is 0.17; mom is seated in chair.seat; use stand_up first",
        None,
        _sp(_a(1, "stand_up", who="mom"), _a(1 + B.POSE_SECONDS, "pick_up", who="mom", what="cap"),
            start=SEATED_MOM, prop_start={**CHAIR, "cap": {"at": "bench_spot"}}),
        "add a stand_up for mom before this pick_up",
    ),
    HintCase(
        "seated_place_on",
        _sp(_a(1, "place_on", who="mom", what="hymn_books", on="bench_spot"),
            start={"mom": {"at": "bench_spot", "in": "chair.seat", "holding": "hymn_books"}}, prop_start=CHAIR),
        "out_of_reach",
        'shot s1, t=1: mom cannot place_on "hymn_books"; mom\'s arm_r cannot reach: target is 0.20 from the '
        "shoulder, reach is 0.17; mom is seated in chair.seat; use stand_up first",
        None,
        _sp(_a(1, "stand_up", who="mom"), _a(1 + B.POSE_SECONDS, "place_on", who="mom", what="hymn_books", on="bench_spot"),
            start={"mom": {"at": "bench_spot", "in": "chair.seat", "holding": "hymn_books"}}, prop_start=CHAIR),
        "add a stand_up for mom before this place_on",
    ),
    HintCase(
        "not_driver_passenger",
        _sp(_a(1, "drive_to", who="mom", what="sedan", to="corner"), set="street",
            start={"mom": {"at": "garage", "in": "sedan.passenger"}}, prop_start=GARAGE),
        "not_driver",
        'shot s1, t=1: mom cannot drive_to "sedan"; mom is in sedan.passenger, not sedan.driver',
        None,
        _sp(_a(1, "get_out", who="mom", what="sedan"), _a(3, "get_in", who="mom", what="sedan", socket="driver"),
            _a(6, "drive_to", who="mom", what="sedan", to="corner"), set="street",
            start={"mom": {"at": "garage", "in": "sedan.passenger"}}, prop_start=GARAGE),
        "add a get_out for mom, then get_in sedan with socket driver, before this drive_to",
    ),
    HintCase(
        "not_driver_outside",
        _sp(_a(1, "drive_to", who="kid", what="sedan", to="corner"), set="street",
            start={"kid": {"at": "garage"}}, prop_start=GARAGE),
        "not_driver",
        'shot s1, t=1: kid cannot drive_to "sedan"; kid is not in sedan.driver; use get_in with socket driver first',
        "add a get_in sedan with socket driver for kid first",
        _sp(_a(1, "get_in", who="kid", what="sedan", socket="driver"), _a(4, "drive_to", who="kid", what="sedan", to="corner"),
            set="street", start={"kid": {"at": "garage"}}, prop_start=GARAGE),
    ),
    HintCase(
        "place_on_itself",
        _sp(_a(1, "place_on", who="dad", what="hymn_books", on="hymn_books"),
            start={"dad": {"at": "by_table", "holding": "hymn_books"}}, prop_start=TABLE),
        "self_target",
        'shot s1, t=1: dad cannot place_on "hymn_books"; "on" is hymn_books itself',
        "place it on a different prop or a mark",
        _sp(_a(1, "place_on", who="dad", what="hymn_books", on="table"),
            start={"dad": {"at": "by_table", "holding": "hymn_books"}}, prop_start=TABLE),
    ),
    HintCase(
        "place_on_a_person",
        _sp(_a(1, "place_on", who="dad", what="hymn_books", on="mom"),
            start={"dad": {"at": "by_table", "holding": "hymn_books"}, "mom": {"at": "table_spot"}}, prop_start=TABLE),
        "bad_target",
        'shot s1, t=1: dad cannot place_on "hymn_books"; "on" must be a prop or a mark, and "mom" is a person; '
        "the props are hymn_books, cap, chair, table, suitcase, lamp, sedan; the marks are offstage_left, "
        "offstage_right, door, bench_spot, beside_bench, by_table, table_spot, curb",
        "place it on a prop or a mark",
        _sp(_a(1, "place_on", who="dad", what="hymn_books", on="table"),
            start={"dad": {"at": "by_table", "holding": "hymn_books"}, "mom": {"at": "table_spot"}}, prop_start=TABLE),
    ),
    HintCase(
        "exit_to_door",
        _sp(_a(1, "exit", who="mom", to="door"), start={"mom": {"at": "curb"}}),
        "unknown_mark",  # not bad_exit: tests/agent/test_agent_skills.py pins it (validate.check_offstage)
        'shot s1, t=1: mom cannot exit; "to" must be offstage_left or offstage_right, not "door"',
        None,
        _sp(_a(1, "exit", who="mom", to="offstage_right"), start={"mom": {"at": "curb"}}),
        "exit to offstage_left or offstage_right",
    ),
    HintCase(
        "enter_from_curb",
        _sp(_a(1, "enter", who="dad", to="door", **{"from": "curb"}), start={"dad": {"at": "offstage_left"}}),
        "bad_exit",
        'shot s1, t=1: dad cannot enter; "from" must be offstage_left or offstage_right, not "curb"',
        "enter from offstage_left or offstage_right",
        _sp(_a(1, "enter", who="dad", to="door", **{"from": "offstage_left"}), start={"dad": {"at": "offstage_left"}}),
    ),
    HintCase(
        "follow_a_prop",
        _sp(_a(0, "walk_to", who="mom", to="curb"), _a(0.5, "follow", who="dad", target="hymn_books"),
            start={"dad": {"at": "door"}, "mom": {"at": "bench_spot"}}),
        "bad_target",
        'shot s1, t=0.5: dad cannot follow; "target" must be a person in the cast, and "hymn_books" is a prop',
        "name a person in the cast as target",
        _sp(_a(0, "walk_to", who="mom", to="curb"), _a(0.5, "follow", who="dad", target="mom"),
            start={"dad": {"at": "door"}, "mom": {"at": "bench_spot"}}),
    ),
    HintCase(
        "hand_to_a_mark",
        _sp(_a(1, "hand_to", who="mom", what="hymn_books", to="beside_bench"),
            start={"mom": {"at": "bench_spot", "holding": "hymn_books"}, "dad": {"at": "beside_bench", "facing": "left"}}),
        "bad_target",
        'shot s1, t=1: mom cannot hand_to "hymn_books"; "to" must be a person in the cast, and "beside_bench" is a mark',
        "name a person in the cast as to",
        _sp(_a(1, "hand_to", who="mom", what="hymn_books", to="dad"),
            start={"mom": {"at": "bench_spot", "holding": "hymn_books"}, "dad": {"at": "beside_bench", "facing": "left"}}),
    ),
    HintCase(
        "open_lamp",
        _sp(_a(1, "open", who="dad", what="lamp"), start={"dad": {"at": "by_table"}},
            prop_start={"lamp": {"on": "table.top", "state": "out"}, **TABLE}),
        "unknown_state",
        'shot s1, t=1: dad cannot open "lamp"; "lamp" has no state "open"; its states are lit, out',
        "use set_state with a state the message lists, or open a prop that has an open state",
        _sp(_a(1, "set_state", who="dad", what="lamp", state="lit"), start={"dad": {"at": "by_table"}},
            prop_start={"lamp": {"on": "table.top", "state": "out"}, **TABLE}),
    ),
    HintCase(
        "close_lamp",
        _sp(_a(1, "close", who="dad", what="lamp"), start={"dad": {"at": "by_table"}},
            prop_start={"lamp": {"on": "table.top", "state": "lit"}, **TABLE}),
        "unknown_state",
        'shot s1, t=1: dad cannot close "lamp"; "lamp" has no state "closed"; its states are lit, out',
        "use set_state with a state the message lists, or close a prop that has a closed state",
        _sp(_a(1, "close", who="dad", what="suitcase"), start={"dad": {"at": "by_table"}},
            prop_start={"suitcase": {"on": "table.top", "state": "open"}, **TABLE}),
    ),
    HintCase(
        "sedan_driven_offstage",
        _sp(_a(1, "get_in", who="dad", what="sedan"), _a(4, "drive_to", who="dad", what="sedan", to="offstage_right"),
            _a(9, "get_in", who="mom", what="sedan"), set="street",
            start={"dad": {"at": "garage"}, "mom": {"at": "corner"}}, prop_start=GARAGE),
        "not_on_stage",
        'shot s1, t=9: mom cannot get_in "sedan"; sedan is offstage at x=1.3; drive_to a mark on stage first',
        None,
        _sp(_a(1, "get_in", who="dad", what="sedan"), _a(4, "drive_to", who="dad", what="sedan", to="offstage_right"),
            _a(9, "drive_to", who="dad", what="sedan", to="corner"), _a(16, "get_in", who="mom", what="sedan"),
            set="street", start={"dad": {"at": "garage"}, "mom": {"at": "corner"}}, prop_start=GARAGE),
        "add a drive_to of sedan to a mark on stage before this get_in",
    ),
    HintCase(
        "chair_offstage",
        _sp(_a(1, "sit_on", who="dad", what="chair"), start={"dad": {"at": "curb"}},
            prop_start={"chair": {"at": "offstage_right"}}),
        "not_on_stage",
        'shot s1, t=1: dad cannot sit_on "chair"; chair is offstage at x=1.3; place it on stage with propStart',
        "place chair on stage with propStart in shot s1",
        _sp(_a(1, "sit_on", who="dad", what="chair"), start={"dad": {"at": "curb"}},
            prop_start={"chair": {"at": "table_spot"}}),
    ),
    HintCase(
        "mark_too_low",
        _sp(_a(1, "walk_to", who="dad", to="pit"), start={"dad": {"at": "door"}}, marks={"pit": [0.5, -0.3]}),
        "bad_mark",
        'shot s1: mark "pit" has h -0.3, but h is the standing surface\'s height above the ground, from 0 to 1.5',
        "give mark pit an h from 0 to 1.5",
        _sp(_a(1, "walk_to", who="dad", to="pit"), start={"dad": {"at": "door"}}, marks={"pit": [0.5, 0.0]}),
    ),
    HintCase(
        "mark_too_deep",
        _sp(_a(1, "walk_to", who="dad", to="front"), start={"dad": {"at": "door"}}, marks={"front": [0.3, 0.0, 4.0]}),
        "bad_mark",
        'shot s1: mark "front" has d 4, but d is a depth from 0 (back of the set) to 1',
        "give mark front a d from 0 to 1",
        _sp(_a(1, "walk_to", who="dad", to="front"), start={"dad": {"at": "door"}}, marks={"front": [0.3, 0.0, 0.7]}),
    ),
    HintCase(
        "malformed_mark",
        _sp(_a(1, "walk_to", who="dad", to="spot"), start={"dad": {"at": "door"}}, marks={"spot": [0.5]}),
        "bad_mark",
        'shot s1: mark "spot" is [0.5], but a mark is [u, h] or [u, h, d] (numbers)',
        "write the mark as [u, h] or [u, h, d] (numbers)",
        _sp(_a(1, "walk_to", who="dad", to="spot"), start={"dad": {"at": "door"}}, marks={"spot": [0.5, 0.0]}),
    ),
    HintCase(
        "wave_too_long",
        _sp(_a(1, "wave", who="kid", dur=1e9), start={"kid": {"at": "door"}}),
        "bad_dur",
        "shot s1, t=1: kid cannot wave; dur must be at most 600 s, not 1e+09",
        "give dur at most 600 seconds",
        _sp(_a(1, "wave", who="kid", dur=600), start={"kid": {"at": "door"}}),
    ),
    HintCase(
        "wave_too_late",
        _sp(_a(1e7, "wave", who="kid"), start={"kid": {"at": "door"}}),
        "bad_time",
        "shot s1, t=10000000: kid cannot wave; t must be at most 600 s, not 1e+07",
        "use a t of at most 600 s",
        _sp(_a(600, "wave", who="kid"), start={"kid": {"at": "door"}}),
    ),
    HintCase(
        "rain_too_long",
        _sp(_a(1, "rain", dur=1e12), start={"mom": {"at": "bench_spot"}}),
        "bad_dur",
        "shot s1, t=1: cannot rain; dur must be at most 600 s, not 1e+12",
        "give dur at most 600 seconds",
        _sp(_a(1, "rain", dur=600), start={"mom": {"at": "bench_spot"}}),
    ),
    HintCase(
        "unknown_prop",
        _sp(start={"dad": {"at": "door"}}, props=[*PROPS, "kite"]),
        "unknown_prop",
        'screenplay: prop "kite" is not a known prop; known props: hymn_books, cap, chair, table, suitcase, lamp, sedan',
        "remove kite from the screenplay's props, or use a prop the message lists",
        _sp(start={"dad": {"at": "door"}}),
    ),
    HintCase(
        "unknown_person",
        _sp(start={"dad": {"at": "door"}}, cast=[*CAST, "gran"]),
        "unknown_person",
        'screenplay: cast member "gran" is not a known person; known people: dad, mom, kid',
        "remove gran from the cast, or use a person the message lists",
        _sp(start={"dad": {"at": "door"}}),
    ),
]


@pytest.mark.parametrize("case", CASES, ids=[c.name for c in CASES])
def test_the_broken_screenplay_gives_its_code_message_and_hint(case: HintCase):
    res = _compile(case.broken)
    assert [(e.code, e.message) for e in res.errors] == [(case.code, case.message)]
    assert hint_for(res.errors[0]) == case.hint


@pytest.mark.parametrize("case", CASES, ids=[c.name for c in CASES])
def test_the_hint_applied_compiles(case: HintCase):
    res = _compile(case.fixed)
    assert res.ok, [(e.code, e.message) for e in res.errors]


def test_the_fixes_change_only_what_the_hint_says():
    """Each fixed screenplay differs from its broken one (a guard against a copy-paste case)."""
    for case in CASES:
        assert case.fixed != case.broken, case.name


PENDING = [c for c in CASES if c.hint is None]


@pytest.mark.parametrize("case", PENDING, ids=[c.name for c in PENDING])
def test_a_pending_hint_is_given_on_request(case: HintCase):
    """errors.PENDING_SKILL_HINTS: hint_for gives None until the directing skill's row names the
    verb, and the pending hint with pending=True. The fixed screenplay above applies it."""
    (err,) = _compile(case.broken).errors
    assert case.pending is not None
    assert hint_for(err, pending=True) == case.pending


def test_every_pending_hint_has_a_case():
    from screenplay.errors import PENDING_SKILL_HINTS

    templates = {t for variants in PENDING_SKILL_HINTS.values() for _, t in variants}
    covered = {t for t in templates for c in PENDING if _matches(t, c.pending)}
    # the get_out from a vehicle seat is covered in test_errors
    assert templates - covered == {"add a get_out for {who} before this {do}"}


def _matches(template: str, text: str | None) -> bool:
    import re

    pattern = "^" + re.sub(r"\\\{\w+\\\}", r"\\S+", re.escape(template)) + "$"
    return text is not None and re.match(pattern, text) is not None
