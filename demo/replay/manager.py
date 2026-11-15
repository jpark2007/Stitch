"""ReplayRunManager: the scripted studio, ending on a PREPARED scene when the telling matches one.

`ReplayRunManager` is `seed.fake_runs.ScriptedRunManager` with one difference. A new memory
whose telling matches a prepared story (demo/replay/fixtures.py) plays the same script, at the
same pace: plan, interviewer, the follow-up question, the merge question when there is one,
the props review, director, narrate, linker, scene_ready, done. But:

- the props review shows the prepared story's props (`reviewProps`, else every prop of its
  screenplay that is not a cast member's signature item), as this family's copies;
- the director stages the prepared screenplay, as authored, instead of the recast hymn-book
  sample. Its sets and props are created in the family when missing (keeping their ids when
  free, else `<id>_<familyId>`, as the scripted runs do); a family that already has an id
  keeps its own copy. Review edits apply as in the scripted runs: rename, replace_photo,
  remove (stripped from the scene; when the scene does not compile without it, it stays);
- the prepared default narration becomes the TELLER's telling (sources
  "telling:<teller>:s<n>"), so whoever tells the story live narrates the polished lines.
  Other narration tracks are dropped: nobody else has told this memory yet;
- the scene is compiled with the real compiler against this family and saved as the memory's
  scene. The telling (the submitted text plus the follow-up reply, and its photos) is saved
  exactly as in the scripted runs. The story's `title` and `year`, when given, are the
  memory's; the cast, the teller and the named participants are its participants.

Everything else is ScriptedRunManager's, unchanged: a telling that matches no story (or a
story whose cast is not in this family), every second telling, answers, streams, shutdown.
If the prepared scene fails to compile at run time, the run logs it and stages the scripted
scene instead, so a live demo never ends on an error.

`install_replay_runs()` puts one behind agent.runs. `use_replay_runs()` makes api/main.py
install it in place of the scripted manager (see demo/replay/app.py).

Logs name the matched story's folder, never the telling's text.
"""

from __future__ import annotations

import importlib
import logging
import os
import sys
import threading
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel
from pymongo.database import Database

import schemas
from db import client as db_client
from db import people as people_db
from db import props as props_db
from screenplay import compiler as screenplay_compiler
from screenplay.schema import Screenplay
from seed import fake_runs
from seed.demo import DEMO_FAMILY_ID
from seed.fake_runs import RunNotWaiting, ScriptedRunManager, compile_scene, remap_ids, strip_prop

from demo.replay.fixtures import (
    Match,
    PreparedStory,
    ReplayFixtureError,
    load_stories,
    match_story,
    replay_dir,
    validate_story,
)

# Under uvicorn's error logger, as api/main.py logs, so the server log shows which story matched.
logger = logging.getLogger("uvicorn.error").getChild("replay")

# ---------------------------------------------------------------------------------------------
# The private seams of seed.fake_runs this module relies on. ScriptedRunManager has no public
# hook for "which scene does a new memory get", so replay overrides four of its private methods
# and calls four private helpers. They are named here, once, and tests/test_seams.py checks
# that each still exists with the parameters used below, so a change in seed.fake_runs fails
# the replay tests loudly instead of silently playing the hymn-book scene.
#
#   overridden: _drive(run, db)                runs on the run's thread before any event
#               _ask(run, name, payload)       the props review pause
#               _emit(run, name, payload)      every event (texts that name the hymn scene)
#               _stage(run, db, sample, edits, removed, text, named) -> _Staged   the director
#   called:     _planned_id, _ensure, _family_lock, _apply_edits; the _Staged dataclass;
#               _Run.run_id / family_id / person_id / fields (fields is replaced, see _pick)
# ---------------------------------------------------------------------------------------------
_Staged = fake_runs._Staged
SEAMS: dict[str, tuple[str, ...]] = {
    "_drive": ("run", "db"),
    "_ask": ("run", "name", "payload"),
    "_emit": ("run", "name", "payload"),
    "_stage": ("run", "db", "sample", "edits", "removed", "text", "named"),
    "_planned_id": ("get", "db", "family_id", "sample_id"),
    "_ensure": ("get", "insert", "db", "family_id", "doc_id"),
    "_family_lock": ("family_id",),
    "_apply_edits": ("db", "family_id", "prop_id", "edits"),
}


@dataclass
class _Replay:
    """One run that matched a prepared story: what it shows and what the family answered."""

    story: PreparedStory
    match: Match
    db: Database
    review_ids: list[str]  # prepared prop ids shown in the props review, in order
    set_name: str
    chip_of: dict[str, str] = field(default_factory=dict)  # prepared prop id -> chip id
    chips: dict[str, schemas.PropChip] = field(default_factory=dict)  # chip id -> chip
    review: schemas.AnswerRequest | None = None
    needed: set[str] = field(default_factory=set)  # removed in the review, but the scene needs them

    def edits_for(self, chip_id: str) -> list[schemas.PropEdit]:
        return [e for e in (self.review.props or []) if e.id == chip_id] if self.review else []

    def asked_removed(self) -> set[str]:
        """Prepared prop ids the family asked to leave out in the review."""
        return {
            pid for pid, chip_id in self.chip_of.items()
            if any(e.action == "remove" for e in self.edits_for(chip_id))
        }

    def removed(self) -> set[str]:
        """Prepared prop ids left out of the scene: asked for, and not needed."""
        return self.asked_removed() - self.needed

    def prop_summary(self) -> str:
        """prop_master's summary: "Reused the tamale pot. Saved a lumpy tamale." """
        parts = []
        removed = self.asked_removed()
        for pid in self.review_ids:
            chip = self.chips.get(self.chip_of.get(pid, ""))
            if chip is None:
                continue
            edits = self.edits_for(chip.id)
            renamed = next((e.name.strip() for e in edits if e.action == "rename" and e.name and e.name.strip()), None)
            name = _the(renamed or chip.name)
            if pid in self.needed:
                parts.append(f"Kept {name}: the scene needs it.")
            elif pid in removed:
                parts.append(f"Left out {name}.")
            elif chip.reused:
                parts.append(f"Reused {name}.")
            else:
                parts.append(f"Saved {name}.")
        return " ".join(parts) or "No props to review."


def _the(name: str) -> str:
    """ "tamale pot" -> "the tamale pot"; "a lumpy tamale" and "Joseph's umbrella" stay as they are."""
    words = name.split()
    if not words or words[0].casefold() in ("a", "an", "the") or words[0].endswith(("'s", "’s")):
        return name
    return f"the {name}"


def narrated_by(screenplay: Screenplay, teller_id: str) -> Screenplay:
    """The prepared screenplay with its default narration told by `teller_id`. Pure.

    Every line of the shots' own narration keeps its words, time and cue; its source becomes
    "telling:<teller>:s<n>" (n counts the lines from 1, in shot order), so the teller is the
    default narrator. `narrations` (other people's tracks) is dropped: on a new memory nobody
    else has told it.
    """
    sp = screenplay.model_copy(deep=True)
    n = 0
    for shot in sp.shots:
        for line in shot.narration:
            n += 1
            line.source = f"telling:{teller_id}:s{n}"
    sp.narrations = None
    return sp


class ReplayRunManager(ScriptedRunManager):
    """ScriptedRunManager that stages a prepared story's scene when the telling matches it."""

    def __init__(self, *, stories: Sequence[PreparedStory] = (), **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._stories = tuple(stories)
        self._replays: dict[str, _Replay] = {}
        self._replay_lock = threading.Lock()

    @property
    def stories(self) -> tuple[PreparedStory, ...]:
        return self._stories

    def match(
        self, family_id: str, fields: schemas.SubmitMemoryFields, *, db: Database | None = None
    ) -> Match | None:
        """The prepared story this submission would replay (never one for a second telling).

        With `db`, only stories whose whole cast is in that family are candidates, so two
        families' stories never shadow each other."""
        if fields.memory_id is not None:
            return None
        stories = self._stories if db is None else [s for s in self._stories if cast_fits(db, family_id, s)]
        return match_story(fields.text, stories, family_id=family_id)

    # ------------------------------------------------------------ overridden seams

    def _drive(self, run: Any, db: Database) -> None:
        replay = self._pick(run, db)
        if replay is None:
            super()._drive(run, db)
            return
        with self._replay_lock:
            self._replays[run.run_id] = replay
        try:
            super()._drive(run, db)
        finally:
            with self._replay_lock:
                self._replays.pop(run.run_id, None)

    def _ask(self, run: Any, name: str, payload: BaseModel) -> schemas.AnswerRequest:
        replay = self._replay_of(run)
        if replay is None or name != "props_found":
            return super()._ask(run, name, payload)
        chips = self._review_chips(run, replay)
        answer = super()._ask(run, name, schemas.PropsFoundEvent(props=chips))
        replay.review = answer
        asked = replay.asked_removed()
        if asked and not self._compiles_without(run, replay, asked):
            logger.info("replay: prepared story %r needs %s; kept", replay.story.name, ", ".join(sorted(asked)))
            replay.needed = asked
        # The script only reads edits for the hymn-book chip, which was never shown: it sees
        # none, and the prepared props' edits are applied in _stage.
        return answer

    def _emit(self, run: Any, name: str, payload: BaseModel) -> None:
        replay = self._replay_of(run)
        if replay is not None:
            payload = self._retext(replay, name, payload)
        super()._emit(run, name, payload)

    def _stage(
        self, run: Any, db: Database, sample: Any, edits: Any, removed: bool, text: str, named: list[str]
    ) -> Any:
        replay = self._replay_of(run)
        if replay is None:
            return super()._stage(run, db, sample, edits, removed, text, named)
        staged = self._stage_prepared(run, db, replay, named)
        if staged.result.ok and staged.result.timeline is not None:
            return staged
        errors = "; ".join(e.message for e in staged.result.errors) or "no timeline"
        logger.error(
            "replay: prepared story %r did not compile for family %r (%s); staging the scripted scene",
            replay.story.name, run.family_id, errors,
        )
        return super()._stage(run, db, sample, [], False, text, named)

    # ------------------------------------------------------------ replay

    def _replay_of(self, run: Any) -> _Replay | None:
        with self._replay_lock:
            return self._replays.get(run.run_id)

    def _pick(self, run: Any, db: Database) -> _Replay | None:
        """The prepared story this run replays, or None to run the scripted script. Never raises."""
        try:
            found = self.match(run.family_id, run.fields, db=db)
            if found is None:
                if self._stories and run.fields.memory_id is None:
                    logger.info("replay: run %s matched no prepared story; scripted scene", run.run_id[:8])
                return None
            story, fid = found.story, run.family_id
            sp = story.screenplay
            lacking = [f"person {pid}" for pid in sp.cast if people_db.get_person(db, fid, pid) is None]
            lacking += [f"set {sid}" for sid in story.set_ids()
                        if story.set(sid) is None and props_db.get_set(db, fid, sid) is None]
            lacking += [f"prop {pid}" for pid in sp.props
                        if story.prop(pid) is None and props_db.get_prop(db, fid, pid) is None]
            if lacking:
                logger.warning(
                    "replay: run %s matched prepared story %r, but family %r has no %s; scripted scene",
                    run.run_id[:8], story.name, fid, ", ".join(lacking),
                )
                return None
            if story.review_props is not None:
                review_ids = list(story.review_props)
            else:
                items = {p.character.item_prop_id for p in people_db.get_people(db, fid, list(sp.cast))}
                review_ids = [pid for pid in dict.fromkeys(sp.props) if pid not in items]
            first_set = story.set_ids()[0]
            set_def = props_db.get_set(db, fid, first_set) or story.set(first_set)
            update: dict[str, Any] = {}
            if story.title:
                update["title"] = story.title
            if story.year is not None:
                update["year"] = story.year
            if update:
                # The script reads run.fields for the saved memory's title and year.
                run.fields = run.fields.model_copy(update=update)
            logger.info(
                "replay: run %s matched prepared story %r (%s, overlap %.2f)",
                run.run_id[:8], story.name, found.kind, found.score,
            )
            return _Replay(
                story=story, match=found, db=db, review_ids=review_ids,
                set_name=set_def.name if set_def is not None else first_set,
            )
        except Exception:  # noqa: BLE001 - a broken match never breaks the run: it is scripted
            logger.exception("replay: run %s could not be matched; scripted scene", run.run_id[:8])
            return None

    def _review_chips(self, run: Any, replay: _Replay) -> list[schemas.PropChip]:
        """The prepared props as this family would get them. `reused` when the family already has its own copy."""
        db, fid = replay.db, run.family_id
        chips: list[schemas.PropChip] = []
        for pid in replay.review_ids:
            prepared = replay.story.prop(pid)
            chip_id = self._planned_id(props_db.get_prop, db, fid, pid) if prepared is not None else pid
            current = props_db.get_prop(db, fid, chip_id)
            reused = current is not None and current.family_id == fid
            shown = current if (reused or prepared is None) else prepared
            if shown is None:
                continue
            chip = schemas.PropChip(
                id=chip_id, name=shown.name, description=shown.description, size=shown.size,
                art=shown.art.get("body"), reused=reused,
            )
            replay.chip_of[pid] = chip_id
            replay.chips[chip_id] = chip
            chips.append(chip)
        return chips

    def _retext(self, replay: _Replay, name: str, payload: BaseModel) -> BaseModel:
        """The script's texts that describe the hymn-book scene, told for the prepared one."""
        story = replay.story
        if name == "subagent_start" and getattr(payload, "name", None) == "director":
            return schemas.SubagentStartEvent(name="director", task=f"Stage the scene: {replay.set_name}")
        if name == "subagent_end" and getattr(payload, "name", None) == "prop_master":
            return schemas.SubagentEndEvent(name="prop_master", summary=replay.prop_summary())
        if story.question:
            if name == "question":
                return schemas.QuestionEvent(text=story.question)
            if name == "subagent_end" and getattr(payload, "name", None) == "interviewer":
                return schemas.SubagentEndEvent(name="interviewer", summary="One follow-up question.")
        return payload

    def _compiles_without(self, run: Any, replay: _Replay, removed: set[str]) -> bool:
        """Whether the prepared scene compiles with `removed` left out. Writes nothing: each set
        and prop is the definition the run would use (the family's own copy, else the story's)."""
        db, fid, story = replay.db, run.family_id, replay.story
        data = story.screenplay.model_dump(mode="json", by_alias=True, exclude_none=True)
        for pid in removed:
            data, _ = strip_prop(data, {}, pid)
        sp = Screenplay.model_validate(data)

        def used(get: Callable[[Database, str, str], Any], doc_id: str, prepared: Any) -> Any:
            if prepared is None:
                return get(db, fid, doc_id)
            found = get(db, fid, self._planned_id(get, db, fid, doc_id))
            return found if found is not None and found.family_id == fid else prepared

        people = {p.id: p for p in people_db.get_people(db, fid, list(sp.cast))}
        props = {pid: used(props_db.get_prop, pid, story.prop(pid)) for pid in sp.props}
        sets = {sid: used(props_db.get_set, sid, story.set(sid)) for sid in dict.fromkeys(s.set for s in sp.shots)}
        try:
            result = screenplay_compiler.compile(sp, sets=sets, props=props, people=people)
        except Exception:  # noqa: BLE001 - a crash is "does not compile"
            logger.exception("replay: the compiler crashed on %r without %s", story.name, sorted(removed))
            return False
        return result.ok

    def _stage_prepared(self, run: Any, db: Database, replay: _Replay, named: list[str]) -> Any:
        removed = replay.removed()
        staged = self._build_scene(run, db, replay, named, removed)
        if removed and not staged.result.ok:
            logger.warning(
                "replay: prepared story %r does not compile without %s; keeping them",
                replay.story.name, ", ".join(sorted(removed)),
            )
            staged = self._build_scene(run, db, replay, named, set())
        return staged

    def _build_scene(self, run: Any, db: Database, replay: _Replay, named: list[str], removed: set[str]) -> Any:
        """The prepared screenplay on this family's sets and props, narrated by the teller, compiled."""
        fid, story = run.family_id, replay.story
        ids: dict[str, str] = {}
        set_ids: list[str] = []
        prop_ids: list[str] = []
        with self._family_lock(fid):  # one run at a time creates a family's copies
            for sid in story.set_ids():
                ids[sid] = self._family_copy(
                    props_db.get_set, lambda doc_id, s: props_db.insert_set(db, fid, s, set_id=doc_id),
                    db, fid, sid, story.set(sid),
                )
                set_ids.append(ids[sid])
            for pid in dict.fromkeys(story.screenplay.props):
                if pid in removed:
                    continue
                final = self._family_copy(
                    props_db.get_prop, lambda doc_id, p: props_db.insert_prop(db, fid, p, prop_id=doc_id),
                    db, fid, pid, story.prop(pid),
                )
                ids[pid] = final
                prop_ids.append(final)
                chip_id = replay.chip_of.get(pid)
                if chip_id is not None:
                    self._apply_edits(db, fid, final, replay.edits_for(chip_id))

        data = story.screenplay.model_dump(mode="json", by_alias=True, exclude_none=True)
        for pid in removed:
            data, _ = strip_prop(data, {}, pid)
        data = remap_ids(data, {k: v for k, v in ids.items() if k != v})
        screenplay = narrated_by(Screenplay.model_validate(data), run.person_id)
        result = compile_scene(db, fid, screenplay)
        if result.ok and result.timeline is not None and fid == "macoskos":
            from demo.whitakers.narration import attach_narration
            result = result.model_copy(update={"timeline": attach_narration(db, fid, screenplay, result.timeline)})
        participants = list(dict.fromkeys([run.person_id, *screenplay.cast, *named]))
        return _Staged(
            screenplay=screenplay, result=result, participants=participants, prop_ids=prop_ids, set_ids=set_ids
        )

    def _family_copy(
        self,
        get: Callable[[Database, str, str], Any],
        insert: Callable[[str | None, Any], Any],
        db: Database,
        family_id: str,
        doc_id: str,
        prepared: Any,
    ) -> str:
        """The family's id for a prepared set or prop. Without a prepared definition the id is
        the family's own (or a library one) and is used as is."""
        if prepared is None:
            return doc_id
        planned = self._planned_id(get, db, family_id, doc_id)
        return self._ensure(get, lambda new_id: insert(new_id, prepared), db, family_id, planned)


# ------------------------------------------------------------------------------ loading


# Families a story without `familyId` is checked against at startup, in order (the first
# whose people include its whole cast). A launcher that seeds more families adds them.
REPLAY_FAMILIES: list[str] = [DEMO_FAMILY_ID]


def add_replay_family(family_id: str) -> None:
    """Check stories without `familyId` against this family too (after the ones before it)."""
    if family_id not in REPLAY_FAMILIES:
        REPLAY_FAMILIES.append(family_id)


def cast_fits(db: Database, family_id: str, story: PreparedStory) -> bool:
    """Whether every cast member of the story is a person of the family."""
    cast = list(dict.fromkeys(story.screenplay.cast))
    return len(people_db.get_people(db, family_id, cast)) == len(cast)


def story_family(db: Database, story: PreparedStory) -> str:
    """The family a story is checked against: its `familyId`, else the first of
    REPLAY_FAMILIES that has its whole cast, else the demo family (which then names the
    missing cast). At run time a story replays in any family that has its cast."""
    if story.family_id:
        return story.family_id
    return next((fid for fid in REPLAY_FAMILIES if cast_fits(db, fid, story)), DEMO_FAMILY_ID)


def family_inputs(
    db: Database, family_id: str, story: PreparedStory
) -> tuple[dict[str, schemas.Person], dict[str, schemas.Prop], dict[str, schemas.SetDef]]:
    """What the family already has of a story's people, props and sets (for `validate_story`)."""
    people = {p.id: p for p in people_db.list_people(db, family_id)}
    props = {pid: p for pid in story.screenplay.props if (p := props_db.get_prop(db, family_id, pid)) is not None}
    sets = {sid: s for sid in story.set_ids() if (s := props_db.get_set(db, family_id, sid)) is not None}
    return people, props, sets


def configured_stories(environ: Mapping[str, str] = os.environ) -> tuple[list[PreparedStory], Path]:
    """The stories in STITCH_REPLAY_DIR (or the default directory) and that directory.

    A missing default directory is no stories (with a warning); a missing directory that
    STITCH_REPLAY_DIR names is a FileNotFoundError. A bad story raises ReplayFixtureError.
    """
    directory, explicit = replay_dir(environ)
    if not directory.is_dir():
        if explicit:
            raise FileNotFoundError(f"STITCH_REPLAY_DIR is not a directory: {directory}")
        logger.warning("replay: no directory %s; every telling gets the scripted scene", directory)
        return [], directory
    return load_stories(directory), directory


def install_replay_runs(
    *,
    step_seconds: float = 0.6,
    stories: Sequence[PreparedStory] | None = None,
    db_getter: Callable[[], Database] = db_client.get_db,
) -> bool:
    """Put a ReplayRunManager behind agent.runs (via its `configure`). True on success.

    Same contract as seed.fake_runs.install_fake_runs, which api/main.py calls at startup.
    `stories` default to `configured_stories()`, each compiled against its family
    (`story_family`: its `familyId`, else the first of REPLAY_FAMILIES with its cast) in the
    installed database; a story that does not
    compile logs an error naming it and raises ReplayFixtureError.
    """
    try:
        runs = importlib.import_module("agent.runs")
    except Exception as exc:  # noqa: BLE001 - any import failure means no runs, as install_fake_runs
        logger.warning("replay runs not installed: agent.runs cannot be imported (%s)", exc)
        return False
    configure = getattr(runs, "configure", None)
    if not callable(configure):
        logger.warning("replay runs not installed: agent.runs has no callable configure()")
        return False
    not_waiting = getattr(runs, "RunNotWaiting", None)
    if not (isinstance(not_waiting, type) and issubclass(not_waiting, ValueError)):
        not_waiting = RunNotWaiting
    where = "the given stories"
    if stories is None:
        loaded, directory = configured_stories()
        db = db_getter()
        for story in loaded:
            family_id = story_family(db, story)
            people, props, sets = family_inputs(db, family_id, story)
            if not people:
                logger.warning("replay: %s is for family %r, which has nobody in this database", story.name, family_id)
                continue
            try:
                validate_story(story, people=people, props=props, sets=sets)
            except ReplayFixtureError:
                logger.error("replay: %s does not compile; fix it or move it out of %s", story.name, directory)
                raise
        stories, where = loaded, str(directory)
    configure(ReplayRunManager(stories=stories, step_seconds=step_seconds, db_getter=db_getter, not_waiting=not_waiting))
    names = ", ".join(s.name for s in stories) or "none"
    logger.info("replay: agent runs replay prepared stories from %s (%s), step %.2fs", where, names, step_seconds)
    return True


# ------------------------------------------------------------------------------ wiring


def use_replay_runs() -> None:
    """Make api/main.py's scripted-run startup install a ReplayRunManager.

    Sets STITCH_AGENT=fake (main.py then calls seed.fake_runs.install_fake_runs at startup,
    looked up by name at that moment) and points that name at `install_replay_runs`. Call
    before the app starts; `ensure_replay_manager` checks it took.
    """
    current = (os.environ.get("STITCH_AGENT") or "").strip().lower()
    if current and current != "fake":
        logger.warning("replay: STITCH_AGENT=%s ignored; replay mode runs scripted (fake) agent runs", current)
    os.environ["STITCH_AGENT"] = "fake"
    fake_runs.install_fake_runs = install_replay_runs  # type: ignore[assignment]


def _configured_manager() -> Any:
    """agent.runs' installed manager, or None. Reads its private `_manager` slot, as api/main.py
    does: get_manager() would build the model studio when nothing is configured."""
    runs = sys.modules.get("agent.runs")
    return getattr(runs, "_manager", None) if runs is not None else None


def ensure_replay_manager(step_seconds: float = 0.6) -> bool:
    """After startup: True when agent.runs has a ReplayRunManager. When it holds another
    manager (use_replay_runs did not take), that one is shut down and a replay one installed."""
    manager = _configured_manager()
    if isinstance(manager, ReplayRunManager):
        return True
    logger.error("replay: startup installed %s, not replay runs; installing them now", type(manager).__name__)
    if manager is not None:
        manager.shutdown()
    return install_replay_runs(step_seconds=step_seconds)


def wrap_lifespan(app: Any, step_seconds: Callable[[], float]) -> None:
    """Run `ensure_replay_manager` right after the app's own startup."""
    inner = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(application: Any) -> AsyncIterator[Any]:
        async with inner(application) as state:
            ensure_replay_manager(step_seconds())
            yield state

    app.router.lifespan_context = lifespan


__all__ = [
    "REPLAY_FAMILIES",
    "SEAMS",
    "ReplayRunManager",
    "add_replay_family",
    "cast_fits",
    "configured_stories",
    "ensure_replay_manager",
    "family_inputs",
    "install_replay_runs",
    "narrated_by",
    "story_family",
    "use_replay_runs",
    "wrap_lifespan",
]
