"""Fitting a generated image to the exact size the art service asked for.

Shared by every image adapter (``images.OpenAIImageModel``, ``higgsfield.HiggsfieldImageModel``)
so a prop or set layer comes out the same whichever provider drew it. The art is never scaled
unevenly: when its aspect ratio is within ``ASPECT_TOLERANCE`` (1%) of the target it is resized;
otherwise it is scaled evenly until it covers the target and then cropped to it, anchored at the
bottom edge and centered horizontally (set layers stand on the ground line at the bottom, so a
wide strip keeps the bottom band of a taller canvas and loses the top).
"""

from __future__ import annotations

import io

from PIL import Image

ASPECT_TOLERANCE = 0.01
"""A plain resize is used only when the source and target aspect ratios differ by at most 1%."""
MAX_PIXELS = 40_000_000
"""Largest generated image decoded (width x height); providers return a few megapixels."""


def has_alpha(im: Image.Image) -> bool:
    """True when ``im`` carries transparency (RGBA, LA, PA, or a palette with a transparent index)."""
    return im.mode in ("RGBA", "LA", "PA") or (im.mode == "P" and "transparency" in im.info)


def fit_to_size(im: Image.Image, size: tuple[int, int]) -> Image.Image:
    """Fit ``im`` to exactly ``size`` without uneven scaling.

    Within ``ASPECT_TOLERANCE`` of the target aspect it is a plain resize. Otherwise the image is
    scaled by one factor so it covers ``size``, then cropped to ``size`` anchored at the bottom
    edge and centered horizontally.
    """
    tw, th = size
    sw, sh = im.size
    if (sw, sh) == (tw, th):
        return im
    if abs((sw / sh) / (tw / th) - 1.0) <= ASPECT_TOLERANCE:
        return im.resize((tw, th), Image.Resampling.LANCZOS)
    scale = max(tw / sw, th / sh)
    # One factor for both axes; rounding can only add a pixel of slack, never squash.
    nw = max(tw, round(sw * scale))
    nh = max(th, round(sh * scale))
    scaled = im.resize((nw, nh), Image.Resampling.LANCZOS)
    left = (nw - tw) // 2
    top = nh - th
    return scaled.crop((left, top, left + tw, top + th))


def to_png(data: bytes, size: tuple[int, int]) -> bytes:
    """Decode ``data`` (any format Pillow reads), keep any alpha, fit to ``size``, encode as PNG.

    Raises ``ValueError`` before decoding when the image has more than ``MAX_PIXELS`` pixels (a
    small file can declare a huge canvas: a decompression bomb).
    """
    with Image.open(io.BytesIO(data)) as src:
        if src.width * src.height > MAX_PIXELS:
            raise ValueError(f"image is {src.width}x{src.height}, more than {MAX_PIXELS} pixels; refused")
        src.load()
        im = src.convert("RGBA" if has_alpha(src) else "RGB")
    im = fit_to_size(im, size)
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


__all__ = ["ASPECT_TOLERANCE", "MAX_PIXELS", "fit_to_size", "has_alpha", "to_png"]
