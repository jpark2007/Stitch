"""Effect verbs -> Timeline.effects entries."""

from __future__ import annotations

import pytest

from screenplay.effects import DEFAULT_AT, EFFECT_VERBS, effect_for
from screenplay.layout import EFFECT_SECONDS
from screenplay.schema import VOCABULARY, Action, TimelineEffect
from screenplay.state import StateError

PLACES = {"curb": (0.8, 0.0, 0.5), "mom": (0.2, 0.3, 0.5), "kettle": (-0.25, 0.4, 0.62)}
SHOT_END = 12.0


def locate(name: str, t: float):
    if name in PLACES:
        return PLACES[name]
    raise StateError("unknown_target", f"{name} is not a person, prop or mark")


def act(**kw) -> Action:
    return Action.model_validate({"t": 0, **kw})


def error(code: str, reason: str, action: Action, t: float = 5.0, shot_end: float = SHOT_END) -> None:
    with pytest.raises(StateError) as e:
        effect_for(action, t, locate, shot_end)
    assert (e.value.code, e.value.reason) == (code, reason)


def test_effect_verbs_are_the_vocabulary_effects():
    assert EFFECT_VERBS == VOCABULARY["effects"] == ["confetti", "steam", "rain", "smoke", "time_of_day"]
    for verb in EFFECT_VERBS:
        assert VOCABULARY["actions"][verb]["kind"] == "effect"


@pytest.mark.parametrize("verb, seconds", [("confetti", 2.0), ("steam", 3.0), ("smoke", 3.0)])
def test_point_effects_default_duration_and_place(verb, seconds):
    assert EFFECT_SECONDS[verb] == seconds
    got = effect_for(act(do=verb), 5.25, locate, SHOT_END)
    assert got == TimelineEffect(t=5.25, kind=verb, at=list(DEFAULT_AT), dur=seconds, value=None)
    assert DEFAULT_AT == (0.0, 0.5, 0.5)


@pytest.mark.parametrize("verb", ["confetti", "steam", "smoke"])
def test_point_effects_at_a_mark_person_or_prop(verb):
    seen = []

    def spy(name, t):
        seen.append((name, t))
        return locate(name, t)

    for name in ("curb", "mom", "kettle"):
        got = effect_for(act(do=verb, at=name, dur=1.5), 6.0, spy, SHOT_END)
        assert got.at == list(PLACES[name]) and got.dur == 1.5
    assert seen == [("curb", 6.0), ("mom", 6.0), ("kettle", 6.0)]


def test_rain_lasts_to_the_end_of_the_shot():
    got = effect_for(act(do="rain"), 4.2, locate, SHOT_END)
    assert got == TimelineEffect(t=4.2, kind="rain", at=None, dur=7.8, value=None)
    assert effect_for(act(do="rain", dur=2), 4.2, locate, SHOT_END).dur == 2.0
    assert effect_for(act(do="rain"), SHOT_END, locate, SHOT_END).dur == 0.0


def test_time_of_day():
    got = effect_for(act(do="time_of_day", value="dusk"), 3.0, locate, SHOT_END)
    assert got == TimelineEffect(t=3.0, kind="time_of_day", at=None, dur=1.0, value="dusk")
    for value in VOCABULARY["timesOfDay"]:
        assert effect_for(act(do="time_of_day", value=value, dur=0.5), 3.0, locate, SHOT_END).value == value


def test_values_are_rounded_like_the_timeline():
    got = effect_for(act(do="steam", at="kettle", dur=1.23456), 1.0000004, locate, SHOT_END)
    assert (got.t, got.dur) == (1.0, 1.235)


@pytest.mark.parametrize("value", ["noon", None, "Dusk"])
def test_bad_time_of_day_value(value):
    error("invalid_value", "time_of_day value must be one of dawn, day, dusk, night", act(do="time_of_day", value=value))


@pytest.mark.parametrize("verb", EFFECT_VERBS)
def test_negative_dur(verb):
    extra = {"value": "night"} if verb == "time_of_day" else {}
    error("bad_time", f"{verb} dur must not be negative, not -1", act(do=verb, dur=-1, **extra))


def test_rain_starting_after_the_shot_ends():
    error("bad_time", "rain starts 0.50 s after the shot ends; give it a dur", act(do="rain"), t=12.5)


def test_unknown_place():
    error("unknown_target", "moon is not a person, prop or mark", act(do="confetti", at="moon"))


def test_params_the_effect_does_not_take():
    error("bad_param", "rain fills the whole stage and takes no at", act(do="rain", at="curb"))
    error("bad_param", "time_of_day fills the whole stage and takes no at", act(do="time_of_day", value="day", at="curb"))
    error("bad_param", "confetti takes no value", act(do="confetti", value="day"))


def test_not_an_effect():
    error("not_an_effect", "wave is not an effect; effects are confetti, steam, rain, smoke, time_of_day",
          act(do="wave", who="dad"))
