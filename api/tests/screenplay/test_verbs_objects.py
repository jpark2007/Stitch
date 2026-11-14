"""Object verb handlers (api/screenplay/verbs_objects.py): put_on, place_on, open, close, set_state.

Every handler is checked through the keys it leaves in a TrackSet (sampled with value_at at the
key times), the world state afterwards, and, for each error, the exact code and reason with the
world and the tracks left untouched.
"""

import inspect
import math

import pytest

import schemas
from screenplay.body import REACH_SECONDS, PropGeom, grip_from_anchor
from screenplay.ik import Body, OutOfReach, body_socket
from screenplay.schema import Action, Shot
from screenplay.state import StateError, World
from screenplay.tracks import TrackSet
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


def test_place_on_out_of_reach():
    tracks = TrackSet()
    w = make_world({"dad": {"at": "home", "holding": "jar"}}, {"far_table": {"at": "far"}})
    err = out_of_reach(apply_place_on, w, tracks, do="place_on", who="dad", what="jar", on="far_table")
    assert err.who == "dad"
    # a mark on the floor is below a standing puppet's reach too: no auto-walk, no auto-kneel
    out_of_reach(apply_place_on, w, tracks, do="place_on", who="dad", what="jar", on="near")


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
