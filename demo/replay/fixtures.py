"""Prepared stories for replay mode: loading, checking and matching them. No database, no network.

A replay directory (STITCH_REPLAY_DIR, default ~/stitch-private/replays) holds one folder per
prepared story. Each folder has a `story.json`:

    {
      "match": {"text": "<the telling as it will be typed>", "keywords": ["tamales"], "minOverlap": 0.8},
      "title": "Sunday tamales at Carmen's",      optional: the saved memory's title
      "year": 1970,                               optional: the saved memory's year
      "question": "Who else was in the kitchen?", optional: the follow-up question asked
      "familyId": "demo_family",                  optional: only tellings in this family match
      "reviewProps": ["tamale_pot"],              optional: the props shown in the props review
      "screenplayFile": "screenplay.json",
      "propsFile": "props.json",                  optional
      "setsFile": "sets.json"                     optional
    }

and the files it names, in the same formats as api/seed/demo_family/ (a Screenplay, and JSON
lists of Prop and SetDef). File names are relative to the story folder. A directory that has
a story.json itself is one story.

Matching (`match_story`): both texts are normalized (Unicode NFKC, case folded, apostrophes
dropped, every other punctuation mark a space, whitespace collapsed). An exact match wins;
else the token overlap (Dice over the two word multisets: 2 * shared words / all words) must
reach `minOverlap`; else, when `keywords` are given, every keyword (a word or a phrase) must
appear. The best match wins: exact, then overlap, then keywords, ties by higher overlap,
then by story name.

Nothing here logs or copies a story's text: logs name the story folder only.
"""

from __future__ import annotations

import json
import os
import re
import unicodedata
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from pydantic.alias_generators import to_camel

import schemas
from screenplay import compiler
from screenplay.schema import Screenplay, Timeline

REPLAY_DIR_ENV = "STITCH_REPLAY_DIR"
DEFAULT_REPLAY_DIR = Path("~/stitch-private/replays")
STORY_FILE = "story.json"
DEFAULT_MIN_OVERLAP = 0.8

MatchKind = Literal["exact", "overlap", "keywords"]
_KIND_RANK: dict[str, int] = {"exact": 3, "overlap": 2, "keywords": 1}


class ReplayFixtureError(ValueError):
    """A prepared story is malformed or does not compile. The message starts with its name."""

    def __init__(self, story: str, message: str) -> None:
        super().__init__(f"prepared story {story!r}: {message}")
        self.story = story


# ------------------------------------------------------------------------------ story.json


class _Strict(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")


class MatchRule(_Strict):
    text: str
    keywords: list[str] = Field(default_factory=list)
    min_overlap: float = Field(default=DEFAULT_MIN_OVERLAP, gt=0, le=1)

    @field_validator("text")
    @classmethod
    def _has_words(cls, value: str) -> str:
        if not normalize(value):
            raise ValueError("match.text has no words")
        return value

    @field_validator("keywords")
    @classmethod
    def _keywords_have_words(cls, value: list[str]) -> list[str]:
        if any(not normalize(k) for k in value):
            raise ValueError("every keyword needs a word")
        return value


class StoryFile(_Strict):
    match: MatchRule
    title: str | None = None
    year: int | None = None
    question: str | None = None
    family_id: str | None = None
    review_props: list[str] | None = None
    screenplay_file: str
    props_file: str | None = None
    sets_file: str | None = None


@dataclass(frozen=True)
class PreparedStory:
    """One prepared story, loaded and parsed (not yet compiled: see `validate_story`)."""

    name: str
    folder: Path
    rule: MatchRule
    title: str | None
    year: int | None
    question: str | None
    family_id: str | None
    review_props: tuple[str, ...] | None
    screenplay: Screenplay
    props: tuple[schemas.Prop, ...]
    sets: tuple[schemas.SetDef, ...]

    @property
    def normalized(self) -> str:
        return normalize(self.rule.text)

    def prop(self, prop_id: str) -> schemas.Prop | None:
        return next((p for p in self.props if p.id == prop_id), None)

    def set(self, set_id: str) -> schemas.SetDef | None:
        return next((s for s in self.sets if s.id == set_id), None)

    def set_ids(self) -> list[str]:
        """The screenplay's sets, in shot order, once each."""
        return list(dict.fromkeys(shot.set for shot in self.screenplay.shots))


# ------------------------------------------------------------------------------ loading


def replay_dir(environ: Mapping[str, str] = os.environ) -> tuple[Path, bool]:
    """(the replay directory, whether STITCH_REPLAY_DIR named it). `~` is expanded."""
    raw = (environ.get(REPLAY_DIR_ENV) or "").strip()
    path = Path(raw) if raw else DEFAULT_REPLAY_DIR
    return path.expanduser().resolve(), bool(raw)


def _read_json(story: str, path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ReplayFixtureError(story, f"{path.name} not found in {path.parent}") from exc
    except json.JSONDecodeError as exc:
        raise ReplayFixtureError(
            story, f"{path.name}: invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}"
        ) from exc


def _models(story: str, path: Path, model: type[schemas.Prop] | type[schemas.SetDef]) -> list:
    raw = _read_json(story, path)
    if not isinstance(raw, list):
        raise ReplayFixtureError(story, f"{path.name}: expected a JSON list of {model.__name__} objects")
    out = []
    for i, item in enumerate(raw):
        try:
            out.append(model.model_validate(item))
        except ValidationError as exc:
            ident = item.get("id") if isinstance(item, dict) else None
            raise ReplayFixtureError(story, f"{path.name}: item {i} ({ident!r}) is not a valid {model.__name__}:\n{exc}") from exc
    ids = [m.id for m in out]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        raise ReplayFixtureError(story, f"{path.name}: duplicate ids {', '.join(dupes)}")
    return out


def load_story(folder: Path, name: str | None = None) -> PreparedStory:
    """One story folder. Raises ReplayFixtureError (naming the story) for anything malformed."""
    name = name or folder.name
    raw = _read_json(name, folder / STORY_FILE)
    try:
        spec = StoryFile.model_validate(raw)
    except ValidationError as exc:
        raise ReplayFixtureError(name, f"{STORY_FILE} is not valid:\n{exc}") from exc
    sp_path = folder / spec.screenplay_file
    try:
        screenplay = Screenplay.model_validate(_read_json(name, sp_path))
    except ValidationError as exc:
        raise ReplayFixtureError(name, f"{sp_path.name} is not a valid Screenplay:\n{exc}") from exc
    props = _models(name, folder / spec.props_file, schemas.Prop) if spec.props_file else []
    sets = _models(name, folder / spec.sets_file, schemas.SetDef) if spec.sets_file else []
    if spec.review_props is not None:
        stray = [pid for pid in spec.review_props if pid not in screenplay.props]
        if stray:
            raise ReplayFixtureError(name, f"reviewProps not in the screenplay's props: {', '.join(stray)}")
    return PreparedStory(
        name=name,
        folder=folder,
        rule=spec.match,
        title=spec.title.strip() if spec.title and spec.title.strip() else None,
        year=spec.year,
        question=spec.question.strip() if spec.question and spec.question.strip() else None,
        family_id=spec.family_id,
        review_props=tuple(dict.fromkeys(spec.review_props)) if spec.review_props is not None else None,
        screenplay=screenplay,
        props=tuple(props),
        sets=tuple(sets),
    )


def load_stories(directory: Path) -> list[PreparedStory]:
    """Every story in `directory` (one per sub-folder with a story.json, by folder name), or
    the directory itself when it has a story.json. Raises ReplayFixtureError for a bad story,
    and for two stories whose match texts are the same once normalized."""
    directory = Path(directory)
    if (directory / STORY_FILE).is_file():
        stories = [load_story(directory)]
    else:
        folders = sorted(p for p in directory.iterdir() if p.is_dir() and (p / STORY_FILE).is_file())
        stories = [load_story(folder) for folder in folders]
    seen: dict[str, str] = {}
    for story in stories:
        other = seen.setdefault(story.normalized, story.name)
        if other != story.name:
            raise ReplayFixtureError(story.name, f"its match text is the same as {other!r}'s")
    return stories


# ------------------------------------------------------------------------------ checking


def validate_story(
    story: PreparedStory,
    *,
    people: Mapping[str, schemas.Person],
    props: Mapping[str, schemas.Prop],
    sets: Mapping[str, schemas.SetDef],
) -> Timeline:
    """Compile the prepared screenplay, as authored, against a family. Returns its timeline.

    `people`, `props` and `sets` are what the family already has. The story's own props and
    sets fill in the ids the family lacks (a family that already has an id keeps its copy,
    as at run time). Raises ReplayFixtureError with every exact compile error otherwise.
    """
    sp = story.screenplay
    missing = [f"cast member {pid!r} is not a person of the family" for pid in sp.cast if pid not in people]
    all_props = {p.id: p for p in story.props} | dict(props)
    all_sets = {s.id: s for s in story.sets} | dict(sets)
    missing += [f"no prop {pid!r} in {story.name}'s props or the family's" for pid in sp.props if pid not in all_props]
    missing += [f"no set {sid!r} in {story.name}'s sets or the family's" for sid in story.set_ids() if sid not in all_sets]
    if not any(shot.narration for shot in sp.shots):
        missing.append("the screenplay has no narration (the teller narrates the shots' own narration lines)")
    if missing:
        raise ReplayFixtureError(story.name, "; ".join(missing))
    result = compiler.compile(
        sp,
        sets={sid: all_sets[sid] for sid in story.set_ids()},
        props={pid: all_props[pid] for pid in sp.props},
        people={pid: people[pid] for pid in sp.cast},
    )
    if not result.ok or result.timeline is None:
        lines = "\n".join(f"  - [{e.code}] {e.message}" for e in result.errors) or "  - no timeline"
        raise ReplayFixtureError(story.name, f"the screenplay does not compile:\n{lines}")
    return result.timeline


def validate_stories(
    stories: Iterable[PreparedStory],
    *,
    people: Mapping[str, schemas.Person],
    props: Mapping[str, schemas.Prop],
    sets: Mapping[str, schemas.SetDef],
) -> None:
    """`validate_story` for each story; the first failure raises."""
    for story in stories:
        validate_story(story, people=people, props=props, sets=sets)


# ------------------------------------------------------------------------------ matching

_APOSTROPHES = re.compile(r"['’‘`´]")
_NOT_WORD = re.compile(r"[\W_]+")


def normalize(text: str) -> str:
    """Case, punctuation and whitespace folded away: "It's  the POT!" -> "its the pot"."""
    text = unicodedata.normalize("NFKC", text or "").casefold()
    text = _APOSTROPHES.sub("", text)
    return " ".join(_NOT_WORD.sub(" ", text).split())


def overlap(a: str, b: str) -> float:
    """Dice overlap of two normalized texts' word multisets, 0..1 (1 = the same words)."""
    left, right = Counter(a.split()), Counter(b.split())
    total = sum(left.values()) + sum(right.values())
    if total == 0:
        return 0.0
    return 2 * sum((left & right).values()) / total


def _has_phrase(text: str, phrase: str) -> bool:
    return f" {phrase} " in f" {text} "


@dataclass(frozen=True)
class Match:
    story: PreparedStory
    kind: MatchKind
    score: float  # the token overlap (1.0 for an exact match)


def match_one(text: str, story: PreparedStory) -> Match | None:
    """How `text` matches `story`, or None."""
    said = normalize(text)
    if not said:
        return None
    if said == story.normalized:
        return Match(story, "exact", 1.0)
    score = overlap(said, story.normalized)
    if score >= story.rule.min_overlap:
        return Match(story, "overlap", score)
    keywords = [normalize(k) for k in story.rule.keywords]
    if keywords and all(_has_phrase(said, k) for k in keywords):
        return Match(story, "keywords", score)
    return None


def match_story(text: str, stories: Sequence[PreparedStory], *, family_id: str | None = None) -> Match | None:
    """The best prepared story for `text` (see the module docstring), or None.

    With `family_id`, a story that names another family never matches.
    """
    found = [
        m for story in stories
        if (story.family_id is None or family_id is None or story.family_id == family_id)
        and (m := match_one(text, story)) is not None
    ]
    if not found:
        return None
    return min(found, key=lambda m: (-_KIND_RANK[m.kind], -m.score, m.story.name))
