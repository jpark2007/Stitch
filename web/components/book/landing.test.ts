/**
 * Landing regression: runs whole page turns through the leaf's real springs
 * and the block's real thickening, poses each sheet's skinned surface the way
 * its bones do, and checks each frame: the landing sheet stays out of the
 * stack it comes down on; wherever it dips below that stack's top, the top
 * is not drawn; and the top is hidden only once the landing sheet's free edge
 * is all but down, so the page under it never flashes blank.
 *
 *   cd web && node --no-warnings --test "components/book/*.test.ts"
 *
 * The bugs: the landing sheet's skin dipped a hair under the stack top for a
 * few frames, so the previous page's words and photo showed through; hiding
 * that stack top on the command alone then blanked it while a quickly swept
 * page was still standing upright, its springs trailing the hand.
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
/** Float32 rounding of a sheet's bone angles: well inside what the front bias resolves. */
const TIE = 2e-6;
/**
 * How far off the stack a landing sheet's free edge may be while the stack
 * top under it is hidden: the page's outer margin (pageArt.ts: words and
 * photos start 96 px of 1000 in), so the strip of page block left showing
 * never stands in for anything printed.
 */
const EDGE_HIDDEN = 0.096;
/** Frame lengths: fast and slow screens. */
const FRAMES = [1 / 144, 1 / 60, 1 / 30];

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
  gapL: number;
  gapR: number;
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
 * How far the free edge of `top` is from that of `under`, at its head and
 * tail corners (the skinned surface, not the springs' own estimate).
 */
function edgeDistance(top: [number, number][][], under: [number, number][][]): number {
  let d = 0;
  for (const r of [0, ROWS.length - 1]) {
    const [x, y] = top[r][X_SEGS];
    const [ux, uy] = under[r][X_SEGS];
    d = Math.max(d, Math.hypot(x - ux, y - uy));
  }
  return d;
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
    return { p, mode: "rest", dir: 1, to: p, curl: 0.42, corner: 0.5, stiff: 1, visible: true, front: false, gapL: Infinity, gapR: Infinity };
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
  // Leaf.tsx: each drawn sheet notes where it was posed; a hidden one is nowhere.
  const pose = (i: number) => {
    const sh = sheets[i];
    sh.gapL = sh.visible ? springs[i].edgeGap(restL, true) : Infinity;
    sh.gapR = sh.visible ? springs[i].edgeGap(restR, false) : Infinity;
  };
  shapeStacks();
  sheets.forEach((_, i) => (aim(i), springs[i].snap(target, twTarget), pose(i)));

  const frames: { along: number; gap: number; staleDrawn: boolean; edge: number; posed: number; landing: boolean }[] = [];
  const end = turn.dur + 0.8;
  for (let t = frame; t <= end; t += frame) {
    const q = Math.min(1, t / turn.dur);
    const leaf = sheets[k];
    leaf.p = turn.from + (turn.to - turn.from) * q;
    if (q >= 1 && turn.mode === "auto") leaf.mode = "rest";
    // The stacks are laid from the poses of the frame before (the order of
    // Book3D's and the leaves' frame callbacks), then the sheets are posed.
    shapeStacks();
    s.layStacks(sheets);
    sheets.forEach((sh, i) => {
      aim(i);
      springs[i].step(target, twTarget, sh.stiff, frame, restR, restL);
      pose(i);
    });
    const top = surface(springs[k].phi, springs[k].tw);
    const under = surface(springs[stale].phi, springs[stale].tw);
    frames.push({
      along: turn.to === 1 ? leaf.p : 1 - leaf.p,
      gap: lowestGap(top, under),
      staleDrawn: sheets[stale].visible,
      edge: edgeDistance(top, under),
      posed: turn.to === 1 ? leaf.gapL : leaf.gapR,
      landing: leaf.mode !== "rest",
    });
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

/** controller.ts: a held page swept to the far edge in `dur` seconds (and held there). */
const fling = (dir: 1 | -1, corner: number, dur = 0.25): Turn => ({
  mode: "drag",
  dir,
  from: dir > 0 ? 0 : 1,
  to: dir > 0 ? 1 : 0,
  dur,
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
  ["forward drag flung across (80 ms)", fling(1, 0.45, 0.08)],
  ["forward drag flung across (150 ms)", fling(1, 0.45, 0.15)],
  ["forward drag flung by the bottom corner (80 ms)", fling(1, 1, 0.08)],
  ["back drag flung across (80 ms)", fling(-1, 0.45, 0.08)],
  ["back drag flung across (150 ms)", fling(-1, 0.45, 0.15)],
];

/** Every turn at 4 and 9 leaves and every frame length. */
function* runs(turn: Turn) {
  for (const leaves of [4, 9]) {
    for (const frame of FRAMES) {
      const at = `${leaves} leaves, ${Math.round(1 / frame)} fps`;
      for (const f of simulate(turn, leaves, frame)) yield { at, ...f };
    }
  }
}

describe("landing sheet versus the stack top under it", () => {
  for (const [name, turn] of TURNS) {
    it(`keeps a ${name} out of the stack it lands on (regression)`, () => {
      for (const f of runs(turn)) {
        assert.ok(f.gap >= -TIE, `${f.at}: ${f.gap.toExponential(1)} below the stack top ${f.along.toFixed(3)} along`);
      }
    });

    it(`never shows the stale page through a ${name} (regression)`, () => {
      for (const f of runs(turn)) {
        if (f.gap >= DIP) continue;
        assert.equal(f.staleDrawn, false, `${f.at}: dips ${f.gap.toExponential(1)} below the drawn stack top ${f.along.toFixed(3)} along`);
      }
    });

    it(`never blanks the page under a ${name} before covering it (regression)`, () => {
      for (const f of runs(turn)) {
        if (f.staleDrawn || !f.landing) continue;
        assert.ok(
          f.edge <= EDGE_HIDDEN,
          `${f.at}: stack top hidden with the landing edge ${f.edge.toFixed(3)} off it, ${f.along.toFixed(3)} along`,
        );
      }
    });

    it(`measures where a ${name} really is`, () => {
      // The leaf's own estimate of its edge (what the stack is laid by) against its skin.
      for (const f of runs(turn)) {
        assert.ok(Math.abs(f.posed - f.edge) < 0.005, `${f.at}: estimated ${f.posed.toFixed(4)}, skinned ${f.edge.toFixed(4)}`);
      }
    });
  }
});

describe("leaf springs against a stack", () => {
  /** A left stack `depth` above the hinge, and a sheet lying on it. */
  const onLeft = (depth: number) => {
    const left = m.restAngles(depth, new Float32Array(BONES), BOW);
    const right = m.restAngles(0.04, new Float32Array(BONES), BOW);
    const target = m.turnTargets({ p: 1, dir: 1, curl: 0, corner: 0 }, right, left, new Float32Array(BONES));
    return { left, right, target };
  };
  const still = new Float32Array(BONES);

  // Regression: a page landing fast thickens the stack under the stack's top
  // sheet in a frame or two. Clamped up onto it, the top sheet kept the
  // spring's speed away from it and bounced up off its block, and the landing
  // page, lying on the block, showed the stale one through it.
  it("rides a stack that thickens under it, without bouncing off (regression)", () => {
    const thin = onLeft(0.02);
    const thick = onLeft(0.045);
    const springs = new sp.LeafSprings(BONES, SEG, PAGE_H);
    springs.snap(thin.target, still);
    // Where the thicker stack lies lower (out along the bow) the sheet springs
    // down after it, a few 1e-4 rad behind; it bounced up 0.03 rad.
    for (let f = 0; f < 12; f++) {
      springs.step(thick.target, still, 1, 1 / 60, thick.right, thick.left);
      springs.phi.forEach((v, i) => {
        const lift = thick.target[i] - v;
        assert.ok(lift < 3e-4, `frame ${f}, bone ${i} lifted ${lift.toExponential(1)} off the stack`);
      });
    }
  });

  // Regression: where a bone lying on the stack met the next one bent up off
  // it, the skin's blend between them cut the corner below the stack top.
  it("lifts off a stack without its skin cutting into it (regression)", () => {
    const { left, right, target } = onLeft(0.03);
    for (const bend of [0.05, 0.3, 1.2]) {
      for (const from of [0, 4, 12]) {
        const kinked = target.map((v, i) => (i >= from ? v - bend : v));
        const springs = new sp.LeafSprings(BONES, SEG, PAGE_H);
        springs.snap(kinked, still);
        springs.step(kinked, still, 1, 1 / 240, right, left);
        const gap = lowestGap(surface(springs.phi, springs.tw), surface(target, still));
        assert.ok(gap >= DIP, `bent ${bend} from bone ${from}: skin ${gap.toExponential(1)} below the stack`);
      }
    }
  });
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

  it("keeps the corner's lead on a sheet well clear of the stacks", () => {
    // Past upright, a forward turn's corner twist lowers the tail toward the
    // left stack: it leads the sheet down, and is held only once it is near.
    for (const p of [0.45, 0.55, 0.65]) {
      const sheet = { p, dir: 1 as const, curl: 0.42, corner: 1 };
      const springs = new sp.LeafSprings(BONES, SEG, PAGE_H);
      const target = m.turnTargets(sheet, right, left, new Float32Array(BONES));
      const tw = sp.twistTargets(m.turnTwist(sheet), new Float32Array(BONES));
      springs.snap(target, tw);
      springs.step(target, tw, 1, 1 / 60, right, left);
      assert.ok(Math.abs(summed(springs.tw) - summed(tw)) < 1e-6, `${p} along: twist ${summed(springs.tw)} of ${summed(tw)}`);
    }
  });
});
