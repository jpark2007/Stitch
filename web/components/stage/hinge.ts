/**
 * The played pop-up's hinge maths, kept free of React and three so it can be
 * checked in node (see hinge.check.ts).
 *
 * A flip is an open/close request in stage time. Per piece, the flips become a
 * piecewise-linear target path (back pieces first when opening, front first
 * when closing) that a critically damped hinge spring follows in closed form.
 */

import { followAt, linearAt, type LinearKeys } from "./timeline";

export interface PopUpTiming {
  /** Seconds between pieces when opening (back first). */
  openStagger: number;
  /** Seconds between pieces when closing (front first). */
  closeStagger: number;
  /** Seconds a unit-height piece takes to rise / fold down (taller is slower). */
  openTime: number;
  closeTime: number;
  /** Hinge spring speed (rad/s): lower is looser, with a bigger pop. */
  omega: number;
  /** For scrubbed `open`: share of the 0..1 range between piece starts. */
  scrubStagger: number;
}

export const DEFAULT_TIMING: PopUpTiming = {
  openStagger: 0.2,
  closeStagger: 0.1,
  openTime: 0.72,
  closeTime: 0.55,
  omega: 11,
  scrubStagger: 0.16,
};

export interface Flip {
  at: number;
  open: boolean;
}

/**
 * The hinge's target path for one piece: for each flip, a ramp from wherever
 * the target is towards 1 (open) or 0 (closed) that starts slowly and speeds
 * up, as a page opening drives a real mechanism. The spring following it
 * carries the final speed into one small overshoot (opening) or a soft landing
 * (closing, clamped at the page).
 *
 * Flips apply in the order they were issued. The stagger delays a flip by a
 * different amount per direction (an open waits for the pieces behind, a close
 * for the pieces in front), so a quick reversal could otherwise land before
 * the flip it reverses; instead it starts no earlier than that flip did, and
 * the last flip issued always sets where the piece ends up.
 */
export function hingePath(flips: readonly Flip[], order: number, count: number, size: number, t: PopUpTiming): LinearKeys {
  const issued = [...flips].sort((a, b) => a.at - b.at);
  let keys: LinearKeys = [];
  let prevAt = -Infinity;
  for (const f of issued) {
    const delay = f.open ? order * t.openStagger : (count - 1 - order) * t.closeStagger;
    const at = Math.max(f.at + delay, prevAt);
    prevAt = at;
    const from = linearAt(keys, at, 0);
    keys = keys.filter((k) => k[0] < at);
    keys.push([at, from]);
    const to = f.open ? 1 : 0;
    const dist = Math.abs(to - from);
    if (dist < 1e-3) continue;
    const dur = (f.open ? t.openTime : t.closeTime) * size * (0.35 + 0.65 * dist);
    keys.push([at + dur * 0.6, from + (to - from) * 0.3]);
    keys.push([at + dur, to]);
  }
  return keys;
}

/** The hinge's rise (0 flat .. 1 upright, briefly above on the pop) along a path at stage time `time`. */
export function riseAlong(keys: LinearKeys, time: number, size: number, t: PopUpTiming): number {
  return Math.max(0, followAt(keys, time, t.omega / Math.sqrt(size), 0));
}

/** Seconds after which a flip has finished for any set: stagger, ramp and settle. */
const SETTLE_WINDOW = 10;
/** Most flips kept, however fast they come (the last one still sets the end pose). */
const MAX_FLIPS = 4;

/**
 * Bound a growing list of issued flips at stage time `now`. Flips that have
 * long settled are dropped, except the newest of them, which holds the pose
 * the later ones start from. The present and future poses are unchanged.
 */
export function trimFlips(flips: readonly Flip[], now: number): Flip[] {
  let first = 0;
  for (let i = 0; i < flips.length - 1; i++) if (flips[i + 1].at < now - SETTLE_WINDOW) first = i + 1;
  return flips.slice(Math.max(first, flips.length - MAX_FLIPS));
}
