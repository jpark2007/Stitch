/**
 * Which sheets are drawn, and which must win a depth tie.
 *
 * Every sheet resting on a side shares that side's rest shape, so only the top
 * of each stack is drawn. A sheet in motion (turning, held, peeled, frozen
 * mid-turn) is drawn too, and at either end of its travel its target is the
 * very rest shape of the stack it is leaving or landing on: it lies exactly on
 * that stack's top sheet. Such a sheet is marked `front`, and the leaf draws it
 * with a small constant depth bias, so the page being turned always shows over
 * the stale one while content standing on it still shows over it.
 *
 * The bias only breaks exact ties. A landing sheet does worse than tie: as
 * the stack thickens under it, its gutter bones come to rest against the
 * stack (clamped to the very same rest shape), and the skinned surface
 * between them, still bending and twisting toward rest, dips a hair below the
 * stack top for a few frames, so the stale page showed through near the
 * gutter. So once a sheet coming down on a stack
 * is within `LANDING_REACH` of it, that stack's top is not drawn at all: the
 * landing sheet already covers it, bar a sliver under its trailing fore-edge
 * where the page block's blank top shows instead (as it does anyway in the
 * frames after the sheet lands while its edge settles).
 *
 * Pure (no imports), so the rule is unit-tested in stacking.test.ts.
 */

export interface StackSheet {
  /** Commanded progress: 0 resting right, 1 resting left. */
  p: number;
  /** "rest" means lying on a stack; anything else is in motion. */
  mode: string;
  /** Turn direction: +1 forward (right to left), -1 back. */
  dir: number;
  /** Where an "auto" turn is headed (0 or 1). */
  to: number;
}

export interface SheetLayer {
  visible: boolean;
  /** Drawn over any sheet it coincides with. */
  front: boolean;
}

/**
 * How near (in progress) a landing sheet comes to its stack before that
 * stack's top is hidden. Run through the leaf's real springs, a landing sheet
 * first dips below the stack top 0.13 out (clicked, queued, calm, released or
 * held turns, either way, 4 to 14 leaves, 20 to 240 fps; landing.test.ts);
 * this leaves a margin, while the landing sheet's fore-edge still falls short
 * of the stack's by little more than 1% of a page.
 */
export const LANDING_REACH = 0.2;

/**
 * The stack (1 left, 0 right) a sheet is coming down on and is within
 * `LANDING_REACH` of, or -1. A clicked or released sheet lands where its clock
 * is headed; a held sheet lands on the far side from where it was picked up.
 * Peels and frozen poses never land, and a sheet lifting off is not landing.
 */
export function landingOn(s: StackSheet): -1 | 0 | 1 {
  let side: 0 | 1;
  if (s.mode === "auto") side = s.to >= 0.5 ? 1 : 0;
  else if (s.mode === "drag") side = s.dir > 0 ? 1 : 0;
  else return -1;
  return Math.abs(s.p - side) <= LANDING_REACH ? side : -1;
}

/**
 * Sets `visible` and `front` on each sheet, in place (it runs every frame).
 * `warm` draws every sheet once, so each texture reaches the GPU before it is
 * first turned to.
 */
export function layStacks(sheets: readonly (StackSheet & SheetLayer)[], warm = false): void {
  let leftTop = -1;
  let rightTop = -1;
  let landsLeft = false;
  let landsRight = false;
  for (let i = 0; i < sheets.length; i++) {
    const s = sheets[i];
    if (s.mode === "rest") {
      if (s.p >= 1) leftTop = i;
      else if (s.p <= 0 && rightTop < 0) rightTop = i;
    } else {
      const side = landingOn(s);
      if (side === 1) landsLeft = true;
      else if (side === 0) landsRight = true;
    }
  }
  if (landsLeft) leftTop = -1;
  if (landsRight) rightTop = -1;
  for (let i = 0; i < sheets.length; i++) {
    const s = sheets[i];
    const moving = s.mode !== "rest";
    s.visible = warm || moving || i === leftTop || i === rightTop;
    s.front = moving;
  }
}

/**
 * Depth bias for a `front` sheet (WebGL polygon offset; negative is nearer).
 * Units only: the tie with a coincident sheet is a float hair wide, and a few
 * units break it. A slope term (`factor`) grows along the curved sheet's
 * oblique bands until it reaches past content lying just above the page (a
 * stage slot's tint sits 0.0008 up), cutting that content away in bands.
 */
export const FRONT_OFFSET = { factor: 0, units: -4 } as const;

/**
 * How far toward the camera (world units) the `front` bias reaches on a
 * surface `distance` from a camera with these planes (24-bit depth buffer).
 */
export function frontBiasReach(distance: number, near: number, far: number): number {
  // Window depth is far/(far-near) * (1 - near/d), so dz/dd = far*near / ((far-near) d^2).
  const step = 2 ** -24;
  return (Math.abs(FRONT_OFFSET.units) * step * (far - near) * distance * distance) / (far * near);
}
