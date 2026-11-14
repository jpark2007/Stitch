/**
 * ?memory=<memoryId> (memoryParam.ts): which spread a book opens at.
 *
 *   cd web && node --no-warnings --test "components/book/*.test.ts"
 *
 * The module is imported by a computed path so tsc does not ask for a `.ts`
 * import extension (see leafShape.test.ts).
 */

import assert from "node:assert/strict";
import { describe, it } from "node:test";

type Param = typeof import("./memoryParam");
const paths = ["./memoryParam.ts"];
const p: Param = await import(paths[0]);

type Initial = { state?: "closed" | "open"; spread?: number; turn?: number | null; focus?: -1 | 0 | 1 };

// A person's book: three moments, then "The end".
const BOOK = [
  { id: "m-wedding-1979", kind: "moment" as const },
  { id: "m-steps-1987" },
  { id: "m-lake-1994", kind: "moment" as const },
  { id: "david:end", kind: "end" as const },
];

describe("memorySpread", () => {
  it("finds the memory's spread, 1-based", () => {
    assert.equal(p.memorySpread(BOOK, "m-wedding-1979"), 1);
    assert.equal(p.memorySpread(BOOK, "m-steps-1987"), 2);
    assert.equal(p.memorySpread(BOOK, "m-lake-1994"), 3);
  });

  it("trims the id", () => {
    assert.equal(p.memorySpread(BOOK, "  m-lake-1994 "), 3);
  });

  it("null for a memory the book does not have", () => {
    assert.equal(p.memorySpread(BOOK, "m-nope"), null);
  });

  it("never the closing spread, even by its id", () => {
    assert.equal(p.memorySpread(BOOK, "david:end"), null);
  });

  it("null with no memory asked for", () => {
    assert.equal(p.memorySpread(BOOK, null), null);
    assert.equal(p.memorySpread(BOOK, undefined), null);
    assert.equal(p.memorySpread(BOOK, ""), null);
    assert.equal(p.memorySpread(BOOK, "   "), null);
  });

  it("null in an empty book", () => {
    assert.equal(p.memorySpread([], "m-steps-1987"), null);
  });

  it("a one-moment book (/memories/<id>?memory=<id>)", () => {
    const one = [{ id: "m-steps-1987" }, { id: "m-steps-1987:end", kind: "end" as const }];
    assert.equal(p.memorySpread(one, "m-steps-1987"), 1);
  });
});

describe("openAtMemory", () => {
  const none: Initial = { state: undefined, spread: undefined, turn: null, focus: 0 };

  it("found, no ?spread: opens at the memory's spread", () => {
    assert.deepEqual(p.openAtMemory(none, BOOK, "m-lake-1994"), { state: "open", spread: 3, turn: null, focus: 0 });
  });

  it("found, with ?spread: ?memory wins", () => {
    const asked: Initial = { ...none, spread: 1 };
    assert.deepEqual(p.openAtMemory(asked, BOOK, "m-steps-1987"), { state: "open", spread: 2, turn: null, focus: 0 });
  });

  it("found, with ?state=closed: ?memory still opens the book", () => {
    const closed: Initial = { ...none, state: "closed" };
    assert.deepEqual(p.openAtMemory(closed, BOOK, "m-steps-1987"), { state: "open", spread: 2, turn: null, focus: 0 });
  });

  it("keeps ?turn and ?focus", () => {
    const close: Initial = { state: undefined, spread: undefined, turn: 0.4, focus: 1 };
    assert.deepEqual(p.openAtMemory(close, BOOK, "m-lake-1994"), { state: "open", spread: 3, turn: 0.4, focus: 1 });
  });

  it("does not change the view it was given", () => {
    const asked: Initial = { ...none, spread: 1 };
    p.openAtMemory(asked, BOOK, "m-lake-1994");
    assert.deepEqual(asked, { ...none, spread: 1 });
  });

  it("not found, no ?spread: the normal opening, untouched", () => {
    assert.equal(p.openAtMemory(none, BOOK, "m-nope"), none);
  });

  it("not found, with ?spread: ?spread as before", () => {
    const asked: Initial = { ...none, spread: 2, state: "open" };
    assert.equal(p.openAtMemory(asked, BOOK, "m-nope"), asked);
  });

  it("no ?memory: untouched", () => {
    const asked: Initial = { ...none, spread: 2 };
    assert.equal(p.openAtMemory(asked, BOOK, null), asked);
  });

  it("empty book: untouched", () => {
    assert.equal(p.openAtMemory(none, [], "m-steps-1987"), none);
  });
});
