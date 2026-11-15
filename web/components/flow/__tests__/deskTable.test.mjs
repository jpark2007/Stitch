// Run with: cd web && node --test "components/flow/__tests__/*.test.mjs"
// The book desk's table beyond the book's canvas (deskTable.ts).
import { test } from "node:test";
import assert from "node:assert/strict";

import { BLEED, deskLayout, tableBands, writingShare } from "../deskTable.ts";

test("deskLayout: 30/70 from 1280, 40/60 below; the canvas wider than its area, centred on it", () => {
  assert.equal(writingShare(1440), 0.3);
  assert.equal(writingShare(1280), 0.3);
  assert.equal(writingShare(1024), 0.4);
  const l = deskLayout(1440, 900);
  assert.equal(l.area.x, 432);
  assert.equal(l.area.width, 1008);
  assert.equal(l.canvas.width, Math.round(1008 * BLEED));
  assert.equal(l.canvas.x, Math.round((1008 - l.canvas.width) / 2));
  assert.equal(l.canvas.height, 900);
  // Never squarer than 1.05: at 1024 the canvas is a landscape box, centred in the height.
  const s = deskLayout(1024, 768);
  assert.ok(s.canvas.width / s.canvas.height >= 1.04);
  assert.equal(s.canvas.y, Math.round((768 - s.canvas.height) / 2));
});

test("tableBands: planks run edge to edge down the window, nearer ones taller", () => {
  for (const [w, h] of [[1440, 900], [1280, 800], [1920, 1080], [1024, 768]]) {
    const bands = tableBands(deskLayout(w, h), h);
    assert.ok(bands.length >= 4, `${w}x${h}: ${bands.length} bands`);
    assert.ok(bands[0].top <= 0, "the first plank reaches the top");
    const last = bands[bands.length - 1];
    assert.ok(last.top + last.height >= h, "the last plank reaches the bottom");
    for (let i = 1; i < bands.length; i++) {
      assert.ok(Math.abs(bands[i].top - (bands[i - 1].top + bands[i - 1].height)) < 0.01, "no gap between planks");
      assert.ok(bands[i].height > bands[i - 1].height, "nearer planks are taller");
      assert.ok(bands[i].sx > bands[i - 1].sx, "nearer grain is wider");
    }
    for (const b of bands) assert.ok([0, 102.4, 204.8, 307.2, 409.6].includes(b.row), `row ${b.row} is a plank's first row`);
  }
});

test("tableBands: the seams fall where the book's canvas draws them (measured from its pixels)", () => {
  // A canvas the size of its area (1008 x 900 at 1440 x 900): the seams the
  // canvas drew there, found as dark rows in a screenshot, at y = 76, 207,
  // 355, 522 and 712 (within the grooves' few pixels).
  const layout = { area: { x: 432, width: 1008, height: 900 }, canvas: { x: 0, y: 0, width: 1008, height: 900 } };
  const seams = tableBands(layout, 900).map((b) => b.top).filter((y) => y > 0);
  const seen = [76, 207, 355, 522, 712];
  assert.equal(seams.length, seen.length);
  seams.forEach((y, i) => assert.ok(Math.abs(y - seen[i]) <= 3, `seam ${i}: ${y.toFixed(1)} vs ${seen[i]}`));
});
