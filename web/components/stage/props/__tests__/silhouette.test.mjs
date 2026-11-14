// Run with: cd web && node --test "components/stage/props/__tests__/*.test.mjs"
//
// The bug: "Sam's red bike" is rigged as a vehicle (so Sam can ride it), and
// its placeholder card was cut by size class alone: a red sedan with windows,
// while the narration says "a red bike with a bell". The prop's own words now
// pick the shape first; the size class is only the fallback.
import { test } from "node:test";
import assert from "node:assert/strict";

import { placeholderLook, rollsOnWheels } from "../silhouette.ts";

const RED_BIKE = {
  name: "Sam's red bike",
  description:
    "a red bicycle with a silver bell, two sizes too big; rigged as a vehicle so its rider can ride it (the seat is the driver socket) and its wheels spin",
};

test("the seed's red bike is a bicycle with a bell, not a car", () => {
  assert.deepEqual(placeholderLook(RED_BIKE), { shape: "bicycle", bell: true });
});

test("bike words: bike, bicycle, tricycle, plurals and any case", () => {
  for (const name of ["bike", "Bicycle", "a tricycle", "two BIKES", "trike", "motorcycle"]) {
    assert.equal(placeholderLook({ name }).shape, "bicycle", name);
  }
  assert.equal(placeholderLook({ name: "bike" }).bell, false, "no bell unless one is mentioned");
});

test("other vehicle words pick their own shapes", () => {
  const cases = {
    "the green sedan": "car",
    "Dad's old car": "car",
    "a station wagon": "car",
    "pickup truck": "truck",
    "a rowboat": "boat",
    "toy train": "train",
    "a paper plane": "plane",
    "little red wagon": "wagon",
    "the stroller": "stroller",
    "a baby carriage": "stroller",
  };
  for (const [name, shape] of Object.entries(cases)) assert.equal(placeholderLook({ name }).shape, shape, name);
});

test("the name is read before the description, and the first word wins", () => {
  assert.equal(placeholderLook({ name: "the wagon", description: "a wagon full of bikes" }).shape, "wagon");
  assert.equal(placeholderLook({ name: "Ana's gift", description: "a bicycle with training wheels" }).shape, "bicycle");
  assert.equal(placeholderLook({ name: "gift", description: "a car seat for the bike" }).shape, "car");
});

test("whole words only: no false vehicles", () => {
  for (const p of [
    { name: "Carmen's hat", description: "a pale blue straw hat with a white silk flower, exactly like Rosa's" },
    { name: "hymn books", description: "a stack of worn blue hymn books" },
    { name: "the road map", description: "a folded paper road map of the coast, soft at the creases" },
    { name: "training shoes" },
    { name: "a careful note", description: "planed oak, scarred" },
  ]) {
    assert.equal(placeholderLook(p).shape, null, p.name);
  }
});

test("no words, no name: the size class decides (null shape)", () => {
  assert.deepEqual(placeholderLook({}), { shape: null, bell: false });
  assert.deepEqual(placeholderLook({ name: null, description: null }), { shape: null, bell: false });
});

test("wheels: by shape when one is named, else only the vehicle size", () => {
  assert.equal(rollsOnWheels("bicycle", "vehicle"), true);
  assert.equal(rollsOnWheels("wagon", "furniture"), true);
  assert.equal(rollsOnWheels("boat", "vehicle"), false);
  assert.equal(rollsOnWheels("plane", "vehicle"), false);
  assert.equal(rollsOnWheels(null, "vehicle"), true);
  assert.equal(rollsOnWheels(null, "furniture"), false);
});
