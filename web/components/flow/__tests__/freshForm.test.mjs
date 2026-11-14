// Run with: cd web && node --test "components/flow/__tests__/*.test.mjs"
// Node strips the erasable TypeScript in freshForm.ts; no build step needed.
import { test } from "node:test";
import assert from "node:assert/strict";

import { freshFormTracker } from "../freshForm.ts";

test("freshFormTracker: no reset before any submit", () => {
  const t = freshFormTracker();
  assert.equal(t.takeReset(), false);
  // Asking again (an effect that runs twice, or the page shown again) changes nothing.
  assert.equal(t.takeReset(), false);
});

test("freshFormTracker: exactly one reset after a submit", () => {
  const t = freshFormTracker();
  t.markSubmitted();
  assert.equal(t.takeReset(), true);
  assert.equal(t.takeReset(), false);
  assert.equal(t.takeReset(), false);
});

test("freshFormTracker: no second reset without a new submit, one more after it", () => {
  const t = freshFormTracker();
  t.markSubmitted();
  assert.equal(t.takeReset(), true);
  assert.equal(t.takeReset(), false);
  t.markSubmitted();
  assert.equal(t.takeReset(), true);
  assert.equal(t.takeReset(), false);
});

test("freshFormTracker: two submits before the page is shown again still reset once", () => {
  const t = freshFormTracker();
  t.markSubmitted();
  t.markSubmitted();
  assert.equal(t.takeReset(), true);
  assert.equal(t.takeReset(), false);
});

test("freshFormTracker: trackers are independent", () => {
  const a = freshFormTracker();
  const b = freshFormTracker();
  a.markSubmitted();
  assert.equal(b.takeReset(), false);
  assert.equal(a.takeReset(), true);
});
