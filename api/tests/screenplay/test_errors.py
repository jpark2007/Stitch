"""CompileError construction: every message form, and the conversions from each error type."""

from screenplay.errors import (
    error,
    error_message,
    from_narration_error,
    from_out_of_reach,
    from_state_error,
    out_of_reach_reason,
)
from screenplay.ik import OutOfReach
from screenplay.narration import NarrationError
from screenplay.schema import Action
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
    err = from_state_error(StateError("unknown_mark", "nowhere is not a mark in shot s1"), "s1", None)
    assert err.message == "shot s1: nowhere is not a mark in shot s1"
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
