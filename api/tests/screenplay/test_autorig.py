"""Auto-rig: alpha-mask anchor, grip snap and size-class scale."""

import json

import pytest
from PIL import Image

from screenplay import autorig as ar

REPO = __import__("pathlib").Path(__file__).resolve().parents[3]


def rgba(w, h, boxes):
    """Transparent w x h RGBA image with opaque boxes (x0, y0, x1, y1), inclusive."""
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    for x0, y0, x1, y1 in boxes:
        for y in range(y0, y1 + 1):
            for x in range(x0, x1 + 1):
                img.putpixel((x, y), (200, 50, 50, 255))
    return img


def approx(a, b):
    return a == pytest.approx(b, abs=1e-12)


# --- mask ---------------------------------------------------------------


def test_alpha_mask_threshold():
    img = Image.new("RGBA", (3, 1), (0, 0, 0, 0))
    img.putpixel((0, 0), (0, 0, 0, 15))
    img.putpixel((1, 0), (0, 0, 0, 16))
    img.putpixel((2, 0), (0, 0, 0, 255))
    assert ar.alpha_mask(img) == [[False, True, True]]
    assert ar.alpha_mask(img, threshold=100) == [[False, False, True]]


def test_rgb_is_fully_opaque():
    img = Image.new("RGB", (4, 2), (10, 10, 10))
    assert ar.alpha_mask(img) == [[True] * 4, [True] * 4]
    assert approx(ar.anchor_from_alpha(img), (0.5, 1.5 / 2))


def test_la_mode():
    img = Image.new("LA", (4, 4), (0, 0))
    img.putpixel((1, 2), (255, 255))
    assert approx(ar.anchor_from_alpha(img), (1.5 / 4, 2.5 / 4))


def test_p_mode_with_transparency():
    img = Image.new("P", (4, 4), 0)
    img.putpalette([0, 0, 0, 255, 0, 0] + [0] * (254 * 3))
    img.info["transparency"] = 0
    img.putpixel((3, 1), 1)
    assert approx(ar.anchor_from_alpha(img), (3.5 / 4, 1.5 / 4))


def test_p_mode_without_transparency_is_opaque():
    img = Image.new("P", (2, 2), 0)
    assert ar.alpha_mask(img) == [[True, True], [True, True]]


# --- anchor -------------------------------------------------------------


def test_centred_rectangle_anchor():
    img = rgba(10, 20, [(3, 4, 6, 15)])
    ax, ay = ar.anchor_from_alpha(img)
    assert approx(ax, 0.5)
    assert approx(ay, 15.5 / 20)


def test_full_image_anchor():
    img = rgba(8, 8, [(0, 0, 7, 7)])
    assert approx(ar.anchor_from_alpha(img), (0.5, 7.5 / 8))


def test_l_shape_anchor_is_bottom_row_centre():
    # Tall bar at x=1..2 for rows 1..9, foot at row 9 extending to x=7.
    img = rgba(10, 12, [(1, 1, 2, 9), (1, 9, 7, 9)])
    ax, ay = ar.anchor_from_alpha(img)
    # bottom row 9 covers x=1..7: centres 1.5..7.5, mean 4.5
    assert approx(ax, 4.5 / 10)
    assert approx(ay, 9.5 / 12)


def test_anchor_ignores_faint_pixels_below_shape():
    img = rgba(10, 10, [(2, 2, 5, 5)])
    img.putpixel((9, 9), (0, 0, 0, 5))  # below threshold
    assert approx(ar.anchor_from_alpha(img), (4.0 / 10, 5.5 / 10))


def test_fully_transparent_raises():
    img = Image.new("RGBA", (5, 5), (0, 0, 0, 0))
    with pytest.raises(ValueError, match="cutout has no opaque pixels"):
        ar.anchor_from_alpha(img)
    with pytest.raises(ValueError, match="cutout has no opaque pixels"):
        ar.snap_grip(img, (0.5, 0.5))
    with pytest.raises(ValueError, match="cutout has no opaque pixels"):
        ar.autorig(img, "hand", "top")


# --- grip snap ----------------------------------------------------------


def test_edge_pixels_of_rectangle():
    mask = ar.alpha_mask(rgba(5, 5, [(1, 1, 3, 3)]))
    edges = ar.edge_pixels(mask)
    assert (2, 2) not in edges
    assert len(edges) == 8
    assert edges == sorted(edges, key=lambda p: (p[1], p[0]))


def test_image_border_counts_as_edge():
    mask = ar.alpha_mask(Image.new("RGB", (3, 3)))
    assert ar.edge_pixels(mask) == [
        (0, 0), (1, 0), (2, 0), (0, 1), (2, 1), (0, 2), (1, 2), (2, 2)
    ]


def test_hint_inside_snaps_to_nearest_edge():
    # Rectangle x=2..17, y=2..17 in 20x20. Interior hint near the left side.
    img = rgba(20, 20, [(2, 2, 17, 17)])
    hint = (5.5 / 20, 10.5 / 20)  # centre of pixel (5, 10)
    assert approx(ar.snap_grip(img, hint), (2.5 / 20, 10.5 / 20))


def test_hint_outside_snaps_to_nearest_edge():
    img = rgba(20, 20, [(5, 5, 14, 14)])
    # Above the shape: nearest edge is the top row directly below.
    assert approx(ar.snap_grip(img, (9.5 / 20, 0.0)), (9.5 / 20, 5.5 / 20))
    # Far top-left corner: nearest edge is the shape's top-left pixel.
    assert approx(ar.snap_grip(img, (0.0, 0.0)), (5.5 / 20, 5.5 / 20))
    # Right of the shape.
    assert approx(ar.snap_grip(img, (1.0, 12.5 / 20)), (14.5 / 20, 12.5 / 20))


def test_hint_on_edge_pixel_returns_it():
    img = rgba(10, 10, [(2, 2, 7, 7)])
    assert approx(ar.snap_grip(img, (4.5 / 10, 2.5 / 10)), (4.5 / 10, 2.5 / 10))


def test_snap_tie_break_prefers_lower_y_then_lower_x():
    # Two single-pixel blobs equidistant from the hint.
    img = rgba(10, 10, [(2, 5, 2, 5), (8, 5, 8, 5)])
    hint = (5.5 / 10, 5.5 / 10)  # exactly between x=2 and x=8
    assert approx(ar.snap_grip(img, hint), (2.5 / 10, 5.5 / 10))
    img2 = rgba(10, 10, [(5, 2, 5, 2), (5, 8, 5, 8)])
    assert approx(ar.snap_grip(img2, hint), (5.5 / 10, 2.5 / 10))


def test_snap_rejects_bad_hint():
    img = rgba(4, 4, [(0, 0, 3, 3)])
    with pytest.raises(ValueError):
        ar.snap_grip(img, "top")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        ar.snap_grip(img, (float("nan"), 0.5))


# --- named hints --------------------------------------------------------


@pytest.mark.parametrize(
    "name,expected",
    [
        ("top", (0.5, 0.0)),
        ("bottom", (0.5, 1.0)),
        ("left", (0.0, 0.5)),
        ("right", (1.0, 0.5)),
        ("center", (0.5, 0.5)),
        ("centre", (0.5, 0.5)),
        ("handle", (0.5, 0.0)),
        ("top-left", (0.0, 0.0)),
        ("Top Right", (1.0, 0.0)),
        ("bottom_left", (0.0, 1.0)),
        ("right-bottom", (1.0, 1.0)),
    ],
)
def test_named_grip_hint(name, expected):
    assert ar.named_grip_hint(name) == expected


def test_named_grip_hint_unknown_lists_valid_names():
    with pytest.raises(ValueError) as exc:
        ar.named_grip_hint("elbow")
    msg = str(exc.value)
    assert "elbow" in msg
    for name in ("top", "bottom", "left", "right", "center", "handle", "top-left"):
        assert name in msg


# --- scale --------------------------------------------------------------


def test_scale_matches_every_vocabulary_size():
    sizes = json.loads((REPO / "shared" / "vocabulary.json").read_text())["sizes"]
    assert sizes
    for name, value in sizes.items():
        assert ar.scale_for_size(name) == value
    assert set(ar.SIZES) == set(sizes)


def test_scale_unknown_size_raises():
    with pytest.raises(ValueError, match="unknown size 'huge'"):
        ar.scale_for_size("huge")


# --- autorig ------------------------------------------------------------


def test_autorig_named_hint():
    img = rgba(20, 20, [(5, 5, 14, 14)])
    rig = ar.autorig(img, "hand", "top")
    assert approx(rig["anchor"], [0.5, 14.5 / 20])
    # top hint (10, 0) is equidistant from x=9 and x=10 centres -> lower x wins
    assert approx(rig["grip"], [9.5 / 20, 5.5 / 20])
    assert rig["scale"] == ar.scale_for_size("hand")
    assert set(rig) == {"anchor", "grip", "scale"}
    assert isinstance(rig["anchor"], list) and isinstance(rig["grip"], list)


def test_autorig_tuple_hint_and_none():
    img = rgba(20, 20, [(5, 5, 14, 14)])
    rig = ar.autorig(img, "carried", (1.0, 0.5))
    assert approx(rig["grip"], [14.5 / 20, 9.5 / 20])
    assert ar.autorig(img, "vehicle", None)["grip"] is None
    assert ar.autorig(img, "vehicle")["grip"] is None


def test_autorig_errors():
    img = rgba(4, 4, [(0, 0, 3, 3)])
    with pytest.raises(ValueError, match="unknown size"):
        ar.autorig(img, "gigantic", None)
    with pytest.raises(ValueError, match="valid names"):
        ar.autorig(img, "hand", "nowhere")


def test_output_json_serialisable():
    img = rgba(8, 8, [(1, 1, 6, 6)])
    rig = ar.autorig(img, "tiny", "bottom-right")
    assert json.loads(json.dumps(rig)) == rig


def test_deterministic_across_calls():
    img = rgba(31, 17, [(3, 2, 9, 15), (3, 13, 25, 15), (20, 4, 27, 8)])
    first = ar.autorig(img, "furniture", "top-right")
    for _ in range(5):
        assert ar.autorig(img.copy(), "furniture", "top-right") == first
        assert ar.anchor_from_alpha(img) == tuple(first["anchor"])
        assert ar.snap_grip(img, ar.named_grip_hint("top-right")) == tuple(first["grip"])


# --- bug hunt: degenerate cutouts and hints -----------------------------


@pytest.mark.parametrize("hint", [(float("inf"), 0.5), (0.5, float("-inf")), (float("inf"), float("inf"))])
def test_infinite_hint_is_rejected_like_nan(hint):
    """Bug: an infinite hint made every edge pixel infinitely far, so the tie-break silently
    snapped the grip to the shape's top-left edge pixel instead of rejecting the hint."""
    img = rgba(3, 3, [(0, 0, 2, 2)])
    with pytest.raises(ValueError, match="grip hint must be a pair of fractions"):
        ar.snap_grip(img, hint)
    with pytest.raises(ValueError, match="grip hint must be a pair of fractions"):
        ar.autorig(img, "hand", hint)


def test_hint_far_outside_the_image_still_snaps_to_the_nearest_edge():
    img = rgba(3, 3, [(0, 0, 2, 2)])
    assert approx(ar.snap_grip(img, (40.0, 0.5)), (2.5 / 3, 1.5 / 3))
    assert approx(ar.snap_grip(img, (-5.0, -5.0)), (0.5 / 3, 0.5 / 3))


@pytest.mark.parametrize("size", [(0, 0), (1, 1), (5, 0), (0, 5)])
def test_empty_and_one_pixel_transparent_cutouts_raise(size):
    with pytest.raises(ValueError, match="no opaque pixels"):
        ar.autorig(Image.new("RGBA", size, (0, 0, 0, 0)), "hand", "top")


@pytest.mark.parametrize("w,h", [(1, 1), (1, 400), (400, 1), (2, 300), (300, 2)])
@pytest.mark.parametrize("hint", ["top", "bottom-right", "left", (0.0, 0.0), (1.0, 1.0), (-3.0, 7.0)])
def test_extreme_aspects_keep_anchor_and_grip_inside_the_image(w, h, hint):
    """One pixel, very tall and very wide all-opaque cutouts (RGBA and no-alpha RGB)."""
    for img in (Image.new("RGBA", (w, h), (9, 9, 9, 255)), Image.new("RGB", (w, h))):
        rig = ar.autorig(img, "carried", hint)
        for v in (*rig["anchor"], *rig["grip"]):
            assert 0.0 < v < 1.0
        assert approx(rig["anchor"], [0.5, (h - 0.5) / h])
