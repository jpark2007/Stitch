"""review_scene: storybook extras the compiler does not require (pageText, explanations,
pull tabs, narration sources). review_timeline: the same scene checked against its compiled
timeline (scrub ranges, explanation tracks, pageText windows, narration cues). Every check
asserts its exact warning text."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

import schemas
from agent.fake_compiler import FakeCompiler
from agent.tools.compile import SceneInputs, load_compiler
from agent.tools.scene_review import review_scene, review_timeline
from screenplay.schema import Screenplay, Timeline

REPO = Path(__file__).resolve().parents[3]
SAMPLES = REPO / "web" / "public" / "samples"

SAMPLE = json.loads((SAMPLES / "hymn_books.screenplay.json").read_text())
MEMORY = json.loads((SAMPLES / "hymn_books.memory.json").read_text())
SAMPLE_PROPS = {p["id"]: schemas.Prop.model_validate(p) for p in MEMORY["props"]}

CAR = schemas.Prop(
    id="car",
    name="the blue Beetle",
    size="vehicle",
    art={"body": None, "wheel": None},
    color="#3355aa",
    parts={"wheel_front": schemas.PropPart(art="wheel", at=[0.25, 0.85], spin=True)},
    states={"lights_on": {}, "lights_off": {}},
)
PLAIN = schemas.Prop(id="plain", name="a plain box", size="hand", art={"body": None}, color="#888888")


def _sample() -> dict:
    return copy.deepcopy(SAMPLE)


def _review(data: dict, props=None) -> list[str]:
    return review_scene(Screenplay.model_validate(data), props=props)


def _with_car(data: dict) -> dict:
    data["props"].append("car")
    data["explanations"].append({"target": "prop:car", "text": "Grandpa's car."})
    return data


def _tab(drives: dict, **extra) -> dict:
    tab = {"id": "tab", "kind": "pull_tab", "label": "Pull", "shot": "s1", "at": [0.5, 0.5], "drives": drives}
    tab.update(extra)
    return tab


# ---------------------------------------------------------------------- the sample


def test_sample_screenplay_has_no_warnings():
    assert _review(_sample()) == []
    assert _review(_sample(), SAMPLE_PROPS) == []


def test_sample_screenplay_with_no_props_mapping_still_clean():
    assert review_scene(Screenplay.model_validate(SAMPLE)) == []


# ----------------------------------------------------------------------- page text


@pytest.mark.parametrize("page_text", [None, "", "   "])
def test_missing_page_text(page_text):
    data = _sample()
    if page_text is None:
        del data["shots"][0]["pageText"]
    else:
        data["shots"][0]["pageText"] = page_text
    assert _review(data) == ["shot s1: no pageText; add one or two short storybook sentences (at most 30 words)"]


def test_page_text_of_30_words_is_fine_and_31_is_too_long():
    data = _sample()
    data["shots"][0]["pageText"] = " ".join(["word"] * 30)
    assert _review(data) == []
    data["shots"][0]["pageText"] = " ".join(["word"] * 31)
    assert _review(data) == ["shot s1: pageText has 31 words; cut it to at most 30 words"]


# --------------------------------------------------------------------- explanations


def test_missing_explanation_for_a_puppet_and_a_prop():
    data = _sample()
    data["explanations"] = [e for e in data["explanations"] if e["target"] == "puppet:mom"]
    assert _review(data) == [
        "explanations: none for puppet:dad; add one with a sentence or two about who they are in this story",
        "explanations: none for prop:hymn_books; add one with a sentence or two about why it matters in this story",
    ]


def test_no_explanations_at_all():
    data = _sample()
    del data["explanations"]
    assert len(_review(data)) == 3


def test_duplicate_explanation():
    data = _sample()
    data["explanations"].append({"target": "puppet:mom", "text": "again"})
    assert _review(data) == ["explanations: 2 for puppet:mom; keep exactly one"]


def test_unknown_explanation_target():
    data = _sample()
    data["explanations"].append({"target": "puppet:grandpa", "text": "not in this story"})
    data["explanations"].append({"target": "hymn_books", "text": "missing the prefix"})
    assert _review(data) == [
        'explanations: target "puppet:grandpa" is not in the cast or props; '
        'point it at "puppet:<cast id>" or "prop:<prop id>", or remove it',
        'explanations: target "hymn_books" is not in the cast or props; '
        'point it at "puppet:<cast id>" or "prop:<prop id>", or remove it',
    ]


# --------------------------------------------------------------------- interactions


def test_duplicate_interaction_id():
    data = _sample()
    data["interactions"].append(copy.deepcopy(data["interactions"][0]))
    assert _review(data) == ['interactions: id "drop_books" is used 2 times; give each interaction its own id']


def test_interaction_on_an_unknown_shot():
    data = _sample()
    data["interactions"][0]["shot"] = "s9"
    assert _review(data) == [
        'interaction drop_books: shot "s9" does not exist; use one of the shot ids (s1) '
        "or leave shot out to show it on every shot"
    ]


def test_interaction_without_a_shot_is_fine():
    data = _sample()
    del data["interactions"][0]["shot"]
    assert _review(data) == []


@pytest.mark.parametrize(("at", "shown"), [([1.2, 0.5], "[1.2, 0.5]"), ([0.5, -0.1], "[0.5, -0.1]"), ([0.5], "[0.5]")])
def test_interaction_off_the_page(at, shown):
    data = _sample()
    data["interactions"][0]["at"] = at
    assert _review(data) == [
        f"interaction drop_books: at {shown} is not on the page; use [u, v] with both between 0 and 1 "
        "(fractions from the top-left)"
    ]


def test_interaction_at_the_page_corners_is_fine():
    data = _sample()
    data["interactions"][0]["at"] = [0, 1]
    assert _review(data) == []


@pytest.mark.parametrize(("frm", "to"), [(3.55, 3.25), (3.25, 3.25)])
def test_scrub_from_not_before_to(frm, to):
    data = _sample()
    data["interactions"][0]["drives"]["from"] = frm
    data["interactions"][0]["drives"]["to"] = to
    assert _review(data) == [
        f"interaction drop_books: scrub from {frm:g} is not before to {to:g}; "
        "make from smaller than to (seconds from the shot start)"
    ]


def test_scrub_with_only_one_end_is_fine():
    data = _sample()
    del data["interactions"][0]["drives"]["to"]
    assert _review(data) == []


def test_prop_state_needs_a_prop_and_states():
    data = _sample()
    data["interactions"] = [_tab({"type": "prop_state"})]
    assert _review(data) == [
        "interaction tab: prop_state has no prop; set drives.prop to one of the screenplay props",
        "interaction tab: prop_state has no states; list the states the tab steps through in drives.states",
    ]


def test_prop_state_prop_must_be_in_the_screenplay():
    data = _sample()
    data["interactions"] = [_tab({"type": "prop_state", "prop": "car", "states": ["lights_on"]})]
    assert _review(data, {"car": CAR}) == [
        'interaction tab: prop "car" is not in the screenplay props; add it to props or use one of them (hymn_books)'
    ]


def test_prop_state_unknown_state_when_props_given():
    data = _with_car(_sample())
    data["interactions"] = [_tab({"type": "prop_state", "prop": "car", "states": ["lights_on", "flying"]})]
    assert _review(data) == []  # no props mapping: states cannot be checked
    assert _review(data, {"car": CAR}) == [
        'interaction tab: prop "car" has no state "flying"; use one of its states (lights_on, lights_off)'
    ]


def test_prop_state_on_a_prop_with_no_states():
    data = _sample()
    data["props"].append("plain")
    data["explanations"].append({"target": "prop:plain", "text": "a box"})
    data["interactions"] = [_tab({"type": "prop_state", "prop": "plain", "states": ["open"]})]
    assert _review(data, {"plain": PLAIN}) == [
        'interaction tab: prop "plain" has no state "open"; it has no states; drive a part or scrub instead'
    ]


def test_prop_part_needs_part_channel_and_range():
    data = _with_car(_sample())
    data["interactions"] = [_tab({"type": "prop_part", "prop": "car"})]
    assert _review(data) == [
        "interaction tab: prop_part has no part or channel or range; "
        "set drives.part, drives.channel, drives.range"
    ]


@pytest.mark.parametrize(("rng", "shown"), [([90, 0], "[90, 0]"), ([0, 0], "[0, 0]"), ([0], "[0]")])
def test_prop_part_range_must_be_min_max(rng, shown):
    data = _with_car(_sample())
    data["interactions"] = [
        _tab({"type": "prop_part", "prop": "car", "part": "wheel_front", "channel": "rot", "range": rng})
    ]
    assert _review(data, {"car": CAR}) == [
        f"interaction tab: range {shown} is not [min, max] with min < max; give two numbers, smaller first"
    ]


def test_prop_part_unknown_part_when_props_given():
    data = _with_car(_sample())
    data["interactions"] = [
        _tab({"type": "prop_part", "prop": "car", "part": "door", "channel": "rot", "range": [0, 60]})
    ]
    assert _review(data) == []
    assert _review(data, {"car": CAR}) == [
        'interaction tab: prop "car" has no part "door"; use one of its parts (wheel_front)'
    ]


def test_good_prop_part_and_prop_state_tabs():
    data = _with_car(_sample())
    data["interactions"] = [
        _tab({"type": "prop_part", "prop": "car", "part": "wheel_front", "channel": "rot", "range": [0, 360]}, id="a"),
        _tab({"type": "prop_state", "prop": "car", "states": ["lights_off", "lights_on"]}, id="b"),
    ]
    assert _review(data, {"car": CAR, **SAMPLE_PROPS}) == []


def test_prop_with_parts_and_no_interactions_asks_for_a_pull_tab():
    data = _with_car(_sample())
    del data["interactions"]
    props = {"car": CAR, **SAMPLE_PROPS}
    assert _review(data, props) == [
        "interactions: none, but car can move or change; "
        "add a pull_tab that drives one of them (prop_part or prop_state)"
    ]
    data["interactions"] = []
    assert len(_review(data, props)) == 1
    assert _review(data) == []  # without the rigs we cannot tell


def test_static_props_and_no_interactions_is_fine():
    data = _sample()
    del data["interactions"]
    assert _review(data, SAMPLE_PROPS) == []


# ------------------------------------------------------------------------ narration


def test_narration_line_without_a_source():
    data = _sample()
    del data["shots"][0]["narration"][1]["source"]
    data["narrations"]["mom"]["s1"][2]["source"] = " "
    assert _review(data) == [
        'shot s1, narration line 2: no source; set source to the telling sentence the words come from, '
        'like "telling:<authorId>:s2"',
        'narrations mom, shot s1, line 3: no source; set source to the telling sentence the words come from, '
        'like "telling:<authorId>:s2"',
    ]


def test_every_warning_is_one_line_in_where_problem_fix_form():
    data = _with_car(_sample())
    del data["shots"][0]["pageText"]
    del data["explanations"]
    data["interactions"] = [
        _tab({"type": "prop_part", "prop": "car", "part": "door", "channel": "rot", "range": [9, 1]}, at=[2, 2]),
        _tab({"type": "prop_state"}, shot="s9"),
    ]
    del data["shots"][0]["narration"][0]["source"]
    warnings = _review(data, {"car": CAR, **SAMPLE_PROPS})
    assert len(warnings) >= 10
    for w in warnings:
        assert "\n" not in w
        where, _, rest = w.partition(": ")
        assert where and "; " in rest


# ======================================================================= the timeline
#
# review_timeline reads the compiled timeline. Most cases start from the sample's compiled
# timeline and break one thing by hand, because FakeCompiler never produces these problems.

TIMELINE = json.loads((SAMPLES / "hymn_books.timeline.json").read_text())
DURATION = TIMELINE["duration"]  # 10.65
SHOT_LENGTH = round(TIMELINE["shots"][0]["end"] - TIMELINE["shots"][0]["actionStart"], 2)  # 9.45


def _timeline() -> dict:
    return copy.deepcopy(TIMELINE)


def _review_tl(screenplay: dict, timeline: dict, props=None) -> list[str]:
    return review_timeline(Screenplay.model_validate(screenplay), Timeline.model_validate(timeline), props=props)


def _scrub(timeline: dict) -> dict:
    return timeline["interactions"][0]["drives"]


def _line(timeline: dict, author: str, n: int) -> dict:
    return timeline["narrations"][author]["lines"][n - 1]


def test_sample_timeline_has_no_warnings():
    assert DURATION == 10.65 and SHOT_LENGTH == 9.45
    assert _review_tl(_sample(), _timeline()) == []
    assert _review_tl(_sample(), _timeline(), SAMPLE_PROPS) == []


def test_fake_compiled_sample_has_no_timeline_warnings():
    scene = SceneInputs(
        sets={s["id"]: schemas.SetDef.model_validate(s) for s in MEMORY["sets"]},
        props=dict(SAMPLE_PROPS),
        people={p["id"]: schemas.Person.model_validate(p) for p in MEMORY["people"]},
    )
    screenplay = Screenplay.model_validate(_sample())
    result = FakeCompiler()(screenplay, scene)
    assert result.ok and result.timeline is not None
    assert review_timeline(screenplay, result.timeline, props=scene.props) == []


def test_compiled_sample_has_no_timeline_warnings_with_whichever_compiler_is_loaded():
    """load_compiler() gives the real screenplay.compiler.compile once it exists, else the fake."""
    scene = SceneInputs(
        sets={s["id"]: schemas.SetDef.model_validate(s) for s in MEMORY["sets"]},
        props=dict(SAMPLE_PROPS),
        people={p["id"]: schemas.Person.model_validate(p) for p in MEMORY["people"]},
    )
    screenplay = Screenplay.model_validate(_sample())
    result = load_compiler()(screenplay, scene)
    assert result.ok, [e.message for e in result.errors]
    assert review_timeline(screenplay, result.timeline, props=scene.props) == []


# ------------------------------------------------------------- interaction ranges


def test_scrub_end_past_the_timeline():
    tl = _timeline()
    _scrub(tl)["to"] = 12.3
    assert _review_tl(_sample(), tl) == [
        f"interaction drop_books: scrub to 12.3s is outside the timeline (0 to 10.65s); "
        f"keep from and to between 0 and {SHOT_LENGTH:g} (seconds from the start of shot s1)"
    ]


def test_scrub_both_ends_outside_the_timeline():
    tl = _timeline()
    _scrub(tl)["from"] = -1
    _scrub(tl)["to"] = 20
    assert _review_tl(_sample(), tl) == [
        "interaction drop_books: scrub from -1s and to 20s are outside the timeline (0 to 10.65s); "
        "keep from and to between 0 and 9.45 (seconds from the start of shot s1)"
    ]


def test_scrub_out_of_range_without_a_shot_gives_a_general_fix():
    tl = _timeline()
    del tl["interactions"][0]["shot"]
    _scrub(tl)["from"] = 11
    _scrub(tl)["to"] = 12
    assert _review_tl(_sample(), tl) == [
        "interaction drop_books: scrub from 11s and to 12s are outside the timeline (0 to 10.65s); "
        "keep from and to inside the shot the tab belongs to (seconds from that shot's start)"
    ]


def test_scrub_ends_exactly_on_the_timeline_edges_are_fine():
    tl = _timeline()
    _scrub(tl)["from"] = 0
    _scrub(tl)["to"] = DURATION
    assert _review_tl(_sample(), tl) == []


@pytest.mark.parametrize(("frm", "to"), [(4.45, 4.45), (4.75, 4.45)])
def test_empty_scrub_range_in_the_timeline(frm, to):
    tl = _timeline()
    _scrub(tl)["from"] = frm
    _scrub(tl)["to"] = to
    assert _review_tl(_sample(), tl) == [
        f"interaction drop_books: scrub from {frm:g}s to {to:g}s covers no time in the timeline; "
        "make from smaller than to so pulling the tab plays something"
    ]


def test_scrub_with_one_end_only_checks_that_end():
    tl = _timeline()
    del _scrub(tl)["from"]
    assert _review_tl(_sample(), tl) == []
    _scrub(tl)["to"] = 99
    assert len(_review_tl(_sample(), tl)) == 1


def test_scrub_target_without_a_track():
    tl = _timeline()
    _scrub(tl)["targets"] = ["prop:hymn_books", "prop:umbrella"]
    assert _review_tl(_sample(), tl) == [
        'interaction drop_books: scrub target "prop:umbrella" has no track in the timeline, so pulling the tab '
        'moves nothing; target a "puppet:<cast id>" or "prop:<prop id>" that is on stage, or leave targets out'
    ]


def test_non_scrub_interactions_have_no_timeline_checks():
    tl = _timeline()
    tl["interactions"][0]["drives"] = {"type": "prop_state", "prop": "hymn_books", "states": ["a", "b"]}
    assert _review_tl(_sample(), tl) == []


# ------------------------------------------------------------ explanation targets


def test_explanation_for_a_puppet_with_no_track():
    tl = _timeline()
    tl["tracks"] = [t for t in tl["tracks"] if t["target"] != "puppet:dad"]
    assert _review_tl(_sample(), tl) == [
        'explanations: target "puppet:dad" has no track in the timeline, so there is nothing to click; '
        "bring dad on stage in a shot (start or enter), or remove the explanation"
    ]


def test_explanation_for_a_prop_with_no_track():
    tl = _timeline()
    tl["tracks"] = [t for t in tl["tracks"] if t["target"] != "prop:hymn_books"]
    _scrub(tl)["targets"] = ["puppet:mom"]
    assert _review_tl(_sample(), tl) == [
        'explanations: target "prop:hymn_books" has no track in the timeline, so there is nothing to click; '
        "put hymn_books on stage (propStart, or someone holding it), or remove the explanation"
    ]


def test_explanations_fall_back_to_the_screenplay_and_skip_malformed_targets():
    tl = _timeline()
    del tl["explanations"]
    data = _sample()
    data["explanations"] += [
        {"target": "puppet:grandpa", "text": "not on stage"},
        {"target": "puppet:grandpa", "text": "said twice"},
        {"target": "grandpa", "text": "no prefix: review_scene's warning"},
    ]
    assert _review_tl(data, tl) == [
        'explanations: target "puppet:grandpa" has no track in the timeline, so there is nothing to click; '
        "bring grandpa on stage in a shot (start or enter), or remove the explanation"
    ]


# ------------------------------------------------------------------ pageText window


def test_page_text_shot_missing_from_the_timeline():
    tl = _timeline()
    tl["shots"][0]["id"] = "s9"
    assert _review_tl(_sample(), tl) == [
        "shot s1: no shot s1 in the timeline, so its pageText is never shown; "
        "give every shot its own id and compile again"
    ]


def test_page_text_dropped_by_the_compiler():
    tl = _timeline()
    del tl["shots"][0]["pageText"]
    assert _review_tl(_sample(), tl) == [
        "shot s1: the timeline shot does not carry this pageText, so the page shows something else; "
        "keep pageText on the shot and compile again"
    ]


def test_page_text_shot_after_the_end_of_the_timeline():
    tl = _timeline()
    tl["shots"][0].update(start=11, actionStart=12.2, end=14)
    assert _review_tl(_sample(), tl) == [
        "shot s1: pageText is never on the page while the shot plays "
        "(the shot runs 11s to 14s, the timeline 0 to 10.65s); "
        "give the shot action or narration that takes time, and keep shots in order"
    ]


def test_page_text_shot_with_no_length():
    tl = _timeline()
    tl["shots"][0].update(start=3, actionStart=3, end=3)
    assert _review_tl(_sample(), tl) == [
        "shot s1: pageText is never on the page while the shot plays "
        "(the shot runs 3s to 3s, the timeline 0 to 10.65s); "
        "give the shot action or narration that takes time, and keep shots in order"
    ]


def test_page_text_shot_overtaken_by_the_next_shot():
    data = _sample()
    second = copy.deepcopy(data["shots"][0])
    second.update(id="s2", pageText="And then they walked home.", narration=[])
    data["shots"].append(second)
    data["narrations"]["mom"]["s2"] = []
    tl = _timeline()
    tl["shots"].append({**tl["shots"][0], "id": "s2", "pageText": "And then they walked home.", "end": 5})
    assert _review_tl(data, tl) == [
        "shot s1: pageText is never on the page while the shot plays "
        "(the shot runs 0s to 10.65s, the timeline 0 to 10.65s, and shot s2 takes the page at 0s); "
        "give the shot action or narration that takes time, and keep shots in order"
    ]
    # in order, the two pages share the timeline and both show
    tl["shots"][0]["end"] = 5
    tl["shots"][1].update(start=5, actionStart=6.2, end=DURATION)
    assert _review_tl(data, tl) == []


def test_shots_without_page_text_are_left_to_review_scene():
    data = _sample()
    del data["shots"][0]["pageText"]
    tl = _timeline()
    tl["shots"][0].update(start=11, actionStart=12.2, end=14)
    assert _review_tl(data, tl) == []


# --------------------------------------------------------------------- narration cues


def test_cue_word_timing_past_the_end_of_the_timeline():
    tl = _timeline()
    line = _line(tl, "mom", 2)
    for w in line["words"]:
        if w["word"].startswith("everywhere"):
            w["t"] = 12
    assert _review_tl(_sample(), tl) == [
        'narrations mom, shot s1, line 2: cue "drop" lands at 12s, after the timeline ends at 10.65s; '
        "start the line earlier in its shot or shorten it, so it is spoken while the shot plays"
    ]


def test_cue_without_word_timings_uses_the_line_start():
    tl = _timeline()
    line = _line(tl, "dad", 3)
    del line["words"]
    line["t"] = 11
    assert _review_tl(_sample(), tl) == [
        'shot s1, narration line 3: cue "hand_back" lands at 11s, after the timeline ends at 10.65s; '
        "start the line earlier in its shot or shorten it, so it is spoken while the shot plays"
    ]


def test_cue_uses_the_cue_word_not_the_line_start():
    tl = _timeline()
    line = _line(tl, "dad", 3)
    line["t"] = -0.5  # the line starts early, but its cue word is spoken in time
    assert _review_tl(_sample(), tl) == []


def test_cue_defaults_to_the_last_word():
    tl = _timeline()
    line = _line(tl, "dad", 2)
    del line["cueWord"]
    line["words"][-1]["t"] = 10.9  # "book." is the last word
    assert _review_tl(_sample(), tl) == [
        'shot s1, narration line 2: cue "drop" lands at 10.9s, after the timeline ends at 10.65s; '
        "start the line earlier in its shot or shorten it, so it is spoken while the shot plays"
    ]


def test_uncued_line_before_the_timeline_starts():
    tl = _timeline()
    _line(tl, "dad", 1)["t"] = -0.5
    assert _review_tl(_sample(), tl) == [
        "shot s1, narration line 1: starts at -0.5s, before the timeline starts; "
        "start the line at 0 or later in its shot"
    ]


def test_narration_line_not_in_the_screenplay_is_named_by_teller_and_index():
    tl = _timeline()
    line = _line(tl, "dad", 1)
    line["text"] = "Words the screenplay never had."
    line["t"] = 11
    assert _review_tl(_sample(), tl) == [
        "narration dad, line 1: starts at 11s, after the timeline ends at 10.65s; "
        "start the line earlier in its shot or shorten it, so it is spoken while the shot plays"
    ]


# --------------------------------------------------------------------------- format


def test_every_timeline_warning_is_one_line_in_where_problem_fix_form():
    tl = _timeline()
    _scrub(tl).update({"from": -1, "to": -1, "targets": ["prop:ghost"]})
    tl["tracks"] = [t for t in tl["tracks"] if t["target"] != "puppet:dad"]
    tl["shots"][0].update(start=11, actionStart=12.2, end=14)
    _line(tl, "dad", 1)["t"] = -3
    _line(tl, "mom", 3)["words"][1]["t"] = 30
    warnings = _review_tl(_sample(), tl)
    assert len(warnings) == 7
    for w in warnings:
        assert "\n" not in w
        where, _, rest = w.partition(": ")
        assert where and "; " in rest
