"""compile()'s schedule within a shot (compiler._schedule): follows run after whatever moves
their leader, actions on a follower wait for the follow, busy spans in shot time, follow
cycles and follow_order errors, a seeded property test with a brute-force oracle, and every
verb case still compiling as it did.

The scheduling tests patch a fake ``follow`` handler into ``compiler.HANDLERS``, so they do not
depend on the real one (verbs_motion)."""

from __future__ import annotations

import copy
import json
import random
import time
from pathlib import Path

import pytest
from verb_cases import CASES

import schemas
from screenplay import compiler as C
from screenplay.compiler import compile as compile_screenplay
from screenplay.schema import Action, CompileResult, Screenplay
from screenplay.tracks import evaluate

SAMPLES = Path(__file__).resolve().parents[3] / "web" / "public" / "samples"
MEMORY = json.loads((SAMPLES / "hymn_books.memory.json").read_text())

SETS = {s["id"]: schemas.SetDef.model_validate(s) for s in MEMORY["sets"]}
PROPS = {p["id"]: schemas.Prop.model_validate(p) for p in MEMORY["props"]}
PROPS["sedan"] = schemas.Prop.model_validate({
    "id": "sedan", "name": "sedan", "size": "vehicle", "aspect": 2.0, "art": {"body": None},
    "sockets": {"driver": [0.35, 0.55], "passenger": [0.6, 0.55]},
    "states": {"parked": {}, "open": {}, "closed": {}},
})
PEOPLE = {p["id"]: schemas.Person.model_validate(p) for p in MEMORY["people"]}
PEOPLE["kid"] = schemas.Person(id="kid", family_id="sample_family", name="Kid", character={"color": "#0a0", "build": "small"})


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


def acts(*items: dict) -> list[Action]:
    return [Action.model_validate(a) for a in items]


def schedule(actions: list[Action], cast, props=(), seated_in=None):
    return C._schedule(actions, [a.t for a in actions], cast, props, seated_in)


CAST = {"dad": None, "mom": None, "kid": None}


# ------------------------------------------------------------- follow scheduling and busy

FOLLOW_START = {
    "mom": {"at": "steps_top", "holding": "hymn_books", "facing": "left"},
    "dad": {"at": "steps_foot", "facing": "right"},
    "kid": {"at": "steps_left", "facing": "right"},
}


def follow_shot(actions):
    return {
        "memoryId": "m", "cast": ["dad", "mom", "kid"], "props": ["hymn_books"],
        "shots": [{"id": "s1", "set": "church_steps_1988", "start": FOLLOW_START, "actions": actions}],
    }


def root_keys(tracks, who):
    return [(k[0], k[1]) for tr in tracks.tracks() if (tr.target, tr.channel) == (f"puppet:{who}", "root.position")
            for k in tr.keys]


@pytest.fixture
def fake_follow(monkeypatch):
    """A stand-in follow handler (verbs_motion is another task's): it records the leader's
    root.position track as it is when the follow runs, then moves the follower over
    ``state["dur"]`` seconds to 0.2 left of where the leader's track ends."""
    state = {"dur": 1.0, "calls": []}

    def apply_follow(world, tracks, action, t, action_start, locate):
        leader_keys = root_keys(tracks, action.target)
        state["calls"].append({"who": action.who, "t": t, "now": world.now, "leader": leader_keys})
        lx, ly, lz = leader_keys[-1][1]
        here = tracks.value_at(f"puppet:{action.who}", "root.position", t)
        dest = [round(lx - 0.2, 6), ly, lz]
        tracks.extend([(f"puppet:{action.who}", "root.position", t, here),
                       (f"puppet:{action.who}", "root.position", t + state["dur"], dest)])
        world.move(action.who, tuple(dest))
        return t + state["dur"]

    monkeypatch.setitem(C.HANDLERS, "follow", apply_follow)
    return state


@pytest.fixture
def ran(monkeypatch):
    """The (who, do) of every real handler call for these verbs, in the order the compiler made them."""
    calls = []
    for verb in ("walk_to", "pose", "hand_to", "point_at", "wave", "hug", "drive_to"):
        real = C.HANDLERS[verb]

        def wrapped(world, tracks, action, t, action_start, locate, real=real):
            calls.append((action.who, action.do))
            return real(world, tracks, action, t, action_start, locate)

        monkeypatch.setitem(C.HANDLERS, verb, wrapped)
    return calls


def test_the_leaders_later_action_runs_before_the_follow(fake_follow, ran):
    res = run(follow_shot([
        {"t": 1, "who": "dad", "do": "follow", "target": "kid"},
        {"t": 4, "who": "kid", "do": "walk_to", "to": "curb"},
    ]))
    assert res.ok, messages(res)
    assert ran == [("kid", "walk_to")]
    (call,) = fake_follow["calls"]
    assert (call["who"], call["t"], call["now"]) == ("dad", 2.2, 1.0)
    # kid's walk to the curb (from t=5.2) is already on kid's track when dad starts following
    assert call["leader"][0] == (0.0, [-0.6, 0.0, 0.5])
    assert call["leader"][-1][1] == [0.8, 0.0, 0.5] and call["leader"][-1][0] > 5.2
    assert value(res, "puppet:dad", "root.position", 3.2) == [0.6, 0.0, 0.5]


def test_the_followers_next_action_waits_for_the_follow_and_starts_where_it_ended(fake_follow, ran):
    res = run(follow_shot([
        {"t": 1, "who": "dad", "do": "follow", "target": "kid"},
        {"t": 3, "who": "dad", "do": "walk_to", "to": "steps_left"},
        {"t": 2, "who": "kid", "do": "walk_to", "to": "curb"},
    ]))
    assert res.ok, messages(res)
    assert ran == [("kid", "walk_to"), ("dad", "walk_to")]
    assert [c["who"] for c in fake_follow["calls"]] == ["dad"]
    keys = track(res, "puppet:dad", "root.position")
    # start, the follow (2.2 -> 3.2), then the walk from the follow's end point back to steps_left
    assert keys[:3] == [(0.0, [0.0, 0.0, 0.5]), (2.2, [0.0, 0.0, 0.5]), (3.2, [0.6, 0.0, 0.5])]
    assert keys[3] == (4.2, [0.6, 0.0, 0.5])
    assert keys[-1][1] == [-0.6, 0.0, 0.5]
    assert value(res, "puppet:dad", "root.facing", keys[-1][0]) == -1


def test_a_follow_cycle_is_reported_and_the_rest_runs_in_time_order(fake_follow, ran):
    res = run(follow_shot([
        {"t": 2, "who": "kid", "do": "follow", "target": "dad"},
        {"t": 1, "who": "dad", "do": "follow", "target": "kid"},
        {"t": 0, "who": "mom", "do": "pose", "pose": "wave"},
    ]))
    assert [(e.code, e.message, e.t, e.who, e.do) for e in res.errors] == [(
        "follow_cycle", "shot s1, t=1: dad cannot follow; dad and kid follow each other; only one can lead",
        1.0, "dad", "follow",
    )]
    assert ran == [("mom", "pose")]
    assert [(c["who"], c["now"]) for c in fake_follow["calls"]] == [("kid", 2.0)]


def test_schedule_runs_a_follow_chain_leader_first():
    actions = acts(
        {"t": 0, "who": "kid", "do": "follow", "target": "dad"},     # 0: waits for dad's follow and walk
        {"t": 1, "who": "dad", "do": "follow", "target": "mom"},     # 1: waits for mom's walk
        {"t": 2, "who": "dad", "do": "walk_to", "to": "curb"},       # 2: behind dad's follow
        {"t": 3, "who": "mom", "do": "walk_to", "to": "curb"},       # 3
        {"t": 0.5, "do": "rain"},                                    # 4: no who, never waits
        {"t": 4, "who": "kid", "do": "follow", "target": "nobody"},  # 5: not a cast member, no wait
    )
    assert schedule(actions, CAST) == ([4, 3, 1, 2, 0, 5], [])
    # a three-way cycle: its earliest follow is reported, naming every link; the rest run in time order
    loop = acts(
        {"t": 2, "who": "kid", "do": "follow", "target": "dad"},
        {"t": 1, "who": "dad", "do": "follow", "target": "mom"},
        {"t": 3, "who": "mom", "do": "follow", "target": "kid"},
        {"t": 0, "who": "mom", "do": "wave"},
    )
    assert schedule(loop, CAST) == (
        [3, 0, 2], [(1, "follow_cycle", "dad follows mom, mom follows kid and kid follows dad; only one can lead")],
    )


def test_schedule_reports_only_the_follows_on_a_cycle():
    cast = {"dad": None, "mom": None, "kid": None, "gran": None, "uncle": None}
    # kid follows dad, who is on the dad <-> mom cycle: kid waits on the cycle but is not on it
    on_a_cycle = acts(
        {"t": 0.5, "who": "kid", "do": "follow", "target": "dad"},
        {"t": 1, "who": "dad", "do": "follow", "target": "mom"},
        {"t": 2, "who": "mom", "do": "follow", "target": "dad"},
    )
    assert schedule(on_a_cycle, cast) == (
        [0, 2], [(1, "follow_cycle", "dad and mom follow each other; only one can lead")],
    )
    # two separate cycles are each reported once, on their earliest follow, in time order
    two = acts(
        {"t": 4, "who": "uncle", "do": "follow", "target": "gran"},
        {"t": 3, "who": "gran", "do": "follow", "target": "uncle"},
        {"t": 2, "who": "mom", "do": "follow", "target": "dad"},
        {"t": 1, "who": "kid", "do": "follow", "target": "gran"},  # waits on the second cycle only
        {"t": 5, "who": "dad", "do": "follow", "target": "mom"},
    )
    assert schedule(two, cast) == (
        [3, 0, 4],
        [(2, "follow_cycle", "mom and dad follow each other; only one can lead"),
         (1, "follow_cycle", "gran and uncle follow each other; only one can lead")],
    )
    # retimed t, not the written t, picks the earliest follow of a cycle; once mom's follow is
    # left out, dad's follow can run, and kid's follow still waits for it
    assert C._schedule(on_a_cycle, [0.5, 3.0, 2.0], cast) == (
        [1, 0], [(2, "follow_cycle", "mom and dad follow each other; only one can lead")],
    )


def test_a_follower_waiting_on_a_cycle_is_not_reported_and_still_follows(fake_follow, ran):
    res = run(follow_shot([
        {"t": 0.5, "who": "kid", "do": "follow", "target": "dad"},
        {"t": 1, "who": "dad", "do": "follow", "target": "mom"},
        {"t": 2, "who": "mom", "do": "follow", "target": "dad"},
    ]))
    assert [(e.code, e.message, e.t, e.who, e.do) for e in res.errors] == [(
        "follow_cycle", "shot s1, t=1: dad cannot follow; dad and mom follow each other; only one can lead",
        1.0, "dad", "follow",
    )]
    assert [(c["who"], c["now"]) for c in fake_follow["calls"]] == [("kid", 0.5), ("mom", 2.0)]


def test_the_followers_own_action_inside_the_follow_is_busy(fake_follow):
    fake_follow["dur"] = 3.0
    res = run(follow_shot([
        {"t": 0, "who": "kid", "do": "walk_to", "to": "curb"},
        {"t": 1, "who": "dad", "do": "follow", "target": "kid"},
        {"t": 2, "who": "dad", "do": "pose", "pose": "wave"},
    ]))
    assert [(e.code, e.message) for e in res.errors] == [
        ("busy", "shot s1, t=2: dad cannot pose; dad is still following until t=4"),
    ]


HAND_OVER_DURING_FOLLOW = [
    {"t": 0, "who": "kid", "do": "walk_to", "to": "curb"},
    {"t": 5, "who": "kid", "do": "pose", "pose": "wave"},  # a pose can move kid: it holds the follow back
    {"t": 1, "who": "dad", "do": "follow", "target": "kid"},
    {"t": 2, "who": "mom", "do": "hand_to", "what": "hymn_books", "to": "dad"},
]


def test_a_hand_over_to_the_follower_waits_for_the_follow_and_sees_where_it_ends(fake_follow, ran):
    # The hand_to (t=2) names dad, so it waits for dad's follow (t=1), which waits for kid's
    # pose (t=5). Mom then hands the books to dad where the follow left him (x=0.6), exactly as
    # if dad had walked there: from the top of the steps that is out of reach.
    fake_follow["dur"] = 0.5
    res = run(follow_shot(HAND_OVER_DURING_FOLLOW))
    assert ran == [("kid", "walk_to"), ("kid", "pose"), ("mom", "hand_to")]
    assert [(c["who"], c["now"]) for c in fake_follow["calls"]] == [("dad", 1.0)]
    walked = follow_shot([
        {"t": 0, "who": "kid", "do": "walk_to", "to": "curb"},
        {"t": 0, "who": "dad", "do": "walk_to", "to": "beside_kid"},
        {"t": 2, "who": "mom", "do": "hand_to", "what": "hymn_books", "to": "dad"},
    ])
    walked["shots"][0]["marks"] = {"beside_kid": [0.8, 0.0]}  # x=0.6 on stage
    expected = 'shot s1, t=2: mom cannot hand_to "hymn_books"; dad\'s arm_r cannot reach: target is 0.26 from the shoulder, reach is 0.19'
    assert messages(run(walked)) == [expected]
    assert [(e.code, e.message) for e in res.errors] == [("out_of_reach", expected)]


def test_a_hand_over_inside_the_follow_is_busy(fake_follow, ran):
    fake_follow["dur"] = 3.0
    res = run(follow_shot(HAND_OVER_DURING_FOLLOW))
    assert ran == [("kid", "walk_to"), ("kid", "pose"), ("mom", "hand_to")]
    assert [(e.code, e.message, e.t, e.who, e.do) for e in res.errors] == [(
        "busy", 'shot s1, t=2: mom cannot hand_to "hymn_books"; dad is still following until t=4',
        2.0, "mom", "hand_to",
    )]


def test_a_hand_over_before_a_later_follow_is_not_a_false_busy(fake_follow, ran):
    # The hand_to (t=2) keeps dad busy taking the books until t=2.9; his follow starts at t=3,
    # after kid's pose (t=5) in schedule order. Busy comes from spans in shot time: no clash.
    fake_follow["dur"] = 0.5
    res = run(follow_shot([
        {"t": 0, "who": "kid", "do": "walk_to", "to": "curb"},
        {"t": 5, "who": "kid", "do": "pose", "pose": "wave"},
        {"t": 3, "who": "dad", "do": "follow", "target": "kid"},
        {"t": 2, "who": "mom", "do": "hand_to", "what": "hymn_books", "to": "dad"},
    ]))
    assert res.ok, messages(res)
    assert ran == [("kid", "walk_to"), ("mom", "hand_to"), ("kid", "pose")]
    assert [c["now"] for c in fake_follow["calls"]] == [3.0]
    assert value(res, "prop:hymn_books", "parent", 8.0) == "puppet:dad.hand_r"
    assert value(res, "puppet:dad", "root.position", 4.7) == [0.6, 0.0, 0.5]


@pytest.fixture
def fake_shower(monkeypatch):
    """confetti (no who, names nobody) that keeps dad busy "being showered" for ``state["dur"]``:
    a handler occupying a puppet the action does not name, as a vehicle carrying a passenger does."""
    state = {"dur": 1.0}

    def apply_confetti(world, tracks, action, t, action_start, locate):
        world.occupy("dad", round(world.now + state["dur"], 6), "being showered", at=world.now)
        return t + state["dur"]

    monkeypatch.setitem(C.HANDLERS, "confetti", apply_confetti)
    return state


SHOWER_DURING_FOLLOW = [
    {"t": 0, "who": "kid", "do": "walk_to", "to": "curb"},
    {"t": 5, "who": "kid", "do": "pose", "pose": "wave"},
    {"t": 1, "who": "dad", "do": "follow", "target": "kid"},
    {"t": 2, "do": "confetti"},  # runs before the held-back follow
]


def test_a_held_back_follow_that_runs_into_a_later_span_is_busy(fake_follow, fake_shower, ran):
    fake_follow["dur"] = 3.0
    res = run(follow_shot(SHOWER_DURING_FOLLOW))
    assert ran == [("kid", "walk_to"), ("kid", "pose")]
    assert [(e.code, e.message, e.t, e.who, e.do) for e in res.errors] == [(
        "busy",
        "shot s1, t=1: dad cannot follow; dad would still be following until t=4, but starts being showered at t=2",
        1.0, "dad", "follow",
    )]


def test_a_clash_with_a_later_span_is_stated_in_the_written_frame(fake_follow, fake_shower, ran):
    # A cued line lands the follow on "along" (shot t=1.5, written t=1): the follow and every
    # uncued action after it move 0.5 s later. The clash times are shown for the follow as
    # written, so they read exactly as in the uncued shot above.
    fake_follow["dur"] = 3.0
    data = follow_shot(SHOWER_DURING_FOLLOW)
    data["shots"][0]["narration"] = [{"t": 1.0, "text": "he went along", "cue": "follow"}]
    res = run(data)
    assert [(e.code, e.message) for e in res.errors] == [(
        "busy",
        "shot s1, t=1: dad cannot follow; dad would still be following until t=4, but starts being showered at t=2",
    )]
    assert [c["now"] for c in fake_follow["calls"]] == [1.5]


def test_a_held_back_follow_that_ends_before_a_later_span_is_not_busy(fake_follow, fake_shower):
    fake_follow["dur"] = 0.5
    res = run(follow_shot(SHOWER_DURING_FOLLOW))
    assert res.ok, messages(res)
    assert [c["now"] for c in fake_follow["calls"]] == [1.0]


def mom_points_at_dad(*extra):
    return follow_shot([
        {"t": 0, "who": "kid", "do": "walk_to", "to": "curb"},
        {"t": 1, "who": "dad", "do": "follow", "target": "kid"},
        {"t": 4, "who": "mom", "do": "point_at", "target": "dad"},
        *extra,
    ])


def test_pointing_at_the_follower_does_not_depend_on_an_unrelated_later_action(fake_follow):
    # Without kid's pose, the follow would run at its own time anyway; with it, the follow is
    # held back, and mom's point (which names dad) is held back behind it. Mom's keys agree.
    alone = run(mom_points_at_dad())
    held = run(mom_points_at_dad({"t": 6, "who": "kid", "do": "pose", "pose": "wave"}))
    assert alone.ok and held.ok, (messages(alone), messages(held))
    mom = sorted({tr.channel for tr in alone.timeline.tracks if tr.target == "puppet:mom"})
    assert mom == sorted({tr.channel for tr in held.timeline.tracks if tr.target == "puppet:mom"})
    for channel in mom:
        assert track(alone, "puppet:mom", channel) == track(held, "puppet:mom", channel), channel
    # mom turns toward dad, who is at x=0.6 when she points (t=5.2), right of the steps' top
    assert value(held, "puppet:dad", "root.position", 5.2) == [0.6, 0.0, 0.5]
    assert value(held, "puppet:mom", "root.facing", 6.0) == 1


# The follow_order message for the 2-puppet case: the leader moves toward its own follower.
LEADER_HUGS_FOLLOWER = (
    "kid's hug at t=6 needs dad where dad's follow of kid at t=1 leaves dad, but that follow is "
    "only worked out after everything that moves kid, this hug included; move the hug before t=1, "
    "or drop the follow"
)


def test_a_leader_moving_to_its_follower_is_a_follow_order_error(fake_follow, ran):
    res = run(follow_shot([
        {"t": 0, "who": "kid", "do": "walk_to", "to": "curb"},
        {"t": 1, "who": "dad", "do": "follow", "target": "kid"},
        {"t": 6, "who": "kid", "do": "hug", "target": "dad"},
    ]))
    assert [(e.code, e.message, e.t, e.who, e.do) for e in res.errors] == [(
        "follow_order", f"shot s1, t=6: kid cannot hug; {LEADER_HUGS_FOLLOWER}", 6.0, "kid", "hug",
    )]
    assert len(LEADER_HUGS_FOLLOWER) <= 220
    # the hug is left out; the follow still runs, after kid's walk
    assert ran == [("kid", "walk_to")]
    assert [(c["who"], c["now"]) for c in fake_follow["calls"]] == [("dad", 1.0)]


def test_schedule_orders_by_shared_puppets_and_props_around_a_follow():
    # mom's pick_up names the ball dad drops behind his follow, so it waits for the drop
    ball = acts(
        {"t": 1, "who": "dad", "do": "follow", "target": "kid"},
        {"t": 2, "who": "dad", "do": "drop", "what": "ball"},
        {"t": 3, "who": "mom", "do": "pick_up", "what": "ball"},
        {"t": 4, "who": "kid", "do": "walk_to", "to": "curb"},
        {"t": 2.5, "who": "mom", "do": "wave"},  # names nothing shared: runs at its time
    )
    assert schedule(ball, CAST, {"ball"}) == ([4, 3, 0, 1, 2], [])
    # without knowing ball is a prop, nothing ties the pick_up to the drop
    assert schedule(ball, CAST) == ([4, 2, 3, 0, 1], [])
    # a loop through other puppets: kid's hug needs mom's hand_to, which needs dad's follow
    chain = acts(
        {"t": 1, "who": "dad", "do": "follow", "target": "kid"},
        {"t": 4, "who": "mom", "do": "hand_to", "what": "books", "to": "dad"},
        {"t": 6, "who": "kid", "do": "hug", "target": "mom"},
    )
    assert schedule(chain, CAST, {"books"}) == (
        [0, 1],
        [(2, "follow_order",
          ("kid's hug at t=6 has to come after mom's hand_to at t=4, which comes after dad's follow of kid "
           "at t=1, but that follow is only worked out after everything that moves kid, this hug included; "
           "move the hug before t=1, or drop the follow"))],
    )
    # a leader that follows someone else and moves to its own follower: the whole run of follows is named
    run_of_follows = acts(
        {"t": 1, "who": "dad", "do": "follow", "target": "kid"},
        {"t": 2, "who": "kid", "do": "follow", "target": "mom"},
        {"t": 5, "who": "mom", "do": "hug", "target": "dad"},
    )
    assert schedule(run_of_follows, CAST) == (
        [1, 0],
        [(2, "follow_order",
          ("mom's hug at t=5 needs dad where dad's follow of kid at t=1 leaves dad, but dad follows kid and "
           "kid follows mom, so that follow is only worked out after everything that moves mom, this hug "
           "included; move the hug before t=1, or drop one of those follows"))],
    )


def test_follow_order_through_a_shared_prop():
    # dad's follow waits for kid's pick_up (a move), which waits for dad's pick_up of the same
    # ball (earlier), which waits behind dad's follow. kid's later walk only waits: not reported.
    actions = acts(
        {"t": 1, "who": "dad", "do": "follow", "target": "kid"},
        {"t": 2, "who": "dad", "do": "pick_up", "what": "ball"},
        {"t": 3, "who": "kid", "do": "pick_up", "what": "ball"},
        {"t": 6, "who": "kid", "do": "walk_to", "to": "curb"},
    )
    assert schedule(actions, CAST, {"ball"}) == (
        [3, 0, 1],
        [(2, "follow_order",
          ("kid's pick_up at t=3 has to come after dad's pick_up at t=2, which comes after dad's follow of "
           "kid at t=1, but that follow is only worked out after everything that moves kid, this pick_up "
           "included; move the pick_up before t=1, or drop the follow"))],
    )


def test_follow_order_on_a_loop_through_two_follows_names_the_other_move():
    # dad follows kid and mom follows gran; kid hugs mom (needs mom's follow) and gran hugs dad
    # (needs dad's follow): one loop through both follows. The earliest move on it is reported.
    actions = acts(
        {"t": 1, "who": "dad", "do": "follow", "target": "kid"},
        {"t": 1, "who": "mom", "do": "follow", "target": "gran"},
        {"t": 4, "who": "kid", "do": "hug", "target": "mom"},
        {"t": 4, "who": "gran", "do": "hug", "target": "dad"},
    )
    assert schedule(actions, {**CAST, "gran": None}) == (
        [0, 3, 1],
        [(2, "follow_order",
          ("kid's hug at t=4 needs mom where mom's follow of gran at t=1 leaves mom, but that follow is only "
           "worked out after everything that moves gran, gran's hug at t=4 included; move the hug before t=1, "
           "or drop the follow"))],
    )


def test_follow_scheduling_is_deterministic(fake_follow):
    data = follow_shot(HAND_OVER_DURING_FOLLOW)
    fake_follow["dur"] = 0.5
    a = run(data).model_dump_json()
    b = run(copy.deepcopy(data)).model_dump_json()
    assert a == b


def test_without_follows_actions_run_in_time_order_then_list_order(ran):
    res = run(follow_shot([
        {"t": 2, "who": "kid", "do": "pose", "pose": "wave"},
        {"t": 1, "who": "dad", "do": "pose", "pose": "wave"},
        {"t": 1, "who": "mom", "do": "pose", "pose": "wave"},
    ]))
    assert res.ok, messages(res)
    assert ran == [("dad", "pose"), ("mom", "pose"), ("kid", "pose")]


# ------------------------------------------- regressions: what a follow waits for (bugs found)

def test_regression_a_leaders_still_action_does_not_hold_the_follow_back():
    """Bug: a follow waited for every action of its leader, so a follower acting on its leader
    after the follow, with any later leader action, was a false follow_order loop."""
    minimal = acts(
        {"t": 1, "who": "dad", "do": "follow", "target": "kid"},
        {"t": 2, "who": "dad", "do": "point_at", "target": "kid"},
        {"t": 3, "who": "kid", "do": "wave"},
    )
    assert schedule(minimal, CAST) == ([0, 1, 2], [])
    # every still verb lets the follow go first; any other verb (a move) holds it back
    for verb in sorted(C.STILL_VERBS):
        assert schedule(acts({"t": 1, "who": "dad", "do": "follow", "target": "kid"},
                             {"t": 2, "who": "kid", "do": verb}), CAST)[0] == [0, 1], verb
    for verb in ("walk_to", "run_to", "enter", "exit", "follow", "pose", "pick_up", "sit_on",
                 "stand_up", "kneel", "hug", "get_in", "get_out", "drive_to", "ride_to"):
        assert schedule(acts({"t": 1, "who": "dad", "do": "follow", "target": "kid"},
                             {"t": 2, "who": "kid", "do": verb}), CAST)[0] == [1, 0], verb


def test_regression_the_follower_points_at_its_leader_who_waves_later(fake_follow, ran):
    """The minimal compile: kid walks, dad follows and points at kid, then kid waves."""
    res = run(follow_shot([
        {"t": 0, "who": "kid", "do": "walk_to", "to": "curb"},
        {"t": 1, "who": "dad", "do": "follow", "target": "kid"},
        {"t": 4, "who": "dad", "do": "point_at", "target": "kid"},
        {"t": 7, "who": "kid", "do": "wave"},
    ]))
    assert res.ok, messages(res)
    assert ran == [("kid", "walk_to"), ("dad", "point_at"), ("kid", "wave")]


def test_regression_a_leader_pointing_at_its_follower_sees_where_the_follow_leaves_him(fake_follow, ran):
    """Was a follow_order error. kid's point_at moves nobody, so dad's follow runs first and kid
    points at dad where the follow left him (x=0.6, left of kid at the curb)."""
    res = run(follow_shot([
        {"t": 0, "who": "kid", "do": "walk_to", "to": "curb"},
        {"t": 1, "who": "dad", "do": "follow", "target": "kid"},
        {"t": 6, "who": "kid", "do": "point_at", "target": "dad"},
    ]))
    assert res.ok, messages(res)
    assert ran == [("kid", "walk_to"), ("kid", "point_at")]
    assert [(c["who"], c["now"]) for c in fake_follow["calls"]] == [("dad", 1.0)]
    assert value(res, "puppet:dad", "root.position", 7.2) == [0.6, 0.0, 0.5]
    assert value(res, "puppet:kid", "root.facing", 8.0) == -1


def test_regression_a_follow_of_a_passenger_waits_for_the_drive(fake_follow, ran):
    """Bug: a follow waited only for its leader's own actions, so following a passenger ran
    before the drive that carries the passenger, and the follower missed the ride."""
    minimal = acts(
        {"t": 1, "who": "dad", "do": "follow", "target": "kid"},
        {"t": 3, "who": "mom", "do": "drive_to", "what": "sedan", "to": "curb"},
    )
    assert schedule(minimal, CAST, {"sedan"}, {"kid": "sedan", "mom": "sedan"}) == ([1, 0], [])
    assert schedule(minimal, CAST, {"sedan"}) == ([0, 1], [])  # kid is not in the sedan
    # getting in during the shot counts too, and so does another rider's ride_to
    got_in = acts(
        {"t": 1, "who": "dad", "do": "follow", "target": "kid"},
        {"t": 0, "who": "kid", "do": "get_in", "what": "sedan"},
        {"t": 3, "who": "gran", "do": "ride_to", "what": "sedan", "to": "curb"},
    )
    assert schedule(got_in, {**CAST, "gran": None}, {"sedan"}) == ([1, 2, 0], [])

    data = follow_shot([
        {"t": 1, "who": "dad", "do": "follow", "target": "kid"},
        {"t": 3, "who": "mom", "do": "drive_to", "what": "sedan", "to": "steps_left"},
    ])
    data["props"].append("sedan")
    shot = data["shots"][0]
    shot["start"] = {"dad": {"at": "steps_foot", "facing": "right"},
                     "mom": {"at": "curb", "in": "sedan.driver"},
                     "kid": {"at": "curb", "in": "sedan.passenger"}}
    shot["propStart"] = {"sedan": {"at": "curb"}}
    res = run(data)
    assert res.ok, messages(res)
    assert ran == [("mom", "drive_to")]
    (call,) = fake_follow["calls"]
    # the ride is on kid's track when dad starts following: kid ends up near steps_left
    assert call["leader"][-1][0] > 4.2 and call["leader"][-1][1][0] < call["leader"][0][1][0]


def sedan_shot(actions):
    """mom in the sedan's driver seat and kid in its passenger seat at the curb; dad at the steps."""
    data = follow_shot(actions)
    data["props"].append("sedan")
    shot = data["shots"][0]
    shot["start"] = {"dad": {"at": "steps_foot", "facing": "right"},
                     "mom": {"at": "curb", "in": "sedan.driver"},
                     "kid": {"at": "curb", "in": "sedan.passenger"}}
    shot["propStart"] = {"sedan": {"at": "curb"}}
    return data


def test_regression_a_drive_after_the_leader_got_out_does_not_hold_the_follow_back(fake_follow, ran):
    """Bug: a follow waited for every drive of a vehicle its leader was in at any time in the
    shot. kid gets out at t=0, so mom's drive at t=4 never moves kid, but dad's follow waited
    for it; mom's wave at dad (behind the follow) then made a false follow_order loop."""
    minimal = acts(
        {"t": 0, "who": "kid", "do": "get_out", "what": "sedan"},
        {"t": 1, "who": "dad", "do": "follow", "target": "kid"},
        {"t": 2, "who": "mom", "do": "wave", "target": "dad"},
        {"t": 4, "who": "mom", "do": "drive_to", "what": "sedan", "to": "steps_left"},
    )
    seated = {"kid": "sedan", "mom": "sedan"}
    assert schedule(minimal, CAST, {"sedan"}, seated) == ([0, 1, 2, 3], [])
    # once kid gets back in, the next drive carries kid again and the follow waits for it
    back_in = acts(
        {"t": 0, "who": "kid", "do": "get_out", "what": "sedan"},
        {"t": 1, "who": "dad", "do": "follow", "target": "kid"},
        {"t": 3, "who": "kid", "do": "get_in", "what": "sedan"},
        {"t": 4, "who": "mom", "do": "drive_to", "what": "sedan", "to": "steps_left"},
    )
    assert schedule(back_in, CAST, {"sedan"}, seated) == ([0, 2, 3, 1], [])
    # end to end: it compiles, and the follow runs at its own time, before the drive
    res = run(sedan_shot([a.model_dump(exclude_none=True) for a in minimal]))
    assert res.ok, messages(res)
    assert ran == [("mom", "wave"), ("mom", "drive_to")]
    assert [(c["who"], c["now"]) for c in fake_follow["calls"]] == [("dad", 1.0)]


def test_regression_a_drive_before_the_leader_gets_in_is_not_blamed():
    """Bug: the same rule made a drive at t=3 count as moving kid, who only gets in at t=5. The
    loop here is real (dad's follow needs kid's get_in, which reads where the drive left the
    sedan, after mom's hand_to to dad), but it was reported on the drive ("this drive_to
    included"). It is the get_in that moves kid."""
    actions = acts(
        {"t": 1, "who": "dad", "do": "follow", "target": "kid"},
        {"t": 2, "who": "mom", "do": "hand_to", "what": "books", "to": "dad"},
        {"t": 3, "who": "mom", "do": "drive_to", "what": "sedan", "to": "curb"},
        {"t": 5, "who": "kid", "do": "get_in", "what": "sedan"},
    )
    assert schedule(actions, CAST, {"books", "sedan"}, {"mom": "sedan"}) == (
        [0, 1, 2],
        [(3, "follow_order",
          ("kid's get_in at t=5 has to come after mom's drive_to at t=3, which comes after mom's hand_to "
           "at t=2, which comes after dad's follow of kid at t=1, but that follow is only worked out after "
           "everything that moves kid, this get_in included; move the get_in before t=1, or drop the follow"))],
    )
    # without the hand_to there is no loop: the drive runs at its time, and the follow after the get_in
    assert schedule([actions[0], actions[2], actions[3]], CAST, {"sedan"}, {"mom": "sedan"}) == ([1, 2, 0], [])


# ----------------------------------------------------- property test with a brute-force oracle

PUPPETS = ("dad", "mom", "kid", "gran")
PROP_POOL = ("ball", "books", "cup", "sedan")
MARKS = ("curb", "steps_top")
# Kept apart from C.STILL_VERBS on purpose: the oracle states the rule independently.
ORACLE_STILL = {"turn_to", "wave", "point_at", "lean_toward", "hold", "drop", "hand_to", "put_on",
                "place_on", "open", "close", "set_state"}


def random_shot(rng: random.Random):
    """2-4 puppets, 0-3 props (one may be a vehicle someone starts in), 2-10 actions mixing
    follows (of a cast member, of themselves, of a prop or of nobody) with actions that name
    puppets and props, moves and still actions, vehicle moves and actions without ``who``."""
    cast = rng.sample(PUPPETS, rng.randint(2, 4))
    props = rng.sample(PROP_POOL, rng.randint(0, 3))
    named = cast + props + list(MARKS) + ["nobody"]
    seated_in = {p: "sedan" for p in cast if "sedan" in props and rng.random() < 0.3}
    actions = []
    for _ in range(rng.randint(2, 10)):
        who = rng.choice(cast)
        thing = rng.choice(props) if props else "ball"
        roll = rng.random()
        if "sedan" in props and rng.random() < 0.3:  # riders get in, get out and are driven about
            a = {"who": who, "do": rng.choice(["get_in", "get_out", "drive_to", "ride_to"]), "what": "sedan", "to": "curb"}
        elif roll < 0.35:
            a = {"who": who, "do": "follow", "target": rng.choice(cast + cast + [who, "nobody", thing])}
        elif roll < 0.43:
            a = {"who": who, "do": "hand_to", "what": thing, "to": rng.choice(cast)}
        elif roll < 0.5:
            a = {"who": who, "do": "point_at", "target": rng.choice(named)}
        elif roll < 0.64:
            a = {"who": who, "do": "hug", "target": rng.choice(cast)}
        elif roll < 0.71:
            a = {"who": who, "do": "pick_up", "what": thing}
        elif roll < 0.77:
            a = {"who": who, "do": "walk_to", "to": rng.choice(MARKS)}
        elif roll < 0.82:
            a = {"who": who, "do": "wave", **({"target": rng.choice(named)} if rng.random() < 0.5 else {})}
        elif roll < 0.88:
            a = {"who": who, "do": rng.choice(["get_in", "get_out", "drive_to", "ride_to"]), "what": "sedan", "to": "curb"}
        elif roll < 0.94:
            a = {"do": "set_state", "what": thing, "state": "open"}
        else:
            a = {"do": "confetti", "at": rng.choice(named)}
        actions.append(Action.model_validate({"t": rng.choice([0, 0.5, 1, 1, 2, 3, 3, 4, 5, 6]), **a}))
    # cues move some actions: retimed t differs from the written t, sometimes into a tie
    retimed = [a.t + (rng.choice([-1.0, -0.5, 0.5, 2.0]) if rng.random() < 0.25 else 0.0) for a in actions]
    return actions, retimed, cast, props, seated_in


def oracle_touches(a: Action, cast, props) -> set[str]:
    names = {a.who} if a.who is not None else set()
    if a.do != "follow":
        names |= {v for v in (a.to, a.target, a.at, a.on, a.what) if v is not None and (v in cast or v in props)}
    return names


def precedence(actions, retimed, cast, props, seated_in) -> dict[tuple[int, int], str]:
    """Every constraint "i runs before j", labelled (b), (c) or (d)."""
    key = [(retimed[i], i) for i in range(len(actions))]
    touch = [oracle_touches(a, cast, props) for a in actions]
    # Who sits where as the shot plays out in time order, and so who each vehicle move carries.
    seat = dict(seated_in)
    carries: dict[int, set[str]] = {}
    for i in sorted(range(len(actions)), key=key.__getitem__):
        a = actions[i]
        if a.do in ("drive_to", "ride_to"):
            carries[i] = {p for p, v in seat.items() if v == a.what}
        if a.do == "get_in":
            seat[a.who] = a.what
        elif a.do == "get_out" and seat.get(a.who) == a.what:
            del seat[a.who]
    edges: dict[tuple[int, int], str] = {}
    for j, f in enumerate(actions):
        if f.do != "follow" or f.target not in cast or f.target == f.who:
            continue
        leader = f.target
        for i, a in enumerate(actions):
            if i != j and ((a.who == leader and a.do not in ORACLE_STILL) or leader in carries.get(i, ())):
                edges[(i, j)] = "c"
    for i, a in enumerate(actions):
        for j, b in enumerate(actions):
            if key[i] < key[j]:
                if a.who is not None and a.who == b.who:
                    edges.setdefault((i, j), "b")
                elif touch[i] & touch[j]:
                    edges.setdefault((i, j), "d")
    return edges


def brute_force_acyclic(nodes, edges) -> bool:
    """Peel off actions nothing left must precede, one round at a time; a loop never peels."""
    left = set(nodes)
    while left:
        free = {v for v in left if not any(i in left and j == v for (i, j) in edges)}
        if not free:
            return False
        left -= free
    return True


def reaches(edges, start, goal) -> bool:
    seen, todo = set(), [j for (i, j) in edges if i == start]
    while todo:
        v = todo.pop()
        if v == goal:
            return True
        if v not in seen:
            seen.add(v)
            todo += [j for (i, j) in edges if i == v]
    return False


def check_shot(actions, retimed, cast, props, seated_in) -> list[str]:
    cast_map = dict.fromkeys(cast)
    order, left_out = C._schedule(actions, retimed, cast_map, props, seated_in)
    out = [i for i, _, _ in left_out]
    problems = []
    # (a) every action exactly once
    if sorted(order + out) != list(range(len(actions))):
        problems.append(f"(a) order={order} left_out={out}")
    pos = {i: k for k, i in enumerate(order)}
    edges = precedence(actions, retimed, cast, props, seated_in)
    # (b) a puppet's own actions, (c) a follow after whatever moves its leader, (d) touching
    # actions in time order. Where (c) and (d) disagree the pair is on a loop, so one of them
    # is left out: among the actions that run, every constraint holds.
    for (i, j), kind in sorted(edges.items()):
        if i in pos and j in pos and pos[i] > pos[j]:
            problems.append(f"({kind}) {i} must run before {j}: order={order}")
    # (e) nothing is left out exactly when the constraints have no loop
    if brute_force_acyclic(range(len(actions)), edges) != (not out):
        problems.append(f"(e) acyclic={not out} is wrong: left_out={left_out}")
    # (g) with no loop, nothing waits more than it must: the earliest action (retimed t, index)
    # whose constraints are all met runs next. This catches a wait the oracle does not have.
    if not out:
        earliest_first, left = [], set(range(len(actions)))
        while left:
            ready = [v for v in left if not any(i in left and j == v for (i, j) in edges)]
            if not ready:
                break
            v = min(ready, key=lambda v: (retimed[v], v))
            earliest_first.append(v)
            left.discard(v)
        if order != earliest_first:
            problems.append(f"(g) order={order}, earliest runnable first gives {earliest_first}")
    # every action left out is on a loop, and the codes are the two loop codes
    for i, code, reason in left_out:
        if not reaches(edges, i, i):
            problems.append(f"left out {i} is on no loop")
        if code not in ("follow_cycle", "follow_order") or not reason:
            problems.append(f"bad report {code!r} {reason!r}")
    # (f) the same answer again
    if C._schedule(actions, retimed, cast_map, props, seated_in) != (order, left_out):
        problems.append("(f) a second call differs")
    return problems


def test_schedule_property_over_random_shots():
    started = time.perf_counter()
    shots = loops = 0
    for seed in range(2500):
        shot = random_shot(random.Random(seed))
        problems = check_shot(*shot)
        assert not problems, (seed, problems, [a.model_dump(exclude_none=True) for a in shot[0]], shot[1])
        shots += 1
        loops += bool(C._schedule(shot[0], shot[1], dict.fromkeys(shot[2]), shot[3], shot[4])[1])
    assert shots == 2500
    assert 250 < loops < 2250, loops  # the generator makes plenty of shots with and without loops
    assert time.perf_counter() - started < 10


def test_the_oracle_flags_a_wrong_order():
    """The property test can fail: a schedule that ignores the follow wait breaks (c) and (e)."""
    actions = acts({"t": 1, "who": "dad", "do": "follow", "target": "kid"},
                   {"t": 2, "who": "kid", "do": "walk_to", "to": "curb"})
    edges = precedence(actions, [1.0, 2.0], ["dad", "kid"], [], {})
    assert edges == {(1, 0): "c"}
    loop = acts({"t": 1, "who": "dad", "do": "follow", "target": "kid"},
                {"t": 2, "who": "kid", "do": "hug", "target": "dad"})
    assert not brute_force_acyclic(range(2), precedence(loop, [1.0, 2.0], ["dad", "kid"], [], {}))


def test_the_property_test_catches_the_old_vehicle_rule(monkeypatch):
    """The oracle's vehicle rule is worked out on its own (who sits where, in time order), so
    the old rule (a follow waits for every drive of any vehicle its leader is in at any time
    in the shot) fails the property test."""
    def old_moves(actions, who, seated_in, by_time):
        rides = {a.what for a in actions if a.who == who and a.do == "get_in"} | (
            {seated_in[who]} if who in seated_in else set())
        return [i for i in by_time
                if (actions[i].who == who and actions[i].do not in C.STILL_VERBS)
                or (actions[i].do in ("drive_to", "ride_to") and actions[i].what in rides)]

    monkeypatch.setattr(C, "_moves", old_moves)
    failing = [seed for seed in range(2500) if check_shot(*random_shot(random.Random(seed)))]
    assert failing, "the property test no longer tells the old vehicle rule apart"


# ------------------------------------------------------------------ the verb cases still hold

# Every case, effects (screenplay/effects.py, wired into compile()) and camera moves included.
VERB_CASES = list(CASES)


def _compile_case(case):
    return compile_screenplay(case.screenplay, sets=case.scene.sets, props=case.scene.props, people=case.scene.people)


@pytest.mark.parametrize("case", VERB_CASES, ids=[f"{c.verb}-{c.name}" for c in VERB_CASES])
def test_every_verb_case_compiles_as_it_did(case):
    res = _compile_case(case)
    if case.ok:
        assert res.ok, messages(res)
    else:
        assert not res.ok
        assert (res.errors[0].code, res.errors[0].message) == (case.code, case.message)


STILL_CASES = [c for c in CASES if c.ok and c.verb in C.STILL_VERBS]


@pytest.mark.parametrize("case", STILL_CASES, ids=[f"{c.verb}-{c.name}" for c in STILL_CASES])
def test_a_still_verb_never_moves_its_puppets_root(case):
    """STILL_VERBS is what lets a follow skip an action: check it against every ok case."""
    res = _compile_case(case)
    if not res.ok:
        assert all(e.code == "unsupported_verb" for e in res.errors), messages(res)
        pytest.skip(f"{case.verb} has no handler on this branch")
    for shot, tl_shot in zip(case.screenplay.shots, res.timeline.shots, strict=True):
        for a in shot.actions:
            if a.do != case.verb or a.who is None:
                continue
            start = tl_shot.action_start + a.t
            later = [b.t for b in shot.actions if b.who == a.who and b.t > a.t]
            stop = tl_shot.action_start + min(later) if later else tl_shot.end
            here = value(res, f"puppet:{a.who}", "root.position", start)
            keys = [v for t, v in track(res, f"puppet:{a.who}", "root.position") if start <= t <= stop]
            assert all(v == here for v in keys), (case.name, here, keys)
