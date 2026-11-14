"""The studio's skills: format the deepagents skills middleware accepts, and content that matches
the shared contracts (vocabulary, poses, reference screenplay) so the director cannot drift from
what the compiler allows."""

import json
import re
from pathlib import Path

import pytest
from deepagents.backends.filesystem import FilesystemBackend
from deepagents.middleware.skills import _list_skills, _parse_skill_metadata, _validate_skill_name

from screenplay.schema import Screenplay

REPO = Path(__file__).resolve().parents[3]
SKILLS = REPO / "api" / "agent" / "skills"
SHARED = REPO / "shared"
SAMPLE = REPO / "web" / "public" / "samples" / "hymn_books.screenplay.json"
SKILL_NAMES = ["popup-style", "storyboarding", "directing"]

VOCAB = json.loads((SHARED / "vocabulary.json").read_text())
POSES = json.loads((SHARED / "rig" / "poses.json").read_text())


def skill_text(name: str) -> str:
    return (SKILLS / name / "SKILL.md").read_text()


def body(name: str) -> str:
    """SKILL.md without its frontmatter."""
    return skill_text(name).split("\n---\n", 1)[1]


# ------------------------------------------------------------------------- format

@pytest.mark.parametrize("name", SKILL_NAMES)
def test_skill_frontmatter_parses_with_deepagents(name):
    path = SKILLS / name / "SKILL.md"
    meta = _parse_skill_metadata(path.read_text(), str(path), name)
    assert meta is not None, f"{name}: deepagents could not parse the frontmatter"
    assert meta["name"] == name
    assert _validate_skill_name(meta["name"], name) == (True, "")
    # within the spec limit, so nothing is truncated from what the model sees
    assert 0 < len(meta["description"]) <= 1024


def test_skills_load_through_the_middleware_backend():
    """Exactly how the studio loads them: a backend source listing skill directories."""
    backend = FilesystemBackend(root_dir=SKILLS, virtual_mode=True)
    found = {s["name"]: s for s in _list_skills(backend, "/")}
    assert set(SKILL_NAMES) <= set(found)
    for name in SKILL_NAMES:
        assert found[name]["path"].endswith(f"{name}/SKILL.md")


@pytest.mark.parametrize("name", SKILL_NAMES)
def test_skill_fits_one_read(name):
    """The middleware tells the model to read_file with limit=1000; keep each skill well inside it."""
    text = skill_text(name)
    assert len(text.splitlines()) < 1000
    assert max(len(line) for line in text.splitlines()) < 5000


# ------------------------------------------------------------------ directing: vocabulary

def table_rows(text: str) -> dict[str, list[str]]:
    """Markdown table rows keyed by their first cell's backticked name."""
    rows = {}
    for line in text.splitlines():
        m = re.match(r"^\|\s*`([a-z_]+)`\s*\|(.*)\|\s*$", line)
        if m and m.group(1) not in rows:
            rows[m.group(1)] = [c.strip() for c in m.group(2).split("|")]
    return rows


def ticked(cell: str) -> set[str]:
    return set(re.findall(r"`([a-zA-Z_]+)`", cell))


def test_directing_teaches_every_verb_with_exact_params():
    text = body("directing")
    rows = table_rows(text)
    for verb, spec in VOCAB["actions"].items():
        assert verb in rows, f"verb {verb} missing from the directing vocabulary table"
        kind, required, optional = rows[verb][:3]
        assert kind == spec["kind"], verb
        assert ticked(required) == set(spec["required"]), f"{verb} required params"
        assert ticked(optional) == set(spec["optional"]), f"{verb} optional params"
    # and no verb the compiler does not know
    action_section = text.split("## Action vocabulary", 1)[1].split("### Poses", 1)[0]
    assert set(table_rows(action_section)) == set(VOCAB["actions"])


def test_directing_teaches_every_camera_move_with_exact_params():
    rows = table_rows(body("directing").split("### Camera moves", 1)[1])
    for move, spec in VOCAB["camera"].items():
        assert move in rows, move
        assert ticked(rows[move][0]) == set(spec["required"]), move
        assert ticked(rows[move][1]) == set(spec["optional"]), move


def test_directing_lists_effects_times_transitions_and_poses():
    text = body("directing")
    for word in VOCAB["effects"] + VOCAB["timesOfDay"] + VOCAB["transitions"] + VOCAB["interactionKinds"]:
        assert f"`{word}`" in text, word
    pose_rows = table_rows(text.split("### Poses", 1)[1].split("### Camera moves", 1)[0])
    assert set(pose_rows) == set(POSES["poses"])


def test_directing_covers_the_contract_conventions():
    text = body("directing")
    for word in [
        "pageText", "interactions", "explanations", "narrations", "cue", "cueWord",
        "offstage_left", "offstage_right", "/screenplay.json", "compile_screenplay",
        "hymn_books.screenplay.json", "z = 0.5", "x = 2u − 1", "1.2 s", "hand_r",
        "beside the dropper's feet", "scrub", "prop_state", "prop_part",
        "puppet:<personId>", "prop:<propId>", "camelCase",
    ]:
        assert word in text, word
    assert re.search(r"at most 3\s+rounds", text, re.IGNORECASE)


def test_directing_teaches_what_the_compiler_simulates():
    """The rules the real compiler checks beyond the vocabulary: one action at a time, on/off
    stage, reach, mark names, cue retiming and narration spacing, with the codes it reports."""
    text = body("directing")
    for code in ("busy", "offstage", "already_on_stage", "out_of_reach", "not_holding", "already_held",
                 "hands_full", "ambiguous_name", "lines_overlap", "cue_before_start", "cue_conflict",
                 "cue_word_missing", "duplicate_telling", "explanation_unknown_target", "follow_order",
                 "nothing_to_follow", "bad_camera", "vehicle_moving", "not_driver", "head_full"):
        assert f"`{code}`" in text, code
    timed = set(duration_rows())
    assert set(VOCAB["actions"]) - set(VOCAB["effects"]) <= timed, "every non-effect verb has a duration"
    assert "0.25 s per word" in text
    assert "0.15 in u" in text


def test_directing_teaches_effects_as_compiling_verbs():
    """Every verb in the vocabulary has a real handler on main or is an effect the compiler turns
    into Timeline.effects (screenplay/effects.py). The skill says so and never calls a verb
    unsupported; test_every_vocabulary_verb_compiles_in_a_minimal_scene compiles each one."""
    from screenplay.compiler import HANDLERS
    from screenplay.effects import EFFECT_VERBS

    assert set(VOCAB["actions"]) <= set(HANDLERS) | set(EFFECT_VERBS)
    assert set(EFFECT_VERBS) == set(VOCAB["effects"])
    text = " ".join(body("directing").split())
    assert "Every verb in the table compiles, effects included." in text
    assert "unsupported" not in text.lower()


@pytest.mark.parametrize(
    "path",
    [*(SKILLS / name / "SKILL.md" for name in SKILL_NAMES),
     *sorted((REPO / "api" / "agent" / "prompts").glob("*.md"))],
    ids=lambda p: f"{p.parent.name}/{p.name}",
)
def test_no_prompt_or_skill_calls_a_vocabulary_verb_unsupported(path):
    """exit, turn_to and follow (since #101) and the effects (since #125) all compile."""
    for para in re.split(r"\n\s*\n|\n *- ", path.read_text()):
        flat = " ".join(para.split())
        if re.search(r"unsupported|not support|cannot do that verb|does not run", flat, re.IGNORECASE):
            for verb in VOCAB["actions"]:
                assert f"`{verb}`" not in flat, f"{path.name}: {flat}"


def test_director_prompt_states_the_compiler_rules():
    text = (REPO / "api" / "agent" / "prompts" / "director.md").read_text()
    for word in ("get_person", "compile_screenplay", "/screenplay.json", "busy", "out_of_reach",
                 "`enter`", "snake_case", "0.25 s per word", "Stop after round 3"):
        assert word in text, word
    assert '"prop": "<' not in text and '"part": "<' not in text  # drives examples are concrete


def test_open_and_close_are_never_taught_on_a_held_prop():
    """The real compiler rejects open/close of a held prop (out_of_reach: the free arm cannot reach
    the holding hand), so both docs say so and give the place_on fix, and neither recommends it."""
    prompt = (REPO / "api" / "agent" / "prompts" / "director.md").read_text()
    skill = body("directing")
    for text in (prompt, skill):
        flat = " ".join(text.split())
        assert re.search(r"never `open` or `close` a prop someone is holding", flat, re.IGNORECASE)
        assert "`place_on` it at hand height" in flat
        assert not re.search(r"the prop is held or|open a prop the person holds|have the person hold the prop", flat)


# ------------------------------------------------------------- directing: worked example

def test_worked_example_is_a_byte_identical_copy_of_the_sample():
    copy = SKILLS / "directing" / "hymn_books.screenplay.json"
    assert copy.read_bytes() == SAMPLE.read_bytes()


def test_worked_example_validates_and_matches_the_walkthrough():
    sp = Screenplay.model_validate_json((SKILLS / "directing" / "hymn_books.screenplay.json").read_bytes())
    text = body("directing")
    shot = sp.shots[0]
    for action in shot.actions:
        assert f'"t": {action.t:g}' in text and f'"do": "{action.do}"' in text, action
    for line in shot.narration + sp.narrations["mom"]["s1"]:
        assert line.source in text and line.text.split()[1] in text
    assert shot.page_text in text.replace("\n  ", " ")
    for e in sp.explanations:
        assert e.target in text


# --------------------------------------------------------------- popup-style, storyboarding

def test_popup_style_points_to_the_locked_prompt_and_size_classes():
    text = body("popup-style")
    assert "style_prompt.txt" in text
    assert re.search(r"never restate", text, re.IGNORECASE)
    rows = table_rows(text)
    for size, height in VOCAB["sizes"].items():
        assert size in rows, size
        assert float(rows[size][0]) == height, size
    for layer in ("Back", "Middle", "Front"):
        assert f"| {layer} |" in text


def test_storyboarding_documents_shotlist_and_sources():
    text = body("storyboarding")
    assert "/shotlist.md" in text
    assert "| shot id | set/place | who | what happens | source |" in text
    assert "telling:<authorId>:s<n>" in text
    assert "1-based" in text
    for word in ("pageText", "1 to 4", "One place per shot", "second telling"):
        assert word.lower() in text.lower(), word
    # the example sources it cites must match the reference sample's numbering
    sample = json.loads(SAMPLE.read_text())
    assert sample["shots"][0]["narration"][1]["source"] == "telling:dad:s3"
    assert "telling:dad:s3" in text


# ======================================================= directing: against the real compiler
#
# The directing skill is what the director knows about the compiler. These tests hold it to the
# real `screenplay.compiler.compile` on main: (a) its error codes are the compiler's, (b) every
# verb it teaches compiles, (c) every example in it compiles with zero errors, (d) every duration
# it states is what the compiler produces, and (e) the reach distances it gives work.

import ast  # noqa: E402
import copy  # noqa: E402

import schemas  # noqa: E402
from screenplay.compiler import compile as real_compile  # noqa: E402
from screenplay.schema import CompileResult  # noqa: E402

API = REPO / "api"
DIRECTING_DIR = SKILLS / "directing"


def section(text: str, heading: str) -> str:
    """The body of a markdown section, up to the next heading of the same or a higher level."""
    level = heading.split(" ", 1)[0]
    rest = text.split(f"\n{heading}\n", 1)[1]
    stop = re.search(rf"\n#{{1,{len(level)}}} ", rest)
    return rest[: stop.start()] if stop else rest


def duration_rows() -> dict[str, str]:
    """'How long actions take': verb -> its 'Busy for' cell."""
    out = {}
    for line in section(body("directing"), "### How long actions take").splitlines():
        if line.startswith("| `"):
            verbs, busy = [c.strip() for c in line.strip("|").split("|")]
            for verb in re.findall(r"`([a-z_]+)`", verbs):
                out[verb] = busy
    return out


# ------------------------------------------------------------------ (a) error codes

_ERROR_CALLS = {"StateError", "NarrationError", "CompileError", "error", "add"}
# The one code built at run time: World._open_close raises f"already_{state}" for open and close.
_FSTRING_CODES = {"already_{state}": {"already_open", "already_closed"}}


def compiler_codes() -> set[str]:
    """Every error code api/screenplay can emit: the code argument of StateError, NarrationError,
    CompileError, errors.error and validate's add(site, code, reason), and every `code = "..."`
    the compiler passes on (follow_cycle, follow_order)."""
    codes: set[str] = set()
    for path in sorted((API / "screenplay").glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "code" for t in node.targets):
                if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                    codes.add(node.value.value)
            if not isinstance(node, ast.Call):
                continue
            f = node.func
            name = f.attr if isinstance(f, ast.Attribute) else f.id if isinstance(f, ast.Name) else ""
            if name not in _ERROR_CALLS:
                continue
            for arg in [*node.args[:2], *(k.value for k in node.keywords if k.arg == "code")]:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str) and re.fullmatch(r"[a-z][a-z_]+", arg.value):
                    codes.add(arg.value)
                elif isinstance(arg, ast.JoinedStr) and node.args and arg is node.args[0]:
                    pattern = "".join(v.value if isinstance(v, ast.Constant) else "{" + ast.unparse(v.value) + "}"
                                      for v in arg.values)
                    assert pattern in _FSTRING_CODES, f"{path.name}: new run-time error code {pattern}"
                    codes |= _FSTRING_CODES[pattern]
    return codes


# Codes agent/tools/compile.py reports around the compiler (bad JSON, a missing file, ...).
AGENT_TOOL_CODES = {"invalid_json", "invalid_screenplay", "missing_file", "compiler_crash", "no_timeline",
                    "unknown_reference"}

# Codes the compiler can emit that the director never sees through compile_screenplay, because
# an earlier check always reports the problem first. The skill leaves them out on purpose.
INTERNAL_CODES = {
    # The screenplay schema's Literal types reject these before compile() runs (invalid_screenplay).
    "unknown_verb": "Action.do is a Literal of the vocabulary verbs",
    "unknown_move": "CameraCue.move is a Literal of the camera moves",
    "bad_facing": "StartState.facing is 'left' or 'right'",
    # Every vocabulary verb has a handler or is an effect (test_directing_teaches_effects_as_compiling_verbs).
    "unsupported_verb": "no vocabulary verb lacks a handler",
    "not_an_effect": "effect_for is only called for effect verbs",
    # validate.py reports these first, under the code the skill names.
    "bad_param": "an effect's at/value the verb does not take is unexpected_param",
    "bad_dur": "a dur <= 0 is bad_time",
    "bad_exit": "an exit to a mark is unknown_mark",
    "bad_mark": "a malformed mark is invalid_value",
    "bad_target": "follow/place_on on the wrong kind of name is unknown_target / unknown_mark",
    "hug_self": "a hug of oneself is self_target",
    "self_hand_to": "a hand_to to oneself is self_target",
    "unknown_id": "a person or prop missing from the scene is unknown_person / unknown_prop",
    "unknown_state": "a state the prop lacks is invalid_value",
    "unknown_socket": "a socket the prop lacks is invalid_value",
    "no_socket": "place_on onto a prop without sockets is invalid_value",
    # Internal guards that a valid screenplay cannot reach.
    "bad_parent": "place_on builds its own parent string",
    "bad_socket": "pick_up takes no socket parameter",
    "invalid_action": "a verb handler's ValueError: a compiler bug, not a screenplay problem",
    "no_narrator": "needs narration with an empty cast, which a screenplay with a shot cannot have",
}


def skill_code_table() -> set[str]:
    rows = section(body("directing"), "### Error codes")
    return {code for line in rows.splitlines() if line.startswith("| `")
            for code in re.findall(r"`([a-z_]+)`", line.strip("|").split("|")[0])}


# Backticked snake_case words in the skill that are names, not error codes.
SKILL_NAMES_NOT_CODES = {
    "steps_top", "steps_left", "steps_foot", "by_the_car", "hymn_books", "sample_hymn_books",
    "hand_back", "drop_books", "hand_r", "hand_l", "offstage_left", "offstage_right",
    "pull_tab", "prop_state", "prop_part", "item_prop_id",
    "read_file", "get_person", "compile_screenplay",  # tools
}


def test_error_codes_in_the_skill_are_the_compilers():
    real = compiler_codes()
    assert {"busy", "follow_order", "follow_cycle", "already_open", "out_of_reach"} <= real  # the scan works
    assert set(INTERNAL_CODES) <= real, sorted(set(INTERNAL_CODES) - real)  # no stale allowlist entry
    table = skill_code_table()
    # every code the skill names exists
    assert table <= real | AGENT_TOOL_CODES, sorted(table - real - AGENT_TOOL_CODES)
    known = set(VOCAB["actions"]) | set(VOCAB["camera"]) | set(POSES["poses"]) | {
        p for spec in VOCAB["actions"].values() for p in spec["required"] + spec["optional"]}
    for word in set(re.findall(r"`([a-z]+(?:_[a-z]+)+)`", body("directing"))) - known - SKILL_NAMES_NOT_CODES:
        assert word in real | AGENT_TOOL_CODES, f"`{word}` reads like an error code the compiler never emits"
        assert word in table, f"`{word}` is named in the skill but missing from its Error codes table"
    # every code the compiler emits is in the table, or internal
    assert real - set(INTERNAL_CODES) <= table, sorted(real - set(INTERNAL_CODES) - table)
    assert not table & set(INTERNAL_CODES), sorted(table & set(INTERNAL_CODES))
    assert {"invalid_json", "invalid_screenplay", "missing_file"} <= table
    src = (API / "agent" / "tools" / "compile.py").read_text()
    assert all(f'"{code}"' in src for code in AGENT_TOOL_CODES)


def test_internal_codes_are_shadowed_by_the_codes_the_skill_names():
    """Spot checks of INTERNAL_CODES: the director sees the earlier code instead."""
    cases = [
        (sp([{"t": 0, "who": "mom", "do": "hug", "target": "mom"}]), "self_target"),
        (sp([{"t": 0, "who": "mom", "do": "exit", "to": "a"}]), "unknown_mark"),
        (sp([{"t": 0, "who": "mom", "do": "wave", "dur": 0}]), "bad_time"),
        (sp([{"t": 0, "do": "rain", "at": "c"}]), "unexpected_param"),
        (sp([{"t": 0, "who": "mom", "do": "set_state", "what": "suitcase", "state": "broken"}],
            props=["suitcase"], propStart={"suitcase": {"at": "b"}}), "invalid_value"),
    ]
    for data, code in cases:
        assert [e.code for e in compile_data(data).errors] == [code], data["shots"][0]["actions"]


# ------------------------------------------------------- a minimal scene on the real compiler

def _person(pid: str, build: str = "average", notes: str | None = None) -> schemas.Person:
    return schemas.Person(id=pid, family_id="f", name=pid,
                          character=schemas.CharacterSheet(color="#888888", build=build, notes=notes))


def _prop(pid: str, size: str = "hand", **kw) -> schemas.Prop:
    return schemas.Prop(id=pid, family_id="f", name=pid, size=size, art={"body": None}, **kw)


PEOPLE = {p.id: p for p in [_person("mom"), _person("gran"), _person("dad", "tall"), _person("kid", "small"),
                            _person("nana", notes="walks slowly with a cane")]}
PROPS = {p.id: p for p in [
    _prop("books", grip=[0.5, 0.0]),
    _prop("cap", size="tiny", grip=[0.5, 0.0]),
    _prop("cake", size="carried", grip=[0.5, 0.0], aspect=1.2),
    _prop("suitcase", size="carried", grip=[0.5, 0.0], aspect=1.4, states={"closed": {}, "open": {}}),
    _prop("table", size="furniture", aspect=1.5, sockets={"top": [0.5, 0.0]}),
    _prop("chair", size="furniture", aspect=0.8, sockets={"seat": [0.5, 0.55]}),
    _prop("sedan", size="vehicle", aspect=2.2, sockets={"driver": [0.6, 0.45], "passenger": [0.4, 0.45]},
          parts={"wheel_front": {"art": "wheel", "at": [0.78, 0.9], "spin": True}}),
    _prop("pickup", size="vehicle", aspect=2.4, sockets={"driver": [0.7, 0.45], "bed": [0.25, 0.35]}),
]}
# a (x -0.6), b (x 0), c (x 0.6) on the floor; step 0.3 up at x 0.2; near is 0.07 in u right of b.
ROOM = schemas.SetDef(id="room", name="room", layers=[schemas.SetLayer(art=None, depth=0, height=1.0, color="#cccccc")],
                      marks={"a": [0.2, 0.0], "b": [0.5, 0.0], "c": [0.8, 0.0], "step": [0.6, 0.3],
                             "near": [0.57, 0.0], "door": [0.599, 0.0]})


def sp(actions: list[dict], start: dict | None = None, props: list[str] = (), **shot) -> dict:
    """One shot in ROOM; `start` defaults to Mom at b facing right."""
    start = start if start is not None else {"mom": {"at": "b", "facing": "right"}}
    return {"memoryId": "m", "cast": sorted(start), "props": list(props), "shots": [{
        "id": "s1", "set": "room", "start": start, "camera": [{"t": 0, "move": "wide"}],
        "actions": actions, "pageText": "A page.", **shot}]}


def compile_data(data: dict, sets=None, props=None, people=None) -> CompileResult:
    return real_compile(Screenplay.model_validate(data), sets=sets or {"room": ROOM},
                        props=props or PROPS, people=people or PEOPLE)


def assert_compiles(data: dict, **scene) -> CompileResult:
    res = compile_data(data, **scene)
    assert res.ok and res.errors == [], [f"{e.code}: {e.message}" for e in res.errors]
    return res


# ------------------------------------------------- (b) every verb compiles in a minimal scene

_SEDAN = {"props": ["sedan"], "propStart": {"sedan": {"at": "b"}}}
_STEP_SUITCASE = {"props": ["suitcase"], "marks": {"by_step": [0.53, 0.0]}}

VERB_SCENES: dict[str, dict] = {
    "walk_to": sp([{"t": 0, "who": "mom", "do": "walk_to", "to": "c"}]),
    "run_to": sp([{"t": 0, "who": "mom", "do": "run_to", "to": "c"}]),
    "enter": sp([{"t": 0, "who": "mom", "do": "enter", "from": "offstage_left", "to": "a"}],
                start={"mom": {"at": "offstage_left", "facing": "right"}}),
    "exit": sp([{"t": 0, "who": "mom", "do": "exit", "to": "offstage_right"}]),
    "turn_to": sp([{"t": 0, "who": "mom", "do": "turn_to", "target": "a"}]),
    "follow": sp([{"t": 0, "who": "mom", "do": "walk_to", "to": "c"}, {"t": 0, "who": "dad", "do": "follow", "target": "mom"}],
                 start={"mom": {"at": "b"}, "dad": {"at": "a"}}),
    "pose": sp([{"t": 0, "who": "mom", "do": "pose", "pose": "arms_raised"}]),
    "wave": sp([{"t": 0, "who": "mom", "do": "wave"}]),
    "point_at": sp([{"t": 0, "who": "mom", "do": "point_at", "target": "c"}]),
    "sit_on": sp([{"t": 0, "who": "mom", "do": "sit_on", "what": "chair"}], props=["chair"], propStart={"chair": {"at": "b"}}),
    "stand_up": sp([{"t": 0, "who": "mom", "do": "kneel"}, {"t": 0.5, "who": "mom", "do": "stand_up"}]),
    "kneel": sp([{"t": 0, "who": "mom", "do": "kneel"}]),
    "hug": sp([{"t": 0, "who": "mom", "do": "hug", "target": "dad"}], start={"mom": {"at": "b"}, "dad": {"at": "near"}}),
    "lean_toward": sp([{"t": 0, "who": "mom", "do": "lean_toward", "target": "c"}]),
    "pick_up": sp([{"t": 0, "who": "mom", "do": "pick_up", "what": "books"}], props=["books"], propStart={"books": {"at": "c"}}),
    "hold": sp([{"t": 0, "who": "mom", "do": "hold", "what": "books"}], start={"mom": {"at": "b", "holding": "books"}}, props=["books"]),
    "drop": sp([{"t": 0, "who": "mom", "do": "drop", "what": "books"}], start={"mom": {"at": "b", "holding": "books"}}, props=["books"]),
    "hand_to": sp([{"t": 0, "who": "mom", "do": "hand_to", "what": "books", "to": "dad"}],
                  start={"mom": {"at": "b", "holding": "books"}, "dad": {"at": "near"}}, props=["books"]),
    "put_on": sp([{"t": 0, "who": "mom", "do": "put_on", "what": "cap"}], start={"mom": {"at": "b", "holding": "cap"}}, props=["cap"]),
    "place_on": sp([{"t": 0, "who": "mom", "do": "place_on", "what": "books", "on": "near"}],
                   start={"mom": {"at": "b", "holding": "books"}}, props=["books"]),
    "open": sp([{"t": 0, "who": "mom", "do": "open", "what": "suitcase"}], start={"mom": {"at": "by_step"}},
               propStart={"suitcase": {"at": "step", "state": "closed"}}, **_STEP_SUITCASE),
    "close": sp([{"t": 0, "who": "mom", "do": "close", "what": "suitcase"}], start={"mom": {"at": "by_step"}},
                propStart={"suitcase": {"at": "step", "state": "open"}}, **_STEP_SUITCASE),
    "set_state": sp([{"t": 0, "do": "set_state", "what": "suitcase", "state": "open"}],
                    props=["suitcase"], propStart={"suitcase": {"at": "c", "state": "closed"}}),
    "get_in": sp([{"t": 0, "who": "mom", "do": "get_in", "what": "sedan"}], start={"mom": {"at": "a"}}, **_SEDAN),
    "get_out": sp([{"t": 0, "who": "mom", "do": "get_out", "what": "sedan"}], start={"mom": {"at": "b", "in": "sedan.driver"}}, **_SEDAN),
    "drive_to": sp([{"t": 0, "who": "mom", "do": "drive_to", "what": "sedan", "to": "c"}],
                   start={"mom": {"at": "b", "in": "sedan.driver"}}, **_SEDAN),
    "ride_to": sp([{"t": 0, "who": "dad", "do": "ride_to", "what": "sedan", "to": "c"}],
                  start={"mom": {"at": "b", "in": "sedan.driver"}, "dad": {"at": "b", "in": "sedan.passenger"}}, **_SEDAN),
    "confetti": sp([{"t": 0, "do": "confetti", "at": "mom", "dur": 2}]),
    "steam": sp([{"t": 0, "do": "steam", "at": "cake"}], start={"mom": {"at": "b", "holding": "cake"}}, props=["cake"]),
    "rain": sp([{"t": 0, "do": "rain", "dur": 3}]),
    "smoke": sp([{"t": 0, "do": "smoke", "at": "c"}]),
    "time_of_day": sp([{"t": 0, "do": "time_of_day", "value": "dusk"}]),
}


def test_every_verb_in_the_skill_is_a_vocabulary_verb_with_a_scene():
    rows = table_rows(section(body("directing"), "## Action vocabulary").split("### Poses", 1)[0])
    assert set(rows) == set(VOCAB["actions"]) == set(VERB_SCENES)


@pytest.mark.parametrize("verb", sorted(VERB_SCENES))
def test_every_vocabulary_verb_compiles_in_a_minimal_scene(verb):
    data = VERB_SCENES[verb]
    assert {a["do"] for a in data["shots"][0]["actions"]} >= {verb}
    res = assert_compiles(data)
    if verb in VOCAB["effects"]:
        assert [e.kind for e in res.timeline.effects] == [verb]


# ----------------------------------------------- (c) every example in the skill compiles

SAMPLE_MEMORY = json.loads((REPO / "web" / "public" / "samples" / "hymn_books.memory.json").read_text())
SAMPLE_SCENE = {
    "sets": {s["id"]: schemas.SetDef.model_validate(s) for s in SAMPLE_MEMORY["sets"]},
    "props": {**{p["id"]: schemas.Prop.model_validate(p) for p in SAMPLE_MEMORY["props"]},
              "suitcase": _prop("suitcase", size="carried", grip=[0.5, 0.0], aspect=1.4,
                                states={"closed": {}, "open": {}}),
              "green_sedan_1985": _prop("green_sedan_1985", size="vehicle", aspect=2.2,
                                        sockets={"driver": [0.6, 0.45], "passenger": [0.4, 0.45]},
                                        parts={"wheel_front": {"art": "wheel", "at": [0.78, 0.9], "spin": True}})},
    "people": {p["id"]: schemas.Person.model_validate(p) for p in SAMPLE_MEMORY["people"]},
}
WORKED_EXAMPLE = json.loads((DIRECTING_DIR / "hymn_books.screenplay.json").read_text())


def skill_examples() -> list[tuple[str, object]]:
    """Every JSON example in the skill: fenced ```json blocks and inline `{...}`, as (label, value).
    Templates with <placeholders> are not examples and are left out."""
    text = body("directing")
    found = re.findall(r"```json\n(.*?)\n```", text, re.DOTALL) + re.findall(r"`(\{.*?\})`", text)
    out = []
    for raw in found:
        if "<" in raw:
            continue
        try:
            out.append((raw[:50], json.loads(raw)))
        except json.JSONDecodeError:
            assert '"?' in raw, f"an example that is neither JSON nor a template: {raw}"
    return out


def screenplay_for(example: dict) -> dict:
    """A whole screenplay that uses the example the way the skill describes it."""
    data = copy.deepcopy(WORKED_EXAMPLE)
    shot = data["shots"][0]
    if "type" in example:  # an interaction's drives, on a prop the worked example lacks if need be
        prop = example.get("prop")
        if prop is not None and prop not in data["props"]:
            data["props"].append(prop)
            shot["propStart"] = {prop: {"at": "curb"}}
            data["explanations"].append({"target": f"prop:{prop}", "text": "A prop."})
        data["interactions"] = [{"id": "tab", "kind": "pull_tab", "label": "Pull", "shot": "s1", "page": "stage",
                                 "at": [0.85, 0.82], "drives": example}]
    elif set(example) == {"target", "text"}:  # an explanation
        data["explanations"] = [e for e in data["explanations"] if e["target"] != example["target"]] + [example]
    elif "move" in example:  # a camera cue: the worked example's own
        assert example in shot["camera"], example
    elif "do" in example:  # an action: the worked example's own, or one added to it
        if example not in shot["actions"]:
            shot["actions"].append(example)
    else:
        raise AssertionError(f"no rule to compile skill example {example}")
    return data


def test_the_skill_has_examples_of_every_kind():
    kinds = {("drives" if "type" in v else "explanation" if "text" in v else "camera" if "move" in v else "action")
             for _, v in skill_examples()}
    assert kinds == {"drives", "explanation", "camera", "action"}


def test_worked_example_compiles_with_zero_errors():
    res = assert_compiles(WORKED_EXAMPLE, **SAMPLE_SCENE)
    assert [s.page_text for s in res.timeline.shots] == [s["pageText"] for s in WORKED_EXAMPLE["shots"]]


@pytest.mark.parametrize("label, example", skill_examples(), ids=[label for label, _ in skill_examples()])
def test_every_skill_example_compiles_with_zero_errors(label, example):
    data = screenplay_for(example)
    Screenplay.model_validate(data)
    assert_compiles(data, **SAMPLE_SCENE)


# ------------------------------------------------- (d) the durations the skill states

def number(text: str, pattern: str, group: int = 1) -> float:
    m = re.search(pattern, " ".join(text.split()))
    assert m, f"{pattern!r} not found in {text!r}"
    return float(m.group(group))


def ends_at(data: dict, who: str, t_end: float, tol: float = 0.05) -> None:
    """`who` is busy until t_end, within tol: free at t_end + tol, still busy at t_end - tol."""
    for t, free in ((t_end + tol, True), (t_end - tol, False)):
        probe = copy.deepcopy(data)
        probe["shots"][0]["actions"].append({"t": round(t, 4), "who": who, "do": "turn_to", "target": "a"})
        res = compile_data(probe)
        codes = [e.code for e in res.errors]
        if free:
            assert res.ok, (who, t_end, [e.message for e in res.errors])
        else:
            assert codes == ["busy"], (who, t_end, "free too early", [e.message for e in res.errors])


def _durations() -> list[tuple[str, dict, str, float]]:
    """(label, scene, who, the end time the skill's own numbers give)."""
    rows = duration_rows()
    stage = " ".join(section(body("directing"), "## Stage, marks and time").split())
    walk = number(rows["walk_to"], r"distance / ([\d.]+)")
    run = number(rows["walk_to"], r"running: / ([\d.]+)")
    stand = number(rows["walk_to"], r"([\d.]+) more first if sitting or kneeling")
    cane = number(stage, r"cane, walker, limp or shuffle walks at × ([\d.]+)")
    pose, pose_back = number(rows["pose"], r"^([\d.]+)"), number(rows["pose"], r"`dur` \+ ([\d.]+)")
    wave = number(rows["wave"], r"default ([\d.]+) for `wave`")
    other_gesture = number(rows["wave"], r"and ([\d.]+) for the others")
    hug = number(rows["hug"], r"default ([\d.]+)")
    kneel, kneel_back = number(rows["kneel"], r"^([\d.]+)"), number(rows["kneel"], r"`dur` \+ ([\d.]+)")
    seat = number(rows["sit_on"], r"then ([\d.]+)")
    short = number(rows["stand_up"], r"^([\d.]+)$")
    pick, pick_floor = (number(rows["pick_up"], r"then ([\d.]+) \(([\d.]+) bending down to the floor\)", g)
                        for g in (1, 2))
    drop = number(rows["drop"], r"^([\d.]+)")
    hand = number(rows["hand_to"], r"^([\d.]+)")
    put = number(rows["put_on"], r"^([\d.]+) from a hand")
    place, place_low = (number(rows["place_on"], r"^([\d.]+) \(([\d.]+) bending down", g) for g in (1, 2))
    opening = number(rows["open"], r"^([\d.]+)$")
    drive = number(rows["drive_to"], r"distance / ([\d.]+) for the driver")
    ride = number(rows["ride_to"], r"distance / ([\d.]+); \*\*everyone in the vehicle\*\*")
    assert rows["turn_to"].startswith("no time / no time")
    holding = {"mom": {"at": "b", "holding": "books"}}
    seated = {"mom": {"at": "b", "in": "sedan.driver"}, "dad": {"at": "b", "in": "sedan.passenger"}}
    s = VERB_SCENES
    return [
        ("walk 0.6", s["walk_to"], "mom", 0.6 / walk),
        ("run 0.6", s["run_to"], "mom", 0.6 / run),
        ("enter 0.7", s["enter"], "mom", 0.7 / walk),
        ("exit 1.3", s["exit"], "mom", 1.3 / walk),
        ("walk with a cane", sp([{"t": 0, "who": "nana", "do": "walk_to", "to": "c"}], start={"nana": {"at": "a"}}),
         "nana", 1.2 / (walk * cane)),
        ("stand up from kneeling, then walk", sp([{"t": 0, "who": "mom", "do": "kneel"},
                                                  {"t": 0.5, "who": "mom", "do": "walk_to", "to": "c"}]),
         "mom", 0.5 + stand + 0.6 / walk),
        ("pose", s["pose"], "mom", pose),
        ("pose dur 2", sp([{"t": 0, "who": "mom", "do": "pose", "pose": "wave", "dur": 2}]), "mom", 2 + pose_back),
        ("wave", s["wave"], "mom", wave),
        ("point_at", s["point_at"], "mom", other_gesture),
        ("lean_toward", s["lean_toward"], "mom", other_gesture),
        ("hug", s["hug"], "mom", hug),
        ("hug, the other person", s["hug"], "dad", hug),
        ("kneel", s["kneel"], "mom", kneel),
        ("kneel dur 2", sp([{"t": 0, "who": "mom", "do": "kneel", "dur": 2}]), "mom", 2 + kneel_back),
        ("sit_on", s["sit_on"], "mom", seat),
        ("get_in at the door", sp([{"t": 0, "who": "mom", "do": "get_in", "what": "sedan"}],
                                  start={"mom": {"at": "door"}}, **_SEDAN), "mom", seat),
        ("stand_up from a chair", sp([{"t": 0, "who": "mom", "do": "stand_up"}], start={"mom": {"at": "b", "in": "chair.seat"}},
                                     props=["chair"], propStart={"chair": {"at": "b"}}), "mom", short),
        ("get_out", s["get_out"], "mom", short),
        ("hold", s["hold"], "mom", short),
        ("pick_up from a table", sp([{"t": 0, "who": "mom", "do": "pick_up", "what": "books"}], props=["books", "table"],
                                    propStart={"table": {"at": "near"}, "books": {"on": "table.top"}}), "mom", pick),
        ("pick_up from the floor", sp([{"t": 0, "who": "mom", "do": "pick_up", "what": "books"}], props=["books"],
                                      propStart={"books": {"at": "b"}}), "mom", pick_floor),
        ("drop", s["drop"], "mom", drop),
        ("hand_to", s["hand_to"], "mom", hand),
        ("hand_to, the receiver", s["hand_to"], "dad", hand),
        ("put_on", s["put_on"], "mom", put),
        ("put_on someone else, the wearer", sp([{"t": 0, "who": "mom", "do": "put_on", "what": "cap", "target": "kid"}],
                                               start={"mom": {"at": "b", "holding": "cap"}, "kid": {"at": "near"}},
                                               props=["cap"]), "kid", put),
        ("place_on a step", sp([{"t": 0, "who": "mom", "do": "place_on", "what": "books", "on": "step"}],
                               start=holding, props=["books"]), "mom", place),
        ("place_on the floor", s["place_on"], "mom", place_low),
        ("open", s["open"], "mom", opening),
        ("close", s["close"], "mom", opening),
        ("drive_to 0.6", s["drive_to"], "mom", 0.6 / drive),
        ("ride_to 0.6, the rider", sp([{"t": 0, "who": "dad", "do": "ride_to", "what": "sedan", "to": "c"}],
                                      start=seated, **_SEDAN), "dad", 0.6 / ride),
        ("ride_to 0.6, the driver", sp([{"t": 0, "who": "dad", "do": "ride_to", "what": "sedan", "to": "c"}],
                                       start=seated, **_SEDAN), "mom", 0.6 / ride),
    ]


@pytest.mark.parametrize("label, data, who, end", _durations(), ids=[d[0] for d in _durations()])
def test_stated_durations_match_the_compiler(label, data, who, end):
    assert_compiles(data)
    ends_at(data, who, end)


@pytest.mark.parametrize("verb", ["turn_to", "set_state", "confetti", "rain"])
def test_verbs_that_take_no_time_occupy_nobody(verb):
    data = copy.deepcopy(VERB_SCENES[verb])
    data["shots"][0]["actions"].append({"t": 0, "who": "mom", "do": "wave"})
    assert_compiles(data)


# -------------------------------------------------------- (e) the reach distances stated

def reach_rows() -> dict[str, str]:
    rows = {}
    for line in section(body("directing"), "## Rules the compiler enforces").splitlines():
        m = re.match(r"^\s*\|\s*`([a-z_]+)`(?:, `[a-z_]+`)?\s*\|(.*)\|\s*$", line)
        if m:
            rows[m.group(1)] = " ".join(m.group(2).split())
    return rows


def _at(du: float, h: float = 0.0) -> dict:
    return {"g": [0.4, 0.0], "m": [round(0.4 + du, 4), h]}


def _reach_cases() -> list[tuple[str, dict]]:
    rows = reach_rows()
    hand = number(rows["hand_to"], r"\*\*([\d.]+) in u\*\* on the same surface")
    two_average = number(rows["hand_to"], r"two `average` puppets reach to ([\d.]+)")
    up_a_step = number(rows["hand_to"], r"two `average` puppets only within ([\d.]+)")
    floor = number(rows["place_on"], r"^([\d.]+) of a mark on the floor")
    step = number(rows["place_on"], r"([\d.]+) of a mark on a step 0.3 up")
    table = number(rows["place_on"], r"about ([\d.]+) of a 0.4-tall table")
    wear = number(rows["put_on"], r"^([\d.]+) of the person")
    lid = number(rows["open"], r"^([\d.]+) of a `carried` prop standing on a step ([\d.]+) to ([\d.]+) up")
    lid_low, lid_high = (number(rows["open"], r"on a step ([\d.]+) to ([\d.]+) up", g) for g in (1, 2))
    cases = []
    for giver, taker in (("mom", "gran"), ("dad", "mom"), ("mom", "dad"), ("mom", "kid"), ("kid", "mom")):
        cases.append((f"hand_to {giver}->{taker} {hand}", sp(
            [{"t": 0, "who": giver, "do": "hand_to", "what": "books", "to": taker}],
            start={giver: {"at": "g", "holding": "books", "facing": "right"}, taker: {"at": "m", "facing": "left"}},
            props=["books"], marks=_at(hand))))
    cases.append((f"hand_to average pair {two_average}", sp(
        [{"t": 0, "who": "mom", "do": "hand_to", "what": "books", "to": "gran"}],
        start={"mom": {"at": "g", "holding": "books"}, "gran": {"at": "m"}}, props=["books"], marks=_at(two_average))))
    cases.append((f"hand_to up a step {up_a_step}", sp(
        [{"t": 0, "who": "mom", "do": "hand_to", "what": "books", "to": "gran"}],
        start={"mom": {"at": "g", "holding": "books"}, "gran": {"at": "m"}}, props=["books"], marks=_at(up_a_step, 0.3))))
    for label, du, h in (("floor", floor, 0.0), ("step", step, 0.3)):
        cases.append((f"place_on {label} {du}", sp(
            [{"t": 0, "who": "mom", "do": "place_on", "what": "books", "on": "m"}],
            start={"mom": {"at": "g", "holding": "books", "facing": "right"}}, props=["books"], marks=_at(du, h))))
    cases.append((f"place_on table {table}", sp(
        [{"t": 0, "who": "mom", "do": "place_on", "what": "books", "on": "table"}],
        start={"mom": {"at": "g", "holding": "books", "facing": "right"}}, props=["books", "table"],
        marks=_at(table), propStart={"table": {"at": "m"}})))
    for wearer in ("dad", "kid"):
        cases.append((f"put_on {wearer} {wear}", sp(
            [{"t": 0, "who": "mom", "do": "put_on", "what": "cap", "target": wearer}],
            start={"mom": {"at": "g", "holding": "cap", "facing": "right"}, wearer: {"at": "m"}},
            props=["cap"], marks=_at(wear))))
    for h in (lid_low, lid_high):
        for verb, state in (("open", "closed"), ("close", "open")):
            cases.append((f"{verb} on a step {h} up at {lid}", sp(
                [{"t": 0, "who": "mom", "do": verb, "what": "suitcase"}],
                start={"mom": {"at": "g", "facing": "right"}}, props=["suitcase"], marks=_at(lid, h),
                propStart={"suitcase": {"at": "m", "state": state}})))
    return cases


@pytest.mark.parametrize("label, data", _reach_cases(), ids=[c[0] for c in _reach_cases()])
def test_stated_reach_distances_compile(label, data):
    assert_compiles(data)


@pytest.mark.parametrize("where", ["the floor", "a table"])
def test_open_does_not_reach_a_carried_prop_on_the_floor_or_a_table(where):
    """The skill: open neither walks nor bends, so a carried prop on the floor is too low and one on a
    table too high for an average puppet (set it on a step)."""
    if where == "the floor":
        prop_start = {"suitcase": {"at": "m", "state": "closed"}}
        props = ["suitcase"]
    else:
        prop_start = {"table": {"at": "m"}, "suitcase": {"on": "table.top", "state": "closed"}}
        props = ["suitcase", "table"]
    for du in (0.0, 0.04, 0.08, 0.12):
        data = sp([{"t": 0, "who": "mom", "do": "open", "what": "suitcase"}], start={"mom": {"at": "g", "facing": "right"}},
                  props=props, marks=_at(du), propStart=prop_start)
        assert [e.code for e in compile_data(data).errors] == ["out_of_reach"], (where, du)


def test_follow_dur_frees_the_leader_as_the_skill_says():
    """A follow without dur waits for every leader move (follow_order when the leader then needs
    the follower); with a dur covering the walk, the same scene compiles."""
    actions = [{"t": 0.5, "who": "kid", "do": "walk_to", "to": "a"},
               {"t": 1, "who": "dad", "do": "follow", "target": "kid", "dur": 3},
               {"t": 8, "who": "dad", "do": "hand_to", "what": "books", "to": "kid"},
               {"t": 12, "who": "kid", "do": "walk_to", "to": "c"}]
    start = {"dad": {"at": "b", "holding": "books"}, "kid": {"at": "near"}}
    assert_compiles(sp(actions, start=start, props=["books"]))
    without = copy.deepcopy(actions)
    del without[1]["dur"]
    assert [e.code for e in compile_data(sp(without, start=start, props=["books"])).errors] == ["follow_order"]
    text = " ".join(body("directing").split())
    assert "`follow_order`" in text and "Give the follow a `dur`" in text


def test_enter_comes_back_from_either_side_as_the_skill_says():
    data = sp([{"t": 0, "who": "mom", "do": "exit", "to": "offstage_right"},
               {"t": 4, "who": "mom", "do": "enter", "from": "offstage_left", "to": "a"}])
    assert_compiles(data)
    walk_on = sp([{"t": 0, "who": "mom", "do": "walk_to", "to": "a"}], start={"mom": {"at": "offstage_left"}})
    assert [e.code for e in compile_data(walk_on).errors] == ["offstage"]
    again = sp([{"t": 0, "who": "mom", "do": "enter", "to": "a"}])
    assert [e.code for e in compile_data(again).errors] == ["already_on_stage"]


def test_vehicle_riders_on_stacked_props_ride_along_as_the_skill_says():
    pickup = {"props": ["pickup", "chair"], "propStart": {"pickup": {"at": "a"}, "chair": {"on": "pickup.bed"}}}
    start = {"mom": {"at": "a", "in": "pickup.driver"}, "kid": {"at": "a", "in": "chair.seat"}}
    speed = number(duration_rows()["ride_to"], r"distance / ([\d.]+)")
    data = sp([{"t": 0, "who": "kid", "do": "ride_to", "what": "pickup", "to": "c"}], start=start, **pickup)
    assert_compiles(data)
    ends_at(data, "mom", 1.2 / speed)  # the driver drives the kid on the chair a to c
    drive = sp([{"t": 0, "who": "mom", "do": "drive_to", "what": "pickup", "to": "c"},
                {"t": 0.5, "who": "kid", "do": "wave"}], start=start, **pickup)
    assert_compiles(drive)  # a drive_to leaves the riders free
    settling = sp([{"t": 0, "who": "kid", "do": "sit_on", "what": "chair"},
                   {"t": 0, "who": "mom", "do": "drive_to", "what": "pickup", "to": "c"}],
                  start={"mom": {"at": "a", "in": "pickup.driver"}, "kid": {"at": "b"}}, **pickup)
    assert [e.code for e in compile_data(settling).errors] == ["busy"]
