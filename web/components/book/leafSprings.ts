/**
 * The springs that pose a leaf: per bone, an elevation angle that chases its
 * turn target and a twist about the head edge that chases the corner lift.
 * Springs are stiff at the hinge and softer toward the free edge, so the edge
 * trails a turn, swings past by a hair and settles, which is what makes the
 * sheet read as paper rather than a rotating board.
 *
 * Pure (no imports, no allocation after construction), so landing.test.ts
 * runs a sheet's real motion.
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
/**
 * A bone keeps at least this share of the next bone's lift off a stack
 * (`liftOff`): with two mesh columns per bone, a third is the least that
 * keeps the skin's blend off the stack. Past `LIFT_REACH` radians the blend
 * cuts no deeper, so the lift it asks for stops growing there.
 */
const LIFT_SHARE = 1 / 3;
const LIFT_REACH = 1.5;
/**
 * How high (page widths) a bone must sit over a stack before its twist may
 * lower the tail toward it (`holdTwist`). The tail between two joints is a
 * blend that sags a little below them, so a tail held exactly to the stack
 * at each joint still dipped into it between.
 */
const TWIST_CLEARANCE = 0.002;

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
   * right rest elevation `restR[i]` and the left one, `PI - restL[i]`, lifts
   * off a stack no more sharply than its skin can follow (`liftOff`), and its
   * twist is held so it cannot press the tail in either (`holdTwist`).
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
    // A bone the stack stops comes to rest on it. Keeping its speed away from
    // the stack (the spring chasing a stack that rose into it) bounced the
    // stack's top sheet up off its block as a page landed, and the landing
    // page, lying on the block, showed the stale one through it.
    for (let i = 0; i < n; i++) {
      const lo = restR[i];
      const hi = Math.PI - restL[i];
      if (phi[i] < lo) {
        phi[i] = lo;
        vel[i] = 0;
      } else if (phi[i] > hi) {
        phi[i] = hi;
        vel[i] = 0;
      }
    }
    this.liftOff(restR, restL);
    this.holdTwist(restR, restL);
  }

  /**
   * A skinned sheet blends each vertex between two bones, so where a bone
   * lying on a stack meets the next one bent up off it, the blend cuts the
   * corner below the stack by up to a quarter segment times the bend: on a
   * page landing fast, the stale page showed through near the gutter. Holding
   * each bone off the stack by `LIFT_SHARE` of the next one's lift keeps
   * every vertex at or above it; the bend runs back toward the gutter as a
   * short ramp, as stiff paper rolls down rather than folding.
   */
  private liftOff(restR: Float32Array, restL: Float32Array) {
    const { phi, vel } = this;
    for (let i = phi.length - 2; i >= 0; i--) {
      const offR = Math.min(LIFT_REACH, phi[i + 1] - restR[i + 1]) * LIFT_SHARE;
      if (phi[i] - restR[i] < offR) {
        phi[i] = restR[i] + offR;
        if (vel[i] < 0) vel[i] = 0;
      }
      const offL = Math.min(LIFT_REACH, Math.PI - restL[i + 1] - phi[i + 1]) * LIFT_SHARE;
      if (Math.PI - restL[i] - phi[i] < offL) {
        phi[i] = Math.PI - restL[i] - offL;
        if (vel[i] > 0) vel[i] = 0;
      }
    }
  }

  /**
   * Twist turns each bone about its own length, so the tail edge (`height`
   * from the head) at the start of bone `i` drops by `height * sum(tw[j] *
   * cos(phi[j]))` over the bones up to it: positive twist presses it down
   * over a sheet lying right, negative over the left. A sheet coming down on
   * a stack still carries some twist from its corner lift, and where its
   * gutter already lies on the stack that pressed the tail into it (the stale
   * page showed through, or the page block on a flung page). Like the stack
   * pressing the paper flat, the drop is held to the sheet's clearance over
   * the stack at that joint. Lifting twist is never limited.
   */
  private holdTwist(restR: Float32Array, restL: Float32Array) {
    const { phi, tw, twv, seg, height } = this;
    let x = 0;
    let y = 0;
    let yR = 0;
    let yL = 0;
    let drop = 0;
    for (let i = 0; i < phi.length; i++) {
      const cos = Math.cos(phi[i]);
      drop += height * tw[i] * cos;
      // Clearance of this bone's start over the stack it is above.
      const room = Math.max(0, y - (x >= 0 ? yR : yL) - TWIST_CLEARANCE);
      if (drop > room && Math.abs(cos) > 1e-3) {
        tw[i] -= (drop - room) / (height * cos);
        drop = room;
        // Its rate into the stack, too.
        if (twv[i] * cos > 0) twv[i] = 0;
      }
      x += seg * cos;
      y += seg * Math.sin(phi[i]);
      yR += seg * Math.sin(restR[i]);
      yL += seg * Math.sin(restL[i]);
    }
  }

  /**
   * How far (page widths) the sheet's free edge is from where it would lie
   * on a stack whose top sheet rests at elevations `rest` (`left` for the
   * left stack, mirrored): the farther of the head and tail corners, the tail
   * carried by the twist as the bones carry it. 0 lying on that stack.
   */
  edgeGap(rest: Float32Array, left: boolean): number {
    const { phi, tw, seg, height } = this;
    let dx = 0;
    let dy = 0;
    let sx = 0;
    let sy = 0;
    for (let i = 0; i < phi.length; i++) {
      const cos = Math.cos(phi[i]);
      const sin = Math.sin(phi[i]);
      const a = left ? Math.PI - rest[i] : rest[i];
      dx += seg * (cos - Math.cos(a));
      dy += seg * (sin - Math.sin(a));
      // The tail's swing about each bone's length (see holdTwist).
      sx += tw[i] * sin;
      sy -= tw[i] * cos;
    }
    const tx = dx + height * sx;
    const ty = dy + height * sy;
    return Math.sqrt(Math.max(dx * dx + dy * dy, tx * tx + ty * ty));
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
