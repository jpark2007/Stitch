"""Ingest: read each uploaded photo once with Pillow.

For every photo it records the EXIF capture time (DateTimeOriginal, else DateTime), the
upright width and height (EXIF orientations 5-8 swap them, as db.media.image_aspect does), a
cheap sharpness score (variance of the Laplacian on a fixed-size grayscale copy, so photos of
different resolutions compare fairly) and three visual signatures of the upright image for
look-alike detection (film.selection.looks_alike):

- `dhash`: a 64-bit difference hash of the grayscale copy (fine structure; moves when the
  framing shifts by a few percent);
- `layout` and `tone`: a LAYOUT_GRID x LAYOUT_GRID colour thumbnail with each channel's mean
  removed and divided by the photo's own contrast, and the photo's overall colour balance.
  Coarse and cheap: used to rule out clearly different photos before the costly comparison;
- `structure` (`Structure`): a STRUCTURE_SIZE thumbnail in YCbCr, each channel normalised to
  its own mean and spread (so exposure, contrast, saturation and white balance cancel out),
  with its 3x3 neighbourhood maxima and minima. Fine enough to see a person; the comparison
  (film.selection.structure_alike) lines two photos up over a few crops and shifts first.

A photo Pillow cannot open or decode is skipped (logged), never fatal.
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass, field
from datetime import datetime

from PIL import Image, ImageFilter, ImageStat

from film.types import SourcePhoto

log = logging.getLogger(__name__)

ANALYSIS_EDGE = 256
"""Long edge (px) of the copy the hash, sharpness and colour layout are computed on."""

LAYOUT_GRID = 8
"""The colour layout is LAYOUT_GRID x LAYOUT_GRID cells of RGB (box-averaged)."""
LAYOUT_SCALE = 32
"""Layout values are stored as 128 + LAYOUT_SCALE * (deviation / contrast), clipped to a byte."""
LAYOUT_MIN_CONTRAST = 6.0
"""The contrast a near-flat photo is divided by, so sensor noise is not blown up."""
TONE_MIN_LEVEL = 8.0
"""The brightness a near-black photo's tone is measured against."""

STRUCTURE_SIZE = (48, 36)
"""The structure thumbnail (width, height); every photo is squashed to it, whatever its shape."""
STRUCTURE_SCALE = 32
"""Structure values are stored as 128 + STRUCTURE_SCALE * (value - mean) / spread, clipped."""
LUMA_MIN_SPREAD = 4.0
"""The brightness spread a near-flat photo is divided by, so noise is not blown up."""
CHROMA_MIN_SPREAD = 6.0
"""The same for the two colour channels."""
COLOURFUL_SPREAD = 2.0
"""A photo whose colour channels spread less than this is treated as black and white."""

_ORIENTATION = 0x0112
_EXIF_IFD = 0x8769
_DATETIME_ORIGINAL = 0x9003
_DATETIME = 0x0132
_ROTATED = frozenset({5, 6, 7, 8})

# What ImageOps.exif_transpose does for each EXIF orientation, applied to the small copy.
_TRANSPOSE = {
    2: Image.Transpose.FLIP_LEFT_RIGHT,
    3: Image.Transpose.ROTATE_180,
    4: Image.Transpose.FLIP_TOP_BOTTOM,
    5: Image.Transpose.TRANSPOSE,
    6: Image.Transpose.ROTATE_270,
    7: Image.Transpose.TRANSVERSE,
    8: Image.Transpose.ROTATE_90,
}

_LAPLACIAN = ImageFilter.Kernel((3, 3), [0, 1, 0, 1, -4, 1, 0, 1, 0], scale=1, offset=128)


@dataclass(frozen=True)
class Structure:
    """A photo's normalised YCbCr thumbnail (mode "RGB" holding Y, Cb, Cr) and its 3x3
    neighbourhood maxima and minima, for film.selection.structure_alike."""

    image: Image.Image
    high: Image.Image
    low: Image.Image
    colourful: bool  # False for a black and white photo: compare brightness only
    detail: float  # spread of the raw brightness; near 0 for a flat photo


@dataclass(frozen=True)
class Ingested:
    """One readable photo and what the pipeline knows about it before any model call."""

    id: str
    data: bytes
    order: int  # position in the upload list
    taken_at: datetime | None
    width: int  # upright
    height: int  # upright
    sharpness: float
    dhash: int  # 64-bit difference hash of the upright image
    layout: bytes = b""  # contrast-normalised colour layout (see colour_layout); b"" = unknown
    tone: tuple[float, float, float] = (1.0, 1.0, 1.0)  # each channel's mean over the overall mean
    structure: Structure | None = field(default=None, compare=False, repr=False)  # None = unknown

    @property
    def portrait(self) -> bool:
        return self.height > self.width


def _parse_exif_time(raw: object) -> datetime | None:
    if isinstance(raw, bytes):
        raw = raw.decode("ascii", "ignore")
    if not isinstance(raw, str):
        return None
    try:
        return datetime.strptime(raw.strip().rstrip("\x00")[:19], "%Y:%m:%d %H:%M:%S")
    except ValueError:
        return None


def _exif_facts(img: Image.Image) -> tuple[int, datetime | None]:
    """(orientation, capture time) from the EXIF block; a broken block means (1, None)."""
    try:
        exif = img.getexif()
    except Exception:  # noqa: BLE001 - a broken EXIF block means "upright, time unknown"
        return 1, None
    try:
        orientation = int(exif.get(_ORIENTATION) or 1)
    except (TypeError, ValueError):
        orientation = 1
    taken: datetime | None = None
    try:
        taken = _parse_exif_time(exif.get_ifd(_EXIF_IFD).get(_DATETIME_ORIGINAL))
    except Exception:  # noqa: BLE001 - a missing or broken sub-IFD
        taken = None
    if taken is None:
        taken = _parse_exif_time(exif.get(_DATETIME))
    return orientation, taken


def dhash(gray: Image.Image) -> int:
    """64-bit difference hash: each bit says whether a pixel is brighter than its right neighbor."""
    px = gray.resize((9, 8), Image.Resampling.BOX).tobytes()  # mode L: one byte per pixel
    bits = 0
    for row in range(8):
        for col in range(8):
            bits = (bits << 1) | (1 if px[row * 9 + col] > px[row * 9 + col + 1] else 0)
    return bits


def hamming(a: int, b: int) -> int:
    return (a ^ b).bit_count()


def sharpness(gray: Image.Image) -> float:
    """Variance of the Laplacian: higher is sharper (blur flattens edges)."""
    return float(ImageStat.Stat(gray.filter(_LAPLACIAN)).var[0])


def colour_layout(rgb: Image.Image) -> tuple[bytes, tuple[float, float, float]]:
    """(layout, tone) of an RGB image.

    layout: LAYOUT_GRID x LAYOUT_GRID box-averaged cells, RGB interleaved, each value
    128 + LAYOUT_SCALE * (value - its channel's mean) / contrast, clipped to 0..255, where
    contrast is the mean absolute deviation over all cells and channels (at least
    LAYOUT_MIN_CONTRAST). Removing each channel's mean and dividing by the contrast makes it
    blind to exposure and white balance; the coarse grid makes a few percent of shift or crop
    move it only a little.

    tone: each channel's mean over the mean of all three (1, 1, 1 for a neutral photo), so a
    brighter or darker copy keeps its tone.
    """
    cells = LAYOUT_GRID * LAYOUT_GRID
    px = rgb.resize((LAYOUT_GRID, LAYOUT_GRID), Image.Resampling.BOX).tobytes()
    means = [sum(px[c::3]) / cells for c in range(3)]
    dev = [v - means[i % 3] for i, v in enumerate(px)]
    contrast = max(sum(abs(d) for d in dev) / len(dev), LAYOUT_MIN_CONTRAST)
    layout = bytes(max(0, min(255, round(128 + LAYOUT_SCALE * d / contrast))) for d in dev)
    level = max(sum(means) / 3, TONE_MIN_LEVEL)
    tone = (means[0] / level, means[1] / level, means[2] / level)
    return layout, tone


def _normalised(channel: Image.Image, min_spread: float) -> tuple[Image.Image, float]:
    """`channel` (mode L) mapped to 128 + STRUCTURE_SCALE * (v - mean) / spread, and the spread."""
    stat = ImageStat.Stat(channel)
    mean, spread = stat.mean[0], stat.stddev[0]
    scale = STRUCTURE_SCALE / max(spread, min_spread)
    return channel.point([max(0, min(255, round(128 + scale * (v - mean)))) for v in range(256)]), spread


def structure(rgb: Image.Image) -> Structure:
    """The structure signature of an RGB image (see the module docstring)."""
    y, cb, cr = rgb.resize(STRUCTURE_SIZE, Image.Resampling.BOX).convert("YCbCr").split()
    y, detail = _normalised(y, LUMA_MIN_SPREAD)
    cb, cb_spread = _normalised(cb, CHROMA_MIN_SPREAD)
    cr, cr_spread = _normalised(cr, CHROMA_MIN_SPREAD)
    image = Image.merge("RGB", (y, cb, cr))
    return Structure(
        image=image,
        high=image.filter(ImageFilter.MaxFilter(3)),
        low=image.filter(ImageFilter.MinFilter(3)),
        colourful=max(cb_spread, cr_spread) >= COLOURFUL_SPREAD,
        detail=detail,
    )


def ingest_one(photo: SourcePhoto, order: int) -> Ingested | None:
    """What Pillow can read of one photo, or None when it cannot open or decode it."""
    try:
        with Image.open(io.BytesIO(photo.data)) as img:
            width, height = img.size
            orientation, taken = _exif_facts(img)
            img.draft("RGB", (ANALYSIS_EDGE * 2, ANALYSIS_EDGE * 2))  # cheap JPEG downscale on decode
            rgb = img.convert("RGB")
    except Exception:  # noqa: BLE001 - any undecodable upload is skipped
        log.info("film ingest: skipping %s, not a readable image (%d bytes)", photo.id, len(photo.data))
        return None
    if width <= 0 or height <= 0:
        return None
    if orientation in _ROTATED:
        width, height = height, width
    rgb.thumbnail((ANALYSIS_EDGE, ANALYSIS_EDGE), Image.Resampling.BILINEAR)
    if orientation in _TRANSPOSE:
        rgb = rgb.transpose(_TRANSPOSE[orientation])
    gray = rgb.convert("L")
    layout, tone = colour_layout(rgb)
    return Ingested(
        id=photo.id,
        data=photo.data,
        order=order,
        taken_at=taken,
        width=width,
        height=height,
        sharpness=sharpness(gray),
        dhash=dhash(gray),
        layout=layout,
        tone=tone,
        structure=structure(rgb),
    )


def ingest(photos: list[SourcePhoto]) -> list[Ingested]:
    """Every readable photo, in upload order. Unreadable ones are skipped."""
    out: list[Ingested] = []
    for order, photo in enumerate(photos):
        found = ingest_one(photo, order)
        if found is not None:
            out.append(found)
    return out


__all__ = [
    "ANALYSIS_EDGE",
    "LAYOUT_GRID",
    "LAYOUT_SCALE",
    "STRUCTURE_SIZE",
    "Ingested",
    "Structure",
    "colour_layout",
    "dhash",
    "hamming",
    "ingest",
    "ingest_one",
    "sharpness",
    "structure",
]
