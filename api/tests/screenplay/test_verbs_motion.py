"""Movement verb handlers: exit, turn_to, follow (shared handler contract)."""

from __future__ import annotations

import inspect

import pytest

import schemas
from screenplay import motion
from screenplay.body import POSE_SECONDS, kneel_keys
from screenplay.ik import Body
from screenplay.schema import Action, Shot
from screenplay.state import BUILTIN_MARKS, OFFSTAGE_LEFT, OFFSTAGE_RIGHT, StateError, World
from screenplay.tracks import TrackSet
from screenplay.verbs_motion import HANDLERS, apply_exit, apply_follow, apply_turn_to

ACTION_START = 1.2
WALK = motion.SPEEDS["walk"]


# ------------------------------------------------------------------ fixtures

def person(pid: str, build: str = "average") -> schemas.Person:
    return schemas.Person(id=pid, family_id="f", name=pid.title(), character={"color": "#123456", "build": build})


def prop(pid: str, size: str = "hand", sockets=None) -> schemas.Prop:
    return schemas.Prop(id=pid, name=pid, size=size, art={"body": None}, sockets=sockets or {})


PEOPLE = [person("dad", "tall"), person("mom"), person("kid", "small")]
PROPS = [prop("hymn_books"), prop("chair", "furniture", {"seat": [0.5, 0.5]})]
MARKS = {
    "steps_left": (-0.6, 0.0, 0.5),
    "steps_foot": (0.0, 0.0, 0.5),
    "steps_top": (0.2, 0.3, 0.5),
    "curb": (0.8, 0.0, 0.5),
    "chair_spot": (0.5, 0.0, 0.5),
}


def act(**kw) -> Action:
    return Action.model_validate(kw)


@pytest.fixture
def world() -> World:
    w = World(PEOPLE, PROPS)
    w.begin_shot(
        Shot.model_validate({
            "id": "s1", "set": "church",
            "start": {
                "dad": {"at": "steps_left", "facing": "right"},
                "mom": {"at": "steps_top", "holding": "hymn_books", "facing": "left"},
                "kid": {"at": "offstage_left", "facing": "right"},
            },
            "prop_start": {"chair": {"at": "chair_spot"}},
        }),
        MARKS, same_set_as_previous=False,
    )
    return w


@pytest.fixture
def tracks() -> TrackSet:
    return TrackSet()


def make_locate(world: World, tracks: TrackSet):
    """A stand-in for the compiler's locate: people from tracks/world, props, then marks."""
    marks = {**BUILTIN_MARKS, **MARKS}

    def locate(name: str, t: float):
        if name in world.people:
            v = tracks.value_at(f"puppet:{name}", "root.position", t)
            return tuple(v) if v is not None else world.people[name].pos
        if name in world.props and world.props[name].world_pos is not None:
            return world.props[name].world_pos
        if name in marks:
            return marks[name]
        raise StateError("unknown_target", f"{name} is not a person, prop or mark")

    return locate


def run(handler, world: World, tracks: TrackSet, t: float, **action) -> float:
    world.now = round(t - ACTION_START, 6)
    return handler(world, tracks, act(t=world.now, **action), t, ACTION_START, make_locate(world, tracks))


def keys_of(tracks: TrackSet, target: str, channel: str) -> list:
    for tr in tracks.tracks():
        if (tr.target, tr.channel) == (target, channel):
            return [list(k) for k in tr.keys]
    return []


def as_tracks(keys) -> list:
    ts = TrackSet()
    ts.extend(keys)
    return [tr.model_dump() for tr in ts.tracks()]


def expect_error(code: str, reason: str | None, handler, world: World, tracks: TrackSet, t: float, **action) -> StateError:
    """The handler raises StateError(code, reason) and changes neither the world nor the tracks."""
    before_world = world.copy()
    before_tracks = [tr.model_dump() for tr in tracks.tracks()]
    with pytest.raises(StateError) as e:
        run(handler, world, tracks, t, **action)
    assert e.value.code == code, e.value
    if reason is not None:
        assert e.value.reason == reason
    world.now = before_world.now
    assert world == before_world
    assert [tr.model_dump() for tr in tracks.tracks()] == before_tracks
    return e.value


# ------------------------------------------------------------------ contract

def test_handlers_are_exactly_the_movement_verbs_this_module_owns():
    assert set(HANDLERS) == {"exit", "turn_to", "follow"}
    assert HANDLERS == {"exit": apply_exit, "turn_to": apply_turn_to, "follow": apply_follow}
    for fn in HANDLERS.values():
        assert list(inspect.signature(fn).parameters) == ["world", "tracks", "action", "t", "action_start", "locate"]


# ---------------------------------------------------------------------- exit

def test_exit_default_is_nearest_side_left(world, tracks):
    end = run(apply_exit, world, tracks, 2.0, do="exit", who="dad")
    want, arrive, facing = motion.exit_keys("dad", "tall", 2.0, MARKS["steps_left"], OFFSTAGE_LEFT, 1)
    assert end == pytest.approx(arrive) == pytest.approx(2.0 + 0.7 / WALK)
    assert facing == -1
    assert [tr.model_dump() for tr in tracks.tracks()] == as_tracks(want)
    assert keys_of(tracks, "puppet:dad", "root.facing") == [[2.0, -1.0]]
    assert keys_of(tracks, "puppet:dad", "root.position") == [[2.0, [-0.6, 0.0, 0.5]], [4.0, [-1.3, 0.0, 0.5]]]
    d = world.people["dad"]
    assert (d.pos, d.facing) == (OFFSTAGE_LEFT, -1)
    assert not world.on_stage("dad")


def test_exit_default_is_nearest_side_right_and_centre_goes_right(world, tracks):
    world.move("dad", MARKS["steps_foot"])  # x = 0: the world sends a centred puppet right
    end = run(apply_exit, world, tracks, 2.0, do="exit", who="dad")
    assert world.people["dad"].pos == OFFSTAGE_RIGHT
    assert end == pytest.approx(2.0 + 1.3 / WALK)
    assert keys_of(tracks, "puppet:dad", "root.facing") == []  # already facing right


def test_exit_explicit_side(world, tracks):
    end = run(apply_exit, world, tracks, 2.0, do="exit", who="dad", to="offstage_right")
    assert end == pytest.approx(2.0 + 1.9 / WALK)
    assert keys_of(tracks, "puppet:dad", "root.position")[-1] == [pytest.approx(end, abs=1e-3), [1.3, 0.0, 0.5]]
    assert world.people["dad"].pos == OFFSTAGE_RIGHT and world.people["dad"].facing == 1


def test_exit_from_a_raised_mark_keeps_the_holding_arm(world, tracks):
    # mom stands on the steps holding the books in hand_r: her right arm keeps its pose.
    end = run(apply_exit, world, tracks, 3.0, do="exit", who="mom")
    want, arrive, facing = motion.exit_keys(
        "mom", "average", 3.0, MARKS["steps_top"], OFFSTAGE_RIGHT, -1, skip_bones=("arm_r.upper", "arm_r.lower"))
    assert end == pytest.approx(arrive) and facing == world.people["mom"].facing == 1
    assert [tr.model_dump() for tr in tracks.tracks()] == as_tracks(want)
    channels = {tr.channel for tr in tracks.tracks()}
    assert "arm_r.upper.rot" not in channels and "arm_l.upper.rot" in channels
    assert keys_of(tracks, "puppet:mom", "root.facing") == [[3.0, 1.0]]
    assert world.holder("hymn_books") == ("mom", "hand_r")  # the books leave with her


def test_exit_starts_from_the_tracked_root(world, tracks):
    # dad walked to x = 0.1 earlier in the shot; his root track, not his start mark, is the origin.
    walk, _, _ = motion.locomotion_keys("dad", "tall", 1.2, MARKS["steps_left"], (0.1, 0.0, 0.5), 1)
    tracks.extend(walk)
    world.move("dad", (0.1, 0.0, 0.5))
    end = run(apply_exit, world, tracks, 4.0, do="exit", who="dad")
    assert keys_of(tracks, "puppet:dad", "root.position")[-2:] == [[4.0, [0.1, 0.0, 0.5]], [pytest.approx(end, abs=1e-3), [1.3, 0.0, 0.5]]]
    assert end == pytest.approx(4.0 + 1.2 / WALK)


def test_exit_while_kneeling_stands_up_first(world, tracks):
    body = Body(id="dad", ground=(-0.6, 0.0), facing=1, build="tall")
    tracks.extend(kneel_keys(body, 1.2, {}))
    world.kneel("dad")
    low = tracks.value_at("puppet:dad", "root.position", 2.0)
    assert low[1] < 0  # rootOffset baked into the root
    end = run(apply_exit, world, tracks, 2.0, do="exit", who="dad")
    t_go = 2.0 + POSE_SECONDS
    assert end == pytest.approx(t_go + 0.7 / WALK)
    assert tracks.value_at("puppet:dad", "root.position", 2.0) == pytest.approx(low)
    assert tracks.value_at("puppet:dad", "root.position", t_go) == pytest.approx([-0.6, 0.0, 0.5])
    assert tracks.value_at("puppet:dad", "leg_l.upper.rot", 2.0) == pytest.approx(1.4)
    assert tracks.value_at("puppet:dad", "leg_l.upper.rot", t_go) == pytest.approx(0.0)
    assert keys_of(tracks, "puppet:dad", "root.facing") == [[pytest.approx(t_go), -1.0]]
    assert world.people["dad"].pose == "stand" and world.people["dad"].pos == OFFSTAGE_LEFT


def test_exit_while_seated_is_refused(world, tracks):
    world.sit_on("mom", "chair")
    expect_error("seated", "mom is seated in chair.seat", apply_exit, world, tracks, 2.0, do="exit", who="mom")


def test_exit_when_already_offstage(world, tracks):
    expect_error("offstage", "kid is already offstage", apply_exit, world, tracks, 2.0, do="exit", who="kid")


def test_exit_to_a_mark_that_is_not_a_side(world, tracks):
    expect_error("bad_exit", 'dad can exit to offstage_left or offstage_right, not "curb"',
                 apply_exit, world, tracks, 2.0, do="exit", who="dad", to="curb")


def test_exit_unknown_person(world, tracks):
    expect_error("unknown_id", "gran is not in the cast", apply_exit, world, tracks, 2.0, do="exit", who="gran")


# ------------------------------------------------------------------- turn_to

def test_turn_to_flips_facing(world, tracks):
    assert run(apply_turn_to, world, tracks, 2.5, do="turn_to", who="mom", target="curb") == 2.5
    assert keys_of(tracks, "puppet:mom", "root.facing") == [[2.5, 1.0]]
    assert world.people["mom"].facing == 1


def test_turn_to_already_facing_is_a_no_op(world, tracks):
    before = world.copy()
    assert run(apply_turn_to, world, tracks, 2.5, do="turn_to", who="mom", target="steps_left") == 2.5
    assert tracks.tracks() == []
    world.now = before.now
    assert world == before


def test_turn_to_a_person_and_a_prop(world, tracks):
    run(apply_turn_to, world, tracks, 2.0, do="turn_to", who="dad", target="kid")  # kid is offstage left
    run(apply_turn_to, world, tracks, 3.0, do="turn_to", who="dad", target="chair")  # chair at x = 0.5
    assert keys_of(tracks, "puppet:dad", "root.facing") == [[2.0, -1.0], [3.0, 1.0]]
    assert world.people["dad"].facing == 1


def test_turn_to_uses_the_tracked_root(world, tracks):
    # dad is walking from x = -0.6 to x = 0.6 (t 1.2 -> ~4.63); at t = 4.5 he is past the chair.
    tracks.extend(motion.locomotion_keys("dad", "tall", 1.2, MARKS["steps_left"], (0.6, 0.0, 0.5), 1)[0])
    world.move("dad", (0.6, 0.0, 0.5))
    run(apply_turn_to, world, tracks, 2.0, do="turn_to", who="dad", target="chair")
    assert keys_of(tracks, "puppet:dad", "root.facing") == []  # at t=2 he is left of the chair
    run(apply_turn_to, world, tracks, 4.6, do="turn_to", who="dad", target="chair")
    assert keys_of(tracks, "puppet:dad", "root.facing") == [[4.6, -1.0]]


def test_turn_to_unknown_target(world, tracks):
    expect_error("unknown_target", "moon is not a person, prop or mark",
                 apply_turn_to, world, tracks, 2.0, do="turn_to", who="dad", target="moon")


def test_turn_to_self(world, tracks):
    expect_error("self_target", "dad cannot turn to face themselves",
                 apply_turn_to, world, tracks, 2.0, do="turn_to", who="dad", target="dad")


# -------------------------------------------------------------------- follow

def lead_dad(tracks: TrackSet, world: World, t0: float = 2.0, to=MARKS["curb"]) -> float:
    keys, arrive, _ = motion.locomotion_keys("dad", "tall", t0, MARKS["steps_left"], to, 1)
    tracks.extend(keys)
    world.move("dad", to)
    return arrive


def test_follow_walks_behind_the_leader_path(world, tracks):
    arrive = lead_dad(tracks, world)
    path = [(t, tuple(v)) for t, v in keys_of(tracks, "puppet:dad", "root.position")]
    want, stop, facing = motion.follow_keys("kid", "small", 2.0, OFFSTAGE_LEFT, 1, path, None)
    assert want
    end = run(apply_follow, world, tracks, 2.0, do="follow", who="kid", target="dad")
    assert end == pytest.approx(stop)
    kid = [tr.model_dump() for tr in tracks.tracks() if tr.target == "puppet:kid"]
    assert kid == as_tracks(want)
    # It stops `spacing` (0.2) behind dad and never before dad arrives + lag.
    assert world.people["kid"].pos == pytest.approx((0.6, 0.0, 0.5))
    assert world.people["kid"].facing == facing == 1
    assert end >= arrive + 0.5 - 1e-6
    for t in [2.0 + i * 0.25 for i in range(40)]:
        assert tracks.value_at("puppet:kid", "root.position", t)[0] <= tracks.value_at("puppet:dad", "root.position", t)[0]


def test_follow_dur_clips_the_walk(world, tracks):
    lead_dad(tracks, world)
    end = run(apply_follow, world, tracks, 2.0, do="follow", who="kid", target="dad", dur=2.0)
    assert end <= 4.0 + 1e-6
    x = world.people["kid"].pos[0]
    assert -1.3 < x < 0.6
    assert tracks.value_at("puppet:kid", "root.position", 99.0)[0] == pytest.approx(x, abs=1e-3)


def test_follow_leader_who_never_moves(world, tracks):
    e = expect_error("nothing_to_follow", "dad does not move after t=0.8",
                     apply_follow, world, tracks, 2.0, do="follow", who="kid", target="dad")
    assert e.reason.endswith("t=0.8")


def test_follow_leader_who_moved_only_before_t(world, tracks):
    arrive = lead_dad(tracks, world, t0=1.2)
    expect_error("nothing_to_follow", "dad does not move after t=4.5",
                 apply_follow, world, tracks, 5.7, do="follow", who="kid", target="dad")
    assert arrive < 5.7


def test_follow_self(world, tracks):
    expect_error("self_target", "kid cannot follow themselves",
                 apply_follow, world, tracks, 2.0, do="follow", who="kid", target="kid")


def test_follow_needs_a_person(world, tracks):
    expect_error("bad_target", "kid can only follow a person, and chair is not one",
                 apply_follow, world, tracks, 2.0, do="follow", who="kid", target="chair")
    expect_error("unknown_target", "ghost is not a person, prop or mark",
                 apply_follow, world, tracks, 2.0, do="follow", who="kid", target="ghost")


def test_follow_bad_dur(world, tracks):
    lead_dad(tracks, world)
    expect_error("bad_time", "follow dur must be positive, not 0",
                 apply_follow, world, tracks, 2.0, do="follow", who="kid", target="dad", dur=0)


def test_follow_while_seated(world, tracks):
    lead_dad(tracks, world)
    world.sit_on("mom", "chair")
    expect_error("seated", "mom is seated in chair.seat",
                 apply_follow, world, tracks, 2.0, do="follow", who="mom", target="dad")


def test_follow_while_kneeling_stands_up_first(world, tracks):
    lead_dad(tracks, world, to=(0.9, 0.0, 0.5))
    world.move("kid", (-0.9, 0.0, 0.5))
    tracks.extend(kneel_keys(Body(id="kid", ground=(-0.9, 0.0), facing=1, build="small"), 1.2, {}))
    world.kneel("kid")
    end = run(apply_follow, world, tracks, 2.0, do="follow", who="kid", target="dad")
    assert tracks.value_at("puppet:kid", "root.position", 2.0 + POSE_SECONDS) == pytest.approx([-0.9, 0.0, 0.5])
    assert world.people["kid"].pose == "stand"
    assert world.people["kid"].pos == pytest.approx((0.7, 0.0, 0.5))
    assert end > 2.0 + POSE_SECONDS


def _kneeling_kid(world, tracks):
    lead_dad(tracks, world, to=(0.9, 0.0, 0.5))
    world.move("kid", (-0.9, 0.0, 0.5))
    tracks.extend(kneel_keys(Body(id="kid", ground=(-0.9, 0.0), facing=1, build="small"), 1.2, {}))
    world.kneel("kid")


def test_follow_dur_shorter_than_standing_up_is_an_error(world, tracks):
    # Standing up takes POSE_SECONDS and cannot be clipped, so a shorter dur would leave the
    # kid busy past it: refuse, atomically.
    _kneeling_kid(world, tracks)
    expect_error("bad_time", f"kid needs {POSE_SECONDS:g}s to stand up before following; follow dur 0.3 "
                 "is shorter than that",
                 apply_follow, world, tracks, 2.0, do="follow", who="kid", target="dad", dur=0.3)


def test_follow_dur_while_kneeling_never_ends_after_dur(world, tracks):
    _kneeling_kid(world, tracks)
    end = run(apply_follow, world, tracks, 2.0, do="follow", who="kid", target="dad", dur=POSE_SECONDS)
    assert end == pytest.approx(2.0 + POSE_SECONDS)
    assert world.people["kid"].pose == "stand"
    assert world.people["kid"].pos == pytest.approx((-0.9, 0.0, 0.5))


def test_follow_dur_while_kneeling_clips_the_walk(world, tracks):
    _kneeling_kid(world, tracks)
    end = run(apply_follow, world, tracks, 2.0, do="follow", who="kid", target="dad", dur=1.0)
    assert 2.0 + POSE_SECONDS < end <= 3.0 + 1e-9


def test_follow_holding_a_prop_keeps_the_arm(world, tracks):
    # Mom (holding the books, facing left on the steps) follows dad as he walks off left.
    keys, _, _ = motion.locomotion_keys("dad", "tall", 2.0, MARKS["steps_left"], OFFSTAGE_LEFT, 1)
    tracks.extend(keys)
    world.move("dad", OFFSTAGE_LEFT)
    run(apply_follow, world, tracks, 2.0, do="follow", who="mom", target="dad")
    mom = {tr.channel for tr in tracks.tracks() if tr.target == "puppet:mom"}
    assert "root.position" in mom and "leg_l.upper.rot" in mom and "arm_r.upper.rot" not in mom
    assert world.people["mom"].pos[0] == pytest.approx(-1.1)
    assert world.people["mom"].pos[1] == pytest.approx(0.0)


# ---------------------------------------------------------------- atomicity

@pytest.mark.parametrize(
    "handler, action, code",
    [
        (apply_exit, {"do": "exit", "who": "mom"}, "seated"),
        (apply_exit, {"do": "exit", "who": "dad", "to": "steps_foot"}, "bad_exit"),
        (apply_turn_to, {"do": "turn_to", "who": "dad", "target": "nowhere"}, "unknown_target"),
        (apply_follow, {"do": "follow", "who": "mom", "target": "dad"}, "seated"),
        (apply_follow, {"do": "follow", "who": "kid", "target": "mom"}, "nothing_to_follow"),
    ],
)
def test_failed_actions_change_nothing(world, tracks, handler, action, code):
    lead_dad(tracks, world)
    world.sit_on("mom", "chair")
    world.kneel("kid")
    expect_error(code, None, handler, world, tracks, 2.0, **action)


def test_success_mutates_world_and_tracks_together(world, tracks):
    end = run(apply_exit, world, tracks, 2.0, do="exit", who="dad")
    root_end = keys_of(tracks, "puppet:dad", "root.position")[-1]
    assert root_end[0] == pytest.approx(end, abs=1e-3)
    assert tuple(root_end[1]) == pytest.approx(world.people["dad"].pos)
