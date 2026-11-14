import { BONES, restAngles } from "./leafShape";

/**
 * Per-frame book state shared by the controller (in Book3D) and the leaves.
 * It is a plain mutable object, read and written inside useFrame, never React
 * state, so a page turn never re-renders React.
 */

export type LeafMode =
  /** Lying on a stack (p is 0 or 1). */
  | "rest"
  /** Clicked: p runs on a clock from `from` to `to`. */
  | "auto"
  /** Held: p follows the pointer. */
  | "drag"
  /** Corner peel under the pointer. */
  | "hover"
  /** Deterministic screenshot pose: no springs, no clock. */
  | "frozen";

export interface LeafState {
  /** Commanded progress: 0 resting right, 1 resting left. */
  p: number;
  dir: 1 | -1;
  curl: number;
  corner: number;
  mode: LeafMode;
  visible: boolean;
  /** "auto" clock. */
  from: number;
  to: number;
  t0: number;
  dur: number;
  /** Drag springs are stiffer, so the sheet keeps up with the hand. */
  stiff: number;
}

export interface BookRuntime {
  leaves: LeafState[];
  /** Hinge height (book space). */
  hinge: number;
  /** Flat top of each block above the hinge. */
  depthL: number;
  depthR: number;
  restL: Float32Array;
  restR: Float32Array;
  /** Bumped whenever the rest shapes change, so settled leaves re-pose. */
  restVersion: number;
  /** Total frames rendered (read by the FPS probe). */
  frames: number;
}

export function createLeafState(p: number): LeafState {
  return {
    p,
    dir: 1,
    curl: 0.42,
    corner: 0.5,
    mode: "rest",
    visible: true,
    from: p,
    to: p,
    t0: 0,
    dur: 1,
    stiff: 1,
  };
}

export function createRuntime(leafCount: number, leftCount: number): BookRuntime {
  return {
    leaves: Array.from({ length: leafCount }, (_, i) => createLeafState(i < leftCount ? 1 : 0)),
    hinge: 0,
    depthL: 0,
    depthR: 0,
    restL: new Float32Array(BONES),
    restR: new Float32Array(BONES),
    restVersion: 0,
    frames: 0,
  };
}

/** Update block depths; recomputes rest shapes only when they really change. */
export function setDepths(rt: BookRuntime, depthL: number, depthR: number) {
  const q = (v: number) => Math.round(v * 20000) / 20000;
  const l = q(depthL);
  const r = q(depthR);
  if (l === rt.depthL && r === rt.depthR) return;
  if (l !== rt.depthL) restAngles(l, rt.restL);
  if (r !== rt.depthR) restAngles(r, rt.restR);
  rt.depthL = l;
  rt.depthR = r;
  rt.restVersion++;
}
