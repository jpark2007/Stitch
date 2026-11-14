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
]

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
                    "mom": {"at": "steps_top", "holding": "hymn_books", "facing": "left"}}),
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
    raises("not_holding", "nobody is holding it", world.hand_to, "dad", "cup", "mom")
    world.put_on("dad", "hat")
    raises("not_holding", "dad is wearing it", world.hand_to, "dad", "hat", "mom")
    raises("not_holding", "dad is wearing it", world.drop, "mom", "hat", (0, 0, 0.5))
    world.begin_shot(shot(id="s2", start={"dad": {"at": "steps_foot"}, "mom": {"at": "steps_top"}},
                          propStart={"crate": {"at": "curb"}, "cup": {"on": "crate.top"}}),
                     MARKS, same_set_as_previous=True)
    raises("not_holding", "it is on crate.top", world.hand_to, "dad", "cup", "mom")


def test_hand_to_other_errors(world: World):
    opening(world)
    raises("self_hand_to", "mom cannot hand hymn_books to themselves", world.hand_to, "mom", "hymn_books", "mom")
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
    raises("bad_socket", "head is not a hand", world.pick_up, "mom", "lamp", "head")


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
             propStart={"sedan": {"at": "curb", "state": "parked"}}),
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
    parked(world)
    assert world.get_in("dad", "bench") == "back"
    assert world.get_in("mom", "bench") == "left_seat"


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
    raises("seated_vehicle", "dad is seated in sedan", world.pick_up, "dad", "cup")
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
    raises("bad_parent", "cannot place cup on puppet:mom.hand_r", world.place_on, "dad", "cup", "puppet:mom.hand_r", (0, 0, 0))
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
    assert world.holder("hymn_books") == ("dad", "hand_r")  # carried offstage, not dropped
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
    raises("double_hold", "dad and mom both start holding hymn_books", world.begin_shot,
           shot(id="s2", start={"dad": {"at": "home", "holding": "hymn_books"},
                                "mom": {"at": "curb", "holding": "hymn_books"}}),
           MARKS, False)
    raises("unknown_mark", "nowhere is not a mark in shot s2", world.begin_shot,
           shot(id="s2", start={"dad": {"at": "nowhere"}}), MARKS, False)
    raises("prop_conflict", "hymn_books is held by dad and also placed in prop_start", world.begin_shot,
           shot(id="s2", start={"dad": {"at": "home", "holding": "hymn_books"}},
                propStart={"hymn_books": {"at": "curb"}}), MARKS, True)
    raises("socket_taken", "dad and mom both start in sedan.driver", world.begin_shot,
           shot(id="s2", start={"dad": {"at": "home", "in": "sedan.driver"}, "mom": {"at": "home", "in": "sedan.driver"}}),
           MARKS, True)
    raises("unknown_socket", "sedan has no socket roof", world.begin_shot,
           shot(id="s2", start={"dad": {"at": "home", "in": "sedan.roof"}}), MARKS, True)
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
