"""errors.ERROR_CODES covers every code the compiler can emit, and nothing else.

A static scan of api/screenplay/*.py with ast collects every code-carrying literal:

- the first argument of ``StateError(...)``, ``NarrationError(...)``, ``error(...)`` and any
  ``<x>.error(...)`` (``E.error``, ``errors.error``, validate's ``_Site.error``);
- the second argument of validate's ``self.add(site, "<code>", reason)``;
- a ``code="<code>"`` keyword (``CompileError(code="bad_camera", ...)``);
- an assignment ``code = "<code>"`` (compiler.py's follow_cycle / follow_order);
- an f-string code such as ``f"already_{state}"``, matched as a pattern.

Every other first argument must be a pass-through of an existing error's code (``code`` or
``e.code``), so a new dynamic code cannot slip past the registry.
"""

from __future__ import annotations

import ast
import json
import re
from collections import defaultdict
from pathlib import Path

import pytest

import schemas
from screenplay.compiler import compile as compile_screenplay
from screenplay.errors import ERROR_CODES, ErrorCode, hint_for
from screenplay.schema import Screenplay

SRC = Path(__file__).resolve().parents[2] / "screenplay"
SNAKE = re.compile(r"^[a-z]+(_[a-z]+)*$")
FIRST_ARG_CALLS = {"StateError", "NarrationError", "error"}
PASS_THROUGH = {"code", "e.code"}


def _call_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def _scan() -> tuple[dict[str, list[str]], dict[str, list[str]], list[str]]:
    """(literal code -> sites, f-string pattern -> sites, unexpected dynamic sites)."""
    literals: dict[str, list[str]] = defaultdict(list)
    patterns: dict[str, list[str]] = defaultdict(list)
    dynamic: list[str] = []

    def take(arg: ast.expr, site: str) -> None:
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            literals[arg.value].append(site)
        elif isinstance(arg, ast.JoinedStr):
            regex = "".join(
                re.escape(v.value) if isinstance(v, ast.Constant) else "[a-z_]+" for v in arg.values
            )
            patterns[f"^{regex}$"].append(site)
        elif ast.unparse(arg) not in PASS_THROUGH:
            dynamic.append(f"{site}: {ast.unparse(arg)}")

    for path in sorted(SRC.glob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            site = f"{path.name}:{getattr(node, 'lineno', 0)}"
            if isinstance(node, ast.Call):
                name = _call_name(node)
                if name in FIRST_ARG_CALLS and node.args:
                    take(node.args[0], site)
                elif name == "add" and isinstance(node.func, ast.Attribute) and len(node.args) == 3:
                    arg = node.args[1]
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        take(arg, site)
                for kw in node.keywords:
                    if kw.arg == "code":
                        take(kw.value, site)
            elif isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "code" for t in node.targets):
                take(node.value, site)
    return literals, patterns, dynamic


LITERALS, PATTERNS, DYNAMIC = _scan()


def test_the_scan_finds_the_known_shapes():
    # A guard on the scanner itself: each collection rule finds what it is meant to.
    assert "not_holding" in LITERALS  # StateError
    assert "cue_conflict" in LITERALS  # NarrationError
    assert "no_shots" in LITERALS  # E.error
    assert "out_of_reach" in LITERALS  # errors.error via from_out_of_reach
    assert "unknown_verb" in LITERALS  # validate's self.add
    assert "bad_camera" in LITERALS  # CompileError(code=...)
    assert "follow_cycle" in LITERALS and "follow_order" in LITERALS  # code = "..."
    assert "^already_[a-z_]+$" in PATTERNS  # f"already_{state}"
    assert len(LITERALS) > 60


def test_no_unregistered_dynamic_codes():
    assert DYNAMIC == [], "a code built at run time: make it a literal or add it to PASS_THROUGH"


def test_every_emitted_code_is_registered():
    missing = {code: sites for code, sites in LITERALS.items() if code not in ERROR_CODES}
    assert missing == {}


def test_every_pattern_code_matches_registered_codes():
    for pattern, sites in PATTERNS.items():
        assert any(re.match(pattern, code) for code in ERROR_CODES), (pattern, sites)


def test_no_stale_registry_entries():
    stale = [
        code for code in ERROR_CODES
        if code not in LITERALS and not any(re.match(p, code) for p in PATTERNS)
    ]
    assert stale == []


@pytest.mark.parametrize("code", sorted(set(LITERALS) | set(ERROR_CODES)))
def test_codes_are_snake_case(code: str):
    assert SNAKE.match(code), code


def test_registry_entries_are_well_formed():
    for code, entry in ERROR_CODES.items():
        assert isinstance(entry, ErrorCode)
        assert entry.summary and entry.summary == entry.summary.strip(), code
        assert entry.summary[0].islower() and not entry.summary.endswith("."), code
        if entry.hint is not None:
            assert entry.hint[0].islower() and not entry.hint.endswith("."), code


SAMPLES = Path(__file__).resolve().parents[3] / "web" / "public" / "samples"


def _compile_sample(mutate) -> list:
    memory = json.loads((SAMPLES / "hymn_books.memory.json").read_text())
    data = json.loads((SAMPLES / "hymn_books.screenplay.json").read_text())
    mutate(data)
    res = compile_screenplay(
        Screenplay.model_validate(data),
        sets={s["id"]: schemas.SetDef.model_validate(s) for s in memory["sets"]},
        props={p["id"]: schemas.Prop.model_validate(p) for p in memory["props"]},
        people={p["id"]: schemas.Person.model_validate(p) for p in memory["people"]},
    )
    assert not res.ok
    return res.errors


def test_compiled_errors_are_registered_and_get_their_hints():
    def no_enter(data):  # dad walks before he has entered
        data["shots"][0]["actions"][0] = {"t": 0, "who": "dad", "do": "walk_to", "to": "steps_left"}

    errors = _compile_sample(no_enter)
    assert errors and all(e.code in ERROR_CODES for e in errors)
    assert errors[0].code == "offstage"
    assert hint_for(errors[0]) == "add an enter for dad before this walk_to"

    def unknown_mark(data):
        data["shots"][0]["actions"][2]["to"] = "the_moon"

    errors = _compile_sample(unknown_mark)
    assert [e.code for e in errors] == ["unknown_mark"]
    assert hint_for(errors[0]) == "use a mark the message lists, or add it to shot s1's marks as [u, h]"


def test_effect_errors_get_a_hint_that_reaims_the_effect():
    def confetti_on_dad_entering(data):  # dad is still offstage at t=0.5
        data["shots"][0]["actions"].insert(1, {"t": 0.5, "do": "confetti", "at": "dad"})

    errors = _compile_sample(confetti_on_dad_entering)
    assert [e.code for e in errors] == ["offstage"]
    assert hint_for(errors[0]) == "aim the confetti at a mark on stage, or give it a t when dad is on stage"

    def confetti_offstage(data):  # a mark beyond the edge: no enter can fix it
        data["shots"][0]["actions"].append({"t": 7.8, "do": "confetti", "at": "offstage_left"})

    errors = _compile_sample(confetti_offstage)
    assert [e.code for e in errors] == ["offstage"]
    assert hint_for(errors[0]) == "aim the confetti at a mark on stage"


def test_not_holding_hint_when_someone_else_holds_the_prop():
    def mom_keeps_the_books(data):  # mom poses instead of dropping: she still holds them at the hand_to
        actions = data["shots"][0]["actions"]
        actions[:] = [a for a in actions if a["do"] != "pick_up"]
        actions[1] = {"t": 3.25, "who": "mom", "do": "pose", "pose": "hold", "id": "drop"}  # keeps the cue
        data["interactions"] = []  # its scrub drives the drop

    errors = _compile_sample(mom_keeps_the_books)
    assert [e.code for e in errors] == ["not_holding"]
    assert errors[0].message.endswith("dad cannot hand_to \"hymn_books\"; mom is holding it")
    assert hint_for(errors[0]) == "have mom hand_to hymn_books to dad before this hand_to"


def test_error_code_is_frozen():
    entry = ERROR_CODES["offstage"]
    with pytest.raises(AttributeError):
        entry.hint = None  # type: ignore[misc]
