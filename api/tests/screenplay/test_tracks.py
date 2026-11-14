"""TrackSet normalisation and contract interpolation."""

import json
from pathlib import Path

import pytest

from screenplay.schema import Timeline, Track
from screenplay.tracks import TrackSet, evaluate, round_value

SAMPLES = Path(__file__).resolve().parents[3] / "web" / "public" / "samples"


@pytest.fixture(scope="module")
def sample() -> Timeline:
    return Timeline.model_validate(json.loads((SAMPLES / "hymn_books.timeline.json").read_text()))


def keys_of(tracks: list[Track], target: str, channel: str) -> list:
    return next([list(k) for k in t.keys] for t in tracks if t.target == target and t.channel == channel)


# ------------------------------------------------------------------ normalisation

def test_rounds_floats_including_lists_and_keeps_ints():
    ts = TrackSet()
    ts.add("puppet:dad", "root.position", 1.23456, (0.1 + 0.2, 1, -0.00001))
    ts.add("puppet:dad", "root.facing", 0, 1)
    ts.add("puppet:dad", "root.facing", 2.0, -1)
    [facing, pos] = ts.tracks()
    assert pos.keys == [(1.235, [0.3, 1, 0.0])]
    assert facing.keys == [(0, 1), (2.0, -1)]
    assert [v for _, v in facing.keys] == [1, -1]  # facing is exactly +-1, never rounded away
    assert json.loads(facing.model_dump_json())["keys"] == [[0, 1], [2.0, -1]]
    assert round_value(1) == 1 and type(round_value(1)) is int
    assert round_value([0, 1.23456]) == [0, 1.235] and type(round_value([0, 1.2])[0]) is int


def test_round_value_rejects_bad_types():
    with pytest.raises(TypeError):
        round_value(True)
    with pytest.raises(TypeError):
        round_value({"a": 1})
    with pytest.raises(TypeError):
        TrackSet().add("camera", "fov", "1", 35)


def test_dedup_and_stable_sort():
    ts = TrackSet()
    ts.add("puppet:mom", "arm_r.upper.rot", 3.0, 0.5)
    ts.add("puppet:mom", "arm_r.upper.rot", 1.0, 0.1)
    ts.add("puppet:mom", "arm_r.upper.rot", 3.0, 0.5)          # exact duplicate: dropped
    ts.add("puppet:mom", "arm_r.upper.rot", 3.0004, 0.2)       # same t after rounding, other value: kept
    ts.add("puppet:mom", "arm_r.upper.rot", 1.0, 0.1000001)    # equal after rounding: dropped
    [tr] = ts.tracks()
    assert tr.keys == [(1.0, 0.1), (3.0, 0.5), (3.0, 0.2)]


def test_tracks_sorted_by_target_then_channel():
    ts = TrackSet()
    ts.add("puppet:mom", "root.position", 0, [0, 0, 0.5])
    ts.add("camera", "position", 0, [0, 1, 2])
    ts.add("camera", "fov", 0, 35)
    ts.add("prop:a", "parent", 0, "ground")
    ts.add("camera", "lookAt", 0, [0, 0, 0])
    assert [(t.target, t.channel) for t in ts.tracks()] == [
        ("camera", "fov"), ("camera", "lookAt"), ("camera", "position"),
        ("prop:a", "parent"), ("puppet:mom", "root.position"),
    ]


def test_extend_has_track_last_key_time():
    ts = TrackSet()
    assert ts.last_key_time() == 0.0
    assert not ts.has_track("camera", "fov")
    ts.extend([("camera", "fov", 0, 35), ("camera", "fov", 9.70004, 28)])
    assert ts.has_track("camera", "fov")
    assert ts.last_key_time() == 9.7
    assert ts.value_at("camera", "position", 1) is None


def test_reproduces_sample_rounding_from_raw_generator_values(sample):
    """Feed the generator's unrounded arithmetic in, get the sample's keys out."""
    ts = TrackSet()
    for i, start in enumerate([0.0, 0.2, 0.4]):
        ts.add(f"set:church_steps_1988.layer{i}", "fold", start, 0.0)
        ts.add(f"set:church_steps_1988.layer{i}", "fold", start + 0.8, 1.0)
    ts.add("puppet:dad", "root.facing", 0, 1)
    ts.add("puppet:mom", "root.facing", 0, -1)
    for tr in ts.tracks():
        assert tr.keys == [tuple(k) for k in keys_of(sample.tracks, tr.target, tr.channel)]
    raw_facing = json.loads((SAMPLES / "hymn_books.timeline.json").read_text())
    dad = next(t for t in raw_facing["tracks"] if t["target"] == "puppet:dad" and t["channel"] == "root.facing")
    ours = next(t for t in ts.tracks() if t.target == "puppet:dad" and t.channel == "root.facing")
    assert json.loads(ours.model_dump_json())["keys"] == dad["keys"]


def test_roundtrip_whole_sample_is_identity(sample):
    raw = json.loads((SAMPLES / "hymn_books.timeline.json").read_text())
    ts = TrackSet()
    for tr in raw["tracks"]:
        for t, v in tr["keys"]:
            ts.add(tr["target"], tr["channel"], t, v)
    assert [json.loads(t.model_dump_json()) for t in ts.tracks()] == raw["tracks"]


# ------------------------------------------------------------------ interpolation

def test_value_at_holds_before_and_after():
    ts = TrackSet()
    ts.add("puppet:a", "head.rot", 1.0, 0.2)
    ts.add("puppet:a", "head.rot", 2.0, 0.4)
    assert ts.value_at("puppet:a", "head.rot", 0.0) == 0.2
    assert ts.value_at("puppet:a", "head.rot", 5.0) == 0.4


def test_value_at_linear_floats_and_lists():
    ts = TrackSet()
    ts.add("puppet:a", "head.rot", 1.0, 0.0)
    ts.add("puppet:a", "head.rot", 3.0, 1.0)
    ts.add("puppet:a", "root.position", 0.0, [0.0, 0.0, 0.5])
    ts.add("puppet:a", "root.position", 2.0, [1.0, 0.4, 0.5])
    assert ts.value_at("puppet:a", "head.rot", 2.0) == pytest.approx(0.5)
    assert ts.value_at("puppet:a", "root.position", 0.5) == pytest.approx([0.25, 0.1, 0.5])


def test_value_at_step_channels_and_strings():
    ts = TrackSet()
    ts.add("puppet:a", "root.facing", 0, 1)
    ts.add("puppet:a", "root.facing", 2, -1)
    ts.add("prop:b", "state", 0, "closed")
    ts.add("prop:b", "state", 2, "open")
    ts.add("prop:b", "parent", 0, "ground")
    ts.add("prop:b", "parent", 2, "puppet:a.hand_r")
    assert ts.value_at("puppet:a", "root.facing", 1.99) == 1
    assert ts.value_at("puppet:a", "root.facing", 2) == -1
    assert ts.value_at("prop:b", "state", 1.5) == "closed"
    assert ts.value_at("prop:b", "state", 2.5) == "open"
    assert ts.value_at("prop:b", "parent", 1.0) == "ground"
    assert ts.value_at("prop:b", "parent", 2.0) == "puppet:a.hand_r"


def test_same_time_keys_last_one_wins_at_and_after():
    ts = TrackSet()
    ts.add("puppet:a", "head.rot", 0, 0.0)
    ts.add("puppet:a", "head.rot", 1, 0.5)
    ts.add("puppet:a", "head.rot", 1, 1.0)
    ts.add("puppet:a", "head.rot", 2, 0.0)
    assert ts.value_at("puppet:a", "head.rot", 0.5) == pytest.approx(0.25)
    assert ts.value_at("puppet:a", "head.rot", 1.0) == 1.0
    assert ts.value_at("puppet:a", "head.rot", 1.5) == pytest.approx(0.5)


def test_prop_position_never_interpolates_across_parent(sample):
    assert evaluate(sample.tracks, "prop:hymn_books", "position", 6.0) == [0.12, 0.3, 0.5]
    assert evaluate(sample.tracks, "prop:hymn_books", "position", 7.1) == [0, 0, 0]
    # interpolates inside one parent span (the fall)
    mid = evaluate(sample.tracks, "prop:hymn_books", "position", 4.6)
    assert mid == pytest.approx([0.0825, 0.4065, 0.5])
    assert evaluate(sample.tracks, "prop:hymn_books", "parent", 6.0) == "ground"
    assert evaluate(sample.tracks, "camera", "position", 99) == [0.2, 0.6, 1.5]
    assert evaluate(sample.tracks, "camera", "nope", 1) is None


def test_prop_position_parent_key_inside_span_trackset():
    ts = TrackSet()
    ts.add("prop:p", "parent", 0, "ground")
    ts.add("prop:p", "position", 0, [0.0, 0.0, 0.5])
    ts.add("prop:p", "parent", 1.0, "puppet:a.hand_r")   # no position key at the parent swap
    ts.add("prop:p", "position", 2.0, [0.0, 0.0, 0.0])
    assert ts.value_at("prop:p", "position", 0.9) == [0.0, 0.0, 0.5]
    assert ts.value_at("prop:p", "position", 1.0) == [0.0, 0.0, 0.0]
    assert ts.value_at("prop:p", "position", 1.5) == [0.0, 0.0, 0.0]
    # other targets' position channels still interpolate
    ts.add("puppet:p", "position", 0, [0.0, 0.0, 0.0])
    ts.add("puppet:p", "position", 2, [1.0, 0.0, 0.0])
    assert ts.value_at("puppet:p", "position", 1) == pytest.approx([0.5, 0.0, 0.0])


def test_evaluate_matches_value_at(sample):
    ts = TrackSet()
    for tr in sample.tracks:
        for t, v in tr.keys:
            ts.add(tr.target, tr.channel, t, v)
    for tr in sample.tracks:
        for t in [0, 0.3, 1.2, 2.5, 4.45, 4.6, 5.0, 6.0, 7.1, 8.0, 8.3, 9.0, 12.0]:
            a = ts.value_at(tr.target, tr.channel, t)
            b = evaluate(sample.tracks, tr.target, tr.channel, t)
            assert a == pytest.approx(b) if not isinstance(a, str) else a == b, (tr.target, tr.channel, t)
