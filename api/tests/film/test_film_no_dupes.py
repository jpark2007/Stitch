"""A film never shows the same picture twice, and the vision calls stay bounded.

Look-alikes (selection.looks_alike) on synthetic photos, the diversity-aware pick, the
semantic pass in the plan (the describer's "scene"), the last guard in make_film, and the
cost bounds. Pillow only, no network.
"""

from __future__ import annotations

import io
import logging
import random
import threading
import time
from datetime import timedelta

import pytest
from PIL import Image

from film_checks import alike_pairs, assert_no_look_alikes, assert_well_cut
from film_images import STORY, T0, blurred, brighter, cropped, encode, outing, photo, scene, tinted_scene
from film.describe import DESCRIBE_PROMPT, FakeDescriber, normalize
from film.ingest import ingest
from film.narrate import offline_narrator
from film.pipeline import distinct_shots, make_film, story_for
from film.timing import LONG_HOLD, NARRATION_PER_PHOTO
from film.plan import (
    EDITOR_SYSTEM,
    PlanBeat,
    PlanShot,
    editor_request,
    fallback_plan,
    make_plan,
    scene_key,
    trim_to,
    validate_plan,
)
from film.selection import (
    MAX_DESCRIBED,
    SECONDS_PER_WORD,
    candidate_count,
    chronological,
    clusters,
    enforce_distinct,
    looks_alike,
    select,
    spread,
    target_count,
    word_count,
)
from film.types import Services, SourcePhoto, Story

# ------------------------------------------------------------------ the look-alike rule


def reencoded(im: Image.Image) -> Image.Image:
    """Through a lower-quality JPEG and back, as a messaging app re-saves a photo."""
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=55)
    return Image.open(io.BytesIO(buf.getvalue())).convert("RGB")


COPIES = {
    "re-encoded": reencoded,
    "resized": lambda im: im.resize((im.width // 2, im.height // 2)),
    "cropped 5% left": lambda im: cropped(im, 0.05, "left"),
    "cropped 5% top": lambda im: cropped(im, 0.05, "top"),
    "cropped 5% centre": lambda im: cropped(im, 0.05, "centre"),
    "brightened": lambda im: brighter(im, 1.2),
    "darkened": lambda im: brighter(im, 0.8),
}


@pytest.mark.parametrize("kind", list(COPIES))
@pytest.mark.parametrize("gap", [timedelta(minutes=1), timedelta(hours=5), None], ids=["1min", "5h", "no-exif"])
def test_a_copy_looks_like_its_original_whatever_its_capture_time(kind, gap):
    for seed in range(8):
        im = scene(seed)
        copy = COPIES[kind](im)
        a, b = ingest([photo("a", im, taken=T0), photo("b", copy, taken=None if gap is None else T0 + gap)])
        assert looks_alike(a, b) and looks_alike(b, a), (kind, seed)
        assert len(clusters([a, b])) == 1


def test_different_scenes_never_look_alike():
    photos = ingest([photo(f"s{i}", scene(i)) for i in range(40)])
    assert alike_pairs(photos) == []
    assert len(clusters(photos)) == 40


@pytest.mark.parametrize("base", [(200, 60, 40), (40, 120, 200), (90, 160, 70), (230, 220, 200)])
def test_different_scenes_in_one_dominant_colour_never_look_alike(base):
    photos = ingest([photo(f"t{i}", tinted_scene(i, base), taken=T0 + timedelta(seconds=30 * i)) for i in range(16)])
    assert alike_pairs(photos) == []
    assert len(clusters(photos)) == 16


def _jpeg(im: Image.Image, quality: int) -> Image.Image:
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=quality)
    return Image.open(io.BytesIO(buf.getvalue())).convert("RGB")


def _rotated(im: Image.Image, degrees: float, black_corners: bool = False) -> Image.Image:
    turned = im.rotate(degrees, resample=Image.Resampling.BICUBIC)
    if black_corners:
        return turned
    w, h = im.size
    return turned.crop((int(w * 0.04), int(h * 0.04), int(w * 0.96), int(h * 0.96))).resize((w, h))


def _shifted(im: Image.Image, frac: float) -> Image.Image:
    w, h = im.size
    dx, dy = int(w * frac), int(h * frac)
    return im.crop((dx, dy, w, h)).resize((w, h))


def _warm(im: Image.Image) -> Image.Image:
    r, g, b = im.split()
    return Image.merge("RGB", (r.point(lambda v: min(255, int(v * 1.15))), g, b.point(lambda v: int(v * 0.85))))


def _enhanced(kind: str, factor: float):
    from PIL import ImageEnhance

    return lambda im: getattr(ImageEnhance, kind)(im).enhance(factor)


HARD_COPIES = {
    "jpeg q20": lambda im: _jpeg(im, 20),
    "jpeg q35": lambda im: _jpeg(im, 35),
    "resize 0.5": lambda im: im.resize((im.width // 2, im.height // 2)),
    "resize 0.25": lambda im: im.resize((im.width // 4, im.height // 4)),
    "crop 3% left": lambda im: cropped(im, 0.03, "left"),
    "crop 5% top": lambda im: cropped(im, 0.05, "top"),
    "crop 8% left": lambda im: cropped(im, 0.08, "left"),
    "crop 8% centre": lambda im: cropped(im, 0.08, "centre"),
    "crop 8% corner": lambda im: _shifted(im, 0.08),
    "crop 8% left, not resized": lambda im: im.crop((int(im.width * 0.08), 0, im.width, im.height)),
    "shift 6%": lambda im: _shifted(im, 0.06),
    "brightness 0.7": _enhanced("Brightness", 0.7),
    "brightness 1.35": _enhanced("Brightness", 1.35),
    "contrast 0.7": _enhanced("Contrast", 0.7),
    "contrast 1.4": _enhanced("Contrast", 1.4),
    "saturation 0.4": _enhanced("Color", 0.4),
    "saturation 1.6": _enhanced("Color", 1.6),
    "black and white": lambda im: im.convert("L").convert("RGB"),
    "rotated 1 degree": lambda im: _rotated(im, 1),
    "rotated -2 degrees": lambda im: _rotated(im, -2),
    "rotated 2 degrees, black corners": lambda im: _rotated(im, 2, black_corners=True),
    "warm filter": _warm,
    "crop 5% + brighter + q40": lambda im: _jpeg(_enhanced("Brightness", 1.2)(_shifted(im, 0.05)), 40),
    "small + contrast + shift 4%": lambda im: _enhanced("Contrast", 1.2)(_shifted(im, 0.04)).resize(
        (im.width * 3 // 10, im.height * 3 // 10)
    ),
}


@pytest.mark.parametrize("kind", list(HARD_COPIES))
def test_a_hard_copy_of_a_day_out_photo_looks_like_its_original(kind):
    for seed in range(6):
        im = outing(700 + seed, ("park", "beach", "lake")[seed % 3])
        a, b = ingest([photo("a", im, taken=T0), photo("b", HARD_COPIES[kind](im), taken=T0 + timedelta(hours=3))])
        assert looks_alike(a, b), (kind, seed)


@pytest.mark.parametrize(
    "place, people, at_least",
    [("park", (2, 5), 27), ("park", (1, 2), 25), ("beach", (1, 5), 28), ("lake", (1, 5), 26)],
)
def test_different_moments_in_one_place_are_kept_apart(place, people, at_least):
    # 30 moments of one day out: the same sky, horizon and ground, people moving about. Their
    # colour layouts are nearly the same; only the structure tells them apart. (Before, the
    # colour layout chained a whole park album into one photo.)
    photos = ingest([
        photo(f"m{i:02d}", outing(100 + i, place, people), taken=T0 + timedelta(minutes=12 * i)) for i in range(30)
    ])
    chosen = select(photos, STORY, count=30)
    assert len(chosen) >= at_least, len(chosen)
    assert alike_pairs(chosen) == []


def test_selecting_from_a_thousand_photos_of_one_place_stays_fast():
    # The worst case for cost: nothing is ruled out by the cheap tests, so every comparison is
    # the structural one.
    photos = [
        photo(f"m{i:04d}", outing(3000 + i, "park", size=(320, 240)), taken=T0 + timedelta(seconds=30 * i))
        for i in range(1000)
    ]
    started = time.perf_counter()
    chosen = select(ingest(photos), STORY, count=candidate_count(24))
    elapsed = time.perf_counter() - started
    assert elapsed < 10, f"ingest + select of 1000 photos of one park took {elapsed:.1f}s"
    assert len(chosen) == MAX_DESCRIBED


def test_flat_photos_of_different_colours_are_different():
    red, blue = Image.new("RGB", (640, 480), (200, 40, 40)), Image.new("RGB", (640, 480), (40, 60, 200))
    a, b, c = ingest([photo("red", red), photo("blue", blue), photo("red2", brighter(red, 1.1))])
    assert not looks_alike(a, b)
    assert looks_alike(a, c)


# ------------------------------------------------------------------ selection


def test_look_alikes_far_apart_in_time_or_without_exif_are_not_both_chosen():
    photos = [photo(f"p{i}", scene(10 + i), taken=T0 + timedelta(minutes=20 * i)) for i in range(6)]
    photos.append(photo("p2-later", cropped(scene(12), 0.05), taken=T0 + timedelta(hours=9)))  # wrong clock
    photos.append(photo("p4-resaved", brighter(scene(14), 1.15)))  # no EXIF
    photos.append(photo("p0-next-year", reencoded(scene(10)), taken=T0 + timedelta(days=365)))
    chosen = select(ingest(photos), STORY, count=30)
    ids = {p.id for p in chosen}
    assert alike_pairs(chosen) == []
    assert len(chosen) == 6
    assert len(ids & {"p2", "p2-later"}) == 1 and len(ids & {"p4", "p4-resaved"}) == 1
    assert len(ids & {"p0", "p0-next-year"}) == 1


def forty_with_twelve_copies() -> tuple[list[SourcePhoto], int]:
    """28 different scenes over a day plus 12 copies of them, of every kind, shuffled."""
    rng = random.Random(7)
    times = [T0 + timedelta(minutes=15 * i) for i in range(28)]
    out = [photo(f"s{i:02d}", scene(400 + i), taken=times[i]) for i in range(28)]
    kinds = [
        lambda i: photo(f"c{i:02d}-burst", blurred(scene(400 + i)), taken=times[i] + timedelta(seconds=2)),
        lambda i: photo(f"c{i:02d}-resaved", reencoded(scene(400 + i))),
        lambda i: photo(f"c{i:02d}-small", scene(400 + i).resize((320, 240)), taken=times[i] + timedelta(hours=3)),
        lambda i: photo(f"c{i:02d}-crop", cropped(scene(400 + i), 0.05, "left"), taken=times[i] + timedelta(minutes=1)),
        lambda i: photo(f"c{i:02d}-cropc", cropped(scene(400 + i), 0.05, "centre")),
        lambda i: photo(f"c{i:02d}-bright", brighter(scene(400 + i), 1.2), taken=times[i] + timedelta(days=2)),
    ]
    for k, i in enumerate(rng.sample(range(28), 12)):
        out.append(kinds[k % len(kinds)](i))
    rng.shuffle(out)
    return out, 28


def test_forty_photos_with_twelve_copies_give_no_look_alike_pair():
    photos, distinct = forty_with_twelve_copies()
    readable = ingest(photos)
    assert len(clusters(readable)) == distinct
    for count in (6, 12, 24, 30):
        chosen = select(readable, STORY, count=count)
        assert alike_pairs(chosen) == [], count
        assert len(chosen) == min(count, distinct)
        assert len({p.id[1:3] for p in chosen}) == len(chosen)  # one photo per scene


def test_spread_refuses_a_look_alike_and_takes_the_next_nearest():
    a, b, c, a_copy = ingest([
        photo("a", scene(60), taken=T0),
        photo("b", scene(61), taken=T0 + timedelta(hours=1)),
        photo("c", scene(62), taken=T0 + timedelta(hours=2)),
        photo("a-copy", cropped(scene(60), 0.05), taken=T0 + timedelta(hours=2, minutes=1)),
    ])
    timeline = chronological([a, b, c, a_copy])
    # The last slot's nearest photo is the copy of the first: c is taken instead.
    assert [p.id for p in spread(timeline, 3)] == ["a", "b", "c"]
    # Asked for all four, it gives three rather than a look-alike.
    assert [p.id for p in spread(timeline, 4)] == ["a", "b", "c"]
    assert [p.id for p in spread(chronological([a, a_copy]), 2)] == ["a"]


def test_enforce_distinct_keeps_the_sharper_of_a_pair_in_order():
    sharp, soft, other = ingest([
        photo("sharp", scene(70), taken=T0 + timedelta(minutes=5)),
        photo("soft", blurred(scene(70)), taken=T0),
        photo("other", scene(71), taken=T0 + timedelta(minutes=9)),
    ])
    assert [p.id for p in enforce_distinct([soft, sharp, other])] == ["sharp", "other"]
    assert enforce_distinct([sharp, other]) == [sharp, other]


def test_candidate_count_adds_a_small_surplus_and_is_bounded():
    assert candidate_count(6) == 8  # at least two spare
    assert candidate_count(12) == 15
    assert candidate_count(24) == MAX_DESCRIBED == 30
    assert all(candidate_count(n) <= MAX_DESCRIBED for n in range(0, 200))


def test_ingest_and_select_scale_to_a_thousand_photos():
    photos = [photo(f"x{i:04d}", scene(5000 + i, size=(160, 120)), taken=T0 + timedelta(seconds=40 * i)) for i in range(1000)]
    started = time.perf_counter()
    chosen = select(ingest(photos), STORY, count=candidate_count(24))
    elapsed = time.perf_counter() - started
    assert elapsed < 10, f"ingest + select of 1000 photos took {elapsed:.1f}s"
    assert len(chosen) == MAX_DESCRIBED and alike_pairs(chosen) == []


# ------------------------------------------------------------------ the describer's "scene"


def test_describer_scene_is_optional_and_normalised():
    offline = FakeDescriber().describe(encode(scene(1)))
    assert "scene" not in offline  # the offline describer cannot tell
    d = normalize({"caption": "dinner", "scene": "  The Family at   Dinner Table "}, offline)
    assert d["scene"] == "the family at dinner table"
    assert "scene" not in normalize({"scene": 3}, offline)
    assert '"scene"' in DESCRIBE_PROMPT
    assert scene_key("The family, at the dinner table!") == scene_key("family at dinner table")
    assert scene_key(None) == "" and scene_key("") == ""


def _facts(scenes: list[tuple[str, int | None]], qualities: list[float] | None = None) -> list[dict]:
    """Described photos in capture order: (scene, minutes after T0 or None) each."""
    out = []
    for i, (label, minutes) in enumerate(scenes):
        out.append({
            "id": f"p{i}", "takenAt": None if minutes is None else (T0 + timedelta(minutes=minutes)).isoformat(),
            "caption": f"photo {i}", "scene": label, "people": [], "setting": "", "mood": "",
            "focus": [0.5, 0.5], "quality": qualities[i] if qualities else 0.5,
        })
    return out


EIGHT = [("arriving", 0), ("family at dinner table", 20), ("The family at the dinner table", 22),
         ("kids in the lake", 40), ("campfire", 60), ("marshmallows", 70), ("stars", 80), ("leaving", 90)]


def _all_in_two_beats(photos: list[dict]) -> dict:
    ids = [p["id"] for p in photos]
    return {"beats": [
        {"title": "One", "narration": "We drove up.", "shots": [{"photoId": i} for i in ids[:4]]},
        {"title": "Two", "narration": "We lit a fire.", "shots": [{"photoId": i} for i in ids[4:]]},
    ]}


def _ids(beats: list[PlanBeat]) -> list[str]:
    return [s.photo_id for b in beats for s in b.shots]


def test_the_plan_leaves_a_repeated_moment_out_before_anything_else():
    photos = _facts(EIGHT, qualities=[0.6, 0.9, 0.95, 0.7, 0.8, 0.3, 0.75, 0.5])
    beats = validate_plan(_all_in_two_beats(photos), photos, wanted=6)
    # p2 repeats p1's moment two minutes later: out first, though it is the best photo. Then
    # the lowest quality, p5. The first and last photos stay.
    assert _ids(beats) == ["p0", "p1", "p3", "p4", "p6", "p7"]
    # The same with no editor.
    story = Story(text=STORY, title=None, year=1998)
    assert _ids(fallback_plan(story, photos, wanted=6)) == ["p0", "p1", "p3", "p4", "p6", "p7"]
    # Nothing to spare: nothing is left out, the repeat included.
    assert len(_ids(validate_plan(_all_in_two_beats(photos), photos, wanted=8))) == 8


def test_the_same_label_far_apart_in_time_is_not_a_repeat_but_unknown_times_are():
    far = list(EIGHT)
    far[2] = ("family at dinner table", 300)
    photos = _facts(far, qualities=[0.6, 0.9, 0.95, 0.7, 0.8, 0.3, 0.75, 0.5])
    assert _ids(validate_plan(_all_in_two_beats(photos), photos, wanted=7)) == ["p0", "p1", "p2", "p3", "p4", "p6", "p7"]
    unknown = _facts([(label, None) for label, _ in EIGHT], qualities=[0.6, 0.9, 0.95, 0.7, 0.8, 0.3, 0.75, 0.5])
    assert "p2" not in _ids(validate_plan(_all_in_two_beats(unknown), unknown, wanted=7))


def test_photos_the_editor_left_out_come_back_only_up_to_the_wanted_count_and_never_a_repeat():
    photos = _facts(EIGHT, qualities=[0.6, 0.9, 0.95, 0.7, 0.8, 0.3, 0.75, 0.5])
    raw = {"beats": [
        {"title": "One", "narration": "We drove up.", "shots": [{"photoId": "p1"}]},
        {"title": "Two", "narration": "We lit a fire.", "shots": [{"photoId": "p4"}, {"photoId": "p7"}]},
    ]}
    beats = validate_plan(raw, photos, wanted=5)
    # Two back, best first: p6 (0.75) and p3 (0.7); p2 (0.95) repeats p1's moment.
    assert sorted(_ids(beats)) == ["p1", "p3", "p4", "p6", "p7"]
    # The editor used more than wanted: trimmed to the wanted count.
    assert len(_ids(validate_plan(_all_in_two_beats(photos), photos, wanted=4))) == 4


def test_trimming_never_takes_a_beats_only_photo():
    photos = _facts(EIGHT[:3])
    beats = [PlanBeat("a", "One.", [PlanShot("p0", None)]), PlanBeat("b", "Two.", [PlanShot("p1", None)]),
             PlanBeat("c", "Three.", [PlanShot("p2", None)])]
    trim_to(beats, photos, 1)
    assert _ids(beats) == ["p0", "p1", "p2"]


def test_make_plan_with_a_wanted_count_trims_both_plans_alike():
    photos = _facts(EIGHT, qualities=[0.6, 0.9, 0.95, 0.7, 0.8, 0.3, 0.75, 0.5])
    story = Story(text=STORY, title=None, year=1998)

    class Editor:
        def plan(self, story, photos):
            return _all_in_two_beats(photos)

    assert len(_ids(make_plan(story, photos, Editor(), 6))) == 6
    assert len(_ids(make_plan(story, photos, None, 6))) == 6
    assert make_plan(story, photos, None) == fallback_plan(story, photos)


def test_the_editor_is_told_the_wanted_count_and_that_leaving_photos_out_is_fine():
    assert "Use most of the photos" not in EDITOR_SYSTEM
    assert "leave photos out" in EDITOR_SYSTEM and "same moment" in EDITOR_SYSTEM and "wantedShots" in EDITOR_SYSTEM
    story = Story(text=STORY, title=None, year=1998)
    photos = _facts(EIGHT)
    request = editor_request(story, photos)
    assert f'"wantedShots": {min(8, target_count(STORY))}' in request
    assert '"scene": "family at dinner table"' in request and '"quality": 0.5' in request


# ------------------------------------------------------------------ make_film


class SceneDescriber:
    """Stub vision: a fixed scene label per photo (by its bytes), counting calls."""

    def __init__(self, labels: dict[bytes, tuple[str, float]]):
        self.labels = labels
        self.calls = 0
        self._lock = threading.Lock()

    def describe(self, data: bytes) -> dict:
        with self._lock:
            self.calls += 1
        label, quality = self.labels.get(data, ("", 0.5))
        return {"caption": label, "scene": label, "people": [], "setting": "", "mood": "",
                "focus": [0.5, 0.5], "quality": quality}


class UseEverything:
    def plan(self, story, photos):
        return {"beats": [{"title": "All", "narration": "We went to the lake and had dinner.",
                           "shots": [{"photoId": p["id"]} for p in photos]}]}


def _dinner(twin_label: str, minutes_apart: int) -> tuple[list[SourcePhoto], dict[bytes, tuple[str, float]]]:
    """Eight different photos; the third and fourth (visually different, `minutes_apart`
    apart) are labelled `twin_label` and "family at dinner table", best quality of all."""
    plan = [("arriving", 0, 0.6), ("swimming", 20, 0.7), ("family at dinner table", 40, 0.95),
            (twin_label, 40 + minutes_apart, 0.9), ("campfire", 120, 0.55), ("marshmallows", 130, 0.5),
            ("stars", 150, 0.65), ("leaving", 200, 0.6)]
    photos, labels = [], {}
    for i, (label, minutes, quality) in enumerate(plan):
        p = photo(f"p{i}", scene(800 + i), taken=T0 + timedelta(minutes=minutes))
        photos.append(p)
        labels[p.data] = (label, quality)
    return photos, labels


@pytest.mark.parametrize("editor", [None, UseEverything()], ids=["fallback", "editor"])
def test_two_photos_of_one_moment_from_different_angles_are_not_both_in_the_film(editor):
    story = Story(text="We went to the lake. We had dinner.", title=None, year=1998)
    assert target_count(story.text) == 6 and candidate_count(6) == 8
    photos, labels = _dinner("Family at the dinner table.", 2)
    readable = ingest(photos)
    assert alike_pairs(readable) == []  # they do not look alike: only the scene label tells
    cut = make_film(photos, story, Services(SceneDescriber(labels), editor, offline_narrator()))
    shown = {s.photo_id for s in cut.shots}
    assert len(shown & {"p2", "p3"}) == 1
    assert_well_cut(cut.shots, cut.beats, cut.words, cut.duration, {p.id for p in photos}, {p.id: p.data for p in photos})
    # Labelled as different moments, both best photos are in.
    photos, labels = _dinner("kids playing cards", 2)
    cut = make_film(photos, story, Services(SceneDescriber(labels), editor, offline_narrator()))
    assert {"p2", "p3"} <= {s.photo_id for s in cut.shots}


def _ingested(*pairs):
    return {p.id: p for p in ingest([photo(pid, im) for pid, im in pairs])}


def test_the_last_guard_drops_a_look_alike_shot_and_merges_an_emptied_beat(caplog):
    photos = _ingested(("a", scene(90)), ("b", scene(91)), ("a-copy", cropped(scene(90), 0.05)), ("c", scene(92)))
    beats = [
        PlanBeat("One", "We arrived.", [PlanShot("a", None), PlanShot("b", None)]),
        PlanBeat("Two", "We swam.", [PlanShot("a-copy", None)]),
        PlanBeat("Three", "We left.", [PlanShot("c", None)]),
    ]
    with caplog.at_level(logging.WARNING, logger="film.pipeline"):
        out = distinct_shots(beats, photos)
    assert [[s.photo_id for s in b.shots] for b in out] == [["a", "b"], ["c"]]
    assert out[0].narration == "We arrived. We swam."
    assert "a-copy" in caplog.text
    # Nothing alike: the plan is untouched and nothing is logged.
    caplog.clear()
    clean = [PlanBeat("One", "We arrived.", [PlanShot("a", None)]), PlanBeat("Two", "We left.", [PlanShot("c", None)])]
    assert distinct_shots(clean, photos) == clean and not caplog.text


def test_copies_of_one_photo_make_a_film_of_that_one_photo():
    base = scene(95)
    photos = [photo("a", base, taken=T0), photo("b", reencoded(base)), photo("c", brighter(base, 1.1)),
              photo("d", cropped(base, 0.05), taken=T0 + timedelta(hours=2))]
    cut = make_film(photos, Story(STORY, None, 1998), Services(FakeDescriber(), None, offline_narrator()))
    assert len({s.photo_id for s in cut.shots}) == 1


def test_a_long_story_over_few_photos_is_trimmed_and_no_photo_is_shown_twice():
    photos = [photo(f"p{i}", outing(900 + i, "lake"), taken=T0 + timedelta(minutes=10 * i)) for i in range(4)]
    long_story = " ".join(f"We swam to the raft number {i} and back again before lunch." for i in range(30))
    told = story_for(Story(long_story, None, 1998), 4)
    assert word_count(told.text) <= NARRATION_PER_PHOTO * 4 / SECONDS_PER_WORD
    assert told.text.startswith("We swam to the raft number 0") and told.text.endswith("number 29 and back again before lunch.")
    cut = make_film(photos, Story(long_story, None, 1998), Services(FakeDescriber(), None, offline_narrator()))
    ids = [s.photo_id for s in cut.shots]
    assert sorted(ids) == ["p0", "p1", "p2", "p3"]  # each once, none twice
    assert_well_cut(cut.shots, cut.beats, cut.words, cut.duration, {p.id for p in photos}, {p.id: p.data for p in photos})
    assert max(s.duration for s in cut.shots) <= LONG_HOLD
    assert story_for(Story("We went to the lake.", None, 1998), 4).text == "We went to the lake."


def test_the_describer_is_called_at_most_max_described_times_for_300_photos():
    photos = [photo(f"x{i:03d}", scene(3000 + i, size=(200, 150)), taken=T0 + timedelta(minutes=2 * i)) for i in range(300)]
    long_story = " ".join(["We swam and we laughed."] * 80)  # wants the most shots
    describer = SceneDescriber({})
    cut = make_film(photos, Story(long_story, None, 1998), Services(describer, None, offline_narrator()))
    assert describer.calls == candidate_count(target_count(long_story)) <= MAX_DESCRIBED
    shown = [s.photo_id for s in cut.shots]
    assert_no_look_alikes(shown, {p.id: p.data for p in photos})
    # A short story describes even fewer.
    describer = SceneDescriber({})
    make_film(photos, Story("We went to the lake.", None, 1998), Services(describer, None, offline_narrator()))
    assert describer.calls == candidate_count(6) == 8
