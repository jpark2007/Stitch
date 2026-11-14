"""Scripted agent runs for local and offline demos: no model calls, no network, no keys.

`ScriptedRunManager` is a stand-in for `agent.runs.RunManager` with the same seam
(start_run, stream_run, answer_run, status, wait, shutdown). It plays a fixed script on a
daemon thread, writes through db/ only, and stages every new memory with the hand-written
hymn-book scene from web/public/samples/hymn_books.memory.json (via `seed.demo.load_sample`),
recast with the run's people. `install_fake_runs()` puts one behind `agent.runs`, so the
add-a-memory flow works with no API keys and no MongoDB (README "Scope for the weekend").

Script for a new memory (`fields.memory_id is None`), one step every `step_seconds`:

1. plan (Read the telling / Ask one follow-up / Find the props / Stage the scene / Narrate)
2. subagent_start interviewer, subagent_end interviewer
3. question "Who else was there that day?", then the run waits for AnswerRequest(kind="answer").
   The reply is kept: it is appended to the telling text as a new paragraph.
   (Skipped when `ask_followup=False`.)
3b. merge, only when the telling (with that reply) calls someone by a family title and a
   capitalised name that is not quite anyone's name in the tree ("Grandpa Joe" when the
   tree has Joseph; see `merge_candidate`), and that someone can carry the title (a
   grandparent title needs a parent; "Grandma" never means a man by his record). A
   lowercase word is never a name, so "Grandma and Grandpa were there" asks nothing. The
   pause is "Is Grandpa Joe the Joseph in your family tree?", and the run waits for
   AnswerRequest(kind="merge"). Approved, that person joins the memory as if named in
   `participant_ids` (in the cast, in their book, asked for their version unless they have
   died; with nobody else named and them dead, the linker step is not planned at all).
   Rejected, nothing changes. At most one merge per run, and never for the
   teller or someone already named. A name that IS someone's name ("Grandma Rosa" when the
   tree has Rosa) asks nothing.
4. plan, subagent_start prop_master, props_found with the hymn-book prop, then the run waits
   for AnswerRequest(kind="props"). The chip's `reused` is True only when the family already
   had its own saved copy before the run (a fresh family gets a new copy: reused=False).
   Edits for the chip's id are honored: `rename` renames the saved prop, `replace_photo`
   puts a photo of this family on it (`photo_id` as "media/<id>" or a bare "<id>"; the art
   becomes {"body": "media/<id>"}, and an unknown id is ignored), `remove` leaves the prop
   out of the memory and strips it from the scene (the saved prop itself is never deleted,
   and is not created for this run if it did not exist yet). subagent_end prop_master says
   "Reused the X.", "Saved the X." or "Left out the X.".
5. plan, subagent_start director. The director stages the sample's screenplay for this
   family and COMPILES it with the real compiler (`screenplay.compiler.compile`) against
   this family's sets, props and people. Person ids are recast: the sample's `mom` becomes
   the teller, and `dad` the first participant who is not the teller, else the teller's
   spouse, else another person of the family. When the family has nobody else, `dad` is
   stripped from the scene (`strip_person`): one puppet, and the books left where they
   fell. Before the hand-off the giver walks up beside the receiver (`stage_handoffs`), so
   arm reach holds for every build. The sample's prop and set are created in the run's
   family when missing (keeping the sample ids when free, otherwise
   `<sampleId>_<familyId>`), and the scene points at the family's copies.
   The sample's narration (its people's words) is then replaced by the TELLER's, in their
   own words (`narrate_as_teller`): one line per sentence of the telling as saved (the
   submitted text plus the follow-up answer), sources "telling:<teller>:s<n>", every action
   pinned where the sample's narration put it. The teller is the default narrator and the
   only narration track; the recast `dad` is a puppet and a participant but narrates
   nothing, since they have no telling. Cued lines are tried first, uncued if they do not
   compile. The sample is timed for its own people: when the recast scene fails only
   because someone is still busy (a slow walker, like a grandparent with a cane, is still
   on the way to the books), the director tries again at a slower pace (`pace`, by each of
   PACE_FACTORS in turn). The family's copies of the set and prop are looked up and created
   under a per-family lock, so two runs at once never make two copies.
   subagent_end director reports the real outcome. If the compiler returns
   errors, the run emits compile_error, then subagent_end director with the first error,
   then one plain error event, and nothing is saved.
6. plan, then the memory is saved: create_memory with the telling, then save_scene with the
   recast, teller-narrated screenplay and the COMPILED timeline (never the sample's own
   timeline). The memory's participants are the teller, the recast `dad` (if any) and the
   named participants.
7. the linker (only when someone besides the teller was named in `participant_ids`): plan
   with "Ask the others for their version" in progress, then one open question per named
   participant, linked to the new memory ("<teller> told the story of "<title>". What do
   you remember?"). A person who has died (agent/prompts/linker.md) or who already has an
   open question for the memory is skipped, and a failure to create one is logged, never
   failing the run. The auto-recast `dad` is not asked unless named.
8. plan (all completed), scene_ready, done(memoryId)

A second telling (`fields.memory_id` set) needs a memory of this family WITH a scene:
`start_run` raises `MemoryNotFound` (a LookupError, like agent.runs.MemoryNotFound) when the
memory is missing, belongs to another family, or has no screenplay or timeline yet, so the
route answers 409 NO_SCENE. The run: plan, interviewer, then the narrator writes the new
teller's narration from the telling text (`telling_narration`: one line per sentence,
spread over the existing shots in order, the first line of each shot cued to an action),
and the scene is recompiled with the real compiler. Anyone but the default narrator (for a
scripted memory, its first teller) gets their own `narrations[<teller>]` track and the
default narrator stays; the default narrator's own retelling replaces the shots' narration,
with the action pinned where it was. Only when that compiles are the telling
added, the participants linked and the scene saved (`save_scene`: the version goes up by
one, and the timeline carries the new teller's narration track). Participants follow
agent/tools/family.py: the teller joins the moment; then, as the real linker does, the named
participants who are new to the moment are linked and each is asked for their version
(never the teller, and never someone who already told it). Then plan, scene_ready with the
new version, done. No question, no prop review. A recompile that fails emits compile_error,
then a plain error event, and writes nothing.

Second-telling edge cases (tests/data/test_demo_second_tellings.py runs each one on the
seeded demo moments):

- A long telling: the lines of one shot follow each other LINE_GAP apart and never overlap,
  so they may run past the shot's last action. The compiler contract allows that: a shot
  ends when its action has settled AND the last word of every telling has been said
  (screenplay/layout.content_end), so the shot, and the timeline, grow until the telling is
  heard; the action does not move. Later shots then start later in timeline time (every
  teller's lines keep their shot time).
- Narration audio (`keep_audio`): a teller's audio is one clip from timeline t=0, so the
  other tellers (and the default narrator's top-level `audio`) keep theirs only while their
  lines are said at exactly the same timeline times. When a longer shot moves their later
  lines, their audio is dropped (None) rather than left playing out of step. The new
  teller's track never has audio here (no TTS in scripted runs).
- The same teller telling again: their telling is replaced (db/memories.add_telling) and
  so is their whole track; never two tellings or duplicate lines.
- Text with no spoken word ("...", "?!", emoji only): the telling is saved and the teller
  gets ONE uncued line of the stripped raw text at the first shot's t=0, with no word
  timings: a caption, nothing to say. A teller never has a telling without a track. Only a
  blank text gives no line at all; that run ends with TELLING_FAILED_MESSAGE and writes
  nothing (the route already refuses a blank text with 422).
- Photos submitted with a second telling are kept on that telling.

Any exception ends the run with a single error event with a plain message. Every payload is
an instance of `schemas.RUN_EVENTS[name]`, and every run ends with exactly one done or error.

Answers (`answer_run`) behave as agent.runs.RunManager's do behind the routes: an unknown or
other-family run is a LookupError (404), another user's answer a PermissionError (403), an
answer while the run is not paused raises `RunNotWaiting` (409 "This run is not waiting for
an answer"; `install_fake_runs` hands the manager agent.runs.RunNotWaiting, the class the
route recognises), and an answer of the wrong kind a ValueError (409); the run stays paused.
Of two answers racing for one pause, exactly one is taken. A reattached stream replays the
run's one event log from the first event, so nothing is duplicated and an answered pause is
followed by what came after it, never asked again.

This module never imports `agent` except inside `install_fake_runs`.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import logging
import re
import threading
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel
from pymongo.database import Database

import schemas
from db import client as db_client
from db import media as media_db
from db import memories as memories_db
from db import people as people_db
from db import props as props_db
from screenplay import compiler as screenplay_compiler
from screenplay import narration as narration_timing
from screenplay.schema import (
    CompileError,
    CompileResult,
    NarrationLine,
    NarrationTrack,
    Screenplay,
    Timeline,
)
from seed import demo

logger = logging.getLogger(__name__)

Event = tuple[str, schemas.Model]

SAMPLE_TELLER = "mom"
SAMPLE_OTHER = "dad"
FOLLOWUP_QUESTION = "Who else was there that day?"
ASK_OTHERS = "Ask the others for their version"
ERROR_MESSAGE = "Something went wrong while making this memory. Please try again."
STOPPED_MESSAGE = "The studio stopped before this memory was finished."
SCENE_FAILED_MESSAGE = "The scene for this memory could not be staged, so nothing was saved. Please try again."
TELLING_FAILED_MESSAGE = "This telling could not be added to the scene, so nothing was saved. Please try again."
TITLE_WORDS = 6

HANDOFF_MARK = "handoff_spot"
HANDOFF_GAP = 0.07
"""How far (in u, a fraction of the stage width) beside the receiver the giver stands to hand
a prop over. Every pair of builds reaches across it (tests/data/test_fake_runs.py checks all)."""
HANDOFF_DELAY = 1.05
"""Seconds the hand-off (and everything after it) moves later, to make room for that walk."""
HANDOFF_WALK_LEAD = 0.2
"""The giver starts walking this long before the hand-off's original time."""
LINE_GAP = 0.4
"""Seconds of silence between two of a second teller's lines in one shot."""


MERGE_QUESTION = "Is {said} the {candidate} in your family tree?"
KIN_TITLES: tuple[str, ...] = (
    "Grandma", "Grandpa", "Granny", "Grandmother", "Grandfather", "Nana", "Nonna", "Abuela", "Abuelo",
    "Uncle", "Aunt", "Auntie", "Tia", "Tio", "Tía", "Tío", "Cousin",
)
"""Family titles the scripted interviewer reads as "this is someone we may already know"."""
_KIN_NAME = re.compile(r"\b(" + "|".join(KIN_TITLES) + r")\s+([^\W\d_][^\W\d_'-]*)")
"""A title and the word after it. Only a capitalised word is a name (`merge_candidate`):
"Grandma and Grandpa were there" names nobody."""

_ELDER_TITLES = frozenset(
    {"grandma", "grandpa", "granny", "grandmother", "grandfather", "nana", "nonna", "abuela", "abuelo"}
)
"""Titles for a grandparent: only someone with a child in the tree can be one."""
_FEMALE_WORDS = frozenset({
    "grandma", "granny", "grandmother", "nana", "nonna", "abuela", "aunt", "auntie", "tia", "tía",
    "mom", "mum", "mother", "mama", "sister", "daughter", "wife", "she", "her", "hers", "herself",
})
_MALE_WORDS = frozenset({
    "grandpa", "grandfather", "abuelo", "uncle", "tio", "tío",
    "dad", "father", "papa", "brother", "son", "husband", "he", "his", "him", "himself",
})
_WORD = re.compile(r"[^\W\d_]+")


class MemoryNotFound(LookupError):
    """`fields.memory_id` is not a memory of this family, or it has no scene yet.

    Mirrors agent.runs.MemoryNotFound, so routes/memories.py answers 409 NO_SCENE for a
    memory it found but that cannot take another telling.
    """


class RunNotWaiting(RuntimeError, ValueError):
    """An answer while the run is not paused. Mirrors agent.runs.RunNotWaiting (the route sends 409).

    `install_fake_runs` gives the manager agent.runs.RunNotWaiting itself, so the route's
    `isinstance(exc, agent.runs.RunNotWaiting)` holds and the 409 says NOT_WAITING.
    """


NEEDS_INPUT = {"question": "answer", "props_found": "props", "merge": "merge"}
"""Event that pauses the run -> the AnswerRequest kind that resumes it."""
TERMINAL = frozenset({"done", "error"})


class _Stopped(Exception):
    """The manager was shut down while the run was in flight."""


# --------------------------------------------------------------------------- one run


@dataclass(eq=False)
class _Run:
    run_id: str
    family_id: str
    user_id: str
    person_id: str
    fields: schemas.SubmitMemoryFields
    photo_ids: list[str]
    cond: threading.Condition = field(default_factory=threading.Condition)
    events: list[Event] = field(default_factory=list)
    status: schemas.RunStatus = "running"
    pending: str | None = None  # the needs-input event the run is paused on
    answer: schemas.AnswerRequest | None = None
    subscribers: list[tuple[asyncio.AbstractEventLoop, asyncio.Event]] = field(default_factory=list)

    @property
    def terminal(self) -> bool:
        return self.status in TERMINAL

    def notify(self) -> None:
        """Wake threads in wait() and every async subscriber. Call with `cond` held."""
        self.cond.notify_all()
        for loop, wake in list(self.subscribers):
            try:
                loop.call_soon_threadsafe(wake.set)
            except RuntimeError:  # that subscriber's loop is closed
                self.subscribers.remove((loop, wake))


@dataclass
class _Staged:
    """The director's work for a new memory: the recast screenplay and its compile."""

    screenplay: Screenplay
    result: CompileResult
    participants: list[str]
    prop_ids: list[str]
    set_ids: list[str]


# --------------------------------------------------------------------- scene recasting

_TOKEN = re.compile(r"[A-Za-z0-9_]+")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9_.:\-]+$")
# Fields that hold prose, never ids: left alone even when a word equals an id.
_PROSE_KEYS = frozenset({"text", "word", "label", "pageText", "title", "description", "notes", "critique"})


def _remap_str(value: str, ids: dict[str, str]) -> str:
    """Replace whole id tokens in an id-like string: "mom", "puppet:mom.hand_r", "telling:dad:s2"."""
    if value in ids:
        return ids[value]
    if not _IDENTIFIER.match(value):
        return value
    return _TOKEN.sub(lambda m: ids.get(m.group(0), m.group(0)), value)


def remap_ids(value: Any, ids: dict[str, str], key: str | None = None) -> Any:
    """Recursively rename ids in a JSON value (dict keys included; prose fields skipped).

    When two keys of one object map to the same id, the first one is kept.
    """
    if not ids:
        return value
    if isinstance(value, dict):
        out: dict[Any, Any] = {}
        for k, v in value.items():
            new_key = _remap_str(k, ids) if isinstance(k, str) else k
            if new_key not in out:
                out[new_key] = remap_ids(v, ids, k if isinstance(k, str) else None)
        return out
    if isinstance(value, list):
        return [remap_ids(v, ids, key) for v in value]
    if isinstance(value, str) and key not in _PROSE_KEYS:
        return _remap_str(value, ids)
    return value


def _dedupe(ids: list[str]) -> list[str]:
    return list(dict.fromkeys(ids))


def _strip_interactions(items: list[dict] | None, target: str, prop_id: str | None) -> list[dict] | None:
    """Drop `target` from interaction drives (and interactions driving `prop_id`, when given)."""
    if items is None:
        return None
    kept = []
    for item in items:
        drives = dict(item.get("drives") or {})
        if prop_id is not None and drives.get("prop") == prop_id:
            continue
        targets = drives.get("targets")
        if targets is not None:
            targets = [t for t in targets if not _names(t, target)]
            if not targets:
                continue
            drives["targets"] = targets
        kept.append({**item, "drives": drives})
    return kept


def _strip_explanations(items: list[dict] | None, target: str) -> list[dict] | None:
    if items is None:
        return None
    return [e for e in items if e.get("target") != target]


def _names(value: Any, ref: str) -> bool:
    """True when `value` is `ref` itself or a part of it: "hymn_books.top", "puppet:dad.hand_r"."""
    return isinstance(value, str) and (value == ref or value.startswith(ref + "."))


def strip_prop(screenplay: dict, timeline: dict, prop_id: str) -> tuple[dict, dict]:
    """Remove one prop from a screenplay and timeline (camelCase JSON dicts)."""
    target = f"prop:{prop_id}"
    sp = dict(screenplay)
    sp["props"] = [p for p in sp.get("props", []) if p != prop_id]
    shots = []
    for shot in sp.get("shots", []):
        shot = dict(shot)
        start = {}
        for who, state in (shot.get("start") or {}).items():
            state = {k: v for k, v in state.items() if not (k in ("holding", "in") and _names(v, prop_id))}
            start[who] = state
        shot["start"] = start
        shot["propStart"] = {
            pid: {k: v for k, v in ps.items() if not (k == "on" and _names(v, prop_id))}
            for pid, ps in (shot.get("propStart") or {}).items()
            if pid != prop_id
        }
        shot["camera"] = [
            {k: v for k, v in cue.items() if not (k == "target" and v == prop_id)} for cue in shot.get("camera", [])
        ]
        actions = [
            a for a in shot.get("actions", [])
            if not any(_names(a.get(k), prop_id) for k in ("what", "on", "target", "at"))
        ]
        shot["actions"] = actions
        cues = {a["id"] for a in actions if a.get("id")} | {a["do"] for a in actions}

        def clean(lines: list[dict], cues: set[str] = cues) -> list[dict]:
            out = []
            for line in lines:
                if line.get("cue") is not None and line["cue"] not in cues:
                    line = {k: v for k, v in line.items() if k not in ("cue", "cueWord")}
                out.append(line)
            return out

        shot["narration"] = clean(shot.get("narration", []))
        if sp.get("narrations"):
            sp["narrations"] = {
                author: {sid: (clean(lines) if sid == shot["id"] else lines) for sid, lines in by_shot.items()}
                for author, by_shot in sp["narrations"].items()
            }
        shots.append(shot)
    sp["shots"] = shots
    sp["interactions"] = _strip_interactions(sp.get("interactions"), target, prop_id)
    sp["explanations"] = _strip_explanations(sp.get("explanations"), target)

    tl = dict(timeline)
    tl["tracks"] = [t for t in tl.get("tracks", []) if t.get("target") != target]
    tl["interactions"] = _strip_interactions(tl.get("interactions"), target, prop_id)
    tl["explanations"] = _strip_explanations(tl.get("explanations"), target)
    return sp, tl


def _from_telling(line: dict, person_id: str) -> bool:
    source = line.get("source")
    return isinstance(source, str) and source.startswith(f"telling:{person_id}:")


def _drop_cues(lines: list[dict], gone: set[str]) -> list[dict]:
    """Drop cue/cueWord from lines whose cue names an action that no longer exists."""
    return [
        {k: v for k, v in line.items() if k not in ("cue", "cueWord")} if line.get("cue") in gone else line
        for line in lines
    ]


def strip_person(screenplay: dict, timeline: dict, person_id: str, *, keep_narrator: str) -> tuple[dict, dict]:
    """Remove one person from a screenplay and timeline (camelCase JSON dicts). Pure.

    Their cast entry, start states, actions (by them, or naming them in to/target/at/on/what),
    camera targets, narrations, interactions and explanations go. Shot narration from their
    telling is replaced with `keep_narrator`'s lines for that shot (which then leave
    `screenplay.narrations`, since they are now the default telling). In the timeline, their
    tracks go; any other target whose keys name them (a prop parented to their hand) is cut,
    with all its channels, from the first such key, so it stays where it last was. When they
    were the default narrator, `keep_narrator` takes over `defaultNarrator`, `captions` and
    `audio`.
    """
    puppet = f"puppet:{person_id}"
    sp = dict(screenplay)
    sp["cast"] = [c for c in sp.get("cast", []) if c != person_id]

    other_narrations: dict[str, dict[str, list[dict]]] = {
        author: dict(by_shot)
        for author, by_shot in (sp.get("narrations") or {}).items()
        if author != person_id
    }
    keep_lines = other_narrations.get(keep_narrator, {})

    shots = []
    removed_cues: set[str] = set()
    kept_cues: set[str] = set()
    for shot in sp.get("shots", []):
        shot = dict(shot)
        shot["start"] = {who: state for who, state in (shot.get("start") or {}).items() if who != person_id}
        shot["camera"] = [
            {k: v for k, v in cue.items() if not (k == "target" and _names(v, person_id))}
            for cue in shot.get("camera", [])
        ]
        actions, dropped = [], []
        for action in shot.get("actions", []):
            gone = action.get("who") == person_id or any(
                _names(action.get(k), person_id) for k in ("to", "target", "at", "on", "what")
            )
            (dropped if gone else actions).append(action)
        shot["actions"] = actions
        cues = {a["id"] for a in actions if a.get("id")} | {a["do"] for a in actions}
        gone_cues = ({a["id"] for a in dropped if a.get("id")} | {a["do"] for a in dropped}) - cues
        removed_cues |= gone_cues
        kept_cues |= cues

        narration = shot.get("narration", [])
        if any(_from_telling(line, person_id) for line in narration):
            narration = list(keep_lines.get(shot["id"], []))
            if shot["id"] in keep_lines:  # now the default telling, no longer an "other" one
                keep_lines = {sid: lines for sid, lines in keep_lines.items() if sid != shot["id"]}
        shot["narration"] = _drop_cues(narration, gone_cues)
        other_narrations = {
            author: {
                sid: (_drop_cues(lines, gone_cues) if sid == shot["id"] else lines) for sid, lines in by_shot.items()
            }
            for author, by_shot in other_narrations.items()
        }
        shots.append(shot)
    sp["shots"] = shots

    if keep_narrator in other_narrations:
        other_narrations[keep_narrator] = {
            sid: lines for sid, lines in other_narrations[keep_narrator].items() if sid in keep_lines
        }
    other_narrations = {author: by_shot for author, by_shot in other_narrations.items() if by_shot}
    if sp.get("narrations") is not None:
        sp["narrations"] = other_narrations or None
    sp["interactions"] = _strip_interactions(sp.get("interactions"), puppet, None)
    sp["explanations"] = _strip_person_explanations(sp.get("explanations"), puppet)

    tl = dict(timeline)
    tracks = [t for t in tl.get("tracks", []) if t.get("target") != puppet]
    cut: dict[str, float] = {}
    for track in tracks:
        for t, value in track.get("keys", []):
            if _names(value, puppet):
                target = track["target"]
                cut[target] = min(t, cut.get(target, t))
    trimmed = []
    for track in tracks:
        if track["target"] in cut:
            keys = [k for k in track.get("keys", []) if k[0] < cut[track["target"]]]
            if not keys:
                continue
            track = {**track, "keys": keys}
        trimmed.append(track)
    tl["tracks"] = trimmed

    gone_cues = removed_cues - kept_cues
    if tl.get("narrations") is not None:
        tl["narrations"] = {
            author: {**track, "lines": _drop_cues(track.get("lines", []), gone_cues)}
            for author, track in tl["narrations"].items()
            if author != person_id
        }
    if tl.get("defaultNarrator") == person_id:
        tl["defaultNarrator"] = keep_narrator
        keep_track = (tl.get("narrations") or {}).get(keep_narrator) or {}
        tl["captions"] = list(keep_track.get("captions", []))
        tl["audio"] = keep_track.get("audio")
    tl["interactions"] = _strip_interactions(tl.get("interactions"), puppet, None)
    tl["explanations"] = _strip_person_explanations(tl.get("explanations"), puppet)
    return sp, tl


def _strip_person_explanations(items: list[dict] | None, puppet: str) -> list[dict] | None:
    if items is None:
        return None
    return [e for e in items if not _names(e.get("target"), puppet)]


def short_title(text: str, words: int = TITLE_WORDS) -> str:
    """A short title from the first sentence of the telling."""
    first = re.split(r"(?<=[.!?])\s+", text.strip(), maxsplit=1)[0]
    tokens = first.split()
    title = " ".join(tokens[:words]).rstrip(".,;:!?-")
    if len(tokens) > words:
        title += "…"
    return title or "A memory"


# ------------------------------------------------------------------------ staging

_MOVES_TO = frozenset({"enter", "walk_to", "run_to"})


def _mark_of(shot: dict, who: str, before: float) -> str | None:
    """The mark `who` stands on just before shot time `before`: their last enter/walk_to/run_to
    destination, else their start mark."""
    at = (shot.get("start") or {}).get(who, {}).get("at")
    for action in sorted(shot.get("actions", []), key=lambda a: a.get("t", 0)):
        if action.get("t", 0) < before and action.get("who") == who and action.get("do") in _MOVES_TO:
            at = action.get("to") or at
    return at


def stage_handoffs(screenplay: dict, sets: dict[str, dict[str, list[float]]]) -> dict:
    """Make every hand_to reachable for any pair of builds (camelCase JSON dict in and out). Pure.

    For each hand_to whose receiver stands on a known mark, the giver first walks to a new
    shot mark HANDOFF_GAP beside the receiver, on the receiver's height and on the giver's
    side. The walk starts HANDOFF_WALK_LEAD before the hand-off's original time, and the
    hand-off, every later action and every narration line from the one cued to it on move
    HANDOFF_DELAY later to make room. `sets` maps set id -> that set's marks.
    """
    sp = json.loads(json.dumps(screenplay))
    for shot in sp.get("shots", []):
        handoffs = [a for a in shot.get("actions", []) if a.get("do") == "hand_to" and a.get("who") and a.get("to")]
        if len(handoffs) != 1:
            continue  # the sample has one; anything else is left as written
        hand = handoffs[0]
        marks = {**sets.get(shot.get("set"), {}), **(shot.get("marks") or {})}
        hand_t = float(hand.get("t", 0))
        taker_mark = marks.get(_mark_of(shot, hand["to"], hand_t) or "")
        giver_mark = marks.get(_mark_of(shot, hand["who"], hand_t) or "")
        if taker_mark is None:
            continue
        side = -1.0 if giver_mark is None or giver_mark[0] <= taker_mark[0] else 1.0
        u = min(1.0, max(0.0, taker_mark[0] + side * HANDOFF_GAP))
        shot["marks"] = {**(shot.get("marks") or {}), HANDOFF_MARK: [round(u, 6), *taker_mark[1:]]}

        cue_names = {name for name in (hand.get("id"), hand.get("do")) if name}
        cued = [line for line in shot.get("narration", []) if line.get("cue") in cue_names]
        line_from = min((float(line["t"]) for line in cued), default=hand_t)
        for action in shot["actions"]:
            if float(action.get("t", 0)) >= hand_t:
                action["t"] = round(float(action["t"]) + HANDOFF_DELAY, 6)
        lines = [shot.get("narration", [])] + [
            by_shot.get(shot["id"], []) for by_shot in (sp.get("narrations") or {}).values()
        ]
        for group in lines:
            for line in group:
                if float(line.get("t", 0)) >= line_from:
                    line["t"] = round(float(line["t"]) + HANDOFF_DELAY, 6)
        walk = {"t": round(max(0.0, hand_t - HANDOFF_WALK_LEAD), 6), "who": hand["who"], "do": "walk_to",
                "to": HANDOFF_MARK}
        shot["actions"].insert(shot["actions"].index(hand), walk)
    return sp


def pace(screenplay: Screenplay, factor: float) -> Screenplay:
    """The screenplay with every start time stretched by `factor`: actions, camera cues and every
    narration line, in every shot. Durations are kept. Pure.

    The sample scene is timed for its own people. A slower recast (Grandpa Joseph "walks
    slowly with a cane") cannot reach the books in the sample's time, so the director gives
    the whole scene more time rather than fail to stage it (see PACE_FACTORS).
    """
    sp = screenplay.model_copy(deep=True)
    for shot in sp.shots:
        for item in [*shot.actions, *shot.camera, *shot.narration]:
            item.t = round(item.t * factor, 6)
        for by_shot in (sp.narrations or {}).values():
            for line in by_shot.get(shot.id, []):
                line.t = round(line.t * factor, 6)
    return sp


PACE_FACTORS: tuple[float, ...] = (1.0, 1.25, 1.5, 2.0, 2.5)
"""Paces the director tries, in order, while the compile fails only because someone is still busy."""


def scene_inputs(
    db: Database, family_id: str, screenplay: Screenplay
) -> tuple[dict[str, schemas.SetDef], dict[str, schemas.Prop], dict[str, schemas.Person], list[CompileError]]:
    """This family's sets, props and people for a screenplay (as agent/wiring.scene_for loads
    them), plus an unknown_set / unknown_prop / unknown_person error for each id it lacks."""
    sets: dict[str, schemas.SetDef] = {}
    props: dict[str, schemas.Prop] = {}
    people: dict[str, schemas.Person] = {}
    missing: list[CompileError] = []
    for person_id in dict.fromkeys(screenplay.cast):
        person = people_db.get_person(db, family_id, person_id)
        if person is None:
            missing.append(CompileError(code="unknown_person", message=f'unknown person "{person_id}"'))
        else:
            people[person_id] = person
    for prop_id in dict.fromkeys(screenplay.props):
        prop = props_db.get_prop(db, family_id, prop_id)
        if prop is None:
            missing.append(CompileError(code="unknown_prop", message=f'unknown prop "{prop_id}"'))
        else:
            props[prop_id] = prop
    for set_id in dict.fromkeys(shot.set for shot in screenplay.shots):
        set_def = props_db.get_set(db, family_id, set_id)
        if set_def is None:
            missing.append(CompileError(code="unknown_set", message=f'unknown set "{set_id}"'))
        else:
            sets[set_id] = set_def
    return sets, props, people, missing


def compile_scene(db: Database, family_id: str, screenplay: Screenplay) -> CompileResult:
    """Compile with the real compiler against this family's sets, props and people.

    Never raises for a bad screenplay: a missing id or a compiler crash comes back as errors.
    """
    sets, props, people, missing = scene_inputs(db, family_id, screenplay)
    if missing:
        return CompileResult(ok=False, errors=missing)
    try:
        result = screenplay_compiler.compile(screenplay, sets=sets, props=props, people=people)
    except Exception as exc:  # noqa: BLE001 - a compiler crash is a failed compile, never a crashed run
        logger.exception("the compiler crashed")
        return CompileResult(
            ok=False, errors=[CompileError(code="compiler_crash", message=f"compiler crashed: {type(exc).__name__}")]
        )
    if result.ok and result.timeline is None:
        return CompileResult(
            ok=False, errors=[CompileError(code="no_timeline", message="compiler returned ok without a timeline")]
        )
    return result


# ------------------------------------------------------------------ second tellings

_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+|(?<=[.!?…][\"'”’)\]])\s+|\n+")


def split_sentences(text: str) -> list[str]:
    """The telling as sentences (and paragraphs), in order, each with at least one spoken word."""
    parts = [p.strip() for p in _SENTENCE_END.split(text.strip()) if p and p.strip()]
    sentences = [p for p in parts if narration_timing.tokenize(p)]
    return sentences or ([text.strip()] if narration_timing.tokenize(text) else [])


def _spread(count: int, shots: int) -> list[range]:
    """Contiguous, in-order slices of `count` sentences over `shots` shots (earlier shots first)."""
    if shots <= 0:
        return []
    if count <= shots:
        return [range(i, i + 1) if i < count else range(0) for i in range(shots)]
    return [range(count * j // shots, count * (j + 1) // shots) for j in range(shots)]


def _cue_for(shot: Any, retimed: list[float]) -> tuple[str, float] | None:
    """(cue name, shot time) of the earliest action a cue can name unambiguously, or None."""
    best: tuple[str, float] | None = None
    for i, action in enumerate(shot.actions):
        name = action.id or action.do
        try:
            if narration_timing.cue_action_index(shot, name) != i:
                continue  # a later action with the same verb and no id cannot be named
        except narration_timing.NarrationError:
            continue
        if retimed[i] >= 0 and (best is None or retimed[i] < best[1]):
            best = (name, retimed[i])
    return best


def telling_narration(screenplay: Screenplay, author_id: str, text: str, *, cued: bool = True) -> Screenplay:
    """The screenplay with `author_id`'s narration of `text` over the existing action. Pure.

    The text is split into sentences, spread over the shots in order, one line per sentence,
    each with source "telling:<author>:s<n>". With `cued`, a shot's first line is cued on its
    first word to the shot's earliest nameable action (where that action already lands, so
    nothing moves) and starts there; the rest follow, LINE_GAP apart, never overlapping.

    A teller who does not tell the default narration gets `narrations[author_id]` (replacing
    any lines they had). The default narrator's retelling replaces the shots' own narration;
    every action is pinned at the time the old narration gave it first, so the shared action
    does not move.

    A text with no spoken word ("...", emoji only) still gives its teller a track: ONE
    uncued line of the stripped raw text at the first shot's t=0, source
    "telling:<author>:s1", which compiles with no word timings (a caption, nothing to say).
    Only a blank text gives no lines at all; the run refuses that (see `_second_telling`).
    """
    sp = screenplay.model_copy(deep=True)
    default = narration_timing.default_author(sp)
    by_shot = _lines_over(sp, author_id, text, cued=cued, pin=author_id == default)
    if not by_shot and text.strip() and sp.shots:
        by_shot = {sp.shots[0].id: [NarrationLine(t=0.0, text=text.strip(), source=f"telling:{author_id}:s1")]}
    if author_id == default:
        for shot in sp.shots:
            shot.narration = by_shot.get(shot.id, [])
        others = {a: lines for a, lines in (sp.narrations or {}).items() if a != author_id}
        sp.narrations = others or None
    else:
        sp.narrations = {**(sp.narrations or {}), author_id: by_shot}
    return sp


def _lines_over(sp: Screenplay, author_id: str, text: str, *, cued: bool, pin: bool) -> dict[str, list[NarrationLine]]:
    """`author_id`'s lines for `text`, by shot id, over `sp`'s action as its CURRENT default
    narration times it. With `pin`, every action of `sp` (mutated in place) is pinned at that
    time, so the action stays put once the default narration is replaced."""
    default = narration_timing.default_author(sp)
    sentences = split_sentences(text)
    slices = _spread(len(sentences), len(sp.shots))
    by_shot: dict[str, list[NarrationLine]] = {}
    for shot, idx in zip(sp.shots, slices, strict=True):
        try:
            retimed = narration_timing.retime_actions(shot, 0.0, author=default)
        except narration_timing.NarrationError:
            retimed = [a.t for a in shot.actions]
        if pin:  # pin the action where the old narration put it
            for action, t in zip(shot.actions, retimed, strict=True):
                action.t = t
        cue = _cue_for(shot, retimed) if cued else None
        lines: list[NarrationLine] = []
        t = cue[1] if cue is not None else 0.0
        for n in idx:
            sentence = sentences[n]
            words = narration_timing.tokenize(sentence)
            line = NarrationLine(t=round(t, 6), text=sentence, source=f"telling:{author_id}:s{n + 1}")
            if cue is not None and not lines:
                line.cue, line.cue_word = cue[0], words[0]
            lines.append(line)
            t += len(words) * narration_timing.WORD_SECONDS + LINE_GAP
        if lines:
            by_shot[shot.id] = lines
    return by_shot


def narrate_as_teller(screenplay: Screenplay, teller_id: str, text: str, *, cued: bool = True) -> Screenplay:
    """The screenplay narrated by `teller_id` alone, in the words of `text`. Pure.

    For a brand-new memory, whose only telling is the teller's: every action is pinned at the
    time the screenplay's current default narration gives it (so the shared action does not
    move), every shot's own `narration` becomes the teller's lines (one per sentence of
    `text`, spread over the shots in order, source "telling:<teller>:s<n>", n 1-based over
    the whole text; with `cued`, a shot's first line is cued on its first word to the shot's
    earliest nameable action, as in `telling_narration`), and `narrations` is None, since
    nobody else has told it yet. Then `narration_timing.default_author(result) == teller_id`.

    A text with no spoken word gives no lines: the shots' narration is empty and, so that the
    teller is still the default narrator (`default_author` falls back to the first cast
    member), the teller moves to the front of `cast` when they are in it.
    """
    sp = screenplay.model_copy(deep=True)
    by_shot = _lines_over(sp, teller_id, text, cued=cued, pin=True)
    for shot in sp.shots:
        shot.narration = by_shot.get(shot.id, [])
    sp.narrations = None
    if not by_shot and teller_id in sp.cast:
        sp.cast = [teller_id, *(c for c in sp.cast if c != teller_id)]
    return sp


def _timing(track: NarrationTrack) -> list[tuple]:
    """Every line's text, timeline time and word timings: what one audio clip is cut to."""
    return [(line.text, line.t, [(w.word, w.t) for w in line.words or []]) for line in track.lines]


def keep_audio(old: Timeline, new: Timeline, author_id: str) -> Timeline:
    """`new` with the narration audio `old` had for every teller but `author_id` (whose words changed).

    A teller's audio is one clip from timeline t=0 (CONTRACTS clarification 5), so it is kept
    only while their lines are said at exactly the same timeline times. When the new telling
    makes an earlier shot longer, every later line moves, and a kept clip would play out of
    step with the captions and the action: that teller's audio is dropped (None) instead, to
    be synthesized again.
    """
    tl = new.model_copy(deep=True)
    old_tracks = old.narrations or {}
    for author, track in (tl.narrations or {}).items():
        before = old_tracks.get(author)
        if author != author_id and before is not None and before.audio is not None and _timing(before) == _timing(track):
            track.audio = before.audio
    default = tl.default_narrator
    if default == old.default_narrator and default != author_id and old.audio is not None:
        new_track = (tl.narrations or {}).get(default) if default else None
        old_track = old_tracks.get(default) if default else None
        same = (
            _timing(old_track) == _timing(new_track)
            if old_track is not None and new_track is not None
            else old.captions == tl.captions
        )
        if same:
            tl.audio = old.audio
    return tl


# ----------------------------------------------------------------------------- linker


def _askees(teller_id: str, participant_ids: list[str]) -> list[str]:
    """Named participants other than the teller, in order, once each."""
    return [pid for pid in _dedupe(participant_ids) if pid != teller_id]


def _first_name(name: str) -> str:
    words = name.split()
    return words[0] if words else ""


def merge_candidate(
    people: list[schemas.Person], teller_id: str, named: set[str] | list[str], text: str
) -> tuple[str, schemas.Person] | None:
    """The first "<title> <Name>" in `text` that may be someone already in the tree. Pure.

    ("Grandpa Joe", Joseph) when Name is capitalised, is nobody's first name in the family,
    and starts with the same two letters (ignoring case) as exactly one person's who is
    neither the teller nor already in `named` and who can carry that title
    (`_fits_title`). None otherwise: a lowercase word is not a name ("Grandma and Grandpa
    were there", "Grandpa liked jam"), and a Name that IS someone's first name ("Grandma
    Rosa", with Rosa in the tree) is that person, with nothing to ask.
    """
    firsts = {_first_name(p.name).casefold() for p in people}
    skip = {teller_id, *named}
    parents = {pid for p in people for pid in p.parent_ids}
    others = [p for p in people if p.id not in skip]
    for match in _KIN_NAME.finditer(text):
        title, said_name = match.group(1), match.group(2)
        key = said_name.casefold()
        if not said_name[0].isupper() or key in firsts or len(key) < 2:
            continue
        alike = [
            p for p in others
            if _first_name(p.name).casefold()[:2] == key[:2] and _fits_title(p, title, parents)
        ]
        if len(alike) == 1:
            return f"{title} {said_name}", alike[0]
    return None


def _gender_hint(person: schemas.Person) -> str | None:
    """"f", "m" or None, from what the record itself says: the words of its id
    ("grandma_rosa", "dad") and the pronouns of its character notes ("hands clasped behind
    his back"). None when it says nothing, or both. Never guessed from the name."""
    words = {w.casefold() for w in _WORD.findall(person.id.replace("_", " "))}
    words |= {w.casefold() for w in _WORD.findall(person.character.notes or "")}
    female, male = bool(words & _FEMALE_WORDS), bool(words & _MALE_WORDS)
    if female == male:
        return None
    return "f" if female else "m"


def _fits_title(person: schemas.Person, title: str, parents: set[str]) -> bool:
    """Whether `person` can be the one a family calls "<title> ...".

    A grandparent title needs someone with a child in the tree (never a child like Lily). A
    gendered title never maps to someone whose record says the other gender ("Grandma Lu"
    is not Luis); a record that says nothing is not ruled out. "Cousin" fits anyone.
    """
    key = title.casefold()
    if key in _ELDER_TITLES and person.id not in parents:
        return False
    wanted = "f" if key in _FEMALE_WORDS else "m" if key in _MALE_WORDS else None
    hint = _gender_hint(person)
    return wanted is None or hint is None or hint == wanted


def ask_participants(
    db: Database, family_id: str, memory: schemas.Memory, teller_id: str, participant_ids: list[str]
) -> list[schemas.Question]:
    """The linker: one open question per named participant (never the teller) for `memory`.

    A person who has died (agent/prompts/linker.md: their record has `died`), who is not in
    the family, or who already has an open question for this memory is skipped, so running
    it again creates nothing new. A failure for one person is logged and skipped. Returns
    the questions created.
    """
    askees = _askees(teller_id, participant_ids)
    if not askees:
        return []
    teller = people_db.get_person(db, family_id, teller_id)
    teller_name = teller.name if teller is not None else "Someone"
    text = f'{teller_name} told the story of "{memory.title}". What do you remember?'
    living = {p.id for p in people_db.get_people(db, family_id, askees) if p.died is None}
    created = []
    for pid in askees:
        if pid not in living:
            continue
        try:
            if any(q.memory_id == memory.id for q in memories_db.open_questions(db, family_id, pid)):
                continue
            created.append(memories_db.create_question(db, family_id, person_id=pid, text=text, memory_id=memory.id))
        except Exception:  # noqa: BLE001 - a missing question never fails the run
            logger.exception("memory %s: could not ask %s for their version", memory.id, pid)
    return created


# --------------------------------------------------------------------------- manager


class ScriptedRunManager:
    """A scripted stand-in for agent.runs.RunManager. Thread-safe; one per API process."""

    def __init__(
        self,
        *,
        db_getter: Callable[[], Database] = db_client.get_db,
        step_seconds: float = 0.6,
        ask_followup: bool = True,
        not_waiting: type[Exception] = RunNotWaiting,
    ) -> None:
        """`not_waiting`: what `answer_run` raises when the run is not paused; it must be a
        ValueError (install_fake_runs passes agent.runs.RunNotWaiting)."""
        if not (isinstance(not_waiting, type) and issubclass(not_waiting, ValueError)):
            raise TypeError("not_waiting must be a ValueError subclass")
        self._db_getter = db_getter
        self._step_seconds = max(0.0, float(step_seconds))
        self._ask_followup = ask_followup
        self._not_waiting = not_waiting
        self._runs: dict[str, _Run] = {}
        self._threads: list[threading.Thread] = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._family_locks: dict[str, threading.Lock] = {}

    # ------------------------------------------------------------------ public API

    def start_run(
        self,
        family_id: str,
        user_id: str,
        person_id: str,
        fields: schemas.SubmitMemoryFields,
        photo_ids: list[str],
    ) -> str:
        """Start a scripted run and return its id at once.

        ValueError: `person_id` (or a participant) is not a person of the family.
        MemoryNotFound (a LookupError): `fields.memory_id` is set but is not a memory of the
        family, or that memory has no screenplay or timeline yet (agent.runs semantics).
        """
        if self._stop.is_set():
            raise RuntimeError("the scripted studio has been shut down")
        db = self._db_getter()
        if people_db.get_person(db, family_id, person_id) is None:
            raise ValueError(f"person {person_id!r} is not in this family")
        if fields.participant_ids:
            found = {p.id for p in people_db.get_people(db, family_id, fields.participant_ids)}
            missing = [pid for pid in fields.participant_ids if pid not in found]
            if missing:
                raise ValueError(f"participants not in this family: {', '.join(missing)}")
        if fields.memory_id is not None:
            memory = memories_db.get_memory(db, family_id, fields.memory_id)
            if memory is None or memory.screenplay is None or memory.timeline is None:
                raise MemoryNotFound(f"memory {fields.memory_id!r} not found, or it has no scene yet")

        run = _Run(
            run_id=uuid.uuid4().hex,
            family_id=family_id,
            user_id=user_id,
            person_id=person_id,
            fields=fields,
            photo_ids=list(photo_ids),
        )
        thread = threading.Thread(
            target=self._drive, args=(run, db), name=f"scripted-run-{run.run_id[:8]}", daemon=True
        )
        with self._lock:
            self._runs[run.run_id] = run
            self._threads = [t for t in self._threads if t.is_alive()] + [thread]
        thread.start()
        return run.run_id

    def stream_run(self, family_id: str, run_id: str) -> AsyncIterator[Event]:
        """Every event from the first, then live ones, until done or error.

        LookupError is raised by this call itself for an unknown or other-family run.
        """
        run = self._get(family_id, run_id)
        return self._follow(run)

    def answer_run(self, family_id: str, run_id: str, user_id: str, answer: schemas.AnswerRequest) -> None:
        """Resume a paused run. Returns at once.

        LookupError: unknown run or another family. PermissionError: not the user who
        started it. `not_waiting` (RunNotWaiting, a ValueError): not paused. ValueError: the
        answer does not fit the pending event; the run stays paused.
        """
        run = self._get(family_id, run_id)
        if run.user_id != user_id:
            raise PermissionError("only the person who started this run can answer it")
        with run.cond:
            if run.status != "waiting" or run.pending is None:
                raise self._not_waiting(f"run {run_id} is {run.status}, not waiting for an answer")
            expected = NEEDS_INPUT[run.pending]
            if answer.kind != expected:
                raise ValueError(f"run {run_id} is waiting for kind={expected!r}, got {answer.kind!r}")
            if answer.kind == "answer" and (answer.text is None or not answer.text.strip()):
                raise ValueError("an answer needs text")
            if answer.kind == "props" and answer.props is None:
                raise ValueError("a props review needs the props list")
            if answer.kind == "merge" and answer.approve is None:
                raise ValueError("a merge answer needs approve")
            run.answer = answer
            run.pending = None
            run.status = "running"
            run.notify()

    def status(self, family_id: str, run_id: str) -> schemas.RunStatus:
        run = self._get(family_id, run_id)
        with run.cond:
            return run.status

    def events(self, family_id: str, run_id: str) -> list[Event]:
        """A snapshot of the run's events so far (tests and debugging)."""
        run = self._get(family_id, run_id)
        with run.cond:
            return list(run.events)

    def wait(self, family_id: str, run_id: str, timeout: float | None = None) -> schemas.RunStatus:
        """Block until the run is not running (waiting, done or error) or `timeout` passes. Tests only."""
        run = self._get(family_id, run_id)
        with run.cond:
            run.cond.wait_for(lambda: run.status != "running", timeout)
            return run.status

    def shutdown(self) -> None:
        """Stop every run in flight (each ends with an error event) and refuse new ones."""
        self._stop.set()
        with self._lock:
            runs = list(self._runs.values())
            threads = list(self._threads)
        for run in runs:
            with run.cond:
                run.notify()
        for thread in threads:
            thread.join(timeout=2.0)

    # ------------------------------------------------------------------ internals

    def _family_lock(self, family_id: str) -> threading.Lock:
        """Held while a run looks up and creates the family's copies of the sample's set and prop."""
        with self._lock:
            return self._family_locks.setdefault(family_id, threading.Lock())

    def _get(self, family_id: str, run_id: str) -> _Run:
        with self._lock:
            run = self._runs.get(run_id)
        if run is None or run.family_id != family_id:
            raise LookupError(f"run {run_id!r} not found")
        return run

    async def _follow(self, run: _Run) -> AsyncIterator[Event]:
        loop = asyncio.get_running_loop()
        wake = asyncio.Event()
        with run.cond:
            run.subscribers.append((loop, wake))
        index = 0
        try:
            while True:
                wake.clear()
                with run.cond:
                    batch = run.events[index:]
                    finished = run.terminal
                for event in batch:
                    yield event
                index += len(batch)
                if finished:
                    return
                if not batch:
                    await wake.wait()
        finally:
            with run.cond:
                if (loop, wake) in run.subscribers:
                    run.subscribers.remove((loop, wake))

    def _emit(self, run: _Run, name: str, payload: BaseModel) -> None:
        expected = schemas.RUN_EVENTS[name]
        if not isinstance(payload, expected):
            raise TypeError(f"{name} needs a {expected.__name__}, got {type(payload).__name__}")
        with run.cond:
            if run.terminal:
                logger.warning("run %s: dropped %s after the run ended", run.run_id, name)
                return
            run.events.append((name, payload))
            if name in TERMINAL:
                run.status = name  # type: ignore[assignment]
            elif name in NEEDS_INPUT:
                run.status = "waiting"
                run.pending = name
            run.notify()

    def _step(self) -> None:
        """Pause between steps; wakes early (and stops the script) on shutdown."""
        stopping = self._stop.wait(self._step_seconds) if self._step_seconds > 0 else self._stop.is_set()
        if stopping:
            raise _Stopped

    def _ask(self, run: _Run, name: str, payload: BaseModel) -> schemas.AnswerRequest:
        """Emit a needs-input event and block this run's thread until it is answered."""
        self._emit(run, name, payload)
        with run.cond:
            run.cond.wait_for(lambda: run.answer is not None or self._stop.is_set())
            if run.answer is None:
                raise _Stopped
            answer, run.answer = run.answer, None
            return answer

    def _drive(self, run: _Run, db: Database) -> None:
        """Play the script. Never raises: any failure becomes the run's one error event."""
        try:
            if run.fields.memory_id is None:
                self._new_memory(run, db)
            else:
                self._second_telling(run, db)
        except _Stopped:
            self._emit(run, "error", schemas.ErrorEvent(message=STOPPED_MESSAGE))
        except Exception:  # noqa: BLE001 - every failure becomes the run's error event
            logger.exception("scripted run %s failed", run.run_id)
            self._emit(run, "error", schemas.ErrorEvent(message=ERROR_MESSAGE))
        else:
            with run.cond:
                ended = run.terminal
            if not ended:  # defensive: a script must always end the run
                self._emit(run, "error", schemas.ErrorEvent(message=ERROR_MESSAGE))

    # ------------------------------------------------------------------ the scripts

    @staticmethod
    def _plan(items: list[str], done: int) -> schemas.PlanEvent:
        """Items before `done` completed, item `done` in progress, the rest pending."""
        return schemas.PlanEvent(
            todos=[
                schemas.PlanItem(
                    text=text,
                    status="completed" if i < done else "in_progress" if i == done else "pending",
                )
                for i, text in enumerate(items)
            ]
        )

    def _new_memory(self, run: _Run, db: Database) -> None:
        fields = run.fields
        askees = _askees(run.person_id, fields.participant_ids)
        todos = ["Read the telling"]
        if self._ask_followup:
            todos.append("Ask one follow-up")
        todos += ["Find the props", "Stage the scene", "Narrate"]
        if askees:  # a new memory has no questions yet, so every named participant gets one
            todos.append(ASK_OTHERS)
        at = {name: i for i, name in enumerate(todos)}

        self._emit(run, "plan", self._plan(todos, 0))
        self._step()
        self._emit(run, "subagent_start", schemas.SubagentStartEvent(
            name="interviewer", task="Read the telling and find what is missing"))
        self._step()
        self._emit(run, "subagent_end", schemas.SubagentEndEvent(
            name="interviewer",
            summary="One follow-up: who else was there." if self._ask_followup else "The telling is complete.",
        ))
        self._step()

        text = fields.text
        if self._ask_followup:
            reply = self._ask(run, "question", schemas.QuestionEvent(text=FOLLOWUP_QUESTION))
            if reply.text and reply.text.strip():
                text = f"{text.rstrip()}\n\n{reply.text.strip()}"
            self._step()

        # Is "Grandpa Joe" the Joseph in the tree? Approved, he is named like a participant.
        named = _dedupe(list(fields.participant_ids))
        merged = self._merge(run, db, named, text)
        if merged is not None:
            named.append(merged.id)
            if merged.died is None and ASK_OTHERS not in todos:  # the linker will ask them
                todos.append(ASK_OTHERS)
                at[ASK_OTHERS] = len(todos) - 1
        askees = _askees(run.person_id, named)

        # Props: review the sample's prop, as this family's copy.
        sample = demo.load_sample()
        sample_prop = sample.props[0]
        prop_id = self._planned_id(props_db.get_prop, db, run.family_id, sample_prop.id)
        current = props_db.get_prop(db, run.family_id, prop_id)
        reused = current is not None and current.family_id == run.family_id
        shown = current if reused else sample_prop
        self._emit(run, "plan", self._plan(todos, at["Find the props"]))
        self._step()
        self._emit(run, "subagent_start", schemas.SubagentStartEvent(
            name="prop_master", task="Find the props in the telling"))
        self._step()
        chip = schemas.PropChip(
            id=prop_id,
            name=shown.name,
            description=shown.description,
            size=shown.size,
            art=shown.art.get("body"),
            reused=reused,
        )
        review = self._ask(run, "props_found", schemas.PropsFoundEvent(props=[chip]))
        edits = [e for e in (review.props or []) if e.id == prop_id]
        removed = any(e.action == "remove" for e in edits)
        if removed:
            summary = f"Left out the {chip.name}."
        elif reused:
            summary = f"Reused the {chip.name}."
        else:
            summary = f"Saved the {chip.name}."
        self._emit(run, "subagent_end", schemas.SubagentEndEvent(name="prop_master", summary=summary))
        self._step()

        self._emit(run, "plan", self._plan(todos, at["Stage the scene"]))
        self._step()
        self._emit(run, "subagent_start", schemas.SubagentStartEvent(
            name="director", task="Stage the scene on the church steps"))
        self._step()
        staged = self._stage(run, db, sample, edits, removed, text, named)
        result = staged.result
        if not result.ok or result.timeline is None:
            errors = result.errors or [CompileError(code="no_timeline", message="the compiler made no timeline")]
            logger.error(
                "scripted run %s: the scene did not compile: %s", run.run_id, "; ".join(e.message for e in errors)
            )
            self._emit(run, "compile_error", schemas.CompileErrorEvent(errors=errors, round=1))
            self._emit(run, "subagent_end", schemas.SubagentEndEvent(
                name="director", summary=f"The scene did not compile: {errors[0].message}"))
            self._emit(run, "error", schemas.ErrorEvent(message=SCENE_FAILED_MESSAGE))
            return
        shots = len(result.timeline.shots)
        self._emit(run, "subagent_end", schemas.SubagentEndEvent(
            name="director",
            summary=f"{shots} shot{'' if shots == 1 else 's'}, {result.timeline.duration:.1f}s, compiled without errors.",
        ))
        self._step()
        self._emit(run, "plan", self._plan(todos, at["Narrate"]))
        self._step()

        memory = self._save(run, db, staged, text)

        # The linker runs when its step is planned: for named people, and for a merged person
        # who is alive (approving someone who has died links them, but nobody is asked).
        if ASK_OTHERS in at:
            self._emit(run, "plan", self._plan(todos, at[ASK_OTHERS]))
            self._step()
            try:
                ask_participants(db, run.family_id, memory, run.person_id, askees)
            except Exception:  # noqa: BLE001 - the linker never fails the run
                logger.exception("run %s: the linker failed; no questions were asked", run.run_id)

        self._emit(run, "plan", self._plan(todos, len(todos)))
        self._step()
        self._emit(run, "scene_ready", schemas.SceneReadyEvent(memory_id=memory.id, version=memory.version))
        self._step()
        self._emit(run, "done", schemas.DoneEvent(memory_id=memory.id))

    def _second_telling(self, run: _Run, db: Database) -> None:
        fields = run.fields
        memory_id = fields.memory_id
        assert memory_id is not None
        fid, teller = run.family_id, run.person_id
        existing = memories_db.get_memory(db, fid, memory_id)
        if existing is None or existing.screenplay is None or existing.timeline is None:
            raise MemoryNotFound(f"memory {memory_id!r} not found, or it has no scene yet")
        told = {t.author_id for t in existing.tellings}
        # The linker's people (agent/prompts/studio.md, second telling): named participants
        # new to the moment. All of them are linked; those who already told it are not asked.
        newcomers = [pid for pid in _askees(teller, fields.participant_ids) if pid not in existing.participant_ids]
        todos = ["Read the telling", "Write the narration", "Add it to the moment"]
        if newcomers:
            todos.append(ASK_OTHERS)
        at = {name: i for i, name in enumerate(todos)}

        self._emit(run, "plan", self._plan(todos, 0))
        self._step()
        self._emit(run, "subagent_start", schemas.SubagentStartEvent(
            name="interviewer", task="Read the new telling of this moment"))
        self._step()
        self._emit(run, "subagent_end", schemas.SubagentEndEvent(
            name="interviewer", summary="The telling is ready to narrate."))
        self._step()

        # The narrator writes this teller's lines over the shared action; the scene is recompiled.
        self._emit(run, "plan", self._plan(todos, at["Write the narration"]))
        self._step()
        self._emit(run, "subagent_start", schemas.SubagentStartEvent(
            name="narrator", task="Write the new teller's narration over the scene"))
        self._step()
        screenplay, result = self._narrate(db, fid, existing.screenplay, teller, fields.text)
        lines = sum(len(by_shot) for by_shot in self._lines_of(screenplay, teller).values())
        if lines == 0:
            # Nothing to narrate (a blank text): never a telling without its track.
            logger.error("scripted run %s: the second telling has nothing to narrate", run.run_id)
            self._emit(run, "subagent_end", schemas.SubagentEndEvent(
                name="narrator", summary="There was nothing to narrate."))
            self._emit(run, "error", schemas.ErrorEvent(message=TELLING_FAILED_MESSAGE))
            return
        if not result.ok or result.timeline is None:
            errors = result.errors or [CompileError(code="no_timeline", message="the compiler made no timeline")]
            logger.error(
                "scripted run %s: the second telling did not compile: %s",
                run.run_id, "; ".join(e.message for e in errors),
            )
            self._emit(run, "compile_error", schemas.CompileErrorEvent(errors=errors, round=1))
            self._emit(run, "subagent_end", schemas.SubagentEndEvent(
                name="narrator", summary=f"The narration did not compile: {errors[0].message}"))
            self._emit(run, "error", schemas.ErrorEvent(message=TELLING_FAILED_MESSAGE))
            return
        self._emit(run, "subagent_end", schemas.SubagentEndEvent(
            name="narrator", summary=f"{lines} line{'' if lines == 1 else 's'}, compiled without errors."))
        self._step()

        self._emit(run, "plan", self._plan(todos, at["Add it to the moment"]))
        self._step()
        telling = schemas.Telling(
            author_id=teller,
            question=fields.question,
            text=fields.text,
            photo_ids=list(run.photo_ids),
        )
        if memories_db.add_telling(db, fid, memory_id, telling) is None:
            raise MemoryNotFound(f"memory {memory_id!r} not found")
        # agent/tools/family.py _add_telling: the teller joins the moment.
        memories_db.link_participants(db, fid, memory_id, [teller])
        timeline = keep_audio(existing.timeline, result.timeline, teller)
        saved = memories_db.save_scene(
            db, fid, memory_id,
            screenplay=screenplay, timeline=timeline,
            prop_ids=existing.prop_ids, set_ids=existing.set_ids,
        )
        if saved is None:
            raise MemoryNotFound(f"memory {memory_id!r} vanished before its scene was saved")

        if newcomers:
            # The linker: add the named newcomers to the moment and ask each for their version.
            self._emit(run, "plan", self._plan(todos, at[ASK_OTHERS]))
            self._step()
            try:
                linked = memories_db.link_participants(db, fid, memory_id, newcomers)
                ask_participants(db, fid, linked or saved, teller, [p for p in newcomers if p not in told])
            except Exception:  # noqa: BLE001 - the linker never fails the run
                logger.exception("run %s: the linker failed; no one new was added", run.run_id)
            saved = memories_db.get_memory(db, fid, memory_id) or saved

        self._emit(run, "plan", self._plan(todos, len(todos)))
        self._step()
        self._emit(run, "scene_ready", schemas.SceneReadyEvent(memory_id=memory_id, version=saved.version))
        self._step()
        self._emit(run, "done", schemas.DoneEvent(memory_id=memory_id))

    def _merge(self, run: _Run, db: Database, named: list[str], text: str) -> schemas.Person | None:
        """Ask whether a "<title> <Name>" of the telling is someone in the tree (`merge_candidate`).

        Returns that person when the family approves, None when they reject or there is
        nothing to ask.
        """
        found = merge_candidate(people_db.list_people(db, run.family_id), run.person_id, named, text)
        if found is None:
            return None
        said, person = found
        reply = self._ask(run, "merge", schemas.MergeEvent(
            text=MERGE_QUESTION.format(said=said, candidate=person.name),
            person_id=person.id,
            candidate_name=person.name,
        ))
        self._step()
        return person if reply.approve else None

    @staticmethod
    def _lines_of(screenplay: Screenplay, author_id: str) -> dict[str, list[NarrationLine]]:
        """`author_id`'s lines by shot, wherever they are (narrations, or the shots' own)."""
        if screenplay.narrations and author_id in screenplay.narrations:
            return screenplay.narrations[author_id]
        return {shot.id: shot.narration for shot in screenplay.shots if shot.narration}

    @staticmethod
    def _narrate(
        db: Database,
        family_id: str,
        screenplay: Screenplay,
        author_id: str,
        text: str,
        write: Callable[..., Screenplay] = telling_narration,
    ) -> tuple[Screenplay, CompileResult]:
        """The teller's narration over the scene (`write`: `telling_narration` for a second
        telling, `narrate_as_teller` for a new memory), compiled. Cued first; uncued if cues fail."""
        drafted = write(screenplay, author_id, text)
        result = compile_scene(db, family_id, drafted)
        if not result.ok:
            plain = write(screenplay, author_id, text, cued=False)
            plain_result = compile_scene(db, family_id, plain)
            if plain_result.ok:
                return plain, plain_result
        return drafted, result

    # ------------------------------------------------------------------ saving

    @staticmethod
    def _planned_id(get: Callable[[Database, str, str], Any], db: Database, family_id: str, sample_id: str) -> str:
        """The sample id if this family already has it, otherwise a family-specific copy id.

        Document ids are unique across families, so a family other than the one holding
        the sample id gets `<sampleId>_<familyId>`.
        """
        found = get(db, family_id, sample_id)
        if found is not None and found.family_id == family_id:
            return sample_id
        return f"{sample_id}_{family_id}"

    @staticmethod
    def _ensure(
        get: Callable[[Database, str, str], Any],
        insert: Callable[[str | None], Any],
        db: Database,
        family_id: str,
        doc_id: str,
    ) -> str:
        """Make sure the family has a copy under `doc_id` (or under a fresh id if that one is taken)."""
        found = get(db, family_id, doc_id)
        if found is not None and found.family_id == family_id:
            return doc_id
        try:
            return insert(doc_id).id
        except ValueError:  # the id belongs to another family
            return insert(None).id

    def _people_map(self, run: _Run, db: Database, named: list[str]) -> dict[str, str | None]:
        """Sample person id -> this family's person id.

        `dad` is the first of `named` (the named participants, then an approved merge) who
        is not the teller, else the teller's spouse, else anyone else. SAMPLE_OTHER maps to
        None when the family has nobody but the teller: the two sample puppets are never
        merged into one person.
        """
        teller = run.person_id
        other = next((p for p in named if p != teller), None)
        if other is None:
            people = people_db.list_people(db, run.family_id)
            me = next((p for p in people if p.id == teller), None)
            spouses = [s for s in (me.spouse_ids if me else []) if s != teller and any(p.id == s for p in people)]
            others = [p.id for p in people if p.id != teller]
            other = next(iter(spouses + others), None)
        return {SAMPLE_TELLER: teller, SAMPLE_OTHER: other}

    def _stage(
        self,
        run: _Run,
        db: Database,
        sample: schemas.MemoryResponse,
        edits: list[schemas.PropEdit],
        removed: bool,
        text: str,
        named: list[str],
    ) -> _Staged:
        """The director: the sample's screenplay recast for this family, narrated by the
        teller in their own words (`text`, the telling as `_save` stores it), and compiled.

        Creates the family's copies of the sample's set and prop when missing (and applies the
        prop review's edits), so the compiler sees exactly what the memory will point at.
        """
        fid = run.family_id
        sample_memory = sample.memory
        assert sample_memory.screenplay is not None and sample_memory.timeline is not None

        people = self._people_map(run, db, named)
        other = people[SAMPLE_OTHER]
        participants = _dedupe([run.person_id, *([other] if other else []), *named])

        # The family's set (and prop, unless the family left it out). One run at a time per
        # family: two runs that both find no copy must not both insert one.
        ids: dict[str, str] = {sample_id: pid for sample_id, pid in people.items() if pid is not None}
        set_ids: list[str] = []
        prop_ids: list[str] = []
        removed_ids: list[str] = []
        sample_prop = sample.props[0]
        with self._family_lock(fid):
            for set_def in sample.sets:
                planned = self._planned_id(props_db.get_set, db, fid, set_def.id)
                ids[set_def.id] = self._ensure(
                    props_db.get_set,
                    lambda doc_id, s=set_def: props_db.insert_set(db, fid, s, set_id=doc_id),
                    db, fid, planned,
                )
                set_ids.append(ids[set_def.id])
            for prop in sample.props:
                if prop is sample_prop and removed:
                    removed_ids.append(prop.id)
                    continue
                planned = self._planned_id(props_db.get_prop, db, fid, prop.id)
                final = self._ensure(
                    props_db.get_prop,
                    lambda doc_id, p=prop: props_db.insert_prop(db, fid, p, prop_id=doc_id),
                    db, fid, planned,
                )
                ids[prop.id] = final
                prop_ids.append(final)
                if prop is sample_prop:
                    self._apply_edits(db, fid, final, edits)

        # Stage on the sample's own ids, strip what this family leaves out, then recast. The
        # sample's timeline is only carried through the strips; the compiler makes the real one.
        screenplay = sample_memory.screenplay.model_dump(mode="json", by_alias=True, exclude_none=True)
        timeline = sample_memory.timeline.model_dump(mode="json", by_alias=True, exclude_none=True)
        screenplay = stage_handoffs(screenplay, {s.id: dict(s.marks) for s in sample.sets})
        for pid in removed_ids:
            screenplay, timeline = strip_prop(screenplay, timeline, pid)
        if other is None:  # a family of one: the teller plays alone
            screenplay, timeline = strip_person(screenplay, timeline, SAMPLE_OTHER, keep_narrator=SAMPLE_TELLER)
        screenplay = remap_ids(screenplay, ids)
        screenplay["cast"] = _dedupe(screenplay.get("cast", []))
        screenplay["props"] = _dedupe(screenplay.get("props", []))
        # The sample's narration is its own people's words: the teller's telling replaces it,
        # and nobody else has a track (the recast other person has not told this memory).
        # The sample is timed for its own people: a slower cast gets a slower pace.
        recast = Screenplay.model_validate(screenplay)
        tries: list[tuple[Screenplay, CompileResult]] = []
        for factor in PACE_FACTORS:
            tries.append(self._narrate(db, fid, pace(recast, factor), run.person_id, text, write=narrate_as_teller))
            result = tries[-1][1]
            if result.ok or not any(e.code == "busy" for e in result.errors):
                break
        # A scene that never compiles is reported as written, not at the last pace tried.
        narrated, result = tries[-1] if tries[-1][1].ok else tries[0]
        return _Staged(
            screenplay=narrated,
            result=result,
            participants=participants,
            prop_ids=prop_ids,
            set_ids=set_ids,
        )

    def _save(self, run: _Run, db: Database, staged: _Staged, text: str) -> schemas.Memory:
        """Create the memory with the teller's telling, then store the staged screenplay and
        the timeline the compiler made from it."""
        fid = run.family_id
        fields = run.fields
        timeline = staged.result.timeline
        assert staged.result.ok and timeline is not None
        memory = memories_db.create_memory(
            db,
            fid,
            title=fields.title or short_title(fields.text),
            year=fields.year,
            participant_ids=staged.participants,
            telling=schemas.Telling(
                author_id=run.person_id,
                question=fields.question,
                text=text,
                photo_ids=list(run.photo_ids),
            ),
            prop_ids=staged.prop_ids,
            set_ids=staged.set_ids,
        )
        saved = memories_db.save_scene(
            db,
            fid,
            memory.id,
            screenplay=staged.screenplay.model_copy(update={"memory_id": memory.id}, deep=True),
            timeline=timeline,
            prop_ids=staged.prop_ids,
            set_ids=staged.set_ids,
        )
        if saved is None:
            raise LookupError(f"memory {memory.id!r} vanished before its scene was saved")
        return saved

    @staticmethod
    def _apply_edits(db: Database, family_id: str, prop_id: str, edits: list[schemas.PropEdit]) -> None:
        for edit in edits:
            if edit.action == "rename" and edit.name and edit.name.strip():
                props_db.update_prop(db, family_id, prop_id, schemas.PropPatch(name=edit.name.strip()))
            elif edit.action == "replace_photo" and edit.photo_id:
                media_id = media_db.parse_media_ref(edit.photo_id)
                if media_id is not None and media_db.media_exists(db, family_id, media_id):
                    patch = schemas.PropPatch(photo_id=media_db.media_ref(media_id))
                    props_db.update_prop(db, family_id, prop_id, patch)
                else:
                    logger.warning("prop %s: ignored replace_photo with unknown photo %r", prop_id, edit.photo_id)


# --------------------------------------------------------------------------- install


def install_fake_runs(*, step_seconds: float = 0.6) -> bool:
    """Put a ScriptedRunManager behind `agent.runs` (via its `configure`). True on success.

    Returns False, with a warning, when agent.runs cannot be imported or has no callable
    `configure`; the run routes then answer 503 as usual.
    """
    try:
        runs = importlib.import_module("agent.runs")
    except Exception as exc:  # noqa: BLE001 - any import failure means no scripted runs
        logger.warning("scripted runs not installed: agent.runs cannot be imported (%s)", exc)
        return False
    configure = getattr(runs, "configure", None)
    if not callable(configure):
        logger.warning("scripted runs not installed: agent.runs has no callable configure()")
        return False
    not_waiting = getattr(runs, "RunNotWaiting", None)
    if not (isinstance(not_waiting, type) and issubclass(not_waiting, ValueError)):
        not_waiting = RunNotWaiting
    configure(ScriptedRunManager(step_seconds=step_seconds, not_waiting=not_waiting))
    logger.info("agent runs are scripted (seed.fake_runs), step %.2fs", step_seconds)
    return True


__all__ = [
    "MemoryNotFound",
    "RunNotWaiting",
    "ScriptedRunManager",
    "ask_participants",
    "compile_scene",
    "install_fake_runs",
    "keep_audio",
    "merge_candidate",
    "narrate_as_teller",
    "remap_ids",
    "scene_inputs",
    "short_title",
    "split_sentences",
    "stage_handoffs",
    "strip_person",
    "strip_prop",
    "telling_narration",
]
