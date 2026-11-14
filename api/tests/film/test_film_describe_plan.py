"""Describe and plan: tolerant parsing, per-photo fallback, hard validation of the editor."""

import io
import json

import pytest
from PIL import Image

from film_images import STORY, encode, scene
from film.describe import (
    DESCRIBE_EDGE,
    FakeDescriber,
    ModelDescriber,
    extract_json_object,
    short_caption,
)
from film.plan import (
    MAX_BEATS,
    MIN_BEATS,
    ModelEditor,
    fallback_plan,
    make_plan,
    sentences,
    trim_story,
    validate_plan,
)
from film.selection import MAX_NARRATION_WORDS, word_count
from film.types import Story

KEYS = {"caption", "people", "setting", "mood", "focus", "quality"}


class StubVision:
    def __init__(self, reply):
        self.reply = reply
        self.images: list[bytes] = []

    def describe(self, images, prompt):
        self.images.extend(images)
        assert "JSON" in prompt
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


def test_fake_describer_has_exactly_the_contract_keys():
    d = FakeDescriber().describe(encode(scene(1)))
    assert set(d) == KEYS
    assert d["focus"] == [0.5, 0.5] and 0 <= d["quality"] <= 1 and d["caption"] == ""
    assert set(FakeDescriber().describe(b"garbage")) == KEYS


def test_model_describer_parses_fenced_json_and_downscales():
    reply = 'Sure!\n```json\n{"caption": "Maya jumps off the old wooden dock into the lake", ' \
        '"people": ["a girl in a red coat", 3], "setting": "a lake dock", "mood": "giddy", ' \
        '"focus": [0.2, 1.7], "quality": 0.9}\n```'
    vision = StubVision(reply)
    big = Image.new("RGB", (3000, 2000), (10, 120, 200))
    d = ModelDescriber(vision).describe(encode(big))
    assert d["caption"] == "Maya jumps off the old wooden dock into"  # cut to 8 words
    assert d["people"] == ["a girl in a red coat"]
    assert d["focus"] == [0.2, 1.0] and d["quality"] == 0.9 and d["mood"] == "giddy"
    sent = Image.open(io.BytesIO(vision.images[0]))
    assert max(sent.size) <= DESCRIBE_EDGE


@pytest.mark.parametrize("reply", ["I can't see anything.", '{"caption": ', '["a list"]', RuntimeError("down")])
def test_model_describer_falls_back_per_photo(reply):
    data = encode(scene(2))
    d = ModelDescriber(StubVision(reply)).describe(data)
    assert d == FakeDescriber().describe(data)


def test_extract_json_object_and_short_caption():
    assert extract_json_object('noise {"a": 1} more {"b": 2}') == {"a": 1}
    with pytest.raises(ValueError):
        extract_json_object("no braces")
    assert short_caption("  ") is None
    assert short_caption("Fire on the beach.") == "Fire on the beach."


def test_sentences_and_trim_story():
    assert sentences('He said "go." Then we went! Did we? Yes…') == ['He said "go."', "Then we went!", "Did we?", "Yes…"]
    long = " ".join(f"Sentence number {i} is here." for i in range(200))
    kept = trim_story(long)
    assert sum(word_count(s) for s in kept) <= MAX_NARRATION_WORDS
    assert kept[0] == "Sentence number 0 is here." and kept[-1] == "Sentence number 199 is here."
    assert word_count(trim_story("word " * 1000)[0]) == MAX_NARRATION_WORDS


def _photos(n: int) -> list[dict]:
    return [
        {"id": f"p{i}", "takenAt": None, "caption": f"photo {i}", "people": [], "setting": "", "mood": "",
         "focus": [0.5, 0.5], "quality": 0.5}
        for i in range(n)
    ]


def test_fallback_plan_is_chronological_and_uses_the_story():
    story = Story(text=STORY, title=None, year=1998)
    photos = _photos(10)
    beats = fallback_plan(story, photos)
    assert MIN_BEATS <= len(beats) <= MAX_BEATS
    assert [s.photo_id for b in beats for s in b.shots] == [p["id"] for p in photos]
    assert " ".join(b.narration for b in beats) == " ".join(sentences(STORY))
    assert all(b.shots and b.title for b in beats)
    assert beats[0].shots[0].caption == "photo 0"


def test_validate_plan_drops_unknown_and_reused_ids_merges_empty_beats_and_adds_leftovers():
    photos = _photos(6)
    raw = {
        "beats": [
            {"title": "", "narration": "We drove up.", "shots": []},  # no photos: narration moves on
            {"title": "Arrival", "narration": "The cabin smelled of cedar.",
             "shots": [{"photoId": "p0", "caption": "Cabin"}, {"photoId": "nope"}, {"photoId": "p0"}]},
            {"title": "Ghost", "narration": "   ", "shots": [{"photoId": "p1", "caption": "  "}]},  # no narration
            {"title": "Fire", "narration": "We lit a fire.", "shots": [{"photoId": "p5", "caption": "x " * 20}]},
            "junk",
        ]
    }
    beats = validate_plan(raw, photos)
    assert beats is not None and len(beats) == 2
    assert beats[0].narration == "We drove up. The cabin smelled of cedar."
    ids = [[s.photo_id for s in b.shots] for b in beats]
    assert ids == [["p0", "p1", "p2", "p3"], ["p4", "p5"]]  # leftovers p2..p4 by capture order
    assert beats[0].shots[1].caption == "photo 1"  # blank caption -> the description
    assert len(beats[1].shots[1].caption.split()) == 8


@pytest.mark.parametrize(
    "raw",
    [
        None, "beats", {"beats": "nope"}, {"beats": []}, {"scenes": []},
        {"beats": [{"title": "a", "narration": "x", "shots": [{"photoId": "unknown"}]}]},
        {"beats": [{"title": "a", "narration": "word " * 1000, "shots": [{"photoId": "p0"}]}]},
        {"beats": [{"narration": "x", "shots": [{"photoId": "p0"}]}] * 9},
    ],
)
def test_malformed_editor_replies_fall_back(raw):
    story = Story(text=STORY, title=None, year=1998)
    photos = _photos(6)

    class Editor:
        def plan(self, story, photos):
            return raw

    assert make_plan(story, photos, Editor()) == fallback_plan(story, photos)


def test_an_editor_that_raises_falls_back():
    class Broken:
        def plan(self, story, photos):
            raise RuntimeError("boom")

    story = Story(text=STORY, title=None, year=1998)
    assert make_plan(story, _photos(6), Broken()) == fallback_plan(story, _photos(6))


class StubModel:
    def __init__(self, content):
        self.content = content
        self.messages = None

    def invoke(self, messages):
        self.messages = messages
        if isinstance(self.content, Exception):
            raise self.content
        return type("AIMessage", (), {"content": self.content})()


def test_model_editor_sends_story_and_photos_and_reads_block_content():
    plan = {"beats": [{"title": "Lake", "narration": "We drove up.", "shots": [{"photoId": "p0", "caption": "Car"}]}]}
    model = StubModel([{"type": "text", "text": "```json\n" + json.dumps(plan) + "\n```"}])
    story = Story(text=STORY, title="Lake Wren", year=1998, people=["Maya", "Sam"])
    assert ModelEditor(model).plan(story, _photos(3)) == plan
    system, human = model.messages
    assert system[0] == "system" and "first person" in system[1]
    assert "Lake Wren" in human[1] and '"p2"' in human[1] and "Maya" in human[1]
    assert ModelEditor(StubModel(RuntimeError("down"))).plan(story, _photos(3)) is None
    assert ModelEditor(StubModel("no json here")).plan(story, _photos(3)) is None
