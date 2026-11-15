"""Fixtures for the replay tests: api/ on sys.path, a seeded in-memory database, the example story.

Run from the repo root: `uv run --project api pytest demo/replay/tests -q`.
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

REPO_DIR = Path(__file__).resolve().parents[3]
API_DIR = REPO_DIR / "api"
for _path in (API_DIR, REPO_DIR):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import mongomock  # noqa: E402

import schemas  # noqa: E402
from db import client as db_client  # noqa: E402
from db import families as families_db  # noqa: E402
from db import people as people_db  # noqa: E402
from seed.demo import seed_demo  # noqa: E402

from demo.replay.fixtures import PreparedStory, load_stories  # noqa: E402

EXAMPLE_DIR = REPO_DIR / "demo" / "replay" / "example"
TIMEOUT = 10.0
FID = "demo_family"
DAVID = "demo_user_david"
ANA = "demo_user_ana"
FAM_B = "fam_b"
B_USER = "user_b"
B_TELLER = "64b000000000000000000001"

EXAMPLE_TEXT = (
    "Every Sunday my mother made tamales, and the whole kitchen smelled of corn husks. "
    "I was allowed to spread the masa, badly. My father pretended every lumpy one was the "
    "best he had ever eaten."
)
OTHER_TEXT = "We waited at the bus stop in the snow. Nobody had a hat."


@pytest.fixture
def mdb():
    client = mongomock.MongoClient(tz_aware=True)
    yield client["stitch_replay_test"]
    client.close()


@pytest.fixture
def db(mdb):
    db_client.ensure_indexes(mdb)
    seed_demo(mdb)
    families_db.create_family(mdb, name="Family B", creator_user_id=B_USER, family_id=FAM_B)
    people_db.create_person(
        mdb, FAM_B, schemas.NewPerson(name="Bea", born=1950), added_by=B_USER, user_id=B_USER, person_id=B_TELLER
    )
    return mdb


@pytest.fixture
def example() -> list[PreparedStory]:
    return load_stories(EXAMPLE_DIR)


def fields(text: str = EXAMPLE_TEXT, **kw: Any) -> schemas.SubmitMemoryFields:
    base: dict[str, Any] = {"text": text, "year": 1999, "question": "What were Sundays like?"}
    base.update(kw)
    return schemas.SubmitMemoryFields(**base)


def keep_all(payload: schemas.PropsFoundEvent) -> schemas.AnswerRequest:
    return schemas.AnswerRequest(kind="props", props=[schemas.PropEdit(id=c.id, action="keep") for c in payload.props])


def drive(
    mgr: Any,
    family_id: str,
    run_id: str,
    user_id: str,
    *,
    reply: str = "Grandma Carmen and Papa.",
    review: Callable[[schemas.PropsFoundEvent], schemas.AnswerRequest] = keep_all,
) -> list[tuple[str, Any]]:
    """Stream a run to its end, answering the question, a merge (yes) and the props review."""

    async def go() -> list[tuple[str, Any]]:
        events: list[tuple[str, Any]] = []
        async for name, payload in mgr.stream_run(family_id, run_id):
            events.append((name, payload))
            if name == "question":
                mgr.answer_run(family_id, run_id, user_id, schemas.AnswerRequest(kind="answer", text=reply))
            elif name == "merge":
                mgr.answer_run(family_id, run_id, user_id, schemas.AnswerRequest(kind="merge", approve=True))
            elif name == "props_found":
                mgr.answer_run(family_id, run_id, user_id, review(payload))
        return events

    return asyncio.run(asyncio.wait_for(go(), TIMEOUT))


def names(events: list[tuple[str, Any]]) -> list[str]:
    return [name for name, _ in events]


def payloads(events: list[tuple[str, Any]], name: str) -> list[Any]:
    return [payload for n, payload in events if n == name]
