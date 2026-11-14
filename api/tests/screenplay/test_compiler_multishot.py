"""compile() end to end across several shots, on small hand-built sets, props and people:
held props and positions carried between shots, set changes, shot timing, per-teller
narration, cross-shot state errors and determinism."""

from __future__ import annotations

import copy

import pytest

import schemas
from screenplay import body, layout
from screenplay.compiler import compile as compile_screenplay
from screenplay.schema import CompileResult, Screenplay
from screenplay.tracks import evaluate

# ------------------------------------------------------------------------- fixtures

SETS = {
    "porch": schemas.SetDef(
        id="porch", name="porch",
        layers=[{"art": None, "depth": 0, "height": 1.0}, {"art": None, "depth": 1, "height": 0.5}],
        marks={"door": [0.3, 0.0], "mid": [0.5, 0.0], "gate": [0.7, 0.0]},
    ),
    "kitchen": schemas.SetDef(
        id="kitchen", name="kitchen",
        layers=[
            {"art": None, "depth": 0, "height": 1.0},
            {"art": None, "depth": 1, "height": 0.6},
            {"art": None, "depth": 2, "height": 0.3},
        ],
        marks={"stove": [0.25, 0.0], "table": [0.6, 0.0]},
    ),
}
PROPS = {
    "cup": schemas.Prop(id="cup", name="cup", size="hand", art={"body": None}, grip=[0.5, 0.5]),
    "bag": schemas.Prop(id="bag", name="bag", size="carried", art={"body": None}, grip=[0.5, 0.1]),
}
PEOPLE = {
    pid: schemas.Person(id=pid, family_id="f", name=pid.title(), character={"color": "#336699", "build": build})
    for pid, build in (("ann", "average"), ("ben", "tall"))
}

# x on stage for a mark u: 2u - 1
DOOR, MID, GATE = [-0.4, 0.0, 0.5], [0.0, 0.0, 0.5], [0.4, 0.0, 0.5]
STOVE, TABLE = [-0.5, 0.0, 0.5], [0.2, 0.0, 0.5]
OFFSTAGE_LEFT = [-1.3, 0.0, 0.5]


def run(data: dict, **kw) -> CompileResult:
    return compile_screenplay(Screenplay.model_validate(data), sets=SETS, props=PROPS, people=PEOPLE, **kw)


def screenplay(*shots: dict, **extra) -> dict:
    return {"memoryId": "m", "cast": ["ann", "ben"], "props": ["cup", "bag"], "shots": list(shots), **extra}


def track(result, target, channel):
    for tr in result.timeline.tracks:
        if (tr.target, tr.channel) == (target, channel):
            return [(k[0], k[1]) for k in tr.keys]
    return []


def value(result, target, channel, t):
    return evaluate(result.timeline.tracks, target, channel, t)


def messages(result):
    return [e.message for e in result.errors]


def targets(result):
    return {tr.target for tr in result.timeline.tracks}


# ------------------------------------------------- (a) two shots on the same set

def test_two_shots_on_the_same_set_carry_held_props_and_positions():
    res = run(screenplay(
        {"id": "s1", "set": "porch",
         "start": {"ann": {"at": "door", "holding": "cup", "facing": "right"}, "ben": {"at": "gate", "facing": "left"}},
         "actions": [{"t": 0, "who": "ann", "do": "walk_to", "to": "mid"}]},
        {"id": "s2", "set": "porch",
         "actions": [{"t": 0.5, "who": "ann", "do": "walk_to", "to": "door"}]},
    ), plant_feet=False)  # exact root keys: foot planting adds keys at cycle times (test_foot_planting.py)
    assert res.ok, messages(res)
    s1, s2 = res.timeline.shots
    # no fold between same-set shots, and no second rise: each layer only rises once
    assert (s2.start, s2.action_start) == (s1.end, round(s1.end + layout.RISE, 3))
    assert track(res, "set:porch.layer0", "fold") == [(0.0, 0.0), (0.8, 1.0)]
    assert track(res, "set:porch.layer1", "fold") == [(0.2, 0.0), (1.0, 1.0)]
    assert not any(t.startswith("set:kitchen") for t in targets(res))
    # the cup never leaves ann's hand: one parent key, and her arm stays in the hold pose
    assert track(res, "prop:cup", "parent") == [(0.0, "puppet:ann.hand_r")]
    assert track(res, "prop:cup", "position") == [(0.0, [0.0, 0.0, 0.0])]
    hold_upper, hold_lower = body.hold_pose("arm_r")
    for t in (s1.end, s2.action_start, s2.end):
        assert value(res, "puppet:ann", "arm_r.upper.rot", t) == pytest.approx(hold_upper)
        assert value(res, "puppet:ann", "arm_r.lower.rot", t) == pytest.approx(hold_lower)
    # ann ends shot 1 at mid and walks back to the door from there in shot 2
    ann = track(res, "puppet:ann", "root.position")
    assert value(res, "puppet:ann", "root.position", s1.end) == MID
    assert value(res, "puppet:ann", "root.position", s2.action_start + 0.5) == MID
    assert (s2.action_start + 0.5, MID) in ann
    assert ann[-1][1] == DOOR
    # no start keys for the same-set shot: shot 1's walk, then shot 2's walk (and its turn)
    assert [k[0] for k in ann] == [0.0, s1.action_start, round(s1.end - layout.ACTION_TAIL, 3),
                                   s2.action_start + 0.5, round(s2.end - layout.ACTION_TAIL, 3)]
    assert track(res, "puppet:ann", "root.facing") == [(0.0, 1.0), (s2.action_start + 0.5, -1.0)]
    # ben is not listed in shot 2 but the set is the same: he stays at the gate
    assert track(res, "puppet:ben", "root.position") == [(0.0, GATE)]


# ------------------------------------------------------------ (b) a set change

def set_change(prop_start=None):
    return screenplay(
        {"id": "s1", "set": "porch",
         "start": {"ann": {"at": "door", "holding": "cup", "facing": "right"},
                   "ben": {"at": "gate", "holding": "bag", "facing": "left"}},
         "actions": [{"t": 0, "who": "ann", "do": "drop", "what": "cup"}]},
        {"id": "s2", "set": "kitchen",
         "start": {"ann": {"at": "stove", "facing": "right"}},
         "propStart": prop_start or {},
         "actions": [{"t": 0, "who": "ann", "do": "walk_to", "to": "table"}]},
    )


def test_a_set_change_folds_rises_and_clears_what_stays_behind():
    res = run(set_change())
    assert res.ok, messages(res)
    s1, s2 = res.timeline.shots
    fold = layout.fold_out_seconds(2)
    content_end = round(s1.end - fold, 3)
    # porch folds front layer first from the end of shot 1's content; the kitchen rises from s2.start
    assert track(res, "set:porch.layer1", "fold") == [
        (0.2, 0.0), (1.0, 1.0), (content_end, 1.0), (round(content_end + 0.4, 3), 0.0),
    ]
    assert track(res, "set:porch.layer0", "fold") == [
        (0.0, 0.0), (0.8, 1.0), (round(content_end + 0.1, 3), 1.0), (round(content_end + 0.5, 3), 0.0),
    ]
    assert s1.end == round(content_end + fold, 3) and s2.start == s1.end
    for i in range(3):
        start = round(s2.start + layout.LAYER_STAGGER * i, 3)
        assert track(res, f"set:kitchen.layer{i}", "fold") == [(start, 0.0), (round(start + layout.LAYER_RISE, 3), 1.0)]

    # the cup ann dropped on the porch is not in the kitchen: it steps offstage at s2.start
    cup_parent = track(res, "prop:cup", "parent")
    assert cup_parent[0] == (0.0, "puppet:ann.hand_r")
    assert cup_parent[1][1] == "ground"
    dropped_at = cup_parent[1][0]
    assert len(cup_parent) == 2  # still "ground" in the kitchen, so no new parent key
    on_porch = value(res, "prop:cup", "position", dropped_at + 1.0)
    assert on_porch[0] == pytest.approx(-0.4 + 0.08)  # beside her feet, facing right
    assert value(res, "prop:cup", "position", s2.start - 0.001) == on_porch  # held, no slide during the fold
    assert value(res, "prop:cup", "position", s2.start) == OFFSTAGE_LEFT

    # ben is not in shot 2: he waits at offstage_left, and (state.py) the bag he carried stays
    # behind with the old set, so it leaves his hand and his arm drops out of the hold pose
    assert value(res, "puppet:ben", "root.position", s2.start - 0.001) == GATE
    assert value(res, "puppet:ben", "root.position", s2.start) == OFFSTAGE_LEFT
    assert track(res, "prop:bag", "parent") == [(0.0, "puppet:ben.hand_r"), (s2.start, "ground")]
    assert value(res, "prop:bag", "position", s2.start) == OFFSTAGE_LEFT
    assert value(res, "puppet:ben", "arm_r.upper.rot", s2.start - 0.001) == pytest.approx(body.hold_pose("arm_r")[0])
    assert value(res, "puppet:ben", "arm_r.upper.rot", s2.start) == 0.0

    # ann steps to the stove at s2.start (not sliding there during the fold) and walks on
    assert value(res, "puppet:ann", "root.position", s2.start - 0.001) == DOOR
    assert value(res, "puppet:ann", "root.position", s2.start) == STOVE
    assert track(res, "puppet:ann", "root.position")[-1][1] == TABLE


def test_a_prop_placed_with_prop_start_is_in_the_new_set():
    res = run(set_change(prop_start={"cup": {"at": "table"}}))
    assert res.ok, messages(res)
    s2 = res.timeline.shots[1]
    assert value(res, "prop:cup", "parent", s2.start) == "ground"
    assert (s2.start, TABLE) in track(res, "prop:cup", "position")
    assert value(res, "prop:cup", "position", s2.start) == TABLE
    assert OFFSTAGE_LEFT not in [v for _, v in track(res, "prop:cup", "position")]


def test_a_listed_puppet_carries_its_prop_into_the_new_set():
    data = set_change()
    data["shots"][1]["start"]["ben"] = {"at": "table", "facing": "left"}
    res = run(data)
    assert res.ok, messages(res)
    s2 = res.timeline.shots[1]
    assert track(res, "prop:bag", "parent") == [(0.0, "puppet:ben.hand_r")]
    assert value(res, "puppet:ben", "root.position", s2.start) == TABLE
    assert value(res, "puppet:ben", "arm_r.upper.rot", s2.start) == pytest.approx(body.hold_pose("arm_r")[0])


# ------------------------------------------------------------ (c) three shots

def test_three_shots_chain_their_timing_like_layout():
    pose = [{"t": 1, "who": "ann", "do": "pose", "pose": "wave"}]
    start = {"ann": {"at": "mid"}}
    res = run(screenplay(
        {"id": "s1", "set": "porch", "start": start, "actions": pose},
        {"id": "s2", "set": "porch", "actions": copy.deepcopy(pose)},
        {"id": "s3", "set": "kitchen", "start": {"ann": {"at": "table"}}, "actions": copy.deepcopy(pose)},
    ))
    assert res.ok, messages(res)
    s1, s2, s3 = res.timeline.shots
    # each shot's content ends ACTION_TAIL after its pose has blended in (1 s + POSE_SECONDS)
    def content_end(shot):
        return layout.content_end(shot.action_start + 1 + body.POSE_SECONDS, None)

    assert (s1.start, s1.action_start) == (0.0, layout.RISE)
    end1, start2, action2, _ = layout.next_shot_timing(content_end(s1), "porch", 2, "porch", "fold")
    assert (s1.end, s2.start, s2.action_start) == (end1, start2, action2)
    end2, start3, action3, _ = layout.next_shot_timing(content_end(s2), "porch", 2, "kitchen", "fold")
    assert (s2.end, s3.start, s3.action_start) == (end2, start3, action3)
    assert s3.end == content_end(s3)
    # the same numbers, written out: same set (no fold), then a 2-layer fold of 0.5 s
    assert [(s.start, s.action_start, s.end) for s in (s1, s2, s3)] == [
        (0.0, 1.2, 3.1), (3.1, 4.3, 6.7), (6.7, 7.9, 9.8),
    ]
    assert res.timeline.duration == s3.end == 9.8
    assert [s.id for s in res.timeline.shots] == ["s1", "s2", "s3"]
    assert [s.set for s in res.timeline.shots] == ["porch", "porch", "kitchen"]


# ------------------------------------------- (d) tellings, explanations, page text

def test_per_teller_narrations_explanations_and_page_text_are_carried():
    res = run(screenplay(
        {"id": "s1", "set": "porch", "start": {"ann": {"at": "door"}, "ben": {"at": "gate"}},
         "narration": [{"t": 0.5, "text": "It was a cold morning.", "source": "telling:ann:t1"}],
         "pageText": "The porch, 1979."},
        {"id": "s2", "set": "kitchen", "start": {"ann": {"at": "stove"}},
         "narration": [{"t": 0, "text": "Then we went inside.", "source": "telling:ann:t2"}],
         "pageText": "The kitchen."},
        narrations={
            "ben": {
                "s1": [{"t": 1, "text": "I remember the frost.", "source": "telling:ben:t1"}],
                "s2": [{"t": 0.25, "text": "The kettle was on.", "source": "telling:ben:t2"}],
            },
        },
        explanations=[
            {"target": "puppet:ann", "text": "Ann was nine."},
            {"target": "prop:cup", "text": "Grandma's cup."},
        ],
    ))
    assert res.ok, messages(res)
    tl = res.timeline
    s1, s2 = tl.shots
    assert tl.default_narrator == "ann"
    assert list(tl.narrations) == ["ann", "ben"]
    ann, ben = tl.narrations["ann"], tl.narrations["ben"]
    assert (ann.author_id, ben.author_id) == ("ann", "ben")
    # each line lands at its shot's action start plus its own t, in shot order
    assert [(line.t, line.text, line.source) for line in ann.lines] == [
        (s1.action_start + 0.5, "It was a cold morning.", "telling:ann:t1"),
        (s2.action_start, "Then we went inside.", "telling:ann:t2"),
    ]
    assert [(line.t, line.text) for line in ben.lines] == [
        (s1.action_start + 1, "I remember the frost."),
        (s2.action_start + 0.25, "The kettle was on."),
    ]
    assert [(c.t, c.text) for c in ben.captions] == [(line.t, line.text) for line in ben.lines]
    # the top-level captions are the default teller's
    assert [(c.t, c.text) for c in tl.captions] == [(line.t, line.text) for line in ann.lines]
    assert [(e.target, e.text) for e in tl.explanations] == [
        ("puppet:ann", "Ann was nine."), ("prop:cup", "Grandma's cup."),
    ]
    assert [s.page_text for s in tl.shots] == ["The porch, 1979.", "The kitchen."]


# ------------------------------------------------------- (e) cross-shot state errors

def dropped_then_handed(second_set):
    return screenplay(
        {"id": "s1", "set": "porch",
         "start": {"ann": {"at": "door", "holding": "cup", "facing": "right"}, "ben": {"at": "mid", "facing": "left"}},
         "actions": [{"t": 0, "who": "ann", "do": "drop", "what": "cup"}]},
        {"id": "s2", "set": second_set,
         "start": {"ann": {"at": "door" if second_set == "porch" else "stove"},
                   "ben": {"at": "mid" if second_set == "porch" else "table"}},
         "actions": [{"t": 1.5, "who": "ann", "do": "hand_to", "what": "cup", "to": "ben"}]},
    )


@pytest.mark.parametrize(("second_set", "reason"), [
    ("porch", "it is on the ground"),     # same set: the cup is still where she dropped it
    ("kitchen", "it is not on stage"),    # new set: the cup stayed behind on the porch
])
def test_handing_over_a_prop_dropped_in_the_previous_shot(second_set, reason):
    res = run(dropped_then_handed(second_set))
    assert not res.ok and res.timeline is None
    assert [(e.code, e.message, e.shot, e.t, e.who, e.do) for e in res.errors] == [(
        "not_holding", f'shot s2, t=1.5: ann cannot hand_to "cup"; {reason}', "s2", 1.5, "ann", "hand_to",
    )]


# --------------------------------------------------------------- (f) determinism

@pytest.mark.parametrize("data", [
    set_change(),
    set_change(prop_start={"cup": {"at": "table"}}),
    dropped_then_handed("kitchen"),
])
def test_compiling_twice_is_byte_identical(data):
    first = run(data).model_dump_json(by_alias=True)
    second = run(copy.deepcopy(data)).model_dump_json(by_alias=True)
    assert first == second
    sp = Screenplay.model_validate(data)
    before = sp.model_dump_json()
    again = compile_screenplay(sp, sets=SETS, props=PROPS, people=PEOPLE).model_dump_json(by_alias=True)
    assert again == first and sp.model_dump_json() == before


# ------------------------------------------------------------ (g) worn signature items

CAP = schemas.Prop(id="cap", name="cap", size="hand", art={"body": None}, grip=[0.5, 0.5])
CARA = schemas.Person(id="cara", family_id="f", name="Cara",
                      character={"color": "#993366", "build": "average", "item_prop_id": "cap"})


def run_worn(data: dict) -> CompileResult:
    return compile_screenplay(Screenplay.model_validate(data), sets=SETS, props={**PROPS, "cap": CAP},
                              people={**PEOPLE, "cara": CARA})


def worn(*shots: dict, cast=("ann", "ben", "cara"), props=("cup", "bag", "cap")) -> dict:
    return {"memoryId": "m", "cast": list(cast), "props": list(props), "shots": list(shots)}


def cap_keys(result) -> list[tuple]:
    return [(tr.target, tr.channel, k[0], k[1]) for tr in result.timeline.tracks if tr.target == "prop:cap"
            for k in tr.keys]


def test_a_signature_item_is_worn_on_the_head_from_the_start():
    res = run_worn(worn(
        {"id": "s1", "set": "porch", "start": {"cara": {"at": "door", "facing": "right"}},
         "actions": [{"t": 0, "who": "cara", "do": "walk_to", "to": "gate"}]},
        {"id": "s2", "set": "kitchen", "start": {"cara": {"at": "stove"}},
         "actions": [{"t": 0, "who": "cara", "do": "walk_to", "to": "table"}]},
    ))
    assert res.ok, messages(res)
    # exactly the two attach keys at t=0: parented to the head, the cap goes wherever cara goes,
    # through her walks and into the next set
    assert cap_keys(res) == [
        ("prop:cap", "parent", 0.0, "puppet:cara.head"),
        ("prop:cap", "position", 0.0, [0.0, 0.0, 0.0]),
    ]


def test_a_signature_item_is_worn_by_someone_offstage_too():
    res = run_worn(worn({"id": "s1", "set": "porch", "start": {"ann": {"at": "door"}}}))
    assert res.ok, messages(res)
    assert cap_keys(res)[:1] == [("prop:cap", "parent", 0.0, "puppet:cara.head")]
    assert value(res, "puppet:cara", "root.position", 0.0) == OFFSTAGE_LEFT


def test_people_without_an_item_get_no_item_keys():
    data = worn({"id": "s1", "set": "porch", "start": {"ann": {"at": "door"}, "ben": {"at": "gate"}},
                 "actions": [{"t": 0, "who": "ann", "do": "walk_to", "to": "mid"}]}, cast=("ann", "ben"))
    res = run_worn(data)
    assert res.ok, messages(res)
    # nobody wears anything: the listed props, never used, only wait offstage on the ground
    # (compiler._park_until_placed), never on a head
    for what in ("cup", "bag", "cap"):
        assert track(res, f"prop:{what}", "parent") == [(0.0, "ground")]
        assert track(res, f"prop:{what}", "position") == [(0.0, OFFSTAGE_LEFT)]
    # and the cap is not worn when the screenplay does not list it
    unlisted = run_worn(worn({"id": "s1", "set": "porch", "start": {"cara": {"at": "door"}}}, props=("cup",)))
    assert unlisted.ok, messages(unlisted)
    assert not any(tr.target == "prop:cap" for tr in unlisted.timeline.tracks)


def test_a_worn_item_set_down_in_the_start_block_is_keyed_there_instead():
    res = run_worn(worn({"id": "s1", "set": "porch", "start": {"cara": {"at": "door"}},
                         "propStart": {"cap": {"at": "mid"}}}))
    assert res.ok, messages(res)
    assert track(res, "prop:cap", "parent") == [(0.0, "ground")]


def test_a_later_put_on_swaps_the_signature_items_parent():
    # cara wears the cap through shot 1; shot 2 sets it down mid-porch, ben walks over, picks it
    # up and puts it on
    res = run_worn(worn(
        {"id": "s1", "set": "porch",
         "start": {"cara": {"at": "gate"}, "ann": {"at": "gate"}, "ben": {"at": "door", "facing": "right"}},
         "actions": [{"t": 0, "who": "cara", "do": "pose", "pose": "wave"}]},
        {"id": "s2", "set": "porch", "propStart": {"cap": {"at": "mid"}},
         "actions": [{"t": 0, "who": "ben", "do": "pick_up", "what": "cap"},
                     {"t": 4, "who": "ben", "do": "put_on", "what": "cap"}]},
    ))
    assert res.ok, messages(res)
    s2 = res.timeline.shots[1]
    parents = track(res, "prop:cap", "parent")
    assert [p for _, p in parents] == ["puppet:cara.head", "ground", "puppet:ben.hand_r", "puppet:ben.head"]
    assert parents[0][0] == 0.0 and parents[1][0] == s2.start
    assert s2.action_start < parents[2][0] < parents[3][0] < s2.end
    assert value(res, "prop:cap", "parent", s2.start - 0.01) == "puppet:cara.head"
    assert value(res, "prop:cap", "parent", s2.end) == "puppet:ben.head"
    assert value(res, "prop:cap", "position", s2.end) == [0.0, 0.0, 0.0]


# ------------------------------------- propStart on a later shot of the same set (verb_cases stage)

def _vc_run(*shots: dict) -> CompileResult:
    from verb_cases import _sp, base_scene

    scene = base_scene()
    return compile_screenplay(_sp(*shots), sets=scene.sets, props=scene.props, people=scene.people)


def _vc():
    import verb_cases

    return verb_cases


def _times_inside(start: float, end: float, n: int = 40) -> list[float]:
    return [round(start + (end - start) * i / n, 4) for i in range(1, n)]


YARD_TABLE_SPOT, YARD_BENCH_SPOT = [0.2, 0.0, 0.5], [-0.4, 0.0, 0.5]
STREET_GARAGE, STREET_CORNER = [-0.4, 0.0, 0.5], [0.6, 0.0, 0.5]


def test_a_prop_re_placed_on_the_same_set_steps_at_the_shot_start():
    vc = _vc()
    res = _vc_run(
        vc._shot(vc._a(1, "wave", who="kid"), start={"kid": vc._at("door")}, prop_start={"table": {"at": "table_spot"}}),
        vc._shot(vc._a(1, "wave", who="kid"), id="s2", prop_start={"table": {"at": "bench_spot"}}),
    )
    assert res.ok, messages(res)
    s1, s2 = res.timeline.shots
    assert s1.end == s2.start
    for t in [s1.start, *_times_inside(s1.start, s2.start)]:
        assert value(res, "prop:table", "position", t) == YARD_TABLE_SPOT, t
    for t in [s2.start, *_times_inside(s2.start, s2.end), s2.end]:
        assert value(res, "prop:table", "position", t) == YARD_BENCH_SPOT, t
    # parent did not change (ground both times), so it gets no second key
    assert track(res, "prop:table", "parent") == [(0.0, "ground")]


def test_re_placing_a_prop_where_it_already_is_adds_no_keys():
    vc = _vc()
    res = _vc_run(
        vc._shot(vc._a(1, "wave", who="kid"), start={"kid": vc._at("door")}, prop_start={"table": {"at": "table_spot"}}),
        vc._shot(vc._a(1, "wave", who="kid"), id="s2", prop_start={"table": {"at": "table_spot"}}),
    )
    assert res.ok, messages(res)
    assert track(res, "prop:table", "position") == [(0.0, YARD_TABLE_SPOT)]


def test_a_prop_state_steps_at_the_shot_start():
    vc = _vc()
    res = _vc_run(
        vc._shot(vc._a(1, "wave", who="kid"), start={"kid": vc._at("door")},
                 prop_start={"lamp": {"at": "table_spot", "state": "lit"}}),
        vc._shot(vc._a(1, "wave", who="kid"), id="s2", prop_start={"lamp": {"state": "out"}}),
    )
    assert res.ok, messages(res)
    s2 = res.timeline.shots[1]
    assert track(res, "prop:lamp", "state") == [(0.0, "lit"), (s2.start, "out")]
    assert value(res, "prop:lamp", "state", s2.start - 0.01) == "lit"


def test_a_prop_moved_onto_another_prop_swaps_parent_and_position_at_the_shot_start():
    vc = _vc()
    res = _vc_run(
        vc._shot(vc._a(1, "wave", who="kid"), start={"kid": vc._at("door")},
                 prop_start={"table": {"at": "table_spot"}, "hymn_books": {"at": "bench_spot"}}),
        vc._shot(vc._a(1, "wave", who="kid"), id="s2", prop_start={"hymn_books": {"on": "table.top"}}),
    )
    assert res.ok, messages(res)
    s2 = res.timeline.shots[1]
    assert track(res, "prop:hymn_books", "parent") == [(0.0, "ground"), (s2.start, "prop:table.top")]
    positions = track(res, "prop:hymn_books", "position")
    assert (s2.start, [0.0, 0.0, 0.0]) in positions  # a position key at the parent key
    assert value(res, "prop:hymn_books", "position", s2.start - 0.01) == YARD_BENCH_SPOT


def _drive_then_re_place(*s2_actions: dict) -> CompileResult:
    vc = _vc()
    return _vc_run(
        vc._shot(vc._a(0, "drive_to", who="dad", what="sedan", to="corner"), set="street",
                 start={"dad": vc._at("garage", **{"in": "sedan.driver"})}, prop_start={"sedan": {"at": "garage"}}),
        vc._shot(*(s2_actions or (vc._a(0, "wave", who="kid"),)), id="s2", set="street",
                 start={"kid": vc._at("garage")}, prop_start={"sedan": {"at": "garage"}}),
    )


def test_a_driven_vehicle_re_placed_next_shot_stays_parked_until_the_shot_start():
    res = _drive_then_re_place()
    assert res.ok, messages(res)
    s1, s2 = res.timeline.shots
    drive_end = max(t for t, _ in track(res, "prop:sedan", "position") if t < s2.start)
    # held by its own position key, not only by the stage's parent-key rule
    assert (s2.start, STREET_CORNER) in track(res, "prop:sedan", "position")
    for t in [*_times_inside(drive_end, s2.start), drive_end]:
        assert value(res, "prop:sedan", "position", t) == STREET_CORNER, t
    for t in [s2.start, *_times_inside(s2.start, s2.end)]:
        assert value(res, "prop:sedan", "position", t) == STREET_GARAGE, t


def test_a_seated_puppet_rides_along_when_its_vehicle_is_re_placed():
    res = _drive_then_re_place()
    assert res.ok, messages(res)
    s1, s2 = res.timeline.shots
    # dad was listed in sedan.driver at the garage in s1: the same seated-root formula, at the
    # same sedan position, is where he must be in s2
    seated_at_garage = value(res, "puppet:dad", "root.position", s1.start)
    seated_at_corner = value(res, "puppet:dad", "root.position", s2.start - 0.01)
    assert seated_at_corner[0] == pytest.approx(seated_at_garage[0] + (STREET_CORNER[0] - STREET_GARAGE[0]))
    assert value(res, "puppet:dad", "root.position", s2.start) == seated_at_garage
    for t in _times_inside(s2.start, s2.end):
        assert value(res, "puppet:dad", "root.position", t) == seated_at_garage, t
    # it holds its s1 value until s2 starts, then steps
    drive_end = max(t for t, _ in track(res, "puppet:dad", "root.position") if t < s2.start)
    for t in _times_inside(drive_end, s2.start):
        assert value(res, "puppet:dad", "root.position", t) == seated_at_corner, t


def test_the_world_agrees_a_re_placed_vehicles_rider_is_at_its_new_seat():
    # dad gets out in s2: the world has him at the garage, so he stands up beside the sedan
    # there (and his get_out keys start from the seat at the garage, not the corner)
    vc = _vc()
    res = _drive_then_re_place(vc._a(0, "get_out", who="dad", what="sedan"))
    assert res.ok, messages(res)
    s2 = res.timeline.shots[1]
    seated_at_garage = value(res, "puppet:dad", "root.position", 0.0)
    assert value(res, "puppet:dad", "root.position", s2.action_start) == seated_at_garage
    stood = value(res, "puppet:dad", "root.position", s2.end)
    assert abs(stood[0] - STREET_GARAGE[0]) < 0.5 and stood[1] == 0.0


def test_a_seated_puppet_rides_along_when_its_chair_is_re_placed():
    vc = _vc()
    res = _vc_run(
        vc._shot(vc._a(1, "wave", who="kid"), start={"kid": vc._at("door"), "mom": vc._at("bench_spot", **{"in": "chair.seat"})},
                 prop_start={"chair": {"at": "bench_spot"}}),
        vc._shot(vc._a(1, "wave", who="kid"), id="s2", prop_start={"chair": {"at": "table_spot"}}),
    )
    assert res.ok, messages(res)
    s1, s2 = res.timeline.shots
    before = value(res, "puppet:mom", "root.position", s1.action_start)
    after = value(res, "puppet:mom", "root.position", s2.start)
    assert value(res, "puppet:mom", "root.position", s2.start - 0.01) == before
    assert after[0] == pytest.approx(before[0] + YARD_TABLE_SPOT[0] - YARD_BENCH_SPOT[0])
    assert after[1:] == before[1:]


def test_a_puppet_seated_on_a_prop_resting_on_the_re_placed_prop_rides_along():
    # the chair stands on the sedan's roof... well, its passenger socket; mom sits on the chair
    vc = _vc()
    res = _vc_run(
        vc._shot(vc._a(0, "wave", who="kid"), set="street",
                 start={"kid": vc._at("garage"), "mom": vc._at("garage", **{"in": "chair.seat"})},
                 prop_start={"sedan": {"at": "garage"}, "chair": {"on": "sedan.passenger"}}),
        vc._shot(vc._a(0, "wave", who="kid"), id="s2", set="street", prop_start={"sedan": {"at": "corner"}}),
    )
    assert res.ok, messages(res)
    s1, s2 = res.timeline.shots
    before = value(res, "puppet:mom", "root.position", s1.action_start)
    after = value(res, "puppet:mom", "root.position", s2.start)
    assert after[0] == pytest.approx(before[0] + STREET_CORNER[0] - STREET_GARAGE[0])
    assert after[1:] == before[1:]
    # the chair is parented to the sedan's socket and follows on its own: no new keys for it
    assert track(res, "prop:chair", "parent") == [(0.0, "prop:sedan.passenger")]


def test_a_listed_puppet_is_placed_by_its_start_not_carried():
    vc = _vc()
    res = _vc_run(
        vc._shot(vc._a(1, "wave", who="kid"), start={"kid": vc._at("door"), "mom": vc._at("bench_spot", **{"in": "chair.seat"})},
                 prop_start={"chair": {"at": "bench_spot"}}),
        vc._shot(vc._a(1, "wave", who="kid"), id="s2", start={"mom": vc._at("curb")},
                 prop_start={"chair": {"at": "table_spot"}}),
    )
    assert res.ok, messages(res)
    assert value(res, "puppet:mom", "root.position", res.timeline.shots[1].start)[0] == pytest.approx(0.8)


def test_a_rider_whose_seat_ends_up_on_a_held_prop_is_a_start_error():
    props = {
        **PROPS,
        "tray": schemas.Prop(id="tray", name="tray", size="hand", art={"body": None}, sockets={"top": [0.5, 0.2]}),
        "stool": schemas.Prop(id="stool", name="stool", size="hand", art={"body": None}, sockets={"seat": [0.5, 0.3]}),
    }
    data = {"memoryId": "m", "cast": ["ann", "ben"], "props": ["tray", "stool"], "shots": [
        {"id": "s1", "set": "porch", "start": {"ann": {"at": "door", "in": "stool.seat"}, "ben": {"at": "gate"}},
         "propStart": {"stool": {"at": "door"}, "tray": {"at": "gate"}}},
        {"id": "s2", "set": "porch", "start": {"ben": {"at": "gate", "holding": "tray"}},
         "propStart": {"stool": {"on": "tray.top"}}},
    ]}
    res = compile_screenplay(Screenplay.model_validate(data), sets=SETS, props=props, people=PEOPLE)
    assert [(e.code, e.message) for e in res.errors] == [(
        "bad_start",
        "shot s2: ann is still seated in stool.seat, but stool now rests on something a puppet is holding; "
        "list ann in this shot's start",
    )]
