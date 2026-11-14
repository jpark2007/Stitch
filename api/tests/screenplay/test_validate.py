"""Static screenplay validation: vocabulary, ids, marks, params, times, narration, extras."""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

import schemas
from screenplay.schema import VOCABULARY, Action, CompileError, Screenplay
from screenplay.validate import CAMERA, VERBS, action_params, validate

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
        E("invalid_value", 'shot s1: mark "spot" is [0.5], but a mark is [u, h] or [u, h, d] (numbers)'),
        E("invalid_value", 'shot s1: mark "far" is [0.5, 0.0, 0.2, 1.0], but a mark is [u, h] or [u, h, d] (numbers)'),
    )


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
    ("run_to", {"to": "mom"}, f'"mom" is not a mark in this shot; {MARKS_LIST}'),
    ("enter", {"to": "nowhere"}, f'"nowhere" is not a mark in this shot; {MARKS_LIST}'),
    ("enter", {"to": "curb", "from": "curb"}, '"from" must be offstage_left or offstage_right, not "curb"'),
    ("exit", {"to": "curb"}, '"to" must be offstage_left or offstage_right, not "curb"'),
    ("place_on", {"what": "hymn_books", "on": "nowhere"},
     f'"nowhere" is not a prop in the screenplay or a mark in this shot; {PROPS_LIST}; {MARKS_LIST}'),
])
def test_unknown_mark(verb, params, reason):
    a = act(verb, who="dad", **params)
    assert_errors(check_actions(a), E("unknown_mark", message_for(verb, a, reason), t=1.0, who="dad", do=verb))


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
    assert_errors(check_actions(a), E("unknown_target", message_for(verb, a, reason), t=1.0, who="dad", do=verb))


def test_hand_to_a_prop_is_unknown_target():
    assert_errors(
        check_actions(act("hand_to", who="dad", what="hymn_books", to="table")),
        E("unknown_target",
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
        E("invalid_value",
          f'shot s1, t=1: kid cannot {verb} "lamp"; "lamp" has no state "{state}"; its states are lit, out',
          t=1.0, who="kid", do=verb),
    )


@pytest.mark.parametrize(("verb", "params", "reason"), [
    ("sit_on", {"what": "chair", "socket": "back"}, '"chair" has no socket "back"; its sockets are seat'),
    ("get_in", {"what": "sedan", "socket": "roof"}, '"sedan" has no socket "roof"; its sockets are driver, passenger'),
    ("place_on", {"what": "hymn_books", "on": "table", "socket": "shelf"}, '"table" has no socket "shelf"; its sockets are top'),
    ("place_on", {"what": "hymn_books", "on": "curb", "socket": "top"},
     'socket only applies when placing on a prop, and "curb" is a mark'),
    ("place_on", {"what": "hymn_books", "on": "piano"}, '"piano" has no sockets to place things on'),
])
def test_bad_sockets(verb, params, reason):
    a = act(verb, who="dad", **params)
    assert_errors(check_actions(a), E("invalid_value", message_for(verb, a, reason), t=1.0, who="dad", do=verb))


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
    assert errors[0].t == -0.5 and errors[1].t == 2.0 and math.isnan(errors[2].t)


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
    ("place_on", {"what": "table", "on": "table"}, '"on" is "table" itself'),
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
        "action ids in this shot: 'drop'"))


def test_other_telling_unknown_cue():
    sp = screenplay(
        shot(**DROP_SHOT, narration=[line(0, "She dropped them.", cue="drop")]),
        narrations={"mom": {"s1": [line(0, "I fell.", cue="fall"), line(1, "He hugged me.", cue="hug")]}},
    )
    assert_errors(
        check(sp),
        E("cue_unknown_action",
          "shot s1, mom's narration line 0: cue \"fall\" matches no action id and no action verb in this shot; "
          "action ids in this shot: 'drop'"),
        E("cue_unknown_action",
          "shot s1, mom's narration line 1: cue \"hug\" matches no action id and no action verb in this shot; "
          "action ids in this shot: 'drop'"),
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
          'its words are: "She", "let", "go"'),
        E("cue_word_missing",
          'shot s1, mom\'s narration line 0: cueWord "fell" is not a word of "Oops."; its words are: "Oops"'),
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


def test_narration_line_bad_time():
    sp = screenplay(shot(narration=[line(-1, "Too early.")]))
    assert_errors(check(sp), E(
        "bad_time", "shot s1, dad's narration line 0: t must be a finite number >= 0, not -1"))


def test_telling_unknown_shot():
    sp = screenplay(shot(), shot(id="s2", set="park"), narrations={"mom": {"s1": [], "s9": [line(0, "Hi.")]}})
    assert_errors(check(sp), E(
        "telling_unknown_shot",
        'screenplay: mom\'s telling has lines for shot "s9", which is not in the screenplay; shots are: s1, s2',
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
        'screenplay: interaction "pull" belongs to shot "s7", which is not in the screenplay; shots are: s1',
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
        E("missing_param", 'screenplay: interaction "e" drives prop_part and needs "part"', shot=None),
        E("missing_param", 'screenplay: interaction "f" drives prop_state and needs "prop"', shot=None),
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
