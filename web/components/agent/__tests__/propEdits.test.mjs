// Run with: cd web && node --test components/agent/__tests__/
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  initialPropDecisions,
  validatePropDecisions,
  toPropEdits,
} from "../propEdits.ts";

const chips = [
  { id: "hymn_books", name: "Hymn books", size: "hand" },
  { id: "pew", name: "Church pew", size: "furniture", reused: true },
  { id: "car", name: "Blue Buick", size: "vehicle" },
];

test("all chips default to keep", () => {
  const d = initialPropDecisions(chips);
  assert.deepEqual(d, {
    hymn_books: { action: "keep" },
    pew: { action: "keep" },
    car: { action: "keep" },
  });
  assert.deepEqual(validatePropDecisions(chips, d), {});
  assert.deepEqual(toPropEdits(chips, d), [
    { id: "hymn_books", action: "keep" },
    { id: "pew", action: "keep" },
    { id: "car", action: "keep" },
  ]);
});

test("empty chip list", () => {
  assert.deepEqual(initialPropDecisions([]), {});
  assert.deepEqual(toPropEdits([], {}), []);
});

test("rename trims the name", () => {
  const d = { ...initialPropDecisions(chips), pew: { action: "rename", name: "  Back pew  " } };
  assert.deepEqual(validatePropDecisions(chips, d), {});
  assert.deepEqual(toPropEdits(chips, d)[1], { id: "pew", action: "rename", name: "Back pew" });
});

test("rename rejects blank names", () => {
  for (const name of ["", "   "]) {
    const d = { ...initialPropDecisions(chips), pew: { action: "rename", name } };
    const errors = validatePropDecisions(chips, d);
    assert.deepEqual(Object.keys(errors), ["pew"]);
    assert.equal(typeof errors.pew, "string");
  }
});

test("rename allows 60 characters and rejects 61", () => {
  const ok = { ...initialPropDecisions(chips), car: { action: "rename", name: "x".repeat(60) } };
  assert.deepEqual(validatePropDecisions(chips, ok), {});
  const long = { ...initialPropDecisions(chips), car: { action: "rename", name: "x".repeat(61) } };
  assert.deepEqual(Object.keys(validatePropDecisions(chips, long)), ["car"]);
  // Length is measured after trimming.
  const padded = { ...initialPropDecisions(chips), car: { action: "rename", name: `  ${"x".repeat(60)}  ` } };
  assert.deepEqual(validatePropDecisions(chips, padded), {});
});

test("rename rejects an unchanged name", () => {
  const d = { ...initialPropDecisions(chips), hymn_books: { action: "rename", name: " Hymn books " } };
  assert.deepEqual(Object.keys(validatePropDecisions(chips, d)), ["hymn_books"]);
});

test("remove produces a remove edit", () => {
  const d = { ...initialPropDecisions(chips), car: { action: "remove" } };
  assert.deepEqual(validatePropDecisions(chips, d), {});
  assert.deepEqual(toPropEdits(chips, d)[2], { id: "car", action: "remove" });
});

test("replace_photo requires a photoId", () => {
  const missing = {
    ...initialPropDecisions(chips),
    hymn_books: { action: "replace_photo", photoId: null, fileName: "books.jpg" },
  };
  assert.deepEqual(Object.keys(validatePropDecisions(chips, missing)), ["hymn_books"]);
  const empty = { ...initialPropDecisions(chips), hymn_books: { action: "replace_photo", photoId: "" } };
  assert.deepEqual(Object.keys(validatePropDecisions(chips, empty)), ["hymn_books"]);

  const ok = {
    ...initialPropDecisions(chips),
    hymn_books: { action: "replace_photo", photoId: "media_123", fileName: "books.jpg" },
  };
  assert.deepEqual(validatePropDecisions(chips, ok), {});
  assert.deepEqual(toPropEdits(chips, ok)[0], {
    id: "hymn_books",
    action: "replace_photo",
    photoId: "media_123",
  });
});

test("output keeps chip order, not decision order", () => {
  const d = {
    car: { action: "remove" },
    pew: { action: "rename", name: "Pew" },
    hymn_books: { action: "keep" },
  };
  assert.deepEqual(
    toPropEdits(chips, d).map((e) => [e.id, e.action]),
    [
      ["hymn_books", "keep"],
      ["pew", "rename"],
      ["car", "remove"],
    ],
  );
});

test("missing decisions count as keep and unknown ids are ignored", () => {
  const d = { pew: { action: "remove" }, ghost: { action: "remove" } };
  assert.deepEqual(toPropEdits(chips, d), [
    { id: "hymn_books", action: "keep" },
    { id: "pew", action: "remove" },
    { id: "car", action: "keep" },
  ]);
});

test("toPropEdits throws when validation fails", () => {
  const d = {
    ...initialPropDecisions(chips),
    pew: { action: "rename", name: "" },
    car: { action: "replace_photo", photoId: null },
  };
  assert.deepEqual(Object.keys(validatePropDecisions(chips, d)).sort(), ["car", "pew"]);
  assert.throws(() => toPropEdits(chips, d), /pew/);
});
