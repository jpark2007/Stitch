"""default_services: the describer, editor and narrator make_film uses, chosen by mode.

`mode` is the resolved STITCH_AGENT value. Anything but "real" is fully offline:
FakeDescriber, no editor (the deterministic plan) and a silent narrator with estimated
timings. "real" builds each real service on its own, and each falls back to its offline
stand-in (logged, by exception type only) when its key is missing or it cannot be built:

    describer  ModelDescriber over agent.adapters.AnthropicVision     ANTHROPIC_API_KEY
    editor     ModelEditor over agent.studio.make_model (STITCH_MODEL) ANTHROPIC_API_KEY
    narrator   TTSNarrator over agent.adapters.ElevenLabsTTS           ELEVENLABS_API_KEY

The real adapters are imported only in "real" mode.
"""

from __future__ import annotations

import logging
import os

from film.describe import FakeDescriber, ModelDescriber
from film.narrate import TTSNarrator, offline_narrator
from film.plan import ModelEditor
from film.types import Describer, Editor, Narrator, Services

log = logging.getLogger(__name__)

ANTHROPIC_KEY = "ANTHROPIC_API_KEY"
ELEVENLABS_KEY = "ELEVENLABS_API_KEY"


def _has(name: str) -> bool:
    return bool((os.environ.get(name) or "").strip())


def offline_services() -> Services:
    return Services(describer=FakeDescriber(), editor=None, narrator=offline_narrator())


def _describer() -> Describer:
    if not _has(ANTHROPIC_KEY):
        log.info("films: %s is not set; photos get offline descriptions", ANTHROPIC_KEY)
        return FakeDescriber()
    try:
        from agent.adapters.vision import AnthropicVision

        return ModelDescriber(AnthropicVision())
    except Exception as exc:  # noqa: BLE001 - a broken adapter means the offline describer
        log.warning("films: could not build the vision describer (%s); using offline descriptions", type(exc).__name__)
        return FakeDescriber()


def _editor() -> Editor | None:
    if not _has(ANTHROPIC_KEY):
        log.info("films: %s is not set; films use the fallback plan", ANTHROPIC_KEY)
        return None
    try:
        from agent.studio import make_model

        return ModelEditor(make_model())
    except Exception as exc:  # noqa: BLE001 - a broken model means the fallback plan
        log.warning("films: could not build the editor model (%s); using the fallback plan", type(exc).__name__)
        return None


def _narrator() -> Narrator:
    if not _has(ELEVENLABS_KEY):
        log.info("films: %s is not set; films are silent with estimated timings", ELEVENLABS_KEY)
        return offline_narrator()
    try:
        from agent.adapters.tts import ElevenLabsTTS

        return TTSNarrator(ElevenLabsTTS())
    except Exception as exc:  # noqa: BLE001 - a broken TTS means silent narration
        log.warning("films: could not build the speech service (%s); films are silent", type(exc).__name__)
        return offline_narrator()


def default_services(mode: str) -> Services:
    """Offline fakes unless mode is "real"; then real services, each with its own fallback."""
    if (mode or "").strip().lower() != "real":
        return offline_services()
    return Services(describer=_describer(), editor=_editor(), narrator=_narrator())


__all__ = ["default_services", "offline_services"]
