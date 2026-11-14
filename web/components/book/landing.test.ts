/**
 * Landing regression: runs whole page turns through the leaf's real springs
 * and the block's real thickening, poses each sheet's skinned surface the way
 * its bones do, and checks that in every frame where the landing sheet dips
 * below the stack top it is coming down on, that stack top is not drawn.
 *
 *   cd web && node --no-warnings --test "components/book/*.test.ts"
 *
 * The bug: once the stack rises into a landing sheet's gutter bones they rest
 * exactly on it (clamped to the same rest shape), and the skinned surface,
 * still bending and twisting toward rest, dipped a hair under the stack top
 * for a few frames, so the previous page's words and photo showed through.
 *
 * Modules are imported by computed paths so tsc does not ask for `.ts`
 * import extensions (see leafShape.test.ts).
 */

import assert from "node:assert/strict";
import { describe, it } from "node:test";

type Stacking = typeof import("./stacking");
type Shape = typeof import("./leafShape");
type Springs = typeof import("./leafSprings");
type Three = typeof import("three");
const paths = ["./stacking.ts", "./leafShape.ts", "./leafSprings.ts", "three"];
const s: Stacking = await import(paths[0]);
const m: Shape = await import(paths[1]);
const sp: Springs = await import(paths[2]);
const T: Three = await import(paths[3]);

const { BONES, SEG, PAGE_W, PAGE_H, BOW, COVER_T, HINGE_LIFT, LEAF_REST_GAP } = m;
/** The leaf mesh's columns (two per bone) and rows (Leaf.tsx). */
const X_SEGS = BONES * 2;
const ROWS = [0, PAGE_H / 2, PAGE_H];
/** Below this, the landing sheet is under the stack top (an exact tie is 0). */
const DIP = -1e-7;

interface Turn {
  mode: "auto" | "drag";
  dir: 1 | -1;
  from: number;
  to: 0 | 1;
  /** Clock length (auto) or how long the hand takes to get there (drag). */
  dur: number;
  curl: number;
  corner: number;
  stiff: number;
}

interface SimSheet {
  p: number;
  mode: string;
  dir: 1 | -1;
  to: number;
  curl: number;
  corner: number;
  stiff: number;
  visible: boolean;
  front: boolean;
}

/** World matrices of a sheet's bones (BONES + the tip bone), as Leaf.tsx poses them. */
function boneWorlds(phi: Float32Array, tw: Float32Array, out: InstanceType<Three["Matrix4"]>[]) {
  const e = new T.Euler(0, 0, 0, "ZXY");
  const local = new T.Matrix4();
  let prev = 0;
  for (let i = 0; i <= BONES; i++) {
    out[i].identity();
    if (i > 0) out[i].copy(out[i - 1]).multiply(local.makeTranslation(SEG, 0, 0));
    if (i < BONES) {
      e.set(tw[i], 0, phi[i] - prev);
      prev = phi[i];
      out[i].multiply(local.makeRotationFromEuler(e));
    }
  }
}

/** Skinned positions of the sheet's mid-surface: [row][column] = [x, y]. */
function surface(phi: Float32Array, tw: Float32Array): [number, number][][] {
  const worlds = Array.from({ length: BONES + 1 }, () => new T.Matrix4());
  boneWorlds(phi, tw, worlds);
  const a = new T.Vector3();
  const b = new T.Vector3();
  return ROWS.map((z) => {
    const row: [number, number][] = [];
    for (let ix = 0; ix <= X_SEGS; ix++) {
      // Leaf.tsx's skin weights: bone `k` and `k + 1`, blended by `f`.
      const x = (ix / X_SEGS) * PAGE_W;
      const xf = x / SEG;
      const k = Math.min(Math.floor(xf), BONES - 1);
      const f = Math.min(1, xf - k);
      a.set(x - k * SEG, 0, z).applyMatrix4(worlds[k]);
      b.set(x - (k + 1) * SEG, 0, z).applyMatrix4(worlds[k + 1]);
      a.multiplyScalar(1 - f).addScaledVector(b, f);
      row.push([a.x, a.y]);
    }
    return row;
  });
}

/** Height of a surface row at `x` (NaN outside it). */
function heightAt(row: [number, number][], x: number): number {
  for (let i = 1; i < row.length; i++) {
    const [x0, y0] = row[i - 1];
    const [x1, y1] = row[i];
    if ((x0 - x) * (x1 - x) <= 0 && x0 !== x1) return y0 + ((y1 - y0) * (x - x0)) / (x1 - x0);
  }
  return NaN;
}

/** Most negative height of `top` over `under`, row by row (0 when they tie). */
function lowestGap(top: [number, number][][], under: [number, number][][]): number {
  let gap = Infinity;
  top.forEach((row, r) => {
    for (const [x, y] of row) {
      const h = heightAt(under[r], x);
      if (!Number.isNaN(h)) gap = Math.min(gap, y - h);
    }
  });
  return gap;
}

/**
 * Runs leaf 1 of a `leaves`-sheet book through `turn` at `frame` seconds per
 * frame, then on until it settles. Returns, per frame, how far the landing
 * sheet sits over the stack top it comes down on, and whether that top is drawn.
 */
function simulate(turn: Turn, leaves: number, frame: number) {
  // Forward from spread 1 (one sheet on the left); back from spread 2.
  const leftCount = turn.dir > 0 ? 1 : 2;
  const sheets: SimSheet[] = Array.from({ length: leaves }, (_, i) => {
    const p = i < leftCount ? 1 : 0;
    return { p, mode: "rest", dir: 1, to: p, curl: 0.42, corner: 0.5, stiff: 1, visible: true, front: false };
  });
  const k = 1;
  const stale = turn.to === 1 ? 0 : 2;
  const restL = new Float32Array(BONES);
  const restR = new Float32Array(BONES);
  const hinge = COVER_T + HINGE_LIFT;
  const shapeStacks = () => {
    let l = 0;
    let r = 0;
    for (const sh of sheets) {
      l += m.shareLeft(sh.p);
      r += m.shareRight(sh.p);
    }
    m.restAngles(Math.max(0, m.stackTop(l, leaves) + LEAF_REST_GAP - hinge), restL, BOW);
    m.restAngles(Math.max(0, m.stackTop(r, leaves) + LEAF_REST_GAP - hinge), restR, BOW);
  };
  const springs = sheets.map(() => new sp.LeafSprings(BONES, SEG, PAGE_H));
  const target = new Float32Array(BONES);
  const twTarget = new Float32Array(BONES);
  const aim = (i: number) => {
    m.turnTargets(sheets[i], restR, restL, target);
    sp.twistTargets(m.turnTwist(sheets[i]), twTarget);
  };

  Object.assign(sheets[k], { mode: turn.mode, dir: turn.dir, p: turn.from, to: turn.to, curl: turn.curl, corner: turn.corner, stiff: turn.stiff });
  shapeStacks();
  sheets.forEach((_, i) => (aim(i), springs[i].snap(target, twTarget)));

  const frames: { along: number; gap: number; staleDrawn: boolean }[] = [];
  const end = turn.dur + 0.8;
  for (let t = frame; t <= end; t += frame) {
    const q = Math.min(1, t / turn.dur);
    const leaf = sheets[k];
    leaf.p = turn.from + (turn.to - turn.from) * q;
    if (q >= 1 && turn.mode === "auto") leaf.mode = "rest";
    shapeStacks();
    s.layStacks(sheets);
    sheets.forEach((sh, i) => {
      aim(i);
      springs[i].step(target, twTarget, sh.stiff, frame, restR, restL);
    });
    const gap = lowestGap(surface(springs[k].phi, springs[k].tw), surface(springs[stale].phi, springs[stale].tw));
    frames.push({ along: turn.to === 1 ? leaf.p : 1 - leaf.p, gap, staleDrawn: sheets[stale].visible });
  }
  return frames;
}

const click = (dir: 1 | -1, dur: number, calm = false): Turn => ({
  mode: "auto",
  dir,
  from: dir > 0 ? 0 : 1,
  to: dir > 0 ? 1 : 0,
  dur,
  curl: calm ? 0.25 : 0.42,
  corner: calm ? 0.3 : 0.55,
  stiff: 1,
});

/** controller.ts: a released drag finishes on the clock, stiffer. */
const release = (from: number, to: 0 | 1): Turn => ({
  mode: "auto",
  dir: 1,
  from,
  to,
  dur: 0.3 + 0.75 * Math.abs(to - from),
  curl: 0.5,
  corner: 0.45,
  stiff: 1.15,
});

/** controller.ts: a held page swept to the far edge (and held there). */
const fling = (dir: 1 | -1, corner: number): Turn => ({
  mode: "drag",
  dir,
  from: dir > 0 ? 0 : 1,
  to: dir > 0 ? 1 : 0,
  dur: 0.25,
  curl: 0.5,
  corner,
  stiff: 1.7,
});

const TURNS: [string, Turn][] = [
  ["forward click", click(1, 1.15)],
  ["forward queued (fast)", click(1, 0.78)],
  ["forward, reduced motion", click(1, 0.575, true)],
  ["back click", click(-1, 1.15)],
  ["back queued (fast)", click(-1, 0.78)],
  ["back, reduced motion", click(-1, 0.575, true)],
  ["released drag that completes", release(0.5, 1)],
  ["released drag that springs back", release(0.3, 0)],
  ["forward drag swept to the edge", fling(1, 0.45)],
  ["forward drag by the bottom corner", fling(1, 1)],
  ["back drag swept to the edge", fling(-1, 0.45)],
];

describe("landing sheet versus the stack top under it", () => {
  it("dips below the stack top in a plain forward turn (what the fix guards)", () => {
    const frames = simulate(click(1, 1.15), 4, 1 / 60);
    const dips = frames.filter((f) => f.gap < DIP);
    assert.ok(dips.length > 0, "the simulation reproduces the dip; if tuning removed it, revisit LANDING_REACH");
    // It happens late in the turn, inside the reach that hides the stack top.
    for (const d of dips) assert.ok(d.along > 1 - s.LANDING_REACH, `dip at ${d.along.toFixed(3)} along`);
  });

  for (const [name, turn] of TURNS) {
    it(`never shows the stale page through a ${name} (regression)`, () => {
      for (const leaves of [4, 9]) {
        for (const frame of [1 / 144, 1 / 60, 1 / 30]) {
          for (const f of simulate(turn, leaves, frame)) {
            if (f.gap >= DIP) continue;
            assert.equal(
              f.staleDrawn,
              false,
              `${leaves} leaves, ${Math.round(1 / frame)} fps: dips ${f.gap.toExponential(1)} below the drawn stack top ${f.along.toFixed(3)} along`,
            );
          }
        }
      }
    });
  }
});

describe("leaf springs' twist against a stack", () => {
  const right = m.restAngles(0.05, new Float32Array(BONES), BOW);
  const left = m.restAngles(0.03, new Float32Array(BONES), BOW);
  const lying = (side: 0 | 1) => {
    const springs = new sp.LeafSprings(BONES, SEG, PAGE_H);
    const target = m.turnTargets({ p: side, dir: 1, curl: 0, corner: 0 }, right, left, new Float32Array(BONES));
    return { springs, target };
  };
  const summed = (tw: Float32Array) => tw.reduce((a, b) => a + b, 0);

  for (const [side, pressing] of [
    [1, -0.004],
    [0, 0.004],
  ] as const) {
    const where = side ? "left" : "right";
    it(`never presses a sheet lying on the ${where} stack into it`, () => {
      const { springs, target } = lying(side);
      // A twist left over from the corner lift, of the sign that would push the tail down.
      const tw = sp.twistTargets(pressing, new Float32Array(BONES));
      springs.snap(target, tw);
      springs.step(target, tw, 1, 1 / 60, right, left);
      // Float32 storage rounds the held twist by a few 1e-9 rad.
      let c = 0;
      for (const v of springs.tw) {
        c += v;
        assert.ok(side ? c >= -1e-7 : c <= 1e-7, `twist ${c} presses into the ${where} stack`);
      }
    });

    it(`leaves a sheet free to lift off the ${where} stack`, () => {
      const { springs, target } = lying(side);
      const tw = sp.twistTargets(-pressing, new Float32Array(BONES));
      springs.snap(target, tw);
      springs.step(target, tw, 1, 1 / 60, right, left);
      assert.ok(Math.abs(summed(springs.tw) + pressing) < 1e-6, "the lifting twist is kept");
    });
  }
});
