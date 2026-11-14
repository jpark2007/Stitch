"""Effect verbs -> Timeline.effects entries."""

from __future__ import annotations

import pytest

from screenplay.effects import DEFAULT_AT, EDGE_SLACK, EFFECT_VERBS, STAGE_EDGE, effect_for
from screenplay.layout import EFFECT_SECONDS
from screenplay.schema import VOCABULARY, Action, TimelineEffect
from screenplay.state import StateError

PLACES = {"curb": (0.8, 0.0, 0.5), "mom": (0.2, 0.3, 0.5), "kettle": (-0.25, 0.4, 0.62)}
# Places at or past the stage edge, and below the floor.
EDGES = {
    "dad": (-1.3, 0.0, 0.5),             # waiting offstage left (the start block's offstage root)
    "kid": (1.3, 0.0, 0.5),              # gone offstage right
    "offstage_left": (-1.3, 0.0, 0.5),   # the builtin marks
    "offstage_right": (1.3, 0.0, 0.5),
    "grandpa": (-1.06, 0.0, 0.5),        # half a step past the left edge
    "aunt": (1.1, 0.2, 0.4),             # exactly at the slack
    "cat": (0.3, -0.25, 0.7),            # a prop sunk below the floor
}
NOT_ON_STAGE = {"cake"}
SHOT_END = 12.0


def locate(name: str, t: float):
    if name in PLACES:
        return PLACES[name]
    if name in EDGES:
        return EDGES[name]
    if name in NOT_ON_STAGE:
        raise StateError("not_on_stage", f"{name} is not on stage")
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
    error("unknown_target", 'confetti at "moon": moon is not a person, prop or mark; aim it at a mark, person or prop',
          act(do="confetti", at="moon"))


# ------------------------------------------------------- targets that cannot be seen

def test_the_stage_edge_matches_the_verbs():
    from screenplay import verbs_core
    assert STAGE_EDGE == verbs_core.STAGE_EDGE == 1.0
    assert EDGE_SLACK == 0.1


@pytest.mark.parametrize("verb", ["confetti", "steam", "smoke"])
def test_a_person_offstage_is_an_offstage_error_naming_the_effect_target_side_and_fix(verb):
    error("offstage", f'{verb} at "dad": dad is offstage left at t=4; aim it at a mark or give it a t when dad is on stage',
          act(do=verb, at="dad", t=4), t=5.2)
    error("offstage", f'{verb} at "kid": kid is offstage right at t=0.5; aim it at a mark or give it a t when kid is on stage',
          act(do=verb, at="kid", t=0.5), t=1.7)


def test_the_reason_uses_the_written_t_not_the_timeline_t():
    # the message head carries the screenplay's t, so the reason does too (fmt_t: two decimals)
    error("offstage", 'smoke at "dad": dad is offstage left at t=2.35; aim it at a mark or give it a t when dad is on stage',
          act(do="smoke", at="dad", t=2.345), t=3.545)


@pytest.mark.parametrize("mark, side", [("offstage_left", "left"), ("offstage_right", "right")])
def test_the_builtin_offstage_marks_are_offstage(mark, side):
    error("offstage", f'confetti at "{mark}": {mark} is a mark offstage {side}; aim it at a mark on stage',
          act(do="confetti", at=mark))


def test_an_offstage_target_gives_no_entry_whatever_the_dur():
    for kw in ({}, {"dur": 1.0}):
        with pytest.raises(StateError) as e:
            effect_for(act(do="steam", at="dad", **kw), 5.0, locate, SHOT_END)
        assert e.value.code == "offstage"


def test_a_target_just_past_the_edge_is_pulled_on_stage():
    got = effect_for(act(do="confetti", at="grandpa"), 5.0, locate, SHOT_END)
    assert got.at == [-1.0, 0.0, 0.5]
    got = effect_for(act(do="smoke", at="aunt", dur=1), 5.0, locate, SHOT_END)
    assert got.at == [1.0, 0.2, 0.4]  # |x| = STAGE_EDGE + EDGE_SLACK is still pulled in; z kept


def test_targets_on_stage_are_not_moved():
    for name, place in PLACES.items():
        got = effect_for(act(do="confetti", at=name), 5.0, locate, SHOT_END)
        assert got.at == list(place)
        assert abs(got.at[0]) <= STAGE_EDGE


def test_an_effect_never_sits_below_the_floor():
    got = effect_for(act(do="steam", at="cat"), 5.0, locate, SHOT_END)
    assert got.at == [0.3, 0.0, 0.7]


@pytest.mark.parametrize("verb", ["confetti", "steam", "smoke"])
def test_a_prop_not_on_stage_names_the_effect_and_the_fix(verb):
    error("not_on_stage",
          f'{verb} at "cake": cake is not on stage at t=2; place it with propStart or aim the effect at a mark',
          act(do=verb, at="cake", t=2), t=3.2)


def test_other_locate_errors_pass_through():
    def broken(name, t):
        raise StateError("cycle", "cake rests on itself")

    with pytest.raises(StateError) as e:
        effect_for(act(do="confetti", at="cake"), 5.0, broken, SHOT_END)
    assert (e.value.code, e.value.reason) == ("cycle", "cake rests on itself")


@pytest.mark.parametrize("target", ["curb", "dad", "cake", "moon", "offstage_left"])
def test_whole_stage_effects_still_refuse_an_at_before_placing_it(target):
    error("bad_param", "rain fills the whole stage and takes no at", act(do="rain", at=target))
    error("bad_param", "time_of_day fills the whole stage and takes no at",
          act(do="time_of_day", value="dusk", at=target))


def test_params_the_effect_does_not_take():
    error("bad_param", "rain fills the whole stage and takes no at", act(do="rain", at="curb"))
    error("bad_param", "time_of_day fills the whole stage and takes no at", act(do="time_of_day", value="day", at="curb"))
    error("bad_param", "confetti takes no value", act(do="confetti", value="day"))


def test_not_an_effect():
    error("not_an_effect", "wave is not an effect; effects are confetti, steam, rain, smoke, time_of_day",
          act(do="wave", who="dad"))
