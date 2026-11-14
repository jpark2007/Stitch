"""Verb handlers for the demo path: enter, walk_to, run_to, pose, pick_up, hold, drop, hand_to.

Handler contract (shared with the other ``verbs_*`` modules):

    def apply_<verb>(world, tracks, action, t, action_start, locate) -> float

- ``t`` is the timeline time the action starts (already cue-retimed); ``action_start`` is the
  timeline time of the shot's t=0. On entry ``world.now == round(t - action_start, 6)``.
- Returns the timeline time when ``action.who`` is free again (>= t).
- The compiler runs ``world.check_free(who)`` before the call and ``world.occupy(who, ...)``
  after it. A handler checks and occupies any SECOND puppet itself (hand_to's taker).
- Atomic: every check (StateError, ik.OutOfReach) happens before any world mutation or key
  emission. Keys are computed into a list, then the world is mutated, then ``tracks.extend``.
"""

from __future__ import annotations

import math
from dataclasses import replace

from screenplay import body as B
from screenplay import ik, motion
from screenplay.locate import (
    Locate,
    arm_at,
    arm_of_hand,
    body_at,
    bone_at,
    bones_at,
    held_arm_bones,
    mark_of,
    puppet_root,
    puppet_target,
)
from screenplay.schema import Action
from screenplay.state import SEATED_POSES, StateError, World, fmt_t
from screenplay.tracks import Key, TrackSet
from screenplay.verb_helpers import (
    CROUCH_BEND_STEP,
    CROUCH_COMFORT,
    CROUCH_KNEEL_COST,
    CROUCH_MAX_BEND,
    CrouchTry,
    Handler,
    Vec3,
    bent_shoulder,
    bone_after,
    crouch_effort,
    crouch_pick,
    crouch_reach_keys,
    crouch_solution,
    crouch_tries,
    kneeling_ground,
    require_on_stage,
    require_person,
    require_prop_on_stage,
    require_value,
    sign,
    solve_bent_arm,
    stand_up_keys,
)

__all__ = [
    "AUTO_WALK_REACH", "CROUCH_BEND_STEP", "CROUCH_COMFORT", "CROUCH_DEEP_BEND", "CROUCH_KNEEL_COST", "CrouchTry",
    "CROUCH_MAX_BEND", "HANDLERS", "Handler", "POSES", "SPOT_RANGE", "SPOT_STEP", "SPOT_WALK_COST",
    "STAGE_EDGE", "apply_drop", "apply_enter", "apply_hand_to", "apply_hold", "apply_pick_up",
    "apply_pose", "apply_run_to", "apply_walk_to",
]

AUTO_WALK_REACH = 0.6  # pick_up walks to this fraction of the arm's reach short of the grip
POSES: dict[str, dict] = ik.POSES["poses"]

# pick_up of a grip below the shoulder that a standing arm cannot reach (a prop on the floor)
# bends forward and kneels with verb_helpers.crouch_solution; its CROUCH_* constants are
# re-exported above (CROUCH_BEND_STEP, CROUCH_MAX_BEND, CROUCH_COMFORT, CROUCH_KNEEL_COST).
# A puppet that steps to a spot to pick up (see _reach_spot) may fold further, down to this, but
# only when no spot on either side reaches within CROUCH_MAX_BEND. The back arm (arm_l) needs it: a forward bend swings its shoulder pivot, which sits behind the hip,
# up as well as forward, so even kneeling at CROUCH_MAX_BEND it cannot reach the floor.
CROUCH_DEEP_BEND = 1.5

# pick_up that the arm cannot make from where the puppet is, nor after the plain walk-up: the
# standing spots it may step to instead (see _reach_spot).
SPOT_STEP = 0.005  # stage units between the root positions tried, outward from the grip
SPOT_RANGE = 2.0   # how far out to try, in arm reaches beyond the shoulder's offset from the root
SPOT_WALK_COST = 5.0  # crouch effort (radians) a stage unit of walking costs: 0.1 walked = a kneel
STAGE_EDGE = 1.0   # spots stay on stage: |x| at most this (World.on_stage)


def _changes_legs(pose: str) -> bool:
    """True when a library pose moves the legs or the root (sit, kneel), so it ends a floor posture."""
    values = POSES[pose]
    return "rootOffset" in values or any(b.startswith("leg_") for b in values)


# ------------------------------------------------------------------------- helpers

# The shared helpers live in verb_helpers; these private names stay as aliases because tests
# and older call sites reference them (verbs_core._crouch_pick, _crouch_tries, ...).
_sign = sign
_stand_up_keys = stand_up_keys
_kneeling_ground = kneeling_ground
_bent_shoulder = bent_shoulder
_solve_bent_arm = solve_bent_arm
_crouch_effort = crouch_effort
_crouch_tries = crouch_tries
_crouch_pick = crouch_pick
_crouch_solution = crouch_solution


def _who(world: World, action: Action) -> str:
    # verbs_core's own codes and wording: missing_param 'it needs "who"', then unknown_person
    # (the other verb modules say unknown_id).
    return require_person(world, require_value(action.who, "who"), code="unknown_person")


def _what(world: World, action: Action) -> str:
    what = require_value(action.what, "what")
    if what not in world.props:
        raise StateError("unknown_prop", f"{what} is not in the screenplay's props")
    return what


def _not_seated(world: World, who: str) -> None:
    if world.people[who].seat is not None:
        raise StateError("seated", world.seat_reason(who))  # "...; use stand_up first" (get_out in a vehicle)


def _travel_keys(world: World, tracks: TrackSet, who: str, t: float, dest: Vec3, gait: str) -> tuple[list[Key], float, int]:
    """Stand up if needed, then walk or run to ``dest`` with held arms kept in the hold pose.
    Returns (keys, arrival, facing after)."""
    p = world.people[who]
    keys, t0 = stand_up_keys(world, tracks, who, t)
    start = p.pos if keys else puppet_root(world, tracks, who, t)
    move, t1, facing = motion.locomotion_keys(
        who, p.build, t0, start, dest, p.facing, gait,  # type: ignore[arg-type]
        skip_bones=sorted(held_arm_bones(world, who)),
        speed_scale=p.speed_scale,
    )
    return keys + move, t1, facing


def _blend_back(keys: list[Key], t: float, t_back: float) -> list[Key]:
    """Hold every channel ``keys`` animate at its final value until ``t_back``, then blend back
    over POSE_SECONDS to the value it had at ``t``."""
    first: dict[tuple[str, str], object] = {}
    for target, channel, kt, value in keys:
        if kt == t:
            first.setdefault((target, channel), value)
    out: list[Key] = []
    for (target, channel), last in B.last_values(keys).items():
        if (target, channel) not in first:
            continue
        out.append((target, channel, t_back, last))
        out.append((target, channel, t_back + B.POSE_SECONDS, first[(target, channel)]))
    return out


# ------------------------------------------------------------------------ movement

def apply_enter(world: World, tracks: TrackSet, action: Action, t: float, action_start: float, locate: Locate) -> float:
    """Come on from offstage (``from``, default the side nearest ``to``) and walk to ``to``."""
    who = _who(world, action)
    to = mark_of(locate, require_value(action.to, "to"), t)
    src = mark_of(locate, action.from_ or motion.nearest_offstage(to[0]), t)
    _not_seated(world, who)
    if world.on_stage(who):
        raise StateError("already_on_stage", f"{who} is already on stage; use walk_to")
    p = world.people[who]
    # Where the root track has the puppet now (offstage_left, an exit point, the start block's
    # parking spot): enter_keys holds it there up to t, so it steps to ``src`` instead of gliding.
    prev = tracks.value_at(puppet_target(who), "root.position", t)
    keys, t1, facing = motion.enter_keys(
        who, p.build, t, src, to, p.facing, "walk", skip_bones=sorted(held_arm_bones(world, who)),
        speed_scale=p.speed_scale, prev_pos=None if prev is None else tuple(float(c) for c in prev),
    )
    world.move(who, to, facing)
    tracks.extend(keys)
    return t1


def _locomote(world: World, tracks: TrackSet, action: Action, t: float, locate: Locate, gait: str) -> float:
    who = _who(world, action)
    dest = mark_of(locate, require_value(action.to, "to"), t)
    require_on_stage(world, who)
    _not_seated(world, who)
    keys, t1, facing = _travel_keys(world, tracks, who, t, dest, gait)
    world.move(who, dest, facing)
    tracks.extend(keys)
    return t1


def apply_walk_to(world: World, tracks: TrackSet, action: Action, t: float, action_start: float, locate: Locate) -> float:
    """Walk to a mark at the walk speed, with the walk cycle."""
    return _locomote(world, tracks, action, t, locate, "walk")


def apply_run_to(world: World, tracks: TrackSet, action: Action, t: float, action_start: float, locate: Locate) -> float:
    """Run to a mark at the run speed, with the run cycle."""
    return _locomote(world, tracks, action, t, locate, "run")


# ---------------------------------------------------------------------------- body

def apply_pose(world: World, tracks: TrackSet, action: Action, t: float, action_start: float, locate: Locate) -> float:
    """Blend into a library pose over POSE_SECONDS. With ``dur``, hold it and blend back."""
    who = _who(world, action)
    pose = require_value(action.pose, "pose")
    if pose not in POSES:
        raise StateError("unknown_pose", f"{pose} is not a pose; poses are: {', '.join(sorted(POSES))}")
    dur = action.dur
    if dur is not None and dur <= 0:
        raise StateError("bad_dur", f"dur must be positive, not {fmt_t(dur)}")
    require_on_stage(world, who)
    p = world.people[who]
    if pose in (*SEATED_POSES, "stand"):
        _not_seated(world, who)

    current = bones_at(tracks, who, t)
    root_now = puppet_root(world, tracks, who, t)
    z = root_now[2]
    standing = (p.pos[0], p.pos[1])
    if pose in SEATED_POSES:
        upright = replace(body_at(world, tracks, who, t), ground=standing)
        gen = (B.sit_keys(upright, t, current, None, z=z) if pose == "sit"
               else B.kneel_keys(upright, t, current, z=z))
        # Start from where the root really is (it may already be lowered by another floor pose).
        keys = [(k[0], k[1], k[2], list(root_now)) if k[1] == "root.position" and k[2] == t else k for k in gen]
    elif pose == "stand":
        skip = held_arm_bones(world, who)
        rest = {b: v for b, v in current.items() if b not in skip}
        if p.pose in SEATED_POSES:
            keys = B.stand_keys(body_at(world, tracks, who, t), t, rest, standing, z=z)
        else:
            keys = B.pose_keys(who, t, rest, "stand")
    else:
        keys = B.pose_keys(who, t, current, pose)

    if dur is None:
        # An upper-body pose (wave, bow_head, ...) does not move the legs or the root, so a
        # puppet sitting or kneeling stays down: the world keeps its floor posture, and a
        # later stand or walk gets it up first.
        if not (p.pose in SEATED_POSES and pose != "stand" and not _changes_legs(pose)):
            world.set_pose(who, pose)
        end = t + B.POSE_SECONDS
    else:  # the pose is held for dur, then the puppet goes back to what it was doing
        keys += _blend_back(keys, t, t + dur)
        end = t + dur + B.POSE_SECONDS
    tracks.extend(keys)
    return end


# --------------------------------------------------------------------------- props

def apply_pick_up(world: World, tracks: TrackSet, action: Action, t: float, action_start: float, locate: Locate) -> float:
    """Reach the prop's grip and take it into the free hand (hand_r first), then raise it into
    the hold pose, with the arm of that hand.

    1. From where the puppet is (``_pick_up_in_place``): a grip out of reach and more than
       AUTO_WALK_REACH of the arm's reach away from the root is walked toward first, to that
       distance short of it. A grip still too low for a standing arm (a prop on the floor) is
       reached by bending forward, kneeling first if needed, and the puppet comes back up holding
       it (see verb_helpers.crouch_solution). A puppet already sitting or kneeling only bends, and stays down.
    2. When that arm still cannot take the grip and the puppet is not seated, it steps to a
       standing spot from which it can (``_reach_spot``): on its own side of the grip, or behind
       itself when it stands right over the grip (a cap at its feet is under the hips, out of a
       forward bend's reach, so it steps back). Every spot is judged from the shoulder pivot of
       the arm actually used, not the root: arm_r's sits in front of the hip and arm_l's behind
       it, where a forward bend lifts it as well as bringing it forward, so when no spot reaches
       within CROUCH_MAX_BEND a stepped-to spot may fold down to CROUCH_DEEP_BEND. Spots stay
       on stage; when its own side has none (a prop by the stage edge), it walks past the grip
       to the far side. It weighs crouch effort against the walk (SPOT_WALK_COST), gets up
       first if it is on the floor, walks there, turns to face the grip, and reaches, standing
       or crouching.
    3. Raises ik.OutOfReach, with the closest miss, only when no spot reaches. A seated puppet
       never moves: it reaches from the seat or fails. On any failure the world is untouched."""
    who = _who(world, action)
    what = _what(world, action)
    hand = world.copy().pick_up(who, what)  # validation only: the real world is untouched
    require_on_stage(world, who)
    anchor = require_prop_on_stage(world, what)

    arm = arm_of_hand(hand)
    geom = B.PropGeom.from_prop(world.catalog[what])
    try:
        keys, end, walk_to, facing = _pick_up_in_place(world, tracks, who, what, arm, geom, anchor, t)
    except ik.OutOfReach as missed:
        if world.people[who].seat is not None:
            raise
        keys, end, walk_to, facing = _pick_up_from_a_spot(world, tracks, who, what, arm, geom, anchor, t, missed)

    # Atomic: the move and the take are tried on a copy first, so a failure (StateError from
    # World.pick_up at the new spot) leaves the real world as it was.
    for w in (world.copy(), world):
        if walk_to is not None:
            w.move(who, walk_to, facing)
        else:
            w.set_facing(who, facing)
        w.pick_up(who, what, hand)
    tracks.extend(keys)
    return end


def _pick_up_in_place(
    world: World, tracks: TrackSet, who: str, what: str, arm: str, geom: B.PropGeom, anchor: Vec3, t: float
) -> tuple[list[Key], float, Vec3 | None, int]:
    """pick_up from where the puppet is, or after the plain walk-up toward the grip (step 1 of
    apply_pick_up). Returns (keys, end, where it walked to or None, facing). Raises
    ik.OutOfReach, or StateError("seated") when a seated puppet would have to walk."""
    p = world.people[who]
    grip = B.grip_from_anchor(geom, anchor)
    here = puppet_root(world, tracks, who, t)
    d = sign(grip[0] - here[0])
    facing = d or p.facing

    keys: list[Key] = []
    t_reach = t
    ground = (here[0], here[1])
    current = arm_at(tracks, who, arm, t)
    torso = bone_at(tracks, who, "torso", t)
    walk_to: Vec3 | None = None
    if not ik.can_reach(arm, grip, ground, facing, p.build, torso=torso):
        reach = ik.arm_reach(p.build, arm)
        if d != 0 and abs(grip[0] - here[0]) > AUTO_WALK_REACH * reach:
            _not_seated(world, who)
            walk_to = (grip[0] - d * AUTO_WALK_REACH * reach, p.pos[1], p.pos[2])
            keys, t_reach, facing = _travel_keys(world, tracks, who, t, walk_to, "walk")
            ground = (walk_to[0], walk_to[1])
            current = (0.0, 0.0)  # the walk cycle ends with the free arm at rest
            # Standing up from the floor first straightens the torso; a plain walk keeps it.
            stood = B.last_values(keys).get((puppet_target(who), "torso.rot"))
            if stood is not None:
                torso = float(stood)  # type: ignore[arg-type]
    if walk_to is None and facing != p.facing:
        keys += [(puppet_target(who), "root.facing", t, facing)]
    body_now = ik.Body(who, ground, facing, p.build, torso)
    shoulder_y = ik.shoulder_world(arm, ground, facing, p.build, torso=torso)[1]
    if ik.can_reach(arm, grip, ground, facing, p.build, torso=torso) or grip[1] >= shoulder_y:
        keys += B.pick_up_keys(body_now, t_reach, current, geom, anchor, arm)
        end = t_reach + 2 * B.REACH_SECONDS
    else:  # too low for a standing arm: bend down (kneeling if needed) to the grip
        on_floor = walk_to is None and (p.seat is not None or p.pose in SEATED_POSES)
        z = here[2] if walk_to is None else walk_to[2]
        crouch, end = _crouch_pick_up_keys(
            tracks, keys, body_now, t_reach, current, what, grip, arm, z, can_kneel=not on_floor
        )
        keys += crouch
    return keys, end, walk_to, facing


def _spot_offsets(reach: float, shoulder_dx: float) -> list[float]:
    """Root distances from the grip that _reach_spot tries on one side: every SPOT_STEP out to
    SPOT_RANGE of the arm's reach beyond the shoulder's offset from the root, plus the spot that
    puts the shoulder (not the root) AUTO_WALK_REACH of the reach short of the grip."""
    far = SPOT_RANGE * reach + abs(shoulder_dx)
    n = int(math.ceil(far / SPOT_STEP))
    return [i * SPOT_STEP for i in range(n + 1)] + [max(0.0, AUTO_WALK_REACH * reach + shoulder_dx)]


def _reach_spot(
    world: World, tracks: TrackSet, who: str, arm: str, grip: tuple[float, float], t: float, missed: ik.OutOfReach
) -> tuple[Vec3, int, float]:
    """The standing spot ``who`` steps to so ``arm`` can take a grip it cannot take from where
    it is (step 2 of apply_pick_up): (spot, facing there, deepest bend allowed there). Raises
    ik.OutOfReach with the closest miss over every spot tried and ``missed`` (the miss from
    where it is) when none reaches.

    Candidates face the grip, keep the torso the walk leaves the puppet with, and lie every
    SPOT_STEP from the grip outward (see _spot_offsets), on the stage only (World.on_stage:
    |x| <= STAGE_EDGE). Each is tried from the shoulder pivot of the arm actually used
    (ik.shoulder_world), not the root: it reaches standing, or with a crouch (kneeling allowed;
    see verb_helpers.crouch_tries). The search goes, first hit wins:

    1. the puppet's own side of the grip (behind it, a step back, when it stands right over the
       grip; its facing breaks the tie), bending down to CROUCH_MAX_BEND;
    2. the far side of the grip (walking past it and turning back), the same way: by a stage
       edge the own side may have no room;
    3. and 4. both again, in that order, bending down to CROUCH_DEEP_BEND: arm_l, whose shoulder
       sits behind the hip, needs it to reach the floor at all.

    Within one of those, best is a comfortable reach (CROUCH_COMFORT) over a full stretch, then
    the lowest cost: crouch effort (verb_helpers.crouch_effort, standing is none) plus SPOT_WALK_COST per
    stage unit walked, then the spot nearest the puppet's root."""
    p = world.people[who]
    here = puppet_root(world, tracks, who, t)
    own = sign(here[0] - grip[0]) or -p.facing
    stand, _ = stand_up_keys(world, tracks, who, t)
    stood = B.last_values(stand).get((puppet_target(who), "torso.rot"))
    torso = float(stood) if stood is not None else bone_at(tracks, who, "torso", t)  # type: ignore[arg-type]
    s = ik.scale(p.build)
    reach = ik.arm_reach(p.build, arm)
    length = ik.RIG["bones"][ik.arm_bones(arm)[0]]["length"]
    y, z = p.pos[1], p.pos[2]

    closest = missed.distance
    spots: dict[int, list[float]] = {}  # side -> the on-stage root x tried there
    for side in (own, -own):
        # How far the shoulder sits from the root toward the grip (negative: away from it).
        shoulder_dx = -side * (ik.shoulder_world(arm, (0.0, y), -side, p.build, torso=torso)[0])
        xs = spots.setdefault(side, [])
        for offset in _spot_offsets(reach, shoulder_dx):
            x = round(grip[0] + side * offset, 3)
            if abs(x) <= STAGE_EDGE and x not in xs:
                xs.append(x)
    for max_bend in (CROUCH_MAX_BEND, CROUCH_DEEP_BEND):
        for side in (own, -own):
            facing = -side
            best: tuple[tuple[int, float, float], float] | None = None
            for x in spots[side]:
                ground = (x, y)
                walk = abs(x - here[0])
                dist, angles = ik.arm_solution(arm, grip, ground, facing, p.build, torso=torso)
                if angles is not None:
                    rank = (0, round(SPOT_WALK_COST * walk, 9), walk)
                elif grip[1] >= ik.shoulder_world(arm, ground, facing, p.build, torso=torso)[1]:
                    closest = min(closest, dist * s)
                    continue
                else:
                    body = ik.Body(who, ground, facing, p.build, torso)
                    tries = crouch_tries(body, arm, grip, can_kneel=True, max_bend=max_bend)
                    pick = crouch_pick(tries, length)
                    if pick is None:
                        closest = min(closest, min(tr[0] for tr in tries) * s)
                        continue
                    comfortable, (_, kneel, bend, _) = pick
                    cost = crouch_effort(kneel, bend) + SPOT_WALK_COST * walk
                    rank = (0 if comfortable else 1, round(cost, 9), walk)
                if best is None or rank < best[0]:
                    best = (rank, x)
            if best is not None:
                return (best[1], y, z), facing, max_bend
    raise ik.OutOfReach(arm, closest, 2 * length * s, who)


def _pick_up_from_a_spot(
    world: World, tracks: TrackSet, who: str, what: str, arm: str, geom: B.PropGeom, anchor: Vec3, t: float,
    missed: ik.OutOfReach,
) -> tuple[list[Key], float, Vec3, int]:
    """Walk to _reach_spot's spot (standing up first if needed), turn to the grip, and reach it
    standing or crouching. Raises ik.OutOfReach (the closest miss) when no spot reaches."""
    p = world.people[who]
    grip = B.grip_from_anchor(geom, anchor)
    spot, facing, max_bend = _reach_spot(world, tracks, who, arm, grip, t, missed)
    keys, t_reach, walked_facing = _travel_keys(world, tracks, who, t, spot, "walk")
    if walked_facing != facing:  # it walked away from the grip (a step back): turn around
        keys.append((puppet_target(who), "root.facing", t_reach, facing))
    emitted = B.last_values(keys)
    upper, lower = ik.arm_bones(arm)
    current = (bone_after(emitted, tracks, who, upper, t), bone_after(emitted, tracks, who, lower, t))
    torso = bone_after(emitted, tracks, who, "torso", t)
    ground = (spot[0], spot[1])
    body = ik.Body(who, ground, facing, p.build, torso)
    if ik.can_reach(arm, grip, ground, facing, p.build, torso=torso):
        keys += B.pick_up_keys(body, t_reach, current, geom, anchor, arm)
        return keys, t_reach + 2 * B.REACH_SECONDS, spot, facing
    crouch, end = _crouch_pick_up_keys(
        tracks, keys, body, t_reach, current, what, grip, arm, spot[2], can_kneel=True, max_bend=max_bend
    )
    return keys + crouch, end, spot, facing


def _crouch_pick_up_keys(
    tracks: TrackSet,
    before: list[Key],
    body: ik.Body,
    t: float,
    current_arm: tuple[float, float],
    what: str,
    grip: tuple[float, float],
    arm: str,
    z: float,
    *,
    can_kneel: bool,
    max_bend: float = CROUCH_MAX_BEND,
) -> tuple[list[Key], float]:
    """pick_up of a low grip: bend (and kneel, if needed) over POSE_SECONDS, reach the grip over
    REACH_SECONDS and take the prop, then come back up over POSE_SECONDS with the prop raised
    into the hold pose. ``before`` are keys this action already emits (an auto-walk), whose last
    values are the puppet's bones at ``t``. Returns (keys, end). See verb_helpers.crouch_reach_keys."""
    return crouch_reach_keys(
        tracks, before, body, t, current_arm, what, grip, arm, z, can_kneel=can_kneel, max_bend=max_bend,
        arm_after=B.hold_pose(arm), parent=f"{puppet_target(body.id)}.{ik.arm_socket(arm)}", position=(0.0, 0.0, 0.0),
    )


def apply_hold(world: World, tracks: TrackSet, action: Action, t: float, action_start: float, locate: Locate) -> float:
    """Raise a prop already in hand into the hold pose."""
    who = _who(world, action)
    what = _what(world, action)
    arm = arm_of_hand(world.hold(who, what))
    keys = B.hold_keys(body_at(world, tracks, who, t), t, arm_at(tracks, who, arm, t), arm)
    tracks.extend(keys)
    return t + B.POSE_SECONDS


def _standing_surface_y(world: World, who: str) -> float:
    """Height of the surface under ``who``'s feet, where a prop they drop lands.

    Standing or kneeling, it is ``pos[1]``: the mark's height (kneeling lowers only the root
    track, not ``pos``). Seated, ``pos`` may be the hip root (get_in), so it is the ground the
    seat stands on instead: the seat prop's world anchor, which is where it meets the ground,
    or where it rests when it stands on another prop.
    """
    p = world.people[who]
    if p.seat is not None:
        anchor = world.props[p.seat[0]].world_pos
        if anchor is not None:
            return anchor[1]
    return p.pos[1]


def apply_drop(world: World, tracks: TrackSet, action: Action, t: float, action_start: float, locate: Locate) -> float:
    """Let go: the prop falls to the dropper's standing surface (under the seat when seated),
    DROP_OFFSET in front of the feet."""
    who = _who(world, action)
    what = _what(world, action)
    arm = arm_of_hand(world.hold(who, what))  # validation: who must hold it in a hand
    z = puppet_root(world, tracks, who, t)[2]
    keys, landing = B.drop_keys(
        body_at(world, tracks, who, t), t, arm_at(tracks, who, arm, t),
        B.PropGeom.from_prop(world.catalog[what]), surface_y=_standing_surface_y(world, who), z=z, arm=arm,
    )
    world.drop(who, what, landing)
    tracks.extend(keys)
    return t + B.FALL_SECONDS


def apply_hand_to(world: World, tracks: TrackSet, action: Action, t: float, action_start: float, locate: Locate) -> float:
    """Pass a held prop to another puppet: both hands meet at the handover point. The two face
    each other first. No auto-walk: OutOfReach when the hands cannot meet."""
    who = _who(world, action)
    what = _what(world, action)
    to = require_value(action.to, "to")
    require_person(world, to, code="unknown_person")
    taker_hand = world.copy().hand_to(who, what, to)  # validation only
    world.check_free(to)
    holder = world.holder(what)
    assert holder is not None  # hand_to validated that who holds it
    giver_arm, taker_arm = arm_of_hand(holder[1]), arm_of_hand(taker_hand)

    g, k = body_at(world, tracks, who, t), body_at(world, tracks, to, t)
    g_facing = sign(k.ground[0] - g.ground[0]) or g.facing
    k_facing = sign(g.ground[0] - k.ground[0]) or k.facing
    keys: list[Key] = []
    for body, facing in ((g, g_facing), (k, k_facing)):
        if facing != body.facing:
            keys.append((puppet_target(body.id), "root.facing", t, facing))
    g = replace(g, facing=g_facing)  # replace keeps each torso: a leaning puppet reaches from it
    k = replace(k, facing=k_facing)
    keys += B.hand_to_keys(
        g, arm_at(tracks, who, giver_arm, t), k, arm_at(tracks, to, taker_arm, t), what, t, giver_arm, taker_arm
    )
    end = t + B.PASS_SECONDS + B.RETURN_SECONDS

    world.set_facing(who, g_facing)
    world.set_facing(to, k_facing)
    world.hand_to(who, what, to)
    world.occupy(to, round(end - action_start, 6), f"taking {what}", at=world.now)
    tracks.extend(keys)
    return end


HANDLERS: dict[str, Handler] = {
    "enter": apply_enter,
    "walk_to": apply_walk_to,
    "run_to": apply_run_to,
    "pose": apply_pose,
    "pick_up": apply_pick_up,
    "hold": apply_hold,
    "drop": apply_drop,
    "hand_to": apply_hand_to,
}
