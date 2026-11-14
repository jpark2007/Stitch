/**
 * The stage's shot: while a moment plays, the book's camera leans into the
 * pop-up and follows the timeline's camera tracks (CONTRACTS.md: camera
 * position / lookAt / fov, eased by the player, Clarification 4).
 *
 * The timeline frames the set for a screen of its own. In the book that same
 * shot would crop away the words printed under the set and the page around
 * it, so the book goes `follow` of the way from its own view of the stage
 * page toward the track's camera: every move in the track (the establishing
 * move, the dolly onto a puppet) lands, scaled, and the page stays a page.
 * Two guards: the stage's width stays in frame on narrow screens, and the
 * words and pull tabs printed under the set stay above the frame's foot.
 *
 *   const shot = createStageShot();            // one per spread; the stage writes it each frame
 *   w = stepShotWeight(w, goal, dt, 1.6, 1.2); // the book eases toward it
 *   blendShot(out, bookPose, stagePagePose, shot, w, { follow, aspect, halfWidth });
 *
 * Pure maths on three's Vector3, no React (see shot.test.ts).
 */

import { Vector3 } from "three";

const DEG = Math.PI / 180;

/** A camera: where it is, what it looks at, its vertical fov (degrees). */
export interface ShotPose {
  readonly position: Vector3;
  readonly target: Vector3;
  fov: number;
}

/** What a playing stage offers the book's camera, in world space. */
export class StageShot implements ShotPose {
  /** 1 while the stage wants its shot (its telling under way, the page open and still), else 0. */
  want = 0;
  readonly position = new Vector3();
  readonly target = new Vector3();
  fov = 30;
  /** A point (world) the shot keeps in frame: the front of what is printed under the set. */
  readonly keep = new Vector3();
  hasKeep = false;

  /**
   * The stage wants (true) or gives up (false) its shot. Returns whether that
   * changed: the book's camera reads `want` before the stages' default-priority
   * work in a frame, so on a canvas that draws on demand the caller must ask
   * for one more frame when it does, or the camera never hears of it.
   */
  offer(on: boolean): boolean {
    const want = on ? 1 : 0;
    if (want === this.want) return false;
    this.want = want;
    return true;
  }

  /** The point the shot must keep in frame (world). */
  keepInFrame(x: number, y: number, z: number) {
    this.keep.set(x, y, z);
    this.hasKeep = true;
  }

  /** This frame's camera: position and look-at (world), vertical fov (degrees). */
  frame(px: number, py: number, pz: number, tx: number, ty: number, tz: number, fov: number) {
    this.position.set(px, py, pz);
    this.target.set(tx, ty, tz);
    this.fov = fov;
  }
}

export function createStageShot(): StageShot {
  return new StageShot();
}

export function createPose(fov = 30): ShotPose {
  return { position: new Vector3(), target: new Vector3(), fov };
}

export function copyPose(out: ShotPose, from: ShotPose): ShotPose {
  out.position.copy(from.position);
  out.target.copy(from.target);
  out.fov = from.fov;
  return out;
}

/**
 * The weight's linear ramp toward `goal` (0 or 1), `inDur` seconds to lean
 * in and `outDur` to come back out. blendShot eases it, so a ramp starts and
 * lands softly. A zero `dt` (the first frame) lands at once.
 */
export function stepShotWeight(w: number, goal: number, dt: number, inDur: number, outDur: number): number {
  if (dt <= 0) return goal;
  if (goal > w) return Math.min(goal, w + dt / inDur);
  if (goal < w) return Math.max(goal, w - dt / outDur);
  return w;
}

export interface ShotBlend {
  /** 0..1: how far from the book's view of the stage page toward the track's camera. */
  follow: number;
  /** Canvas width / height. */
  aspect: number;
  /** Half the width (world units) that must stay in frame at the shot's target. */
  halfWidth: number;
  /** A point (world) to keep in frame, above `keepBelow` of the frame's lower half (0..1). */
  keep?: Vector3 | null;
  keepBelow?: number;
}

const tmpPos = new Vector3();
const tmpTarget = new Vector3();
const tmpFwd = new Vector3();
const tmpUp = new Vector3();
const tmpKeep = new Vector3();

/**
 * The book's camera at shot weight `w` (0..1, eased here with smoothstep):
 * `book` at 0; at 1, `stagePage` (the book's own view of the stage page)
 * moved `follow` of the way toward `track`; backed off along its line of
 * sight if `halfWidth` either side of its target would leave the frame, and
 * tilted down if the `keep` point would. Writes `out`; no allocation.
 */
export function blendShot(out: ShotPose, book: ShotPose, stagePage: ShotPose, track: ShotPose, w: number, opts: ShotBlend): ShotPose {
  const a = w <= 0 ? 0 : w >= 1 ? 1 : w * w * (3 - 2 * w);
  if (a === 0) return copyPose(out, book);
  const k = opts.follow;
  tmpTarget.lerpVectors(stagePage.target, track.target, k);
  tmpPos.lerpVectors(stagePage.position, track.position, k);
  const fov = stagePage.fov + (track.fov - stagePage.fov) * k;
  const tanV = Math.tan((fov * DEG) / 2);
  // Fit: never so close that the stage's width leaves the frame.
  const need = opts.halfWidth / (tanV * Math.max(0.1, opts.aspect));
  tmpPos.sub(tmpTarget);
  let d = tmpPos.length();
  if (d > 1e-6 && d < need) {
    tmpPos.multiplyScalar(need / d);
    d = need;
  }
  tmpPos.add(tmpTarget);
  if (d > 1e-6 && opts.keep) {
    // Keep the point in frame by tilting down to it, not backing off: a
    // push-in still lands closer, and looks down at the words if it must.
    tmpFwd.subVectors(tmpTarget, tmpPos).multiplyScalar(1 / d);
    tmpUp.set(0, 1, 0).addScaledVector(tmpFwd, -tmpFwd.y).normalize();
    tmpKeep.subVectors(opts.keep, tmpPos);
    const below = Math.atan2(-tmpKeep.dot(tmpUp), tmpKeep.dot(tmpFwd));
    const limit = Math.atan(tanV * (opts.keepBelow ?? 0.85));
    if (below > limit) {
      // Rotating the line of sight about the camera's right axis by -δ
      // turns it toward -up: f' = f cos δ - up sin δ.
      const delta = below - limit;
      tmpFwd.multiplyScalar(Math.cos(delta)).addScaledVector(tmpUp, -Math.sin(delta));
      tmpTarget.copy(tmpPos).addScaledVector(tmpFwd, d);
    }
  }
  out.position.lerpVectors(book.position, tmpPos, a);
  out.target.lerpVectors(book.target, tmpTarget, a);
  out.fov = book.fov + (fov - book.fov) * a;
  return out;
}
