"""CompileError construction: every message form, and the conversions from each error type."""

import math

import schemas
from screenplay.compiler import compile as compile_screenplay
from screenplay.errors import (
    ERROR_CODES,
    HINT_VARIANTS,
    PENDING_SKILL_HINTS,
    NotedOutOfReach,
    error,
    error_message,
    error_t,
    from_narration_error,
    from_out_of_reach,
    from_state_error,
    hint_for,
    out_of_reach_reason,
)
from screenplay.ik import OutOfReach
from screenplay.narration import NarrationError
from screenplay.schema import Action, CompileError, CompileResult, Screenplay
from screenplay.state import StateError
from screenplay.validate import validate
from verb_cases import CAST, PROPS, base_scene


def test_full_form_is_exact():
    msg = error_message("s1", 5, "dad", "hand_to", "hymn_books", "mom is holding it")
    assert msg == 'shot s1, t=5: dad cannot hand_to "hymn_books"; mom is holding it'


def test_time_is_formatted_like_state_fmt_t():
    assert error_message("s1", 6.6, "dad", "walk_to", None, "x").startswith("shot s1, t=6.6: ")
    assert error_message("s1", 4.7449, "dad", "walk_to", None, "x").startswith("shot s1, t=4.74: ")
    assert error_message("s1", 0, "dad", "walk_to", None, "x").startswith("shot s1, t=0: ")


def test_without_what():
    assert error_message("s2", 1.5, "mom", "walk_to", None, "porch is not a mark in shot s2") == (
        "shot s2, t=1.5: mom cannot walk_to; porch is not a mark in shot s2"
    )


def test_do_without_who():
    assert error_message("s1", 2, None, "set_state", "lamp", "lamp has no state lit") == (
        'shot s1, t=2: cannot set_state "lamp"; lamp has no state lit'
    )
    assert error_message("s1", 2, None, "rain", None, "it needs nothing") == "shot s1, t=2: cannot rain; it needs nothing"


def test_reason_only_forms():
    assert error_message("s3", None, None, None, None, "steps is not a mark in shot s3") == (
        "shot s3: steps is not a mark in shot s3"
    )
    assert error_message("s3", 4, None, None, None, "too late") == "shot s3, t=4: too late"
    assert error_message(None, None, None, None, None, "kitchen is not a known set") == "screenplay: kitchen is not a known set"
    # without a shot, t is meaningless and is left out
    assert error_message(None, 3, "dad", "walk_to", None, "r") == "screenplay: dad cannot walk_to; r"


def test_who_without_do_is_reason_only():
    assert error_message("s1", 1, "dad", None, None, "r") == "shot s1, t=1: r"


def _action(**kw) -> Action:
    return Action.model_validate(kw)


def test_from_state_error_uses_the_written_t_and_fills_fields():
    a = _action(t=6.6, who="dad", do="hand_to", what="hymn_books", to="mom")
    err = from_state_error(StateError("not_holding", "mom is holding it"), "s1", a)
    assert err.code == "not_holding"
    assert err.message == 'shot s1, t=6.6: dad cannot hand_to "hymn_books"; mom is holding it'
    assert (err.shot, err.t, err.who, err.do) == ("s1", 6.6, "dad", "hand_to")


def test_from_state_error_without_action():
    err = from_state_error(StateError("unknown_mark", "nowhere is not a mark in this shot; the marks are a, b"), "s1", None)
    assert err.message == "shot s1: nowhere is not a mark in this shot; the marks are a, b"
    assert (err.code, err.shot, err.t, err.who, err.do) == ("unknown_mark", "s1", None, None, None)


def test_from_out_of_reach_names_the_puppet_and_two_decimals():
    a = _action(t=5.5, who="dad", do="pick_up", what="hymn_books")
    err = from_out_of_reach(OutOfReach("arm_r", 0.6234, 0.374, who="dad"), "s1", a)
    assert err.code == "out_of_reach"
    assert err.message == (
        'shot s1, t=5.5: dad cannot pick_up "hymn_books"; '
        "dad's arm_r cannot reach: target is 0.62 from the shoulder, reach is 0.37"
    )
    assert (err.t, err.who, err.do) == (5.5, "dad", "pick_up")


def test_out_of_reach_names_the_other_puppet_when_it_was_their_arm():
    # hand_to: the taker's arm can be the one that fails
    a = _action(t=1, who="dad", do="hand_to", what="cup", to="mom")
    err = from_out_of_reach(OutOfReach("arm_r", 1.0, 0.34, who="mom"), "s1", a)
    assert "; mom's arm_r cannot reach: target is 1.00" in err.message


def test_out_of_reach_falls_back_to_the_actor():
    assert out_of_reach_reason(OutOfReach("arm_l", 0.5, 0.25), "kid") == (
        "kid's arm_l cannot reach: target is 0.50 from the shoulder, reach is 0.25"
    )
    assert out_of_reach_reason(OutOfReach("arm_l", 0.5, 0.25)).startswith("arm_l cannot reach")


def test_from_narration_error():
    e = NarrationError("cue_word_missing", 'cueWord "fell" is not a word', author="dad", shot="s1", line_index=1)
    err = from_narration_error(e)
    assert (err.code, err.shot, err.t, err.who) == ("cue_word_missing", "s1", None, None)
    assert err.message == "shot s1, dad's narration line 1: " + 'cueWord "fell" is not a word'
    top = from_narration_error(NarrationError("no_narrator", "nobody narrates"))
    assert top.shot is None and top.message == "screenplay: nobody narrates"


def test_error_helper():
    assert error("unknown_set", "kitchen is not a known set").model_dump() == {
        "code": "unknown_set", "message": "screenplay: kitchen is not a known set",
        "shot": None, "t": None, "who": None, "do": None,
    }
    assert error("bad_start", "r", "s2").message == "shot s2: r"
    a = _action(t=3, who="mom", do="drop", what="cup")
    err = error("unsupported_verb", "the compiler does not support drop yet", "s1", a)
    assert err.message == 'shot s1, t=3: mom cannot drop "cup"; the compiler does not support drop yet'
    assert (err.t, err.who, err.do) == (3.0, "mom", "drop")


def test_error_t_is_the_written_time_or_none_when_not_finite():
    assert error_t(3) == 3.0 and isinstance(error_t(3), float)
    assert error_t(0.0) == 0.0
    assert error_t(-2.5) == -2.5  # negative but finite: kept, the message says why it is wrong
    assert error_t(1e12) == 1e12
    for bad in (None, math.inf, -math.inf, math.nan):
        assert error_t(bad) is None


def test_an_error_on_a_non_finite_t_has_no_t_but_says_it_in_the_message():
    for t, shown in ((math.inf, "inf"), (-math.inf, "-inf"), (math.nan, "nan")):
        err = error("bad_time", f"t must be a finite number >= 0, not {shown}", "s1",
                    _action(t=t, who="dad", do="wave"))
        assert err.model_dump() == {
            "code": "bad_time",
            "message": f"shot s1, t={shown}: dad cannot wave; t must be a finite number >= 0, not {shown}",
            "shot": "s1", "t": None, "who": "dad", "do": "wave",
        }
        assert CompileError.model_validate_json(err.model_dump_json()) == err


def _non_finite_result(t: float) -> CompileResult:
    """One shot whose camera cue, action and narration line are all written at ``t``."""
    scene = base_scene()
    sp = Screenplay.model_validate({
        "memoryId": "non_finite", "cast": list(CAST), "props": list(PROPS), "shots": [{
            "id": "s1", "set": "yard", "start": {"dad": {"at": "door"}},
            "camera": [{"t": t, "move": "wide"}],
            "actions": [{"t": t, "do": "wave", "who": "dad"}],
            "narration": [{"t": t, "text": "we waved"}],
        }],
    })
    return compile_screenplay(sp, sets=scene.sets, props=scene.props, people=scene.people)


def test_a_compile_result_with_a_non_finite_t_round_trips_through_json_byte_identically():
    for t, shown in ((math.inf, "inf"), (-math.inf, "-inf"), (math.nan, "nan")):
        result = _non_finite_result(t)
        assert [(e.code, e.message, e.t) for e in result.errors] == [
            ("bad_time", f"shot s1, t={shown}: cannot wide; t must be a finite number >= 0, not {shown}", None),
            ("bad_time", f"shot s1, t={shown}: dad cannot wave; t must be a finite number >= 0, not {shown}", None),
            ("bad_time", f"shot s1, dad's narration line 0: t must be a finite number >= 0, not {shown}", None),
        ]
        dumped = result.model_dump_json()
        again = CompileResult.model_validate_json(dumped)
        assert again == result
        assert again.model_dump_json() == dumped
        assert "Infinity" not in dumped and "NaN" not in dumped


def test_who_and_do_count_when_not_none_and_what_when_not_empty():
    # validate.py builds every message here; these are the edge cases its old formatter defined
    assert error_message("s1", 1, "", "wave", None, "r") == "shot s1, t=1:  cannot wave; r"
    assert error_message("s1", 1, None, "", None, "r") == "shot s1, t=1: cannot ; r"
    assert error_message("s1", 1, "dad", "drop", "", "r") == "shot s1, t=1: dad cannot drop; r"


# ------------------------------------------------------------------------------ hints

def _state(code: str, reason: str, **action) -> CompileError:
    return from_state_error(StateError(code, reason), "s1", _action(**action) if action else None)


def test_hint_offstage_names_the_person_who_is_offstage():
    # hand_to to an offstage taker: the fix is mom's enter, not dad's
    err = _state("offstage", "mom is offstage", t=6.6, who="dad", do="hand_to", what="hymn_books", to="mom")
    assert hint_for(err) == "add an enter for mom before this hand_to"
    err = _state("offstage", "dad is offstage; use enter first", t=1, who="dad", do="walk_to", to="steps_foot")
    assert hint_for(err) == "add an enter for dad before this walk_to"


def test_hint_not_on_stage():
    err = _state("not_on_stage", "hymn_books is not on stage; place it with propStart", t=5.5, who="dad", do="pick_up", what="hymn_books")
    assert hint_for(err) == "place hymn_books with propStart in shot s1, or have someone carry it in"


def test_hint_unknown_mark_from_validate_and_from_state():
    err = CompileError(
        code="unknown_mark",
        message='shot s2, t=1: dad cannot walk_to; "the_moon" is not a mark in this shot; the marks are a, b',
        shot="s2", t=1.0, who="dad", do="walk_to",
    )
    assert hint_for(err) == "use a mark the message lists, or add it to shot s2's marks as [u, h]"
    assert hint_for(error("unknown_mark", "nowhere is not a mark in this shot; the marks are a, b", "s3")) == (
        "use a mark the message lists, or add it to shot s3's marks as [u, h]"
    )


def test_hint_not_holding_names_the_prop_from_the_message_head():
    err = _state("not_holding", "it is on the ground", t=6.6, who="dad", do="hand_to", what="hymn_books", to="mom")
    assert hint_for(err) == "have dad pick_up hymn_books before this hand_to"


def test_hint_not_holding_when_someone_else_holds_it():
    # picking it up would only fail with already_held: the holder has to hand it over
    err = _state("not_holding", "mom is holding it", t=6.6, who="dad", do="drop", what="hymn_books")
    assert hint_for(err) == "have mom hand_to hymn_books to dad before this drop"


def test_hint_not_holding_by_where_the_prop_is():
    err = _state("not_holding", "it is on the ground", t=6.6, who="dad", do="drop", what="hymn_books")
    assert hint_for(err) == "have dad pick_up hymn_books before this drop"
    err = _state("not_holding", "it is on table.top", t=6.6, who="dad", do="drop", what="cup")
    assert hint_for(err) == "have dad pick_up cup before this drop"
    err = _state("not_holding", "it is not on stage", t=2, who="dad", do="drop", what="cup")
    assert hint_for(err) == "place cup with propStart in shot s1, then have dad pick_up cup"
    # worn on a head: no verb takes it off, so there is no obvious fix
    assert hint_for(_state("not_holding", "mom is wearing it", t=2, who="dad", do="drop", what="hat")) is None


def test_hint_prop_conflict_names_the_prop_and_the_holder():
    err = error(
        "prop_conflict",
        "dad starts holding hymn_books, and propStart also places it; "
        "remove hymn_books from propStart or from dad's holding",
        "s1",
    )
    assert hint_for(err) == "remove hymn_books from propStart or from dad's start holding"


def _effect(code: str, reason: str, do: str = "confetti", at: str = "dad") -> CompileError:
    return from_state_error(StateError(code, reason), "s1", _action(t=0.5, do=do, at=at))


def test_hint_effect_aimed_at_someone_offstage_reaims_the_effect():
    # effects.py's reasons start '<effect> at "<name>": '; the effect verb is not the subject
    err = _effect("offstage", 'confetti at "dad": dad is offstage left at t=0.5; '
                              "aim it at a mark or give it a t when dad is on stage")
    assert err.who is None
    assert hint_for(err) == "aim the confetti at a mark on stage, or give it a t when dad is on stage"


def test_hint_effect_aimed_at_a_mark_offstage_has_no_enter():
    err = _effect("offstage", 'confetti at "offstage_left": offstage_left is a mark offstage left; '
                              "aim it at a mark on stage", at="offstage_left")
    assert hint_for(err) == "aim the confetti at a mark on stage"


def test_hint_effect_aimed_at_a_prop_not_on_stage():
    err = _effect("not_on_stage", 'smoke at "cup": cup is not on stage at t=2; '
                                  "place it with propStart or aim the effect at a mark", do="smoke", at="cup")
    assert hint_for(err) == "place cup with propStart in shot s1, or aim the smoke at a mark"


def test_hint_effect_aimed_at_an_unknown_target():
    err = _effect("unknown_target", 'steam at "gran": gran is not a person, prop or mark in this shot; '
                                    "aim it at a mark, person or prop", do="steam", at="gran")
    assert hint_for(err) == "aim the steam at a person, prop or mark the message lists"


def test_hint_variants_are_registered_codes():
    # a code with no general hint (bad_target) may still have variants for the cases that do
    for code, variants in HINT_VARIANTS.items():
        assert code in ERROR_CODES, code
        for _, hint in variants:
            assert hint is None or (hint[0].islower() and not hint.endswith(".")), (code, hint)
    assert ERROR_CODES["bad_target"].hint is None
    assert hint_for(_state("bad_target", "dad can only follow a person, and lamp is not one",
                           t=1, who="dad", do="follow", target="lamp")) is None


def test_hint_hands_full_names_whose_hands():
    err = _state("hands_full", "mom's hands are full (hand_r: cup, hand_l: lamp)",
                 t=2, who="dad", do="hand_to", what="hymn_books", to="mom")
    assert hint_for(err) == "have mom drop or place_on a prop first"
    err = _state("hands_full", "dad's hand_r is holding cup", t=2, who="dad", do="pick_up", what="lamp")
    assert hint_for(err) == "have dad drop or place_on a prop first"


def test_hint_seated():
    err = _state("seated", "dad is seated in pew.seat; use stand_up first", t=4, who="dad", do="walk_to", to="door")
    assert hint_for(err) == "add a stand_up (or get_out, in a vehicle) for dad before this walk_to"
    # the reason names its fix (state.World.seat_reason), so a vehicle seat gets get_out alone
    err = _state("seated", "mom is seated in sedan.driver; use get_out first", t=4, who="dad", do="hug", target="mom")
    assert hint_for(err) == "add a get_out for mom before this hug"
    err = _state("already_seated", "dad is seated in sedan.driver; use get_out first",
                 t=4, who="dad", do="sit_on", what="pew")
    assert hint_for(err) == "add a get_out for dad first"
    err = _state("already_seated", "dad is seated in pew.seat; use stand_up first", t=4, who="dad", do="sit_on", what="pew")
    assert hint_for(err) == "add a stand_up (or get_out, in a vehicle) for dad first"


def test_hint_busy_gives_the_time_the_actor_is_free():
    err = _state("busy", "dad is still walking until t=5.35", t=4, who="dad", do="pick_up", what="hymn_books")
    assert hint_for(err) == "start this pick_up at t=5.35 or later"
    # the compiler's clash form
    err = _state("busy", "dad would still be walking until t=6, but starts sitting at t=4",
                 t=4, who="dad", do="sit_on", what="pew")
    assert hint_for(err) == "start this sit_on at t=6 or later"


def test_hint_missing_param_quotes_the_parameter():
    err = CompileError(
        code="missing_param",
        message='shot s1, t=1: dad cannot walk_to; walk_to requires who, to; "to" is missing',
        shot="s1", t=1.0, who="dad", do="walk_to",
    )
    assert hint_for(err) == 'add "to" to this walk_to'
    assert hint_for(_state("missing_param", "set_state needs state", t=1, do="set_state", what="lamp")) == (
        'add "state" to this set_state'
    )
    assert hint_for(_state("missing_param", "dad cannot turn_to: no target given", t=1, who="dad", do="turn_to")) == (
        'add "target" to this turn_to'
    )
    # the verb modules and effects now use validate's wording too (state.missing_param_reason)
    err = _state("missing_param", 'turn_to requires who, target; "target" is missing', t=1, who="dad", do="turn_to")
    assert hint_for(err) == 'add "target" to this turn_to'
    err = _state("missing_param", 'time_of_day requires value; "value" is missing', t=1, do="time_of_day")
    assert hint_for(err) == 'add "value" to this time_of_day'
    # screenplay level: no action, so the generic words fill in
    top = error("missing_param", 'interaction "f" drives prop_state, which requires prop; "prop" is missing')
    assert hint_for(top) == 'add "prop" to this action'


def test_hint_unknown_pose_lists_every_pose():
    err = _state("unknown_pose", "moonwalk is not a pose", t=1, who="dad", do="pose", pose="moonwalk")
    hint = hint_for(err)
    assert hint is not None and hint.startswith("use one of the poses: stand, hold, sit, kneel, ")
    assert "bow_head" in hint


def test_hint_out_of_reach():
    a = _action(t=5.5, who="dad", do="pick_up", what="hymn_books")
    err = from_out_of_reach(OutOfReach("arm_r", 0.62, 0.37, who="dad"), "s1", a)
    assert hint_for(err) == "walk dad closer first, or move the target nearer"


def test_a_noted_out_of_reach_appends_its_note_and_stays_an_out_of_reach():
    base = OutOfReach("arm_r", 0.234, 0.17)
    e = NotedOutOfReach(base, "mom is seated in chair.seat; use stand_up first", who="mom")
    assert isinstance(e, OutOfReach) and (e.arm, e.distance, e.reach, e.who) == ("arm_r", 0.234, 0.17, "mom")
    assert str(e) == str(base.with_who("mom"))
    assert out_of_reach_reason(e) == (
        "mom's arm_r cannot reach: target is 0.23 from the shoulder, reach is 0.17; "
        "mom is seated in chair.seat; use stand_up first"
    )


def test_hint_out_of_reach_from_a_seat():
    a = _action(t=1, who="mom", do="pick_up", what="cap")
    seated = NotedOutOfReach(OutOfReach("arm_r", 0.23, 0.17, "mom"), "mom is seated in chair.seat; use stand_up first")
    err = from_out_of_reach(seated, "s1", a)
    assert err.code == "out_of_reach"
    assert err.message.endswith("; mom is seated in chair.seat; use stand_up first")
    # not "walk mom closer first": a seated puppet cannot walk. The reason names the fix; the
    # hint waits for the directing skill's row (errors.PENDING_SKILL_HINTS)
    assert hint_for(err) is None
    assert hint_for(err, pending=True) == "add a stand_up for mom before this pick_up"
    in_car = NotedOutOfReach(OutOfReach("arm_r", 0.4, 0.17, "mom"), "mom is seated in sedan.driver; use get_out first")
    err = from_out_of_reach(in_car, "s1", _action(t=1, who="mom", do="open", what="suitcase"))
    assert hint_for(err) is None
    assert hint_for(err, pending=True) == "add a get_out for mom before this open"
    # hand_to: the seated taker's arm missed, so the stand_up is the taker's
    a = _action(t=1, who="dad", do="hand_to", what="cup", to="gran")
    taker = NotedOutOfReach(OutOfReach("arm_r", 0.5, 0.17, "gran"), "gran is seated in chair.seat; use stand_up first")
    assert hint_for(from_out_of_reach(taker, "s1", a), pending=True) == "add a stand_up for gran before this hand_to"
    # standing, the general hint still applies
    standing = from_out_of_reach(OutOfReach("arm_r", 0.5, 0.17, "mom"), "s1", _action(t=1, who="mom", do="pick_up",
                                                                                        what="cap"))
    assert hint_for(standing) == hint_for(standing, pending=True) == "walk mom closer first, or move the target nearer"


def test_hint_not_driver():
    err = _state("not_driver", "mom is in sedan.passenger, not sedan.driver", t=1, who="mom", do="drive_to",
                 what="sedan", to="corner")
    # PENDING_SKILL_HINTS: the general get_in hint would fail with already_seated, so none yet
    assert hint_for(err) is None
    assert hint_for(err, pending=True) == (
        "add a get_out for mom, then get_in sedan with socket driver, before this drive_to"
    )
    err = _state("not_driver", "kid is not in sedan.driver; use get_in with socket driver first", t=1, who="kid",
                 do="drive_to", what="sedan", to="corner")
    assert hint_for(err) == "add a get_in sedan with socket driver for kid first"


def test_hint_offstage_props():
    # PENDING_SKILL_HINTS: a vehicle's fix (drive_to) is in its reason; no propStart hint for it
    err = _state("not_on_stage", "sedan is offstage at x=1.3; drive_to a mark on stage first",
                 t=9, who="mom", do="get_in", what="sedan")
    assert hint_for(err) is None
    assert hint_for(err, pending=True) == "add a drive_to of sedan to a mark on stage before this get_in"
    err = _state("not_on_stage", "suitcase is on pickup, offstage at x=1.4; drive_to a mark on stage first",
                 t=9, who="mom", do="pick_up", what="suitcase")
    assert hint_for(err) is None
    assert hint_for(err, pending=True) == "add a drive_to of pickup to a mark on stage before this pick_up"
    err = _state("not_on_stage", "hymn_books is on table, offstage at x=1.3; place table on stage with propStart",
                 t=9, who="mom", do="pick_up", what="hymn_books")
    assert hint_for(err) == "place table on stage with propStart in shot s1"
    err = _state("not_on_stage", "chair is offstage at x=1.3; place it on stage with propStart",
                 t=1, who="dad", do="sit_on", what="chair")
    assert hint_for(err) == "place chair on stage with propStart in shot s1"


def test_hint_mark_and_time_ranges():
    assert hint_for(error("bad_mark", 'mark "pit" has h -0.3, but h is the standing surface\'s height above the '
                                      "ground, from 0 to 1.5", "s1")) == "give mark pit an h from 0 to 1.5"
    assert hint_for(error("bad_mark", 'mark "front" has d 4, but d is a depth from 0 (back of the set) to 1',
                          "s1")) == "give mark front a d from 0 to 1"
    assert hint_for(error("bad_mark", 'mark "spot" is [0.5], but a mark is [u, h] or [u, h, d] (numbers)', "s1")) == (
        "write the mark as [u, h] or [u, h, d] (numbers)"
    )
    err = _state("bad_time", "t must be at most 600 s, not 1e+07", t=1e7, who="dad", do="wave")
    assert hint_for(err) == "use a t of at most 600 s"
    err = _state("bad_dur", "dur must be at most 600 s, not 1e+09", t=1, who="dad", do="wave", dur=1e9)
    assert hint_for(err) == "give dur at most 600 seconds"
    err = _state("bad_time", "t must be a finite number >= 0, not -1", t=-1, who="dad", do="wave")
    assert hint_for(err) == "use a finite t >= 0 and a dur > 0, in order"


def _unknown_in_scene(**overrides) -> CompileError:
    """The screenplay-level error validate reports for a cast member or prop the scene lacks."""
    people = {pid: schemas.Person(id=pid, family_id="f", name=pid, character={"color": "#123456"})
              for pid in ("dad", "mom")}
    props = {"lamp": schemas.Prop(id="lamp", name="lamp", size="hand", art={"body": None})}
    data = {"memoryId": "m", "cast": ["dad"], "props": ["lamp"], "shots": [{"id": "s1", "set": "yard"}], **overrides}
    sets = {"yard": schemas.SetDef(id="yard", name="Yard", layers=[{"art": None, "depth": 0, "height": 1.0}])}
    errors = validate(Screenplay.model_validate(data), sets, props, people)
    assert len(errors) == 1, errors
    return errors[0]


def test_hint_unknown_prop_and_person_from_validates_reasons():
    err = _unknown_in_scene(props=["lamp", "kite"])
    assert (err.code, err.message) == ("unknown_prop", 'screenplay: prop "kite" is not a known prop; known props: lamp')
    assert hint_for(err) == "remove kite from the screenplay's props, or use a prop the message lists"
    err = _unknown_in_scene(cast=["dad", "gran"])
    assert (err.code, err.message) == (
        "unknown_person", 'screenplay: cast member "gran" is not a known person; known people: dad, mom')
    assert hint_for(err) == "remove gran from the cast, or use a person the message lists"
    # an action naming someone outside the cast keeps the general hint
    err = _state("unknown_person", '"gran" is not in the cast; the cast is dad', t=1, who="gran", do="wave")
    assert hint_for(err) == "add the person to the cast, or use a name the message lists"


def test_hint_from_a_narration_error():
    e = NarrationError("cue_unknown_action", 'cue "hug" matches no action', author="dad", shot="s2", line_index=0)
    assert hint_for(from_narration_error(e)) == "set cue to an action id or verb in shot s2"


def test_hint_is_none_without_an_obvious_fix_or_for_an_unknown_code():
    assert hint_for(_state("follow_order", "x", t=1, who="dad", do="follow", target="mom")) is None
    assert hint_for(CompileError(code="no_such_code", message="screenplay: r")) is None


def test_every_hint_formats_with_and_without_fields():
    full = _state("x", "mom is still walking until t=2", t=1, who="dad", do="hand_to", what="cup", to="mom")
    bare = CompileError(code="x", message="screenplay: r")
    for code, entry in ERROR_CODES.items():
        if entry.hint is None:
            continue
        for err in (full, bare):
            hint = hint_for(err.model_copy(update={"code": code}))
            assert hint and "{" not in hint and "}" not in hint, (code, hint)
    # every variant, reached by a bare screenplay-level error whose reason matches it
    samples = {
        "offstage": ['confetti at "x": x is a mark offstage left', 'confetti at "x": x is offstage left'],
        "not_on_stage": ['smoke at "x": x is not on stage'],
        "unknown_target": ['smoke at "x": x is not a person, prop or mark'],
        "not_holding": ["mom is holding it", "it is not on stage"],
        "prop_conflict": ["dad starts holding cup, and propStart also places it"],
        "seated": ["mom is seated in sedan.driver; use get_out first"],
        "already_seated": ["mom is seated in sedan.driver; use get_out first"],
        "bad_exit": ['"from" must be offstage_left or offstage_right, not "curb"'],
        "bad_target": ['"target" must be a person in the cast, and "lamp" is a prop',
                       '"on" must be a prop or a mark, and "mom" is a person',
                       "mom is a person; place_on needs a prop or a mark"],
        "self_target": ['"on" is cup itself'],
        "unknown_state": ['"lamp" has no state "open"', '"lamp" has no state "closed"'],
        "unknown_prop": ['prop "kite" is not a known prop'],
        "unknown_person": ['cast member "gran" is not a known person'],
        "bad_mark": ['mark "pit" has h -1', 'mark "pit" has d 4'],
        "bad_time": ["t must be at most 600 s, not 601"],
        "bad_dur": ["dur must be at most 600 s, not 601"],
    }
    # the PENDING_SKILL_HINTS give no hint yet, and their pending one on request
    pending = {
        "out_of_reach": ["mom's arm_r cannot reach; mom is seated in chair.seat; use stand_up first",
                         "mom's arm_r cannot reach; mom is seated in sedan.driver; use get_out first"],
        "unknown_mark": ['"to" must be offstage_left or offstage_right, not "door"'],
        "not_driver": ["mom is in sedan.passenger, not sedan.driver"],
        "not_on_stage": ["sedan is offstage at x=1.3; drive_to a mark on stage first",
                         "cup is on sedan, offstage at x=1.3; drive_to a mark on stage first"],
    }
    for code, reasons in pending.items():
        for reason in reasons:
            assert hint_for(error(code, reason)) is None, (code, reason)
            hint = hint_for(error(code, reason), pending=True)
            assert hint and "{" not in hint and "}" not in hint, (code, reason, hint)
    samples["not_on_stage"] += ["chair is offstage at x=1.3; place it on stage with propStart",
                                "cup is on table, offstage at x=1.3; place table on stage with propStart"]
    for code, reasons in samples.items():
        for reason in reasons:
            hint = hint_for(error(code, reason))
            assert hint and "{" not in hint and "}" not in hint, (code, reason, hint)
    assert set(samples) | set(pending) == set(HINT_VARIANTS)


def test_pending_hints_are_variants_that_give_none_for_now():
    """Each errors.PENDING_SKILL_HINTS pattern is a HINT_VARIANTS entry of the same code whose
    hint is None, so hint_for never falls through to the code's general (wrong) hint."""
    for code, pending in PENDING_SKILL_HINTS.items():
        variants = dict(HINT_VARIANTS[code])
        for pattern, template in pending:
            assert pattern in variants and variants[pattern] is None, (code, pattern.pattern)
            assert template[0].islower() and not template.endswith("."), template


def test_hints_are_not_on_compile_error():
    # CompileError is a frozen contract: the hint is computed beside it, not stored on it
    assert "hint" not in CompileError.model_fields
