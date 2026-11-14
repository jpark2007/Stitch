"""Anchor and grip points from a cutout's alpha mask.

ARCHITECTURE.md section 1 "Auto-rig":
- anchor: bottom-centre of the cutout's opaque shape (where it meets the ground)
- grip: a named or fractional hint snapped to the nearest edge of the shape
- scale: from the prop's size class (shared/vocabulary.json "sizes")

All points are fractions of the image, [0, 0] top-left (shared/CONTRACTS.md).
Pixel positions are measured at pixel centres: pixel (x, y) sits at
((x + 0.5) / width, (y + 0.5) / height).

Pure and deterministic: no I/O except reading the vocabulary once.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PIL import Image

DEFAULT_THRESHOLD = 16

Mask = list[list[bool]]


def _load_sizes() -> dict[str, float]:
    try:
        from screenplay.schema import VOCABULARY  # type: ignore[attr-defined]

        sizes = VOCABULARY["sizes"]
    except (ImportError, AttributeError, KeyError):
        path = Path(__file__).resolve().parents[2] / "shared" / "vocabulary.json"
        sizes = json.loads(path.read_text())["sizes"]
    return {str(k): float(v) for k, v in sizes.items()}


SIZES: dict[str, float] = _load_sizes()

_NAMED_HINTS: dict[str, tuple[float, float]] = {
    "center": (0.5, 0.5),
    "top": (0.5, 0.0),
    "bottom": (0.5, 1.0),
    "left": (0.0, 0.5),
    "right": (1.0, 0.5),
    "handle": (0.5, 0.0),
    "top-left": (0.0, 0.0),
    "top-right": (1.0, 0.0),
    "bottom-left": (0.0, 1.0),
    "bottom-right": (1.0, 1.0),
}
_ALIASES: dict[str, str] = {
    "centre": "center",
    "middle": "center",
    "left-top": "top-left",
    "right-top": "top-right",
    "left-bottom": "bottom-left",
    "right-bottom": "bottom-right",
}


def _alpha_channel(img: Image.Image) -> Image.Image | None:
    """The image's alpha as an 'L' image, or None when it has no transparency."""
    mode = img.mode
    if mode in ("RGBA", "LA", "PA", "RGBa", "La"):
        return img.getchannel("A")
    if "transparency" in img.info:
        # P with a transparent index, or L/RGB with a colour key.
        return img.convert("RGBA").getchannel("A")
    return None


def alpha_mask(img: Image.Image, threshold: int = DEFAULT_THRESHOLD) -> Mask:
    """mask[y][x] is True when the pixel's alpha is >= threshold.

    Images with no alpha channel or transparency key are fully opaque.
    """
    w, h = img.size
    alpha = _alpha_channel(img)
    if alpha is None:
        return [[True] * w for _ in range(h)]
    data = alpha.tobytes()
    return [[data[y * w + x] >= threshold for x in range(w)] for y in range(h)]


def _require_opaque(mask: Mask) -> None:
    if not any(any(row) for row in mask):
        raise ValueError("cutout has no opaque pixels")


def _anchor_from_mask(mask: Mask, w: int, h: int) -> tuple[float, float]:
    _require_opaque(mask)
    for y in range(h - 1, -1, -1):
        xs = [x for x, opaque in enumerate(mask[y]) if opaque]
        if xs:
            cx = sum(x + 0.5 for x in xs) / len(xs)
            return (cx / w, (y + 0.5) / h)
    raise AssertionError("unreachable")  # pragma: no cover


def anchor_from_alpha(
    img: Image.Image, threshold: int = DEFAULT_THRESHOLD
) -> tuple[float, float]:
    """Bottom-centre of the opaque shape, as fractions of width and height.

    y is the lowest opaque row; x is the mean of the opaque pixel centres in that row.
    """
    w, h = img.size
    return _anchor_from_mask(alpha_mask(img, threshold), w, h)


def _is_edge(mask: Mask, x: int, y: int, w: int, h: int) -> bool:
    for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
        if nx < 0 or ny < 0 or nx >= w or ny >= h or not mask[ny][nx]:
            return True
    return False


def edge_pixels(mask: Mask) -> list[tuple[int, int]]:
    """Opaque pixels with a transparent or out-of-bounds 4-neighbour, as (x, y), row-major."""
    h = len(mask)
    w = len(mask[0]) if h else 0
    return [
        (x, y)
        for y in range(h)
        for x in range(w)
        if mask[y][x] and _is_edge(mask, x, y, w, h)
    ]


def _validate_hint(hint: Any) -> tuple[float, float]:
    try:
        hx, hy = hint
        hx, hy = float(hx), float(hy)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"grip hint must be a pair of fractions, got {hint!r}") from exc
    if hx != hx or hy != hy:  # NaN
        raise ValueError(f"grip hint must be a pair of fractions, got {hint!r}")
    return hx, hy


def _snap_in_mask(
    mask: Mask, w: int, h: int, hint: tuple[float, float]
) -> tuple[float, float]:
    _require_opaque(mask)
    hx, hy = _validate_hint(hint)
    px, py = hx * w, hy * h
    best: tuple[float, int, int] | None = None
    for x, y in edge_pixels(mask):
        dx = x + 0.5 - px
        dy = y + 0.5 - py
        key = (dx * dx + dy * dy, y, x)
        if best is None or key < best:
            best = key
    assert best is not None  # an opaque shape always has an edge pixel
    _, by, bx = best
    return ((bx + 0.5) / w, (by + 0.5) / h)


def snap_grip(
    img: Image.Image,
    hint: tuple[float, float],
    threshold: int = DEFAULT_THRESHOLD,
) -> tuple[float, float]:
    """Nearest edge pixel of the opaque shape to `hint` (fractions), as fractions.

    Ties are broken by (distance, y, x).
    """
    w, h = img.size
    return _snap_in_mask(alpha_mask(img, threshold), w, h, hint)


def named_grip_hint(name: str) -> tuple[float, float]:
    """Map a named location ('top', 'top-left', 'handle', ...) to fractions."""
    key = str(name).strip().lower().replace("_", "-").replace(" ", "-")
    key = _ALIASES.get(key, key)
    if key not in _NAMED_HINTS:
        valid = ", ".join(sorted(_NAMED_HINTS))
        raise ValueError(f"unknown grip name {name!r}; valid names: {valid}")
    return _NAMED_HINTS[key]


def scale_for_size(size: str) -> float:
    """Scale for a size class from shared/vocabulary.json 'sizes'."""
    if size not in SIZES:
        valid = ", ".join(SIZES)
        raise ValueError(f"unknown size {size!r}; valid sizes: {valid}")
    return SIZES[size]


def autorig(
    img: Image.Image,
    size: str,
    grip_hint: str | tuple[float, float] | list[float] | None = None,
    threshold: int = DEFAULT_THRESHOLD,
) -> dict[str, Any]:
    """{'anchor': [ax, ay], 'grip': [gx, gy] or None, 'scale': s} for a cutout."""
    scale = scale_for_size(size)
    w, h = img.size
    mask = alpha_mask(img, threshold)
    ax, ay = _anchor_from_mask(mask, w, h)
    grip: list[float] | None = None
    if grip_hint is not None:
        hint = named_grip_hint(grip_hint) if isinstance(grip_hint, str) else grip_hint
        gx, gy = _snap_in_mask(mask, w, h, hint)  # type: ignore[arg-type]
        grip = [gx, gy]
    return {"anchor": [ax, ay], "grip": grip, "scale": scale}
