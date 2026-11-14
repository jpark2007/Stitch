"""Foot planting: keep the lowest foot on the standing surface through walk and run cycles.

shared/CONTRACTS.md, Clarifications 1: bent legs shorten the puppet, so with the hip at
``hipHeight`` above ``root.position`` the feet lift off the surface during a walk or run cycle.
The compiler plants them: at every walk / run cycle key it lowers ``root.position.y`` by
``(hipHeight - lowestFoot) * scale`` and sets ``Timeline.feetPlanted = true``, so the stage does
not plant them a second time.

``lowest_foot`` is a direct port of ``lowestFoot`` in web/components/stage/puppet/kinematics.ts:
forward kinematics composes each bone's pivot in its parent's frame, rotated by the parent's
absolute angle (angle = parent angle + rotation); a bone's tip is pivot + rotate(dir * length);
the lowest foot is ``max(0, -tip.y)`` over the two lower-leg bones, in rig units below the hip.
The legs hang from the root, not the torso, so the run cycle's torso lean does not move the feet.

``plant_feet`` is a post-pass over the finished TrackSet. motion.locomotion_keys leaves a
PLANT_CHANNEL marker at each interior cycle key time (tracks.py); for each marker it samples the
final leg rotations (whatever later actions keyed) and the final root, and lowers the root's y
there. The surface under the puppet at a marker time is the unplanted root's y (on a walk onto a
raised mark that is the lerp of the start and end heights), so planting never changes x, z or
any key away from a marker time. Pure and deterministic.
"""

from __future__ import annotations

import math
from collections.abc import Mapping

from screenplay import ik
from screenplay.tracks import TrackSet, round_value

FEET: tuple[str, str] = ("leg_l.lower", "leg_r.lower")
LEG_BONES: tuple[str, ...] = ("leg_l.upper", "leg_l.lower", "leg_r.upper", "leg_r.lower")
ROOT = "root.position"
PUPPET_PREFIX = "puppet:"

# A drop at or below this is no drop: standing legs give exactly 0, and no key is added.
_DROP_EPS = 1e-9


def bone_tip(bone: str, rots: Mapping[str, float]) -> tuple[float, float]:
    """A bone's tip in the hip frame, rig units, unmirrored: pivot + rotate(dir * length)."""
    (px, py), angle = ik.bone_frame(bone, rots)
    b = ik.RIG["bones"][bone]
    dx, dy = b["dir"][0] * b["length"], b["dir"][1] * b["length"]
    c, s = math.cos(angle), math.sin(angle)
    return (px + dx * c - dy * s, py + dx * s + dy * c)


def lowest_foot(rots: Mapping[str, float]) -> float:
    """How far below the hip the lower of the two feet is, in rig units (kinematics.ts
    ``lowestFoot``). Standing (no rotations) gives ``hipHeight``; bent legs give less."""
    drop = 0.0
    for foot in FEET:
        drop = max(drop, -bone_tip(foot, rots)[1])
    return drop


def plant_drop(rots: Mapping[str, float], build: str) -> float:
    """How far to lower root.position.y (stage units) so the lowest foot is on the surface."""
    return (ik.RIG["hipHeight"] - lowest_foot(rots)) * ik.scale(build)


def plant_feet(tracks: TrackSet, builds: Mapping[str, str]) -> None:
    """Lower ``root.position.y`` at every foot-planting marker of every puppet, in place.

    ``builds`` maps a person id to its build. For each marker time t (TrackSet.plant_times) the
    four leg rotations and the root are sampled from the tracks as they stand (every marker is
    sampled before any key changes). When the drop is above 1e-9, the root key(s) already at t
    (same time at the tracks' 3 dp) have their y lowered by it, so a two-key jump still steps;
    with no key at t, one is added at t: the sampled root with y lowered. Nothing else changes.
    """
    for target in tracks.plant_targets():
        if not target.startswith(PUPPET_PREFIX) or not tracks.has_track(target, ROOT):
            continue
        build = builds[target[len(PUPPET_PREFIX):]]
        drops: dict[float, tuple[float, list[float]]] = {}  # t -> (drop, sampled root)
        for t in tracks.plant_times(target):
            rots = {}
            for bone in LEG_BONES:
                v = tracks.value_at(target, f"{bone}.rot", t)
                rots[bone] = 0.0 if v is None else float(v)
            drop = plant_drop(rots, build)
            if drop > _DROP_EPS:
                root = tracks.value_at(target, ROOT, t)
                drops[t] = (drop, [float(c) for c in root])
        if not drops:
            continue
        keys = tracks.raw_keys(target, ROOT)
        keyed: set[float] = set()
        out: list[tuple[float, object]] = []
        for kt, value in keys:
            rt = round_value(float(kt))
            if rt in drops:
                keyed.add(rt)
                x, y, z = (float(c) for c in value)  # type: ignore[union-attr]
                value = [x, y - drops[rt][0], z]
            out.append((kt, value))
        for t, (drop, root) in drops.items():
            if t not in keyed:
                out.append((t, [root[0], root[1] - drop, root[2]]))
        tracks.replace_keys(target, ROOT, out)
