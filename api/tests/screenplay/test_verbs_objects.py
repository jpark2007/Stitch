"""Object verb handlers (api/screenplay/verbs_objects.py): put_on, place_on, open, close, set_state.

Every handler is checked through the keys it leaves in a TrackSet (sampled with value_at at the
key times), the world state afterwards, and, for each error, the exact code and reason with the
world and the tracks left untouched.
"""

import inspect
import json
import math
from pathlib import Path

import pytest

import schemas
from screenplay.body import (
    POSE_SECONDS,
    PUT_ON_SETTLE_MAX,
    PUT_ON_SETTLE_SECONDS,
    REACH_SECONDS,
    PropGeom,
    anchor_from_grip,
    grip_from_anchor,
)
from screenplay.compiler import compile as compile_screenplay
from screenplay.ik import POSES as RIG_POSES
from screenplay.ik import RIG, Body, OutOfReach, body_socket, scale, socket_world
from screenplay.motion import mark_position
from screenplay.schema import Action, Screenplay, Shot
from screenplay.state import StateError, World
from screenplay.tracks import TrackSet, evaluate
from screenplay.verbs_objects import (
    DRESSED_ACTIVITY,
    HANDLERS,
    apply_close,
    apply_open,
    apply_place_on,
    apply_put_on,
    apply_set_state,
)

R = REACH_SECONDS
P = POSE_SECONDS
POSES = RIG_POSES["poses"]
KNEEL_ROOT = POSES["kneel"]["rootOffset"]
SAMPLES = Path(__file__).resolve().parents[3] / "web" / "public" / "samples"
ACTION_START = 1.2
T = 3.2  # timeline time of every action below: shot time 2.0
HOLD = (0.3, 1.2)  # the hold pose's arm values


# ------------------------------------------------------------------ fixtures

def person(pid: str, build: str = "average") -> schemas.Person:
    return schemas.Person(id=pid, family_id="f", name=pid.title(), character={"color": "#123456", "build": build})


def prop(pid: str, size: str = "hand", sockets=None, states=None, grip=None, aspect: float = 1.0) -> schemas.Prop:
    return schemas.Prop(
        id=pid, name=pid, size=size, art={"body": None}, aspect=aspect,
        sockets=sockets or {}, states=states or {}, grip=grip,
    )


PEOPLE = [person("dad", "tall"), person("mom"), person("kid", "small")]
PROPS = [
    prop("hat"),
    prop("cap"),
    prop("jar", states={"open": {}, "closed": {}}, grip=[0.5, 0.2]),
    prop("box", "carried"),
    prop("lamp", "carried", states={"lit": {}, "out": {}}),
    prop("table", "furniture", {"top": [0.5, 0.0]}, aspect=1.5),
    prop("far_table", "furniture", {"top": [0.5, 0.0]}, aspect=1.5),
    prop("shelf", "furniture", {"low": [0.5, 0.5], "high": [0.5, 0.0]}),
    prop("stool", "furniture"),
    prop("cushion", sockets={"seat": [0.5, 0.3]}),
    prop("tray", "carried", {"top": [0.3, 0.0]}, aspect=1.5),
    prop("bowl", sockets={"rim": [0.5, 0.0]}),
]
MARKS = {
    "home": (0.0, 0.0, 0.5),
    "near": (0.15, 0.0, 0.5),
    "beside": (0.1, 0.0, 0.5),
    "far": (0.8, 0.0, 0.5),
    "ledge": (0.12, 0.35, 0.5),  # a raised surface within dad's reach
    "behind": (-0.2, 0.0, 0.5),
}
GEOM = {p.id: PropGeom.from_prop(p) for p in PROPS}
DAD = Body("dad", (0.0, 0.0), 1, "tall")


def shot(**kw) -> Shot:
    return Shot.model_validate({"id": "s1", "set": "room", **kw})


def make_world(start: dict, prop_start: dict | None = None) -> World:
    w = World(PEOPLE, PROPS)
    w.begin_shot(shot(start=start, propStart=prop_start or {}), MARKS, same_set_as_previous=False)
    w.now = round(T - ACTION_START, 6)
    return w


def make_locate(world: World, tracks: TrackSet):
    """The compiler's locate, for tests: people by root, props by anchor (held: the hand), marks."""
    def locate(name: str, t: float):
        if name in world.people:
            v = tracks.value_at(f"puppet:{name}", "root.position", t)
            return tuple(v) if v is not None else world.people[name].pos
        if name in world.props:
            st = world.props[name]
            if st.world_pos is not None:
                return st.world_pos
            h = world.holder(name)
            if h is not None:
                p = world.people[h[0]]
                rots = {b: tracks.value_at(f"puppet:{p.id}", f"{b}.rot", t) or 0.0
                        for b in ("arm_r.upper", "arm_r.lower", "arm_l.upper", "arm_l.lower")}
                x, y = body_socket(Body(p.id, (p.pos[0], p.pos[1]), p.facing, p.build), h[1], rots)
                return (x, y, 0.5)
        if name in MARKS:
            return MARKS[name]
        raise StateError("unknown_target", f"{name} is not a person, prop or mark")
    return locate


def act(**kw) -> Action:
    return Action.model_validate({"t": T - ACTION_START, **kw})


def run(handler, world: World, tracks: TrackSet, **kw) -> float:
    return handler(world, tracks, act(**kw), T, ACTION_START, make_locate(world, tracks))


def at(tracks: TrackSet, target: str, channel: str, t: float):
    return tracks.value_at(target, channel, t)


def dump(tracks: TrackSet) -> list:
    return [tr.model_dump() for tr in tracks.tracks()]


def rejects(code: str, reason: str, handler, world: World, tracks: TrackSet, **kw) -> StateError:
    """Assert the handler raises StateError(code, reason) and leaves the world and tracks as they were."""
    before_w, before_t = world.copy(), dump(tracks)
    with pytest.raises(StateError) as ei:
        run(handler, world, tracks, **kw)
    assert (ei.value.code, ei.value.reason) == (code, reason)
    assert world == before_w, "world changed on error"
    assert dump(tracks) == before_t, "keys emitted on error"
    return ei.value


def out_of_reach(handler, world: World, tracks: TrackSet, **kw) -> OutOfReach:
    before_w, before_t = world.copy(), dump(tracks)
    with pytest.raises(OutOfReach) as ei:
        run(handler, world, tracks, **kw)
    assert world == before_w, "world changed on error"
    assert dump(tracks) == before_t, "keys emitted on error"
    return ei.value


def arm_at(tracks: TrackSet, who: str, arm: str, t: float) -> tuple[float, float]:
    return (at(tracks, f"puppet:{who}", f"{arm}.upper.rot", t) or 0.0,
            at(tracks, f"puppet:{who}", f"{arm}.lower.rot", t) or 0.0)


def hand_at(tracks: TrackSet, body: Body, arm: str, t: float) -> tuple[float, float]:
    """World position of the arm's hand socket at timeline t, from the emitted rotations."""
    u, lo = arm_at(tracks, body.id, arm, t)
    return body_socket(body, "hand_r" if arm == "arm_r" else "hand_l", {f"{arm}.upper": u, f"{arm}.lower": lo})


def holding_hat(tracks: TrackSet) -> World:
    """Dad at home holding the hat in hand_r, arm in the hold pose since t=0."""
    tracks.extend([("puppet:dad", "arm_r.upper.rot", 0.0, HOLD[0]), ("puppet:dad", "arm_r.lower.rot", 0.0, HOLD[1])])
    return make_world({"dad": {"at": "home", "holding": "hat", "facing": "right"}})


def r3(v):
    return [round(x, 3) for x in v]


# -------------------------------------------------------------------- contract

def test_handlers_are_exactly_the_object_verbs():
    assert set(HANDLERS) == {"put_on", "place_on", "open", "close", "set_state"}
    assert HANDLERS["put_on"] is apply_put_on and HANDLERS["set_state"] is apply_set_state
    for fn in HANDLERS.values():
        assert list(inspect.signature(fn).parameters) == ["world", "tracks", "action", "t", "action_start", "locate"]


# ---------------------------------------------------------------------- put_on

def test_put_on_from_the_hand():
    tracks = TrackSet()
    w = holding_hat(tracks)
    end = run(apply_put_on, w, tracks, do="put_on", who="dad", what="hat")
    assert end == pytest.approx(T + 2 * R)
    # the arm starts from the hold pose, carries the hat to the head, then rests
    assert arm_at(tracks, "dad", "arm_r", T) == pytest.approx(HOLD)
    head = body_socket(DAD, "head", {})
    assert hand_at(tracks, DAD, "arm_r", T + R) == pytest.approx(grip_from_anchor(GEOM["hat"], head), abs=2e-3)
    assert arm_at(tracks, "dad", "arm_r", T + 2 * R) == (0.0, 0.0)
    assert at(tracks, "prop:hat", "parent", T + R) == "puppet:dad.head"
    assert at(tracks, "prop:hat", "position", T + R) == [0.0, 0.0, 0.0]
    assert w.holder("hat") == ("dad", "head") and w.people["dad"].holding == {"head": "hat"}


def test_put_on_straight_off_the_ground():
    tracks = TrackSet()
    w = make_world({"dad": {"at": "home"}}, {"hat": {"at": "ledge"}})
    end = run(apply_put_on, w, tracks, do="put_on", who="dad", what="hat")
    assert end == pytest.approx(T + 3 * R)
    grip = grip_from_anchor(GEOM["hat"], MARKS["ledge"])
    assert hand_at(tracks, DAD, "arm_r", T + R) == pytest.approx(grip, abs=2e-3)
    assert at(tracks, "prop:hat", "parent", T + R) == "puppet:dad.hand_r"
    assert at(tracks, "prop:hat", "parent", T + 2 * R) == "puppet:dad.head"
    head_grip = grip_from_anchor(GEOM["hat"], body_socket(DAD, "head", {}))
    assert hand_at(tracks, DAD, "arm_r", T + 2 * R) == pytest.approx(head_grip, abs=2e-3)
    assert arm_at(tracks, "dad", "arm_r", T + 3 * R) == (0.0, 0.0)
    assert w.holder("hat") == ("dad", "head")


def test_put_on_someone_else_uses_their_head_and_keeps_them_busy():
    tracks = TrackSet()
    w = holding_hat(tracks)
    w.begin_shot(shot(start={"mom": {"at": "beside", "facing": "left"}}), MARKS, same_set_as_previous=True)
    w.now = round(T - ACTION_START, 6)
    end = run(apply_put_on, w, tracks, do="put_on", who="dad", what="hat", target="mom")
    mom = Body("mom", (0.1, 0.0), -1, "average")
    assert hand_at(tracks, DAD, "arm_r", T + R) == pytest.approx(
        grip_from_anchor(GEOM["hat"], body_socket(mom, "head", {})), abs=2e-3)
    assert at(tracks, "prop:hat", "parent", T + R) == "puppet:mom.head"
    assert w.holder("hat") == ("mom", "head") and w.people["dad"].holding == {}
    assert w.people["mom"].busy_until == pytest.approx(end - ACTION_START)
    assert w.people["mom"].activity == DRESSED_ACTIVITY


def test_put_on_errors():
    tracks = TrackSet()
    w = make_world({"dad": {"at": "home"}, "mom": {"at": "beside", "holding": "hat"}, "kid": {"at": "far"}},
                   {"cap": {"at": "ledge"}})
    rejects("not_holding", "mom is holding it", apply_put_on, w, tracks, do="put_on", who="dad", what="hat")
    rejects("not_on_stage", "box is not on stage", apply_put_on, w, tracks, do="put_on", who="dad", what="box")
    rejects("offstage", "dad is offstage", apply_put_on,
            make_world({"dad": {"at": "offstage_left"}}, {"cap": {"at": "ledge"}}), tracks,
            do="put_on", who="dad", what="cap")
    w.occupy("mom", 9.0, "waving")
    rejects("busy", "mom is still waving until t=9", apply_put_on, w, tracks, do="put_on", who="dad", what="cap",
            target="mom")


def test_put_on_a_prop_someone_sits_on():
    """Regression: put_on straight off the ground skipped pick_up's seat checks."""
    tracks = TrackSet()
    w = make_world({"dad": {"at": "home"}, "kid": {"at": "beside", "in": "cushion.seat"}}, {"cushion": {"at": "ledge"}})
    rejects("occupied", "kid is sitting on it", apply_put_on, w, tracks, do="put_on", who="dad", what="cushion")
    assert w.people["kid"].seat == ("cushion", "seat") and w.props["cushion"].parent == "ground"
    w2 = make_world({"dad": {"at": "home", "in": "cushion.seat"}}, {"cushion": {"at": "ledge"}})
    rejects("occupied", "dad is sitting on it", apply_put_on, w2, tracks, do="put_on", who="dad", what="cushion")


TOP_CAP = prop("top_cap", grip=[0.5, 0.0])  # held by its top edge: the exact grip point is above the head


def top_cap_world(start: dict, prop_start: dict | None = None) -> World:
    w = World(PEOPLE, [*PROPS, TOP_CAP])
    w.begin_shot(shot(start=start, propStart=prop_start or {}), MARKS, same_set_as_previous=False)
    w.now = round(T - ACTION_START, 6)
    return w


def test_put_on_settle_fits_inside_the_end_time():
    assert PUT_ON_SETTLE_SECONDS <= REACH_SECONDS


def test_put_on_from_the_hand_with_a_grip_above_the_head_settles_before_the_end():
    tracks = TrackSet()
    tracks.extend([("puppet:dad", "arm_r.upper.rot", 0.0, HOLD[0]), ("puppet:dad", "arm_r.lower.rot", 0.0, HOLD[1])])
    w = top_cap_world({"dad": {"at": "home", "holding": "top_cap", "facing": "right"}})
    end = run(apply_put_on, w, tracks, do="put_on", who="dad", what="top_cap")
    assert end == pytest.approx(T + 2 * R)
    head = body_socket(DAD, "head", {})
    assert at(tracks, "prop:top_cap", "parent", T + R) == "puppet:dad.head"
    offset = at(tracks, "prop:top_cap", "position", T + R)
    assert 0.0 < math.hypot(offset[0], offset[1]) <= PUT_ON_SETTLE_MAX
    # where the hand lets go is where the stage puts the anchor at the swap
    geom = PropGeom.from_prop(TOP_CAP)
    hand = hand_at(tracks, DAD, "arm_r", T + R)
    assert anchor_from_grip(geom, hand) == pytest.approx((head[0] + offset[0], head[1] + offset[1]), abs=2e-3)
    t_settled = T + R + PUT_ON_SETTLE_SECONDS
    assert t_settled <= end
    assert at(tracks, "prop:top_cap", "position", t_settled) == [0.0, 0.0, 0.0]
    assert at(tracks, "prop:top_cap", "position", end) == [0.0, 0.0, 0.0]
    assert arm_at(tracks, "dad", "arm_r", end) == (0.0, 0.0)
    assert w.holder("top_cap") == ("dad", "head")


def test_put_on_straight_off_the_ground_with_a_grip_above_the_head():
    """The ground path: reach the grip on the ledge, take it, then the clamped carry to the head."""
    tracks = TrackSet()
    w = top_cap_world({"dad": {"at": "home"}}, {"top_cap": {"at": "ledge"}})
    end = run(apply_put_on, w, tracks, do="put_on", who="dad", what="top_cap")
    assert end == pytest.approx(T + 3 * R)
    geom = PropGeom.from_prop(TOP_CAP)
    assert hand_at(tracks, DAD, "arm_r", T + R) == pytest.approx(grip_from_anchor(geom, MARKS["ledge"]), abs=2e-3)
    assert at(tracks, "prop:top_cap", "parent", T + R) == "puppet:dad.hand_r"
    assert at(tracks, "prop:top_cap", "position", T + R) == [0.0, 0.0, 0.0]
    t_on = T + 2 * R
    assert at(tracks, "prop:top_cap", "parent", t_on) == "puppet:dad.head"
    offset = at(tracks, "prop:top_cap", "position", t_on)
    assert 0.0 < math.hypot(offset[0], offset[1]) <= PUT_ON_SETTLE_MAX
    assert t_on + PUT_ON_SETTLE_SECONDS <= end
    assert at(tracks, "prop:top_cap", "position", t_on + PUT_ON_SETTLE_SECONDS) == [0.0, 0.0, 0.0]
    assert arm_at(tracks, "dad", "arm_r", end) == (0.0, 0.0)
    assert w.holder("top_cap") == ("dad", "head")


def test_put_on_someone_out_of_reach():
    tracks = TrackSet()
    w = holding_hat(tracks)
    w.begin_shot(shot(start={"kid": {"at": "far"}}), MARKS, same_set_as_previous=True)
    w.now = round(T - ACTION_START, 6)
    err = out_of_reach(apply_put_on, w, tracks, do="put_on", who="dad", what="hat", target="kid")
    assert err.who == "dad" and err.arm == "arm_r"
    assert w.people["kid"].busy_until == 0.0


# -------------------------------------------------------------------- place_on

def test_place_on_a_prop_socket():
    tracks = TrackSet()
    w = make_world({"dad": {"at": "home", "holding": "jar"}}, {"table": {"at": "near"}})
    end = run(apply_place_on, w, tracks, do="place_on", who="dad", what="jar", on="table")
    assert end == pytest.approx(T + 2 * R)
    dest = (0.15, 0.4, 0.5)  # table top: furniture 0.4 tall, socket at top-centre
    assert hand_at(tracks, DAD, "arm_r", T + R) == pytest.approx(grip_from_anchor(GEOM["jar"], dest), abs=2e-3)
    assert at(tracks, "prop:jar", "parent", T + R) == "prop:table.top"
    assert at(tracks, "prop:jar", "position", T + R) == [0.0, 0.0, 0.0]
    assert arm_at(tracks, "dad", "arm_r", T + 2 * R) == (0.0, 0.0)
    assert w.props["jar"].parent == "prop:table.top" and w.props["jar"].world_pos == pytest.approx(dest)
    assert w.props["table"].occupants == {"top": "prop:jar"} and w.people["dad"].holding == {}


def test_place_on_a_named_socket_with_the_left_hand():
    tracks = TrackSet()
    w = make_world({"dad": {"at": "home", "holding": "hat"}}, {"shelf": {"at": "beside"}, "jar": {"at": "ledge"}})
    w.pick_up("dad", "jar")  # into hand_l
    assert w.holder("jar") == ("dad", "hand_l")
    run(apply_place_on, w, tracks, do="place_on", who="dad", what="jar", on="shelf", socket="high")
    dest = (0.1, 0.4, 0.5)
    assert at(tracks, "prop:jar", "parent", T + R) == "prop:shelf.high"
    assert hand_at(tracks, DAD, "arm_l", T + R) == pytest.approx(grip_from_anchor(GEOM["jar"], dest), abs=2e-3)
    assert arm_at(tracks, "dad", "arm_l", T + 2 * R) == (0.0, 0.0)
    assert not tracks.has_track("puppet:dad", "arm_r.upper.rot")
    assert w.props["jar"].world_pos == pytest.approx(dest)
    assert w.props["shelf"].occupants == {"high": "prop:jar"}


def test_place_on_a_mark():
    tracks = TrackSet()
    w = make_world({"dad": {"at": "home", "holding": "hat"}})
    run(apply_place_on, w, tracks, do="place_on", who="dad", what="hat", on="ledge")
    assert at(tracks, "prop:hat", "parent", T + R) == "ground"
    assert at(tracks, "prop:hat", "position", T + R) == r3(MARKS["ledge"])
    assert w.props["hat"].parent == "ground" and w.props["hat"].world_pos == MARKS["ledge"]


def test_place_on_errors():
    tracks = TrackSet()
    w = make_world({"dad": {"at": "home", "holding": "hat"}, "mom": {"at": "beside", "holding": "cap"}},
                   {"table": {"at": "near"}, "stool": {"at": "near"}, "jar": {"on": "table.top"}})
    kw = {"do": "place_on", "who": "dad", "what": "hat"}
    rejects("unknown_socket", "table has no socket shelf", apply_place_on, w, tracks, on="table", socket="shelf", **kw)
    rejects("socket_taken", "table.top is taken by jar", apply_place_on, w, tracks, on="table", socket="top", **kw)
    rejects("socket_taken", "table has no free socket", apply_place_on, w, tracks, on="table", **kw)
    rejects("no_socket", "stool has no sockets to place on", apply_place_on, w, tracks, on="stool", **kw)
    rejects("not_holding", "mom is holding it", apply_place_on, w, tracks,
            do="place_on", who="dad", what="cap", on="ledge")
    rejects("bad_target", "mom is a person; place_on needs a prop or a mark", apply_place_on, w, tracks, on="mom", **kw)
    rejects("unknown_target", "attic is not a person, prop or mark", apply_place_on, w, tracks, on="attic", **kw)
    rejects("unknown_socket", "ledge is a mark and has no socket top", apply_place_on, w, tracks,
            on="ledge", socket="top", **kw)
    rejects("not_on_stage", "shelf is not on stage", apply_place_on, w, tracks, on="shelf", **kw)


def test_place_on_a_carried_surface_or_itself():
    tracks = TrackSet()
    w = make_world({"dad": {"at": "home", "holding": "hat"}, "mom": {"at": "beside", "holding": "shelf"}})
    # the code says why the surface has no place on stage: someone carries it
    rejects("held", "mom is holding shelf", apply_place_on, w, tracks,
            do="place_on", who="dad", what="hat", on="shelf")
    w1 = make_world({"dad": {"at": "home", "holding": "hat"}, "mom": {"at": "beside", "holding": "tray"}},
                    {"bowl": {"on": "tray.top"}})
    rejects("held", "bowl is on tray, which mom is holding", apply_place_on, w1, tracks,
            do="place_on", who="dad", what="hat", on="bowl")
    w2 = make_world({"dad": {"at": "home", "holding": "shelf"}})
    rejects("cycle", "shelf cannot rest on shelf", apply_place_on, w2, tracks,
            do="place_on", who="dad", what="shelf", on="shelf", socket="high")


def test_place_on_a_mark_keys_are_unchanged():
    """A grip the standing arm reaches keeps exactly the plain reach: no torso, legs or root keys."""
    tracks = TrackSet()
    w = make_world({"dad": {"at": "home", "holding": "hat"}})
    end = run(apply_place_on, w, tracks, do="place_on", who="dad", what="hat", on="ledge")
    assert end == pytest.approx(T + 2 * R)
    assert sorted((tr.target, tr.channel, [list(k) for k in tr.keys]) for tr in tracks.tracks()) == sorted([
        ("prop:hat", "parent", [[3.6, "ground"]]),
        ("prop:hat", "position", [[3.6, [0.12, 0.35, 0.5]]]),
        ("puppet:dad", "arm_r.lower.rot", [[3.2, 0.0], [3.6, 2.306], [4.0, 0.0]]),
        ("puppet:dad", "arm_r.upper.rot", [[3.2, 0.0], [3.6, 0.04], [4.0, 0.0]]),
    ])


def test_place_on_out_of_reach():
    tracks = TrackSet()
    w = make_world({"dad": {"at": "home", "holding": "jar"}}, {"far_table": {"at": "far"}})
    err = out_of_reach(apply_place_on, w, tracks, do="place_on", who="dad", what="jar", on="far_table")
    assert err.who == "dad"
    # a floor mark across the stage stays out of reach even crouching: no auto-walk
    err = out_of_reach(apply_place_on, w, tracks, do="place_on", who="dad", what="jar", on="far")
    assert (err.who, err.arm) == ("dad", "arm_r")
    assert err.distance > err.reach > 0


# ------------------------------------------------- place_on a low target (crouch)

def keys_of(tracks: TrackSet, who: str, channel: str) -> list:
    for tr in tracks.tracks():
        if (tr.target, tr.channel) == (f"puppet:{who}", channel):
            return [tuple(k) for k in tr.keys]
    return []


def compiled_hand(tracks: TrackSet, w: World, who: str, socket: str, t: float) -> tuple[float, float]:
    """A hand socket from every bone the tracks animate (torso and legs included) and the root track."""
    root = tracks.value_at(f"puppet:{who}", "root.position", t) or w.people[who].pos
    rots = {}
    for tr in tracks.tracks():
        if tr.target == f"puppet:{who}" and tr.channel.endswith(".rot"):
            rots[tr.channel[:-4]] = tracks.value_at(tr.target, tr.channel, t)
    p = w.people[who]
    return socket_world(socket, rots, (root[0], root[1]), p.facing, p.build)


def test_place_on_a_floor_mark_kneels_bends_and_stands_back_up():
    tracks = TrackSet()
    tracks.extend([("puppet:dad", "arm_r.upper.rot", 0.0, HOLD[0]), ("puppet:dad", "arm_r.lower.rot", 0.0, HOLD[1])])
    w = make_world({"dad": {"at": "home", "holding": "hat", "facing": "right"}})
    end = run(apply_place_on, w, tracks, do="place_on", who="dad", what="hat", on="near")
    t_down, t_release, t_up = T + P, T + P + R, T + 2 * P + R
    assert end == pytest.approx(t_up)
    grip = grip_from_anchor(GEOM["hat"], MARKS["near"])
    assert compiled_hand(tracks, w, "dad", "hand_r", t_release) == pytest.approx(grip, abs=1e-3)
    assert at(tracks, "prop:hat", "parent", t_release) == "ground"
    assert at(tracks, "prop:hat", "position", t_release) == r3(MARKS["near"])
    torso = keys_of(tracks, "dad", "torso.rot")
    assert [k[0] for k in torso] == [round(t, 3) for t in (T, t_down, t_release, t_up)]
    assert torso[0][1] == 0.0 and torso[-1][1] == 0.0 and torso[1][1] == torso[2][1] < 0
    # the arm holds still while the body goes down, reaches over REACH_SECONDS, then rests
    assert arm_at(tracks, "dad", "arm_r", t_down) == pytest.approx(HOLD)
    assert arm_at(tracks, "dad", "arm_r", t_up) == (0.0, 0.0)
    # kneels on the way down and stands back up on the way out
    low = [round(KNEEL_ROOT[0] * scale("tall"), 3), round(KNEEL_ROOT[1] * scale("tall"), 3), 0.5]
    assert keys_of(tracks, "dad", "root.position") == [
        (round(T, 3), [0.0, 0.0, 0.5]), (round(t_down, 3), low), (round(t_release, 3), low),
        (round(t_up, 3), [0.0, 0.0, 0.5]),
    ]
    assert at(tracks, "puppet:dad", "leg_r.lower.rot", t_release) == pytest.approx(POSES["kneel"]["leg_r.lower"])
    for bone in (b for b in POSES["kneel"] if b.startswith("leg_")):
        assert at(tracks, "puppet:dad", f"{bone}.rot", t_up) == 0.0
    assert w.props["hat"].parent == "ground" and w.props["hat"].world_pos == MARKS["near"]
    assert w.people["dad"].holding == {} and w.people["dad"].pose == "stand"


def test_place_on_a_low_prop_socket_crouches_and_parents_to_the_socket():
    tracks = TrackSet()
    w = make_world({"dad": {"at": "home", "holding": "jar"}}, {"cushion": {"at": "near"}})
    end = run(apply_place_on, w, tracks, do="place_on", who="dad", what="jar", on="cushion")
    t_release = T + P + R
    assert end == pytest.approx(T + 2 * P + R)
    dest = w.props["jar"].world_pos
    assert dest is not None and dest[1] < 0.1  # a low surface
    assert at(tracks, "prop:jar", "parent", t_release) == "prop:cushion.seat"
    assert at(tracks, "prop:jar", "position", t_release) == [0.0, 0.0, 0.0]
    assert compiled_hand(tracks, w, "dad", "hand_r", t_release) == pytest.approx(
        grip_from_anchor(GEOM["jar"], dest), abs=1e-3)
    assert w.props["cushion"].occupants == {"seat": "prop:jar"}


def test_place_on_a_floor_mark_while_kneeling_only_bends_and_stays_down():
    tracks = TrackSet()
    low = [round(KNEEL_ROOT[0] * scale("tall"), 3), round(KNEEL_ROOT[1] * scale("tall"), 3), 0.5]
    tracks.extend([("puppet:dad", "root.position", 0.0, low), ("puppet:dad", "torso.rot", 0.0, -0.1)])
    w = make_world({"dad": {"at": "home", "holding": "hat", "pose": "kneel"}})
    end = run(apply_place_on, w, tracks, do="place_on", who="dad", what="hat", on="near")
    t_release = T + P + R
    assert end == pytest.approx(T + 2 * P + R)
    assert keys_of(tracks, "dad", "root.position") == [(0.0, low)]  # stays down
    assert not any(tr.channel.startswith("leg_") for tr in tracks.tracks())
    torso = keys_of(tracks, "dad", "torso.rot")
    assert torso[1][1] < 0 and torso[-1] == (round(T + 2 * P + R, 3), -0.1)  # back to its value at t
    grip = grip_from_anchor(GEOM["hat"], MARKS["near"])
    assert compiled_hand(tracks, w, "dad", "hand_r", t_release) == pytest.approx(grip, abs=1e-3)
    assert w.people["dad"].pose == "kneel"


def test_place_on_a_floor_mark_while_seated_only_bends():
    tracks = TrackSet()
    w = make_world({"dad": {"at": "home", "in": "cushion.seat", "holding": "hat"}}, {"cushion": {"at": "home"}})
    sx, sy, _ = w.socket_world("cushion", "seat")
    seated = [round(sx, 3), round(sy - RIG["hipHeight"] * scale("tall"), 3), 0.5]  # body.sit_keys' root
    tracks.add("puppet:dad", "root.position", 0.0, seated)
    run(apply_place_on, w, tracks, do="place_on", who="dad", what="hat", on="near")
    assert keys_of(tracks, "dad", "root.position") == [(0.0, seated)]  # no kneel: still seated
    assert not any(tr.channel.startswith("leg_") for tr in tracks.tracks())
    assert keys_of(tracks, "dad", "torso.rot")[1][1] < 0
    grip = grip_from_anchor(GEOM["hat"], MARKS["near"])
    assert compiled_hand(tracks, w, "dad", "hand_r", T + P + R) == pytest.approx(grip, abs=1e-3)
    assert w.people["dad"].seat == ("cushion", "seat") and w.props["hat"].world_pos == MARKS["near"]


def test_place_on_a_floor_mark_out_of_reach_even_crouching_is_atomic():
    tracks = TrackSet()
    w = make_world({"dad": {"at": "home", "holding": "hat"}})
    err = out_of_reach(apply_place_on, w, tracks, do="place_on", who="dad", what="hat", on="far")
    assert (err.who, err.arm) == ("dad", "arm_r") and err.distance > err.reach
    assert w.holder("hat") == ("dad", "hand_r")


# ------------------------------------- place_on a low target, through the compiler

def _kitchen_compile(actions: list, start: dict | None = None):
    memory = json.loads((SAMPLES / "hymn_books.memory.json").read_text())
    sets = {"kitchen": schemas.SetDef(
        id="kitchen", name="kitchen",
        layers=[{"art": None, "depth": 0, "height": 1.0}, {"art": None, "depth": 1, "height": 0.4}],
        marks={"stove": [0.3, 0.0], "door": [0.8, 0.0]},
    )}
    props = {p["id"]: schemas.Prop.model_validate(p) for p in memory["props"]}
    people = {p["id"]: schemas.Person.model_validate(p) for p in memory["people"]}
    sp = Screenplay.model_validate({
        "memoryId": "m", "cast": ["dad"], "props": ["hymn_books"],
        "shots": [{"id": "s1", "set": "kitchen",
                   "start": start or {"dad": {"at": "stove", "holding": "hymn_books", "facing": "right"}},
                   "actions": actions}],
    })
    return compile_screenplay(sp, sets=sets, props=props, people=people), props, people


def _timeline_keys(res, target: str, channel: str) -> list:
    for tr in res.timeline.tracks:
        if (tr.target, tr.channel) == (target, channel):
            return [tuple(k) for k in tr.keys]
    return []


def _compiled_hand(res, people, who: str, t: float) -> tuple[float, float]:
    tl = res.timeline.tracks
    root = evaluate(tl, f"puppet:{who}", "root.position", t)
    facing = evaluate(tl, f"puppet:{who}", "root.facing", t)
    rots = {tr.channel[:-4]: evaluate(tl, tr.target, tr.channel, t)
            for tr in tl if tr.target == f"puppet:{who}" and tr.channel.endswith(".rot")}
    assert "torso" in rots
    return socket_world("hand_r", rots, (root[0], root[1]), int(facing), people[who].character.build)


def test_compiled_place_on_the_stove_mark_crouches_and_stands_back_up():
    """Regression: place_on a ground-level mark failed with out_of_reach (target 0.34 from the
    shoulder, reach 0.19); pick_up from the same spot worked."""
    res, props, people = _kitchen_compile([{"t": 2, "do": "place_on", "who": "dad", "what": "hymn_books", "on": "stove"}])
    assert res.ok, [e.message for e in res.errors]
    (t_release, parent), = [k for k in _timeline_keys(res, "prop:hymn_books", "parent") if k[1] == "ground"]
    assert parent == "ground"
    position = dict(_timeline_keys(res, "prop:hymn_books", "position"))[t_release]
    stove = mark_position([0.3, 0.0])  # the kitchen's stove mark, in stage units
    assert position == pytest.approx(list(stove), abs=1e-3)
    grip = grip_from_anchor(PropGeom.from_prop(props["hymn_books"]), position)
    assert _compiled_hand(res, people, "dad", t_release) == pytest.approx(grip, abs=1e-3)
    torso = _timeline_keys(res, "puppet:dad", "torso.rot")
    t0 = torso[0][0]
    assert t_release == pytest.approx(t0 + P + R) and torso[-1] == (pytest.approx(t0 + 2 * P + R), 0.0)
    roots = _timeline_keys(res, "puppet:dad", "root.position")
    assert min(k[1][1] for k in roots) < 0  # it knelt on the way down
    assert roots[-1][1] == pytest.approx(list(stove), abs=1e-3)  # and stands back up on the stove mark
    for bone in (b for b in POSES["kneel"] if b.startswith("leg_")):
        assert _timeline_keys(res, "puppet:dad", f"{bone}.rot")[-1][1] == 0.0


def test_compiled_place_on_the_stove_mark_after_kneeling_only_bends():
    res, props, people = _kitchen_compile([
        {"t": 0, "do": "kneel", "who": "dad"},
        {"t": 2, "do": "place_on", "who": "dad", "what": "hymn_books", "on": "stove"},
    ])
    assert res.ok, [e.message for e in res.errors]
    (t_release, _), = [k for k in _timeline_keys(res, "prop:hymn_books", "parent") if k[1] == "ground"]
    position = dict(_timeline_keys(res, "prop:hymn_books", "position"))[t_release]
    grip = grip_from_anchor(PropGeom.from_prop(props["hymn_books"]), position)
    assert _compiled_hand(res, people, "dad", t_release) == pytest.approx(grip, abs=1e-3)
    roots = _timeline_keys(res, "puppet:dad", "root.position")
    kneeling_y = roots[-1][1][1]
    assert kneeling_y < 0  # still kneeling at the end: the last root key is the kneel's
    assert all(k[0] < t_release - P - R + 1e-6 for k in roots)  # place_on added no root keys
    torso = _timeline_keys(res, "puppet:dad", "torso.rot")
    assert any(v < 0 for _, v in torso) and torso[-1][1] == 0.0
    knee = _timeline_keys(res, "puppet:dad", "leg_r.lower.rot")
    assert knee[-1][1] == pytest.approx(POSES["kneel"]["leg_r.lower"])


def test_compiled_place_on_a_far_mark_is_a_precise_out_of_reach():
    res, _, _ = _kitchen_compile([{"t": 2, "do": "place_on", "who": "dad", "what": "hymn_books", "on": "door"}])
    assert not res.ok and res.timeline is None
    (err,) = res.errors
    assert (err.code, err.shot, err.who, err.do) == ("out_of_reach", "s1", "dad", "place_on")
    assert err.message.startswith('shot s1, t=2: dad cannot place_on "hymn_books"; dad\'s arm_r cannot reach: ')


# ------------------------------------------------------------------ open / close

def jar_on_table(state: str = "closed", **start) -> tuple[World, TrackSet]:
    tracks = TrackSet()
    w = make_world({"dad": {"at": "home", **start}},
                   {"table": {"at": "near"}, "jar": {"on": "table.top", "state": state},
                    "far_table": {"at": "far"}, "lamp": {"on": "far_table.top", "state": "out"}})
    return w, tracks


def test_open_reaches_the_grip_then_returns():
    w, tracks = jar_on_table()
    tracks.extend([("puppet:dad", "arm_r.upper.rot", 0.0, 0.1), ("puppet:dad", "arm_r.lower.rot", 0.0, 0.2)])
    end = run(apply_open, w, tracks, do="open", who="dad", what="jar")
    assert end == pytest.approx(T + 2 * R)
    grip = grip_from_anchor(GEOM["jar"], (0.15, 0.4, 0.5))
    assert arm_at(tracks, "dad", "arm_r", T) == pytest.approx((0.1, 0.2))
    assert hand_at(tracks, DAD, "arm_r", T + R) == pytest.approx(grip, abs=2e-3)
    assert arm_at(tracks, "dad", "arm_r", T + 2 * R) == pytest.approx((0.1, 0.2))
    state_track = next(tr for tr in tracks.tracks() if (tr.target, tr.channel) == ("prop:jar", "state"))
    assert [list(k) for k in state_track.keys] == [[round(T + R, 3), "open"]]  # flips as the hand arrives
    assert w.props["jar"].state == "open"


def test_close_uses_the_free_left_hand_and_the_middle_of_a_gripless_prop():
    tracks = TrackSet()
    w = make_world({"dad": {"at": "home", "holding": "hat"}},
                   {"table": {"at": "beside"}, "box": {"on": "table.top", "state": "open"}})
    end = run(apply_close, w, tracks, do="close", who="dad", what="box")
    assert end == pytest.approx(T + 2 * R)
    middle = (0.1, 0.4 + GEOM["box"].height / 2)
    assert hand_at(tracks, DAD, "arm_l", T + R) == pytest.approx(middle, abs=2e-3)
    assert not tracks.has_track("puppet:dad", "arm_r.upper.rot")
    assert at(tracks, "prop:box", "state", T + R) == "closed"
    assert w.props["box"].state == "closed"


def test_open_a_prop_in_ones_own_hand():
    """The jar is in hand_l (held up in the hold pose); the free right hand opens it."""
    tracks = TrackSet()
    tracks.extend([("puppet:dad", "arm_l.upper.rot", 0.0, HOLD[0]), ("puppet:dad", "arm_l.lower.rot", 0.0, HOLD[1])])
    w = make_world({"dad": {"at": "home"}}, {"jar": {"at": "ledge", "state": "closed"}})
    w.pick_up("dad", "jar", "hand_l")
    run(apply_open, w, tracks, do="open", who="dad", what="jar")
    hand_l = body_socket(DAD, "hand_l", {"arm_l.upper": HOLD[0], "arm_l.lower": HOLD[1]})
    assert hand_at(tracks, DAD, "arm_r", T + R) == pytest.approx(hand_l, abs=2e-3)
    assert arm_at(tracks, "dad", "arm_l", T + R) == pytest.approx(HOLD)  # the holding arm stays put
    assert w.props["jar"].state == "open"


def tray_anchor(holder: Body, rots: dict) -> tuple[float, float]:
    """The tray's anchor when its grip is on the holder's right hand, mirrored with the holder."""
    gx, gy = body_socket(holder, "hand_r", rots)
    g = GEOM["tray"]
    return (gx + holder.facing * (g.anchor[0] - g.grip[0]) * g.width, gy + (g.grip[1] - g.anchor[1]) * g.height)


@pytest.mark.parametrize("dad_facing, mom_at, mom_facing", [("right", "beside", "left"), ("left", "behind", "right")])
def test_open_a_prop_on_a_tray_someone_holds(dad_facing, mom_at, mom_facing):
    """Regression: a jar riding on a held tray failed with unknown_target. The hand now goes to
    the jar's grip: tray anchor (grip on dad's hand) + top socket offset, mirrored with dad."""
    tracks = TrackSet()
    tracks.extend([("puppet:dad", "arm_r.upper.rot", 0.0, HOLD[0]), ("puppet:dad", "arm_r.lower.rot", 0.0, HOLD[1])])
    w = make_world({"dad": {"at": "home", "holding": "tray", "facing": dad_facing},
                    "mom": {"at": mom_at, "facing": mom_facing}},
                   {"jar": {"on": "tray.top", "state": "closed"}})
    dad = Body("dad", (0.0, 0.0), 1 if dad_facing == "right" else -1, "tall")
    ax, ay = tray_anchor(dad, {"arm_r.upper": HOLD[0], "arm_r.lower": HOLD[1]})
    g = GEOM["tray"]
    top = (ax + dad.facing * (0.3 - g.anchor[0]) * g.width, ay + (g.anchor[1] - 0.0) * g.height)
    j = GEOM["jar"]
    grip = (top[0] - dad.facing * (j.anchor[0] - j.grip[0]) * j.width, top[1] - (j.grip[1] - j.anchor[1]) * j.height)
    end = run(apply_open, w, tracks, do="open", who="mom", what="jar")
    assert end == pytest.approx(T + 2 * R)
    mom = Body("mom", MARKS[mom_at][:2], 1 if mom_facing == "right" else -1, "average")
    assert hand_at(tracks, mom, "arm_r", T + R) == pytest.approx(grip, abs=2e-3)
    assert at(tracks, "prop:jar", "state", T + R) == "open" and w.props["jar"].state == "open"


def test_open_errors():
    w, tracks = jar_on_table(state="open")
    rejects("already_open", "it is already open", apply_open, w, tracks, do="open", who="dad", what="jar")
    rejects("unknown_state", "lamp has no state open", apply_open, w, tracks, do="open", who="dad", what="lamp")
    rejects("not_on_stage", "stool is not on stage", apply_open, w, tracks, do="open", who="dad", what="stool")


def test_open_out_of_reach():
    tracks = TrackSet()
    w = make_world({"dad": {"at": "home"}}, {"far_table": {"at": "far"}, "jar": {"on": "far_table.top"}})
    err = out_of_reach(apply_open, w, tracks, do="open", who="dad", what="jar")
    assert err.who == "dad" and err.arm == "arm_r"
    assert w.props["jar"].state is None


def test_close_errors():
    w, tracks = jar_on_table(state="closed")
    rejects("already_closed", "it is already closed", apply_close, w, tracks, do="close", who="dad", what="jar")
    rejects("unknown_state", "lamp has no state closed", apply_close, w, tracks, do="close", who="dad", what="lamp")
    w2 = make_world({"dad": {"at": "offstage_right"}}, {"table": {"at": "near"}, "jar": {"on": "table.top"}})
    rejects("offstage", "dad is offstage", apply_close, w2, tracks, do="close", who="dad", what="jar")


# -------------------------------------------------------------------- set_state

def test_set_state_with_and_without_who():
    w, tracks = jar_on_table()
    assert run(apply_set_state, w, tracks, do="set_state", what="lamp", state="lit") == T
    assert at(tracks, "prop:lamp", "state", T) == "lit"
    assert w.props["lamp"].state == "lit"
    assert run(apply_set_state, w, tracks, do="set_state", who="dad", what="box", state="glowing") == T
    assert w.props["box"].state == "glowing"  # a prop with no declared states takes any
    assert not tracks.has_track("puppet:dad", "arm_r.upper.rot")


def test_set_state_errors():
    w, tracks = jar_on_table()
    rejects("unknown_state", "lamp has no state broken", apply_set_state, w, tracks,
            do="set_state", what="lamp", state="broken")
    rejects("unknown_id", "anvil is not a known prop", apply_set_state, w, tracks,
            do="set_state", what="anvil", state="lit")


# ------------------------------------------------------------------- atomicity

@pytest.mark.parametrize("handler, kw, exc", [
    (apply_put_on, {"do": "put_on", "who": "dad", "what": "cap", "target": "kid"}, OutOfReach),
    (apply_put_on, {"do": "put_on", "who": "dad", "what": "lamp"}, StateError),
    (apply_place_on, {"do": "place_on", "who": "dad", "what": "hat", "on": "far"}, OutOfReach),
    (apply_place_on, {"do": "place_on", "who": "dad", "what": "hat", "on": "table", "socket": "top"}, StateError),
    (apply_open, {"do": "open", "who": "dad", "what": "lamp"}, StateError),
    (apply_open, {"do": "open", "who": "dad", "what": "anvil"}, StateError),
    (apply_open, {"do": "open", "who": "dad", "what": "lamp_far"}, StateError),
    (apply_close, {"do": "close", "who": "dad", "what": "jar"}, StateError),
    (apply_set_state, {"do": "set_state", "what": "lamp", "state": "broken"}, StateError),
])
def test_every_error_leaves_world_and_tracks_untouched(handler, kw, exc):
    tracks = TrackSet()
    tracks.extend([("puppet:dad", "arm_r.upper.rot", 0.0, HOLD[0]), ("prop:lamp", "state", 0.0, "out")])
    w = make_world({"dad": {"at": "home", "holding": "hat"}, "kid": {"at": "far"}},
                   {"cap": {"at": "ledge"}, "table": {"at": "near"}, "jar": {"on": "table.top", "state": "closed"},
                    "far_table": {"at": "far"}, "lamp": {"on": "far_table.top", "state": "out"}})
    before_w, before_t = w.copy(), dump(tracks)
    with pytest.raises(exc):
        run(handler, w, tracks, **kw)
    assert w == before_w
    assert dump(tracks) == before_t


def test_deterministic():
    def go():
        w, tracks = jar_on_table()
        t1 = run(apply_open, w, tracks, do="open", who="dad", what="jar")
        assert not math.isnan(t1)
        run(apply_set_state, w, tracks, do="set_state", what="lamp", state="lit")
        return dump(tracks), w.snapshot()
    assert go() == go()
