/**
 * Unit tests for sheet visibility and depth priority:
 *
 *   cd web && node --no-warnings --test "components/book/*.test.ts"
 *
 * Modules are imported by computed paths so tsc does not ask for `.ts`
 * import extensions (see leafShape.test.ts).
 */

import assert from "node:assert/strict";
import { describe, it } from "node:test";

type Stacking = typeof import("./stacking");
type Shape = typeof import("./leafShape");
type Springs = typeof import("./leafSprings");
const paths = ["./stacking.ts", "./leafShape.ts", "./leafSprings.ts"];
const s: Stacking = await import(paths[0]);
const m: Shape = await import(paths[1]);
const sp: Springs = await import(paths[2]);

interface Sheet {
  p: number;
  mode: string;
  dir: 1 | -1;
  to: number;
  visible: boolean;
  front: boolean;
  /** Where the sheet's free edge was posed (see runtime.ts). */
  gapL: number;
  gapR: number;
}

/** A spread-1 book of six sheets: two turned to the left, four on the right. */
function book(): Sheet[] {
  return [1, 1, 0, 0, 0, 0].map((p) => ({ p, mode: "rest", dir: 1, to: p, visible: false, front: false, gapL: 2 - 2 * p, gapR: 2 * p }));
}

/** Lays the stacks out and returns the sheets. */
function lay(sheets: Sheet[], warm = false) {
  s.layStacks(sheets, warm);
  return sheets;
}

/** Where `sheet` sits with these rest shapes (angles per bone). */
function pose(sheet: Sheet, dir: 1 | -1, right: Float32Array, left: Float32Array) {
  const at = sheet.mode === "rest" ? { p: sheet.p, dir: 1 as const, curl: 0, corner: 0 } : { p: sheet.p, dir, curl: 0.5, corner: 0.45 };
  return m.turnTargets(at, right, left, new Float32Array(m.BONES));
}

describe("stack layers", () => {
  const right = m.restAngles(0.05, new Float32Array(m.BONES), m.BOW);
  const left = m.restAngles(0.03, new Float32Array(m.BONES), m.BOW);

  it("draws only the top of each stack at rest, none in front", () => {
    const layers = lay(book());
    assert.deepEqual(
      layers.map((l) => l.visible),
      [false, true, true, false, false, false],
    );
    assert.ok(layers.every((l) => !l.front));
  });

  it("draws every sheet while warming up", () => {
    assert.ok(lay(book(), true).every((l) => l.visible));
  });

  /** Sheet `i` in motion, posed where its command puts it (or at `posedAt`, as its springs trail). */
  const moving = (i: number, mode: string, p: number, dir: 1 | -1, to = dir > 0 ? 1 : 0, posedAt = p) => {
    const sheets = book();
    Object.assign(sheets[i], { mode, p, dir, to });
    const springs = new sp.LeafSprings(m.BONES, m.SEG, m.PAGE_H);
    springs.snap(pose({ ...sheets[i], p: posedAt }, dir, right, left), new Float32Array(m.BONES));
    sheets[i].gapL = springs.edgeGap(left, true);
    sheets[i].gapR = springs.edgeGap(right, false);
    return lay(sheets);
  };

  // Regression: a page dragged past the far edge and held lay exactly on that
  // stack's top sheet, with nothing to break the depth tie, so the stale
  // page showed instead of the one being turned. It is drawn in front, and
  // the stack top under it is not drawn at all.
  for (const [name, i, p, dir, stale] of [
    ["forward drag held past the left edge", 2, 1, 1, 1],
    ["back drag held past the right edge", 1, 0, -1, 2],
  ] as const) {
    it(`shows only the moving sheet on a ${name} (regression)`, () => {
      const sheets = moving(i, "drag", p, dir);
      const a = pose(sheets[i], dir, right, left);
      const b = pose(book()[stale], dir, right, left);
      // They coincide, which is why the stale page must not be drawn.
      a.forEach((v, k) => assert.ok(Math.abs(v - b[k]) < 1e-6, `bone ${k} coincides`));
      assert.ok(sheets[i].visible && sheets[i].front, "the turning sheet is drawn in front");
      assert.equal(sheets[stale].visible, false, "the stale stack top is not drawn");
    });
  }

  // Regression: in the last frames of a turn the landing sheet's lift twist
  // dipped it a hair below the stack top it was covering (which a depth bias
  // cannot fix), so the previous page's words and photo showed through.
  for (const [name, i, dir, stale, other] of [
    ["forward", 2, 1, 1, 3],
    ["back", 1, -1, 2, 0],
  ] as const) {
    it(`hides the stack top under a landing sheet on a ${name} turn (regression)`, () => {
      const to = dir > 0 ? 1 : 0;
      const at = (along: number, posed = along) => moving(i, "auto", dir > 0 ? along : 1 - along, dir, to, dir > 0 ? posed : 1 - posed);
      for (const along of [0.995, 0.999, 1]) {
        const sheets = at(along);
        assert.equal(sheets[stale].visible, false, `stale top hidden ${along} along`);
        assert.ok(sheets[i].visible && sheets[i].front, `landing sheet drawn in front ${along} along`);
        assert.ok(sheets[other].visible, `the other stack keeps its top ${along} along`);
        assert.equal(s.landingOn(sheets[i]), to);
      }
      for (const along of [0.2, 0.5, 1 - s.LANDING_REACH - 0.01, 0.95]) {
        assert.ok(at(along)[stale].visible, `stale top still drawn ${along} along, the sheet well clear`);
      }
    });

    // Regression: a held page swept quickly across reaches the far edge in
    // command a tenth of a second before its springs bring it down; hiding
    // the stack top on the command alone blanked the page it was to cover.
    it(`keeps the stack top while a ${name} sweep's page is still coming down (regression)`, () => {
      const to = dir > 0 ? 1 : 0;
      const p = to;
      for (const posed of [0.5, 0.8, 0.95]) {
        const sheets = moving(i, "drag", p, dir, to, dir > 0 ? posed : 1 - posed);
        assert.ok(sheets[stale].visible, `posed ${posed} along: the page under it is drawn`);
        assert.equal(s.landingOn(sheets[i]), -1);
      }
      assert.equal(moving(i, "drag", p, dir, to)[stale].visible, false, "and hidden once the page is down");
    });
  }

  it("lands a released sheet that springs back where it came from", () => {
    // Forward drag let go early: the clock runs it back down onto the right, and it is all but down.
    const sheets = moving(2, "auto", 0.005, 1, 0);
    assert.equal(s.landingOn(sheets[2]), 0);
    assert.equal(sheets[3].visible, false, "the right stack's top is covered");
    assert.ok(sheets[1].visible, "the left stack's top is untouched");
  });

  it("never hides the stack a sheet lifts off, peels from or is frozen on", () => {
    for (const [mode, i, p, dir, under] of [
      ["auto", 2, 0.05, 1, 3],
      ["auto", 1, 0.95, -1, 0],
      ["drag", 2, 0.05, 1, 3],
      ["drag", 1, 0.95, -1, 0],
      ["hover", 2, 0.045, 1, 3],
      ["hover", 1, 0.955, -1, 0],
      ["frozen", 2, 1, 1, 1],
      ["frozen", 1, 0, -1, 2],
    ] as const) {
      const sheets = moving(i, mode, p, dir);
      assert.equal(s.landingOn(sheets[i]), -1, `${mode} ${p}`);
      assert.ok(sheets[under].visible, `${mode} ${p}: the page under it is drawn`);
      assert.ok(sheets[i].front, `${mode} ${p}: the moving sheet wins any tie`);
      assert.ok(sheets.filter((_, k) => k !== i).every((l) => !l.front), `${mode} ${p}`);
    }
  });

  it("biases toward the camera", () => {
    assert.ok(s.FRONT_OFFSET.units < 0);
  });

  // Regression: a slope-scaled bias (factor -2) on the curved sheet reached
  // past the stage slot's tint (SlotProbe, 0.0008 above the page) and cut it
  // away in vertical bands while a page was held on the stage's page.
  it("never reaches content standing just above the page (regression)", () => {
    assert.equal(s.FRONT_OFFSET.factor, 0, "no slope term");
    // Book3D's camera planes, out to well past the farthest framing.
    const [near, far] = [0.4, 40];
    const stageLift = 0.0008;
    for (const d of [1, 3, 4.5, 6, 10]) {
      assert.ok(s.frontBiasReach(d, near, far) < stageLift / 4, `at distance ${d}`);
    }
  });
});
