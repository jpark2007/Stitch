"""Which image provider draws prop and set art: one rule for agent.production and agent.preflight.

    IMAGE_PROVIDER=higgsfield   Higgsfield (HIGGSFIELD_API_KEY [+ HIGGSFIELD_API_SECRET])
    IMAGE_PROVIDER=openai       the OpenAI-compatible images API (IMAGE_API_KEY)
    unset                       Higgsfield when HIGGSFIELD_API_KEY is set, else OpenAI when
                                IMAGE_API_KEY is set, else placeholder art
    anything else               a warning, then the unset rule

An explicit provider whose key is not set gives placeholder art (with the reason), never the
other provider: the owner said which service may be billed.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from agent.adapters import higgsfield, images
from agent.guards import env_value

PROVIDER_ENV = "IMAGE_PROVIDER"
PROVIDERS: tuple[str, ...] = ("higgsfield", "openai")


@dataclass(frozen=True)
class ImageProviderChoice:
    provider: str
    """'higgsfield', 'openai' or 'placeholder'."""
    reason: str
    """Why, in one short clause (for logs and the preflight report)."""
    warning: str | None = None
    """Set when IMAGE_PROVIDER has an unknown value."""


def choose_image_provider(env: Mapping[str, str] | None = None) -> ImageProviderChoice:
    """The provider the studio uses for ``env`` (``os.environ`` when None). Never raises."""
    has_hf = env_value(env, higgsfield.API_KEY_ENV) is not None
    has_openai = env_value(env, images.API_KEY_ENV) is not None
    raw = env_value(env, PROVIDER_ENV)
    wanted = raw.lower() if raw else None
    if wanted == "higgsfield":
        if has_hf:
            return ImageProviderChoice("higgsfield", f"{PROVIDER_ENV}=higgsfield")
        return ImageProviderChoice("placeholder", f"{PROVIDER_ENV}=higgsfield but {higgsfield.API_KEY_ENV} is not set")
    if wanted == "openai":
        if has_openai:
            return ImageProviderChoice("openai", f"{PROVIDER_ENV}=openai")
        return ImageProviderChoice("placeholder", f"{PROVIDER_ENV}=openai but {images.API_KEY_ENV} is not set")
    warning = None
    if wanted is not None:
        warning = f"{PROVIDER_ENV}={raw!r} is not one of {', '.join(PROVIDERS)}; using the default rule"
    if has_hf:
        return ImageProviderChoice("higgsfield", f"{higgsfield.API_KEY_ENV} is set", warning)
    if has_openai:
        return ImageProviderChoice("openai", f"{images.API_KEY_ENV} is set", warning)
    return ImageProviderChoice(
        "placeholder", f"neither {higgsfield.API_KEY_ENV} nor {images.API_KEY_ENV} is set", warning
    )


__all__ = ["PROVIDERS", "PROVIDER_ENV", "ImageProviderChoice", "choose_image_provider"]
