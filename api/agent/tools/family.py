"""get_person, find_memories, save_memory, link_participants.

Every tool here is built by `make_family_tools(repo, run)` and closes over the run's
family id: no tool schema has a family id parameter, so nothing the model writes can
reach another family. `save_scene` is the plain-Python core of save_memory, shared by
the tool and by runs.py.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Annotated, Any

from deepagents.backends.utils import file_data_to_string
from langchain_core.tools import BaseTool, tool
from langgraph.config import get_stream_writer
from langgraph.prebuilt import InjectedState
from pydantic import BaseModel, ValidationError

import schemas
from agent.repo import AgentRepo
from agent.run_info import RunInfo
from schemas import Memory, Telling
from screenplay.schema import VOCABULARY, NarrationLine, Screenplay, Timeline

SCREENPLAY_PATH = "/screenplay.json"
TIMELINE_PATH = "/timeline.json"
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
) -> Memory:
    """Save the run's compiled scene as a new memory, or as a second telling of an existing one.

    New memory: a fresh id, the teller's Telling, participants that are real people in
    the family, props and sets from the screenplay, era from the year, version 1.

    Second telling (run.fields.memory_id set): the existing action (screenplay shots and
    timeline tracks) is kept untouched. The new Telling is appended and the draft's
    narration for the teller is added as screenplay.narrations[author] and
    timeline.narrations[author]; version goes up by one.
    """
    family_id = run.family_id
    fields = run.fields
    telling = Telling(
        author_id=run.author_id,
        question=fields.question,
        text=fields.text,
        photo_ids=list(run.photo_ids),
        created_at=datetime.now(UTC),
    )

    if run.is_second_telling:
        memory = _add_telling(repo, run, telling, screenplay=screenplay, timeline=timeline)
    else:
        memory_id = repo.new_id(family_id, "memory")
        candidates = _dedup([run.author_id, *participant_ids, *fields.participant_ids, *screenplay.cast])
        memory = Memory(
            id=memory_id,
            family_id=family_id,
            title=(fields.title or title).strip() or "Untitled",
            year=fields.year,
            era=era_for_year(fields.year),
            participant_ids=[pid for pid in candidates if repo.get_person(family_id, pid) is not None],
            tellings=[telling],
            screenplay=screenplay.model_copy(update={"memory_id": memory_id}, deep=True),
            timeline=timeline.model_copy(deep=True),
            prop_ids=_dedup(list(screenplay.props)),
            set_ids=_dedup([shot.set for shot in screenplay.shots]),
            version=1,
        )

    # Round-trip through validation so a bad draft never reaches the database.
    memory = Memory.model_validate(memory.model_dump(mode="json", by_alias=True))
    return repo.save_memory(family_id, memory)


def _add_telling(
    repo: AgentRepo,
    run: RunInfo,
    telling: Telling,
    *,
    screenplay: Screenplay,
    timeline: Timeline,
) -> Memory:
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

    shot_ids = {shot.id for shot in existing.screenplay.shots}
    lines_by_shot: dict[str, list[NarrationLine]]
    if screenplay.narrations and author in screenplay.narrations:
        lines_by_shot = {sid: lines for sid, lines in screenplay.narrations[author].items() if sid in shot_ids}
    else:
        lines_by_shot = {shot.id: shot.narration for shot in screenplay.shots if shot.id in shot_ids}

    new_screenplay = existing.screenplay.model_copy(deep=True)
    new_screenplay.narrations = {**(new_screenplay.narrations or {}), author: lines_by_shot}

    new_timeline = existing.timeline.model_copy(deep=True)
    new_timeline.narrations = {**(new_timeline.narrations or {}), author: draft_track.model_copy(deep=True)}
    if new_timeline.default_narrator == author:
        # Top-level audio and captions repeat the default narrator's; keep them in step.
        new_timeline.audio = draft_track.audio
        new_timeline.captions = [c.model_copy() for c in draft_track.captions]

    return existing.model_copy(
        update={
            "tellings": [*existing.tellings, telling],
            "participant_ids": _dedup([*existing.participant_ids, author]),
            "screenplay": new_screenplay,
            "timeline": new_timeline,
            "version": existing.version + 1,
        }
    )


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


def make_family_tools(repo: AgentRepo, run: RunInfo) -> list[BaseTool]:
    """The family tools for one run, bound to run.family_id."""
    family_id = run.family_id

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
    def link_participants(memory_id: str, person_ids: list[str], question: str) -> str:
        """Add people to a saved memory and ask each of them for their version of it.

        Args:
            memory_id: The saved memory's id.
            person_ids: People in this family who were there.
            question: What to ask them, e.g. "Dad says you met on the church steps. What do you remember?"
        """
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
        for pid in known:
            if pid == run.author_id or pid in told:
                continue
            repo.create_question(family_id, person_id=pid, text=question, memory_id=memory_id)
            asked.append(pid)
        parts = [f"linked {', '.join(known) or 'nobody'} to {memory_id}"]
        parts.append(f"asked {', '.join(asked)}" if asked else "no one new to ask")
        if unknown:
            parts.append(f"not found in this family: {', '.join(unknown)}")
        return "; ".join(parts)

    @tool(parse_docstring=True)
    def save_memory(
        title: str,
        participant_ids: list[str],
        state: Annotated[dict, InjectedState],
    ) -> str:
        """Save the compiled scene from /screenplay.json and /timeline.json as this run's memory (or as a new telling of the memory being retold).

        Args:
            title: A short title for the moment, e.g. "How I met your mother".
            participant_ids: Everyone in this family who was there.
        """
        screenplay, error = _read_draft(state, SCREENPLAY_PATH, Screenplay)
        if error:
            return error
        timeline, error = _read_draft(state, TIMELINE_PATH, Timeline)
        if error:
            return error
        try:
            memory = save_scene(
                repo, run, title=title, participant_ids=participant_ids, screenplay=screenplay, timeline=timeline
            )
        except (ValueError, ValidationError) as exc:
            return f"error: could not save: {exc}"
        _emit("scene_ready", schemas.SceneReadyEvent(memory_id=memory.id, version=memory.version))
        return f"saved {memory.id} v{memory.version}"

    return [get_person, find_memories, link_participants, save_memory]
