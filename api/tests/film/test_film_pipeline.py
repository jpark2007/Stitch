"""make_film end to end with stub or offline services, and default_services. No network."""

from datetime import timedelta

import pytest

import schemas
from film_checks import assert_well_cut
from film_images import STORY, T0, blurred, event, photo, scene
from agent.tools.narration import FakeTTS
from film.describe import FakeDescriber, ModelDescriber
from film.narrate import TTSNarrator, offline_narrator
from film.pipeline import make_film
from film.plan import ModelEditor
from film.services import default_services
from film.types import FilmCut, FilmError, Narration, Services, SourcePhoto, Story

STORY_OBJ = Story(text=STORY, title=None, year=1998, people=["Maya", "Sam"])


def mixed_photos() -> list[SourcePhoto]:
    """14 distinct timed photos plus a blurred near-duplicate, a portrait, an EXIF-rotated
    photo and an undecodable blob."""
    photos = event(14)
    photos.insert(3, photo("dup_soft", blurred(scene(2)), taken=T0 + timedelta(minutes=40, seconds=1)))
    photos.append(photo("portrait", scene(50, size=(480, 640)), taken=T0 + timedelta(hours=5)))
    photos.append(photo("rotated", scene(51), taken=T0 + timedelta(hours=5, minutes=10), orientation=6))
    photos.append(SourcePhoto("broken", b"\x89PNG not really", "image/png"))
    return photos


def check(cut: FilmCut, photos: list[SourcePhoto]) -> None:
    assert isinstance(cut, FilmCut)
    assert_well_cut(cut.shots, cut.beats, cut.words, cut.duration, {p.id for p in photos}, {p.id: p.data for p in photos})
    shown = {s.photo_id for s in cut.shots}
    assert "broken" not in shown and "dup_soft" not in shown


def test_fully_offline_film_is_valid_and_watchable():
    photos = mixed_photos()
    cut = make_film(photos, STORY_OBJ, default_services("fake"))
    check(cut, photos)
    assert cut.audio is None and cut.audio_content_type is None
    assert cut.title == "We drove up to Lake Wren"
    assert 3 <= len(cut.beats) <= 6
    assert len({s.photo_id for s in cut.shots}) == len(cut.shots)  # nothing shown twice
    assert 8 <= len(cut.shots) <= 24
    assert 35 <= cut.duration <= 120  # ~125 words at FakeTTS's brisk 0.3 s a word
    assert " ".join(w.word for w in cut.words).startswith("We drove up to Lake Wren")
    # The whole day is covered, from the first photo to the evening ones.
    assert cut.shots[0].photo_id == "p00" and cut.shots[-1].photo_id == "rotated"


class StubDescriber:
    def __init__(self):
        self.seen = 0

    def describe(self, data):
        self.seen += 1
        return {"caption": "kids on the dock", "people": ["two kids"], "setting": "a dock", "mood": "giddy",
                "focus": [0.8, 0.3], "quality": 0.9}


class StubEditor:
    def __init__(self, reply=None):
        self.reply = reply
        self.photos = None

    def plan(self, story, photos):
        self.photos = photos
        if self.reply is not None:
            return self.reply
        ids = [p["id"] for p in photos]
        half = len(ids) // 2
        return {"beats": [
            {"title": "Getting there", "narration": "We drove up to Lake Wren the summer Maya turned seven.",
             "shots": [{"photoId": i, "caption": "the drive"} for i in ids[:half]]},
            {"title": "The heron", "narration": "She swore she saw a heron, and nobody believed her until the third day. "
                                                "On the last night we lit a fire on the beach.",
             "shots": [{"photoId": i, "caption": "the lake"} for i in ids[half:]]},
        ]}


def test_stub_services_drive_every_step():
    photos = mixed_photos()
    describer, editor = StubDescriber(), StubEditor()
    services = Services(describer=describer, editor=editor, narrator=TTSNarrator(FakeTTS()))
    cut = make_film(photos, Story(STORY, "Lake Wren", 1998), services)
    check(cut, photos)
    assert cut.title == "Lake Wren"
    assert [b.title for b in cut.beats] == ["Getting there", "The heron"]
    assert cut.audio is not None and cut.audio[:4] == b"RIFF" and cut.audio_content_type == "audio/wav"
    assert all(s.focus == (0.8, 0.3) for s in cut.shots)
    assert {s.caption for s in cut.shots} <= {"the drive", "the lake", None}
    assert describer.seen == len(editor.photos)
    taken = [p["takenAt"] for p in editor.photos]
    assert taken == sorted(taken) and all(taken)  # capture order, ISO times
    assert {"id", "takenAt", "caption", "people", "setting", "mood", "focus", "quality"} <= set(editor.photos[0])


@pytest.mark.parametrize("reply", [{"beats": "nope"}, {"beats": [{"narration": "", "shots": []}]}, {"x": 1}])
def test_a_malformed_editor_reply_gives_the_same_film_as_no_editor(reply):
    photos = mixed_photos()
    with_bad = make_film(photos, STORY_OBJ, Services(FakeDescriber(), StubEditor(reply), offline_narrator()))
    without = make_film(photos, STORY_OBJ, Services(FakeDescriber(), None, offline_narrator()))
    assert with_bad == without


def test_failing_describer_and_narrator_degrade_to_offline():
    class Broken:
        def describe(self, data):
            raise RuntimeError("vision down")

        def speak(self, lines):
            raise RuntimeError("tts down")

    photos = mixed_photos()
    cut = make_film(photos, STORY_OBJ, Services(Broken(), None, Broken()))
    assert cut == make_film(photos, STORY_OBJ, default_services("off"))


def test_a_narration_that_does_not_fit_its_lines_is_retimed_silently():
    class Wrong:
        def speak(self, lines):
            return Narration(b"RIFFjunk", "audio/wav", [0.0], [(0.0, "x")], 1.0)

    photos = mixed_photos()
    cut = make_film(photos, STORY_OBJ, Services(FakeDescriber(), None, Wrong()))
    check(cut, photos)
    assert cut.audio is None and cut.audio_content_type is None


def test_fewer_than_three_usable_photos_is_a_film_error():
    photos = [photo("a", scene(1)), photo("b", scene(2))] + [SourcePhoto(f"x{i}", b"nope", "image/jpeg") for i in range(6)]
    with pytest.raises(FilmError) as exc:
        make_film(photos, STORY_OBJ, default_services("fake"))
    assert "2 of the 8 photos" in exc.value.message and str(exc.value) == exc.value.message


def test_an_empty_story_is_a_film_error():
    with pytest.raises(FilmError):
        make_film(event(8), Story(text="   ", title=None, year=1998), default_services("fake"))


def test_three_photos_and_a_one_line_story_still_make_a_film():
    photos = event(3)
    cut = make_film(photos, Story("We went to the lake.", None, 2001), default_services("fake"))
    check(cut, photos)
    assert len(cut.beats) == 1 and len(cut.shots) == 3


def test_cut_fills_a_ready_film():
    photos = mixed_photos()
    cut = make_film(photos, STORY_OBJ, default_services("fake"))
    film = schemas.Film(
        id="f1", family_id="fam", author_id="mom", title=cut.title, year=1998, text=STORY,
        participant_ids=[], status="ready", error=None, photo_ids=[p.id for p in photos], shots=cut.shots,
        beats=cut.beats, words=cut.words, audio=None, duration=cut.duration, created_at=T0,
    )
    assert schemas.Film.model_validate(film.model_dump(mode="json", by_alias=True)) == film


@pytest.mark.parametrize("mode", ["fake", "off", "", "FAKE", "nonsense"])
def test_default_services_offline_modes(mode):
    s = default_services(mode)
    assert isinstance(s.describer, FakeDescriber) and s.editor is None
    assert isinstance(s.narrator, TTSNarrator) and s.narrator.silent


def test_real_mode_without_keys_falls_back_to_each_fake(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    s = default_services("real")
    assert isinstance(s.describer, FakeDescriber) and s.editor is None and s.narrator.silent


def test_real_mode_with_keys_builds_real_adapters_without_calling_them(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-a-key")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "el-test-not-a-key")
    s = default_services("real")
    assert isinstance(s.describer, ModelDescriber) and isinstance(s.editor, ModelEditor)
    assert isinstance(s.narrator, TTSNarrator) and not s.narrator.silent
    # Only ElevenLabs set: the describer and editor still fall back on their own.
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    s = default_services("real")
    assert isinstance(s.describer, FakeDescriber) and s.editor is None and not s.narrator.silent
