/**
 * Foot planting for timelines the compiler has not planted
 * (CONTRACTS.md "Clarifications" 1: `Timeline.feetPlanted` absent or false).
 *
 * Walk and run cycles bend the legs, which shortens the puppet, so with the
 * hip held at hipHeight its feet lift off the surface. The fix is the one the
 * compiler (and the puppet lab) uses: lower root.position.y by
 * `(hipHeight − lowestFoot) × scale`.
 *
 * Only on walk and run keys, though. Sit and kneel also bend the legs, but
 * their rootOffset is already baked into root.position, so lowering them again
 * would sink the puppet into its seat. The player cannot see verbs, but it can
 * see locomotion: walk and run keys are exactly the stretches where
 * root.position travels. So the drop applies inside travelling stretches
 * (consecutive moving segments merged) and eases in and out at their ends,
 * where the cycle is back at standing anyway.
 *
 * Pure and allocation-free per call; type-only imports, so node runs it.
 */

import type { RigDef } from "../puppet/kinematics";

/** Seconds over which the drop eases in and out at a stretch's ends. */
const EDGE = 0.08;

interface LegChain {
  upper: string;
  lower: string;
  /** upper pivot height (hip frame) */
  py: number;
  /** lower pivot (the knee) in the upper's frame */
  lx: number;
  ly: number;
  /** lower bone vector */
  vx: number;
  vy: number;
}

function chain(rig: RigDef, side: "l" | "r"): LegChain | null {
  const upper = `leg_${side}.upper`;
  const lower = `leg_${side}.lower`;
  const a = rig.bones[upper];
  const b = rig.bones[lower];
  if (!a || !b) return null;
  return {
    upper,
    lower,
    py: a.pivot[1],
    lx: b.pivot[0],
    ly: b.pivot[1],
    vx: b.dir[0] * b.length,
    vy: b.dir[1] * b.length,
  };
}

/** y of the vector (x, y) turned by a radians (CCW). */
const rot2y = (x: number, y: number, a: number) => x * Math.sin(a) + y * Math.cos(a);

/**
 * How far below the hip the lower foot is, in rig units, for leg rotations
 * read through `get` (both legs hang off the root, so the torso does not
 * matter). Standing, it equals hipHeight.
 */
export function lowestFootDepth(legs: readonly LegChain[], get: (bone: string) => number): number {
  let drop = 0;
  for (let i = 0; i < legs.length; i++) {
    const c = legs[i];
    const a = get(c.upper);
    const b = a + get(c.lower);
    // Knee: the lower's pivot in the upper's frame; foot: the lower's tip.
    const ky = c.py + rot2y(c.lx, c.ly, a);
    const fy = ky + rot2y(c.vx, c.vy, b);
    if (-fy > drop) drop = -fy;
  }
  return drop;
}

/** Times and travel flags of a root.position track, merged into stretches. */
export interface Stretches {
  /** start0, end0, start1, end1, ... (sorted) */
  readonly bounds: Float64Array;
}

/**
 * The stretches where a root.position track travels (x or z changes across a
 * key segment), with consecutive moving segments merged. `keys` are the
 * track's [t, [x, y, z]] pairs, sorted by t.
 */
export function travelStretches(keys: readonly (readonly [number, unknown])[]): Stretches {
  const out: number[] = [];
  let open = -1;
  for (let i = 0; i + 1 < keys.length; i++) {
    const a = keys[i][1];
    const b = keys[i + 1][1];
    const moving =
      Array.isArray(a) &&
      Array.isArray(b) &&
      (Math.abs(Number(a[0]) - Number(b[0])) > 1e-6 || Math.abs(Number(a[2] ?? 0.5) - Number(b[2] ?? 0.5)) > 1e-6);
    if (moving && open < 0) open = keys[i][0];
    if (!moving && open >= 0) {
      out.push(open, keys[i][0]);
      open = -1;
    }
  }
  if (open >= 0 && keys.length) out.push(open, keys[keys.length - 1][0]);
  return { bounds: Float64Array.from(out) };
}

/** 0..1: how much of the planting drop applies at time t. */
export function travelWeight(s: Stretches, t: number): number {
  const b = s.bounds;
  for (let i = 0; i + 1 < b.length; i += 2) {
    const t0 = b[i];
    const t1 = b[i + 1];
    if (t < t0) return 0;
    if (t <= t1) {
      const e = Math.min(EDGE, (t1 - t0) / 2);
      const k = e > 0 ? Math.min(1, (t - t0) / e, (t1 - t) / e) : 1;
      return k * k * (3 - 2 * k);
    }
  }
  return 0;
}

/** Plants one puppet's feet. Build once per puppet per timeline. */
export class FootPlanter {
  private readonly legs: LegChain[];
  private readonly hipHeight: number;
  private readonly stretches: Stretches;

  constructor(rig: RigDef, positionKeys: readonly (readonly [number, unknown])[]) {
    this.legs = [chain(rig, "l"), chain(rig, "r")].filter((c): c is LegChain => c !== null);
    this.hipHeight = rig.hipHeight;
    this.stretches = travelStretches(positionKeys);
  }

  /** True when there is nothing to plant (the puppet never travels). */
  get idle(): boolean {
    return this.stretches.bounds.length === 0;
  }

  /**
   * How far to lower root.position.y at time t (stage units, >= 0), for bone
   * rotations read through `get` and the puppet's `scale`.
   */
  drop(t: number, get: (bone: string) => number, scale: number): number {
    const w = travelWeight(this.stretches, t);
    if (w <= 0) return 0;
    const lift = this.hipHeight - lowestFootDepth(this.legs, get);
    return lift > 0 ? lift * scale * w : 0;
  }
}
