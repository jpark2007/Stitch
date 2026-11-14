"""Ingest and select: EXIF time and orientation, hashes, dedupe, spread across the event."""

from datetime import timedelta

from PIL import Image

from film_images import T0, blurred, encode, event, photo, scene
from film.ingest import hamming, ingest
from film.selection import MAX_SHOTS, MIN_SHOTS, clusters, select, target_count
from film.types import SourcePhoto


def test_ingest_reads_time_upright_size_and_skips_undecodable():
    landscape = scene(1)
    photos = [
        photo("timed", scene(0), taken=T0),
        photo("portrait", scene(2, size=(480, 640))),
        photo("rotated", landscape, orientation=6),  # stored 640x480, shown upright 480x640
        SourcePhoto(id="junk", data=b"not an image at all", content_type="image/jpeg"),
        SourcePhoto(id="empty", data=b"", content_type="image/png"),
    ]
    got = {p.id: p for p in ingest(photos)}
    assert set(got) == {"timed", "portrait", "rotated"}
    assert got["timed"].taken_at == T0
    assert got["portrait"].taken_at is None
    assert (got["portrait"].width, got["portrait"].height) == (480, 640)
    assert (got["rotated"].width, got["rotated"].height) == (480, 640) and got["rotated"].portrait
    assert [p.order for p in ingest(photos)] == [0, 1, 2]


def test_hash_is_upright_so_a_rotated_copy_matches_its_original():
    im = scene(7)
    upright = im.transpose(Image.Transpose.ROTATE_270)  # what a viewer sees for orientation 6
    a, b = ingest([photo("a", upright), photo("b", im, orientation=6)])
    assert hamming(a.dhash, b.dhash) <= 4
    c = ingest([photo("c", scene(8))])[0]
    assert hamming(a.dhash, c.dhash) > 12


def test_sharpness_ranks_blur_lower():
    sharp, soft = ingest([photo("sharp", scene(3)), photo("soft", blurred(scene(3)))])
    assert sharp.sharpness > soft.sharpness * 2


def test_dedupe_keeps_the_sharper_of_near_duplicates():
    base = scene(4)
    photos = [
        photo("soft", blurred(base), taken=T0),
        photo("sharp", base, taken=T0 + timedelta(seconds=2)),
        photo("other", scene(5), taken=T0 + timedelta(minutes=30)),
        photo("third", scene(6), taken=T0 + timedelta(minutes=60)),
    ]
    readable = ingest(photos)
    groups = clusters(readable)
    assert sorted(len(g) for g in groups) == [1, 1, 2]
    chosen = [p.id for p in select(readable, "Short story.")]
    assert "sharp" in chosen and "soft" not in chosen
    assert chosen == ["sharp", "other", "third"]  # chronological


def _pair(dx: int, gap: timedelta | None):
    base = scene(9)
    moved = base.crop((dx, 0, 640, 480)).resize((640, 480))  # the camera moved a little
    return ingest([photo("a", base, taken=T0), photo("b", moved, taken=None if gap is None else T0 + gap)])


def test_capture_times_tighten_or_loosen_the_duplicate_rule():
    # A bigger move (hash distance 11..20) is one shot only inside a burst.
    burst = _pair(60, timedelta(seconds=1))
    assert 10 < hamming(burst[0].dhash, burst[1].dhash) <= 20
    assert len(clusters(burst)) == 1
    assert len(clusters(_pair(60, timedelta(minutes=2)))) == 2
    assert len(clusters(_pair(60, None))) == 2
    # A small move (distance 7..10) is a duplicate nearby in time, two moments hours apart.
    near = _pair(50, timedelta(minutes=2))
    assert 6 < hamming(near[0].dhash, near[1].dhash) <= 10
    assert len(clusters(near)) == 1
    assert len(clusters(_pair(50, timedelta(hours=3)))) == 2


def test_target_count_scales_with_story_and_is_clamped():
    assert target_count("one two three") == MIN_SHOTS
    assert target_count(" ".join(["word"] * 150)) == 12  # 60 s of narration, one shot per 5 s
    assert target_count(" ".join(["word"] * 5000)) <= MAX_SHOTS


def test_select_spreads_over_the_whole_event_not_the_first_photos():
    # 30 photos: 25 in the first hour, then 5 across the evening.
    photos = [photo(f"m{i:02d}", scene(100 + i), taken=T0 + timedelta(minutes=2 * i)) for i in range(25)]
    photos += [photo(f"e{i}", scene(200 + i), taken=T0 + timedelta(hours=6 + i)) for i in range(5)]
    chosen = select(ingest(photos), " ".join(["word"] * 100))  # wants 8 shots
    ids = [p.id for p in chosen]
    assert len(ids) == 8
    assert ids[0] == "m00" and ids[-1] == "e4"
    assert sum(i.startswith("e") for i in ids) >= 3
    assert ids == sorted(ids, key=lambda i: [p.id for p in photos].index(i))


def test_never_more_than_the_distinct_photos_and_at_least_three():
    distinct = event(4)
    chosen = select(ingest(distinct), " ".join(["word"] * 300))
    assert len(chosen) == 4
    same = [photo(f"d{i}", scene(42), taken=T0 + timedelta(seconds=i)) for i in range(8)]
    assert len(select(ingest(same), "We went.")) == 3


def test_encode_helper_round_trips_exif():
    data = encode(scene(1), taken=T0, orientation=6)
    got = ingest([SourcePhoto("x", data, "image/jpeg")])[0]
    assert got.taken_at == T0 and (got.width, got.height) == (480, 640)
