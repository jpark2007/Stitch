"""screenplay.verb_helpers: the helpers the verbs_* handler modules share.

Each check raises the exact code and message the verb modules have always emitted; the value
helpers and the crouch solver are pinned to their formulas; and the module stays outside
compiler handler discovery, imports no verbs_* module, and is what the verb modules' old
private names now point at.
"""

from __future__ import annotations

import ast
import math
from pathlib import Path

import pytest

import schemas
from screenplay import body as B
from screenplay import compiler, ik, verb_helpers, verbs_core, verbs_objects, verbs_vehicles
from screenplay import verb_helpers as H
from screenplay.motion import resolve_marks
from screenplay.schema import Action, Shot
from screenplay.state import StateError, World
from screenplay.tracks import TrackSet

MARKS = resolve_marks({"spot": [0.2, 0.0], "near": [0.3, 0.0]}, {})


def person(pid: str, build: str = "average") -> schemas.Person:
    return schemas.Person(id=pid, family_id="f", name=pid, character={"color": "#123456", "build": build})


def prop(pid: str, size: str = "hand", sockets: dict | None = None) -> schemas.Prop:
    return schemas.Prop(
        id=pid, name=pid, size=size, art={"body": None}, anchor=[0.5, 1.0], grip=[0.5, 0.0], sockets=sockets or {}
    )


def scene(start: dict | None = None, prop_start: dict | None = None) -> tuple[World, TrackSet]:
    world = World(
        [person("dad", "tall"), person("mom"), person("kid", "small")],
        [prop("books"), prop("cup"), prop("chair", "furniture", {"seat": [0.5, 0.5]})],
    )
    shot = Shot.model_validate({
        "id": "s1", "set": "x",
        "start": start if start is not None else {"dad": {"at": "spot", "facing": "right"}},
        "propStart": prop_start if prop_start is not None else {"cup": {"at": "near"}},
    })
    world.begin_shot(shot, MARKS, False)
    return world, TrackSet()


def action(**kw) -> Action:
    return Action.model_validate({"t": 0, **kw})


def raised(fn, *args, **kw) -> StateError:
    with pytest.raises(StateError) as e:
        fn(*args, **kw)
    return e.value


# ------------------------------------------------------------------ the module itself

def test_not_a_verb_module_and_imports_none():
    """compiler._discover_handlers scans screenplay.verbs_*; verb_helpers must not be one, and
    must not import one (every verb module imports it)."""
    assert not verb_helpers.__name__.split(".")[-1].startswith("verbs_")
    assert not hasattr(verb_helpers, "HANDLERS")
    tree = ast.parse(Path(verb_helpers.__file__).read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
            imported.update(f"{node.module}.{a.name}" for a in node.names)
        elif isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
    assert not [m for m in imported if m.split(".")[-1].startswith("verbs_")], imported
    assert compiler.HANDLER_PROBLEMS == []


def test_every_public_name_is_exported():
    for name in H.__all__:
        assert hasattr(H, name), name


def test_private_names_the_tests_use_are_the_shared_helpers():
    assert verbs_core._crouch_pick is H.crouch_pick
    assert verbs_core._crouch_tries is H.crouch_tries
    assert verbs_core._crouch_solution is H.crouch_solution
    assert verbs_core._kneeling_ground is H.kneeling_ground
    assert verbs_core._bent_shoulder is H.bent_shoulder
    assert verbs_core._solve_bent_arm is H.solve_bent_arm
    assert verbs_core._crouch_effort is H.crouch_effort
    assert verbs_core._stand_up_keys is H.stand_up_keys
    assert verbs_core._sign is H.sign
    assert verbs_core.CROUCH_MAX_BEND == H.CROUCH_MAX_BEND == 1.2
    assert verbs_core.CROUCH_DEEP_BEND == 1.5  # verbs_core's own: only pick_up's step-to-a-spot uses it
    assert callable(verbs_vehicles._seated) and callable(verbs_vehicles._stacked)
    assert callable(verbs_objects._open_close)


# ------------------------------------------------------------------------- checks

def test_require_param_returns_the_value():
    assert H.require_param(action(do="sit_on", who="dad", what="chair"), "what") == "chair"


@pytest.mark.parametrize(
    ("do", "field", "message"),
    [
        ("hug", "target", "hug needs target"),
        ("get_in", "what", "get_in needs what"),
        ("drive_to", "to", "drive_to needs to"),
        ("set_state", "state", "set_state needs state"),
        ("enter", "from_", "enter needs from"),  # the attribute is from_, the message says from
    ],
)
def test_require_param_names_the_verb_and_the_field(do, field, message):
    e = raised(H.require_param, action(do=do), field)
    assert (e.code, e.reason) == ("missing_param", message)


def test_require_value():
    assert H.require_value("dad", "who") == "dad"
    e = raised(H.require_value, None, "to")
    assert (e.code, e.reason) == ("missing_param", 'it needs "to"')


def test_require_person():
    world, _ = scene()
    assert H.require_person(world, "mom") == "mom"
    e = raised(H.require_person, world, "uncle")
    assert (e.code, e.reason) == ("unknown_id", "uncle is not in the cast")
    e = raised(H.require_person, world, None)
    assert (e.code, e.reason) == ("unknown_id", "None is not in the cast")
    e = raised(H.require_person, world, "uncle", code="unknown_person")  # verbs_core's code
    assert (e.code, e.reason) == ("unknown_person", "uncle is not in the cast")


def test_require_on_stage():
    world, _ = scene()
    H.require_on_stage(world, "dad")  # on stage: no error
    e = raised(H.require_on_stage, world, "mom")
    assert (e.code, e.reason) == ("offstage", "mom is offstage; use enter first")
    assert H.OFFSTAGE_HINT == "; use enter first"
    e = raised(H.require_on_stage, world, "mom", hint="")  # pick_up's wording
    assert (e.code, e.reason) == ("offstage", "mom is offstage")


def test_require_prop_on_stage():
    world, _ = scene()
    assert H.require_prop_on_stage(world, "cup") == world.props["cup"].world_pos
    assert H.require_prop_on_stage(world, "cup") == pytest.approx(MARKS["near"])
    e = raised(H.require_prop_on_stage, world, "books")  # never placed
    assert (e.code, e.reason) == ("not_on_stage", "books is not on stage")


# ------------------------------------------------------------------------- values

@pytest.mark.parametrize(("v", "s"), [(0.5, 1), (-0.5, -1), (0.0, 0), (1e-10, 0), (-1e-10, 0), (2e-9, 1)])
def test_sign(v, s):
    assert H.sign(v) == s


def test_vec3():
    out = H.vec3([1, 2.5, -3])
    assert out == (1.0, 2.5, -3.0)
    assert isinstance(out, tuple) and all(type(c) is float for c in out)
    with pytest.raises(ValueError):
        H.vec3([1.0, 2.0])


def test_bone_after_prefers_emitted_keys_then_tracks_then_zero():
    tracks = TrackSet()
    tracks.add("puppet:dad", "torso.rot", 0.0, -0.4)
    emitted = B.last_values([("puppet:dad", "arm_r.upper.rot", 1.0, 0.7), ("puppet:dad", "arm_r.upper.rot", 2.0, 0.9)])
    assert H.bone_after(emitted, tracks, "dad", "arm_r.upper", 5.0) == 0.9  # the last emitted value
    assert H.bone_after(emitted, tracks, "dad", "torso", 5.0) == -0.4      # the track
    assert H.bone_after(emitted, tracks, "dad", "leg_l.upper", 5.0) == 0.0  # neither
    assert H.bone_after({}, tracks, "mom", "torso", 5.0) == 0.0           # another puppet's track


# ---------------------------------------------------------------------- stand up

def test_stand_up_keys_standing_puppet_gets_nothing():
    world, tracks = scene()
    assert H.stand_up_keys(world, tracks, "dad", 2.0) == ([], 2.0)


def test_stand_up_keys_seated_on_a_prop_gets_nothing():
    world, tracks = scene(prop_start={"chair": {"at": "near"}})
    world.sit_on("dad", "chair", "seat")
    assert H.stand_up_keys(world, tracks, "dad", 2.0) == ([], 2.0)


def test_stand_up_keys_from_kneeling_returns_tracked_bones_to_zero_and_keeps_held_arms():
    world, tracks = scene()
    p = world.people["dad"]
    world.pick_up("dad", "cup", "hand_r")  # holding: arm_r keeps its hold pose
    world.kneel("dad")
    body = ik.Body("dad", (p.pos[0], p.pos[1]), p.facing, p.build)
    tracks.extend(B.kneel_keys(body, 0.0, {}, z=p.pos[2]))
    tracks.add("puppet:dad", "arm_r.upper.rot", 0.0, 0.3)
    keys, t_up = H.stand_up_keys(world, tracks, "dad", 2.0)
    assert t_up == 2.0 + B.POSE_SECONDS
    channels = {(k[1], k[2]): k[3] for k in keys}
    assert ("arm_r.upper.rot", 2.0) not in channels  # a held arm is left alone
    assert channels[("leg_l.upper.rot", 2.0 + B.POSE_SECONDS)] == 0.0
    assert not any(ch.startswith("arm_l") for ch, _ in channels)  # only bones with a track
    assert channels[("root.position", 2.0 + B.POSE_SECONDS)] == [p.pos[0], p.pos[1], p.pos[2]]


# ------------------------------------------------------------------------ crouch

def test_kneeling_ground_is_the_kneel_root_offset():
    off = ik.POSES["poses"]["kneel"]["rootOffset"]
    for facing in (1, -1):
        body = ik.Body("dad", (0.2, 0.1), facing, "tall")
        s = ik.scale("tall")
        assert H.kneeling_ground(body) == pytest.approx((0.2 + facing * off[0] * s, 0.1 + off[1] * s))


def test_bent_arm_helpers_are_the_ik_formula():
    for bend in (0.0, -0.5):
        assert H.bent_shoulder("arm_l", (0.0, 0.0), 1, "average", bend) == ik.shoulder_world(
            "arm_l", (0.0, 0.0), 1, "average", torso=bend)
        assert H.solve_bent_arm("arm_l", (0.2, 0.1), (0.0, 0.0), 1, "average", bend) == ik.arm_solution(
            "arm_l", (0.2, 0.1), (0.0, 0.0), 1, "average", torso=bend)


def test_crouch_effort():
    assert H.crouch_effort(False, -0.3) == pytest.approx(0.3)
    assert H.crouch_effort(True, -0.3) == pytest.approx(0.3 + H.CROUCH_KNEEL_COST)


def test_crouch_tries_cover_every_bend_standing_then_kneeling_least_effort_first():
    body = ik.Body("dad", (0.0, 0.0), 1, "tall")
    tries = H.crouch_tries(body, "arm_r", (0.3, 0.0), can_kneel=True)
    steps = int(round(H.CROUCH_MAX_BEND / H.CROUCH_BEND_STEP)) + 1
    assert len(tries) == 2 * steps
    efforts = [H.crouch_effort(kneel, bend) for _, kneel, bend, _ in tries]
    assert efforts == sorted(efforts)
    assert len(H.crouch_tries(body, "arm_r", (0.3, 0.0), can_kneel=False)) == steps
    assert len(H.crouch_tries(body, "arm_r", (0.3, 0.0), can_kneel=False, max_bend=0.5)) == 11


def test_crouch_pick_prefers_a_comfortable_reach():
    length = 1.0
    full = 2 * length
    snug = (0.9 * full, False, -0.1, (0.1, 0.2))
    stretched = (0.99 * full, False, 0.0, (0.0, 0.0))
    miss = (3.0, False, 0.0, None)
    assert H.crouch_pick([miss, stretched, snug], length) == (True, snug)
    assert H.crouch_pick([miss, stretched], length) == (False, stretched)
    assert H.crouch_pick([miss], length) is None


def test_crouch_solution_reaches_the_floor_and_matches_the_ik():
    body = ik.Body("dad", (0.0, 0.0), 1, "tall")
    grip = (0.15, 0.0)  # on the floor in front of a tall puppet: kneel and bend
    kneel, bend, angles = H.crouch_solution(body, "arm_r", grip, can_kneel=True)
    assert kneel and -H.CROUCH_MAX_BEND <= bend < 0.0
    with pytest.raises(ik.OutOfReach):  # a bend alone cannot get there
        H.crouch_solution(body, "arm_r", grip, can_kneel=False)
    ground = H.kneeling_ground(body) if kneel else body.ground
    assert ik.arm_solution("arm_r", grip, ground, 1, "tall", torso=bend)[1] == angles


def test_crouch_solution_raises_out_of_reach_with_the_closest_miss():
    body = ik.Body("dad", (0.0, 0.0), 1, "tall")
    with pytest.raises(ik.OutOfReach) as e:
        H.crouch_solution(body, "arm_r", (3.0, 0.0), can_kneel=True)
    tries = H.crouch_tries(body, "arm_r", (3.0, 0.0), can_kneel=True)
    s = ik.scale("tall")
    length = ik.RIG["bones"]["arm_r.upper"]["length"]
    assert e.value.who == "dad" and e.value.arm == "arm_r"
    assert e.value.distance == pytest.approx(min(t[0] for t in tries) * s)
    assert e.value.reach == pytest.approx(2 * length * s)


@pytest.mark.parametrize("kneel_allowed", [True, False])
def test_crouch_reach_keys(kneel_allowed):
    tracks = TrackSet()
    tracks.add("puppet:dad", "torso.rot", 0.0, -0.1)
    body = ik.Body("dad", (0.0, 0.0), 1, "tall", -0.1)
    grip = (0.15, 0.15)  # kneels when it may, else a deep bend reaches it standing
    keys, end = H.crouch_reach_keys(
        tracks, [], body, 1.0, (0.2, 0.3), "cup", grip, "arm_r", 0.5,
        can_kneel=kneel_allowed, arm_after=(0.5, 0.6), parent="ground", position=(0.35, 0.0, 0.5),
    )
    kneel, bend, reach = H.crouch_solution(body, "arm_r", grip, kneel_allowed)
    t_down, t_grab = 1.0 + B.POSE_SECONDS, 1.0 + B.POSE_SECONDS + B.REACH_SECONDS
    assert end == pytest.approx(t_grab + B.POSE_SECONDS)
    by = {(k[1], k[2]): k[3] for k in keys if k[0] == "puppet:dad"}
    assert [by[("torso.rot", t)] for t in (1.0, t_down, t_grab, end)] == [-0.1, bend, bend, -0.1]
    assert [by[("arm_r.upper.rot", t)] for t in (1.0, t_down, t_grab, end)] == [0.2, 0.2, reach[0], 0.5]
    assert [by[("arm_r.lower.rot", t)] for t in (1.0, t_down, t_grab, end)] == [0.3, 0.3, reach[1], 0.6]
    assert kneel == kneel_allowed  # this grip needs a kneel whenever one is allowed
    if kneel:  # down, held kneeling while reaching, then back up to where it stood
        assert by[("root.position", 1.0)] == [0.0, 0.0, 0.5]
        assert by[("root.position", end)] == [0.0, 0.0, 0.5]
        assert by[("leg_l.upper.rot", t_down)] == pytest.approx(ik.POSES["poses"]["kneel"]["leg_l.upper"])
    else:  # already down: legs and root are left alone
        assert not any(ch == "root.position" or ch.startswith("leg_") for ch, _ in by)
    assert keys[-2:] == [("prop:cup", "parent", t_grab, "ground"), ("prop:cup", "position", t_grab, [0.35, 0.0, 0.5])]


def test_crouch_reach_keys_reads_bones_from_the_keys_before_it():
    tracks = TrackSet()
    tracks.add("puppet:dad", "torso.rot", 0.0, -0.1)
    body = ik.Body("dad", (0.0, 0.0), 1, "tall")
    before = [("puppet:dad", "torso.rot", 0.5, 0.0)]  # e.g. standing up straightened it
    keys, _ = H.crouch_reach_keys(
        tracks, before, body, 1.0, (0.0, 0.0), "cup", (0.15, 0.0), "arm_r", 0.5,
        can_kneel=True, arm_after=(0.0, 0.0), parent="puppet:dad.hand_r", position=(0, 0, 0),
    )
    assert keys[0] == ("puppet:dad", "torso.rot", 1.0, 0.0)
    assert keys[-1] == ("prop:cup", "position", 1.0 + B.POSE_SECONDS + B.REACH_SECONDS, [0.0, 0.0, 0.0])
    assert all(isinstance(c, float) for c in keys[-1][3])


def test_crouch_constants():
    assert (H.CROUCH_BEND_STEP, H.CROUCH_MAX_BEND, H.CROUCH_COMFORT, H.CROUCH_KNEEL_COST) == (0.05, 1.2, 0.95, 0.5)
    assert math.isclose(H.CROUCH_MAX_BEND / H.CROUCH_BEND_STEP, 24)
