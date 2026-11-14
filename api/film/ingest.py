"""Ingest: read each uploaded photo once with Pillow.

For every photo it records the EXIF capture time (DateTimeOriginal, else DateTime), the
upright width and height (EXIF orientations 5-8 swap them, as db.media.image_aspect does), a
64-bit difference hash (dHash) of the upright image for near-duplicate detection, and a cheap
sharpness score (variance of the Laplacian on a fixed-size grayscale copy, so photos of
different resolutions compare fairly).

A photo Pillow cannot open or decode is skipped (logged), never fatal.
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass
from datetime import datetime

from PIL import Image, ImageFilter, ImageStat

from film.types import SourcePhoto

log = logging.getLogger(__name__)

ANALYSIS_EDGE = 256
"""Long edge (px) of the grayscale copy the hash and sharpness are computed on."""

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


def ingest_one(photo: SourcePhoto, order: int) -> Ingested | None:
    """What Pillow can read of one photo, or None when it cannot open or decode it."""
    try:
        with Image.open(io.BytesIO(photo.data)) as img:
            width, height = img.size
            orientation, taken = _exif_facts(img)
            img.draft("L", (ANALYSIS_EDGE * 2, ANALYSIS_EDGE * 2))  # cheap JPEG downscale on decode
            gray = img.convert("L")
    except Exception:  # noqa: BLE001 - any undecodable upload is skipped
        log.info("film ingest: skipping %s, not a readable image (%d bytes)", photo.id, len(photo.data))
        return None
    if width <= 0 or height <= 0:
        return None
    if orientation in _ROTATED:
        width, height = height, width
    if orientation in _TRANSPOSE:
        gray = gray.transpose(_TRANSPOSE[orientation])
    gray.thumbnail((ANALYSIS_EDGE, ANALYSIS_EDGE), Image.Resampling.BILINEAR)
    return Ingested(
        id=photo.id,
        data=photo.data,
        order=order,
        taken_at=taken,
        width=width,
        height=height,
        sharpness=sharpness(gray),
        dhash=dhash(gray),
    )


def ingest(photos: list[SourcePhoto]) -> list[Ingested]:
    """Every readable photo, in upload order. Unreadable ones are skipped."""
    out: list[Ingested] = []
    for order, photo in enumerate(photos):
        found = ingest_one(photo, order)
        if found is not None:
            out.append(found)
    return out


__all__ = ["ANALYSIS_EDGE", "Ingested", "dhash", "hamming", "ingest", "ingest_one", "sharpness"]
