"""The demo family for the stage demo: three generations, three accounts, books ready to open.

README "Scope for the weekend": the stage demo starts with a full tree and books. This seeds

- the family "The Maos" (id `demo_family`), with David as admin and Ana and Sam as members;
- eight people in three generations with hand-picked character sheets;
- the hand-written hymn-book scene from web/public/samples/hymn_books.memory.json (memory,
  prop, set, screenplay and timeline), plus five more memories without scenes across the
  eras, and two placeholder photos;
- two open questions the real studio can answer: Sam is asked for his telling of the
  church-steps day (the hymn-book memory, the only one with a scene, so the studio can
  add to it), and Ana gets a general prompt about the day Lily was born, which starts a
  new memory. A question never points at a memory without a scene, because the real
  studio cannot add a telling to one.

Run it against MONGODB_URI / MONGODB_DB from api/:

    uv run python -m seed.demo            # seed once; a no-op when the family exists
    uv run python -m seed.demo --reset    # delete the demo data and rebuild it

Every read and write, including the reset, goes through the db/ functions; the reset is
scoped to the demo family id, the demo user ids and the demo emails, so non-demo data is
never touched.
"""

from __future__ import annotations

import argparse
import io
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import NamedTuple

from PIL import Image, ImageDraw
from pymongo.database import Database

import auth
import schemas
from db import families as families_db
from db import media as media_db
from db import memories as memories_db
from db import people as people_db
from db import props as props_db
from db import users as users_db

REPO_DIR = Path(__file__).resolve().parents[2]
SAMPLE_PATH = REPO_DIR / "web" / "public" / "samples" / "hymn_books.memory.json"

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


# ------------------------------------------------------------------------------ data

def load_sample() -> schemas.MemoryResponse:
    """The hand-written hymn-book scene, validated against the API contract."""
    return schemas.MemoryResponse.model_validate_json(SAMPLE_PATH.read_text())


def _sample_people() -> dict[str, dict]:
    raw = json.loads(SAMPLE_PATH.read_text())
    return {p["id"]: p for p in raw["people"]}


def _cs(color: str, item: str, build: str, notes: str | None = None) -> schemas.CharacterSheet:
    return schemas.CharacterSheet(color=color, item=item, build=build, notes=notes)


class _P(NamedTuple):
    id: str
    name: str
    born: int
    died: int | None
    parent_ids: list[str]
    spouse_ids: list[str]
    character: schemas.CharacterSheet


def demo_people() -> list[_P]:
    """Everyone in the tree, parents before children. David and Ana match the sample scene."""
    sample = _sample_people()
    dad = schemas.CharacterSheet.model_validate(sample["dad"]["character"])
    mom = schemas.CharacterSheet.model_validate(sample["mom"]["character"])
    return [
        _P("grandpa_joseph", "Joseph", 1934, 2011, [], [],
           _cs("#7d9a5b", "walking cane", "short", "walks slowly with a cane")),
        _P("grandma_rosa", "Rosa", 1938, None, [], ["grandpa_joseph"],
           _cs("#c77d9b", "pearl necklace", "small", "small quick steps, always humming")),
        _P("grandpa_luis", "Luis", 1936, 2015, [], [],
           _cs("#d9973a", "straw hat", "tall", "stands very straight, hands clasped behind his back")),
        _P("grandma_carmen", "Carmen", 1940, None, [], ["grandpa_luis"],
           _cs("#8a5a9e", "knitted cardigan", "short", "sways a little when she laughs")),
        _P("dad", sample["dad"]["name"], sample["dad"]["born"], None,
           ["grandpa_joseph", "grandma_rosa"], [], dad),
        _P("mom", sample["mom"]["name"], sample["mom"]["born"], None,
           ["grandpa_luis", "grandma_carmen"], ["dad"], mom),
        _P("sam", "Sam", 1990, None, ["dad", "mom"], [], _cs("#2f8f8a", "baseball cap", "average")),
        _P("lily", "Lily", 1993, None, ["dad", "mom"], [], _cs("#e0b84f", "yellow raincoat", "small")),
    ]


DEMO_PERSON_IDS: tuple[str, ...] = (
    "grandpa_joseph", "grandma_rosa", "grandpa_luis", "grandma_carmen", "dad", "mom", "sam", "lily",
)


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


# Memories without a scene yet, oldest first. Every teller is in the tree; the books show
# them as the family wrote them.
EXTRA_MEMORIES: tuple[_M, ...] = (
    _M("demo_rosa_joseph_wedding", "Rosa and Joseph's wedding", 1958,
       ["grandma_rosa", "grandpa_joseph"], [
           _T("grandma_rosa", "What do you remember about your wedding day?",
              "It rained all morning and I was sure the whole day was ruined. Joseph showed up "
              "with his good shoes soaked through and did not say a word about it. By the time we "
              "came out of the church the sun was out and everyone was laughing at his socks."),
           _T("dad", "What did your parents tell you about their wedding?",
              "Mom always says it rained, and Dad always says it didn't. The one photo we have "
              "shows wet steps and a lot of umbrellas, so I think she wins that one."),
       ]),
    _M("demo_sunday_tamales", "Sunday tamales at Carmen's", 1970,
       ["grandma_carmen", "grandpa_luis", "mom"], [
           _T("mom", "What did Sundays look like when you were little?",
              "Every Sunday my mother made tamales and the whole kitchen smelled of corn husks. "
              "I was allowed to spread the masa, badly. My father pretended every lumpy one was "
              "the best he had ever eaten."),
       ]),
    _M("demo_green_sedan_trip", "The road trip in the green sedan", 1972,
       ["grandpa_joseph", "grandma_rosa", "dad"], [
           _T("dad", "Tell me about a trip you took as a kid.",
              "Dad bought a green sedan that summer and decided we had to drive it all the way to "
              "the coast. I sat in the back with a map I could not read. We got a flat tire outside "
              "a peach stand and ate peaches on the hood while he fixed it.", photos=True),
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
              "louder than anyone in the hall."),
           _T("lily", "Mom says you turned a trip into a bow. What really happened?",
              "My gown was too long and I caught the hem on the second step. I figured if I bowed "
              "nobody would know. Sam knew, and he still brings it up every Thanksgiving."),
       ]),
)

HYMN_MEMORY_ID = "sample_hymn_books"
DEMO_MEMORY_IDS: tuple[str, ...] = (HYMN_MEMORY_ID,) + tuple(m.id for m in EXTRA_MEMORIES)

# (person id, text, memory id). A memory id must name a memory with a scene: the real
# studio raises MemoryNotFound for one without, so answering on stage would fail.
DEMO_QUESTIONS: tuple[tuple[str, str, str | None], ...] = (
    ("sam", "Mom and Dad met on the church steps. What's the version of that day you grew up hearing?",
     HYMN_MEMORY_ID),
    ("mom", "What do you remember about the day Lily was born?", None),
)


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


def _put_demo_photos(db: Database) -> list[str]:
    photos = [
        ("green_sedan_1972.png", _placeholder_png("1972", "#bcd4e0", "#c9b48a", "#5e8c4a")),
        ("peach_stand_1972.png", _placeholder_png("peaches", "#f2d8b5", "#9fb27a", "#d4826a")),
    ]
    return [
        media_db.put_media(db, DEMO_FAMILY_ID, data, content_type="image/png", filename=name, kind="photo")
        for name, data in photos
    ]


# ----------------------------------------------------------------------------- reset

def _purge(db: Database) -> None:
    """Delete everything that belongs to the demo, and nothing else."""
    users_db.delete_users(db, emails=list(DEMO_EMAILS), user_ids=list(DEMO_USER_IDS))
    families_db.purge_family(db, DEMO_FAMILY_ID)  # the family, its data and its media


# ----------------------------------------------------------------------------- build

def _demo_seed(created: bool) -> DemoSeed:
    return DemoSeed(
        family_id=DEMO_FAMILY_ID,
        accounts=[(a.email, DEMO_PASSWORD, a.person_id) for a in DEMO_ACCOUNTS],
        person_ids=list(DEMO_PERSON_IDS),
        memory_ids=list(DEMO_MEMORY_IDS),
        created=created,
    )


def _build(db: Database) -> None:
    sample = load_sample()
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

    for prop in sample.props:
        props_db.insert_prop(db, DEMO_FAMILY_ID, prop, prop_id=prop.id)
    for set_def in sample.sets:
        props_db.insert_set(db, DEMO_FAMILY_ID, set_def, set_id=set_def.id)

    # The hymn-book memory: David's telling, Ana's telling, then the hand-written scene.
    memory = sample.memory
    first, *rest = memory.tellings
    memories_db.create_memory(
        db,
        DEMO_FAMILY_ID,
        title=memory.title,
        year=memory.year,
        participant_ids=memory.participant_ids,
        telling=first,
        prop_ids=memory.prop_ids,
        set_ids=memory.set_ids,
        memory_id=memory.id,
        now=base,
    )
    for telling in rest:
        memories_db.add_telling(db, DEMO_FAMILY_ID, memory.id, telling)
    memories_db.save_scene(
        db,
        DEMO_FAMILY_ID,
        memory.id,
        screenplay=memory.screenplay,
        timeline=memory.timeline,
        prop_ids=memory.prop_ids,
        set_ids=memory.set_ids,
    )

    photo_ids = _put_demo_photos(db)
    for offset, extra in enumerate(EXTRA_MEMORIES, start=1):
        tellings = [
            schemas.Telling(
                author_id=t.author_id,
                question=t.question,
                text=t.text,
                photo_ids=list(photo_ids) if t.photos else [],
            )
            for t in extra.tellings
        ]
        memories_db.create_memory(
            db,
            DEMO_FAMILY_ID,
            title=extra.title,
            year=extra.year,
            participant_ids=extra.participant_ids,
            telling=tellings[0],
            memory_id=extra.id,
            now=base + timedelta(seconds=offset),
        )
        for telling in tellings[1:]:
            memories_db.add_telling(db, DEMO_FAMILY_ID, extra.id, telling)

    for person_id, text, memory_id in DEMO_QUESTIONS:
        memories_db.create_question(db, DEMO_FAMILY_ID, person_id=person_id, text=text, memory_id=memory_id)

    # The family document goes last: its existence marks a complete seed.
    families_db.create_family(db, name=DEMO_FAMILY_NAME, creator_user_id=_ADMIN_USER_ID,
                              family_id=DEMO_FAMILY_ID, now=base)
    for offset, account in enumerate(DEMO_ACCOUNTS[1:], start=1):
        families_db.add_member(db, DEMO_FAMILY_ID, account.user_id, account.role,
                               now=base + timedelta(seconds=offset))


def seed_demo(db: Database, *, reset: bool = False) -> DemoSeed:
    """Seed the demo family. Idempotent.

    When the family already exists and reset is False, nothing is written and the result
    has created=False. Otherwise any leftover demo data (from reset, or from an interrupted
    earlier run, since the family document is written last) is deleted and everything is
    rebuilt. Data outside the demo family, demo users and demo media is never touched.
    """
    if not reset and families_db.get_family(db, DEMO_FAMILY_ID) is not None:
        return _demo_seed(created=False)
    _purge(db)
    _build(db)
    return _demo_seed(created=True)


# ------------------------------------------------------------------------------- CLI

def main(argv: list[str] | None = None) -> int:
    from db import client

    parser = argparse.ArgumentParser(prog="python -m seed.demo", description=__doc__.splitlines()[0])
    parser.add_argument("--reset", action="store_true", help="delete the demo data and rebuild it")
    args = parser.parse_args(argv)

    db = client.get_db()
    client.ensure_indexes(db)
    result = seed_demo(db, reset=args.reset)

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
