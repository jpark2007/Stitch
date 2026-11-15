"""Matching a telling to a prepared story: normalization, exact, overlap, keywords, best match."""

from __future__ import annotations

import dataclasses

import pytest

from demo.replay.fixtures import MatchRule, PreparedStory, match_one, match_story, normalize, overlap
from demo.replay.tests.conftest import EXAMPLE_TEXT, FID, OTHER_TEXT, fields


def story(base: PreparedStory, name: str, text: str, **rule) -> PreparedStory:
    return dataclasses.replace(base, name=name, rule=MatchRule(text=text, **rule))


def test_normalize_folds_case_punctuation_and_whitespace():
    assert normalize("  It's the  POT!\n\nSee—the lid?") == "its the pot see the lid"
    assert normalize("Grandma’s   kitchen_table") == "grandmas kitchen table"
    assert normalize("...?!") == ""


def test_overlap_is_symmetric_over_word_multisets():
    assert overlap("a b c", "a b c") == 1.0
    assert overlap("a b", "c d") == 0.0
    assert overlap("a b c d", "a b") == pytest.approx(2 * 2 / 6)
    assert overlap("a a b", "a b b") == overlap("a b b", "a a b")
    assert overlap("", "") == 0.0


def test_exact_match_ignores_case_punctuation_and_spacing(example):
    [tamales] = example
    typed = EXAMPLE_TEXT.upper().replace(",", "").replace(". ", ".\n\n  ")
    found = match_one(typed, tamales)
    assert found is not None and found.kind == "exact" and found.score == 1.0


def test_close_text_matches_by_overlap(example):
    [tamales] = example
    typo = EXAMPLE_TEXT.replace("husks", "husk").replace("badly", "very badly")
    found = match_one(typo, tamales)
    assert found is not None and found.kind == "overlap" and 0.8 <= found.score < 1.0


def test_overlap_below_min_overlap_does_not_match(example):
    [tamales] = example
    strict = dataclasses.replace(tamales, rule=MatchRule(text=EXAMPLE_TEXT, min_overlap=0.99))
    assert match_one(EXAMPLE_TEXT.replace("husks", "husk"), strict) is None


def test_keywords_match_a_different_telling_only_when_all_are_present(example):
    [tamales] = example
    found = match_one("We made tamales and I spread the masa.", tamales)
    assert found is not None and found.kind == "keywords"
    assert match_one("We made tamales on Sunday.", tamales) is None
    no_keywords = dataclasses.replace(tamales, rule=MatchRule(text=EXAMPLE_TEXT))
    assert match_one("We made tamales and I spread the masa.", no_keywords) is None


def test_keyword_phrases_match_whole_words(example):
    [tamales] = example
    phrase = story(tamales, "p", "a completely different text", keywords=["corn husks"])
    assert match_one("It smelled of Corn-Husks.", phrase) is not None
    assert match_one("It smelled of popcorn husks.", phrase) is None


def test_unrelated_and_empty_tellings_match_nothing(example):
    assert match_story(OTHER_TEXT, example) is None
    assert match_story("", example) is None
    assert match_story("?!", example) is None


def test_the_best_story_wins(example):
    [tamales] = example
    close = story(tamales, "a_close", EXAMPLE_TEXT.replace("husks", "leaves"))
    exact = story(tamales, "b_exact", EXAMPLE_TEXT)
    keyword = story(tamales, "c_keyword", "nothing alike at all", keywords=["tamales"])
    assert match_story(EXAMPLE_TEXT, [keyword, close, exact]).story.name == "b_exact"
    assert match_story(EXAMPLE_TEXT, [keyword, close]).story.name == "a_close"
    assert match_story("tamales again", [keyword, close]).story.name == "c_keyword"


def test_ties_go_to_the_higher_overlap_then_the_name(example):
    [tamales] = example
    one = story(tamales, "zz", "nothing alike", keywords=["tamales"])
    two = story(tamales, "aa", "nothing alike either", keywords=["tamales"])
    three = story(tamales, "mm", "tamales were made", keywords=["tamales"])
    assert match_story("tamales were made today", [one, two, three]).story.name == "mm"
    assert match_story("tamales", [one, two]).story.name == "aa"


def test_a_story_for_another_family_never_matches(example):
    [tamales] = example
    mine = dataclasses.replace(tamales, family_id=FID)
    assert match_story(EXAMPLE_TEXT, [mine], family_id=FID) is not None
    assert match_story(EXAMPLE_TEXT, [mine], family_id="someone_else") is None


def test_the_manager_never_matches_a_second_telling(example, db):
    from demo.replay.manager import ReplayRunManager

    mgr = ReplayRunManager(stories=example, db_getter=lambda: db, step_seconds=0)
    try:
        assert mgr.match(FID, fields()) is not None
        assert mgr.match(FID, fields(memory_id="demo_sunday_tamales")) is None
    finally:
        mgr.shutdown()
