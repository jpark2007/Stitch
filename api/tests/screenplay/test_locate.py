"""make_locate: person, prop, mark, held prop, prop on a prop, unknown, unplaced; and the
puppet geometry helpers the verb handlers share."""

import pytest

import schemas
from screenplay import ik
from screenplay.locate import (
    arm_of_hand,
    body_at,
    bone_at,
    bones_at,
    gesture_arm,
    held_arm_bones,
    held_arms,
    make_locate,
    mark_of,
    puppet_root,
    puppet_socket_at,
)
from screenplay.motion import resolve_marks
from screenplay.schema import Shot
from screenplay.state import StateError, World
from screenplay.tracks import TrackSet


def person(pid, build="average"):
    return schemas.Person(id=pid, family_id="f", name=pid, character={"color": "#123456", "build": build})


def prop(pid, size="hand", sockets=None):
    return schemas.Prop(id=pid, name=pid, size=size, art={"body": None}, grip=[0.5, 0.0], sockets=sockets or {})


MARKS = resolve_marks({"steps_top": [0.6, 0.3], "steps_foot": [0.5, 0.0], "porch": [0.9, 0.0, 0.3]}, {})


@pytest.fixture
def scene():
    world = World(
        [person("dad", "tall"), person("mom")],
        [prop("books"), prop("cup"), prop("table", "furniture", {"top": [0.5, 0.0]}), prop("ghost")],
    )
    shot = Shot.model_validate({
        "id": "s1", "set": "x",
        "start": {"dad": {"at": "steps_foot", "facing": "right"}, "mom": {"at": "steps_top", "holding": "books", "facing": "left"}},
        "propStart": {"table": {"at": "porch"}, "cup": {"on": "table.top"}},
    })
    world.begin_shot(shot, MARKS, False)
    return world, TrackSet()


def test_person_uses_world_position_without_tracks(scene):
    world, tracks = scene
    loc = make_locate(world, tracks, MARKS, "s1")
    assert loc("dad", 3.0) == (0.0, 0.0, 0.5)


def test_person_follows_root_track(scene):
    world, tracks = scene
    tracks.add("puppet:dad", "root.position", 1.0, [0.0, 0.0, 0.5])
    tracks.add("puppet:dad", "root.position", 3.0, [0.4, 0.0, 0.5])
    loc = make_locate(world, tracks, MARKS, "s1")
    assert loc("dad", 2.0) == pytest.approx((0.2, 0.0, 0.5))
    assert loc("dad", 9.0) == pytest.approx((0.4, 0.0, 0.5))


def test_mark(scene):
    world, tracks = scene
    loc = make_locate(world, tracks, MARKS, "s1")
    assert loc("steps_top", 0) == pytest.approx((0.2, 0.3, 0.5))
    assert loc("offstage_right", 0) == pytest.approx((1.3, 0.0, 0.5))
    assert loc("porch", 0) == pytest.approx((0.8, 0.0, 0.3))
    assert loc.mark("porch") == pytest.approx((0.8, 0.0, 0.3))


def test_ground_prop_is_its_world_anchor(scene):
    world, tracks = scene
    loc = make_locate(world, tracks, MARKS, "s1")
    assert loc("table", 0) == pytest.approx((0.8, 0.0, 0.3))


def test_ground_prop_follows_its_position_track(scene):
    world, tracks = scene
    tracks.add("prop:table", "parent", 0.0, "ground")
    tracks.add("prop:table", "position", 0.0, [0.8, 0.0, 0.3])
    tracks.add("prop:table", "position", 2.0, [0.0, 0.0, 0.3])
    loc = make_locate(world, tracks, MARKS, "s1")
    assert loc("table", 1.0) == pytest.approx((0.4, 0.0, 0.3))


def test_prop_on_a_prop_is_the_socket(scene):
    world, tracks = scene
    loc = make_locate(world, tracks, MARKS, "s1")
    # table: furniture 0.4 tall, anchor [0.5, 1.0]; its top socket [0.5, 0.0] is 0.4 above the anchor
    assert loc("cup", 0) == pytest.approx((0.8, 0.4, 0.3))
    assert world.props["cup"].world_pos == pytest.approx((0.8, 0.4, 0.3))
    tracks.add("prop:table", "parent", 0.0, "ground")
    tracks.add("prop:table", "position", 0.0, [0.0, 0.0, 0.5])
    tracks.add("prop:cup", "parent", 0.0, "prop:table.top")
    tracks.add("prop:cup", "position", 0.0, [0.0, 0.0, 0.0])
    assert loc("cup", 1.0) == pytest.approx((0.0, 0.4, 0.5))


def test_held_prop_is_the_holders_hand(scene):
    world, tracks = scene
    loc = make_locate(world, tracks, MARKS, "s1")
    # no tracks: arms hang straight down
    hx, hy = ik.socket_world("hand_r", {}, (0.2, 0.3), -1, "average")
    assert loc("books", 0) == pytest.approx((hx, hy, 0.5))
    # with tracks: the hold pose and the parent track
    tracks.add("puppet:mom", "arm_r.upper.rot", 0.0, 0.3)
    tracks.add("puppet:mom", "arm_r.lower.rot", 0.0, 1.2)
    tracks.add("prop:books", "parent", 0.0, "puppet:mom.hand_r")
    tracks.add("prop:books", "position", 0.0, [0.0, 0.0, 0.0])
    hx, hy = ik.socket_world("hand_r", {"arm_r.upper": 0.3, "arm_r.lower": 1.2}, (0.2, 0.3), -1, "average")
    assert loc("books", 1.0) == pytest.approx((hx, hy, 0.5))
    assert puppet_socket_at(world, tracks, "mom", "hand_r", 1.0) == pytest.approx((hx, hy, 0.5))


def test_held_prop_location_changes_with_parent_track(scene):
    world, tracks = scene
    tracks.add("prop:books", "parent", 0.0, "puppet:mom.hand_r")
    tracks.add("prop:books", "position", 0.0, [0.0, 0.0, 0.0])
    tracks.add("prop:books", "parent", 5.0, "ground")
    tracks.add("prop:books", "position", 5.0, [0.12, 0.3, 0.5])
    loc = make_locate(world, tracks, MARKS, "s1")
    assert loc("books", 6.0) == pytest.approx((0.12, 0.3, 0.5))


def test_precedence_person_then_prop_then_mark(scene):
    world, tracks = scene
    marks = {**MARKS, "dad": (0.9, 0.0, 0.5), "table": (-0.9, 0.0, 0.5)}
    loc = make_locate(world, tracks, marks, "s1")
    assert loc("dad", 0) == (0.0, 0.0, 0.5)
    assert loc("table", 0) == pytest.approx((0.8, 0.0, 0.3))


def test_unknown_name(scene):
    world, tracks = scene
    loc = make_locate(world, tracks, MARKS, "s1")
    with pytest.raises(StateError) as e:
        loc("gran", 0)
    assert (e.value.code, e.value.reason) == ("unknown_target", "gran is not a person, prop or mark in shot s1")
    with pytest.raises(StateError) as e:
        loc.mark("dad")
    assert (e.value.code, e.value.reason) == ("unknown_mark", "dad is not a mark in shot s1")


def test_unplaced_prop_is_not_on_stage(scene):
    world, tracks = scene
    loc = make_locate(world, tracks, MARKS, "s1")
    with pytest.raises(StateError) as e:
        loc("ghost", 0)
    assert (e.value.code, e.value.reason) == ("not_on_stage", "ghost is not on stage")


def _new_set(world, tracks):
    """What compile() does at a cut to a set that does not bring the table along: state.py
    takes it off the stage, and the start block parks its tracks at offstage_left."""
    tracks.add("prop:table", "parent", 0.0, "ground")
    tracks.add("prop:table", "position", 0.0, [0.8, 0.0, 0.3])
    tracks.add("prop:table", "position", 5.0, [0.8, 0.0, 0.3])
    tracks.add("prop:table", "position", 5.0, [-1.3, 0.0, 0.5])
    shot = Shot.model_validate({"id": "s2", "set": "y", "start": {"dad": {"at": "steps_foot"}}})
    world.begin_shot(shot, MARKS, False)
    assert world.is_unplaced("table")


def test_a_prop_the_previous_set_left_behind_is_not_on_stage(scene):
    world, tracks = scene
    _new_set(world, tracks)
    loc = make_locate(world, tracks, MARKS, "s2")
    for t in (5.0, 6.0, 9.0):  # its parked tracks would say offstage_left
        with pytest.raises(StateError) as e:
            loc("table", t)
        assert (e.value.code, e.value.reason) == ("not_on_stage", "table is not on stage")
    with pytest.raises(StateError) as e:
        loc.prop("table", 6.0)
    assert e.value.code == "not_on_stage"


def test_a_prop_on_stage_at_the_shot_start_still_resolves_from_its_tracks(scene):
    """The not-on-stage check is a snapshot at the shot's start: a prop that was on stage then
    and left later is still placed from its tracks (the end-of-shot camera pass looks back)."""
    world, tracks = scene
    tracks.add("prop:table", "parent", 0.0, "ground")
    tracks.add("prop:table", "position", 0.0, [0.8, 0.0, 0.3])
    loc = make_locate(world, tracks, MARKS, "s1")
    st = world.props["table"]
    st.world_pos = None  # it leaves the stage during the shot
    assert world.is_unplaced("table")
    assert loc("table", 1.0) == pytest.approx((0.8, 0.0, 0.3))


def test_a_left_behind_prop_placed_again_resolves_normally(scene):
    world, tracks = scene
    _new_set(world, tracks)
    world.begin_shot(Shot.model_validate({"id": "s3", "set": "z", "propStart": {"table": {"at": "steps_foot"}}}),
                     MARKS, False)
    tracks.add("prop:table", "position", 10.0, [0.0, 0.0, 0.5])
    loc = make_locate(world, tracks, MARKS, "s3")
    assert loc("table", 11.0) == pytest.approx((0.0, 0.0, 0.5))


# ------------------------------------------------------- through compile(): set changes

def _compile_two_sets(s2_actions=(), s2_camera=(), s2_prop_start=None):
    from verb_cases import base_scene

    from screenplay.compiler import compile as compile_screenplay
    from screenplay.schema import Screenplay

    sp = Screenplay.model_validate({"memoryId": "m", "cast": ["dad", "mom"], "props": ["hymn_books"], "shots": [
        {"id": "s1", "set": "yard", "start": {"dad": {"at": "door"}}, "propStart": {"hymn_books": {"at": "bench_spot"}},
         "actions": [{"t": 0, "do": "turn_to", "who": "dad", "target": "hymn_books"}]},
        {"id": "s2", "set": "street", "start": {"dad": {"at": "garage"}}, "propStart": s2_prop_start or {},
         "camera": list(s2_camera), "actions": list(s2_actions)},
    ]})
    sc = base_scene()
    return compile_screenplay(sp, sets=sc.sets, props=sc.props, people=sc.people)


def test_turn_to_a_prop_the_previous_set_left_behind_is_an_exact_error():
    res = _compile_two_sets([{"t": 0, "do": "turn_to", "who": "dad", "target": "hymn_books"}])
    assert not res.ok
    assert [(e.code, e.message) for e in res.errors] == [
        ("not_on_stage", "shot s2, t=0: dad cannot turn_to; hymn_books is not on stage"),
    ]


def test_point_at_a_prop_the_previous_set_left_behind_is_an_exact_error():
    res = _compile_two_sets([{"t": 0, "do": "point_at", "who": "dad", "target": "hymn_books"}])
    assert [e.message for e in res.errors] == ["shot s2, t=0: dad cannot point_at; hymn_books is not on stage"]


def test_camera_close_up_on_a_prop_the_previous_set_left_behind_is_an_exact_error():
    res = _compile_two_sets(s2_camera=[{"t": 0, "move": "close_up", "target": "hymn_books"}])
    assert not res.ok
    assert [(e.code, e.message) for e in res.errors] == [
        ("bad_camera", 'shot s2, t=0: cannot close_up "hymn_books"; hymn_books is not on stage'),
    ]


def test_a_prop_the_new_shot_places_resolves_normally():
    res = _compile_two_sets([{"t": 0, "do": "turn_to", "who": "dad", "target": "hymn_books"}],
                            [{"t": 0, "move": "close_up", "target": "hymn_books"}],
                            {"hymn_books": {"at": "corner"}})
    assert res.ok, [e.message for e in res.errors]


def test_mark_of_works_with_a_plain_callable():
    assert mark_of(lambda name, t: (1.0, 2.0, 3.0), "anything", 0) == (1.0, 2.0, 3.0)


def test_geometry_helpers(scene):
    world, tracks = scene
    assert puppet_root(world, tracks, "mom", 0) == pytest.approx((0.2, 0.3, 0.5))
    assert bone_at(tracks, "mom", "head", 0) == 0.0
    tracks.add("puppet:mom", "head.rot", 1.0, -0.4)
    assert bone_at(tracks, "mom", "head", 2.0) == -0.4
    assert bones_at(tracks, "mom", 2.0) == {"head": -0.4}
    assert len(bones_at(tracks, "mom", 2.0, all_bones=True)) == len(ik.RIG["bones"])
    b = body_at(world, tracks, "mom", 0)
    assert (b.id, b.facing, b.build) == ("mom", -1, "average") and b.ground == pytest.approx((0.2, 0.3))
    assert arm_of_hand("hand_l") == "arm_l"
    assert held_arms(world, "mom") == ["arm_r"] and held_arm_bones(world, "mom") == {"arm_r.upper", "arm_r.lower"}
    assert gesture_arm(world, "mom") == "arm_l"
    assert gesture_arm(world, "dad") == "arm_r"
