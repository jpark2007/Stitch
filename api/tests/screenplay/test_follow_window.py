"""A follow's window (compiler._schedule, compiler._LeaderWindow): a follow with ``dur`` reads
its leader's path only up to ``t + dur``, so it waits only for the leader's moves that start
before then. A later leader move (a hand-over or a hug with the follower afterwards) is no
longer a false follow_order loop, and it cannot change the follow even when it runs first.
A follow without ``dur`` still waits for every move of its leader, as before.

Everything here runs the real handlers on verb_cases.base_scene()'s back yard."""

from __future__ import annotations

import copy

import pytest
from verb_cases import base_scene

from screenplay import compiler as C
from screenplay.compiler import compile as compile_screenplay
from screenplay.schema import Action, CompileResult, Screenplay
from screenplay.tracks import TrackSet

# --------------------------------------------------------------------------- helpers


def shot(actions: list[dict], start: dict | None = None) -> dict:
    return {
        "memoryId": "m", "cast": ["dad", "mom", "kid"], "props": ["hymn_books"],
        "shots": [{
            "id": "s1", "set": "yard",
            "start": start or {"dad": {"at": "door", "holding": "hymn_books"},
                               "kid": {"at": "beside_bench"}, "mom": {"at": "curb"}},
            "actions": actions,
        }],
    }


def run(data: dict) -> CompileResult:
    scene = base_scene()
    return compile_screenplay(Screenplay.model_validate(data), sets=scene.sets, props=scene.props, people=scene.people)


def keys(result: CompileResult, target: str, channel: str) -> list:
    for tr in result.timeline.tracks:
        if (tr.target, tr.channel) == (target, channel):
            return [(k[0], k[1]) for k in tr.keys]
    return []


def errors(result: CompileResult) -> list[tuple[str, str]]:
    return [(e.code, e.message) for e in result.errors]


def without_dur(actions: list[dict]) -> list[dict]:
    out = copy.deepcopy(actions)
    for a in out:
        if a["do"] == "follow":
            a.pop("dur", None)
    return out


@pytest.fixture
def ran(monkeypatch):
    """(who, do, written t) of every action the compiler runs, in the order it runs them."""
    calls: list[tuple[str | None, str, float]] = []
    real = C._run_action

    def spy(world, tracks, action, *args, **kwargs):
        calls.append((action.who, action.do, action.t))
        return real(world, tracks, action, *args, **kwargs)

    monkeypatch.setattr(C, "_run_action", spy)
    return calls


# The two screenplays from the bug report: dad follows kid for a while, and kid comes back to
# dad long after that follow has ended.
HAND_OVER_LATER = [
    {"t": 0.5, "who": "kid", "do": "walk_to", "to": "by_table"},
    {"t": 1, "who": "dad", "do": "follow", "target": "kid", "dur": 3},
    {"t": 8, "who": "dad", "do": "hand_to", "what": "hymn_books", "to": "kid"},
    {"t": 12, "who": "kid", "do": "walk_to", "to": "curb"},
]
HUG_LATER = [
    {"t": 0.5, "who": "kid", "do": "walk_to", "to": "by_table"},
    {"t": 1, "who": "dad", "do": "follow", "target": "kid", "dur": 2},
    {"t": 12, "who": "kid", "do": "hug", "target": "dad"},
]

# Exactly what main reports for the same screenplays without dur (a real loop there).
HAND_OVER_LOOP = (
    "shot s1, t=12: kid cannot walk_to; kid's walk_to at t=12 has to come after dad's hand_to at t=8, "
    "which comes after dad's follow of kid at t=1, but that follow is only worked out after everything "
    "that moves kid, this walk_to included; move the walk_to before t=1, or drop the follow"
)
HUG_LOOP = (
    "shot s1, t=12: kid cannot hug; kid's hug at t=12 needs dad where dad's follow of kid at t=1 leaves "
    "dad, but that follow is only worked out after everything that moves kid, this hug included; move "
    "the hug before t=1, or drop the follow"
)


# --------------------------------------------------------------------- the bug report


def test_a_hand_over_and_a_walk_after_the_follow_compile(ran):
    res = run(shot(HAND_OVER_LATER))
    assert res.ok, errors(res)
    # time order: the follow runs at its own time, after kid's walk inside its window
    assert ran == [("kid", "walk_to", 0.5), ("dad", "follow", 1.0), ("dad", "hand_to", 8.0), ("kid", "walk_to", 12.0)]
    # dad follows kid to the table (kid stops at x=0.1; dad 0.2 behind, by t=4 + the 1.2 rise)
    assert keys(res, "puppet:dad", "root.position") == [
        (0.0, [-0.8, 0.0, 0.5]), (2.2, [-0.8, 0.0, 0.5]), (4.2, [-0.1, 0.0, 0.5])]
    # the hand-over (t=8) and kid's walk to the curb (t=12) both produce keys
    assert keys(res, "prop:hymn_books", "parent") == [(0.0, "puppet:dad.hand_r"), (9.7, "puppet:kid.hand_r")]
    kid = keys(res, "puppet:kid", "root.position")
    assert kid[-2:] == [(13.2, [0.1, 0.0, 0.5]), (15.2, [0.8, 0.0, 0.5])]


def test_a_hug_after_the_follow_compiles(ran):
    res = run(shot(HUG_LATER))
    assert res.ok, errors(res)
    assert ran == [("kid", "walk_to", 0.5), ("dad", "follow", 1.0), ("kid", "hug", 12.0)]
    # kid hugs dad where the follow left him: kid steps from x=0.1 toward dad at x=-0.1
    kid = keys(res, "puppet:kid", "root.position")
    assert kid[-1][0] > 13.2 and kid[-1][1][0] < 0.1


@pytest.mark.parametrize(("actions", "message"), [(HAND_OVER_LATER, HAND_OVER_LOOP), (HUG_LATER, HUG_LOOP)],
                         ids=["hand_to", "hug"])
def test_without_dur_the_loop_is_still_reported_word_for_word(actions, message):
    res = run(shot(without_dur(actions)))
    assert errors(res) == [("follow_order", message)]


# ------------------------------------------------------------------ inside the window


def window_shot(*extra: dict) -> dict:
    """kid walks to the table, dad follows for 3 s from t=1 (window to t=4), plus ``extra``."""
    return shot([
        {"t": 0.5, "who": "kid", "do": "walk_to", "to": "by_table"},
        {"t": 1, "who": "dad", "do": "follow", "target": "kid", "dur": 3},
        *extra,
    ], start={"dad": {"at": "door"}, "kid": {"at": "beside_bench"}, "mom": {"at": "curb"}})


INSIDE = {"t": 3, "who": "kid", "do": "walk_to", "to": "curb"}  # starts before t=1 + 3
AFTER = {"t": 12, "who": "kid", "do": "hug", "target": "dad"}    # starts after the window


def test_a_leader_move_inside_the_window_still_holds_the_follow_back(ran):
    res = run(window_shot(INSIDE, AFTER))
    assert res.ok, errors(res)
    # the walk at t=3 runs before the follow (t=1): the follow waits for it
    assert ran == [("kid", "walk_to", 0.5), ("kid", "walk_to", 3.0), ("dad", "follow", 1.0), ("kid", "hug", 12.0)]
    dad = keys(res, "puppet:dad", "root.position")
    # the follower's keys are exactly those of the run that has the walk and no later hug ...
    alone = run(window_shot(INSIDE))
    assert alone.ok, errors(alone)
    assert dad == keys(alone, "puppet:dad", "root.position")
    # ... and the walk changed them: without it dad stops behind kid at the table (x=-0.1)
    no_walk = run(window_shot(AFTER))
    assert no_walk.ok, errors(no_walk)
    assert keys(no_walk, "puppet:dad", "root.position")[-1] == (4.2, [-0.1, 0.0, 0.5])
    # with it dad is still walking after kid toward the curb when the window closes at t=4 (5.2)
    assert dad[-1][0] == pytest.approx(5.2) and dad[-1][1][0] > -0.1


def test_a_move_starting_exactly_at_the_windows_end_does_not_hold_the_follow_back(ran):
    res = run(window_shot({"t": 4, "who": "kid", "do": "walk_to", "to": "curb"}, AFTER))
    assert res.ok, errors(res)
    assert ran == [("kid", "walk_to", 0.5), ("dad", "follow", 1.0), ("kid", "walk_to", 4.0), ("kid", "hug", 12.0)]


# ------------------------------------------------- a later move that runs before the follow


def chain(walk_to: str, *extra: dict) -> dict:
    """mom follows kid with no dur (so she waits for every move of kid), dad follows mom for a
    moment (waits for mom's follow), then follows kid for 3 s: dad's second follow waits behind
    his first, so a later move of kid runs before it."""
    return shot([
        {"t": 0.5, "who": "kid", "do": "walk_to", "to": walk_to},
        {"t": 0.1, "who": "mom", "do": "follow", "target": "kid"},
        {"t": 0.2, "who": "dad", "do": "follow", "target": "mom", "dur": 1.2},
        {"t": 1.5, "who": "dad", "do": "follow", "target": "kid", "dur": 3},
        *extra,
    ], start={"dad": {"at": "door"}, "kid": {"at": "beside_bench"}, "mom": {"at": "bench_spot"}})


LATER_WALK = {"t": 6, "who": "kid", "do": "walk_to", "to": "door"}


def test_a_later_leader_move_that_runs_first_does_not_change_the_follow(ran):
    res = run(chain("curb", LATER_WALK))
    assert res.ok, errors(res)
    # kid's walk at t=6 runs before dad's follow of kid (t=1.5) ...
    assert ran.index(("kid", "walk_to", 6.0)) < ran.index(("dad", "follow", 1.5))
    alone = run(chain("curb"))
    assert alone.ok, errors(alone)
    # ... and dad's keys are the same as without it: the follow read kid's path up to t=4.5 only
    dad = keys(res, "puppet:dad", "root.position")
    assert dad == keys(alone, "puppet:dad", "root.position")
    assert dad[-1] == (5.7, [0.25, 0.0, 0.5])


def test_a_follow_whose_leader_only_moves_after_the_window_has_nothing_to_follow(ran):
    """kid's walk to the table ends before dad's second follow starts (t=1.5): kid stands still
    all window long. Whether kid's walk at t=6 runs before that follow (it does here) or not,
    the follow finds nothing to follow; the message names the window."""
    with_later = run(chain("by_table", LATER_WALK))
    assert ran.index(("kid", "walk_to", 6.0)) < ran.index(("dad", "follow", 1.5))
    assert errors(with_later) == [(
        "nothing_to_follow",
        "shot s1, t=1.5: dad cannot follow; kid does not move between t=1.5 and t=4.5, when the follow ends",
    )]
    # with no later move the handler's own message stands
    assert errors(run(chain("by_table"))) == [
        ("nothing_to_follow", "shot s1, t=1.5: dad cannot follow; kid does not move after t=1.5")]


# ----------------------------------------------------------------------- the pieces


def acts(*items: dict) -> list[Action]:
    return [Action.model_validate(a) for a in items]


CAST = {"dad": None, "mom": None, "kid": None}


def test_schedule_waits_only_for_leader_moves_inside_the_window():
    actions = acts(
        {"t": 1, "who": "dad", "do": "follow", "target": "kid", "dur": 3},
        {"t": 3.9, "who": "kid", "do": "walk_to", "to": "curb"},  # inside: 3.9 < 4
        {"t": 6, "who": "kid", "do": "hug", "target": "dad"},     # after: plain time order (touches dad)
    )
    assert C._schedule(actions, [a.t for a in actions], CAST) == ([1, 0, 2], [])
    # the window is the retimed one: a cue that moves the follow to t=3.5 brings the hug inside it
    assert C._schedule(actions, [3.5, 3.9, 6.0], CAST)[1][0][:2] == (2, "follow_order")
    # no dur: every move of the leader, as before
    no_dur = [a.model_copy(update={"dur": None}) if a.do == "follow" else a for a in actions]
    assert C._schedule(no_dur, [a.t for a in no_dur], CAST)[1][0][:2] == (2, "follow_order")


def test_window_end_and_in_window():
    follow = Action.model_validate({"t": 1, "who": "dad", "do": "follow", "target": "kid", "dur": 3})
    assert C._window_end(follow, 1.5) == 4.5  # the retimed t, not the written one
    assert C._window_end(follow.model_copy(update={"dur": None}), 1.5) is None
    assert C._window_end(Action.model_validate({"t": 1, "who": "dad", "do": "walk_to", "to": "curb", "dur": 3}), 1) is None
    assert C._in_window(4.5 - 2e-6, 4.5) and not C._in_window(4.5 - 1e-7, 4.5) and not C._in_window(5, 4.5)
    assert C._in_window(99.0, None)


def test_follow_windows_flag_a_later_leader_move():
    actions = acts(
        {"t": 1, "who": "dad", "do": "follow", "target": "kid", "dur": 3},
        {"t": 2, "who": "mom", "do": "follow", "target": "kid", "dur": 9},
        {"t": 1, "who": "kid", "do": "follow", "target": "mom"},  # no dur: no window
        {"t": 6, "who": "kid", "do": "walk_to", "to": "curb"},
        {"t": 7, "who": "dad", "do": "follow", "target": "nobody", "dur": 1},  # not a cast member
    )
    assert C._follow_windows(actions, [a.t for a in actions], CAST) == {
        0: C._FollowWindow("kid", True), 1: C._FollowWindow("kid", False)}


def test_clip_after_keeps_the_move_under_way_and_drops_later_ones():
    raw = [(0.0, [0, 0, 0]), (5.0, [1, 0, 0]), (2.0, [0, 0, 0]), (6.0, [1, 0, 0]), (8.0, [2, 0, 0])]
    # sorted by time; everything before t=4, then the key that ends the move under way at t=4
    assert C._clip_after(raw, 4.0) == [(0.0, [0, 0, 0]), (2.0, [0, 0, 0]), (5.0, [1, 0, 0])]
    assert C._clip_after(raw, 100.0) == sorted(raw, key=lambda k: k[0])
    # ties keep insertion order, and only the first key at the boundary is kept
    assert C._clip_after([(0.0, "a"), (4.0, "b"), (4.0, "c")], 4.0) == [(0.0, "a"), (4.0, "b")]


def test_leader_window_reads_clipped_and_writes_through():
    tracks = TrackSet()
    tracks.extend([("puppet:kid", "root.position", 0.0, [0.0, 0.0, 0.5]),
                   ("puppet:kid", "root.position", 1.0, [0.0, 0.0, 0.5]),
                   ("puppet:kid", "root.position", 3.0, [1.0, 0.0, 0.5]),
                   ("puppet:kid", "root.position", 6.0, [1.0, 0.0, 0.5]),
                   ("puppet:kid", "root.position", 8.0, [-1.0, 0.0, 0.5]),
                   ("puppet:dad", "root.position", 0.0, [-0.5, 0.0, 0.5])])
    view = C._LeaderWindow(tracks, "kid", 4.0)
    assert view.value_at("puppet:kid", "root.position", 9.0) == [1.0, 0.0, 0.5]
    assert tracks.value_at("puppet:kid", "root.position", 9.0) == [-1.0, 0.0, 0.5]
    assert [k[0] for tr in view.tracks() if tr.target == "puppet:kid" for k in tr.keys] == [0.0, 1.0, 3.0, 6.0]
    view.extend([("puppet:dad", "root.position", 2.0, [0.5, 0.0, 0.5]), ("puppet:dad", "root.facing", 2.0, -1)])
    assert tracks.value_at("puppet:dad", "root.position", 2.0) == [0.5, 0.0, 0.5]
    assert tracks.value_at("puppet:dad", "root.facing", 2.0) == -1
    # a leader with no track yet reads as none
    assert not C._LeaderWindow(tracks, "mom", 4.0).has_track("puppet:mom", "root.position")


def test_window_compiles_are_deterministic():
    data = chain("curb", LATER_WALK)
    assert run(data).model_dump_json() == run(copy.deepcopy(data)).model_dump_json()
