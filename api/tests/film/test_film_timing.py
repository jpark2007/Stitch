"""Narrate and time: one track with pauses, shots cut on words, bounds kept by moving,
dropping or (last resort) repeating photos."""

import io
import wave

import pytest

from film_checks import assert_well_cut
from agent.tools.narration import FakeTTS
from film.narrate import BEAT_PAUSE, LEAD_IN, TTSNarrator, silent_narration
from film.plan import PlanBeat, PlanShot
from film.timing import MAX_SHOT, MIN_SHOT, TAIL, check_narration, time_film
from film.types import Narration


def beat(narration: str, *ids: str, title: str = "t") -> PlanBeat:
    return PlanBeat(title=title, narration=narration, shots=[PlanShot(i, f"cap {i}") for i in ids])


def words(n: int, end: str = ".") -> str:
    return " ".join(f"w{i}" for i in range(n)) + end


def describe(*ids: str, **focus) -> dict[str, dict]:
    return {i: {"focus": focus.get(i, [0.5, 0.5]), "quality": 0.5} for i in ids}


def ids_of(beats: list[PlanBeat]) -> set[str]:
    return {s.photo_id for b in beats for s in b.shots}


def test_narrator_lays_lines_end_to_end_with_pauses_in_one_wav():
    lines = ["We drove up to the lake.", "The cabin smelled like cedar, and old books."]
    n = TTSNarrator(FakeTTS()).speak(lines)
    assert n.content_type == "audio/wav" and n.audio is not None
    assert n.line_starts[0] == LEAD_IN
    first = FakeTTS().synthesize(lines[0])
    assert n.line_starts[1] == pytest.approx(LEAD_IN + first.duration + BEAT_PAUSE, abs=1e-3)
    with wave.open(io.BytesIO(n.audio)) as w:
        assert w.getnframes() / w.getframerate() == pytest.approx(n.duration, abs=1e-3)
    assert [w for _, w in n.words][:3] == ["We", "drove", "up"]
    silent = silent_narration(lines)
    assert silent.audio is None and silent.content_type is None
    assert silent.line_starts == n.line_starts and silent.words == n.words


def test_a_failing_tts_gives_silent_estimated_timings():
    class Down:
        calls = 0

        def synthesize(self, text, *, voice=None):
            Down.calls += 1
            raise RuntimeError("503")

    n = TTSNarrator(Down()).speak(["One line.", "Two lines."])
    assert n.audio is None and n.words == silent_narration(["One line.", "Two lines."]).words
    assert Down.calls == 2  # one retry for the whole film, not one per line


def test_normal_film_cuts_on_words_and_beats_start_with_their_lines():
    plan = [beat(words(30), "a", "b", "c"), beat(words(25), "d", "e"), beat(words(20), "f", "g")]
    narration = silent_narration([b.narration for b in plan])
    cut = time_film(plan, narration, describe(*"abcdefg"))
    assert_well_cut(cut.shots, cut.beats, cut.words, cut.duration, ids_of(plan))
    assert [s.photo_id for s in cut.shots] == list("abcdefg")
    assert [b.start for b in cut.beats] == [0, narration.line_starts[1], narration.line_starts[2]]
    assert cut.duration == pytest.approx(narration.duration + TAIL, abs=1e-3)
    word_times = {round(t, 3) for t, _ in narration.words}
    beat_starts = {b.start for b in cut.beats}
    inner = [s.start for s in cut.shots if s.start not in beat_starts]
    assert inner and all(t in word_times for t in inner)
    assert cut.shots[0].caption == "cap a"


def test_too_many_photos_for_a_short_beat_move_to_the_next_beat_in_order():
    plan = [beat(words(8), *"abcdef"), beat(words(40), "g")]
    cut = time_film(plan, silent_narration([b.narration for b in plan]), describe(*"abcdefg"))
    assert_well_cut(cut.shots, cut.beats, cut.words, cut.duration, ids_of(plan))
    assert [s.photo_id for s in cut.shots] == list("abcdefg")
    assert [s.beat for s in cut.shots] == [0, 1, 1, 1, 1, 1, 1]


def test_when_no_beat_has_room_the_lowest_quality_photos_are_dropped():
    # Five 4.9 s beats hold one shot each; the last (to 36 s) holds four: 9 of 12 photos.
    ids = "abcdefghijkl"
    plan = [beat(f"Line {k}.", ids[2 * k], ids[2 * k + 1]) for k in range(6)]
    narration = Narration(None, None, [0.6, 4.9, 9.8, 14.7, 19.6, 24.5], [], 26.0)
    desc = describe(*ids)
    for worst in "cfh":
        desc[worst]["quality"] = 0.1
    cut = time_film(plan, narration, desc)
    assert_well_cut(cut.shots, cut.beats, cut.words, cut.duration, set(ids))
    shown = [s.photo_id for s in cut.shots]
    assert len(shown) == 9 and not set("cfh") & set(shown)
    assert shown == sorted(shown)


def test_long_narration_with_few_photos_repeats_rather_than_holding_past_max():
    plan = [beat(words(120), "a", "b")]
    narration = silent_narration([plan[0].narration])
    cut = time_film(plan, narration, describe("a", "b"))
    assert_well_cut(cut.shots, cut.beats, cut.words, cut.duration, {"a", "b"})
    assert len(cut.shots) >= cut.duration / MAX_SHOT
    assert sum(s.caption is not None for s in cut.shots) == 2  # a repeat carries no caption


def test_a_beat_too_short_for_a_shot_merges_into_the_next():
    plan = [beat(words(20), "a", "b", title="One"), beat("Yes.", "c", title="Two"), beat(words(20), "d", "e", title="Three")]
    narration = silent_narration([b.narration for b in plan])
    cut = time_film(plan, narration, describe(*"abcde"))
    assert_well_cut(cut.shots, cut.beats, cut.words, cut.duration, ids_of(plan))
    assert [b.title for b in cut.beats] == ["One", "Two"]
    assert cut.beats[1].narration.startswith("Yes. w0")


def test_short_story_still_gives_each_photo_time():
    plan = [beat("We went to the lake.", *"abcdef")]
    cut = time_film(plan, silent_narration([plan[0].narration]), describe(*"abcdef"))
    assert_well_cut(cut.shots, cut.beats, cut.words, cut.duration, set("abcdef"))
    assert len(cut.shots) == 6 and all(s.duration >= MIN_SHOT for s in cut.shots)


def test_motion_follows_focus_and_never_repeats_back_to_back():
    plan = [beat(words(60), *"abcd")]
    desc = describe(*"abcd", a=[0.1, 0.5], b=[0.15, 0.4], c=[0.9, 0.5], d=[0.5, 0.5])
    cut = time_film(plan, silent_narration([plan[0].narration]), desc)
    # a pans left; b wants left too, so it takes the next motion; c wants right, now taken; ...
    assert [s.motion for s in cut.shots] == ["left", "right", "in", "out"]
    assert cut.shots[0].focus == (0.1, 0.5)
    assert all(a.motion != b.motion for a, b in zip(cut.shots, cut.shots[1:]))


def test_check_narration_rejects_a_narration_that_does_not_fit():
    with pytest.raises(ValueError):
        check_narration(Narration(None, None, [0.0], [], 3.0), 2)
    with pytest.raises(ValueError):
        check_narration(Narration(None, None, [2.0, 1.0], [], 3.0), 2)
    with pytest.raises(ValueError):
        check_narration(Narration(None, None, [0.0], [(float("nan"), "x")], 3.0), 1)
