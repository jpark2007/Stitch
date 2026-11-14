/**
 * Hover lift regression: the hover affordance on a page with its pop-up
 * standing must not cut through the pop-up, its caption or its pull tabs.
 *
 *   cd web && node --no-warnings --test "components/book/*.test.ts"
 *
 * The bug: hovering the stage page's edge curled the sheet up from the
 * gutter (turnTargets at a small p, plus corner twist), while the stage group
 * standing on it stayed put, so the opaque lifted paper sliced through the
 * layers' bases and covered the caption and the pull-tab card. And a back
 * peel from the left page, counted as a "hover" whatever its side, raised the
 * previous spread's pop-up over this spread's printed page.
 *
 * The fix: on a staged page the hover is a rigid tilt of the sheet about its
 * root at the hinge (tiltTargets), and the controller rotates the stage group
 * by the same angle about the same point (tiltAbout), so paper and pop-up
 * share one motion. A back peel folds the stage under it (stageGoal).
 *
 * Modules are imported by computed paths so tsc does not ask for `.ts`
 * import extensions (see leafShape.test.ts).
 */

import assert from "node:assert/strict";
import { describe, it } from "node:test";

type Shape = typeof import("./leafShape");
const modulePath = "./leafShape.ts";
const m: Shape = await import(modulePath);
const { BONES, SEG, BOW, PAGE_W, COVER_T, HINGE_LIFT, LEAF_REST_GAP } = m;

/** The open book's hinge (controller.ts: COVER_T + HINGE_LIFT once open). */
const HINGE = COVER_T + HINGE_LIFT;
/** Right-block depths above the hinge: a thin, a middling and a thick stack. */
const DEPTHS = [0.02, 0.05, 0.09];
const ANGLES = [0.02, m.HOVER_TILT, 0.07];
/** The pop-up's footprint across the page (stage 0.92 wide, centred; the pulled lever's reach). */
const FOOTPRINT: [number, number] = [0.04, 0.96];

/** Joints (book space x, y) of a sheet rooted at the hinge, like tipOf walks it. */
function joints(angles: Float32Array): [number, number][] {
  const pts: [number, number][] = [[0, HINGE]];
  let x = 0;
  let y = HINGE;
  for (const a of angles) {
    x += SEG * Math.cos(a);
    y += SEG * Math.sin(a);
    pts.push([x, y]);
  }
  return pts;
}

/** Height of a sheet's polyline at x (between its joints), or NaN off it. */
function heightAt(pts: [number, number][], x: number): number {
  for (let i = 0; i < pts.length - 1; i++) {
    const [x0, y0] = pts[i];
    const [x1, y1] = pts[i + 1];
    if (x >= x0 && x <= x1) return x1 === x0 ? y0 : y0 + ((y1 - y0) * (x - x0)) / (x1 - x0);
  }
  return NaN;
}

const restR = (depth: number) => m.restAngles(depth, new Float32Array(BONES), BOW);

describe("hover on a pop-up page: a rigid tilt", () => {
  it("poses the sheet as its rest shape rotated about the hinge", () => {
    for (const depth of DEPTHS) {
      const rest = restR(depth);
      const still = joints(rest);
      for (const a of ANGLES) {
        const tilted = joints(m.tiltTargets(rest, a, new Float32Array(BONES)));
        tilted.forEach(([x, y], i) => {
          const [ex, ey] = m.tiltAbout(still[i][0], still[i][1], 0, HINGE, a);
          assert.ok(Math.hypot(x - ex, y - ey) < 1e-6, `depth ${depth} angle ${a}: joint ${i} off by ${Math.hypot(x - ex, y - ey)}`);
        });
      }
    }
  });

  it("keeps everything standing on the page on the paper (zero clearance change)", () => {
    for (const depth of DEPTHS) {
      const rest = restR(depth);
      const still = joints(rest);
      // The stage group's origin (controller.ts): over the spine, on the page at its centre line.
      const y0 = HINGE + m.pageProfile(PAGE_W / 2, depth, BOW) + LEAF_REST_GAP;
      for (const a of ANGLES) {
        const tilted = joints(m.tiltTargets(rest, a, new Float32Array(BONES)));
        const [ox, oy] = m.tiltAbout(0, y0, 0, HINGE, a);
        const c = Math.cos(a);
        const s = Math.sin(a);
        for (let k = 0; k <= 46; k++) {
          const x = FOOTPRINT[0] + ((FOOTPRINT[1] - FOOTPRINT[0]) * k) / 46;
          // The paper under a thing standing at x, as the stage sees it at rest...
          const [px, py] = m.tiltAbout(x, heightAt(still, x), 0, HINGE, a);
          // ...is where the tilted sheet is.
          const off = py - heightAt(tilted, px);
          assert.ok(Math.abs(off) < 1e-4, `depth ${depth} angle ${a}: paper at x=${x.toFixed(2)} is ${off.toExponential(2)} off`);
          // And the group's transform (origin + rotation.z) carries the thing's
          // base exactly as the sheet's tilt carries that point of paper.
          const bx = ox + x * c;
          const by = oy + x * s;
          const [ex, ey] = m.tiltAbout(x, y0, 0, HINGE, a);
          assert.ok(Math.hypot(bx - ex, by - ey) < 1e-9, `the group's pivot is the hinge (x=${x.toFixed(2)})`);
          // So the base keeps its rest clearance over that paper (measured
          // across the tilted page, i.e. rotated back about the hinge).
          const [, baseY] = m.tiltAbout(bx, by, 0, HINGE, -a);
          const [, paperY] = m.tiltAbout(px, py, 0, HINGE, -a);
          const restGap = y0 - heightAt(still, x);
          assert.ok(Math.abs(baseY - paperY - restGap) < 1e-9, `clearance at x=${x.toFixed(2)}`);
        }
      }
    }
  });

  it("is exactly today's placement with no tilt", () => {
    assert.deepEqual(m.tiltAbout(0, 0.0915, 0, HINGE, 0), [0, 0.0915]);
    const rest = restR(0.05);
    assert.deepEqual(Array.from(m.tiltTargets(rest, 0, new Float32Array(BONES))), Array.from(rest));
  });

  it("raises the fore-edge about a twentieth of a page", () => {
    const rest = restR(0.05);
    const still = joints(rest);
    const tilted = joints(m.tiltTargets(rest, m.HOVER_TILT, new Float32Array(BONES)));
    const rise = tilted[BONES][1] - still[BONES][1];
    assert.ok(rise > 0.035 && rise < 0.07, `rise ${rise}`);
  });

  it("is needed: a curled peel lifts the paper through a pop-up left standing", () => {
    // The old hover on every page (controller.ts setHover): p 0.045, curl 0.55.
    const rest = restR(0.05);
    const still = joints(rest);
    const peel = joints(m.turnTargets({ p: 0.045, dir: 1, curl: 0.55, corner: 0.6 }, rest, rest, new Float32Array(BONES)));
    const rise = heightAt(peel, 0.9) - heightAt(still, 0.9);
    assert.ok(rise > LEAF_REST_GAP * 2, `the peel rises ${rise} under the pop-up's footprint`);
  });
});

describe("stage goal", () => {
  const turned = { p: 1, mode: "rest", dir: 1 };
  const coverUp = 1;

  it("folds the previous spread's pop-up under a back peel (regression)", () => {
    assert.equal(m.stageGoal(turned, { mode: "hover", dir: -1, p: 0.955 }, coverUp), 0);
  });

  it("keeps the pop-up standing on a forward hover of its own page", () => {
    assert.equal(m.stageGoal(turned, { mode: "hover", dir: 1, p: 0 }, coverUp), coverUp);
    assert.equal(m.stageGoal(turned, { mode: "hover", dir: 1, p: m.HOVER_TILT / Math.PI }, coverUp), coverUp);
    assert.equal(m.stageGoal(turned, { mode: "hover", dir: 1, p: 0.045 }, coverUp), coverUp);
  });

  it("folds it as the page really lifts", () => {
    assert.equal(m.stageGoal(turned, { mode: "drag", dir: 1, p: 0.1 }, coverUp), 0);
    assert.equal(m.stageGoal(turned, { mode: "auto", dir: 1, p: 0.1 }, coverUp), 0);
  });

  it("stands at rest, with the cover", () => {
    const rest = { mode: "rest", dir: 1, p: 0 };
    assert.equal(m.stageGoal(turned, rest, coverUp), coverUp);
    assert.equal(m.stageGoal(turned, rest, 0.4), 0.4);
    assert.equal(m.stageGoal({ p: 0, mode: "rest", dir: 1 }, rest, coverUp), 0, "not before the leaf before it has turned");
  });
});
