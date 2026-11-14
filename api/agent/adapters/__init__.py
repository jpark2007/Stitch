"""Production adapters for the agent's service seams.

- ``AnthropicVision``: ``agent.tools.photos.VisionModel`` (Claude vision).
- ``OpenAIImageModel``: ``agent.tools.art.ImageModel`` (an OpenAI-compatible images API).
- ``ElevenLabsTTS``: ``agent.tools.narration.TTS`` (speech with word timings).
- ``AlphaAwareRemover``: ``agent.tools.art.BackgroundRemover`` (local, Pillow only).

Each reads its key from the environment and raises ``RuntimeError`` naming the variable
when it is missing; tests inject fake clients or ``httpx.MockTransport`` instead.

Every network adapter is bounded, so a hung provider cannot stall a run (each module's
docstring has the details):

    adapter            request timeout                          retries
    -----------------  ---------------------------------------  ------------------------------
    AnthropicVision    STITCH_MODEL_TIMEOUT_S (120 s)           STITCH_MODEL_MAX_RETRIES (2), by the SDK
    OpenAIImageModel   120 s read/write, 10 s connect           1, only for no connection or
                                                                HTTP 408/429/5xx
    ElevenLabsTTS      30 s read/write, 10 s connect            2, same rules

A call that still fails raises, and the tool that made it degrades instead of failing the
run: placeholder art (``agent.tools.art.ArtService``), silent narration with estimated word
timings (``agent.tools.narration``), a photo or frame critique skipped
(``agent.tools.photos``, ``agent.tools.frames``). ``AlphaAwareRemover`` is local Pillow code
with no network; when it raises, the cutout is a placeholder too.
"""

from agent.adapters.cutout import AlphaAwareRemover
from agent.adapters.images import OpenAIImageModel
from agent.adapters.tts import ElevenLabsTTS
from agent.adapters.vision import AnthropicVision

__all__ = ["AlphaAwareRemover", "AnthropicVision", "ElevenLabsTTS", "OpenAIImageModel"]
