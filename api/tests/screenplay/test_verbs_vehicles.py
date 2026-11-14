"""Vehicle verbs: get_in, get_out, drive_to, ride_to (screenplay/verbs_vehicles.py)."""

from __future__ import annotations

import inspect
import math

import pytest

import schemas
from screenplay import motion, verbs_vehicles
from screenplay.ik import RIG, scale
from screenplay.schema import Action, Shot
from screenplay.state import StateError, World
from screenplay.tracks import TrackSet
from screenplay.verbs_vehicles import HANDLERS, WHEEL_RADIUS_FRACTION

# ------------------------------------------------------------------ fixtures


def person(pid: str, build: str = "average") -> schemas.Person:
    return schemas.Person(id=pid, family_id="f", name=pid.title(), character={"color": "#123456", "build": build})


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
    """A TrackSet that also keeps every raw (unrounded) key, for exact checks."""

    def __init__(self) -> None:
        super().__init__()
        self.raw: list[tuple] = []

    def add(self, target, channel, t, value):  # noqa: ANN001
        super().add(target, channel, t, value)
        self.raw.append((target, channel, t, value))


def make_world(start: dict | None = None, prop_start: dict | None = None) -> World:
    world = World(PEOPLE, PROPS)
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
    assert_atomic(world, tracks, "already_seated", "dad is seated in car.driver", do="get_in", who="dad", what="car")


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
    assert_atomic(world, tracks, "unknown_socket", "car has no socket roof",
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
    assert_atomic(world, tracks, "not_in_vehicle", "kid is not in car", do="get_out", who="kid", what="car")


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
    assert_atomic(world, tracks, "not_driver", "mom is in car.passenger, not the driver's seat",
                  do="drive_to", who="mom", what="car", to="church")


def test_drive_to_by_someone_outside():
    tracks = TrackSet()
    world = seated_world(tracks, "dad")
    assert_atomic(world, tracks, "not_driver", "kid is not in the driver's seat of car",
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
    assert_atomic(world, tracks, "no_driver", "nobody is driving car", do="ride_to", who="mom", what="car", to="church")


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
    assert_atomic(world, tracks, "not_in_vehicle", "kid is not in car", do="ride_to", who="kid", what="car", to="church")


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
