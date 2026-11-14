"""Body verb handlers: wave, point_at, sit_on, stand_up, kneel, hug, lean_toward.

Handlers are called directly, the way the compiler calls them: world.now is the shot time,
`t` is timeline time, and locate() resolves people, props and marks.
"""

import inspect
import math

import pytest

import schemas
from screenplay import verbs_body
from screenplay.ik import RIG, scale, shoulder_world
from screenplay.schema import VOCABULARY, Action, Shot
from screenplay.state import StateError, World
from screenplay.tracks import TrackSet
from screenplay.verbs_body import HANDLERS, HUG_GAP

AS = 2.0  # timeline time of the shot's t=0
WALK = VOCABULARY["speeds"]["walk"]
HIP = RIG["hipHeight"]


# ------------------------------------------------------------------ fixtures

def person(pid: str, build: str = "average", notes: str | None = None) -> schemas.Person:
    return schemas.Person(id=pid, family_id="f", name=pid.title(),
                          character={"color": "#123456", "build": build, "notes": notes})


def prop(pid: str, size: str = "hand", sockets=None, aspect: float = 1.0) -> schemas.Prop:
    return schemas.Prop(id=pid, name=pid, size=size, art={"body": None}, aspect=aspect, sockets=sockets or {})


PEOPLE = [person("dad", "tall"), person("mom"), person("kid", "small")]
PROPS = [
    prop("hymn_books"),
    prop("hat"),
    prop("chair", "furniture", {"seat": [0.5, 0.5]}),
    prop("stool", "furniture", {"seat": [0.5, 0.5]}),
    prop("sedan", "vehicle", {"driver": [0.35, 0.55], "passenger": [0.6, 0.55]}, aspect=2.0),
]

MARKS = {
    "left": (-0.6, 0.0, 0.5),
    "mid": (0.0, 0.0, 0.5),
    "right": (0.6, 0.0, 0.5),
    "close": (-0.15, 0.0, 0.5),
    "chair_spot": (0.3, 0.0, 0.5),
    "by_chair": (0.33, 0.0, 0.5),
    "stage": (-0.4, 0.3, 0.5),  # a raised platform
}

# furniture is 0.4 tall, anchor [0.5, 1.0], seat [0.5, 0.5]: the seat is 0.2 above the anchor.
SEAT_Y = 0.2


def begin(world: World, start: dict, prop_start: dict | None = None) -> World:
    shot = Shot.model_validate({"id": "s1", "set": "porch", "start": start, "propStart": prop_start or {}})
    world.begin_shot(shot, MARKS, same_set_as_previous=False)
    return world


@pytest.fixture
def world() -> World:
    return World(PEOPLE, PROPS)


def locate_in(world: World):
    def locate(name: str, t: float):
        if name in world.people:
            return world.people[name].pos
        if name in world.props and world.props[name].world_pos is not None:
            return world.props[name].world_pos
        if name in MARKS:
            return MARKS[name]
        raise StateError("unknown_target", f"{name} is not a person, prop or mark")
    return locate


def run(world: World, tracks: TrackSet, t: float, do: str, **params) -> float:
    """Call a handler like the compiler does (who free check + occupy are the compiler's)."""
    action = Action(t=round(t - AS, 6), do=do, **params)
    world.now = round(t - AS, 6)
    return HANDLERS[do](world, tracks, action, t, AS, locate_in(world))


def fails(world: World, tracks: TrackSet, code: str, reason: str, t: float, do: str, **params) -> None:
    """The handler raises StateError(code, reason) and leaves the world and tracks untouched."""
    world.now = round(t - AS, 6)
    before_world, before_tracks = world.snapshot(), tracks.tracks()
    with pytest.raises(StateError) as e:
        run(world, tracks, t, do, **params)
    assert (e.value.code, e.value.reason) == (code, reason)
    assert world.snapshot() == before_world
    assert tracks.tracks() == before_tracks


def at(tracks: TrackSet, who: str, channel: str, t: float):
    return tracks.value_at(f"puppet:{who}", channel, round(t, 3))


def keys(tracks: TrackSet, target: str, channel: str) -> list:
    for tr in tracks.tracks():
        if (tr.target, tr.channel) == (target, channel):
            return [list(k) for k in tr.keys]
    return []


def close(a, b, tol=1e-3) -> bool:
    if isinstance(a, (list, tuple)):
        return len(a) == len(b) and all(close(x, y, tol) for x, y in zip(a, b))
    return abs(a - b) <= tol


# ------------------------------------------------------------------ the table

def test_handlers_are_exactly_the_body_verbs_with_the_shared_signature():
    body_verbs = {v for v, spec in VOCABULARY["actions"].items() if spec["kind"] == "body"} - {"pose"}
    assert set(HANDLERS) == body_verbs == {"wave", "point_at", "sit_on", "stand_up", "kneel", "hug", "lean_toward"}
    for verb, fn in HANDLERS.items():
        assert fn is getattr(verbs_body, f"apply_{verb}")
        assert list(inspect.signature(fn).parameters) == ["world", "tracks", "action", "t", "action_start", "locate"]


# ------------------------------------------------------------------ wave

def test_wave_raises_the_right_arm_swings_twice_and_returns(world):
    begin(world, {"mom": {"at": "mid", "facing": "right"}})
    tracks = TrackSet()
    end = run(world, tracks, 3.0, "wave", who="mom")
    assert end == pytest.approx(4.2)
    # ramp 0.4, hold 0.4: upper 0 -> 2.6 -> 2.6 -> 0
    for t, v in [(3.0, 0.0), (3.4, 2.6), (3.8, 2.6), (4.2, 0.0)]:
        assert at(tracks, "mom", "arm_r.upper.rot", t) == pytest.approx(v)
    # lower: 0.4 held, swinging +-0.3 at fifths of the hold
    for t, v in [(3.0, 0.0), (3.4, 0.4), (3.48, 0.7), (3.56, 0.1), (3.64, 0.7), (3.72, 0.1), (3.8, 0.4), (4.2, 0.0)]:
        assert at(tracks, "mom", "arm_r.lower.rot", t) == pytest.approx(v)
    assert not tracks.has_track("puppet:mom", "arm_l.upper.rot")
    assert not tracks.has_track("puppet:mom", "root.facing")
    assert world.people["mom"].facing == 1


def test_wave_toward_a_target_behind_turns_first(world):
    begin(world, {"mom": {"at": "mid", "facing": "right"}, "dad": {"at": "left"}})
    tracks = TrackSet()
    end = run(world, tracks, 3.0, "wave", who="mom", target="dad", dur=2.0)
    assert end == pytest.approx(5.0)
    assert keys(tracks, "puppet:mom", "root.facing") == [[3.0, -1.0]]
    assert world.people["mom"].facing == -1
    assert at(tracks, "mom", "arm_r.upper.rot", 3.4) == pytest.approx(2.6)
    assert at(tracks, "mom", "arm_r.upper.rot", 5.0) == pytest.approx(0.0)


def test_wave_uses_the_free_left_arm_when_the_right_hand_is_full(world):
    begin(world, {"mom": {"at": "mid", "holding": "hymn_books"}})
    tracks = TrackSet()
    run(world, tracks, 3.0, "wave", who="mom")
    assert at(tracks, "mom", "arm_l.upper.rot", 3.4) == pytest.approx(2.6)
    assert at(tracks, "mom", "arm_l.lower.rot", 3.48) == pytest.approx(0.7)
    assert not tracks.has_track("puppet:mom", "arm_r.upper.rot")
    assert world.people["mom"].holding == {"hand_r": "hymn_books"}


def test_wave_with_both_hands_full_uses_the_right_arm_and_keeps_the_prop(world):
    begin(world, {"mom": {"at": "mid", "holding": "hymn_books"}}, {"hat": {"at": "mid"}})
    assert world.pick_up("mom", "hat") == "hand_l"
    tracks = TrackSet()
    tracks.add("puppet:mom", "arm_r.upper.rot", 0.0, 0.3)  # in the hold pose
    tracks.add("puppet:mom", "arm_r.lower.rot", 0.0, 1.2)
    run(world, tracks, 3.0, "wave", who="mom")
    assert at(tracks, "mom", "arm_r.upper.rot", 3.0) == pytest.approx(0.3)
    assert at(tracks, "mom", "arm_r.upper.rot", 3.4) == pytest.approx(2.6)
    assert at(tracks, "mom", "arm_r.upper.rot", 4.2) == pytest.approx(0.3)  # back into the hold
    assert at(tracks, "mom", "arm_r.lower.rot", 4.2) == pytest.approx(1.2)
    assert not tracks.has_track("puppet:mom", "arm_l.upper.rot")
    assert world.people["mom"].holding == {"hand_r": "hymn_books", "hand_l": "hat"}


def test_wave_errors(world):
    begin(world, {"mom": {"at": "mid"}})
    tracks = TrackSet()
    fails(world, tracks, "unknown_target", "ghost is not a person, prop or mark", 3.0, "wave", who="mom", target="ghost")
    fails(world, tracks, "bad_dur", "dur must be a positive number of seconds, got 0.0", 3.0, "wave", who="mom", dur=0)


# ------------------------------------------------------------------ point_at

def test_point_at_aims_the_upper_arm_at_the_target(world):
    begin(world, {"mom": {"at": "mid", "facing": "right"}})
    tracks = TrackSet()
    end = run(world, tracks, 3.0, "point_at", who="mom", target="right")
    assert end == pytest.approx(4.0)
    sh = shoulder_world("arm_r", (0.0, 0.0), 1, "average")
    aim = math.atan2(0.6 - sh[0], -(0.0 - sh[1]))
    ramp = 1.0 / 3  # a 1 s gesture blends for a third of it at each end
    for t, v in [(3.0, 0.0), (3.0 + ramp, aim), (4.0 - ramp, aim), (4.0, 0.0)]:
        assert at(tracks, "mom", "arm_r.upper.rot", t) == pytest.approx(v, abs=1e-3)
    assert at(tracks, "mom", "arm_r.lower.rot", 3.0 + ramp) == pytest.approx(0.1)
    assert not tracks.has_track("puppet:mom", "root.facing")
    assert world.people["mom"].facing == 1


def test_point_at_behind_turns_and_uses_the_left_arm_when_the_right_is_full(world):
    begin(world, {"mom": {"at": "mid", "facing": "right", "holding": "hymn_books"}, "dad": {"at": "left"}})
    tracks = TrackSet()
    run(world, tracks, 3.0, "point_at", who="mom", target="dad", dur=2.0)
    assert keys(tracks, "puppet:mom", "root.facing") == [[3.0, -1.0]]
    assert world.people["mom"].facing == -1
    sh = shoulder_world("arm_l", (0.0, 0.0), -1, "average")
    aim = math.atan2(-1 * (-0.6 - sh[0]), -(0.0 - sh[1]))
    assert at(tracks, "mom", "arm_l.upper.rot", 3.4) == pytest.approx(aim, abs=1e-3)
    assert at(tracks, "mom", "arm_l.upper.rot", 5.0) == pytest.approx(0.0)
    assert not tracks.has_track("puppet:mom", "arm_r.upper.rot")


def test_point_at_errors(world):
    begin(world, {"mom": {"at": "mid"}})
    tracks = TrackSet()
    fails(world, tracks, "unknown_target", "ghost is not a person, prop or mark", 3.0, "point_at", who="mom", target="ghost")
    fails(world, tracks, "missing_param", "point_at needs target", 3.0, "point_at", who="mom")


# ------------------------------------------------------------------ lean_toward

def test_lean_toward_turns_and_leans_the_torso(world):
    begin(world, {"mom": {"at": "mid", "facing": "right"}, "dad": {"at": "left"}})
    tracks = TrackSet()
    end = run(world, tracks, 3.0, "lean_toward", who="mom", target="dad")
    assert end == pytest.approx(4.0)
    ramp = 1.0 / 3
    for t, v in [(3.0, 0.0), (3.0 + ramp, -0.2), (4.0 - ramp, -0.2), (4.0, 0.0)]:
        assert at(tracks, "mom", "torso.rot", t) == pytest.approx(v, abs=1e-3)
    assert keys(tracks, "puppet:mom", "root.facing") == [[3.0, -1.0]]
    assert world.people["mom"].facing == -1


def test_lean_toward_errors(world):
    begin(world, {"mom": {"at": "mid"}})
    tracks = TrackSet()
    fails(world, tracks, "unknown_target", "ghost is not a person, prop or mark", 3.0, "lean_toward", who="mom", target="ghost")
    fails(world, tracks, "bad_dur", "dur must be a positive number of seconds, got -1.0", 3.0,
          "lean_toward", who="mom", target="left", dur=-1)


# ------------------------------------------------------------------ sit_on

def test_sit_on_walks_to_the_seat_then_lands_the_hips_on_it(world):
    begin(world, {"dad": {"at": "left", "facing": "left"}}, {"chair": {"at": "chair_spot"}})
    tracks = TrackSet()
    end = run(world, tracks, 3.0, "sit_on", who="dad", what="chair")
    arrive = 3.0 + 0.9 / WALK
    assert end == pytest.approx(arrive + 0.4)
    # the walk: turn right, slide from the mark to the floor under the seat
    assert keys(tracks, "puppet:dad", "root.facing") == [[3.0, 1.0]]
    assert at(tracks, "dad", "root.position", 3.0) == pytest.approx([-0.6, 0.0, 0.5])
    assert at(tracks, "dad", "root.position", arrive) == pytest.approx([0.3, 0.0, 0.5], abs=1e-3)
    assert at(tracks, "dad", "leg_l.upper.rot", arrive) == pytest.approx(0.0)
    # the sit: hips on the socket
    root = at(tracks, "dad", "root.position", end)
    s = scale("tall")
    assert root == pytest.approx([0.3, SEAT_Y - HIP * s, 0.5], abs=1e-3)
    assert root[1] + HIP * s == pytest.approx(SEAT_Y, abs=1e-3)
    for bone, v in [("leg_l.upper", 1.5), ("leg_l.lower", -1.5), ("leg_r.upper", 1.5), ("leg_r.lower", -1.5)]:
        assert at(tracks, "dad", f"{bone}.rot", end) == pytest.approx(v)
    dad = world.people["dad"]
    assert (dad.pose, dad.seat, dad.facing) == ("sit", ("chair", "seat"), 1)
    assert dad.pos == pytest.approx((0.3, 0.0, 0.5))
    assert world.props["chair"].occupants == {"seat": "puppet:dad"}


def test_sit_on_close_to_the_seat_sits_without_walking(world):
    begin(world, {"mom": {"at": "by_chair"}}, {"chair": {"at": "chair_spot"}})
    tracks = TrackSet()
    end = run(world, tracks, 3.0, "sit_on", who="mom", what="chair", socket="seat")
    assert end == pytest.approx(3.4)
    s = scale("average")
    assert keys(tracks, "puppet:mom", "root.position") == [
        [3.0, [0.33, 0.0, 0.5]], [3.4, [0.3, round(SEAT_Y - HIP * s, 3), 0.5]]]
    assert not tracks.has_track("puppet:mom", "root.facing")
    assert not tracks.has_track("puppet:mom", "arm_r.upper.rot")
    assert at(tracks, "mom", "leg_r.upper.rot", 3.4) == pytest.approx(1.5)
    assert world.people["mom"].seat == ("chair", "seat")


def test_sit_on_walk_leaves_a_holding_arm_alone(world):
    begin(world, {"mom": {"at": "left", "holding": "hymn_books"}}, {"chair": {"at": "chair_spot"}})
    tracks = TrackSet()
    run(world, tracks, 3.0, "sit_on", who="mom", what="chair")
    assert not tracks.has_track("puppet:mom", "arm_r.upper.rot")
    assert tracks.has_track("puppet:mom", "arm_l.upper.rot")
    assert world.people["mom"].holding == {"hand_r": "hymn_books"}


def test_sit_on_errors(world):
    begin(world, {"dad": {"at": "left"}, "mom": {"at": "by_chair"}, "kid": {"at": "mid", "pose": "kneel"}},
          {"chair": {"at": "chair_spot"}, "sedan": {"at": "right"}, "hymn_books": {"at": "mid"}})
    tracks = TrackSet()
    fails(world, tracks, "no_seat", "hymn_books has nowhere to sit", 3.0, "sit_on", who="dad", what="hymn_books")
    fails(world, tracks, "not_on_stage", "stool is not on stage", 3.0, "sit_on", who="dad", what="stool")
    fails(world, tracks, "vehicle_seat", "sedan is a vehicle; use get_in", 3.0, "sit_on", who="dad", what="sedan")
    fails(world, tracks, "unknown_socket", "chair has no socket arm", 3.0, "sit_on", who="dad", what="chair", socket="arm")
    fails(world, tracks, "unknown_id", "piano is not a known prop", 3.0, "sit_on", who="dad", what="piano")
    fails(world, tracks, "not_standing", "kid is kneeling; stand_up first", 3.0, "sit_on", who="kid", what="chair")
    run(world, tracks, 3.0, "sit_on", who="mom", what="chair")
    fails(world, tracks, "socket_taken", "chair.seat is taken by mom", 4.0, "sit_on", who="dad", what="chair")
    fails(world, tracks, "already_seated", "mom is seated in chair.seat", 4.0, "sit_on", who="mom", what="chair")


def test_sit_on_a_vehicle_is_the_worlds_vehicle_seat_error_and_no_local_check_is_left(world):
    begin(world, {"dad": {"at": "left"}}, {"sedan": {"at": "right"}})
    tracks = TrackSet()
    fails(world, tracks, "vehicle_seat", "sedan is a vehicle; use get_in", 3.0, "sit_on", who="dad", what="sedan")
    src = inspect.getsource(verbs_body)
    assert "is_vehicle" not in src and '"vehicle"' not in src  # World.sit_on owns the check


def test_sit_on_a_held_or_offstage_prop_or_from_offstage(world):
    begin(world, {"dad": {"at": "left", "holding": "stool"}, "mom": {"at": "mid"}, "kid": {"at": "offstage_right"}},
          {"chair": {"at": "chair_spot"}})
    tracks = TrackSet()
    fails(world, tracks, "held", "dad is holding it", 3.0, "sit_on", who="mom", what="stool")
    fails(world, tracks, "held", "dad is holding it", 3.0, "sit_on", who="dad", what="stool")
    fails(world, tracks, "offstage", "kid is offstage; use enter first", 3.0, "sit_on", who="kid", what="chair")


# ------------------------------------------------------------------ stand_up

def test_stand_up_from_a_seat_lands_on_the_floor_in_front_of_it(world):
    begin(world, {"dad": {"at": "stage"}}, {"chair": {"at": "stage"}})  # chair on a 0.3 platform
    tracks = TrackSet()
    sat = run(world, tracks, 3.0, "sit_on", who="dad", what="chair")
    assert sat == pytest.approx(3.4)
    s = scale("tall")
    assert at(tracks, "dad", "root.position", 3.4) == pytest.approx([-0.4, 0.3 + SEAT_Y - HIP * s, 0.5], abs=1e-3)
    end = run(world, tracks, 5.0, "stand_up", who="dad")
    assert end == pytest.approx(5.4)
    assert at(tracks, "dad", "root.position", 5.0) == pytest.approx([-0.4, 0.3 + SEAT_Y - HIP * s, 0.5], abs=1e-3)
    assert at(tracks, "dad", "root.position", 5.4) == pytest.approx([-0.4, 0.3, 0.5], abs=1e-3)
    for bone in ("leg_l.upper", "leg_l.lower", "leg_r.upper", "leg_r.lower"):
        assert at(tracks, "dad", f"{bone}.rot", 5.0) == pytest.approx(1.5 if "upper" in bone else -1.5)
        assert at(tracks, "dad", f"{bone}.rot", 5.4) == pytest.approx(0.0)
    dad = world.people["dad"]
    assert (dad.pose, dad.seat) == ("stand", None)
    assert dad.pos == pytest.approx((-0.4, 0.3, 0.5))
    assert world.props["chair"].occupants == {}


def test_stand_up_from_a_start_seat_moves_the_world_position_to_the_floor(world):
    begin(world, {"mom": {"at": "left", "in": "chair.seat"}}, {"chair": {"at": "chair_spot"}})
    tracks = TrackSet()
    run(world, tracks, 3.0, "stand_up", who="mom")
    assert at(tracks, "mom", "root.position", 3.4) == pytest.approx([0.3, 0.0, 0.5])
    assert world.people["mom"].pos == pytest.approx((0.3, 0.0, 0.5))
    assert world.people["mom"].seat is None


def test_stand_up_from_kneeling_keeps_a_holding_arm(world):
    begin(world, {"mom": {"at": "mid", "holding": "hymn_books"}})
    tracks = TrackSet()
    tracks.add("puppet:mom", "arm_r.upper.rot", 0.0, 0.3)  # hold pose
    tracks.add("puppet:mom", "arm_r.lower.rot", 0.0, 1.2)
    run(world, tracks, 3.0, "kneel", who="mom")
    end = run(world, tracks, 4.0, "stand_up", who="mom")
    assert end == pytest.approx(4.4)
    assert at(tracks, "mom", "root.position", 4.0) == pytest.approx([0.0, -0.24 * scale("average"), 0.5])
    assert at(tracks, "mom", "root.position", 4.4) == pytest.approx([0.0, 0.0, 0.5])
    assert at(tracks, "mom", "leg_l.upper.rot", 4.4) == pytest.approx(0.0)
    assert at(tracks, "mom", "arm_r.upper.rot", 4.4) == pytest.approx(0.3)
    assert keys(tracks, "puppet:mom", "arm_r.upper.rot") == [[0.0, 0.3]]
    assert world.people["mom"].pose == "stand"


def test_stand_up_errors(world):
    begin(world, {"dad": {"at": "mid"}, "mom": {"at": "right", "in": "sedan.driver"}}, {"sedan": {"at": "right"}})
    tracks = TrackSet()
    fails(world, tracks, "not_sitting", "dad is not sitting or kneeling", 3.0, "stand_up", who="dad")
    fails(world, tracks, "seated_vehicle", "mom is in sedan; use get_out", 3.0, "stand_up", who="mom")


# ------------------------------------------------------------------ kneel

def test_kneel_lowers_the_root_and_bends_the_legs(world):
    begin(world, {"mom": {"at": "mid"}})
    tracks = TrackSet()
    end = run(world, tracks, 3.0, "kneel", who="mom")
    assert end == pytest.approx(3.4)
    assert keys(tracks, "puppet:mom", "root.position") == [
        [3.0, [0.0, 0.0, 0.5]], [3.4, [0.0, round(-0.24 * scale("average"), 3), 0.5]]]
    for bone, v in [("leg_l.upper", 1.4), ("leg_l.lower", -1.5), ("leg_r.upper", 0.0), ("leg_r.lower", -1.5)]:
        assert at(tracks, "mom", f"{bone}.rot", 3.0) == pytest.approx(0.0)
        assert at(tracks, "mom", f"{bone}.rot", 3.4) == pytest.approx(v)
    assert world.people["mom"].pose == "kneel"
    assert world.people["mom"].pos == (0.0, 0.0, 0.5)  # the standing mark is remembered


def test_kneel_with_dur_stands_back_up(world):
    begin(world, {"dad": {"at": "left"}})
    tracks = TrackSet()
    end = run(world, tracks, 3.0, "kneel", who="dad", dur=2.0)
    assert end == pytest.approx(5.4)
    low = round(-0.24 * scale("tall"), 3)
    assert keys(tracks, "puppet:dad", "root.position") == [
        [3.0, [-0.6, 0.0, 0.5]], [3.4, [-0.6, low, 0.5]], [5.0, [-0.6, low, 0.5]], [5.4, [-0.6, 0.0, 0.5]]]
    assert at(tracks, "dad", "leg_l.upper.rot", 5.0) == pytest.approx(1.4)
    assert at(tracks, "dad", "leg_l.upper.rot", 5.4) == pytest.approx(0.0)
    assert world.people["dad"].pose == "stand"


def test_kneel_with_a_short_dur_shortens_the_blend(world):
    begin(world, {"dad": {"at": "left"}})
    tracks = TrackSet()
    end = run(world, tracks, 3.0, "kneel", who="dad", dur=0.3)
    assert end == pytest.approx(3.7)
    assert at(tracks, "dad", "leg_l.upper.rot", 3.3) == pytest.approx(1.4)
    assert at(tracks, "dad", "leg_l.upper.rot", 3.7) == pytest.approx(0.0)


def test_kneel_errors(world):
    begin(world, {"dad": {"at": "left", "in": "chair.seat"}, "mom": {"at": "mid", "pose": "kneel"},
                  "kid": {"at": "right", "pose": "sit"}}, {"chair": {"at": "left"}})
    tracks = TrackSet()
    fails(world, tracks, "seated", "dad is seated in chair.seat", 3.0, "kneel", who="dad")
    fails(world, tracks, "already_kneeling", "mom is already kneeling", 3.0, "kneel", who="mom")
    fails(world, tracks, "not_standing", "kid is sitting on the floor; stand_up first", 3.0, "kneel", who="kid")
    fails(world, tracks, "bad_dur", "dur must be a positive number of seconds, got 0.0", 3.0, "kneel", who="kid", dur=0)


def test_gestures_and_kneel_refuse_an_offstage_puppet(world):
    begin(world, {"mom": {"at": "mid"}})  # dad is not listed: he waits offstage
    tracks = TrackSet()
    for do, params in [("wave", {}), ("point_at", {"target": "mom"}), ("lean_toward", {"target": "mom"}),
                       ("kneel", {})]:
        fails(world, tracks, "offstage", "dad is offstage; use enter first", 3.0, do, who="dad", **params)
    fails(world, tracks, "offstage", "dad is offstage", 3.0, "hug", who="dad", target="mom")
    fails(world, tracks, "unknown_id", "ghost is not in the cast", 3.0, "wave", who="ghost")


# ------------------------------------------------------------------ hug

def test_hug_walks_up_to_the_target_and_occupies_it(world):
    begin(world, {"dad": {"at": "left", "facing": "right"}, "mom": {"at": "mid", "facing": "right"}})
    tracks = TrackSet()
    end = run(world, tracks, 3.0, "hug", who="dad", target="mom")
    arrive = 3.0 + (0.6 - HUG_GAP) / WALK
    assert end == pytest.approx(arrive + 1.5)
    assert at(tracks, "dad", "root.position", 3.0) == pytest.approx([-0.6, 0.0, 0.5])
    assert at(tracks, "dad", "root.position", arrive) == pytest.approx([-HUG_GAP, 0.0, 0.5], abs=1e-3)
    gap = 0.0 - at(tracks, "dad", "root.position", end)[0]
    assert gap == pytest.approx(HUG_GAP, abs=1e-3)
    # both blend into the hug pose, hold, and release
    for who in ("dad", "mom"):
        assert at(tracks, who, "arm_r.upper.rot", arrive) == pytest.approx(0.0)
        assert at(tracks, who, "arm_r.upper.rot", arrive + 0.4) == pytest.approx(1.3)
        assert at(tracks, who, "arm_l.lower.rot", arrive + 0.4) == pytest.approx(0.9)
        assert at(tracks, who, "torso.rot", end - 0.4) == pytest.approx(-0.1)
        assert at(tracks, who, "arm_r.upper.rot", end) == pytest.approx(0.0)
    # mom turns to face dad
    assert keys(tracks, "puppet:mom", "root.facing") == [[round(arrive, 3), -1.0]]
    assert not tracks.has_track("puppet:mom", "root.position")
    dad, mom = world.people["dad"], world.people["mom"]
    assert (dad.facing, mom.facing) == (1, -1)
    assert dad.pos == pytest.approx((-HUG_GAP, 0.0, 0.5))
    assert (mom.activity, mom.busy_until) == ("hugging", pytest.approx(end - AS))


def test_hug_close_enough_does_not_walk(world):
    begin(world, {"dad": {"at": "close", "facing": "right"}, "mom": {"at": "mid", "facing": "left"}})
    tracks = TrackSet()
    end = run(world, tracks, 3.0, "hug", who="dad", target="mom", dur=2.0)
    assert end == pytest.approx(5.0)
    assert not tracks.has_track("puppet:dad", "root.position")
    assert not tracks.has_track("puppet:dad", "root.facing")
    assert not tracks.has_track("puppet:mom", "root.facing")
    assert at(tracks, "dad", "arm_r.upper.rot", 3.4) == pytest.approx(1.3)
    assert world.people["mom"].busy_until == pytest.approx(3.0)


def test_hug_errors(world):
    begin(world, {"dad": {"at": "left"}, "mom": {"at": "mid"}, "kid": {"at": "offstage_right"}})
    tracks = TrackSet()
    fails(world, tracks, "hug_self", "dad cannot hug themselves", 3.0, "hug", who="dad", target="dad")
    fails(world, tracks, "offstage", "kid is offstage", 3.0, "hug", who="dad", target="kid")
    fails(world, tracks, "unknown_id", "chair is not in the cast", 3.0, "hug", who="dad", target="chair")
    world.occupy("mom", 5.0, "walking", at=0.0)
    fails(world, tracks, "busy", "mom is still walking until t=5", 3.0, "hug", who="dad", target="mom")


def test_hug_rejects_a_seated_target(world):
    begin(world, {"dad": {"at": "left"}, "mom": {"at": "mid", "in": "chair.seat"}}, {"chair": {"at": "mid"}})
    tracks = TrackSet()
    fails(world, tracks, "seated", "mom is seated in chair.seat", 3.0, "hug", who="dad", target="mom")


def test_hug_a_kneeling_hugger_cannot_walk_over(world):
    begin(world, {"dad": {"at": "left", "pose": "kneel"}, "mom": {"at": "mid"}})
    tracks = TrackSet()
    fails(world, tracks, "not_standing", "dad is kneeling; stand_up first", 3.0, "hug", who="dad", target="mom")


# ------------------------------------------------------------------ atomicity

def test_a_failing_handler_after_earlier_keys_changes_nothing(world):
    begin(world, {"dad": {"at": "left"}, "mom": {"at": "by_chair"}}, {"chair": {"at": "chair_spot"}})
    tracks = TrackSet()
    run(world, tracks, 3.0, "sit_on", who="mom", what="chair")
    run(world, tracks, 4.0, "wave", who="dad", target="mom")
    # every verb that can fail late (after walks or geometry) fails cleanly
    fails(world, tracks, "socket_taken", "chair.seat is taken by mom", 6.0, "sit_on", who="dad", what="chair")
    fails(world, tracks, "seated", "mom is seated in chair.seat", 6.0, "hug", who="dad", target="mom")
    fails(world, tracks, "unknown_target", "ghost is not a person, prop or mark", 6.0, "point_at", who="dad", target="ghost")
    fails(world, tracks, "seated", "mom is seated in chair.seat", 6.0, "kneel", who="mom")
    fails(world, tracks, "not_sitting", "dad is not sitting or kneeling", 6.0, "stand_up", who="dad")


# ------------------------------------------------------------- speed from the sheet's notes
# The sit_on and hug approaches walk at World.people[who].speed_scale times the walk speed
# (0.6 with a cane, 1.25 "quickly"); the sit, the hug pose, kneel and stand_up keep their timings.

CANE, QUICK = "walks with a cane", "quickly"
SCALED = [(CANE, 0.6), (QUICK, 1.25)]


def noted_world(notes: dict[str, str | None]) -> World:
    """The fixture's people and props, with CharacterSheet.notes from ``notes`` (by person id)."""
    people = [person(p.id, p.character.build, notes.get(p.id)) for p in PEOPLE]
    return World(people, PROPS)


@pytest.mark.parametrize("notes,factor", SCALED)
def test_sit_on_walks_to_the_seat_at_the_notes_speed(notes, factor):
    world = begin(noted_world({"dad": notes}), {"dad": {"at": "left", "facing": "left"}}, {"chair": {"at": "chair_spot"}})
    assert world.people["dad"].speed_scale == factor
    tracks = TrackSet()
    end = run(world, tracks, 3.0, "sit_on", who="dad", what="chair")
    arrive = 3.0 + 0.9 / WALK / factor  # plain: 3.0 + 0.9 / WALK
    assert end == pytest.approx(arrive + 0.4, abs=1e-3)  # the sit itself is still 0.4
    assert at(tracks, "dad", "root.position", arrive) == pytest.approx([0.3, 0.0, 0.5], abs=1e-3)
    s = scale("tall")
    assert at(tracks, "dad", "root.position", end) == pytest.approx([0.3, SEAT_Y - HIP * s, 0.5], abs=1e-3)
    assert world.people["dad"].seat == ("chair", "seat")


@pytest.mark.parametrize("notes,factor", SCALED)
def test_hug_approach_walks_at_the_notes_speed(notes, factor):
    world = begin(noted_world({"dad": notes}),
                  {"dad": {"at": "left", "facing": "right"}, "mom": {"at": "mid", "facing": "right"}})
    tracks = TrackSet()
    end = run(world, tracks, 3.0, "hug", who="dad", target="mom")
    arrive = 3.0 + (0.6 - HUG_GAP) / WALK / factor
    assert end == pytest.approx(arrive + 1.5, abs=1e-3)  # the hug itself is still 1.5
    assert at(tracks, "dad", "root.position", arrive) == pytest.approx([-HUG_GAP, 0.0, 0.5], abs=1e-3)
    assert keys(tracks, "puppet:mom", "root.facing") == [[round(arrive, 3), -1.0]]
    assert world.people["mom"].busy_until == pytest.approx(end - AS)


def test_hug_uses_the_huggers_speed_not_the_targets():
    world = begin(noted_world({"mom": CANE}),
                  {"dad": {"at": "left", "facing": "right"}, "mom": {"at": "mid", "facing": "right"}})
    end = run(world, TrackSet(), 3.0, "hug", who="dad", target="mom")
    assert end == pytest.approx(3.0 + (0.6 - HUG_GAP) / WALK + 1.5, abs=1e-3)


def _same_with_any_notes(start: dict, prop_start: dict | None, *calls) -> None:
    """Run ``calls`` with dad's notes None, cane and quickly: ends and tracks are identical."""
    results = []
    for notes in (None, CANE, QUICK):
        world = begin(noted_world({"dad": notes}), start, prop_start)
        tracks = TrackSet()
        ends = [run(world, tracks, t, do, **params) for t, do, params in calls]
        results.append((ends, [tr.model_dump() for tr in tracks.tracks()]))
    assert results[1] == results[0]
    assert results[2] == results[0]


def test_kneel_and_stand_up_do_not_depend_on_notes():
    _same_with_any_notes({"dad": {"at": "left"}}, None,
                         (3.0, "kneel", {"who": "dad"}), (5.0, "stand_up", {"who": "dad"}))
    _same_with_any_notes({"dad": {"at": "left"}}, None, (3.0, "kneel", {"who": "dad", "dur": 2.0}))


def test_sitting_down_close_and_standing_up_from_a_seat_do_not_depend_on_notes():
    _same_with_any_notes({"dad": {"at": "stage"}}, {"chair": {"at": "stage"}},
                         (3.0, "sit_on", {"who": "dad", "what": "chair"}), (5.0, "stand_up", {"who": "dad"}))


def test_gestures_do_not_depend_on_notes():
    _same_with_any_notes({"dad": {"at": "left", "facing": "left"}, "mom": {"at": "mid"}}, None,
                         (3.0, "wave", {"who": "dad", "target": "mom"}),
                         (5.0, "point_at", {"who": "dad", "target": "right"}),
                         (7.0, "lean_toward", {"who": "dad", "target": "mom"}))
