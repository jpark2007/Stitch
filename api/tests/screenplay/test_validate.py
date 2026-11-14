"""Static screenplay validation: vocabulary, ids, marks, params, times, narration, extras."""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

import schemas
from screenplay.schema import VOCABULARY, Action, CompileError, Screenplay
from screenplay.validate import CAMERA, MAX_SECONDS, VERBS, action_params, validate

SAMPLES = Path(__file__).resolve().parents[3] / "web" / "public" / "samples"


# ------------------------------------------------------------------ fixtures

def person(pid: str) -> schemas.Person:
    return schemas.Person(id=pid, family_id="f", name=pid.title(), character={"color": "#123456"})


def prop(pid: str, size: str = "hand", sockets=None, states=None, parts=None) -> schemas.Prop:
    return schemas.Prop(
        id=pid, name=pid, size=size, art={"body": None},
        sockets=sockets or {}, states=states or {}, parts=parts or {},
    )


def layers(n: int) -> list[dict]:
    return [{"art": None, "depth": i, "height": 1.0} for i in range(n)]


SETS = {
    "church": schemas.SetDef(
        id="church", name="Church", layers=layers(3),
        marks={"steps_left": [0.2, 0.0], "steps_top": [0.6, 0.3], "curb": [0.9, 0.0]},
    ),
    "park": schemas.SetDef(id="park", name="Park", layers=layers(1), marks={"lawn": [0.5, 0.0]}),
}
PEOPLE = {p.id: p for p in map(person, ["dad", "mom", "kid", "grandma"])}
PROPS = {p.id: p for p in [
    prop("hymn_books"),
    prop("hat"),
    prop("lamp", "carried", states={"lit": {}, "out": {}}),
    prop("suitcase", "carried", states={"open": {}, "closed": {}}),
    prop("chair", "furniture", sockets={"seat": [0.5, 0.5]}),
    prop("table", "furniture", sockets={"top": [0.5, 0.0]}),
    prop("piano", "furniture"),
    prop("sedan", "vehicle", sockets={"driver": [0.35, 0.55], "passenger": [0.6, 0.55]}),
    prop("chapel", "building"),
    prop("clock", "carried", parts={"hand": {"art": "hand", "at": [0.5, 0.5]}}),
    prop("trumpet"),  # in the family's props but not in the screenplay's
]}
CAST = ["dad", "mom", "kid"]
SP_PROPS = [p for p in PROPS if p != "trumpet"]

CAST_LIST = "the cast is dad, mom, kid"
PROPS_LIST = "the props are " + ", ".join(SP_PROPS)
MARKS_LIST = "the marks are offstage_left, offstage_right, steps_left, steps_top, curb"
POSES_LIST = "poses are stand, hold, sit, kneel, wave, point, reach, hug, slumped, arms_raised, bow_head"


def shot(**kw) -> dict:
    return {"id": kw.pop("id", "s1"), "set": kw.pop("set", "church"), **kw}


def act(do: str, t: float = 1.0, **params) -> dict:
    return {"t": t, "do": do, **params}


def screenplay(*shots: dict, **kw) -> Screenplay:
    data = {"memoryId": "m", "cast": list(CAST), "props": list(SP_PROPS), "shots": list(shots) or [shot()]}
    data.update(kw)
    return Screenplay.model_validate(data)


def check(sp: Screenplay) -> list[CompileError]:
    return validate(sp, SETS, PROPS, PEOPLE)


def check_actions(*actions: dict, **shot_kw) -> list[CompileError]:
    return check(screenplay(shot(actions=list(actions), **shot_kw)))


def E(code: str, message: str, shot: str | None = "s1", t: float | None = None,
      who: str | None = None, do: str | None = None) -> dict:
    return {"code": code, "message": message, "shot": shot, "t": t, "who": who, "do": do}


def assert_errors(errors: list[CompileError], *expected: dict) -> None:
    assert [e.model_dump() for e in errors] == list(expected)


# --------------------------------------------------------------- the sample

def load_sample():
    memory = json.loads((SAMPLES / "hymn_books.memory.json").read_text())
    sp = Screenplay.model_validate_json((SAMPLES / "hymn_books.screenplay.json").read_text())
    sets = {s["id"]: schemas.SetDef.model_validate(s) for s in memory["sets"]}
    props = {p["id"]: schemas.Prop.model_validate(p) for p in memory["props"]}
    people = {p["id"]: schemas.Person.model_validate(p) for p in memory["people"]}
    return sp, sets, props, people


def test_sample_is_valid():
    assert validate(*load_sample()) == []


def test_validate_is_pure_and_deterministic():
    sp, sets, props, people = load_sample()
    bad = sp.model_copy(deep=True)
    bad.shots[0].actions[0].to = "nowhere"
    bad.cast.append("ghost")
    before = bad.model_dump()
    first = validate(bad, sets, props, people)
    assert first == validate(bad, sets, props, people)
    assert bad.model_dump() == before
    assert [e.code for e in first] == ["unknown_person", "unknown_mark"]


# ------------------------------------------------------- every verb, every param

MINIMAL: dict[str, dict] = {
    "walk_to": {"who": "dad", "to": "curb"},
    "run_to": {"who": "dad", "to": "curb"},
    "enter": {"who": "dad", "to": "steps_left"},
    "exit": {"who": "dad"},
    "turn_to": {"who": "dad", "target": "mom"},
    "follow": {"who": "dad", "target": "mom"},
    "pose": {"who": "dad", "pose": "bow_head"},
    "wave": {"who": "dad"},
    "point_at": {"who": "dad", "target": "curb"},
    "sit_on": {"who": "dad", "what": "chair"},
    "stand_up": {"who": "dad"},
    "kneel": {"who": "dad"},
    "hug": {"who": "dad", "target": "mom"},
    "lean_toward": {"who": "dad", "target": "hymn_books"},
    "pick_up": {"who": "dad", "what": "hymn_books"},
    "hold": {"who": "dad", "what": "hymn_books"},
    "drop": {"who": "dad", "what": "hymn_books"},
    "hand_to": {"who": "dad", "what": "hymn_books", "to": "mom"},
    "put_on": {"who": "dad", "what": "hat"},
    "place_on": {"who": "dad", "what": "hymn_books", "on": "table"},
    "open": {"who": "dad", "what": "suitcase"},
    "close": {"who": "dad", "what": "suitcase"},
    "set_state": {"what": "lamp", "state": "lit"},
    "get_in": {"who": "dad", "what": "sedan"},
    "get_out": {"who": "dad", "what": "sedan"},
    "drive_to": {"who": "dad", "what": "sedan", "to": "curb"},
    "ride_to": {"who": "dad", "what": "sedan", "to": "curb"},
    "confetti": {},
    "steam": {},
    "rain": {},
    "smoke": {},
    "time_of_day": {"value": "dusk"},
}

# A valid value for every optional parameter, per verb.
OPTIONAL_VALUES: dict[str, dict] = {
    "enter": {"from": "offstage_right"},
    "exit": {"to": "offstage_left"},
    "follow": {"dur": 2},
    "pose": {"dur": 0.5},
    "wave": {"target": "mom", "dur": 1},
    "point_at": {"dur": 1},
    "sit_on": {"socket": "seat"},
    "kneel": {"dur": 1},
    "hug": {"dur": 1},
    "lean_toward": {"dur": 1},
    "put_on": {"target": "mom"},
    "place_on": {"socket": "top"},
    "set_state": {"who": "dad"},
    "get_in": {"socket": "passenger"},
    "confetti": {"at": "mom", "dur": 2},
    "steam": {"at": "hymn_books", "dur": 2},
    "rain": {"dur": 3},
    "smoke": {"at": "curb", "dur": 2},
    "time_of_day": {"dur": 2},
}

# A value for any parameter a verb might wrongly be given.
ANY_VALUE = {"who": "dad", "to": "curb", "from": "offstage_left", "what": "hymn_books", "target": "mom",
             "on": "table", "socket": "seat", "pose": "wave", "state": "lit", "at": "curb", "value": "day", "dur": 1}


def message_for(verb: str, params: dict, reason: str) -> str:
    who, what = params.get("who"), params.get("what")
    subject = f"{who} cannot {verb}" if who else f"cannot {verb}"
    if what:
        subject += f' "{what}"'
    return f"shot s1, t=1: {subject}; {reason}"


def test_verb_tables_cover_the_vocabulary():
    assert set(MINIMAL) == set(VERBS) == set(VOCABULARY["actions"])
    for verb, opts in OPTIONAL_VALUES.items():
        assert set(opts) == set(VERBS[verb]["optional"]), verb
    assert {v for v in VERBS if VERBS[v]["optional"]} == set(OPTIONAL_VALUES)
    assert set(ANY_VALUE) == {f.alias or n for n, f in Action.model_fields.items()} - {"t", "do", "id"}


@pytest.mark.parametrize("verb", sorted(VERBS))
def test_minimal_action_is_valid(verb):
    assert set(MINIMAL[verb]) == set(VERBS[verb]["required"])
    assert check_actions(act(verb, **MINIMAL[verb])) == []


@pytest.mark.parametrize("verb", sorted(VERBS))
def test_every_optional_param_is_accepted(verb):
    assert check_actions(act(verb, **MINIMAL[verb], **OPTIONAL_VALUES.get(verb, {}))) == []


@pytest.mark.parametrize(
    ("verb", "param"), [(v, p) for v in sorted(VERBS) for p in VERBS[v]["required"]]
)
def test_dropping_a_required_param_is_missing_param(verb, param):
    params = {k: v for k, v in MINIMAL[verb].items() if k != param}
    reason = f'{verb} requires {", ".join(VERBS[verb]["required"])}; "{param}" is missing'
    assert_errors(
        check_actions(act(verb, **params)),
        E("missing_param", message_for(verb, params, reason), t=1.0, who=params.get("who"), do=verb),
    )


@pytest.mark.parametrize("verb", sorted(VERBS))
def test_a_param_outside_the_vocabulary_is_unexpected(verb):
    allowed = VERBS[verb]["required"] + VERBS[verb]["optional"]
    extra = next(p for p in ANY_VALUE if p not in allowed)
    params = {**MINIMAL[verb], extra: ANY_VALUE[extra]}
    reason = f'{verb} takes {", ".join(allowed)}; "{extra}" is not one of them'
    assert_errors(
        check_actions(act(verb, **params)),
        E("unexpected_param", message_for(verb, params, reason), t=1.0, who=params.get("who"), do=verb),
    )


@pytest.mark.parametrize("verb", sorted(VERBS))
def test_every_disallowed_param_is_reported_in_field_order(verb):
    allowed = VERBS[verb]["required"] + VERBS[verb]["optional"]
    extras = {p: v for p, v in ANY_VALUE.items() if p not in allowed}
    errors = check_actions(act(verb, **MINIMAL[verb], **extras))
    assert [e.code for e in errors] == ["unexpected_param"] * len(extras)
    assert [e.message.rsplit('; "', 1)[1] for e in errors] == [f'{p}" is not one of them' for p in extras]


def test_unexpected_param_matches_the_spec_example():
    assert_errors(
        check_actions(act("wave", t=2, who="dad", what="hymn_books")),
        E("unexpected_param",
          'shot s1, t=2: dad cannot wave "hymn_books"; wave takes who, target, dur; "what" is not one of them',
          t=2.0, who="dad", do="wave"),
    )


def test_missing_who_has_no_who_in_the_head():
    assert_errors(
        check_actions(act("walk_to", to="curb")),
        E("missing_param", 'shot s1, t=1: cannot walk_to; walk_to requires who, to; "who" is missing',
          t=1.0, do="walk_to"),
    )


def test_unknown_verb_from_an_unvalidated_model():
    sp = screenplay(shot(actions=[act("wave", who="dad")]))
    sp.shots[0].actions[0] = Action.model_construct(t=1.0, do="moonwalk", who="dad")
    errors = check(sp)
    assert [e.code for e in errors] == ["unknown_verb"]
    assert errors[0].message.startswith('shot s1, t=1: dad cannot moonwalk; "moonwalk" is not a verb; verbs are walk_to')


# ---------------------------------------------------------------- action_params

def test_action_params_uses_json_names_and_skips_t_do_id_and_none():
    a = Action.model_validate({"t": 2, "do": "enter", "id": "in", "who": "dad", "from": "offstage_left", "to": "curb"})
    assert action_params(a) == {"who": "dad", "to": "curb", "from": "offstage_left"}
    assert list(action_params(a)) == ["who", "to", "from"]
    assert action_params(Action(t=0, do="rain")) == {}


def test_exports():
    assert VERBS is VOCABULARY["actions"]
    assert CAMERA is VOCABULARY["camera"]


# ----------------------------------------------------------- screenplay level

def test_unknown_person_in_the_cast():
    assert_errors(
        check(screenplay(cast=["dad", "ghost"])),
        E("unknown_person",
          'screenplay: cast member "ghost" is not a known person; known people: dad, mom, kid, grandma', shot=None),
    )


def test_unknown_prop_in_the_screenplay_props():
    assert_errors(
        check(screenplay(props=["hymn_books", "ghost_lamp"])),
        E("unknown_prop",
          'screenplay: prop "ghost_lamp" is not a known prop; known props: ' + ", ".join(PROPS), shot=None),
    )


def test_ambiguous_person_and_prop():
    assert_errors(
        check(screenplay(props=["hymn_books", "mom"])),
        E("unknown_prop",
          'screenplay: prop "mom" is not a known prop; known props: ' + ", ".join(PROPS), shot=None),
        E("ambiguous_name", 'screenplay: "mom" is both a cast member and a prop; rename one', shot=None),
    )


def test_duplicate_shot():
    assert_errors(
        check(screenplay(shot(), shot(set="park"), shot(id="s2"))),
        E("duplicate_shot", 'shot s1: another shot already has id "s1"; shot ids must be unique'),
    )


# ---------------------------------------------------------------- shot level

def test_unknown_set_skips_mark_checks():
    assert_errors(
        check_actions(act("walk_to", who="dad", to="somewhere"), set="moon", start={"mom": {"at": "elsewhere"}}),
        E("unknown_set", 'shot s1: set "moon" is not a known set; known sets: church, park'),
    )


def test_shot_marks_extend_and_override_set_marks():
    assert check_actions(act("walk_to", who="dad", to="altar"), act("run_to", t=3, who="dad", to="curb"),
                         marks={"altar": [0.5, 0.2], "curb": [0.95, 0.0]}) == []


def test_ambiguous_mark_names():
    assert_errors(
        check(screenplay(shot(marks={"mom": [0.5, 0.0], "hat": [0.1, 0.0], "spot": [0.2, 0.0]}))),
        E("ambiguous_name", 'shot s1: mark "mom" has the same name as a cast member; rename the mark'),
        E("ambiguous_name", 'shot s1: mark "hat" has the same name as a prop; rename the mark'),
    )


def test_set_mark_shadowing_a_prop():
    sets = {**SETS, "church": SETS["church"].model_copy(update={"marks": {"hymn_books": [0.5, 0.0]}})}
    errors = validate(screenplay(), sets, PROPS, PEOPLE)
    assert_errors(errors, E("ambiguous_name",
                            'shot s1: mark "hymn_books" has the same name as a prop; rename the mark'))


def test_malformed_shot_mark():
    assert_errors(
        check(screenplay(shot(marks={"spot": [0.5], "far": [0.5, 0.0, 0.2, 1.0]}))),
        E("bad_mark", 'shot s1: mark "spot" is [0.5], but a mark is [u, h] or [u, h, d] (numbers)'),
        E("bad_mark", 'shot s1: mark "far" is [0.5, 0.0, 0.2, 1.0], but a mark is [u, h] or [u, h, d] (numbers)'),
    )


def test_shot_mark_out_of_range():
    assert_errors(
        check(screenplay(shot(marks={"pit": [0.5, -0.3], "front": [0.3, 0.0, 4.0], "sky": [0.5, 2, -0.5]}))),
        E("bad_mark", 'shot s1: mark "pit" has h -0.3, but h is the standing surface\'s height above the ground, '
                      "from 0 to 1.5"),
        E("bad_mark", 'shot s1: mark "front" has d 4, but d is a depth from 0 (back of the set) to 1'),
        E("bad_mark", 'shot s1: mark "sky" has h 2, but h is the standing surface\'s height above the ground, '
                      "from 0 to 1.5"),
        E("bad_mark", 'shot s1: mark "sky" has d -0.5, but d is a depth from 0 (back of the set) to 1'),
    )


@pytest.mark.parametrize("mark", [[0.5, 0.0], [0.5, 0.3, 0.7], [0.5, 1.5, 1.0], [0.5, 0.0, 0.0], [-0.2, 0.0],
                                  [1.2, 0.1]])
def test_shot_marks_in_range_are_valid(mark):
    # u is free: an offstage mark lies past the edge
    assert check(screenplay(shot(marks={"spot": mark}))) == []


def test_set_marks_are_not_range_checked():
    sets = {**SETS, "park": SETS["park"].model_copy(update={"marks": {"lawn": [0.5, 2.0, 3.0]}})}
    assert validate(screenplay(shot(set="park")), sets, PROPS, PEOPLE) == []


# ---------------------------------------------------------------------- start

def test_start_unknown_person():
    assert_errors(
        check(screenplay(shot(start={"grandma": {"at": "curb"}}))),
        E("unknown_person", f'shot s1: start names "grandma", who is not in the cast; {CAST_LIST}', who="grandma"),
    )


def test_start_unknown_mark():
    assert_errors(
        check(screenplay(shot(start={"dad": {"at": "nowhere"}}))),
        E("unknown_mark", f'shot s1: dad starts at "nowhere", which is not a mark in this shot; {MARKS_LIST}',
          who="dad"),
    )


def test_start_holding():
    assert_errors(
        check(screenplay(shot(start={"dad": {"at": "curb", "holding": "trumpet"},
                                     "mom": {"at": "curb", "holding": "piano"},
                                     "kid": {"at": "curb", "holding": "hat"}}))),
        E("unknown_prop",
          f"shot s1: dad starts holding \"trumpet\", which is not in the screenplay's props; {PROPS_LIST}", who="dad"),
        E("too_big", 'shot s1: mom starts holding "piano", which is furniture-sized and too big to carry', who="mom"),
    )


def test_start_pose():
    assert check(screenplay(shot(start={"dad": {"at": "curb", "pose": "sit"}}))) == []
    assert_errors(
        check(screenplay(shot(start={"dad": {"at": "curb", "pose": "dance"}}))),
        E("unknown_pose", f'shot s1: dad starts in pose "dance", which is not a pose; {POSES_LIST}', who="dad"),
    )


@pytest.mark.parametrize(("seat", "code", "reason"), [
    ("sedan.driver", None, None),
    ("sedan", "invalid_value", 'dad starts in "sedan", which is not "<prop>.<socket>"; sedan: its sockets are driver, passenger'),
    ("sedan.", "invalid_value", 'dad starts in "sedan.", which is not "<prop>.<socket>"; sedan: its sockets are driver, passenger'),
    ("sedan.roof", "invalid_value", 'dad starts in "sedan.roof", but "sedan" has no socket "roof"; its sockets are driver, passenger'),
    ("trumpet.bell", "unknown_prop", f'dad starts in "trumpet.bell", but "trumpet" is not in the screenplay\'s props; {PROPS_LIST}'),
])
def test_start_in(seat, code, reason):
    errors = check(screenplay(shot(start={"dad": {"at": "curb", "in": seat}})))
    if code is None:
        assert errors == []
    else:
        assert_errors(errors, E(code, f"shot s1: {reason}", who="dad"))


# ------------------------------------------------------------------ propStart

def test_prop_start_unknown_prop_and_mark():
    assert_errors(
        check(screenplay(shot(prop_start={"trumpet": {"at": "curb"}, "hat": {"at": "nowhere"}}))),
        E("unknown_prop", f"shot s1: propStart names \"trumpet\", which is not in the screenplay's props; {PROPS_LIST}"),
        E("unknown_mark", f'shot s1: "hat" starts at "nowhere", which is not a mark in this shot; {MARKS_LIST}'),
    )


@pytest.mark.parametrize(("on", "code", "reason"), [
    ("table.top", None, None),
    ("table", None, None),  # bare prop with sockets: state.py picks a free one
    ("table.shelf", "invalid_value", '"hymn_books" starts on "table.shelf", but "table" has no socket "shelf"; its sockets are top'),
    ("piano", "invalid_value", '"hymn_books" starts on "piano", which is not "<prop>.<socket>"; piano: it has no sockets'),
    ("trumpet.bell", "unknown_prop", f'"hymn_books" starts on "trumpet.bell", but "trumpet" is not in the screenplay\'s props; {PROPS_LIST}'),
])
def test_prop_start_on(on, code, reason):
    errors = check(screenplay(shot(prop_start={"hymn_books": {"on": on}})))
    if code is None:
        assert errors == []
    else:
        assert_errors(errors, E(code, f"shot s1: {reason}"))


def test_prop_start_state_and_at_with_on():
    assert check(screenplay(shot(prop_start={"lamp": {"at": "curb", "state": "lit"},
                                             "hymn_books": {"at": "curb", "state": "anything"}}))) == []
    assert_errors(
        check(screenplay(shot(prop_start={"lamp": {"at": "curb", "on": "table.top", "state": "on"}}))),
        E("invalid_value", 'shot s1: propStart gives "lamp" both at and on; give one'),
        E("invalid_value", 'shot s1: "lamp" starts in state "on", which it does not have; its states are lit, out'),
    )


# --------------------------------------------------------------------- camera

TARGET_MOVES = [m for m in CAMERA if CAMERA[m]["required"] == ["target"]]
FREE_MOVES = [m for m in CAMERA if not CAMERA[m]["required"]]


def test_camera_moves_are_covered():
    assert set(TARGET_MOVES) == {"dolly_to", "pan_to", "close_up"}
    assert set(FREE_MOVES) == {"wide", "pull_back"}
    assert set(TARGET_MOVES) | set(FREE_MOVES) == set(CAMERA)


def cam(*cues: dict) -> list[CompileError]:
    return check(screenplay(shot(camera=list(cues))))


@pytest.mark.parametrize("move", TARGET_MOVES)
def test_target_move_needs_a_target(move):
    for target in ("mom", "hymn_books", "curb"):
        assert cam({"t": 2, "move": move, "target": target, "dur": 1.5}) == []
    assert_errors(
        cam({"t": 2, "move": move}),
        E("missing_param", f'shot s1, t=2: cannot {move}; {move} requires target; "target" is missing',
          t=2.0, do=move),
    )
    assert_errors(
        cam({"t": 2, "move": move, "target": "moon"}),
        E("unknown_target",
          f'shot s1, t=2: cannot {move} "moon"; "moon" is not a person, prop or mark in this shot; '
          f"{CAST_LIST}; {PROPS_LIST}; {MARKS_LIST}", t=2.0, do=move),
    )


@pytest.mark.parametrize("move", FREE_MOVES)
def test_free_move_forbids_a_target(move):
    assert cam({"t": 0, "move": move}, {"t": 3, "move": move, "dur": 2}) == []
    assert_errors(
        cam({"t": 2.5, "move": move, "target": "mom"}),
        E("unexpected_param", f'shot s1, t=2.5: cannot {move} "mom"; {move} takes dur; "target" is not one of them',
          t=2.5, do=move),
    )


def test_camera_target_not_in_the_cast_or_props():
    assert_errors(
        cam({"t": 1, "move": "close_up", "target": "grandma"}, {"t": 4, "move": "pan_to", "target": "trumpet"}),
        E("unknown_person", f'shot s1, t=1: cannot close_up "grandma"; "grandma" is not in the cast; {CAST_LIST}',
          t=1.0, do="close_up"),
        E("unknown_prop",
          f"shot s1, t=4: cannot pan_to \"trumpet\"; \"trumpet\" is not in the screenplay's props; {PROPS_LIST}",
          t=4.0, do="pan_to"),
    )


def test_camera_bad_time():
    assert_errors(
        cam({"t": -1, "move": "wide", "dur": 0}),
        E("bad_time", "shot s1, t=-1: cannot wide; t must be a finite number >= 0, not -1", t=-1.0, do="wide"),
        E("bad_time", "shot s1, t=-1: cannot wide; dur must be a finite number > 0, not 0", t=-1.0, do="wide"),
    )


# -------------------------------------------------------------------- actions

def one(errors: list[CompileError]) -> CompileError:
    assert len(errors) == 1, errors
    return errors[0]


def test_duplicate_action_id():
    assert_errors(
        check_actions(act("wave", t=1, who="dad", id="hi"), act("wave", t=2, who="mom", id="hi")),
        E("duplicate_action_id", 'shot s1, t=2: mom cannot wave; another action in this shot already has id "hi"',
          t=2.0, who="mom", do="wave"),
    )


def test_unknown_who():
    assert_errors(
        check_actions(act("wave", who="ghost"), act("kneel", t=2, who="grandma")),
        E("unknown_person", f'shot s1, t=1: ghost cannot wave; "ghost" is not in the cast; {CAST_LIST}',
          t=1.0, who="ghost", do="wave"),
        E("unknown_person", f'shot s1, t=2: grandma cannot kneel; "grandma" is not in the cast; {CAST_LIST}',
          t=2.0, who="grandma", do="kneel"),
    )


@pytest.mark.parametrize(("verb", "params", "message"), [
    ("hand_to", {"what": "hymn_books", "to": "ghost"},
     f'dad cannot hand_to "hymn_books"; "ghost" is not in the cast; {CAST_LIST}'),
    ("hug", {"target": "grandma"}, f'dad cannot hug; "grandma" is not in the cast; {CAST_LIST}'),
    ("follow", {"target": "ghost"}, f'dad cannot follow; "ghost" is not in the cast; {CAST_LIST}'),
    ("put_on", {"what": "hat", "target": "ghost"}, f'dad cannot put_on "hat"; "ghost" is not in the cast; {CAST_LIST}'),
    ("turn_to", {"target": "grandma"}, f'dad cannot turn_to; "grandma" is not in the cast; {CAST_LIST}'),
    ("confetti", {"at": "grandma"}, f'cannot confetti; "grandma" is not in the cast; {CAST_LIST}'),
])
def test_person_params_must_be_in_the_cast(verb, params, message):
    who = None if verb == "confetti" else "dad"
    a = act(verb, **({"who": who} if who else {}), **params)
    assert_errors(check_actions(a), E("unknown_person", f"shot s1, t=1: {message}", t=1.0, who=who, do=verb))


def test_unknown_prop_in_an_action():
    assert_errors(
        check_actions(act("pick_up", who="dad", what="trumpet")),
        E("unknown_prop",
          f"shot s1, t=1: dad cannot pick_up \"trumpet\"; \"trumpet\" is not in the screenplay's props; {PROPS_LIST}",
          t=1.0, who="dad", do="pick_up"),
    )


def test_listed_prop_missing_from_the_scene_is_reported_once():
    props = {k: v for k, v in PROPS.items() if k != "chapel"}
    errors = validate(screenplay(shot(actions=[act("pick_up", who="dad", what="chapel")])), SETS, props, PEOPLE)
    assert [e.code for e in errors] == ["unknown_prop"]
    assert errors[0].message.startswith('screenplay: prop "chapel" is not a known prop')


@pytest.mark.parametrize(("verb", "params", "reason"), [
    ("walk_to", {"to": "nowhere"}, f'"nowhere" is not a mark in this shot; {MARKS_LIST}'),
    ("run_to", {"to": "mom"}, f'"to" must be a mark, and "mom" is a person; {MARKS_LIST}'),
    ("walk_to", {"to": "hat"}, f'"to" must be a mark, and "hat" is a prop; {MARKS_LIST}'),
    ("enter", {"to": "kid"}, f'"to" must be a mark, and "kid" is a person; {MARKS_LIST}'),
    # place_on onto a person is bad_target: test_place_on_a_person_is_bad_target
    ("enter", {"to": "nowhere"}, f'"nowhere" is not a mark in this shot; {MARKS_LIST}'),
    # exit's "to" stays unknown_mark: tests/agent/test_agent_skills.py pins it (validate.check_offstage)
    ("exit", {"to": "curb"}, '"to" must be offstage_left or offstage_right, not "curb"'),
    ("place_on", {"what": "hymn_books", "on": "nowhere"},
     f'"nowhere" is not a prop in the screenplay or a mark in this shot; {PROPS_LIST}; {MARKS_LIST}'),
])
def test_unknown_mark(verb, params, reason):
    a = act(verb, who="dad", **params)
    assert_errors(check_actions(a), E("unknown_mark", message_for(verb, a, reason), t=1.0, who="dad", do=verb))


def test_enter_from_a_mark_on_stage_is_bad_exit():
    a = act("enter", who="dad", to="curb", **{"from": "curb"})
    assert_errors(check_actions(a), E(
        "bad_exit", 'shot s1, t=1: dad cannot enter; "from" must be offstage_left or offstage_right, not "curb"',
        t=1.0, who="dad", do="enter"))
    assert check_actions(act("enter", who="dad", to="curb", **{"from": "offstage_right"})) == []


@pytest.mark.parametrize("verb", ["drive_to", "ride_to"])
def test_vehicle_destination_must_be_a_mark(verb):
    a = act(verb, who="dad", what="sedan", to="church_door")
    assert_errors(check_actions(a), E(
        "unknown_mark", message_for(verb, a, f'"church_door" is not a mark in this shot; {MARKS_LIST}'),
        t=1.0, who="dad", do=verb))


def test_place_on_a_mark_is_valid():
    assert check_actions(act("place_on", who="dad", what="hymn_books", on="steps_top")) == []


@pytest.mark.parametrize("verb", ["turn_to", "point_at", "lean_toward", "wave"])
def test_mixed_targets(verb):
    for target in ("mom", "hymn_books", "steps_top", "offstage_left"):
        assert check_actions(act(verb, who="dad", target=target)) == [], target
    assert_errors(
        check_actions(act(verb, who="dad", target="moon")),
        E("unknown_target",
          f'shot s1, t=1: dad cannot {verb}; "moon" is not a person, prop or mark in this shot; '
          f"{CAST_LIST}; {PROPS_LIST}; {MARKS_LIST}", t=1.0, who="dad", do=verb),
    )


@pytest.mark.parametrize(("verb", "target", "reason"), [
    ("hug", "hat", '"target" must be a person in the cast, and "hat" is a prop'),
    ("follow", "curb", '"target" must be a person in the cast, and "curb" is a mark'),
    ("put_on", "curb", '"target" must be a person in the cast, and "curb" is a mark'),
])
def test_person_targets_reject_props_and_marks(verb, target, reason):
    a = act(verb, who="dad", target=target, **({"what": "hat"} if verb == "put_on" else {}))
    assert_errors(check_actions(a), E("bad_target", message_for(verb, a, reason), t=1.0, who="dad", do=verb))


def test_a_person_target_that_is_nothing_at_all_is_unknown_person():
    a = act("hug", who="dad", target="ghost")
    reason = f'"ghost" is not in the cast; {CAST_LIST}'
    assert_errors(check_actions(a), E("unknown_person", message_for("hug", a, reason), t=1.0, who="dad", do="hug"))


def test_hand_to_a_prop_is_bad_target():
    assert_errors(
        check_actions(act("hand_to", who="dad", what="hymn_books", to="table")),
        E("bad_target",
          'shot s1, t=1: dad cannot hand_to "hymn_books"; "to" must be a person in the cast, and "table" is a prop',
          t=1.0, who="dad", do="hand_to"),
    )


@pytest.mark.parametrize("verb", ["confetti", "steam", "smoke"])
def test_effect_at(verb):
    for at in ("mom", "hymn_books", "curb"):
        assert check_actions(act(verb, at=at, dur=2)) == []
    assert_errors(
        check_actions(act(verb, at="moon")),
        E("unknown_target",
          f'shot s1, t=1: cannot {verb}; "moon" is not a person, prop or mark in this shot; '
          f"{CAST_LIST}; {PROPS_LIST}; {MARKS_LIST}", t=1.0, do=verb),
    )


def test_rain_takes_no_at():
    assert_errors(
        check_actions(act("rain", at="curb")),
        E("unexpected_param", 'shot s1, t=1: cannot rain; rain takes dur; "at" is not one of them', t=1.0, do="rain"),
    )


@pytest.mark.parametrize("value", VOCABULARY["timesOfDay"])
def test_time_of_day_values(value):
    assert check_actions(act("time_of_day", value=value)) == []


def test_time_of_day_bad_value():
    assert_errors(
        check_actions(act("time_of_day", value="noon")),
        E("invalid_value",
          'shot s1, t=1: cannot time_of_day; "noon" is not a time of day; use one of dawn, day, dusk, night',
          t=1.0, do="time_of_day"),
    )


def test_unknown_pose():
    assert_errors(
        check_actions(act("pose", who="mom", pose="dance")),
        E("unknown_pose", f'shot s1, t=1: mom cannot pose; "dance" is not a pose; {POSES_LIST}',
          t=1.0, who="mom", do="pose"),
    )


def test_set_state():
    assert check_actions(act("set_state", what="hymn_books", state="anything")) == []  # declares no states
    assert_errors(
        check_actions(act("set_state", what="lamp", state="on")),
        E("invalid_value", 'shot s1, t=1: cannot set_state "lamp"; "lamp" has no state "on"; its states are lit, out',
          t=1.0, do="set_state"),
    )


@pytest.mark.parametrize(("verb", "state"), [("open", "open"), ("close", "closed")])
def test_open_close_need_the_state(verb, state):
    assert check_actions(act(verb, who="kid", what="hymn_books")) == []  # declares no states
    assert_errors(
        check_actions(act(verb, who="kid", what="lamp")),
        E("unknown_state",
          f'shot s1, t=1: kid cannot {verb} "lamp"; "lamp" has no state "{state}"; its states are lit, out',
          t=1.0, who="kid", do=verb),
    )


@pytest.mark.parametrize(("verb", "params", "reason"), [
    ("sit_on", {"what": "chair", "socket": "back"}, '"chair" has no socket "back"; its sockets are seat'),
    ("get_in", {"what": "sedan", "socket": "roof"}, '"sedan" has no socket "roof"; its sockets are driver, passenger'),
    ("place_on", {"what": "hymn_books", "on": "table", "socket": "shelf"}, '"table" has no socket "shelf"; its sockets are top'),
    ("place_on", {"what": "hymn_books", "on": "curb", "socket": "top"},
     'socket only applies when placing on a prop, and "curb" is a mark'),
])
def test_bad_sockets(verb, params, reason):
    a = act(verb, who="dad", **params)
    assert_errors(check_actions(a), E("invalid_value", message_for(verb, a, reason), t=1.0, who="dad", do=verb))


def test_place_on_a_prop_with_no_sockets_is_no_socket():
    # The same code and words as verbs_objects' runtime check.
    a = act("place_on", who="dad", what="hymn_books", on="piano")
    reason = '"piano" has no sockets to place on'
    assert_errors(check_actions(a), E("no_socket", message_for("place_on", a, reason), t=1.0, who="dad", do="place_on"))


def test_place_on_itself_is_one_self_target_error():
    # hymn_books has no sockets, but no_socket would only repeat the problem
    a = act("place_on", who="dad", what="hymn_books", on="hymn_books")
    assert_errors(check_actions(a), E("self_target", message_for("place_on", a, '"on" is hymn_books itself'),
                                      t=1.0, who="dad", do="place_on"))
    # nor does a socket the prop has (or lacks) add a second error
    for socket in ("top", "shelf"):
        a = act("place_on", who="dad", what="table", on="table", socket=socket)
        assert [e.code for e in check_actions(a)] == ["self_target"], socket


def test_place_on_a_person_is_bad_target():
    a = act("place_on", who="dad", what="hymn_books", on="mom")
    reason = f'"on" must be a prop or a mark, and "mom" is a person; {PROPS_LIST}; {MARKS_LIST}'
    assert_errors(check_actions(a), E("bad_target", message_for("place_on", a, reason), t=1.0, who="dad", do="place_on"))


def test_times_over_the_cap():
    assert MAX_SECONDS == 600
    errors = check_actions(act("wave", t=1e7, who="dad"), act("wave", t=1, who="mom", dur=1e9),
                           act("rain", t=1, dur=1e12))
    assert [(e.code, e.message) for e in errors] == [
        ("bad_time", "shot s1, t=10000000: dad cannot wave; t must be at most 600 s, not 1e+07"),
        ("bad_dur", "shot s1, t=1: mom cannot wave; dur must be at most 600 s, not 1e+09"),
        ("bad_dur", "shot s1, t=1: cannot rain; dur must be at most 600 s, not 1e+12"),
    ]


def test_times_at_the_cap_are_valid():
    assert check_actions(act("wave", t=600, who="dad", dur=600), act("rain", t=600, dur=600)) == []
    assert check(screenplay(shot(camera=[{"t": 600, "move": "wide", "dur": 600}]))) == []
    assert check(screenplay(shot(narration=[line(600, "At the end.")]))) == []


def test_camera_and_narration_times_over_the_cap():
    assert_errors(
        cam({"t": 601, "move": "wide", "dur": 700}),
        E("bad_time", "shot s1, t=601: cannot wide; t must be at most 600 s, not 601", t=601.0, do="wide"),
        E("bad_dur", "shot s1, t=601: cannot wide; dur must be at most 600 s, not 700", t=601.0, do="wide"),
    )
    assert_errors(check(screenplay(shot(narration=[line(6000, "Much later.")]))), E(
        "bad_time", "shot s1, dad's narration line 0: t must be at most 600 s, not 6000"))


def test_bad_time():
    # NaN != NaN, so compare fields rather than whole dumps.
    errors = check_actions(act("wave", t=-0.5, who="dad"), act("kneel", t=2, who="mom", dur=0),
                           act("kneel", t=math.nan, who="kid", dur=math.inf))
    assert [e.message for e in errors] == [
        "shot s1, t=-0.5: dad cannot wave; t must be a finite number >= 0, not -0.5",
        "shot s1, t=2: mom cannot kneel; dur must be a finite number > 0, not 0",
        "shot s1, t=nan: kid cannot kneel; t must be a finite number >= 0, not nan",
        "shot s1, t=nan: kid cannot kneel; dur must be a finite number > 0, not inf",
    ]
    assert [e.code for e in errors] == ["bad_time"] * 4
    assert [(e.shot, e.who, e.do) for e in errors] == [("s1", "dad", "wave"), ("s1", "mom", "kneel"),
                                                       ("s1", "kid", "kneel"), ("s1", "kid", "kneel")]
    # A t that is not finite is left out of CompileError.t (JSON has no nan); the message keeps it.
    assert errors[0].t == -0.5 and errors[1].t == 2.0 and errors[2].t is None and errors[3].t is None


@pytest.mark.parametrize("verb", ["pick_up", "hold", "drop", "hand_to", "put_on"])
@pytest.mark.parametrize(("what", "size"), [("piano", "furniture"), ("sedan", "vehicle"), ("chapel", "building")])
def test_too_big(verb, what, size):
    a = act(verb, who="dad", what=what, **({"to": "mom"} if verb == "hand_to" else {}))
    assert_errors(check_actions(a), E(
        "too_big", f'shot s1, t=1: dad cannot {verb} "{what}"; "{what}" is {size}-sized and too big to carry',
        t=1.0, who="dad", do=verb))


@pytest.mark.parametrize("verb", ["get_in", "get_out", "drive_to", "ride_to"])
def test_not_a_vehicle(verb):
    a = act(verb, who="dad", what="chair", **({"to": "curb"} if verb in ("drive_to", "ride_to") else {}))
    assert_errors(check_actions(a), E(
        "not_a_vehicle", f'shot s1, t=1: dad cannot {verb} "chair"; "chair" is furniture-sized, not a vehicle',
        t=1.0, who="dad", do=verb))


def test_no_seat():
    assert_errors(
        check_actions(act("sit_on", who="dad", what="piano")),
        E("no_seat", 'shot s1, t=1: dad cannot sit_on "piano"; "piano" has no sockets to sit on',
          t=1.0, who="dad", do="sit_on"),
    )


def test_sit_on_a_vehicle_seat_is_allowed_statically():
    assert check_actions(act("sit_on", who="dad", what="sedan", socket="passenger")) == []


@pytest.mark.parametrize(("verb", "params", "reason"), [
    ("hand_to", {"what": "hymn_books", "to": "dad"}, '"to" is dad themselves'),
    ("hug", {"target": "dad"}, '"target" is dad themselves'),
    ("follow", {"target": "dad"}, '"target" is dad themselves'),
    ("place_on", {"what": "table", "on": "table"}, '"on" is table itself'),
])
def test_self_target(verb, params, reason):
    a = act(verb, who="dad", **params)
    assert_errors(check_actions(a), E("self_target", message_for(verb, a, reason), t=1.0, who="dad", do=verb))


def test_put_on_oneself_is_fine():
    assert check_actions(act("put_on", who="dad", what="hat", target="dad")) == []


# ------------------------------------------------------------------ narration

def line(t: float, text: str, **kw) -> dict:
    return {"t": t, "text": text, **kw}


DROP_SHOT = {
    "start": {"mom": {"at": "steps_top", "holding": "hymn_books"}},
    "actions": [act("drop", t=2, who="mom", what="hymn_books", id="drop"), act("wave", t=4, who="dad")],
}


def test_valid_cues_in_every_telling():
    sp = screenplay(
        shot(**DROP_SHOT, narration=[line(0, "Then she dropped them all.", cue="drop", cueWord="dropped")]),
        narrations={"mom": {"s1": [line(0, "I dropped every book.", cue="drop", cueWord="dropped"),
                                   line(2, "He waved.", cue="wave")]}},
    )
    assert check(sp) == []


def test_default_telling_unknown_cue():
    sp = screenplay(shot(**DROP_SHOT, narration=[line(0, "Hello."), line(1, "And then he jumped.", cue="jump")]))
    assert_errors(check(sp), E(
        "cue_unknown_action",
        "shot s1, dad's narration line 1: cue \"jump\" matches no action id and no action verb in this shot; "
        "the action ids in this shot are drop; its verbs are drop, wave"))


def test_other_telling_unknown_cue():
    sp = screenplay(
        shot(**DROP_SHOT, narration=[line(0, "She dropped them.", cue="drop")]),
        narrations={"mom": {"s1": [line(0, "I fell.", cue="fall"), line(1, "He hugged me.", cue="hug")]}},
    )
    assert_errors(
        check(sp),
        E("cue_unknown_action",
          "shot s1, mom's narration line 0: cue \"fall\" matches no action id and no action verb in this shot; "
          "the action ids in this shot are drop; its verbs are drop, wave"),
        E("cue_unknown_action",
          "shot s1, mom's narration line 1: cue \"hug\" matches no action id and no action verb in this shot; "
          "the action ids in this shot are drop; its verbs are drop, wave"),
    )


def test_cue_word_missing_in_any_telling():
    sp = screenplay(
        shot(**DROP_SHOT, narration=[line(0, "She let go.", cue="drop", cueWord="dropped")]),
        narrations={"mom": {"s1": [line(0, "Oops.", cue="drop", cueWord="fell")]}},
    )
    assert_errors(
        check(sp),
        E("cue_word_missing",
          'shot s1, dad\'s narration line 0: cueWord "dropped" is not a word of "She let go."; '
          "its words are She, let, go"),
        E("cue_word_missing",
          'shot s1, mom\'s narration line 0: cueWord "fell" is not a word of "Oops."; its words are Oops'),
    )


def test_cue_conflict_in_the_default_telling():
    sp = screenplay(shot(**DROP_SHOT, narration=[line(0, "She dropped them.", cue="drop"),
                                                 line(2, "All of them.", cue="drop")]))
    assert_errors(check(sp), E(
        "cue_conflict",
        "shot s1, dad's narration line 1: action 0 (drop) is already cued by narration line 0; "
        "an action can land on only one word"))


def test_other_telling_cue_before_start():
    sp = screenplay(
        shot(**DROP_SHOT, narration=[line(0, "Dropped.", cue="drop")]),
        narrations={"mom": {"s1": [line(0, "one two three four five dropped", cue="drop")]}},
    )
    assert_errors(check(sp), E(
        "cue_before_start",
        'shot s1, mom\'s narration line 0: to land its cue word on the action, "one two three four five dropped" '
        "would start at shot t=-1.25, before the shot starts; shorten the words before the cue word or move the "
        "action later"))


# The default telling overlaps: line 0 is said until t=1 (last word 0.75 + WORD_SECONDS),
# but line 1 starts at t=0.5. Line 0 cues the drop, which moves to its first word (t=0).
OVERLAPPING_DEFAULT = [line(0, "Dropped one two three", cue="drop", cueWord="Dropped"), line(0.5, "four")]
DEFAULT_OVERLAP = E(
    "lines_overlap",
    "shot s1, dad's narration line 1: line 1 starts at shot t=0.5, before line 0 has been said "
    "(its last word ends at shot t=1); start line 1 later or shorten line 0")


def test_default_overlap_with_no_other_teller_is_static():
    sp = screenplay(shot(**DROP_SHOT, narration=OVERLAPPING_DEFAULT))
    assert_errors(check(sp), DEFAULT_OVERLAP)


def test_default_overlap_message_is_narrations_own():
    from screenplay import narration as N

    sp = screenplay(shot(**DROP_SHOT, narration=OVERLAPPING_DEFAULT))
    s = sp.shots[0]
    with pytest.raises(N.NarrationError) as raised:
        N.build_shot_narration(s, 0.0, N.retime_actions(s, 0.0, author="dad"), {}, "dad")
    (err,) = check(sp)
    assert (err.code, err.message) == (raised.value.code, raised.value.message)


def test_default_overlap_is_reported_once_with_two_clean_tellers():
    sp = screenplay(
        shot(**DROP_SHOT, narration=OVERLAPPING_DEFAULT),
        narrations={"mom": {"s1": [line(0, "I let go.")]}, "kid": {"s1": [line(0, "Books everywhere.")]}},
    )
    assert_errors(check(sp), DEFAULT_OVERLAP)


def test_each_tellers_own_error_comes_before_the_one_default_overlap():
    sp = screenplay(
        shot(**DROP_SHOT, narration=OVERLAPPING_DEFAULT),
        narrations={
            "mom": {"s1": [line(0, "a b c d"), line(0.2, "e")]},
            "kid": {"s1": [line(0, "one two three four five dropped", cue="drop")]},
        },
    )
    assert_errors(
        check(sp),
        E("lines_overlap",
          "shot s1, mom's narration line 1: line 1 starts at shot t=0.2, before line 0 has been said "
          "(its last word ends at shot t=1); start line 1 later or shorten line 0"),
        E("cue_before_start",
          'shot s1, kid\'s narration line 0: to land its cue word on the action, "one two three four five dropped" '
          "would start at shot t=-1.25, before the shot starts; shorten the words before the cue word or move the "
          "action later"),
        DEFAULT_OVERLAP,
    )


def test_teller_overlap_alone_does_not_report_the_clean_default():
    sp = screenplay(
        shot(**DROP_SHOT, narration=[line(0, "Dropped.", cue="drop")]),
        narrations={"mom": {"s1": [line(0, "a b c d"), line(0.2, "e")]}},
    )
    assert [(e.code, e.message.split(":")[0]) for e in check(sp)] == [
        ("lines_overlap", "shot s1, mom's narration line 1")]


def test_no_default_lines_needs_no_default_check():
    sp = screenplay(shot(**DROP_SHOT), narrations={"mom": {"s1": [line(0, "I dropped them.", cue="drop")]}})
    assert check(sp) == []


def test_default_overlap_is_not_checked_when_its_lines_fail():
    # A per-line problem in the default telling stops its timing (no retime, no overlap check).
    sp = screenplay(shot(**DROP_SHOT, narration=[line(0, "one two three four"), line(0.5, "jumped", cue="jump")]))
    assert [e.code for e in check(sp)] == ["cue_unknown_action"]


def test_default_overlap_is_checked_in_every_shot():
    sp = screenplay(
        shot(**DROP_SHOT, narration=OVERLAPPING_DEFAULT),
        shot(id="s2", **DROP_SHOT, narration=OVERLAPPING_DEFAULT),
        narrations={"mom": {"s1": [line(0, "Hi.")], "s2": [line(0, "Hi.")]}},
    )
    assert [(e.code, e.shot) for e in check(sp)] == [("lines_overlap", "s1"), ("lines_overlap", "s2")]


def test_compile_stops_at_a_static_default_overlap():
    from screenplay.compiler import compile as compile_screenplay

    sp = screenplay(shot(
        start={"mom": {"at": "steps_top", "holding": "hymn_books"}},
        # dad drops what he never held: a state error compile() would find if it simulated
        actions=[act("drop", t=2, who="mom", what="hymn_books", id="drop"),
                 act("drop", t=4, who="dad", what="hymn_books")],
        narration=OVERLAPPING_DEFAULT,
    ))
    static = check(sp)
    assert_errors(static, DEFAULT_OVERLAP)
    res = compile_screenplay(sp, sets=SETS, props=PROPS, people=PEOPLE)
    assert res.ok is False
    assert res.errors == static


def test_narration_line_bad_time():
    sp = screenplay(shot(narration=[line(-1, "Too early.")]))
    assert_errors(check(sp), E(
        "bad_time", "shot s1, dad's narration line 0: t must be a finite number >= 0, not -1"))


def test_telling_unknown_shot():
    sp = screenplay(shot(), shot(id="s2", set="park"), narrations={"mom": {"s1": [], "s9": [line(0, "Hi.")]}})
    assert_errors(check(sp), E(
        "telling_unknown_shot",
        'screenplay: mom\'s telling has lines for shot "s9", which is not in the screenplay; the shots are s1, s2',
        shot=None))


def test_duplicate_teller():
    sp = screenplay(shot(narration=[line(0, "Hi.", source="telling:dad:s1")]),
                    narrations={"dad": {"s1": [line(0, "Hello.")]}})
    assert_errors(check(sp), E(
        "duplicate_telling",
        "screenplay: dad tells the default narration (Shot.narration) and also has a telling in "
        "Screenplay.narrations; give each teller one telling", shot=None))


# ------------------------------------------------------------- explanations

def test_explanations():
    sp = screenplay(explanations=[
        {"target": "puppet:dad", "text": "ok"},
        {"target": "prop:hymn_books", "text": "ok"},
        {"target": "dad", "text": "bad"},
        {"target": "set:church", "text": "bad"},
        {"target": "puppet:grandma", "text": "unknown"},
        {"target": "prop:trumpet", "text": "unknown"},
    ])
    assert_errors(
        check(sp),
        E("explanation_bad_target",
          'screenplay: explanation 2 targets "dad"; a target is "puppet:<personId>" or "prop:<propId>"', shot=None),
        E("explanation_bad_target",
          'screenplay: explanation 3 targets "set:church"; a target is "puppet:<personId>" or "prop:<propId>"',
          shot=None),
        E("explanation_unknown_target",
          f'screenplay: explanation 4 targets "puppet:grandma", but "grandma" is not in the cast; {CAST_LIST}',
          shot=None),
        E("explanation_unknown_target",
          f"screenplay: explanation 5 targets \"prop:trumpet\", but \"trumpet\" is not in the screenplay's props; "
          f"{PROPS_LIST}", shot=None),
    )


# -------------------------------------------------------------- interactions

def tab(drives: dict, shot_id: str | None = "s1", iid: str = "tab") -> dict:
    return {"id": iid, "kind": "pull_tab", "label": "Pull", "shot": shot_id, "at": [0.5, 0.5], "drives": drives}


def test_valid_interactions():
    sp = screenplay(interactions=[
        tab({"type": "scrub", "from": 1, "to": 2,
             "targets": ["puppet:mom", "prop:hymn_books", "set:church.layer2", "camera"]}),
        tab({"type": "scrub", "from": 0, "to": 1, "targets": ["set:church.layer0"]}, shot_id=None),
        tab({"type": "prop_state", "prop": "lamp", "states": ["lit", "out"]}),
        tab({"type": "prop_state", "prop": "hymn_books", "states": ["anything"]}),
        tab({"type": "prop_part", "prop": "clock", "part": "hand", "channel": "rot", "range": [0, 6.28]}),
    ])
    assert check(sp) == []


def test_interaction_unknown_shot():
    sp = screenplay(interactions=[tab({"type": "scrub", "from": 1, "to": 2}, shot_id="s7", iid="pull")])
    assert_errors(check(sp), E(
        "interaction_unknown_shot",
        'screenplay: interaction "pull" belongs to shot "s7", which is not in the screenplay; the shots are s1',
        shot=None))


def test_interaction_scrub_targets():
    sp = screenplay(interactions=[tab({"type": "scrub", "from": 1, "to": 2, "targets": [
        "mom", "puppet:grandma", "prop:trumpet", "set:park.layer0", "set:church.layer3", "set:church", "light:sun",
    ]})])
    lead = 'screenplay: interaction "tab" scrubs'
    shape = ('a scrub target is "puppet:<personId>", "prop:<propId>", "set:<setId>.layer<n>" or "camera"')
    assert_errors(
        check(sp),
        E("interaction_bad_target", f'{lead} "mom"; {shape}', shot=None),
        E("interaction_bad_target", f'{lead} "puppet:grandma", but "grandma" is not in the cast; {CAST_LIST}',
          shot=None),
        E("interaction_bad_target",
          f"{lead} \"prop:trumpet\", but \"trumpet\" is not in the screenplay's props; {PROPS_LIST}", shot=None),
        E("interaction_bad_target", f"{lead} \"set:park.layer0\", but shot s1's set is church", shot=None),
        E("interaction_bad_target", f'{lead} "set:church.layer3", but church has 3 layers (layer0 to layer2)',
          shot=None),
        E("interaction_bad_target", f'{lead} "set:church"; {shape}', shot=None),
        E("interaction_bad_target", f'{lead} "light:sun"; {shape}', shot=None),
    )


def test_interaction_scrub_times():
    sp = screenplay(interactions=[tab({"type": "scrub", "from": -1, "to": 2}),
                                  tab({"type": "scrub", "from": 3, "to": 2}, iid="back")])
    assert_errors(
        check(sp),
        E("bad_time", 'screenplay: interaction "tab": scrub "from" must be a finite number >= 0, not -1', shot=None),
        E("bad_time", 'screenplay: interaction "back": scrub "from" (3) must be before "to" (2)', shot=None),
    )


def test_interaction_prop_drives():
    sp = screenplay(interactions=[
        tab({"type": "prop_state", "prop": "trumpet"}, iid="a"),
        tab({"type": "prop_state", "prop": "lamp", "states": ["lit", "blazing"]}, iid="b"),
        tab({"type": "prop_part", "prop": "clock", "part": "pendulum"}, iid="c"),
        tab({"type": "prop_part", "prop": "hat", "part": "brim"}, iid="d"),
        tab({"type": "prop_part", "prop": "clock"}, iid="e"),
        tab({"type": "prop_state"}, iid="f"),
    ])
    assert_errors(
        check(sp),
        E("interaction_unknown_prop",
          f"screenplay: interaction \"a\" drives \"trumpet\", which is not in the screenplay's props; {PROPS_LIST}",
          shot=None),
        E("invalid_value",
          'screenplay: interaction "b" steps "lamp" to state "blazing", which it does not have; its states are lit, out',
          shot=None),
        E("interaction_unknown_part", 'screenplay: interaction "c" drives part "pendulum" of "clock"; its parts are hand',
          shot=None),
        E("interaction_unknown_part", 'screenplay: interaction "d" drives part "brim" of "hat"; it has no moving parts',
          shot=None),
        E("missing_param", 'screenplay: interaction "e" drives prop_part, which requires prop, part; "part" is missing',
          shot=None),
        E("missing_param", 'screenplay: interaction "f" drives prop_state, which requires prop; "prop" is missing',
          shot=None),
    )


# ------------------------------------------------------------------- ordering

def test_every_problem_is_reported_in_order():
    sp = screenplay(
        shot(
            set="church",
            marks={"kid": [0.4, 0.0]},
            start={"ghost": {"at": "curb"}, "dad": {"at": "nowhere"}},
            prop_start={"trumpet": {"at": "curb"}},
            camera=[{"t": 1, "move": "dolly_to"}],
            actions=[act("walk_to", t=1, who="dad", to="nowhere"), act("pick_up", t=2, who="mom", what="piano")],
            narration=[line(0, "Then.", cue="jump")],
        ),
        shot(id="s2", set="moon", actions=[act("hug", t=0, who="dad", target="dad")]),
        shot(id="s1", set="park"),
        cast=["dad", "mom", "kid", "ghost2"],
        narrations={"mom": {"s5": []}},
        explanations=[{"target": "nobody", "text": "?"}],
        interactions=[tab({"type": "scrub", "from": 0, "to": 1}, shot_id="s5")],
    )
    got = [(e.code, e.shot, e.t) for e in check(sp)]
    assert got == [
        ("unknown_person", None, None),          # cast
        ("telling_unknown_shot", None, None),    # tellings
        ("explanation_bad_target", None, None),  # explanations
        ("interaction_unknown_shot", None, None),
        ("ambiguous_name", "s1", None),          # shot s1: marks
        ("unknown_person", "s1", None),          # start
        ("unknown_mark", "s1", None),
        ("unknown_prop", "s1", None),            # propStart
        ("missing_param", "s1", 1.0),            # camera
        ("unknown_mark", "s1", 1.0),             # actions in order
        ("too_big", "s1", 2.0),
        ("cue_unknown_action", "s1", None),      # narration
        ("unknown_set", "s2", None),             # shot s2
        ("self_target", "s2", 0.0),
        ("duplicate_shot", "s1", None),          # third shot reuses s1
    ]


# ------------------------------------------------------------- one message grammar

def test_validate_builds_its_messages_with_errors_error_message():
    import screenplay.validate as V
    from screenplay.errors import error_message

    assert not hasattr(V, "_message")
    cases = [
        (V._Site(), None, None, None, None, None),
        (V._Site(shot="s1"), "s1", None, None, None, None),
        (V._Site(shot="s1", who="dad"), "s1", None, "dad", None, None),
        (V._Site(shot="s1", t=4.7449, do="pan_to", what="mom"), "s1", 4.7449, None, "pan_to", "mom"),
        (V._Site(shot="s1", t=5, who="dad", do="hand_to", what="hymn_books"), "s1", 5, "dad", "hand_to", "hymn_books"),
    ]
    for site, shot, t, who, do, what in cases:
        err = site.error("code", "the reason")
        assert err.message == error_message(shot, t, who, do, what, "the reason")
        assert (err.shot, err.t, err.who, err.do) == (shot, t, who, do)
    assert V._Site(shot="s1", t=4.7449, do="pan_to", what="mom").error("c", "r").message == (
        'shot s1, t=4.74: cannot pan_to "mom"; r'
    )


# ------------------------------------------------------------------- bug hunt (wave 3)
#
# validate() on its own, over the compile fuzz's random corpus and hand-built shapes: every
# input gives a finite list of CompileErrors whose codes are in errors.ERROR_CODES, whose hints
# format, and whose shot / t / who / do agree with the message head
# (test_compile_fuzz.error_site_problems), and which round-trip through JSON unchanged.

def _hunt_problems(sp: Screenplay, errors: list[CompileError]) -> list[str]:
    from screenplay.errors import ERROR_CODES, hint_for
    from screenplay.schema import CompileResult
    from test_compile_fuzz import error_problems, error_site_problems

    out: list[str] = []
    for e in errors:
        if e.code not in ERROR_CODES:
            out.append(f"code {e.code!r} is not in ERROR_CODES: {e.message}")
        hint_for(e)  # never raises
    if errors:
        result = CompileResult(ok=False, errors=errors)
        out += error_problems(result) + error_site_problems(sp, result)
        dumped = result.model_dump_json()
        if CompileResult.model_validate_json(dumped).model_dump_json() != dumped:
            out.append("the errors do not round-trip through JSON")
    return out


def test_validate_alone_keeps_its_invariants_on_the_random_fuzz_corpus():
    from test_compile_fuzz import generate, scene_for

    scene = scene_for("random")
    seen: set[str] = set()
    for i in range(3000):
        sample = generate("random", i)
        if sample.screenplay is None:
            continue
        errors = validate(sample.screenplay, scene.sets, scene.props, scene.people)
        seen.update(e.code for e in errors)
        assert _hunt_problems(sample.screenplay, errors) == [], i
    assert len(seen) >= 25


WRONG_KINDS = ["mom", "dad", "hat", "chair", "sedan", "lamp", "curb", "steps_top", "offstage_left", "zzz", "",
               "wave", "open", "seat", "hand_r", "dusk", "table.top", "prop:hat", "puppet:mom", "été", " curb"]
VALID = {"who": "dad", "to": "curb", "from": "offstage_left", "what": "hat", "target": "mom", "on": "table",
         "socket": "top", "pose": "wave", "state": "lit", "at": "curb", "value": "dusk", "dur": 2.0}


@pytest.mark.parametrize("verb", sorted(VERBS))
def test_every_parameter_of_every_verb_given_the_wrong_kind_of_value(verb):
    spec = VERBS[verb]
    params = [*spec["required"], *spec["optional"]]
    for p in [*params, "who", "what", "to", "on", "target", "at", "socket"]:
        values: list[object] = [0.0, -1.0, 1e9, math.nan, math.inf] if p == "dur" else [*WRONG_KINDS, None]
        for value in values:
            a = act(verb, **{q: VALID[q] for q in params})
            if value is None:
                a.pop(p, None)
            else:
                a[p] = value
            sp = screenplay(shot(start={"dad": {"at": "steps_left"}}, prop_start={"table": {"at": "curb"}},
                                 actions=[a]))
            assert _hunt_problems(sp, check(sp)) == [], (p, value)


def test_what_must_be_a_prop_not_a_person_or_a_mark():
    a = act("pick_up", who="dad", what="mom")
    b = act("drop", t=2, who="dad", what="curb")
    assert_errors(
        check_actions(a, b),
        E("unknown_prop", message_for("pick_up", a, f'"what" must be a prop, and "mom" is a person; {PROPS_LIST}'),
          t=1.0, who="dad", do="pick_up"),
        E("unknown_prop",
          'shot s1, t=2: dad cannot drop "curb"; "what" must be a prop, and "curb" is a mark; ' + PROPS_LIST,
          t=2.0, who="dad", do="drop"),
    )


def test_a_name_of_two_kinds_is_reported_once_as_ambiguous():
    """A mark named like a person, used as a mark, and a prop named like a mark, used as a prop:
    ambiguous_name says so; the action is not also told the name is of the wrong kind."""
    sp = screenplay(shot(marks={"mom": [0.5, 0.0], "hat": [0.1, 0.0]},
                         actions=[act("walk_to", who="dad", to="mom"), act("pick_up", t=2, who="dad", what="hat")]))
    assert [e.code for e in check(sp)] == ["ambiguous_name", "ambiguous_name"]


def test_a_mark_parameter_in_a_shot_on_an_unknown_set_says_nothing_more():
    """The set is unknown, so its marks are too: no cascade, and no crash naming a person."""
    sp = screenplay(shot(set="moon", actions=[act("walk_to", who="dad", to="mom"),
                                              act("place_on", t=2, who="dad", what="hat", on="kid")]))
    assert [e.code for e in check(sp)] == ["unknown_set"]


def test_duplicate_shot_ids_are_reported_once_and_keep_consistent_sites():
    sp = screenplay(
        shot(actions=[act("wave", who="dad", id="w")]),
        shot(actions=[act("kneel", t=2, who="dad")]),
        narrations={"mom": {"s1": [line(0, "We waved.", cue="w")]}},
    )
    errors = check(sp)
    assert [(e.code, e.shot) for e in errors] == [("duplicate_shot", "s1"), ("cue_unknown_action", "s1")]
    assert errors[0].message == 'shot s1: another shot already has id "s1"; shot ids must be unique'
    assert _hunt_problems(sp, errors) == []


def test_unicode_ids_are_quoted_as_written():
    sp = screenplay(shot(id="ショット1", marks={"été": [0.3, 0.0]}, start={"dad": {"at": "été"}},
                         actions=[act("walk_to", who="dad", to="nöwhere"), act("wave", t=2, who="dâd")]))
    errors = check(sp)
    assert [e.message for e in errors] == [
        f'shot ショット1, t=1: dad cannot walk_to; "nöwhere" is not a mark in this shot; {MARKS_LIST}, été',
        f'shot ショット1, t=2: dâd cannot wave; "dâd" is not in the cast; {CAST_LIST}',
    ]
    assert _hunt_problems(sp, errors) == []


def test_a_layer_number_must_be_ascii_digits():
    """'²' passes str.isdigit but not int(): validate raised ValueError on it."""
    lead = 'screenplay: interaction "tab" scrubs'
    shape = 'a scrub target is "puppet:<personId>", "prop:<propId>", "set:<setId>.layer<n>" or "camera"'
    sp = screenplay(interactions=[tab({"type": "scrub", "from": 0, "to": 1,
                                       "targets": ["set:church.layer²", "set:church.layer١", "set:church.layer1"]})])
    assert_errors(
        check(sp),
        E("interaction_bad_target", f'{lead} "set:church.layer²"; {shape}', shot=None),
        E("interaction_bad_target", f'{lead} "set:church.layer١"; {shape}', shot=None),
    )


def test_an_interaction_is_at_two_finite_fractions():
    sp = screenplay(interactions=[{**tab({"type": "scrub", "from": 0, "to": 1}), "at": at}
                                  for at in ([5], [0.5, math.nan], [0.1, 0.2, 0.3], [0.5, 0.5])])
    assert [e.message for e in check(sp)] == [
        'screenplay: interaction "tab" is at [5], but at is [u, v]: two finite fractions of the page',
        'screenplay: interaction "tab" is at [0.5, nan], but at is [u, v]: two finite fractions of the page',
        'screenplay: interaction "tab" is at [0.1, 0.2, 0.3], but at is [u, v]: two finite fractions of the page',
    ]
    assert {e.code for e in check(sp)} == {"invalid_value"}


def test_a_prop_part_range_is_two_finite_numbers():
    """A nan range used to compile into the timeline, where JSON turns it into null."""
    ranges = ([math.nan], [0, math.inf], [0, 1, 2], [0, 6.28], [1, 0])
    sp = screenplay(interactions=[
        tab({"type": "prop_part", "prop": "clock", "part": "hand", "channel": "rot", "range": r}, iid=f"r{i}")
        for i, r in enumerate(ranges)
    ])
    assert_errors(
        check(sp),
        E("invalid_value", 'screenplay: interaction "r0" gives range [nan], but a range is [min, max]: two finite numbers',
          shot=None),
        E("invalid_value", 'screenplay: interaction "r1" gives range [0, inf], but a range is [min, max]: two finite '
          "numbers", shot=None),
        E("invalid_value", 'screenplay: interaction "r2" gives range [0, 1, 2], but a range is [min, max]: two finite '
          "numbers", shot=None),
    )


def test_an_empty_cast_with_effects():
    """Effects need nobody; one aimed at a person names the empty cast. With nobody to tell it,
    compile() then stops at no_narrator (narration.default_author) even without narration lines."""
    from screenplay.compiler import compile as compile_screenplay

    rain = shot(actions=[act("rain", t=0), act("smoke", t=1, at="curb"), act("time_of_day", t=1, value="dusk")])
    assert check(screenplay(rain, cast=[])) == []
    confetti = screenplay(shot(actions=[act("confetti", at="dad")]), cast=[])
    assert_errors(check(confetti), E("unknown_person", 'shot s1, t=1: cannot confetti; "dad" is not in the cast; '
                                     "the cast is empty", t=1.0, do="confetti"))
    result = compile_screenplay(screenplay(rain, cast=[]), sets=SETS, props=PROPS, people=PEOPLE)
    assert [(e.code, e.message) for e in result.errors] == [(
        "no_narrator",
        "screenplay: nobody narrates: no narration source names a teller and the cast is empty; add someone to the cast",
    )]


def _cued_wave(scrub_to: float) -> Screenplay:
    """A wave cued to the last word of its line, with a scrub tab written from 0.5 to ``scrub_to``."""
    return screenplay(
        shot(start={"dad": {"at": "steps_left"}}, actions=[act("wave", t=1, who="dad", id="w")],
             narration=[line(0, "One, two, three, and then he waved.", cue="w", cueWord="waved")]),
        interactions=[tab({"type": "scrub", "from": 0.5, "to": scrub_to})],
    )


def test_a_scrub_inside_a_cued_shot_follows_its_actions():
    """An interaction range across narration: the cue moves the wave onto its word, and the
    scrub written over it moves with it, inside the shot."""
    from screenplay.compiler import compile as compile_screenplay

    sp = _cued_wave(2)
    assert check(sp) == []
    result = compile_screenplay(sp, sets=SETS, props=PROPS, people=PEOPLE)
    assert result.ok, [e.message for e in result.errors]
    tl = result.timeline
    drives = tl.interactions[0].drives
    shot0 = tl.shots[0]
    assert shot0.action_start < drives.from_ < drives.to <= shot0.end


@pytest.mark.xfail(strict=True, raises=AssertionError, reason=(
    "narration.py retime_interactions: a scrub's `to` past its shot's end goes into the timeline as it "
    "is, though its docstring says a tab only ever scrubs its own shot. Written past the end (to=500: "
    "shot s1 ends at t=3.9, the tab scrubs to t=501.2, past the whole timeline), or pushed past it by "
    "narration retiming (to=3 over a wave cued to the last word of its line: the shot ends at t=4.4, "
    "the tab scrubs to t=4.7, into the fold). validate cannot know a shot's length; the compiler "
    "should clamp `to` to the shot's end or raise interaction_bad_range."
))
@pytest.mark.parametrize("sp", [
    pytest.param(screenplay(shot(start={"dad": {"at": "steps_left"}}, actions=[act("wave", t=1, who="dad")]),
                            interactions=[tab({"type": "scrub", "from": 0, "to": 500})]), id="written_past_the_end"),
    pytest.param(_cued_wave(3), id="pushed_past_the_end_by_a_cue"),
])
def test_a_scrub_never_runs_past_its_shot(sp):
    from screenplay.compiler import compile as compile_screenplay

    assert check(sp) == []
    result = compile_screenplay(sp, sets=SETS, props=PROPS, people=PEOPLE)
    if result.ok:
        assert result.timeline.interactions[0].drives.to <= result.timeline.shots[0].end + 1e-6
    else:
        assert [e.code for e in result.errors] == ["interaction_bad_range"]
