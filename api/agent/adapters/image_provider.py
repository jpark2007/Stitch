"""Which image provider draws prop and set art: one rule for agent.production and agent.preflight.

    IMAGE_PROVIDER=higgsfield   Higgsfield (HIGGSFIELD_API_KEY [+ HIGGSFIELD_API_SECRET])
    IMAGE_PROVIDER=cloudflare   Cloudflare Workers AI (CLOUDFLARE_ACCOUNT_ID + CLOUDFLARE_API_TOKEN)
    IMAGE_PROVIDER=openai       the OpenAI-compatible images API (IMAGE_API_KEY)
    unset                       the first whose keys are set, in this order: Higgsfield
                                (HIGGSFIELD_API_KEY), Cloudflare (both CLOUDFLARE_ACCOUNT_ID and
                                CLOUDFLARE_API_TOKEN), OpenAI (IMAGE_API_KEY); else placeholder art
    anything else               a warning, then the unset rule

Cloudflare sits between the two older providers, so an existing setup keeps its provider: one
with a Higgsfield key stays on Higgsfield, and one with only IMAGE_API_KEY stays on OpenAI unless
both Cloudflare variables are also set (a lone CLOUDFLARE_API_TOKEN, as wrangler uses, does not
switch it).

An explicit provider whose key is not set gives placeholder art (with the reason), never another
provider: the owner said which service may be billed.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from agent.adapters import cloudflare, higgsfield, images
from agent.guards import env_value

PROVIDER_ENV = "IMAGE_PROVIDER"
PROVIDERS: tuple[str, ...] = ("higgsfield", "openai", "cloudflare")
"""The values IMAGE_PROVIDER takes (the unset rule's order is in the module docstring)."""


@dataclass(frozen=True)
class ImageProviderChoice:
    provider: str
    """'higgsfield', 'cloudflare', 'openai' or 'placeholder'."""
    reason: str
    """Why, in one short clause (for logs and the preflight report)."""
    warning: str | None = None
    """Set when IMAGE_PROVIDER has an unknown value."""


def _cloudflare_missing(env: Mapping[str, str] | None) -> str | None:
    missing = [name for name in (cloudflare.ACCOUNT_ENV, cloudflare.TOKEN_ENV) if env_value(env, name) is None]
    return " and ".join(missing) if missing else None


def choose_image_provider(env: Mapping[str, str] | None = None) -> ImageProviderChoice:
    """The provider the studio uses for ``env`` (``os.environ`` when None). Never raises."""
    has_hf = env_value(env, higgsfield.API_KEY_ENV) is not None
    cf_missing = _cloudflare_missing(env)
    has_cf = cf_missing is None
    has_openai = env_value(env, images.API_KEY_ENV) is not None
    raw = env_value(env, PROVIDER_ENV)
    wanted = raw.lower() if raw else None
    if wanted == "higgsfield":
        if has_hf:
            return ImageProviderChoice("higgsfield", f"{PROVIDER_ENV}=higgsfield")
        return ImageProviderChoice("placeholder", f"{PROVIDER_ENV}=higgsfield but {higgsfield.API_KEY_ENV} is not set")
    if wanted == "cloudflare":
        if has_cf:
            return ImageProviderChoice("cloudflare", f"{PROVIDER_ENV}=cloudflare")
        verb = "are" if " and " in (cf_missing or "") else "is"
        return ImageProviderChoice("placeholder", f"{PROVIDER_ENV}=cloudflare but {cf_missing} {verb} not set")
    if wanted == "openai":
        if has_openai:
            return ImageProviderChoice("openai", f"{PROVIDER_ENV}=openai")
        return ImageProviderChoice("placeholder", f"{PROVIDER_ENV}=openai but {images.API_KEY_ENV} is not set")
    warning = None
    if wanted is not None:
        warning = f"{PROVIDER_ENV}={raw!r} is not one of {', '.join(PROVIDERS)}; using the default rule"
    if has_hf:
        return ImageProviderChoice("higgsfield", f"{higgsfield.API_KEY_ENV} is set", warning)
    if has_cf:
        return ImageProviderChoice(
            "cloudflare", f"{cloudflare.ACCOUNT_ENV} and {cloudflare.TOKEN_ENV} are set", warning
        )
    if has_openai:
        return ImageProviderChoice("openai", f"{images.API_KEY_ENV} is set", warning)
    return ImageProviderChoice(
        "placeholder",
        f"neither {higgsfield.API_KEY_ENV} nor {images.API_KEY_ENV} is set, "
        f"nor both {cloudflare.ACCOUNT_ENV} and {cloudflare.TOKEN_ENV}",
        warning,
    )


__all__ = ["PROVIDERS", "PROVIDER_ENV", "ImageProviderChoice", "choose_image_provider"]
