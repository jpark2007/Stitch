/**
 * Book dimensions and the pure maths of a turning leaf.
 *
 * Book space: the spine runs along z at x = 0, right pages extend to +x, left
 * pages to -x, y is up and the table is y = 0. One unit is one page width.
 *
 * A leaf is a skinned sheet with a chain of BONES bones along its width, all
 * hinged at the gutter. Its shape is described by one angle per bone segment,
 * measured in the x-y plane from +x (0 = lying flat on the right, PI = flat on
 * the left). `turnTargets` turns a progress value into those angles; the leaf
 * then springs each bone toward its target, softer toward the free edge, so
 * the paper lags, overshoots a hair and settles like a real sheet.
 */

export const PAGE_W = 1;
export const PAGE_H = 1.3;
/** Cover board overhang around the page block. */
export const COVER_OVERHANG = 0.045;
export const COVER_T = 0.026;
/** Thickness of the whole page block. */
export const BLOCK_T = 0.1;
/** Width of the region where the pages dip into the gutter. */
export const GUTTER_W = 0.2;
/** Hinge height above the cover's inside face when the book is open. */
export const HINGE_LIFT = 0.007;
/** Gap between a resting leaf and the block under it. */
export const LEAF_REST_GAP = 0.0025;

export const BONES = 30;
export const SEG = PAGE_W / BONES;

const clamp01 = (t: number) => (t < 0 ? 0 : t > 1 ? 1 : t);
export const smoothstep = (a: number, b: number, t: number) => {
  const x = clamp01((t - a) / (b - a));
  return x * x * (3 - 2 * x);
};
/** Gentle in-out: the page leaves slowly, swings through, lands softly. */
export const easeTurn = (t: number) => {
  const x = clamp01(t);
  return x < 0.5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2;
};

/**
 * Height of the page surface above the hinge, `x` from the spine, for a block
 * whose flat top is `depth` above the hinge. Steep at the gutter, flat beyond.
 */
export function gutterProfile(x: number, depth: number): number {
  if (depth <= 0) return 0;
  const u = clamp01(x / GUTTER_W);
  return depth * (1 - Math.pow(1 - u, 2.4));
}

/**
 * How much of a sheet at progress `p` counts toward each page block: it leaves
 * its stack as it lifts and joins the other as it lands.
 */
export const shareLeft = (p: number) => smoothstep(0.82, 1, p);
export const shareRight = (p: number) => 1 - smoothstep(0, 0.18, p);

/** Flat top (book space) of a page block holding `count` of `leaves` sheets. */
export const stackTop = (count: number, leaves: number) => COVER_T + (count * BLOCK_T) / leaves;

/** Rise of the gentle bow an open page makes across its width. */
export const BOW = 0.012;

/**
 * The resting page surface: it dives into the gutter, swells a little across
 * the page (pages held by a sewn spine never lie quite flat) and eases back
 * down to the block's height at the fore-edge. `bow` is 0 on a closed book.
 */
export function pageProfile(x: number, depth: number, bow = 0): number {
  const f = clamp01(x / PAGE_W);
  return gutterProfile(x, depth) + bow * Math.sin(Math.PI * Math.pow(f, 0.7)) * (1 - 0.35 * f);
}

/**
 * Segment angles (elevation, radians) of a sheet resting on a block whose top
 * is `depth` above the hinge, walking out from the hinge so each joint lands
 * exactly on the surface (`pageProfile`).
 */
export function restAngles(depth: number, out: Float32Array, bow = 0): Float32Array {
  let x = 0;
  let y = 0;
  for (let i = 0; i < out.length; i++) {
    if ((depth <= 1e-5 || x >= GUTTER_W) && bow <= 1e-5) {
      out[i] = 0;
      x += SEG;
      continue;
    }
    // Bisection on the elevation that puts the next joint on the surface.
    let lo = -0.4;
    let hi = 1.45;
    for (let k = 0; k < 18; k++) {
      const a = (lo + hi) / 2;
      const g = y + SEG * Math.sin(a) - pageProfile(x + SEG * Math.cos(a), depth, bow);
      if (g > 0) hi = a;
      else lo = a;
    }
    const a = (lo + hi) / 2;
    out[i] = a;
    x += SEG * Math.cos(a);
    y += SEG * Math.sin(a);
  }
  return out;
}

export interface TurnInput {
  /** 0 = resting on the right, 1 = resting on the left. */
  p: number;
  /** +1 turning forward (right to left), -1 back. Sets which edge leads. */
  dir: 1 | -1;
  /** How much the free edge leads the hinge, 0..1. */
  curl: number;
  /** Corner lift (the grabbed bottom corner rises first), 0..1. */
  corner: number;
}

/** How far along the turn each point of the sheet is, for a forward turn. */
function forwardT(u: number, p: number, curl: number): number {
  const base = easeTurn(p);
  // The free edge leads while it is being lifted, then trails as it lands.
  const lead = 1 - 1.7 * smoothstep(0.45, 1, p);
  const bend = curl * Math.sin(Math.PI * p) * Math.pow(u, 1.25) * lead;
  return clamp01(base + bend);
}

/**
 * Target segment angles for a turn. `right` and `left` are rest elevations on
 * each side (from `restAngles`). Writes angles from +x into `out`.
 */
export function turnTargets(
  input: TurnInput,
  right: Float32Array,
  left: Float32Array,
  out: Float32Array,
): Float32Array {
  const { p, dir, curl } = input;
  const n = out.length;
  for (let i = 0; i < n; i++) {
    const u = (i + 0.5) / n;
    const t = dir > 0 ? forwardT(u, p, curl) : 1 - forwardT(u, 1 - p, curl);
    const a = right[i];
    const b = Math.PI - left[i];
    out[i] = a + (b - a) * t;
  }
  return out;
}

/** Total twist (radians) about the sheet's head edge: lifts the bottom corner. */
export function turnTwist(input: TurnInput): number {
  const q = input.dir > 0 ? input.p : 1 - input.p;
  const env = q < 0.7 ? Math.sin((Math.PI * q) / 0.7) : 0;
  // A negative local x-rotation lifts +z while the sheet lies on the right and
  // keeps that corner leading (toward -x) through the turn; mirrored going back.
  return -input.dir * input.corner * 0.42 * env;
}

/**
 * Position of the sheet's free edge (x, y relative to the hinge) for a set of
 * segment angles. Used to make a dragged corner follow the pointer.
 */
export function tipOf(angles: Float32Array): [number, number] {
  let x = 0;
  let y = 0;
  for (let i = 0; i < angles.length; i++) {
    x += SEG * Math.cos(angles[i]);
    y += SEG * Math.sin(angles[i]);
  }
  return [x, y];
}

/**
 * How far along a turn (0 where it starts, 1 turned) the free edge crosses
 * the spine, for the given direction, curl and rest shapes.
 */
export function spineCrossing(dir: 1 | -1, curl: number, right: Float32Array, left: Float32Array): number {
  const out = new Float32Array(right.length);
  const input: TurnInput = { p: 0, dir, curl, corner: 0 };
  let lo = 0;
  let hi = 1;
  for (let k = 0; k < 20; k++) {
    const along = (lo + hi) / 2;
    input.p = dir > 0 ? along : 1 - along;
    turnTargets(input, right, left, out);
    // Still on the side it started from?
    if (tipOf(out)[0] * dir > 0) lo = along;
    else hi = along;
  }
  return (lo + hi) / 2;
}

/** How far ahead (seconds) a released page's motion is projected. */
export const FLICK_LOOKAHEAD = 0.2;

/**
 * Whether a released drag completes the turn. `along` is progress along the
 * turn (0 where it started, 1 turned), `speed` its rate (per second, positive
 * toward turning), `crossing` the progress at which the free edge passes the
 * spine (`spineCrossing`). The page keeps going if, carried on by its own
 * motion for a moment, its edge would be past the spine; a quick flick from
 * a page lifted only a little also completes it.
 */
export function releaseCompletes(along: number, speed: number, crossing: number): boolean {
  if (speed > 1.1 && along > 0.08) return true;
  return along + speed * FLICK_LOOKAHEAD >= crossing;
}
