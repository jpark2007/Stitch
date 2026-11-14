"""Movement verbs the walk module does not cover: ``exit``, ``turn_to`` and ``follow``.

Each handler follows the compiler's shared handler contract::

    def apply_<verb>(world, tracks, action, t, action_start, locate) -> float

- ``t`` is the timeline time the action starts (already moved onto its cue word) and
  ``action_start`` the timeline time of the shot's t=0. On entry ``world.now`` is the shot
  time ``round(t - action_start, 6)``.
- The return value is the timeline time when ``action.who`` is free again (``>= t``).
- The compiler runs ``world.check_free(who)`` before the call and ``world.occupy(...)`` after
  it. These verbs involve no second puppet, so the handlers never occupy anyone.
- Atomic: every check runs first and raises ``StateError`` before the world changes or a key
  is emitted. Keys are computed into a list, then the world is mutated, then
  ``tracks.extend(keys)`` runs.
- Geometry comes from the tracks: a puppet's root is its ``root.position`` value at ``t`` when
  that track exists, else ``world.people[id].pos``; its facing is ``world.people[id].facing``.

``exit`` and ``follow`` walk at the puppet's own speed: the gait speed times
``PuppetState.speed_scale`` (from the character sheet's notes, ``character.speed_scale``).
Standing up first always takes ``body.POSE_SECONDS``, whatever the speed.

A puppet kneeling or sitting on the floor (pose ``kneel`` / ``sit`` with no seat) gets up
before it walks: ``body.stand_keys`` over ``body.POSE_SECONDS``, bringing the root from the
lowered (rootOffset) position back to its standing spot, then the walk starts. Arms holding
props keep their pose throughout (the same ``skip_bones`` as a walk). ``stand_up_first`` is
public so other verb modules can get a puppet up the same way.

``follow`` reads the leader's path from the ``puppet:<target>`` ``root.position`` keys already
in ``tracks``. **The compiler must therefore run every ``follow`` after the shot's other
actions**, so the leader's movement for the whole shot is known (wave 2 wires that order).
The follower walks on the surface the leader stands on, never at its root height while it is
seated in a prop (a car seat, a chair) or kneeling / sitting on the floor: each path key's y
is moved onto that surface first (``_standing_y``), keeping its x, z and time.
"""

from __future__ import annotations

from itertools import pairwise

from screenplay import motion
from screenplay.body import POSE_SECONDS, last_values, stand_keys
from screenplay.ik import POSES, RIG, Body, scale
from screenplay.locate import Locate, bone_at, held_arm_bones, puppet_root, puppet_target
from screenplay.motion import prop_socket_offset
from screenplay.schema import Action
from screenplay.state import (
    GROUND,
    HANDS,
    SEATED_POSES,
    StateError,
    World,
    fmt_t,
    missing_param_reason,
    self_target_reason,
)
from screenplay.tracks import Key, TrackSet
from screenplay.verb_helpers import Handler, Vec3, require_person, vec3

EXIT_SIDES = ("offstage_left", "offstage_right")

_EPS = 1e-9
_POS_TOL = 2e-3  # track values are rounded to 3 dp (tracks.DECIMALS)
_ROT_TOL = 2e-3
_ROOT = "root.position"
# turn_to: a target within this of the puppet's x (the tracks' 3 dp rounding of the root, see
# tracks.DECIMALS, is 5e-4) is straight above or below it: there is no side to face, so no turn.
TURN_DEAD_ZONE = 1e-3
# Every rig bone a stand-up returns to 0 (stand_up_first), tracked or not.
_BONES: tuple[str, ...] = tuple(b for b in RIG["bones"] if b not in HANDS)


# ------------------------------------------------------------------------- helpers

# Shared checks and geometry come from verb_helpers (require_person: unknown_id) and locate
# (puppet_root, bone_at, held_arm_bones, puppet_target).

def _skip_bones(world: World, who: str) -> tuple[str, ...]:
    """Arm bones of the hands holding props (locate.held_arm_bones), sorted: a walk leaves them
    in their hold pose. The motion module only tests membership, so the order is cosmetic."""
    return tuple(sorted(held_arm_bones(world, who)))


def stand_up_first(
    world: World, tracks: TrackSet, who: str, t: float, skip_bones: tuple[str, ...]
) -> tuple[list[Key], float, Vec3]:
    """Keys that get a kneeling / floor-sitting puppet onto its feet before it moves.

    Returns ``(keys, time standing completes, standing root)``. A puppet kneeling or sitting
    on the floor (pose ``kneel`` / ``sit`` with no seat) gets ``body.stand_keys`` over
    ``body.POSE_SECONDS`` from timeline ``t``, bringing its root from the lowered position
    (its ``root.position`` track at ``t``) back up to its standing spot
    (``world.people[who].pos``); bones in ``skip_bones`` (arms holding props) keep their pose.
    A puppet already standing, or seated on a prop (the world refuses to move it), gets no keys,
    ``t`` unchanged and its current root.

    Pure with respect to its inputs: it reads ``world`` and ``tracks`` and changes neither.
    """
    p = world.people[who]
    root = puppet_root(world, tracks, who, t)
    if p.pose not in SEATED_POSES or p.seat is not None:
        return [], t, root
    current = {b: bone_at(tracks, who, b, t) for b in _BONES if b not in skip_bones}
    body = Body(id=who, ground=(root[0], root[1]), facing=p.facing, build=p.build)
    stand_at = (p.pos[0], p.pos[1])
    keys: list[Key] = list(stand_keys(body, t, current, stand_at, z=root[2]))
    return keys, t + POSE_SECONDS, (stand_at[0], stand_at[1], root[2])


def _leader_path(tracks: TrackSet, target: str) -> list[tuple[float, Vec3]]:
    """The leader's root.position keys as a sorted (t, position) list (empty if none)."""
    for track in tracks.tracks():
        if track.target == puppet_target(target) and track.channel == _ROOT:
            return [(float(k[0]), vec3(k[1])) for k in track.keys]
    return []


def _close(a: Vec3, b: Vec3, tol: float = _POS_TOL) -> bool:
    return all(abs(a[i] - b[i]) <= tol for i in range(3))


def _prop_anchor(world: World, tracks: TrackSet, locate: Locate, what: str, t: float) -> Vec3 | None:
    """A prop's world anchor at timeline t, or None when it is not on stage.

    On the ground its anchor is its ``position`` track (a vehicle on the move), else the world's
    position; resting on something else, ``locate`` resolves it.
    """
    target = f"prop:{what}"
    parent = tracks.value_at(target, "parent", t)
    if parent is None:
        parent = world.props[what].parent
    if parent == GROUND:
        v = tracks.value_at(target, "position", t)
        if v is not None:
            return vec3(v)
        if world.props[what].world_pos is not None:
            return world.props[what].world_pos
    try:
        v = locate(what, t)
    except StateError:
        return None
    return vec3(v)


def _standing_y(world: World, tracks: TrackSet, locate: Locate, who: str, t: float, root: Vec3) -> float:
    """The height of the surface ``who`` stands on at timeline t, given its root there.

    - Seated on a prop's socket (a car seat, a chair): the root is the seat minus
      ``hipHeight * scale`` (``body.sit_keys``), so it is matched against every socket of every
      prop on stage, and the standing surface is that prop's anchor height: its anchor is at its
      bottom, so that is the ground the prop stands on.
    - Kneeling or sitting on the floor (its legs hold the ``kneel`` / ``sit`` pose): the root
      is lowered by the pose's ``rootOffset * scale``, which is added back.
    - Otherwise the root already is on the standing surface.
    """
    s = scale(world.people[who].build)
    hip = RIG["hipHeight"] * s
    for what, prop in world.catalog.items():
        if not prop.sockets or what not in world.props:
            continue
        anchor = _prop_anchor(world, tracks, locate, what, t)
        if anchor is None:
            continue
        for socket in prop.sockets:
            off = prop_socket_offset(prop, socket)
            seat_root = (anchor[0] + off[0], anchor[1] + off[1] - hip, anchor[2] + off[2])
            if _close(root, seat_root):
                return anchor[1]
    for pose in SEATED_POSES:
        values = POSES["poses"][pose]
        legs = {b: float(v) for b, v in values.items() if b != "rootOffset" and b.startswith("leg_")}
        if legs and all(abs(bone_at(tracks, who, b, t) - v) <= _ROT_TOL for b, v in legs.items()):
            return root[1] - float(values.get("rootOffset", [0.0, 0.0])[1]) * s
    return root[1]


def _standing_path(
    world: World, tracks: TrackSet, locate: Locate, who: str, path: list[tuple[float, Vec3]]
) -> list[tuple[float, Vec3]]:
    """The leader's path with every key's y on the surface it stands on (see ``_standing_y``).

    x, z and the key times are unchanged, so a follower walks the same route on the ground
    instead of at a seated or kneeling leader's lowered or raised root height.
    """
    return [(kt, (p[0], _standing_y(world, tracks, locate, who, kt, p), p[2])) for kt, p in path]


def _moves_after(path: list[tuple[float, Vec3]], t: float) -> bool:
    """True when some move of the path (two keys at different positions) ends after t."""
    for (_, pa), (tb, pb) in pairwise(path):
        if tb > t + _EPS and max(abs(pb[i] - pa[i]) for i in range(3)) > _EPS:
            return True
    return False


# --------------------------------------------------------------------------- verbs

def apply_exit(world: World, tracks: TrackSet, action: Action, t: float, action_start: float, locate: Locate) -> float:
    """Walk offstage: to ``action.to`` (offstage_left / offstage_right), default the nearest side.

    Errors: ``bad_exit`` for any other ``to``, ``seated`` (world rule), ``offstage`` when the
    puppet is not on stage. Returns the arrival time.
    """
    who = require_person(world, action.who)
    if action.to is not None and action.to not in EXIT_SIDES:
        raise StateError("bad_exit", f'{who} can exit to offstage_left or offstage_right, not "{action.to}"')
    explicit = locate(action.to, t) if action.to is not None else None
    # The world decides the default side and refuses a seated puppet; try it on a copy first.
    dest = world.copy().exit(who, explicit)
    if not world.on_stage(who):
        raise StateError("offstage", f"{who} is already offstage")

    p = world.people[who]
    skip = _skip_bones(world, who)
    keys, t_go, start = stand_up_first(world, tracks, who, t, skip)
    walk, arrive, facing = motion.exit_keys(
        who, p.build, t_go, start, dest, p.facing, skip_bones=skip, speed_scale=p.speed_scale
    )
    keys += walk

    world.exit(who, dest)
    world.set_facing(who, facing)
    tracks.extend(keys)
    return max(t, arrive)


def apply_turn_to(world: World, tracks: TrackSet, action: Action, t: float, action_start: float, locate: Locate) -> float:
    """Face a person, prop or mark: one ``root.facing`` key when the facing changes, else none.
    A target within TURN_DEAD_ZONE of the puppet's x (straight above or below it, such as a mark
    on a riser over its head) has no side to face: the facing stays.

    Errors: ``self_target``, and locate's ``unknown_target``. Takes no time (returns t).
    """
    who = require_person(world, action.who)
    if action.target is None:
        raise StateError("missing_param", missing_param_reason("turn_to", "target"))
    if action.target == who:
        raise StateError("self_target", self_target_reason("target", who))
    x = locate(action.target, t)[0]
    p = world.people[who]
    here = puppet_root(world, tracks, who, t)[0]
    if abs(x - here) <= TURN_DEAD_ZONE:  # straight above or below: keep the facing
        x = here
    keys, facing = motion.turn_keys(who, t, here, x, p.facing)

    world.set_facing(who, facing)
    tracks.extend(keys)
    return t


def apply_follow(world: World, tracks: TrackSet, action: Action, t: float, action_start: float, locate: Locate) -> float:
    """Walk behind another puppet along the path its root.position keys already describe.

    Uses ``motion.follow_keys`` (spacing, lag, never overtaking); ``dur`` clips it. Errors:
    ``self_target``, ``bad_target`` (not a person), ``bad_time`` (dur <= 0, or shorter than the
    ``POSE_SECONDS`` a kneeling / floor-sitting follower needs to stand up), ``seated`` (world
    rule), ``nothing_to_follow`` (the leader does not move after t), and locate's
    ``unknown_target``. Run it after the shot's other actions (see the module docstring).
    The follower's keys and the standing position it ends at are at standing-surface height:
    a seated leader's path is followed on the ground under its seat, a kneeling leader's at
    the height it knelt from (``_standing_path``). Returns the time the follower stops.
    """
    who = require_person(world, action.who)
    target = action.target
    if target is None:
        raise StateError("missing_param", missing_param_reason("follow", "target"))
    if target == who:
        raise StateError("self_target", self_target_reason("target", who))
    if target not in world.people:
        locate(target, t)  # unknown names raise unknown_target
        raise StateError("bad_target", f"{who} can only follow a person, and {target} is not one")
    if action.dur is not None and action.dur <= 0:
        raise StateError("bad_time", f"follow dur must be positive, not {action.dur:g}")
    p = world.people[who]
    world.copy().move(who, p.pos)  # a seated puppet cannot walk (world rule)
    path = _leader_path(tracks, target)
    if not _moves_after(path, t):
        raise StateError("nothing_to_follow", f"{target} does not move after t={fmt_t(world.now)}")

    skip = _skip_bones(world, who)
    keys, t_go, start = stand_up_first(world, tracks, who, t, skip)
    if action.dur is not None and t_go - t > action.dur + _EPS:
        # Standing up cannot be clipped, so a shorter dur would keep the puppet busy past it.
        raise StateError(
            "bad_time",
            f"{who} needs {fmt_t(t_go - t)}s to stand up before following; follow dur "
            f"{action.dur:g} is shorter than that",
        )
    dur = None if action.dur is None else action.dur - (t_go - t)
    # Walk on the ground under the leader, never at its seat or kneeling height.
    ground_path = _standing_path(world, tracks, locate, target, path)
    walk, stop, facing = motion.follow_keys(
        who, p.build, t_go, start, p.facing, ground_path, dur, skip_bones=skip, speed_scale=p.speed_scale
    )
    keys += walk
    final = last_values(walk).get((puppet_target(who), _ROOT))  # None: the follower holds where it stands
    end = start if final is None else vec3(final)  # type: ignore[arg-type]

    world.move(who, end, facing)
    tracks.extend(keys)
    return max(t, t_go, stop)


HANDLERS: dict[str, Handler] = {
    "exit": apply_exit,
    "turn_to": apply_turn_to,
    "follow": apply_follow,
}
