/**
 * Two-bone arm IK, exactly as shared/CONTRACTS.md "The puppet" states it:
 *
 *   v  = shoulder-to-target, in the shoulder's parent frame, rig units
 *   θ  = atan2(v.x, −v.y)
 *   a  = acos(|v| / 2L)
 *   upper = θ − a,  lower = 2a          (out of reach when |v| > 2L)
 *
 * Pure and dependency-free (no runtime imports), so `node --test` can run it
 * directly. See ik.test.mjs.
 */

export interface TwoBoneSolution {
  /** Rotation of the upper bone, relative to its parent (radians, CCW). */
  upper: number;
  /** Rotation of the lower bone, relative to the upper bone. */
  lower: number;
  /** False when the target is further than 2L: the arm points straight at it. */
  reachable: boolean;
}

/**
 * Solve the contract IK for a vector `(vx, vy)` from the shoulder to the
 * target and a bone length `L` (both bones equal). Out of reach, the arm is
 * fully extended toward the target (`a = 0`). Writes into `out` when given,
 * so a per-frame caller allocates nothing.
 */
export function solveTwoBoneIK(
  vx: number,
  vy: number,
  L: number,
  out: TwoBoneSolution = { upper: 0, lower: 0, reachable: true },
): TwoBoneSolution {
  const d = Math.hypot(vx, vy);
  if (d < 1e-9) {
    // Target on the shoulder: fold the arm straight back on itself.
    out.upper = -Math.PI / 2;
    out.lower = Math.PI;
    out.reachable = true;
    return out;
  }
  const theta = Math.atan2(vx, -vy);
  const reachable = d <= 2 * L;
  const a = reachable ? Math.acos(d / (2 * L)) : 0;
  out.upper = theta - a;
  out.lower = 2 * a;
  out.reachable = reachable;
  return out;
}

/** The subset of puppet.json the arm solver reads. */
export interface ArmRig {
  bones: Record<string, { pivot: readonly number[]; length: number }>;
  ik: Record<string, { upper: string; lower: string; socket: string }>;
}

/**
 * Put an arm's hand socket on a target given in the puppet's hip (root)
 * frame, in rig units, unmirrored. Accounts for the torso rotation (the
 * shoulder pivot turns with the torso and the result is relative to it).
 * `arm` is a key of `rig.ik` ("arm_l" or "arm_r").
 */
export function solveArmIK(
  rig: ArmRig,
  arm: string,
  targetX: number,
  targetY: number,
  torsoRot = 0,
  out: TwoBoneSolution = { upper: 0, lower: 0, reachable: true },
): TwoBoneSolution {
  const chain = rig.ik[arm];
  if (!chain) throw new Error(`ik: unknown arm "${arm}"`);
  const upper = rig.bones[chain.upper];
  const [px, py] = upper.pivot;
  const c = Math.cos(torsoRot);
  const s = Math.sin(torsoRot);
  // Shoulder in the hip frame (the torso pivots at the hip origin).
  const sx = px * c - py * s;
  const sy = px * s + py * c;
  // Shoulder-to-target, rotated back into the torso frame.
  const wx = targetX - sx;
  const wy = targetY - sy;
  const vx = wx * c + wy * s;
  const vy = -wx * s + wy * c;
  return solveTwoBoneIK(vx, vy, upper.length, out);
}
