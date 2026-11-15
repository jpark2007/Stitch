"""Loading and checking prepared stories: the example compiles, broken ones fail loudly by name."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from demo.replay.fixtures import (
    ReplayFixtureError,
    load_stories,
    replay_dir,
    validate_story,
)
from demo.replay.serve import check_stories
from demo.replay.tests.conftest import API_DIR, EXAMPLE_DIR, EXAMPLE_TEXT

TAMALES = API_DIR / "seed" / "demo_family" / "demo_sunday_tamales.screenplay.json"
DEMO_PROPS = API_DIR / "seed" / "demo_family" / "props.json"
DEMO_SETS = API_DIR / "seed" / "demo_family" / "sets.json"


def write_story(root: Path, name: str, *, screenplay: dict | None = None, **story) -> Path:
    folder = root / name
    folder.mkdir(parents=True)
    sp = screenplay if screenplay is not None else json.loads(TAMALES.read_text())
    (folder / "screenplay.json").write_text(json.dumps(sp))
    (folder / "props.json").write_text(DEMO_PROPS.read_text())
    (folder / "sets.json").write_text(DEMO_SETS.read_text())
    spec = {
        "match": {"text": f"{name}: {EXAMPLE_TEXT}"},
        "screenplayFile": "screenplay.json",
        "propsFile": "props.json",
        "setsFile": "sets.json",
        **story,
    }
    (folder / "story.json").write_text(json.dumps(spec))
    return folder


def test_the_example_loads_and_compiles_against_the_demo_family():
    [story] = load_stories(EXAMPLE_DIR)
    assert story.name == "sunday_tamales"
    assert story.title == "Tamale Sundays" and story.year == 1970
    assert story.review_props == ("tamale_pot", "lumpy_tamale")
    assert story.set_ids() == ["carmen_kitchen_1970"]
    assert check_stories([story]) == []


def test_a_story_folder_can_be_the_directory_itself():
    [story] = load_stories(EXAMPLE_DIR / "sunday_tamales")
    assert story.name == "sunday_tamales"


def test_stories_load_by_folder_name_and_skip_folders_without_story_json(tmp_path):
    write_story(tmp_path, "b_second")
    write_story(tmp_path, "a_first")
    (tmp_path / "notes").mkdir()
    assert [s.name for s in load_stories(tmp_path)] == ["a_first", "b_second"]
    check_stories(load_stories(tmp_path))


def test_a_screenplay_that_does_not_compile_fails_with_the_story_name(tmp_path):
    sp = json.loads(TAMALES.read_text())
    sp["shots"][0]["actions"][1]["to"] = "nowhere_mark"
    write_story(tmp_path, "broken_kitchen", screenplay=sp)
    [story] = load_stories(tmp_path)
    with pytest.raises(ReplayFixtureError) as err:
        check_stories([story])
    assert err.value.story == "broken_kitchen"
    assert "'broken_kitchen'" in str(err.value) and "does not compile" in str(err.value)
    assert "nowhere_mark" in str(err.value)


def test_a_cast_member_outside_the_family_fails(tmp_path):
    sp = json.loads(TAMALES.read_text())
    sp["cast"].append("cousin_nobody")
    write_story(tmp_path, "stranger", screenplay=sp)
    with pytest.raises(ReplayFixtureError, match="'stranger'.*cousin_nobody"):
        check_stories(load_stories(tmp_path))


def test_a_screenplay_without_narration_fails(tmp_path):
    sp = json.loads(TAMALES.read_text())
    for shot in sp["shots"]:
        shot["narration"] = []
    write_story(tmp_path, "silent", screenplay=sp)
    with pytest.raises(ReplayFixtureError, match="'silent'.*no narration"):
        check_stories(load_stories(tmp_path))


def test_a_missing_prop_or_set_fails(tmp_path):
    folder = write_story(tmp_path, "bare")
    (folder / "props.json").write_text("[]")
    (folder / "sets.json").write_text("[]")
    [story] = load_stories(tmp_path)
    with pytest.raises(ReplayFixtureError, match="no prop 'tamale_pot'"):
        validate_story(story, people={p: object() for p in story.screenplay.cast}, props={}, sets={})  # type: ignore[misc]


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"screenplayFile": "missing.json"}, "missing.json not found"),
        ({"reviewProps": ["no_such_prop"]}, "reviewProps not in the screenplay"),
        ({"surprise": 1}, "story.json is not valid"),
        ({"match": {"text": "?!"}}, "story.json is not valid"),
        ({"match": {"text": "fine", "minOverlap": 1.5}}, "story.json is not valid"),
        ({"match": {"text": "fine", "keywords": [" - "]}}, "story.json is not valid"),
    ],
)
def test_malformed_story_files_fail_with_the_story_name(tmp_path, change, message):
    write_story(tmp_path, "odd", **change)
    with pytest.raises(ReplayFixtureError, match=message) as err:
        load_stories(tmp_path)
    assert err.value.story == "odd"


def test_bad_json_names_the_file_and_the_story(tmp_path):
    folder = write_story(tmp_path, "typo")
    (folder / "props.json").write_text("[{]")
    with pytest.raises(ReplayFixtureError, match="'typo'.*props.json: invalid JSON"):
        load_stories(tmp_path)


def test_two_stories_with_the_same_text_are_refused(tmp_path):
    write_story(tmp_path, "one", match={"text": EXAMPLE_TEXT})
    write_story(tmp_path, "two", match={"text": EXAMPLE_TEXT.upper()})
    with pytest.raises(ReplayFixtureError, match="'two'.*same as 'one'"):
        load_stories(tmp_path)


def test_replay_dir_defaults_to_the_private_folder(tmp_path):
    path, explicit = replay_dir({})
    assert path == (Path.home() / "stitch-private" / "replays").resolve() and not explicit
    path, explicit = replay_dir({"STITCH_REPLAY_DIR": str(tmp_path)})
    assert path == tmp_path.resolve() and explicit
