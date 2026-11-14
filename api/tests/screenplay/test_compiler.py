"""compile(): reference checks, exact errors, multi-shot timing, start keys, determinism,
every vocabulary verb, and the agent's compile tool picking up the real compiler."""

import copy
import json
from pathlib import Path

import pytest

import schemas
from screenplay import compiler as C
from screenplay import ik
from screenplay.compiler import compile as compile_screenplay
from screenplay.schema import VOCABULARY, CompileResult, Screenplay
from screenplay.tracks import evaluate

SAMPLES = Path(__file__).resolve().parents[3] / "web" / "public" / "samples"


def _load(name):
    return json.loads((SAMPLES / name).read_text())


SAMPLE = _load("hymn_books.screenplay.json")
MEMORY = _load("hymn_books.memory.json")

SETS = {s["id"]: schemas.SetDef.model_validate(s) for s in MEMORY["sets"]}
SETS["kitchen"] = schemas.SetDef(
    id="kitchen", name="kitchen",
    layers=[{"art": None, "depth": 0, "height": 1.0}, {"art": None, "depth": 1, "height": 0.4}],
    marks={"stove": [0.3, 0.0], "door": [0.8, 0.0]},
)
PROPS = {p["id"]: schemas.Prop.model_validate(p) for p in MEMORY["props"]}
for extra in (
    {"id": "chair", "name": "chair", "size": "furniture", "art": {"body": None}, "sockets": {"seat": [0.5, 0.5]}},
    {"id": "sedan", "name": "sedan", "size": "vehicle", "aspect": 2.0, "art": {"body": None},
     "sockets": {"driver": [0.35, 0.55], "passenger": [0.6, 0.55]},
     "states": {"parked": {}, "open": {}, "closed": {}}},
):
    PROPS[extra["id"]] = schemas.Prop.model_validate(extra)
PEOPLE = {p["id"]: schemas.Person.model_validate(p) for p in MEMORY["people"]}
PEOPLE["kid"] = schemas.Person(id="kid", family_id="sample_family", name="Kid", character={"color": "#0a0", "build": "small"})


def sample() -> dict:
    return copy.deepcopy(SAMPLE)


def run(data: dict) -> CompileResult:
    return compile_screenplay(Screenplay.model_validate(data), sets=SETS, props=PROPS, people=PEOPLE)


def track(result, target, channel):
    for tr in result.timeline.tracks:
        if (tr.target, tr.channel) == (target, channel):
            return [(k[0], k[1]) for k in tr.keys]
    return []


def value(result, target, channel, t):
    return evaluate(result.timeline.tracks, target, channel, t)


def messages(result):
    return [e.message for e in result.errors]


# ------------------------------------------------------------------- reference checks

def test_unknown_set_person_and_prop_are_all_reported():
    data = sample()
    data["shots"][0]["set"] = "nowhere"
    data["cast"].append("gran")
    data["props"].append("piano")
    res = run(data)
    assert not res.ok and res.timeline is None
    assert [(e.code, e.message, e.shot) for e in res.errors] == [
        ("unknown_set", "shot s1: nowhere is not a known set", "s1"),
        ("unknown_person", "screenplay: gran is not a known person", None),
        ("unknown_prop", "screenplay: piano is not a known prop", None),
    ]


def test_other_screenplay_level_checks():
    data = sample()
    data["narrations"]["mom"]["s9"] = [{"t": 0, "text": "hello"}]
    data["explanations"].append({"target": "prop:piano", "text": "x"})
    data["explanations"].append({"target": "set:church", "text": "x"})
    res = run(data)
    assert [e.code for e in res.errors] == ["telling_unknown_shot", "explanation_unknown_target", "explanation_bad_target"]
    assert res.errors[0].message == 'screenplay: mom\'s telling has lines for shot "s9", which is not in the screenplay'
    assert res.errors[1].message == (
        'screenplay: explanation 3 targets "prop:piano", but piano is not in the screenplay\'s props'
    )
    empty = run({"memoryId": "m", "cast": ["dad"], "shots": []})
    assert [e.code for e in empty.errors] == ["no_shots"]
    dup = sample()
    dup["shots"].append(copy.deepcopy(dup["shots"][0]))
    assert "duplicate_shot" in [e.code for e in run(dup).errors]


# ------------------------------------------------------------------------ action errors

def test_hand_to_while_mom_holds_the_books_is_the_exact_message():
    data = sample()
    shot = data["shots"][0]
    shot["actions"] = [a for a in shot["actions"] if a["do"] not in ("drop", "pick_up")]
    for lines in (shot["narration"], data["narrations"]["mom"]["s1"]):
        for line in lines:
            if line.get("cue") == "drop":
                del line["cue"], line["cueWord"]
    res = run(data)
    assert not res.ok and res.timeline is None
    (err,) = res.errors
    assert err.message == 'shot s1, t=6.6: dad cannot hand_to "hymn_books"; mom is holding it'
    assert (err.code, err.shot, err.t, err.who, err.do) == ("not_holding", "s1", 6.6, "dad", "hand_to")


def test_unsupported_verb_is_an_error_not_an_exception(monkeypatch):
    monkeypatch.delitem(C.HANDLERS, "pose")
    res = run(sample())
    (err,) = res.errors
    assert err.code == "unsupported_verb"
    assert err.message == "shot s1, t=7.7: mom cannot pose; the compiler does not support pose yet"
    assert (err.t, err.who, err.do) == (7.7, "mom", "pose")


def test_independent_errors_are_all_reported_in_action_order():
    data = sample()
    actions = data["shots"][0]["actions"]
    actions[5]["pose"] = "moonwalk"
    actions.append({"t": 1, "who": "mom", "do": "walk_to", "to": "nowhere"})
    actions.append({"t": 1, "who": "dad", "do": "pose", "pose": "wave"})
    res = run(data)
    assert [(e.code, e.t) for e in res.errors] == [("unknown_pose", 7.7), ("unknown_mark", 1.0), ("busy", 1.0)]
    assert res.errors[0].message.startswith("shot s1, t=7.7: mom cannot pose; moonwalk is not a pose; poses are: ")
    assert res.errors[1].message == "shot s1, t=1: mom cannot walk_to; nowhere is not a mark in shot s1"
    assert res.errors[2].message == "shot s1, t=1: dad cannot pose; dad is still entering until t=2"


def test_parameters_are_checked_against_the_vocabulary():
    data = sample()
    data["shots"][0]["actions"] += [
        {"t": 9, "who": "dad", "do": "walk_to", "to": "curb", "what": "hymn_books"},
        {"t": 9, "who": "mom", "do": "hand_to", "what": "hymn_books"},
        {"t": 9, "who": "gran", "do": "walk_to", "to": "curb"},
        {"t": 9, "do": "rain", "who": "dad"},
        {"t": 9, "do": "time_of_day", "value": "noon"},
        {"t": 9, "who": "dad", "do": "pose", "pose": "wave", "dur": -1},
        {"t": 9, "who": "dad", "do": "hug", "target": "curb"},
        {"t": 9, "who": "dad", "do": "enter", "to": "curb", "from": "attic"},
    ]
    res = run(data)
    assert [(e.code, e.message) for e in res.errors] == [
        ("unexpected_param",
         'shot s1, t=9: dad cannot walk_to "hymn_books"; "what" is not a parameter of walk_to; it takes "who", "to"'),
        ("missing_param", 'shot s1, t=9: mom cannot hand_to "hymn_books"; it needs "to"'),
        ("unknown_person", "shot s1, t=9: gran cannot walk_to; gran is not in the cast"),
        ("unexpected_param", 'shot s1, t=9: dad cannot rain; "who" is not a parameter of rain; it takes "dur"'),
        ("bad_value", "shot s1, t=9: cannot time_of_day; noon is not a time of day; use dawn, day, dusk, night"),
        ("bad_dur", "shot s1, t=9: dad cannot pose; dur must be positive, not -1"),
        ("unknown_person", "shot s1, t=9: dad cannot hug; curb is not in the cast"),
        ("unknown_mark", "shot s1, t=9: dad cannot enter; attic is not a mark in shot s1"),
    ]


def test_out_of_reach_message():
    data = sample()
    actions = data["shots"][0]["actions"]
    actions[2]["to"] = "steps_left"  # dad stays at steps_left, so pick_up walks him over first...
    data["shots"][0]["marks"]["steps_top"] = [0.6, 1.2]  # ...but mom stands so high the books land out of reach
    res = run(data)
    err = next(e for e in res.errors if e.code == "out_of_reach")
    assert err.message.startswith('shot s1, t=5.5: dad cannot pick_up "hymn_books"; dad\'s arm_r cannot reach: target is ')
    assert err.message.endswith("from the shoulder, reach is 0.19")


def test_shot_start_errors_stop_compilation():
    data = sample()
    data["shots"][0]["start"]["mom"]["at"] = "nowhere"
    res = run(data)
    assert [(e.code, e.message, e.shot, e.t) for e in res.errors] == [
        ("unknown_mark", "shot s1: nowhere is not a mark in shot s1", "s1", None)
    ]


def test_bad_start_pose_and_unplaced_seat():
    data = sample()
    data["cast"].append("kid")
    data["props"].append("chair")
    data["shots"][0]["start"]["dad"]["pose"] = "moonwalk"
    data["shots"][0]["start"]["kid"] = {"at": "curb", "in": "chair.seat"}
    res = run(data)
    assert [(e.code, e.message, e.shot) for e in res.errors] == [
        ("bad_start", "shot s1: moonwalk is not a pose; poses are: " + ", ".join(sorted(ik.POSES["poses"])), "s1"),
        ("bad_start", "shot s1: kid starts in chair.seat, but chair is not on stage", "s1"),
    ]


def test_camera_errors():
    data = sample()
    data["shots"][0]["camera"][1]["target"] = "gran"
    res = run(data)
    (err,) = res.errors
    assert err.code == "bad_camera"
    assert err.message == 'shot s1, t=5.5: camera cannot dolly_to "gran"; gran is not a person, prop or mark in shot s1'
    assert (err.shot, err.t, err.do) == ("s1", 5.5, "dolly_to")
    data["shots"][0]["camera"][1].pop("target")
    (err,) = run(data).errors
    assert err.message == "shot s1, t=5.5: camera cannot dolly_to; it needs a target"


def test_narration_errors_are_reported_with_action_errors():
    data = sample()
    data["shots"][0]["narration"][1]["cueWord"] = "fell"
    data["shots"][0]["actions"].append({"t": 1, "who": "mom", "do": "walk_to", "to": "nowhere"})
    res = run(data)
    codes = [e.code for e in res.errors]
    assert "cue_word_missing" in codes and "unknown_mark" in codes
    cue = next(e for e in res.errors if e.code == "cue_word_missing")
    assert cue.shot == "s1" and cue.message.startswith('shot s1, dad\'s narration line 1: cueWord "fell" is not a word of')


# ------------------------------------------------------------------------- start keys

def test_start_keys_for_seated_and_kneeling_puppets():
    data = sample()
    data["props"].append("chair")
    shot = data["shots"][0]
    shot["propStart"] = {"chair": {"at": "steps_left"}}
    shot["start"] = {"mom": {"at": "steps_left", "in": "chair.seat", "holding": "hymn_books"},
                     "dad": {"at": "steps_foot", "pose": "kneel", "facing": "right"}}
    shot["actions"] = []
    shot["camera"] = []
    data["narrations"] = None
    for line in shot["narration"]:
        line.pop("cue", None), line.pop("cueWord", None)
    data["interactions"] = None
    res = run(data)
    assert res.ok, messages(res)
    # chair: furniture 0.4 tall, seat at half height; hips on the seat
    assert track(res, "puppet:mom", "root.position") == [(0.0, [-0.6, round(0.2 - 0.48 * 0.5, 3), 0.5])]
    assert track(res, "puppet:mom", "leg_l.upper.rot") == [(0.0, 1.5)]
    assert track(res, "puppet:mom", "arm_r.upper.rot") == [(0.0, 0.3)]
    assert track(res, "prop:hymn_books", "parent") == [(0.0, "puppet:mom.hand_r")]
    assert track(res, "prop:chair", "parent") == [(0.0, "ground")]
    assert track(res, "prop:chair", "position") == [(0.0, [-0.6, 0.0, 0.5])]
    assert track(res, "puppet:dad", "root.position") == [(0.0, [0.0, round(-0.24 * 0.55, 3), 0.5])]
    assert track(res, "puppet:dad", "leg_l.upper.rot") == [(0.0, 1.4)]


def test_cast_not_in_start_waits_offstage():
    data = sample()
    data["cast"].append("kid")
    res = run(data)
    assert res.ok
    assert track(res, "puppet:kid", "root.position") == [(0.0, [-1.3, 0.0, 0.5])]
    assert track(res, "puppet:kid", "root.facing") == [(0.0, 1)]


# ------------------------------------------------------------------------ multi-shot

def two_shots(second_set="kitchen", second_start=None, transition="fold"):
    return {
        "memoryId": "m", "cast": ["dad", "mom"], "props": ["hymn_books"],
        "shots": [
            {"id": "s1", "set": "church_steps_1988", "transition": transition,
             "start": {"dad": {"at": "offstage_left", "facing": "right"},
                       "mom": {"at": "steps_top", "holding": "hymn_books", "facing": "left"}},
             "actions": [{"t": 0, "who": "dad", "do": "enter", "from": "offstage_left", "to": "steps_left"}],
             "pageText": "one"},
            {"id": "s2", "set": second_set,
             "start": second_start or {"mom": {"at": "stove", "facing": "right"}},
             "actions": [{"t": 1, "who": "mom", "do": "drop", "what": "hymn_books"}],
             "pageText": "two"},
        ],
    }


def test_two_shots_on_different_sets():
    res = run(two_shots())
    assert res.ok, messages(res)
    tl = res.timeline
    # s1: content ends 0.5 s after dad arrives at 3.2; three layers fold front first, 0.1 apart
    s1, s2 = tl.shots
    assert (s1.start, s1.action_start, s1.end) == (0.0, 1.2, 4.3)
    assert (s2.start, s2.action_start, s2.end) == (4.3, 5.5, 7.3)
    assert (s1.page_text, s2.page_text) == ("one", "two")
    assert tl.duration == 7.3
    assert track(res, "set:church_steps_1988.layer2", "fold") == [(0.4, 0.0), (1.2, 1.0), (3.7, 1.0), (4.1, 0.0)]
    assert track(res, "set:church_steps_1988.layer1", "fold") == [(0.2, 0.0), (1.0, 1.0), (3.8, 1.0), (4.2, 0.0)]
    assert track(res, "set:church_steps_1988.layer0", "fold") == [(0.0, 0.0), (0.8, 1.0), (3.9, 1.0), (4.3, 0.0)]
    # s2's set rises from its start, back layer first; the last shot does not fold
    assert track(res, "set:kitchen.layer0", "fold") == [(4.3, 0.0), (5.1, 1.0)]
    assert track(res, "set:kitchen.layer1", "fold") == [(4.5, 0.0), (5.3, 1.0)]
    # the books are carried into shot 2 in mom's hand, still in the hold pose, and she drops them there
    assert track(res, "prop:hymn_books", "parent") == [(0.0, "puppet:mom.hand_r"), (6.5, "ground")]
    assert value(res, "prop:hymn_books", "parent", 5.0) == "puppet:mom.hand_r"
    assert value(res, "puppet:mom", "arm_r.upper.rot", 6.4) == 0.3
    assert value(res, "prop:hymn_books", "position", 7.0) == [round(-0.4 + 0.08, 3), 0.0, 0.5]
    # mom steps to her new mark when the shot starts, not sliding there during the fold
    assert value(res, "puppet:mom", "root.position", 4.29) == pytest.approx([0.2, 0.3, 0.5])
    assert value(res, "puppet:mom", "root.position", 4.3) == [-0.4, 0.0, 0.5]
    assert value(res, "puppet:mom", "root.facing", 4.3) == 1
    # dad is not in shot 2, so he waits offstage
    assert value(res, "puppet:dad", "root.position", 4.29) == pytest.approx([-0.6, 0.0, 0.5])
    assert value(res, "puppet:dad", "root.position", 4.3) == [-1.3, 0.0, 0.5]
    # the camera holds the end of shot 1 into shot 2
    assert value(res, "camera", "position", 4.3) == [0.0, 0.6, 2.6]


def test_transition_none_snaps_the_set_flat():
    res = run(two_shots(transition="none"))
    assert res.ok
    s1, s2 = res.timeline.shots
    assert s1.end == 3.7 and s2.start == 3.7 and s2.action_start == 4.9
    assert track(res, "set:church_steps_1988.layer0", "fold")[-2:] == [(3.7, 1.0), (3.71, 0.0)]


def test_same_set_shots_do_not_fold_or_rise_again():
    res = run(two_shots(second_set="church_steps_1988", second_start={"mom": {"at": "steps_foot"}}))
    assert res.ok, messages(res)
    s1, s2 = res.timeline.shots
    assert (s1.end, s2.start, s2.action_start) == (3.7, 3.7, 4.9)
    for i, start in enumerate((0.0, 0.2, 0.4)):
        assert track(res, f"set:church_steps_1988.layer{i}", "fold") == [(start, 0.0), (round(start + 0.8, 3), 1.0)]
    # dad was not listed in shot 2 but the set is the same: he stays where he was
    assert value(res, "puppet:dad", "root.position", 5.0) == [-0.6, 0.0, 0.5]
    assert value(res, "puppet:mom", "root.position", 3.7) == [0.0, 0.0, 0.5]


def test_state_errors_in_a_later_shot_name_that_shot():
    data = two_shots()
    data["shots"][1]["actions"].append({"t": 3, "who": "mom", "do": "drop", "what": "hymn_books"})
    (err,) = run(data).errors
    assert err.message == 'shot s2, t=3: mom cannot drop "hymn_books"; it is on the ground'
    assert err.code == "not_holding"


# ------------------------------------------------------------------------ determinism

def test_determinism():
    a = run(sample()).model_dump_json(by_alias=True)
    b = run(sample()).model_dump_json(by_alias=True)
    c = run(json.loads(json.dumps(SAMPLE))).model_dump_json(by_alias=True)
    assert a == b == c
    bad = sample()
    bad["shots"][0]["actions"][5]["pose"] = "moonwalk"
    assert run(bad).model_dump_json() == run(copy.deepcopy(bad)).model_dump_json()


def test_input_is_not_mutated():
    sp = Screenplay.model_validate(sample())
    before = sp.model_dump_json()
    compile_screenplay(sp, sets=SETS, props=PROPS, people=PEOPLE)
    assert sp.model_dump_json() == before


# ------------------------------------------------------------------- every vocabulary verb

BASE_START = {
    "dad": {"at": "steps_foot", "facing": "right"},
    "mom": {"at": "steps_top", "holding": "hymn_books", "facing": "left"},
    "kid": {"at": "offstage_left"},
}

VERB_ACTIONS = {
    "walk_to": [{"who": "dad", "do": "walk_to", "to": "curb"}],
    "run_to": [{"who": "dad", "do": "run_to", "to": "curb"}],
    "enter": [{"who": "kid", "do": "enter", "to": "steps_left"}],
    "exit": [{"who": "dad", "do": "exit"}],
    "turn_to": [{"who": "dad", "do": "turn_to", "target": "steps_left"}],
    "follow": [{"who": "kid", "do": "follow", "target": "dad"}],
    "pose": [{"who": "dad", "do": "pose", "pose": "wave"}],
    "wave": [{"who": "dad", "do": "wave"}],
    "point_at": [{"who": "dad", "do": "point_at", "target": "mom"}],
    "sit_on": [{"who": "dad", "do": "sit_on", "what": "chair"}],
    "stand_up": [{"who": "dad", "do": "kneel"}, {"t": 2, "who": "dad", "do": "stand_up"}],
    "kneel": [{"who": "dad", "do": "kneel"}],
    "hug": [{"who": "dad", "do": "hug", "target": "mom"}],
    "lean_toward": [{"who": "dad", "do": "lean_toward", "target": "mom"}],
    "pick_up": [{"who": "mom", "do": "drop", "what": "hymn_books"}, {"t": 1, "who": "dad", "do": "pick_up", "what": "hymn_books"}],
    "hold": [{"who": "mom", "do": "hold", "what": "hymn_books"}],
    "drop": [{"who": "mom", "do": "drop", "what": "hymn_books"}],
    "hand_to": [{"who": "mom", "do": "hand_to", "what": "hymn_books", "to": "dad"}],
    "put_on": [{"who": "mom", "do": "put_on", "what": "hymn_books"}],
    "place_on": [{"who": "mom", "do": "place_on", "what": "hymn_books", "on": "steps_top"}],
    "open": [{"who": "dad", "do": "open", "what": "sedan"}],
    "close": [{"who": "dad", "do": "close", "what": "sedan"}],
    "set_state": [{"do": "set_state", "what": "sedan", "state": "parked"}],
    "get_in": [{"who": "dad", "do": "get_in", "what": "sedan"}],
    "get_out": [{"who": "dad", "do": "get_in", "what": "sedan"}, {"t": 2, "who": "dad", "do": "get_out", "what": "sedan"}],
    "drive_to": [{"who": "dad", "do": "get_in", "what": "sedan"}, {"t": 2, "who": "dad", "do": "drive_to", "what": "sedan", "to": "steps_left"}],
    "ride_to": [{"who": "dad", "do": "get_in", "what": "sedan"}, {"who": "kid", "do": "enter", "to": "curb"},
                {"t": 5, "who": "kid", "do": "get_in", "what": "sedan"},
                {"t": 7, "who": "kid", "do": "ride_to", "what": "sedan", "to": "steps_left"}],
    "confetti": [{"do": "confetti", "at": "mom"}],
    "steam": [{"do": "steam", "at": "steps_top"}],
    "rain": [{"do": "rain"}],
    "smoke": [{"do": "smoke", "at": "sedan", "dur": 2}],
    "time_of_day": [{"do": "time_of_day", "value": "dusk"}],
}


def test_every_vocabulary_verb_has_a_case():
    assert sorted(VERB_ACTIONS) == sorted(VOCABULARY["actions"])
    assert set(C.ACTIVITY) == set(VOCABULARY["actions"])
    assert all(word.split()[0].endswith("ing") for word in C.ACTIVITY.values())


@pytest.mark.parametrize("verb", sorted(VOCABULARY["actions"]))
def test_every_verb_compiles_or_gives_a_precise_error(verb):
    data = {
        "memoryId": "m", "cast": ["dad", "mom", "kid"], "props": ["hymn_books", "chair", "sedan"],
        "shots": [{
            "id": "s1", "set": "church_steps_1988", "start": BASE_START,
            "propStart": {"chair": {"at": "steps_left"}, "sedan": {"at": "curb"}},
            "actions": [{"t": 0, **a} for a in VERB_ACTIONS[verb]],
        }],
    }
    res = run(data)  # never raises
    assert isinstance(res, CompileResult)
    if res.ok:
        assert res.timeline is not None and res.errors == []
        return
    assert res.timeline is None
    for err in res.errors:
        assert err.code and err.message.startswith("shot s1"), err
        assert err.code not in ("invalid_action",), err
        if err.code == "unsupported_verb":
            assert err.do not in C.HANDLERS
            assert err.message.endswith(f"the compiler does not support {err.do} yet")


@pytest.mark.parametrize("verb", ["enter", "walk_to", "run_to", "pose", "pick_up", "hold", "drop", "hand_to"])
def test_core_verbs_compile(verb):
    data = {
        "memoryId": "m", "cast": ["dad", "mom", "kid"], "props": ["hymn_books"],
        "shots": [{"id": "s1", "set": "church_steps_1988", "start": BASE_START,
                   "actions": [{"t": 0, **a} for a in VERB_ACTIONS[verb]]}],
    }
    res = run(data)
    assert res.ok, messages(res)


def test_handlers_are_discovered_from_verbs_modules(monkeypatch):
    import pkgutil
    import sys
    import types

    def apply_wave(world, tracks, action, t, action_start, locate):
        return t + 1.0

    fake = types.ModuleType("screenplay.verbs_zz_fake")
    fake.HANDLERS = {"wave": apply_wave}
    monkeypatch.setitem(sys.modules, "screenplay.verbs_zz_fake", fake)
    real_iter = pkgutil.iter_modules

    def iter_modules(path=None, prefix=""):
        yield from real_iter(path, prefix)
        yield pkgutil.ModuleInfo(None, "verbs_zz_fake", False)

    monkeypatch.setattr(pkgutil, "iter_modules", iter_modules)
    found = C._discover_handlers()
    assert found["wave"] is apply_wave
    assert found["walk_to"] is C.verbs_core.apply_walk_to

    fake.HANDLERS = {"walk_to": apply_wave}
    with pytest.raises(RuntimeError, match="walk_to"):
        C._discover_handlers()


def test_a_discovered_handler_is_used_and_occupies_its_puppet(monkeypatch):
    calls = []

    def apply_wave(world, tracks, action, t, action_start, locate):
        calls.append((action.who, t, world.now))
        return t + 1.0

    monkeypatch.setitem(C.HANDLERS, "wave", apply_wave)
    data = sample()
    data["shots"][0]["actions"] += [{"t": 8, "who": "dad", "do": "wave"}, {"t": 8.5, "who": "dad", "do": "wave"}]
    res = run(data)
    assert calls == [("dad", 9.2, 8.0)]
    assert messages(res) == ["shot s1, t=8.5: dad cannot wave; dad is still waving until t=9"]


# ------------------------------------------------------------------ the agent's tool

def test_load_compiler_returns_the_real_adapter_and_the_sample_compiles():
    from agent.tools.compile import (
        SceneInputs,
        _RealCompilerAdapter,
        load_compiler,
        parse_and_compile,
    )

    compiler = load_compiler()
    assert isinstance(compiler, _RealCompilerAdapter)
    scene = SceneInputs(sets=SETS, props=PROPS, people=PEOPLE)
    res = parse_and_compile(json.dumps(SAMPLE), compiler, lambda _sp: scene)
    assert res.ok, messages(res)
    assert res.timeline.duration == 10.65
    bad = sample()
    bad["shots"][0]["actions"][4]["t"] = 5
    bad["shots"][0]["actions"].pop(3)
    res = parse_and_compile(json.dumps(bad), compiler, lambda _sp: scene)
    assert not res.ok
    assert 'shot s1, t=5: dad cannot hand_to "hymn_books"; it is on the ground' in messages(res)
