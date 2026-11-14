"""Getting up or out by the stage edge, and the body and vehicle verbs at the edge.

A seat can overhang the stage edge while its prop stands on stage: the aisle end of a pew at the
back yard's gate, the passenger door of a car parked by the curb. Getting up from it (stand_up)
or out of it (get_out) used to leave the puppet past |x| = 1 with no error, so its next action
failed as if it had left the stage (fuzz bugs B8 and B9 on jonah/compiler-fuzz-sanity-bughunt).
Now the puppet takes a short step inward (verbs_body.dismount_step, motion.locomotion_keys, so
foot planting covers it) to DISMOUNT_EDGE_MARGIN inside the edge, on the same surface.

The rest is the edge bug hunt on verbs_body and verbs_vehicles:

- sitting down on (sit_on) or getting into (get_in) an overhanging seat keeps the puppet on stage
  while it sits, as a start block's ``in`` does (verbs_body.seated_world_pos), and so does
  driving such a car about on stage;
- driving a car past the edge (to offstage_right, say) takes its riders offstage with it, which
  is CONTRACTS' "offstage is beyond +-1": an exit. Getting out there leaves the puppet offstage
  (enter brings it on); a gesture from the seat names the fix that works (drive back on), since
  enter fails on a seated puppet;
- wave, point_at, kneel and hug at |x| = 1, and a hug between marks of different heights.

Every scenario is compiled twice, planted and with ``plant_feet=False``, and must pass
timeline_sanity.sanity_problems and test_foot_planting.planting_problems.
"""

from __future__ import annotations

import pytest

import schemas
from screenplay import verbs_body, verbs_vehicles
from screenplay.compiler import compile as compile_screenplay
from screenplay.effects import STAGE_EDGE
from screenplay.errors import hint_for
from screenplay.schema import CompileResult, Screenplay, Timeline
from screenplay.tracks import evaluate
from screenplay.verbs_body import DISMOUNT_EDGE_MARGIN
from story_scenes import STORIES, story_scene
from test_foot_planting import cycle_key_times, planting_problems
from timeline_sanity import sanity_problems
from verb_cases import CASES, CAST, PROPS, Scene, base_scene

INSIDE = STAGE_EDGE - DISMOUNT_EDGE_MARGIN  # where a dismount step ends, |x|


# --------------------------------------------------------------------------- helpers

def _shot(sid: str, set_id: str, start: dict, actions: list[dict], prop_start: dict | None = None,
          marks: dict | None = None) -> dict:
    shot = {"id": sid, "set": set_id, "start": start, "propStart": prop_start or {}, "actions": actions}
    if marks:
        shot["marks"] = marks
    return shot


def _sp(*shots: dict, cast: list[str] = CAST, props: list[str] = PROPS) -> Screenplay:
    return Screenplay.model_validate({"memoryId": "stage_edge", "cast": list(cast), "props": list(props),
                                      "shots": list(shots)})


def _story(*shots: dict, props: list[str]) -> tuple[Screenplay, Scene]:
    sets, prop_defs, people = story_scene()
    return _sp(*shots, cast=["dad", "mom"], props=props), Scene(sets, prop_defs, people)


def _compile(sp: Screenplay, scene: Scene | None = None, **kw) -> CompileResult:
    scene = scene or base_scene()
    return compile_screenplay(sp, sets=scene.sets, props=scene.props, people=scene.people, **kw)


def _errors(result: CompileResult) -> list[str]:
    return [f"{e.code}: {e.message}" for e in result.errors]


def _checked(sp: Screenplay, scene: Scene | None = None) -> tuple[Timeline, Timeline]:
    """(planted, unplanted) timelines; both compile with zero errors, are sane, and planting
    holds at every walk / run cycle key."""
    scene = scene or base_scene()
    planted = _compile(sp, scene)
    unplanted = _compile(sp, scene, plant_feet=False)
    assert planted.ok, _errors(planted)
    assert unplanted.ok, _errors(unplanted)
    assert sanity_problems(planted.timeline) == []
    assert sanity_problems(unplanted.timeline) == []
    builds = {pid: p.character.build for pid, p in scene.people.items()}
    assert planting_problems(planted.timeline, unplanted.timeline, builds) == []
    return planted.timeline, unplanted.timeline


def _root_keys(tl: Timeline, who: str) -> list[tuple[float, list[float]]]:
    return next(([(float(k[0]), list(k[1])) for k in tr.keys] for tr in tl.tracks
                 if (tr.target, tr.channel) == (f"puppet:{who}", "root.position")), [])


def _assert_stepped_in(tl: Timeline, who: str, side: int) -> float:
    """The puppet's root went past the edge on ``side`` (the overhanging seat), then stepped in
    to INSIDE and stayed within the band at every key after. Returns when the step ended."""
    keys = _root_keys(tl, who)
    past = [i for i, (_, v) in enumerate(keys) if abs(v[0]) > STAGE_EDGE]
    assert past, f"{who} never sat past the edge: {keys}"
    assert all(v[0] * side > 0 for _, v in (keys[i] for i in past))
    last = past[-1]
    assert last + 1 < len(keys), f"{who} ends past the edge: {keys}"
    t_in, landed = keys[last + 1]
    assert landed[0] == pytest.approx(side * INSIDE, abs=1e-3)
    for t, v in keys[last + 1:]:
        assert abs(v[0]) <= STAGE_EDGE + 1e-9, f"{who} root x {v[0]} past the edge at t={t}"
    return t_in


def _y_at(tl: Timeline, who: str, t: float) -> float:
    return float(evaluate(tl.tracks, f"puppet:{who}", "root.position", t)[1])


# A car parked by the right edge: its passenger seat (u = 0.6) overhangs it.
EDGE_RIGHT = {"edge": [0.92, 0.0]}  # x = 0.84
# A car parked by the left edge: its driver seat (u = 0.42) overhangs it.
EDGE_LEFT = {"edge": [0.06, 0.0]}   # x = -0.88


# ------------------------------------------------- the two fuzz reproductions (B8, B9)

def test_getting_out_of_a_car_by_the_edge_stays_on_stage():
    """Fuzz bug B8 (verbs_vehicles.apply_get_out): the passenger door of a car parked at x = 0.84
    is past x = 1; the next action failed 'dad is offstage; use enter first'."""
    sp = _sp(_shot("s1", "yard", {"dad": {"at": "edge", "in": "sedan.passenger"}},
                   [{"t": 1, "do": "wave", "who": "dad"}, {"t": 5, "do": "get_out", "who": "dad", "what": "sedan"},
                    {"t": 9, "do": "kneel", "who": "dad"}],
                   prop_start={"sedan": {"at": "edge"}}, marks=EDGE_RIGHT))
    result = _compile(sp)
    assert result.ok, _errors(result)
    planted, unplanted = _checked(sp)
    for tl in (planted, unplanted):
        _assert_stepped_in(tl, "dad", 1)


def test_standing_up_from_a_pew_by_the_edge_stays_on_stage():
    """Fuzz bug B9 (verbs_body.apply_stand_up): a two-seat pew at the back yard's gate
    (x = 0.84) has its aisle seat past x = 1."""
    sp, scene = _story(_shot("s1", "backyard", {"dad": {"at": "gate", "in": "pew.seat_aisle"}},
                             [{"t": 1, "do": "stand_up", "who": "dad"}, {"t": 5, "do": "kneel", "who": "dad"}],
                             prop_start={"pew": {"at": "gate"}}), props=["pew"])
    result = _compile(sp, scene)
    assert result.ok, _errors(result)
    planted, unplanted = _checked(sp, scene)
    for tl in (planted, unplanted):
        _assert_stepped_in(tl, "dad", 1)


# ------------------------------------------------------------ both edges, both doors

@pytest.mark.parametrize("seat, marks, side", [
    ("sedan.passenger", EDGE_RIGHT, 1),
    ("sedan.driver", EDGE_LEFT, -1),
], ids=["passenger_right_edge", "driver_left_edge"])
@pytest.mark.parametrize("then", ["kneel", "wave", "walk_to"])
def test_get_out_by_either_edge_steps_in_and_the_next_action_compiles(seat, marks, side, then):
    follow = {"t": 9, "do": then, "who": "dad"}
    if then == "walk_to":
        follow["to"] = "table_spot"
    sp = _sp(_shot("s1", "yard", {"dad": {"at": "edge", "in": seat}},
                   [{"t": 5, "do": "get_out", "who": "dad", "what": "sedan"}, follow],
                   prop_start={"sedan": {"at": "edge"}}, marks=marks))
    planted, unplanted = _checked(sp)
    for tl in (planted, unplanted):
        _assert_stepped_in(tl, "dad", side)


@pytest.mark.parametrize("seat, mark, side", [
    ("pew.seat_aisle", [0.92, 0.0], 1),    # x = 0.84, the aisle seat (v 0.7) overhangs the right edge
    ("pew.seat_wall", [0.08, 0.0], -1),    # x = -0.84, the wall seat (v 0.3) overhangs the left edge
], ids=["aisle_right_edge", "wall_left_edge"])
@pytest.mark.parametrize("then", ["kneel", "wave"])
def test_stand_up_by_either_edge_steps_in_and_the_next_action_compiles(seat, mark, side, then):
    sp, scene = _story(_shot("s1", "backyard", {"dad": {"at": "end", "in": seat}},
                             [{"t": 1, "do": "stand_up", "who": "dad"}, {"t": 5, "do": then, "who": "dad"}],
                             prop_start={"pew": {"at": "end"}}, marks={"end": mark}), props=["pew"])
    planted, unplanted = _checked(sp, scene)
    for tl in (planted, unplanted):
        _assert_stepped_in(tl, "dad", side)


def test_the_step_keeps_the_surface_height_of_a_raised_pew():
    """A pew on a riser 0.2 up by the edge: the step ends on the riser, not the ground."""
    sp, scene = _story(_shot("s1", "backyard", {"dad": {"at": "riser", "in": "pew.seat_aisle"}},
                             [{"t": 1, "do": "stand_up", "who": "dad"}, {"t": 5, "do": "wave", "who": "dad"}],
                             prop_start={"pew": {"at": "riser"}}, marks={"riser": [0.92, 0.2]}), props=["pew"])
    _, unplanted = _checked(sp, scene)
    t_in = _assert_stepped_in(unplanted, "dad", 1)
    assert _y_at(unplanted, "dad", t_in) == pytest.approx(0.2, abs=1e-3)
    assert _root_keys(unplanted, "dad")[-1][1] == pytest.approx([INSIDE, 0.2, 0.5], abs=1e-3)


def test_the_action_ends_after_the_step():
    """get_out / stand_up keep the puppet busy through the step: an action at the stand's end
    (POSE_SECONDS after it starts) is busy until the step is over."""
    car = _sp(_shot("s1", "yard", {"dad": {"at": "edge", "in": "sedan.passenger"}},
                    [{"t": 5, "do": "get_out", "who": "dad", "what": "sedan"}, {"t": 5.4, "do": "kneel", "who": "dad"}],
                    prop_start={"sedan": {"at": "edge"}}, marks=EDGE_RIGHT))
    assert _errors(_compile(car)) == [
        "busy: shot s1, t=5.4: dad cannot kneel; dad is still getting out until t=5.6"]
    pew, scene = _story(_shot("s1", "backyard", {"dad": {"at": "gate", "in": "pew.seat_aisle"}},
                              [{"t": 1, "do": "stand_up", "who": "dad"}, {"t": 1.4, "do": "wave", "who": "dad"}],
                              prop_start={"pew": {"at": "gate"}}), props=["pew"])
    assert _errors(_compile(pew, scene)) == [
        "busy: shot s1, t=1.4: dad cannot wave; dad is still standing up until t=1.63"]


def test_a_held_prop_stays_in_the_hand_through_the_step():
    sp, scene = _story(_shot("s1", "backyard", {"dad": {"at": "gate", "in": "pew.seat_aisle", "holding": "hymn_books"}},
                             [{"t": 1, "do": "stand_up", "who": "dad"}, {"t": 5, "do": "walk_to", "who": "dad", "to": "lawn_left"}],
                             prop_start={"pew": {"at": "gate"}}), props=["pew", "hymn_books"])
    planted, _ = _checked(sp, scene)
    t_in = _assert_stepped_in(planted, "dad", 1)
    arm = next(tr for tr in planted.tracks if (tr.target, tr.channel) == ("puppet:dad", "arm_r.upper.rot"))
    assert len({round(float(k[1]), 6) for k in arm.keys if float(k[0]) <= t_in}) == 1  # the hold pose throughout


def test_a_long_step_out_of_a_wide_vehicle_is_a_planted_walk():
    """A bus whose rear door overhangs the edge by 0.4: the step is a real walk with cycle keys,
    and the feet stay planted through it."""
    scene = base_scene()
    bus = schemas.Prop(id="sedan", family_id="verb_cases", name="bus", size="vehicle",
                       art={"body": None}, color="#e0b020", aspect=4.0, anchor=[0.5, 1.0],
                       sockets={"driver": [0.1, 0.55], "passenger": [0.95, 0.55]})
    scene.props["sedan"] = bus
    sp = _sp(_shot("s1", "yard", {"mom": {"at": "edge", "in": "sedan.passenger"}},
                   [{"t": 1, "do": "get_out", "who": "mom", "what": "sedan"}, {"t": 8, "do": "wave", "who": "mom"}],
                   prop_start={"sedan": {"at": "edge"}}, marks={"edge": [0.9, 0.0]}))
    planted, unplanted = _checked(sp, scene)
    t_in = _assert_stepped_in(planted, "mom", 1)
    keys = _root_keys(unplanted, "mom")
    assert max(v[0] for _, v in keys) - INSIDE > 0.3  # a long step
    assert [t for t in cycle_key_times(planted, "puppet:mom") if t <= t_in]  # cycle keys: planting applies


# --------------------------------------------- inside the band: exactly as before

def _spy(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """Record every time the edge handling changes anything (a step taken, a seated position
    held at the edge); the verbs otherwise run unchanged."""
    fired: list[tuple[str, str]] = []
    real_step, real_pos = verbs_body.dismount_step, verbs_body.seated_world_pos

    def step(world, who, *rest):  # noqa: ANN001
        out = real_step(world, who, *rest)
        if out[0]:
            fired.append(("step", who))
        return out

    def pos(p, seat_x):  # noqa: ANN001
        out = real_pos(p, seat_x)
        if out != p:
            fired.append(("held", repr(p)))
        return out

    for module in (verbs_body, verbs_vehicles):
        monkeypatch.setattr(module, "dismount_step", step)
        monkeypatch.setattr(module, "seated_world_pos", pos)
    return fired


def _without_edge_handling(monkeypatch: pytest.MonkeyPatch) -> None:
    """Today's behaviour before the edge handling: no step, the seat's position as it is."""
    def no_step(world, who, t, stand_at, seat_x, facing):  # noqa: ANN001
        return [], t, stand_at, facing

    for module in (verbs_body, verbs_vehicles):
        monkeypatch.setattr(module, "dismount_step", no_step)
        monkeypatch.setattr(module, "seated_world_pos", lambda p, seat_x: p)


DISMOUNT_CASES = [c for c in CASES if c.verb in ("get_out", "stand_up") and c.ok]


def _dump(sp: Screenplay, scene: Scene) -> str:
    return _compile(sp, scene).model_dump_json()


@pytest.mark.parametrize("case", DISMOUNT_CASES, ids=[f"{c.verb}:{c.name}" for c in DISMOUNT_CASES])
def test_a_seat_inside_the_band_compiles_exactly_as_before(case, monkeypatch):
    fired = _spy(monkeypatch)
    now = _dump(case.screenplay, case.scene)
    assert fired == []
    monkeypatch.undo()
    _without_edge_handling(monkeypatch)
    assert _dump(case.screenplay, case.scene) == now


@pytest.mark.parametrize("name", sorted(STORIES))
def test_every_story_compiles_exactly_as_before(name, monkeypatch):
    sets, props, people = story_scene()
    scene = Scene(sets, props, people)
    sp = Screenplay.model_validate(STORIES[name])
    fired = _spy(monkeypatch)
    now = _dump(sp, scene)
    assert fired == []
    monkeypatch.undo()
    _without_edge_handling(monkeypatch)
    assert _dump(sp, scene) == now


def test_there_are_get_out_and_stand_up_cases_to_compare():
    assert {c.verb for c in DISMOUNT_CASES} == {"get_out", "stand_up"}


def test_a_seat_just_inside_the_edge_takes_no_step(monkeypatch):
    """The driver's seat of the car parked by the right edge is inside the band: no step."""
    fired = _spy(monkeypatch)
    sp = _sp(_shot("s1", "yard", {"dad": {"at": "edge", "in": "sedan.driver"}},
                   [{"t": 5, "do": "get_out", "who": "dad", "what": "sedan"}, {"t": 9, "do": "kneel", "who": "dad"}],
                   prop_start={"sedan": {"at": "edge"}}, marks=EDGE_RIGHT))
    planted, _ = _checked(sp)
    assert fired == []
    assert all(abs(v[0]) <= STAGE_EDGE for _, v in _root_keys(planted, "dad"))


# ----------------------------------------------- sitting down on an overhanging seat

def test_sit_on_an_overhanging_pew_seat_keeps_the_puppet_on_stage():
    """sit_on walks to the aisle seat past the edge; seated there, dad is still on stage (a
    start block's `in` would say so), so he can wave, then stands up and steps back in."""
    sp, scene = _story(_shot("s1", "backyard", {"dad": {"at": "lawn_right"}},
                             [{"t": 1, "do": "sit_on", "who": "dad", "what": "pew", "socket": "seat_aisle"},
                              {"t": 6, "do": "wave", "who": "dad"}, {"t": 9, "do": "stand_up", "who": "dad"},
                              {"t": 13, "do": "point_at", "who": "dad", "target": "porch_steps"}],
                             prop_start={"pew": {"at": "gate"}}), props=["pew"])
    planted, unplanted = _checked(sp, scene)
    for tl in (planted, unplanted):
        _assert_stepped_in(tl, "dad", 1)


def test_get_in_at_an_overhanging_door_keeps_the_puppet_on_stage():
    sp = _sp(_shot("s1", "yard", {"mom": {"at": "door"}},
                   [{"t": 1, "do": "get_in", "who": "mom", "what": "sedan", "socket": "passenger"},
                    {"t": 9, "do": "wave", "who": "mom"}, {"t": 12, "do": "get_out", "who": "mom", "what": "sedan"},
                    {"t": 15, "do": "lean_toward", "who": "mom", "target": "door"}],
                   prop_start={"sedan": {"at": "edge"}}, marks=EDGE_RIGHT))
    planted, unplanted = _checked(sp)
    for tl in (planted, unplanted):
        _assert_stepped_in(tl, "mom", 1)


def test_driving_along_the_edge_keeps_an_overhanging_passenger_on_stage():
    """mom gets in by the edge and dad drives a little further right, the car still on stage:
    mom (her seat past x = 1) can still wave, then gets out and steps in."""
    sp = _sp(_shot("s1", "yard", {"dad": {"at": "edge", "in": "sedan.driver"}, "mom": {"at": "door"}},
                   [{"t": 1, "do": "get_in", "who": "mom", "what": "sedan", "socket": "passenger"},
                    {"t": 9, "do": "drive_to", "who": "dad", "what": "sedan", "to": "nearer"},
                    {"t": 15, "do": "wave", "who": "mom"}, {"t": 18, "do": "get_out", "who": "mom", "what": "sedan"},
                    {"t": 22, "do": "wave", "who": "mom"}],
                   prop_start={"sedan": {"at": "edge"}}, marks={"edge": [0.9, 0.0], "nearer": [0.94, 0.0]}))
    planted, unplanted = _checked(sp)
    for tl in (planted, unplanted):
        _assert_stepped_in(tl, "mom", 1)


# ---------------------------------------------- driving off: an exit, consistently

STREET = {"dad": {"at": "garage", "in": "sedan.driver"}, "mom": {"at": "garage", "in": "sedan.passenger"}}
GARAGE = {"sedan": {"at": "garage"}}


def test_driving_offstage_takes_the_riders_off_and_a_gesture_names_the_fix():
    """CONTRACTS: offstage is beyond +-1. A car driven to offstage_right takes its riders off
    with it. Waving from the seat there fails; the reason says to bring the car back, since
    enter fails on a seated puppet."""
    drive = {"t": 1, "do": "drive_to", "who": "dad", "what": "sedan", "to": "offstage_right"}
    wave = _sp(_shot("s1", "street", STREET, [drive, {"t": 9, "do": "wave", "who": "dad"}], prop_start=GARAGE))
    assert _errors(_compile(wave)) == [
        "offstage: shot s1, t=9: dad cannot wave; dad is offstage in sedan.driver; drive_to or ride_to a mark on stage first"]
    enter = _sp(_shot("s1", "street", STREET, [drive, {"t": 9, "do": "enter", "who": "dad", "to": "corner"}],
                      prop_start=GARAGE))
    assert _errors(_compile(enter)) == [
        "seated: shot s1, t=9: dad cannot enter; dad is seated in sedan.driver; use get_out first"]
    for verb, extra in (("point_at", {"target": "corner"}), ("lean_toward", {"target": "corner"})):
        sp = _sp(_shot("s1", "street", STREET, [drive, {"t": 9, "do": verb, "who": "mom", **extra}], prop_start=GARAGE))
        assert _errors(_compile(sp)) == [
            f"offstage: shot s1, t=9: mom cannot {verb}; mom is offstage in sedan.passenger; "
            "drive_to or ride_to a mark on stage first"]


def test_ride_to_offstage_is_the_same_exit():
    sp = _sp(_shot("s1", "street", STREET,
                   [{"t": 1, "do": "ride_to", "who": "mom", "what": "sedan", "to": "offstage_left"},
                    {"t": 9, "do": "wave", "who": "mom"}], prop_start=GARAGE))
    assert _errors(_compile(sp)) == [
        "offstage: shot s1, t=9: mom cannot wave; mom is offstage in sedan.passenger; drive_to or ride_to a mark on stage first"]


def test_driving_back_on_brings_the_riders_back():
    sp = _sp(_shot("s1", "street", STREET,
                   [{"t": 1, "do": "drive_to", "who": "dad", "what": "sedan", "to": "offstage_right"},
                    {"t": 9, "do": "drive_to", "who": "dad", "what": "sedan", "to": "corner"},
                    {"t": 16, "do": "wave", "who": "mom"}, {"t": 19, "do": "get_out", "who": "dad", "what": "sedan"},
                    {"t": 22, "do": "wave", "who": "dad"}], prop_start=GARAGE))
    _checked(sp)


def test_getting_out_of_a_car_offstage_leaves_the_puppet_offstage_until_it_enters():
    """The car itself is offstage, so getting out is not an entrance: no step, and enter brings
    dad on."""
    sp = _sp(_shot("s1", "street", STREET,
                   [{"t": 1, "do": "drive_to", "who": "dad", "what": "sedan", "to": "offstage_right"},
                    {"t": 9, "do": "get_out", "who": "dad", "what": "sedan"},
                    {"t": 12, "do": "enter", "who": "dad", "to": "corner", "from": "offstage_right"},
                    {"t": 18, "do": "wave", "who": "dad"}], prop_start=GARAGE))
    _, unplanted = _checked(sp)
    xs = [round(v[0], 3) for t, v in _root_keys(unplanted, "dad")]
    assert INSIDE not in xs  # no dismount step: get_out ends offstage, enter starts there
    assert min(xs[xs.index(0.6) - 2:xs.index(0.6)]) > STAGE_EDGE
    wave = _sp(_shot("s1", "street", STREET,
                     [{"t": 1, "do": "drive_to", "who": "dad", "what": "sedan", "to": "offstage_right"},
                      {"t": 9, "do": "get_out", "who": "dad", "what": "sedan"}, {"t": 12, "do": "wave", "who": "dad"}],
                     prop_start=GARAGE))
    assert _errors(_compile(wave)) == ["offstage: shot s1, t=12: dad cannot wave; dad is offstage; use enter first"]


def test_a_car_parked_just_past_the_edge_lets_a_door_inside_the_band_out_on_stage():
    """Anchor at x = 1.04 (offstage) but the driver's door at about x = 0.97: dad gets out on
    stage with no step."""
    sp = _sp(_shot("s1", "street", {"dad": {"at": "past", "in": "sedan.driver"}},
                   [{"t": 1, "do": "get_out", "who": "dad", "what": "sedan"}, {"t": 5, "do": "wave", "who": "dad"}],
                   prop_start={"sedan": {"at": "past"}}, marks={"past": [1.02, 0.0]}))
    planted, _ = _checked(sp)
    end = _root_keys(planted, "dad")[-1][1]
    assert abs(end[0]) <= STAGE_EDGE and round(end[0], 3) != INSIDE


# -------------------------------------------- gestures, kneel and hug at |x| = 1

@pytest.mark.parametrize("u", [0.0, 0.005, 0.995, 1.0], ids=["x=-1", "x=-0.99", "x=0.99", "x=1"])
def test_body_verbs_at_the_very_edge_compile(u):
    other = 0.5
    sp = _sp(_shot("s1", "yard", {"dad": {"at": "e"}, "mom": {"at": "o"}},
                   [{"t": 1, "do": "wave", "who": "dad", "target": "offstage_right"},
                    {"t": 3, "do": "point_at", "who": "dad", "target": "mom"},
                    {"t": 5, "do": "lean_toward", "who": "dad", "target": "offstage_left"},
                    {"t": 7, "do": "kneel", "who": "dad", "dur": 1.5},
                    {"t": 10, "do": "hug", "who": "mom", "target": "dad"},
                    {"t": 16, "do": "hug", "who": "dad", "target": "mom"}],
                   marks={"e": [u, 0.0], "o": [other, 0.0]}))
    _checked(sp)


@pytest.mark.parametrize("h", [0.1, 0.3])
@pytest.mark.parametrize("far", [False, True], ids=["side_by_side", "walk_up"])
def test_hug_between_marks_of_different_heights(h, far):
    """mom on a riser h up. Side by side, they hug where they stand; from across the yard dad
    walks up onto her surface (her standing height, not his), then walks back down to the door."""
    sp = _sp(_shot("s1", "yard", {"dad": {"at": "low"}, "mom": {"at": "high"}},
                   [{"t": 1, "do": "hug", "who": "dad", "target": "mom"},
                    {"t": 6, "do": "walk_to", "who": "dad", "to": "door"}],
                   marks={"low": [0.2 if far else 0.45, 0.0], "high": [0.6 if far else 0.5, h]}))
    _, unplanted = _checked(sp)
    keys = _root_keys(unplanted, "dad")
    hug_at = [v for t, v in keys if v[1] > 1e-6]
    if far:
        assert hug_at and hug_at[-1][1] == pytest.approx(h, abs=1e-3)
    assert keys[-1][1] == pytest.approx([-0.8, 0.0, 0.5], abs=1e-3)


# ------------------------------------------------------ known gaps elsewhere (strict xfail)

@pytest.mark.xfail(strict=True, raises=AssertionError, reason=(
    "errors.py hint_for / HINT_VARIANTS['offstage']: the offstage code's only hint is "
    "'add an enter for {subject} before this {do}', so a rider in a car driven offstage ('dad is "
    "offstage in sedan.driver; drive_to or ride_to a mark on stage first') gets the hint 'add an "
    "enter for dad before this wave', and that enter fails ('dad is seated in sedan.driver; use "
    "get_out first'). HINT_VARIANTS['offstage'] needs a variant for r'is offstage in \\S+; drive_to'."
))
def test_the_hint_for_a_rider_offstage_does_not_say_enter():
    sp = _sp(_shot("s1", "street", STREET,
                   [{"t": 1, "do": "drive_to", "who": "dad", "what": "sedan", "to": "offstage_right"},
                    {"t": 9, "do": "wave", "who": "dad"}], prop_start=GARAGE))
    (error,) = _compile(sp).errors
    hint = hint_for(error)
    assert hint is not None and "enter" not in hint
