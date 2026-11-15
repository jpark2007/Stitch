"""The demo family for the stage demo: three generations, three accounts, books ready to open.

README "Scope for the weekend": the stage demo starts with a full tree and books. This seeds

- the family "The Maos" (id `demo_family`), with David as admin and Ana and Sam as members;
- eight people in three generations with hand-picked character sheets (color, signature
  item, build, how they move); Luis and Sam wear their signature hats on stage;
- seven moments from 1958 to 2019, each with a hand-authored screenplay that the seed
  COMPILES with the real compiler (screenplay.compiler.compile) and stores with the
  resulting timeline, so every book opens on playable scenes. Six moments have two
  tellings by different people, and most have two to six participants so threads cross;
- the props and sets those scenes use (placeholder art: `art` null with a color, unless a
  hand-made image is in seed/demo_family/art/, see load_demo_art and that folder's README);
- two placeholder photos on the 1972 road trip;
- five traditions for the Book of Family Traditions, the same ids and text as the web
  mock (web/components/flow/mock/demoTraditions.ts) with this family's person ids; the
  summer road trip reuses the peach-stand photo;
- open questions asking participants of a moment who have not told it yet for their
  version (every one points at a moment with a scene, so either studio can add the
  telling), and one general prompt for Ana that starts a new memory.

The authored data lives next to this file in seed/demo_family/: one
`<memoryId>.screenplay.json` per moment, plus `props.json` and `sets.json`. Edit those, then
run the seed (or `uv run pytest tests/data/test_seed_scenes.py`): every screenplay is
compiled before anything is written, and a screenplay that does not compile stops the seed
with a DemoCompileError listing the memory id and every exact CompileError message. See
seed/README.md.

Effects (confetti, steam, rain, smoke, time_of_day) are authored in the screenplays and
compiled like every other action, so they land in each stored Timeline.effects. Nothing
authored is ever left out: what is in the file is what is compiled and stored.

Run it against MONGODB_URI / MONGODB_DB from api/ (it reads api/.env and ../.env itself,
like the API; variables already set win):

    uv run python -m seed.demo            # seed once; a no-op when the family exists
    uv run python -m seed.demo --reset    # delete the demo data and rebuild it
    uv run python -m seed.demo --cut-out-art  # save white-background art with transparency

It prints only the redacted target (scheme and hosts) and the database name, creates the
indexes and, best effort, the Atlas Vector Search indexes, then seeds. Exit codes: 0 done,
1 bad demo data (nothing written), 2 bad arguments, 3 MongoDB unreachable or failing;
errors are one scrubbed message on stderr, never a traceback. Every run writes the same ids
(people, memories, props, sets, photos, questions, traditions), so two seeded databases match.

Every read and write, including the reset, goes through the db/ functions; the reset is
scoped to the demo family id, the demo user ids and the demo emails, so non-demo data is
never touched.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import logging
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import NamedTuple

from PIL import Image, ImageDraw, ImageOps
from pydantic import ValidationError
from pymongo.database import Database

import auth
import schemas
from db import families as families_db
from db import media as media_db
from db import memories as memories_db
from db import people as people_db
from db import props as props_db
from db import traditions as traditions_db
from db import users as users_db
from screenplay import compiler
from screenplay.narration import default_author
from screenplay.schema import VOCABULARY, CompileError, Screenplay, Timeline

REPO_DIR = Path(__file__).resolve().parents[2]
SAMPLE_PATH = REPO_DIR / "web" / "public" / "samples" / "hymn_books.memory.json"
DATA_DIR = Path(__file__).resolve().parent / "demo_family"
PROPS_PATH = DATA_DIR / "props.json"
SETS_PATH = DATA_DIR / "sets.json"
SCREENPLAY_SUFFIX = ".screenplay.json"

log = logging.getLogger(__name__)

DEMO_FAMILY_ID = "demo_family"
DEMO_FAMILY_NAME = "The Maos"
DEMO_PASSWORD = "stitch-demo"


class DemoAccount(NamedTuple):
    user_id: str
    email: str
    name: str
    person_id: str
    role: schemas.Role


DEMO_ACCOUNTS: tuple[DemoAccount, ...] = (
    DemoAccount("demo_user_david", "david@demo.stitch", "David Mao", "dad", "admin"),
    DemoAccount("demo_user_ana", "ana@demo.stitch", "Ana Mao", "mom", "member"),
    DemoAccount("demo_user_sam", "sam@demo.stitch", "Sam Mao", "sam", "member"),
)
DEMO_EMAILS: tuple[str, ...] = tuple(a.email for a in DEMO_ACCOUNTS)
DEMO_USER_IDS: tuple[str, ...] = tuple(a.user_id for a in DEMO_ACCOUNTS)
_ADMIN_USER_ID = DEMO_ACCOUNTS[0].user_id


@dataclass(frozen=True)
class DemoSeed:
    family_id: str
    accounts: list[tuple[str, str, str]]  # (email, password, person_id)
    person_ids: list[str]
    memory_ids: list[str]
    created: bool
    tradition_ids: list[str] = field(default_factory=list)


class DemoDataError(ValueError):
    """A file in seed/demo_family/ (or the moments table below) is malformed or inconsistent."""


class DemoCompileError(RuntimeError):
    """A seeded screenplay does not compile. The message lists every exact CompileError."""

    def __init__(self, memory_id: str, errors: list[CompileError]) -> None:
        self.memory_id = memory_id
        self.errors = list(errors)
        lines = "\n".join(f"  - [{e.code}] {e.message}" for e in self.errors) or "  - (no error detail)"
        path = DATA_DIR / f"{memory_id}{SCREENPLAY_SUFFIX}"
        super().__init__(
            f"demo moment {memory_id!r} ({path.name}) does not compile; "
            f"{len(self.errors)} error(s):\n{lines}"
        )


# ------------------------------------------------------------------------------ data

def load_sample() -> schemas.MemoryResponse:
    """The hand-written hymn-book scene, validated against the API contract."""
    return schemas.MemoryResponse.model_validate_json(SAMPLE_PATH.read_text())


def _sample_people() -> dict[str, dict]:
    raw = json.loads(SAMPLE_PATH.read_text())
    return {p["id"]: p for p in raw["people"]}


def _read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text())
    except FileNotFoundError as exc:
        raise DemoDataError(f"{path.name}: file not found in {path.parent}") from exc
    except json.JSONDecodeError as exc:
        raise DemoDataError(f"{path.name}: invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}") from exc


def _validate_list(path: Path, model: type[schemas.Prop] | type[schemas.SetDef]) -> list:
    raw = _read_json(path)
    if not isinstance(raw, list):
        raise DemoDataError(f"{path.name}: expected a JSON list of {model.__name__} objects")
    out = []
    for i, item in enumerate(raw):
        try:
            out.append(model.model_validate(item))
        except ValidationError as exc:
            ident = item.get("id") if isinstance(item, dict) else None
            raise DemoDataError(f"{path.name}: item {i} ({ident!r}) is not a valid {model.__name__}:\n{exc}") from exc
    ids = [m.id for m in out]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        raise DemoDataError(f"{path.name}: duplicate ids {', '.join(dupes)}")
    return out


def load_props(path: Path = PROPS_PATH) -> list[schemas.Prop]:
    """seed/demo_family/props.json as Prop models (family id set to the demo family)."""
    return [p.model_copy(update={"family_id": DEMO_FAMILY_ID}) for p in _validate_list(path, schemas.Prop)]


def load_sets(path: Path = SETS_PATH) -> list[schemas.SetDef]:
    """seed/demo_family/sets.json as SetDef models (family id set to the demo family)."""
    return [s.model_copy(update={"family_id": DEMO_FAMILY_ID}) for s in _validate_list(path, schemas.SetDef)]


def screenplay_path(memory_id: str, directory: Path = DATA_DIR) -> Path:
    return directory / f"{memory_id}{SCREENPLAY_SUFFIX}"


def load_screenplay(memory_id: str, directory: Path = DATA_DIR) -> Screenplay:
    """seed/demo_family/<memoryId>.screenplay.json as a Screenplay; its memoryId must match."""
    path = screenplay_path(memory_id, directory)
    raw = _read_json(path)
    try:
        screenplay = Screenplay.model_validate(raw)
    except ValidationError as exc:
        raise DemoDataError(f"{path.name}: not a valid Screenplay:\n{exc}") from exc
    if screenplay.memory_id != memory_id:
        raise DemoDataError(f"{path.name}: memoryId is {screenplay.memory_id!r}, expected {memory_id!r}")
    return screenplay


def _cs(color: str, item: str, build: str, notes: str | None = None,
        item_prop_id: str | None = None) -> schemas.CharacterSheet:
    return schemas.CharacterSheet(color=color, item=item, build=build, notes=notes, item_prop_id=item_prop_id)


class _P(NamedTuple):
    id: str
    name: str
    born: int
    died: int | None
    parent_ids: list[str]
    spouse_ids: list[str]
    character: schemas.CharacterSheet


def demo_people() -> list[_P]:
    """Everyone in the tree, parents before children. David and Ana match the sample scene.

    Builds vary (small, short, average, tall) and every color and signature item is
    distinct. Luis and Sam wear theirs as props (`itemPropId`), so their hats go on stage
    with them in any scene that lists the prop.
    """
    sample = _sample_people()
    dad = schemas.CharacterSheet.model_validate(sample["dad"]["character"])
    mom = schemas.CharacterSheet.model_validate(sample["mom"]["character"])
    return [
        _P("grandpa_joseph", "Joseph", 1934, 2011, [], [],
           _cs("#7d9a5b", "walking cane", "short", "walks slowly with a cane")),
        _P("grandma_rosa", "Rosa", 1938, None, [], ["grandpa_joseph"],
           _cs("#c77d9b", "pearl necklace", "small", "small quick steps, always humming")),
        _P("grandpa_luis", "Luis", 1936, 2015, [], [],
           _cs("#d9973a", "straw hat", "tall", "stands very straight, hands clasped behind his back",
               item_prop_id="luis_straw_hat")),
        _P("grandma_carmen", "Carmen", 1940, None, [], ["grandpa_luis"],
           _cs("#8a5a9e", "knitted cardigan", "short", "sways a little when she laughs")),
        _P("dad", sample["dad"]["name"], sample["dad"]["born"], None,
           ["grandpa_joseph", "grandma_rosa"], [], dad),
        _P("mom", sample["mom"]["name"], sample["mom"]["born"], None,
           ["grandpa_luis", "grandma_carmen"], ["dad"], mom),
        _P("sam", "Sam", 1990, None, ["dad", "mom"], [],
           _cs("#2f8f8a", "baseball cap", "average", "bounces on his toes when he talks", item_prop_id="sam_cap")),
        _P("lily", "Lily", 1993, None, ["dad", "mom"], [],
           _cs("#e0b84f", "yellow raincoat", "small", "walks briskly, always a step ahead")),
    ]


DEMO_PERSON_IDS: tuple[str, ...] = (
    "grandpa_joseph", "grandma_rosa", "grandpa_luis", "grandma_carmen", "dad", "mom", "sam", "lily",
)


def scene_people(family_id: str = DEMO_FAMILY_ID) -> dict[str, schemas.Person]:
    """The demo people as the Person models the compiler sees, keyed by id."""
    linked = {a.person_id: a.user_id for a in DEMO_ACCOUNTS}
    return {
        p.id: schemas.Person(
            id=p.id, family_id=family_id, user_id=linked.get(p.id), name=p.name, born=p.born, died=p.died,
            parent_ids=p.parent_ids, spouse_ids=p.spouse_ids, character=p.character, added_by=_ADMIN_USER_ID,
        )
        for p in demo_people()
    }


class _T(NamedTuple):
    author_id: str
    question: str | None
    text: str
    photos: bool = False  # attach the two placeholder photos


class _M(NamedTuple):
    id: str
    title: str
    year: int
    participant_ids: list[str]
    tellings: list[_T]


HYMN_MEMORY_ID = "sample_hymn_books"


def _hymn_moment() -> _M:
    """The hymn-book memory's title, year, participants and tellings, from the sample."""
    memory = load_sample().memory
    return _M(memory.id, memory.title, memory.year, list(memory.participant_ids),
              [_T(t.author_id, t.question, t.text) for t in memory.tellings])


# Every moment, oldest first. The first telling's author tells the scene's default
# narration (Shot.narration); every other teller has a telling in Screenplay.narrations.
# Everyone on stage is a participant, and every participant was alive that year.
EXTRA_MEMORIES: tuple[_M, ...] = (
    _M("demo_rosa_joseph_wedding", "Rosa and Joseph's wedding", 1958,
       ["grandma_rosa", "grandpa_joseph"], [
           _T("grandma_rosa", "What do you remember about your wedding day?",
              "It rained all morning, and I was sure the whole day was ruined. Joseph showed up "
              "with his good shoes soaked through and gave me his umbrella without a word. By the "
              "time we came out of the church the sun was out. And everyone was laughing at his socks."),
           _T("dad", "What did your parents tell you about their wedding?",
              "Mom always says it rained, and Dad always says it didn't. The one photo we have "
              "shows wet steps and a lot of umbrellas. So I think she wins that one."),
       ]),
    _M("demo_sunday_tamales", "Sunday tamales at Carmen's", 1970,
       ["grandma_carmen", "grandpa_luis", "mom"], [
           _T("mom", "What did Sundays look like when you were little?",
              "Every Sunday my mother made tamales, and the whole kitchen smelled of corn husks. "
              "I was allowed to spread the masa, badly. My father pretended every lumpy one was "
              "the best he had ever eaten."),
       ]),
    _M("demo_green_sedan_trip", "The road trip in the green sedan", 1972,
       ["grandpa_joseph", "grandma_rosa", "dad"], [
           _T("dad", "Tell me about a trip you took as a kid.",
              "Dad bought a green sedan that summer and decided we had to drive it all the way to "
              "the coast. I sat in the back with a map I could not read. We got a flat tire outside "
              "a peach stand. We sat on a fruit crate and ate peaches while he fixed it.", photos=True),
           _T("grandma_rosa", "David remembers the green sedan trip. What do you remember?",
              "Joseph would not let anyone else drive that car. When the tire went, he changed it "
              "in his church trousers. David ate so many peaches he was sick the rest of the way."),
       ]),
    _M("demo_sams_first_bike", "Sam's first bike", 1997,
       ["dad", "sam"], [
           _T("dad", "What do you remember about teaching Sam to ride a bike?",
              "It was a red bike with a bell, two sizes too big because I said he would grow into "
              "it. I ran behind him holding the seat up and down the whole street. When I finally "
              "let go he didn't notice for half a block."),
           _T("sam", "Dad says you didn't notice when he let go. Is that true?",
              "I noticed. I just didn't want to stop, because stopping meant falling. I rang the "
              "bell the whole way to the corner so he would know I was fine."),
       ]),
    _M("demo_lily_graduation", "Lily's graduation", 2015,
       ["lily", "mom", "dad", "sam", "grandma_rosa", "grandma_carmen"], [
           _T("mom", "What do you remember about Lily's graduation?",
              "Both grandmothers came and sat in the front row in matching hats, which was not "
              "planned. Lily tripped on the stage stairs and turned it into a bow. Rosa clapped "
              "louder than anyone in the hall. Outside she handed me that diploma, and I would "
              "not let go of her."),
           _T("lily", "Mom says you turned a trip into a bow. What really happened?",
              "My gown was too long and I caught the hem on the second step. I figured if I bowed "
              "nobody would know. Sam knew, and he still brings it up every Thanksgiving."),
       ]),
    _M("demo_sam_wedding_2019", "Sam's wedding in the orchard", 2019,
       ["sam", "lily", "mom", "dad"], [
           _T("sam", "What do you remember about your wedding day?",
              "I was so nervous I forgot Lily had the rings. She walked up the aisle and pressed "
              "the box into my hand like it was nothing. Mom and Dad sat on the front bench holding "
              "hands the whole time. At the party Mom hugged me so hard my boutonniere fell off."),
           _T("dad", "What do you remember about Sam's wedding?",
              "Sam looked exactly like he did on that red bike, terrified and pretending not to be. "
              "Lily saved the day with the rings. When the confetti came down, Ana cried and I "
              "waved at everyone like a fool."),
       ]),
)

MOMENTS: tuple[_M, ...] = tuple(sorted((_hymn_moment(), *EXTRA_MEMORIES), key=lambda m: m.year))
DEMO_MEMORY_IDS: tuple[str, ...] = tuple(m.id for m in MOMENTS)

# (person id, text, memory id). Each memory-linked question asks a participant of that
# moment who has not told it yet, and who was born by then, for their version; every moment
# has a compiled scene, so either studio can add the telling. Questions go to living people
# only. Sam, Ana and David each get at least one, so the stage demo can show the flow from
# any of the three accounts. The last one is a general prompt that starts a new memory.
DEMO_QUESTIONS: tuple[tuple[str, str, str | None], ...] = (
    ("sam", "Lily says you still bring up her graduation every Thanksgiving. "
            "What did you see from your seat that day?", "demo_lily_graduation"),
    ("dad", "Ana and Lily have both told Lily's graduation. What do you remember about that day?",
     "demo_lily_graduation"),
    ("mom", "Sam says you hugged him so hard at the wedding that his flower fell off. "
            "How do you remember that day?", "demo_sam_wedding_2019"),
    ("lily", "You carried the rings at Sam's wedding. What was that walk up the aisle like?",
     "demo_sam_wedding_2019"),
    ("grandma_carmen", "Ana still talks about Sunday tamales in your kitchen. "
                       "What were those Sundays like for you?", "demo_sunday_tamales"),
    ("grandma_rosa", "Ana says you clapped louder than anyone at Lily's graduation. "
                     "What do you remember about it?", "demo_lily_graduation"),
    ("mom", "What do you remember about the day Lily was born?", None),
)


# The Book of Family Traditions. Same ids, titles, descriptions, categories, `when` and
# started years as web/components/flow/mock/demoTraditions.ts, in the same order (each is
# created a second after the one before, so the book lists them in this order). The mock's
# person ids map to this family's: rosa -> grandma_rosa, luis -> grandpa_luis, maya ->
# lily; dad, mom and sam are the same. Its user_david / user_ana are the demo accounts.
# (id, created by, request; photoIds are filled in at build time from _TRADITION_PHOTOS.)
DEMO_TRADITIONS: tuple[tuple[str, str, schemas.CreateTraditionRequest], ...] = (
    ("tamales_christmas_eve", "demo_user_david", schemas.CreateTraditionRequest(
        title="Tamales on Christmas Eve",
        description=(
            "Every Christmas Eve the whole kitchen turns into an assembly line. Rosa spreads the masa, Luis folds, "
            "and the kids are in charge of the corn husks until they're old enough to be trusted with the filling. "
            "We eat the first batch standing up, straight out of the pot."
        ),
        category="food",
        when=schemas.TraditionWhen(kind="yearly", label="Christmas Eve", month=12),
        participant_ids=["grandma_rosa", "grandpa_luis", "dad", "mom", "lily", "sam"],
        started_year=1962,
    )),
    ("sunday_pancakes", "demo_user_ana", schemas.CreateTraditionRequest(
        title="Sunday pancakes",
        description=(
            "David makes pancakes every Sunday morning, always one shaped like the first letter of your name. "
            "Sam once asked for an S and got something closer to a snake. Nobody is allowed to skip breakfast."
        ),
        category="food",
        when=schemas.TraditionWhen(kind="weekly", label="every Sunday"),
        participant_ids=["dad", "mom", "lily", "sam"],
        started_year=1995,
    )),
    ("summer_road_trip", "demo_user_david", schemas.CreateTraditionRequest(
        title="The summer road trip to the coast",
        description=(
            "Each July we pack the car the night before and leave before the sun is up. "
            "The first stop is always the same diner for cinnamon rolls, and whoever spots the ocean first "
            "picks the music for the rest of the drive."
        ),
        category="trip",
        when=schemas.TraditionWhen(kind="yearly", label="every July", month=7),
        participant_ids=["dad", "mom", "lily", "sam"],
        started_year=1997,
    )),
    ("hymns_after_dinner", "demo_user_ana", schemas.CreateTraditionRequest(
        title="Hymns after dinner",
        description=(
            "When the family is all at one table, someone starts humming and Rosa takes it from there. "
            "Ana carries the harmony, David pretends he doesn't know the words, and by the second verse "
            "everyone is singing."
        ),
        category="music",
        when=schemas.TraditionWhen(kind="occasional", label="when everyone is home"),
        participant_ids=["grandma_rosa", "dad", "mom", "lily", "sam"],
        started_year=None,
    )),
    ("rosas_easter_hats", "demo_user_david", schemas.CreateTraditionRequest(
        title="Rosa's Easter hats",
        description=(
            "Rosa makes a new Easter hat for each of the grandchildren, out of whatever she has: ribbon, paper "
            "flowers, once a whole bird's nest. We wear them to church and then to lunch, no matter how tall they "
            "are. She has never made the same hat twice."
        ),
        category="craft",
        when=schemas.TraditionWhen(kind="yearly", label="Easter Sunday", month=4),
        participant_ids=["grandma_rosa", "lily", "sam"],
        started_year=1992,
    )),
)
DEMO_TRADITION_IDS: tuple[str, ...] = tuple(t[0] for t in DEMO_TRADITIONS)
# Tradition id -> indexes into the seeded photos (DEMO_PHOTO_IDS) it shows. The peach stand
# is a roadside stop with no year printed on it, so it fits the road trip.
_TRADITION_PHOTOS: dict[str, tuple[int, ...]] = {"summer_road_trip": (1,)}


# ------------------------------------------------------------------------- the scenes

class DemoScene(NamedTuple):
    screenplay: Screenplay  # the authored file, exactly as compiled and stored
    timeline: Timeline
    prop_ids: list[str]
    set_ids: list[str]


EFFECT_VERBS: frozenset[str] = frozenset(VOCABULARY["effects"])


def tellers(screenplay: Screenplay) -> set[str]:
    """Everyone who narrates the scene: the default narrator and every other telling's author."""
    return {default_author(screenplay), *(screenplay.narrations or {})}


def _check_moment(moment: _M, screenplay: Screenplay, people: Mapping[str, schemas.Person]) -> None:
    """Lifetimes, cast and tellers agree with the moment. Raises DemoDataError."""
    where = f"{moment.id} ({moment.year})"
    for pid in dict.fromkeys([*moment.participant_ids, *(t.author_id for t in moment.tellings)]):
        if pid not in people:
            raise DemoDataError(f"{where}: {pid!r} is not a demo person")
    for pid in moment.participant_ids:
        person = people[pid]
        if person.born is not None and person.born > moment.year:
            raise DemoDataError(f"{where}: participant {pid} was born in {person.born}")
        if person.died is not None and person.died < moment.year:
            raise DemoDataError(f"{where}: participant {pid} died in {person.died}")
    off_list = [c for c in screenplay.cast if c not in moment.participant_ids]
    if off_list:
        raise DemoDataError(f"{where}: cast {', '.join(off_list)} not among the participants")
    authors = [t.author_id for t in moment.tellings]
    if len(set(authors)) != len(authors):
        raise DemoDataError(f"{where}: one telling per author")
    if tellers(screenplay) != set(authors):
        raise DemoDataError(
            f"{where}: the screenplay is narrated by {sorted(tellers(screenplay))}, "
            f"but the tellings are by {sorted(authors)}"
        )
    if default_author(screenplay) != authors[0]:
        raise DemoDataError(f"{where}: the default narration should be {authors[0]}'s, the first telling")


def compile_moment(
    screenplay: Screenplay,
    *,
    sets: Mapping[str, schemas.SetDef],
    props: Mapping[str, schemas.Prop],
    people: Mapping[str, schemas.Person],
) -> Timeline:
    """Compile one authored screenplay, as authored, with the real compiler.

    Returns its timeline. Raises DemoCompileError with every exact CompileError when it
    does not compile.
    """
    result = compiler.compile(screenplay, sets=sets, props=props, people=people)
    if not result.ok or result.timeline is None:
        raise DemoCompileError(screenplay.memory_id, result.errors)
    return result.timeline


@dataclass(frozen=True)
class DemoData:
    """Everything the seed writes from seed/demo_family/, checked and compiled."""

    props: list[schemas.Prop]
    sets: list[schemas.SetDef]
    scenes: dict[str, DemoScene]  # memory id -> compiled scene, in MOMENTS order
    art: tuple[DemoArtFile, ...] = ()  # processed images from seed/demo_family/art/, written by _build


def load_demo_data(directory: Path | None = None, art_dir: Path | None = None) -> DemoData:
    """Load, check and compile every moment's screenplay. Nothing touches a database.

    `directory` defaults to DATA_DIR and `art_dir` to ART_DIR (both read at call time; with
    another `directory`, its own `art/` folder; see load_demo_art). The art is applied to the props and sets
    before compiling, so each timeline is staged with the aspect and anchor of the image
    it will draw. Raises DemoDataError for a malformed or inconsistent file (naming it) and
    DemoCompileError for a screenplay that does not compile; a bad art file is only a
    warning (its prop or layer keeps the placeholder). So is a body image whose aspect or
    anchor would stop a moment compiling: that prop keeps the placeholder body and its
    authored rig (see _compile_with_art).
    """
    if art_dir is None:
        art_dir = ART_DIR if directory is None else directory / ART_DIR_NAME
    directory = DATA_DIR if directory is None else directory
    authored_props = load_props(directory / PROPS_PATH.name)
    props, sets, art = load_demo_art(authored_props, load_sets(directory / SETS_PATH.name), art_dir)
    people = scene_people()
    for person in people.values():
        item = person.character.item_prop_id
        if item is not None and item not in {p.id for p in props}:
            raise DemoDataError(f"{PROPS_PATH.name}: no prop {item!r}, the signature item of {person.id}")
    stray = sorted(
        p.name for p in directory.glob(f"*{SCREENPLAY_SUFFIX}")
        if p.name[: -len(SCREENPLAY_SUFFIX)] not in DEMO_MEMORY_IDS
    )
    if stray:
        raise DemoDataError(f"screenplays for no demo moment in {directory.name}/: {', '.join(stray)}")
    screenplays: dict[str, Screenplay] = {}
    for moment in MOMENTS:
        screenplay = load_screenplay(moment.id, directory)
        _check_moment(moment, screenplay, people)
        screenplays[moment.id] = screenplay
    props, art, timelines = _compile_with_art(screenplays, authored_props, props, sets, art, people)
    scenes = {
        memory_id: DemoScene(screenplay, timelines[memory_id], list(screenplay.props),
                             list(dict.fromkeys(shot.set for shot in screenplay.shots)))
        for memory_id, screenplay in screenplays.items()
    }
    return DemoData(props, sets, scenes, art)


def _compile_all(screenplays: Mapping[str, Screenplay], props: list[schemas.Prop],
                 sets: list[schemas.SetDef], people: Mapping[str, schemas.Person]) -> dict[str, Timeline]:
    by_id_props = {p.id: p for p in props}
    by_id_sets = {s.id: s for s in sets}
    return {
        memory_id: compile_moment(screenplay, sets=by_id_sets, props=by_id_props, people=people)
        for memory_id, screenplay in screenplays.items()
    }


def _compile_with_art(
    screenplays: Mapping[str, Screenplay],
    authored: list[schemas.Prop],
    props: list[schemas.Prop],
    sets: list[schemas.SetDef],
    art: tuple[DemoArtFile, ...],
    people: Mapping[str, schemas.Person],
) -> tuple[list[schemas.Prop], tuple[DemoArtFile, ...], dict[str, Timeline]]:
    """Compile every moment; when a body image's aspect or anchor stops one compiling, drop it.

    A hand-made body image restages its prop (a wider car parks further offstage, a lower
    anchor moves where a hand reaches). When a moment fails and some of its props have a rig
    from art (aspect or anchor differing from props.json), the prop to blame is found by
    reverting one at a time (all of them when no single one fixes it); each is put back to
    its authored aspect, anchor and placeholder body, with a warning naming the file and the
    compile error, and everything is compiled again. A failure with no art involved raises
    DemoCompileError exactly as without art.
    """
    by_authored = {p.id: p for p in authored}
    props = list(props)

    def rig(p: schemas.Prop) -> tuple:
        return (p.aspect, list(p.anchor))

    def revert(current: list[schemas.Prop], prop_id: str) -> list[schemas.Prop]:
        base = by_authored[prop_id]
        return [
            p.model_copy(update={"aspect": base.aspect, "anchor": list(base.anchor),
                                 "art": {**p.art, props_db.BODY: base.art.get(props_db.BODY)}})
            if p.id == prop_id else p
            for p in current
        ]

    while True:
        try:
            return props, art, _compile_all(screenplays, props, sets, people)
        except DemoCompileError as exc:
            screenplay = screenplays[exc.memory_id]
            by_id = {p.id: p for p in props}
            rigged = [pid for pid in dict.fromkeys(screenplay.props)
                      if pid in by_id and pid in by_authored and rig(by_id[pid]) != rig(by_authored[pid])]
            if not rigged:
                raise
            blamed = rigged
            for pid in rigged:
                try:
                    _compile_all({exc.memory_id: screenplay}, revert(props, pid), sets, people)
                except DemoCompileError:
                    continue
                blamed = [pid]
                break
            reason = exc.errors[0].message if exc.errors else "no error detail"
            for pid in blamed:
                media_id = demo_art_media_id(pid, props_db.BODY)
                name = next((f.filename for f in art if f.media_id == media_id), f"{pid}{ART_SUFFIX}")
                log.warning(
                    "demo art %s/%s skipped, the placeholder stays: with aspect %.2f and anchor %s, "
                    "moment %r does not compile (%s)",
                    ART_DIR_NAME, name, by_id[pid].aspect, by_id[pid].anchor, exc.memory_id, reason,
                )
                props = revert(props, pid)
                art = tuple(f for f in art if f.media_id != media_id)


def compile_demo_scenes(directory: Path | None = None) -> dict[str, DemoScene]:
    """memory id -> compiled scene for every moment (see load_demo_data)."""
    return load_demo_data(directory).scenes


# ---------------------------------------------------------------------------- photos

def _placeholder_png(title: str, sky: str, ground: str, accent: str) -> bytes:
    """A small, deterministic 'photo' (sky, ground, a car-shaped block) as PNG bytes."""
    width, height = 320, 240
    image = Image.new("RGB", (width, height), sky)
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 150, width, height), fill=ground)
    draw.rounded_rectangle((90, 110, 230, 165), radius=14, fill=accent)
    draw.ellipse((105, 152, 135, 182), fill="#2b2b2b")
    draw.ellipse((185, 152, 215, 182), fill="#2b2b2b")
    draw.rectangle((0, 0, width - 1, height - 1), outline="#f6f1e7", width=8)
    draw.text((16, 16), title, fill="#3a3a3a")
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


def _demo_object_id(kind: int, n: int) -> str:
    """A fixed, valid ObjectId string for seeded data: '5eed' + kind (4 hex) + n (16 hex).

    ObjectId-shaped so code that sorts or parses ids as ObjectIds keeps working, and
    increasing in n, so seeded questions sort in the order they are listed.
    """
    return f"5eed{kind:04x}{n:016x}"


# Fixed ids, so every seeded database (every teammate's STITCH_FAKE_DB run, every reset)
# holds the same documents and the demo matches the one rehearsed.
DEMO_PHOTO_IDS: tuple[str, ...] = (_demo_object_id(1, 1), _demo_object_id(1, 2))
DEMO_QUESTION_IDS: tuple[str, ...] = tuple(_demo_object_id(2, n) for n in range(1, len(DEMO_QUESTIONS) + 1))


def _put_demo_photos(db: Database) -> list[str]:
    photos = [
        ("green_sedan_1972.png", _placeholder_png("1972", "#bcd4e0", "#c9b48a", "#5e8c4a")),
        ("peach_stand_1972.png", _placeholder_png("peaches", "#f2d8b5", "#9fb27a", "#d4826a")),
    ]
    return [
        media_db.put_media(db, DEMO_FAMILY_ID, data, content_type="image/png", filename=name, kind="photo",
                           media_id=media_id)
        for (name, data), media_id in zip(photos, DEMO_PHOTO_IDS, strict=True)
    ]


# ------------------------------------------------------------------------------- art

# Hand-made prop and set images in seed/demo_family/art/ (naming rules in its README.md):
#   <propId>.png            the prop's body
#   <propId>.<artKey>.png   any other key of the prop's `art` map (a part such as `wheel`,
#                           or the art a state switches to, such as `open`)
#   <setId>.<layer>.png     a set layer: back, middle or front
# Each file is processed the way the studio processes generated art, stored as a "cutout"
# under a fixed media id, and set on its prop or layer. Anything absent keeps `art: null`.

ART_DIR_NAME = "art"
ART_DIR = DATA_DIR / ART_DIR_NAME
ART_SUFFIX = ".png"
ART_IGNORED = frozenset({"README.md", ".gitkeep", ".DS_Store"})
DEMO_ART_MAX_PIXELS = 16_000_000
"""Files with more pixels than this (e.g. 4096x4096) are skipped before they are decoded."""
_ART_ID_KIND = 3  # _demo_object_id kind: 1 photos, 2 questions, 3 art
_LAYER_DEPTHS: dict[str, int] = {"back": 0, "middle": 1, "front": 2}
_OPAQUE_ALPHA = 128  # agent.tools.art.ALPHA_THRESHOLD: a pixel at or above it belongs to the shape


@dataclass(frozen=True)
class DemoArtFile:
    """One processed image, ready for put_media."""

    media_id: str  # bare ObjectId string
    filename: str  # the source file's name
    png: bytes


class _SkipArt(ValueError):
    """An art file that cannot be used; the message says why."""


def demo_art_media_id(owner_id: str, key: str) -> str:
    """The fixed media id of `<owner_id>.<key>.png`, the same on every run and every machine.

    `key` is a prop art key or a layer name. Kind 3 keeps it apart from the photo and
    question ids; the low 16 hex digits are a hash of the name, so adding or removing other
    files never moves it.
    """
    digest = hashlib.sha256(f"{owner_id}.{key}".encode()).hexdigest()
    return _demo_object_id(_ART_ID_KIND, int(digest[:16], 16))


def _art_target(stem: str, props: Mapping[str, schemas.Prop],
                sets: Mapping[str, schemas.SetDef]) -> tuple[str, str, str]:
    """('prop' | 'set', id, art key or layer name) for a file stem, or _SkipArt."""
    owner, _, key = stem.partition(".")
    if "." in key:
        raise _SkipArt("name has too many dots (expected <id>.png or <id>.<key>.png)")
    if owner in sets:
        if key not in _LAYER_DEPTHS:
            raise _SkipArt(f"set {owner!r} needs a layer name: {owner}.back.png, .middle.png or .front.png")
        return "set", owner, key
    if owner in props:
        key = key or props_db.BODY
        if key not in props[owner].art:
            keys = ", ".join(sorted(props[owner].art))
            raise _SkipArt(f"prop {owner!r} has no art key {key!r} (its keys: {keys})")
        return "prop", owner, key
    raise _SkipArt(f"no prop or set with id {owner!r} in {PROPS_PATH.name} or {SETS_PATH.name}")


def _open_art(path: Path) -> bytes:
    """The file's image bytes, upright. Checks the pixel cap from the header, before decoding."""
    data = path.read_bytes()
    try:
        with Image.open(io.BytesIO(data)) as src:
            width, height = src.size
            if width <= 0 or height <= 0:
                raise _SkipArt("image is empty")
            if width * height > DEMO_ART_MAX_PIXELS:
                raise _SkipArt(f"{width}x{height} is over the {DEMO_ART_MAX_PIXELS:,} pixel cap")
            try:
                orientation = int(src.getexif().get(0x0112) or 1)  # EXIF Orientation
            except Exception:  # noqa: BLE001 - a broken EXIF block means "upright"
                orientation = 1
            if orientation == 1:
                src.load()  # decode now, so a truncated file is reported here
                return data
            src.load()
            upright = ImageOps.exif_transpose(src)
            out = io.BytesIO()
            upright.save(out, format="PNG")
            return out.getvalue()
    except _SkipArt:
        raise
    except Exception as exc:  # not an image Pillow can read
        raise _SkipArt(f"not a readable image ({type(exc).__name__}: {exc})") from exc


def _check_media_size(png: bytes) -> None:
    if len(png) > media_db.MAX_BYTES:
        raise _SkipArt(f"the processed image is {len(png):,} bytes, over the media limit of {media_db.MAX_BYTES:,}")


def _png(im: Image.Image) -> bytes:
    out = io.BytesIO()
    im.save(out, format="PNG")
    return out.getvalue()


def _remove_white(data: bytes) -> tuple[bytes, bool]:
    """The production cut-out remover (agent.adapters.cutout.AlphaAwareRemover): an image with
    real transparency comes back as it is; otherwise the white background touching the border
    is flood-filled away. The flag says whether that (slow, pure-Python) fill ran."""
    from agent.adapters.cutout import AlphaAwareRemover

    out = AlphaAwareRemover().remove(data)
    return out, out is not data


def _prop_art(data: bytes):
    """The studio's prop path: white background removed (real alpha kept), cropped to the
    shape (agent.tools.art.analyze_cutout: aspect and bottom-center anchor). -> (Cutout, filled)."""
    from agent.tools.art import analyze_cutout

    cut, filled = _remove_white(data)
    try:
        return analyze_cutout(cut), filled
    except ValueError as exc:  # analyze_cutout: no opaque pixels
        raise _SkipArt(str(exc)) from exc


def _layer_art(data: bytes, layer: str, height: float) -> tuple[bytes, bool]:
    """The studio's layer path: fitted to the layer's frame (layer_size, scaled evenly and
    cropped from the bottom, as the image adapter fits a model's canvas), never cropped to the
    shape; the back layer made fully opaque, the middle and front layers with their white
    background removed. -> (png, filled)."""
    from agent.adapters.images import fit_to_size
    from agent.tools.sets import layer_size

    with Image.open(io.BytesIO(data)) as src:
        src.load()
        im = fit_to_size(src.convert("RGBA"), layer_size(height))
    if layer == "back":
        im.putalpha(255)
        return _png(im), False
    cut, filled = _remove_white(_png(im))
    with Image.open(io.BytesIO(cut)) as out:
        alpha = out.convert("RGBA").getchannel("A")
    if alpha.point(lambda a: 255 if a >= _OPAQUE_ALPHA else 0).getbbox() is None:
        raise _SkipArt("layer has no opaque pixels")
    return cut, filled


def load_demo_art(
    props: list[schemas.Prop], sets: list[schemas.SetDef], art_dir: Path
) -> tuple[list[schemas.Prop], list[schemas.SetDef], tuple[DemoArtFile, ...]]:
    """Apply the images in `art_dir` to `props` and `sets` (new models; the inputs are untouched).

    Returns the props and sets with their art refs set ('media/<fixed id>'), and the processed
    files to store. A prop's body image also sets its aspect (clamped into
    db.props.MIN_PHOTO_ASPECT..MAX_PHOTO_ASPECT, like a photo swap, so every scene keeps
    compiling) and its anchor. A missing or empty folder changes nothing. A file that cannot be
    used (unknown name, unreadable, not an image, over DEMO_ART_MAX_PIXELS, no opaque pixels,
    or a second file for the same slot) is logged as a warning naming it and skipped, so its
    prop or layer keeps the placeholder.
    """
    if not art_dir.is_dir():
        return props, sets, ()
    files = sorted(p for p in art_dir.iterdir() if p.is_file() and p.name not in ART_IGNORED)
    if not files:
        return props, sets, ()

    by_prop = {p.id: p for p in props}
    by_set = {s.id: s for s in sets}
    prop_updates: dict[str, dict] = {}
    layer_art: dict[tuple[str, int], str] = {}
    stored: list[DemoArtFile] = []
    white: list[str] = []
    started = time.perf_counter()
    taken: dict[tuple[str, str, str], str] = {}

    for path in files:
        where = f"{art_dir.name}/{path.name}"
        try:
            if path.suffix != ART_SUFFIX:
                raise _SkipArt(f"not a {ART_SUFFIX} file")
            kind, owner, key = _art_target(path.name[: -len(ART_SUFFIX)], by_prop, by_set)
            if (kind, owner, key) in taken:
                raise _SkipArt(f"{taken[kind, owner, key]} already gives this {kind} its {key!r} art")
            data = _open_art(path)
            media_id = demo_art_media_id(owner, key)
            if kind == "set":
                depth = _LAYER_DEPTHS[key]
                layer = next((x for x in by_set[owner].layers if x.depth == depth), None)
                if layer is None:
                    raise _SkipArt(f"set {owner!r} has no {key} layer (depth {depth})")
                png, filled = _layer_art(data, key, layer.height)
                _check_media_size(png)
                layer_art[owner, depth] = media_db.media_ref(media_id)
            else:
                cut, filled = _prop_art(data)
                png = cut.png
                _check_media_size(png)
                update = prop_updates.setdefault(owner, {"art": dict(by_prop[owner].art)})
                update["art"][key] = media_db.media_ref(media_id)
                if key == props_db.BODY:
                    aspect = props_db.clamp_photo_aspect(round(cut.aspect, 4))
                    if aspect != round(cut.aspect, 4):
                        log.warning("demo art %s: aspect %.2f clamped to %.2f so every scene still compiles",
                                    where, cut.aspect, aspect)
                    update["aspect"] = aspect
                    update["anchor"] = [round(v, 4) for v in cut.anchor]
        except _SkipArt as exc:
            log.warning("demo art %s skipped, the placeholder stays: %s", where, exc)
            continue
        except Exception as exc:  # noqa: BLE001 - one bad file never stops the seed
            log.warning("demo art %s skipped, the placeholder stays: %s: %s", where, type(exc).__name__, exc)
            continue
        taken[kind, owner, key] = path.name
        stored.append(DemoArtFile(media_id=media_id, filename=path.name, png=png))
        if filled:
            white.append(path.name)

    if white:
        log.warning(
            "demo art: %d file(s) have a white background, removed again on every start (%.1fs in all "
            "for the art folder): %s. Run `uv run python -m seed.demo --cut-out-art` once to save them "
            "with transparency.", len(white), time.perf_counter() - started, ", ".join(white),
        )

    new_props = [p.model_copy(update=prop_updates[p.id]) if p.id in prop_updates else p for p in props]
    new_sets = [
        s.model_copy(update={"layers": [
            layer.model_copy(update={"art": layer_art[s.id, layer.depth]})
            if (s.id, layer.depth) in layer_art else layer
            for layer in s.layers
        ]})
        if any(owner == s.id for owner, _ in layer_art) else s
        for s in sets
    ]
    return new_props, new_sets, tuple(stored)


def cut_out_demo_art(art_dir: Path | None = None) -> list[str]:
    """Save every white-background prop or middle/front layer image in the art folder with
    its background made transparent (the same remover the seed runs), so later seeds skip
    the slow flood fill. Back layers and images that already have transparency are left as
    they are; a file the seed would skip is only warned about. Returns the rewritten names.
    `python -m seed.demo --cut-out-art`.
    """
    art_dir = ART_DIR if art_dir is None else art_dir
    by_prop = {p.id: p for p in load_props()}
    by_set = {s.id: s for s in load_sets()}
    rewritten: list[str] = []
    for path in sorted(art_dir.glob(f"*{ART_SUFFIX}")) if art_dir.is_dir() else []:
        try:
            kind, _owner, key = _art_target(path.name[: -len(ART_SUFFIX)], by_prop, by_set)
            if kind == "set" and key == "back":
                continue
            out, filled = _remove_white(_open_art(path))
            if filled:
                tmp = path.with_name(path.name + ".tmp")
                tmp.write_bytes(out)
                tmp.replace(path)
                rewritten.append(path.name)
        except Exception as exc:  # noqa: BLE001 - report it and go on with the others
            log.warning("demo art %s/%s left as it is: %s", art_dir.name, path.name, exc)
    return rewritten


# ----------------------------------------------------------------------------- reset

def _purge(db: Database) -> None:
    """Delete everything that belongs to the demo, and nothing else.

    The family goes first (purge_family deletes the family document before its data), and
    the accounts last: a purge that is interrupted part way always leaves the family
    document missing, so the next seed_demo sees an incomplete demo and rebuilds it. The
    other order could leave the family with no accounts, which the next run would accept.
    """
    families_db.purge_family(db, DEMO_FAMILY_ID)  # the family, its data and its media
    users_db.delete_users(db, emails=list(DEMO_EMAILS), user_ids=list(DEMO_USER_IDS))


def demo_is_complete(db: Database) -> bool:
    """True when the demo family exists and every demo account can sign in to it.

    The family document is written last in one insert with every member, so it marks a
    finished seed; the accounts are checked too, so a database where they were deleted
    (by hand, or by an older interrupted reset) is rebuilt instead of left unusable.
    """
    family = families_db.get_family(db, DEMO_FAMILY_ID)
    if family is None:
        return False
    found = {u.id: u.email for u in users_db.get_users(db, list(DEMO_USER_IDS))}
    return all(found.get(a.user_id) == a.email for a in DEMO_ACCOUNTS)


# ----------------------------------------------------------------------------- build

def _demo_seed(created: bool) -> DemoSeed:
    return DemoSeed(
        family_id=DEMO_FAMILY_ID,
        accounts=[(a.email, DEMO_PASSWORD, a.person_id) for a in DEMO_ACCOUNTS],
        person_ids=list(DEMO_PERSON_IDS),
        memory_ids=list(DEMO_MEMORY_IDS),
        created=created,
        tradition_ids=list(DEMO_TRADITION_IDS),
    )


def _build(db: Database, data: DemoData) -> None:
    linked = {a.person_id: a.user_id for a in DEMO_ACCOUNTS}
    base = datetime.now(UTC)

    for account in DEMO_ACCOUNTS:
        users_db.create_user(
            db,
            email=account.email,
            password_hash=auth.hash_password(DEMO_PASSWORD),
            name=account.name,
            user_id=account.user_id,
            now=base,
        )

    for person in demo_people():
        people_db.create_person(
            db,
            DEMO_FAMILY_ID,
            schemas.NewPerson(
                name=person.name,
                born=person.born,
                died=person.died,
                parent_ids=person.parent_ids,
                spouse_ids=person.spouse_ids,
                character=person.character,
            ),
            added_by=_ADMIN_USER_ID,
            user_id=linked.get(person.id),
            person_id=person.id,
        )

    for art in data.art:  # before the props and sets that point at it
        media_db.put_media(db, DEMO_FAMILY_ID, art.png, content_type="image/png", filename=art.filename,
                           kind="cutout", media_id=art.media_id)
    for prop in data.props:
        props_db.insert_prop(db, DEMO_FAMILY_ID, prop, prop_id=prop.id)
    for set_def in data.sets:
        props_db.insert_set(db, DEMO_FAMILY_ID, set_def, set_id=set_def.id)

    photo_ids = _put_demo_photos(db)
    for offset, moment in enumerate(MOMENTS):
        scene = data.scenes[moment.id]
        tellings = [
            schemas.Telling(
                author_id=t.author_id,
                question=t.question,
                text=t.text,
                photo_ids=list(photo_ids) if t.photos else [],
            )
            for t in moment.tellings
        ]
        memories_db.create_memory(
            db,
            DEMO_FAMILY_ID,
            title=moment.title,
            year=moment.year,
            participant_ids=moment.participant_ids,
            telling=tellings[0],
            prop_ids=scene.prop_ids,
            set_ids=scene.set_ids,
            memory_id=moment.id,
            now=base + timedelta(seconds=offset),
        )
        for telling in tellings[1:]:
            memories_db.add_telling(db, DEMO_FAMILY_ID, moment.id, telling)
        memories_db.save_scene(
            db,
            DEMO_FAMILY_ID,
            moment.id,
            screenplay=scene.screenplay,
            timeline=scene.timeline,
            prop_ids=scene.prop_ids,
            set_ids=scene.set_ids,
        )

    for offset, ((person_id, text, memory_id), question_id) in enumerate(
        zip(DEMO_QUESTIONS, DEMO_QUESTION_IDS, strict=True)
    ):
        memories_db.create_question(db, DEMO_FAMILY_ID, person_id=person_id, text=text, memory_id=memory_id,
                                    question_id=question_id, now=base + timedelta(seconds=offset))

    for offset, (tradition_id, created_by, request) in enumerate(DEMO_TRADITIONS):
        photos = [photo_ids[i] for i in _TRADITION_PHOTOS.get(tradition_id, ())]
        traditions_db.create_tradition(
            db, DEMO_FAMILY_ID, request.model_copy(update={"photo_ids": photos}),
            created_by=created_by, tradition_id=tradition_id, now=base + timedelta(seconds=offset),
        )

    # The family document goes last, with every member in the same insert: its existence
    # marks a complete seed, so an interrupted run never leaves a half-joined family.
    families_db.create_family(
        db, name=DEMO_FAMILY_NAME, creator_user_id=_ADMIN_USER_ID, family_id=DEMO_FAMILY_ID, now=base,
        members=[
            schemas.Member(user_id=account.user_id, role=account.role, joined_at=base + timedelta(seconds=offset))
            for offset, account in enumerate(DEMO_ACCOUNTS[1:], start=1)
        ],
    )


def seed_demo(db: Database, *, reset: bool = False) -> DemoSeed:
    """Seed the demo family. Idempotent.

    When the demo is complete (demo_is_complete: the family and every demo account exist)
    and reset is False, nothing is written and the result has created=False. Otherwise
    every screenplay is compiled first (a DemoDataError or DemoCompileError is raised
    before anything is deleted or written), then any leftover demo data (from reset, or
    from an interrupted earlier run, since the family document is written last) is deleted
    and everything is rebuilt, with the same ids every time. Data outside the demo family,
    demo users and demo media is never touched; the demo users' other families keep them,
    since the accounts come back under the same ids (and their sessions stay valid).
    """
    if not reset and demo_is_complete(db):
        return _demo_seed(created=False)
    data = load_demo_data()
    _purge(db)
    _build(db, data)
    return _demo_seed(created=True)


# ------------------------------------------------------------------------------- CLI

EXIT_OK = 0
EXIT_BAD_DATA = 1  # a file in seed/demo_family/ is malformed or does not compile
EXIT_NO_DATABASE = 3  # MongoDB is unreachable or refused the writes
# argparse exits with 2 on a bad command line.


_DB_HINT = (
    "Check MONGODB_URI and MONGODB_DB (or run `uv run python -m seed.preflight` for a full check), "
    "or run the API with STITCH_FAKE_DB=1 to use the in-memory demo database instead."
)


def _vector_index_line(outcomes: list) -> str:
    """One line for ensure_search_indexes_report's outcomes (best effort; never fails the seed)."""
    by_action: dict[str, list[str]] = {}
    for outcome in outcomes:
        by_action.setdefault(outcome.action, []).append(outcome.name)
    if set(by_action) <= {"not_created"}:
        return ("Vector search indexes: not supported on this server (not Atlas); "
                "ask and prop matching use keyword search.")
    words = {"created": "created", "existed": "already present", "updated": "updated (stale definition)",
             "wrong_type": "blocked by a search index of another type", "not_created": "not created"}
    parts = [f"{words[action]} {', '.join(names)}" for action, names in by_action.items()]
    return "Vector search indexes: " + "; ".join(parts) + " (python -m seed.preflight shows their status)."


def main(argv: list[str] | None = None, *, connect=None) -> int:
    """`python -m seed.demo [--reset | --cut-out-art]`. Exit codes: 0 seeded or already present, 1 bad demo
    data (nothing was written), 2 bad arguments, 3 MongoDB unreachable or failing. Errors go
    to stderr as one readable message, never a traceback, and never hold the URI's
    credentials or query string.

    It loads api/.env and ../.env itself (seed.realdb.load_env: variables already set win;
    not under pytest or with STITCH_NO_DOTENV=1), connects with a short timeout
    (seed.realdb.connect, or `connect` with the same signature), creates the indexes and,
    best effort, the Atlas Vector Search indexes, then seeds. A database installed with
    db.client.set_db (tests) is used as it is, without connecting.
    """
    import contextlib
    import logging
    import os
    import sys

    from pymongo.errors import PyMongoError

    from agent.preflight import scrub, scrubbed_logs, secret_values
    from db import client
    from seed import realdb

    parser = argparse.ArgumentParser(prog="python -m seed.demo", description=__doc__.splitlines()[0])
    parser.add_argument("--reset", action="store_true", help="delete the demo data and rebuild it")
    parser.add_argument("--cut-out-art", action="store_true",
                        help="save white-background images in seed/demo_family/art/ with transparency "
                             "(no database needed), so the seed starts faster")
    args = parser.parse_args(argv)
    if args.cut_out_art:
        names = cut_out_demo_art()
        print(f"Saved {len(names)} demo art file(s) with transparency" + (f": {', '.join(names)}" if names else "."))
        return EXIT_OK

    mongo_client = None
    target: str | None = None  # the URI connected to (None: a database installed with set_db)
    installed = getattr(client, "_db_override", None)
    if installed is not None:
        db = installed
        secrets: list[str] = secret_values(os.environ)
    else:
        realdb.load_env()
        uri = realdb.uri_from_env()
        db_name = realdb.db_name_from_env()
        target = uri or realdb.DEFAULT_MONGODB_URI
        secrets = secret_values({**os.environ, "MONGODB_URI": target})
        if uri is None:
            print(
                "MONGODB_URI is not set (looked in the environment, api/.env, ../.env): seeding "
                f"db.client's default, {realdb.DEFAULT_MONGODB_URI}."
            )
        # Flushed, so it is on screen before a slow connect (or above its error on stderr).
        print(f"Seeding {realdb.redacted(target)}, database {db_name!r} ...", flush=True)
        try:
            mongo_client, db = (connect or realdb.connect)(
                target, db_name, timeout_ms=realdb.DEFAULT_TIMEOUT_MS, appname="stitch-seed"
            )
        except Exception as exc:  # noqa: BLE001 - one readable sentence, never a traceback
            print(f"Demo seed failed: {realdb.describe_failure(exc, secrets, uri=target)} {_DB_HINT}",
                  file=sys.stderr)
            return EXIT_NO_DATABASE

    db_log = logging.getLogger("db.client")
    level = db_log.level
    try:
        with scrubbed_logs(secrets):
            # The vector index line below says what the per-index warnings would.
            db_log.setLevel(logging.ERROR)
            try:
                client.ensure_indexes(db)
                index_line = _vector_index_line(client.ensure_search_indexes_report(db))
                result = seed_demo(db, reset=args.reset)
            finally:
                db_log.setLevel(level)
    except (DemoDataError, DemoCompileError) as exc:
        print(f"Demo seed stopped before writing anything: {exc}", file=sys.stderr)
        return EXIT_BAD_DATA
    except PyMongoError as exc:
        print(
            f"Demo seed failed on database {db.name!r}: "
            f"{realdb.describe_failure(exc, secrets, uri=target)} {_DB_HINT}",
            file=sys.stderr,
        )
        return EXIT_NO_DATABASE
    finally:
        if mongo_client is not None:
            with contextlib.suppress(Exception):
                mongo_client.close()

    status = "seeded" if result.created else "already present (use --reset to rebuild)"
    print(scrub(index_line, secrets))
    print(f"Demo family {result.family_id!r} ({DEMO_FAMILY_NAME}) {status} in database {db.name!r}.")
    print(f"People: {', '.join(result.person_ids)}")
    print(f"Memories: {len(result.memory_ids)}")
    print(f"Accounts (password {DEMO_PASSWORD!r}):")
    for email, _password, person_id in result.accounts:
        print(f"  {email:<20} person {person_id}")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
