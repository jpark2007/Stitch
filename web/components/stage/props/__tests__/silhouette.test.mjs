// Run with: cd web && node --test "components/stage/props/__tests__/*.test.mjs"
//
// The bug: "Sam's red bike" is rigged as a vehicle (so Sam can ride it), and
// its placeholder card was cut by size class alone: a red sedan with windows,
// while the narration says "a red bike with a bell". The prop's own words now
// pick the shape first; the size class is only the fallback.
import { test } from "node:test";
import assert from "node:assert/strict";

import { mirrorForGrip, placeholderLook, rollsOnWheels, tagLabel, TAG_LABEL_MAX } from "../silhouette.ts";

const VEHICLES = new Set(["bicycle", "car", "truck", "boat", "train", "plane", "wagon", "stroller"]);

const RED_BIKE = {
  name: "Sam's red bike",
  description:
    "a red bicycle with a silver bell, two sizes too big; rigged as a vehicle so its rider can ride it (the seat is the driver socket) and its wheels spin",
};

test("the seed's red bike is a bicycle with a bell, not a car", () => {
  assert.deepEqual(placeholderLook(RED_BIKE), { shape: "bicycle", bell: true, variant: null });
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
    assert.ok(!VEHICLES.has(placeholderLook(p).shape), p.name);
  }
  assert.equal(placeholderLook({ name: "Carmen's hat" }).shape, "hat", "Carmen is no car, and her hat is a hat");
  for (const name of ["the road map", "training shoes", "a careful note"]) assert.equal(placeholderLook({ name }).shape, null, name);
});

test("no words, no name: the size class decides (null shape)", () => {
  assert.deepEqual(placeholderLook({}), { shape: null, bell: false, variant: null });
  assert.deepEqual(placeholderLook({ name: null, description: null }), { shape: null, bell: false, variant: null });
});

test("wheels: by shape when one is named, else only the vehicle size", () => {
  assert.equal(rollsOnWheels("bicycle", "vehicle"), true);
  assert.equal(rollsOnWheels("wagon", "furniture"), true);
  assert.equal(rollsOnWheels("boat", "vehicle"), false);
  assert.equal(rollsOnWheels("plane", "vehicle"), false);
  assert.equal(rollsOnWheels(null, "vehicle"), true);
  assert.equal(rollsOnWheels(null, "furniture"), false);
  for (const shape of ["flowers", "cup", "suitcase", "ball", "books"]) assert.equal(rollsOnWheels(shape, "hand"), false, shape);
});

/* ------------------------------------------------------------------------ */
/* Things held, carried and worn (Issue #1: everything hand-sized was books) */
/* ------------------------------------------------------------------------ */

const shapeOf = (name, description) => placeholderLook({ name, description }).shape;

test("each held thing the words name gets its own shape", () => {
  const cases = {
    "a bouquet of roses": "flowers",
    "Mom's flowers": "flowers",
    "a single red rose": "flowers",
    "yellow tulips": "flowers",
    "the letter": "letter",
    "an envelope": "letter",
    "a birthday card": "letter",
    "a postcard from Lisbon": "letter",
    "Grandma's teacup": "cup",
    "a coffee mug": "cup",
    "the cup": "cup",
    "a straw hat": "hat",
    "baseball cap": "hat",
    "birthday cake": "cake",
    "a cupcake": "cake",
    "wedding ring": "ring",
    "a photo of Dad": "photo",
    "the picture frame": "photo",
    "an old photograph": "photo",
    "a soccer ball": "ball",
    "baseball": "ball",
    "the basketball": "ball",
    "a ball": "ball",
    "milk bottle": "bottle",
    "the house key": "key",
    "a suitcase": "suitcase",
    "luggage": "suitcase",
    "a red umbrella": "umbrella",
    "Dad's guitar": "guitar",
    "a violin": "violin",
    "a toy piano": "piano",
    "a lantern": "lamp",
    "the desk lamp": "lamp",
    "picnic basket": "basket",
    "a wrapped gift": "gift",
    "a birthday present": "gift",
    "her diploma": "diploma",
    "a certificate": "diploma",
    "an old scroll": "diploma",
    "the phone": "phone",
    "a rotary telephone": "phone",
    "a camera": "camera",
    "a rag doll": "doll",
    "a teddy bear": "doll",
    "a paper kite": "kite",
  };
  for (const [name, shape] of Object.entries(cases)) assert.equal(shapeOf(name), shape, name);
});

test("variants: the words pick which kind of a shape", () => {
  const variantOf = (name, description) => placeholderLook({ name, description }).variant;
  assert.equal(variantOf("a soccer ball"), "soccer");
  assert.equal(variantOf("a football"), "soccer");
  assert.equal(variantOf("baseball"), "baseball");
  assert.equal(variantOf("basketball"), "basketball");
  assert.equal(variantOf("beach ball"), "beach");
  assert.equal(variantOf("a ball"), null);
  assert.equal(variantOf("baseball cap"), "cap", "a baseball cap is a cap, not a ball");
  assert.equal(variantOf("a straw hat"), null);
  assert.equal(variantOf("coffee mug"), "mug");
  assert.equal(variantOf("teacup"), null);
  assert.equal(variantOf("a postcard"), "postcard");
  assert.equal(variantOf("lantern"), "lantern");
  assert.equal(variantOf("rotary phone"), "rotary");
  assert.equal(variantOf("smartphone"), null);
  assert.equal(variantOf("teddy bear"), "teddy");
  assert.equal(variantOf("birthday cake", "chocolate, with seven candles"), "candles", "candles when mentioned");
  assert.equal(variantOf("birthday cake", "chocolate, two layers"), null, "no candles unless mentioned");
  assert.equal(variantOf("engagement ring"), "gem");
  assert.equal(variantOf("a ring"), "gem");
  assert.equal(variantOf("wedding ring", "a plain gold band"), null);
  assert.equal(variantOf("wedding ring", "gold, with a small diamond"), "gem");
});

test("whole words: Carmen is no car, a cupboard no cup, a keyboard no key", () => {
  assert.notEqual(shapeOf("Carmen"), "car");
  assert.equal(shapeOf("Carmen"), null);
  assert.equal(shapeOf("the cupboard"), null);
  assert.equal(shapeOf("keyboard"), null);
  assert.equal(shapeOf("a hatbox"), null);
  assert.equal(shapeOf("ringlets"), null);
  assert.equal(shapeOf("cardboard box"), null);
  assert.equal(shapeOf("a balloon"), null);
  assert.equal(shapeOf("the bottleneck"), null);
  assert.equal(shapeOf("a keystone"), null);
  assert.equal(shapeOf("capstan"), null);
  assert.equal(shapeOf("Rose's hat"), "hat", "Rose's is a name, her hat a hat");
  assert.equal(shapeOf("Lily's diary"), "books");
});

test("books only when the words name a book", () => {
  for (const name of ["hymn books", "a book", "a hymnal", "her diary", "his journal", "the photo album", "a picture book", "a notebook"]) {
    assert.equal(shapeOf(name), "books", name);
  }
  assert.equal(shapeOf("hymn books", "a stack of worn blue hymn books"), "books");
  // Things that used to fall to the book stack by size class.
  for (const name of ["Grandpa's pocket watch", "a thimble", "the seashell", "her locket", "a jar lid"]) {
    assert.notEqual(shapeOf(name), "books", name);
  }
});

test("unknown words: no shape, so the neutral keepsake tag", () => {
  for (const name of ["Grandpa's pocket watch", "a thimble", "the seashell", "her locket", "stuff", ""]) {
    assert.equal(shapeOf(name), null, name);
  }
});

test("the name before the description; a gift is only a gift when nothing else is named", () => {
  assert.equal(shapeOf("the bouquet", "roses wrapped in a birthday card"), "flowers");
  assert.equal(shapeOf("Mom's keepsake", "a small framed photo of the farm"), "photo");
  assert.equal(shapeOf("Ana's gift", "a teddy bear with a red bow"), "doll");
  assert.equal(shapeOf("a birthday present", "wrapped in gold paper"), "gift");
  assert.equal(shapeOf("the thing", "a present for Lucia"), "gift");
});

test("a handle goes to the grip's side", () => {
  assert.equal(mirrorForGrip("cup", [0.5, 0.5]), false, "centre grip: handle stays right");
  assert.equal(mirrorForGrip("cup", [0.2, 0.5]), true, "grip on the left: handle mirrored left");
  assert.equal(mirrorForGrip("cup", [0.85, 0.5]), false);
  assert.equal(mirrorForGrip("key", [0.15, 0.5]), false, "bow already left");
  assert.equal(mirrorForGrip("key", [0.8, 0.5]), true);
  assert.equal(mirrorForGrip("flowers", [0.1, 0.5]), false, "no handle: never mirrored");
  assert.equal(mirrorForGrip(null, [0.1, 0.5]), false);
  assert.equal(mirrorForGrip("cup", null), false);
  assert.equal(mirrorForGrip("cup", [Number.NaN, 0]), false);
});

test("tag labels: short, without articles or owners when too long", () => {
  assert.equal(tagLabel("a thimble"), "thimble");
  assert.equal(tagLabel("The seashell"), "seashell");
  assert.equal(tagLabel("Mom's thimble"), "Mom's thimble");
  assert.equal(tagLabel("Grandpa's pocket watch"), "pocket watch");
  assert.equal(tagLabel("jar of buttons from the old house"), "jar of buttons");
  assert.equal(tagLabel("an extraordinarily long thingamajig"), "thingamajig");
  assert.equal(tagLabel("Grandpa's old brass pocket watch"), "pocket watch");
  assert.equal(tagLabel("the silver locket Aunt Rosa wore on Sundays"), "silver locket");
  assert.equal(tagLabel(""), "keepsake");
  assert.equal(tagLabel(null), "keepsake");
  assert.equal(tagLabel("   "), "keepsake");
  for (const name of ["Grandpa's pocket watch", "the silver locket Aunt Rosa wore on Sundays", "supercalifragilisticexpialidocious"]) {
    assert.ok(tagLabel(name).length <= TAG_LABEL_MAX + 2, `${name} -> ${tagLabel(name)}`);
  }
});
