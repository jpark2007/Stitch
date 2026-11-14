"""Fixtures for the data layer: an in-memory mongomock db, and an opt-in real MongoDB.

Real-MongoDB tests use the `real_db` fixture and skip unless MONGODB_URI_TEST is set.
"""

from __future__ import annotations

import os
import uuid

import mongomock
import pytest
from pymongo import MongoClient

TEST_JWT_SECRET = "stitch-test-secret-0123456789abcdef0123456789"


@pytest.fixture(autouse=True)
def _jwt_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JWT_SECRET", TEST_JWT_SECRET)
    monkeypatch.delenv("COOKIE_SECURE", raising=False)


@pytest.fixture
def mdb():
    """A fresh in-memory database per test."""
    client = mongomock.MongoClient(tz_aware=True)
    yield client["stitch_test"]
    client.close()


@pytest.fixture
def real_db():
    """A throwaway database on a real MongoDB, dropped afterwards. Skips without MONGODB_URI_TEST."""
    uri = os.environ.get("MONGODB_URI_TEST")
    if not uri:
        pytest.skip("MONGODB_URI_TEST is not set")
    client = MongoClient(uri, tz_aware=True, serverSelectionTimeoutMS=3000)
    name = f"stitch_test_{uuid.uuid4().hex[:8]}"
    try:
        yield client[name]
    finally:
        client.drop_database(name)
        client.close()
