"""Vehicle verbs: get_in, get_out, drive_to, ride_to (screenplay/verbs_vehicles.py)."""

from __future__ import annotations

import hashlib
import inspect
import json
import math

import pytest

import schemas
from screenplay import motion, verbs_body, verbs_core, verbs_vehicles
from screenplay.body import POSE_SECONDS, hold_pose
from screenplay.compiler import compile as compile_screenplay
from screenplay.ik import RIG, scale
from screenplay.schema import Action, Shot
from screenplay.state import StateError, World
from screenplay.tracks import PLANT_CHANNEL, TrackSet
from screenplay.verbs_vehicles import HANDLERS, WHEEL_RADIUS_FRACTION
from verb_cases import SEDAN_CURB, _a, _at, _one, base_scene

# ------------------------------------------------------------------ fixtures


def person(pid: str, build: str = "average", notes: str | None = None) -> schemas.Person:
    return schemas.Person(id=pid, family_id="f", name=pid.title(),
                          character={"color": "#123456", "build": build, "notes": notes})


CAR = schemas.Prop(
    id="car", name="Car", size="vehicle", art={"body": None, "wheel": None}, aspect=2.0,
    sockets={"driver": [0.35, 0.55], "passenger": [0.6, 0.55]},
    parts={"wheel_front": {"art": "wheel", "at": [0.22, 0.9], "spin": True},
           "wheel_back": {"art": "wheel", "at": [0.8, 0.9], "spin": True}},
    states={"parked": {}, "driving": {}},
)
CHAIR = schemas.Prop(id="chair", name="Chair", size="furniture", art={"body": None}, sockets={"seat": [0.5, 0.5]})
SUITCASE = schemas.Prop(id="suitcase", name="Suitcase", size="carried", art={"body": None})

PEOPLE = [person("dad", "tall"), person("mom"), person("kid", "small")]
PROPS = [CAR, CHAIR, SUITCASE]

# Every coordinate below is exact at 3 dp, so rounded tracks can be checked to 1e-6.
MARKS = {
    "lot": (-0.4, 0.0, 0.5),
    "church": (0.5, 0.0, 0.5),
    "far_left": (-0.8, 0.0, 0.5),
    "door_d": (-0.67, 0.0, 0.5),   # under the driver socket of a car parked on "lot"
    "door_p": (-0.22, 0.0, 0.5),   # under the passenger socket
    "porch": (0.8, 0.0, 0.5),
    "chair_spot": (0.3, 0.0, 0.5),
}
HIP = {"dad": RIG["hipHeight"] * scale("tall"), "mom": RIG["hipHeight"] * scale("average"),
       "kid": RIG["hipHeight"] * scale("small")}
RADIUS = WHEEL_RADIUS_FRACTION * motion.prop_dimensions("vehicle", 2.0)[1]
ACTIVITY = {"get_in": "getting in", "get_out": "getting out", "drive_to": "driving", "ride_to": "riding"}


class RecordingTracks(TrackSet):
    """A TrackSet that also keeps every raw (unrounded) key, for exact checks. Foot-planting
    markers (PLANT_CHANNEL) are not keys, so they are not recorded; plant_times has them."""

    def __init__(self) -> None:
        super().__init__()
        self.raw: list[tuple] = []

    def add(self, target, channel, t, value):  # noqa: ANN001
        super().add(target, channel, t, value)
        if channel != PLANT_CHANNEL:
            self.raw.append((target, channel, t, value))


def make_world(start: dict | None = None, prop_start: dict | None = None,
               notes: dict[str, str | None] | None = None) -> World:
    """`notes` gives CharacterSheet.notes by person id (default: nobody has any)."""
    people = [person(p.id, p.character.build, (notes or {}).get(p.id)) for p in PEOPLE] if notes else PEOPLE
    world = World(people, PROPS)
    shot = Shot.model_validate({
        "id": "s1", "set": "street",
        "start": start if start is not None else {"dad": {"at": "door_d"}, "mom": {"at": "door_p"}, "kid": {"at": "porch"}},
        "prop_start": prop_start if prop_start is not None else {"car": {"at": "lot", "state": "parked"},
                                                                 "chair": {"at": "chair_spot"}},
    })
    world.begin_shot(shot, MARKS, same_set_as_previous=False)
    return world


def make_locate(world: World):
    def locate(name: str, t: float):
        if name in MARKS:
            return MARKS[name]
        if name in world.people:
            return world.people[name].pos
        if name in world.props and world.props[name].world_pos is not None:
            return world.props[name].world_pos
        raise StateError("unknown_target", f"{name} is not a person, prop or mark")
    return locate


def run(world: World, tracks: TrackSet, t: float, action_start: float = 0.0, **fields) -> float:
    """Do what the compiler does around a handler call."""
    action = Action.model_validate({"t": round(t - action_start, 6), **fields})
    world.now = round(t - action_start, 6)
    if action.who:
        world.check_free(action.who)
    end = HANDLERS[action.do](world, tracks, action, t, action_start, make_locate(world))
    assert end >= t
    if action.who:
        world.occupy(action.who, end - action_start, ACTIVITY[action.do], at=world.now)
    return end


def seated_world(tracks: TrackSet, *who: str) -> World:
    world = make_world()
    t = 0.0
    for w in who:
        t = run(world, tracks, t, do="get_in", who=w, what="car")
    return world


def car_path(tracks: TrackSet) -> list[tuple[float, list[float]]]:
    for tr in tracks.tracks():
        if tr.target == "prop:car" and tr.channel == "position":
            return [(k[0], k[1]) for k in tr.keys]
    return []


def raises(code: str, reason: str):
    class _Ctx:
        def __enter__(self):
            self.cm = pytest.raises(StateError)
            self.info = self.cm.__enter__()
            return self.info

        def __exit__(self, *exc):
            out = self.cm.__exit__(*exc)
            assert (self.info.value.code, self.info.value.reason) == (code, reason)
            return out
    return _Ctx()


def assert_atomic(world: World, tracks: TrackSet, code: str, reason: str, **fields) -> None:
    t = fields.pop("t", 5.0)
    action = Action.model_validate({"t": t, **fields})
    world.now = t
    before_world, before_tracks = world.snapshot(), tracks.tracks()
    with raises(code, reason):
        HANDLERS[action.do](world, tracks, action, t, 0.0, make_locate(world))
    assert world.snapshot() == before_world
    assert tracks.tracks() == before_tracks


# ------------------------------------------------------------------ contract


def test_handlers_are_exactly_the_vehicle_verbs_with_the_shared_signature():
    assert set(HANDLERS) == {"get_in", "get_out", "drive_to", "ride_to"}
    for verb, fn in HANDLERS.items():
        assert fn is getattr(verbs_vehicles, f"apply_{verb}")
        assert list(inspect.signature(fn).parameters) == ["world", "tracks", "action", "t", "action_start", "locate"]


# ------------------------------------------------------------------ get_in


def test_get_in_default_takes_driver_then_next_free_seat():
    tracks = TrackSet()
    world = make_world()
    run(world, tracks, 0.0, do="get_in", who="mom", what="car")
    assert world.people["mom"].seat == ("car", "driver")
    run(world, tracks, 1.0, do="get_in", who="dad", what="car")
    assert world.people["dad"].seat == ("car", "passenger")
    assert world.props["car"].occupants == {"driver": "puppet:mom", "passenger": "puppet:dad"}


def test_get_in_explicit_socket():
    tracks = TrackSet()
    world = make_world()
    run(world, tracks, 0.0, do="get_in", who="dad", what="car", socket="passenger")
    assert world.people["dad"].seat == ("car", "passenger")
    run(world, tracks, 0.0, do="get_in", who="mom", what="car")
    assert world.people["mom"].seat == ("car", "driver")


def test_get_in_puts_the_hips_on_the_socket_and_sits():
    tracks = RecordingTracks()
    world = make_world()
    end = run(world, tracks, 2.0, do="get_in", who="dad", what="car")  # dad stands under the driver socket
    assert end == pytest.approx(2.4)
    seat = world.socket_world("car", "driver")
    assert seat == pytest.approx((-0.67, 0.405, 0.5))
    root = tracks.value_at("puppet:dad", "root.position", end)
    assert root[0] == pytest.approx(seat[0], abs=1e-9)
    assert root[1] + HIP["dad"] == pytest.approx(seat[1], abs=1e-6)
    assert root[2] == pytest.approx(0.5)
    # No walk: just the sit's two root keys, from where dad stands onto the seat.
    assert [k[2] for k in tracks.raw if k[1] == "root.position"] == [2.0, 2.4]
    assert not any(k[1] == "root.facing" for k in tracks.raw)
    p = world.people["dad"]
    assert p.pose == "sit"
    assert p.pos == pytest.approx((seat[0], seat[1] - HIP["dad"], 0.5))
    assert world.props["car"].occupants["driver"] == "puppet:dad"
    assert world.people["dad"].busy_until == pytest.approx(2.4)
    assert world.people["dad"].activity == "getting in"


def test_get_in_walks_to_the_door_first_when_far():
    tracks = RecordingTracks()
    world = make_world()
    end = run(world, tracks, 0.0, do="get_in", who="kid", what="car", socket="passenger")
    seat = world.socket_world("car", "passenger")  # (-0.22, 0.405, 0.5)
    walk = (0.8 - seat[0]) / motion.SPEEDS["walk"]
    assert end == pytest.approx(walk + 0.4)
    # Walks left, so turns to face left at the start.
    assert ("puppet:kid", "root.facing", 0.0, -1) in tracks.raw
    at_door = motion.position_at(motion.path_from_keys(tracks.raw, "puppet:kid", "root.position"), walk)
    assert at_door == pytest.approx((seat[0], 0.0, 0.5))
    root = tracks.value_at("puppet:kid", "root.position", end)
    assert [root[0], root[1] + HIP["kid"], root[2]] == pytest.approx(list(seat), abs=1e-6)
    assert world.people["kid"].facing == -1
    # The walk cycle ends with legs at rest before the sit pose blends in from there.
    at_walk_end = [k[3] for k in tracks.raw if k[1] == "leg_l.upper.rot" and k[2] == pytest.approx(walk)]
    assert len(at_walk_end) == 2 and at_walk_end == pytest.approx([0.0, 0.0])  # walk end, sit start


def test_get_in_full_vehicle():
    tracks = TrackSet()
    world = seated_world(tracks, "dad", "mom")
    assert_atomic(world, tracks, "no_free_seat", "every seat in car is taken", do="get_in", who="kid", what="car")


def test_get_in_named_socket_taken():
    tracks = TrackSet()
    world = seated_world(tracks, "dad")
    assert_atomic(world, tracks, "socket_taken", "car.driver is taken by dad",
                  do="get_in", who="kid", what="car", socket="driver")


def test_get_in_already_seated():
    tracks = TrackSet()
    world = seated_world(tracks, "dad")
    assert_atomic(world, tracks, "already_seated", "dad is seated in car.driver; use get_out first", do="get_in", who="dad", what="car")


def test_get_in_unplaced_vehicle():
    tracks = TrackSet()
    world = make_world(prop_start={})
    assert_atomic(world, tracks, "not_on_stage", "car is not on stage", do="get_in", who="dad", what="car")


def test_get_in_not_a_vehicle():
    tracks = TrackSet()
    world = make_world()
    assert_atomic(world, tracks, "not_a_vehicle", "chair is not a vehicle", do="get_in", who="dad", what="chair")


def test_get_in_unknown_prop_and_socket():
    tracks = TrackSet()
    world = make_world()
    assert_atomic(world, tracks, "unknown_id", "bus is not a known prop", do="get_in", who="dad", what="bus")
    assert_atomic(world, tracks, "unknown_socket", "car has no socket roof; its sockets are driver, passenger",
                  do="get_in", who="dad", what="car", socket="roof")


# ------------------------------------------------------------------ get_out


def test_get_out_stands_beside_the_seat_at_the_acting_plane():
    tracks = TrackSet()
    world = seated_world(tracks, "dad", "mom")
    end = run(world, tracks, 5.0, do="get_out", who="mom", what="car")
    assert end == pytest.approx(5.4)
    seat = (-0.4 + 0.18, 0.405)
    # Starts exactly where she sits, ends on the ground under the seat.
    assert tracks.value_at("puppet:mom", "root.position", 5.0) == pytest.approx([seat[0], seat[1] - HIP["mom"], 0.5])
    assert tracks.value_at("puppet:mom", "root.position", end) == pytest.approx([seat[0], 0.0, 0.5])
    for bone in ("leg_l.upper", "leg_l.lower", "leg_r.upper", "leg_r.lower"):
        assert tracks.value_at("puppet:mom", f"{bone}.rot", end) == pytest.approx(0.0)
    m = world.people["mom"]
    assert (m.seat, m.pose) == (None, "stand")
    assert m.pos == pytest.approx((seat[0], 0.0, 0.5))
    assert "passenger" not in world.props["car"].occupants
    assert world.props["car"].occupants["driver"] == "puppet:dad"


def test_get_out_when_not_in_that_vehicle():
    tracks = TrackSet()
    world = seated_world(tracks, "dad")
    assert_atomic(world, tracks, "not_in_vehicle", "kid is not in car; use get_in first", do="get_out", who="kid", what="car")


def test_get_out_of_a_chair_is_not_a_vehicle():
    tracks = TrackSet()
    world = make_world()
    world.sit_on("kid", "chair")
    assert_atomic(world, tracks, "not_a_vehicle", "chair is not a vehicle", do="get_out", who="kid", what="chair")


def test_get_out_while_the_vehicle_is_moving():
    tracks = TrackSet()
    world = seated_world(tracks, "dad", "mom")
    run(world, tracks, 2.0, do="drive_to", who="dad", what="car", to="church")  # arrives at 3.0
    assert_atomic(world, tracks, "vehicle_moving", "car is moving until t=3",
                  do="get_out", who="mom", what="car", t=2.5)


# ------------------------------------------------------------------ drive_to


def test_drive_to_moves_the_vehicle_spins_the_wheels_and_carries_the_riders():
    tracks = RecordingTracks()
    world = seated_world(tracks, "dad", "mom")
    t0 = 2.0
    arrival = run(world, tracks, t0, do="drive_to", who="dad", what="car", to="church")
    dist = 0.9
    assert arrival == pytest.approx(t0 + dist / motion.DRIVE_SPEED)

    # Vehicle anchor keys.
    assert car_path(tracks) == [(t0, [-0.4, 0.0, 0.5]), (pytest.approx(arrival, abs=1e-3), [0.5, 0.0, 0.5])]

    # Wheels: -distance / radius for +x travel, from 0.
    for part in ("wheel_front", "wheel_back"):
        raw = sorted((k[2], k[3]) for k in tracks.raw if k[1] == f"part.{part}.rot")
        assert raw == [(t0, 0.0), (pytest.approx(arrival), pytest.approx(-dist / RADIUS, abs=1e-9))]
        assert tracks.value_at("prop:car", f"part.{part}.rot", arrival) == pytest.approx(-dist / RADIUS, abs=1e-3)

    # Every seated puppet's hips stay on its socket: start, middle, arrival.
    raw_path = motion.path_from_keys(tracks.raw, "prop:car")
    rounded_path = car_path(tracks)
    for who, sock in (("dad", "driver"), ("mom", "passenger")):
        for t in (t0, (t0 + arrival) / 2, arrival):
            # Raw keys: exact.
            root = motion.position_at(motion.path_from_keys(tracks.raw, f"puppet:{who}", "root.position"), t)
            want = motion.vehicle_socket_position(CAR, sock, raw_path, t)
            assert [root[0], root[1] + HIP[who], root[2]] == pytest.approx(list(want), abs=1e-6)
            # The rounded tracks the stage plays.
            root = tracks.value_at(f"puppet:{who}", "root.position", t)
            want = motion.vehicle_socket_position(CAR, sock, rounded_path, t)
            assert [root[0], root[1] + HIP[who], root[2]] == pytest.approx(list(want), abs=1e-6)

    # World state.
    assert world.props["car"].world_pos == pytest.approx(MARKS["church"])
    assert world.socket_world("car", "driver") == pytest.approx((0.5 - 0.27, 0.405, 0.5))
    assert world.people["dad"].pos == pytest.approx((-0.67 + 0.9, 0.405 - HIP["dad"], 0.5))
    assert world.people["mom"].pos == pytest.approx((-0.22 + 0.9, 0.405 - HIP["mom"], 0.5))
    assert world.people["dad"].busy_until == pytest.approx(arrival)
    assert world.people["dad"].activity == "driving"


def test_drive_to_toward_minus_x_spins_the_wheels_positive():
    tracks = RecordingTracks()
    world = seated_world(tracks, "dad")
    arrival = run(world, tracks, 2.0, do="drive_to", who="dad", what="car", to="far_left")
    assert arrival == pytest.approx(2.0 + 0.4 / motion.DRIVE_SPEED)
    end_rot = [k[3] for k in tracks.raw if k[1] == "part.wheel_front.rot" and k[2] == arrival]
    assert end_rot == [pytest.approx(0.4 / RADIUS, abs=1e-9)]
    assert world.people["dad"].pos == pytest.approx((-1.07, 0.405 - HIP["dad"], 0.5))


def test_two_drives_keep_the_wheel_angle_continuous():
    tracks = RecordingTracks()
    world = seated_world(tracks, "dad")
    a1 = run(world, tracks, 2.0, do="drive_to", who="dad", what="car", to="church")        # +0.9
    a2 = run(world, tracks, a1 + 1.0, do="drive_to", who="dad", what="car", to="lot")     # -0.9
    a3 = run(world, tracks, a2 + 1.0, do="drive_to", who="dad", what="car", to="porch")   # +1.2
    after1 = -0.9 / RADIUS
    after2 = after1 + 0.9 / RADIUS
    after3 = after2 - 1.2 / RADIUS
    # The stage plays the rounded track; every drive starts from the angle the last one ended on.
    track = next(tr for tr in tracks.tracks() if tr.target == "prop:car" and tr.channel == "part.wheel_back.rot")
    keys = [(k[0], k[1]) for k in track.keys]
    assert [k[0] for k in keys] == pytest.approx([2.0, a1, a1 + 1.0, a2, a2 + 1.0, a3], abs=1e-3)
    assert [k[1] for k in keys] == pytest.approx([0.0, after1, after1, after2, after2, after3], abs=2e-3)
    assert keys[1][1] == keys[2][1] and keys[3][1] == keys[4][1]  # no jump at the start of a drive
    # Each drive turns the wheel by exactly its own distance / radius (raw keys).
    raw = sorted((k[2], k[3]) for k in tracks.raw if k[1] == "part.wheel_back.rot")
    turns = [raw[i + 1][1] - raw[i][1] for i in (0, 2, 4)]
    assert turns == pytest.approx([-0.9 / RADIUS, 0.9 / RADIUS, -1.2 / RADIUS], abs=1e-9)
    # Continuous: the wheel holds still between drives.
    for t in (a1 + 0.5, a2 + 0.5):
        assert tracks.value_at("prop:car", "part.wheel_back.rot", t) == pytest.approx(
            tracks.value_at("prop:car", "part.wheel_back.rot", t - 0.5), abs=1e-9)
    assert world.props["car"].world_pos == pytest.approx(MARKS["porch"])


def test_drive_to_where_it_already_is_emits_nothing():
    tracks = TrackSet()
    world = seated_world(tracks, "dad")
    before = tracks.tracks()
    assert run(world, tracks, 3.0, do="drive_to", who="dad", what="car", to="lot") == 3.0
    assert tracks.tracks() == before
    assert world.props["car"].world_pos == pytest.approx(MARKS["lot"])


def test_drive_to_carries_props_resting_on_a_socket_without_keys():
    tracks = TrackSet()
    world = make_world(prop_start={"car": {"at": "lot"}, "suitcase": {"on": "car.passenger"}})
    run(world, tracks, 0.0, do="get_in", who="dad", what="car")
    run(world, tracks, 1.0, do="drive_to", who="dad", what="car", to="church")
    assert not any(tr.target == "prop:suitcase" for tr in tracks.tracks())
    assert world.props["suitcase"].parent == "prop:car.passenger"
    assert world.props["suitcase"].world_pos == pytest.approx(world.socket_world("car", "passenger"))


def test_drive_to_by_a_passenger_is_not_the_driver():
    tracks = TrackSet()
    world = seated_world(tracks, "dad", "mom")
    assert_atomic(world, tracks, "not_driver", "mom is in car.passenger, not car.driver",
                  do="drive_to", who="mom", what="car", to="church")


def test_drive_to_by_someone_outside():
    tracks = TrackSet()
    world = seated_world(tracks, "dad")
    assert_atomic(world, tracks, "not_driver", "kid is not in car.driver; use get_in with socket driver first",
                  do="drive_to", who="kid", what="car", to="church")


def test_drive_to_a_chair_is_not_a_vehicle():
    tracks = TrackSet()
    world = make_world()
    assert_atomic(world, tracks, "not_a_vehicle", "chair is not a vehicle",
                  do="drive_to", who="dad", what="chair", to="church")


def test_drive_to_unknown_mark():
    tracks = TrackSet()
    world = seated_world(tracks, "dad", "mom")
    assert_atomic(world, tracks, "unknown_target", "moon is not a person, prop or mark",
                  do="drive_to", who="dad", what="car", to="moon")


def test_drive_to_while_a_rider_is_still_getting_in():
    tracks = TrackSet()
    world = make_world()
    run(world, tracks, 0.0, do="get_in", who="dad", what="car")             # sits by 0.4
    end = run(world, tracks, 0.0, do="get_in", who="kid", what="car")      # walks from the porch
    assert end > 2.0
    assert_atomic(world, tracks, "busy", f"kid is still settling into car.passenger until t={round(end, 2):g}",
                  do="drive_to", who="dad", what="car", to="church", t=1.0)


# ------------------------------------------------------------------ ride_to


def test_ride_to_the_driver_drives_and_everyone_is_occupied():
    tracks = RecordingTracks()
    world = seated_world(tracks, "dad", "mom")
    start = 10.0  # the shot's t=0 is at timeline 10
    t0 = 13.0
    arrival = run(world, tracks, t0, action_start=start, do="ride_to", who="mom", what="car", to="church")
    assert arrival == pytest.approx(t0 + 0.9 / motion.DRIVE_SPEED)
    assert car_path(tracks)[-1][1] == [0.5, 0.0, 0.5]
    dad, mom = world.people["dad"], world.people["mom"]
    assert (dad.busy_until, dad.activity) == (pytest.approx(arrival - start), "driving")
    assert (mom.busy_until, mom.activity) == (pytest.approx(arrival - start), "riding")
    assert world.props["car"].world_pos == pytest.approx(MARKS["church"])
    raw_path = motion.path_from_keys(tracks.raw, "prop:car")
    for who, sock in (("dad", "driver"), ("mom", "passenger")):
        for t in (t0, (t0 + arrival) / 2, arrival):
            root = motion.position_at(motion.path_from_keys(tracks.raw, f"puppet:{who}", "root.position"), t)
            want = motion.vehicle_socket_position(CAR, sock, raw_path, t)
            assert [root[0], root[1] + HIP[who], root[2]] == pytest.approx(list(want), abs=1e-6)
    end_rot = [k[3] for k in tracks.raw if k[1] == "part.wheel_front.rot" and k[2] == arrival]
    assert end_rot == [pytest.approx(-0.9 / RADIUS, abs=1e-9)]


def test_ride_to_occupies_every_other_rider_too():
    tracks = TrackSet()
    world = make_world()
    run(world, tracks, 0.0, do="get_in", who="dad", what="car")
    run(world, tracks, 0.5, do="get_in", who="mom", what="car")
    run(world, tracks, 1.0, do="get_out", who="dad", what="car")
    world.now = 2.0
    world.sit_on("kid", "chair")  # kid is not in the car; untouched
    run(world, tracks, 2.0, do="get_in", who="dad", what="car")  # back in the driver's seat
    t = 6.0
    arrival = run(world, tracks, t, do="ride_to", who="mom", what="car", to="porch")
    assert world.people["dad"].busy_until == pytest.approx(arrival)
    assert world.people["kid"].activity is None


def test_ride_to_without_a_driver():
    tracks = TrackSet()
    world = make_world()
    run(world, tracks, 0.0, do="get_in", who="mom", what="car", socket="passenger")
    assert_atomic(world, tracks, "no_driver", "nobody is in car.driver; someone must get_in it with socket driver first", do="ride_to", who="mom", what="car", to="church")


def test_ride_to_with_a_busy_driver():
    tracks = TrackSet()
    world = seated_world(tracks, "dad", "mom")
    world.now = 1.0
    world.occupy("dad", 7.5, "waving")
    assert_atomic(world, tracks, "busy", "dad is still waving until t=7.5",
                  do="ride_to", who="mom", what="car", to="church", t=5.0)


def test_ride_to_when_not_in_the_vehicle():
    tracks = TrackSet()
    world = seated_world(tracks, "dad")
    assert_atomic(world, tracks, "not_in_vehicle", "kid is not in car; use get_in first", do="ride_to", who="kid", what="car", to="church")


def test_ride_to_a_chair_is_not_a_vehicle():
    tracks = TrackSet()
    world = make_world()
    assert_atomic(world, tracks, "not_a_vehicle", "chair is not a vehicle",
                  do="ride_to", who="kid", what="chair", to="church")


def test_ride_to_by_the_driver_works_like_drive_to():
    tracks = TrackSet()
    world = seated_world(tracks, "dad")
    arrival = run(world, tracks, 2.0, do="ride_to", who="dad", what="car", to="church")
    assert arrival == pytest.approx(3.0)
    assert world.people["dad"].activity == "riding"  # the compiler's ACTIVITY for ride_to
    assert world.props["car"].world_pos == pytest.approx(MARKS["church"])


# ------------------------------------------------------------------ round trip


def test_get_in_drive_get_out_round_trip():
    tracks = TrackSet()
    world = seated_world(tracks, "dad", "mom")
    arrival = run(world, tracks, 2.0, do="drive_to", who="dad", what="car", to="church")
    end = run(world, tracks, arrival, do="get_out", who="mom", what="car")
    seat_x = 0.5 + 0.18
    assert tracks.value_at("puppet:mom", "root.position", end) == pytest.approx([seat_x, 0.0, 0.5])
    assert world.people["mom"].pos == pytest.approx((seat_x, 0.0, 0.5))
    assert not math.isnan(tracks.value_at("prop:car", "part.wheel_front.rot", end))


# ------------------------------------------------------------- speed from the sheet's notes
# get_in's walk to the door goes at World.people[who].speed_scale times the walk speed; the
# sit onto the seat, get_out and the drive itself do not depend on the riders' notes.

CANE, QUICK = "walks with a cane", "quickly"


@pytest.mark.parametrize("notes,factor", [(CANE, 0.6), (QUICK, 1.25)])
def test_get_in_walks_to_the_door_at_the_notes_speed(notes, factor):
    tracks = RecordingTracks()
    world = make_world(notes={"kid": notes})
    assert world.people["kid"].speed_scale == factor
    end = run(world, tracks, 0.0, do="get_in", who="kid", what="car", socket="passenger")
    seat = world.socket_world("car", "passenger")
    walk = (0.8 - seat[0]) / motion.SPEEDS["walk"] / factor
    assert end == pytest.approx(walk + 0.4)  # the sit onto the seat is still 0.4
    at_door = motion.position_at(motion.path_from_keys(tracks.raw, "puppet:kid", "root.position"), walk)
    assert at_door == pytest.approx((seat[0], 0.0, 0.5))
    root = tracks.value_at("puppet:kid", "root.position", end)
    assert [root[0], root[1] + HIP["kid"], root[2]] == pytest.approx(list(seat), abs=1e-6)
    assert world.people["kid"].seat == ("car", "passenger")


def test_get_in_at_the_door_get_out_and_drive_do_not_depend_on_notes():
    results = []
    for notes in (None, CANE, QUICK):
        tracks = TrackSet()
        world = make_world(notes={"dad": notes, "mom": notes})
        ends = [run(world, tracks, 0.0, do="get_in", who="dad", what="car"),
                run(world, tracks, 0.5, do="get_in", who="mom", what="car")]
        ends.append(run(world, tracks, 1.0, do="drive_to", who="dad", what="car", to="church"))
        ends.append(run(world, tracks, ends[-1], do="get_out", who="mom", what="car"))
        results.append((ends, [tr.model_dump() for tr in tracks.tracks()]))
    assert results[0][0][:2] == [pytest.approx(0.4), pytest.approx(0.9)]  # no walk: already at the doors
    assert results[1] == results[0]
    assert results[2] == results[0]


# ------------------------------------------- held arms keep the hold pose; floor puppets get up
# get_in and get_out leave the arms that hold props in the hold pose (the walk cycle and the
# sit / stand blends skip them, as verbs_core's walk and stand_up do), and get_in gets a puppet
# kneeling or sitting on the floor onto its feet before it walks to the door.

LAMP = schemas.Prop(id="lamp", name="Lamp", size="hand", art={"body": None}, grip=[0.5, 0.3])
HOLD_UPPER, HOLD_LOWER = hold_pose("arm_r")
ARM_R = ("arm_r.upper", "arm_r.lower")
LEGS = ("leg_l.upper", "leg_l.lower", "leg_r.upper", "leg_r.lower")
ANY_HANDLER = {**verbs_core.HANDLERS, **verbs_body.HANDLERS, **HANDLERS}


def lamp_world(start: dict, prop_start: dict | None = None) -> World:
    """make_world with a hand-sized lamp in the catalogue (start may say who holds it)."""
    world = World(PEOPLE, [*PROPS, LAMP])
    shot = Shot.model_validate({
        "id": "s1", "set": "street", "start": start,
        "prop_start": prop_start if prop_start is not None else {"car": {"at": "lot", "state": "parked"},
                                                                 "chair": {"at": "chair_spot"}},
    })
    world.begin_shot(shot, MARKS, same_set_as_previous=False)
    return world


def run_any(world: World, tracks: TrackSet, t: float, **fields) -> float:
    """run() for any core, body or vehicle verb."""
    action = Action.model_validate({"t": t, **fields})
    world.now = t
    world.check_free(action.who)
    end = ANY_HANDLER[action.do](world, tracks, action, t, 0.0, make_locate(world))
    world.occupy(action.who, end, action.do, at=world.now)
    return end


def raw_keys(tracks: RecordingTracks, who: str, channel: str, after: float = -1.0) -> list[tuple[float, object]]:
    return sorted((k[2], k[3]) for k in tracks.raw
                  if k[0] == f"puppet:{who}" and k[1] == channel and k[2] >= after - 1e-9)


def assert_holding_pose(tracks: TrackSet, who: str, times) -> None:
    for t in times:
        assert tracks.value_at(f"puppet:{who}", "arm_r.upper.rot", t) == pytest.approx(HOLD_UPPER, abs=1e-9), t
        assert tracks.value_at(f"puppet:{who}", "arm_r.lower.rot", t) == pytest.approx(HOLD_LOWER, abs=1e-9), t


def test_get_in_walking_to_the_door_keeps_a_held_arm_in_the_hold_pose():
    tracks = RecordingTracks()
    world = lamp_world({"dad": {"at": "church", "holding": "lamp"}})
    run_any(world, tracks, 0.0, do="hold", who="dad", what="lamp")  # arm_r in the hold pose by 0.4
    end = run(world, tracks, 1.0, do="get_in", who="dad", what="car")
    walk = (0.5 - (-0.67)) / motion.SPEEDS["walk"]
    assert end == pytest.approx(1.0 + walk + POSE_SECONDS)
    # get_in keys no held arm bone at all: the walk cycle and the sit skip arm_r.
    for bone in ARM_R:
        assert raw_keys(tracks, "dad", f"{bone}.rot", after=1.0) == []
    # The free arm still swings.
    assert len(raw_keys(tracks, "dad", "arm_l.upper.rot", after=1.0)) > 4
    # Sampled at every walk key (the cycle's quarter times) and at the end.
    quarters = [kt for kt, _ in raw_keys(tracks, "dad", "leg_l.upper.rot", after=1.0)]
    assert len(quarters) > 8
    assert_holding_pose(tracks, "dad", [1.0, *quarters, end, end + 1.0])
    # The lamp never leaves dad's right hand.
    assert world.holder("lamp") == ("dad", "hand_r")
    assert not [k for k in tracks.raw if k[0] == "prop:lamp"]
    assert world.people["dad"].seat == ("car", "driver")


def test_get_in_with_both_hands_full_keeps_both_held_arms():
    tracks = RecordingTracks()
    world = lamp_world({"dad": {"at": "church", "holding": "lamp"}},
                       prop_start={"car": {"at": "lot"}, "suitcase": {"at": "church"}})
    world.pick_up("dad", "suitcase")  # hand_l
    end = run(world, tracks, 1.0, do="get_in", who="dad", what="car")
    for bone in (*ARM_R, "arm_l.upper", "arm_l.lower"):
        assert raw_keys(tracks, "dad", f"{bone}.rot") == []
    assert raw_keys(tracks, "dad", "leg_l.upper.rot")[-1] == (pytest.approx(end), 1.5)


def test_get_in_compiled_from_the_door_keeps_the_hold_pose_and_the_lamp_in_hand():
    sc = base_scene()
    sp = _one(_a(1, "get_in", who="dad", what="sedan"),
              start={"dad": _at("door", holding="lamp")}, prop_start=SEDAN_CURB)
    res = compile_screenplay(sp, sets=sc.sets, props=sc.props, people=sc.people)
    assert res.ok, res.errors
    tl = res.timeline
    a0 = tl.shots[0].action_start
    tracks = TrackSet()
    tracks.extend((tr.target, tr.channel, kt, v) for tr in tl.tracks for kt, v in tr.keys)
    legs = [kt for tr in tl.tracks if (tr.target, tr.channel) == ("puppet:dad", "leg_l.upper.rot")
            for kt, _ in tr.keys if kt >= a0 + 1.0]
    assert len(legs) > 8  # a real walk from the door to the curb
    assert_holding_pose(tracks, "dad", [a0 + 1.0, *legs, tl.duration])
    lamp_parent = [tr.keys for tr in tl.tracks if (tr.target, tr.channel) == ("prop:lamp", "parent")]
    assert lamp_parent == [[(0.0, "puppet:dad.hand_r")]]


def test_get_out_keeps_a_held_arm_in_the_hold_pose_like_stand_up_from_a_chair():
    out_tracks = RecordingTracks()
    world = lamp_world({"dad": {"at": "door_d", "holding": "lamp"}})
    run_any(world, out_tracks, 0.0, do="hold", who="dad", what="lamp")
    run(world, out_tracks, 0.4, do="get_in", who="dad", what="car")  # seated in car.driver by 0.8
    end = run(world, out_tracks, 1.0, do="get_out", who="dad", what="car")
    assert end == pytest.approx(1.0 + POSE_SECONDS)
    for bone in ARM_R:
        assert raw_keys(out_tracks, "dad", f"{bone}.rot", after=0.41) == []  # 0.4 ends the hold
    assert_holding_pose(out_tracks, "dad", [1.0, 1.0 + POSE_SECONDS / 2, end, end + 1.0])
    for bone in LEGS:  # the legs still stand up
        assert out_tracks.value_at("puppet:dad", f"{bone}.rot", end) == pytest.approx(0.0)
    assert world.holder("lamp") == ("dad", "hand_r")

    # stand_up from a chair already keeps the hold pose; get_out now matches it bone for bone.
    chair_tracks = RecordingTracks()
    chair = lamp_world({"dad": {"at": "chair_spot", "holding": "lamp"}})
    run_any(chair, chair_tracks, 0.0, do="hold", who="dad", what="lamp")
    run_any(chair, chair_tracks, 0.4, do="sit_on", who="dad", what="chair")
    chair_end = run_any(chair, chair_tracks, 1.0, do="stand_up", who="dad")
    assert chair_end == pytest.approx(end)
    assert_holding_pose(chair_tracks, "dad", [1.0, chair_end])
    def rot_channels(tr):
        return {k[1] for k in tr.raw if k[0] == "puppet:dad" and k[2] >= 1.0 and k[1].endswith(".rot")}
    assert rot_channels(out_tracks) == rot_channels(chair_tracks) == {f"{b}.rot" for b in LEGS}


def test_get_out_compiled_from_the_drivers_seat_keeps_the_hold_pose():
    sc = base_scene()
    sp = _one(_a(1, "get_out", who="dad", what="sedan"),
              start={"dad": _at("curb", holding="lamp", **{"in": "sedan.driver"})}, prop_start=SEDAN_CURB)
    res = compile_screenplay(sp, sets=sc.sets, props=sc.props, people=sc.people)
    assert res.ok, res.errors
    tl = res.timeline
    t = tl.shots[0].action_start + 1.0
    tracks = TrackSet()
    tracks.extend((tr.target, tr.channel, kt, v) for tr in tl.tracks for kt, v in tr.keys)
    assert_holding_pose(tracks, "dad", [t, t + POSE_SECONDS / 2, t + POSE_SECONDS, tl.duration])
    for bone in LEGS:
        assert tracks.value_at("puppet:dad", f"{bone}.rot", t + POSE_SECONDS) == pytest.approx(0.0)


def test_get_in_from_kneeling_stands_up_first_then_walks():
    tracks = RecordingTracks()
    world = make_world()
    run_any(world, tracks, 0.0, do="kneel", who="kid")
    assert world.people["kid"].pose == "kneel"
    end = run(world, tracks, 1.0, do="get_in", who="kid", what="car", socket="passenger")
    t_up = 1.0 + POSE_SECONDS
    walk = (0.8 - (-0.22)) / motion.SPEEDS["walk"]
    assert end == pytest.approx(t_up + walk + POSE_SECONDS)  # later by the stand-up time
    # The legs blend back to 0 from the kneel, by t_up, before the walk cycle starts.
    for bone in LEGS:
        keys = raw_keys(tracks, "kid", f"{bone}.rot", after=1.0)
        assert keys[0][0] == pytest.approx(1.0)
        at_up = [v for kt, v in keys if kt == pytest.approx(t_up)]
        assert at_up and at_up[0] == pytest.approx(0.0)
        assert not [kt for kt, _ in keys if 1.0 + 1e-9 < kt < t_up - 1e-9]
    # The walk starts at t_up from the standing root, not at t from the kneeling one.
    roots = raw_keys(tracks, "kid", "root.position", after=1.0)
    assert roots[0][0] == pytest.approx(1.0) and roots[0][1][1] < 0  # still down on the knee
    assert [list(v) for kt, v in roots if kt == pytest.approx(t_up)] == [[0.8, 0.0, 0.5]] * 2
    for kt, v in roots:
        if kt >= t_up - 1e-9:
            assert v[1] >= -1e-9, (kt, v)
    facing = [k for k in tracks.raw if k[1] == "root.facing" and k[2] >= 1.0]
    assert [k[2] for k in facing] == [pytest.approx(t_up)]
    path = motion.path_from_keys(tracks.raw, "puppet:kid", "root.position")
    assert motion.position_at(path, t_up + walk) == pytest.approx((-0.22, 0.0, 0.5))
    k = world.people["kid"]
    assert (k.seat, k.pose) == (("car", "passenger"), "sit")


def test_get_in_from_kneeling_at_the_door_stands_then_sits():
    tracks = RecordingTracks()
    world = make_world()
    run_any(world, tracks, 0.0, do="kneel", who="dad")
    end = run(world, tracks, 1.0, do="get_in", who="dad", what="car")
    t_up = 1.0 + POSE_SECONDS
    assert end == pytest.approx(t_up + POSE_SECONDS)
    roots = raw_keys(tracks, "dad", "root.position", after=1.0)
    assert [kt for kt, _ in roots] == pytest.approx([1.0, t_up, t_up, end])
    assert list(roots[1][1]) == pytest.approx([-0.67, 0.0, 0.5])  # standing at the door
    assert list(roots[2][1]) == pytest.approx([-0.67, 0.0, 0.5])  # the sit starts from there
    assert roots[-1][1][1] + HIP["dad"] == pytest.approx(0.405)
    for kt, v in roots[1:]:
        assert v[1] >= -1e-9
    for bone in LEGS:
        assert tracks.value_at("puppet:dad", f"{bone}.rot", t_up) == pytest.approx(0.0, abs=1e-3)


def test_get_in_compiled_after_a_kneel_stands_first():
    sc = base_scene()
    sp = _one(_a(0, "kneel", who="dad"), _a(1, "get_in", who="dad", what="sedan"),
              start={"dad": _at("door")}, prop_start=SEDAN_CURB)
    # unplanted: no root key under the ground once up (planting lowers the walk to the door's
    # cycle keys; test_foot_planting.py checks those feet are on the ground)
    res = compile_screenplay(sp, sets=sc.sets, props=sc.props, people=sc.people, plant_feet=False)
    assert res.ok, res.errors
    tl = res.timeline
    t_up = tl.shots[0].action_start + 1.0 + POSE_SECONDS
    keys = {tr.channel: tr.keys for tr in tl.tracks if tr.target == "puppet:dad"}
    for kt, v in keys["root.position"]:
        if kt >= t_up - 1e-6:
            assert v[1] >= -1e-9, (kt, v)
    for bone in LEGS:
        at_up = [v for kt, v in keys[f"{bone}.rot"] if abs(kt - t_up) < 1e-6]
        assert at_up == [0.0]
    walk_start = min(kt for kt, _ in keys["arm_r.upper.rot"])
    assert walk_start == pytest.approx(t_up, abs=1e-6)


def test_get_in_from_kneeling_while_holding_keeps_the_hold_pose_through_the_stand_and_walk():
    tracks = RecordingTracks()
    world = lamp_world({"kid": {"at": "porch", "holding": "lamp"}})
    run_any(world, tracks, 0.0, do="hold", who="kid", what="lamp")
    run_any(world, tracks, 0.4, do="kneel", who="kid")
    end = run(world, tracks, 1.0, do="get_in", who="kid", what="car", socket="passenger")
    for bone in ARM_R:
        assert raw_keys(tracks, "kid", f"{bone}.rot", after=1.0) == []
    quarters = [kt for kt, _ in raw_keys(tracks, "kid", "leg_l.upper.rot", after=1.0)]
    assert_holding_pose(tracks, "kid", [1.0, *quarters, end])


def test_get_in_from_sitting_on_the_floor_stands_up_first():
    tracks = RecordingTracks()
    world = make_world()
    world.set_pose("kid", "sit")  # on the floor: no seat
    assert world.people["kid"].seat is None
    end = run(world, tracks, 1.0, do="get_in", who="kid", what="car", socket="passenger")
    walk = (0.8 - (-0.22)) / motion.SPEEDS["walk"]
    assert end == pytest.approx(1.0 + POSE_SECONDS + walk + POSE_SECONDS)
    assert world.people["kid"].seat == ("car", "passenger")


def test_get_in_from_kneeling_into_a_full_vehicle_is_atomic():
    tracks = TrackSet()
    world = seated_world(tracks, "dad", "mom")
    run_any(world, tracks, 1.0, do="kneel", who="kid")
    assert_atomic(world, tracks, "no_free_seat", "every seat in car is taken", do="get_in", who="kid", what="car")


# ---------------------------------------------- standing, empty-handed: keys exactly as before
# Digests of every raw key (rounded to 1e-9) captured on main before the held-arm / stand-up
# change: the change must not touch the common case.

def _digest(raw: list[tuple]) -> tuple[int, str]:
    def r(v):
        if isinstance(v, (list, tuple)):
            return [round(float(c), 9) for c in v]
        return round(v, 9) if isinstance(v, float) else v
    canon = sorted((tg, ch, round(float(kt), 9), r(v)) for tg, ch, kt, v in raw)
    return len(canon), hashlib.sha256(json.dumps(canon).encode()).hexdigest()


def test_standing_empty_handed_get_in_and_get_out_keys_are_unchanged():
    tracks = RecordingTracks()
    world = make_world()
    run(world, tracks, 0.0, do="get_in", who="kid", what="car", socket="passenger")  # walks to the door
    assert _digest(tracks.raw) == (125, "8c9962b5e34611b79bc20e44f14135becbe03d963ceed983f531eb4c2bc9c442")
    # the walk to the door marks its cycle keys for foot planting
    leg_times = {round(t, 3) for tg, ch, t, _ in tracks.raw if (tg, ch) == ("puppet:kid", "leg_l.upper.rot")}
    marks = tracks.plant_times("puppet:kid")
    assert marks and set(marks) < leg_times

    tracks = RecordingTracks()
    world = make_world()
    run(world, tracks, 0.0, do="get_in", who="dad", what="car")  # already at the door
    n = len(tracks.raw)
    assert _digest(tracks.raw) == (10, "510382d0c32a1e12cd37ab08361d3699db7e40f38be345714a59002039ca1ad1")
    run(world, tracks, 1.0, do="get_out", who="dad", what="car")
    assert _digest(tracks.raw[n:]) == (10, "2afb0cdf1ec939eb6c87874355f60fd2ec1e1fc22d703b1c232575a63df07e67")


# ------------------------------------------------------- riders on props stacked on the vehicle

PICKUP = schemas.Prop(
    id="pickup", name="Pickup", size="vehicle", art={"body": None, "wheel": None}, aspect=2.0,
    sockets={"driver": [0.35, 0.55], "passenger": [0.5, 0.55], "bed": [0.8, 0.5]},
    parts={"wheel_front": {"art": "wheel", "at": [0.22, 0.9], "spin": True}},
)
CRATE = schemas.Prop(id="crate", name="Crate", size="furniture", art={"body": None}, sockets={"top": [0.5, 0.0]})


def pickup_world(start: dict | None = None, chair_on: str = "pickup.bed") -> World:
    """The pickup on the lot with the chair on its bed: dad drives, mom is the passenger, kid
    sits on the chair. ``chair_on="crate.top"`` puts the crate on the bed and the chair on it;
    ``"chair_spot"`` stands the chair on the ground instead."""
    prop_start: dict = {"pickup": {"at": "lot"}}
    if chair_on == "crate.top":
        prop_start["crate"] = {"on": "pickup.bed"}
    prop_start["chair"] = {"at": "chair_spot"} if chair_on == "chair_spot" else {"on": chair_on}
    world = World(PEOPLE, [PICKUP, CHAIR, CRATE])
    world.begin_shot(Shot.model_validate({
        "id": "s1", "set": "street",
        "start": start if start is not None else {"dad": {"at": "lot", "in": "pickup.driver"},
                                                  "mom": {"at": "lot", "in": "pickup.passenger"},
                                                  "kid": {"at": "lot", "in": "chair.seat"}},
        "prop_start": prop_start,
    }), MARKS, same_set_as_previous=False)
    return world


def test_seated_lists_only_the_vehicles_own_sockets():
    world = pickup_world()
    assert verbs_vehicles._seated(world, "pickup") == [("driver", "dad"), ("passenger", "mom")]
    assert verbs_vehicles._stacked(world, "pickup") == [("chair", "seat", "kid")]


@pytest.mark.parametrize("chair_on", ["pickup.bed", "crate.top"], ids=["on_the_bed", "on_a_crate_on_the_bed"])
def test_drive_to_carries_a_rider_on_a_stacked_prop_by_the_vehicles_travel(chair_on):
    tracks = RecordingTracks()
    world = pickup_world(chair_on=chair_on)
    kid_before = world.people["kid"].pos
    arrival = run(world, tracks, 2.0, do="drive_to", who="dad", what="pickup", to="church")
    path = motion.path_from_keys(tracks.raw, "prop:pickup")
    start = path[0][1]
    kid = motion.path_from_keys(tracks.raw, "puppet:kid", "root.position")
    # one key per path key, at the same times, translated by the pickup's travel so far
    assert [t for t, _ in kid] == [t for t, _ in path] == [2.0, arrival]
    for (_, k), (_, a) in zip(kid, path, strict=True):
        assert list(k) == pytest.approx([kid_before[i] + a[i] - start[i] for i in range(3)], abs=1e-12)
    # the same place World.move_vehicle put kid
    assert list(kid[-1][1]) == pytest.approx(list(world.people["kid"].pos), abs=1e-12)
    assert world.people["kid"].pos[0] == pytest.approx(kid_before[0] + 0.9)
    # the driver and the passenger still ride on their sockets (motion.seated_root_keys)
    for who, sock in (("dad", "driver"), ("mom", "passenger")):
        root = motion.path_from_keys(tracks.raw, f"puppet:{who}", "root.position")
        assert root == [(t, (a[0] + o[0], a[1] + o[1] - HIP[who], a[2] + o[2]))
                        for (t, a), o in zip(path, [motion.prop_socket_offset(PICKUP, sock)] * len(path))]


def test_drive_to_uses_the_stacked_riders_track_where_it_has_one():
    tracks = RecordingTracks()
    world = pickup_world()
    tracks.add("puppet:kid", "root.position", 0.0, [0.25, 0.5, 0.5])
    run(world, tracks, 1.0, do="drive_to", who="dad", what="pickup", to="church")
    kid = [k for k in tracks.raw if k[0] == "puppet:kid"][1:]
    assert [k[3] for k in kid] == [pytest.approx([0.25, 0.5, 0.5]), pytest.approx([1.15, 0.5, 0.5])]


def test_drive_to_while_a_stacked_rider_is_still_sitting_down():
    tracks = TrackSet()
    world = pickup_world(start={"dad": {"at": "lot", "in": "pickup.driver"}, "kid": {"at": "porch"}})
    action = Action.model_validate({"t": 0.0, "do": "sit_on", "who": "kid", "what": "chair"})
    world.now = 0.0
    end = verbs_body.HANDLERS["sit_on"](world, tracks, action, 0.0, 0.0, make_locate(world))  # walks over first
    assert end > 1.0
    assert_atomic(world, tracks, "busy",
                  f"kid is still settling into chair.seat, which rests on pickup, until t={round(end, 2):g}",
                  do="drive_to", who="dad", what="pickup", to="church", t=1.0)


def test_ride_to_from_a_stacked_seat_drives_and_occupies_everyone_else():
    tracks = TrackSet()
    world = pickup_world()
    arrival = run(world, tracks, 1.0, do="ride_to", who="kid", what="pickup", to="church")
    assert (world.people["dad"].busy_until, world.people["dad"].activity) == (pytest.approx(arrival), "driving")
    assert (world.people["mom"].busy_until, world.people["mom"].activity) == (pytest.approx(arrival), "riding")
    assert world.people["kid"].activity == "riding"  # run() occupies the one who asked


def test_ride_to_by_the_driver_occupies_the_stacked_rider():
    tracks = TrackSet()
    world = pickup_world()
    arrival = run(world, tracks, 1.0, do="ride_to", who="dad", what="pickup", to="church")
    assert (world.people["kid"].busy_until, world.people["kid"].activity) == (pytest.approx(arrival), "riding")


def test_ride_to_with_a_busy_stacked_rider():
    tracks = TrackSet()
    world = pickup_world()
    world.occupy("kid", 5.0, "waving", at=0.0)
    assert_atomic(world, tracks, "busy", "kid is still waving until t=5",
                  do="ride_to", who="mom", what="pickup", to="church", t=1.0)


def test_ride_to_from_a_chair_on_the_ground_is_not_in_the_vehicle():
    tracks = TrackSet()
    world = pickup_world(chair_on="chair_spot")
    assert_atomic(world, tracks, "not_in_vehicle", "kid is not in pickup; use get_in first",
                  do="ride_to", who="kid", what="pickup", to="church", t=1.0)
