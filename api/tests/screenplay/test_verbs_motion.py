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
from screenplay.compiler import HANDLER_PROBLEMS
from screenplay.compiler import HANDLERS as COMPILER_HANDLERS
from screenplay.compiler import compile as compile_screenplay
from screenplay.locate import make_locate as make_shot_locate
from screenplay.verbs_motion import HANDLERS, apply_exit, apply_follow, apply_turn_to, stand_up_first
from screenplay.verbs_vehicles import HANDLERS as VEHICLE_HANDLERS
from verb_cases import STREET_MARKS, _a, _at, _one, base_scene, cases_for

ACTION_START = 1.2
WALK = motion.SPEEDS["walk"]


# ------------------------------------------------------------------ fixtures

def person(pid: str, build: str = "average", notes: str | None = None) -> schemas.Person:
    return schemas.Person(
        id=pid, family_id="f", name=pid.title(), character={"color": "#123456", "build": build, "notes": notes})


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


def build_world(people: list[schemas.Person] = PEOPLE) -> World:
    w = World(people, PROPS)
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
def world() -> World:
    return build_world()


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


def test_follow_onto_a_raised_mark_keeps_its_height(world, tracks):
    # Standing surfaces are kept: dad climbs onto the steps (y 0.3), and kid ends up there too.
    lead_dad(tracks, world, to=MARKS["steps_top"])
    run(apply_follow, world, tracks, 2.0, do="follow", who="kid", target="dad")
    assert world.people["kid"].pos == pytest.approx((0.0, 0.3, 0.5))
    assert keys_of(tracks, "puppet:kid", "root.position")[-1][1] == pytest.approx([0.0, 0.3, 0.5])


# ------------------------------------------- follow: on the ground, not at seat/pose height
#
# The leader's root.position is its standing spot only while it stands: seated in a prop it is
# the seat minus hipHeight * scale, kneeling or sitting on the floor it is lowered by the pose's
# rootOffset. A follower walks on the ground under that path, never at those heights.

STREET = "street"
IN_SEDAN = {"in": "sedan.driver"}


def compile_case(sp):
    """Compiled unplanted: these tests check the follower's path is at standing-surface height
    (y 0 on the street), and foot planting lowers the root below it at walk cycle keys
    (test_foot_planting.py checks the feet stay on that surface)."""
    scene = base_scene()
    res = compile_screenplay(sp, sets=scene.sets, props=scene.props, people=scene.people, plant_feet=False)
    assert res.ok, [e.message for e in res.errors]
    return res.timeline


def root_keys(timeline, who: str) -> list[tuple[float, list[float]]]:
    for tr in timeline.tracks:
        if (tr.target, tr.channel) == (f"puppet:{who}", "root.position"):
            return [(float(k[0]), [float(v) for v in k[1]]) for k in tr.keys]
    return []


def test_follow_a_driver_walks_on_the_street_not_at_seat_height():
    # Regression: kid used to copy dad's seated root (y 0.141 in the sedan) and walk in the air.
    tl = compile_case(_one(
        _a(0, "drive_to", who="dad", what="sedan", to="corner"),
        _a(0, "follow", who="kid", target="dad"),
        set=STREET, start={"dad": _at("garage", **IN_SEDAN), "kid": _at("garage")},
        prop_start={"sedan": {"at": "garage"}},
    ))
    dad, kid = root_keys(tl, "dad"), root_keys(tl, "kid")
    assert dad[-1][1][1] > 0.1  # dad really is seated above the street
    assert kid and all(v[1] == 0.0 for _, v in kid)
    garage_x, corner_x = 2 * STREET_MARKS["garage"][0] - 1, 2 * STREET_MARKS["corner"][0] - 1
    # kid stops `spacing` (0.2) behind dad's seat, which is a little left of the sedan's anchor
    assert kid[-1][1][0] == pytest.approx(dad[-1][1][0] - 0.2, abs=1e-3)
    assert garage_x < kid[-1][1][0] and kid[-1][1][0] == pytest.approx(corner_x, abs=0.4)


def _street_world() -> tuple[World, TrackSet, object]:
    scene = base_scene()
    marks = {k: (2 * v[0] - 1, v[1], 0.5) for k, v in STREET_MARKS.items()}
    w = World(scene.people.values(), scene.props.values())
    w.begin_shot(Shot.model_validate({
        "id": "s1", "set": STREET,
        "start": {"dad": {"at": "garage", "in": "sedan.driver"}, "kid": {"at": "garage"}},
        "prop_start": {"sedan": {"at": "garage"}},
    }), marks, same_set_as_previous=False)
    ts = TrackSet()
    return w, ts, make_shot_locate(w, ts, {**BUILTIN_MARKS, **marks}, "s1")


def test_follow_a_driver_stores_a_ground_level_standing_position():
    # The end position the world keeps (where later verbs start from) is on the street too.
    w, ts, locate = _street_world()
    w.now = 0.0
    VEHICLE_HANDLERS["drive_to"](w, ts, act(t=0, do="drive_to", who="dad", what="sedan", to="corner"),
                                 ACTION_START, ACTION_START, locate)
    end = apply_follow(w, ts, act(t=0, do="follow", who="kid", target="dad"), ACTION_START, ACTION_START, locate)
    dad_end = keys_of(ts, "puppet:dad", "root.position")[-1][1]
    assert dad_end[1] > 0.1  # dad's root rides at seat height
    kid = w.people["kid"]
    assert kid.pos[1] == 0.0 and kid.pose == "stand"
    assert kid.pos[0] == pytest.approx(dad_end[0] - 0.2, abs=1e-3)
    assert all(k[1][1] == 0.0 for k in keys_of(ts, "puppet:kid", "root.position"))
    assert end > ACTION_START


def test_follow_a_leader_who_walks_then_kneels_stays_on_the_ground():
    # Documents (this already held before the fix): the kneel is a height-only move with no
    # ground distance, so the follower walks only to the walk's standing end.
    tl = compile_case(_one(
        _a(0, "walk_to", who="dad", to="table_spot"),
        _a(4, "kneel", who="dad"),
        _a(0, "follow", who="kid", target="dad"),
        start={"dad": _at("door"), "kid": _at("offstage_left")},
    ))
    dad, kid = root_keys(tl, "dad"), root_keys(tl, "kid")
    assert dad[-1][1][1] < -0.1  # dad ends kneeling, root lowered
    assert all(v[1] == 0.0 for _, v in kid)
    assert kid[-1][1][0] == pytest.approx(0.0)  # table_spot (x 0.2) less spacing


@pytest.mark.parametrize("kneel", [{}, {"dur": 1}], ids=["kneels", "kneels_and_stands"])
def test_follow_a_standing_leader_who_then_kneels_stays_on_the_ground(kneel):
    # Regression: dad's only movement after t is kneeling, a height-only move; kid used to walk
    # to it and end at the lowered kneeling root height (y -0.132), even after dad stood up.
    tl = compile_case(_one(
        _a(1, "kneel", who="dad", **kneel),
        _a(0, "follow", who="kid", target="dad"),
        start={"dad": _at("table_spot"), "kid": _at("door")},
    ))
    kid = root_keys(tl, "kid")
    assert kid and all(v[1] == 0.0 for _, v in kid)
    assert kid[-1][1][0] == pytest.approx(0.0)  # walked up to spacing short of dad (x 0.2)


def test_follow_a_leader_who_sits_on_a_chair_stays_on_the_ground():
    tl = compile_case(_one(
        _a(1, "sit_on", who="dad", what="chair"),
        _a(0, "follow", who="kid", target="dad"),
        start={"dad": _at("table_spot"), "kid": _at("curb")}, prop_start={"chair": {"at": "bench_spot"}},
    ))
    assert root_keys(tl, "dad")[-1][1][1] != 0.0
    kid = root_keys(tl, "kid")
    assert kid and all(v[1] == 0.0 for _, v in kid)


def test_follow_a_leader_who_gets_in_a_car_stays_on_the_ground():
    tl = compile_case(_one(
        _a(0, "get_in", who="dad", what="sedan"),
        _a(0, "follow", who="kid", target="dad"),
        start={"dad": _at("door"), "kid": _at("offstage_left")}, prop_start={"sedan": {"at": "curb"}},
    ))
    assert root_keys(tl, "dad")[-1][1][1] > 0.1
    kid = root_keys(tl, "kid")
    assert kid and all(v[1] == 0.0 for _, v in kid)


def test_follow_a_leader_who_gets_out_and_walks_stays_on_the_ground():
    tl = compile_case(_one(
        _a(0, "get_out", who="dad", what="sedan"),
        _a(3, "walk_to", who="dad", to="door"),
        _a(0, "follow", who="kid", target="dad"),
        start={"dad": _at("curb", **IN_SEDAN), "kid": _at("offstage_right")}, prop_start={"sedan": {"at": "curb"}},
    ))
    kid = root_keys(tl, "kid")
    assert kid and all(v[1] == 0.0 for _, v in kid)
    assert kid[-1][1][0] == pytest.approx(-0.6)  # door (x -0.8) less spacing, walking left


# ------------------------------------------------------------ stand_up_first

def test_stand_up_first_signature():
    assert list(inspect.signature(stand_up_first).parameters) == ["world", "tracks", "who", "t", "skip_bones"]


def test_stand_up_first_standing_puppet_gets_no_keys_and_keeps_t(world, tracks):
    before_world = world.copy()
    keys, t_up, root = stand_up_first(world, tracks, "dad", 2.0, ())
    assert keys == [] and t_up == 2.0
    assert root == pytest.approx(MARKS["steps_left"])
    assert world == before_world and list(tracks.tracks()) == []


def test_stand_up_first_uses_the_tracked_root_when_standing(world, tracks):
    keys, _, _ = motion.locomotion_keys("dad", "tall", 1.2, MARKS["steps_left"], MARKS["curb"], 1)
    tracks.extend(keys)
    world.move("dad", MARKS["curb"])
    mid = tracks.value_at("puppet:dad", "root.position", 1.5)
    out, t_up, root = stand_up_first(world, tracks, "dad", 1.5, ())
    assert out == [] and t_up == 1.5 and root == pytest.approx(tuple(mid))


def test_stand_up_first_seated_on_a_prop_gets_no_keys(world, tracks):
    world.sit_on("mom", "chair")
    keys, t_up, _ = stand_up_first(world, tracks, "mom", 2.0, ())
    assert keys == [] and t_up == 2.0


def test_stand_up_first_kneeling_puppet_takes_pose_seconds(world, tracks):
    tracks.extend(kneel_keys(Body(id="dad", ground=(-0.6, 0.0), facing=1, build="tall"), 1.2, {}))
    world.kneel("dad")
    before_world = world.copy()
    before_tracks = [tr.model_dump() for tr in tracks.tracks()]
    low = tracks.value_at("puppet:dad", "root.position", 2.0)
    keys, t_up, root = stand_up_first(world, tracks, "dad", 2.0, ())
    assert t_up == pytest.approx(2.0 + POSE_SECONDS)
    assert root == pytest.approx((-0.6, 0.0, 0.5))
    assert keys and all(k[0] == "puppet:dad" for k in keys)
    assert min(k[2] for k in keys) == pytest.approx(2.0)
    assert max(k[2] for k in keys) == pytest.approx(2.0 + POSE_SECONDS)
    # Reads only: the world and tracks are unchanged until the caller applies the keys.
    assert world == before_world
    assert [tr.model_dump() for tr in tracks.tracks()] == before_tracks
    tracks.extend(keys)
    assert tracks.value_at("puppet:dad", "root.position", 2.0) == pytest.approx(low)
    assert tracks.value_at("puppet:dad", "root.position", t_up) == pytest.approx([-0.6, 0.0, 0.5])
    assert tracks.value_at("puppet:dad", "leg_l.upper.rot", t_up) == pytest.approx(0.0)


def test_stand_up_first_skips_the_bones_it_is_told_to(world, tracks):
    tracks.extend(kneel_keys(Body(id="dad", ground=(-0.6, 0.0), facing=1, build="tall"), 1.2, {}))
    world.kneel("dad")
    skip = ("arm_r.upper", "arm_r.lower")
    unskipped = {k[1] for k in stand_up_first(world, tracks, "dad", 2.0, ())[0]}
    assert {"arm_r.upper.rot", "arm_r.lower.rot"} <= unskipped
    keys, _, _ = stand_up_first(world, tracks, "dad", 2.0, skip)
    channels = {k[1] for k in keys}
    assert "leg_l.upper.rot" in channels
    assert not channels & {f"{b}.rot" for b in skip}


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


# ------------------------------------------------------------ walking speed

CANE = "walks with a cane"


def cane_world(who: str) -> World:
    """The fixture's stage, with ``who``'s character sheet noting a cane (speed 0.6)."""
    people = [person(p.id, p.character.build, CANE if p.id == who else None) for p in PEOPLE]
    w = build_world(people)
    assert w.people[who].speed_scale == pytest.approx(0.6)
    assert all(p.speed_scale == 1.0 for pid, p in w.people.items() if pid != who)
    return w


def test_exit_with_a_cane_takes_one_over_point_six_as_long(world, tracks):
    plain = run(apply_exit, world, tracks, 2.0, do="exit", who="dad")
    slow_world, slow_tracks = cane_world("dad"), TrackSet()
    slow = run(apply_exit, slow_world, slow_tracks, 2.0, do="exit", who="dad")
    assert plain == pytest.approx(2.0 + 0.7 / WALK)
    assert (slow - 2.0) == pytest.approx((plain - 2.0) / 0.6, abs=1e-3)
    # same path, same place, same facing: only the timing stretches
    assert slow_world.people["dad"].pos == world.people["dad"].pos == OFFSTAGE_LEFT
    assert keys_of(slow_tracks, "puppet:dad", "root.position") == [
        [2.0, [-0.6, 0.0, 0.5]], [pytest.approx(slow, abs=1e-3), [-1.3, 0.0, 0.5]]]
    assert keys_of(slow_tracks, "puppet:dad", "root.facing") == [[2.0, -1.0]]


def test_exit_with_a_cane_stands_up_at_the_usual_pace(tracks):
    w = cane_world("dad")
    tracks.extend(kneel_keys(Body(id="dad", ground=(-0.6, 0.0), facing=1, build="tall"), 1.2, {}))
    w.kneel("dad")
    end = run(apply_exit, w, tracks, 2.0, do="exit", who="dad")
    t_go = 2.0 + POSE_SECONDS  # standing up is not slowed
    assert tracks.value_at("puppet:dad", "root.position", t_go) == pytest.approx([-0.6, 0.0, 0.5])
    assert end == pytest.approx(t_go + 0.7 / (WALK * 0.6))


def _stationary_leader_setup(w: World, tracks: TrackSet) -> float:
    """dad shuffles 0.1 right and stops at 2.29, while kneeling kid (at x 0.9) stands up until 2.4.

    By the time kid sets off, dad stands still: kid walks up to 0.2 short of him (x -0.3)."""
    keys, arrive, _ = motion.locomotion_keys("dad", "tall", 2.0, MARKS["steps_left"], (-0.5, 0.0, 0.5), 1)
    tracks.extend(keys)
    w.move("dad", (-0.5, 0.0, 0.5))
    w.move("kid", (0.9, 0.0, 0.5))
    tracks.extend(kneel_keys(Body(id="kid", ground=(0.9, 0.0), facing=-1, build="small"), 1.2, {}))
    w.kneel("kid")
    return arrive


def test_follow_a_stationary_leader_with_a_cane_takes_one_over_point_six_as_long(world, tracks):
    arrive = _stationary_leader_setup(world, tracks)
    t_go = 2.0 + POSE_SECONDS
    assert arrive < t_go  # dad is standing still before kid is on its feet
    plain = run(apply_follow, world, tracks, 2.0, do="follow", who="kid", target="dad")
    slow_world, slow_tracks = cane_world("kid"), TrackSet()
    _stationary_leader_setup(slow_world, slow_tracks)
    slow = run(apply_follow, slow_world, slow_tracks, 2.0, do="follow", who="kid", target="dad")
    assert plain == pytest.approx(t_go + 1.2 / WALK)
    assert (slow - t_go) == pytest.approx((plain - t_go) / 0.6, abs=1e-3)
    assert world.people["kid"].pos == pytest.approx((-0.3, 0.0, 0.5))
    assert slow_world.people["kid"].pos == pytest.approx((-0.3, 0.0, 0.5))
    assert slow_tracks.value_at("puppet:kid", "root.position", t_go) == pytest.approx([0.9, 0.0, 0.5])


def test_follow_with_a_cane_walks_at_its_own_pace_behind_a_moving_leader(tracks):
    # dad hops 0.2 right in 0.1 s at t=2, so neither follower is held back by him: each walks
    # at its natural pace from 2.5 (dad set off + lag), the cane walker 1 / 0.6 as long.
    def hop(w: World, ts: TrackSet) -> None:
        ts.extend([("puppet:dad", "root.position", 2.0, [-0.6, 0.0, 0.5]),
                   ("puppet:dad", "root.position", 2.1, [-0.4, 0.0, 0.5])])
        w.move("dad", (-0.4, 0.0, 0.5))

    plain_world, slow_world, slow_tracks = build_world(), cane_world("kid"), TrackSet()
    hop(plain_world, tracks)
    hop(slow_world, slow_tracks)
    plain = run(apply_follow, plain_world, tracks, 2.0, do="follow", who="kid", target="dad")
    slow = run(apply_follow, slow_world, slow_tracks, 2.0, do="follow", who="kid", target="dad")
    set_off = 2.0 + 0.5
    assert plain == pytest.approx(set_off + 0.7 / WALK)
    assert (slow - set_off) == pytest.approx((plain - set_off) / 0.6, abs=1e-3)
    assert slow >= 2.1 + 0.5  # never before the leader's arrival + lag
    assert slow_world.people["kid"].pos == pytest.approx((-0.6, 0.0, 0.5))


# ------------------------------------------------------- the verb case catalogue

MOTION_CASES = [c for verb in ("exit", "turn_to", "follow") for c in cases_for(verb)]


def test_the_catalogue_covers_every_verb_this_module_owns():
    assert {c.verb for c in MOTION_CASES} == set(HANDLERS)
    assert HANDLER_PROBLEMS == []
    for verb, fn in HANDLERS.items():
        assert COMPILER_HANDLERS[verb] is fn


@pytest.mark.parametrize("case", MOTION_CASES, ids=[c.name for c in MOTION_CASES])
def test_catalogue_cases_compile(case):
    res = compile_screenplay(
        case.screenplay, sets=case.scene.sets, props=case.scene.props, people=case.scene.people)
    if case.ok:
        assert res.ok, [e.message for e in res.errors]
        assert res.timeline is not None
    else:
        assert not res.ok and res.errors
        assert res.errors[0].code == case.code
        assert res.errors[0].message == case.message
