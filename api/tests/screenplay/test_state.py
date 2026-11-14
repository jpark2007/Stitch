"""World state simulation: holders, sockets, seats, prop states, positions, preconditions."""

import json
from pathlib import Path

import pytest

import schemas
from screenplay.schema import Screenplay, Shot
from screenplay.state import (
    OFFSTAGE_LEFT,
    OFFSTAGE_RIGHT,
    PropState,
    PuppetState,
    StateError,
    World,
    fmt_t,
)

SAMPLES = Path(__file__).resolve().parents[3] / "web" / "public" / "samples"


# ------------------------------------------------------------------ fixtures

def person(pid: str, build: str = "average") -> schemas.Person:
    return schemas.Person(id=pid, family_id="f", name=pid.title(), character={"color": "#123456", "build": build})


def prop(pid: str, size: str = "hand", sockets=None, states=None, aspect: float = 1.0) -> schemas.Prop:
    return schemas.Prop(
        id=pid, name=pid, size=size, art={"body": None}, aspect=aspect,
        sockets=sockets or {}, states=states or {},
    )


PEOPLE = [person("dad", "tall"), person("mom"), person("kid", "small")]
PROPS = [
    prop("hymn_books"),
    prop("hat"),
    prop("scarf"),
    prop("cup"),
    prop("sedan", "vehicle", {"driver": [0.35, 0.55], "passenger": [0.6, 0.55], "back_seat": [0.8, 0.55]}, aspect=2.0),
    prop("chair", "furniture", {"seat": [0.5, 0.5]}),
    prop("bench", "furniture", {"back": [0.5, 0.1], "left_seat": [0.3, 0.6], "right_seat": [0.7, 0.6]}, aspect=2.0),
    prop("crate", "furniture", {"top": [0.5, 0.0]}),
    prop("table", "furniture", {"top": [0.5, 0.0]}, aspect=1.5),
    prop("lamp", "carried", states={"lit": {}, "out": {}}),
    prop("wagon", "vehicle", {"bed": [0.6, 0.4], "seat": [0.2, 0.5]}, aspect=2.0),
]

# Loose props the fixtures stand on stage, so puppets can pick them up and wear them.
LOOSE = {"cup": {"at": "home"}, "hat": {"at": "home"}, "scarf": {"at": "home"}, "lamp": {"at": "steps_left"}}

MARKS = {
    "steps_left": (-0.6, 0.0, 0.5),
    "steps_foot": (0.0, 0.0, 0.5),
    "steps_top": (0.2, 0.3, 0.5),
    "curb": (0.8, 0.0, 0.5),
    "home": (-0.5, 0.0, 0.5),
}


def shot(**kw) -> Shot:
    return Shot.model_validate({"id": kw.pop("id", "s1"), "set": kw.pop("set", "church"), **kw})


@pytest.fixture
def world() -> World:
    return World(PEOPLE, PROPS)


def opening(world: World) -> World:
    """Mom at the top of the steps holding the books; dad offstage left."""
    world.begin_shot(
        shot(start={"dad": {"at": "offstage_left", "facing": "right"},
                    "mom": {"at": "steps_top", "holding": "hymn_books", "facing": "left"}},
             propStart=LOOSE),
        MARKS, same_set_as_previous=False,
    )
    return world


def raises(code: str, reason: str | None, fn, *args, **kw) -> StateError:
    """Assert fn raises StateError(code, reason) and leaves the world untouched."""
    w: World = fn.__self__
    before = w.copy()
    with pytest.raises(StateError) as ei:
        fn(*args, **kw)
    assert ei.value.code == code, (ei.value.code, ei.value.reason)
    if reason is not None:
        assert ei.value.reason == reason
    assert str(ei.value) == ei.value.reason
    assert w == before, "world changed on error"
    return ei.value


# ------------------------------------------------------------- construction

def test_initial_state(world: World):
    dad = world.people["dad"]
    assert dad == PuppetState(id="dad", build="tall", pos=(-1.3, 0.0, 0.5), facing=1)
    assert OFFSTAGE_LEFT == pytest.approx((-1.3, 0.0, 0.5))
    assert OFFSTAGE_RIGHT == pytest.approx((1.3, 0.0, 0.5))
    assert world.people["kid"].build == "small"
    assert world.props["cup"] == PropState(id="cup")
    assert world.holder("cup") is None
    assert not world.on_stage("dad")
    assert world.free_hand("dad") == "hand_r"
    assert world.free_hand("dad", prefer="hand_l") == "hand_l"


def test_unknown_ids_are_state_errors_not_key_errors(world: World):
    raises("unknown_id", "ghost is not in the cast", world.move, "ghost", (0, 0, 0.5))
    raises("unknown_id", "anvil is not a known prop", world.pick_up, "dad", "anvil")
    raises("unknown_id", None, world.hand_to, "dad", "cup", "ghost")
    raises("unknown_id", None, world.set_prop_state, "anvil", "lit")
    with pytest.raises(StateError) as ei:
        world.holder("anvil")
    assert ei.value.code == "unknown_id"


def test_fmt_t():
    assert fmt_t(4.7400001) == "4.74"
    assert fmt_t(5.0) == "5"
    assert fmt_t(0.5) == "0.5"
    assert fmt_t(3.256) == "3.26"


# ------------------------------------------------------------ the sample scene

def test_sample_sequence(world: World):
    opening(world)
    mom, dad = world.people["mom"], world.people["dad"]
    assert mom.pos == MARKS["steps_top"] and mom.facing == -1
    assert dad.pos == pytest.approx(OFFSTAGE_LEFT) and dad.facing == 1
    assert world.holder("hymn_books") == ("mom", "hand_r")
    assert mom.holding == {"hand_r": "hymn_books"}
    assert world.props["hymn_books"].parent == "puppet:mom.hand_r"
    assert world.props["hymn_books"].pos == (0.0, 0.0, 0.0)

    world.move("dad", MARKS["steps_left"], 1)
    world.drop("mom", "hymn_books", (0.12, 0.3, 0.5))
    books = world.props["hymn_books"]
    assert books.parent == "ground" and books.pos == (0.12, 0.3, 0.5) and books.world_pos == (0.12, 0.3, 0.5)
    assert world.holder("hymn_books") is None and mom.holding == {}

    world.move("dad", MARKS["steps_foot"])
    assert world.pick_up("dad", "hymn_books") == "hand_r"
    assert world.holder("hymn_books") == ("dad", "hand_r")
    assert world.hand_to("dad", "hymn_books", "mom") == "hand_r"
    assert world.holder("hymn_books") == ("mom", "hand_r")
    assert world.props["hymn_books"].parent == "puppet:mom.hand_r"
    assert dad.holding == {} and mom.holding == {"hand_r": "hymn_books"}


def test_sample_files_drive_the_world():
    """The real reference screenplay and memory build the same opening state."""
    sp = Screenplay.model_validate(json.loads((SAMPLES / "hymn_books.screenplay.json").read_text()))
    mem = json.loads((SAMPLES / "hymn_books.memory.json").read_text())
    w = World([schemas.Person.model_validate(p) for p in mem["people"]],
              [schemas.Prop.model_validate(p) for p in mem["props"]])
    s1 = sp.shots[0]
    set_marks = next(s for s in mem["sets"] if s["id"] == s1.set)["marks"]
    marks = {k: (2 * m[0] - 1, m[1], m[2] if len(m) > 2 else 0.5) for k, m in {**set_marks, **s1.marks}.items()}
    w.begin_shot(s1, marks, same_set_as_previous=False)
    assert w.people["dad"].build == "tall" and w.people["mom"].build == "average"
    assert w.holder("hymn_books") == ("mom", "hand_r")
    assert w.people["mom"].pos == pytest.approx((0.2, 0.3, 0.5))
    assert w.people["mom"].facing == -1
    w.move("dad", marks["steps_left"])
    w.drop("mom", "hymn_books", (0.12, 0.3, 0.5))
    w.move("dad", marks["steps_foot"])
    assert w.pick_up("dad", "hymn_books") == "hand_r"
    assert w.hand_to("dad", "hymn_books", "mom") == "hand_r"
    assert w.holder("hymn_books") == ("mom", "hand_r")


# ------------------------------------------------------------ holding errors

def test_hand_to_while_someone_else_holds(world: World):
    opening(world)
    world.move("dad", MARKS["steps_foot"])
    err = raises("not_holding", "mom is holding it", world.hand_to, "dad", "hymn_books", "mom")
    assert err.code == "not_holding"


def test_hand_to_after_drop_is_on_the_ground(world: World):
    opening(world)
    world.move("dad", MARKS["steps_foot"])
    world.drop("mom", "hymn_books", (0.12, 0.3, 0.5))
    raises("not_holding", "it is on the ground", world.hand_to, "dad", "hymn_books", "mom")
    raises("not_holding", "it is on the ground", world.drop, "mom", "hymn_books", (0, 0, 0.5))
    raises("not_holding", "it is on the ground", world.hold, "dad", "hymn_books")


def test_not_holding_reasons(world: World):
    opening(world)
    world.move("dad", MARKS["steps_foot"])
    raises("not_holding", "it is on the ground", world.hand_to, "dad", "cup", "mom")
    raises("not_holding", "it is not on stage", world.hand_to, "dad", "crate", "mom")
    world.put_on("dad", "hat")
    raises("not_holding", "dad is wearing it", world.hand_to, "dad", "hat", "mom")
    raises("not_holding", "dad is wearing it", world.drop, "mom", "hat", (0, 0, 0.5))
    world.begin_shot(shot(id="s2", start={"dad": {"at": "steps_foot"}, "mom": {"at": "steps_top"}},
                          propStart={"crate": {"at": "curb"}, "cup": {"on": "crate.top"}}),
                     MARKS, same_set_as_previous=True)
    raises("not_holding", "it is on crate.top", world.hand_to, "dad", "cup", "mom")


def test_hand_to_other_errors(world: World):
    opening(world)
    raises("self_hand_to", '"to" is mom themselves', world.hand_to, "mom", "hymn_books", "mom")
    raises("offstage", "dad is offstage", world.hand_to, "mom", "hymn_books", "dad")
    world.move("dad", MARKS["steps_foot"])
    world.pick_up("dad", "cup")
    world.pick_up("dad", "lamp")
    raises("hands_full", "dad's hands are full (hand_r: cup, hand_l: lamp)",
           world.hand_to, "mom", "hymn_books", "dad")
    world.move("mom", OFFSTAGE_RIGHT)
    raises("offstage", "mom is offstage", world.hand_to, "mom", "hymn_books", "dad")


def test_hand_to_uses_the_takers_free_hand(world: World):
    opening(world)
    world.move("dad", MARKS["steps_foot"])
    world.pick_up("dad", "cup")
    assert world.hand_to("mom", "hymn_books", "dad") == "hand_l"
    assert world.people["dad"].holding == {"hand_r": "cup", "hand_l": "hymn_books"}
    assert world.people["mom"].holding == {}


def test_hands_full_and_already_held(world: World):
    opening(world)
    world.move("dad", MARKS["steps_foot"])
    assert world.pick_up("dad", "cup") == "hand_r"
    assert world.pick_up("dad", "lamp") == "hand_l"
    raises("hands_full", "dad's hands are full (hand_r: cup, hand_l: lamp)", world.pick_up, "dad", "scarf")
    raises("already_held", "dad is already holding it", world.pick_up, "dad", "cup")
    raises("already_held", "mom is holding it", world.pick_up, "dad", "hymn_books")
    raises("already_held", "dad is holding it", world.pick_up, "mom", "cup")
    world.put_on("mom", "hat")
    raises("already_held", "mom is wearing it", world.pick_up, "kid", "hat")


def test_pick_up_socket_choice(world: World):
    opening(world)
    assert world.pick_up("mom", "cup") == "hand_l"  # hand_r has the books
    world.drop("mom", "cup", (0.1, 0.3, 0.5))
    assert world.pick_up("mom", "cup", "hand_l") == "hand_l"
    raises("hands_full", "mom's hand_r is holding hymn_books", world.pick_up, "mom", "lamp", "hand_r")
    raises("bad_socket", "head is not a hand; use hand_r or hand_l", world.pick_up, "mom", "lamp", "head")


def test_pick_up_from_a_surface_frees_the_socket(world: World):
    world.begin_shot(shot(start={"dad": {"at": "home"}},
                          propStart={"table": {"at": "curb"}, "cup": {"on": "table.top"}}),
                     MARKS, same_set_as_previous=False)
    assert world.props["cup"].parent == "prop:table.top"
    assert world.props["table"].occupants == {"top": "prop:cup"}
    world.pick_up("dad", "cup")
    assert world.props["table"].occupants == {}
    assert world.props["cup"].parent == "puppet:dad.hand_r"


def test_hold(world: World):
    opening(world)
    assert world.hold("mom", "hymn_books") == "hand_r"
    raises("not_holding", "mom is holding it", world.hold, "dad", "hymn_books")


# -------------------------------------------------------------- busy / time

def test_busy_reason(world: World):
    opening(world)
    world.occupy("dad", 4.7400001, "walking")
    assert world.people["dad"].busy_until == pytest.approx(4.74)
    assert world.people["dad"].activity == "walking"
    raises("busy", "dad is still walking until t=4.74", world.occupy, "dad", 6, "picking up", at=3.0)
    raises("busy", "dad is still walking until t=4.74", world.check_free, "dad", 4.0)
    world.check_free("dad", 4.74)
    world.occupy("dad", 6.0, "reaching", at=4.74)
    world.now = 5.0
    raises("busy", "dad is still reaching until t=6", world.occupy, "dad", 7, "waving")
    world.now = 6.0
    world.occupy("dad", 7, "waving")


def test_busy_time_offset_shifts_only_the_shown_time(world: World):
    opening(world)
    world.occupy("dad", 7.3, "picking up")
    world.now = 7.0
    world.busy_time_offset = -1.0  # the compiler: written t 6.0 runs at retimed 7.0
    raises("busy", "dad is still picking up until t=6.3", world.check_free, "dad")
    raises("busy", "dad is still picking up until t=6.3", world.occupy, "dad", 8.0, "handing over")
    assert world.people["dad"].busy_until == 7.3  # the busy time itself is untouched
    assert world.copy().busy_time_offset == -1.0
    world.busy_time_offset = 0.0
    raises("busy", "dad is still picking up until t=7.3", world.check_free, "dad")
    world.check_free("dad", 7.3)


def test_begin_shot_resets_busy(world: World):
    opening(world)
    world.occupy("dad", 9.0, "walking")
    world.now = 3.0
    world.begin_shot(shot(id="s2"), MARKS, same_set_as_previous=True)
    assert world.people["dad"].busy_until == 0.0 and world.people["dad"].activity is None
    assert world.now == 0.0
    world.occupy("dad", 1.0, "walking")


# ------------------------------------------------------------------ vehicles

def parked(world: World) -> World:
    world.begin_shot(
        shot(start={"dad": {"at": "home"}, "mom": {"at": "home"}, "kid": {"at": "home"}},
             propStart={"sedan": {"at": "curb", "state": "parked"}, **LOOSE,
                        "chair": {"at": "steps_foot"}, "bench": {"at": "steps_left"}}),
        MARKS, same_set_as_previous=False,
    )
    return world


def test_get_in_takes_driver_then_next_free(world: World):
    parked(world)
    assert world.get_in("mom", "sedan") == "driver"
    assert world.get_in("dad", "sedan") == "passenger"
    assert world.get_in("kid", "sedan") == "back_seat"
    assert world.people["mom"].seat == ("sedan", "driver") and world.people["mom"].pose == "sit"
    assert world.props["sedan"].occupants == {"driver": "puppet:mom", "passenger": "puppet:dad", "back_seat": "puppet:kid"}
    world.get_out("kid", "sedan")
    assert world.get_in("kid", "sedan") == "back_seat"
    raises("already_seated", "mom is seated in sedan.driver", world.get_in, "mom", "sedan")


def test_get_in_errors(world: World):
    parked(world)
    world.get_in("dad", "sedan", "passenger")
    assert world.get_in("mom", "sedan") == "driver"
    raises("socket_taken", "sedan.passenger is taken by dad", world.get_in, "kid", "sedan", "passenger")
    raises("unknown_socket", "sedan has no socket roof", world.get_in, "kid", "sedan", "roof")
    world.get_in("kid", "sedan")
    world.get_out("kid", "sedan")
    world.get_in("kid", "sedan", "back_seat")
    world.get_out("kid", "sedan")
    world.sit_on("kid", "chair")
    raises("already_seated", "kid is seated in chair.seat", world.get_in, "kid", "sedan")
    world.stand_up("kid")
    w2 = World(PEOPLE + [person("aunt")], PROPS)
    parked(w2)
    for who in ("dad", "mom", "kid"):
        w2.get_in(who, "sedan")
    raises("no_free_seat", "every seat in sedan is taken", w2.get_in, "aunt", "sedan")


def test_get_in_without_driver_socket_uses_dict_order(world: World):
    world.begin_shot(shot(start={"dad": {"at": "home"}, "mom": {"at": "home"}},
                          propStart={"wagon": {"at": "curb"}}), MARKS, same_set_as_previous=False)
    assert world.get_in("dad", "wagon") == "bed"
    assert world.get_in("mom", "wagon") == "seat"


def test_ride_and_drive_checks(world: World):
    parked(world)
    world.get_in("dad", "sedan", "passenger")
    raises("no_driver", "nobody is driving sedan", world.check_rider, "dad", "sedan")
    raises("not_in_vehicle", "kid is not in sedan", world.check_rider, "kid", "sedan")
    raises("not_driver", "dad is in sedan.passenger, not the driver's seat", world.check_driver, "dad", "sedan")
    raises("not_driver", "kid is not in the driver's seat of sedan", world.check_driver, "kid", "sedan")
    world.get_in("mom", "sedan")
    world.check_driver("mom", "sedan")
    world.check_rider("dad", "sedan")
    raises("not_in_vehicle", "kid is not in sedan", world.get_out, "kid", "sedan")


def test_seated_cannot_move_and_vehicle_carries_occupants(world: World):
    parked(world)
    world.get_in("mom", "sedan")
    world.get_in("dad", "sedan")
    raises("seated", "mom is seated in sedan.driver", world.move, "mom", MARKS["curb"])
    raises("seated", "dad is seated in sedan.passenger", world.exit, "dad")
    raises("seated_vehicle", "dad is seated in sedan; use get_out first", world.pick_up, "dad", "cup")
    raises("seated_vehicle", "mom is in sedan; use get_out", world.stand_up, "mom")
    world.put_on("kid", "hat")  # sanity: others unaffected
    world.move_vehicle("sedan", (-0.2, 0.0, 0.5))
    assert world.props["sedan"].pos == (-0.2, 0.0, 0.5)
    assert world.props["sedan"].world_pos == (-0.2, 0.0, 0.5)
    assert world.people["mom"].pos == pytest.approx((-1.5, 0.0, 0.5))
    assert world.people["dad"].pos == pytest.approx((-1.5, 0.0, 0.5))
    assert world.people["kid"].pos == MARKS["home"]
    world.get_out("mom", "sedan")
    assert world.people["mom"].seat is None and world.people["mom"].pose == "stand"
    assert "driver" not in world.props["sedan"].occupants
    world.move("mom", MARKS["curb"])


def test_move_vehicle_requires_ground(world: World):
    opening(world)
    raises("not_on_ground", "mom is holding it", world.move_vehicle, "hymn_books", (0, 0, 0.5))


def test_move_vehicle_moves_stacked_props(world: World):
    world.begin_shot(shot(start={}, propStart={"table": {"at": "curb"}, "cup": {"on": "table.top"}}),
                     MARKS, same_set_as_previous=False)
    before = world.props["cup"].world_pos
    # table: furniture 0.4 tall, aspect 1.5, anchor bottom-centre; top socket at top-centre
    assert before == pytest.approx((0.8, 0.4, 0.5))
    world.move_vehicle("table", (0.5, 0.0, 0.5))
    assert world.props["cup"].world_pos == pytest.approx((0.5, 0.4, 0.5))
    assert world.props["cup"].pos == (0.0, 0.0, 0.0)


# -------------------------------------------------------------------- seating

def test_sit_on_socket_defaults(world: World):
    parked(world)
    assert world.sit_on("dad", "chair") == "seat"
    assert world.people["dad"].pose == "sit" and world.people["dad"].seat == ("chair", "seat")
    assert world.sit_on("mom", "bench") == "left_seat"  # first free *seat* socket, not "back"
    assert world.sit_on("kid", "bench") == "right_seat"
    w = World(PEOPLE, PROPS)
    parked(w)
    assert w.sit_on("dad", "table") == "top"  # no seat-like socket: first free socket


def test_sit_on_errors(world: World):
    parked(world)
    world.sit_on("dad", "chair")
    raises("socket_taken", "chair.seat is taken by dad", world.sit_on, "mom", "chair")
    raises("no_seat", "cup has nowhere to sit", world.sit_on, "mom", "cup")
    raises("unknown_socket", "bench has no socket arm", world.sit_on, "mom", "bench", "arm")
    raises("already_seated", "dad is seated in chair.seat", world.sit_on, "dad", "bench")
    world.sit_on("mom", "bench", "back")
    world.sit_on("kid", "bench")
    w2 = World(PEOPLE + [person("aunt")], PROPS)
    parked(w2)
    for who in ("dad", "mom", "kid"):
        w2.sit_on(who, "bench")
    raises("no_seat", "every seat on bench is taken", w2.sit_on, "aunt", "bench")


def test_stand_up_and_kneel(world: World):
    parked(world)
    raises("not_sitting", "dad is not sitting or kneeling", world.stand_up, "dad")
    world.sit_on("dad", "chair")
    raises("seated", "dad is seated in chair.seat", world.kneel, "dad")
    raises("seated", "dad is seated in chair.seat", world.move, "dad", MARKS["curb"])
    world.stand_up("dad")
    assert world.people["dad"].pose == "stand" and world.people["dad"].seat is None
    assert world.props["chair"].occupants == {}
    world.kneel("dad")
    assert world.people["dad"].pose == "kneel"
    world.stand_up("dad")
    assert world.people["dad"].pose == "stand"
    world.kneel("mom")
    world.move("mom", MARKS["curb"])  # walking off ends the kneel
    assert world.people["mom"].pose == "stand"


def test_cannot_pick_up_an_occupied_prop(world: World):
    parked(world)
    world.sit_on("dad", "chair")
    raises("occupied", "dad is sitting on it", world.pick_up, "mom", "chair")


# -------------------------------------------------------------- wear & place

def test_put_on(world: World):
    opening(world)
    world.move("dad", MARKS["steps_foot"])
    world.put_on("dad", "hat")  # from the ground (not yet placed)
    assert world.holder("hat") == ("dad", "head")
    assert world.people["dad"].holding == {"head": "hat"}
    assert world.props["hat"].parent == "puppet:dad.head"
    raises("already_held", "dad is already wearing it", world.put_on, "dad", "hat")
    world.pick_up("dad", "scarf")
    raises("head_full", "dad is already wearing hat", world.put_on, "dad", "scarf")
    world.put_on("dad", "scarf", target="mom")
    assert world.holder("scarf") == ("mom", "head")
    assert world.people["dad"].holding == {"head": "hat"}
    raises("not_holding", "mom is holding it", world.put_on, "dad", "hymn_books")
    raises("offstage", "kid is offstage", world.put_on, "dad", "cup", "kid")


def test_put_on_from_a_surface_is_rejected(world: World):
    world.begin_shot(shot(start={"dad": {"at": "home"}}, propStart={"table": {"at": "curb"}, "hat": {"on": "table"}}),
                     MARKS, same_set_as_previous=False)
    assert world.props["hat"].parent == "prop:table.top"
    raises("not_holding", "it is on table.top", world.put_on, "dad", "hat")


def test_place_on_socket_and_ground(world: World):
    opening(world)
    world.begin_shot(shot(id="s2", start={"mom": {"at": "steps_top", "holding": "hymn_books"},
                                          "dad": {"at": "home", "holding": "cup"}},
                          propStart={"table": {"at": "curb"}}),
                     MARKS, same_set_as_previous=True)
    world.place_on("mom", "hymn_books", "prop:table.top", (0, 0, 0))
    assert world.props["hymn_books"].parent == "prop:table.top"
    assert world.props["table"].occupants == {"top": "prop:hymn_books"}
    assert world.props["hymn_books"].world_pos == pytest.approx((0.8, 0.4, 0.5))
    assert world.people["mom"].holding == {}
    raises("socket_taken", "table.top is taken by hymn_books", world.place_on, "dad", "cup", "prop:table.top", (0, 0, 0))
    raises("unknown_socket", "table has no socket shelf", world.place_on, "dad", "cup", "prop:table.shelf", (0, 0, 0))
    raises("not_holding", "it is on table.top", world.place_on, "mom", "hymn_books", "ground", (0, 0, 0.5))
    raises("bad_parent", "puppet:mom.hand_r is not the ground or a prop socket", world.place_on, "dad", "cup", "puppet:mom.hand_r", (0, 0, 0))
    world.place_on("dad", "cup", "ground", (-0.45, 0.0, 0.5))
    cup = world.props["cup"]
    assert cup.parent == "ground" and cup.pos == (-0.45, 0.0, 0.5) and cup.world_pos == (-0.45, 0.0, 0.5)
    assert world.people["dad"].holding == {}


def test_place_on_rejects_cycles(world: World):
    world.begin_shot(shot(start={"dad": {"at": "home", "holding": "crate"}},
                          propStart={"cup": {"on": "crate.top"}}),
                     MARKS, same_set_as_previous=False)
    # the cup rides on the crate while it is carried
    assert world.props["cup"].parent == "prop:crate.top"
    assert world.props["cup"].world_pos is None
    raises("cycle", "crate cannot rest on crate", world.place_on, "dad", "crate", "prop:crate.top", (0, 0, 0))


def test_prop_state(world: World):
    world.set_prop_state("lamp", "lit")
    assert world.props["lamp"].state == "lit"
    raises("unknown_state", "lamp has no state broken", world.set_prop_state, "lamp", "broken")
    world.set_prop_state("cup", "full")  # a prop with no declared states accepts any (validate.py checks)
    assert world.props["cup"].state == "full"


# --------------------------------------------------------------------- people

def test_hug_check(world: World):
    opening(world)
    raises("hug_self", "mom cannot hug themselves", world.hug_check, "mom", "mom")
    raises("offstage", "dad is offstage", world.hug_check, "mom", "dad")
    world.move("dad", MARKS["steps_foot"])
    world.hug_check("dad", "mom")
    world.begin_shot(shot(id="s2", start={"dad": {"at": "home"}, "mom": {"at": "home"}}),
                     MARKS, same_set_as_previous=True)
    world.sit_on("mom", "chair")
    raises("seated", "mom is seated in chair.seat", world.hug_check, "dad", "mom")


def test_move_exit_and_facing(world: World):
    opening(world)
    world.move("dad", MARKS["steps_left"], -1)
    assert world.people["dad"].pos == MARKS["steps_left"] and world.people["dad"].facing == -1
    raises("bad_facing", "facing must be 1 or -1, not 0", world.set_facing, "dad", 0)
    raises("bad_facing", None, world.move, "dad", MARKS["curb"], 2)
    world.set_facing("dad", 1)
    assert world.people["dad"].facing == 1
    assert world.on_stage("dad")
    assert world.exit("dad") == pytest.approx(OFFSTAGE_LEFT)
    assert world.people["dad"].facing == -1 and not world.on_stage("dad")
    assert world.exit("mom") == pytest.approx(OFFSTAGE_RIGHT)  # nearest side
    assert world.people["mom"].facing == 1
    assert world.holder("hymn_books") == ("mom", "hand_r")  # exiting keeps what you carry
    world.set_pose("mom", "bow_head")
    assert world.people["mom"].pose == "bow_head"


# ------------------------------------------------------------------- shots

def test_begin_shot_same_set_keeps_unlisted_cast(world: World):
    opening(world)
    world.move("dad", MARKS["steps_foot"], 1)
    world.kneel("dad")
    world.drop("mom", "hymn_books", (0.12, 0.3, 0.5))
    world.pick_up("dad", "hymn_books")
    world.begin_shot(shot(id="s2", start={"mom": {"at": "curb", "facing": "right"}}), MARKS, same_set_as_previous=True)
    dad = world.people["dad"]
    assert dad.pos == MARKS["steps_foot"] and dad.facing == 1 and dad.pose == "kneel"
    assert world.holder("hymn_books") == ("dad", "hand_r")
    assert world.people["mom"].pos == MARKS["curb"] and world.people["mom"].facing == 1


def test_begin_shot_new_set_sends_unlisted_cast_offstage(world: World):
    opening(world)
    world.move("dad", MARKS["steps_foot"], -1)
    world.drop("mom", "hymn_books", (0.12, 0.3, 0.5))
    world.pick_up("dad", "hymn_books")
    world.sit_on("mom", "chair")
    world.begin_shot(shot(id="s2", set="kitchen", start={"kid": {"at": "home"}}), MARKS, same_set_as_previous=False)
    dad, mom = world.people["dad"], world.people["mom"]
    assert dad.pos == pytest.approx(OFFSTAGE_LEFT) and dad.facing == 1 and dad.pose == "stand"
    # dad is not in the new shot, so what he carried does not come along to the new set
    assert world.holder("hymn_books") is None and dad.holding == {}
    assert world.props["hymn_books"].parent == "ground" and world.props["hymn_books"].world_pos is None
    assert mom.pos == pytest.approx(OFFSTAGE_LEFT) and mom.seat is None and mom.pose == "stand"
    assert world.props["chair"].occupants == {}
    assert world.people["kid"].pos == MARKS["home"]


def test_begin_shot_start_state_details(world: World):
    opening(world)
    world.move("dad", MARKS["steps_foot"])
    world.pick_up("dad", "cup")
    world.begin_shot(
        shot(id="s2", start={
            "dad": {"at": "home", "holding": "hymn_books", "facing": "left"},  # taken from mom
            "mom": {"at": "curb", "in": "sedan.driver"},
            "kid": {"at": "steps_top", "pose": "kneel"},
        }, propStart={"sedan": {"at": "curb", "state": "parked"}, "lamp": {"at": "steps_left", "state": "lit"}}),
        MARKS, same_set_as_previous=True,
    )
    assert world.people["dad"].holding == {"hand_r": "cup", "hand_l": "hymn_books"}
    assert world.people["dad"].facing == -1
    assert world.people["mom"].holding == {}
    assert world.people["mom"].seat == ("sedan", "driver") and world.people["mom"].pose == "sit"
    assert world.props["sedan"].occupants == {"driver": "puppet:mom"}
    assert world.props["sedan"].state == "parked" and world.props["sedan"].world_pos == MARKS["curb"]
    assert world.people["kid"].pose == "kneel"
    assert world.props["lamp"].parent == "ground" and world.props["lamp"].pos == MARKS["steps_left"]
    assert world.props["lamp"].state == "lit"
    world.check_driver("mom", "sedan")
    # next shot: mom listed without `in` stands up out of the car
    world.begin_shot(shot(id="s3", start={"mom": {"at": "home"}}), MARKS, same_set_as_previous=True)
    assert world.people["mom"].seat is None and world.props["sedan"].occupants == {}


def test_begin_shot_prop_start_takes_prop_from_hand(world: World):
    opening(world)
    world.begin_shot(shot(id="s2", start={}, propStart={"hymn_books": {"at": "curb"}}), MARKS, same_set_as_previous=True)
    assert world.holder("hymn_books") is None and world.people["mom"].holding == {}
    assert world.props["hymn_books"].pos == MARKS["curb"]


def test_begin_shot_errors_leave_world_unchanged(world: World):
    opening(world)
    raises("double_hold", "dad and mom both start holding hymn_books; give it to one of them", world.begin_shot,
           shot(id="s2", start={"dad": {"at": "home", "holding": "hymn_books"},
                                "mom": {"at": "curb", "holding": "hymn_books"}}),
           MARKS, False)
    raises("unknown_mark", "nowhere is not a mark in shot s2", world.begin_shot,
           shot(id="s2", start={"dad": {"at": "nowhere"}}), MARKS, False)
    raises("prop_conflict",
           "dad starts holding hymn_books, and propStart also places it; "
           "remove hymn_books from propStart or from dad's holding", world.begin_shot,
           shot(id="s2", start={"dad": {"at": "home", "holding": "hymn_books"}},
                propStart={"hymn_books": {"at": "curb"}}), MARKS, True)
    raises("socket_taken", "dad and mom both start in sedan.driver; seat one elsewhere", world.begin_shot,
           shot(id="s2", start={"dad": {"at": "home", "in": "sedan.driver"}, "mom": {"at": "home", "in": "sedan.driver"}}),
           MARKS, True)
    raises("unknown_socket", "sedan has no socket roof; its sockets are driver, passenger, back_seat", world.begin_shot,
           shot(id="s2", start={"dad": {"at": "home", "in": "sedan.roof"}}), MARKS, True)
    raises("unknown_socket", "dad starts in sedan with no socket; its sockets are driver, passenger, back_seat",
           world.begin_shot, shot(id="s2", start={"dad": {"at": "home", "in": "sedan"}}), MARKS, True)
    raises("unknown_socket", "dad starts in cup with no socket; it has no sockets", world.begin_shot,
           shot(id="s2", start={"dad": {"at": "home", "in": "cup"}}), MARKS, True)
    raises("cycle", "crate cannot rest on table, which rests on crate", world.begin_shot,
           shot(id="s2", propStart={"table": {"on": "crate.top"}, "crate": {"on": "table.top"}}), MARKS, True)
    raises("socket_taken", "every socket on table is taken", world.begin_shot,
           shot(id="s2", propStart={"table": {"at": "curb"}, "cup": {"on": "table.top"}, "lamp": {"on": "table"}}),
           MARKS, True)
    raises("unknown_id", "ghost is not in the cast", world.begin_shot,
           shot(id="s2", start={"ghost": {"at": "home"}}), MARKS, True)
    raises("socket_taken", "table.top is taken by cup", world.begin_shot,
           shot(id="s2", propStart={"table": {"at": "curb"}, "cup": {"on": "table.top"}, "lamp": {"on": "table.top"}}),
           MARKS, True)
    # late failure (after some mutation on the scratch copy) still leaves the world as it was
    world.move("dad", MARKS["home"])
    world.pick_up("dad", "cup")
    world.pick_up("dad", "lamp")
    raises("hands_full", "dad's hands are full (hand_r: cup, hand_l: lamp)", world.begin_shot,
           shot(id="s2", start={"dad": {"at": "curb", "holding": "hymn_books"}, "mom": {"at": "home"}}),
           MARKS, True)


def test_builtin_marks_always_available(world: World):
    world.begin_shot(shot(start={"dad": {"at": "offstage_right"}}), {}, same_set_as_previous=False)
    assert world.people["dad"].pos == pytest.approx(OFFSTAGE_RIGHT)


# ---------------------------------------------------------------------- copy

def test_copy_is_deep(world: World):
    opening(world)
    c = world.copy()
    assert c == world and c is not world
    c.drop("mom", "hymn_books", (0.1, 0.3, 0.5))
    c.move("dad", MARKS["curb"])
    c.occupy("dad", 3.0, "walking")
    assert world.holder("hymn_books") == ("mom", "hand_r")
    assert world.people["mom"].holding == {"hand_r": "hymn_books"}
    assert world.people["dad"].pos == pytest.approx(OFFSTAGE_LEFT)
    assert world.people["dad"].busy_until == 0.0
    assert c != world
    s = world.snapshot()
    assert s["props"]["hymn_books"]["parent"] == "puppet:mom.hand_r"


def test_deterministic(world: World):
    def run() -> dict:
        w = World(PEOPLE, PROPS)
        opening(w)
        w.move("dad", MARKS["steps_foot"])
        w.drop("mom", "hymn_books", (0.12, 0.3, 0.5))
        w.pick_up("dad", "hymn_books")
        w.hand_to("dad", "hymn_books", "mom")
        return w.snapshot()
    assert run() == run()


# ------------------------------------------------------------- open / close

DOOR = prop("door", "furniture", states={"open": {}, "closed": {}})


def door_world() -> World:
    w = World(PEOPLE, PROPS + [DOOR])
    w.begin_shot(shot(start={"mom": {"at": "home"}}, propStart={**LOOSE, "door": {"at": "curb", "state": "closed"}}),
                 MARKS, same_set_as_previous=False)
    return w


def test_open_and_close():
    w = door_world()
    w.open("mom", "door")
    assert w.props["door"].state == "open"
    raises("already_open", "it is already open", w.open, "mom", "door")
    w.close("mom", "door")
    assert w.props["door"].state == "closed"
    raises("already_closed", "it is already closed", w.close, "mom", "door")
    # a prop without declared states accepts open / closed (validate.py checks the vocabulary)
    w.open("mom", "cup")
    assert w.props["cup"].state == "open"
    w.close("mom", "cup")
    assert w.props["cup"].state == "closed"


def test_open_close_errors():
    w = door_world()
    raises("unknown_state", "lamp has no state open", w.open, "mom", "lamp")
    raises("unknown_state", "lamp has no state closed", w.close, "mom", "lamp")
    raises("offstage", "dad is offstage", w.open, "dad", "door")
    raises("not_on_stage", "crate is not on stage", w.open, "mom", "crate")
    raises("unknown_id", "ghost is not in the cast", w.open, "ghost", "door")
    raises("unknown_id", "anvil is not a known prop", w.close, "mom", "anvil")
    w.pick_up("mom", "cup")
    w.open("mom", "cup")  # a held prop is on stage
    assert w.props["cup"].state == "open"


# --------------------------------------------- a set change clears the stage

def test_new_set_clears_props_left_behind(world: World):
    """Regression: a prop dropped on set A stayed at its coordinates on set B."""
    opening(world)
    world.drop("mom", "hymn_books", (0.12, 0.3, 0.5))
    world.begin_shot(shot(id="s2", set="kitchen", start={"mom": {"at": "home"}}), MARKS, same_set_as_previous=False)
    books = world.props["hymn_books"]
    assert books.parent == "ground" and books.world_pos is None and world.is_unplaced("hymn_books")
    for what in LOOSE:
        assert world.is_unplaced(what), what
    raises("not_on_stage", "hymn_books is not on stage", world.pick_up, "mom", "hymn_books")
    raises("not_holding", "it is not on stage", world.hold, "mom", "hymn_books")


def test_same_set_keeps_props_where_they_were(world: World):
    opening(world)
    world.drop("mom", "hymn_books", (0.12, 0.3, 0.5))
    world.begin_shot(shot(id="s2", start={"mom": {"at": "home"}}), MARKS, same_set_as_previous=True)
    assert world.props["hymn_books"].world_pos == (0.12, 0.3, 0.5)
    assert world.props["cup"].world_pos == MARKS["home"]


def test_new_set_clears_surfaces_seats_and_what_unlisted_cast_carried(world: World):
    world.begin_shot(shot(start={"dad": {"at": "home", "holding": "cup"}, "mom": {"at": "home", "in": "sedan.driver"},
                                 "kid": {"at": "home", "in": "chair.seat"}},
                          propStart={"sedan": {"at": "curb"}, "chair": {"at": "home"}, "table": {"at": "steps_foot"},
                                     "lamp": {"on": "table.top"}}),
                     MARKS, same_set_as_previous=False)
    world.begin_shot(shot(id="s2", set="kitchen", start={"kid": {"at": "steps_left"}}), MARKS, same_set_as_previous=False)
    for what in ("sedan", "chair", "table", "lamp", "cup"):
        st = world.props[what]
        assert st.parent == "ground" and st.world_pos is None and st.occupants == {}, what
    assert world.people["mom"].seat is None and world.people["kid"].seat is None
    assert world.people["dad"].holding == {}


def test_new_set_keeps_what_the_shot_brings(world: World):
    world.begin_shot(shot(start={"dad": {"at": "home", "holding": "crate"}, "mom": {"at": "home", "holding": "lamp"}},
                          propStart={"cup": {"on": "crate.top"}, "table": {"at": "curb"}, "hat": {"on": "table.top"},
                                     "scarf": {"at": "curb"}}),
                     MARKS, same_set_as_previous=False)
    world.begin_shot(
        shot(id="s2", set="kitchen",
             start={"dad": {"at": "steps_left"}, "kid": {"at": "home", "holding": "scarf"}},
             propStart={"hat": {"state": "worn"}, "bench": {"at": "home"}}),
        MARKS, same_set_as_previous=False,
    )
    # carried in by a listed puppet, with what rides on it
    assert world.holder("crate") == ("dad", "hand_r")
    assert world.props["cup"].parent == "prop:crate.top"
    # start.holding takes it from the old set
    assert world.holder("scarf") == ("kid", "hand_r")
    # in prop_start: kept, but the stale table it rested on is gone, so it is loose
    assert world.is_unplaced("hat") and world.props["hat"].state == "worn"
    assert world.props["table"].occupants == {}
    assert world.props["bench"].world_pos == MARKS["home"]
    # mom is not in s2, so the lamp she carried stays behind
    assert world.is_unplaced("lamp") and world.people["mom"].holding == {}


def test_held_prop_carries_from_shot_one_to_shot_two(world: World):
    opening(world)
    # shot 2 on a new set lists mom without `holding`: she still has the books
    world.begin_shot(shot(id="s2", set="kitchen", start={"mom": {"at": "home"}, "dad": {"at": "curb"}}),
                     MARKS, same_set_as_previous=False)
    assert world.holder("hymn_books") == ("mom", "hand_r")
    assert world.props["hymn_books"].parent == "puppet:mom.hand_r"
    assert world.hold("mom", "hymn_books") == "hand_r"
    raises("not_holding", "mom is holding it", world.hand_to, "dad", "hymn_books", "mom")


def test_seat_conflict_across_shots(world: World):
    opening(world)
    world.begin_shot(shot(id="s1b", start={"mom": {"at": "home", "in": "chair.seat"}},
                          propStart={"chair": {"at": "home"}}), MARKS, same_set_as_previous=True)
    assert world.props["chair"].occupants == {"seat": "puppet:mom"}
    # same set, mom unlisted so she stays seated; dad cannot start in her seat
    raises("socket_taken", "chair.seat is taken by mom", world.begin_shot,
           shot(id="s2", start={"dad": {"at": "home", "in": "chair.seat"}}), MARKS, True)
    world.move("dad", MARKS["home"])
    raises("socket_taken", "chair.seat is taken by mom", world.sit_on, "dad", "chair")


def test_hand_to_a_prop_mom_holds_exact_reason(world: World):
    """The ARCHITECTURE.md example: shot s1, t=5: dad cannot hand_to "hymn_books"; mom is holding it."""
    opening(world)
    world.move("dad", MARKS["steps_foot"])
    err = raises("not_holding", "mom is holding it", world.hand_to, "dad", "hymn_books", "mom")
    assert str(err) == "mom is holding it"


# ----------------------------------------------- start seats must be on stage

def test_start_seat_must_be_on_stage(world: World):
    opening(world)
    raises("not_on_stage", "chair is not on stage in shot s2; place it with propStart", world.begin_shot,
           shot(id="s2", start={"mom": {"at": "home", "in": "chair.seat"}}), MARKS, True)
    raises("not_on_stage", "sedan is not on stage in shot s2; place it with propStart", world.begin_shot,
           shot(id="s2", start={"mom": {"at": "home", "in": "sedan.driver"}}), MARKS, False)
    # a seat on a carried prop is not on stage either
    world.move("dad", MARKS["home"])
    raises("not_on_stage", "chair is not on stage in shot s2; place it with propStart", world.begin_shot,
           shot(id="s2", start={"dad": {"at": "home", "holding": "chair"}, "mom": {"at": "home", "in": "chair.seat"}}),
           MARKS, True)
    # prop_start is applied before seats, so placing it in the same shot works
    world.begin_shot(shot(id="s2", start={"mom": {"at": "home", "in": "chair.seat"}},
                          propStart={"chair": {"at": "home"}}), MARKS, same_set_as_previous=False)
    assert world.people["mom"].seat == ("chair", "seat")


def test_prop_start_on_a_prop_that_is_not_on_stage(world: World):
    opening(world)
    raises("not_on_stage", "table is not on stage in shot s2; place it with propStart", world.begin_shot,
           shot(id="s2", propStart={"cup": {"on": "table.top"}}), MARKS, True)
    # order inside prop_start does not matter
    world.begin_shot(shot(id="s2", propStart={"cup": {"on": "table.top"}, "table": {"at": "curb"}}), MARKS, True)
    assert world.props["cup"].world_pos == pytest.approx((0.8, 0.4, 0.5))


def test_prop_start_state_must_exist(world: World):
    opening(world)
    raises("unknown_state", "lamp has no state broken; its states are lit, out", world.begin_shot,
           shot(id="s2", propStart={"lamp": {"state": "broken"}}), MARKS, True)


def test_start_holding_a_prop_someone_sits_on(world: World):
    parked(world)
    world.sit_on("mom", "chair")
    raises("occupied", "mom is sitting on chair; start mom somewhere else", world.begin_shot,
           shot(id="s2", start={"dad": {"at": "home", "holding": "chair"}}), MARKS, True)


# ------------------------------------------------------- props must be on stage

def test_pick_up_and_put_on_an_unplaced_prop(world: World):
    world.begin_shot(shot(start={"dad": {"at": "home"}}), MARKS, same_set_as_previous=False)
    raises("not_on_stage", "cup is not on stage", world.pick_up, "dad", "cup")
    raises("not_on_stage", "hat is not on stage", world.put_on, "dad", "hat")
    raises("not_holding", "it is not on stage", world.hand_to, "dad", "cup", "mom")
    raises("not_holding", "it is not on stage", world.drop, "dad", "cup", (0, 0, 0.5))


def test_offstage_puppets_cannot_handle_props(world: World):
    opening(world)
    raises("offstage", "dad is offstage", world.pick_up, "dad", "cup")
    raises("offstage", "dad is offstage", world.put_on, "dad", "hat")
    world.exit("mom")
    raises("offstage", "mom is offstage", world.hold, "mom", "hymn_books")
    raises("offstage", "mom is offstage", world.drop, "mom", "hymn_books", (0, 0, 0.5))
    raises("offstage", "mom is offstage", world.put_on, "mom", "hymn_books")
    raises("offstage", "mom is offstage", world.place_on, "mom", "hymn_books", "ground", (0, 0, 0.5))


def test_put_on_from_the_ground_needs_a_free_hand(world: World):
    opening(world)
    world.move("dad", MARKS["home"])
    world.pick_up("dad", "cup")
    world.pick_up("dad", "lamp")
    raises("hands_full", "dad's hands are full (hand_r: cup, hand_l: lamp)", world.put_on, "dad", "hat")
    world.put_on("dad", "lamp")  # from a hand is fine
    assert world.holder("lamp") == ("dad", "head")


# ---------------------------------------------------------- vehicles vs seats

def test_get_in_needs_a_vehicle_and_sit_on_refuses_one(world: World):
    parked(world)
    raises("not_a_vehicle", "chair is not a vehicle", world.get_in, "dad", "chair")
    raises("vehicle_seat", "sedan is a vehicle; use get_in", world.sit_on, "dad", "sedan")
    raises("vehicle_seat", "sedan is a vehicle; use get_in", world.sit_on, "dad", "sedan", "driver")


def test_move_vehicle_carries_puppets_seated_on_stacked_props(world: World):
    world.begin_shot(shot(start={"kid": {"at": "curb", "in": "chair.seat"}},
                          propStart={"wagon": {"at": "curb"}, "chair": {"on": "wagon.bed"}}),
                     MARKS, same_set_as_previous=False)
    chair_before = world.props["chair"].world_pos
    world.move_vehicle("wagon", (0.5, 0.0, 0.5))
    assert world.people["kid"].pos == pytest.approx((0.5, 0.0, 0.5))  # moved by the same -0.3
    assert world.props["chair"].world_pos == pytest.approx((chair_before[0] - 0.3, chair_before[1], 0.5))


def test_move_vehicle_that_is_not_on_stage(world: World):
    raises("not_on_stage", "sedan is not on stage", world.move_vehicle, "sedan", (0, 0, 0.5))


def test_exit_while_seated_or_kneeling(world: World):
    parked(world)
    world.sit_on("dad", "chair")
    raises("seated", "dad is seated in chair.seat", world.exit, "dad")
    world.kneel("mom")
    world.exit("mom")
    assert world.people["mom"].pose == "stand" and not world.on_stage("mom")


# ------------------------------------------- put_on keeps pick_up's seat rules

def test_put_on_a_prop_someone_sits_on(world: World):
    """Regression: put_on straight off the ground skipped the seat checks pick_up has."""
    parked(world)
    world.sit_on("dad", "chair")
    raises("occupied", "dad is sitting on it", world.put_on, "mom", "chair")
    raises("occupied", "dad is sitting on it", world.put_on, "dad", "chair")
    assert world.people["dad"].seat == ("chair", "seat") and world.props["chair"].occupants == {"seat": "puppet:dad"}


def test_put_on_from_the_ground_while_seated_in_a_vehicle(world: World):
    parked(world)
    world.get_in("dad", "sedan")
    raises("seated_vehicle", "dad is seated in sedan; use get_out first", world.put_on, "dad", "hat")
    raises("seated_vehicle", "dad is seated in sedan; use get_out first", world.pick_up, "dad", "hat")


def test_cannot_take_a_prop_someone_sits_on_through_a_stacked_prop(world: World):
    """Regression: only direct occupants were checked, so taking the crate under the chair kid
    sat on carried kid's chair away while kid stayed seated."""
    world.begin_shot(shot(start={"mom": {"at": "curb"}, "kid": {"at": "curb", "in": "chair.seat"}},
                          propStart={"crate": {"at": "curb"}, "chair": {"on": "crate.top"}}),
                     MARKS, same_set_as_previous=False)
    raises("occupied", "kid is sitting on chair, which rests on it", world.pick_up, "mom", "crate")
    raises("occupied", "kid is sitting on chair, which rests on it", world.put_on, "mom", "crate")
    # the sitter is told the same way about what is under their own seat
    raises("occupied", "kid is sitting on chair, which rests on it", world.pick_up, "kid", "crate")


# --------------------------------------------- character traits (character.py)

def sheet_person(pid: str, notes: str | None = None, item: str | None = None, build: str = "average") -> schemas.Person:
    return schemas.Person(id=pid, family_id="f", name=pid.title(),
                          character={"color": "#123456", "build": build, "notes": notes, "item_prop_id": item})


TRAIT_PROPS = PROPS + [prop("flat_cap"), prop("bonnet", "tiny"), prop("crown", "carried"), prop("house", "building")]


def cast_with_cap() -> World:
    """Grandpa wears his flat cap; mom and kid have no item."""
    return World([sheet_person("grandpa", "walks slowly with a cane", "flat_cap"), sheet_person("mom"),
                  sheet_person("kid", "spry")], TRAIT_PROPS)


def wears(w: World, who: str, item: str) -> None:
    assert w.people[who].holding.get("head") == item
    assert w.holder(item) == (who, "head")
    st = w.props[item]
    assert st.parent == f"puppet:{who}.head" and st.pos == (0.0, 0.0, 0.0) and st.world_pos is None


def test_speed_scale_from_notes():
    w = cast_with_cap()
    assert w.people["grandpa"].speed_scale == 0.6
    assert w.people["mom"].speed_scale == 1.0
    assert w.people["kid"].speed_scale == 1.25
    assert World([sheet_person("a", "Moves SLOWLY")], []).people["a"].speed_scale == 0.75
    # the plain fixtures have no notes
    assert all(p.speed_scale == 1.0 and p.item is None for p in World(PEOPLE, PROPS).people.values())


def test_signature_item_starts_worn():
    w = cast_with_cap()
    g = w.people["grandpa"]
    assert g.item == "flat_cap" and g.holding == {"head": "flat_cap"}
    wears(w, "grandpa", "flat_cap")
    assert w.people["mom"].item is None and w.people["mom"].holding == {}
    # tiny and carried props are wearable too
    w2 = World([sheet_person("a", item="bonnet"), sheet_person("b", item="crown")], TRAIT_PROPS)
    wears(w2, "a", "bonnet")
    wears(w2, "b", "crown")
    assert w2.people["a"].item == "bonnet" and w2.people["b"].item == "crown"


@pytest.mark.parametrize("item", ["sedan", "chair", "house", "anvil"])
def test_signature_item_only_when_passed_in_and_wearable(item: str):
    """A vehicle, furniture or a building is never worn, nor is a prop the scene does not have."""
    w = World([sheet_person("a", item=item)], TRAIT_PROPS)
    assert w.people["a"].item is None and w.people["a"].holding == {}
    if item in w.props:
        assert w.props[item] == PropState(id=item)
    # the same person with the prop missing from the scene's props
    w = World([sheet_person("a", item="flat_cap")], PROPS)
    assert w.people["a"].item is None and w.people["a"].holding == {}


def test_shared_signature_item_first_in_cast_wins():
    w = World([sheet_person("mom", item="flat_cap"), sheet_person("dad", item="flat_cap")], TRAIT_PROPS)
    wears(w, "mom", "flat_cap")
    assert w.people["mom"].item == "flat_cap"
    assert w.people["dad"].item is None and w.people["dad"].holding == {}
    w = World([sheet_person("dad", item="flat_cap"), sheet_person("mom", item="flat_cap")], TRAIT_PROPS)
    wears(w, "dad", "flat_cap")
    assert w.people["mom"].item is None


def test_copy_eq_and_snapshot_include_traits():
    w = cast_with_cap()
    c = w.copy()
    assert c == w
    c.people["grandpa"].speed_scale = 1.0
    assert c != w
    c = w.copy()
    c.people["grandpa"].item = None
    assert c != w
    snap = w.snapshot()
    assert snap["people"]["grandpa"]["speed_scale"] == 0.6 and snap["people"]["grandpa"]["item"] == "flat_cap"
    assert snap["people"]["grandpa"]["holding"] == {"head": "flat_cap"}
    assert snap["props"]["flat_cap"]["parent"] == "puppet:grandpa.head"
    assert snap["people"]["mom"]["speed_scale"] == 1.0 and snap["people"]["mom"]["item"] is None
    assert cast_with_cap().snapshot() == snap  # deterministic


def test_signature_item_blocks_a_second_hat_and_reports_who_wears_it():
    w = cast_with_cap()
    w.begin_shot(shot(start={"grandpa": {"at": "home"}, "mom": {"at": "home"}}, propStart=LOOSE), MARKS, False)
    raises("head_full", "grandpa is already wearing flat_cap", w.put_on, "grandpa", "hat")
    raises("already_held", "grandpa is wearing it", w.pick_up, "mom", "flat_cap")
    raises("already_held", "grandpa is already wearing it", w.put_on, "grandpa", "flat_cap")


# Rule (1): the head item stays on across shots, for listed and unlisted cast alike.

def test_signature_item_stays_on_across_same_set_shots():
    w = cast_with_cap()
    w.begin_shot(shot(start={"grandpa": {"at": "home"}}), MARKS, same_set_as_previous=False)
    wears(w, "grandpa", "flat_cap")
    w.begin_shot(shot(id="s2", start={"grandpa": {"at": "curb"}}), MARKS, same_set_as_previous=True)
    wears(w, "grandpa", "flat_cap")
    w.begin_shot(shot(id="s3", start={"mom": {"at": "curb"}}), MARKS, same_set_as_previous=True)  # grandpa unlisted
    wears(w, "grandpa", "flat_cap")


def test_signature_item_stays_on_across_set_changes():
    w = cast_with_cap()
    w.begin_shot(shot(start={"grandpa": {"at": "home", "holding": "cup"}, "mom": {"at": "home"}}),
                 MARKS, same_set_as_previous=False)
    # listed on the new set: carries both the cup and the cap in
    w.begin_shot(shot(id="s2", set="kitchen", start={"grandpa": {"at": "curb"}}), MARKS, same_set_as_previous=False)
    wears(w, "grandpa", "flat_cap")
    assert w.holder("cup") == ("grandpa", "hand_r")
    # unlisted on the next set: waits offstage, loses the hand-held cup (#50) but keeps the cap
    w.begin_shot(shot(id="s3", set="porch", start={"mom": {"at": "home"}}), MARKS, same_set_as_previous=False)
    g = w.people["grandpa"]
    assert g.pos == pytest.approx(OFFSTAGE_LEFT) and not w.on_stage("grandpa")
    assert g.holding == {"head": "flat_cap"}
    wears(w, "grandpa", "flat_cap")
    assert w.holder("cup") is None and w.is_unplaced("cup")
    # and walks into a later set still wearing it
    w.begin_shot(shot(id="s4", set="yard", start={"grandpa": {"at": "home"}}), MARKS, same_set_as_previous=False)
    wears(w, "grandpa", "flat_cap")
    assert g.item == "flat_cap"


def test_hat_put_on_stays_on_for_unlisted_cast_too():
    """The signature item is treated like a hat put on with put_on: same rule for both."""
    w = World(PEOPLE, PROPS)
    opening(w)
    w.move("dad", MARKS["home"])
    w.put_on("dad", "hat")
    w.pick_up("dad", "cup")
    w.begin_shot(shot(id="s2", set="kitchen", start={"mom": {"at": "home"}}), MARKS, same_set_as_previous=False)
    assert w.people["dad"].holding == {"head": "hat"}
    assert w.holder("hat") == ("dad", "head") and w.is_unplaced("cup")


# Rule (2): someone else's start `holding`, or prop_start, takes it off the head silently.

def test_someone_elses_start_holding_takes_the_item_off_the_head():
    w = cast_with_cap()
    w.begin_shot(shot(start={"grandpa": {"at": "home"}, "mom": {"at": "home"}}), MARKS, same_set_as_previous=False)
    w.begin_shot(shot(id="s2", start={"grandpa": {"at": "home"}, "kid": {"at": "curb", "holding": "flat_cap"}}),
                 MARKS, same_set_as_previous=True)
    assert w.holder("flat_cap") == ("kid", "hand_r") and w.props["flat_cap"].parent == "puppet:kid.hand_r"
    assert w.people["grandpa"].holding == {}
    assert w.people["grandpa"].item == "flat_cap"  # still his signature item, just not on his head


def test_someone_elses_start_holding_takes_it_on_a_set_change_from_unlisted_wearer():
    w = cast_with_cap()
    w.begin_shot(shot(start={"grandpa": {"at": "home"}}), MARKS, same_set_as_previous=False)
    # grandpa is not in s2 (he waits offstage); mom starts the new set with his cap
    w.begin_shot(shot(id="s2", set="kitchen", start={"mom": {"at": "curb", "holding": "flat_cap"}}),
                 MARKS, same_set_as_previous=False)
    assert w.holder("flat_cap") == ("mom", "hand_r")
    assert w.people["grandpa"].holding == {}


def test_no_hands_full_or_conflict_from_the_head_alone():
    w = cast_with_cap()
    w.begin_shot(shot(start={"grandpa": {"at": "home", "holding": "cup"}, "mom": {"at": "home", "holding": "lamp"}},
                      propStart={"scarf": {"at": "home"}}),
                 MARKS, same_set_as_previous=False)
    w.pick_up("grandpa", "scarf")  # both hands full and the head worn
    w.begin_shot(shot(id="s2", start={"grandpa": {"at": "home"}, "kid": {"at": "home", "holding": "flat_cap"}}),
                 MARKS, same_set_as_previous=True)
    assert w.holder("flat_cap") == ("kid", "hand_r")
    assert w.people["grandpa"].holding == {"hand_r": "cup", "hand_l": "scarf"}


def test_prop_start_takes_the_item_off_the_head():
    w = cast_with_cap()
    w.begin_shot(shot(start={"grandpa": {"at": "home"}}), MARKS, same_set_as_previous=False)
    w.begin_shot(shot(id="s2", start={"grandpa": {"at": "home"}}, propStart={"flat_cap": {"at": "curb"}}),
                 MARKS, same_set_as_previous=True)
    assert w.holder("flat_cap") is None and w.people["grandpa"].holding == {}
    assert w.props["flat_cap"].parent == "ground" and w.props["flat_cap"].world_pos == MARKS["curb"]
    # onto a surface, from an unlisted wearer, on a set change
    w = cast_with_cap()
    w.begin_shot(shot(start={"grandpa": {"at": "home"}}), MARKS, same_set_as_previous=False)
    w.begin_shot(shot(id="s2", set="kitchen", start={"mom": {"at": "home"}},
                      propStart={"table": {"at": "curb"}, "flat_cap": {"on": "table.top"}}),
                 MARKS, same_set_as_previous=False)
    assert w.props["flat_cap"].parent == "prop:table.top" and w.props["table"].occupants == {"top": "prop:flat_cap"}
    assert w.people["grandpa"].holding == {}


# Rule (3): the wearer's own start `holding` moves it from head to hand.

def test_wearers_own_start_holding_moves_it_to_a_hand():
    w = cast_with_cap()
    w.begin_shot(shot(start={"grandpa": {"at": "home", "holding": "flat_cap"}}), MARKS, same_set_as_previous=False)
    assert w.holder("flat_cap") == ("grandpa", "hand_r")
    assert w.people["grandpa"].holding == {"hand_r": "flat_cap"}
    assert w.props["flat_cap"].parent == "puppet:grandpa.hand_r"
    # once in hand it can be put back on with put_on
    w.put_on("grandpa", "flat_cap")
    wears(w, "grandpa", "flat_cap")


def test_wearers_own_start_holding_uses_the_free_hand():
    w = cast_with_cap()
    w.begin_shot(shot(start={"grandpa": {"at": "home", "holding": "cup"}}), MARKS, same_set_as_previous=False)
    w.begin_shot(shot(id="s2", start={"grandpa": {"at": "home", "holding": "flat_cap"}}),
                 MARKS, same_set_as_previous=True)
    assert w.people["grandpa"].holding == {"hand_r": "cup", "hand_l": "flat_cap"}


# ------------------------------------------------------- riders of a re-placed prop

def _seat_root(world: World):
    """A stand-in seated-root formula: 0.1 below the seat socket."""
    return lambda who, seat: (seat[0], seat[1] - 0.1, seat[2])


def test_reseat_riders_moves_a_seated_puppet_to_its_seat_at_the_new_place(world: World):
    world.begin_shot(shot(start={"dad": {"at": "home", "in": "sedan.driver"}, "kid": {"at": "curb"}},
                          propStart={"sedan": {"at": "home"}}), MARKS, same_set_as_previous=False)
    world.move_vehicle("sedan", MARKS["curb"])
    at_curb = world.people["dad"].pos
    world.begin_shot(shot(id="s2", start={"kid": {"at": "steps_left"}}, propStart={"sedan": {"at": "home"}}),
                     MARKS, same_set_as_previous=True)
    # begin_shot keeps dad seated but does not move him
    assert world.people["dad"].seat == ("sedan", "driver") and world.people["dad"].pos == at_curb
    seat = world.socket_world("sedan", "driver")
    assert world.reseat_riders(["sedan"], _seat_root(world), skip={"kid"}) == {"dad": (seat[0], seat[1] - 0.1, seat[2])}
    assert world.people["dad"].pos == (seat[0], seat[1] - 0.1, seat[2])
    assert world.people["dad"].seat == ("sedan", "driver") and world.people["dad"].pose == "sit"


def test_reseat_riders_follows_props_stacked_on_the_moved_one(world: World):
    world.begin_shot(shot(start={"mom": {"at": "home", "in": "chair.seat"}},
                          propStart={"wagon": {"at": "home"}, "chair": {"on": "wagon.bed"}}),
                     MARKS, same_set_as_previous=False)
    world.begin_shot(shot(id="s2", propStart={"wagon": {"at": "curb"}}), MARKS, same_set_as_previous=True)
    seat = world.socket_world("chair", "seat")
    assert seat[0] > 0.5  # the chair rode along on the wagon
    assert world.reseat_riders(["wagon"], _seat_root(world)) == {"mom": (seat[0], seat[1] - 0.1, seat[2])}


def test_reseat_riders_skips_listed_unrelated_and_unseated_puppets(world: World):
    world.begin_shot(shot(start={"dad": {"at": "home", "in": "sedan.driver"}, "mom": {"at": "curb", "in": "chair.seat"}},
                          propStart={"sedan": {"at": "home"}, "chair": {"at": "curb"}}), MARKS, same_set_as_previous=False)
    before = world.people["mom"].pos
    assert world.reseat_riders(["sedan"], _seat_root(world), skip={"dad"}) == {}
    assert world.reseat_riders([], _seat_root(world)) == {}
    assert world.reseat_riders(["chair"], _seat_root(world)) == {"mom": world.people["mom"].pos}
    assert world.people["mom"].pos != before


def test_reseat_riders_leaves_a_puppet_whose_seat_has_no_world_position(world: World):
    # mom stays seated in the chair, which s2 puts on a crate dad is carrying
    world.begin_shot(shot(start={"mom": {"at": "home", "in": "chair.seat"}, "dad": {"at": "curb"}},
                          propStart={"chair": {"at": "home"}}), MARKS, same_set_as_previous=False)
    world.begin_shot(shot(id="s2", start={"dad": {"at": "curb", "holding": "crate"}},
                          propStart={"chair": {"on": "crate.top"}}), MARKS, same_set_as_previous=True)
    assert world.people["mom"].seat == ("chair", "seat") and world.socket_world("chair", "seat") is None
    before = world.people["mom"].pos
    assert world.reseat_riders(["chair"], _seat_root(world)) == {"mom": None}
    assert world.people["mom"].pos == before


# ----------------------------------------------- riders of a vehicle and what a prop rests on

def stacked(world: World) -> World:
    """The wagon at the curb with the crate on its bed and the bench on the crate's top; kid on
    the bench's right seat, dad on its left one, mom on the wagon's seat; the chair on its own."""
    world.begin_shot(
        shot(start={"kid": {"at": "curb", "in": "bench.right_seat"}, "mom": {"at": "curb", "in": "wagon.seat"},
                    "dad": {"at": "curb", "in": "bench.left_seat"}},
             propStart={"wagon": {"at": "curb"}, "crate": {"on": "wagon.bed"}, "bench": {"on": "crate.top"},
                        "chair": {"at": "home"}}),
        MARKS, same_set_as_previous=False,
    )
    return world


def test_riders_lists_the_vehicles_own_seats_first_then_stacked_props_in_order(world: World):
    stacked(world)
    assert world.riders("wagon") == [
        ("wagon", "seat", "mom"),
        ("bench", "right_seat", "kid"),  # sockets in the order they were taken (start order)
        ("bench", "left_seat", "dad"),
    ]
    assert world._riders("wagon") == ["mom", "kid", "dad"]
    assert world.riders("crate") == [("bench", "right_seat", "kid"), ("bench", "left_seat", "dad")]
    assert world.riders("chair") == []
    # a puppet seated straight on a prop and one on top of another prop on it, in descendant order
    world.stand_up("kid")
    world.sit_on("kid", "bench", "right_seat")
    assert world.riders("wagon") == [("wagon", "seat", "mom"), ("bench", "left_seat", "dad"),
                                     ("bench", "right_seat", "kid")]
    raises("unknown_id", "boat is not a known prop", world.riders, "boat")


def test_riders_follow_descendants_depth_first(world: World):
    world.begin_shot(
        shot(start={"kid": {"at": "curb", "in": "chair.seat"}, "mom": {"at": "curb", "in": "bench.left_seat"}},
             propStart={"wagon": {"at": "curb"}, "crate": {"on": "wagon.bed"}, "chair": {"on": "crate.top"},
                        "bench": {"on": "wagon.seat"}}),
        MARKS, same_set_as_previous=False,
    )
    # the wagon's occupants in insertion order: crate (bed), then bench (seat); the crate's chair
    # comes before the bench, as World._descendants walks them
    assert list(world._descendants("wagon")) == ["crate", "chair", "bench"]
    assert world.riders("wagon") == [("chair", "seat", "kid"), ("bench", "left_seat", "mom")]


def test_supports_lists_what_a_prop_rests_on_nearest_first(world: World):
    stacked(world)
    assert world.supports("bench") == ["crate", "wagon"]
    assert world.supports("crate") == ["wagon"]
    assert world.supports("wagon") == [] and world.supports("chair") == []
    w = opening(World(PEOPLE, PROPS))
    assert w.supports("hymn_books") == []  # a held prop rests on nothing
    raises("unknown_id", "boat is not a known prop", world.supports, "boat")


def test_move_vehicle_carries_riders_of_stacked_props(world: World):
    stacked(world)
    before = {w: world.people[w].pos for w in ("kid", "dad", "mom")}
    world.move_vehicle("wagon", (0.3, 0.0, 0.5))
    for w in ("kid", "dad", "mom"):
        assert world.people[w].pos == pytest.approx((before[w][0] - 0.5, before[w][1], before[w][2]))


def test_check_rider_accepts_a_puppet_on_a_prop_stacked_on_the_vehicle(world: World):
    world.begin_shot(
        shot(start={"kid": {"at": "curb", "in": "chair.seat"}, "mom": {"at": "curb", "in": "sedan.driver"},
                    "dad": {"at": "home", "in": "bench.left_seat"}},
             propStart={"sedan": {"at": "curb"}, "crate": {"on": "sedan.back_seat"}, "chair": {"on": "crate.top"},
                        "bench": {"at": "home"}}),
        MARKS, same_set_as_previous=False,
    )
    world.check_rider("kid", "sedan")  # on the chair, on the crate, on the sedan
    world.check_rider("mom", "sedan")
    raises("not_in_vehicle", "dad is not in sedan", world.check_rider, "dad", "sedan")
    raises("not_in_vehicle", "kid is not in wagon", world.check_rider, "kid", "wagon")
    world.get_out("mom", "sedan")
    raises("no_driver", "nobody is driving sedan", world.check_rider, "kid", "sedan")
