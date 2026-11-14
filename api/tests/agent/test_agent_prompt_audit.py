"""Prompt and tool-list audit: every prompt names real tools, args, vocabulary and file paths.

No keys exist, so the first real run with Claude depends on the prompts matching the tools
exactly. This test makes that mechanical. It builds the real tool set on fake services
(`wiring.build_run_tools`) and checks, for the studio prompt, the eight sub-agent prompts
and the two skills the sub-agents read (popup-style, storyboarding):

(a) every backticked or call-shaped tool name is in that agent's toolset or a deepagents
    built-in, and the built-ins are the ones the real graph binds;
(b) every argument name a prompt shows for a tool call is in that tool's args schema;
(c) every backticked word is known vocabulary (shared/vocabulary.json, the rig poses, the
    screenplay schema, tool args and results) or on a short commented allowlist, and the
    allowed values a prompt lists for a field are the field's real values;
(d) every virtual file path is a constant the tools or the studio use, or a draft that one
    prompt tells its agent to write, and no prompt writes a file a tool owns;
(e) the run context keys the studio prompt names are exactly `agent.runs.run_context`'s;
(f) no prompt is a prefix of another (the fake studio tells agents apart by prefix).

Extraction is plain regex over backticks and `name(` patterns. director.md and the
directing skill are owned by another task: director.md's known mismatches sit in the one
xfail list below, and the directing skill is not audited here.
"""

from __future__ import annotations

import json
import re
import typing
from collections.abc import Iterable
from functools import cache
from pathlib import Path
from typing import Any

import pytest
from deepagents.middleware.filesystem import _FS_TOOL_ORDER, FilesystemMiddleware
from deepagents.middleware.subagents import TaskToolSchema
from langchain.agents.middleware import TodoListMiddleware
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from pydantic import BaseModel

import schemas
from agent import runs, studio, subagents, wiring
from agent.fake_llm import ScriptedChatModel, system_prompt_of, tool_calls
from agent.run_info import RunInfo
from agent.subagents import SKILLS_ROOT, STUDIO_PROMPT, SUBAGENT_NAMES, SUBAGENT_SKILLS, SUBAGENT_TOOLS, load_prompt
from agent.tools import compile as compile_tool
from agent.tools import frames, props, sets
from screenplay import schema as screenplay_schema

API = Path(__file__).resolve().parents[2]
REPO = API.parent
SKILLS_DIR = API / "agent" / "skills"
VOCAB: dict[str, Any] = json.loads((REPO / "shared" / "vocabulary.json").read_text())
POSES: dict[str, Any] = json.loads((REPO / "shared" / "rig" / "poses.json").read_text())
SAMPLE_SCREENPLAY: dict[str, Any] = json.loads(
    (REPO / "web" / "public" / "samples" / "hymn_books.screenplay.json").read_text()
)

# --------------------------------------------------------------------------- the xfail list

DIRECTOR_OWNER = "director.md owned by agent-real-compiler-readiness"

# (check, unit) pairs known to fail on a file this task does not own. One list, one reason.
XFAIL: dict[tuple[str, str], str] = {
    # director.md step 2 says "write them to `/compile_errors.txt`", but compile_screenplay
    # already writes that file itself (agent.tools.compile.ERRORS_PATH) on every failure.
    ("tool_owned_files", "director"): DIRECTOR_OWNER,
}

# --------------------------------------------------------------------------- allowlist

# Backticked words that are prose or examples, not vocabulary. Keep this short; each says why.
ALLOWED_WORDS: dict[str, str] = {
    "u": "mark / grip coordinate name, [u, h, d] and [u, v] (shared/CONTRACTS.md, Marks)",
    "v": "grip / interaction position coordinate, [u, v]",
    "h": "mark height coordinate, [u, h]",
    "d": "mark depth coordinate, [u, h, d]",
    "n": "sentence number in telling:<authorId>:s<n>",
    "null": "JSON null (placeholder art)",
    "steps_top": "example mark name; the sample set's mark (hymn_books.memory.json)",
    "steps_left": "example mark name; the sample set's mark",
    "curb": "example mark name in set_designer.md",
    "church_steps_2": "example suffixed set id (agent.tools.sets: never overwrite a saved place)",
    "also_named": "interviewer return-shape key: people named in the story but not in the tree (never cast)",
}

# --------------------------------------------------------------------------- what is audited

PROMPT_UNITS: tuple[str, ...] = ("studio", *SUBAGENT_NAMES)
SKILL_UNITS: tuple[str, ...] = ("popup-style", "storyboarding")
UNITS: tuple[str, ...] = (*PROMPT_UNITS, *(f"skill:{s}" for s in SKILL_UNITS))


def unit_text(unit: str) -> str:
    if unit.startswith("skill:"):
        return (SKILLS_DIR / unit.removeprefix("skill:") / "SKILL.md").read_text(encoding="utf-8")
    return load_prompt(unit)


def skill_users(skill: str) -> list[str]:
    return [name for name, paths in SUBAGENT_SKILLS.items() if f"{SKILLS_ROOT}{skill}/" in paths]


# --------------------------------------------------------------------------- the real tools


def _idle_model() -> ScriptedChatModel:
    return ScriptedChatModel(responder=lambda _messages, _names: AIMessage(content="ok"))


def _run(memory_id: str | None = None) -> RunInfo:
    fields = schemas.SubmitMemoryFields(text="We met on the church steps.", year=1988, memory_id=memory_id)
    return RunInfo(run_id="audit", family_id="fam", user_id="u1", author_id="dad", fields=fields)


@cache
def real_tools() -> dict[str, Any]:
    return wiring.build_run_tools(_run(), wiring.fake_services(model=_idle_model()))


FS_TOOLS: frozenset[str] = frozenset(_FS_TOOL_ORDER)
"""deepagents' filesystem tools (ls, read_file, write_file, edit_file, delete, glob, grep).
`execute` is only bound with a sandbox backend, which the studio does not use."""
STUDIO_BUILTINS: frozenset[str] = FS_TOOLS | {"task", "write_todos"}
SUBAGENT_BUILTINS: frozenset[str] = FS_TOOLS
"""Declarative sub-agents get the filesystem tools only: TodoListMiddleware is the studio's
own middleware (studio.build_studio), and `task` belongs to the parent."""


def toolset(unit: str) -> set[str]:
    if unit == "studio":
        return set(wiring.STUDIO_TOOL_NAMES) | STUDIO_BUILTINS
    if unit.startswith("skill:"):
        users = skill_users(unit.removeprefix("skill:"))
        assert users, f"{unit}: no sub-agent loads it"
        return set().union(*(toolset(u) for u in users))
    return set(SUBAGENT_TOOLS[unit]) | SUBAGENT_BUILTINS


def _builtin_tools() -> dict[str, Any]:
    tools = {t.name: t for t in [*FilesystemMiddleware().tools, *TodoListMiddleware().tools]}
    return {name: tools[name] for name in STUDIO_BUILTINS - {"task"}}


def _schema_of(tool: Any) -> dict[str, Any]:
    return tool.tool_call_schema.model_json_schema()


@cache
def tool_schemas() -> dict[str, dict[str, Any]]:
    """Model-facing JSON schema of every real and built-in tool, by name."""
    out = {name: _schema_of(t) for name, t in {**real_tools(), **_builtin_tools()}.items()}
    out["task"] = TaskToolSchema.model_json_schema()
    return out


@cache
def all_tool_names() -> frozenset[str]:
    return frozenset(tool_schemas())


def _walk_properties(schema: Any) -> Iterable[tuple[str, dict[str, Any]]]:
    """Every (property name, property schema) at any depth, $defs included."""
    if isinstance(schema, dict):
        for name, prop in (schema.get("properties") or {}).items():
            yield name, prop
        for value in schema.values():
            yield from _walk_properties(value)
    elif isinstance(schema, list):
        for value in schema:
            yield from _walk_properties(value)


def _enum_values(prop: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(prop, dict):
        for key in ("enum",):
            found |= {v for v in prop.get(key) or [] if isinstance(v, str)}
        if isinstance(prop.get("const"), str):
            found.add(prop["const"])
        for key in ("anyOf", "oneOf", "allOf", "items"):
            sub = prop.get(key)
            for s in sub if isinstance(sub, list) else [sub]:
                found |= _enum_values(s)
    return found


def top_args(tool: str) -> set[str]:
    return set(tool_schemas()[tool].get("properties") or {})


def any_depth_keys(tool: str) -> set[str]:
    return {name for name, _ in _walk_properties(tool_schemas()[tool])}


# --------------------------------------------------------------------------- vocabulary


def _models(module: Any) -> list[type[BaseModel]]:
    return [
        v for v in vars(module).values() if isinstance(v, type) and issubclass(v, BaseModel) and v is not BaseModel
    ]


def _literal_values(annotation: Any) -> set[str]:
    if typing.get_origin(annotation) is typing.Literal:
        return {a for a in typing.get_args(annotation) if isinstance(a, str)}
    return set().union(*(_literal_values(a) for a in typing.get_args(annotation))) if typing.get_args(annotation) else set()


@cache
def schema_fields() -> dict[str, set[str]]:
    """Field name (alias and python name) -> its Literal values, across the screenplay and API schemas."""
    fields: dict[str, set[str]] = {}
    for model in [*_models(screenplay_schema), *_models(schemas)]:
        for name, info in model.model_fields.items():
            values = _literal_values(info.annotation)
            for key in {name.rstrip("_"), info.alias or name}:
                fields.setdefault(key, set()).update(values)
    return fields


@cache
def enums() -> dict[str, set[str]]:
    """Field or argument name -> its allowed string values, wherever the code pins them."""
    out: dict[str, set[str]] = {k: set(v) for k, v in schema_fields().items() if v}
    for schema in tool_schemas().values():
        for name, prop in _walk_properties(schema):
            values = _enum_values(prop)
            if values:
                out.setdefault(name, set()).update(values)
    out.setdefault("size", set()).update(props.SIZES)
    out.setdefault("layer", set()).update(sets.LAYER_DEPTH)
    out.setdefault("kind", set()).update(VOCAB["interactionKinds"])
    return out


def _state_names_from_verbs() -> set[str]:
    """The prop states the open/close verbs set ("sets prop state to open")."""
    return {
        m.group(1)
        for spec in VOCAB["actions"].values()
        if (m := re.search(r"sets prop state to (\w+)", spec.get("note", "")))
    }


@cache
def tool_result_keys() -> set[str]:
    """Keys of the JSON the prop and set tools return, from real calls on fake services."""
    tools = real_tools()
    keys: set[str] = set()
    made = json.loads(tools["generate_prop"].invoke({"name": "hymn books", "description": "blue books", "size": "hand"}))
    keys |= set(made)
    for item in json.loads(tools["find_prop"].invoke({"description": "hymn books"})):
        keys |= set(item)
    layer = json.loads(
        tools["generate_set_layer"].invoke({"place": "church steps", "layer": "back", "description": "a wall"})
    )
    keys |= set(layer)
    return keys


@cache
def vocabulary() -> dict[str, set[str]]:
    """Every known word, by where it comes from."""
    return {
        "actions": set(VOCAB["actions"]),
        "camera": set(VOCAB["camera"]),
        "effects": set(VOCAB["effects"]),
        "timesOfDay": set(VOCAB["timesOfDay"]),
        "transitions": set(VOCAB["transitions"]),
        "builtinMarks": set(VOCAB["builtinMarks"]),
        "sizes": set(VOCAB["sizes"]),
        "builds": set(VOCAB["builds"]),
        "interactionKinds": set(VOCAB["interactionKinds"]),
        "eras": {e["id"] for e in VOCAB["eras"]},
        "action params": {p for spec in VOCAB["actions"].values() for p in spec["required"] + spec["optional"]},
        "poses": set(POSES["poses"]),
        "prop states": _state_names_from_verbs(),
        "prop body part": {props.BODY},
        "set layers": set(sets.LAYER_DEPTH),
        "schema fields": set(schema_fields()),
        "schema values": set().union(*enums().values()),
        "tool args": set().union(*(any_depth_keys(t) for t in tool_schemas())),
        "tool results": tool_result_keys(),
        "run context": set(run_context_keys()),
        "skills": {p.name for p in SKILLS_DIR.iterdir() if p.is_dir()},
        "sample ids": {*SAMPLE_SCREENPLAY["cast"], *SAMPLE_SCREENPLAY["props"]},
        "allowlist": set(ALLOWED_WORDS),
    }


def run_context_keys() -> list[str]:
    return list(runs.run_context(_run(), "Dad"))


# --------------------------------------------------------------------------- extraction

BACKTICK = re.compile(r"`([^`\n]+)`")
IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
CALL = re.compile(r"(?<![\w.])([a-z][a-z0-9_]*)\(([^()\n]*)\)")
SHOT_OR_SENTENCE_ID = re.compile(r"^s\d+$")


def code_free(text: str) -> str:
    """The text without fenced code blocks (output formats and examples, not instructions)."""
    return re.sub(r"```.*?```", "", text, flags=re.DOTALL)


def paragraphs(text: str) -> list[str]:
    return [p for p in re.split(r"\n\s*\n", code_free(text)) if p.strip()]


def sentences(paragraph: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9*(-])|\n\s*[-0-9]+[.)]?\s", paragraph) if s.strip()]


def backticked(text: str) -> list[str]:
    return BACKTICK.findall(code_free(text))


def idents(text: str) -> list[str]:
    return [t for t in backticked(text) if IDENT.match(t)]


def calls(text: str) -> list[re.Match[str]]:
    """Call-shaped `name(...)` mentions. Outside backticks only snake_case names count, so
    prose like "action(s)" is not taken for a call."""
    body = code_free(text)
    found = [m for chunk in BACKTICK.findall(body) for m in CALL.finditer(chunk)]
    found += [m for m in CALL.finditer(BACKTICK.sub("", body)) if "_" in m.group(1)]
    return found


def tools_named(text: str) -> set[str]:
    return {t for t in idents(text) if t in all_tool_names()} | {m.group(1) for m in calls(text)}


def json_keys(snippet: str) -> list[str]:
    return re.findall(r'"(\w+)"\s*:', snippet)


def json_string_pairs(snippet: str) -> list[tuple[str, str]]:
    return re.findall(r'"(\w+)"\s*:\s*"([^"<>]*)"', snippet)


# --------------------------------------------------------------------------- helpers


def _fail_or_xfail(check: str, unit: str, problems: list[str]) -> None:
    reason = XFAIL.get((check, unit))
    if problems and reason:
        pytest.xfail(f"{reason}: {problems}")
    assert not problems, f"{unit}: " + "; ".join(problems)


def test_xfail_list_only_covers_director():
    assert {unit for _check, unit in XFAIL} <= {"director"}
    assert set(XFAIL.values()) == {DIRECTOR_OWNER}


# --------------------------------------------------------------------------- (a) tool names


@pytest.mark.parametrize("unit", UNITS)
def test_a_every_tool_named_is_in_the_agents_toolset(unit):
    text = unit_text(unit)
    allowed = toolset(unit)
    problems = [f"`{name}` is not one of its tools" for name in sorted(tools_named(text) - allowed)]
    _fail_or_xfail("tool_names", unit, problems)


@pytest.mark.parametrize("name", SUBAGENT_NAMES)
def test_a_every_subagent_tool_is_named_in_its_prompt(name):
    """A tool the prompt never names is a tool the model has no instructions for."""
    named = tools_named(load_prompt(name))
    assert set(SUBAGENT_TOOLS[name]) <= named, f"{name}.md never names {set(SUBAGENT_TOOLS[name]) - named}"


def test_a_every_declared_tool_exists():
    tools = real_tools()
    assert set(wiring.STUDIO_TOOL_NAMES) <= set(tools)
    for name, wanted in SUBAGENT_TOOLS.items():
        assert set(wanted) <= set(tools), f"{name}: {set(wanted) - set(tools)} not built by wiring"


def _drive_every_agent() -> list[tuple[list[str], list[BaseMessage]]]:
    """Run the real studio graph once: the studio calls `task` for every sub-agent, which answer at once."""

    def responder(messages: list[BaseMessage], names: list[str]) -> AIMessage:
        if "task" not in names:
            return AIMessage(content="done")
        if any(isinstance(m, ToolMessage) for m in messages):
            return AIMessage(content="finished")
        return tool_calls(*[("task", {"description": "audit", "subagent_type": n}) for n in SUBAGENT_NAMES])

    model = ScriptedChatModel(responder=responder)
    run = _run()
    graph = wiring.build_run_graph(run, wiring.fake_services(model=model))
    graph.invoke({"messages": [HumanMessage(content="{}")]}, studio.thread_config(run.run_id))
    return model.calls


def _role(messages: list[BaseMessage]) -> str:
    prompt = system_prompt_of(messages).lstrip()
    matches = [unit for unit in PROMPT_UNITS if prompt.startswith(unit_text(unit).strip())]
    assert len(matches) == 1, f"system prompt matches {matches}: {prompt[:60]!r}"
    return matches[0]


def test_a_builtins_are_what_the_real_graph_binds():
    """The toolsets above are exactly the tools the model is offered, agent by agent."""
    bound: dict[str, set[str]] = {}
    for names, messages in _drive_every_agent():
        bound.setdefault(_role(messages), set(names))
    assert set(bound) == set(PROMPT_UNITS)
    for unit in PROMPT_UNITS:
        assert bound[unit] == toolset(unit), f"{unit}: bound {sorted(bound[unit] ^ toolset(unit))} differ"


# --------------------------------------------------------------------------- (b) argument names

ARG_VERB = re.compile(r"\b(?:[Pp]ass(?:es|ing)?|[Ll]eave(?= `\w+` out))\b")
WITH_CLAUSE = re.compile(r"`(\w+)`[^.`;]{0,40}?\bwith\b(.*)$")
KEY_STRING = re.compile(r'^(\w+):\s*"[^"]*"$')


def argument_mentions(text: str) -> list[tuple[str, set[str], str]]:
    """(argument, tools it may belong to, where) for every argument a prompt shows for a call."""
    found: list[tuple[str, set[str], str]] = []
    tool_names = all_tool_names()
    for m in calls(text):
        tool, inner = m.group(1), m.group(2)
        for piece in filter(None, (p.strip() for p in inner.split(","))):
            if piece[0] in "\"'0123456789[{":
                continue
            arg = re.split(r"\s*[=:]\s*", piece)[0]
            if IDENT.match(arg):
                found.append((arg, {tool}, m.group(0)))
    in_unit = tools_named(text) & tool_names
    for paragraph in paragraphs(text):
        # A paragraph that names no tool continues the ones around it (a skill's grip section).
        here = (tools_named(paragraph) & tool_names) or in_unit
        for sentence in sentences(paragraph):
            if (w := WITH_CLAUSE.search(sentence)) and w.group(1) in tool_names:
                clause = w.group(2)
                for token in BACKTICK.findall(clause):
                    if IDENT.match(token) and token not in tool_names:
                        found.append((token, {w.group(1)}, sentence.strip()))
                    elif token.startswith("{"):
                        found += [(k, {w.group(1)}, token) for k in json_keys(token)]
            if "arguments are" in sentence:
                found += [(t, here, sentence.strip()) for t in idents(sentence) if t not in tool_names]
            # "pass `grip`", "pass its id to `generate_prop` as `reference_photo_id`", "Leave `edits` out":
            # the first non-tool word after the verb.
            for verb in ARG_VERB.finditer(sentence):
                after = [t for t in idents(sentence[verb.end():]) if t not in tool_names]
                if after:
                    found.append((after[0], here, sentence.strip()))
            for token in BACKTICK.findall(sentence):
                if (k := KEY_STRING.match(token)) is not None:
                    found.append((k.group(1), here, token))
    return found


def argument_problems(text: str) -> list[str]:
    problems = []
    for arg, tools, where in argument_mentions(text):
        if not tools & all_tool_names():
            problems.append(f"{where!r} calls an unknown tool {sorted(tools)}")
            continue
        if not any(arg in any_depth_keys(t) for t in tools if t in all_tool_names()):
            problems.append(f"`{arg}` is not an argument of {sorted(tools)} ({where[:80]!r})")
    return problems


@pytest.mark.parametrize("unit", UNITS)
def test_b_every_argument_shown_is_in_the_tools_schema(unit):
    _fail_or_xfail("arguments", unit, argument_problems(unit_text(unit)))


@pytest.mark.parametrize(
    "bad",
    [
        "Call `generate_prop` with a `colour` for it.",
        "Then call `find_set(location)`.",
        "Call `render_frames` with `{\"time\": [1.0]}`.",
        "For a held prop, pass `handle` to `generate_prop`.",
        "Call `generate_set_layer` for each layer: `level: \"back\"`.",
    ],
)
def test_b_a_wrong_argument_is_caught(bad):
    assert argument_problems(bad), bad


def test_b_the_extraction_finds_the_arguments_it_should():
    """Guard the regexes: known argument mentions in the prompts are found."""
    found = {(arg, frozenset(tools)) for unit in UNITS for arg, tools, _ in argument_mentions(unit_text(unit))}
    for arg, tool in [
        ("place", "find_set"),
        ("grip", "generate_prop"),
        ("name", "generate_prop"),
        ("size", "generate_prop"),
        ("times", "render_frames"),
        ("person_ids", "link_participants"),
        ("question", "ask_teller"),
        ("subagent_type", "task"),
        ("description", "find_prop"),
        ("edits", "confirm_props"),
        ("reference_photo_id", "generate_prop"),
        ("reference_photo_id", "generate_set_layer"),
        ("candidate_name", "merge_people"),
    ]:
        assert any(a == arg and tool in tools for a, tools in found), (arg, tool)


# --------------------------------------------------------------------------- (c) vocabulary


def classify(word: str, unit: str) -> str | None:
    if word in all_tool_names():
        return "tool" if word in toolset(unit) else None
    if SHOT_OR_SENTENCE_ID.match(word):
        return "shot or sentence id"
    for source, words in vocabulary().items():
        if word in words:
            return source
    return None


@pytest.mark.parametrize("unit", UNITS)
def test_c_every_backticked_word_is_known_vocabulary(unit):
    unknown = sorted({w for w in idents(unit_text(unit)) if classify(w, unit) is None})
    _fail_or_xfail("vocabulary", unit, [f"`{w}` is not in the vocabulary, the schemas or the allowlist" for w in unknown])


VALUE_LIST = re.compile(r"`(\w+)`\s*(?:\(|is\s+|=\s*|:\s*)((?:[^`\n)]{0,30}?`[^`\n]+`){1,6})")


def listed_values(text: str) -> list[tuple[str, str]]:
    """(field, value) for "`field` (`a` or `b`)", "`field` is `a` or `b`", `field: "a"` and JSON "field": "a"."""
    pairs: list[tuple[str, str]] = []
    body = code_free(text)
    for m in VALUE_LIST.finditer(body):
        for value in BACKTICK.findall(m.group(2)):
            value = value.strip().strip('"')
            if IDENT.match(value):
                pairs.append((m.group(1), value))
    for token in BACKTICK.findall(body):
        if (k := re.match(r'^(\w+):\s*"([^"<>]*)"$', token)) is not None:
            pairs.append((k.group(1), k.group(2)))
        if token.startswith("{"):
            pairs += json_string_pairs(token)
    return pairs


@pytest.mark.parametrize("unit", UNITS)
def test_c_listed_values_are_the_fields_real_values(unit):
    known = enums()
    problems = [
        f"`{field}` is never {value!r} (allowed: {sorted(known[field])})"
        for field, value in listed_values(unit_text(unit))
        if field in known and value not in known[field]
    ]
    _fail_or_xfail("values", unit, problems)


@pytest.mark.parametrize("unit", UNITS)
def test_c_size_class_lists_hold_only_size_classes(unit):
    problems = []
    for line in code_free(unit_text(unit)).splitlines():
        if "size class" not in line.lower():
            continue
        for word in BACKTICK.findall(line):
            fields = vocabulary()["tool args"] | vocabulary()["schema fields"]
            if IDENT.match(word) and word not in all_tool_names() and word not in fields:
                if word not in VOCAB["sizes"]:
                    problems.append(f"`{word}` in a size-class line is not a size class")
    _fail_or_xfail("sizes", unit, problems)


@pytest.mark.parametrize("unit", UNITS)
def test_c_sources_use_the_telling_format(unit):
    pattern = re.compile(r"^telling:(?:<\w+>|[a-z0-9_]+):s(?:<n>|\d+)$")
    tokens = [t for t in backticked(unit_text(unit)) if t.startswith("telling:")]
    assert all(pattern.match(t) for t in tokens), tokens
    for shot in SAMPLE_SCREENPLAY["shots"]:
        for line in shot["narration"]:
            assert pattern.match(line["source"]), line["source"]


def test_c_vocabulary_check_catches_a_wrong_word():
    assert classify("walk_to", "director") == "actions"
    assert classify("pull_tab", "director") == "interactionKinds"
    assert classify("huge", "prop_master") is None
    assert classify("generate_prop", "narrator") is None  # a real tool, but not the narrator's
    assert ("size", "huge") in listed_values('pass `size: "huge"`')
    assert "huge" not in enums()["size"]


# --------------------------------------------------------------------------- (d) virtual paths

PATH = re.compile(r"(?<![\w.:/<>\]])(/[A-Za-z_<][\w<>./-]*)")

TOOL_OWNED: dict[str, str] = {
    compile_tool.TIMELINE_PATH: "compile_screenplay (then synthesize_narration)",
    compile_tool.ERRORS_PATH: "compile_screenplay",
    compile_tool.LOG_PATH: "compile_screenplay",
    frames.FRAMES_PATH: "render_frames",
}
TOOL_PATHS: set[str] = {compile_tool.SCREENPLAY_PATH, *TOOL_OWNED}

# Drafts no tool reads: the agent that writes each one, per its prompt.
DRAFTS: dict[str, str] = {
    "/shotlist.md": "storyboarder",
    "/critique.md": "checker",
    "/open_questions.md": "interviewer",
}


def virtual_paths(text: str) -> set[str]:
    return {p.rstrip(".,;:)") for p in PATH.findall(text)}


def path_problem(path: str) -> str | None:
    if path in TOOL_PATHS or path in DRAFTS:
        return None
    if path.startswith(studio.MEMORIES_ROUTE):
        return None
    if path.startswith(SKILLS_ROOT):
        rest = path.removeprefix(SKILLS_ROOT).strip("/")
        if not rest or (SKILLS_DIR / rest.split("/")[0]).is_dir():
            return None
        return f"{path}: no such skill"
    return f"{path} is not a path any tool or the studio uses"


def test_d_code_constants_agree():
    from agent.tools import family, narration

    assert family.SCREENPLAY_PATH == narration.SCREENPLAY_PATH == compile_tool.SCREENPLAY_PATH
    assert family.TIMELINE_PATH == narration.TIMELINE_PATH == compile_tool.TIMELINE_PATH
    assert studio.SKILLS_ROUTE == SKILLS_ROOT
    assert studio.MEMORIES_ROUTE == "/memories/"


@pytest.mark.parametrize("unit", UNITS)
def test_d_every_virtual_path_is_real(unit):
    problems = [p for path in sorted(virtual_paths(unit_text(unit))) if (p := path_problem(path))]
    _fail_or_xfail("paths", unit, problems)


def test_d_a_wrong_path_is_caught():
    assert path_problem("/scenes.json")
    assert path_problem("/skills/acting/SKILL.md")
    assert path_problem(compile_tool.SCREENPLAY_PATH) is None
    assert path_problem(frames.FRAMES_PATH) is None
    assert path_problem("/memories/people/dad.md") is None
    assert virtual_paths("read `/screenplay.json`, then /memories/style.md.") == {"/screenplay.json", "/memories/style.md"}


def test_d_every_draft_has_a_writer():
    for path, writer in DRAFTS.items():
        text = load_prompt(writer)
        assert re.search(rf"[Ww]rite\s+`{re.escape(path)}`", text), f"{writer}.md never says to write {path}"
        assert "write_file" in toolset(writer)


@pytest.mark.parametrize("unit", UNITS)
def test_d_no_prompt_writes_a_file_a_tool_owns(unit):
    pattern = re.compile(r"(?<![Nn]ever )(?<![Nn]ot )\b[Ww]rite (?:them |it |the \w+ )?(?:to |into )?`(/[^`]+)`")
    problems = [
        f"says to write {m.group(1)}, which {TOOL_OWNED[m.group(1)]} writes"
        for m in pattern.finditer(unit_text(unit))
        if m.group(1) in TOOL_OWNED
    ]
    _fail_or_xfail("tool_owned_files", unit, problems)


# --------------------------------------------------------------------------- (e) run context


def _section(text: str, heading: str) -> str:
    start = text.index(heading)
    end = text.find("\n## ", start + len(heading))
    return text[start:] if end == -1 else text[start:end]


def test_e_studio_names_exactly_the_run_context_keys():
    section = _section(STUDIO_PROMPT, "## Run context")
    named = {w for w in idents(section) if w not in all_tool_names()}
    keys = set(run_context_keys())
    assert named == keys, f"named but not sent: {named - keys}; sent but not named: {keys - named}"


def test_e_run_context_is_the_studios_first_message():
    """runs.py sends run_context as JSON in the first message; the keys survive a round trip."""
    context = runs.run_context(_run(memory_id="m1"), "Dad")
    assert json.loads(json.dumps(context)) == context
    assert context["memoryId"] == "m1"
    assert context["authorId"] == "dad"


# --------------------------------------------------------------------------- (f) prefixes


def test_f_no_prompt_is_a_prefix_of_another():
    texts = {name: load_prompt(name).strip() for name in subagents.PROMPT_NAMES}
    for a, ta in texts.items():
        for b, tb in texts.items():
            if a != b:
                assert not tb.startswith(ta), f"{a}.md is a prefix of {b}.md"


def test_f_every_agent_is_told_apart_by_its_prompt():
    """The real graph's system prompts each start with exactly one prompt (fake_studio.role_of)."""
    roles = {_role(messages) for _names, messages in _drive_every_agent()}
    assert roles == set(PROMPT_UNITS)


# --------------------------------------------------------------------------- the studio's story


def test_studio_states_the_order_the_cap_second_tellings_and_one_question():
    text = STUDIO_PROMPT
    new_memory = _section(text, "## Step 2").split("### Second telling")[0]
    order = ["**interviewer**", "**storyboarder**", "**prop_master**", "**set_designer**", "**director**",
             "**checker**", "**narrator**, voice pass", "`save_memory`", "**linker**"]
    positions = [new_memory.index(item) for item in order]
    assert positions == sorted(positions)
    lowered = text.lower()
    assert "at most 3 compile rounds" in lowered
    cap = _section(text, "## At most 3 compile rounds").lower()
    assert "do not call `save_memory`" in cap and "final message" in cap
    assert "one question at a time" in lowered
    assert "never two calls in the same turn" in lowered
    mode = _section(text, "## Run context").lower()
    assert "no new props, sets or shots" in mode and "narration only, then save" in mode
