"""The demo family for the stage demo: three generations, three accounts, books ready to open.

README "Scope for the weekend": the stage demo starts with a full tree and books. This seeds

- the family "The Maos" (id `demo_family`), with David as admin and Ana and Sam as members;
- eight people in three generations with hand-picked character sheets (color, signature
  item, build, how they move); Luis and Sam wear their signature hats on stage;
- seven moments from 1958 to 2019, each with a hand-authored screenplay that the seed
  COMPILES with the real compiler (screenplay.compiler.compile) and stores with the
  resulting timeline, so every book opens on playable scenes. Six moments have two
  tellings by different people, and most have two to six participants so threads cross;
- the props and sets those scenes use (placeholder art: `art` null with a color);
- two placeholder photos on the 1972 road trip;
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

Run it against MONGODB_URI / MONGODB_DB from api/:

    uv run python -m seed.demo            # seed once; a no-op when the family exists
    uv run python -m seed.demo --reset    # delete the demo data and rebuild it

Exit codes: 0 done, 1 bad demo data (nothing written), 2 bad arguments, 3 MongoDB
unreachable or failing; errors are one message on stderr, never a traceback. Every run writes the same ids
(people, memories, props, sets, photos, questions), so two seeded databases match.

Every read and write, including the reset, goes through the db/ functions; the reset is
scoped to the demo family id, the demo user ids and the demo emails, so non-demo data is
never touched.
"""

from __future__ import annotations

import argparse
import io
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import NamedTuple

from PIL import Image, ImageDraw
from pydantic import ValidationError
from pymongo.database import Database

import auth
import schemas
from db import families as families_db
from db import media as media_db
from db import memories as memories_db
from db import people as people_db
from db import props as props_db
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


def load_demo_data(directory: Path | None = None) -> DemoData:
    """Load, check and compile every moment's screenplay. Nothing touches a database.

    `directory` defaults to DATA_DIR (read at call time). Raises DemoDataError for a
    malformed or inconsistent file (naming it) and DemoCompileError for a screenplay that
    does not compile.
    """
    directory = DATA_DIR if directory is None else directory
    props = load_props(directory / PROPS_PATH.name)
    sets = load_sets(directory / SETS_PATH.name)
    by_id_props = {p.id: p for p in props}
    by_id_sets = {s.id: s for s in sets}
    people = scene_people()
    for person in people.values():
        item = person.character.item_prop_id
        if item is not None and item not in by_id_props:
            raise DemoDataError(f"{PROPS_PATH.name}: no prop {item!r}, the signature item of {person.id}")
    stray = sorted(
        p.name for p in directory.glob(f"*{SCREENPLAY_SUFFIX}")
        if p.name[: -len(SCREENPLAY_SUFFIX)] not in DEMO_MEMORY_IDS
    )
    if stray:
        raise DemoDataError(f"screenplays for no demo moment in {directory.name}/: {', '.join(stray)}")
    scenes: dict[str, DemoScene] = {}
    for moment in MOMENTS:
        screenplay = load_screenplay(moment.id, directory)
        _check_moment(moment, screenplay, people)
        timeline = compile_moment(screenplay, sets=by_id_sets, props=by_id_props, people=people)
        set_ids = list(dict.fromkeys(shot.set for shot in screenplay.shots))
        scenes[moment.id] = DemoScene(screenplay, timeline, list(screenplay.props), set_ids)
    return DemoData(props, sets, scenes)


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


def main(argv: list[str] | None = None) -> int:
    """`python -m seed.demo [--reset]`. Exit codes: 0 seeded or already present, 1 bad demo
    data (nothing was written), 2 bad arguments, 3 MongoDB unreachable or failing. Errors go
    to stderr as one readable message, never a traceback."""
    import sys

    from pymongo.errors import PyMongoError

    from db import client

    parser = argparse.ArgumentParser(prog="python -m seed.demo", description=__doc__.splitlines()[0])
    parser.add_argument("--reset", action="store_true", help="delete the demo data and rebuild it")
    args = parser.parse_args(argv)

    db = client.get_db()
    try:
        client.ensure_indexes(db)
        result = seed_demo(db, reset=args.reset)
    except (DemoDataError, DemoCompileError) as exc:
        print(f"Demo seed stopped before writing anything: {exc}", file=sys.stderr)
        return EXIT_BAD_DATA
    except PyMongoError as exc:
        print(
            f"Demo seed failed: MongoDB error on database {db.name!r} ({type(exc).__name__}: {exc}). "
            "Check MONGODB_URI and MONGODB_DB, or run the API with STITCH_FAKE_DB=1 to use the "
            "in-memory demo database instead.",
            file=sys.stderr,
        )
        return EXIT_NO_DATABASE

    status = "seeded" if result.created else "already present (use --reset to rebuild)"
    print(f"Demo family {result.family_id!r} ({DEMO_FAMILY_NAME}) {status} in database {db.name!r}.")
    print(f"People: {', '.join(result.person_ids)}")
    print(f"Memories: {len(result.memory_ids)}")
    print(f"Accounts (password {DEMO_PASSWORD!r}):")
    for email, _password, person_id in result.accounts:
        print(f"  {email:<20} person {person_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
