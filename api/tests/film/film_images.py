"""Synthetic photos for the film pipeline tests (Pillow only, deterministic)."""

from __future__ import annotations

import io
import random
from datetime import datetime, timedelta

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter

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


def cropped(im: Image.Image, frac: float, side: str = "left") -> Image.Image:
    """`frac` of the frame cut off one side ("left" or "top") or split over all four
    ("centre"), scaled back to the original size: the same photo, re-framed."""
    w, h = im.size
    dx, dy = int(w * frac), int(h * frac)
    box = {
        "left": (dx, 0, w, h),
        "top": (0, dy, w, h),
        "centre": (dx // 2, dy // 2, w - dx // 2, h - dy // 2),
    }[side]
    return im.crop(box).resize((w, h))


def brighter(im: Image.Image, factor: float = 1.2) -> Image.Image:
    return ImageEnhance.Brightness(im).enhance(factor)


def tinted_scene(seed: int, base: tuple[int, int, int], size: tuple[int, int] = (640, 480)) -> Image.Image:
    """A busy picture whose every colour is within 40 of `base`: scenes of the same dominant
    colour (a day at the beach, a green park) that are still different pictures."""
    rng = random.Random(seed)

    def colour() -> tuple[int, ...]:
        return tuple(max(0, min(255, c + rng.randrange(-40, 41))) for c in base)

    im = Image.new("RGB", size, colour())
    draw = ImageDraw.Draw(im)
    for _ in range(40):
        x, y = rng.randrange(size[0]), rng.randrange(size[1])
        w, h = rng.randrange(20, size[0] // 2), rng.randrange(20, size[1] // 2)
        draw.rectangle([x, y, x + w, y + h], fill=colour())
    return im


_PLACES = {
    # sky top, sky at the horizon, ground near the horizon, ground at the bottom
    "park": ((110, 165, 235), (190, 215, 245), (80, 150, 60), (40, 100, 30)),
    "beach": ((100, 160, 230), (195, 215, 240), (215, 200, 160), (180, 160, 120)),
    "lake": ((120, 170, 230), (200, 220, 240), (60, 110, 150), (30, 70, 110)),
}


def _lerp(a: tuple[int, ...], b: tuple[int, ...], t: float) -> tuple[int, ...]:
    return tuple(int(x + (y - x) * t) for x, y in zip(a, b))


def outing(
    seed: int, place: str = "park", people: tuple[int, int] = (2, 5), size: tuple[int, int] = (640, 480)
) -> Image.Image:
    """A moment of one day out: the same `place` (sky gradient, horizon, a tree line in the
    park, the sea's edge at the beach, a far shore at the lake) at about the same camera
    height, with `people` figures of different sizes, shirts and places, and a little sensor
    noise. Two seeds are two different moments in the same place."""
    rng = random.Random(seed)
    w, h = size
    sky_top, sky_low, ground_top, ground_low = _PLACES[place]
    im = Image.new("RGB", size)
    draw = ImageDraw.Draw(im)
    horizon = int(h * (0.45 + rng.uniform(-0.04, 0.04)))
    for y in range(h):
        if y < horizon:
            colour = _lerp(sky_top, sky_low, y / horizon)
        else:
            colour = _lerp(ground_top, ground_low, (y - horizon) / (h - horizon))
        draw.line([(0, y), (w, y)], fill=colour)
    if place == "park":
        for x in range(0, w, 24):
            r = rng.randint(16, 36)
            draw.ellipse([x - r, horizon - 2 * r, x + r, horizon + 4], fill=(30 + rng.randint(0, 20), 80 + rng.randint(0, 30), 30))
    elif place == "beach":
        draw.rectangle([0, horizon, w, horizon + h // 12], fill=(70, 130, 190))
    else:
        draw.rectangle([0, horizon - h // 20, w, horizon], fill=(70, 95, 70))
    figures, mask = Image.new("RGB", size), Image.new("L", size, 0)
    fd, md = ImageDraw.Draw(figures), ImageDraw.Draw(mask)
    for _ in range(rng.randint(*people)):
        cx = rng.uniform(0.1, 0.9) * w
        tall = h * rng.uniform(0.15, 0.45)
        wide = tall * 0.35
        foot = rng.uniform(horizon + 20, h)
        top = foot - tall
        shirt = tuple(rng.randint(20, 240) for _ in range(3))
        for d, fill in ((fd, shirt), (md, 255)):
            d.rectangle([cx - wide / 2, top + tall * 0.2, cx + wide / 2, foot], fill=fill)
        for d, fill in ((fd, (225, 185, 150)), (md, 255)):
            d.ellipse([cx - wide / 3, top, cx + wide / 3, top + tall * 0.22], fill=fill)
    im = Image.composite(figures, im, mask.filter(ImageFilter.GaussianBlur(2)))
    grain = Image.frombytes("L", (w // 4, h // 4), rng.randbytes((w // 4) * (h // 4))).resize(size)
    return Image.blend(im, Image.merge("RGB", (grain, grain, grain)), 0.06)


def subject_moved(seed: int, frac: float = 0.3) -> Image.Image:
    """scene(seed) with a band `frac` of its width replaced, as when someone steps into the
    middle of the frame between two frames of a burst."""
    im = scene(seed)
    w, h = im.size
    patch = scene(seed + 500).crop((0, 0, int(w * frac), h))
    im.paste(patch, (int(w * (1 - frac)) // 2, 0))
    return im


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
