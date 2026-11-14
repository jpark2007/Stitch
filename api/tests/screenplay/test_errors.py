"""CompileError construction: every message form, and the conversions from each error type."""

from screenplay.errors import (
    ERROR_CODES,
    HINT_VARIANTS,
    error,
    error_message,
    from_narration_error,
    from_out_of_reach,
    from_state_error,
    hint_for,
    out_of_reach_reason,
)
from screenplay.ik import OutOfReach
from screenplay.narration import NarrationError
from screenplay.schema import Action, CompileError
from screenplay.state import StateError


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


def test_hint_variants_are_registered_codes_with_hints():
    for code, variants in HINT_VARIANTS.items():
        assert code in ERROR_CODES and ERROR_CODES[code].hint is not None, code
        for _, hint in variants:
            assert hint is None or (hint[0].islower() and not hint.endswith(".")), (code, hint)


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
    }
    for code, reasons in samples.items():
        for reason in reasons:
            hint = hint_for(error(code, reason))
            assert hint and "{" not in hint and "}" not in hint, (code, reason, hint)


def test_hints_are_not_on_compile_error():
    # CompileError is a frozen contract: the hint is computed beside it, not stored on it
    assert "hint" not in CompileError.model_fields
