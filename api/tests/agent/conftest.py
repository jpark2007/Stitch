"""Agent tests never see a developer's Higgsfield settings or image-provider choice.

Without this, a HIGGSFIELD_API_KEY or IMAGE_PROVIDER exported in the shell would switch the
image provider under tests written for the OpenAI-compatible adapter or for placeholder art.
Tests that want them set them with monkeypatch.
"""

from __future__ import annotations

import pytest

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
