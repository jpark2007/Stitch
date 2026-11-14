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
 * Pure (no imports), so the rule is unit-tested in stacking.test.ts.
 */

export interface StackSheet {
  /** Commanded progress: 0 resting right, 1 resting left. */
  p: number;
  /** "rest" means lying on a stack; anything else is in motion. */
  mode: string;
}

export interface SheetLayer {
  visible: boolean;
  /** Drawn over any sheet it coincides with. */
  front: boolean;
}

/**
 * Visibility and depth priority for each sheet. `warm` draws every sheet once
 * (so each texture reaches the GPU before it is first turned to).
 */
export function stackLayers(sheets: readonly StackSheet[], warm = false): SheetLayer[] {
  let leftTop = -1;
  let rightTop = -1;
  sheets.forEach((s, i) => {
    if (s.mode === "rest" && s.p >= 1) leftTop = i;
    if (s.mode === "rest" && s.p <= 0 && rightTop < 0) rightTop = i;
  });
  return sheets.map((s, i) => {
    const moving = s.mode !== "rest";
    return { visible: warm || moving || i === leftTop || i === rightTop, front: moving };
  });
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
