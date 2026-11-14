"""FakeCompiler: compiles the hymn_books sample to a valid timeline, and rejects bad screenplays
with exact errors."""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path

import pytest

import schemas
from agent.fake_compiler import FakeCompiler
from agent.tools.compile import SceneInputs
from screenplay.schema import CompileResult, Screenplay, Timeline

REPO = Path(__file__).resolve().parents[3]
SAMPLES = REPO / "web" / "public" / "samples"
PUPPET = json.loads((REPO / "shared" / "rig" / "puppet.json").read_text())


def _load(name: str) -> dict:
    return json.loads((SAMPLES / name).read_text())


@pytest.fixture(scope="module")
def scene() -> SceneInputs:
    mem = _load("hymn_books.memory.json")
    return SceneInputs(
        sets={s["id"]: schemas.SetDef.model_validate(s) for s in mem["sets"]},
        props={p["id"]: schemas.Prop.model_validate(p) for p in mem["props"]},
        people={p["id"]: schemas.Person.model_validate(p) for p in mem["people"]},
    )


@pytest.fixture(scope="module")
def sample() -> Screenplay:
    return Screenplay.model_validate(_load("hymn_books.screenplay.json"))


@pytest.fixture(scope="module")
def result(sample, scene) -> CompileResult:
    return FakeCompiler()(sample, scene)


# --------------------------------------------------------------------- the sample


def test_hymn_books_compiles_to_a_valid_timeline(result):
    assert result.ok, [e.message for e in result.errors]
    assert result.errors == []
    tl = result.timeline
    assert tl is not None
    again = Timeline.model_validate(tl.model_dump(mode="json", by_alias=True))
    assert again == tl
    assert tl.duration == tl.shots[-1].end


def test_shot_timing_and_copied_fields(result, sample):
    tl = result.timeline
    (shot,) = tl.shots
    assert shot.id == "s1" and shot.set == "church_steps_1988"
    assert shot.start == 0 and shot.action_start == pytest.approx(1.2)
    # end = action_start + max(last action t + 1.5, last narration t + 2.5, motion)
    assert shot.end >= shot.action_start + max(7.7 + 1.5, 6.35 + 2.5) - 1e-9
    assert shot.page_text == sample.shots[0].page_text
    assert tl.explanations == sample.explanations
    (inter,) = tl.interactions
    assert inter.drives.from_ == pytest.approx(3.25 + 1.2)
    assert inter.drives.to == pytest.approx(3.55 + 1.2)
    assert inter.drives.targets == ["prop:hymn_books", "puppet:mom"]
    # the sample screenplay itself is untouched
    assert sample.interactions[0].drives.from_ == 3.25


def test_narrations_one_track_per_teller(result, sample):
    tl = result.timeline
    assert set(tl.narrations) == {"dad", "mom"}
    assert tl.default_narrator == "dad"
    assert tl.captions == tl.narrations["dad"].captions
    dad = tl.narrations["dad"]
    assert [ln.t for ln in dad.lines] == pytest.approx([1.2 + ln.t for ln in sample.shots[0].narration])
    assert [c.text for c in dad.captions] == [ln.text for ln in sample.shots[0].narration]
    mom = tl.narrations["mom"]
    assert mom.author_id == "mom"
    assert [ln.t for ln in mom.lines] == pytest.approx([1.2 + ln.t for ln in sample.narrations["mom"]["s1"]])
    assert mom.lines[1].cue == "drop" and mom.lines[1].cue_word == "everywhere"


def _channel_ok(target: str, channel: str, value, cast, props, sets) -> bool:
    bones = set(PUPPET["bones"])
    kind, _, ident = target.partition(":")
    if target == "camera":
        if channel in ("position", "lookAt"):
            return isinstance(value, list) and len(value) == 3
        return channel == "fov" and isinstance(value, float)
    if kind == "puppet" and ident in cast:
        if channel == "root.position":
            return isinstance(value, list) and len(value) == 3
        if channel == "root.facing":
            return value in (1, -1)
        m = re.fullmatch(r"(.+)\.rot", channel)
        return bool(m) and m.group(1) in bones and isinstance(value, float)
    if kind == "prop" and ident in props:
        if channel == "parent":
            hands = "|".join(map(re.escape, cast))
            return value == "ground" or bool(
                re.fullmatch(rf"puppet:({hands})\.(hand_l|hand_r|head)", value) or re.fullmatch(r"prop:[^.]+\.\w+", value)
            )
        if channel == "position":
            return isinstance(value, list) and len(value) == 3
        if channel == "rotation" or re.fullmatch(r"part\.\w+\.rot", channel):
            return isinstance(value, float)
        return channel == "state" and isinstance(value, str)
    m = re.fullmatch(r"set:([^.]+)\.layer(\d+)", target)
    if m and m.group(1) in sets:
        return channel == "fold" and int(m.group(2)) < len(sets[m.group(1)].layers) and 0.0 <= value <= 1.0
    return False


def test_every_track_follows_the_channel_grammar(result, sample, scene):
    tl = result.timeline
    pairs = [(tr.target, tr.channel) for tr in tl.tracks]
    assert len(pairs) == len(set(pairs)), "one track per target + channel"
    for tr in tl.tracks:
        times = [k[0] for k in tr.keys]
        assert times == sorted(times) and len(times) == len(set(times)), (tr.target, tr.channel)
        for _, value in tr.keys:
            assert _channel_ok(tr.target, tr.channel, value, sample.cast, sample.props, scene.sets), (
                tr.target, tr.channel, value,
            )
    targets = {tr.target for tr in tl.tracks}
    assert {"puppet:dad", "puppet:mom", "prop:hymn_books", "camera"} <= targets
    assert {f"set:church_steps_1988.layer{n}" for n in range(3)} <= targets


def _track(tl: Timeline, target: str, channel: str):
    return next(tr for tr in tl.tracks if tr.target == target and tr.channel == channel)


def test_prop_parent_and_position_keys_are_paired(result):
    tl = result.timeline
    for prop_target in {tr.target for tr in tl.tracks if tr.target.startswith("prop:")}:
        parent = _track(tl, prop_target, "parent")
        position = _track(tl, prop_target, "position")
        pos_at = dict((t, v) for t, v in position.keys)
        for t, value in parent.keys:
            assert t in pos_at, f"{prop_target}: no position key at parent key t={t}"
            if value != "ground":
                assert pos_at[t] == [0.0, 0.0, 0.0]


def test_hymn_books_pass_hand_to_hand(result):
    """Mom holds, drops to the ground, Dad picks up, hands straight back to Mom: one parent at a time."""
    tl = result.timeline
    parent = _track(tl, "prop:hymn_books", "parent")
    values = [v for _, v in parent.keys]
    assert values == ["puppet:mom.hand_r", "ground", "puppet:dad.hand_r", "puppet:mom.hand_r"]
    times = [t for t, _ in parent.keys]
    assert times[1] == pytest.approx(1.2 + 3.25)  # the drop
    assert times[2] > 1.2 + 5.5 - 1e-9  # picked up at or after the pick_up starts
    assert times[3] > times[2]  # never in two hands: dad's grip ends exactly when mom's begins
    dropped_on_ground = dict(_track(tl, "prop:hymn_books", "position").keys)[times[1]]
    # Decision 6: beside mom's feet (steps_top x=0.2, facing left -> 0.12)
    assert dropped_on_ground[0] == pytest.approx(0.12)


def test_marks_become_positions(result):
    tl = result.timeline
    dad = _track(tl, "puppet:dad", "root.position")
    assert dad.keys[0][1] == pytest.approx([-1.3, 0.0, 0.5])  # offstage_left
    assert [-0.6, 0.0, 0.5] in [v for _, v in dad.keys]  # steps_left
    assert dad.keys[-1][1] == pytest.approx([0.0, 0.0, 0.5])  # steps_foot
    mom = _track(tl, "puppet:mom", "root.position")
    assert mom.keys[0][1] == pytest.approx([0.2, 0.3, 0.5])  # steps_top
    assert _track(tl, "puppet:mom", "root.facing").keys[0][1] == -1
    assert _track(tl, "puppet:mom", "head.rot").keys[-1][1] == pytest.approx(-0.4)  # bow_head


def test_output_is_deterministic(sample, scene):
    a = FakeCompiler()(sample, scene).model_dump_json(by_alias=True)
    b = FakeCompiler()(sample, scene).model_dump_json(by_alias=True)
    assert a == b


def test_multi_shot_carries_holding_and_folds_sets(sample, scene):
    data = sample.model_dump(mode="json", by_alias=True)
    s2 = {
        "id": "s2",
        "set": "church_steps_1988",
        "start": {"dad": {"at": "steps_foot"}, "mom": {"at": "steps_top"}},
        "actions": [{"t": 0.5, "who": "mom", "do": "hand_to", "what": "hymn_books", "to": "dad"}],
        "narration": [{"t": 0, "text": "Later that day.", "source": "telling:dad:s5"}],
    }
    data["shots"].append(s2)
    res = FakeCompiler()(Screenplay.model_validate(data), scene)
    assert res.ok, [e.message for e in res.errors]
    tl = res.timeline
    assert [s.id for s in tl.shots] == ["s1", "s2"]
    assert tl.shots[1].start == tl.shots[0].end
    fold = _track(tl, "set:church_steps_1988.layer0", "fold")
    assert [v for _, v in fold.keys] == [0.0, 1.0, 1.0, 0.0, 1.0]
    values = [v for _, v in _track(tl, "prop:hymn_books", "parent").keys]
    assert values[-1] == "puppet:dad.hand_r"


def test_effects_become_timeline_effects(sample, scene):
    data = sample.model_dump(mode="json", by_alias=True)
    data["shots"][0]["actions"].append({"t": 8, "do": "confetti", "at": "steps_top", "dur": 2})
    res = FakeCompiler()(Screenplay.model_validate(data), scene)
    assert res.ok, [e.message for e in res.errors]
    (fx,) = res.timeline.effects
    assert fx.kind == "confetti" and fx.t == pytest.approx(9.2) and fx.at == pytest.approx([0.2, 0.3, 0.5]) and fx.dur == 2


# ------------------------------------------------------------------------- errors


BASE = {
    "memoryId": "m1",
    "cast": ["dad", "mom"],
    "props": ["hymn_books"],
    "shots": [
        {
            "id": "s1",
            "set": "church_steps_1988",
            "start": {
                "dad": {"at": "steps_left", "facing": "right"},
                "mom": {"at": "steps_top", "holding": "hymn_books", "facing": "left"},
            },
            "actions": [],
        }
    ],
}


def _compile(scene, actions=None, **shot_overrides) -> CompileResult:
    data = copy.deepcopy(BASE)
    if actions is not None:
        data["shots"][0]["actions"] = actions
    data["shots"][0].update(shot_overrides)
    return FakeCompiler()(Screenplay.model_validate(data), scene)


def _only_error(res: CompileResult):
    assert not res.ok and res.timeline is None
    assert len(res.errors) == 1, [e.message for e in res.errors]
    return res.errors[0]


def test_base_screenplay_compiles(scene):
    res = _compile(scene, [{"t": 1, "who": "mom", "do": "hand_to", "what": "hymn_books", "to": "dad"}])
    assert res.ok, [e.message for e in res.errors]


def test_hand_to_without_holding_names_the_holder(scene):
    err = _only_error(_compile(scene, [{"t": 5, "who": "dad", "do": "hand_to", "what": "hymn_books", "to": "mom"}]))
    assert err.code == "not_holding"
    assert err.message == 'shot s1, t=5: dad cannot hand_to "hymn_books"; mom is holding it'
    assert (err.shot, err.t, err.who, err.do) == ("s1", 5, "dad", "hand_to")


def test_hand_to_when_nobody_holds_it(scene):
    err = _only_error(
        _compile(
            scene,
            [
                {"t": 1, "who": "mom", "do": "drop", "what": "hymn_books"},
                {"t": 5, "who": "dad", "do": "hand_to", "what": "hymn_books", "to": "mom"},
            ],
        )
    )
    assert err.message == 'shot s1, t=5: dad cannot hand_to "hymn_books"; nobody is holding it'


def test_holding_is_simulated_in_time_order(scene):
    # listed out of order: the drop at t=1 happens before the hand_to at t=2
    err = _only_error(
        _compile(
            scene,
            [
                {"t": 2, "who": "mom", "do": "hand_to", "what": "hymn_books", "to": "dad"},
                {"t": 1, "who": "mom", "do": "drop", "what": "hymn_books"},
            ],
        )
    )
    assert err.message == 'shot s1, t=2: mom cannot hand_to "hymn_books"; nobody is holding it'


def test_pick_up_of_a_held_prop(scene):
    err = _only_error(_compile(scene, [{"t": 1.5, "who": "dad", "do": "pick_up", "what": "hymn_books"}]))
    assert err.code == "already_held"
    assert err.message == 'shot s1, t=1.5: dad cannot pick_up "hymn_books"; mom is holding it'


def test_unknown_mark(scene):
    err = _only_error(_compile(scene, [{"t": 1, "who": "dad", "do": "walk_to", "to": "the_moon"}]))
    assert err.code == "unknown_mark"
    assert err.message == 'shot s1, t=1: unknown mark "the_moon"'


def test_shot_marks_and_builtin_marks_are_known(scene):
    res = _compile(
        scene,
        [{"t": 0, "who": "dad", "do": "walk_to", "to": "pew"}, {"t": 4, "who": "dad", "do": "exit", "to": "offstage_right"}],
        marks={"pew": [0.3, 0.0]},
    )
    assert res.ok, [e.message for e in res.errors]


def test_unknown_person(scene):
    err = _only_error(_compile(scene, [{"t": 2, "who": "grandpa", "do": "wave"}]))
    assert err.code == "unknown_person"
    assert err.message == 'shot s1, t=2: unknown person "grandpa"'
    err = _only_error(_compile(scene, [{"t": 2, "who": "mom", "do": "hand_to", "what": "hymn_books", "to": "grandpa"}]))
    assert err.message == 'shot s1, t=2: unknown person "grandpa"'


def test_unknown_prop(scene):
    err = _only_error(_compile(scene, [{"t": 3, "who": "dad", "do": "pick_up", "what": "umbrella"}]))
    assert err.code == "unknown_prop"
    assert err.message == 'shot s1, t=3: unknown prop "umbrella"'


def test_prop_must_be_in_scene_props(scene):
    data = copy.deepcopy(BASE)
    data["props"].append("umbrella")
    data["shots"][0]["actions"] = [{"t": 3, "who": "dad", "do": "pick_up", "what": "umbrella"}]
    err = _only_error(FakeCompiler()(Screenplay.model_validate(data), scene))
    assert err.code == "unknown_prop"


def test_unknown_pose(scene):
    err = _only_error(_compile(scene, [{"t": 7.7, "who": "mom", "do": "pose", "pose": "dab"}]))
    assert err.code == "unknown_pose"
    assert err.message == 'shot s1, t=7.7: unknown pose "dab"'


def test_missing_required_param(scene):
    err = _only_error(_compile(scene, [{"t": 1, "who": "mom", "do": "hand_to", "what": "hymn_books"}]))
    assert err.code == "missing_param"
    assert err.message == 'shot s1, t=1: hand_to needs "to"'


def test_unexpected_param(scene):
    err = _only_error(_compile(scene, [{"t": 1, "who": "dad", "do": "walk_to", "to": "steps_foot", "what": "hymn_books"}]))
    assert err.code == "unexpected_param"
    assert err.message == 'shot s1, t=1: walk_to does not take "what"'
    assert (err.who, err.do) == ("dad", "walk_to")


def test_unknown_set(scene):
    err = _only_error(_compile(scene, set="the_moon", marks={"steps_left": [0.2, 0], "steps_top": [0.6, 0.3]}))
    assert err.code == "unknown_set"
    assert err.message == 'shot s1, t=0: unknown set "the_moon"'


def test_unknown_cue(scene):
    res = _compile(scene, narration=[{"t": 0, "text": "She dropped them.", "cue": "fumble"}])
    err = _only_error(res)
    assert err.code == "unknown_cue"


def test_holding_carries_between_shots(scene):
    data = copy.deepcopy(BASE)
    data["shots"][0]["actions"] = [{"t": 1, "who": "mom", "do": "drop", "what": "hymn_books"}]
    data["shots"].append(
        {
            "id": "s2",
            "set": "church_steps_1988",
            "start": {"dad": {"at": "steps_foot"}, "mom": {"at": "steps_top"}},
            "actions": [{"t": 2, "who": "mom", "do": "hand_to", "what": "hymn_books", "to": "dad"}],
        }
    )
    err = _only_error(FakeCompiler()(Screenplay.model_validate(data), scene))
    assert err.message == 'shot s2, t=2: mom cannot hand_to "hymn_books"; nobody is holding it'


def test_all_errors_are_reported_together(scene):
    res = _compile(
        scene,
        [
            {"t": 1, "who": "dad", "do": "walk_to", "to": "the_moon"},
            {"t": 2, "who": "mom", "do": "pose", "pose": "dab"},
        ],
    )
    assert [e.code for e in res.errors] == ["unknown_mark", "unknown_pose"]
