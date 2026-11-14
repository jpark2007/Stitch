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
const stackingPath = "./stacking.ts";
const shapePath = "./leafShape.ts";
const s: Stacking = await import(stackingPath);
const m: Shape = await import(shapePath);

/** A spread-1 book of six sheets: two turned to the left, four on the right. */
function book() {
  return [1, 1, 0, 0, 0, 0].map((p) => ({ p, mode: "rest" }));
}

/** Where `sheet` sits with these rest shapes (angles per bone). */
function pose(sheet: { p: number; mode: string }, dir: 1 | -1, right: Float32Array, left: Float32Array) {
  const at = sheet.mode === "rest" ? { p: sheet.p, dir: 1 as const, curl: 0, corner: 0 } : { p: sheet.p, dir, curl: 0.5, corner: 0.45 };
  return m.turnTargets(at, right, left, new Float32Array(m.BONES));
}

describe("stack layers", () => {
  const right = m.restAngles(0.05, new Float32Array(m.BONES), m.BOW);
  const left = m.restAngles(0.03, new Float32Array(m.BONES), m.BOW);

  it("draws only the top of each stack at rest, none in front", () => {
    const layers = s.stackLayers(book());
    assert.deepEqual(
      layers.map((l) => l.visible),
      [false, true, true, false, false, false],
    );
    assert.ok(layers.every((l) => !l.front));
  });

  it("draws every sheet while warming up", () => {
    assert.ok(s.stackLayers(book(), true).every((l) => l.visible));
  });

  // Regression: a page dragged past the far edge and held lay exactly on that
  // stack's top sheet, with nothing to break the depth tie, so the stale
  // page showed instead of the one being turned.
  for (const [name, i, p, dir, stale] of [
    ["forward drag held past the left edge", 2, 1, 1, 1],
    ["back drag held past the right edge", 1, 0, -1, 2],
  ] as const) {
    it(`keeps the moving sheet in front on a ${name} (regression)`, () => {
      const sheets = book();
      sheets[i] = { p, mode: "drag" };
      const layers = s.stackLayers(sheets);
      assert.ok(layers[i].visible && layers[stale].visible, "both sheets are drawn");
      const a = pose(sheets[i], dir, right, left);
      const b = pose(sheets[stale], dir, right, left);
      // They coincide, which is why the moving sheet needs the depth bias.
      a.forEach((v, k) => assert.ok(Math.abs(v - b[k]) < 1e-6, `bone ${k} coincides`));
      assert.equal(layers[i].front, true, "the turning sheet wins the tie");
      assert.equal(layers[stale].front, false, "the stale stack top does not");
    });
  }

  it("keeps landing, peeled and frozen sheets in front of the stack they touch", () => {
    for (const [mode, p] of [["auto", 1], ["hover", 0.045], ["frozen", 1]] as const) {
      const sheets = book();
      sheets[2] = { p, mode };
      const layers = s.stackLayers(sheets);
      assert.equal(layers[2].front, true, mode);
      assert.ok(layers.filter((_, k) => k !== 2).every((l) => !l.front), mode);
    }
  });

  it("biases toward the camera", () => {
    assert.ok(s.FRONT_OFFSET_FACTOR < 0 && s.FRONT_OFFSET_UNITS < 0);
  });
});
