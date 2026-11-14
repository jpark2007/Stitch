"""Helpers shared by the ``verbs_*`` handler modules.

Deliberately not named ``verbs_*``: ``compiler._discover_handlers`` imports every
``screenplay.verbs_*`` module looking for ``HANDLERS``, and this module has none. It imports no
``verbs_*`` module either, so the import graph stays acyclic (every verb module may import it).

Puppet geometry read back from the tracks (``puppet_root``, ``bone_at``, ``bones_at``,
``body_at``, ``arm_at``, ``held_arm_bones``, ``gesture_arm``, ``arm_of_hand``,
``puppet_target``) lives in ``screenplay.locate``; the verb modules import it from there.
What is here:

- parameter and world checks that raise ``StateError`` with the exact codes and messages the
  verb modules have always used (``require_param``, ``require_value``, ``require_person``,
  ``require_on_stage``, ``require_prop_on_stage``);
- small value helpers (``sign``, ``vec3``, ``bone_after``);
- getting a puppet up off the floor before it moves (``stand_up_keys``);
- the crouch that pick_up and place_on use to reach a grip too low for a standing arm: the
  solver (``crouch_solution`` and its parts, ``kneeling_ground``) and its keys
  (``crouch_reach_keys``).

Everything is pure: it reads ``world`` and ``tracks`` and changes neither.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Mapping
from dataclasses import replace

from screenplay import body as B
from screenplay import ik
from screenplay.locate import Locate, Vec3, body_at, bone_at, bones_at, held_arm_bones, puppet_target
from screenplay.schema import Action
from screenplay.state import SEATED_POSES, StateError, World
from screenplay.tracks import Key, TrackSet

__all__ = [
    "CROUCH_BEND_STEP",
    "CROUCH_COMFORT",
    "CROUCH_KNEEL_COST",
    "CROUCH_MAX_BEND",
    "OFFSTAGE_HINT",
    "CrouchTry",
    "Handler",
    "Key",
    "Locate",
    "Vec3",
    "bent_shoulder",
    "bone_after",
    "crouch_effort",
    "crouch_pick",
    "crouch_reach_keys",
    "crouch_solution",
    "crouch_tries",
    "kneeling_ground",
    "require_on_stage",
    "require_param",
    "require_person",
    "require_prop_on_stage",
    "require_value",
    "sign",
    "solve_bent_arm",
    "stand_up_keys",
    "vec3",
]

Handler = Callable[[World, TrackSet, Action, float, float, Locate], float]
"""``apply_<verb>(world, tracks, action, t, action_start, locate) -> free-again time``."""

POSES: dict[str, dict] = ik.POSES["poses"]

# The suffix walk_to, run_to, pose and the body verbs put on the "offstage" error: a puppet
# off the stage is brought on with ``enter``.
OFFSTAGE_HINT = "; use enter first"


# ----------------------------------------------------------------------- checks

def require_param(action: Action, field: str) -> str:
    """``getattr(action, field)``, or ``StateError("missing_param", "<verb> needs <field>")``.

    ``field`` is the Action attribute name; ``from_`` is reported as ``from`` (its screenplay
    spelling). The wording of verbs_body, verbs_vehicles and set_state.
    """
    value = getattr(action, field)
    if value is None:
        name = "from" if field == "from_" else field
        raise StateError("missing_param", f"{action.do} needs {name}")
    return value


def require_value(value: str | None, name: str) -> str:
    """``value``, or ``StateError("missing_param", 'it needs "<name>"')`` when it is None.

    verbs_core's wording (enter, walk_to, run_to, pose, pick_up, hold, drop, hand_to). It
    differs from ``require_param``'s and is kept so those verbs' messages do not change.
    """
    if value is None:
        raise StateError("missing_param", f'it needs "{name}"')
    return value


def require_person(world: World, who: str | None, *, code: str = "unknown_id") -> str:
    """``who`` when it is a cast member, else ``StateError(code, "<who> is not in the cast")``.

    A missing ``who`` (None) reads "None is not in the cast". verbs_core uses
    ``code="unknown_person"``; the other verb modules the default ``unknown_id``.
    """
    if who is None or who not in world.people:
        raise StateError(code, f"{who} is not in the cast")
    return who


def require_on_stage(world: World, who: str, hint: str = OFFSTAGE_HINT) -> None:
    """``StateError("offstage", "<who> is offstage<hint>")`` unless the puppet is on stage.

    The default hint is ``OFFSTAGE_HINT`` ("; use enter first"); pick_up passes ``""``.
    """
    if not world.on_stage(who):
        raise StateError("offstage", f"{who} is offstage{hint}")


def require_prop_on_stage(world: World, what: str) -> Vec3:
    """A prop's world anchor, or ``StateError("not_on_stage", "<what> is not on stage")`` when
    it has none (not placed yet, or held)."""
    anchor = world.props[what].world_pos
    if anchor is None:
        raise StateError("not_on_stage", f"{what} is not on stage")
    return anchor


# ----------------------------------------------------------------------- values

def sign(v: float) -> int:
    """1, -1, or 0 within 1e-9 of zero."""
    return 1 if v > 1e-9 else -1 if v < -1e-9 else 0


def vec3(v: Iterable[float]) -> Vec3:
    """Any 3-sequence (a track's ``[x, y, z]``) as a tuple of floats."""
    x, y, z = (float(c) for c in v)
    return (x, y, z)


def bone_after(emitted: Mapping[tuple[str, str], object], tracks: TrackSet, who: str, bone: str, t: float) -> float:
    """A bone's rotation once keys this action already emits have played: its last value in
    ``emitted`` (``body.last_values`` of those keys), else its track at ``t`` (0 with none)."""
    v = emitted.get((puppet_target(who), f"{bone}.rot"))
    return float(v) if v is not None else bone_at(tracks, who, bone, t)  # type: ignore[arg-type]


# --------------------------------------------------------------------- stand up

def stand_up_keys(world: World, tracks: TrackSet, who: str, t: float) -> tuple[list[Key], float]:
    """A puppet sitting or kneeling on the floor gets up before it moves. Held arms keep the
    hold pose. Returns (keys, when it is standing); a puppet already standing, or seated on a
    prop, gets no keys and ``t``.

    Only bones that already have a track are returned to 0. verbs_motion.stand_up_first is a
    different variant (every rig bone, and it also returns the standing root), kept there."""
    p = world.people[who]
    if p.seat is not None or p.pose not in SEATED_POSES:
        return [], t
    skip = held_arm_bones(world, who)
    current = {b: v for b, v in bones_at(tracks, who, t).items() if b not in skip}
    keys = B.stand_keys(body_at(world, tracks, who, t), t, current, (p.pos[0], p.pos[1]), z=p.pos[2])
    return keys, t + B.POSE_SECONDS


# ----------------------------------------------------------------------- crouch

# A grip below the shoulder that a standing arm cannot reach (a prop on the floor): the puppet
# bends forward at the torso, kneeling first if a bend alone is not enough.
CROUCH_BEND_STEP = 0.05  # radians between the torso bends tried
CROUCH_MAX_BEND = 1.2    # deepest forward bend (applied as a negative torso rotation)
CROUCH_COMFORT = 0.95    # prefer a bend that leaves the arm at most this fraction of full stretch
CROUCH_KNEEL_COST = 0.5  # kneeling counts as this much extra bend when choosing how to get down

CrouchTry = tuple[float, bool, float, "tuple[float, float] | None"]  # (dist, kneel, bend, angles)


def kneeling_ground(body: ik.Body) -> tuple[float, float]:
    """The root of a puppet that kneels where ``body`` stands (body.kneel_keys' arithmetic)."""
    s = ik.scale(body.build)
    off = POSES["kneel"]["rootOffset"]
    return (body.ground[0] + body.facing * off[0] * s, body.ground[1] + off[1] * s)


def bent_shoulder(arm: str, ground: tuple[float, float], facing: int, build: str, bend: float) -> tuple[float, float]:
    """World position of an arm's shoulder pivot with the torso rotated by ``bend``."""
    return ik.shoulder_world(arm, ground, facing, build, torso=bend)


def solve_bent_arm(
    arm: str, target: tuple[float, float], ground: tuple[float, float], facing: int, build: str, bend: float
) -> tuple[float, tuple[float, float] | None]:
    """Two-bone IK from a torso at ``bend`` (ik.arm_solution: the upper arm's rotation is
    relative to the torso). Returns (shoulder-to-target distance in rig units, (upper, lower)
    or None when out of reach)."""
    return ik.arm_solution(arm, target, ground, facing, build, torso=bend)


def crouch_effort(kneel: bool, bend: float) -> float:
    """How much getting down costs: the bend in radians, plus CROUCH_KNEEL_COST for kneeling."""
    return -bend + (CROUCH_KNEEL_COST if kneel else 0.0)


def crouch_tries(
    body: ik.Body, arm: str, grip: tuple[float, float], can_kneel: bool, max_bend: float = CROUCH_MAX_BEND
) -> list[CrouchTry]:
    """Every bend down to ``max_bend`` where it stands, and while kneeling if allowed, with the
    arm's solve (``dist`` in rig units), least effort first (standing before kneeling on a tie)."""
    postures = [(False, body.ground)]
    if can_kneel:
        postures.append((True, kneeling_ground(body)))
    steps = int(round(max_bend / CROUCH_BEND_STEP))
    bends = [-i * CROUCH_BEND_STEP for i in range(steps + 1)]
    tries: list[CrouchTry] = []
    for kneel, ground in postures:
        for bend in bends:
            dist, angles = solve_bent_arm(arm, grip, ground, body.facing, body.build, bend)
            tries.append((dist, kneel, bend, angles))
    tries.sort(key=lambda tr: (crouch_effort(tr[1], tr[2]), tr[1]))
    return tries


def crouch_pick(tries: list[CrouchTry], length: float) -> tuple[bool, CrouchTry] | None:
    """(comfortable, the try) for the least effort within CROUCH_COMFORT of full stretch,
    else (False, the least effort that reaches at all), else None."""
    for comfortable, limit in ((True, CROUCH_COMFORT * 2 * length), (False, math.inf)):
        for tr in tries:
            if tr[3] is not None and tr[0] <= limit:
                return comfortable, tr
    return None


def crouch_solution(
    body: ik.Body, arm: str, grip: tuple[float, float], can_kneel: bool, max_bend: float = CROUCH_MAX_BEND
) -> tuple[bool, float, tuple[float, float]]:
    """The gentlest way down to a low grip: (kneel, torso bend, arm angles).

    Candidates are a bend where it stands or a bend while kneeling, ranked by effort: the bend
    in radians, plus CROUCH_KNEEL_COST for kneeling (so a slight stoop beats kneeling, and
    kneeling beats a deep stoop). The least effort that leaves the arm within CROUCH_COMFORT of
    full stretch wins; failing that, the least effort that reaches at all. Raises ik.OutOfReach
    with the closest miss when nothing reaches. Bends go down to ``max_bend``."""
    s = ik.scale(body.build)
    length = ik.RIG["bones"][ik.arm_bones(arm)[0]]["length"]
    tries = crouch_tries(body, arm, grip, can_kneel, max_bend)
    pick = crouch_pick(tries, length)
    if pick is not None:
        _, (_, kneel, bend, angles) = pick
        assert angles is not None
        return kneel, bend, angles
    closest = min(dist for dist, *_ in tries)
    raise ik.OutOfReach(arm, closest * s, 2 * length * s, body.id)


def crouch_reach_keys(
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
    arm_after: tuple[float, float],
    parent: str,
    position: Iterable[float],
) -> tuple[list[Key], float]:
    """A hand reaching a grip too low for a standing arm, and back up (pick_up's take and
    place_on's release share it).

    Bends (and kneels, if crouch_solution needs it) over POSE_SECONDS, reaches the grip over
    REACH_SECONDS, where ``what`` gets a ``parent`` key and a ``position`` key (``position``
    as a list of floats), then comes back up over POSE_SECONDS with the arm at ``arm_after``
    (the hold pose for pick_up, rest for place_on). ``before`` are keys the action already
    emits ahead of this (an auto-walk), whose last values are the puppet's bones at ``t``;
    bones it does not touch come from the tracks at ``t``. ``can_kneel=False`` (a puppet
    already down) only bends, leaving the legs and root alone. Raises ik.OutOfReach (the
    closest miss) when even crouching cannot reach. Returns (keys, end)."""
    kneel, bend, reach = crouch_solution(body, arm, grip, can_kneel, max_bend)
    who = body.id
    target = puppet_target(who)
    emitted = B.last_values(before)

    def now(bone: str) -> float:
        return bone_after(emitted, tracks, who, bone, t)

    t_down = t + B.POSE_SECONDS
    t_grab = t_down + B.REACH_SECONDS
    t_up = t_grab + B.POSE_SECONDS
    upper, lower = ik.arm_bones(arm)
    torso = now("torso")
    keys: list[Key] = [
        (target, "torso.rot", t, torso), (target, "torso.rot", t_down, bend),
        (target, "torso.rot", t_grab, bend), (target, "torso.rot", t_up, torso),
    ]
    for bone, values in ((upper, (current_arm[0], reach[0], arm_after[0])),
                         (lower, (current_arm[1], reach[1], arm_after[1]))):
        keys += [(target, f"{bone}.rot", t, float(values[0])), (target, f"{bone}.rot", t_down, float(values[0])),
                 (target, f"{bone}.rot", t_grab, float(values[1])), (target, f"{bone}.rot", t_up, float(values[2]))]
    if kneel:  # down over POSE_SECONDS, held kneeling while reaching, then up again
        legs = {b: now(b) for b in POSES["kneel"] if b.startswith("leg_")}
        keys += B.kneel_keys(body, t, legs, z=z)
        low = replace(body, ground=kneeling_ground(body))
        kneeling = {b: float(v) for b, v in POSES["kneel"].items() if b.startswith("leg_")}
        keys += B.stand_keys(low, t_grab, kneeling, body.ground, z=z)
    keys += [(f"prop:{what}", "parent", t_grab, parent),
             (f"prop:{what}", "position", t_grab, [float(c) for c in position])]
    return keys, t_up
