/**
 * Unit tests for the family-tree layout. No test runner needed:
 *
 *   cd web && node --no-warnings --test "components/tree/*.test.ts"
 *
 * Node runs the TypeScript directly (type stripping); the module is imported
 * by a computed path so tsc does not ask for a `.ts` import extension.
 */

import assert from "node:assert/strict";
import { describe, it } from "node:test";

type Layout = typeof import("./layout");
type LayoutPerson = import("./layout").LayoutPerson;
type TreeLayout = import("./layout").TreeLayout;
const modulePath = "./layout.ts";
const { DEFAULT_LAYOUT, layoutFamily }: Layout = await import(modulePath);

const P = (id: string, parentIds: string[] = [], spouseIds: string[] = []): LayoutPerson => ({
  id,
  parentIds,
  spouseIds,
});

const gen = (l: TreeLayout, id: string) => l.byId.get(id)!.gen;
const x = (l: TreeLayout, id: string) => l.byId.get(id)!.x;

/** Left-to-right ids in each row. */
function rowsOf(l: TreeLayout): string[][] {
  const rows: string[][] = Array.from({ length: l.rows }, () => []);
  [...l.people].sort((a, b) => a.x - b.x).forEach((p) => rows[p.gen].push(p.id));
  return rows;
}

/** Nobody overlaps a neighbour, and everyone sits inside the canvas. */
function assertTidy(l: TreeLayout) {
  for (const row of rowsOf(l)) {
    for (let i = 1; i < row.length; i++) {
      const gap = x(l, row[i]) - x(l, row[i - 1]);
      assert.ok(
        gap >= DEFAULT_LAYOUT.spouseStep - 0.01,
        `${row[i - 1]} and ${row[i]} are only ${gap.toFixed(1)} apart`,
      );
    }
  }
  for (const p of l.people) {
    assert.ok(p.x - DEFAULT_LAYOUT.nodeWidth / 2 >= 0, `${p.id} hangs off the left edge`);
    assert.ok(p.x + DEFAULT_LAYOUT.nodeWidth / 2 <= l.width + 0.01, `${p.id} hangs off the right edge`);
  }
}

const fourGenerations = [
  P("g1", [], ["g2"]),
  P("g2", [], ["g1"]),
  P("p1", ["g1", "g2"], ["p2"]),
  P("p2", [], ["p1"]),
  P("u1", ["g1", "g2"], ["u2"]),
  P("u2", [], ["u1"]),
  P("u3", ["g1", "g2"]),
  P("c1", ["p1", "p2"], ["c1s"]),
  P("c1s", [], ["c1"]),
  P("c2", ["p1", "p2"]),
  P("c3", ["u1", "u2"]),
  P("c4", ["u1", "u2"]),
  P("c5", ["u1", "u2"]),
  P("k1", ["c1", "c1s"]),
  P("k2", ["c1", "c1s"]),
  P("k3", ["c3"]),
];

describe("layoutFamily", () => {
  it("lays out nobody as an empty tree", () => {
    const l = layoutFamily([]);
    assert.equal(l.rows, 0);
    assert.equal(l.people.length, 0);
    assert.equal(l.width, 0);
  });

  it("places a family of one", () => {
    const l = layoutFamily([P("solo")]);
    assert.equal(l.rows, 1);
    assert.equal(l.people.length, 1);
    assertTidy(l);
  });

  it("stacks four generations, children below parents and spouses side by side", () => {
    const l = layoutFamily(fourGenerations);
    assert.equal(l.rows, 4);
    assert.deepEqual(
      ["g1", "p1", "c1", "k1"].map((id) => gen(l, id)),
      [0, 1, 2, 3],
    );
    // Married-in spouses join their partner's row.
    assert.equal(gen(l, "p2"), gen(l, "p1"));
    assert.equal(gen(l, "c1s"), gen(l, "c1"));
    for (const c of l.couples) assert.equal(Math.abs(x(l, c.a) - x(l, c.b)), DEFAULT_LAYOUT.spouseStep);
    assertTidy(l);
  });

  it("keeps each sibling group under its parents", () => {
    const l = layoutFamily(fourGenerations);
    for (const fam of l.families) {
      const kids = fam.childIds.map((id) => x(l, id));
      const lo = Math.min(...kids) - DEFAULT_LAYOUT.nodeWidth;
      const hi = Math.max(...kids) + DEFAULT_LAYOUT.nodeWidth;
      assert.ok(fam.anchorX >= lo && fam.anchorX <= hi, `${fam.key} hangs away from its children`);
    }
  });

  it("hangs a single parent's children from that parent alone", () => {
    const l = layoutFamily([P("mum"), P("a", ["mum"]), P("b", ["mum"])]);
    assert.equal(l.couples.length, 0);
    assert.equal(l.families.length, 1);
    assert.equal(l.families[0].fromCouple, false);
    assert.deepEqual(l.families[0].childIds, ["a", "b"]);
    assert.ok(Math.abs(l.families[0].anchorX - (x(l, "a") + x(l, "b")) / 2) < 1);
    assertTidy(l);
  });

  it("keeps a couple without children together", () => {
    const l = layoutFamily([P("a", [], ["b"]), P("b", [], ["a"]), P("c"), P("d", ["c"])]);
    assert.deepEqual(
      l.couples.map((c) => [c.a, c.b].sort()),
      [["a", "b"]],
    );
    assertTidy(l);
  });

  it("seats someone married twice between both partners", () => {
    // x is listed first, so plain data order would put y and z side by side.
    const l = layoutFamily([
      P("x", [], ["y", "z"]),
      P("y", [], ["x"]),
      P("z", [], ["x"]),
      P("c1", ["x", "y"]),
      P("c2", ["x", "z"]),
    ]);
    assert.equal(rowsOf(l)[0][1], "x");
    const pairs = l.couples.map((c) => [c.a, c.b].sort().join("+")).sort();
    assert.deepEqual(pairs, ["x+y", "x+z"]);
    assert.ok(l.families.every((f) => f.fromCouple));
    // Each child sits on its own parents' side.
    assert.ok(x(l, "c1") < x(l, "c2") === x(l, "y") < x(l, "z"));
    assertTidy(l);
  });

  it("pairs spouses linked from one side only", () => {
    const l = layoutFamily([P("a"), P("b", [], ["a"]), P("kid", ["a", "b"])]);
    assert.equal(l.couples.length, 1);
    assert.equal(l.families[0].fromCouple, true);
    assertTidy(l);
  });

  it("ignores links to people outside the tree, to oneself, and repeated people", () => {
    const l = layoutFamily([
      P("a", ["ghost", "a"], ["nobody", "a"]),
      P("b", ["a", "a"]),
      P("a", [], []),
    ]);
    assert.equal(l.people.length, 2);
    assert.equal(l.rows, 2);
    assert.equal(gen(l, "a"), 0);
    assert.equal(gen(l, "b"), 1);
    assert.equal(l.couples.length, 0);
    assert.deepEqual(l.families[0].parentIds, ["a"]);
  });

  it("keeps a wide, uneven family tidy", () => {
    const people: LayoutPerson[] = [P("r1", [], ["r2"]), P("r2", [], ["r1"])];
    for (let i = 0; i < 6; i++) {
      people.push(P(`k${i}`, ["r1", "r2"], i % 2 ? [`s${i}`] : []));
      if (i % 2) people.push(P(`s${i}`, [], [`k${i}`]));
      for (let j = 0; j < (i % 3) + 1; j++) people.push(P(`g${i}${j}`, i % 2 ? [`k${i}`, `s${i}`] : [`k${i}`]));
    }
    people.push(P("loner"));
    const l = layoutFamily(people);
    assert.equal(l.rows, 3);
    assert.equal(l.people.length, people.length);
    assertTidy(l);
  });
});
