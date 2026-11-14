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
    retime_actions,
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

    # "everywhere" is the 6th word: landing it on drop at shot t=0.5 needs a start of -0.75.
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
