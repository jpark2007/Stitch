"""Narration timing: word timings, cue resolution, retiming, tellings, captions, interactions."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from screenplay.narration import (
    WORD_SECONDS,
    NarrationError,
    align_line_start,
    assemble,
    build_shot_narration,
    convert_interactions,
    cue_action_index,
    cue_word_time,
    default_author,
    last_word_time,
    line_words,
    narrate_shot,
    retime_actions,
    retime_point,
    shot_tellings,
    tokenize,
)
from screenplay.schema import (
    Action,
    Interaction,
    NarrationLine,
    Screenplay,
    Shot,
    Timeline,
    WordTiming,
)

SAMPLES = Path(__file__).resolve().parents[3] / "web" / "public" / "samples"
ACTION_START = 1.2


def load(name: str) -> dict:
    return json.loads((SAMPLES / name).read_text())


@pytest.fixture
def sp() -> Screenplay:
    return Screenplay.model_validate(load("hymn_books.screenplay.json"))


@pytest.fixture(scope="module")
def tl() -> Timeline:
    return Timeline.model_validate(load("hymn_books.timeline.json"))


def rounded(obj):
    if isinstance(obj, float):
        return round(obj, 3)
    if isinstance(obj, list):
        return [rounded(x) for x in obj]
    if isinstance(obj, dict):
        return {k: rounded(v) for k, v in obj.items()}
    return obj


def dump(model) -> dict:
    return rounded(model.model_dump(mode="json", by_alias=True, exclude_none=True))


def line(t: float, text: str, cue: str | None = None, cue_word: str | None = None, **kw) -> NarrationLine:
    return NarrationLine(t=t, text=text, cue=cue, cue_word=cue_word, **kw)


def run_sample(sp: Screenplay):
    shot = sp.shots[0]
    default = default_author(sp)
    retimed = retime_actions(shot, ACTION_START)
    other = {a: by_shot.get(shot.id, []) for a, by_shot in (sp.narrations or {}).items()}
    built = build_shot_narration(shot, ACTION_START, retimed, other, default)
    return default, retimed, built


# --------------------------------------------------------------------------- tokenize

def test_tokenize_strips_ellipsis_and_period():
    assert tokenize("…and then she dropped every single hymn book.") == [
        "and", "then", "she", "dropped", "every", "single", "hymn", "book",
    ]


def test_tokenize_keeps_inner_apostrophes_and_case():
    assert tokenize("I handed them back, and she wouldn't even look at me.") == [
        "I", "handed", "them", "back", "and", "she", "wouldn't", "even", "look", "at", "me",
    ]
    assert tokenize("Couldn't—won't!") == ["Couldn't—won't"]


@pytest.mark.parametrize(
    "text, expected",
    [
        ('"Hello," she said; "goodbye!"', ["Hello", "she", "said", "goodbye"]),
        ("(quietly) [softly] {loudly}", ["quietly", "softly", "loudly"]),
        ("wait — what?! really...", ["wait", "what", "really"]),
        ("“Curly quotes” and ‘single’ ones", ["Curly", "quotes", "and", "single", "ones"]),
        ("  spaced\tout\nwords  ", ["spaced", "out", "words"]),
        ("… — !!! ", []),
        ("", []),
        ("1988, the year.", ["1988", "the", "year"]),
        ("Ça va? Él dijo: ¡sí!", ["Ça", "va", "Él", "dijo", "sí"]),
    ],
)
def test_tokenize_cases(text, expected):
    assert tokenize(text) == expected


# --------------------------------------------------------------------------- words

def test_line_words_default_spacing_in_timeline_time():
    words = line_words(line(2.5, "…and then she dropped."), ACTION_START + 2.5)
    assert [(w.t, w.word) for w in words] == [(3.7, "and"), (3.95, "then"), (4.2, "she"), (4.45, "dropped")]
    assert WORD_SECONDS == 0.25


def test_line_words_honours_given_offsets():
    given = [WordTiming(t=0.0, word="He"), WordTiming(t=0.4, word="handed"), WordTiming(t=1.1, word="them")]
    words = line_words(line(6.0, "He handed them", words=given), 7.2)
    assert [(w.t, w.word) for w in words] == [(7.2, "He"), (7.6, "handed"), (8.3, "them")]


def test_given_word_offsets_drive_cue_and_retime():
    given = [WordTiming(t=0.0, word="And"), WordTiming(t=0.3, word="then,"), WordTiming(t=1.0, word="DROPPED!")]
    shot = Shot(
        id="s1",
        set="x",
        actions=[Action(t=1.0, do="drop", who="mom", what="b"), Action(t=2.0, do="pose", who="mom", pose="p")],
        narration=[line(0.5, "And then, dropped!", cue="drop", cue_word="dropped", words=given)],
    )
    assert retime_actions(shot, 1.0) == [1.5, 2.5]
    built = build_shot_narration(shot, 1.0, [1.5, 2.5], {}, "dad")
    assert [(w.t, w.word) for w in built["dad"][0].words] == [(1.5, "And"), (1.8, "then,"), (2.5, "DROPPED!")]


# --------------------------------------------------------------------------- cues

def test_default_author_from_source(sp):
    assert default_author(sp) == "dad"


def test_default_author_falls_back_to_first_cast_member(sp):
    data = sp.model_dump(by_alias=True)
    for ln in data["shots"][0]["narration"]:
        ln["source"] = None
    data["cast"] = ["mom", "dad"]
    assert default_author(Screenplay.model_validate(data)) == "mom"


def test_default_author_skips_non_telling_sources(sp):
    data = sp.model_dump(by_alias=True)
    data["shots"][0]["narration"][0]["source"] = "photo:p1"
    data["shots"][0]["narration"][1]["source"] = "telling:grandma:s9"
    assert default_author(Screenplay.model_validate(data)) == "grandma"


def test_default_author_no_cast_no_source_is_an_error():
    sp = Screenplay(memory_id="m", cast=[], shots=[Shot(id="s1", set="x")])
    with pytest.raises(NarrationError) as e:
        default_author(sp)
    assert e.value.code == "no_narrator"


def test_cue_action_index_prefers_id_then_first_verb(sp):
    shot = sp.shots[0]
    assert cue_action_index(shot, "drop") == 1
    assert cue_action_index(shot, "hand_back") == 4
    assert cue_action_index(shot, "pick_up") == 3
    two = Shot(
        id="s2",
        set="x",
        actions=[
            Action(t=0, do="walk_to", who="a", to="m"),
            Action(t=1, do="walk_to", who="b", to="m"),
            Action(t=2, do="pose", who="a", pose="p", id="walk_to"),
        ],
    )
    assert cue_action_index(two, "walk_to") == 2  # an id wins over a verb


def test_cue_word_time_case_insensitive_first_match_and_default_last():
    ln = line(0, "The books, the BOOKS everywhere", cue="drop", cue_word="books")
    words = line_words(ln, 10.0)
    assert cue_word_time(ln, words) == 10.25
    ln2 = line(0, "The books, the BOOKS everywhere", cue="drop")
    assert cue_word_time(ln2, line_words(ln2, 10.0)) == 11.0
    ln3 = line(0, "he said “Everywhere!”", cue="drop", cue_word="everywhere,")
    assert cue_word_time(ln3, line_words(ln3, 0.0)) == 0.5


# ------------------------------------------------------------------------ retiming

def test_sample_retime_keeps_original_times(sp):
    assert retime_actions(sp.shots[0], ACTION_START) == [0, 3.25, 3.6, 5.5, 6.6, 7.7]


def _with_drop_line_at(sp: Screenplay, t: float) -> Shot:
    shot = sp.shots[0].model_copy(deep=True)
    shot.narration[1].t = t
    return shot


@pytest.mark.parametrize("dt", [-0.5, -1.25, 0.6, 1.0])
def test_moving_drop_cue_ripples_uncued_but_cued_actions_hold_their_words(sp, dt):
    shot = _with_drop_line_at(sp, 2.5 + dt)
    new = retime_actions(shot, ACTION_START)
    orig = [0, 3.25, 3.6, 5.5, 6.6, 7.7]
    assert new[0] == orig[0]  # before the cued action: untouched
    assert new[1] == pytest.approx(3.25 + dt)  # drop lands on "dropped"
    assert new[2] == pytest.approx(3.6 + dt)  # walk_to ripples
    assert new[3] == pytest.approx(5.5 + dt)  # pick_up ripples
    assert new[4] == pytest.approx(6.6)  # hand_back stays on "handed"
    assert new[5] == pytest.approx(7.7)  # pose keeps its spacing after hand_back
    # The drop action now starts exactly on its cue word in timeline time.
    words = line_words(shot.narration[1], ACTION_START + shot.narration[1].t)
    assert ACTION_START + new[1] == pytest.approx(cue_word_time(shot.narration[1], words))


def test_moving_cue_word_within_line_shifts_action(sp):
    shot = sp.shots[0].model_copy(deep=True)
    shot.narration[1].cue_word = "book"  # 4 words later than "dropped" -> +1.0 s
    new = retime_actions(shot, ACTION_START)
    assert new == [0, 4.25, 4.6, 6.5, 6.6, 7.7]


def test_ripple_through_later_cue_when_its_word_moves(sp):
    shot = sp.shots[0].model_copy(deep=True)
    shot.narration[1].t = 3.5  # drop +1.0
    shot.narration[2].t = 7.35  # hand_back +1.0
    assert retime_actions(shot, ACTION_START) == [0, 4.25, 4.6, 6.5, 7.6, 8.7]


def test_deltas_accumulate_across_cues():
    shot = Shot(
        id="s1",
        set="x",
        actions=[
            Action(t=1.0, do="drop", who="a", what="p"),
            Action(t=2.0, do="pose", who="a", pose="x"),
            Action(t=3.0, do="pose", who="b", pose="y", id="late"),
            Action(t=4.0, do="pose", who="b", pose="z"),
        ],
        narration=[
            line(1.5, "dropped", cue="drop"),
            line(2.5, "a b c d e", cue="late", cue_word="e"),
        ],
    )
    # drop 1.0 -> 1.5 (+0.5): x 2.0 -> 2.5, late ripples 3.0 -> 3.5, z 4.0 -> 4.5.
    # late is then placed on "e" at 3.5 (delta 0.0 from where the ripple left it): z stays 4.5.
    assert retime_actions(shot, 0.0) == [1.5, 2.5, 3.5, 4.5]
    # late's word moves 0.75 s earlier: z keeps its 1.0 s spacing after late.
    shot.narration[1].t = 1.75
    assert retime_actions(shot, 0.0) == [1.5, 2.5, 2.75, 3.75]


def test_ripple_ignores_actions_at_the_same_time():
    shot = Shot(
        id="s1",
        set="x",
        actions=[Action(t=1.0, do="drop", who="a", what="p"), Action(t=1.0, do="pose", who="b", pose="x")],
        narration=[line(1.0, "now then", cue="drop")],
    )
    assert retime_actions(shot, 0.0) == [1.25, 1.0]


def test_uncued_lines_do_not_move_actions(sp):
    shot = sp.shots[0].model_copy(deep=True)
    shot.narration[0].t = 0.4
    assert retime_actions(shot, ACTION_START) == [0, 3.25, 3.6, 5.5, 6.6, 7.7]


# ------------------------------------------------------------------------- errors

def _err(fn, *args, **kw) -> NarrationError:
    with pytest.raises(NarrationError) as e:
        fn(*args, **kw)
    return e.value


def test_error_cue_unknown_action(sp):
    shot = sp.shots[0].model_copy(deep=True)
    shot.narration[2].cue = "hug"
    e = _err(retime_actions, shot, ACTION_START, author="dad")
    assert (e.code, e.shot, e.line_index, e.author) == ("cue_unknown_action", "s1", 2, "dad")
    assert '"hug"' in e.reason and "'drop'" in e.reason and "'hand_back'" in e.reason
    assert str(e).startswith("shot s1, dad's narration line 2: ")


def test_error_cue_word_missing(sp):
    shot = sp.shots[0].model_copy(deep=True)
    shot.narration[1].cue_word = "fell"
    e = _err(retime_actions, shot, ACTION_START)
    assert (e.code, e.shot, e.line_index) == ("cue_word_missing", "s1", 1)
    assert '"fell"' in e.reason and '"dropped"' in e.reason and '"book"' in e.reason


def test_error_cue_word_missing_on_empty_line():
    ln = line(0, "… —", cue="drop")
    e = _err(cue_word_time, ln, [], shot="s3", line_index=4, author="mom")
    assert (e.code, e.shot, e.line_index, e.author) == ("cue_word_missing", "s3", 4, "mom")


def test_error_cue_before_start(sp):
    shot = sp.shots[0].model_copy(deep=True)
    shot.narration[1].t = -1.0  # "dropped" at shot t = -0.25
    e = _err(retime_actions, shot, ACTION_START)
    assert (e.code, e.shot, e.line_index) == ("cue_before_start", "s1", 1)


def test_cue_exactly_at_start_is_fine():
    shot = Shot(
        id="s1",
        set="x",
        actions=[Action(t=2.0, do="drop", who="a", what="p")],
        narration=[line(0.0, "Dropped!", cue="drop")],
    )
    assert retime_actions(shot, 5.0) == [0.0]


def test_error_cue_conflict(sp):
    shot = sp.shots[0].model_copy(deep=True)
    shot.narration[2].cue = "drop"
    shot.narration[2].cue_word = "handed"
    e = _err(retime_actions, shot, ACTION_START)
    assert (e.code, e.shot, e.line_index) == ("cue_conflict", "s1", 2)
    assert "line 1" in e.reason


def test_other_telling_errors_carry_author_shot_and_line(sp):
    shot = sp.shots[0]
    retimed = retime_actions(shot, ACTION_START)
    base = [ln.model_copy(deep=True) for ln in sp.narrations["mom"]["s1"]]

    bad = [ln.model_copy(deep=True) for ln in base]
    bad[1].cue = "nope"
    e = _err(build_shot_narration, shot, ACTION_START, retimed, {"mom": bad}, "dad")
    assert (e.code, e.author, e.shot, e.line_index) == ("cue_unknown_action", "mom", "s1", 1)

    bad = [ln.model_copy(deep=True) for ln in base]
    bad[2].cue_word = "dropped"
    e = _err(build_shot_narration, shot, ACTION_START, retimed, {"mom": bad}, "dad")
    assert (e.code, e.author, e.shot, e.line_index) == ("cue_word_missing", "mom", "s1", 2)

    bad = [ln.model_copy(deep=True) for ln in base]
    bad[2].cue = "drop"
    e = _err(build_shot_narration, shot, ACTION_START, retimed, {"mom": bad}, "dad")
    assert (e.code, e.author, e.shot, e.line_index) == ("cue_conflict", "mom", "s1", 2)

    # "everywhere" is the 6th word (1.25 s in): landing it on drop at shot t=0.0 needs a start of
    # -1.25, before the set starts rising at -1.2.
    early = shot.model_copy(deep=True)
    early.narration[1].t = -0.75  # dad's "dropped" at shot t=0.0
    retimed_early = retime_actions(early, ACTION_START)
    assert retimed_early[1] == 0.0
    e = _err(build_shot_narration, early, ACTION_START, retimed_early, {"mom": base}, "dad")
    assert (e.code, e.author, e.shot, e.line_index) == ("cue_before_start", "mom", "s1", 1)

    e = _err(build_shot_narration, shot, ACTION_START, retimed, {"dad": base}, "dad")
    assert (e.code, e.author, e.shot) == ("duplicate_telling", "dad", "s1")


# ------------------------------------------------------------------------- tellings

def test_sample_narrations_captions_and_default(sp, tl):
    default, retimed, built = run_sample(sp)
    assert default == "dad" == tl.default_narrator
    assert list(built) == ["dad", "mom"]
    tracks, captions = assemble([built], default)
    assert list(tracks) == ["dad", "mom"]
    assert {a: dump(t) for a, t in tracks.items()} == {a: dump(t) for a, t in tl.narrations.items()}
    assert [dump(c) for c in captions] == [dump(c) for c in tl.captions]
    assert round(last_word_time(built) + 0.6, 3) == tl.duration == 10.65


def test_sample_lines_keep_every_field(sp, tl):
    _, _, built = run_sample(sp)
    for author in ("dad", "mom"):
        for got, want in zip(built[author], tl.narrations[author].lines, strict=True):
            assert (got.text, got.source, got.cue, got.cue_word) == (want.text, want.source, want.cue, want.cue_word)
            assert got.t == pytest.approx(want.t)
            assert [(w.t, w.word) for w in got.words] == [(pytest.approx(w.t), w.word) for w in want.words]


def test_non_default_cued_line_is_shifted_onto_the_action(sp):
    shot = sp.shots[0]
    mom = [ln.model_copy(deep=True) for ln in sp.narrations["mom"]["s1"]]
    mom[1].t = 0.9  # written 1.1 s too early
    mom[2].t = 7.0  # written 0.65 s too late
    retimed = retime_actions(shot, ACTION_START)
    built = build_shot_narration(shot, ACTION_START, retimed, {"mom": mom}, "dad")
    m = built["mom"]
    assert m[0].t == 1.2  # uncued: unchanged
    assert m[1].t == pytest.approx(3.2)
    assert next(w.t for w in m[1].words if w.word == "everywhere") == pytest.approx(ACTION_START + retimed[1])
    assert m[2].t == pytest.approx(7.55)
    assert next(w.t for w in m[2].words if w.word == "handed") == pytest.approx(ACTION_START + retimed[4])
    # The input lines are not mutated.
    assert mom[1].t == 0.9 and mom[1].words is None


def test_non_default_telling_follows_retimed_action(sp):
    shot = _with_drop_line_at(sp, 3.0)  # dad's drop moves +0.5
    retimed = retime_actions(shot, ACTION_START)
    built = build_shot_narration(shot, ACTION_START, retimed, {"mom": sp.narrations["mom"]["s1"]}, "dad")
    everywhere = next(w.t for w in built["mom"][1].words if w.word == "everywhere")
    assert everywhere == pytest.approx(ACTION_START + 3.75)
    assert built["mom"][1].t == pytest.approx(3.7)


def test_align_line_start():
    cued = line(2.0, "And of course they went everywhere", cue="drop", cue_word="everywhere")
    assert align_line_start(cued, 3.2, 4.45) == pytest.approx(3.2)
    assert align_line_start(cued, 3.0, 5.0) == pytest.approx(3.75)
    last = line(0, "they went everywhere", cue="drop")
    assert align_line_start(last, 1.0, 2.0) == pytest.approx(1.5)
    uncued = line(2.0, "And of course they went everywhere")
    assert align_line_start(uncued, 3.2, 99.0) == 3.2


def test_build_shot_narration_does_not_mutate_screenplay(sp):
    before = sp.model_dump()
    run_sample(sp)
    assert sp.model_dump() == before


def test_last_word_time_empty():
    assert last_word_time({}) is None
    assert last_word_time({"dad": [line(0, "…")]}) is None


def test_assemble_orders_authors_and_concatenates_shots():
    def built(t: float, text: str) -> NarrationLine:
        return line(t, text).model_copy(update={"words": line_words(line(t, text), t)})

    s1 = {"dad": [built(1.0, "one")], "mom": [built(1.0, "uno")]}
    s2 = {"dad": [built(5.0, "two")], "gran": [built(5.0, "zwei")], "mom": [built(5.5, "dos")]}
    tracks, captions = assemble([s1, s2], "dad")
    assert list(tracks) == ["dad", "mom", "gran"]
    assert [(c.t, c.text) for c in captions] == [(1.0, "one"), (5.0, "two")]
    assert [(c.t, c.text) for c in tracks["mom"].captions] == [(1.0, "uno"), (5.5, "dos")]
    assert tracks["gran"].author_id == "gran" and tracks["gran"].audio is None

    tracks, captions = assemble([{"mom": [built(0.0, "hi")]}], "dad")
    assert list(tracks) == ["dad", "mom"] and captions == [] and tracks["dad"].lines == []


# ---------------------------------------------------------------------- interactions

def test_convert_interactions_sample(sp, tl):
    out = convert_interactions(sp.interactions, {"s1": ACTION_START}, "s1")
    assert out[0].drives.from_ == 4.45 and out[0].drives.to == 4.75
    assert [dump(i) for i in out] == [dump(i) for i in tl.interactions]
    assert sp.interactions[0].drives.from_ == 3.25  # input untouched


def test_convert_interactions_default_shot_and_other_drives():
    scrub = Interaction.model_validate(
        {"id": "a", "kind": "pull_tab", "label": "Pull", "at": [0.5, 0.5],
         "drives": {"type": "scrub", "from": 1.0, "to": 2.0}}
    )
    to_s2 = scrub.model_copy(update={"id": "b", "shot": "s2"})
    toggle = Interaction.model_validate(
        {"id": "c", "kind": "pull_tab", "label": "Open", "shot": "s2", "at": [0.1, 0.2],
         "drives": {"type": "prop_state", "prop": "door", "states": ["shut", "open"]}}
    )
    out = convert_interactions([scrub, to_s2, toggle], {"s1": 1.2, "s2": 12.0}, "s1")
    assert (out[0].drives.from_, out[0].drives.to, out[0].shot) == (2.2, 3.2, None)
    assert (out[1].drives.from_, out[1].drives.to) == (13.0, 14.0)
    assert out[2] == toggle


def test_convert_interactions_unknown_shot():
    scrub = Interaction.model_validate(
        {"id": "a", "kind": "pull_tab", "label": "Pull", "shot": "s9", "at": [0.5, 0.5],
         "drives": {"type": "scrub", "from": 1.0, "to": 2.0}}
    )
    e = _err(convert_interactions, [scrub], {"s1": 1.2}, "s1")
    assert (e.code, e.shot, e.line_index) == ("interaction_unknown_shot", "s9", 0)


# ---------------------------------------------------------------------- retime_point

ORIG = [1.0, 2.0, 4.0]
NEW = [1.5, 3.0, 4.25]


def test_retime_point_before_the_first_action_is_unchanged():
    assert retime_point(0.4, ORIG, NEW) == 0.4
    assert retime_point(-2.0, ORIG, NEW) == -2.0


def test_retime_point_exactly_on_an_action_lands_on_its_new_time():
    assert [retime_point(t, ORIG, NEW) for t in ORIG] == NEW


def test_retime_point_between_actions_follows_the_latest_earlier_action():
    assert retime_point(1.25, ORIG, NEW) == 1.75  # after action 0 (+0.5)
    assert retime_point(3.9, ORIG, NEW) == 4.9  # after action 1 (+1.0), not pulled back by action 2
    assert retime_point(6.0, ORIG, NEW) == 6.25  # after the last action (+0.25)


def test_retime_point_does_not_depend_on_action_order():
    assert retime_point(3.0, [4.0, 1.0, 2.0], [4.25, 1.5, 3.0]) == 4.0  # follows the action written at 2.0


def test_retime_point_ties_follow_the_last_action_at_that_time():
    original = [1.0, 1.0, 3.0]
    retimed = [1.25, 1.0, 3.0]  # retime_actions: the cued action moves, its same-time sibling does not
    assert retime_point(1.0, original, retimed) == 1.0
    assert retime_point(1.5, original, retimed) == 1.5
    assert retime_point(1.5, original, [1.0, 2.0, 3.0]) == 2.5


def test_retime_point_with_no_actions_is_unchanged():
    assert retime_point(2.5, [], []) == 2.5
    assert retime_point(2.5, (), ()) == 2.5


def test_retime_point_rounds_float_noise():
    assert retime_point(4.45, [3.25], [4.0]) == 5.2


def test_retime_point_needs_one_retimed_t_per_action():
    with pytest.raises(ValueError):
        retime_point(1.0, [1.0, 2.0], [1.0])


# ------------------------------------------------------- interactions after retiming

def _scrub(id_: str, t_from: float | None, t_to: float | None, shot: str | None = None) -> Interaction:
    drives: dict = {"type": "scrub"}
    if t_from is not None:
        drives["from"] = t_from
    if t_to is not None:
        drives["to"] = t_to
    return Interaction.model_validate(
        {"id": id_, "kind": "pull_tab", "label": "Pull", "shot": shot, "at": [0.5, 0.5], "drives": drives}
    )


@pytest.mark.parametrize("dt", [-0.5, 0.6, 1.0])
def test_scrub_follows_a_drop_cued_later(sp, dt):
    shot = _with_drop_line_at(sp, 2.5 + dt)
    original = [a.t for a in shot.actions]
    retimed = retime_actions(shot, ACTION_START)
    assert retimed[1] == pytest.approx(3.25 + dt)
    out = convert_interactions(
        sp.interactions, {"s1": ACTION_START}, "s1", retimed_by_shot={"s1": (original, retimed)}
    )
    # Written as shot 3.25..3.55 around the drop at 3.25: both ends move by the drop's delta.
    assert out[0].drives.from_ == pytest.approx(ACTION_START + 3.25 + dt)
    assert out[0].drives.to == pytest.approx(ACTION_START + 3.55 + dt)
    assert out[0].drives.to - out[0].drives.from_ == pytest.approx(0.3)
    assert sp.interactions[0].drives.from_ == 3.25  # input untouched
    # Without the retiming the tab would scrub the old window, off the drop.
    old = convert_interactions(sp.interactions, {"s1": ACTION_START}, "s1")
    assert old[0].drives.from_ == 4.45


def test_scrub_output_without_retimed_by_shot_is_unchanged(sp, tl):
    """Without retiming, written ends only shift by the shot's action_start. A missing ``from``
    is the shot's t=0 and a missing ``to`` the shot's end (``end_by_shot``), so the timeline
    never carries None; without an end for the shot, a missing ``to`` is an error."""
    scrubs = [*sp.interactions, _scrub("b", 1.0, 2.0, shot="s2"), _scrub("c", None, 2.0), _scrub("d", 0.5, None)]
    starts = {"s1": ACTION_START, "s2": 12.0}
    ends = {"s1": 11.5, "s2": 20.0}
    plain = convert_interactions(scrubs, starts, "s1", end_by_shot=ends)
    assert [i.model_dump_json() for i in plain] == [
        i.model_dump_json() for i in convert_interactions(scrubs, starts, "s1", retimed_by_shot=None, end_by_shot=ends)
    ]
    assert [dump(i) for i in plain[:1]] == [dump(i) for i in tl.interactions]
    assert [(i.drives.from_, i.drives.to) for i in plain] == [(4.45, 4.75), (13.0, 14.0), (ACTION_START, 3.2), (1.7, 11.5)]
    # Written ends need no shot end: the sample converts the same without end_by_shot.
    assert [dump(i) for i in convert_interactions(scrubs[:3], starts, "s1")] == [dump(i) for i in plain[:3]]
    # A missing "to" with no end for its shot cannot be filled in.
    for kwargs in ({}, {"end_by_shot": {"s2": 20.0}}):
        e = _err(convert_interactions, scrubs, starts, "s1", **kwargs)
        assert (e.code, e.shot, e.line_index) == ("interaction_missing_to", "s1", 3)
        assert e.reason == 'interaction "d" in shot "s1" needs to: the shot time its scrub ends'
    # Identity retiming, or retiming for another shot only, changes nothing for s1's scrubs.
    shot = sp.shots[0]
    same = [a.t for a in shot.actions]
    in_s1 = [scrubs[0], *scrubs[2:]]
    for timing in ({"s1": (same, same)}, {"s2": ([0.0], [5.0])}):
        got = convert_interactions(in_s1, starts, "s1", retimed_by_shot=timing, end_by_shot=ends)
        assert [i.model_dump_json() for i in got] == [i.model_dump_json() for i in [plain[0], *plain[2:]]]


def test_scrub_missing_bounds_are_its_own_shot_bounds():
    out = convert_interactions(
        [_scrub("a", None, None, shot="s2"), _scrub("b", None, 1.0, shot="s2"), _scrub("c", 2.0, None, shot="s2"),
         _scrub("d", None, None)],
        {"s1": 1.2, "s2": 11.85},
        "s1",
        end_by_shot={"s1": 10.0, "s2": 16.0},
    )
    assert [(i.drives.from_, i.drives.to) for i in out] == [(11.85, 16.0), (11.85, 12.85), (13.85, 16.0), (1.2, 10.0)]
    assert all(i.drives.from_ is not None and i.drives.to is not None for i in out)


def test_scrub_from_past_its_shot_end_is_a_bad_range():
    e = _err(convert_interactions, [_scrub("late", 5.0, None)], {"s1": 1.0}, "s1", end_by_shot={"s1": 4.0})
    assert (e.code, e.shot, e.line_index) == ("interaction_bad_range", "s1", 0)
    assert e.reason == (
        'interaction "late" would scrub from shot t=5.00 back to t=3.00, where shot "s1" ends; '
        'give it a "to" after its "from"'
    )
    # Ending exactly where the shot ends is a point, which is allowed.
    out = convert_interactions([_scrub("edge", 3.0, None)], {"s1": 1.0}, "s1", end_by_shot={"s1": 4.0})
    assert (out[0].drives.from_, out[0].drives.to) == (4.0, 4.0)


def test_scrub_retiming_maps_each_present_end_and_only_its_own_shot():
    timing = {"s2": ([1.0, 2.0], [1.5, 3.0])}
    out = convert_interactions(
        [_scrub("a", 1.0, 2.0), _scrub("b", 1.2, 2.5, shot="s2"), _scrub("c", None, 2.5, shot="s2"),
         _scrub("d", 0.5, None, shot="s2")],
        {"s1": 1.0, "s2": 10.0},
        "s1",
        retimed_by_shot=timing,
        end_by_shot={"s1": 9.0, "s2": 18.0},
    )
    # Filled-in defaults (s2's t=0 and its end) are not retimed; written ends are.
    assert [(i.drives.from_, i.drives.to) for i in out] == [(2.0, 3.0), (11.7, 13.5), (10.0, 13.5), (10.5, 18.0)]


def test_scrub_retimed_from_past_the_filled_in_end_is_a_bad_range():
    # "from" 2.0 follows its action to 3.0; the shot ends at shot t=2.5.
    e = _err(
        convert_interactions, [_scrub("x", 2.0, None)], {"s1": 1.0}, "s1",
        retimed_by_shot={"s1": ([2.0], [3.0])}, end_by_shot={"s1": 3.5},
    )
    assert (e.code, e.line_index) == ("interaction_bad_range", 0)
    assert "from shot t=3.00 back to t=2.50" in e.reason


def test_scrub_retimed_to_before_the_filled_in_from_is_a_bad_range():
    # "to" 1.0 follows its action to shot t=-0.5, before the shot's t=0 where "from" defaults.
    e = _err(
        convert_interactions, [_scrub("y", None, 1.0)], {"s1": 1.0}, "s1",
        retimed_by_shot={"s1": ([1.0], [-0.5])}, end_by_shot={"s1": 9.0},
    )
    assert (e.code, e.line_index) == ("interaction_bad_range", 0)
    assert e.reason == (
        'interaction "y" would scrub from shot t=0.00, where shot "s1" starts, back to t=-0.50; '
        'give it a "from" before its "to"'
    )


def _crossed_cues_shot() -> Shot:
    # b is cued first on "now" (shot 2.0, where it was written); a is then cued on "later"
    # (shot 3.0). b is already pinned, so a now lands after b.
    return Shot(
        id="s1",
        set="x",
        actions=[Action(t=1.0, do="drop", who="a", what="p"), Action(t=2.0, do="pose", who="b", pose="x", id="b")],
        narration=[line(2.0, "now", cue="b"), line(3.0, "later", cue="drop")],
    )


def test_scrub_range_turned_around_by_cues_is_an_error():
    shot = _crossed_cues_shot()
    original = [a.t for a in shot.actions]
    retimed = retime_actions(shot, 0.0)
    assert retimed == [3.0, 2.0]
    ok = _scrub("fine", 0.5, 1.5)  # before a, then after a: 0.5 .. 3.5
    bad = _scrub("pull", 1.5, 2.5)  # after a (-> 3.5) to after b (-> 2.5)
    e = _err(
        convert_interactions, [ok, bad], {"s1": 1.2}, "s1", retimed_by_shot={"s1": (original, retimed)}
    )
    assert (e.code, e.shot, e.line_index, e.author) == ("interaction_bad_range", "s1", 1, None)
    assert e.reason == (
        'interaction "pull" would scrub from shot t=3.50 back to t=2.50 once its actions move onto '
        "their cue words; widen the range or cue the actions in order"
    )
    assert str(e) == f"shot s1, narration line 1: {e.reason}"
    # Without the retiming the same range converts as written.
    out = convert_interactions([ok, bad], {"s1": 1.2}, "s1")
    assert (out[1].drives.from_, out[1].drives.to) == (2.7, 3.7)


def test_scrub_range_collapsing_to_a_point_is_allowed():
    out = convert_interactions(
        [_scrub("a", 1.5, 2.5)], {"s1": 0.0}, "s1", retimed_by_shot={"s1": ([1.0, 2.0], [2.0, 2.0])}
    )
    assert (out[0].drives.from_, out[0].drives.to) == (2.5, 2.5)


# ------------------------------------------------------------- shot_tellings / narrate_shot

def test_cue_word_missing_exact_message(sp):
    shot = sp.shots[0].model_copy(deep=True)
    shot.narration[1].cue_word = "fell"
    e = _err(retime_actions, shot, ACTION_START, author="dad")
    assert (e.code, e.author, e.shot, e.line_index) == ("cue_word_missing", "dad", "s1", 1)
    assert str(e) == e.message == (
        "shot s1, dad's narration line 1: "
        'cueWord "fell" is not a word of "…and then she dropped every single hymn book."; '
        'its words are: "and", "then", "she", "dropped", "every", "single", "hymn", "book"'
    )


def test_shot_tellings(sp):
    assert shot_tellings(sp, "s1") == {"mom": sp.narrations["mom"]["s1"]}
    assert shot_tellings(sp, "s2") == {}
    data = sp.model_dump(by_alias=True)
    data["narrations"] = {
        "gran": {"s1": [{"t": 0, "text": "Hello"}]},
        "kid": {"s1": []},  # no lines for the shot: left out
        "mom": data["narrations"]["mom"],
        "uncle": {"s2": [{"t": 0, "text": "Elsewhere"}]},
    }
    assert list(shot_tellings(Screenplay.model_validate(data), "s1")) == ["gran", "mom"]
    assert shot_tellings(sp.model_copy(update={"narrations": None}), "s1") == {}


def test_shot_tellings_skips_authors_with_an_empty_list_for_the_shot(sp):
    data = sp.model_dump(by_alias=True)
    data["narrations"] = {"kid": {"s1": [], "s2": [{"t": 0, "text": "Later"}]}, "mom": data["narrations"]["mom"]}
    sp2 = Screenplay.model_validate(data)
    assert list(shot_tellings(sp2, "s1")) == ["mom"]
    assert list(shot_tellings(sp2, "s2")) == ["kid"]
    # So narrate_shot builds no empty telling for the kid in s1.
    _, built = narrate_shot(sp2, sp2.shots[0], ACTION_START, default_author(sp2))
    assert list(built) == ["dad", "mom"]


def test_narrate_shot_reproduces_sample(sp, tl):
    shot = sp.shots[0]
    retimed, built = narrate_shot(sp, shot, tl.shots[0].action_start, default_author(sp))
    assert retimed == [0, 3.25, 3.6, 5.5, 6.6, 7.7]
    assert list(built) == ["dad", "mom"]
    tracks, captions = assemble([built], "dad")
    assert list(tracks) == list(tl.narrations)
    for author, track in tracks.items():
        want = tl.narrations[author]
        assert track.author_id == want.author_id and track.audio == want.audio
        for got_c, want_c in zip(track.captions, want.captions, strict=True):
            assert got_c.text == want_c.text and abs(got_c.t - want_c.t) < 1e-3
        for got_l, want_l in zip(track.lines, want.lines, strict=True):
            assert (got_l.text, got_l.source, got_l.cue, got_l.cue_word) == (
                want_l.text, want_l.source, want_l.cue, want_l.cue_word)
            assert abs(got_l.t - want_l.t) < 1e-3
            for gw, ww in zip(got_l.words, want_l.words, strict=True):
                assert gw.word == ww.word and abs(gw.t - ww.t) < 1e-3
    for got_c, want_c in zip(captions, tl.captions, strict=True):
        assert got_c.text == want_c.text and abs(got_c.t - want_c.t) < 1e-3
    inter = convert_interactions(sp.interactions, {"s1": tl.shots[0].action_start}, "s1")
    for got_i, want_i in zip(inter, tl.interactions, strict=True):
        assert abs(got_i.drives.from_ - want_i.drives.from_) < 1e-3
        assert abs(got_i.drives.to - want_i.drives.to) < 1e-3
        assert got_i.model_copy(update={"drives": None}) == want_i.model_copy(update={"drives": None})
        assert got_i.drives.targets == want_i.drives.targets


def test_narrate_shot_names_the_default_teller_in_errors(sp):
    shot = sp.shots[0].model_copy(deep=True)
    shot.narration[2].cue = "nope"
    e = _err(narrate_shot, sp, shot, ACTION_START, "dad")
    assert (e.code, e.author, e.line_index) == ("cue_unknown_action", "dad", 2)


# ------------------------------------------- bug 1: a ripple pushes an action before t=0

def test_ripple_pushing_an_uncued_action_before_the_shot_is_an_error():
    # p (1.5) < q (2.0) < wave (2.2). Line 0 pins q at 0 (-2.0): wave ripples to 0.2.
    # Line 1 then pins p at 0.25 (-1.25): q is pinned, but wave ripples again, to -1.05.
    shot = Shot(
        id="s1",
        set="x",
        actions=[
            Action(t=1.5, do="pose", who="a", pose="x", id="p"),
            Action(t=2.0, do="pose", who="a", pose="y", id="q"),
            Action(t=2.2, do="wave", who="b"),
        ],
        narration=[line(0.0, "Now", cue="q"), line(0.25, "Then", cue="p")],
    )
    e = _err(retime_actions, shot, 0.0, author="dad")
    assert (e.code, e.author, e.shot, e.line_index) == ("cue_before_start", "dad", "s1", 1)
    assert e.reason == (
        "action 2 (wave) would start at shot t=-1.05, before the shot starts: it keeps its spacing "
        "after action 0 (p), which this line moves to shot t=0.25; give action 2 a later t or a cue of its own"
    )
    # The same cues in the other order, or with wave cued itself, are fine.
    shot.narration.reverse()
    assert retime_actions(shot, 0.0) == [0.25, 0.0, 0.2]
    shot.narration.reverse()
    shot.narration.append(line(0.5, "Bye", cue="wave"))
    assert retime_actions(shot, 0.0) == [0.25, 0.0, 0.5]


def test_authored_negative_t_passes_through_retiming():
    # No contract forbids a negative Action.t here (validation owns that rule), so an uncued
    # action written before t=0 and never rippled is returned as written: no KeyError and no
    # retiming error (the ripple check names a line, and no line moved this action).
    shot = Shot(id="s1", set="x", actions=[Action(t=-0.5, do="wave", who="a")], narration=[])
    assert retime_actions(shot, 1.2) == [-0.5]
    # Earlier than the cued action, so the ripple skips it and it keeps its written t.
    shot = Shot(
        id="s1",
        set="x",
        actions=[Action(t=0.5, do="pose", who="a", pose="x", id="p"), Action(t=-0.5, do="wave", who="b")],
        narration=[line(0.0, "Now", cue="p")],
    )
    assert retime_actions(shot, 0.0) == [0.0, -0.5]
    # A ripple that moves a negative-t action later is applied as usual.
    shot = Shot(
        id="s1",
        set="x",
        actions=[Action(t=-2.0, do="pose", who="a", pose="x", id="p"), Action(t=-0.5, do="wave", who="b")],
        narration=[line(0.0, "Now", cue="p")],
    )
    assert retime_actions(shot, 0.0) == [0.0, 1.5]


# ------------------------------------------------ bug 2: overlapping lines in one telling

def test_default_telling_lines_overlap():
    shot = Shot(id="s1", set="x", narration=[line(0.0, "one two three four"), line(0.5, "five six")])
    e = _err(build_shot_narration, shot, ACTION_START, [], {}, "dad")
    assert (e.code, e.author, e.shot, e.line_index) == ("lines_overlap", "dad", "s1", 1)
    assert e.reason == (
        "line 1 starts at shot t=0.50, before line 0 has been said (its last word ends at shot t=1.00); "
        "start line 1 later or shorten line 0"
    )
    assert str(e).startswith("shot s1, dad's narration line 1: line 1 starts")
    # narrate_shot reports it for a shot with no other tellings too.
    sp = Screenplay.model_validate({"memoryId": "m", "cast": ["dad"], "shots": [shot.model_dump(by_alias=True)]})
    e = _err(narrate_shot, sp, sp.shots[0], ACTION_START, "dad")
    assert (e.code, e.author, e.line_index) == ("lines_overlap", "dad", 1)


def _overlapping_default_shot() -> Shot:
    return Shot(
        id="s1",
        set="x",
        actions=[Action(t=0.3, do="drop", who="mom", what="b", id="drop")],
        narration=[line(0.0, "one two three four"), line(0.3, "Dropped.", cue="drop")],
    )


def test_a_default_overlap_never_hides_a_tellers_own_errors():
    shot = _overlapping_default_shot()
    retimed = retime_actions(shot, 0.0)
    kid = [line(0.0, "a b c d"), line(0.2, "e")]
    e = _err(build_shot_narration, shot, 0.0, retimed, {"kid": kid}, "dad")
    assert (e.code, e.author, e.line_index) == ("lines_overlap", "kid", 1)
    early = [line(0.0, "a b c d e f g h", cue="drop", cue_word="h")]  # would start at -1.45
    e = _err(build_shot_narration, shot, 0.0, retimed, {"kid": early}, "dad")
    assert (e.code, e.author, e.line_index) == ("cue_before_start", "kid", 0)
    # With clean tellers, the default overlap is reported.
    e = _err(build_shot_narration, shot, 0.0, retimed, {"kid": [line(0.0, "Kid says")]}, "dad")
    assert (e.code, e.author, e.line_index) == ("lines_overlap", "dad", 1)


def test_check_default_false_reports_a_default_overlap_once_per_shot():
    # One teller per call (as validation does): check_default=False on those calls and a
    # single default-only call give each problem once.
    shot = _overlapping_default_shot()
    retimed = retime_actions(shot, 0.0)
    for author in ("mom", "kid"):
        built = build_shot_narration(shot, 0.0, retimed, {author: [line(0.0, "says")]}, "dad", check_default=False)
        assert list(built) == ["dad", author]
    e = _err(build_shot_narration, shot, 0.0, retimed, {}, "dad")
    assert (e.code, e.author, e.line_index) == ("lines_overlap", "dad", 1)
    # check_default=False still checks the other tellers.
    kid = [line(0.0, "a b c d"), line(0.2, "e")]
    e = _err(build_shot_narration, shot, 0.0, retimed, {"kid": kid}, "dad", check_default=False)
    assert (e.code, e.author) == ("lines_overlap", "kid")


def test_each_teller_is_checked_before_the_next():
    shot = _rise_shot(0.5)
    retimed = retime_actions(shot, ACTION_START)
    other = {
        "mom": [line(0.0, "a b c d"), line(0.2, "e")],  # overlaps
        "kid": [line(0.0, "a b c d e f g h", cue="drop", cue_word="h")],  # before the shot starts
    }
    e = _err(build_shot_narration, shot, ACTION_START, retimed, other, "dad")
    assert (e.code, e.author) == ("lines_overlap", "mom")
    e = _err(build_shot_narration, shot, ACTION_START, retimed, dict(reversed(other.items())), "dad")
    assert (e.code, e.author) == ("cue_before_start", "kid")


def test_lines_may_follow_back_to_back():
    # "four" is at 0.75 and is said by 1.0: a line may start exactly then.
    shot = Shot(id="s1", set="x", narration=[line(0.0, "one two three four"), line(1.0, "five six")])
    built = build_shot_narration(shot, ACTION_START, [], {}, "dad")
    assert [ln.t for ln in built["dad"]] == [1.2, 2.2]
    mom = [line(0.0, "one two three four"), line(1.0, "five six")]
    assert [ln.t for ln in build_shot_narration(shot, ACTION_START, [], {"mom": mom}, "dad")["mom"]] == [1.2, 2.2]


def test_overlap_uses_tts_word_times_and_skips_silent_lines():
    given = [WordTiming(t=0.0, word="Well"), WordTiming(t=1.4, word="then")]
    shot = Shot(id="s1", set="x", narration=[line(0.0, "Well then", words=given), line(0.5, "…"), line(1.5, "Go")])
    e = _err(build_shot_narration, shot, 0.0, [], {}, "dad")
    assert (e.code, e.line_index) == ("lines_overlap", 2)
    assert e.reason == (
        "line 2 starts at shot t=1.50, before line 0 has been said (its last word ends at shot t=1.65); "
        "start line 2 later or shorten line 0"
    )
    shot.narration[2].t = 1.65
    build_shot_narration(shot, 0.0, [], {}, "dad")


def test_other_telling_overlaps_after_its_alignment_shift(sp):
    shot = sp.shots[0]
    retimed = retime_actions(shot, ACTION_START)
    mom = [ln.model_copy(deep=True) for ln in sp.narrations["mom"]["s1"]]
    # Aligned, line 1 starts at 2.0 ("everywhere" lands on the drop at 3.25). A 12-word
    # line 0 is said until 3.0 and runs into it.
    mom[0].text = "I was carrying far too many hymn books up those stone steps."
    e = _err(build_shot_narration, shot, ACTION_START, retimed, {"mom": mom}, "dad")
    assert (e.code, e.author, e.shot, e.line_index) == ("lines_overlap", "mom", "s1", 1)
    assert e.reason == (
        "line 1 starts at shot t=2.00, before line 0 has been said (its last word ends at shot t=3.00); "
        "start line 1 later or shorten line 0"
    )
    assert str(e).startswith("shot s1, mom's narration line 1: ")


def test_sample_lines_are_back_to_back_and_build(sp):
    # dad's line 1 and mom's line 1 each start exactly when the line before has been said.
    _, _, built = run_sample(sp)
    for author in ("dad", "mom"):
        first, second = built[author][0], built[author][1]
        assert second.t == pytest.approx(first.words[-1].t + WORD_SECONDS)


# ------------------------------------- bug 3: another telling may start during the set rise

def _rise_shot(drop_at: float) -> Shot:
    return Shot(
        id="s1",
        set="x",
        actions=[Action(t=drop_at, do="drop", who="mom", what="b", id="drop")],
        narration=[line(drop_at, "Down", cue="drop")],
    )


def test_other_telling_may_start_while_the_set_rises():
    shot = _rise_shot(0.5)
    mom = [line(0.0, "a b c d e f", cue="drop", cue_word="f")]  # "f" is 1.25 s in
    built = build_shot_narration(shot, ACTION_START, retime_actions(shot, ACTION_START), {"mom": mom}, "dad")
    assert built["mom"][0].t == pytest.approx(ACTION_START - 0.75)
    assert built["mom"][0].words[-1].t == pytest.approx(ACTION_START + 0.5)
    # Exactly when the set starts rising is still fine.
    shot = _rise_shot(0.55)
    mom = [line(0.0, "a b c d e f g h", cue="drop", cue_word="h")]  # "h" is 1.75 s in
    built = build_shot_narration(shot, ACTION_START, retime_actions(shot, ACTION_START), {"mom": mom}, "dad")
    assert built["mom"][0].t == pytest.approx(0.0)


def test_other_telling_before_the_set_rises_is_an_error():
    shot = _rise_shot(0.5)
    mom = [line(0.0, "a b c d e f g h", cue="drop", cue_word="h")]
    e = _err(build_shot_narration, shot, ACTION_START, retime_actions(shot, ACTION_START), {"mom": mom}, "dad")
    assert (e.code, e.author, e.shot, e.line_index) == ("cue_before_start", "mom", "s1", 0)
    # The shot starts (TimelineShot.start) when its set starts rising, RISE before t=0. The
    # wording matches what validation reports (tests/screenplay/test_validate.py).
    assert e.reason == (
        'to land its cue word on the action, "a b c d e f g h" would start at shot t=-1.25, before the '
        "shot starts; shorten the words before the cue word or move the action later"
    )
