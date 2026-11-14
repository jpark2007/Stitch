"""Contract tests: the shared files agree with each other and with the samples."""

import json
import math
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

import schemas
from screenplay.schema import VOCABULARY, Action, Screenplay, Timeline

REPO = Path(__file__).resolve().parents[2]
SHARED = REPO / "shared"
SAMPLES = REPO / "web" / "public" / "samples"

PUPPET = json.loads((SHARED / "rig" / "puppet.json").read_text())
POSES = json.loads((SHARED / "rig" / "poses.json").read_text())


def load(name: str) -> dict:
    return json.loads((SAMPLES / name).read_text())


@pytest.fixture(scope="module")
def screenplay() -> Screenplay:
    return Screenplay.model_validate(load("hymn_books.screenplay.json"))


@pytest.fixture(scope="module")
def timeline() -> Timeline:
    return Timeline.model_validate(load("hymn_books.timeline.json"))


@pytest.fixture(scope="module")
def memory() -> schemas.MemoryResponse:
    return schemas.MemoryResponse.model_validate(load("hymn_books.memory.json"))


# ------------------------------------------------------------------ samples validate

def test_samples_validate_and_round_trip(screenplay, timeline, memory):
    assert Screenplay.model_validate(screenplay.model_dump(mode="json")) == screenplay
    assert Timeline.model_validate(timeline.model_dump(mode="json")) == timeline
    assert schemas.MemoryResponse.model_validate(memory.model_dump(mode="json")) == memory
    # serialises back to camelCase
    assert "memoryId" in screenplay.model_dump(mode="json")
    assert "defaultNarrator" in timeline.model_dump(mode="json")


def test_memory_sample_embeds_the_same_scene(screenplay, timeline, memory):
    assert memory.memory.screenplay == screenplay
    assert memory.memory.timeline == timeline


def test_unknown_fields_are_rejected():
    bad = load("hymn_books.screenplay.json")
    bad["shots"][0]["actions"][0]["teleport"] = True
    with pytest.raises(ValidationError):
        Screenplay.model_validate(bad)
    bad_timeline = load("hymn_books.timeline.json")
    bad_timeline["script"] = "<b>hi</b>"
    with pytest.raises(ValidationError):
        Timeline.model_validate(bad_timeline)


def test_unknown_verb_is_rejected():
    with pytest.raises(ValidationError):
        Action.model_validate({"t": 0, "who": "dad", "do": "teleport"})


# ------------------------------------------------------------- vocabulary consistency

def test_vocabulary_shape():
    param_names = set(Action.model_json_schema(by_alias=True)["properties"])
    for verb, spec in VOCABULARY["actions"].items():
        assert spec["kind"] in {"movement", "body", "prop", "vehicle", "effect"}, verb
        for p in spec["required"] + spec["optional"]:
            assert p in param_names, f"{verb}: parameter {p!r} is not a field of Action"
    assert set(VOCABULARY["effects"]) == {v for v, s in VOCABULARY["actions"].items() if s["kind"] == "effect"}
    eras = VOCABULARY["eras"]
    assert all(a["to"] + 1 == b["from"] for a, b in zip(eras, eras[1:])), "eras must be contiguous"


def test_sample_actions_satisfy_vocabulary(screenplay):
    for shot in screenplay.shots:
        for action in shot.actions:
            spec = VOCABULARY["actions"][action.do]
            data = action.model_dump(by_alias=True, exclude_none=True)
            for p in spec["required"]:
                assert p in data, f"{action.do} at t={action.t} is missing {p!r}"
            allowed = set(spec["required"]) | set(spec["optional"]) | {"t", "do", "id"}
            assert set(data) <= allowed, f"{action.do} has extra parameters {set(data) - allowed}"
        for cue in shot.camera:
            assert cue.move in VOCABULARY["camera"]


def test_sample_references_resolve(screenplay, memory):
    people = {p.id for p in memory.people}
    props = {p.id for p in memory.props}
    sets = {s.id: s for s in memory.sets}
    assert set(screenplay.cast) <= people
    assert set(screenplay.props) <= props
    assert set(memory.memory.participant_ids) <= people
    for shot in screenplay.shots:
        assert shot.set in sets
        marks = set(sets[shot.set].marks) | set(shot.marks) | set(VOCABULARY["builtinMarks"])
        for who, start in shot.start.items():
            assert who in people and start.at in marks
            assert start.holding is None or start.holding in props
        action_ids = {a.id for a in shot.actions if a.id} | {a.do for a in shot.actions}
        for a in shot.actions:
            assert a.who is None or a.who in people
            assert a.what is None or a.what in props
            if a.do in ("walk_to", "run_to", "enter", "drive_to", "ride_to"):
                assert a.to in marks
            if a.do == "hand_to":
                assert a.to in people
            if a.do == "pose":
                assert a.pose in POSES["poses"]
        narrations = [shot.narration] + [by_shot.get(shot.id, []) for by_shot in (screenplay.narrations or {}).values()]
        for lines in narrations:
            for line in lines:
                assert line.cue is None or line.cue in action_ids
    assert set(screenplay.narrations or {}) <= people
    for e in screenplay.explanations or []:
        kind, _, ident = e.target.partition(":")
        assert (kind == "puppet" and ident in people) or (kind == "prop" and ident in props)


# ------------------------------------------------------------------------ rig and poses

def test_rig_is_a_tree_and_stands_one_unit_tall():
    bones = PUPPET["bones"]
    for name, bone in bones.items():
        assert bone["parent"] == PUPPET["root"] or bone["parent"] in bones, name
        assert math.isclose(math.hypot(*bone["dir"]), 1.0)
    assert set(PUPPET["drawOrder"]) == set(bones)
    for socket in PUPPET["sockets"].values():
        assert socket["bone"] in bones
    for chain in PUPPET["ik"].values():
        assert bones[chain["lower"]]["parent"] == chain["upper"]
        assert chain["socket"] in PUPPET["sockets"]
    legs = bones["leg_l.upper"]["length"] + bones["leg_l.lower"]["length"]
    assert math.isclose(legs, PUPPET["hipHeight"])
    assert math.isclose(PUPPET["hipHeight"] + bones["torso"]["length"] + bones["head"]["length"], PUPPET["height"])


def test_poses_only_use_real_bones():
    bones = set(PUPPET["bones"])
    required = {"stand", "sit", "kneel", "wave", "point", "reach", "hug", "slumped", "arms_raised", "bow_head", "hold"}
    assert required <= set(POSES["poses"])
    for name, pose in POSES["poses"].items():
        assert set(pose) - {"rootOffset"} <= bones, name
    for name, cycle in POSES["cycles"].items():
        assert cycle["strideLength"] > 0
        assert [f["phase"] for f in cycle["frames"]] == sorted(f["phase"] for f in cycle["frames"])
        for frame in cycle["frames"]:
            assert set(frame) - {"phase"} <= bones, name


# ----------------------------------------------------------------------------- timeline

PUPPET_CHANNELS = {"root.position", "root.facing"} | {f"{b}.rot" for b in PUPPET["bones"]}
PROP_CHANNELS = {"parent", "position", "rotation", "state"}
CAMERA_CHANNELS = {"position", "lookAt", "fov"}


def test_timeline_tracks_follow_the_channel_grammar(timeline, memory):
    people = {p.id for p in memory.people}
    props = {p.id for p in memory.props}
    sets = {s.id: s for s in memory.sets}
    seen = set()
    for track in timeline.tracks:
        assert (track.target, track.channel) not in seen, "one track per target+channel"
        seen.add((track.target, track.channel))
        times = [k[0] for k in track.keys]
        assert times == sorted(times) and times[0] >= 0 and times[-1] <= timeline.duration
        kind, _, ident = track.target.partition(":")
        if kind == "puppet":
            assert ident in people and track.channel in PUPPET_CHANNELS, track
        elif kind == "prop":
            assert ident in props
            assert track.channel in PROP_CHANNELS or track.channel.startswith("part."), track
        elif kind == "set":
            set_id, _, layer = ident.rpartition(".layer")
            assert set_id in sets and int(layer) < len(sets[set_id].layers) and track.channel == "fold"
        else:
            assert track.target == "camera" and track.channel in CAMERA_CHANNELS, track
        if track.channel == "parent":
            for _, value in track.keys:
                if value != "ground":
                    owner, _, socket = value.partition(".")
                    okind, _, oid = owner.partition(":")
                    assert okind == "puppet" and oid in people and socket in PUPPET["sockets"], value


def test_prop_is_never_in_two_hands_and_swaps_match_the_screenplay(timeline, screenplay):
    parent = next(t for t in timeline.tracks if t.target == "prop:hymn_books" and t.channel == "parent")
    assert [v for _, v in parent.keys] == ["puppet:mom.hand_r", "ground", "puppet:dad.hand_r", "puppet:mom.hand_r"]
    shot = timeline.shots[0]
    drop = next(a for a in screenplay.shots[0].actions if a.do == "drop")
    assert math.isclose(parent.keys[1][0], shot.action_start + drop.t)


def test_cued_words_land_on_their_actions(timeline, screenplay):
    shot = timeline.shots[0]
    actions = {a.id: a for a in screenplay.shots[0].actions if a.id}
    assert set(timeline.narrations) == {"dad", "mom"}
    for track in timeline.narrations.values():
        for line in track.lines:
            if line.cue:
                word = next(w for w in line.words if w.word == line.cue_word)
                assert math.isclose(word.t, shot.action_start + actions[line.cue].t), line.text


def test_sample_generator_reproduces_the_committed_files(tmp_path):
    before = {p.name: p.read_text() for p in SAMPLES.glob("hymn_books.*.json")}
    subprocess.run([sys.executable, str(Path(__file__).with_name("gen_hymn_books_sample.py"))], check=True, capture_output=True)
    after = {p.name: p.read_text() for p in SAMPLES.glob("hymn_books.*.json")}
    assert before == after


# -------------------------------------------------------------------------- API models

def test_no_response_model_can_carry_a_password():
    for name in schemas.__all__:
        model = getattr(schemas, name)
        if not hasattr(model, "model_fields") or name in {"SignupRequest", "LoginRequest"}:
            continue
        for field in model.model_fields:
            assert "password" not in field.lower(), f"{name}.{field}"
    with pytest.raises(ValidationError):
        schemas.User.model_validate({"id": "u1", "email": "a@b.c", "name": "A", "passwordHash": "x"})


def test_every_run_event_has_a_model():
    documented = {"plan", "subagent_start", "subagent_end", "question", "props_found", "compile_error", "frames", "scene_ready", "done", "error"}
    assert documented <= set(schemas.RUN_EVENTS)


def test_design_additions_are_present_and_optional(timeline):
    slim = timeline.model_dump(mode="json")
    for optional in ("narrations", "interactions", "explanations", "defaultNarrator"):
        slim.pop(optional)
    Timeline.model_validate(slim)
    assert timeline.interactions[0].drives.type == "scrub"
    assert timeline.shots[0].page_text
    assert "era" in schemas.Memory.model_fields and "era" in schemas.MomentSummary.model_fields
    assert {"people", "moments"} == set(schemas.ThreadsResponse.model_fields)


def test_typescript_mirror_is_up_to_date():
    result = subprocess.run(
        [sys.executable, str(SHARED / "gen_types.py"), "--check"], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stdout + result.stderr
