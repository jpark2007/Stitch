// Run with: cd web && node --test "components/stage/props/__tests__/*.test.mjs"
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import { compilePropTracks, lastAtOrBefore, samplePropTracks } from "../poseTrack.ts";
import { createPropPose } from "../rig.ts";

const timeline = JSON.parse(
  readFileSync(new URL("../../../../public/samples/hymn_books.timeline.json", import.meta.url), "utf8"),
);
const books = compilePropTracks(timeline.tracks, "hymn_books");
const at = (t) => samplePropTracks(books, t, createPropPose());
const near = (a, b, eps = 1e-9) => assert.ok(Math.abs(a - b) < eps, `${a} != ${b}`);
const nearVec = (v, w) => v.forEach((x, i) => near(x, w[i]));

test("binary search finds the last key at or before t", () => {
  const times = Float64Array.from([0, 1, 1, 3]);
  assert.equal(lastAtOrBefore(times, -1), -1);
  assert.equal(lastAtOrBefore(times, 0), 0);
  assert.equal(lastAtOrBefore(times, 1), 2);
  assert.equal(lastAtOrBefore(times, 2.9), 2);
  assert.equal(lastAtOrBefore(times, 99), 3);
});

test("the sample's parent swaps happen at their keys", () => {
  assert.equal(at(-1).parent, "puppet:mom.hand_r");
  assert.equal(at(4.44).parent, "puppet:mom.hand_r");
  assert.equal(at(4.45).parent, "ground");
  assert.equal(at(7.1).parent, "puppet:dad.hand_r");
  assert.equal(at(8.3).parent, "puppet:mom.hand_r");
  assert.equal(at(99).parent, "puppet:mom.hand_r");
});

test("a dropped prop falls between keys inside its ground segment", () => {
  nearVec(at(4.45).position, [0.045, 0.513, 0.5]);
  nearVec(at(4.6).position, [0.0825, 0.4065, 0.5]);
  nearVec(at(4.75).position, [0.12, 0.3, 0.5]);
});

test("position never interpolates across a parent change", () => {
  // Held offset before the drop, not a blend toward the ground position.
  nearVec(at(4.4).position, [0, 0, 0]);
  // Resting on the steps until the very moment Dad picks them up...
  nearVec(at(7.0999).position, [0.12, 0.3, 0.5]);
  // ...then the held offset, as a step.
  nearVec(at(7.1).position, [0, 0, 0]);
});

test("rotation, state and parts sample linearly or by step", () => {
  const tracks = [
    { target: "prop:car", channel: "rotation", keys: [[0, 0], [2, 1]] },
    { target: "prop:car", channel: "state", keys: [[0, "parked"], [1, "driving"]] },
    { target: "prop:car", channel: "part.wheel_front.rot", keys: [[0, 0], [1, -10]] },
    { target: "prop:other", channel: "rotation", keys: [[0, 5]] },
    { target: "prop:car", channel: "position", keys: [[0, "bad"], [1, [1, 0, 0.5]]] },
  ];
  const car = compilePropTracks(tracks, "car");
  const p = samplePropTracks(car, 0.5, createPropPose({ parent: "ground" }));
  near(p.rotation, 0.25);
  assert.equal(p.state, "parked");
  near(p.parts.wheel_front, -5);
  assert.equal(p.parent, "ground");
  // The malformed key is skipped; the valid one holds from the start.
  nearVec(p.position, [1, 0, 0.5]);
  assert.equal(samplePropTracks(car, 1, p).state, "driving");
  near(samplePropTracks(car, 5, p).parts.wheel_front, -10);
});

test("sampling into the same pose allocates no new arrays", () => {
  const p = createPropPose();
  const pos = p.position;
  for (let t = 0; t < 10; t += 0.1) samplePropTracks(books, t, p);
  assert.equal(p.position, pos);
});
