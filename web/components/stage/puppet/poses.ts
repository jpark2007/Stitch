/**
 * Poses and locomotion cycles from shared/rig/poses.json.
 *
 *   applyPose(pose, bones, out)       every bone's angle for a pose (0 if unlisted)
 *   poseRootOffset(pose)              the pose's rootOffset, rig units [x, y]
 *   compileCycle(cycle, bones)        flat, allocation-free form of a cycle
 *   sampleCycle(compiled, phase, out) angles at phase 0..1 (wraps)
 *   cyclePhase(distance, stride, scale)  phase from distance walked on stage
 *   dampRotations(cur, target, bones, k) ease one set of angles toward another
 *
 * Pure and dependency-free, so `node --test` runs it. The sampling functions
 * write into caller-owned objects and allocate nothing per frame.
 */

import type { Rotations } from "./kinematics";

/** A pose entry: bone angles, plus an optional rootOffset. */
export type PoseDef = { rootOffset?: readonly number[] } & { [bone: string]: number | readonly number[] | undefined };

export interface CycleFrame {
  phase: number;
  [bone: string]: number;
}

export interface CycleDef {
  strideLength: number;
  frames: readonly CycleFrame[];
}

export interface PoseLibrary {
  poses: Record<string, PoseDef>;
  cycles: Record<string, CycleDef>;
}

/** Write every bone's angle for `pose` into `out` (bones it does not list are 0). */
export function applyPose(pose: PoseDef | undefined, bones: readonly string[], out: Rotations): Rotations {
  for (const b of bones) {
    const v = pose?.[b];
    out[b] = typeof v === "number" ? v : 0;
  }
  return out;
}

/** The pose's rootOffset in rig units, `[0, 0]` when it has none. */
export function poseRootOffset(pose: PoseDef | undefined): [number, number] {
  const r = pose?.rootOffset;
  return r ? [r[0] ?? 0, r[1] ?? 0] : [0, 0];
}

/** A cycle reshaped for fast sampling: sorted phases and one value row per bone. */
export interface CompiledCycle {
  strideLength: number;
  bones: readonly string[];
  phases: Float64Array;
  /** values[boneIndex][frameIndex] */
  values: Float64Array[];
}

export function compileCycle(cycle: CycleDef, bones: readonly string[]): CompiledCycle {
  const frames = [...cycle.frames].sort((a, b) => a.phase - b.phase);
  if (frames.length === 0) throw new Error("poses: a cycle needs at least one frame");
  const phases = Float64Array.from(frames, (f) => f.phase);
  const values = bones.map((b) => Float64Array.from(frames, (f) => (typeof f[b] === "number" ? f[b] : 0)));
  return { strideLength: cycle.strideLength, bones, phases, values };
}

const wrap01 = (p: number) => p - Math.floor(p);

/**
 * The cycle's angles at `phase` (any real number; wraps to 0..1) into `out`.
 * `smooth` uses a periodic Catmull-Rom spline through the frames, which reads
 * as a softer gait; `false` interpolates linearly, which is what a timeline
 * built from the same frames plays (the stage interpolates keys linearly).
 */
export function sampleCycle(c: CompiledCycle, phase: number, out: Rotations, smooth = true): Rotations {
  const n = c.phases.length;
  const p = wrap01(phase);
  // Segment i runs from frame i to frame i+1 (wrapping past 1).
  let i = n - 1;
  for (let k = 0; k < n - 1; k++) {
    if (p < c.phases[k + 1]) {
      i = k;
      break;
    }
  }
  if (p < c.phases[0]) i = n - 1;
  const j = (i + 1) % n;
  const p0 = c.phases[i];
  let p1 = c.phases[j];
  if (p1 <= p0) p1 += 1;
  let pp = p;
  if (pp < p0) pp += 1;
  const t = p1 > p0 ? (pp - p0) / (p1 - p0) : 0;
  const h = (i - 1 + n) % n;
  const l = (j + 1) % n;
  for (let b = 0; b < c.bones.length; b++) {
    const v = c.values[b];
    const a = v[i];
    const d = v[j];
    if (!smooth || n < 3) {
      out[c.bones[b]] = a + (d - a) * t;
    } else {
      // Uniform Catmull-Rom (the rig's frames are evenly spaced).
      const m0 = (d - v[h]) * 0.5;
      const m1 = (v[l] - a) * 0.5;
      const t2 = t * t;
      const t3 = t2 * t;
      out[c.bones[b]] =
        (2 * t3 - 3 * t2 + 1) * a + (t3 - 2 * t2 + t) * m0 + (-2 * t3 + 3 * t2) * d + (t3 - t2) * m1;
    }
  }
  return out;
}

/**
 * Cycle phase after walking `distance` stage units, for a puppet of `scale`
 * stage units per rig unit: one full cycle (two steps) covers
 * strideLength × scale.
 */
export function cyclePhase(distance: number, strideLength: number, scale: number): number {
  return wrap01(distance / (strideLength * scale));
}

/**
 * Move `cur` toward `target` for the listed bones by fraction `k` (0..1).
 * For frame-rate independent easing pass k = 1 − exp(−dt × rate).
 */
export function dampRotations(cur: Rotations, target: Rotations, bones: readonly string[], k: number): Rotations {
  for (const b of bones) {
    const a = cur[b] ?? 0;
    cur[b] = a + ((target[b] ?? 0) - a) * k;
  }
  return cur;
}
