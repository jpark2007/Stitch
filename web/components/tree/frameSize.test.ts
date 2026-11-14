/**
 * Unit tests for the tree frame's width limits:
 *
 *   cd web && node --no-warnings --test "components/tree/*.test.ts"
 */

import assert from "node:assert/strict";
import { describe, it } from "node:test";

type FrameSize = typeof import("./frameSize");
const modulePath = "./frameSize.ts";
const { frameWidths }: FrameSize = await import(modulePath);

describe("frameWidths", () => {
  it("leaves the frame unlimited with no limits", () => {
    assert.deepEqual(frameWidths(168, 192.8, {}), { maxWidth: undefined, minWidth: undefined });
  });

  it("narrows to the height cap, keeping the tree's shape", () => {
    assert.equal(frameWidths(200, 100, { maxHeight: "50vh" }).maxWidth, "calc(50vh * 2.0000)");
  });

  it("caps the scale, so one person is not drawn giant", () => {
    // One person: W 168, H 192.8 (the case that filled a 1280x900 page).
    assert.equal(frameWidths(168, 192.8, { maxScale: 1.05 }).maxWidth, "176.4px");
  });

  it("takes the smaller of the height cap and the scale cap", () => {
    const { maxWidth } = frameWidths(286, 192.8, { maxHeight: "calc(100dvh - 17rem)", maxScale: 1.05 });
    assert.equal(maxWidth, "min(calc(calc(100dvh - 17rem) * 1.4834), 300.3px)");
  });

  it("keeps a new family's first tree (one person, a couple) near the demo scale", async () => {
    // Every new family starts as its creator alone; check against the real layout.
    const { DEFAULT_LAYOUT, layoutFamily }: typeof import("./layout") = await import("./layout.ts" as string);
    for (const people of [[{ id: "a" }], [{ id: "a" }, { id: "b", spouseIds: ["a"] }]]) {
      const W = layoutFamily(people, DEFAULT_LAYOUT).width;
      const cap = Number.parseFloat(frameWidths(W, 200, { maxScale: 1.05 }).maxWidth!);
      // A puppet then stands about 131px tall, not the 400px it once filled the page with.
      assert.ok(cap < 320, `${people.length} people: the frame may grow to ${cap}px`);
    }
  });

  it("sets a floor from the smallest scale", () => {
    assert.equal(frameWidths(1000, 600, { minScale: 0.42 }).minWidth, "420.0px");
  });
});
