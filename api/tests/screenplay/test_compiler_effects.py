"""compile() and the effect verbs (ARCHITECTURE.md section 3, Effects; shared/CONTRACTS.md
'Timeline': effects are a list ``{t, kind, at, dur, value}``, not tracks).

Every effect verb lands in ``Timeline.effects`` at timeline time (``actionStart + t``), with
``at`` placed by the shot's locate, the default ``at`` / ``dur`` from effects.py and layout.py,
rain with no ``dur`` lasting until its shot's end, the shot's end covering every finite effect,
and a deterministic order across shots."""

from __future__ import annotations

import pytest
from verb_cases import SEDAN_CURB, TABLE, _a, _at, _shot, _sp, base_scene

from screenplay import layout
from screenplay.compiler import compile as compile_screenplay
from screenplay.effects import DEFAULT_AT, EFFECT_VERBS
from screenplay.schema import CompileResult, Screenplay, TimelineEffect
from screenplay.tracks import evaluate

SCENE = base_scene()
MOM_ON_STAGE = {"mom": _at("bench_spot")}
ACTION_START = layout.RISE  # the first shot's t=0

# Stage positions of the yard's marks (x = 2u - 1, acting plane z = 0.5).
CURB = [0.8, 0.0, 0.5]
BENCH_SPOT = [-0.4, 0.0, 0.5]
TABLE_SPOT = [0.2, 0.0, 0.5]


def run(sp: Screenplay) -> CompileResult:
    return compile_screenplay(sp, sets=SCENE.sets, props=SCENE.props, people=SCENE.people)


def one(*actions: dict, **shot_kw) -> CompileResult:
    shot_kw.setdefault("start", MOM_ON_STAGE)
    return run(_sp(_shot(*actions, **shot_kw)))


def effects_of(res: CompileResult) -> list[dict]:
    assert res.ok, [e.message for e in res.errors]
    return [e.model_dump() for e in res.timeline.effects]


def entry(t, kind, at=None, dur=None, value=None) -> dict:
    return TimelineEffect(t=t, kind=kind, at=at, dur=dur, value=value).model_dump()


# --------------------------------------------------------------- one entry per verb

def test_confetti_at_a_mark():
    res = one(_a(1, "confetti", at="curb", dur=2))
    assert effects_of(res) == [entry(ACTION_START + 1, "confetti", CURB, 2.0)]


def test_confetti_with_no_at_or_dur_takes_the_defaults():
    res = one(_a(1, "confetti"))
    assert effects_of(res) == [entry(2.2, "confetti", list(DEFAULT_AT), layout.EFFECT_SECONDS["confetti"])]


def test_steam_at_a_prop():
    res = one(_a(1.5, "steam", at="table"), prop_start=TABLE)
    assert effects_of(res) == [entry(2.7, "steam", TABLE_SPOT, 3.0)]


def test_steam_with_no_at_is_centre_stage():
    res = one(_a(0, "steam", dur=1.25))
    assert effects_of(res) == [entry(1.2, "steam", list(DEFAULT_AT), 1.25)]


def test_smoke_at_a_person():
    res = one(_a(1, "smoke", at="mom", dur=2))
    assert effects_of(res) == [entry(2.2, "smoke", BENCH_SPOT, 2.0)]


def test_smoke_defaults():
    res = one(_a(2, "smoke"))
    assert effects_of(res) == [entry(3.2, "smoke", list(DEFAULT_AT), layout.EFFECT_SECONDS["smoke"])]


def test_smoke_at_a_walking_person_is_placed_where_they_are_by_then():
    # dad walks door -> curb from t=0; the smoke (written first in the list) waits on dad via
    # its `at`, so it reads his finished walk, not where he started.
    res = one(_a(9, "smoke", at="dad", dur=1), _a(0, "walk_to", who="dad", to="curb"),
              start={"dad": _at("door")})
    (fx,) = effects_of(res)
    assert fx["t"] == 10.2
    assert fx["at"] == evaluate(res.timeline.tracks, "puppet:dad", "root.position", 10.2) == CURB


def test_rain_with_a_dur():
    res = one(_a(1, "rain", dur=3))
    assert effects_of(res) == [entry(2.2, "rain", None, 3.0)]


def test_time_of_day_with_its_value():
    res = one(_a(1, "time_of_day", value="dusk", dur=2))
    assert effects_of(res) == [entry(2.2, "time_of_day", None, 2.0, "dusk")]


def test_time_of_day_default_dur():
    res = one(_a(0.5, "time_of_day", value="night"))
    assert effects_of(res) == [entry(1.7, "time_of_day", None, layout.EFFECT_SECONDS["time_of_day"], "night")]


@pytest.mark.parametrize("verb", EFFECT_VERBS)
def test_every_effect_verb_compiles_to_one_entry(verb):
    params = {"value": "dawn"} if verb == "time_of_day" else {}
    res = one(_a(1, verb, **params))
    (fx,) = effects_of(res)
    assert (fx["kind"], fx["t"]) == (verb, 2.2)
    assert res.errors == []


def test_an_effect_at_a_prop_that_is_not_on_stage_is_a_precise_error():
    res = one(_a(1, "confetti", at="cap"))
    assert not res.ok and res.timeline is None
    (err,) = res.errors
    assert (err.code, err.message) == (
        "not_on_stage",
        'shot s1, t=1: cannot confetti; confetti at "cap": cap is not on stage at t=1; '
        "place it with propStart or aim the effect at a mark",
    )
    assert (err.shot, err.t, err.who, err.do) == ("s1", 1.0, None, "confetti")


def test_effects_occupy_nobody():
    # smoke at mom does not keep mom busy: she can wave while it plays
    res = one(_a(1, "smoke", at="mom", dur=3), _a(1.5, "wave", who="mom"))
    assert res.ok, [e.message for e in res.errors]


# ------------------------------------------------------- targets nobody would see

def only_error(res: CompileResult):
    assert not res.ok and res.timeline is None
    (err,) = res.errors
    return err


def test_confetti_at_a_cast_member_not_in_the_start_is_offstage():
    # dad is not in the shot's start, so he waits offstage left; confetti there would be unseen
    err = only_error(one(_a(1, "confetti", at="dad")))
    assert err.code == "offstage"
    assert err.message.startswith("shot s1, t=1: ")
    assert err.message == (
        'shot s1, t=1: cannot confetti; confetti at "dad": dad is offstage left at t=1; '
        "aim it at a mark or give it a t when dad is on stage"
    )
    assert (err.shot, err.t, err.who, err.do) == ("s1", 1.0, None, "confetti")


def test_confetti_at_a_person_after_they_enter_compiles():
    res = one(_a(0, "enter", who="dad", to="curb"), _a(6, "confetti", at="dad"))
    assert effects_of(res) == [entry(ACTION_START + 6, "confetti", CURB, layout.EFFECT_SECONDS["confetti"])]


def test_confetti_at_a_person_still_walking_on_from_the_wings_is_offstage():
    err = only_error(one(_a(0, "enter", who="dad", to="curb"), _a(0.1, "confetti", at="dad")))
    assert (err.code, err.message) == (
        "offstage",
        'shot s1, t=0.1: cannot confetti; confetti at "dad": dad is offstage right at t=0.1; '
        "aim it at a mark or give it a t when dad is on stage",
    )


def test_confetti_at_a_person_who_has_exited_is_offstage():
    err = only_error(one(_a(0, "exit", who="mom"), _a(8, "confetti", at="mom")))
    assert err.code == "offstage" and 'mom is offstage left at t=8' in err.message


def test_smoke_at_a_car_on_stage_compiles():
    res = one(_a(1, "smoke", at="sedan", dur=1.5), prop_start=SEDAN_CURB)
    assert effects_of(res) == [entry(ACTION_START + 1, "smoke", CURB, 1.5)]


def test_smoke_at_a_car_that_has_driven_off_is_offstage():
    res = one(_a(0, "drive_to", who="dad", what="sedan", to="offstage_right"), _a(9, "smoke", at="sedan"),
              start={"dad": _at("curb", **{"in": "sedan.driver"})}, prop_start=SEDAN_CURB)
    err = only_error(res)
    assert (err.code, err.message) == (
        "offstage",
        'shot s1, t=9: cannot smoke; smoke at "sedan": sedan is offstage right at t=9; '
        "aim it at a mark or give it a t when sedan is on stage",
    )


def test_confetti_at_a_prop_left_behind_by_the_previous_set_is_not_on_stage():
    res = run(_sp(
        _shot(_a(0, "steam", at="table"), start=MOM_ON_STAGE, prop_start=TABLE),
        _shot(_a(0.5, "confetti", at="table"), id="s2", set="street", start={"kid": _at("corner")}),
    ))
    err = only_error(res)
    assert (err.code, err.message) == (
        "not_on_stage",
        'shot s2, t=0.5: cannot confetti; confetti at "table": table is not on stage at t=0.5; '
        "place it with propStart or aim the effect at a mark",
    )
    assert (err.shot, err.t) == ("s2", 0.5)


def test_effects_at_marks_past_the_edge_are_offstage():
    sp = _sp(_shot(_a(1, "confetti", at="wings"), _a(2, "smoke", at="offstage_right"), start=MOM_ON_STAGE))
    sp.shots[0].marks = {"wings": [-0.2, 0.0]}  # x = -1.4
    res = run(sp)
    assert not res.ok
    assert [(e.code, e.message) for e in res.errors] == [
        ("offstage", 'shot s1, t=1: cannot confetti; confetti at "wings": wings is a mark offstage left; '
                     "aim it at a mark on stage"),
        ("offstage", 'shot s1, t=2: cannot smoke; smoke at "offstage_right": offstage_right is a mark offstage '
                     "right; aim it at a mark on stage"),
    ]


def test_an_effect_at_a_person_half_a_step_past_the_edge_is_pulled_on_stage():
    sp = _sp(_shot(_a(0, "walk_to", who="mom", to="lip"), _a(8, "confetti", at="mom"), start=MOM_ON_STAGE))
    sp.shots[0].marks = {"lip": [1.03, 0.0]}  # x = 1.06, within the slack
    res = run(sp)
    assert evaluate(res.timeline.tracks, "puppet:mom", "root.position", ACTION_START + 8)[0] == pytest.approx(1.06)
    assert effects_of(res) == [entry(ACTION_START + 8, "confetti", [1.0, 0.0, 0.5], layout.EFFECT_SECONDS["confetti"])]


# ------------------------------------------------------------------ shot end and rain

def test_rain_with_no_dur_lasts_until_its_shots_end():
    res = run(_sp(
        _shot(_a(1, "rain"), _a(0, "walk_to", who="mom", to="curb"), start=MOM_ON_STAGE),
        _shot(_a(0, "wave", who="dad"), id="s2", set="street", start={"dad": _at("garage")}),
    ))
    (fx,) = effects_of(res)
    s1 = res.timeline.shots[0]
    assert fx["kind"] == "rain" and fx["t"] == 2.2
    assert round(fx["t"] + fx["dur"], 3) == s1.end  # the end includes the outgoing fold
    assert s1.end == res.timeline.shots[1].start


def test_rain_with_no_dur_in_the_last_shot_ends_with_the_timeline():
    res = one(_a(0, "walk_to", who="mom", to="curb"), _a(1, "rain"))
    (fx,) = effects_of(res)
    assert round(fx["t"] + fx["dur"], 3) == res.timeline.shots[-1].end == res.timeline.duration


def test_rain_with_no_dur_still_lasts_when_nothing_else_happens():
    # the shot's end covers the rain's start, so it never starts after the shot is over
    res = one(_a(1, "rain"))
    (fx,) = effects_of(res)
    assert fx["dur"] == layout.ACTION_TAIL
    assert round(fx["t"] + fx["dur"], 3) == res.timeline.shots[0].end


def test_an_effect_that_runs_past_the_last_key_extends_its_shot():
    # without the confetti the shot would end ACTION_TAIL after its last key (the set's rise)
    plain = one()
    res = one(_a(1, "confetti", dur=5))
    (fx,) = effects_of(res)
    assert plain.timeline.shots[0].end < fx["t"] + fx["dur"]
    assert res.timeline.shots[0].end == round(fx["t"] + fx["dur"] + layout.ACTION_TAIL, 3)


def test_the_next_shots_fold_waits_for_the_effect():
    res = run(_sp(
        _shot(_a(0, "steam", at="table", dur=6), start=MOM_ON_STAGE, prop_start=TABLE),
        _shot(_a(0, "wave", who="dad"), id="s2", set="street", start={"dad": _at("garage")}),
    ))
    (fx,) = effects_of(res)
    s1, s2 = res.timeline.shots
    burst_end = fx["t"] + fx["dur"]
    content_end = round(burst_end + layout.ACTION_TAIL, 3)
    assert s1.end == round(content_end + layout.fold_out_seconds(3), 3) == s2.start
    # the yard's front layer only starts folding once the steam is over
    folds = [list(k) for tr in res.timeline.tracks if (tr.target, tr.channel) == ("set:yard.layer2", "fold")
             for k in tr.keys]
    assert folds[-2] == [content_end, 1.0] and burst_end < content_end


def test_an_effect_inside_the_action_does_not_change_the_shot_end():
    res = one(_a(0, "walk_to", who="mom", to="curb"), _a(0.5, "confetti", dur=1))
    plain = one(_a(0, "walk_to", who="mom", to="curb"))
    assert res.timeline.shots[0].end == plain.timeline.shots[0].end


# ------------------------------------------------------------- order and determinism

def two_shots() -> Screenplay:
    return _sp(
        _shot(
            _a(2, "confetti", at="curb"),                 # listed first, later in time
            _a(1, "smoke", at="mom", dur=1),
            _a(1, "steam", at="table", dur=1),            # same t as the smoke: list order
            start=MOM_ON_STAGE, prop_start=TABLE,
        ),
        _shot(
            _a(0.5, "time_of_day", value="night"),
            _a(0, "rain"),
            _a(0.5, "confetti", at="kid", dur=1),         # same t as time_of_day: list order
            id="s2", set="street", start={"kid": _at("corner")},
        ),
    )


def test_effects_from_two_shots_are_ordered_by_time_then_shot_then_action():
    res = run(two_shots())
    fxs = effects_of(res)
    s1, s2 = res.timeline.shots
    assert [(f["kind"], f["t"]) for f in fxs] == [
        ("smoke", 2.2), ("steam", 2.2), ("confetti", 3.2),
        ("rain", s2.action_start), ("time_of_day", s2.action_start + 0.5), ("confetti", s2.action_start + 0.5),
    ]
    rain = fxs[3]
    assert round(rain["t"] + rain["dur"], 3) == s2.end
    assert fxs[5]["at"] == [0.6, 0.0, 0.5]  # kid at the street's corner


def test_effect_order_does_not_depend_on_list_order_across_time():
    # The same effects listed in reverse: by t first, then list order only among equal t.
    sp = two_shots()
    for shot in sp.shots:
        shot.actions.reverse()
    fxs = effects_of(run(sp))
    assert [(f["kind"], f["t"]) for f in fxs][:3] == [("steam", 2.2), ("smoke", 2.2), ("confetti", 3.2)]
    assert [f["kind"] for f in fxs][3:] == ["rain", "confetti", "time_of_day"]


def test_the_same_screenplay_gives_byte_identical_json():
    a = run(two_shots())
    b = run(two_shots())
    assert a.ok and b.ok
    assert a.model_dump_json() == b.model_dump_json()
