"""People the story names who are not in the family tree are mentioned, never cast.

No tool creates a person, and the compiler rejects any cast id that is not in the tree
(SceneLookupError 'person:<id>'). So the interviewer reports tree people under `who` and
everyone else under `also_named`, and the storyboarder casts only `who`.
"""

from __future__ import annotations

import re

from agent.subagents import load_prompt

INTERVIEWER = load_prompt("interviewer")
STORYBOARDER = load_prompt("storyboarder")


def _flat(text: str) -> str:
    return re.sub(r"\s+", " ", text).lower()


def _return_shape(text: str) -> list[str]:
    section = text[text.index("## What you return"):]
    block = re.search(r"```\n(.*?)```", section, flags=re.DOTALL)
    assert block, "interviewer.md has no fenced return shape"
    return block.group(1).splitlines()


def test_interviewer_no_longer_invents_people():
    flat = _flat(INTERVIEWER)
    assert "treat the name as a new person" not in flat
    assert "new name, one per line" not in flat
    assert "no tool adds a person to the family tree" in flat


def test_interviewer_return_shape_splits_who_and_also_named():
    lines = _return_shape(INTERVIEWER)
    keys = [line.split(":", 1)[0] for line in lines if ":" in line]
    assert "who" in keys and "also_named" in keys
    assert keys.index("also_named") == keys.index("who") + 1
    who = next(line for line in lines if line.startswith("who:"))
    assert "family tree" in who and "new name" not in who
    also = next(line for line in lines if line.startswith("also_named:"))
    assert "not in the tree" in also


def test_interviewer_says_who_holds_only_tree_ids():
    flat = _flat(INTERVIEWER)
    assert "`who` holds only person ids from the family tree" in flat
    assert "never cast" in flat


def test_interviewer_routes_rejected_and_unmatched_names_to_also_named():
    flat = _flat(INTERVIEWER)
    assert re.search(r'"rejected:" \([^)]*`also_named`', flat), "a rejected merge must go under also_named"
    assert "no possible match in the tree also goes under `also_named`" in flat


def test_also_named_people_are_never_missing_for_the_stageable_test():
    test = _flat(INTERVIEWER.split("## The stageable test")[1].split("## Steps")[0])
    assert "never count as missing" in test and "`also_named`" in test


def test_storyboarder_never_casts_people_outside_the_tree():
    flat = _flat(STORYBOARDER)
    assert "people not in the family tree are never cast" in flat
    assert "`also_named`" in flat
    assert "never become puppets" in flat
    assert "narration" in flat and "page text" in flat
    who_line = next(line for line in STORYBOARDER.splitlines() if line.startswith("- **who**"))
    assert "only people in the family tree" in who_line
