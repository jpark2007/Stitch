// Run with: cd web && node --test "components/stage/play/*.test.ts"
// The time-of-day light grade.

import { test } from "node:test";
import assert from "node:assert/strict";

import type * as Grade from "./grade";

const { blendGrade, createGrade } = (await import(new URL("./grade.ts", import.meta.url).href)) as typeof Grade;

test("no time of day is plain day: the identity", () => {
  const g = blendGrade(createGrade(), null, null, 0);
  assert.deepEqual([g.key, g.fill, g.rim, g.hemi], [1, 1, 1, 1]);
  assert.deepEqual(g.keyTint.toArray(), [1, 1, 1]);
});

test("night dims and cools the lamp; the blend eases from the previous time of day", () => {
  const g = createGrade();
  const night = blendGrade(g, "day", "night", 1);
  assert.ok(night.key <= 0.6, "the lamp dims");
  assert.ok(night.keyTint.b > night.keyTint.r, "moonlight is blue");
  const half = blendGrade(createGrade(), "day", "night", 0.5).key;
  assert.ok(half < 1 && half > night.key);
  assert.equal(blendGrade(createGrade(), "day", "night", 0).key, 1);
  // Unknown names are day; out-of-range mixes clamp.
  assert.equal(blendGrade(createGrade(), "teatime", "elevenses", 0.7).key, 1);
  assert.equal(blendGrade(createGrade(), "day", "night", 3).key, night.key);
});
