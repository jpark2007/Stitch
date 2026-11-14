"""Object verbs: put_on, place_on, open, close, set_state (ARCHITECTURE.md section 3, steps 2-4).

Each handler follows the shared handler contract (see compiler.py):

- ``t`` is the timeline time the action starts (already cue-retimed); ``action_start`` is the
  timeline time of the shot's t=0; on entry ``world.now == round(t - action_start, 6)``.
- It returns the timeline time when ``action.who`` is free again (>= t).
- It is atomic: every ``StateError`` / ``ik.OutOfReach`` is raised before the world changes or a
  key is emitted. Keys are computed into a list, then the world is mutated, then
  ``tracks.extend(keys)``.

World rules are checked by running the World method on a scratch copy first, so the verbs and
the state model can never disagree on what is allowed, and world errors win over reach errors.
"""

from __future__ import annotations

from collections.abc import Callable

from screenplay import ik
from screenplay.body import (
    POSE_SECONDS,
    PUT_ON_SETTLE_SECONDS,
    REACH_SECONDS,
    REST_ARM,
    PropGeom,
    grip_from_anchor,
    kneel_keys,
    place_on_keys,
    put_on_keys,
    stand_keys,
    state_keys,
)
from screenplay.ik import Body, arm_bones, solve_body_arm
from screenplay.locate import body_at, bone_at, puppet_root
from screenplay.schema import Action
from screenplay.state import CLOSED, GROUND, OPEN, SEATED_POSES, StateError, World, parse_parent
from screenplay.tracks import TrackSet
from screenplay.verbs_core import _crouch_solution, _kneeling_ground

Vec3 = tuple[float, float, float]
Locate = Callable[[str, float], Vec3]
Handler = Callable[[World, TrackSet, Action, float, float, Locate], float]
Key = tuple[str, str, float, object]

# The activity a second puppet is busy with while someone else dresses them (put_on target).
DRESSED_ACTIVITY = "being dressed"

__all__ = [
    "DRESSED_ACTIVITY", "HANDLERS", "Handler", "Locate", "apply_close", "apply_open", "apply_place_on",
    "apply_put_on", "apply_set_state",
]


# ---------------------------------------------------------------------- geometry

def _body(world: World, tracks: TrackSet, who: str, t: float) -> Body:
    """The puppet's ``ik.Body`` at ``t`` (locate.body_at: the one source of truth)."""
    return body_at(world, tracks, who, t)


def _rots_now(tracks: TrackSet, who: str, bones: tuple[str, ...], t: float) -> dict[str, float]:
    out: dict[str, float] = {}
    for bone in bones:
        v = tracks.value_at(f"puppet:{who}", f"{bone}.rot", t)
        out[bone] = float(v) if v is not None else 0.0
    return out


def _arm_now(tracks: TrackSet, who: str, arm: str, t: float) -> tuple[float, float]:
    upper, lower = arm_bones(arm)
    rots = _rots_now(tracks, who, (upper, lower), t)
    return rots[upper], rots[lower]


def _arm_keys(who: str, arm: str, t: float, values: tuple[float, float]) -> list[Key]:
    upper, lower = arm_bones(arm)
    return [
        (f"puppet:{who}", f"{upper}.rot", t, float(values[0])),
        (f"puppet:{who}", f"{lower}.rot", t, float(values[1])),
    ]


def _arm_of(hand: str) -> str:
    return "arm_l" if hand == "hand_l" else "arm_r"


def _reach_arm(world: World, who: str) -> str:
    """arm_r, unless hand_r is holding something and hand_l is free."""
    holding = world.people[who].holding
    return "arm_l" if "hand_r" in holding and "hand_l" not in holding else "arm_r"


def _geom(world: World, what: str) -> PropGeom:
    return PropGeom.from_prop(world.catalog[what])


# ------------------------------------------------------------------------ put_on

# put_on_keys swaps the parent at (start + REACH_SECONDS) and, when the hand could not carry the
# grip all the way, settles the prop onto the head PUT_ON_SETTLE_SECONDS later. apply_put_on's end
# time is start + 2 * REACH_SECONDS, so the settle must finish within one more REACH_SECONDS.
assert PUT_ON_SETTLE_SECONDS <= REACH_SECONDS, "put_on's end time would cut the settle short"


def apply_put_on(world: World, tracks: TrackSet, action: Action, t: float, action_start: float, locate: Locate) -> float:
    """Wear a prop on ``target``'s head (default: who's own).

    From a hand: the hand carries it to the wearer's head socket and returns to rest. Straight
    off the ground: the free hand reaches its grip first and takes it, then does the same. When
    the grip cannot reach the head exactly, the hand goes as far as it can and the prop settles
    onto the head (body.put_on_keys); the end time covers that settle.
    """
    who, what = action.who, action.what
    world.copy().put_on(who, what, action.target)  # every world rule, no mutation
    target = who if action.target is None else action.target
    if target != who:
        world.check_free(target)

    geom = _geom(world, what)
    me = _body(world, tracks, who, t)
    wearer = me if target == who else _body(world, tracks, target, t)
    held = world.holder(what)
    if held is not None:  # in one of who's hands (put_on rules)
        arm = _arm_of(held[1])
        keys = put_on_keys(me, t, _arm_now(tracks, who, arm, t), geom, wearer, arm)
        end = t + 2 * REACH_SECONDS
    else:  # on the ground, on stage
        hand = world.free_hand(who)
        assert hand is not None  # world.put_on checked it
        arm = _arm_of(hand)
        grip = grip_from_anchor(geom, world.props[what].world_pos)
        # TODO(put_on off the floor): a prop on the floor with a low grip is below a standing
        # hand's reach and fails here with OutOfReach; pick_up crouches for that, put_on does not.
        reach = solve_body_arm(me, arm, grip)
        t_take = t + REACH_SECONDS
        keys = _arm_keys(who, arm, t, _arm_now(tracks, who, arm, t)) + _arm_keys(who, arm, t_take, reach)
        keys += [(f"prop:{what}", "parent", t_take, f"puppet:{who}.{hand}"),
                 (f"prop:{what}", "position", t_take, [0.0, 0.0, 0.0])]
        # From the grip the arm carries it to the head. put_on_keys repeats the reach key at
        # t_take with the same value; TrackSet drops exact duplicates.
        keys += put_on_keys(me, t_take, reach, geom, wearer, arm)
        end = t_take + 2 * REACH_SECONDS

    world.put_on(who, what, action.target)
    if target != who:
        world.occupy(target, round(end - action_start, 6), DRESSED_ACTIVITY, at=world.now)
    tracks.extend(keys)
    return end


# ---------------------------------------------------------------------- place_on

def _first_free_socket(world: World, on: str) -> str:
    sockets = world.catalog[on].sockets
    if not sockets:
        raise StateError("no_socket", f"{on} has no sockets to place on")
    occ = world.props[on].occupants
    free = [k for k in sockets if k not in occ]
    if not free:
        raise StateError("socket_taken", f"{on} has no free socket")
    return free[0]


def _surface_off_ground(world: World, on: str) -> StateError:
    """Why a surface has no world position: someone carries it (``held``), or it is not on stage."""
    cur, seen = on, set()
    while cur not in seen:
        seen.add(cur)
        h = world.holder(cur)
        if h is not None:
            if cur == on:
                return StateError("held", f"{h[0]} is holding {on}")
            return StateError("held", f"{on} is on {cur}, which {h[0]} is holding")
        parsed = parse_parent(world.props[cur].parent)
        if parsed is None or parsed[0] != "prop":
            break
        cur = parsed[1]
    return StateError("not_on_stage", f"{on} is not on stage")


def apply_place_on(world: World, tracks: TrackSet, action: Action, t: float, action_start: float, locate: Locate) -> float:
    """Set a held prop down on a prop's socket (``on`` = prop id) or on a mark (``on`` = mark).

    A destination the standing arm reaches (or one at or above the shoulder) is a plain arm
    reach (body.place_on_keys). One too low for a standing arm (a mark on the floor, a low
    surface) is reached the way pick_up reaches a prop on the floor: bend forward, kneeling
    first if a bend alone is not enough, let go, and come back up (``_crouch_place_on_keys``).
    A puppet already sitting or kneeling only bends, and stays down.

    No auto-walk: OutOfReach (with the closest miss) when the grip is beyond the arm even
    crouching.
    """
    who, what, on = action.who, action.what, action.on
    hand = world.hold(who, what)  # in who's hand, who on stage
    if on is None:
        raise StateError("bad_target", "place_on needs on: a prop with a socket, or a mark")
    if on in world.catalog:
        socket = action.socket if action.socket is not None else _first_free_socket(world, on)
        parent = f"prop:{on}.{socket}"
        world.copy().place_on(who, what, parent, (0.0, 0.0, 0.0))  # socket exists and is free, no cycle
        dest = world.socket_world(on, socket)
        if dest is None:
            raise _surface_off_ground(world, on)
        pos: Vec3 = (0.0, 0.0, 0.0)
    elif on in world.people:
        raise StateError("bad_target", f"{on} is a person; place_on needs a prop or a mark")
    else:
        dest = tuple(float(c) for c in locate(on, t))  # type: ignore[assignment]
        if action.socket is not None:
            raise StateError("unknown_socket", f"{on} is a mark and has no socket {action.socket}")
        parent = GROUND
        world.copy().place_on(who, what, parent, dest)
        pos = dest

    arm = _arm_of(hand)
    me = _body(world, tracks, who, t)
    geom = _geom(world, what)
    grip = grip_from_anchor(geom, dest)
    current = _arm_now(tracks, who, arm, t)
    shoulder_y = ik.shoulder_world(arm, me.ground, me.facing, me.build)[1]
    if ik.can_reach(arm, grip, me.ground, me.facing, me.build) or grip[1] >= shoulder_y:
        keys = place_on_keys(me, t, current, geom, dest, parent, arm)
        end = t + 2 * REACH_SECONDS
    else:  # too low for a standing arm: bend down (kneeling if needed) to the grip
        p = world.people[who]
        on_floor = p.seat is not None or p.pose in SEATED_POSES
        z = puppet_root(world, tracks, who, t)[2]
        keys, end = _crouch_place_on_keys(
            tracks, me, t, current, what, grip, arm, parent, pos, z, can_kneel=not on_floor
        )

    world.place_on(who, what, parent, pos)
    tracks.extend(keys)
    return end


def _crouch_place_on_keys(
    tracks: TrackSet,
    body: Body,
    t: float,
    current_arm: tuple[float, float],
    what: str,
    grip: tuple[float, float],
    arm: str,
    parent: str,
    position: Vec3,
    z: float,
    *,
    can_kneel: bool,
) -> tuple[list[Key], float]:
    """place_on of a low grip, mirroring verbs_core._crouch_pick_up_keys: bend (and kneel, if
    needed) over POSE_SECONDS, reach the grip over REACH_SECONDS and let go (the prop's parent
    and position keys), then come back up over POSE_SECONDS with the arm at rest. A puppet that
    cannot kneel (already down) only bends, and its legs and root are left alone. Raises
    ik.OutOfReach (closest miss) when even crouching cannot reach. Returns (keys, end)."""
    kneel, bend, reach = _crouch_solution(body, arm, grip, can_kneel)
    who = body.id
    target = f"puppet:{who}"
    t_down = t + POSE_SECONDS
    t_release = t_down + REACH_SECONDS
    t_up = t_release + POSE_SECONDS
    upper, lower = arm_bones(arm)
    torso = bone_at(tracks, who, "torso", t)
    keys: list[Key] = [
        (target, "torso.rot", t, torso), (target, "torso.rot", t_down, bend),
        (target, "torso.rot", t_release, bend), (target, "torso.rot", t_up, torso),
    ]
    for bone, values in ((upper, (current_arm[0], reach[0], REST_ARM[0])),
                         (lower, (current_arm[1], reach[1], REST_ARM[1]))):
        keys += [(target, f"{bone}.rot", t, float(values[0])), (target, f"{bone}.rot", t_down, float(values[0])),
                 (target, f"{bone}.rot", t_release, float(values[1])), (target, f"{bone}.rot", t_up, float(values[2]))]
    if kneel:  # down over POSE_SECONDS, held kneeling while reaching, then up again
        kneel_pose = ik.POSES["poses"]["kneel"]
        legs = {b: bone_at(tracks, who, b, t) for b in kneel_pose if b.startswith("leg_")}
        keys += kneel_keys(body, t, legs, z=z)
        low = Body(who, _kneeling_ground(body), body.facing, body.build)
        kneeling = {b: float(v) for b, v in kneel_pose.items() if b.startswith("leg_")}
        keys += stand_keys(low, t_release, kneeling, body.ground, z=z)
    keys += [(f"prop:{what}", "parent", t_release, parent),
             (f"prop:{what}", "position", t_release, [float(c) for c in position])]
    return keys, t_up


# ------------------------------------------------------------------ open / close

def _anchor_and_mirror(world: World, what: str, locate: Locate, t: float) -> tuple[tuple[float, float], int]:
    """A prop's world anchor and the x-mirror it is drawn with.

    On the ground (or on a ground prop's socket): its world anchor, mirror 1. Held: its grip is
    on the holder's hand socket (locate), and it is mirrored with the holder (mirror = facing).
    Riding on a socket of a held prop: the base's anchor plus the socket offset, in the base's
    mirror, plus the prop's own offset from the socket.
    """
    st = world.props[what]
    if st.world_pos is not None:
        return (st.world_pos[0], st.world_pos[1]), 1
    parsed = parse_parent(st.parent)
    if parsed is None:  # world.open / world.close reject an unplaced prop first
        raise StateError("not_on_stage", f"{what} is not on stage")
    kind, base, sock = parsed
    geom = _geom(world, what)
    if kind == "puppet":
        mirror = world.people[base].facing
        gx, gy = (float(c) for c in locate(what, t)[:2])
        return (gx + mirror * (geom.anchor[0] - geom.grip[0]) * geom.width,
                gy + (geom.grip[1] - geom.anchor[1]) * geom.height), mirror
    (bx, by), mirror = _anchor_and_mirror(world, base, locate, t)
    bgeom = _geom(world, base)
    su, sv = world.catalog[base].sockets[sock][0], world.catalog[base].sockets[sock][1]
    return (bx + mirror * ((su - bgeom.anchor[0]) * bgeom.width + st.pos[0]),
            by + (bgeom.anchor[1] - sv) * bgeom.height + st.pos[1]), mirror


def _handle_point(world: World, what: str, locate: Locate, t: float) -> tuple[float, float]:
    """Where a hand goes to open or close a prop: its grip, else the middle of its height.

    A prop held in a hand is opened at that hand.
    """
    if world.holder(what) is not None:
        p = locate(what, t)
        return (float(p[0]), float(p[1]))
    (ax, ay), mirror = _anchor_and_mirror(world, what, locate, t)
    geom = _geom(world, what)
    if world.catalog[what].grip is not None:
        return (ax - mirror * (geom.anchor[0] - geom.grip[0]) * geom.width,
                ay - (geom.grip[1] - geom.anchor[1]) * geom.height)
    return (ax, ay + geom.height / 2)


def _open_close(world: World, tracks: TrackSet, action: Action, t: float, locate: Locate, state: str) -> float:
    who, what = action.who, action.what
    scratch = world.copy()
    (scratch.open if state == OPEN else scratch.close)(who, what)

    arm = _reach_arm(world, who)
    me = _body(world, tracks, who, t)
    reach = solve_body_arm(me, arm, _handle_point(world, what, locate, t))
    now = _arm_now(tracks, who, arm, t)
    t_touch = t + REACH_SECONDS
    keys = _arm_keys(who, arm, t, now) + _arm_keys(who, arm, t_touch, reach)
    keys += _arm_keys(who, arm, t_touch + REACH_SECONDS, now)
    keys += state_keys(what, t_touch, state)

    (world.open if state == OPEN else world.close)(who, what)
    tracks.extend(keys)
    return t_touch + REACH_SECONDS


def apply_open(world: World, tracks: TrackSet, action: Action, t: float, action_start: float, locate: Locate) -> float:
    """Reach the prop, its state becomes "open" as the hand arrives, and the arm returns."""
    return _open_close(world, tracks, action, t, locate, OPEN)


def apply_close(world: World, tracks: TrackSet, action: Action, t: float, action_start: float, locate: Locate) -> float:
    """Reach the prop, its state becomes "closed" as the hand arrives, and the arm returns."""
    return _open_close(world, tracks, action, t, locate, CLOSED)


# --------------------------------------------------------------------- set_state

def apply_set_state(world: World, tracks: TrackSet, action: Action, t: float, action_start: float, locate: Locate) -> float:
    """Switch a prop's state at t (no body motion). ``who`` is optional and is not moved."""
    if action.state is None:
        raise StateError("missing_param", "set_state needs state")
    keys = state_keys(action.what, t, action.state)
    world.set_prop_state(action.what, action.state)  # validates before it changes anything
    tracks.extend(keys)
    return t


HANDLERS: dict[str, Handler] = {
    "put_on": apply_put_on,
    "place_on": apply_place_on,
    "open": apply_open,
    "close": apply_close,
    "set_state": apply_set_state,
}
