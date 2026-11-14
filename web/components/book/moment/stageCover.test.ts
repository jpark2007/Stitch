/**
 * Which spread's pop-up may be drawn (tellings.ts, stageUncovered).
 *
 *   cd web && node --no-warnings --test components/book/moment/stageCover.test.ts
 *
 * The bug: turning back from the 1997 spread to 1988 in a person's book, the
 * 1997 red bike still stood (folding) on the 1988 page. Leaves turn on the
 * wall clock, the stage folds on the frame clock (dt capped at 0.05 s), so a
 * slow frame lands the leaf with the old stage half up: traced in the browser
 * as leaf 2 at p = 0 while spread 2's reveal was still 0.5, falling for
 * seconds at swiftshader's frame rate. A stage under a leaf is now not drawn.
 *
 * The module is imported by a computed path so tsc does not ask for a `.ts`
 * import extension (see ../leafShape.test.ts).
 */

import assert from "node:assert/strict";
import { describe, it } from "node:test";

type Tellings = typeof import("./tellings");
const paths = ["./tellings.ts"];
const { stageUncovered }: Tellings = await import(paths[0]);

/** Leaves at rest for a book open at spread `open` (0-based): spread j's
 *  stage page is leaf j + 1, so leaves 0..open lie on the left. */
const openAt = (open: number, n = 7) => Array.from({ length: n }, (_, i) => ({ p: i <= open ? 1 : 0 }));

describe("stageUncovered", () => {
  it("the open spread's stage shows, its neighbours' do not", () => {
    const leaves = openAt(2);
    assert.equal(stageUncovered(leaves, 2), true);
    assert.equal(stageUncovered(leaves, 1), false, "1988's page is turned away to the left");
    assert.equal(stageUncovered(leaves, 3), false, "2015's page is under 1997's");
  });

  it("turning back from 1997 (spread 2) to 1988 (spread 1): 1997 hides once the leaf is past the spine", () => {
    const leaves = openAt(2);
    for (const [p, shown] of [
      [1, true],
      [0.9, true],
      [0.62, true],
      [0.51, true],
      [0.5, false],
      [0.2, false],
      [0, false],
    ] as const) {
      leaves[2] = { p };
      assert.equal(stageUncovered(leaves, 2), shown, `1997 at leaf p=${p}`);
      // 1988's stage page is that same leaf: it shows once the page is over
      // on the right (it only starts rising as the leaf lies down, stageGoal).
      assert.equal(stageUncovered(leaves, 1), p <= 0.5, `1988 at leaf p=${p}`);
      // Never both: one page, one scene.
      assert.ok(!(stageUncovered(leaves, 1) && stageUncovered(leaves, 2)), `both at p=${p}`);
    }
  });

  it("the traced bad frame: leaf landed, old reveal still up, old stage hidden", () => {
    const leaves = openAt(1); // leaf 2 has landed back on the right
    assert.equal(stageUncovered(leaves, 2), false);
    assert.equal(stageUncovered(leaves, 1), true);
  });

  it("turning forward from 1988 to 1997: 1988 hides once its own page passes the spine", () => {
    const leaves = openAt(1);
    leaves[2] = { p: 0.3 };
    assert.equal(stageUncovered(leaves, 1), true, "page lifting, the stage still folding on it");
    assert.equal(stageUncovered(leaves, 2), false, "1997 still under the turning page");
    leaves[2] = { p: 0.7 };
    assert.equal(stageUncovered(leaves, 1), false);
    assert.equal(stageUncovered(leaves, 2), true);
  });

  it("a page tilting under the pointer (a small p) keeps its stage", () => {
    const leaves = openAt(2);
    leaves[3] = { p: 0.05 / Math.PI };
    assert.equal(stageUncovered(leaves, 2), true);
  });

  it("past either end of the leaves there is nothing to cover it", () => {
    assert.equal(stageUncovered([], 0), true);
    assert.equal(stageUncovered([{ p: 1 }], 0), true);
  });
});
