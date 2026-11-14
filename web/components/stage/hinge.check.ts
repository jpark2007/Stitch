/**
 * Node checks for the played pop-up's hinge: the last flip issued must always
 * decide where every piece ends up, however quickly flips follow each other.
 *
 *   cd web && npx jiti components/stage/hinge.check.ts
 */

import { DEFAULT_TIMING, hingePath, riseAlong, trimFlips, type Flip } from "./hinge";

const COUNT = 6;
const SIZE = Math.sqrt(1.62);
let failures = 0;

function settled(flips: Flip[], order: number) {
  const end = flips[flips.length - 1].at + 30;
  return riseAlong(hingePath(flips, order, COUNT, SIZE, DEFAULT_TIMING), end, SIZE, DEFAULT_TIMING);
}

function expectEnd(name: string, flips: Flip[]) {
  const want = flips[flips.length - 1].open ? 1 : 0;
  for (let order = 0; order < COUNT; order++) {
    const got = settled(flips, order);
    if (Math.abs(got - want) > 1e-3) {
      failures++;
      console.error(`FAIL ${name}: order ${order} ends at ${got.toFixed(3)}, want ${want}`);
    }
  }
}

// The review's two reversals.
expectEnd("close then quick reopen", [
  { at: 0.5, open: true },
  { at: 5, open: false },
  { at: 5.2, open: true },
]);
expectEnd("open then quick close", [
  { at: 0.5, open: true },
  { at: 0.7, open: false },
]);

// Every gap between rapid toggles, both starting directions.
for (const startOpen of [true, false]) {
  for (let gap = 0.02; gap < 1.6; gap += 0.04) {
    const flips: Flip[] = [];
    let open = startOpen;
    for (let i = 0; i < 4; i++, open = !open) flips.push({ at: 1 + i * gap, open });
    for (let n = 1; n <= flips.length; n++) expectEnd(`toggles gap ${gap.toFixed(2)} x${n}`, flips.slice(0, n));
  }
}

// Trimming keeps the list short without changing where pieces end up.
{
  let flips: Flip[] = [];
  let open = false;
  let at = 0;
  for (let i = 0; i < 40; i++) {
    open = !open;
    at += [0.05, 0.3, 12, 0.6, 2, 0.15, 25][i % 7];
    flips = trimFlips([...flips, { at, open }], at);
    if (flips.length > 4) {
      failures++;
      console.error(`FAIL trim: ${flips.length} flips kept`);
    }
    expectEnd(`trimmed after ${i + 1} flips`, flips);
  }
}

if (failures) {
  console.error(`${failures} hinge check(s) failed`);
  process.exit(1);
}
console.log("hinge checks passed");
