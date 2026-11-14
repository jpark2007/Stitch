/**
 * Unit tests for the pure page maths. No test runner needed:
 *
 *   cd web && node --no-warnings --test "components/book/*.test.ts"
 *
 * Node runs the TypeScript directly (type stripping); the module is imported
 * by a computed path so tsc does not ask for a `.ts` import extension.
 */

import assert from "node:assert/strict";
import { describe, it } from "node:test";

type Shape = typeof import("./leafShape");
const modulePath = "./leafShape.ts";
const m: Shape = await import(modulePath);
const { BONES, SEG } = m;

/** Rest shapes for a block whose top sits `depth` above the hinge. */
function rest(depth: number) {
  return m.restAngles(depth, new Float32Array(BONES));
}

/** Joint positions (x, y) walking out from the hinge. */
function joints(angles: Float32Array) {
  const pts: [number, number][] = [[0, 0]];
  let x = 0;
  let y = 0;
  for (const a of angles) {
    x += SEG * Math.cos(a);
    y += SEG * Math.sin(a);
    pts.push([x, y]);
  }
  return pts;
}

function tipAt(p: number, dir: 1 | -1, curl: number, depth = 0.05) {
  const out = new Float32Array(BONES);
  m.turnTargets({ p, dir, curl, corner: 0.5 }, rest(depth), rest(depth), out);
  return m.tipOf(out);
}

describe("resting page profile", () => {
  it("dives into the gutter and is flat beyond it", () => {
    const d = 0.05;
    assert.equal(m.gutterProfile(0, d), 0);
    assert.ok(m.gutterProfile(m.GUTTER_W * 0.25, d) > d * 0.4, "climbs steeply out of the gutter");
    assert.ok(Math.abs(m.gutterProfile(m.GUTTER_W, d) - d) < 1e-9);
    assert.ok(Math.abs(m.gutterProfile(0.8, d) - d) < 1e-9);
  });

  it("is flat when the block has no depth (closed book)", () => {
    for (const x of [0, 0.1, 0.5, 1]) assert.equal(m.gutterProfile(x, 0), 0);
    assert.ok(rest(0).every((a) => a === 0));
  });

  it("lays every joint of a resting sheet on the profile", () => {
    for (const [d, bow] of [[0.06, 0], [0.06, m.BOW], [0.004, m.BOW]]) {
      for (const [x, y] of joints(m.restAngles(d, new Float32Array(BONES), bow))) {
        const off = y - m.pageProfile(x, d, bow);
        assert.ok(Math.abs(off) < 2e-4, `depth ${d} bow ${bow}: joint at x=${x.toFixed(3)} is ${off.toFixed(5)} off`);
      }
    }
  });

  it("bows gently across an open page and settles at the fore-edge", () => {
    const d = 0.05;
    const mid = m.pageProfile(0.45, d, m.BOW) - d;
    assert.ok(mid > m.BOW * 0.7 && mid <= m.BOW, `swell ${mid}`);
    assert.ok(Math.abs(m.pageProfile(m.PAGE_W, d, m.BOW) - d) < 1e-9, "back to the block at the edge");
    assert.equal(m.pageProfile(0, d, m.BOW), 0, "still dives into the gutter");
  });
});

describe("turn shape", () => {
  it("matches the rest shapes at both ends of a turn", () => {
    const right = rest(0.07);
    const left = rest(0.03);
    const out = new Float32Array(BONES);
    for (const dir of [1, -1] as const) {
      m.turnTargets({ p: 0, dir, curl: 0.5, corner: 0.5 }, right, left, out);
      out.forEach((a, i) => assert.ok(Math.abs(a - right[i]) < 1e-6));
      m.turnTargets({ p: 1, dir, curl: 0.5, corner: 0.5 }, right, left, out);
      out.forEach((a, i) => assert.ok(Math.abs(a - (Math.PI - left[i])) < 1e-6));
    }
  });

  it("lets the free edge lead the hinge early in a forward turn", () => {
    const out = new Float32Array(BONES);
    const flat = new Float32Array(BONES);
    m.turnTargets({ p: 0.25, dir: 1, curl: 0.5, corner: 0.5 }, flat, flat, out);
    assert.ok(out[BONES - 1] > out[0] + 0.2, "the edge is further over than the hinge");
    // More curl, more lead.
    const less = m.turnTargets({ p: 0.25, dir: 1, curl: 0.2, corner: 0.5 }, flat, flat, new Float32Array(BONES));
    assert.ok(out[BONES - 1] > less[BONES - 1]);
  });

  it("mirrors a backward turn", () => {
    for (const p of [0.2, 0.5, 0.8]) {
      const [xf, yf] = tipAt(p, 1, 0.5, 0);
      const [xb, yb] = tipAt(1 - p, -1, 0.5, 0);
      assert.ok(Math.abs(xf + xb) < 1e-6 && Math.abs(yf - yb) < 1e-6, `p=${p}`);
    }
  });

  it("only lifts the corner (twist) mid-turn, and leads with it in both directions", () => {
    assert.equal(Math.abs(m.turnTwist({ p: 0, dir: 1, curl: 0.5, corner: 1 })), 0);
    assert.equal(Math.abs(m.turnTwist({ p: 1, dir: 1, curl: 0.5, corner: 1 })), 0);
    const f = m.turnTwist({ p: 0.3, dir: 1, curl: 0.5, corner: 1 });
    const b = m.turnTwist({ p: 0.7, dir: -1, curl: 0.5, corner: 1 });
    assert.ok(f < 0 && b > 0 && Math.abs(f + b) < 1e-9);
  });
});

describe("drag release", () => {
  const right = rest(0.05);
  const left = rest(0.05);

  it("finds where the free edge crosses the spine", () => {
    for (const dir of [1, -1] as const) {
      const c = m.spineCrossing(dir, 0.5, right, left);
      // The curl carries the edge over before the hinge is half way.
      assert.ok(c > 0.3 && c < 0.5, `crossing ${c}`);
      const pBefore = dir > 0 ? c - 0.01 : 1 - (c - 0.01);
      const pAfter = dir > 0 ? c + 0.01 : 1 - (c + 0.01);
      assert.ok(tipAt(pBefore, dir, 0.5)[0] * dir > 0, "still on its own side just before");
      assert.ok(tipAt(pAfter, dir, 0.5)[0] * dir < 0, "past the spine just after");
    }
  });

  it("completes a turn released with the corner just past the spine (regression)", () => {
    for (const dir of [1, -1] as const) {
      const c = m.spineCrossing(dir, 0.5, right, left);
      assert.ok(c < 0.5, "a plain 'past half way' rule would spring this page back");
      assert.equal(m.releaseCompletes(c + 0.02, 0, c), true);
    }
  });

  it("springs back when released short of the spine and still", () => {
    const c = m.spineCrossing(1, 0.5, right, left);
    assert.equal(m.releaseCompletes(c - 0.05, 0, c), false);
    assert.equal(m.releaseCompletes(0.1, 0, c), false);
  });

  it("follows a flick: forward completes early, backward springs back late", () => {
    const c = m.spineCrossing(1, 0.5, right, left);
    assert.equal(m.releaseCompletes(0.15, 1.5, c), true, "a quick flick from a small lift");
    assert.equal(m.releaseCompletes(c - 0.1, 0.8, c), true, "moving toward the spine");
    assert.equal(m.releaseCompletes(c + 0.1, -1.5, c), false, "thrown back after crossing");
  });
});
