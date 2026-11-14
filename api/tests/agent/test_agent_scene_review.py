"""review_scene: storybook extras the compiler does not require (pageText, explanations,
pull tabs, narration sources). Every check asserts its exact warning text."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

import schemas
from agent.tools.scene_review import review_scene
from screenplay.schema import Screenplay

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
        "make from smaller than to (timeline seconds)"
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
