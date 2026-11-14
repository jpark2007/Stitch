"""Set rise/fold tracks, shot timing and effects."""

import json
from pathlib import Path

import pytest

from screenplay.layout import (
    RISE,
    SNAP,
    content_end,
    effect_entry,
    fold_keys,
    fold_out_seconds,
    next_shot_timing,
    rise_keys,
)
from screenplay.schema import TimelineEffect
from screenplay.tracks import TrackSet

SAMPLES = Path(__file__).resolve().parents[3] / "web" / "public" / "samples"


def as_tracks(keys) -> dict:
    ts = TrackSet()
    ts.extend(keys)
    return {(t.target, t.channel): json.loads(t.model_dump_json())["keys"] for t in ts.tracks()}


def test_rise_keys_match_sample():
    raw = json.loads((SAMPLES / "hymn_books.timeline.json").read_text())
    want = {(t["target"], t["channel"]): t["keys"] for t in raw["tracks"] if t["target"].startswith("set:")}
    assert as_tracks(rise_keys("church_steps_1988", 3, 0, "fold")) == want


def test_rise_keys_none_snaps():
    got = as_tracks(rise_keys("s", 2, 5.0, "none"))
    assert got == {("set:s.layer0", "fold"): [[5.0, 0.0], [5.01, 1.0]],
                   ("set:s.layer1", "fold"): [[5.0, 0.0], [5.01, 1.0]]}


def test_fold_keys_front_first():
    keys, used = fold_keys("s", 3, 10.0, "fold")
    got = as_tracks(keys)
    assert got[("set:s.layer2", "fold")] == [[10.0, 1.0], [10.4, 0.0]]
    assert got[("set:s.layer1", "fold")] == [[10.1, 1.0], [10.5, 0.0]]
    assert got[("set:s.layer0", "fold")] == [[10.2, 1.0], [10.6, 0.0]]
    assert used == pytest.approx(0.6) == fold_out_seconds(3)
    assert fold_out_seconds(1) == pytest.approx(0.4)


def test_fold_keys_none_snaps():
    keys, used = fold_keys("s", 2, 10.0, "none")
    assert used == 0
    assert as_tracks(keys)[("set:s.layer0", "fold")] == [[10.0, 1.0], [10 + SNAP, 0.0]]


def test_unknown_transition_raises():
    with pytest.raises(ValueError):
        rise_keys("s", 1, 0, "dissolve")
    with pytest.raises(ValueError):
        fold_keys("s", 1, 0, "dissolve")


def test_next_shot_timing_different_set_fold():
    prev_end, start, action, keys = next_shot_timing(10.65, "church", 3, "park", "fold")
    assert prev_end == start == pytest.approx(11.25)
    assert action == pytest.approx(11.25 + RISE)
    assert keys == fold_keys("church", 3, 10.65, "fold")[0]
    assert all(k[0].startswith("set:church.") for k in keys)


def test_next_shot_timing_different_set_none():
    prev_end, start, action, keys = next_shot_timing(10.65, "church", 3, "park", "none")
    assert prev_end == start == 10.65
    assert action == pytest.approx(11.85)
    assert keys == fold_keys("church", 3, 10.65, "none")[0]
    assert len(keys) == 6


def test_next_shot_timing_same_set():
    prev_end, start, action, keys = next_shot_timing(10.65, "church", 3, "church", "fold")
    assert (prev_end, start, keys) == (10.65, 10.65, [])
    assert action == pytest.approx(11.85)


def test_content_end():
    assert content_end(9.7, 10.05) == 10.65
    assert content_end(9.7, None) == 10.2
    assert content_end(12.0, 10.05) == 12.5


@pytest.mark.parametrize("kind,dur", [("confetti", 2.0), ("steam", 3.0), ("smoke", 3.0), ("time_of_day", 1.0)])
def test_effect_default_durations(kind, dur):
    e = effect_entry(kind, 4.0, (0.1, 0.2, 0.5), None, "dusk" if kind == "time_of_day" else None, 20.0)
    assert isinstance(e, TimelineEffect)
    assert e.dur == dur and e.kind == kind and e.t == 4.0
    assert e.at == [0.1, 0.2, 0.5]
    TimelineEffect.model_validate(json.loads(e.model_dump_json()))


def test_effect_rain_lasts_to_shot_end_and_explicit_dur_wins():
    rain = effect_entry("rain", 4.2, None, None, None, 20.0)
    assert rain.dur == pytest.approx(15.8) and rain.at is None
    assert effect_entry("rain", 4.2, None, 2.5, None, 20.0).dur == 2.5
    assert effect_entry("confetti", 1.0, (0, 0, 0.5), 0.75, None, 9.0).dur == 0.75
    tod = effect_entry("time_of_day", 0.0, None, None, "night", 9.0)
    assert tod.value == "night"
    assert json.loads(tod.model_dump_json()) == {"t": 0.0, "kind": "time_of_day", "at": None, "dur": 1.0, "value": "night"}


def test_effect_unknown_kind_fails_validation():
    with pytest.raises(Exception):
        effect_entry("fireworks", 0.0, None, 1.0, None, 5.0)
