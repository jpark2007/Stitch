import assert from "node:assert/strict";
import test from "node:test";
import { pickItem } from "./items.ts";

const cases = [
  ["blue flat cap", "flat_cap", "#46699e"],
  ["red scarf", "scarf", "#b8473a"],
  ["red knitted shawl", "scarf", "#b8473a"],
  ["pearl reading glasses", "glasses", "#e9e1d3"],
  ["fishing hat with a lure", "hat", null],
  ["green wool beanie", "beanie", "#5e8a55"],
  ["navy baseball cap", "flat_cap", "#2f4466"],
  ["yellow hair bow", "bow", "#e2b445"],
  ["striped headband", "headband", null],
  ["Grandpa's old Fedora", "hat", null],
  ["a lucky coin", "token", null],
  ["", "token", null],
  [null, "token", null],
];

for (const [text, kind, color] of cases) {
  test(`pickItem(${JSON.stringify(text)}) -> ${kind}`, () => {
    const r = pickItem(text);
    assert.equal(r.kind, kind);
    assert.equal(r.color, color);
  });
}

test("word boundaries: 'that' is not a hat, 'escape' is not a cap, 'elbow' is not a bow", () => {
  assert.equal(pickItem("that thing").kind, "token");
  assert.equal(pickItem("escape key").kind, "token");
  assert.equal(pickItem("elbow patch").kind, "token");
});

test("bowler is a hat, not a bow", () => {
  assert.equal(pickItem("black bowler").kind, "hat");
});
