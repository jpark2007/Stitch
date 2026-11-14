"""Production adapters for the agent's service seams.

- ``AnthropicVision``: ``agent.tools.photos.VisionModel`` (Claude vision).
- ``OpenAIImageModel``: ``agent.tools.art.ImageModel`` (an OpenAI-compatible images API).
- ``HiggsfieldImageModel``: ``agent.tools.art.ImageModel`` (the Higgsfield API: submit, poll,
  download). ``choose_image_provider`` picks between the two (``IMAGE_PROVIDER``).
- ``ElevenLabsTTS``: ``agent.tools.narration.TTS`` (speech with word timings).
- ``AlphaAwareRemover``: ``agent.tools.art.BackgroundRemover`` (local, Pillow only).

Each reads its key from the environment and raises ``RuntimeError`` naming the variable
when it is missing; tests inject fake clients or ``httpx.MockTransport`` instead.

Every network adapter is bounded, so a hung provider cannot stall a run (each module's
docstring has the details):

    adapter            request timeout                          retries
    -----------------  ---------------------------------------  ------------------------------
    AnthropicVision    STITCH_MODEL_TIMEOUT_S (120 s)           STITCH_MODEL_MAX_RETRIES (2), by the
                                                                adapter (SDK max_retries=0): a failed
                                                                connection, HTTP 408/409/429/5xx;
                                                                never a timeout
    OpenAIImageModel   120 s read/write, 10 s connect           1, only for no connection or
                                                                HTTP 408/429/5xx
    HiggsfieldImage-   120 s for submit + polls + download,     1 submit, only for no connection or
    Model              30 s per call, 10 s connect              HTTP 408/429/5xx (same idempotency
                                                                key); never after a request id
    ElevenLabsTTS     30 s read/write, 10 s connect            2, same rules

Every wait before a retry is capped at ``images.MAX_BACKOFF_S`` (8 s), whatever the provider's
``Retry-After`` asks. The anthropic SDK alone would obey any ``Retry-After`` uncapped, which is
why ``AnthropicVision`` retries itself; its worst case per call is
``vision.worst_case_s(timeout, max_retries)``, 376 s with the defaults.

A call that still fails raises, and the tool that made it degrades instead of failing the
run: placeholder art (``agent.tools.art.ArtService``), silent narration with estimated word
timings (``agent.tools.narration``), a photo or frame critique skipped
(``agent.tools.photos``, ``agent.tools.frames``). ``AlphaAwareRemover`` is local Pillow code
with no network; when it raises, the cutout is a placeholder too.
"""

from agent.adapters.cutout import AlphaAwareRemover
from agent.adapters.higgsfield import HiggsfieldImageModel
from agent.adapters.image_provider import ImageProviderChoice, choose_image_provider
from agent.adapters.images import OpenAIImageModel
from agent.adapters.tts import ElevenLabsTTS
from agent.adapters.vision import AnthropicVision

__all__ = [
    "AlphaAwareRemover",
    "AnthropicVision",
    "ElevenLabsTTS",
    "HiggsfieldImageModel",
    "ImageProviderChoice",
    "OpenAIImageModel",
    "choose_image_provider",
]
