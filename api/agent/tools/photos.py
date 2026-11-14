"""describe_photo: what is in one of this run's photos, read by a vision model.

The vision model sits behind `VisionModel` so the pipeline runs offline: tests use
`FakeVision`, which describes size and dominant color deterministically with Pillow. In
production it is `agent.adapters.AnthropicVision` (Claude vision, bounded timeout and retries).

A photo is extra detail, never required: when the vision model or the media store fails, or
the model answers with nothing, describe_photo returns one `could not describe photo <id>:
<reason>` line (the exception type only; its message goes to the server log) and the run
goes on without that photo's description.
"""

from __future__ import annotations

import colorsys
import io
import logging
from collections.abc import Callable, Sequence
from typing import Protocol

from langchain_core.tools import BaseTool, StructuredTool
from PIL import Image, UnidentifiedImageError

log = logging.getLogger(__name__)

PHOTO_PROMPT = """\
Describe this family photo so it can be rebuilt as a cut-paper pop-up scene.
Cover, in short plain sentences:
- The setting: indoors or outdoors, what kind of place, time of day and season if clear.
- The objects that matter, each with its color and rough size (handheld, furniture, large).
- Era clues: clothing styles, cars, signs, furniture or technology that date the photo.
- The people: how many, where they stand, what they are doing, and the colors of their
  clothing and any hats, glasses or carried items.
Do not guess who anyone is, their names, or how they are related. Do not invent details
you cannot see; say "unclear" instead.
"""

UNKNOWN_PHOTO = "unknown photo"


class VisionModel(Protocol):
    def describe(self, images: Sequence[bytes], prompt: str) -> str:
        """Describe the images, following the prompt."""
        ...


_NAMED = [
    ("red", 0), ("orange", 30), ("yellow", 55), ("green", 120),
    ("teal", 175), ("blue", 220), ("purple", 275), ("pink", 330), ("red", 360),
]


def _color_name(rgb: tuple[int, int, int]) -> str:
    r, g, b = (c / 255 for c in rgb)
    h, l, s = colorsys.rgb_to_hls(r, g, b)
    if l < 0.15:
        return "black"
    if l > 0.88:
        return "white"
    if s < 0.18:
        return "gray"
    hue = h * 360
    if 15 <= hue < 45 and l < 0.45:
        return "brown"
    return min(_NAMED, key=lambda named: abs(named[1] - hue))[0]


def _describe_one(data: bytes) -> str:
    try:
        with Image.open(io.BytesIO(data)) as img:
            width, height = img.size
            small = img.convert("RGB").resize((32, 32), Image.Resampling.BOX)
    except (UnidentifiedImageError, OSError, ValueError):
        return f"An image that could not be read ({len(data)} bytes)."
    # Quantize to a few colors so near-identical pixels count together, then take the most
    # common one; ties break on the color value so the result never depends on ordering.
    quant = small.quantize(colors=8, method=Image.Quantize.MEDIANCUT)
    palette = quant.getpalette() or []
    counts = quant.getcolors() or [(1, 0)]
    _, index = max(counts, key=lambda c: (c[0], -c[1]))
    rgb = tuple(palette[index * 3 : index * 3 + 3]) or (0, 0, 0)
    hex_color = "#{:02x}{:02x}{:02x}".format(*rgb)
    return f"A photo, mostly {_color_name(rgb)} ({hex_color}), {width}x{height}."


class FakeVision:
    """Deterministic stand-in: size and dominant color of each image, no model call."""

    def __init__(self) -> None:
        self.calls: list[tuple[int, str]] = []

    def describe(self, images: Sequence[bytes], prompt: str) -> str:
        self.calls.append((len(images), prompt))
        return " ".join(_describe_one(data) for data in images)


def make_photo_tools(
    vision: VisionModel,
    get_media: Callable[[str], tuple[bytes, str] | None],
    allowed_photo_ids: Sequence[str],
) -> list[BaseTool]:
    """Build describe_photo for one run.

    `get_media(media_id) -> (bytes, content_type) | None` must already be bound to the run's
    family; `allowed_photo_ids` are the photos submitted with this telling. Any other id is
    refused, so a prompt cannot read photos from another run or family.
    """
    allowed = frozenset(allowed_photo_ids)

    def describe_photo(photo_id: str) -> str:
        """Describe one of the photos the teller attached: setting, objects and their colors,
        era clues, and what people wear. Pass a photo id given in the task."""
        if photo_id not in allowed:
            return UNKNOWN_PHOTO
        try:
            media = get_media(photo_id)
        except Exception as e:  # noqa: BLE001 - a failed media store must not end the run
            log.warning("describe_photo could not read %s", photo_id, exc_info=True)
            return f"could not describe photo {photo_id}: {type(e).__name__}"
        if media is None:
            return UNKNOWN_PHOTO
        data, content_type = media
        if content_type and not content_type.startswith("image/"):
            return f"photo {photo_id} is not an image ({content_type})"
        try:
            described = vision.describe([data], PHOTO_PROMPT)
        except Exception as e:  # noqa: BLE001 - a failed vision call must not end the run
            log.warning("describe_photo: the vision model failed on %s", photo_id, exc_info=True)
            return f"could not describe photo {photo_id}: {type(e).__name__}"
        if not isinstance(described, str) or not described.strip():
            return f"could not describe photo {photo_id}: the vision model returned no text"
        return described

    return [StructuredTool.from_function(describe_photo, name="describe_photo")]


__all__ = [
    "PHOTO_PROMPT",
    "UNKNOWN_PHOTO",
    "FakeVision",
    "VisionModel",
    "make_photo_tools",
]
