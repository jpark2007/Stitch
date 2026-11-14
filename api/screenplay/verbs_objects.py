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

from dataclasses import replace

from screenplay import ik, motion
from screenplay.body import (
    PUT_ON_SETTLE_SECONDS,
    REACH_SECONDS,
    REST_ARM,
    PropGeom,
    grip_from_anchor,
    last_values,
    place_on_keys,
    put_on_keys,
    state_keys,
)
from screenplay.ik import Body, arm_bones, solve_body_arm
from screenplay.locate import (
    Locate,
    arm_at,
    arm_of_hand,
    body_at,
    gesture_arm,
    held_arm_bones,
    puppet_root,
    puppet_target,
)
from screenplay.schema import Action
from screenplay.state import CLOSED, GROUND, OPEN, SEATED_POSES, StateError, World, parse_parent
from screenplay.tracks import TrackSet
from screenplay.verb_helpers import (
    CROUCH_MAX_BEND,
    Handler,
    Key,
    Vec3,
    bone_after,
    crouch_reach_keys,
    crouch_solution,
    require_param,
)
from screenplay.verbs_core import CROUCH_DEEP_BEND, STAGE_EDGE  # verbs_core imports nothing from here: no cycle

# The activity a second puppet is busy with while someone else dresses them (put_on target).
DRESSED_ACTIVITY = "being dressed"

# place_on turns to face a destination whose grip lies behind the puppet's root by more than this
# (stage units); within it the grip is under the puppet and it keeps its facing.
PLACE_FACE_DEAD_ZONE = 0.02
# A standing puppet whose crouch cannot reach a grip too close to it (under its own feet) steps
# back, away from the grip, by the smallest multiple of PLACE_STEP that lets it reach, at most
# PLACE_STEP_BACK_MAX (stage units).
PLACE_STEP = 0.005
PLACE_STEP_BACK_MAX = 0.15

__all__ = [
    "DRESSED_ACTIVITY", "HANDLERS", "Handler", "Locate", "PLACE_FACE_DEAD_ZONE", "PLACE_STEP",
    "PLACE_STEP_BACK_MAX", "apply_close", "apply_open", "apply_place_on", "apply_put_on", "apply_set_state",
]


# ---------------------------------------------------------------------- geometry

# Puppet geometry comes from locate: body_at, arm_at (an arm's two rotations, 0 with no track),
# arm_of_hand (only ever given hand_l / hand_r here: World.put_on, hold and free_hand return a
# hand) and gesture_arm (arm_r, unless hand_r holds something and hand_l is free).

def _arm_keys(who: str, arm: str, t: float, values: tuple[float, float]) -> list[Key]:
    upper, lower = arm_bones(arm)
    return [
        (puppet_target(who), f"{upper}.rot", t, float(values[0])),
        (puppet_target(who), f"{lower}.rot", t, float(values[1])),
    ]


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
    me = body_at(world, tracks, who, t)
    wearer = me if target == who else body_at(world, tracks, target, t)
    held = world.holder(what)
    if held is not None:  # in one of who's hands (put_on rules)
        arm = arm_of_hand(held[1])
        keys = put_on_keys(me, t, arm_at(tracks, who, arm, t), geom, wearer, arm)
        end = t + 2 * REACH_SECONDS
    else:  # on the ground, on stage
        hand = world.free_hand(who)
        assert hand is not None  # world.put_on checked it
        arm = arm_of_hand(hand)
        grip = grip_from_anchor(geom, world.props[what].world_pos)
        # TODO(put_on off the floor): a prop on the floor with a low grip is below a standing
        # hand's reach and fails here with OutOfReach; pick_up crouches for that, put_on does not.
        reach = solve_body_arm(me, arm, grip)
        t_take = t + REACH_SECONDS
        keys = _arm_keys(who, arm, t, arm_at(tracks, who, arm, t)) + _arm_keys(who, arm, t_take, reach)
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

    1. A grip behind the puppet's root by more than PLACE_FACE_DEAD_ZONE turns it to face the
       grip first (one root.facing key at t, as pick_up turns), standing, kneeling or seated. A
       turn takes no time.
    2. A destination the standing arm reaches (or one at or above the shoulder) is a plain arm
       reach (body.place_on_keys). One too low for a standing arm (a mark on the floor, a low
       surface) is reached the way pick_up reaches a prop on the floor: bend forward, kneeling
       first if a bend alone is not enough, let go, and come back up (``_crouch_place_on_keys``).
       A puppet already sitting or kneeling only bends, and stays down.
    3. A standing puppet whose crouch cannot reach the grip because it is too close (under its
       own feet, under the hips) steps back, away from it, by the smallest distance that lets
       the crouch reach, at most PLACE_STEP_BACK_MAX (``_place_low``), and turns back to it.
       Failing that it bends as deep as pick_up lets a stepped-to puppet bend (CROUCH_DEEP_BEND:
       arm_l's shoulder sits behind the hip), in place, then stepping back. A puppet sitting or
       kneeling never steps and never folds past CROUCH_MAX_BEND.

    No auto-walk toward the grip: OutOfReach (the miss of the plain crouch where it stands)
    when nothing reaches. Atomic: nothing is emitted and the world is untouched on failure.
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

    arm = arm_of_hand(hand)
    me = body_at(world, tracks, who, t)
    geom = _geom(world, what)
    grip = grip_from_anchor(geom, dest)
    turn: list[Key] = []
    if (grip[0] - me.ground[0]) * me.facing < -PLACE_FACE_DEAD_ZONE:  # behind it: face the grip
        turn, facing = motion.turn_keys(who, t, me.ground[0], grip[0], me.facing)
        me = replace(me, facing=facing)
    current = arm_at(tracks, who, arm, t)
    shoulder_y = ik.shoulder_world(arm, me.ground, me.facing, me.build)[1]
    spot: Vec3 | None = None
    if ik.can_reach(arm, grip, me.ground, me.facing, me.build) or grip[1] >= shoulder_y:
        keys = turn + place_on_keys(me, t, current, geom, dest, parent, arm)
        end = t + 2 * REACH_SECONDS
    else:  # too low for a standing arm: bend down (kneeling if needed) to the grip
        keys, end, spot = _place_low(world, tracks, me, t, current, what, grip, arm, parent, pos, turn)

    if spot is not None:
        world.move(who, spot, me.facing)
    else:
        world.set_facing(who, me.facing)
    world.place_on(who, what, parent, pos)
    tracks.extend(keys)
    return end


def _step_backs(world: World, who: str, body: Body, arm: str, grip: tuple[float, float]) -> list[float]:
    """The root x a standing ``who`` may step back to for a grip too close to reach (smallest
    step first), on stage; none when it is sitting or kneeling, or the grip is beyond a full
    arm's reach of its root (too far: stepping back only takes it further away)."""
    p = world.people[who]
    if p.seat is not None or p.pose in SEATED_POSES:
        return []
    if abs(grip[0] - body.ground[0]) > ik.arm_reach(body.build, arm):
        return []
    n = int(round(PLACE_STEP_BACK_MAX / PLACE_STEP))
    xs = [round(body.ground[0] - body.facing * i * PLACE_STEP, 6) for i in range(1, n + 1)]
    return [x for x in xs if abs(x) <= STAGE_EDGE]


def _place_low(
    world: World,
    tracks: TrackSet,
    me: Body,
    t: float,
    current: tuple[float, float],
    what: str,
    grip: tuple[float, float],
    arm: str,
    parent: str,
    pos: Vec3,
    turn: list[Key],
) -> tuple[list[Key], float, Vec3 | None]:
    """place_on of a grip too low for a standing arm (steps 2 and 3 of apply_place_on), facing
    the way ``me`` faces; ``turn`` is the turn keys at t (none, or one root.facing key). Returns
    (keys, end, where it stepped back to or None). Raises ik.OutOfReach, the plain crouch's
    miss where it stands, when no crouch reaches.

    Tried in order, first reach wins: a crouch where it stands down to CROUCH_MAX_BEND (always
    first, so every place_on that reached before reaches the same way); then, standing only,
    every step back (_step_backs, smallest first) at CROUCH_MAX_BEND; then both again down to
    CROUCH_DEEP_BEND."""
    who = me.id
    p = world.people[who]
    on_floor = p.seat is not None or p.pose in SEATED_POSES
    z = puppet_root(world, tracks, who, t)[2]
    try:
        keys, end = _crouch_place_on_keys(tracks, me, t, current, what, grip, arm, parent, pos, z,
                                          can_kneel=not on_floor)
        return turn + keys, end, None
    except ik.OutOfReach as missed:
        if on_floor:
            raise
        first_miss = missed

    backs = _step_backs(world, who, me, arm, grip)
    # None stands for "where it stands"; a step back keeps the height of the surface it stands on.
    tries: tuple[tuple[float, list[float | None]], ...] = (
        (CROUCH_MAX_BEND, list(backs)), (CROUCH_DEEP_BEND, [None, *backs]))
    for max_bend, xs in tries:
        for x in xs:
            ground = me.ground if x is None else (x, p.pos[1])
            try:
                crouch_solution(replace(me, ground=ground), arm, grip, True, max_bend)
            except ik.OutOfReach:
                continue
            if x is None:  # a deeper bend where it stands
                keys, end = crouch_reach_keys(
                    tracks, [], me, t, current, what, grip, arm, z, can_kneel=True, max_bend=max_bend,
                    arm_after=REST_ARM, parent=parent, position=pos,
                )
                return turn + keys, end, None
            spot = (x, p.pos[1], p.pos[2])
            keys, end = _step_back_and_place(world, tracks, me, t, spot, what, grip, arm, parent, pos, max_bend)
            return keys, end, spot
    raise first_miss


def _step_back_and_place(
    world: World,
    tracks: TrackSet,
    me: Body,
    t: float,
    spot: Vec3,
    what: str,
    grip: tuple[float, float],
    arm: str,
    parent: str,
    pos: Vec3,
    max_bend: float,
) -> tuple[list[Key], float]:
    """Walk back to ``spot`` (motion.locomotion_keys, with the planting markers; held arms keep
    the hold pose), turn to face the grip (``me.facing``) on arrival, then crouch and let go,
    as pick_up does from a stepped-to spot (verbs_core._pick_up_from_a_spot). The walk starts
    from the puppet's world facing, so a turn toward the grip at t is not keyed: it walks off
    facing away and turns once, at the spot. Returns (keys, end)."""
    who = me.id
    p = world.people[who]
    keys, t_reach, walked_facing = motion.locomotion_keys(
        who, p.build, t, puppet_root(world, tracks, who, t), spot, p.facing, "walk",  # type: ignore[arg-type]
        skip_bones=sorted(held_arm_bones(world, who)), speed_scale=p.speed_scale,
    )
    if walked_facing != me.facing:
        keys.append((puppet_target(who), "root.facing", t_reach, me.facing))
    emitted = last_values(keys)
    upper, lower = arm_bones(arm)
    current = (bone_after(emitted, tracks, who, upper, t), bone_after(emitted, tracks, who, lower, t))
    body = replace(me, ground=(spot[0], spot[1]), torso=bone_after(emitted, tracks, who, "torso", t))
    crouch, end = crouch_reach_keys(
        tracks, keys, body, t_reach, current, what, grip, arm, spot[2], can_kneel=True, max_bend=max_bend,
        arm_after=REST_ARM, parent=parent, position=pos,
    )
    return keys + crouch, end


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
    """place_on of a low grip: bend (and kneel, if needed) over POSE_SECONDS, reach the grip over
    REACH_SECONDS and let go (the prop's parent and position keys), then come back up over
    POSE_SECONDS with the arm at rest. A puppet that cannot kneel (already down) only bends, and
    its legs and root are left alone. Raises ik.OutOfReach (closest miss) when even crouching
    cannot reach. Returns (keys, end). See verb_helpers.crouch_reach_keys, which pick_up's
    crouch shares; no keys come before it, so its bones are read from the tracks at ``t``."""
    return crouch_reach_keys(
        tracks, [], body, t, current_arm, what, grip, arm, z, can_kneel=can_kneel,
        arm_after=REST_ARM, parent=parent, position=position,
    )


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

    arm = gesture_arm(world, who)
    me = body_at(world, tracks, who, t)
    reach = solve_body_arm(me, arm, _handle_point(world, what, locate, t))
    now = arm_at(tracks, who, arm, t)
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
    require_param(action, "state")  # missing_param "set_state needs state"
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
