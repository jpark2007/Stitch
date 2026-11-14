"""compile_screenplay: wraps screenplay/compiler.py for the director's write-compile-fix loop.

The director writes a draft screenplay to `/screenplay.json` in its scratch filesystem and calls
`compile_screenplay`. On success the tool writes `/timeline.json`; on failure it writes
`/compile_errors.txt` (one exact `CompileError.message` per line), emits a `compile_error` run
event, and returns the same lines so the director can fix the draft and try again. After
`MAX_COMPILE_ROUNDS` consecutive failures the tool tells the director to stop and report, and
the cap is enforced here, not only in the prompts: once the count has reached
`MAX_COMPILE_ROUNDS`, a further call neither reads nor compiles the draft, emits no event and
changes no file; it repeats the STOP text and the last errors. So an emitted `compile_error`
never has a round above `MAX_COMPILE_ROUNDS`. A successful compile resets the count.

Every failed compile also blanks `/timeline.json`, so a timeline from an earlier successful
compile can never be voiced, rendered or saved after the draft stopped compiling, and blanks an
existing `/scene_review.txt`, whose warnings were about that earlier draft.

After a successful compile the tool also runs `scene_review.review_scene` (pageText,
explanations, pull tabs, narration sources), `scene_review.review_cue_drift` (narration cues
that move their action far from where it was written) and `scene_review.review_timeline` (scrub ranges,
explanation targets, pageText windows and narration cues against the compiled timeline), and
appends their warnings under the `ok:` summary, writing them to `/scene_review.txt`. Warnings
never fail a compile and never use a round.

The real compiler is called through `_RealCompilerAdapter`, which compiles
`cue_camera.carry_camera_cues(screenplay)[0]`: camera cues move with the actions the narration's
cue words move. The draft file keeps the written times. Each cue moved more than 0.05 s gets a
line `note: shot <id> camera cue <n> (<move>) moves with its action from t=<a> to t=<b>` right
under the `ok:` summary (before any warnings); notes need no fix.

The compiler seam
-----------------
The real compiler (`api/screenplay/compiler.py`, built by the compiler stream) is expected to
expose, and this module calls it as::

    def compile(
        screenplay: Screenplay,
        *,
        sets: dict[str, SetDef],      # set id -> SetDef (api/schemas.py)
        props: dict[str, Prop],       # prop id -> Prop
        people: dict[str, Person],    # person id -> Person
    ) -> CompileResult: ...           # api/screenplay/schema.py; never raises for bad screenplays

This signature is a contract request to the compiler stream. Until `screenplay.compiler.compile`
exists, `load_compiler()` falls back to `agent.fake_compiler.FakeCompiler`, a deterministic
validator good enough to drive the agent pipeline in tests.

Shared conventions: the model never passes a family id (scene inputs come from `scene_for`,
which the studio binds to the run's family); draft files live in deepagents state
(`state["files"]`); events go through `langgraph.config.get_stream_writer()`.
"""

from __future__ import annotations

import importlib
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Annotated, Any, Protocol

from deepagents.backends.utils import create_file_data, file_data_to_string
from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool, InjectedToolCallId, tool
from langgraph.prebuilt import InjectedState
from langgraph.types import Command
from pydantic import BaseModel, ValidationError

import schemas
from agent.tools.cue_camera import carry_camera_cues
from agent.tools.scene_review import review_cue_drift, review_scene, review_timeline
from screenplay.schema import CompileError, CompileResult, Screenplay

log = logging.getLogger(__name__)

MAX_COMPILE_ROUNDS = 3

SCREENPLAY_PATH = "/screenplay.json"
TIMELINE_PATH = "/timeline.json"
ERRORS_PATH = "/compile_errors.txt"
LOG_PATH = "/compile_log.json"
REVIEW_PATH = "/scene_review.txt"

WARNINGS_HEADER = (
    f"warnings (fix in {SCREENPLAY_PATH}, then compile again; these do not use a compile round):"
)


@dataclass
class SceneInputs:
    """Everything the compiler needs besides the screenplay, keyed by id."""

    sets: dict[str, schemas.SetDef] = field(default_factory=dict)
    props: dict[str, schemas.Prop] = field(default_factory=dict)
    people: dict[str, schemas.Person] = field(default_factory=dict)


class Compiler(Protocol):
    def __call__(self, screenplay: Screenplay, scene: SceneInputs) -> CompileResult: ...


# ------------------------------------------------------------------------ loading


class _RealCompilerAdapter:
    """Calls `screenplay.compiler.compile(carried, sets=..., props=..., people=...)`, where
    `carried` is `cue_camera.carry_camera_cues(screenplay)[0]`: camera cues follow the actions
    the narration's cue words move (the compiler itself leaves them at their written times)."""

    def __init__(self, fn: Callable[..., CompileResult]) -> None:
        self._fn = fn

    def __call__(self, screenplay: Screenplay, scene: SceneInputs) -> CompileResult:
        # The real compiler moves cued actions onto their words but not the camera cues; the
        # carried copy moves them too. The caller's screenplay (and /screenplay.json) keeps the
        # written times, so a later recompile maps from them again and never shifts twice.
        carried, _notes = carry_camera_cues(screenplay)
        result = self._fn(carried, sets=scene.sets, props=scene.props, people=scene.people)
        if not isinstance(result, CompileResult):
            # Accept a dict or another model with the same shape; keep the contract type.
            data = result.model_dump(mode="json") if isinstance(result, BaseModel) else result
            result = CompileResult.model_validate(data)
        return result

    def __repr__(self) -> str:
        return f"_RealCompilerAdapter({getattr(self._fn, '__module__', '?')}.{getattr(self._fn, '__name__', '?')})"


def load_compiler() -> Compiler:
    """The real compiler when `screenplay.compiler.compile` exists, else the fake."""
    try:
        module = importlib.import_module("screenplay.compiler")
    except ImportError as exc:
        log.info("compile_screenplay: screenplay.compiler not importable (%s); using FakeCompiler", exc)
        module = None
    fn = getattr(module, "compile", None) if module is not None else None
    if callable(fn):
        log.info("compile_screenplay: using screenplay.compiler.compile")
        return _RealCompilerAdapter(fn)

    from agent.fake_compiler import FakeCompiler

    log.info("compile_screenplay: screenplay.compiler has no compile(); using FakeCompiler")
    return FakeCompiler()


# ------------------------------------------------------------------- parse + compile

_KEY_ERROR_CODES = {"set": "unknown_set", "prop": "unknown_prop", "person": "unknown_person"}


class SceneLookupError(KeyError):
    """Raised by a `scene_for` to say exactly which kind of id was missing.

    `SceneLookupError("prop", "hymn_books")`. A plain KeyError whose first argument starts with
    "set", "prop" or "person" (e.g. `KeyError("prop:hymn_books")`) is understood too.
    """

    def __init__(self, kind: str, id: str) -> None:
        super().__init__(f"{kind}:{id}")
        self.kind = kind
        self.id = id


def _key_error_to_compile_error(exc: KeyError, screenplay: Screenplay) -> CompileError:
    """Map a scene lookup KeyError to unknown_set / unknown_prop / unknown_person.

    Understands SceneLookupError, KeyError("<kind>:<id>"), and a bare KeyError("<id>") whose id is
    a set, prop or cast member named in the screenplay.
    """
    if isinstance(exc, SceneLookupError):
        kind, ident = exc.kind, exc.id
    else:
        raw = str(exc.args[0]) if exc.args else ""
        kind, _, ident = raw.partition(":")
        if kind not in _KEY_ERROR_CODES or not ident:
            ident = raw
            if raw in {shot.set for shot in screenplay.shots}:
                kind = "set"
            elif raw in screenplay.props:
                kind = "prop"
            elif raw in screenplay.cast:
                kind = "person"
            else:
                kind = ""
    code = _KEY_ERROR_CODES.get(kind)
    if code is None:
        return CompileError(code="unknown_reference", message=f"unknown reference {ident!r}")
    return CompileError(code=code, message=f'unknown {kind} "{ident}"')


def _validation_errors(exc: ValidationError) -> list[CompileError]:
    out = []
    for err in exc.errors():
        loc = ".".join(str(part) for part in err.get("loc", ()))
        out.append(CompileError(code="invalid_screenplay", message=f"{loc}: {err['msg']}" if loc else err["msg"]))
    return out


@dataclass
class _Compiled:
    """parse_and_compile's result plus what it parsed and gathered on the way (None if it stopped early)."""

    result: CompileResult
    screenplay: Screenplay | None = None
    scene: SceneInputs | None = None


def _parse_and_compile(
    text: str,
    compiler: Compiler,
    scene_for: Callable[[Screenplay], SceneInputs],
) -> _Compiled:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        return _Compiled(
            CompileResult(
                ok=False,
                errors=[
                    CompileError(
                        code="invalid_json",
                        message=f"invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}",
                    )
                ],
            )
        )
    try:
        screenplay = Screenplay.model_validate(data)
    except ValidationError as exc:
        return _Compiled(CompileResult(ok=False, errors=_validation_errors(exc)))
    try:
        scene = scene_for(screenplay)
    except KeyError as exc:
        return _Compiled(CompileResult(ok=False, errors=[_key_error_to_compile_error(exc, screenplay)]), screenplay)
    return _Compiled(compiler(screenplay, scene), screenplay, scene)


def parse_and_compile(
    text: str,
    compiler: Compiler,
    scene_for: Callable[[Screenplay], SceneInputs],
) -> CompileResult:
    """Parse draft JSON, validate it as a Screenplay, gather scene inputs, compile.

    Never raises for a bad draft: every problem comes back as CompileError entries.
    """
    return _parse_and_compile(text, compiler, scene_for).result


def _review(compiled: _Compiled) -> list[str]:
    """Scene and timeline review warnings for a successful compile, scene first, duplicates dropped.

    Each review runs on its own: a bug in one never hides the other's warnings and never breaks
    the tool.
    """
    if compiled.screenplay is None:
        return []
    screenplay = compiled.screenplay
    props = compiled.scene.props if compiled.scene is not None else None
    timeline = compiled.result.timeline
    warnings: list[str] = []
    try:
        warnings += review_scene(screenplay, props=props)
    except Exception:  # noqa: BLE001 - warnings are advice; a crash here must not fail a good compile
        log.exception("scene review crashed")
    try:
        warnings += review_cue_drift(screenplay)
    except Exception:  # noqa: BLE001 - same: advice only
        log.exception("cue drift review crashed")
    if timeline is not None:
        try:
            warnings += review_timeline(screenplay, timeline, props=props)
        except Exception:  # noqa: BLE001 - same: advice only
            log.exception("timeline review crashed")
    return list(dict.fromkeys(warnings))


# ----------------------------------------------------------------------------- events


def _emit(name: str, model: BaseModel) -> None:
    """Send a run event on the custom stream; silent no-op outside a graph."""
    try:
        from langgraph.config import get_stream_writer

        writer = get_stream_writer()
    except Exception:  # noqa: BLE001 - outside a runnable context
        return
    try:
        writer({"event": name, "data": model.model_dump(mode="json", by_alias=True)})
    except Exception:  # noqa: BLE001 - never let event delivery break a tool
        log.debug("could not emit %s", name, exc_info=True)


# ------------------------------------------------------------------------------- tool


def _read_file(files: dict[str, Any], path: str) -> str | None:
    data = files.get(path)
    if data is None:
        return None
    return file_data_to_string(data)


def _rounds(files: dict[str, Any]) -> int:
    raw = _read_file(files, LOG_PATH)
    if not raw:
        return 0
    try:
        return int(json.loads(raw).get("rounds", 0))
    except (ValueError, AttributeError, TypeError):
        return 0


def _summary(result: CompileResult) -> str:
    tl = result.timeline
    assert tl is not None
    return f"ok: {len(tl.shots)} shots, {round(tl.duration, 2):g}s, {len(tl.tracks)} tracks"


def _camera_notes(compiler: Compiler, compiled: _Compiled | None) -> list[str]:
    """The `note:` lines for camera cues the real compiler adapter carried with their actions
    (none for any other compiler: FakeCompiler does not retime anything)."""
    if not isinstance(compiler, _RealCompilerAdapter) or compiled is None or compiled.screenplay is None:
        return []
    try:
        return carry_camera_cues(compiled.screenplay)[1]
    except Exception:  # noqa: BLE001 - notes are information; a crash here must not fail a good compile
        log.exception("camera cue notes crashed")
        return []


def _success_text(result: CompileResult, warnings: list[str], notes: list[str] | None = None) -> str:
    text = _summary(result)
    for note in notes or []:
        text += "\n" + note
    if warnings:
        text += "\n" + WARNINGS_HEADER + "".join(f"\n- {w}" for w in warnings)
    return text


STOP_TEXT = (
    f"STOP: {MAX_COMPILE_ROUNDS} compile rounds have failed. Do not edit or recompile again; "
    "report these errors as your final answer."
)


def _failure_text(errors: list[CompileError], rounds: int) -> str:
    lines = [e.message for e in errors] or ["compile failed with no error detail"]
    text = "\n".join(lines) + f"\nround {rounds} of {MAX_COMPILE_ROUNDS}"
    if rounds >= MAX_COMPILE_ROUNDS:
        text += "\n" + STOP_TEXT
    else:
        text += f"\nFix {SCREENPLAY_PATH} and call compile_screenplay again."
    return text


def _capped_text(files: dict[str, Any]) -> str:
    """What a call past the cap returns: the STOP text again, then the last saved errors."""
    last = (_read_file(files, ERRORS_PATH) or "").strip()
    return STOP_TEXT + "\n" + (last or "compile failed with no error detail")


def make_compile_tool(
    compiler: Compiler,
    scene_for: Callable[[Screenplay], SceneInputs],
) -> BaseTool:
    """Build the `compile_screenplay` tool bound to one compiler and one family's scene lookup."""

    @tool("compile_screenplay")
    def compile_screenplay(
        state: Annotated[dict, InjectedState],
        tool_call_id: Annotated[str, InjectedToolCallId],
        path: str = SCREENPLAY_PATH,
    ) -> Command:
        """Compile the draft screenplay in your files into a timeline.

        Reads the screenplay JSON at `path` (default /screenplay.json). On success writes
        /timeline.json and returns "ok: <shots> shots, <duration>s, <tracks> tracks", followed by
        any scene review warnings (missing pageText, explanations, pull tabs, narration sources,
        and timing problems in the compiled timeline; also in /scene_review.txt). Fix those in
        the draft and compile again; they do not use a round. A `note:` line says a camera cue
        moved with the action its narration cue moved; it needs no fix. On failure writes
        /compile_errors.txt and returns the exact errors, one per line,
        plus the round number; fix the draft and call again. After 3 failed rounds it tells you
        to stop.
        """
        files: dict[str, Any] = state.get("files") or {}
        rounds = _rounds(files)
        if rounds >= MAX_COMPILE_ROUNDS:
            # The cap is spent: no read, no compile, no event, no file change. Only a successful
            # compile resets the count, and none can happen from here, so the run must report.
            return Command(
                update={
                    "messages": [
                        ToolMessage(content=_capped_text(files), tool_call_id=tool_call_id, name="compile_screenplay")
                    ]
                }
            )
        text = _read_file(files, path)
        compiled: _Compiled | None = None
        if text is None:
            result = CompileResult(
                ok=False,
                errors=[CompileError(code="missing_file", message=f"no file at {path}; write the screenplay there first")],
            )
        else:
            try:
                compiled = _parse_and_compile(text, compiler, scene_for)
                result = compiled.result
            except Exception as exc:  # noqa: BLE001 - a compiler crash is still feedback, not a run failure
                log.exception("compiler crashed")
                result = CompileResult(
                    ok=False,
                    errors=[CompileError(code="compiler_crash", message=f"compiler crashed: {type(exc).__name__}: {exc}")],
                )

        if result.ok and result.timeline is not None:
            warnings = _review(compiled) if compiled is not None else []
            notes = _camera_notes(compiler, compiled)
            updates = {
                TIMELINE_PATH: create_file_data(result.timeline.model_dump_json(by_alias=True, indent=1)),
                ERRORS_PATH: create_file_data(""),
                LOG_PATH: create_file_data(json.dumps({"rounds": 0})),
                REVIEW_PATH: create_file_data("".join(f"{w}\n" for w in warnings)),
            }
            content = _success_text(result, warnings, notes)
        else:
            if result.ok:  # ok without a timeline is a broken compiler; treat as failure
                result = CompileResult(
                    ok=False, errors=[CompileError(code="no_timeline", message="compiler returned ok without a timeline")]
                )
            rounds += 1
            errors = result.errors
            updates = {
                ERRORS_PATH: create_file_data("\n".join(e.message for e in errors) + "\n"),
                LOG_PATH: create_file_data(json.dumps({"rounds": rounds})),
                # Blank the timeline so one from an earlier successful compile cannot be voiced,
                # rendered or saved. An empty file, not a None deletion: the director compiles
                # inside a sub-agent, and deepagents hands a sub-agent's result back to the
                # studio as its whole merged `files` dict (subagents.py,
                # _return_command_with_state_update). A key the sub-agent deleted is simply
                # absent from that dict, and the studio's _file_data_reducer keeps absent keys,
                # so the studio would still hold the old timeline. An empty file is a value, so
                # it travels back; synthesize_narration, render_frames and save_memory all
                # reject it as invalid JSON / not a valid timeline.
                TIMELINE_PATH: create_file_data(""),
            }
            if REVIEW_PATH in files:
                # The review of the last draft that compiled would outlive it: blank it too.
                updates[REVIEW_PATH] = create_file_data("")
            _emit("compile_error", schemas.CompileErrorEvent(errors=errors, round=rounds))
            content = _failure_text(errors, rounds)

        return Command(
            update={
                "files": updates,
                "messages": [ToolMessage(content=content, tool_call_id=tool_call_id, name="compile_screenplay")],
            }
        )

    return compile_screenplay
