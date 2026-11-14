"""Demo-path verb handlers, called directly with a World and a TrackSet.

Every verb has success and error tests, and every error path is checked to be atomic: the world
snapshot and the tracks are unchanged after a failed handler.
"""

import copy
import math

import pytest

import schemas
from screenplay import body as B
from screenplay import ik, verbs_core
from screenplay.compiler import compile as compile_screenplay
from screenplay.locate import make_locate, puppet_root, puppet_socket_at
from screenplay.motion import resolve_marks
from screenplay.schema import Action, Screenplay, Shot
from screenplay.state import StateError, World
from screenplay.tracks import TrackSet, evaluate
from screenplay.verbs_core import HANDLERS
from verb_cases import base_scene

ACTION_START = 1.2


def person(pid, build="average", notes=None):
    return schemas.Person(
        id=pid, family_id="f", name=pid, character={"color": "#123456", "build": build, "notes": notes}
    )


def prop(pid, size="hand", sockets=None):
    return schemas.Prop(
        id=pid, name=pid, size=size, art={"body": None}, anchor=[0.5, 1.0], grip=[0.5, 0.0], sockets=sockets or {}
    )


MARKS = resolve_marks(
    {"steps_left": [0.2, 0.0], "steps_foot": [0.5, 0.0], "steps_top": [0.6, 0.3], "curb": [0.9, 0.0],
     "far": [0.95, 0.0], "high": [0.5, 1.5]},
    {},
)


def scene(start=None, prop_start=None, notes=None):
    """`notes` maps a person id to their CharacterSheet.notes (everyone else has none)."""
    notes = notes or {}
    world = World(
        [person("dad", "tall", notes.get("dad")), person("mom", notes=notes.get("mom")),
         person("kid", "small", notes.get("kid"))],
        [prop("books"), prop("cup"), prop("hat"), prop("chair", "furniture", {"seat": [0.5, 0.5]})],
    )
    shot = Shot.model_validate({
        "id": "s1", "set": "x",
        "start": start if start is not None else {
            "dad": {"at": "offstage_left", "facing": "right"},
            "mom": {"at": "steps_top", "holding": "books", "facing": "left"},
        },
        "propStart": prop_start or {},
    })
    world.begin_shot(shot, MARKS, False)
    return world, TrackSet()


def run(world, tracks, t, **action):
    """Call the handler like the compiler does: world.now is shot time, t is timeline time."""
    a = Action.model_validate({"t": t, **action})
    world.now = round(t, 6)
    return HANDLERS[a.do](world, tracks, a, ACTION_START + t, ACTION_START, make_locate(world, tracks, MARKS, "s1"))


def keys(tracks, target, channel):
    for tr in tracks.tracks():
        if (tr.target, tr.channel) == (target, channel):
            return [(k[0], k[1]) for k in tr.keys]
    return []


def frozen(world, tracks):
    return copy.deepcopy(world.snapshot()), [tr.model_dump() for tr in tracks.tracks()]


def fails(world, tracks, code, t=1.0, **action):
    """Run an action that must fail with `code`, and check nothing changed."""
    world.now = round(t, 6)  # the compiler sets this before every call
    before = frozen(world, tracks)
    with pytest.raises((StateError, ik.OutOfReach)) as e:
        run(world, tracks, t, **action)
    after = frozen(world, tracks)
    assert after == before, "a failed handler changed the world or the tracks"
    got = e.value.code if isinstance(e.value, StateError) else "out_of_reach"
    assert got == code, e.value
    return e.value


def test_handlers_are_exactly_the_core_verbs():
    assert sorted(HANDLERS) == sorted(["enter", "walk_to", "run_to", "pose", "pick_up", "hold", "drop", "hand_to"])


# ---------------------------------------------------------------------------- enter

def test_enter_walks_on_from_the_named_side():
    world, tracks = scene()
    end = run(world, tracks, 0, who="dad", do="enter", **{"from": "offstage_left"}, to="steps_left")
    assert end == pytest.approx(1.2 + 0.7 / 0.35)
    assert keys(tracks, "puppet:dad", "root.position") == [(1.2, [-1.3, 0.0, 0.5]), (3.2, [-0.6, 0.0, 0.5])]
    assert keys(tracks, "puppet:dad", "root.facing") == []  # already facing right
    assert len(keys(tracks, "puppet:dad", "leg_l.upper.rot")) > 2  # walk cycle
    assert world.people["dad"].pos == pytest.approx((-0.6, 0.0, 0.5))


def test_enter_defaults_to_the_nearest_side_and_turns():
    world, tracks = scene()
    end = run(world, tracks, 0, who="dad", do="enter", to="curb")
    # curb is at x=0.8, so dad comes on from offstage_right (x=1.3) walking left
    pos = keys(tracks, "puppet:dad", "root.position")
    assert pos[0] == (1.2, [1.3, 0.0, 0.5]) and pos[-1][1] == [0.8, 0.0, 0.5]
    assert keys(tracks, "puppet:dad", "root.facing") == [(1.2, -1)]
    assert end == pytest.approx(1.2 + 0.5 / 0.35)
    assert world.people["dad"].facing == -1


def test_enter_errors():
    world, tracks = scene()
    e = fails(world, tracks, "already_on_stage", who="mom", do="enter", to="curb")
    assert e.reason == "mom is already on stage; use walk_to"
    e = fails(world, tracks, "unknown_mark", who="dad", do="enter", to="porch")
    assert e.reason == "porch is not a mark in shot s1"
    fails(world, tracks, "unknown_person", who="gran", do="enter", to="curb")


# -------------------------------------------------------------------------- walk/run

def test_walk_to_reproduces_the_sample_walk():
    world, tracks = scene({"dad": {"at": "steps_left", "facing": "right"}})
    end = run(world, tracks, 3.6, who="dad", do="walk_to", to="steps_foot")
    assert end == pytest.approx(4.8 + 0.6 / 0.35)
    assert keys(tracks, "puppet:dad", "root.position") == [(4.8, [-0.6, 0.0, 0.5]), (6.514, [0.0, 0.0, 0.5])]
    assert world.people["dad"].pos == pytest.approx((0.0, 0.0, 0.5))


def test_walk_keeps_a_holding_arm_in_the_hold_pose():
    world, tracks = scene()
    run(world, tracks, 0, who="mom", do="walk_to", to="steps_foot")
    assert keys(tracks, "puppet:mom", "arm_r.upper.rot") == []
    assert keys(tracks, "puppet:mom", "arm_l.upper.rot") != []
    assert keys(tracks, "puppet:mom", "root.facing") == []  # steps_foot is to her left; she faces left


def test_walk_from_kneeling_stands_up_first():
    world, tracks = scene({"dad": {"at": "steps_left", "facing": "right", "pose": "kneel"}})
    low = -0.24 * ik.scale("tall")
    tracks.add("puppet:dad", "root.position", 0.0, [-0.6, low, 0.5])  # as the compiler's start keys put it
    tracks.add("puppet:dad", "leg_l.upper.rot", 0.0, 1.4)
    end = run(world, tracks, 0, who="dad", do="walk_to", to="steps_foot")
    pos = keys(tracks, "puppet:dad", "root.position")
    assert pos[-2:] == [(1.6, [-0.6, 0.0, 0.5]), (round(1.6 + 0.6 / 0.35, 3), [0.0, 0.0, 0.5])]
    assert pos[1] == (1.2, [-0.6, round(low, 3), 0.5])  # stands up from the lowered kneeling root
    assert keys(tracks, "puppet:dad", "leg_l.upper.rot")[1:3] == [(1.2, 1.4), (1.6, 0.0)]
    assert end == pytest.approx(1.6 + 0.6 / 0.35)
    assert world.people["dad"].pose == "stand"


def test_walk_to_errors():
    world, tracks = scene({"dad": {"at": "steps_left"}, "mom": {"at": "steps_foot", "in": "chair.seat"}},
                          {"chair": {"at": "steps_foot"}})
    e = fails(world, tracks, "unknown_mark", who="dad", do="walk_to", to="nowhere")
    assert e.reason == "nowhere is not a mark in shot s1"
    e = fails(world, tracks, "seated", who="mom", do="walk_to", to="curb")
    assert e.reason == "mom is seated in chair.seat"
    fails(world, tracks, "missing_param", who="dad", do="walk_to")


def test_run_to_is_faster_and_uses_the_run_cycle():
    world, tracks = scene({"dad": {"at": "steps_left", "facing": "right"}})
    end = run(world, tracks, 0, who="dad", do="run_to", to="curb")
    assert end == pytest.approx(1.2 + 1.4 / 0.8)
    assert keys(tracks, "puppet:dad", "torso.rot")  # the run leans the torso
    assert world.people["dad"].pos == pytest.approx((0.8, 0.0, 0.5))


def test_run_to_errors():
    world, tracks = scene({"dad": {"at": "steps_left"}, "mom": {"at": "steps_foot", "in": "chair.seat"}},
                          {"chair": {"at": "steps_foot"}})
    fails(world, tracks, "unknown_mark", who="dad", do="run_to", to="nowhere")
    fails(world, tracks, "seated", who="mom", do="run_to", to="curb")


def test_walk_run_and_pose_need_the_puppet_on_stage():
    world, tracks = scene()  # dad waits at offstage_left
    for action in ({"do": "walk_to", "to": "curb"}, {"do": "run_to", "to": "curb"}, {"do": "pose", "pose": "wave"}):
        e = fails(world, tracks, "offstage", who="dad", **action)
        assert e.reason == "dad is offstage; use enter first"


# ----------------------------------------------------------------------------- pose

def test_pose_bow_head_reproduces_the_sample():
    world, tracks = scene()
    end = run(world, tracks, 7.7, who="mom", do="pose", pose="bow_head")
    assert end == pytest.approx(9.3)
    assert keys(tracks, "puppet:mom", "head.rot") == [(8.9, 0.0), (9.3, -0.4)]
    assert world.people["mom"].pose == "bow_head"


def test_pose_with_dur_blends_back():
    world, tracks = scene()
    end = run(world, tracks, 1.0, who="mom", do="pose", pose="bow_head", dur=2.0)
    assert end == pytest.approx(2.2 + 2.0 + B.POSE_SECONDS)
    assert keys(tracks, "puppet:mom", "head.rot") == [(2.2, 0.0), (2.6, -0.4), (4.2, -0.4), (4.6, 0.0)]
    assert world.people["mom"].pose == "stand"  # back to what it was


def test_pose_sit_and_kneel_lower_the_root_and_stand_raises_it():
    world, tracks = scene({"dad": {"at": "steps_foot", "facing": "right"}})
    run(world, tracks, 0, who="dad", do="pose", pose="kneel")
    drop = 0.24 * ik.scale("tall")
    assert keys(tracks, "puppet:dad", "root.position") == [(1.2, [0.0, 0.0, 0.5]), (1.6, [0.0, round(-drop, 3), 0.5])]
    assert world.people["dad"].pose == "kneel"
    assert keys(tracks, "puppet:dad", "leg_l.upper.rot")[-1] == (1.6, 1.4)
    world.people["dad"].busy_until = 0
    run(world, tracks, 1.0, who="dad", do="pose", pose="stand")
    assert keys(tracks, "puppet:dad", "root.position")[-1] == (2.6, [0.0, 0.0, 0.5])
    assert keys(tracks, "puppet:dad", "leg_l.upper.rot")[-1] == (2.6, 0.0)
    run(world, tracks, 2.0, who="dad", do="pose", pose="sit")
    assert keys(tracks, "puppet:dad", "root.position")[-1] == (3.6, [0.0, round(-drop, 3), 0.5])
    assert world.people["dad"].pose == "sit"


def test_pose_stand_keeps_a_held_prop_in_the_hold_pose():
    world, tracks = scene()
    tracks.add("puppet:mom", "arm_r.upper.rot", 0.0, 0.3)
    tracks.add("puppet:mom", "arm_r.lower.rot", 0.0, 1.2)
    run(world, tracks, 0, who="mom", do="pose", pose="bow_head")
    run(world, tracks, 1, who="mom", do="pose", pose="stand")
    assert keys(tracks, "puppet:mom", "head.rot")[-1] == (2.6, 0.0)
    assert keys(tracks, "puppet:mom", "arm_r.upper.rot") == [(0.0, 0.3)]


def test_an_upper_body_pose_keeps_a_kneeling_puppet_down_and_stand_raises_it():
    world, tracks = scene({"dad": {"at": "steps_foot", "facing": "right"}})
    low = round(-0.24 * ik.scale("tall"), 3)
    run(world, tracks, 0, who="dad", do="pose", pose="kneel")
    run(world, tracks, 1, who="dad", do="pose", pose="wave")
    assert world.people["dad"].pose == "kneel"  # wave moves only the arm: still down
    assert keys(tracks, "puppet:dad", "root.position")[-1] == (1.6, [0.0, low, 0.5])
    end = run(world, tracks, 2, who="dad", do="pose", pose="stand")
    assert end == pytest.approx(3.6)
    assert keys(tracks, "puppet:dad", "root.position")[-2:] == [(3.2, [0.0, low, 0.5]), (3.6, [0.0, 0.0, 0.5])]
    assert keys(tracks, "puppet:dad", "leg_r.lower.rot")[-1] == (3.6, 0.0)
    assert keys(tracks, "puppet:dad", "arm_r.upper.rot")[-1] == (3.6, 0.0)
    assert world.people["dad"].pose == "stand"


def test_walk_after_an_upper_body_pose_on_the_floor_stands_up_first():
    world, tracks = scene({"dad": {"at": "steps_foot", "facing": "right"}})
    low = round(-0.24 * ik.scale("tall"), 3)
    run(world, tracks, 0, who="dad", do="pose", pose="sit")
    run(world, tracks, 2, who="dad", do="pose", pose="bow_head")
    assert world.people["dad"].pose == "sit"
    run(world, tracks, 4, who="dad", do="walk_to", to="curb")
    pos = keys(tracks, "puppet:dad", "root.position")
    assert pos[-3:] == [(5.2, [0.0, low, 0.5]), (5.6, [0.0, 0.0, 0.5]), (round(5.6 + 0.8 / 0.35, 3), [0.8, 0.0, 0.5])]
    assert keys(tracks, "puppet:dad", "leg_r.upper.rot")[1:4] == [(1.6, 1.5), (5.2, 1.5), (5.6, 0.0)]
    assert world.people["dad"].pose == "stand"


def test_an_upper_body_pose_keeps_a_seated_puppet_seated():
    world, tracks = scene({"mom": {"at": "steps_foot", "in": "chair.seat"}}, {"chair": {"at": "steps_foot"}})
    run(world, tracks, 0, who="mom", do="pose", pose="wave")
    assert world.people["mom"].pose == "sit" and world.people["mom"].seat == ("chair", "seat")


def test_a_leg_pose_replaces_a_floor_posture():
    world, tracks = scene({"dad": {"at": "steps_foot", "facing": "right"}})
    run(world, tracks, 0, who="dad", do="pose", pose="sit")
    run(world, tracks, 1, who="dad", do="pose", pose="kneel")
    assert world.people["dad"].pose == "kneel"


def test_pose_errors():
    world, tracks = scene({"mom": {"at": "steps_foot", "in": "chair.seat"}, "dad": {"at": "curb"}},
                          {"chair": {"at": "steps_foot"}})
    e = fails(world, tracks, "unknown_pose", who="dad", do="pose", pose="moonwalk")
    assert e.reason.startswith("moonwalk is not a pose; poses are: arms_raised, bow_head")
    fails(world, tracks, "bad_dur", who="dad", do="pose", pose="wave", dur=0)
    fails(world, tracks, "seated", who="mom", do="pose", pose="kneel")


# -------------------------------------------------------------------------- pick_up

def books_on_ground(at, notes=None):
    world, tracks = scene({"dad": {"at": at, "facing": "right"}, "mom": {"at": "steps_top", "facing": "left"}},
                          notes=notes)
    world.props["books"].parent = "ground"
    world._to_ground("books", (0.12, 0.3, 0.5))
    return world, tracks


def test_pick_up_in_reach_reproduces_the_sample():
    world, tracks = books_on_ground("steps_foot")
    end = run(world, tracks, 5.5, who="dad", do="pick_up", what="books")
    assert end == pytest.approx(6.7 + 0.8)
    assert keys(tracks, "prop:books", "parent") == [(7.1, "puppet:dad.hand_r")]
    assert keys(tracks, "prop:books", "position") == [(7.1, [0.0, 0.0, 0.0])]
    up = keys(tracks, "puppet:dad", "arm_r.upper.rot")
    assert [k[0] for k in up] == [6.7, 7.1, 7.5]
    assert up[1][1] == pytest.approx(-0.052, abs=1e-3)  # the sample's pick-up IK
    assert keys(tracks, "puppet:dad", "arm_r.lower.rot")[1][1] == pytest.approx(2.256, abs=1e-3)
    assert up[2][1] == 0.3
    assert world.holder("books") == ("dad", "hand_r")


def test_pick_up_out_of_reach_walks_there_first():
    world, tracks = books_on_ground("steps_left")
    end = run(world, tracks, 0, who="dad", do="pick_up", what="books")
    reach = ik.arm_reach("tall")
    stop = 0.12 - 0.6 * reach
    pos = keys(tracks, "puppet:dad", "root.position")
    assert pos[0] == (1.2, [-0.6, 0.0, 0.5])
    assert pos[-1][1] == [round(stop, 3), 0.0, 0.5]
    arrive = 1.2 + (stop + 0.6) / 0.35
    assert pos[-1][0] == pytest.approx(arrive, abs=1e-3)
    assert keys(tracks, "prop:books", "parent") == [(round(arrive + 0.4, 3), "puppet:dad.hand_r")]
    assert end == pytest.approx(arrive + 0.8)
    assert world.people["dad"].pos == pytest.approx((stop, 0.0, 0.5))


def test_pick_up_turns_to_face_the_prop():
    world, tracks = scene({"dad": {"at": "steps_foot", "facing": "left"}})
    world._to_ground("cup", (0.1, 0.3, 0.5))  # on the step behind him
    run(world, tracks, 0, who="dad", do="pick_up", what="cup")
    assert keys(tracks, "puppet:dad", "root.facing") == [(1.2, 1)]
    assert world.people["dad"].facing == 1


def test_pick_up_uses_the_free_hand():
    world, tracks = books_on_ground("steps_foot")
    world._attach_puppet("hat", "dad", "hand_r")
    run(world, tracks, 0, who="dad", do="pick_up", what="books")
    assert keys(tracks, "prop:books", "parent") == [(1.6, "puppet:dad.hand_l")]
    assert keys(tracks, "puppet:dad", "arm_l.upper.rot")


def grip_of(world, what):
    return B.grip_from_anchor(B.PropGeom.from_prop(world.catalog[what]), world.props[what].world_pos)


def hand_at(world, tracks, who, t, arm_socket="hand_r"):
    x, y, _ = puppet_socket_at(world, tracks, who, arm_socket, t)
    return x, y


def test_pick_up_from_the_floor_kneels_and_bends_to_the_grip():
    world, tracks = scene({"mom": {"at": "steps_foot", "facing": "right"}})
    world._to_ground("cup", (0.08, 0.0, 0.5))  # on the floor just in front of her feet
    grip = grip_of(world, "cup")
    assert not ik.can_reach("arm_r", grip, (0.0, 0.0), 1, "average")  # a standing arm is too short
    end = run(world, tracks, 0, who="mom", do="pick_up", what="cup")
    t_down, t_grab, t_up = 1.6, 2.0, 2.4
    assert end == pytest.approx(t_up)
    low = round(-0.24 * ik.scale("average"), 3)
    assert keys(tracks, "puppet:mom", "root.position") == [
        (1.2, [0.0, 0.0, 0.5]), (t_down, [0.0, low, 0.5]), (t_grab, [0.0, low, 0.5]), (t_up, [0.0, 0.0, 0.5]),
    ]
    torso = keys(tracks, "puppet:mom", "torso.rot")
    assert [k[0] for k in torso] == [1.2, t_down, t_grab, t_up]
    assert torso[0][1] == 0.0 and torso[-1][1] == 0.0 and torso[1][1] < 0  # bends forward, then back up
    assert keys(tracks, "puppet:mom", "leg_r.lower.rot") == [(1.2, 0.0), (t_down, -1.5), (t_grab, -1.5), (t_up, 0.0)]
    assert keys(tracks, "prop:cup", "parent") == [(t_grab, "puppet:mom.hand_r")]
    assert hand_at(world, tracks, "mom", t_grab) == pytest.approx(grip, abs=2e-3)  # the hand is on the grip
    assert keys(tracks, "puppet:mom", "arm_r.upper.rot")[-1] == (t_up, 0.3)  # then into the hold pose
    assert world.holder("cup") == ("mom", "hand_r")
    assert world.people["mom"].pose == "stand"


def test_pick_up_just_below_reach_only_stoops():
    world, tracks = scene({"dad": {"at": "steps_foot", "facing": "right"}})
    shoulder = ik.shoulder_world("arm_r", (0.0, 0.0), 1, "tall")
    reach = ik.arm_reach("tall")
    geom = B.PropGeom.from_prop(world.catalog["cup"])
    grip = (shoulder[0] + 0.02, shoulder[1] - reach - 0.01)
    world._to_ground("cup", (*B.anchor_from_grip(geom, grip), 0.5))
    run(world, tracks, 0, who="dad", do="pick_up", what="cup")
    assert keys(tracks, "puppet:dad", "root.position") == []  # no kneel
    bend = keys(tracks, "puppet:dad", "torso.rot")[1][1]
    assert -0.5 < bend < 0
    assert hand_at(world, tracks, "dad", 2.0) == pytest.approx(grip, abs=2e-3)


def test_pick_up_from_the_floor_while_kneeling_only_bends():
    world, tracks = scene({"mom": {"at": "steps_foot", "facing": "right", "pose": "kneel"}})
    low = round(-0.24 * ik.scale("average"), 3)
    tracks.add("puppet:mom", "root.position", 0.0, [0.0, low, 0.5])  # as the start keys put it
    world._to_ground("cup", (0.1, 0.0, 0.5))
    grip = grip_of(world, "cup")
    end = run(world, tracks, 0, who="mom", do="pick_up", what="cup")
    assert end == pytest.approx(2.4)
    assert keys(tracks, "puppet:mom", "root.position") == [(0.0, [0.0, low, 0.5])]  # stays down
    assert keys(tracks, "puppet:mom", "leg_r.lower.rot") == []
    assert hand_at(world, tracks, "mom", 2.0) == pytest.approx(grip, abs=2e-3)
    assert world.people["mom"].pose == "kneel"


def test_pick_up_from_the_floor_after_the_auto_walk():
    world, tracks = scene({"kid": {"at": "curb", "facing": "left"}})
    world._to_ground("cup", (0.1, 0.0, 0.5))
    grip = grip_of(world, "cup")
    end = run(world, tracks, 0, who="kid", do="pick_up", what="cup")
    stop = grip[0] + 0.6 * ik.arm_reach("small")
    arrive = 1.2 + (0.8 - stop) / 0.35
    pos = keys(tracks, "puppet:kid", "root.position")
    assert pos[1] == (pytest.approx(arrive, abs=1e-3), [round(stop, 3), 0.0, 0.5])
    assert pos[-1] == (pytest.approx(arrive + 1.2, abs=1e-3), [round(stop, 3), 0.0, 0.5])
    assert end == pytest.approx(arrive + 1.2)
    t_grab = round(arrive + 0.8, 3)
    assert keys(tracks, "prop:cup", "parent") == [(t_grab, "puppet:kid.hand_r")]
    assert hand_at(world, tracks, "kid", t_grab) == pytest.approx(grip, abs=2e-3)
    assert world.people["kid"].pos == pytest.approx((stop, 0.0, 0.5))


def test_pick_up_below_the_floor_is_out_of_reach_even_crouching():
    world, tracks = scene({"mom": {"at": "steps_foot", "facing": "right"}})
    world._to_ground("cup", (0.05, -1.0, 0.5))
    e = fails(world, tracks, "out_of_reach", who="mom", do="pick_up", what="cup")
    assert e.who == "mom" and e.distance > e.reach


# pick_up steps to a spot when the arm cannot take the grip where the puppet is (finding F3):
# a grip on the floor right under the hips is behind a forward bend's reach.

def low_cup(world, x, grip_y=0.03):
    """The cup on the floor with its grip at (x, grip_y), as low as base_scene's cap."""
    geom = B.PropGeom.from_prop(world.catalog["cup"])
    world._to_ground("cup", (*B.anchor_from_grip(geom, (x, grip_y)), 0.5))
    return (x, grip_y)


def test_pick_up_of_a_grip_under_the_hips_steps_back_first():
    world, tracks = scene({"mom": {"at": "steps_foot", "facing": "right"}})
    grip = low_cup(world, 0.0)
    end = run(world, tracks, 0, who="mom", do="pick_up", what="cup")
    x = world.people["mom"].pos[0]
    assert x <= -0.01  # facing right, so it steps back to the left
    arrive = round(1.2 + -x / 0.35, 3)
    pos = keys(tracks, "puppet:mom", "root.position")
    assert pos[:2] == [(1.2, [0.0, 0.0, 0.5]), (arrive, [x, 0.0, 0.5])]
    # The walk turns it away from the cup; it turns back on arrival, before it reaches.
    assert keys(tracks, "puppet:mom", "root.facing") == [(1.2, -1), (arrive, 1)]
    t_grab = round(arrive + B.POSE_SECONDS + B.REACH_SECONDS, 3)  # down, then reach
    assert keys(tracks, "prop:cup", "parent") == [(t_grab, "puppet:mom.hand_r")]
    assert hand_at(world, tracks, "mom", t_grab) == pytest.approx(grip, abs=2e-3)
    assert end == pytest.approx(arrive + 2 * B.POSE_SECONDS + B.REACH_SECONDS, abs=1e-3)
    assert world.people["mom"].facing == 1 and world.people["mom"].pose == "stand"
    assert world.holder("cup") == ("mom", "hand_r")


def test_pick_up_from_kneeling_over_the_grip_stands_steps_back_and_kneels_again():
    world, tracks = scene({"mom": {"at": "steps_foot", "facing": "left", "pose": "kneel"}})
    low = round(-0.24 * ik.scale("average"), 3)
    tracks.add("puppet:mom", "root.position", 0.0, [0.0, low, 0.5])  # as the start keys put it
    grip = low_cup(world, 0.0)
    run(world, tracks, 0, who="mom", do="pick_up", what="cup")
    x = world.people["mom"].pos[0]
    assert x >= 0.01  # facing left, so the step back is to the right
    pos = keys(tracks, "puppet:mom", "root.position")
    assert pos[1:3] == [(1.2, [0.0, low, 0.5]), (1.6, [0.0, 0.0, 0.5])]  # gets up first
    assert pos[3][1] == [x, 0.0, 0.5]
    t_grab = keys(tracks, "prop:cup", "parent")[0][0]
    assert hand_at(world, tracks, "mom", t_grab) == pytest.approx(grip, abs=2e-3)
    assert world.people["mom"].facing == -1 and world.people["mom"].pose == "stand"


def test_pick_up_with_the_right_hand_full_stops_where_the_left_shoulder_reaches():
    world, tracks = scene({"dad": {"at": "curb", "facing": "left", "holding": "hat"}})
    grip = low_cup(world, 0.0)
    run(world, tracks, 0, who="dad", do="pick_up", what="cup")
    t_grab, hand = keys(tracks, "prop:cup", "parent")[0]
    assert hand == "puppet:dad.hand_l"
    assert hand_at(world, tracks, "dad", t_grab, "hand_l") == pytest.approx(grip, abs=2e-3)
    assert keys(tracks, "prop:hat", "parent") == []  # still in hand_r, untouched
    x = world.people["dad"].pos[0]
    assert x > 0.0  # it walked up from the curb's side
    # The plain walk-up stop (0.6 of the reach from the root) is where main failed: the left
    # shoulder sits behind the hip, and kneeling at CROUCH_MAX_BEND the arm misses the floor.
    plain = ik.Body("dad", (0.6 * ik.arm_reach("tall"), 0.0), -1, "tall")
    assert verbs_core._crouch_pick(verbs_core._crouch_tries(plain, "arm_l", grip, True), 0.17) is None
    # The spot it took reaches with the least effort the search found: kneeling, deeply bent.
    torso = keys(tracks, "puppet:dad", "torso.rot")
    assert torso[1][1] == pytest.approx(-1.4)
    assert tracks.value_at("puppet:dad", "leg_r.lower.rot", t_grab) == -1.5  # kneeling at the grab


def test_pick_up_step_search_failure_is_atomic_and_reports_the_closest_miss():
    world, tracks = scene({"dad": {"at": "steps_foot", "facing": "right"}})
    grip = low_cup(world, 0.3, grip_y=1.5)  # far above every standing shoulder
    e = fails(world, tracks, "out_of_reach", who="dad", do="pick_up", what="cup")
    # The closest spot puts the shoulder right under the grip (spots are SPOT_STEP apart).
    shoulder_y = ik.shoulder_world("arm_r", (0.0, 0.0), 1, "tall")[1]
    assert e.distance == pytest.approx(grip[1] - shoulder_y, abs=1e-4)
    assert (e.who, e.arm, e.reach) == ("dad", "arm_r", pytest.approx(ik.arm_reach("tall")))


def test_pick_up_from_a_seat_never_steps():
    world, tracks = scene({"mom": {"at": "steps_foot", "in": "chair.seat", "facing": "right"}},
                          prop_start={"chair": {"at": "steps_foot"}})
    low_cup(world, 0.0)
    e = fails(world, tracks, "out_of_reach", who="mom", do="pick_up", what="cup")
    assert e.who == "mom" and e.distance > e.reach


def test_the_deep_bend_is_only_tried_by_a_stepped_to_spot():
    body = ik.Body("dad", (0.15, 0.0), -1, "tall")
    plain = verbs_core._crouch_tries(body, "arm_l", (0.0, 0.03), True)
    deep = verbs_core._crouch_tries(body, "arm_l", (0.0, 0.03), True, verbs_core.CROUCH_DEEP_BEND)
    assert min(tr[2] for tr in plain) == pytest.approx(-verbs_core.CROUCH_MAX_BEND)
    assert min(tr[2] for tr in deep) == pytest.approx(-verbs_core.CROUCH_DEEP_BEND)
    # arm_l cannot reach the floor kneeling at CROUCH_MAX_BEND, from anywhere: the deep bend can.
    assert verbs_core._crouch_pick(plain, 0.17) is None
    assert verbs_core._crouch_pick(deep, 0.17) is not None


def test_pick_up_errors():
    world, tracks = scene({"dad": {"at": "steps_foot"}, "mom": {"at": "steps_top", "holding": "books"}})
    e = fails(world, tracks, "already_held", who="dad", do="pick_up", what="books")
    assert e.reason == "mom is holding it"
    e = fails(world, tracks, "not_on_stage", who="dad", do="pick_up", what="cup")
    assert e.reason == "cup is not on stage"
    world._to_ground("cup", (0.0, 1.5, 0.5))  # straight above dad's head, far out of reach
    e = fails(world, tracks, "out_of_reach", who="dad", do="pick_up", what="cup")
    assert e.who == "dad"
    world._to_ground("cup", (0.8, 1.5, 0.5))  # walking toward it does not help either
    fails(world, tracks, "out_of_reach", who="dad", do="pick_up", what="cup")
    fails(world, tracks, "unknown_prop", who="dad", do="pick_up", what="piano")


def test_pick_up_offstage_and_hands_full():
    world, tracks = scene({"dad": {"at": "offstage_left"}, "mom": {"at": "steps_top", "holding": "books"}})
    world._to_ground("cup", (0.0, 0.0, 0.5))
    e = fails(world, tracks, "offstage", who="dad", do="pick_up", what="cup")
    assert e.reason == "dad is offstage"
    world._attach_puppet("hat", "mom", "hand_l")
    e = fails(world, tracks, "hands_full", who="mom", do="pick_up", what="cup")
    assert e.reason == "mom's hands are full (hand_r: books, hand_l: hat)"


# ----------------------------------------------------------------------------- hold

def test_hold_raises_the_held_prop():
    world, tracks = scene({"mom": {"at": "steps_top", "holding": "books"}})
    end = run(world, tracks, 1.0, who="mom", do="hold", what="books")
    assert end == pytest.approx(2.2 + B.POSE_SECONDS)
    assert keys(tracks, "puppet:mom", "arm_r.upper.rot") == [(2.2, 0.0), (2.6, 0.3)]
    assert keys(tracks, "puppet:mom", "arm_r.lower.rot") == [(2.2, 0.0), (2.6, 1.2)]


def test_hold_errors():
    world, tracks = scene({"dad": {"at": "steps_foot"}, "mom": {"at": "steps_top", "holding": "books"}})
    e = fails(world, tracks, "not_holding", who="dad", do="hold", what="books")
    assert e.reason == "mom is holding it"
    e = fails(world, tracks, "not_holding", who="dad", do="hold", what="cup")
    assert e.reason == "it is not on stage"


# ----------------------------------------------------------------------------- drop

def test_drop_reproduces_the_sample():
    world, tracks = scene()
    tracks.add("puppet:mom", "arm_r.upper.rot", 0.0, 0.3)
    tracks.add("puppet:mom", "arm_r.lower.rot", 0.0, 1.2)
    end = run(world, tracks, 3.25, who="mom", do="drop", what="books")
    assert end == pytest.approx(4.45 + 0.3)
    assert keys(tracks, "prop:books", "parent") == [(4.45, "ground")]
    assert keys(tracks, "prop:books", "position") == [(4.45, [0.045, 0.513, 0.5]), (4.75, [0.12, 0.3, 0.5])]
    assert keys(tracks, "puppet:mom", "arm_r.upper.rot") == [(0.0, 0.3), (4.45, 0.3), (4.75, 0.0)]
    assert world.props["books"].world_pos == pytest.approx((0.12, 0.3, 0.5))
    assert world.holder("books") is None


def test_drop_while_kneeling_on_a_raised_mark_lands_on_the_mark_not_at_the_lowered_root():
    world, tracks = scene()  # mom on steps_top (h = 0.3), holding books
    run(world, tracks, 0.0, who="mom", do="pose", pose="kneel")
    root_y = puppet_root(world, tracks, "mom", ACTION_START + 2.0)[1]
    assert root_y < 0.3 - 0.05  # the kneel lowered the root well below the mark
    run(world, tracks, 2.0, who="mom", do="drop", what="books")
    assert keys(tracks, "prop:books", "position")[-1][1][1] == pytest.approx(0.3)
    assert world.props["books"].world_pos[1] == pytest.approx(0.3)


def test_drop_while_seated_lands_on_the_ground_under_the_seat_not_at_the_hips():
    # A chair on the raised steps_top (h = 0.3); mom sits on it with her pos at the hip root, as
    # get_in leaves it. The books land at the chair's anchor height, not the hips.
    world, tracks = scene(prop_start={"chair": {"at": "steps_top"}})
    world.sit_on("mom", "chair")
    seat = world.socket_world("chair", "seat")
    world.people["mom"].pos = (seat[0], seat[1] + 0.05, seat[2])
    assert world.people["mom"].pos[1] > 0.3 + 0.05
    run(world, tracks, 1.0, who="mom", do="drop", what="books")
    landing = keys(tracks, "prop:books", "position")[-1][1]
    assert landing[1] == pytest.approx(0.3)
    assert landing[0] == pytest.approx(seat[0] - B.DROP_OFFSET)  # x unchanged: in front of her feet, facing left
    assert world.props["books"].world_pos[1] == pytest.approx(0.3)


def test_drop_errors():
    world, tracks = scene({"dad": {"at": "steps_foot"}, "mom": {"at": "steps_top", "holding": "books"}})
    e = fails(world, tracks, "not_holding", who="dad", do="drop", what="books")
    assert e.reason == "mom is holding it"
    fails(world, tracks, "missing_param", who="dad", do="drop")


# -------------------------------------------------------------------------- hand_to

def handover_scene(dad_at="steps_foot", dad_facing="right"):
    world, tracks = scene({"dad": {"at": dad_at, "holding": "books", "facing": dad_facing},
                           "mom": {"at": "steps_top", "facing": "left"}})
    tracks.add("puppet:dad", "arm_r.upper.rot", 0.0, 0.3)
    tracks.add("puppet:dad", "arm_r.lower.rot", 0.0, 1.2)
    return world, tracks


def test_hand_to_reproduces_the_sample():
    world, tracks = handover_scene()
    end = run(world, tracks, 6.6, who="dad", do="hand_to", what="books", to="mom")
    assert end == pytest.approx(7.8 + 0.9)
    assert keys(tracks, "prop:books", "parent") == [(8.3, "puppet:mom.hand_r")]
    assert keys(tracks, "puppet:mom", "arm_r.upper.rot")[-1] == (8.7, 0.3)
    assert keys(tracks, "puppet:dad", "arm_r.upper.rot")[-1] == (8.7, 0.0)
    assert world.holder("books") == ("mom", "hand_r")
    # the taker is busy until the pass ends
    assert world.people["mom"].busy_until == pytest.approx(7.5)
    assert world.people["mom"].activity == "taking books"


def test_hand_to_turns_both_to_face_each_other():
    world, tracks = handover_scene(dad_facing="left")
    world.people["mom"].facing = 1
    run(world, tracks, 0, who="dad", do="hand_to", what="books", to="mom")
    assert keys(tracks, "puppet:dad", "root.facing") == [(1.2, 1)]
    assert keys(tracks, "puppet:mom", "root.facing") == [(1.2, -1)]


def test_hand_to_not_holding_is_the_exact_reason():
    world, tracks = scene({"dad": {"at": "steps_foot"}, "mom": {"at": "steps_top", "holding": "books"}})
    e = fails(world, tracks, "not_holding", who="dad", do="hand_to", what="books", to="mom")
    assert e.reason == "mom is holding it"


def test_hand_to_errors():
    world, tracks = handover_scene()
    world.people["mom"].busy_until = 5.0
    world.people["mom"].activity = "dropping"
    e = fails(world, tracks, "busy", who="dad", do="hand_to", what="books", to="mom", t=1.0)
    assert e.reason == "mom is still dropping until t=5"
    world.people["mom"].busy_until = 0.0
    fails(world, tracks, "self_hand_to", who="dad", do="hand_to", what="books", to="dad")
    fails(world, tracks, "unknown_person", who="dad", do="hand_to", what="books", to="gran")
    fails(world, tracks, "offstage", who="dad", do="hand_to", what="books", to="kid")


def test_hand_to_out_of_reach_does_not_walk():
    world, tracks = handover_scene(dad_at="steps_left")
    e = fails(world, tracks, "out_of_reach", who="dad", do="hand_to", what="books", to="mom")
    assert e.who in ("dad", "mom")
    assert e.distance > e.reach


# ---------------------------------------------------------------------- atomicity

@pytest.mark.parametrize("action", [
    {"who": "dad", "do": "enter", "to": "nowhere"},
    {"who": "mom", "do": "enter", "to": "curb"},
    {"who": "dad", "do": "walk_to", "to": "nowhere"},
    {"who": "dad", "do": "run_to", "to": "nowhere"},
    {"who": "dad", "do": "pose", "pose": "moonwalk"},
    {"who": "dad", "do": "pick_up", "what": "books"},
    {"who": "dad", "do": "pick_up", "what": "cup"},
    {"who": "dad", "do": "hold", "what": "books"},
    {"who": "dad", "do": "drop", "what": "books"},
    {"who": "dad", "do": "hand_to", "what": "books", "to": "mom"},
])
def test_failed_handlers_change_nothing(action):
    world, tracks = scene()
    run(world, tracks, 0, who="mom", do="pose", pose="bow_head")  # some existing keys
    world.now = 1.0
    before = frozen(world, tracks)
    with pytest.raises((StateError, ik.OutOfReach)):
        run(world, tracks, 1.0, **action)
    assert frozen(world, tracks) == before


def test_out_of_reach_after_auto_walk_is_atomic():
    world, tracks = books_on_ground("steps_left")
    world._to_ground("books", (0.8, 1.2, 0.5))
    before = frozen(world, tracks)
    with pytest.raises(ik.OutOfReach):
        run(world, tracks, 0, who="dad", do="pick_up", what="books")
    assert frozen(world, tracks) == before
    assert not math.isnan(world.people["dad"].pos[0])


# ------------------------------------------------------------- speed from the sheet's notes
# World.people[who].speed_scale comes from CharacterSheet.notes (screenplay.character):
# a cane makes every walk and run take 1/0.6 as long, "quickly" 1/1.25. Poses, the stand-up
# before a walk and the reach of a pick-up keep their own timings.

CANE, QUICK = "walks with a cane", "quickly"
SCALED = [(CANE, 0.6), (QUICK, 1.25)]


def _end(notes, start, **action):
    world, tracks = scene(start, notes={"dad": notes})
    return run(world, tracks, 0, who="dad", **action), world, tracks


@pytest.mark.parametrize("notes,factor", SCALED)
@pytest.mark.parametrize("verb,to", [("walk_to", "steps_foot"), ("run_to", "curb")])
def test_walk_and_run_take_longer_with_a_cane_and_less_quickly(notes, factor, verb, to):
    start = {"dad": {"at": "steps_left", "facing": "right"}}
    plain, _, _ = _end(None, start, do=verb, to=to)
    slow, world, tracks = _end(notes, start, do=verb, to=to)
    assert world.people["dad"].speed_scale == factor
    assert slow - ACTION_START == pytest.approx((plain - ACTION_START) / factor, abs=1e-3)
    assert keys(tracks, "puppet:dad", "root.position")[-1] == (round(slow, 3), [MARKS[to][0], 0.0, 0.5])
    assert world.people["dad"].pos == pytest.approx(MARKS[to])


@pytest.mark.parametrize("notes,factor", SCALED)
def test_enter_takes_longer_with_a_cane_and_less_quickly(notes, factor):
    start = {"dad": {"at": "offstage_left", "facing": "right"}}
    action = {"do": "enter", "from": "offstage_left", "to": "steps_left"}
    plain, _, _ = _end(None, start, **action)
    slow, _, tracks = _end(notes, start, **action)
    assert plain == pytest.approx(1.2 + 0.7 / 0.35)
    assert slow - ACTION_START == pytest.approx(0.7 / 0.35 / factor, abs=1e-3)
    assert keys(tracks, "puppet:dad", "root.position") == [(1.2, [-1.3, 0.0, 0.5]), (round(slow, 3), [-0.6, 0.0, 0.5])]


def test_the_walk_cycle_spreads_over_the_slower_walk():
    """Same stride count, so the cane walk has the same number of cycle keys, stretched in time."""
    start = {"dad": {"at": "steps_left", "facing": "right"}}
    _, _, plain = _end(None, start, do="walk_to", to="steps_foot")
    _, _, slow = _end(CANE, start, do="walk_to", to="steps_foot")
    p, s = keys(plain, "puppet:dad", "leg_l.upper.rot"), keys(slow, "puppet:dad", "leg_l.upper.rot")
    assert len(p) == len(s) > 2
    for (tp, vp), (ts, vs) in zip(p, s):
        assert vs == vp
        assert ts - ACTION_START == pytest.approx((tp - ACTION_START) / 0.6, abs=2e-3)


@pytest.mark.parametrize("notes,factor", SCALED)
def test_pick_up_auto_walk_is_scaled_and_the_reach_is_not(notes, factor):
    reach = ik.arm_reach("tall")
    stop = 0.12 - 0.6 * reach
    plain_walk = (stop + 0.6) / 0.35
    world, tracks = books_on_ground("steps_left", notes={"dad": notes})
    end = run(world, tracks, 0, who="dad", do="pick_up", what="books")
    arrive = ACTION_START + plain_walk / factor
    pos = keys(tracks, "puppet:dad", "root.position")
    assert pos[-1][0] == pytest.approx(arrive, abs=1e-3)
    assert pos[-1][1] == [round(stop, 3), 0.0, 0.5]
    # the reach (0.4 out, 0.4 back) after the walk is the same as without notes
    assert keys(tracks, "prop:books", "parent") == [(round(arrive + 0.4, 3), "puppet:dad.hand_r")]
    assert end == pytest.approx(arrive + 0.8)


def test_pick_up_in_reach_does_not_depend_on_notes():
    ends = []
    for notes in (None, CANE, QUICK):
        world, tracks = books_on_ground("steps_foot", notes={"dad": notes})
        ends.append(run(world, tracks, 5.5, who="dad", do="pick_up", what="books"))
    assert ends == [pytest.approx(7.5)] * 3


def test_walk_from_kneeling_scales_only_the_walk_not_the_stand_up():
    world, tracks = scene({"dad": {"at": "steps_left", "facing": "right", "pose": "kneel"}}, notes={"dad": CANE})
    tracks.add("puppet:dad", "root.position", 0.0, [-0.6, -0.24 * ik.scale("tall"), 0.5])
    end = run(world, tracks, 0, who="dad", do="walk_to", to="steps_foot")
    stood = ACTION_START + B.POSE_SECONDS  # standing up takes POSE_SECONDS with or without a cane
    assert keys(tracks, "puppet:dad", "root.position")[-2][0] == pytest.approx(stood)
    assert end == pytest.approx(stood + 0.6 / 0.35 / 0.6)


@pytest.mark.parametrize("pose,dur", [("bow_head", None), ("bow_head", 2.0), ("kneel", None), ("sit", None),
                                      ("stand", None)])
def test_pose_durations_do_not_depend_on_notes(pose, dur):
    ends, tracks_by_notes = [], []
    for notes in (None, CANE, QUICK):
        world, tracks = scene({"dad": {"at": "steps_foot", "facing": "right",
                                       "pose": "kneel" if pose == "stand" else "stand"}}, notes={"dad": notes})
        action = {"who": "dad", "do": "pose", "pose": pose, **({"dur": dur} if dur else {})}
        ends.append(run(world, tracks, 0, **action))
        tracks_by_notes.append([tr.model_dump() for tr in tracks.tracks()])
    assert ends[1] == ends[2] == ends[0]
    assert tracks_by_notes[1] == tracks_by_notes[2] == tracks_by_notes[0]


# ------------------------------------------------- drop from a seat, through compile()

def _compile_yard(actions, start, prop_start):
    sp = Screenplay.model_validate({
        "memoryId": "m", "cast": ["dad", "mom", "kid"],
        "props": ["hymn_books", "chair", "table", "sedan"],
        "shots": [{"id": "s1", "set": "yard", "start": start, "propStart": prop_start,
                   "actions": actions}],
    })
    sc = base_scene()
    return compile_screenplay(sp, sets=sc.sets, props=sc.props, people=sc.people)


def _track(result, target, channel):
    for tr in result.timeline.tracks:
        if (tr.target, tr.channel) == (target, channel):
            return [(k[0], k[1]) for k in tr.keys]
    return []


def _books_land(result):
    """The books' last parent and their final position."""
    parents = _track(result, "prop:hymn_books", "parent")
    positions = _track(result, "prop:hymn_books", "position")
    return parents[-1], positions[-1]


def test_drop_from_inside_the_sedan_lands_on_the_ground_not_at_the_hips():
    res = _compile_yard(
        [{"t": 1, "do": "get_in", "who": "dad", "what": "sedan"},
         {"t": 4, "do": "drop", "who": "dad", "what": "hymn_books"}],
        start={"dad": {"at": "curb", "holding": "hymn_books"}},
        prop_start={"sedan": {"at": "curb"}},
    )
    assert res.ok, [e.message for e in res.errors]
    (t_parent, parent), (t_land, landing) = _books_land(res)
    assert parent == "ground"
    assert t_land == pytest.approx(t_parent + B.FALL_SECONDS)
    assert landing[1] == 0.0  # the curb's ground, not the seated hip root (0.141)
    assert landing[2] == pytest.approx(0.5)


def test_books_dropped_in_the_sedan_stay_on_the_ground_when_it_drives_away():
    res = _compile_yard(
        [{"t": 1, "do": "get_in", "who": "dad", "what": "sedan"},
         {"t": 4, "do": "drop", "who": "dad", "what": "hymn_books"},
         {"t": 5, "do": "drive_to", "who": "dad", "what": "sedan", "to": "door"}],
        start={"dad": {"at": "curb", "holding": "hymn_books"}},
        prop_start={"sedan": {"at": "curb"}},
    )
    assert res.ok, [e.message for e in res.errors]
    (_, parent), (_, landing) = _books_land(res)
    assert parent == "ground"
    assert landing[1] == 0.0


def test_drop_from_a_chair_lands_on_the_ground_under_it():
    res = _compile_yard(
        [{"t": 1, "do": "sit_on", "who": "dad", "what": "chair"},
         {"t": 4, "do": "drop", "who": "dad", "what": "hymn_books"}],
        start={"dad": {"at": "beside_bench", "holding": "hymn_books"}},
        prop_start={"chair": {"at": "bench_spot"}},
    )
    assert res.ok, [e.message for e in res.errors]
    (_, parent), (_, landing) = _books_land(res)
    assert parent == "ground"
    assert landing[1] == 0.0


def test_drop_from_a_chair_standing_on_a_table_lands_at_the_table_top():
    res = _compile_yard(
        [{"t": 1, "do": "sit_on", "who": "dad", "what": "chair"},
         {"t": 4, "do": "drop", "who": "dad", "what": "hymn_books"}],
        start={"dad": {"at": "by_table", "holding": "hymn_books"}},
        prop_start={"table": {"at": "table_spot"}, "chair": {"on": "table.top"}},
    )
    assert res.ok, [e.message for e in res.errors]
    sc = base_scene()
    world = World(sc.people.values(), sc.props.values())
    world.begin_shot(Shot.model_validate({"id": "s1", "set": "yard",
                                          "propStart": {"table": {"at": "table_spot"}, "chair": {"on": "table.top"}}}),
                     resolve_marks(sc.sets["yard"].marks, {}), False)
    resting = world.props["chair"].world_pos[1]
    assert resting > 0.2  # the chair's anchor is on the table top, well off the ground
    (_, parent), (_, landing) = _books_land(res)
    assert parent == "ground"
    assert landing[1] == pytest.approx(resting)


def test_drop_while_kneeling_on_the_ground_lands_on_the_ground():
    res = _compile_yard(
        [{"t": 1, "do": "kneel", "who": "dad"},
         {"t": 4, "do": "drop", "who": "dad", "what": "hymn_books"}],
        start={"dad": {"at": "curb", "holding": "hymn_books"}},
        prop_start={},
    )
    assert res.ok, [e.message for e in res.errors]
    (_, parent), (_, landing) = _books_land(res)
    assert parent == "ground"
    assert landing[1] == 0.0


# ------------------------------------------------- the torso: reaches from a leaning body

def _compile(shots, cast=("dad", "mom"), props=("hymn_books",)):
    sp = Screenplay.model_validate({"memoryId": "m", "cast": list(cast), "props": list(props), "shots": shots})
    sc = base_scene()
    return compile_screenplay(sp, sets=sc.sets, props=sc.props, people=sc.people)


def _hand_on_timeline(timeline, who, socket, t):
    """A hand's world position from the compiled tracks alone, every bone (torso included)."""
    sc = base_scene()
    rots = {b: evaluate(timeline.tracks, f"puppet:{who}", f"{b}.rot", t) or 0.0 for b in ik.RIG["bones"]}
    x, y, _ = evaluate(timeline.tracks, f"puppet:{who}", "root.position", t)
    facing = int(evaluate(timeline.tracks, f"puppet:{who}", "root.facing", t))
    return ik.socket_world(socket, rots, (x, y), facing, sc.people[who].character.build)


@pytest.mark.parametrize("slumped", [True, False])
def test_hand_to_from_a_slumped_giver_meets_hand_to_hand(slumped):
    """Dad leans (slumped: torso -0.25) and hands the books over: both hands are on the same
    point when the books change parent. Before the IK knew the torso, the gap was 0.055."""
    actions = [{"t": 1, "do": "hand_to", "who": "dad", "what": "hymn_books", "to": "mom"}]
    if slumped:
        actions.insert(0, {"t": 0, "do": "pose", "who": "dad", "pose": "slumped"})
    res = _compile([{"id": "s1", "set": "yard",
                     "start": {"dad": {"at": "bench_spot", "holding": "hymn_books", "facing": "right"},
                               "mom": {"at": "beside_bench", "facing": "left"}},
                     "actions": actions}])
    assert res.ok, [e.message for e in res.errors]
    tl = res.timeline
    (t_swap, _), = [k for k in _track(res, "prop:hymn_books", "parent") if k[1] == "puppet:mom.hand_r"]
    if slumped:
        assert evaluate(tl.tracks, "puppet:dad", "torso.rot", t_swap) == pytest.approx(-0.25)
    gap = math.dist(_hand_on_timeline(tl, "dad", "hand_r", t_swap), _hand_on_timeline(tl, "mom", "hand_r", t_swap))
    assert gap <= 1e-3


def test_hand_to_keeps_each_bodys_torso_when_it_turns_them():
    world, tracks = handover_scene(dad_facing="left")
    tracks.add("puppet:dad", "torso.rot", 0.0, -0.25)
    tracks.add("puppet:mom", "torso.rot", 0.0, -0.1)
    world.people["mom"].facing = 1
    run(world, tracks, 0, who="dad", do="hand_to", what="books", to="mom")
    t_meet = 1.2 + B.PASS_SECONDS
    dad = puppet_socket_at(world, tracks, "dad", "hand_r", t_meet)
    mom = puppet_socket_at(world, tracks, "mom", "hand_r", t_meet)
    assert math.dist(dad[:2], mom[:2]) <= 2e-3  # keys are rounded to 3 dp


def test_pick_up_from_a_slumped_torso_lands_the_hand_on_the_grip():
    world, tracks = scene({"dad": {"at": "steps_foot", "facing": "right"}})
    tracks.add("puppet:dad", "torso.rot", 0.0, -0.25)
    geom = B.PropGeom.from_prop(world.catalog["cup"])
    shoulder = ik.shoulder_world("arm_r", (0.0, 0.0), 1, "tall", torso=-0.25)
    grip = (shoulder[0] + 0.1, shoulder[1] - 0.05)
    world._to_ground("cup", (*B.anchor_from_grip(geom, grip), 0.5))
    run(world, tracks, 0, who="dad", do="pick_up", what="cup")
    t_grab = 1.2 + B.REACH_SECONDS
    assert keys(tracks, "prop:cup", "parent") == [(t_grab, "puppet:dad.hand_r")]
    assert hand_at(world, tracks, "dad", t_grab) == pytest.approx(grip, abs=2e-3)


def test_pick_up_reach_check_uses_the_torso_rotated_shoulder():
    """A grip just beyond an upright reach but within a forward lean's is taken where the
    puppet stands, with no auto-walk."""
    world, tracks = scene({"dad": {"at": "steps_foot", "facing": "right"}})
    tracks.add("puppet:dad", "torso.rot", 0.0, -0.25)
    geom = B.PropGeom.from_prop(world.catalog["cup"])
    bent = ik.shoulder_world("arm_r", (0.0, 0.0), 1, "tall", torso=-0.25)
    grip = (bent[0] + 0.97 * ik.arm_reach("tall"), bent[1])
    assert not ik.can_reach("arm_r", grip, (0.0, 0.0), 1, "tall")
    world._to_ground("cup", (*B.anchor_from_grip(geom, grip), 0.5))
    end = run(world, tracks, 0, who="dad", do="pick_up", what="cup")
    assert keys(tracks, "puppet:dad", "root.position") == []  # no walk
    assert end == pytest.approx(1.2 + 2 * B.REACH_SECONDS)
    assert hand_at(world, tracks, "dad", 1.2 + B.REACH_SECONDS) == pytest.approx(grip, abs=2e-3)


def test_bent_arm_helpers_are_the_ik_formula():
    from screenplay.verbs_core import _bent_shoulder, _solve_bent_arm

    for bend in (0.0, -0.3, -0.9):
        assert _bent_shoulder("arm_r", (0.1, 0.0), -1, "small", bend) == ik.shoulder_world(
            "arm_r", (0.1, 0.0), -1, "small", torso=bend
        )
        grip = (0.0, 0.05)
        dist, angles = _solve_bent_arm("arm_r", grip, (0.1, 0.0), -1, "small", bend)
        assert (dist, angles) == ik.arm_solution("arm_r", grip, (0.1, 0.0), -1, "small", torso=bend)
        if angles is not None:
            assert angles == ik.solve_arm("arm_r", grip, (0.1, 0.0), -1, "small", torso=bend)


# ------------------------------------------------------ enter: a step offstage, no glide

def _x_before(res, who, t_from, t_to, step=0.05):
    """root.position x at every sample in [t_from, t_to)."""
    xs = []
    t = t_from
    while t < t_to - 1e-9:
        xs.append(evaluate(res.timeline.tracks, f"puppet:{who}", "root.position", t)[0])
        t += step
    xs.append(evaluate(res.timeline.tracks, f"puppet:{who}", "root.position", t_to - 1e-6)[0])
    return xs


def test_enter_from_the_other_side_steps_offstage_instead_of_gliding():
    res = _compile([{"id": "s1", "set": "yard", "start": {"dad": {"at": "offstage_left", "facing": "right"}},
                     "actions": [{"t": 1, "do": "enter", "who": "dad", "from": "offstage_right", "to": "curb"}]}])
    assert res.ok, [e.message for e in res.errors]
    t_enter = res.timeline.shots[0].action_start + 1
    assert set(_x_before(res, "dad", 0.0, t_enter)) == {-1.3}
    assert evaluate(res.timeline.tracks, "puppet:dad", "root.position", t_enter)[0] == 1.3
    pos = _track(res, "puppet:dad", "root.position")
    at_enter = [k for k in pos if k[0] == pytest.approx(t_enter)]
    assert at_enter == [(t_enter, [-1.3, 0.0, 0.5]), (t_enter, [1.3, 0.0, 0.5])]  # held, then the step


def test_exit_right_then_enter_left_waits_offstage_right():
    res = _compile([{"id": "s1", "set": "yard", "start": {"dad": {"at": "curb", "facing": "right"}},
                     "actions": [{"t": 0, "do": "exit", "who": "dad", "to": "offstage_right"},
                                 {"t": 6, "do": "enter", "who": "dad", "from": "offstage_left", "to": "door"}]}])
    assert res.ok, [e.message for e in res.errors]
    a0 = res.timeline.shots[0].action_start
    gone = a0 + (1.3 - 0.8) / 0.35  # the exit walk arrives offstage right
    assert set(_x_before(res, "dad", gone + 1e-3, a0 + 6)) == {1.3}
    assert evaluate(res.timeline.tracks, "puppet:dad", "root.position", a0 + 6)[0] == -1.3


def test_a_puppet_parked_by_a_new_set_enters_from_the_right_without_gliding():
    res = _compile([
        {"id": "s1", "set": "yard", "start": {"dad": {"at": "door"}, "mom": {"at": "bench_spot"}},
         "actions": [{"t": 0, "do": "pose", "who": "mom", "pose": "bow_head"}]},
        {"id": "s2", "set": "street", "start": {"dad": {"at": "garage"}},
         "actions": [{"t": 1, "do": "enter", "who": "mom", "from": "offstage_right", "to": "corner"}]},
    ])
    assert res.ok, [e.message for e in res.errors]
    s2 = res.timeline.shots[1]
    t_enter = s2.action_start + 1
    assert set(_x_before(res, "mom", s2.start, t_enter)) == {-1.3}
    assert evaluate(res.timeline.tracks, "puppet:mom", "root.position", t_enter)[0] == 1.3


def test_enter_from_where_the_puppet_already_is_adds_no_key():
    """The hymn sample: dad waits at offstage_left and enters from there, so his root keys
    stay exactly the reference's."""
    import json
    from pathlib import Path

    samples = Path(__file__).resolve().parents[3] / "web" / "public" / "samples"
    memory = json.loads((samples / "hymn_books.memory.json").read_text())
    res = compile_screenplay(
        Screenplay.model_validate(json.loads((samples / "hymn_books.screenplay.json").read_text())),
        sets={s["id"]: schemas.SetDef.model_validate(s) for s in memory["sets"]},
        props={p["id"]: schemas.Prop.model_validate(p) for p in memory["props"]},
        people={p["id"]: schemas.Person.model_validate(p) for p in memory["people"]},
    )
    assert res.ok, [e.message for e in res.errors]
    pos = _track(res, "puppet:dad", "root.position")
    assert pos[:3] == [(0.0, [-1.3, 0.0, 0.5]), (1.2, [-1.3, 0.0, 0.5]), (3.2, [-0.6, 0.0, 0.5])]
    times = [k[0] for k in pos]
    assert len(times) == len(set(times))  # no two keys at one time anywhere


def test_enter_holds_the_previous_root_in_the_handler():
    world, tracks = scene()
    tracks.add("puppet:dad", "root.position", 0.0, [-1.3, 0.0, 0.5])
    run(world, tracks, 0, who="dad", do="enter", **{"from": "offstage_right"}, to="curb")
    assert keys(tracks, "puppet:dad", "root.position") == [
        (0.0, [-1.3, 0.0, 0.5]), (1.2, [-1.3, 0.0, 0.5]), (1.2, [1.3, 0.0, 0.5]),
        (pytest.approx(1.2 + 0.5 / 0.35, abs=1e-3), [0.8, 0.0, 0.5]),
    ]
    assert puppet_root(world, tracks, "dad", 1.0)[0] == -1.3
    assert puppet_root(world, tracks, "dad", 1.2)[0] == 1.3
