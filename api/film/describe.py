"""Describe: structured facts about each chosen photo (film.types.Describer).

`ModelDescriber` asks the existing vision seam (agent.tools.photos.VisionModel; in
production agent.adapters.AnthropicVision) for strict JSON, one call per photo, on a copy
downscaled to DESCRIBE_EDGE px. Its reply is parsed tolerantly (code fences and chatter
around the object are ignored, missing or odd keys are filled from the offline describer);
any failure falls back to `FakeDescriber` for that photo only.

`FakeDescriber` is deterministic and offline: a mood from the dominant color (Pillow), a
centre focus, quality from contrast. It knows nothing about the content, so its caption,
people and setting are empty (the film shows no caption rather than a made-up one) and it
gives no "scene" label (the optional key the plan uses to leave out repeated moments).
"""

from __future__ import annotations

import colorsys
import io
import json
import logging
import re
from typing import Any

from PIL import Image, ImageOps, ImageStat

from film.types import Describer

log = logging.getLogger(__name__)

DESCRIBE_EDGE = 1024
"""Long edge (px) of the copy sent to the vision model."""

MAX_CAPTION_WORDS = 8

DESCRIBE_PROMPT = """\
This is one photo from a family's set of pictures of a single event. Describe it for a short
narrated film made from the photos. Reply with ONLY a JSON object, no other text, exactly:

{"caption": "<at most 8 words: what is happening, plain and warm>",
 "scene": "<2 to 5 lowercase words naming the moment: who, doing what, where>",
 "people": ["<each visible person as seen, e.g. 'a girl in a red coat'>"],
 "setting": "<where it is, e.g. 'a lake dock at dusk'>",
 "mood": "<one or two words>",
 "focus": [<u>, <v>],
 "quality": <0 to 1>}

scene is a short, plain, generic label of the moment shown, e.g. "family at dinner table",
"kids in the lake", "cutting the birthday cake". Use the simplest common words, no names, no
colours or camera details, so that two photos of the same moment get the same label.
focus is the point of interest as fractions across (u) and down (v) the photo, 0 to 1:
usually the face or the action. quality is how usable the photo is: 1 for a sharp, well lit,
well framed photo, near 0 for a blurred, dark or accidental one.
Do not guess who anyone is, their names, or how they are related. Do not invent what you
cannot see. Use an empty list when nobody is visible.
"""


# ------------------------------------------------------------------ offline describer

_NAMED = [
    ("red", 0), ("orange", 30), ("yellow", 55), ("green", 120),
    ("teal", 175), ("blue", 220), ("purple", 275), ("pink", 330), ("red", 360),
]


def color_name(rgb: tuple[int, int, int]) -> str:
    r, g, b = (c / 255 for c in rgb)
    h, lightness, s = colorsys.rgb_to_hls(r, g, b)
    if lightness < 0.15:
        return "black"
    if lightness > 0.88:
        return "white"
    if s < 0.18:
        return "gray"
    hue = h * 360
    if 15 <= hue < 45 and lightness < 0.45:
        return "brown"
    return min(_NAMED, key=lambda named: abs(named[1] - hue))[0]


_WARM = {"red", "orange", "yellow", "brown", "pink"}


class FakeDescriber:
    """Deterministic stand-in: mood from the dominant color, centre focus, contrast as quality."""

    def __init__(self) -> None:
        self.calls = 0

    def describe(self, photo: bytes) -> dict:
        self.calls += 1
        try:
            with Image.open(io.BytesIO(photo)) as img:
                img.draft("RGB", (128, 128))
                upright = ImageOps.exif_transpose(img)
                small = upright.convert("RGB").resize((32, 32), Image.Resampling.BOX)
        except Exception:  # noqa: BLE001 - an unreadable image still gets a neutral description
            return blank_description()
        quant = small.quantize(colors=8, method=Image.Quantize.MEDIANCUT)
        palette = quant.getpalette() or []
        counts = quant.getcolors() or [(1, 0)]
        _, index = max(counts, key=lambda c: (c[0], -c[1]))
        rgb = tuple(palette[index * 3 : index * 3 + 3]) or (0, 0, 0)
        color = color_name(rgb)  # type: ignore[arg-type]
        contrast = ImageStat.Stat(small.convert("L")).stddev[0]
        return {
            "caption": "",
            "people": [],
            "setting": "",
            "mood": "warm" if color in _WARM else "quiet",
            "focus": [0.5, 0.5],
            "quality": round(max(0.2, min(1.0, contrast / 64)), 3),
        }


def blank_description() -> dict:
    return {"caption": "", "people": [], "setting": "", "mood": "", "focus": [0.5, 0.5], "quality": 0.5}


# ------------------------------------------------------------------ parsing a model reply

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def extract_json_object(text: str) -> dict:
    """The first JSON object in a model reply (inside a code fence or bare). ValueError if none."""
    if not isinstance(text, str):
        raise ValueError("reply is not text")
    candidates = [m.group(1) for m in _FENCE.finditer(text)] + [text]
    for candidate in candidates:
        start = candidate.find("{")
        while start != -1:
            try:
                value, _ = json.JSONDecoder().raw_decode(candidate, start)
            except ValueError:
                start = candidate.find("{", start + 1)
                continue
            if isinstance(value, dict):
                return value
            start = candidate.find("{", start + 1)
    raise ValueError("no JSON object in the reply")


def _clean_str(value: Any, limit: int = 200) -> str | None:
    if not isinstance(value, str):
        return None
    return " ".join(value.split())[:limit]


def short_caption(text: str | None, max_words: int = MAX_CAPTION_WORDS) -> str | None:
    """A caption of at most `max_words` words, trailing punctuation trimmed; None if empty."""
    if not text:
        return None
    words = text.split()[:max_words]
    out = " ".join(words).rstrip(" ,;:-—")
    if len(words) == max_words and len(text.split()) > max_words:
        out = out.rstrip(".!?")
    return out or None


def _fraction(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value != value:  # NaN
        return None
    return max(0.0, min(1.0, float(value)))


MAX_SCENE_CHARS = 60


def normalize(raw: dict, fallback: dict) -> dict:
    """A Describer dict from a parsed reply: every key present and well typed, `fallback` fills
    gaps. The optional "scene" is kept (lower case, at most MAX_SCENE_CHARS) when the reply
    has one as text; otherwise it is whatever `fallback` has, or absent."""
    out = dict(fallback)
    caption = _clean_str(raw.get("caption"))
    if caption is not None:
        out["caption"] = short_caption(caption) or ""
    scene = _clean_str(raw.get("scene"), MAX_SCENE_CHARS)
    if scene is not None:
        out["scene"] = scene.lower()
    people = raw.get("people")
    if isinstance(people, list):
        out["people"] = [p for p in (_clean_str(x, 80) for x in people) if p][:12]
    for key in ("setting", "mood"):
        value = _clean_str(raw.get(key), 80)
        if value is not None:
            out[key] = value
    focus = raw.get("focus")
    if isinstance(focus, (list, tuple)) and len(focus) == 2:
        u, v = _fraction(focus[0]), _fraction(focus[1])
        if u is not None and v is not None:
            out["focus"] = [u, v]
    quality = _fraction(raw.get("quality"))
    if quality is not None:
        out["quality"] = quality
    return out


def parse_description(text: str, fallback: dict) -> dict:
    """Describer dict from a vision reply. ValueError when the reply holds no JSON object."""
    return normalize(extract_json_object(text), fallback)


# ------------------------------------------------------------------ model describer


def downscale(photo: bytes, edge: int = DESCRIBE_EDGE) -> bytes:
    """An upright JPEG copy no longer than `edge` px on its long side."""
    with Image.open(io.BytesIO(photo)) as img:
        img.draft("RGB", (edge, edge))
        upright = ImageOps.exif_transpose(img).convert("RGB")
    upright.thumbnail((edge, edge), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    upright.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


class ModelDescriber:
    """film.types.Describer over a photos.VisionModel, one call per photo."""

    def __init__(self, vision: Any, fallback: Describer | None = None) -> None:
        self.vision = vision
        self.fallback: Describer = fallback or FakeDescriber()

    def describe(self, photo: bytes) -> dict:
        offline = self.fallback.describe(photo)
        try:
            reply = self.vision.describe([downscale(photo)], DESCRIBE_PROMPT)
            return parse_description(reply, offline)
        except Exception as exc:  # noqa: BLE001 - a failed description costs only this photo's detail
            log.warning("film describe: vision failed (%s); using the offline description", type(exc).__name__)
            return offline


__all__ = [
    "DESCRIBE_EDGE",
    "DESCRIBE_PROMPT",
    "MAX_CAPTION_WORDS",
    "MAX_SCENE_CHARS",
    "FakeDescriber",
    "ModelDescriber",
    "blank_description",
    "downscale",
    "extract_json_object",
    "normalize",
    "parse_description",
    "short_caption",
]
