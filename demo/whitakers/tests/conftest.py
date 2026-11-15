"""Fixtures for the second-family tests: api/ on sys.path and a SYNTHETIC family ("The Testers").

Run from the repo root: `uv run --project api pytest demo/whitakers/tests -q`. The fixture in
fixture/data/ is made up for the tests; no real family's data belongs in this folder.
"""

from __future__ import annotations

import json
import shutil
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

from db import client as db_client  # noqa: E402
from seed.demo import seed_demo  # noqa: E402

FIXTURE_DIR = Path(__file__).resolve().parent / "fixture" / "data"
FID = "testers"
CLEO = "cleo@testers.example"
DEV = "dev@testers.example"
PASSWORD = "tester-pass"
# The fixture's prepared story (replays/porch_books), typed as the teller would type it.
PORCH_TEXT = "We met on the porch steps when I dropped all my library books and he picked them up."


@pytest.fixture
def data_copy(tmp_path) -> Path:
    """A writable copy of the fixture data directory."""
    target = tmp_path / "data"
    shutil.copytree(FIXTURE_DIR, target)
    return target


def edit_json(path: Path, change: Callable[[Any], Any]) -> None:
    """Rewrite a JSON file after `change` mutates its value in place."""
    value = json.loads(path.read_text())
    change(value)
    path.write_text(json.dumps(value, indent=2))


@pytest.fixture
def mdb():
    client = mongomock.MongoClient(tz_aware=True)
    db = client["stitch_whitakers_test"]
    db_client.ensure_indexes(db)
    seed_demo(db)
    yield db
    client.close()
