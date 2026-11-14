"""AlphaAwareRemover: the production ``art.BackgroundRemover`` for art on a white background.

The style prompt asks the image model for each prop on a plain white background, so the
background is the near-white region that touches the image border. Rules:

1. If the image already has real transparency (at least ``TRANSPARENT_FRACTION`` of its
   pixels have alpha below 250), it is returned unchanged (as an RGBA PNG; an RGBA PNG input
   comes back byte for byte).
2. Otherwise the background is found by flood fill from the image border over near-white
   pixels (every channel >= ``threshold``, default 235). White areas enclosed by the subject
   (a white plate, a shirt, the inside of a ring) are not reached, so they stay opaque. This
   is the difference from ``art.FakeBackgroundRemover``, which clears every near-white pixel.
3. The alpha edge is feathered by 1 px inward only: subject pixels next to the background
   become partly transparent, background pixels stay fully transparent (no white halo).

Pure Pillow: no model, no network, deterministic.
"""

from __future__ import annotations

import io

from PIL import Image, ImageChops, ImageDraw, ImageFilter

DEFAULT_THRESHOLD = 235
TRANSPARENT_ALPHA = 250
"""A pixel counts as transparent when its alpha is below this."""
TRANSPARENT_FRACTION = 0.01
"""An image with at least this fraction of transparent pixels is already cut out."""

_BACKGROUND = 128  # flood-fill marker in the near-white mask


def _png(im: Image.Image) -> bytes:
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def _transparent_fraction(rgba: Image.Image) -> float:
    hist = rgba.getchannel("A").histogram()
    total = rgba.width * rgba.height
    return sum(hist[:TRANSPARENT_ALPHA]) / total if total else 0.0


class AlphaAwareRemover:
    """``art.BackgroundRemover``: keep real alpha, else flood-fill the white background away."""

    def __init__(self, threshold: int = DEFAULT_THRESHOLD, feather: int = 1) -> None:
        if not 0 <= threshold <= 255:
            raise ValueError("threshold must be 0..255")
        if feather < 0:
            raise ValueError("feather must be >= 0")
        self.threshold = threshold
        self.feather = feather

    def remove(self, png: bytes) -> bytes:
        with Image.open(io.BytesIO(png)) as src:
            fmt = src.format
            src.load()
            rgba = src.convert("RGBA")
            was_rgba = src.mode == "RGBA"
        if rgba.width == 0 or rgba.height == 0:
            raise ValueError("image is empty")

        if _transparent_fraction(rgba) >= TRANSPARENT_FRACTION:
            return png if (fmt == "PNG" and was_rgba) else _png(rgba)

        alpha = self._background_alpha(rgba)
        if self.feather:
            # Inward feather: never raise alpha, so cleared background pixels stay at 0.
            soft = alpha.filter(ImageFilter.BoxBlur(self.feather))
            alpha = ImageChops.darker(alpha, soft)
        rgba.putalpha(alpha)
        return _png(rgba)

    def _background_alpha(self, rgba: Image.Image) -> Image.Image:
        """Mode 'L': 0 for the border-connected near-white background, 255 elsewhere."""
        t = self.threshold
        r, g, b, _ = rgba.split()
        r, g, b = (ch.point(lambda v: 255 if v >= t else 0) for ch in (r, g, b))
        near_white = ImageChops.darker(ImageChops.darker(r, g), b)

        # Pad with a 1 px near-white frame so one fill from a corner reaches every
        # near-white region that touches any edge of the image.
        w, h = rgba.size
        mask = Image.new("L", (w + 2, h + 2), 255)
        mask.paste(near_white, (1, 1))
        ImageDraw.floodfill(mask, (0, 0), _BACKGROUND, thresh=0)
        mask = mask.crop((1, 1, w + 1, h + 1))
        return mask.point(lambda v: 0 if v == _BACKGROUND else 255)


__all__ = ["DEFAULT_THRESHOLD", "AlphaAwareRemover"]
