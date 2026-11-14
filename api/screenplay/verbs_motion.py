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
"""

from __future__ import annotations

from collections.abc import Callable
from itertools import pairwise

from screenplay import motion
from screenplay.body import POSE_SECONDS, last_values, stand_keys
from screenplay.ik import RIG, Body, arm_bones
from screenplay.schema import Action
from screenplay.state import HANDS, SEATED_POSES, StateError, World, fmt_t
from screenplay.tracks import Key, TrackSet

Vec3 = tuple[float, float, float]
Locate = Callable[[str, float], Vec3]
Handler = Callable[[World, TrackSet, Action, float, float, Locate], float]

EXIT_SIDES = ("offstage_left", "offstage_right")

_EPS = 1e-9
_ROOT = "root.position"
# Hand socket -> the arm bones that hold it ("hand_r" -> ("arm_r.upper", "arm_r.lower")).
_HAND_BONES: dict[str, tuple[str, str]] = {RIG["ik"][arm]["socket"]: arm_bones(arm) for arm in RIG["ik"]}
_BONES: tuple[str, ...] = tuple(b for b in RIG["bones"] if b not in HANDS)


# ------------------------------------------------------------------------- helpers

def _puppet(who: str) -> str:
    return f"puppet:{who}"


def _require_person(world: World, who: str | None) -> str:
    if who is None or who not in world.people:
        raise StateError("unknown_id", f"{who} is not in the cast")
    return who


def _root(world: World, tracks: TrackSet, who: str, t: float) -> Vec3:
    """The puppet's root at timeline t: from its root.position track, else the world."""
    if tracks.has_track(_puppet(who), _ROOT):
        v = tracks.value_at(_puppet(who), _ROOT, t)
        return (float(v[0]), float(v[1]), float(v[2]))
    return world.people[who].pos


def _bone(tracks: TrackSet, who: str, bone: str, t: float) -> float:
    v = tracks.value_at(_puppet(who), f"{bone}.rot", t)
    return 0.0 if v is None else float(v)


def _skip_bones(world: World, who: str) -> tuple[str, ...]:
    """Arm bones of the hands holding props: a walk leaves them in their hold pose."""
    holding = world.people[who].holding
    return tuple(b for hand, bones in _HAND_BONES.items() if hand in holding for b in bones)


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
    root = _root(world, tracks, who, t)
    if p.pose not in SEATED_POSES or p.seat is not None:
        return [], t, root
    current = {b: _bone(tracks, who, b, t) for b in _BONES if b not in skip_bones}
    body = Body(id=who, ground=(root[0], root[1]), facing=p.facing, build=p.build)
    stand_at = (p.pos[0], p.pos[1])
    keys: list[Key] = list(stand_keys(body, t, current, stand_at, z=root[2]))
    return keys, t + POSE_SECONDS, (stand_at[0], stand_at[1], root[2])


def _leader_path(tracks: TrackSet, target: str) -> list[tuple[float, Vec3]]:
    """The leader's root.position keys as a sorted (t, position) list (empty if none)."""
    for track in tracks.tracks():
        if track.target == _puppet(target) and track.channel == _ROOT:
            return [(float(k[0]), (float(k[1][0]), float(k[1][1]), float(k[1][2]))) for k in track.keys]
    return []


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
    who = _require_person(world, action.who)
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

    Errors: ``self_target``, and locate's ``unknown_target``. Takes no time (returns t).
    """
    who = _require_person(world, action.who)
    if action.target is None:
        raise StateError("missing_param", f"{who} cannot turn_to: no target given")
    if action.target == who:
        raise StateError("self_target", f"{who} cannot turn to face themselves")
    x = locate(action.target, t)[0]
    p = world.people[who]
    keys, facing = motion.turn_keys(who, t, _root(world, tracks, who, t)[0], x, p.facing)

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
    Returns the time the follower stops.
    """
    who = _require_person(world, action.who)
    target = action.target
    if target is None:
        raise StateError("missing_param", f"{who} cannot follow: no target given")
    if target == who:
        raise StateError("self_target", f"{who} cannot follow themselves")
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
    walk, stop, facing = motion.follow_keys(
        who, p.build, t_go, start, p.facing, path, dur, skip_bones=skip, speed_scale=p.speed_scale
    )
    keys += walk
    final = last_values(walk).get((_puppet(who), _ROOT))  # None: the follower holds where it stands
    end = start if final is None else (float(final[0]), float(final[1]), float(final[2]))

    world.move(who, end, facing)
    tracks.extend(keys)
    return max(t, t_go, stop)


HANDLERS: dict[str, Handler] = {
    "exit": apply_exit,
    "turn_to": apply_turn_to,
    "follow": apply_follow,
}
