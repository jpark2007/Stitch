"""compile_screenplay: wraps screenplay/compiler.py for the director's write-compile-fix loop.

The director writes a draft screenplay to `/screenplay.json` in its scratch filesystem and calls
`compile_screenplay`. On success the tool writes `/timeline.json`; on failure it writes
`/compile_errors.txt` (one exact `CompileError.message` per line), emits a `compile_error` run
event, and returns the same lines so the director can fix the draft and try again. After
`MAX_COMPILE_ROUNDS` consecutive failures the tool tells the director to stop and report.

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
from screenplay.schema import CompileError, CompileResult, Screenplay

log = logging.getLogger(__name__)

MAX_COMPILE_ROUNDS = 3

SCREENPLAY_PATH = "/screenplay.json"
TIMELINE_PATH = "/timeline.json"
ERRORS_PATH = "/compile_errors.txt"
LOG_PATH = "/compile_log.json"


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
    """Calls `screenplay.compiler.compile(screenplay, sets=..., props=..., people=...)`."""

    def __init__(self, fn: Callable[..., CompileResult]) -> None:
        self._fn = fn

    def __call__(self, screenplay: Screenplay, scene: SceneInputs) -> CompileResult:
        result = self._fn(screenplay, sets=scene.sets, props=scene.props, people=scene.people)
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


def parse_and_compile(
    text: str,
    compiler: Compiler,
    scene_for: Callable[[Screenplay], SceneInputs],
) -> CompileResult:
    """Parse draft JSON, validate it as a Screenplay, gather scene inputs, compile.

    Never raises for a bad draft: every problem comes back as CompileError entries.
    """
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        return CompileResult(
            ok=False,
            errors=[
                CompileError(
                    code="invalid_json",
                    message=f"invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}",
                )
            ],
        )
    try:
        screenplay = Screenplay.model_validate(data)
    except ValidationError as exc:
        return CompileResult(ok=False, errors=_validation_errors(exc))
    try:
        scene = scene_for(screenplay)
    except KeyError as exc:
        return CompileResult(ok=False, errors=[_key_error_to_compile_error(exc, screenplay)])
    return compiler(screenplay, scene)


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


def _failure_text(errors: list[CompileError], rounds: int) -> str:
    lines = [e.message for e in errors] or ["compile failed with no error detail"]
    text = "\n".join(lines) + f"\nround {rounds} of {MAX_COMPILE_ROUNDS}"
    if rounds >= MAX_COMPILE_ROUNDS:
        text += (
            f"\nSTOP: {MAX_COMPILE_ROUNDS} compile rounds have failed. Do not edit or recompile again; "
            "report these errors as your final answer."
        )
    else:
        text += f"\nFix {SCREENPLAY_PATH} and call compile_screenplay again."
    return text


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
        /timeline.json and returns "ok: <shots> shots, <duration>s, <tracks> tracks". On failure
        writes /compile_errors.txt and returns the exact errors, one per line, plus the round
        number; fix the draft and call again. After 3 failed rounds it tells you to stop.
        """
        files: dict[str, Any] = state.get("files") or {}
        rounds = _rounds(files)
        text = _read_file(files, path)
        if text is None:
            result = CompileResult(
                ok=False,
                errors=[CompileError(code="missing_file", message=f"no file at {path}; write the screenplay there first")],
            )
        else:
            try:
                result = parse_and_compile(text, compiler, scene_for)
            except Exception as exc:  # noqa: BLE001 - a compiler crash is still feedback, not a run failure
                log.exception("compiler crashed")
                result = CompileResult(
                    ok=False,
                    errors=[CompileError(code="compiler_crash", message=f"compiler crashed: {type(exc).__name__}: {exc}")],
                )

        if result.ok and result.timeline is not None:
            updates = {
                TIMELINE_PATH: create_file_data(result.timeline.model_dump_json(by_alias=True, indent=1)),
                ERRORS_PATH: create_file_data(""),
                LOG_PATH: create_file_data(json.dumps({"rounds": 0})),
            }
            content = _summary(result)
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
            }
            _emit("compile_error", schemas.CompileErrorEvent(errors=errors, round=rounds))
            content = _failure_text(errors, rounds)

        return Command(
            update={
                "files": updates,
                "messages": [ToolMessage(content=content, tool_call_id=tool_call_id, name="compile_screenplay")],
            }
        )

    return compile_screenplay
