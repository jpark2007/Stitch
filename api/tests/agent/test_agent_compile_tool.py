"""compile_screenplay tool, parse_and_compile, and the compiler seam.

Every test that runs a compiler runs twice, on the FakeCompiler and on `load_compiler()` (the
real `screenplay.compiler.compile` on main), through the `compiler` fixture. Nothing here pins
one compiler's wording: the bad draft's message is read back from the compiler under test
(`bad_message`), and only its head (`BAD_HEAD`) and code are fixed.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from deepagents.backends.utils import create_file_data, file_data_to_string
from deepagents.middleware.filesystem import FilesystemState
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode
from pydantic import ValidationError

import schemas
from agent.fake_compiler import FakeCompiler
from agent.tools import compile as compile_mod
from agent.tools.compile import (
    MAX_COMPILE_ROUNDS,
    Compiler,
    SceneInputs,
    SceneLookupError,
    load_compiler,
    make_compile_tool,
    parse_and_compile,
)
from screenplay.schema import CompileError, CompileResult, Screenplay, Timeline

REPO = Path(__file__).resolve().parents[3]
SAMPLES = REPO / "web" / "public" / "samples"


def _load(name: str) -> dict:
    return json.loads((SAMPLES / name).read_text())


SAMPLE = _load("hymn_books.screenplay.json")
SAMPLE_TEXT = json.dumps(SAMPLE)
MEMORY = _load("hymn_books.memory.json")
SCENE = SceneInputs(
    sets={s["id"]: schemas.SetDef.model_validate(s) for s in MEMORY["sets"]},
    props={p["id"]: schemas.Prop.model_validate(p) for p in MEMORY["props"]},
    people={p["id"]: schemas.Person.model_validate(p) for p in MEMORY["people"]},
)

BAD = copy.deepcopy(SAMPLE)
BAD["shots"][0]["actions"][4]["t"] = 5  # hand_to before dad picks the books up (mom has dropped them)
BAD["shots"][0]["actions"].pop(3)  # remove pick_up
BAD_TEXT = json.dumps(BAD)
BAD_HEAD = 'shot s1, t=5: dad cannot hand_to "hymn_books"; '  # the reason after it is the compiler's
ROUND_1 = "round 1 of 3\nFix /screenplay.json and call compile_screenplay again."
STOP = ("STOP: 3 compile rounds have failed. Do not edit or recompile again; "
        "report these errors as your final answer.")


def scene_for(_: Screenplay) -> SceneInputs:
    return SCENE


def _real_compiler() -> Compiler:
    compiler = load_compiler()
    if isinstance(compiler, FakeCompiler):
        pytest.skip("screenplay.compiler.compile is not importable; load_compiler() fell back to the fake")
    return compiler


@pytest.fixture(params=["fake", "real"])
def compiler(request) -> Compiler:
    return FakeCompiler() if request.param == "fake" else _real_compiler()


def bad_message(compiler: Compiler) -> str:
    """The one error the compiler under test gives for BAD: dad hands over books nobody holds."""
    res = parse_and_compile(BAD_TEXT, compiler, scene_for)
    (err,) = res.errors
    assert err.code == "not_holding"
    assert err.message.startswith(BAD_HEAD) and len(err.message) > len(BAD_HEAD), err.message
    return err.message


# ------------------------------------------------------------------ parse_and_compile


def test_parse_and_compile_ok(compiler):
    res = parse_and_compile(SAMPLE_TEXT, compiler, scene_for)
    assert res.ok and isinstance(res.timeline, Timeline)


def test_invalid_json(compiler):
    res = parse_and_compile('{"memoryId": "m1",\n  "cast": [', compiler, scene_for)
    assert not res.ok
    (err,) = res.errors
    assert err.code == "invalid_json"
    assert "line 2" in err.message and "column" in err.message


def _pydantic_error_count(data: dict) -> int:
    with pytest.raises(ValidationError) as info:
        Screenplay.model_validate(data)
    return len(info.value.errors())


def test_unknown_verb_gives_one_error_per_pydantic_error(compiler):
    data = copy.deepcopy(SAMPLE)
    data["shots"][0]["actions"][0]["do"] = "teleport"
    res = parse_and_compile(json.dumps(data), compiler, scene_for)
    assert not res.ok
    assert len(res.errors) == _pydantic_error_count(data)
    assert all(e.code == "invalid_screenplay" for e in res.errors)
    assert res.errors[0].message.startswith("shots.0.actions.0.do: Input should be ")


def test_unknown_field_gives_one_error_per_pydantic_error(compiler):
    data = copy.deepcopy(SAMPLE)
    data["shots"][0]["actions"][1]["teleport"] = True
    data["shots"][0]["pageTxt"] = "typo"
    res = parse_and_compile(json.dumps(data), compiler, scene_for)
    assert len(res.errors) == _pydantic_error_count(data) == 2
    messages = {e.message for e in res.errors}
    assert "shots.0.actions.1.teleport: Extra inputs are not permitted" in messages
    assert "shots.0.pageTxt: Extra inputs are not permitted" in messages


@pytest.mark.parametrize(
    ("exc", "code", "message"),
    [
        (SceneLookupError("set", "church_steps_1988"), "unknown_set", 'unknown set "church_steps_1988"'),
        (KeyError("prop:hymn_books"), "unknown_prop", 'unknown prop "hymn_books"'),
        (KeyError("mom"), "unknown_person", 'unknown person "mom"'),
        (KeyError("church_steps_1988"), "unknown_set", 'unknown set "church_steps_1988"'),
        (KeyError("hymn_books"), "unknown_prop", 'unknown prop "hymn_books"'),
    ],
)
def test_scene_lookup_key_errors_become_compile_errors(compiler, exc, code, message):
    def failing(_: Screenplay) -> SceneInputs:
        raise exc

    res = parse_and_compile(SAMPLE_TEXT, compiler, failing)
    assert not res.ok
    assert [(e.code, e.message) for e in res.errors] == [(code, message)]


def test_compiler_errors_pass_through_verbatim(compiler):
    direct = compiler(Screenplay.model_validate(BAD), SCENE)
    res = parse_and_compile(BAD_TEXT, compiler, scene_for)
    assert [e.message for e in res.errors] == [e.message for e in direct.errors] == [bad_message(compiler)]


def test_both_compilers_give_the_same_bad_draft_message():
    """The fake says what the real compiler says for a prop lying on the ground."""
    assert bad_message(FakeCompiler()) == bad_message(_real_compiler())


# ----------------------------------------------------------------------- load_compiler


def test_load_compiler_falls_back_to_fake_while_compiler_is_a_stub(monkeypatch):
    import screenplay.compiler as real

    monkeypatch.delattr(real, "compile", raising=False)
    assert isinstance(load_compiler(), FakeCompiler)


def test_load_compiler_uses_the_real_compile_when_present(monkeypatch):
    import screenplay.compiler as real

    calls = []
    canned = CompileResult(ok=False, errors=[CompileError(code="out_of_reach", message="shot s1, t=1: too far")])

    def fake_compile(screenplay, *, sets, props, people):
        calls.append((screenplay, sets, props, people))
        return canned

    monkeypatch.setattr(real, "compile", fake_compile, raising=False)
    compiler = load_compiler()
    assert not isinstance(compiler, FakeCompiler)
    sp = Screenplay.model_validate(SAMPLE)
    assert compiler(sp, SCENE) is canned
    ((got_sp, sets, props, people),) = calls
    assert got_sp is sp and sets is SCENE.sets and props is SCENE.props and people is SCENE.people
    # and through parse_and_compile the errors come back verbatim
    res = parse_and_compile(SAMPLE_TEXT, compiler, scene_for)
    assert res.errors[0].message == "shot s1, t=1: too far"


# ------------------------------------------------------------------------------ the tool


def _graph(compiler: Compiler):
    tool = make_compile_tool(compiler, scene_for)
    assert tool.name == "compile_screenplay"
    builder = StateGraph(FilesystemState)
    builder.add_node("tools", ToolNode([tool]))
    builder.add_edge(START, "tools")
    builder.add_edge("tools", END)
    return builder.compile(checkpointer=InMemorySaver())


def _call(graph, thread: str, n: int, files: dict | None = None, args: dict | None = None):
    """Invoke the tool once; returns (state, custom events)."""
    msg = AIMessage(content="", tool_calls=[{"name": "compile_screenplay", "args": args or {}, "id": f"call_{n}"}])
    inp: dict = {"messages": [msg]}
    if files is not None:
        inp["files"] = files
    config = {"configurable": {"thread_id": thread}}
    events = []
    for mode, chunk in graph.stream(inp, config, stream_mode=["custom", "values"]):
        if mode == "custom":
            events.append(chunk)
    state = graph.get_state(config).values
    return state, events


def _text(state, path):
    return file_data_to_string(state["files"][path])


def _last_tool_message(state) -> ToolMessage:
    msg = state["messages"][-1]
    assert isinstance(msg, ToolMessage) and msg.name == "compile_screenplay"
    return msg


def test_tool_good_draft_writes_timeline(compiler):
    graph = _graph(compiler)
    state, events = _call(graph, "good", 1, files={"/screenplay.json": create_file_data(SAMPLE_TEXT)})
    msg = _last_tool_message(state)
    assert msg.tool_call_id == "call_1"
    tl = Timeline.model_validate_json(_text(state, "/timeline.json"))
    assert msg.content == f"ok: 1 shots, {round(tl.duration, 2):g}s, {len(tl.tracks)} tracks"
    assert "defaultNarrator" in json.loads(_text(state, "/timeline.json"))  # camelCase
    assert _text(state, "/compile_errors.txt") == ""
    assert json.loads(_text(state, "/compile_log.json")) == {"rounds": 0}
    assert events == []


def test_tool_good_draft_writes_an_empty_scene_review(compiler):
    graph = _graph(compiler)
    state, _ = _call(graph, "clean", 1, files={"/screenplay.json": create_file_data(SAMPLE_TEXT)})
    assert "\n" not in _last_tool_message(state).content
    assert _text(state, "/scene_review.txt") == ""


NO_PAGE_TEXT = copy.deepcopy(SAMPLE)
del NO_PAGE_TEXT["shots"][0]["pageText"]
NO_PAGE_TEXT_WARNING = "shot s1: no pageText; add one or two short storybook sentences (at most 30 words)"


def test_tool_success_appends_scene_review_warnings_without_using_a_round(compiler):
    graph = _graph(compiler)
    state, events = _call(graph, "review", 1, files={"/screenplay.json": create_file_data(json.dumps(NO_PAGE_TEXT))})
    tl = Timeline.model_validate_json(_text(state, "/timeline.json"))
    lines = _last_tool_message(state).content.splitlines()
    assert lines == [
        f"ok: 1 shots, {round(tl.duration, 2):g}s, {len(tl.tracks)} tracks",
        "warnings (fix in /screenplay.json, then compile again; these do not use a compile round):",
        f"- {NO_PAGE_TEXT_WARNING}",
    ]
    assert _text(state, "/scene_review.txt") == NO_PAGE_TEXT_WARNING + "\n"
    assert json.loads(_text(state, "/compile_log.json")) == {"rounds": 0}
    assert _text(state, "/compile_errors.txt") == ""
    assert events == []

    # fixing the draft clears the review file
    state, events = _call(graph, "review", 2, files={"/screenplay.json": create_file_data(SAMPLE_TEXT)})
    assert _text(state, "/scene_review.txt") == ""
    assert events == []


def test_tool_scene_review_gets_the_scene_props(compiler):
    """The scene review sees the scene inputs the compiler gets: the full scene, car included."""
    car = schemas.Prop(
        id="car", name="car", size="vehicle", art={"body": None}, color="#335",
        parts={"wheel": schemas.PropPart(art="body", at=[0.2, 0.9])},
    )
    scene = SceneInputs(sets=SCENE.sets, props={**SCENE.props, "car": car}, people=SCENE.people)
    data = copy.deepcopy(SAMPLE)
    data["props"].append("car")
    data["shots"][0]["propStart"] = {"car": {"at": "curb"}}  # on stage, so its explanation has a track
    data["explanations"].append({"target": "prop:car", "text": "the car"})
    del data["interactions"]

    def same_scene(screenplay, _scene):
        assert _scene is scene
        return compiler(screenplay, _scene)

    tool = make_compile_tool(same_scene, lambda _: scene)
    builder = StateGraph(FilesystemState)
    builder.add_node("tools", ToolNode([tool]))
    builder.add_edge(START, "tools")
    builder.add_edge("tools", END)
    graph = builder.compile(checkpointer=InMemorySaver())
    state, events = _call(graph, "props", 1, files={"/screenplay.json": create_file_data(json.dumps(data))})
    assert _last_tool_message(state).content.startswith("ok: ")
    assert _text(state, "/scene_review.txt") == (
        "interactions: none, but car can move or change; "
        "add a pull_tab that drives one of them (prop_part or prop_state)\n"
    )
    assert events == []


def test_tool_scene_review_crash_does_not_fail_the_compile(compiler, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("review bug")

    monkeypatch.setattr(compile_mod, "review_scene", boom)
    graph = _graph(compiler)
    state, events = _call(graph, "boom", 1, files={"/screenplay.json": create_file_data(json.dumps(NO_PAGE_TEXT))})
    content = _last_tool_message(state).content
    assert content.startswith("ok: ") and "\n" not in content
    assert _text(state, "/scene_review.txt") == ""
    assert events == []


LATE_TAB = copy.deepcopy(NO_PAGE_TEXT)
LATE_TAB["interactions"][0]["drives"]["to"] = 50  # shot time; lands far past the end of the timeline


def _late_tab_warning(tl: Timeline) -> str:
    shot = tl.shots[0]
    return (
        f"interaction drop_books: scrub to {shot.action_start + 50:g}s is outside the timeline "
        f"(0 to {tl.duration:g}s); keep from and to between 0 and "
        f"{round(shot.end - shot.action_start, 2):g} (seconds from the start of shot s1)"
    )


def test_tool_success_appends_timeline_warnings_after_scene_warnings_without_using_a_round(compiler):
    bad = bad_message(compiler)
    graph = _graph(compiler)
    # a failed round first, so we can see the warnings neither add a round nor stop the loop
    state, _ = _call(graph, "tl", 1, files={"/screenplay.json": create_file_data(BAD_TEXT)})
    assert json.loads(_text(state, "/compile_log.json")) == {"rounds": 1}

    for n in (2, 3, 4):  # more successful compiles with warnings than MAX_COMPILE_ROUNDS
        state, events = _call(graph, "tl", n, files={"/screenplay.json": create_file_data(json.dumps(LATE_TAB))})
        tl = Timeline.model_validate_json(_text(state, "/timeline.json"))
        warnings = [NO_PAGE_TEXT_WARNING, _late_tab_warning(tl)]
        assert _last_tool_message(state).content.splitlines() == [
            f"ok: 1 shots, {round(tl.duration, 2):g}s, {len(tl.tracks)} tracks",
            "warnings (fix in /screenplay.json, then compile again; these do not use a compile round):",
            *(f"- {w}" for w in warnings),
        ]
        assert _text(state, "/scene_review.txt") == "".join(f"{w}\n" for w in warnings)
        assert json.loads(_text(state, "/compile_log.json")) == {"rounds": 0}
        assert _text(state, "/compile_errors.txt") == ""
        assert events == []

    state, events = _call(graph, "tl", 5, files={"/screenplay.json": create_file_data(BAD_TEXT)})
    assert _last_tool_message(state).content == f"{bad}\n{ROUND_1}"
    assert [e["event"] for e in events] == ["compile_error"]
    assert events[0]["data"]["round"] == 1


def test_tool_timeline_review_reads_the_compiled_timeline(compiler):
    """A compiler whose timeline leaves dad without tracks: the explanation for him warns."""

    def no_dad(screenplay, scene):
        result = compiler(screenplay, scene)
        tl = result.timeline.model_copy(
            update={"tracks": [t for t in result.timeline.tracks if t.target != "puppet:dad"]}
        )
        return result.model_copy(update={"timeline": tl})

    warning = (
        'explanations: target "puppet:dad" has no track in the timeline, so there is nothing to click; '
        "bring dad on stage in a shot (start or enter), or remove the explanation"
    )
    graph = _graph(no_dad)
    state, events = _call(graph, "nodad", 1, files={"/screenplay.json": create_file_data(SAMPLE_TEXT)})
    lines = _last_tool_message(state).content.splitlines()
    assert lines[0].startswith("ok: ")
    assert lines[1:] == [
        "warnings (fix in /screenplay.json, then compile again; these do not use a compile round):",
        f"- {warning}",
    ]
    assert _text(state, "/scene_review.txt") == warning + "\n"
    assert json.loads(_text(state, "/compile_log.json")) == {"rounds": 0}
    assert events == []


def test_tool_timeline_review_crash_keeps_the_scene_warnings(compiler, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("timeline review bug")

    monkeypatch.setattr(compile_mod, "review_timeline", boom)
    graph = _graph(compiler)
    state, events = _call(graph, "tlboom", 1, files={"/screenplay.json": create_file_data(json.dumps(LATE_TAB))})
    lines = _last_tool_message(state).content.splitlines()
    assert lines[0].startswith("ok: ")
    assert lines[2:] == [f"- {NO_PAGE_TEXT_WARNING}"]
    assert _text(state, "/scene_review.txt") == NO_PAGE_TEXT_WARNING + "\n"
    assert events == []


def test_tool_failure_output_is_unchanged_and_writes_no_scene_review(compiler):
    bad = bad_message(compiler)
    graph = _graph(compiler)
    state, _ = _call(graph, "fail", 1, files={"/screenplay.json": create_file_data(BAD_TEXT)})
    assert _last_tool_message(state).content == f"{bad}\n{ROUND_1}"
    assert "/scene_review.txt" not in state["files"]
    state, _ = _call(graph, "fail", 2)
    state, _ = _call(graph, "fail", 3)
    assert _last_tool_message(state).content == f"{bad}\nround 3 of 3\n{STOP}"


def test_tool_reads_a_custom_path(compiler):
    graph = _graph(compiler)
    state, _ = _call(graph, "path", 1, files={"/drafts/v2.json": create_file_data(SAMPLE_TEXT)}, args={"path": "/drafts/v2.json"})
    assert _last_tool_message(state).content.startswith("ok: ")


def test_tool_missing_draft_is_a_failed_round(compiler):
    graph = _graph(compiler)
    state, events = _call(graph, "missing", 1, files={})
    assert "no file at /screenplay.json" in _last_tool_message(state).content
    assert events[0]["data"]["round"] == 1


def test_tool_bad_draft_writes_errors_and_emits_event_then_stops_at_round_three(compiler):
    bad = bad_message(compiler)
    graph = _graph(compiler)
    state, events = _call(graph, "bad", 1, files={"/screenplay.json": create_file_data(BAD_TEXT)})
    msg = _last_tool_message(state)
    assert msg.content.splitlines()[0] == bad
    assert f"round 1 of {MAX_COMPILE_ROUNDS}" in msg.content
    assert "STOP" not in msg.content
    assert _text(state, "/compile_errors.txt") == bad + "\n"
    assert "/timeline.json" not in state["files"]
    assert json.loads(_text(state, "/compile_log.json")) == {"rounds": 1}
    (event,) = events
    assert event["event"] == "compile_error"
    payload = schemas.CompileErrorEvent.model_validate(event["data"])
    assert payload.round == 1
    assert [e.message for e in payload.errors] == [bad]
    assert payload.errors[0].code == "not_holding"

    state, events = _call(graph, "bad", 2)
    assert "round 2 of 3" in _last_tool_message(state).content
    assert events[0]["data"]["round"] == 2

    state, events = _call(graph, "bad", 3)
    content = _last_tool_message(state).content
    assert content.splitlines()[0] == bad
    assert "round 3 of 3" in content
    assert "STOP" in content and "report these errors" in content
    assert events[0]["data"]["round"] == 3


def test_tool_success_resets_the_round_counter(compiler):
    graph = _graph(compiler)
    _call(graph, "fix", 1, files={"/screenplay.json": create_file_data(BAD_TEXT)})
    _call(graph, "fix", 2)
    state, _ = _call(graph, "fix", 3, files={"/screenplay.json": create_file_data(SAMPLE_TEXT)})
    assert _last_tool_message(state).content.startswith("ok: ")
    assert json.loads(_text(state, "/compile_log.json")) == {"rounds": 0}
    assert _text(state, "/compile_errors.txt") == ""
    state, events = _call(graph, "fix", 4, files={"/screenplay.json": create_file_data(BAD_TEXT)})
    assert events[0]["data"]["round"] == 1


def test_tool_reports_a_compiler_crash_as_an_error():
    def boom(screenplay, scene):
        raise RuntimeError("kaboom")

    graph = _graph(boom)
    state, events = _call(graph, "crash", 1, files={"/screenplay.json": create_file_data(SAMPLE_TEXT)})
    assert "compiler crashed: RuntimeError: kaboom" in _last_tool_message(state).content
    assert events[0]["data"]["errors"][0]["code"] == "compiler_crash"


def test_emit_is_a_silent_no_op_outside_a_graph():
    compile_mod._emit("compile_error", schemas.CompileErrorEvent(errors=[], round=1))
