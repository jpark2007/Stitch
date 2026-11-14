/**
 * Node checks for the played pop-up's hinge: the last flip issued must always
 * decide where every piece ends up, however quickly flips follow each other.
 *
 *   cd web && npx jiti components/stage/hinge.check.ts
 */

import { DEFAULT_TIMING, hingePath, riseAlong, trimFlips, type Flip, type PopUpTiming } from "./hinge";

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
    const gap = [0.05, 0.3, 12, 0.6, 2, 0.15, 25][i % 7];
    at += gap;
    flips = trimFlips([...flips, { at, open }], at);
    if (gap >= 12 && flips.length > 2) {
      failures++;
      console.error(`FAIL trim: ${flips.length} flips kept after a ${gap}s pause`);
    }
    expectEnd(`trimmed after ${i + 1} flips`, flips);
  }
}

/**
 * Trimming must never move a piece: toggle live (as <PopUp> does, trimming at
 * each push) and compare the trimmed pose with the untrimmed one at every
 * sample from each push until the next, and across each push itself.
 */
function expectTrimInvisible(name: string, scheduled: Flip[], startOpen: boolean, gaps: number[], first = 1, timing: PopUpTiming = DEFAULT_TIMING) {
  const pose = (live: Flip[], order: number, time: number) =>
    riseAlong(hingePath([...scheduled, ...live], order, COUNT, SIZE, timing), time, SIZE, timing);
  let full: Flip[] = [];
  let kept: Flip[] = [];
  let open = startOpen;
  let at = first;
  for (let i = 0; i <= gaps.length; i++) {
    open = !open;
    const before = kept;
    full = [...full, { at, open }];
    kept = trimFlips([...kept, { at, open }], at, scheduled);
    const until = i < gaps.length ? at + gaps[i] : at + 15;
    for (let order = 0; order < COUNT; order++) {
      const jump = Math.abs(pose(kept, order, at) - pose(before, order, at));
      if (jump > 1e-3) {
        failures++;
        console.error(`FAIL ${name}: order ${order} jumps ${jump.toFixed(3)} at flip ${i + 1} (t=${at.toFixed(2)})`);
      }
      for (let t = at; t <= until; t += 0.05) {
        const off = Math.abs(pose(kept, order, t) - pose(full, order, t));
        if (off > 1e-3) {
          failures++;
          console.error(`FAIL ${name}: order ${order} off by ${off.toFixed(3)} at t=${t.toFixed(2)} after flip ${i + 1}`);
          break;
        }
      }
    }
    if (i < gaps.length) at += gaps[i];
  }
}

// The review's repro: starting open, five quick toggles from t=5.
expectTrimInvisible("five quick toggles", [{ at: 0.4, open: true }], true, [0.25, 0.25, 0.25, 0.25], 5);
// Long runs of quick toggles, pauses of every length, a scheduled close in the middle.
expectTrimInvisible("toggle runs", [{ at: 0.4, open: true }], true, [0.05, 0.3, 12, 0.6, 2, 0.15, 25, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 11, 4.9, 5.1, 9.9]);
expectTrimInvisible("from closed", [], false, [0.2, 0.2, 0.2, 0.2, 0.2, 0.2, 10.5, 0.2, 0.2, 0.2, 0.2, 0.2, 30, 0.4]);
// Quick toggles for longer than a flip takes to settle: nothing in the run may be dropped.
expectTrimInvisible("a 25s run of quick toggles", [{ at: 0.4, open: true }], true, Array.from({ length: 28 }, (_, i) => (i % 2 ? 0.6 : 0.3)));
expectTrimInvisible("around a scheduled close", [{ at: 0.4, open: true }, { at: 22, open: false }], true, [0.3, 0.3, 0.3, 0.3, 0.3, 15, 0.3, 0.3, 0.3, 0.3, 12]);
{
  // Seeded random gaps.
  let seed = 7;
  const rnd = () => ((seed = (seed * 16807) % 2147483647) / 2147483647);
  for (let run = 0; run < 6; run++) {
    const gaps = Array.from({ length: 24 }, () => (rnd() < 0.7 ? rnd() * 0.6 : rnd() * 14));
    expectTrimInvisible(`random run ${run}`, run % 2 ? [] : [{ at: 0.4, open: true }], run % 2 === 0, gaps);
  }
}

if (failures) {
  console.error(`${failures} hinge check(s) failed`);
  process.exit(1);
}
console.log("hinge checks passed");
