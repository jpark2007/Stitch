"""describe_photo and FakeVision."""

from __future__ import annotations

import io

from PIL import Image

from agent.tools.photos import PHOTO_PROMPT, UNKNOWN_PHOTO, FakeVision, make_photo_tools


def png(color: tuple[int, int, int], size: tuple[int, int] = (640, 480), accent: bool = False) -> bytes:
    img = Image.new("RGB", size, color)
    if accent:  # a smaller block of another color must not win
        img.paste((200, 30, 30), (0, 0, size[0] // 4, size[1] // 4))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


GREEN = png((74, 122, 58), accent=True)
BLUE = png((40, 70, 200), size=(300, 400))
MEDIA = {
    "media/green": (GREEN, "image/png"),
    "media/blue": (BLUE, "image/png"),
    "media/other_run": (BLUE, "image/png"),
    "media/notes": (b"hello", "text/plain"),
}


def tool_for(allowed: list[str], vision: FakeVision | None = None):
    (describe,) = make_photo_tools(vision or FakeVision(), MEDIA.get, allowed)
    assert describe.name == "describe_photo"
    return describe


def test_fake_vision_is_deterministic() -> None:
    a, b = FakeVision(), FakeVision()
    first = a.describe([GREEN], PHOTO_PROMPT)
    assert first == b.describe([GREEN], PHOTO_PROMPT) == a.describe([GREEN], PHOTO_PROMPT)
    assert first == "A photo, mostly green (#4a7a3a), 640x480."
    assert a.describe([BLUE], PHOTO_PROMPT) == "A photo, mostly blue (#2846c8), 300x400."
    assert a.describe([b"not an image"], PHOTO_PROMPT) == "An image that could not be read (12 bytes)."


def test_describe_photo_allowed_id() -> None:
    vision = FakeVision()
    describe = tool_for(["media/green", "media/blue"], vision)
    assert describe.invoke({"photo_id": "media/green"}) == "A photo, mostly green (#4a7a3a), 640x480."
    assert describe.invoke({"photo_id": "media/blue"}).startswith("A photo, mostly blue")
    assert vision.calls == [(1, PHOTO_PROMPT), (1, PHOTO_PROMPT)]


def test_describe_photo_refuses_other_ids() -> None:
    vision = FakeVision()
    describe = tool_for(["media/green"], vision)
    # Exists for the family but is not one of this run's photos.
    assert describe.invoke({"photo_id": "media/other_run"}) == UNKNOWN_PHOTO
    assert describe.invoke({"photo_id": "media/missing"}) == UNKNOWN_PHOTO
    assert describe.invoke({"photo_id": ""}) == UNKNOWN_PHOTO
    assert vision.calls == []


def test_describe_photo_missing_media_and_non_images() -> None:
    describe = tool_for(["media/gone", "media/notes"])
    assert describe.invoke({"photo_id": "media/gone"}) == UNKNOWN_PHOTO
    assert "not an image" in describe.invoke({"photo_id": "media/notes"})


def test_describe_photo_survives_vision_failure() -> None:
    class Broken:
        def describe(self, images, prompt):
            raise RuntimeError("down")

    (describe,) = make_photo_tools(Broken(), MEDIA.get, ["media/green"])
    assert describe.invoke({"photo_id": "media/green"}) == "could not describe photo media/green: RuntimeError"


def test_photo_prompt_covers_the_brief() -> None:
    text = PHOTO_PROMPT.lower()
    for word in ("setting", "color", "era", "clothing", "do not guess who"):
        assert word in text
