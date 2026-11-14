"""Production adapters for the agent's service seams.

- ``AnthropicVision``: ``agent.tools.photos.VisionModel`` (Claude vision).
- ``OpenAIImageModel``: ``agent.tools.art.ImageModel`` (an OpenAI-compatible images API).
- ``ElevenLabsTTS``: ``agent.tools.narration.TTS`` (speech with word timings).
- ``AlphaAwareRemover``: ``agent.tools.art.BackgroundRemover`` (local, Pillow only).

Each reads its key from the environment and raises ``RuntimeError`` naming the variable
when it is missing; tests inject fake clients or ``httpx.MockTransport`` instead.
"""

from agent.adapters.cutout import AlphaAwareRemover
from agent.adapters.images import OpenAIImageModel
from agent.adapters.tts import ElevenLabsTTS
from agent.adapters.vision import AnthropicVision

__all__ = ["AlphaAwareRemover", "AnthropicVision", "ElevenLabsTTS", "OpenAIImageModel"]
