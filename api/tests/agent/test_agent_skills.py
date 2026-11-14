"""The studio's skills: format the deepagents skills middleware accepts, and content that matches
the shared contracts (vocabulary, poses, reference screenplay) so the director cannot drift from
what the compiler allows."""

import json
import re
from pathlib import Path

import pytest
from deepagents.backends.filesystem import FilesystemBackend
from deepagents.middleware.skills import _list_skills, _parse_skill_metadata, _validate_skill_name

from screenplay.schema import Screenplay

REPO = Path(__file__).resolve().parents[3]
SKILLS = REPO / "api" / "agent" / "skills"
SHARED = REPO / "shared"
SAMPLE = REPO / "web" / "public" / "samples" / "hymn_books.screenplay.json"
SKILL_NAMES = ["popup-style", "storyboarding", "directing"]

VOCAB = json.loads((SHARED / "vocabulary.json").read_text())
POSES = json.loads((SHARED / "rig" / "poses.json").read_text())


def skill_text(name: str) -> str:
    return (SKILLS / name / "SKILL.md").read_text()


def body(name: str) -> str:
    """SKILL.md without its frontmatter."""
    return skill_text(name).split("\n---\n", 1)[1]


# ------------------------------------------------------------------------- format

@pytest.mark.parametrize("name", SKILL_NAMES)
def test_skill_frontmatter_parses_with_deepagents(name):
    path = SKILLS / name / "SKILL.md"
    meta = _parse_skill_metadata(path.read_text(), str(path), name)
    assert meta is not None, f"{name}: deepagents could not parse the frontmatter"
    assert meta["name"] == name
    assert _validate_skill_name(meta["name"], name) == (True, "")
    # within the spec limit, so nothing is truncated from what the model sees
    assert 0 < len(meta["description"]) <= 1024


def test_skills_load_through_the_middleware_backend():
    """Exactly how the studio loads them: a backend source listing skill directories."""
    backend = FilesystemBackend(root_dir=SKILLS, virtual_mode=True)
    found = {s["name"]: s for s in _list_skills(backend, "/")}
    assert set(SKILL_NAMES) <= set(found)
    for name in SKILL_NAMES:
        assert found[name]["path"].endswith(f"{name}/SKILL.md")


@pytest.mark.parametrize("name", SKILL_NAMES)
def test_skill_fits_one_read(name):
    """The middleware tells the model to read_file with limit=1000; keep each skill well inside it."""
    text = skill_text(name)
    assert len(text.splitlines()) < 1000
    assert max(len(line) for line in text.splitlines()) < 5000


# ------------------------------------------------------------------ directing: vocabulary

def table_rows(text: str) -> dict[str, list[str]]:
    """Markdown table rows keyed by their first cell's backticked name."""
    rows = {}
    for line in text.splitlines():
        m = re.match(r"^\|\s*`([a-z_]+)`\s*\|(.*)\|\s*$", line)
        if m and m.group(1) not in rows:
            rows[m.group(1)] = [c.strip() for c in m.group(2).split("|")]
    return rows


def ticked(cell: str) -> set[str]:
    return set(re.findall(r"`([a-zA-Z_]+)`", cell))


def test_directing_teaches_every_verb_with_exact_params():
    text = body("directing")
    rows = table_rows(text)
    for verb, spec in VOCAB["actions"].items():
        assert verb in rows, f"verb {verb} missing from the directing vocabulary table"
        kind, required, optional = rows[verb][:3]
        assert kind == spec["kind"], verb
        assert ticked(required) == set(spec["required"]), f"{verb} required params"
        assert ticked(optional) == set(spec["optional"]), f"{verb} optional params"
    # and no verb the compiler does not know
    action_section = text.split("## Action vocabulary", 1)[1].split("### Poses", 1)[0]
    assert set(table_rows(action_section)) == set(VOCAB["actions"])


def test_directing_teaches_every_camera_move_with_exact_params():
    rows = table_rows(body("directing").split("### Camera moves", 1)[1])
    for move, spec in VOCAB["camera"].items():
        assert move in rows, move
        assert ticked(rows[move][0]) == set(spec["required"]), move
        assert ticked(rows[move][1]) == set(spec["optional"]), move


def test_directing_lists_effects_times_transitions_and_poses():
    text = body("directing")
    for word in VOCAB["effects"] + VOCAB["timesOfDay"] + VOCAB["transitions"] + VOCAB["interactionKinds"]:
        assert f"`{word}`" in text, word
    pose_rows = table_rows(text.split("### Poses", 1)[1].split("### Camera moves", 1)[0])
    assert set(pose_rows) == set(POSES["poses"])


def test_directing_covers_the_contract_conventions():
    text = body("directing")
    for word in [
        "pageText", "interactions", "explanations", "narrations", "cue", "cueWord",
        "offstage_left", "offstage_right", "/screenplay.json", "compile_screenplay",
        "hymn_books.screenplay.json", "z = 0.5", "x = 2u − 1", "1.2 s", "hand_r",
        "beside the dropper's feet", "scrub", "prop_state", "prop_part",
        "puppet:<personId>", "prop:<propId>", "camelCase",
    ]:
        assert word in text, word
    assert re.search(r"at most 3\s+rounds", text, re.IGNORECASE)


def test_directing_teaches_what_the_compiler_simulates():
    """The rules the real compiler checks beyond the vocabulary: one action at a time, on/off
    stage, reach, mark names, cue retiming and narration spacing, with the codes it reports."""
    text = body("directing")
    for code in ("busy", "offstage", "already_on_stage", "out_of_reach", "not_holding", "already_held",
                 "hands_full", "ambiguous_name", "lines_overlap", "cue_before_start", "cue_conflict",
                 "cue_word_missing", "duplicate_telling", "explanation_unknown_target", "unsupported_verb"):
        assert f"`{code}`" in text, code
    durations = text.split("### How long actions take", 1)[1].split("\n## ", 1)[0]
    timed = {verb for line in durations.splitlines() if line.startswith("| `")
             for verb in re.findall(r"`([a-z_]+)`", line.split("|")[1])}
    assert set(VOCAB["actions"]) - set(VOCAB["effects"]) <= timed, "every non-effect verb has a duration"
    assert "0.25 s per word" in text
    assert "0.15 in u" in text


def test_director_prompt_states_the_compiler_rules():
    text = (REPO / "api" / "agent" / "prompts" / "director.md").read_text()
    for word in ("get_person", "compile_screenplay", "/screenplay.json", "`busy`", "`out_of_reach`",
                 "`enter`", "snake_case", "0.25 s per word", "Stop after round 3"):
        assert word in text, word
    assert '"prop": "<' not in text and '"part": "<' not in text  # drives examples are concrete


# ------------------------------------------------------------- directing: worked example

def test_worked_example_is_a_byte_identical_copy_of_the_sample():
    copy = SKILLS / "directing" / "hymn_books.screenplay.json"
    assert copy.read_bytes() == SAMPLE.read_bytes()


def test_worked_example_validates_and_matches_the_walkthrough():
    sp = Screenplay.model_validate_json((SKILLS / "directing" / "hymn_books.screenplay.json").read_bytes())
    text = body("directing")
    shot = sp.shots[0]
    for action in shot.actions:
        assert f'"t": {action.t:g}' in text and f'"do": "{action.do}"' in text, action
    for line in shot.narration + sp.narrations["mom"]["s1"]:
        assert line.source in text and line.text.split()[1] in text
    assert shot.page_text in text.replace("\n  ", " ")
    for e in sp.explanations:
        assert e.target in text


# --------------------------------------------------------------- popup-style, storyboarding

def test_popup_style_points_to_the_locked_prompt_and_size_classes():
    text = body("popup-style")
    assert "style_prompt.txt" in text
    assert re.search(r"never restate", text, re.IGNORECASE)
    rows = table_rows(text)
    for size, height in VOCAB["sizes"].items():
        assert size in rows, size
        assert float(rows[size][0]) == height, size
    for layer in ("Back", "Middle", "Front"):
        assert f"| {layer} |" in text


def test_storyboarding_documents_shotlist_and_sources():
    text = body("storyboarding")
    assert "/shotlist.md" in text
    assert "| shot id | set/place | who | what happens | source |" in text
    assert "telling:<authorId>:s<n>" in text
    assert "1-based" in text
    for word in ("pageText", "1 to 4", "One place per shot", "second telling"):
        assert word.lower() in text.lower(), word
    # the example sources it cites must match the reference sample's numbering
    sample = json.loads(SAMPLE.read_text())
    assert sample["shots"][0]["narration"][1]["source"] == "telling:dad:s3"
    assert "telling:dad:s3" in text
