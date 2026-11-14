"""Camera cues follow their cued actions on the real compiler (agent.tools.cue_camera).

screenplay.compiler moves each action the default telling cues onto its cue word and leaves
`Shot.camera` at the written times; `_RealCompilerAdapter` compiles `carry_camera_cues(sp)[0]`
so the camera moves with the action. Here:

- unit cases of carry_camera_cues (no screenplay is compiled);
- the hymn_books sample still compiles to exactly what screenplay.compiler gives it (its
  worked example has no drift);
- a drifting hymn_books draft through compile_screenplay, the FakeTTS voice pass and a
  recompile with other word timings: /screenplay.json keeps the written camera times and the
  camera is never shifted twice;
- every story of the corpus (story_screenplays.STORY_BUILDERS) end to end on the real
  compiler after the FakeTTS voice pass: each camera cue written at an action's t is keyed
  within CUE_EPS of that action's compiled start;
- a guard that fails if screenplay.compiler starts retiming camera cues itself.

Offline: no keys, no network.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from deepagents.backends.utils import create_file_data, file_data_to_string
from deepagents.middleware.filesystem import FilesystemState
from langchain_core.messages import AIMessage
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode
from story_screenplays import STORY_BUILDERS
from test_agent_stories import FAMILY, STORIES, Studio

import schemas
from agent import wiring
from agent.fake_compiler import FakeCompiler
from agent.tools.compile import Compiler, SceneInputs, load_compiler, make_compile_tool
from agent.tools.cue_camera import carry_camera_cues
from agent.tools.narration import FakeTTS, make_narration_tool
from screenplay import compiler as screenplay_compiler
from screenplay import narration as screenplay_narration
from screenplay.schema import Screenplay, Timeline

SAMPLES = Path(__file__).resolve().parents[3] / "web" / "public" / "samples"
CUE_EPS = 0.05
"""Seconds a carried camera cue may sit from its action's compiled start."""


def _load(name: str) -> dict:
    return json.loads((SAMPLES / name).read_text())


# ------------------------------------------------------------------------- unit cases


def _line(t: float, text: str, cue: str | None = None, words: list[float] | None = None, **kw) -> dict:
    """A narration line; `words` gives each word's t from the line start (default: 0.3 s apart)."""
    tokens = text.rstrip(".").split()
    starts = words if words is not None else [round(0.3 * i, 4) for i in range(len(tokens))]
    line = {"t": t, "text": text, "source": "telling:mom:s1",
            "words": [{"t": w, "word": tok} for w, tok in zip(starts, tokens, strict=True)]}
    if cue is not None:
        line["cue"] = cue
    return {**line, **kw}


def _sp(actions: list[dict], camera: list[dict], narration: list[dict]) -> Screenplay:
    return Screenplay.model_validate({
        "memoryId": "m1", "cast": ["mom"], "props": [],
        "shots": [{"id": "s1", "set": "garden", "actions": actions, "camera": camera, "narration": narration}],
    })


def _a(t: float, do: str = "walk_to", id: str | None = None, **kw) -> dict:
    if "frm" in kw:
        kw["from"] = kw.pop("frm")
    out = {"t": t, "do": do, "who": "mom", "to": "row", **kw}
    if id is not None:
        out["id"] = id
    return out


def _cams(sp: Screenplay) -> list[float]:
    return [c.t for c in sp.shots[0].camera]


def test_no_cue_leaves_the_camera_alone() -> None:
    sp = _sp([_a(0, "enter", frm="offstage_left"), _a(2.0, id="go")],
             [{"t": 0, "move": "wide"}, {"t": 2.0, "move": "pan_to", "target": "mom"}],
             [_line(0.5, "Every evening I walked the row.")])
    out, notes = carry_camera_cues(sp)
    assert notes == []
    assert out == sp and out is not sp  # a copy, even when nothing moved


def test_a_cue_later_than_written_carries_the_camera_later() -> None:
    # "row" (the last word) is at 2.5 + 1.2 = 3.7: the walk moves from 2.0 to 3.7.
    sp = _sp([_a(0, "enter", frm="offstage_left"), _a(2.0, id="go"), _a(4.0, "pose", pose="wave")],
             [{"t": 0, "move": "wide"}, {"t": 2.0, "move": "pan_to", "target": "mom"},
              {"t": 2.5, "move": "close_up", "target": "mom"}, {"t": 4.5, "move": "wide"}],
             [_line(2.5, "I walked the long row.", cue="go")])
    out, notes = carry_camera_cues(sp)
    assert _cams(out) == [0, 3.7, 4.2, 6.2]  # each keeps its offset from the action before it
    assert notes == [
        "note: shot s1 camera cue 2 (pan_to) moves with its action from t=2 to t=3.7",
        "note: shot s1 camera cue 3 (close_up) moves with its action from t=2.5 to t=4.2",
        "note: shot s1 camera cue 4 (wide) moves with its action from t=4.5 to t=6.2",
    ]


def test_a_cue_earlier_than_written_carries_the_camera_earlier() -> None:
    # "row" at 0.5 + 0.6 = 1.1: the walk moves from 3.0 to 1.1.
    sp = _sp([_a(0, "enter", frm="offstage_left"), _a(3.0, id="go")],
             [{"t": 0, "move": "wide"}, {"t": 3.0, "move": "pan_to", "target": "mom", "dur": 2}],
             [_line(0.5, "Down the row.", cue="go")])
    out, notes = carry_camera_cues(sp)
    assert _cams(out) == [0, 1.1]
    assert out.shots[0].camera[1].dur == 2  # only t moves
    assert notes == ["note: shot s1 camera cue 2 (pan_to) moves with its action from t=3 to t=1.1"]


def test_a_camera_cue_between_two_cued_actions_follows_the_first() -> None:
    # The walk (1.0) moves to 1.6 and the wave (4.0) to 4.3; a cue at 2.5 keeps 1.5 s after the walk.
    sp = _sp([_a(1.0, id="go"), _a(4.0, "pose", id="wave", pose="wave")],
             [{"t": 2.5, "move": "pan_to", "target": "mom"}],
             [_line(1.0, "I walked over.", cue="go"), _line(3.4, "And waved at her.", cue="wave", words=[0, 0.3, 0.6, 0.9])])
    out, notes = carry_camera_cues(sp)
    retimed = screenplay_narration.retime_actions(sp.shots[0], 0.0)
    assert retimed == [1.6, 4.3]
    assert _cams(out) == [3.1]
    assert notes == ["note: shot s1 camera cue 1 (pan_to) moves with its action from t=2.5 to t=3.1"]


def test_the_opening_framing_stays_at_t0() -> None:
    # The action written at t=0 is cued to 0.6; the opening cues still frame the shot's start.
    sp = _sp([_a(0, "enter", id="in", frm="offstage_left")],
             [{"t": 0, "move": "wide"}, {"t": 0, "move": "close_up", "target": "mom"},
              {"t": 0.5, "move": "pan_to", "target": "mom"}],
             [_line(0, "She came in.", cue="in")])
    out, notes = carry_camera_cues(sp)
    assert _cams(out) == [0, 0, 1.1]
    assert notes == ["note: shot s1 camera cue 3 (pan_to) moves with its action from t=0.5 to t=1.1"]


def test_a_small_move_is_carried_without_a_note() -> None:
    sp = _sp([_a(0, "enter", frm="offstage_left"), _a(2.0, id="go")],
             [{"t": 2.0, "move": "pan_to", "target": "mom"}],
             [_line(1.0, "Down the row.", cue="go", words=[0, 0.5, 1.04])])
    out, notes = carry_camera_cues(sp)
    assert _cams(out) == [2.04] and notes == []


def test_the_input_is_never_mutated() -> None:
    sp = _sp([_a(0, "enter", frm="offstage_left"), _a(2.0, id="go")],
             [{"t": 0, "move": "wide"}, {"t": 2.0, "move": "pan_to", "target": "mom"}],
             [_line(2.5, "I walked the long row.", cue="go")])
    before = sp.model_dump(mode="json", by_alias=True)
    out, notes = carry_camera_cues(sp)
    assert notes and out.shots[0].camera[1].t == 3.7
    assert sp.model_dump(mode="json", by_alias=True) == before
    assert out.shots[0].camera is not sp.shots[0].camera


@pytest.mark.parametrize("problem", ["unknown cue", "cue before start", "nobody narrates"])
def test_a_narration_error_returns_the_input_with_no_notes(problem: str) -> None:
    line = _line(2.5, "I walked the long row.", cue="go")
    if problem == "unknown cue":
        line["cue"] = "fly"
    elif problem == "cue before start":
        line["words"][-1]["t"] = -5.0
    sp = _sp([_a(0, "enter", frm="offstage_left"), _a(2.0, id="go")],
             [{"t": 2.0, "move": "pan_to", "target": "mom"}], [line])
    if problem == "nobody narrates":
        sp = sp.model_copy(update={"cast": []})
        for ln in sp.shots[0].narration:
            ln.source = None
    out, notes = carry_camera_cues(sp)
    assert out is sp and notes == []


# ---------------------------------------------------------------- the real compiler: hymn_books


SAMPLE = _load("hymn_books.screenplay.json")
MEMORY = _load("hymn_books.memory.json")
SCENE = SceneInputs(
    sets={s["id"]: schemas.SetDef.model_validate(s) for s in MEMORY["sets"]},
    props={p["id"]: schemas.Prop.model_validate(p) for p in MEMORY["props"]},
    people={p["id"]: schemas.Person.model_validate(p) for p in MEMORY["people"]},
)


def scene_for(_: Screenplay) -> SceneInputs:
    return SCENE


@pytest.fixture(scope="module")
def real() -> Compiler:
    compiler = load_compiler()
    if isinstance(compiler, FakeCompiler):
        pytest.skip("screenplay.compiler.compile is not importable; load_compiler() fell back to the fake")
    return compiler


def _drifting() -> dict:
    """hymn_books with dad's two cued lines 0.8 s late: the drop moves 3.25 -> 4.05 and every
    action after it 0.8 s later too, so the dolly_to written at the pick_up (5.5) belongs at 6.3."""
    draft = copy.deepcopy(SAMPLE)
    lines = draft["shots"][0]["narration"]
    lines[1]["t"] = 3.3
    lines[2]["t"] = 7.15
    return draft


DOLLY_NOTE = "note: shot s1 camera cue 2 (dolly_to) moves with its action from t=5.5 to t=6.3"


def _camera_key_times(timeline: Timeline) -> list[float]:
    (track,) = [t for t in timeline.tracks if t.target == "camera" and t.channel == "position"]
    return [t for t, _ in track.keys]


def _has_key(timeline: Timeline, at: float, eps: float = 1e-4) -> bool:
    return any(abs(t - at) <= eps for t in _camera_key_times(timeline))


def test_hymn_books_compiles_exactly_as_the_compiler_alone_compiles_it(real: Compiler) -> None:
    """The sample's worked example has no drift: the carry changes nothing about it."""
    sp = Screenplay.model_validate(SAMPLE)
    carried, notes = carry_camera_cues(sp)
    assert notes == [] and carried == sp
    direct = screenplay_compiler.compile(sp, sets=SCENE.sets, props=SCENE.props, people=SCENE.people)
    through = real(sp, SCENE)
    assert through.ok and direct.ok
    assert through.model_dump(mode="json", by_alias=True) == direct.model_dump(mode="json", by_alias=True)


def test_the_compiler_itself_leaves_camera_cues_at_their_written_times() -> None:
    """GUARD. screenplay.compiler retimes cued actions but not camera cues; agent.tools.cue_camera
    makes up for it. If this fails the compiler now carries camera cues itself, and the agent
    would shift every moved cue twice."""
    sp = Screenplay.model_validate(_drifting())
    result = screenplay_compiler.compile(sp, sets=SCENE.sets, props=SCENE.props, people=SCENE.people)
    assert result.ok and result.timeline is not None, [e.message for e in result.errors]
    start = result.timeline.shots[0].action_start
    assert _has_key(result.timeline, start + 5.5) and not _has_key(result.timeline, start + 6.3), (
        "screenplay/compiler.py now retimes camera cues itself: delete the agent-side carry "
        "(agent/tools/cue_camera.py and its call in agent/tools/compile.py _RealCompilerAdapter), "
        "or every camera cue a narration cue moves is shifted twice"
    )


def test_the_adapter_compiles_the_carried_screenplay_and_never_touches_the_draft(real: Compiler) -> None:
    sp = Screenplay.model_validate(_drifting())
    before = sp.model_dump(mode="json", by_alias=True)
    result = real(sp, SCENE)
    assert result.ok and result.timeline is not None, [e.message for e in result.errors]
    start = result.timeline.shots[0].action_start
    assert _has_key(result.timeline, start + 6.3) and not _has_key(result.timeline, start + 5.5)
    assert sp.model_dump(mode="json", by_alias=True) == before
    assert carry_camera_cues(sp)[1] == [DOLLY_NOTE]


def test_fake_compiler_is_not_carried() -> None:
    """FakeCompiler retimes nothing; the compile tool gives it no notes."""
    state = _invoke(_compile_graph(FakeCompiler()), "compile_screenplay", {"/screenplay.json": create_file_data(json.dumps(_drifting()))})
    assert _tool_text(state).startswith("ok:") and "note:" not in _tool_text(state)


def _compile_graph(compiler: Compiler):
    return _graph(make_compile_tool(compiler, scene_for))


def _graph(tool):
    graph = StateGraph(FilesystemState)
    graph.add_node("tools", ToolNode([tool]))
    graph.add_edge(START, "tools")
    graph.add_edge("tools", END)
    return graph.compile()


def _invoke(graph, name: str, files: dict) -> dict:
    msg = AIMessage(content="", tool_calls=[{"name": name, "args": {}, "id": "call_1"}])
    return graph.invoke({"messages": [msg], "files": files})


def _tool_text(state: dict) -> str:
    return state["messages"][-1].content


def _file(state: dict, path: str) -> str:
    return file_data_to_string(state["files"][path])


class _Media:
    def __init__(self) -> None:
        self.n = 0

    def put(self, _data: bytes, _content_type: str) -> str:
        self.n += 1
        return f"media_{self.n}"


def _cue_start(sp: Screenplay, shot_index: int, action_start: float, action_index: int) -> float:
    """Timeline start of an action, exactly as the compiler computes it."""
    shot = sp.shots[shot_index]
    retimed = screenplay_narration.retime_actions(shot, 0.0, author=screenplay_narration.default_author(sp))
    return action_start + retimed[action_index]


def test_compile_voice_and_recompile_never_shift_the_camera_twice(real: Compiler) -> None:
    """The director's compile notes the carry; the voice pass writes /screenplay.json with the
    camera cues as written; a recompile with other word timings maps from the written times."""
    draft = _drifting()
    files = {"/screenplay.json": create_file_data(json.dumps(draft))}

    state = _invoke(_compile_graph(real), "compile_screenplay", files)
    text = _tool_text(state)
    assert text.splitlines()[0].startswith("ok:") and text.splitlines()[1] == DOLLY_NOTE, text
    assert "warnings" not in text  # 0.8 s is within review_cue_drift's limit
    assert _file(state, "/screenplay.json") == json.dumps(draft)  # the draft is not rewritten
    compiled = Timeline.model_validate_json(_file(state, "/timeline.json"))
    assert _has_key(compiled, compiled.shots[0].action_start + 6.3)

    voice = make_narration_tool(FakeTTS(), _Media().put, author_id="dad", voice=None, second_telling=False,
                                compiler=real, scene_for=scene_for)
    state = _invoke(_graph(voice), "synthesize_narration", state["files"])
    assert _tool_text(state).startswith("Narrated") and "warning" not in _tool_text(state), _tool_text(state)
    voiced_sp = Screenplay.model_validate_json(_file(state, "/screenplay.json"))
    assert [c.model_dump() for c in voiced_sp.shots[0].camera] == [
        c.model_dump() for c in Screenplay.model_validate(draft).shots[0].camera
    ]  # written times, never the carried ones
    voiced = Timeline.model_validate_json(_file(state, "/timeline.json"))
    start = voiced.shots[0].action_start
    pick_up = _cue_start(voiced_sp, 0, start, 3)
    assert _has_key(voiced, pick_up), (pick_up, _camera_key_times(voiced))

    # New word timings (a faster speaker): the camera lands on the new pick-up time, not on
    # a carry of a carry.
    faster = voiced_sp.model_copy(deep=True)
    for line in faster.shots[0].narration:
        for w in line.words or []:
            w.t = round(w.t * 0.8, 4)
    again = real(faster, SCENE)
    assert again.ok and again.timeline is not None, [e.message for e in again.errors]
    new_pick_up = _cue_start(faster, 0, again.timeline.shots[0].action_start, 3)
    assert abs(new_pick_up - pick_up) > CUE_EPS  # the words really moved it
    assert _has_key(again.timeline, new_pick_up), (new_pick_up, _camera_key_times(again.timeline))
    double = carry_camera_cues(carry_camera_cues(faster)[0])[0]
    twice = again.timeline.shots[0].action_start + double.shots[0].camera[1].t
    assert abs(twice - new_pick_up) > CUE_EPS and not _has_key(again.timeline, twice)


# ------------------------------------------------------------------- the corpus, end to end


@pytest.fixture(scope="module")
def corpus(real: Compiler) -> Iterator[dict[str, tuple[Studio, schemas.Memory]]]:
    """Every corpus story run once through the fake studio on the real compiler."""
    assert {s.name for s in STORIES} == set(STORY_BUILDERS), "every builder needs a Story to run it"
    runs: dict[str, tuple[Studio, schemas.Memory]] = {}
    try:
        for story in STORIES:
            studio = Studio(story)
            runs[story.name] = (studio, studio.run())
        yield runs
    finally:
        for studio, _ in runs.values():
            studio.manager.shutdown(wait=True)


def _cued_camera(sp: Screenplay, tl: Timeline) -> Iterator[tuple[str, int, str, float, float, float]]:
    """(shot, cue number, move, written t, the action's compiled start, the cue's key time) for
    every camera cue after t=0 written at some action's t."""
    author = screenplay_narration.default_author(sp)
    windows = {s.id: s for s in tl.shots}
    for shot in sp.shots:
        retimed = screenplay_narration.retime_actions(shot, 0.0, author=author)
        carried = carry_camera_cues(sp)[0].shots[[s.id for s in sp.shots].index(shot.id)]
        start = windows[shot.id].action_start
        for n, (cue, moved) in enumerate(zip(shot.camera, carried.camera, strict=True), start=1):
            hits = [i for i, a in enumerate(shot.actions) if a.t == cue.t]
            if cue.t == 0 or not hits:
                continue
            yield shot.id, n, cue.move, cue.t, start + retimed[hits[-1]], start + moved.t


@pytest.mark.parametrize("name", list(STORY_BUILDERS))
def test_every_story_keeps_its_camera_on_the_action(name: str, corpus) -> None:
    studio, memory = corpus[name]
    sp, tl = memory.screenplay, Timeline.model_validate(memory.timeline.model_dump(mode="json", by_alias=True))
    assert sp is not None and all(line.words for shot in sp.shots for line in shot.narration)  # voiced
    cues = list(_cued_camera(sp, tl))
    assert cues, f"{name}: no camera cue is written at an action's t"
    for shot_id, n, move, written, action_at, key_at in cues:
        where = f"{name}: shot {shot_id} camera cue {n} ({move}), written at t={written}"
        assert _has_key(tl, key_at), (where, key_at, _camera_key_times(tl))
        assert abs(key_at - action_at) <= CUE_EPS, (
            f"{where}, is keyed at {key_at:.2f}, {abs(key_at - action_at):.2f} s from its action's compiled "
            f"start {action_at:.2f}"
        )


def test_the_corpus_really_drifts(corpus) -> None:
    """The check above has teeth: in the voiced corpus some camera cues moved by more than
    CUE_EPS, and left at their written time they would have missed their action."""
    moved = [
        (name, shot_id, n) for name, (_, memory) in corpus.items()
        for shot_id, n, _, written, action_at, _ in _cued_camera(memory.screenplay, memory.timeline)
        if abs(action_at - (_window(memory.timeline, shot_id) + written)) > CUE_EPS
    ]
    assert len(moved) >= 3, moved


def _window(tl: Timeline, shot_id: str) -> float:
    return next(s.action_start for s in tl.shots if s.id == shot_id)


@pytest.mark.parametrize("name", list(STORY_BUILDERS))
def test_recompiling_a_saved_story_gives_the_same_camera(name: str, corpus) -> None:
    """The saved screenplay keeps its written camera times: compiling it again (as a save or a
    second telling does) gives exactly the saved camera, never a second shift."""
    studio, memory = corpus[name]
    result = load_compiler()(memory.screenplay, wiring.scene_for(studio.repo, FAMILY)(memory.screenplay))
    assert result.ok and result.timeline is not None, [e.message for e in result.errors]
    saved = [t for t in memory.timeline.tracks if t.target == "camera"]
    again = [t for t in result.timeline.tracks if t.target == "camera"]
    assert again == saved
