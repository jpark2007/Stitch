"""get_person, find_memories, save_memory, link_participants.

Every tool here is built by `make_family_tools(repo, run)` and closes over the run's
family id: no tool schema has a family id parameter, so nothing the model writes can
reach another family. `save_scene` is the plain-Python core of save_memory, shared by
the tool and by runs.py.
"""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from typing import Annotated, Any
from urllib.parse import quote

from deepagents.backends.utils import create_file_data, file_data_to_string
from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool, InjectedToolCallId, tool
from langgraph.config import get_stream_writer
from langgraph.prebuilt import InjectedState, ToolRuntime
from langgraph.types import Command
from pydantic import BaseModel, ValidationError

import schemas
from agent.repo import AgentRepo
from agent.run_info import RunInfo
from agent.tools.compile import ERRORS_PATH
from schemas import Memory, Telling
from screenplay.narration import NarrationError, default_author
from screenplay.schema import VOCABULARY, NarrationLine, Screenplay, Timeline

SCREENPLAY_PATH = "/screenplay.json"
TIMELINE_PATH = "/timeline.json"
# Run bookkeeping kept in the run's state files (not in a closure), so it survives the
# tools being rebuilt when a run resumes. The prompts never name these files.
SAVED_MEMORY_PATH = "/saved_memory.json"
"""{"memoryId", "runId"}: the memory this new-memory run saved, so a later save updates it."""
ASKED_QUESTIONS_DIR = "/asked_questions/"
"""Who link_participants already asked: one file per (memory, person), at
`asked_question_path(memoryId, personId)`. A file per pair, not one shared record, so
parallel calls in one message (or parallel linker sub-agents) never overwrite each
other: the files reducer merges per path."""


def asked_question_path(memory_id: str, person_id: str) -> str:
    """The state file that records link_participants asked `person_id` about `memory_id`."""
    return f"{ASKED_QUESTIONS_DIR}{quote(memory_id, safe='')}/{quote(person_id, safe='')}"

TELLING_PREVIEW_CHARS = 200


def _emit(name: str, model: BaseModel) -> None:
    """Send one run event on the LangGraph custom stream. Outside a graph it does nothing."""
    assert name in schemas.RUN_EVENTS, name
    try:
        writer = get_stream_writer()
    except (RuntimeError, KeyError):
        return
    writer({"event": name, "data": model.model_dump(mode="json", by_alias=True)})


def era_for_year(year: int) -> str | None:
    """The era id from shared/vocabulary.json whose [from, to] range holds `year`."""
    for era in VOCABULARY["eras"]:
        if era["from"] <= year <= era["to"]:
            return era["id"]
    return None


def _dedup(ids: list[str]) -> list[str]:
    return list(dict.fromkeys(ids))


# -------------------------------------------------------------------- save_scene


def save_scene(
    repo: AgentRepo,
    run: RunInfo,
    *,
    title: str,
    participant_ids: list[str],
    screenplay: Screenplay,
    timeline: Timeline,
    update_memory_id: str | None = None,
) -> Memory:
    """Save the run's compiled scene as a new memory, or as a second telling of an existing one.

    The timeline must be voiced: timeline.narrations[run.author_id] with at least one line.

    New memory: a fresh id, the teller's Telling, participants that are real people in
    the family, props and sets from the screenplay, era from the year, version 1. The
    timeline's shot ids must be the screenplay's, in order. With `update_memory_id` (the
    memory this same run saved earlier) that memory is updated instead: same id, version
    up by one, scene, title, props and sets replaced, the teller's telling replaced in
    place, and participants already linked kept.

    Second telling (run.fields.memory_id set): the existing action (screenplay shots and
    timeline tracks) is kept untouched. The teller's Telling is appended, or replaces
    their own earlier telling in place; the teller's lines must be in
    screenplay.narrations[author] (or, for the default narrator only, in the draft's shot
    `narration` when the draft's default teller is that author, which is the form the
    compiler accepts). They become screenplay.narrations[author], or each shot's
    `narration` when the teller is the default narrator, and the draft's
    timeline.narrations[author] is stored; the duration grows when the new narration
    outlasts the action; version goes up by one.
    """
    memory, _ = save_scene_with_report(
        repo,
        run,
        title=title,
        participant_ids=participant_ids,
        screenplay=screenplay,
        timeline=timeline,
        update_memory_id=update_memory_id,
    )
    return memory


def _require_voiced(timeline: Timeline, author: str) -> None:
    track = (timeline.narrations or {}).get(author)
    if track is None or not track.lines:
        raise ValueError(f"the scene has no narration for {author}; run the narrator's voice pass first")


def _require_compiled_from(screenplay: Screenplay, timeline: Timeline) -> None:
    """The timeline's shots must be the screenplay's, in order: otherwise it is a stale compile."""
    sp_ids = [shot.id for shot in screenplay.shots]
    tl_ids = [shot.id for shot in timeline.shots]
    if sp_ids != tl_ids:
        raise ValueError(
            f"the timeline's shots ({', '.join(tl_ids) or 'none'}) are not the screenplay's "
            f"({', '.join(sp_ids) or 'none'}); compile {SCREENPLAY_PATH} again before saving"
        )


def _draft_default_teller(screenplay: Screenplay) -> str | None:
    """Who tells the draft's shot `narration`, by the compiler's rule; None when it cannot tell."""
    try:
        return default_author(screenplay)
    except NarrationError:
        return None


def _with_telling(tellings: list[Telling], telling: Telling) -> list[Telling]:
    """One telling per author: replace the author's own in place, or append it."""
    out = list(tellings)
    mine = next((i for i, t in enumerate(out) if t.author_id == telling.author_id), None)
    if mine is None:
        out.append(telling)
    else:
        out[mine] = telling
    return out


def save_scene_with_report(
    repo: AgentRepo,
    run: RunInfo,
    *,
    title: str,
    participant_ids: list[str],
    screenplay: Screenplay,
    timeline: Timeline,
    update_memory_id: str | None = None,
) -> tuple[Memory, list[str]]:
    """`save_scene`, also returning the draft narration shot ids that were dropped.

    Only a second telling drops anything: narration the draft wrote for a shot id that
    is not in the existing screenplay has no action to play over. Raises ValueError for
    a draft that must not be saved (unvoiced, stale, or missing the teller's lines).
    """
    family_id = run.family_id
    fields = run.fields
    _require_voiced(timeline, run.author_id)
    telling = Telling(
        author_id=run.author_id,
        question=fields.question,
        text=fields.text,
        photo_ids=list(run.photo_ids),
        created_at=datetime.now(UTC),
    )

    ignored: list[str] = []
    if run.is_second_telling:
        memory, ignored = _add_telling(repo, run, telling, screenplay=screenplay, timeline=timeline)
    else:
        _require_compiled_from(screenplay, timeline)
        candidates = _dedup([run.author_id, *participant_ids, *fields.participant_ids, *screenplay.cast])
        if update_memory_id is None:
            memory_id = repo.new_id(family_id, "memory")
            tellings = [telling]
            version = 1
        else:
            prior = repo.get_memory(family_id, update_memory_id)
            if prior is None:
                raise ValueError(f"memory {update_memory_id!r}, saved earlier in this run, was not found")
            if not any(t.author_id == run.author_id for t in prior.tellings):
                raise ValueError(f"memory {update_memory_id!r} is not {run.author_id}'s telling")
            memory_id = prior.id
            tellings = _with_telling(prior.tellings, telling)
            version = prior.version + 1
            # People the linker already added keep the moment in their book.
            candidates = _dedup([*candidates, *prior.participant_ids])
        memory = Memory(
            id=memory_id,
            family_id=family_id,
            title=(fields.title or title).strip() or "Untitled",
            year=fields.year,
            era=era_for_year(fields.year),
            participant_ids=[pid for pid in candidates if repo.get_person(family_id, pid) is not None],
            tellings=tellings,
            screenplay=screenplay.model_copy(update={"memory_id": memory_id}, deep=True),
            timeline=timeline.model_copy(deep=True),
            prop_ids=_dedup(list(screenplay.props)),
            set_ids=_dedup([shot.set for shot in screenplay.shots]),
            version=version,
        )

    # Round-trip through validation so a bad draft never reaches the database.
    memory = Memory.model_validate(memory.model_dump(mode="json", by_alias=True))
    return repo.save_memory(family_id, memory), ignored


DURATION_EPSILON = 1e-6


def _add_telling(
    repo: AgentRepo,
    run: RunInfo,
    telling: Telling,
    *,
    screenplay: Screenplay,
    timeline: Timeline,
) -> tuple[Memory, list[str]]:
    family_id = run.family_id
    author = run.author_id
    existing = repo.get_memory(family_id, run.fields.memory_id or "")
    if existing is None:
        raise ValueError(f"memory {run.fields.memory_id!r} not found in this family")
    if existing.screenplay is None or existing.timeline is None:
        raise ValueError(f"memory {existing.id!r} has no scene yet to add a telling to")

    draft_track = (timeline.narrations or {}).get(author)
    if draft_track is None:
        raise ValueError(f"the draft timeline has no narrations[{author!r}] for this telling")

    default_narrator = existing.timeline.default_narrator or (
        existing.tellings[0].author_id if existing.tellings else None
    )

    # The teller's lines, keyed by shot. They come from narrations[author]: each shot's
    # `narration` is the default telling, which may be another teller's words. The one
    # exception is the default narrator retelling: the compiler refuses a default teller
    # who also has narrations[author] (duplicate_telling), so a draft that compiles carries
    # the retold lines in each shot's `narration`. Those are read only when the draft's
    # own default teller, by the compiler's rule, is this author.
    drafted: dict[str, list[NarrationLine]]
    if screenplay.narrations and author in screenplay.narrations:
        drafted = dict(screenplay.narrations[author])
    elif author == default_narrator and _draft_default_teller(screenplay) == author:
        drafted = {shot.id: shot.narration for shot in screenplay.shots}
    else:
        raise ValueError(f"the draft has no narrations[{author!r}] for this telling")
    # Only shots the saved action has can carry them.
    shot_ids = {shot.id for shot in existing.screenplay.shots}
    lines_by_shot = {sid: lines for sid, lines in drafted.items() if sid in shot_ids}
    ignored = _dedup([sid for sid, lines in drafted.items() if sid not in shot_ids and lines])

    new_screenplay = existing.screenplay.model_copy(deep=True)
    if author == default_narrator:
        # The default narrator retold it: the new lines are the default telling, which
        # lives in each shot's `narration`, never under narrations[author].
        for shot in new_screenplay.shots:
            shot.narration = [line.model_copy(deep=True) for line in lines_by_shot.get(shot.id, [])]
        others = {k: v for k, v in (new_screenplay.narrations or {}).items() if k != author}
        new_screenplay.narrations = others or None
    else:
        new_screenplay.narrations = {**(new_screenplay.narrations or {}), author: lines_by_shot}

    new_timeline = existing.timeline.model_copy(deep=True)
    new_timeline.narrations = {**(new_timeline.narrations or {}), author: draft_track.model_copy(deep=True)}
    if author == default_narrator:
        # Top-level audio and captions repeat the default narrator's; keep them in step.
        new_timeline.audio = draft_track.audio
        new_timeline.captions = [c.model_copy() for c in draft_track.captions]

    # apply_narration grows the draft's duration when this telling outlasts the action.
    # Grow the saved one to match (never shrink it: other tellings may be longer), and
    # hold the closing shot on stage for the extra time.
    old_duration = existing.timeline.duration
    new_duration = max(old_duration, timeline.duration)
    if new_duration > old_duration:
        new_timeline.duration = new_duration
        if new_timeline.shots and abs(new_timeline.shots[-1].end - old_duration) <= DURATION_EPSILON:
            new_timeline.shots[-1].end = new_duration

    # One telling per author: a retelling replaces the author's own, in place.
    tellings = _with_telling(existing.tellings, telling)

    memory = existing.model_copy(
        update={
            "tellings": tellings,
            "participant_ids": _dedup([*existing.participant_ids, author]),
            "screenplay": new_screenplay,
            "timeline": new_timeline,
            "version": existing.version + 1,
        }
    )
    return memory, ignored


# ------------------------------------------------------------------------- tools


def _person_json(person: schemas.Person) -> str:
    return json.dumps(person.model_dump(mode="json", by_alias=True), ensure_ascii=False)


def _memory_brief(memory: Memory) -> dict[str, Any]:
    return {
        "id": memory.id,
        "title": memory.title,
        "year": memory.year,
        "participants": memory.participant_ids,
        "tellings": [
            {"authorId": t.author_id, "text": t.text[:TELLING_PREVIEW_CHARS]} for t in memory.tellings
        ],
    }


def _read_draft(state: dict[str, Any], path: str, model: type[BaseModel]) -> tuple[Any, str | None]:
    """Parse a draft file from deepagents state. Returns (model, None) or (None, error)."""
    file_data = (state.get("files") or {}).get(path)
    if file_data is None:
        return None, f"error: {path} is missing; write it before calling save_memory"
    try:
        text = file_data_to_string(file_data)
        return model.model_validate_json(text), None
    except (ValidationError, ValueError, TypeError) as exc:
        return None, f"error: {path} is not a valid {model.__name__}: {exc}"


def _file_text(files: dict[str, Any], path: str) -> str | None:
    file_data = files.get(path)
    if file_data is None:
        return None
    try:
        return file_data_to_string(file_data)
    except (ValueError, TypeError, KeyError, AttributeError):
        return None


def _state_files(state: Any) -> dict[str, Any]:
    files = state.get("files") if isinstance(state, dict) else getattr(state, "files", None)
    return files if isinstance(files, dict) else {}


def _saved_memory_id(files: dict[str, Any], run: RunInfo) -> tuple[str | None, str | None]:
    """The memory this run already saved, from SAVED_MEMORY_PATH. Returns (id or None, error)."""
    text = _file_text(files, SAVED_MEMORY_PATH)
    if text is None or not text.strip():
        return None, None
    try:
        record = json.loads(text)
        memory_id = record["memoryId"]
        run_id = record.get("runId")
    except (ValueError, TypeError, KeyError, AttributeError):
        return None, "error: could not save: this run's record of its saved memory is unreadable"
    if not isinstance(memory_id, str) or not memory_id or run_id != run.run_id:
        return None, "error: could not save: this run's record of its saved memory does not belong to this run"
    return memory_id, None


def make_family_tools(repo: AgentRepo, run: RunInfo) -> list[BaseTool]:
    """The family tools for one run, bound to run.family_id."""
    family_id = run.family_id
    # Parallel link_participants calls run on separate threads of one tool node and each
    # sees the same earlier state. The lock makes each call's read-check-ask-save atomic
    # in this process, and `asked_here` covers pairs a parallel sibling asked before its
    # files update lands in state. The state files stay the durable record (resume).
    link_lock = threading.Lock()
    asked_here: set[tuple[str, str]] = set()

    @tool(parse_docstring=True)
    def get_person(person_id: str) -> str:
        """Look up a person in this family: name, years, relatives and character sheet (color, item, build, notes on how they move).

        Args:
            person_id: The person's id, e.g. "dad".
        """
        person = repo.get_person(family_id, person_id)
        return _person_json(person) if person else f"not found: person {person_id!r}"

    @tool(parse_docstring=True)
    def find_memories(query: str | None = None, person_id: str | None = None) -> str:
        """Search this family's saved memories, oldest first. Returns id, title, year, participants and the start of each telling.

        Args:
            query: Words to look for in titles and tellings, e.g. "church steps". Omit to list all.
            person_id: Only memories this person is in.
        """
        memories = repo.find_memories(family_id, person_id=person_id, query=query, limit=10)
        return json.dumps([_memory_brief(m) for m in memories], ensure_ascii=False)

    @tool(parse_docstring=True)
    def link_participants(
        memory_id: str,
        person_ids: list[str],
        question: str,
        runtime: ToolRuntime = None,  # type: ignore[assignment]  # injected by the tool node
    ) -> str | Command:
        """Add people to a saved memory and ask each of them for their version of it.

        Args:
            memory_id: The saved memory's id.
            person_ids: People in this family who were there.
            question: What to ask them, e.g. "Dad says you met on the church steps. What do you remember?"
        """
        # Who this run already asked, from the run's state files, so a repeated call (or a
        # second linker pass) never asks anyone twice about one memory.
        files = _state_files(runtime.state) if runtime is not None else {}
        with link_lock:
            memory = repo.get_memory(family_id, memory_id)
            if memory is None:
                return f"not found: memory {memory_id!r}"
            known: list[str] = []
            unknown: list[str] = []
            for pid in _dedup(person_ids):
                (known if repo.get_person(family_id, pid) is not None else unknown).append(pid)
            if known:
                memory = memory.model_copy(update={"participant_ids": _dedup([*memory.participant_ids, *known])})
                repo.save_memory(family_id, memory)
            told = {t.author_id for t in memory.tellings}
            asked: list[str] = []
            already: list[str] = []
            for pid in known:
                if pid == run.author_id or pid in told:
                    continue
                if asked_question_path(memory_id, pid) in files or (memory_id, pid) in asked_here:
                    already.append(pid)
                    continue
                repo.create_question(family_id, person_id=pid, text=question, memory_id=memory_id)
                asked_here.add((memory_id, pid))
                asked.append(pid)
        parts = [f"linked {', '.join(known) or 'nobody'} to {memory_id}"]
        parts.append(f"asked {', '.join(asked)}" if asked else "no one new to ask")
        if already:
            parts.append(f"already asked: {', '.join(already)}")
        if unknown:
            parts.append(f"not found in this family: {', '.join(unknown)}")
        content = "; ".join(parts)
        if runtime is None or not asked:
            return content
        return Command(
            update={
                "files": {
                    asked_question_path(memory_id, pid): create_file_data(
                        json.dumps({"memoryId": memory_id, "personId": pid}, ensure_ascii=False)
                    )
                    for pid in asked
                },
                "messages": [ToolMessage(content=content, tool_call_id=runtime.tool_call_id, name="link_participants")],
            }
        )

    @tool(parse_docstring=True)
    def save_memory(
        title: str,
        participant_ids: list[str],
        state: Annotated[dict, InjectedState],
        tool_call_id: Annotated[str, InjectedToolCallId],
    ) -> str | Command:
        """Save the compiled scene from /screenplay.json and /timeline.json as this run's memory (or as a new telling of the memory being retold).

        Args:
            title: A short title for the moment, e.g. "How I met your mother".
            participant_ids: Everyone in this family who was there.
        """
        files = _state_files(state)
        errors = _file_text(files, ERRORS_PATH)
        if errors and errors.strip():
            return (
                f"error: could not save: the last compile failed; fix {SCREENPLAY_PATH} "
                "and compile again before saving"
            )
        screenplay, error = _read_draft(state, SCREENPLAY_PATH, Screenplay)
        if error:
            return error
        timeline, error = _read_draft(state, TIMELINE_PATH, Timeline)
        if error:
            return error
        saved_id: str | None = None
        if not run.is_second_telling:
            saved_id, error = _saved_memory_id(files, run)
            if error:
                return error
        try:
            memory, ignored = save_scene_with_report(
                repo,
                run,
                title=title,
                participant_ids=participant_ids,
                screenplay=screenplay,
                timeline=timeline,
                update_memory_id=saved_id,
            )
        except (ValueError, ValidationError) as exc:
            return f"error: could not save: {exc}"
        _emit("scene_ready", schemas.SceneReadyEvent(memory_id=memory.id, version=memory.version))
        result = f"saved {memory.id} v{memory.version}"
        if ignored:
            result += f"; ignored narration for unknown shot(s): {', '.join(ignored)}"
        update: dict[str, Any] = {
            "messages": [ToolMessage(content=result, tool_call_id=tool_call_id, name="save_memory")],
        }
        if not run.is_second_telling:
            # One memory per run: a later save_memory in this run updates this one.
            record = {"memoryId": memory.id, "runId": run.run_id}
            update["files"] = {SAVED_MEMORY_PATH: create_file_data(json.dumps(record))}
        return Command(update=update)

    return [get_person, find_memories, link_participants, save_memory]
