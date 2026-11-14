"""Synthetic photos for the film pipeline tests (Pillow only, deterministic)."""

from __future__ import annotations

import io
import random
from datetime import datetime, timedelta

from PIL import Image, ImageDraw, ImageFilter

from film.types import SourcePhoto

T0 = datetime(1998, 7, 4, 9, 0, 0)


def scene(seed: int, size: tuple[int, int] = (640, 480)) -> Image.Image:
    """A busy, sharp picture that differs strongly from every other seed."""
    rng = random.Random(seed)
    im = Image.new("RGB", size, tuple(rng.randrange(256) for _ in range(3)))
    draw = ImageDraw.Draw(im)
    for _ in range(40):
        x, y = rng.randrange(size[0]), rng.randrange(size[1])
        w, h = rng.randrange(20, size[0] // 2), rng.randrange(20, size[1] // 2)
        draw.rectangle([x, y, x + w, y + h], fill=tuple(rng.randrange(256) for _ in range(3)))
    return im


def encode(im: Image.Image, *, taken: datetime | None = None, orientation: int | None = None) -> bytes:
    exif = Image.Exif()
    if orientation is not None:
        exif[0x0112] = orientation
    if taken is not None:
        exif.get_ifd(0x8769)[0x9003] = taken.strftime("%Y:%m:%d %H:%M:%S")
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=92, exif=exif)
    return buf.getvalue()


def photo(pid: str, im: Image.Image, **kw) -> SourcePhoto:
    return SourcePhoto(id=pid, data=encode(im, **kw), content_type="image/jpeg")


def blurred(im: Image.Image) -> Image.Image:
    return im.filter(ImageFilter.GaussianBlur(3))


def event(n: int, *, minutes_apart: int = 20, start_seed: int = 0) -> list[SourcePhoto]:
    """n distinct photos of one day, `minutes_apart` apart, with EXIF capture times."""
    return [
        photo(f"p{i:02d}", scene(start_seed + i), taken=T0 + timedelta(minutes=minutes_apart * i)) for i in range(n)
    ]


STORY = (
    "We drove up to Lake Wren the summer Maya turned seven. Dad packed the car the night before, "
    "and Sam fell asleep before we left the driveway. The cabin smelled like cedar and old books. "
    "Every morning Maya ran down to the dock in her red coat. She swore she saw a heron, and nobody "
    "believed her until the third day. On the last night we lit a fire on the beach. Sam burned every "
    "marshmallow on purpose, and Dad pretended not to notice. I remember thinking we should come back "
    "every year. We did, for a while, and then the kids grew up and the cabin was sold. I still have "
    "the heron feather Maya found on the dock."
)
