"""The verb case catalogue (verb_cases.py): coverage of the vocabulary, case shape, and agreement
with static validation. Wave 3 runs the same cases through compile()."""

from __future__ import annotations

import io
import json
import re
import tokenize
from collections import Counter
from pathlib import Path

import pytest

import schemas
from screenplay.schema import VOCABULARY, Screenplay
from screenplay.state import fmt_t
from screenplay.validate import validate
from verb_cases import CASES, Scene, VerbCase, base_scene, cases_for

ACTIONS = list(VOCABULARY["actions"])
MOVES = list(VOCABULARY["camera"])
VERBS = ACTIONS + MOVES
OK = [c for c in CASES if c.ok]
STATIC = [c for c in CASES if c.stage == "static"]
DYNAMIC = [c for c in CASES if c.stage == "dynamic"]


def ids(cases: list[VerbCase]) -> list[str]:
    return [c.name for c in cases]


def check(case: VerbCase):
    return validate(case.screenplay, case.scene.sets, case.scene.props, case.scene.people)


# ------------------------------------------------------------------------ coverage

def test_the_vocabulary_is_the_one_the_catalogue_was_written_for():
    assert len(ACTIONS) == 32
    assert len(MOVES) == 5
    assert set(VOCABULARY["effects"]) <= set(ACTIONS)
    assert not set(ACTIONS) & set(MOVES)


@pytest.mark.parametrize("verb", VERBS)
def test_every_verb_and_camera_move_has_an_ok_and_an_error_case(verb):
    cases = cases_for(verb)
    assert any(c.ok for c in cases), f"{verb} has no ok case"
    assert any(not c.ok for c in cases), f"{verb} has no error case"


def test_cases_for_returns_the_cases_of_one_verb_in_order():
    for verb in VERBS:
        assert cases_for(verb) == [c for c in CASES if c.verb == verb]
    assert sum(len(cases_for(v)) for v in VERBS) == len(CASES)
    with pytest.raises(KeyError):
        cases_for("juggle")


# -------------------------------------------------------------------------- shape

def test_case_names_are_unique_and_verbs_are_vocabulary_names():
    names = Counter(c.name for c in CASES)
    assert [n for n, k in names.items() if k > 1] == []
    assert {c.verb for c in CASES} <= set(VERBS)
    for c in CASES:
        assert c.name.startswith(f"{c.verb}:"), c.name


@pytest.mark.parametrize("case", CASES, ids=ids(CASES))
def test_case_fields_agree_with_ok(case):
    assert isinstance(case.screenplay, Screenplay)
    assert isinstance(case.scene, Scene)
    if case.ok:
        assert (case.stage, case.code, case.message) == (None, None, None)
    else:
        assert case.stage in ("static", "dynamic")
        assert case.code and re.fullmatch(r"[a-z]+(_[a-z]+)*", case.code)
        assert case.message and case.message.startswith("shot ")


def test_cases_are_frozen():
    with pytest.raises(AttributeError):
        CASES[0].ok = False  # type: ignore[misc]


@pytest.mark.parametrize("case", CASES, ids=ids(CASES))
def test_each_case_uses_its_verb(case):
    """The verb under test appears in the screenplay (an action's do or a camera cue's move)."""
    used = {a.do for s in case.screenplay.shots for a in s.actions}
    used |= {c.move for s in case.screenplay.shots for c in s.camera}
    assert case.verb in used


def _heads(case: VerbCase) -> set[str]:
    """Every message head an action or cue using the case's verb could produce, in the
    errors.error_message grammar: ``shot <id>, t=<t>: [<who> ]cannot <do>[ "<what>"]; ``."""
    out: set[str] = set()
    for shot in case.screenplay.shots:
        items = [(a.t, a.who, a.do, a.what) for a in shot.actions]
        items += [(c.t, None, c.move, c.target) for c in shot.camera]
        for t, who, do, what in items:
            if do != case.verb:
                continue
            subject = f"{who} cannot {do}" if who else f"cannot {do}"
            if what:
                subject += f' "{what}"'
            out.add(f"shot {shot.id}, t={fmt_t(t)}: {subject}; ")
    return out


@pytest.mark.parametrize("case", STATIC + DYNAMIC, ids=ids(STATIC + DYNAMIC))
def test_error_messages_follow_the_compile_error_grammar(case):
    heads = _heads(case)
    assert any(case.message.startswith(h) and len(case.message) > len(h) for h in heads), (case.message, heads)


# ----------------------------------------------------------------- validation

@pytest.mark.parametrize("case", OK + DYNAMIC, ids=ids(OK + DYNAMIC))
def test_ok_and_dynamic_cases_pass_static_validation(case):
    assert check(case) == []


@pytest.mark.parametrize("case", STATIC, ids=ids(STATIC))
def test_static_cases_fail_validation_with_their_code_and_message(case):
    errors = check(case)
    assert errors, "validate found nothing"
    assert (errors[0].code, errors[0].message) == (case.code, case.message)


@pytest.mark.parametrize("case", CASES, ids=ids(CASES))
def test_screenplays_round_trip_through_json(case):
    sp = case.screenplay
    text = sp.model_dump_json(by_alias=True)
    again = Screenplay.model_validate(json.loads(text))
    assert again == sp
    assert again.model_dump_json(by_alias=True) == text


def test_validate_does_not_mutate_the_cases():
    before = [c.screenplay.model_dump_json() for c in CASES]
    for c in CASES:
        check(c)
    assert [c.screenplay.model_dump_json() for c in CASES] == before


# ------------------------------------------------------------------- the stage

def test_base_scene_is_the_shared_stage():
    sc = base_scene()
    assert set(sc.sets) == {"yard", "street"}
    yard = sc.sets["yard"]
    assert len(yard.layers) == 3
    assert {"door", "bench_spot", "table_spot", "curb"} <= set(yard.marks)
    assert {p: sc.people[p].character.build for p in sc.people} == {"dad": "tall", "mom": "average", "kid": "small"}

    p = sc.props
    assert set(p) == {"hymn_books", "cap", "chair", "table", "suitcase", "lamp", "sedan"}
    assert (p["hymn_books"].size, p["cap"].size, p["lamp"].size) == ("hand", "hand", "hand")
    assert p["hymn_books"].grip is not None and p["suitcase"].grip is not None
    assert (p["chair"].size, list(p["chair"].sockets)) == ("furniture", ["seat"])
    assert (p["table"].size, list(p["table"].sockets)) == ("furniture", ["top"])
    assert (p["suitcase"].size, set(p["suitcase"].states)) == ("carried", {"open", "closed"})
    assert set(p["lamp"].states) == {"lit", "out"}
    sedan = p["sedan"]
    assert sedan.size == "vehicle"
    assert set(sedan.sockets) == {"driver", "passenger"}
    assert set(sedan.states) == {"parked", "driving"}
    assert [n for n, part in sedan.parts.items() if part.spin] == ["wheel_back", "wheel_front"]
    for part in sedan.parts.values():
        assert part.art in sedan.art


def test_base_scene_round_trips_and_ids_match_keys():
    sc = base_scene()
    for table, model in ((sc.sets, schemas.SetDef), (sc.props, schemas.Prop), (sc.people, schemas.Person)):
        for key, item in table.items():
            assert item.id == key
            assert model.model_validate_json(item.model_dump_json()) == item


def test_table_top_is_within_reach_of_a_standing_puppet():
    """The table top sits about where the hymn sample's step does (y = 0.3), so a hand-size
    prop on it is gripped near shoulder height."""
    table = base_scene().props["table"]
    height = VOCABULARY["sizes"][table.size]
    top_y = (table.anchor[1] - table.sockets["top"][1]) * height
    assert top_y == pytest.approx(0.3)


def test_base_scene_is_fresh_on_every_call():
    a, b = base_scene(), base_scene()
    assert a == b
    assert a.sets is not b.sets and a.props["sedan"] is not b.props["sedan"]
    assert len({id(c.scene) for c in CASES}) == len(CASES)


# --------------------------------------------- every dynamic message names its source

VERB_CASES_SRC = Path(__file__).with_name("verb_cases.py").read_text()


def _dynamic_calls_without_source_comment(src: str) -> list[int]:
    """Line numbers of ``_dynamic(`` calls with no ``# <file>.py <function>`` comment inside
    the call."""
    tokens = list(tokenize.generate_tokens(io.StringIO(src).readline))
    missing: list[int] = []
    for i, tok in enumerate(tokens):
        if not (tok.type == tokenize.NAME and tok.string == "_dynamic"):
            continue
        if i + 1 >= len(tokens) or tokens[i + 1].string != "(" or (i and tokens[i - 1].string == "def"):
            continue
        depth, j, found = 0, i + 1, False
        while j < len(tokens):
            s = tokens[j]
            if s.type == tokenize.OP and s.string == "(":
                depth += 1
            elif s.type == tokenize.OP and s.string == ")":
                depth -= 1
                if depth == 0:
                    break
            elif s.type == tokenize.COMMENT and re.search(r"\b\w+\.py\b.*\b\w+", s.string):
                found = True
            j += 1
        if not found:
            missing.append(tok.start[0])
    return missing


def test_every_dynamic_case_names_the_source_of_its_reason():
    assert _dynamic_calls_without_source_comment(VERB_CASES_SRC) == []
    assert len(re.findall(r"(?<!def )\b_dynamic\(", VERB_CASES_SRC)) == len(DYNAMIC)


def test_the_source_comment_check_catches_a_missing_comment():
    src = (
        "def _dynamic(*a): pass\n"
        "x = [\n"
        "    _dynamic('a', 'b', None, 'c',\n"
        "             # state.py World.drop\n"
        "             'm'),\n"
        "    _dynamic('a', 'b', None, 'c',\n"
        "             'm'),  # trailing comment outside the call does not count\n"
        "]\n"
    )
    assert _dynamic_calls_without_source_comment(src) == [6]


# ------------------------------------------------------------------- foot planting

@pytest.mark.parametrize("case", OK, ids=ids(OK))
def test_every_ok_case_compiles_planted_with_its_feet_on_the_surface(case):
    """compile() plants by default (feetPlanted true); against plant_feet=False only puppet root
    y changes, and only so the lowest foot is on the surface at walk / run cycle keys."""
    from screenplay.compiler import compile as compile_screenplay
    from test_foot_planting import planting_problems

    sc = case.scene
    planted = compile_screenplay(case.screenplay, sets=sc.sets, props=sc.props, people=sc.people)
    unplanted = compile_screenplay(case.screenplay, sets=sc.sets, props=sc.props, people=sc.people, plant_feet=False)
    assert planted.ok and unplanted.ok, [e.message for e in planted.errors + unplanted.errors]
    builds = {pid: p.character.build for pid, p in sc.people.items()}
    assert planting_problems(planted.timeline, unplanted.timeline, builds) == []
