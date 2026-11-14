/**
 * The springs that pose a leaf: per bone, an elevation angle that chases its
 * turn target and a twist about the head edge that chases the corner lift.
 * Springs are stiff at the hinge and softer toward the free edge, so the edge
 * trails a turn, swings past by a hair and settles, which is what makes the
 * sheet read as paper rather than a rotating board.
 *
 * Pure (no imports, no allocation after construction), so the landing test in
 * stacking.test.ts runs a sheet's real motion.
 */

/* Natural frequency (rad/s) at the hinge and at the free edge, and damping. */
const W_ROOT = 24;
const W_TIP = 13.5;
const ZETA = 0.7;
const W_TWIST = 15;
const TWIST_ZETA = 0.85;
/** Largest integration step (s). */
const MAX_STEP = 1 / 240;
/** Longest frame the springs integrate; a longer stall is taken as this. */
export const MAX_FRAME = 1 / 20;

/** Per-bone twist targets for a total `twist`, building toward the free edge. */
export function twistTargets(twist: number, out: Float32Array): Float32Array {
  const n = out.length;
  const norm = ((n - 1) * n) / 2;
  for (let i = 0; i < n; i++) out[i] = (twist * i) / norm;
  return out;
}

export class LeafSprings {
  /** Elevation of each bone segment (radians from +x) and its rate. */
  readonly phi: Float32Array;
  readonly vel: Float32Array;
  /** Twist of each bone about the sheet's width and its rate. */
  readonly tw: Float32Array;
  readonly twv: Float32Array;
  /** Length of one bone segment. */
  private readonly seg: number;
  /** The sheet's extent along the twist axis (head edge to tail). */
  private readonly height: number;

  constructor(bones: number, seg: number, height: number) {
    this.seg = seg;
    this.height = height;
    this.phi = new Float32Array(bones);
    this.vel = new Float32Array(bones);
    this.tw = new Float32Array(bones);
    this.twv = new Float32Array(bones);
  }

  /** Jump straight to the targets, still. */
  snap(target: Float32Array, twTarget: Float32Array) {
    this.phi.set(target);
    this.tw.set(twTarget);
    this.vel.fill(0);
    this.twv.fill(0);
  }

  /**
   * Advance `dt` seconds toward the targets. `stiff` scales every spring.
   * The sheet never sinks into either stack: each bone is clamped between the
   * right rest elevation `restR[i]` and the left one, `PI - restL[i]`, and
   * the twist is held so it cannot press the tail in either (`holdTwist`).
   */
  step(target: Float32Array, twTarget: Float32Array, stiff: number, dt: number, restR: Float32Array, restL: Float32Array) {
    const { phi, vel, tw, twv } = this;
    const n = phi.length;
    const t = Math.min(dt, MAX_FRAME);
    const steps = Math.max(1, Math.ceil(t / MAX_STEP));
    const h = t / steps;
    const wt = W_TWIST * stiff;
    for (let s = 0; s < steps; s++) {
      for (let i = 0; i < n; i++) {
        const w = (W_ROOT + ((W_TIP - W_ROOT) * i) / (n - 1)) * stiff;
        vel[i] += (w * w * (target[i] - phi[i]) - 2 * ZETA * w * vel[i]) * h;
        phi[i] += vel[i] * h;
        twv[i] += (wt * wt * (twTarget[i] - tw[i]) - 2 * TWIST_ZETA * wt * twv[i]) * h;
        tw[i] += twv[i] * h;
      }
    }
    for (let i = 0; i < n; i++) {
      const lo = restR[i];
      const hi = Math.PI - restL[i];
      if (phi[i] < lo) {
        phi[i] = lo;
        if (vel[i] < 0) vel[i] = 0;
      } else if (phi[i] > hi) {
        phi[i] = hi;
        if (vel[i] > 0) vel[i] = 0;
      }
    }
    this.holdTwist(restR, restL);
  }

  /**
   * Twist turns the sheet about its head edge, so at bone `i` the tail drops
   * by `height * sin(c) * cos(phi)` (`c` the twist summed up the chain) over
   * a sheet lying right, and rises by it over the left. A sheet coming down
   * on a stack still carries some twist from its corner lift, and where its
   * gutter already lies on the stack that pressed the tail into it (the stale
   * page showed through, or the page block on a flung page). Like the stack
   * pressing the paper flat, `c` is held to what the sheet's clearance over
   * the stack under that bone allows. Lifting twist is never limited.
   */
  private holdTwist(restR: Float32Array, restL: Float32Array) {
    const { phi, tw, twv, seg, height } = this;
    let y = 0;
    let yR = 0;
    let yL = 0;
    let c = 0;
    for (let i = 0; i < phi.length; i++) {
      // Heights of this bone's far joint: the sheet, and each stack's top sheet.
      y += seg * Math.sin(phi[i]);
      yR += seg * Math.sin(restR[i]);
      yL += seg * Math.sin(restL[i]);
      c += tw[i];
      const cos = Math.cos(phi[i]);
      const room = (Math.max(0, cos >= 0 ? y - yR : y - yL) / height) / Math.max(1e-6, Math.abs(cos));
      if (room >= 1) continue;
      const limit = Math.asin(room);
      // Over the right stack a positive twist presses in; over the left, a negative one.
      if (cos >= 0 && c > limit) {
        tw[i] -= c - limit;
        c = limit;
        if (twv[i] > 0) twv[i] = 0;
      } else if (cos < 0 && c < -limit) {
        tw[i] += -limit - c;
        c = -limit;
        if (twv[i] < 0) twv[i] = 0;
      }
    }
  }

  /** How far from still at the targets (radians, speeds weighted down). */
  error(target: Float32Array, twTarget: Float32Array): number {
    const { phi, vel, tw, twv } = this;
    let err = 0;
    for (let i = 0; i < phi.length; i++) {
      err = Math.max(err, Math.abs(target[i] - phi[i]), Math.abs(vel[i]) * 0.05);
      err = Math.max(err, Math.abs(twTarget[i] - tw[i]), Math.abs(twv[i]) * 0.05);
    }
    return err;
  }
}
