// Run with: cd web && node --test "components/flow/__tests__/*.test.mjs"
// Node strips the erasable TypeScript in buttonLayers.ts; no build step needed.
import { test } from "node:test";
import assert from "node:assert/strict";

import { busyLayers } from "../buttonLayers.ts";

test("busyLayers: no busy props renders the children alone", () => {
  assert.equal(busyLayers({}), "none");
  assert.equal(busyLayers({ busy: undefined, busyLabel: undefined }), "none");
});

test("busyLayers: busy={false} reserves the busy width (a caller that can go busy)", () => {
  assert.equal(busyLayers({ busy: false }), "reserve");
});

test("busyLayers: busy={true} reserves the busy width", () => {
  assert.equal(busyLayers({ busy: true }), "reserve");
});

test("busyLayers: a busyLabel alone reserves the busy width", () => {
  assert.equal(busyLayers({ busyLabel: "Saving…" }), "reserve");
  // An empty label is still a label the caller chose.
  assert.equal(busyLayers({ busyLabel: "" }), "reserve");
  assert.equal(busyLayers({ busy: false, busyLabel: "Saving…" }), "reserve");
});
