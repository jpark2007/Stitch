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


VOCAB = Path(__file__).resolve().parents[3] / "shared" / "vocabulary.json"


@pytest.mark.parametrize("transition", json.loads(VOCAB.read_text())["transitions"])
def test_every_vocabulary_transition_resolves(transition):
    rise = as_tracks(rise_keys("s", 3, 2.0, transition))
    assert set(rise) == {(f"set:s.layer{i}", "fold") for i in range(3)}
    assert all(k[0][1] == 0.0 and k[-1][1] == 1.0 for k in rise.values())
    keys, used = fold_keys("s", 3, 10.0, transition)
    fold = as_tracks(keys)
    assert all(k[0][1] == 1.0 and k[-1][1] == 0.0 for k in fold.values())
    assert used >= 0
    prev_end, start, action, _ = next_shot_timing(10.0, "s", 3, "t", transition)
    assert prev_end == start and action == pytest.approx(start + RISE)


def test_unknown_transition_error_names_it():
    with pytest.raises(ValueError, match="'dissolve'"):
        next_shot_timing(1.0, "a", 1, "b", "dissolve")


def test_hymn_books_camera_and_fold_tracks_within_tolerance():
    """Regression: camera + set-fold tracks this code makes for the hymn_books shot equal the golden ones."""
    from screenplay.camera import shot_camera_keys
    from screenplay.schema import CameraCue

    timeline = json.loads((SAMPLES / "hymn_books.timeline.json").read_text())
    screenplay = json.loads((SAMPLES / "hymn_books.screenplay.json").read_text())
    memory = json.loads((SAMPLES / "hymn_books.memory.json").read_text())
    shot = screenplay["shots"][0]
    tshot = timeline["shots"][0]
    set_ = next(s for s in memory["sets"] if s["id"] == shot["set"])
    marks = {**set_["marks"], **shot["marks"]}

    def target_at(name, t):  # mom stays on steps_top for the whole shot
        assert name == "mom"
        m = marks[shot["start"]["mom"]["at"]]
        return (2 * m[0] - 1, m[1], 0.5)

    keys = rise_keys(shot["set"], len(set_["layers"]), tshot["start"], shot["transition"])
    cam, _ = shot_camera_keys(
        [CameraCue.model_validate(c) for c in shot["camera"]], tshot["start"], tshot["actionStart"], None, target_at
    )
    got = as_tracks(keys + cam)
    want = {
        (t["target"], t["channel"]): t["keys"]
        for t in timeline["tracks"]
        if t["target"] == "camera" or t["target"].startswith("set:")
    }
    assert set(got) == set(want)
    for k, wkeys in want.items():
        gkeys = got[k]
        assert len(gkeys) == len(wkeys), k
        for (gt, gv), (wt, wv) in zip(gkeys, wkeys):
            assert gt == pytest.approx(wt, abs=1e-3), k
            assert gv == pytest.approx(wv, abs=1e-3), (k, gt)


# ------------------------------------------------------------ bug hunt: shot ends, zero layers

@pytest.mark.parametrize(("t", "shot_end"), [(4.6955, 5.365), (1.0005, 3.0), (2.2225, 9.9)])
def test_rain_without_dur_never_outlasts_the_shot(t, shot_end):
    """Bug: rain with no dur rounded t and dur separately, so t + dur could pass the shot's end
    (and the timeline's duration, for the last shot) by a millisecond: 4.696 + 0.67 > 5.365."""
    rain = effect_entry("rain", t, None, None, None, shot_end)
    assert rain.t + rain.dur <= shot_end + 1e-9
    assert rain.t + rain.dur == pytest.approx(shot_end, abs=1e-9)


def test_rain_rounding_holds_for_any_start():
    import random

    rng = random.Random(7)
    for _ in range(5000):
        t = round(rng.uniform(0, 30), 6)
        end = round(t + rng.uniform(0.5, 10), 3)
        rain = effect_entry("rain", t, None, None, None, end)
        assert rain.t + rain.dur <= end + 1e-9, (t, end, rain)


@pytest.mark.parametrize("t", [7.0, 7.0006, 7.0004, 1.2345675])
def test_rain_starting_at_the_shot_end_lasts_nothing(t):
    """The compiler first builds a rain with shot_end = t (a placeholder): rounding t up must
    not make that a negative duration."""
    assert effect_entry("rain", t, None, None, None, t).dur == 0.0


def test_rain_ending_before_it_starts_is_still_an_error():
    with pytest.raises(ValueError, match="negative duration"):
        effect_entry("rain", 7.0, None, None, None, 6.0)


def test_a_set_with_no_layers_takes_no_time_to_fold():
    """Bug: fold_out_seconds(0) said 0.4 s while fold_keys of a zero-layer set takes 0 s."""
    keys, used = fold_keys("void", 0, 3.0, "fold")
    assert (keys, used) == ([], 0.0)
    assert fold_out_seconds(0) == used
    assert rise_keys("void", 0, 0.0, "fold") == [] == rise_keys("void", 0, 0.0, "none")
    prev_end, start, action, keys = next_shot_timing(5.0, "void", 0, "church", "fold")
    assert (prev_end, start, keys) == (5.0, 5.0, [])
    assert action == pytest.approx(5.0 + RISE)


def test_content_end_with_no_keys_and_no_words():
    assert content_end(0.0, None) == 0.5
    assert content_end(0.0, 0.0) == 0.6


@pytest.mark.parametrize("t", [3.2405, 3.2775, 3.6475])
def test_compiled_rain_ends_within_the_timeline(t):
    """End to end: the shot's end comes from a camera move, so the rain's t + dur (rounded
    apart) was 7.701 on a 7.7 s timeline."""
    from verb_cases import base_scene

    from screenplay.compiler import compile as compile_screenplay
    from screenplay.schema import Screenplay

    scene = base_scene()
    sp = Screenplay.model_validate({"memoryId": "m", "cast": ["dad"], "props": [], "shots": [{
        "id": "s1", "set": "yard", "start": {"dad": {"at": "door"}},
        "camera": [{"t": 5, "move": "pull_back", "dur": 1}], "actions": [{"t": t, "do": "rain"}],
    }]})
    res = compile_screenplay(sp, sets=scene.sets, props=scene.props, people=scene.people)
    assert res.ok, res.errors
    (rain,) = res.timeline.effects
    assert rain.t + rain.dur <= res.timeline.duration + 1e-9
