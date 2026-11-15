// Run with: cd web && node --test --experimental-strip-types "components/demo/__tests__/*.test.mjs"
import { describe, test } from "node:test";
import assert from "node:assert/strict";

import { NO_ARRIVALS, byYearDesc, isNew, noteArrivals, withNewFirst } from "../arrivals.ts";

const m = (id, year, title = id) => ({ id, year, title });

describe("noteArrivals", () => {
  test("the first list is the baseline: nothing in it is new", () => {
    const a = noteArrivals(NO_ARRIVALS, ["a", "b"], 1);
    assert.deepEqual([...a.baseline], ["a", "b"]);
    assert.equal(a.arrived.size, 0);
    assert.equal(isNew(a, "a"), false);
  });

  test("an id seen later is new, with the time it was first seen, kept across polls", () => {
    let a = noteArrivals(NO_ARRIVALS, ["a"], 1);
    a = noteArrivals(a, ["a", "c"], 5);
    a = noteArrivals(a, ["a", "c", "d"], 9);
    assert.equal(a.arrived.get("c"), 5);
    assert.equal(a.arrived.get("d"), 9);
    assert.equal(isNew(a, "c"), true);
    assert.equal(isNew(a, "a"), false);
  });

  test("a poll with nothing new returns the same object", () => {
    const a = noteArrivals(NO_ARRIVALS, ["a"], 1);
    assert.equal(noteArrivals(a, ["a"], 2), a);
    const b = noteArrivals(a, ["a", "b"], 3);
    assert.equal(noteArrivals(b, ["a", "b"], 4), b);
  });

  test("an empty first list still counts as loaded", () => {
    let a = noteArrivals(NO_ARRIVALS, [], 1);
    a = noteArrivals(a, ["x"], 2);
    assert.equal(isNew(a, "x"), true);
  });

  test("an item that disappears and comes back keeps its first arrival time", () => {
    let a = noteArrivals(NO_ARRIVALS, [], 1);
    a = noteArrivals(a, ["x"], 2);
    a = noteArrivals(a, [], 3);
    a = noteArrivals(a, ["x"], 4);
    assert.equal(a.arrived.get("x"), 2);
  });
});

describe("withNewFirst", () => {
  test("new items first, latest arrival first; the rest by year, newest first", () => {
    let a = noteArrivals(NO_ARRIVALS, ["old1", "old2"], 1);
    a = noteArrivals(a, ["old1", "old2", "n1"], 2);
    a = noteArrivals(a, ["old1", "old2", "n1", "n2"], 3);
    const items = [m("old1", 1962), m("n1", 1950), m("old2", 1990), m("n2", 1940)];
    const out = withNewFirst(items, a, byYearDesc);
    assert.deepEqual(out.map((x) => x.id), ["n2", "n1", "old2", "old1"]);
    assert.deepEqual(items.map((x) => x.id), ["old1", "n1", "old2", "n2"], "input untouched");
  });

  test("same year sorts by title", () => {
    const out = withNewFirst([m("b", 2000, "Beta"), m("a", 2000, "Alpha")], NO_ARRIVALS, byYearDesc);
    assert.deepEqual(out.map((x) => x.id), ["a", "b"]);
  });
});
