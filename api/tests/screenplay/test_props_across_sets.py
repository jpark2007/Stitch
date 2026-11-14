"""Props across sets: no prop shows on a set before it is placed there or after it is left behind.

There is no visibility channel, so "not on this set" is the ground at OFFSTAGE_LEFT, said with the
parent and position channels (screenplay/compiler.py module docstring, "Props across sets"):

- a prop whose first keys come in a later shot (propStart, a start ``holding``, carried in by an
  enter) waits offstage from t=0 and steps to its first value at its first key time;
- a listed prop that is never used waits offstage the whole film (not centre stage at [0, 0, 0]);
- a prop left behind when the set changes steps offstage at the new shot's start;
- a set that returns (A, B, A) does not bring back what was left lying on it.

Every timeline here also passes timeline_sanity.sanity_problems, which includes
check_props_on_set; the last tests show that check catching hand-built leaks.
"""

from __future__ import annotations

from typing import Any

import pytest

import schemas
from screenplay.compiler import _park_until_placed
from screenplay.compiler import compile as compile_screenplay
from screenplay.schema import CompileResult, Screenplay, Timeline
from screenplay.tracks import TrackSet, evaluate
from timeline_sanity import check_parent_position, check_props_on_set, sanity_problems

# ------------------------------------------------------------------------- the scene

SETS = {
    "porch": schemas.SetDef(
        id="porch", name="porch",
        layers=[{"art": None, "depth": 0, "height": 1.0}, {"art": None, "depth": 1, "height": 0.5}],
        marks={"door": [0.3, 0.0], "mid": [0.5, 0.0], "gate": [0.7, 0.0]},
    ),
    "kitchen": schemas.SetDef(
        id="kitchen", name="kitchen",
        layers=[{"art": None, "depth": 0, "height": 1.0}, {"art": None, "depth": 1, "height": 0.6}],
        marks={"stove": [0.25, 0.0], "table": [0.6, 0.0]},
    ),
    "street": schemas.SetDef(
        id="street", name="street",
        layers=[{"art": None, "depth": 0, "height": 1.0}, {"art": None, "depth": 1, "height": 0.3}],
        marks={"garage": [0.3, 0.0], "corner": [0.8, 0.0]},
    ),
}
PROPS = {
    "cup": schemas.Prop(id="cup", name="cup", size="hand", art={"body": None}, grip=[0.5, 0.5]),
    "bag": schemas.Prop(id="bag", name="bag", size="carried", art={"body": None}, grip=[0.5, 0.1]),
    "books": schemas.Prop(id="books", name="hymn books", size="hand", art={"body": None},
                          anchor=[0.5, 1.0], grip=[0.5, 0.0]),
    "cap": schemas.Prop(id="cap", name="cap", size="hand", art={"body": None}, anchor=[0.5, 1.0], grip=[0.5, 0.6]),
    "sedan": schemas.Prop(id="sedan", name="sedan", size="vehicle", art={"body": None}, aspect=2.0,
                          anchor=[0.5, 1.0], sockets={"driver": [0.42, 0.55], "passenger": [0.6, 0.55]}),
}
PEOPLE = {
    "ann": schemas.Person(id="ann", family_id="f", name="Ann", character={"color": "#336699", "build": "average"}),
    "ben": schemas.Person(id="ben", family_id="f", name="Ben", character={"color": "#993366", "build": "tall"}),
    "cara": schemas.Person(id="cara", family_id="f", name="Cara",
                           character={"color": "#669933", "build": "average", "item_prop_id": "cap"}),
}

# x on stage for a mark u: 2u - 1
DOOR, MID, GATE = [-0.4, 0.0, 0.5], [0.0, 0.0, 0.5], [0.4, 0.0, 0.5]
STOVE, TABLE = [-0.5, 0.0, 0.5], [0.2, 0.0, 0.5]
GARAGE = [-0.4, 0.0, 0.5]
OFFSTAGE_LEFT = [-1.3, 0.0, 0.5]
STEP = 1e-3


def run(*shots: dict, cast=("ann", "ben"), props=("cup", "bag", "books", "sedan")) -> CompileResult:
    data = {"memoryId": "m", "cast": list(cast), "props": list(props), "shots": list(shots)}
    return compile_screenplay(Screenplay.model_validate(data), sets=SETS, props=PROPS, people=PEOPLE)


def shot(sid: str, set_id: str, start: dict, *actions: dict, prop_start: dict | None = None) -> dict:
    return {"id": sid, "set": set_id, "start": start, "propStart": prop_start or {}, "actions": list(actions)}


def a(t: float, do: str, **params: Any) -> dict:
    return {"t": t, "do": do, **params}


def ok(result: CompileResult) -> Timeline:
    assert result.ok, [e.message for e in result.errors]
    assert result.timeline is not None
    return result.timeline


def track(tl: Timeline, target: str, channel: str) -> list[tuple[float, Any]]:
    for tr in tl.tracks:
        if (tr.target, tr.channel) == (target, channel):
            return [(k[0], k[1]) for k in tr.keys]
    return []


def at(tl: Timeline, target: str, channel: str, t: float) -> Any:
    return evaluate(tl.tracks, target, channel, t)


def samples(start: float, end: float, n: int = 20) -> list[float]:
    return [round(start + (end - start) * i / n, 4) for i in range(n)]


def offstage_until(tl: Timeline, what: str, t: float) -> None:
    """``what`` is on the ground at OFFSTAGE_LEFT from 0 up to just before ``t`` (no glide)."""
    for s in [*samples(0.0, t), round(t - STEP, 4)]:
        assert at(tl, f"prop:{what}", "parent", s) == "ground", s
        assert at(tl, f"prop:{what}", "position", s) == OFFSTAGE_LEFT, s


# ------------------------------------------------------- the scenarios (each must be sane)

def first_placed_by_prop_start() -> CompileResult:
    return run(
        shot("s1", "porch", {"ann": {"at": "door"}}, a(0, "walk_to", who="ann", to="gate")),
        shot("s2", "kitchen", {"ann": {"at": "stove"}}, a(0, "walk_to", who="ann", to="table"),
             prop_start={"cup": {"at": "table"}}),
    )


def first_held_in_shot_2() -> CompileResult:
    # ben is on the porch, empty-handed, in shot 1; he starts shot 2 holding the cup
    return run(
        shot("s1", "porch", {"ann": {"at": "door"}, "ben": {"at": "gate"}}, a(0, "wave", who="ben")),
        shot("s2", "kitchen", {"ben": {"at": "stove", "holding": "cup"}}, a(0, "walk_to", who="ben", to="table")),
    )


def carried_in_by_an_enter() -> CompileResult:
    return run(
        shot("s1", "porch", {"ann": {"at": "door"}, "ben": {"at": "gate"}}, a(0, "wave", who="ann")),
        shot("s2", "kitchen", {"ben": {"at": "offstage_left", "holding": "bag"}}, a(0, "enter", who="ben", to="table")),
    )


def never_used() -> CompileResult:
    return run(shot("s1", "porch", {"ann": {"at": "door"}}, a(0, "walk_to", who="ann", to="gate")))


def dropped_at_a_set_change() -> CompileResult:
    return run(
        shot("s1", "porch", {"ann": {"at": "door", "holding": "cup"}}, a(0, "drop", who="ann", what="cup")),
        shot("s2", "kitchen", {"ann": {"at": "stove"}}, a(0, "walk_to", who="ann", to="table")),
    )


def a_b_a(replace: bool = False) -> CompileResult:
    return run(
        shot("s1", "porch", {"ann": {"at": "door"}}, a(0, "wave", who="ann"), prop_start={"books": {"at": "mid"}}),
        shot("s2", "kitchen", {"ann": {"at": "stove"}}, a(0, "wave", who="ann")),
        shot("s3", "porch", {"ann": {"at": "door"}}, a(0, "walk_to", who="ann", to="gate"),
             prop_start={"books": {"at": "mid"}} if replace else None),
    )


def vehicle_in_shot_2() -> CompileResult:
    return run(
        shot("s1", "porch", {"ann": {"at": "door"}}, a(0, "wave", who="ann")),
        shot("s2", "street", {"ben": {"at": "garage", "in": "sedan.driver"}},
             a(0, "drive_to", who="ben", what="sedan", to="corner"), prop_start={"sedan": {"at": "garage"}}),
    )


def worn_item_and_props_placed_at_zero() -> CompileResult:
    return run(
        shot("s1", "porch", {"cara": {"at": "door", "holding": "cup"}}, a(0, "walk_to", who="cara", to="gate"),
             prop_start={"books": {"at": "mid"}}),
        shot("s2", "kitchen", {"cara": {"at": "stove"}}, a(0, "walk_to", who="cara", to="table")),
        cast=("cara",), props=("cup", "books", "cap"),
    )


SCENARIOS = {
    "prop_start_in_shot_2": first_placed_by_prop_start,
    "holding_in_shot_2": first_held_in_shot_2,
    "carried_in_by_an_enter": carried_in_by_an_enter,
    "never_used": never_used,
    "dropped_at_a_set_change": dropped_at_a_set_change,
    "a_b_a": a_b_a,
    "a_b_a_re_placed": lambda: a_b_a(replace=True),
    "vehicle_in_shot_2": vehicle_in_shot_2,
    "worn_and_placed_at_zero": worn_item_and_props_placed_at_zero,
}


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_every_scenario_compiles_sanely(name):
    tl = ok(SCENARIOS[name]())
    assert sanity_problems(tl) == []
    assert check_props_on_set(tl) == []


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_every_parent_key_has_a_position_key_at_the_same_time(name):
    tl = ok(SCENARIOS[name]())
    assert check_parent_position(tl) == []
    for tr in tl.tracks:
        if tr.target.startswith("prop:") and tr.channel == "parent":
            times = {k[0] for k in track(tl, tr.target, "position")}
            assert {k[0] for k in tr.keys} <= times, tr.target


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_every_listed_prop_has_parent_and_position_tracks_from_t0(name):
    tl = ok(SCENARIOS[name]())
    for tr in tl.tracks:
        if tr.target.startswith("prop:") and tr.channel in ("parent", "position"):
            assert tr.keys[0][0] == 0.0, (tr.target, tr.channel)


def test_compiling_twice_is_byte_identical():
    for build in SCENARIOS.values():
        assert build().model_dump_json(by_alias=True) == build().model_dump_json(by_alias=True)


# ------------------------------------------------------------- (a) first placed later

def test_a_prop_first_placed_by_prop_start_in_shot_2_waits_offstage_and_steps_in():
    tl = ok(first_placed_by_prop_start())
    s2 = tl.shots[1]
    assert track(tl, "prop:cup", "parent") == [(0.0, "ground"), (s2.start, "ground")]
    assert track(tl, "prop:cup", "position") == [(0.0, OFFSTAGE_LEFT), (s2.start, OFFSTAGE_LEFT), (s2.start, TABLE)]
    offstage_until(tl, "cup", s2.start)
    for s in [s2.start, *samples(s2.start, s2.end)]:
        assert at(tl, "prop:cup", "position", s) == TABLE, s


def test_a_prop_first_held_in_shot_2_is_not_in_the_hand_in_shot_1():
    tl = ok(first_held_in_shot_2())
    s2 = tl.shots[1]
    assert track(tl, "prop:cup", "parent") == [(0.0, "ground"), (s2.start, "puppet:ben.hand_r")]
    assert track(tl, "prop:cup", "position") == [
        (0.0, OFFSTAGE_LEFT), (s2.start, OFFSTAGE_LEFT), (s2.start, [0.0, 0.0, 0.0]),
    ]
    offstage_until(tl, "cup", s2.start)  # not in ben's hand on the porch
    assert at(tl, "prop:cup", "parent", s2.start) == "puppet:ben.hand_r"
    assert at(tl, "prop:cup", "position", s2.start) == [0.0, 0.0, 0.0]


def test_a_prop_carried_in_by_an_enter_waits_offstage_until_its_shot():
    tl = ok(carried_in_by_an_enter())
    s2 = tl.shots[1]
    offstage_until(tl, "bag", s2.start)
    assert at(tl, "prop:bag", "parent", s2.start) == "puppet:ben.hand_r"
    # ben walks in from offstage with it, and ends at the table still holding it
    assert at(tl, "puppet:ben", "root.position", s2.start)[0] == pytest.approx(OFFSTAGE_LEFT[0])
    assert at(tl, "puppet:ben", "root.position", s2.end)[0] == pytest.approx(TABLE[0])
    assert at(tl, "prop:bag", "parent", s2.end) == "puppet:ben.hand_r"


def test_the_first_key_is_a_step_not_a_glide():
    for build, what, shot_index in ((first_placed_by_prop_start, "cup", 1), (first_held_in_shot_2, "cup", 1),
                                    (carried_in_by_an_enter, "bag", 1), (vehicle_in_shot_2, "sedan", 1)):
        tl = ok(build())
        first = tl.shots[shot_index].start
        assert at(tl, f"prop:{what}", "position", first - STEP) == OFFSTAGE_LEFT, build.__name__
        assert at(tl, f"prop:{what}", "position", first) != OFFSTAGE_LEFT, build.__name__


# -------------------------------------------------------------------- (b) never used

def test_a_listed_prop_never_used_waits_offstage_the_whole_film():
    tl = ok(never_used())
    for what in ("cup", "bag", "books", "sedan"):
        assert track(tl, f"prop:{what}", "parent") == [(0.0, "ground")]
        assert track(tl, f"prop:{what}", "position") == [(0.0, OFFSTAGE_LEFT)]
        assert at(tl, f"prop:{what}", "position", tl.duration) == OFFSTAGE_LEFT


def test_a_prop_not_listed_gets_no_tracks():
    tl = ok(run(shot("s1", "porch", {"ann": {"at": "door"}}, a(0, "wave", who="ann")), props=("cup",)))
    assert {tr.target for tr in tl.tracks if tr.target.startswith("prop:")} == {"prop:cup"}


# ------------------------------------------------------ left behind at a set change

def test_a_prop_dropped_before_a_set_change_steps_offstage_at_the_new_shot():
    tl = ok(dropped_at_a_set_change())
    s2 = tl.shots[1]
    dropped = at(tl, "prop:cup", "position", s2.start - STEP)
    assert dropped[0] == pytest.approx(DOOR[0] + 0.08)  # beside ann's feet on the porch
    assert at(tl, "prop:cup", "parent", s2.start) == "ground"
    for s in [s2.start, *samples(s2.start, s2.end), s2.end]:
        assert at(tl, "prop:cup", "position", s) == OFFSTAGE_LEFT, s


def test_a_set_that_returns_does_not_bring_back_what_was_left_on_it():
    tl = ok(a_b_a())
    s1, s2, s3 = tl.shots
    for s in samples(s1.start, s2.start):
        assert at(tl, "prop:books", "position", s) == MID, s
    for s in [s2.start, *samples(s2.start, s3.end), s3.end]:
        assert at(tl, "prop:books", "position", s) == OFFSTAGE_LEFT, s


def test_a_set_that_returns_shows_a_prop_its_prop_start_places_again():
    tl = ok(a_b_a(replace=True))
    s1, s2, s3 = tl.shots
    for s in samples(s2.start, s3.start):
        assert at(tl, "prop:books", "position", s) == OFFSTAGE_LEFT, s
    for s in [s3.start, *samples(s3.start, s3.end)]:
        assert at(tl, "prop:books", "position", s) == MID, s


# ------------------------------------------------------------------------- vehicles

def test_a_vehicle_first_placed_in_shot_2_waits_offstage_and_steps_in():
    tl = ok(vehicle_in_shot_2())
    s2 = tl.shots[1]
    offstage_until(tl, "sedan", s2.start)
    assert at(tl, "prop:sedan", "position", s2.start) == GARAGE
    assert at(tl, "prop:sedan", "position", s2.end)[0] > GARAGE[0]  # then it drives to the corner


# ------------------------------------------------------------------------- untouched

def test_worn_items_and_props_placed_at_t0_are_untouched():
    tl = ok(worn_item_and_props_placed_at_zero())
    s2 = tl.shots[1]
    assert track(tl, "prop:cap", "parent") == [(0.0, "puppet:cara.head")]
    assert track(tl, "prop:cap", "position") == [(0.0, [0.0, 0.0, 0.0])]
    assert track(tl, "prop:cup", "parent") == [(0.0, "puppet:cara.hand_r")]
    assert track(tl, "prop:cup", "position") == [(0.0, [0.0, 0.0, 0.0])]
    # books: placed at t=0, then left behind at the set change (no extra t=0 keys)
    assert track(tl, "prop:books", "parent") == [(0.0, "ground")]
    assert track(tl, "prop:books", "position") == [(0.0, MID), (s2.start, MID), (s2.start, OFFSTAGE_LEFT)]


# --------------------------------------------- _park_until_placed on one-channel first keys

def _parked(keys: list[tuple[str, float, Any]]) -> Timeline:
    ts = TrackSet()
    for channel, t, value in keys:
        ts.add("prop:cup", channel, t, value)
    _park_until_placed(ts, "cup")
    return Timeline.model_validate({
        "duration": 10.0, "tracks": [tr.model_dump(by_alias=True) for tr in ts.tracks()],
        "shots": [{"id": "s1", "set": "porch", "start": 0.0, "actionStart": 1.2, "end": 10.0}],
    })


def test_a_first_parent_key_alone_gets_a_position_key_with_what_it_read():
    # a parent swap at t=4 with the first position key only at t=6: the stage held that first
    # position backwards, so the new position key at t=4 keeps it from t=4 on
    tl = _parked([("parent", 4.0, "puppet:ann.hand_r"), ("position", 6.0, [0.0, 0.1, 0.0])])
    assert track(tl, "prop:cup", "parent") == [(0.0, "ground"), (4.0, "puppet:ann.hand_r")]
    assert track(tl, "prop:cup", "position") == [
        (0.0, OFFSTAGE_LEFT), (4.0, OFFSTAGE_LEFT), (4.0, [0.0, 0.1, 0.0]), (6.0, [0.0, 0.1, 0.0]),
    ]
    assert check_parent_position(tl) == []


def test_a_first_parent_key_with_no_position_track_gets_the_default_origin():
    tl = _parked([("parent", 4.0, "puppet:ann.hand_r")])
    assert track(tl, "prop:cup", "position") == [(0.0, OFFSTAGE_LEFT), (4.0, OFFSTAGE_LEFT), (4.0, [0.0, 0.0, 0.0])]


def test_a_first_position_key_alone_keeps_the_parent_it_read():
    # the parent track starts later, at t=6: from t=4 on the prop still reads that parent
    tl = _parked([("position", 4.0, [0.2, 0.0, 0.5]), ("parent", 6.0, "prop:table.top"),
                  ("position", 6.0, [0.0, 0.0, 0.0])])
    assert track(tl, "prop:cup", "parent") == [(0.0, "ground"), (4.0, "prop:table.top"), (6.0, "prop:table.top")]
    assert at(tl, "prop:cup", "position", 4.0 - STEP) == OFFSTAGE_LEFT
    assert check_parent_position(tl) == []


def test_a_prop_keyed_at_t0_is_left_alone():
    ts = TrackSet()
    ts.add("prop:cup", "parent", 0.0, "ground")
    ts.add("prop:cup", "position", 0.0, [0.2, 0.0, 0.5])
    ts.add("prop:cup", "position", 3.0, [0.4, 0.0, 0.5])
    before = ts.tracks()
    _park_until_placed(ts, "cup")
    assert ts.tracks() == before


# ------------------------------------------------- check_props_on_set catches leaks

def _two_shots(tracks: list[dict], second_set: str = "porch") -> Timeline:
    return Timeline.model_validate({
        "duration": 10.0,
        "tracks": tracks,
        "shots": [
            {"id": "s1", "set": "kitchen", "start": 0.0, "actionStart": 1.2, "end": 5.0},
            {"id": "s2", "set": second_set, "start": 5.0, "actionStart": 6.2, "end": 10.0},
        ],
    })


CUP_AT_0_2 = [
    {"target": "prop:cup", "channel": "parent", "keys": [[0, "ground"]]},
    {"target": "prop:cup", "channel": "position", "keys": [[0, [0.2, 0.0, 0.5]]]},
]


def test_check_props_on_set_flags_a_prop_left_on_a_different_set():
    problems = check_props_on_set(_two_shots(CUP_AT_0_2))
    assert problems == [
        "props_on_set: shot s2 (porch) at t=5: prop:cup is on the ground at x=0.2 but has no parent or "
        "position key in this shot, nor in an earlier shot of this set"
    ]
    assert [p for p in sanity_problems(_two_shots(CUP_AT_0_2)) if p.startswith("props_on_set: ")] == problems


def test_check_props_on_set_allows_the_same_set_and_offstage_props():
    assert check_props_on_set(_two_shots(CUP_AT_0_2, second_set="kitchen")) == []
    parked = [CUP_AT_0_2[0], {"target": "prop:cup", "channel": "position",
                              "keys": [[0, [0.2, 0.0, 0.5]], [5, [0.2, 0.0, 0.5]], [5, OFFSTAGE_LEFT]]}]
    assert check_props_on_set(_two_shots(parked)) == []
    assert sanity_problems(_two_shots(parked)) == []


def test_check_props_on_set_flags_a_first_key_held_backwards():
    # first placed in shot 2 at x=0.2: before the fix it showed there in shot 1 too
    late = [{"target": "prop:cup", "channel": "parent", "keys": [[5, "ground"]]},
            {"target": "prop:cup", "channel": "position", "keys": [[5, [0.2, 0.0, 0.5]]]}]
    assert check_props_on_set(_two_shots(late)) == [
        "props_on_set: shot s1 (kitchen) at t=0: prop:cup is on the ground at x=0.2 but has no parent or "
        "position key in this shot, nor in an earlier shot of this set"
    ]


def test_check_props_on_set_flags_a_prop_with_no_position_track_at_centre_stage():
    only_state = [{"target": "prop:lamp", "channel": "state", "keys": [[0, "lit"]]}]
    problems = check_props_on_set(_two_shots(only_state))
    assert [p.split(":")[1].strip() for p in problems] == ["shot s1 (kitchen) at t=0", "shot s2 (porch) at t=5"]
    assert all("prop:lamp is on the ground at x=0 " in p for p in problems)


def test_check_props_on_set_ignores_held_props():
    held = [{"target": "prop:cup", "channel": "parent", "keys": [[0, "puppet:ann.hand_r"]]},
            {"target": "prop:cup", "channel": "position", "keys": [[0, [0.0, 0.0, 0.0]]]}]
    assert check_props_on_set(_two_shots(held)) == []
