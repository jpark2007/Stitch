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
                "dad": {"at": "steps_foot", "facing": "right"},
                "mom": {"at": "steps_top", "holding": "hymn_books", "facing": "left"},
            },
            "actions": [],
        }
    ],
}
MARKS_LIST = "the marks are offstage_left, offstage_right, steps_left, steps_foot, steps_top, curb"


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
    assert err.message == f'shot s1, t=1: dad cannot walk_to; "the_moon" is not a mark in this shot; {MARKS_LIST}'


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
    assert err.message == 'shot s1, t=2: grandpa cannot wave; "grandpa" is not in the cast; the cast is dad, mom'
    err = _only_error(_compile(scene, [{"t": 2, "who": "mom", "do": "hand_to", "what": "hymn_books", "to": "grandpa"}]))
    assert err.message == ('shot s1, t=2: mom cannot hand_to "hymn_books"; "grandpa" is not in the cast; '
                           "the cast is dad, mom")


def test_unknown_prop(scene):
    err = _only_error(_compile(scene, [{"t": 3, "who": "dad", "do": "pick_up", "what": "umbrella"}]))
    assert err.code == "unknown_prop"
    assert err.message == ('shot s1, t=3: dad cannot pick_up "umbrella"; "umbrella" is not in the '
                           "screenplay's props; the props are hymn_books")


def test_prop_must_be_in_scene_props(scene):
    data = copy.deepcopy(BASE)
    data["props"].append("umbrella")
    data["shots"][0]["actions"] = [{"t": 3, "who": "dad", "do": "pick_up", "what": "umbrella"}]
    err = _only_error(FakeCompiler()(Screenplay.model_validate(data), scene))
    assert err.code == "unknown_prop"


def test_unknown_pose(scene):
    err = _only_error(_compile(scene, [{"t": 7.7, "who": "mom", "do": "pose", "pose": "dab"}]))
    assert err.code == "unknown_pose"
    assert err.message.startswith('shot s1, t=7.7: mom cannot pose; "dab" is not a pose; poses are stand, hold,')


def test_missing_required_param(scene):
    err = _only_error(_compile(scene, [{"t": 1, "who": "mom", "do": "hand_to", "what": "hymn_books"}]))
    assert err.code == "missing_param"
    assert err.message == 'shot s1, t=1: mom cannot hand_to "hymn_books"; hand_to requires who, what, to; "to" is missing'


def test_unexpected_param(scene):
    err = _only_error(_compile(scene, [{"t": 1, "who": "dad", "do": "walk_to", "to": "steps_left", "what": "hymn_books"}]))
    assert err.code == "unexpected_param"
    assert err.message == 'shot s1, t=1: dad cannot walk_to "hymn_books"; walk_to takes who, to; "what" is not one of them'
    assert (err.who, err.do) == ("dad", "walk_to")


def test_unknown_set(scene):
    err = _only_error(_compile(scene, set="the_moon", marks={"steps_left": [0.2, 0], "steps_top": [0.6, 0.3]}))
    assert err.code == "unknown_set"
    assert err.message == 'shot s1: set "the_moon" is not a known set; known sets: church_steps_1988'
    assert (err.shot, err.t) == ("s1", None)


def test_unknown_cue(scene):
    res = _compile(scene, narration=[{"t": 0, "text": "She dropped them.", "source": "telling:dad:s1", "cue": "fumble"}])
    err = _only_error(res)
    assert err.code == "cue_unknown_action"
    assert err.message.startswith("shot s1, dad's narration line 0: cue \"fumble\" matches no action")


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


# ------------------------------------------------------- what the real compiler enforces


def test_one_action_at_a_time_per_puppet(scene):
    """busy: an action that starts before the same puppet's previous one ends (walks take
    distance / 0.35 s; steps_foot -> curb is 0.8 units, so 2.29 s)."""
    err = _only_error(_compile(scene, [
        {"t": 0, "who": "dad", "do": "walk_to", "to": "curb"},
        {"t": 1, "who": "dad", "do": "wave"},
    ]))
    assert err.code == "busy"
    assert err.message == "shot s1, t=1: dad cannot wave; dad is still walking until t=2.29"
    ok = _compile(scene, [
        {"t": 0, "who": "dad", "do": "walk_to", "to": "curb"},
        {"t": 2.3, "who": "dad", "do": "wave"},
    ])
    assert ok.ok, [e.message for e in ok.errors]


def test_hand_to_occupies_the_receiver_too(scene):
    err = _only_error(_compile(scene, [
        {"t": 0, "who": "mom", "do": "hand_to", "what": "hymn_books", "to": "dad"},
        {"t": 0.5, "who": "dad", "do": "wave"},
    ]))
    assert err.code == "busy"
    assert err.message == "shot s1, t=0.5: dad cannot wave; dad is still taking hymn_books until t=0.9"


def test_hand_to_needs_the_hands_to_meet(scene):
    """No auto-walk for hand_to: out_of_reach when the giver and receiver stand too far apart."""
    data = copy.deepcopy(BASE)
    data["shots"][0]["start"]["dad"]["at"] = "steps_left"
    data["shots"][0]["actions"] = [{"t": 1, "who": "mom", "do": "hand_to", "what": "hymn_books", "to": "dad"}]
    err = _only_error(FakeCompiler()(Screenplay.model_validate(data), scene))
    assert err.code == "out_of_reach"
    assert err.message == ('shot s1, t=1: mom cannot hand_to "hymn_books"; dad\'s arm_r cannot reach: '
                           "target is 0.61 from the shoulder, reach is 0.19")
    # side by side on the same ground, 0.4 apart (0.2 in u), is still within reach
    data["shots"][0]["start"] = {"dad": {"at": "a", "facing": "right"},
                                 "mom": {"at": "b", "holding": "hymn_books", "facing": "left"}}
    data["shots"][0]["marks"] = {"a": [0.3, 0.0], "b": [0.5, 0.0]}
    assert FakeCompiler()(Screenplay.model_validate(data), scene).ok


def test_pick_up_and_hug_walk_up_first(scene):
    res = _compile(scene, [
        {"t": 0, "who": "mom", "do": "drop", "what": "hymn_books"},
        {"t": 0.5, "who": "dad", "do": "walk_to", "to": "curb"},
        {"t": 3, "who": "dad", "do": "pick_up", "what": "hymn_books"},
        {"t": 7, "who": "mom", "do": "hug", "target": "dad"},
    ])
    assert res.ok, [e.message for e in res.errors]


def test_enter_and_offstage(scene):
    err = _only_error(_compile(scene, [{"t": 0, "who": "dad", "do": "enter", "to": "steps_left"}]))
    assert (err.code, err.message) == ("already_on_stage",
                                       "shot s1, t=0: dad cannot enter; dad is already on stage; use walk_to")
    data = copy.deepcopy(BASE)
    data["shots"][0]["start"]["dad"]["at"] = "offstage_left"
    data["shots"][0]["actions"] = [{"t": 0, "who": "dad", "do": "walk_to", "to": "steps_left"}]
    err = _only_error(FakeCompiler()(Screenplay.model_validate(data), scene))
    assert (err.code, err.message) == ("offstage", "shot s1, t=0: dad cannot walk_to; dad is offstage; use enter first")
    data["shots"][0]["actions"] = [{"t": 0, "who": "dad", "do": "enter", "to": "steps_left"}]
    assert FakeCompiler()(Screenplay.model_validate(data), scene).ok


def test_cast_left_out_of_start_waits_offstage(scene):
    data = copy.deepcopy(BASE)
    del data["shots"][0]["start"]["dad"]
    data["shots"][0]["actions"] = [{"t": 0, "who": "dad", "do": "wave"}]
    err = _only_error(FakeCompiler()(Screenplay.model_validate(data), scene))
    assert err.code == "offstage"


def test_cued_actions_move_onto_their_words(scene, sample):
    """Cue retiming happens before the busy and holding checks (narration.retime_actions): moving
    the "dropped" line 0.75 s later moves the drop, and the uncued actions after it, 0.75 s later,
    so dad is still busy when the hand_to, still cued on "handed", comes round."""
    data = sample.model_dump(mode="json", by_alias=True)
    data["shots"][0]["narration"][1]["t"] = 3.25  # "dropped" now lands at 4.0, not 3.25
    err = _only_error(FakeCompiler()(Screenplay.model_validate(data), scene))
    assert err.code == "busy" and err.t == 6.6 and "dad is still" in err.message


def test_camera_target_must_be_on_stage(scene):
    data = copy.deepcopy(BASE)
    data["shots"][0]["camera"] = [{"t": 0, "move": "wide"}, {"t": 1, "move": "close_up", "target": "hymn_books"}]
    assert FakeCompiler()(Screenplay.model_validate(data), scene).ok
    del data["shots"][0]["start"]["mom"]["holding"]  # the books are nowhere on stage now
    err = _only_error(FakeCompiler()(Screenplay.model_validate(data), scene))
    assert (err.code, err.message) == ("bad_camera",
                                       'shot s1, t=1: cannot close_up "hymn_books"; hymn_books is not on stage')


def test_not_holding_on_the_ground_keeps_its_old_wording(scene):
    """The real compiler says "it is on the ground"; the fake keeps "nobody is holding it" (see
    LEGACY_REASONS) because tests/agent/test_agent_compile_tool.py pins that text."""
    err = _only_error(_compile(scene, [
        {"t": 0, "who": "mom", "do": "drop", "what": "hymn_books"},
        {"t": 1, "who": "mom", "do": "hold", "what": "hymn_books"},
    ]))
    assert err.message == 'shot s1, t=1: mom cannot hold "hymn_books"; nobody is holding it'


def test_static_errors_stop_before_the_simulation(scene):
    """As in the real compiler: fix the static errors first; no knock-on world errors."""
    res = _compile(scene, [
        {"t": 0, "who": "dad", "do": "walk_to", "to": "the_moon"},
        {"t": 0.1, "who": "dad", "do": "wave"},
    ])
    assert [e.code for e in res.errors] == ["unknown_mark"]


def test_unused_prop_missing_from_the_scene_passes(scene):
    """Documented gap: a listed prop the scene lacks and nothing uses passes in the fake (the
    studio's scene_for reports it first); a used one is unknown_prop, as in the real compiler."""
    data = copy.deepcopy(BASE)
    data["props"].append("car")
    assert FakeCompiler()(Screenplay.model_validate(data), scene).ok
    data["shots"][0]["camera"] = [{"t": 0, "move": "close_up", "target": "car"}]
    err = _only_error(FakeCompiler()(Screenplay.model_validate(data), scene))
    assert err.code == "unknown_prop" and err.message.startswith('screenplay: prop "car" is not a known prop')


def test_effects_occupy_nobody(scene):
    res = _compile(scene, [
        {"t": 0, "do": "rain", "dur": 3},
        {"t": 0, "do": "time_of_day", "value": "dusk"},
        {"t": 0, "who": "dad", "do": "wave"},
    ])
    assert res.ok, [e.message for e in res.errors]
    assert [fx.kind for fx in res.timeline.effects] == ["rain", "time_of_day"]


@pytest.mark.parametrize("who, verb, reason", [
    ("mom", "open", "mom's arm_l cannot reach: target is 0.22 from the shoulder, reach is 0.17"),
    ("mom", "close", "mom's arm_l cannot reach: target is 0.22 from the shoulder, reach is 0.17"),
])
def test_open_or_close_of_a_held_prop_is_out_of_reach(scene, who, verb, reason):
    """As in the real compiler (verbs_objects._open_close): the free arm reaches for the hand that
    holds the prop, in the hold pose, and comes up short. The directing skill says never to."""
    err = _only_error(_compile(scene, [{"t": 0.5, "who": who, "do": verb, "what": "hymn_books"}]))
    assert err.code == "out_of_reach"
    assert err.message == f'shot s1, t=0.5: {who} cannot {verb} "hymn_books"; {reason}'
    # raised into the hold pose first: the same
    err = _only_error(_compile(scene, [{"t": 0.5, "who": who, "do": "hold", "what": "hymn_books"},
                                       {"t": 2, "who": who, "do": verb, "what": "hymn_books"}]))
    assert err.code == "out_of_reach" and err.t == 2


def test_open_a_held_prop_after_setting_it_down_at_hand_height(scene):
    """The skill's fix for the above: place_on it at hand height (a mark one step up, from the
    foot of the steps), then open it there."""
    res = _compile(scene, [{"t": 0.5, "who": "mom", "do": "place_on", "what": "hymn_books", "on": "steps_top"},
                           {"t": 1.5, "who": "mom", "do": "open", "what": "hymn_books"}],
                   marks={"by_steps": [0.53, 0.0]},
                   start={"dad": {"at": "steps_foot", "facing": "right"},
                          "mom": {"at": "by_steps", "holding": "hymn_books", "facing": "right"}})
    assert res.ok, [e.message for e in res.errors]


def test_kneeling_lowers_the_shoulders(scene):
    """A kneeling puppet's root (and so its shoulders) is lowered by the kneel rootOffset, as in
    the real tracks: a handle at standing hand height can go out of reach."""
    start = {"dad": {"at": "steps_foot", "facing": "right"},
             "mom": {"at": "steps_top", "holding": "hymn_books", "facing": "left"}}
    standing = _compile(scene, [{"t": 0.5, "who": "dad", "do": "close", "what": "hymn_books"}], start=start)
    assert standing.ok, [e.message for e in standing.errors]
    err = _only_error(_compile(scene, [{"t": 0, "who": "dad", "do": "kneel"},
                                       {"t": 2, "who": "dad", "do": "close", "what": "hymn_books"}], start=start))
    assert err.code == "out_of_reach" and err.t == 2


def test_hug_threshold_uses_rounded_positions(scene):
    """steps_foot and steps_top are 0.2 apart in x, exactly HUG_GAP + HUG_SLACK. The real
    compiler reads roots back rounded to 3 dp, so the hugger walks over first (and a kneeling
    hugger cannot): the fake decides the same way."""
    err = _only_error(_compile(scene, [{"t": 0, "who": "mom", "do": "kneel"},
                                       {"t": 3, "who": "mom", "do": "hug", "target": "dad"}]))
    assert (err.code, err.message) == ("not_standing", "shot s1, t=3: mom cannot hug; mom is kneeling; stand_up first")


def test_follow_counts_kneeling_as_the_leader_moving(scene):
    """The real follow reads the leader's root.position track, where kneeling lowers the root."""
    res = _compile(scene, [{"t": 4, "who": "mom", "do": "kneel"},
                           {"t": 4, "who": "dad", "do": "follow", "target": "mom"}])
    assert res.ok, [e.message for e in res.errors]
    err = _only_error(_compile(scene, [{"t": 4, "who": "dad", "do": "follow", "target": "mom"}]))
    assert err.code == "nothing_to_follow"


def test_pick_up_above_the_shoulder_still_needs_reach(scene):
    """A grip level with or above the shoulder is a standing reach (no crouch helps): out of reach
    when the arm cannot get there, as in the real pick_up_keys."""
    start = {"dad": {"at": "steps_foot", "facing": "right"}, "mom": {"at": "curb", "facing": "left"}}
    for height, ok in ((0.5, True), (0.9, False)):
        res = _compile(scene, [{"t": 0.5, "who": "dad", "do": "pick_up", "what": "hymn_books"}], start=start,
                       marks={"shelf": [0.0, height]}, propStart={"hymn_books": {"at": "shelf"}})
        assert res.ok == ok, [e.message for e in res.errors]
    assert (res.errors[0].code, res.errors[0].message) == (
        "out_of_reach", 'shot s1, t=0.5: dad cannot pick_up "hymn_books"; dad\'s arm_r cannot reach: '
                        "target is 0.57 from the shoulder, reach is 0.19")
