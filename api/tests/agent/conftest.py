"""Agent tests never see a developer's Higgsfield settings or image-provider choice.

Without this, a HIGGSFIELD_API_KEY or IMAGE_PROVIDER exported in the shell would switch the
image provider under tests written for the OpenAI-compatible adapter or for placeholder art.
Tests that want them set them with monkeypatch.
"""

from __future__ import annotations

from typing import Any

import pytest

from db import client as db_client

IMAGE_PROVIDER_ENVS = (
    "IMAGE_PROVIDER",
    "HIGGSFIELD_API_KEY",
    "HIGGSFIELD_API_SECRET",
    "HIGGSFIELD_API_URL",
    "HIGGSFIELD_IMAGE_MODEL",
    "HIGGSFIELD_STYLE_SUFFIX",
    "HIGGSFIELD_RESULT_HOSTS",
)


@pytest.fixture(autouse=True)
def _no_image_provider_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in IMAGE_PROVIDER_ENVS:
        monkeypatch.delenv(name, raising=False)


def _no_real_app_client(*_args: Any, **_kwargs: Any) -> Any:
    raise AssertionError(
        "db.client.get_client() built the real process-wide MongoClient in an agent test; "
        "use the mongomock override (db_client.set_db(mongomock...)) instead"
    )


@pytest.fixture(autouse=True)
def _no_real_app_mongo_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """No agent test may create the API's real process-wide MongoClient (db.client.get_client).

    That client is lazy but global: once built it keeps a background monitor thread trying
    localhost:27017 for the rest of the session, which trips the socket spies of later tests
    (test_agent_preflight's and test_agent_production's no_network). A test that reaches
    db_client.get_db() without db_client.set_db(<mongomock db>) fails here, at the cause.
    Tests against a real MongoDB (MONGODB_URI_TEST) build their own pymongo client and are
    not affected.
    """
    monkeypatch.setattr(db_client, "MongoClient", _no_real_app_client)
